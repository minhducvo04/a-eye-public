"""Typed replay of memory questions on /ws2 (memory pack, server voice): what it says and how fast.

    .venv/bin/python -m voice.bench_memory ws://127.0.0.1:8016/ws2

Typed text only, a synthetic camera frame, no person. Prints each answer with first say and first audio.
"""
import asyncio
import json
import statistics
import sys
import time
import uuid

import websockets

from voice.bench_ws import board_jpeg

QUESTIONS = ['Where are my keys?', 'Where are my glasses?', 'Did I take my pill?', 'Where are my keys?',
             'Should I take another pill?']


async def main(url):
    sid = uuid.uuid4().hex
    rows = []
    async with websockets.connect(url, max_size=None) as ws:
        async def send(kind, **fields):
            await ws.send(json.dumps(dict(v=2, type=kind, session_id=sid, **fields)))

        await send('hello', pack='memory', tts='server', stt='browser', camera=True)
        jpeg = board_jpeg()
        for seq in range(1, 4):
            await send('frame', seq=seq, ts_ms=int(time.time() * 1000), w=640, h=480, jpeg_b64=jpeg)
            await asyncio.sleep(1)
        inbox = asyncio.Queue()

        async def reader():
            async for raw in ws:
                await inbox.put((time.monotonic(), json.loads(raw)))

        listen = asyncio.create_task(reader())
        for question in QUESTIONS:
            while not inbox.empty():
                inbox.get_nowait()
            asked = time.monotonic()
            await send('user_text', text=question, final=True, source='typed')
            says, first_say, first_audio = [], None, None
            while True:
                try:
                    at, msg = await asyncio.wait_for(inbox.get(), 15)
                except TimeoutError:
                    break
                if msg['type'] == 'say':
                    says.append(msg['text'])
                    first_say = first_say or (at - asked) * 1000
                if msg['type'] == 'audio' and msg.get('pcm16_b64'):
                    first_audio = first_audio or (at - asked) * 1000
                if msg['type'] == 'turn' and msg['stage'] in ('done', 'failed', 'cancelled'):
                    break
            rows.append((first_say, first_audio))
            print(f'Q: {question}\n   A: {" ".join(says)}\n   first say {first_say and round(first_say)} ms, '
                  f'first audio {first_audio and round(first_audio)} ms')
            await asyncio.sleep(1)
        listen.cancel()
    says = [r[0] for r in rows if r[0]]
    audio = [r[1] for r in rows if r[1]]
    print(f'median first say {statistics.median(says):.0f} ms, first audio '
          f'{statistics.median(audio):.0f} ms' if audio else 'no audio', f'n={len(rows)}')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'ws://127.0.0.1:8016/ws2'))
