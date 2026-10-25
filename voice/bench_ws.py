"""End-to-end /ws2 voice turn bench: typed final words in, timings out (the browser speaks the first say).

    .venv/bin/python -m voice.bench_ws ws://127.0.0.1:8010/ws2 5

Sends a synthetic board picture once a second so the scene feed (Qwen2.5-VL) runs, then asks questions.
Prints per turn: first say, first draw, planner note spoken, scene age when asked. No audio, no real camera.
"""
import asyncio
import base64
import io
import json
import os
import statistics
import sys
import time
import uuid

import websockets
from PIL import Image, ImageDraw

QUESTIONS = ['where does this fan cable go?', 'which slot is the RAM?', 'is that the CPU fan header?',
             'what do I plug in next?', 'where is the front panel header?']


def board_jpeg():
    image = Image.new('RGB', (640, 480), (30, 90, 50))
    draw = ImageDraw.Draw(image)
    draw.rectangle((380, 70, 430, 90), fill=(20, 20, 20))
    draw.text((372, 95), 'CPU_FAN', fill=(240, 240, 240))
    for x in (470, 490, 510, 530):
        draw.rectangle((x, 140, x + 10, 400), fill=(60, 60, 70))
    draw.text((470, 405), 'DDR5', fill=(240, 240, 240))
    draw.rectangle((150, 300, 260, 360), fill=(10, 10, 10))
    draw.text((150, 365), 'fan cable', fill=(240, 240, 240))
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=70)
    return base64.b64encode(out.getvalue()).decode()


async def main(url, turns):
    sid = uuid.uuid4().hex
    jpeg = board_jpeg()
    last_track = None
    rows = []
    async with websockets.connect(url, max_size=8_000_000) as ws:
        async def send(kind, **fields):
            await ws.send(json.dumps(dict(v=2, type=kind, session_id=sid, **fields)))

        await send('hello', pack='pc_build', tts=os.environ.get('BENCH_TTS', 'browser'), stt='browser', camera=True)
        seq = 0

        async def frames():
            nonlocal seq
            while True:
                seq += 1
                await send('frame', seq=seq, ts_ms=int(time.time() * 1000), w=640, h=480, jpeg_b64=jpeg)
                await asyncio.sleep(1)

        feeder = asyncio.create_task(frames())
        inbox = asyncio.Queue()

        async def reader():
            nonlocal last_track
            async for raw in ws:
                msg = json.loads(raw)
                if msg['type'] == 'track':
                    last_track = time.monotonic()
                await inbox.put((time.monotonic(), msg))

        listen = asyncio.create_task(reader())
        await send('confirm', what='power_off', value=True)
        await asyncio.sleep(6)  # let the scene feed warm up
        for i in range(turns):
            while not inbox.empty():
                inbox.get_nowait()
            asked = time.monotonic()
            age = (asked - last_track) * 1000 if last_track else None
            await send('user_text', text=QUESTIONS[i % len(QUESTIONS)], final=True, source='typed')
            first_say = first_draw = first_audio = None
            says = []
            while True:
                try:
                    at, msg = await asyncio.wait_for(inbox.get(), 12)
                except TimeoutError:
                    break
                if msg['type'] == 'say':
                    says.append(msg['text'])
                    first_say = first_say or (at - asked) * 1000
                if msg['type'] == 'audio' and msg.get('pcm16_b64'):
                    first_audio = first_audio or (at - asked) * 1000
                if msg['type'] == 'draw' and len(msg['ops']) > 1:
                    first_draw = first_draw or (at - asked) * 1000
                if msg['type'] == 'turn' and msg['stage'] in ('done', 'failed', 'cancelled'):
                    break
            rows.append((first_say, first_draw, age, says, first_audio))
            print(f'turn {i}: first say {first_say and round(first_say)} ms, first audio {first_audio and round(first_audio)} ms, draw {first_draw and round(first_draw)} ms, '
                  f'scene age {age and round(age)} ms, parts {len(says)}: {" | ".join(says)[:160]}')
            await asyncio.sleep(1.5)
        feeder.cancel()
        listen.cancel()
    firsts = [r[0] for r in rows if r[0]]
    draws = [r[1] for r in rows if r[1]]
    audio = [r[4] for r in rows if r[4]]
    if audio:
        print(f'median first server audio {statistics.median(audio):.0f} ms [{min(audio):.0f}-{max(audio):.0f}] n={len(audio)}')
    stale = sum(1 for r in rows if r[2] is None or r[2] > 1500)
    print(f'median first say {statistics.median(firsts):.0f} ms [{min(firsts):.0f}-{max(firsts):.0f}], '
          f'median first draw {statistics.median(draws):.0f} ms' if draws else 'no draws', f'n={len(rows)}, '
          f'scene older than 1.5 s at ask: {stale}/{len(rows)}')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'ws://127.0.0.1:8010/ws2', int(sys.argv[2]) if len(sys.argv) > 2 else 5))
