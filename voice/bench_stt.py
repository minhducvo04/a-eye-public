"""Server-STT turn timeline on /ws2: synthetic speech streamed in real time, as the page sends it.

    .venv/bin/python -m voice.bench_stt ws://127.0.0.1:8016/ws2 4

Per turn, from the final audio chunk: transcribed (stage thinking), first say, first server audio.
Uses macOS `say` speech only; no real mic.
"""
import asyncio
import base64
import json
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import os
import wave

import websockets

LINES = ['Where does this fan cable go?', 'Which slot is the RAM?', 'Is that the CPU fan header?',
         'What do I plug in next?']
CHUNK = 3200  # 200 ms of 16 kHz samples, like voice.js


def speech(text):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'q.wav')
        subprocess.run(['say', '-o', path, '--data-format=LEI16@16000', text], check=True)
        with wave.open(path) as source:
            return source.readframes(source.getnframes())


async def main(url, turns):
    sid = uuid.uuid4().hex
    rows = []
    async with websockets.connect(url, max_size=None) as ws:
        async def send(kind, **fields):
            await ws.send(json.dumps(dict(v=2, type=kind, session_id=sid, **fields)))

        await send('hello', pack='general', tts='server', stt='server', camera=False,
                   **({'voice': os.environ['BENCH_VOICE']} if os.environ.get('BENCH_VOICE') else {}))
        inbox = asyncio.Queue()

        async def reader():
            async for raw in ws:
                await inbox.put((time.monotonic(), json.loads(raw)))

        listen = asyncio.create_task(reader())
        await asyncio.sleep(1)
        seq = 0
        for i in range(turns):
            pcm = speech(LINES[i % len(LINES)]) + b'\0\0' * 6400  # 400 ms of trailing quiet (VAD hang)
            while not inbox.empty():
                inbox.get_nowait()
            started = time.monotonic()
            for at in range(0, len(pcm), CHUNK * 2):
                await send('audio_in', seq=seq, sample_rate=16000, final=False,
                           pcm16_b64=base64.b64encode(pcm[at:at + CHUNK * 2]).decode())
                seq += 1
                await asyncio.sleep(max(0, started + (at // 2 + CHUNK) / 16000 - time.monotonic()))
            await send('audio_in', seq=seq, sample_rate=16000, final=True, pcm16_b64='')
            seq += 1
            final_at = time.monotonic()
            got = {}
            text = ''
            while True:
                try:
                    at, msg = await asyncio.wait_for(inbox.get(), 15)
                except TimeoutError:
                    break
                if msg['type'] == 'turn':
                    got.setdefault(msg['stage'], (at - final_at) * 1000)
                    text = msg.get('text', text)
                    if msg['stage'] in ('done', 'failed', 'cancelled'):
                        break
                if msg['type'] == 'say':
                    got.setdefault('say', (at - final_at) * 1000)
                if msg['type'] == 'audio' and msg.get('pcm16_b64'):
                    got.setdefault('audio', (at - final_at) * 1000)
            rows.append(got)
            fmt = lambda k: f"{got[k]:.0f}" if k in got else '-'
            print(f"turn {i}: heard '{text[:40]}' | transcribed {fmt('thinking')} ms | say {fmt('say')} ms | "
                  f"audio {fmt('audio')} ms | end {[k for k in got if k in ('done', 'failed', 'cancelled')]}")
            await asyncio.sleep(2)
        listen.cancel()
    for key in ('thinking', 'say', 'audio'):
        values = [r[key] for r in rows if key in r]
        if values:
            print(f'{key}: median {statistics.median(values):.0f} ms [{min(values):.0f}-{max(values):.0f}] n={len(values)}')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'ws://127.0.0.1:8016/ws2',
                     int(sys.argv[2]) if len(sys.argv) > 2 else 4))
