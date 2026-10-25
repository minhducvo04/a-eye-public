"""Loud-hall replay on /ws2: crowd-noise utterances between real questions, as the page streams them.

    ~/Projects/kyra/.venv/bin/python -m voice.bench_noise_turns ws://127.0.0.1:8016/ws2

Pattern per round: a noisy question (speech + babble at 5 dB SNR), then while the reply speaks, two
noise-only utterances (babble bursts the client VAD let through). Reports the same turn stages and
metrics the live log has: completed, failed ("Didn't catch that"), cancelled, and the timeline split.
All audio is synthetic (macOS say); nothing is recorded.
"""
import asyncio
import base64
import json
import statistics
import sys
import time
import uuid

import numpy as np
import websockets

from voice.noise_bench import TARGETS, crowd, mix, say

CHUNK = 3200


def pcm16(x):
    return (np.clip(x, -1, 1) * 32767).astype('<i2').tobytes()


async def main(url, rounds=4):
    sid = uuid.uuid4().hex
    stages, metrics, details = [], [], []
    async with websockets.connect(url, max_size=None) as ws:
        async def send(kind, **fields):
            await ws.send(json.dumps(dict(v=2, type=kind, session_id=sid, **fields)))

        async def stream(pcm):
            started = time.monotonic()
            for i, at in enumerate(range(0, len(pcm), CHUNK * 2)):
                await send('audio_in', seq=int(time.time() * 1000) % 10**9 + i, sample_rate=16000, final=False,
                           pcm16_b64=base64.b64encode(pcm[at:at + CHUNK * 2]).decode())
                await asyncio.sleep(max(0, started + (at // 2 + CHUNK) / 16000 - time.monotonic()))
            await send('audio_in', seq=int(time.time() * 1000) % 10**9 + 999, sample_rate=16000, final=True,
                       pcm16_b64='')

        async def reader():
            async for raw in ws:
                msg = json.loads(raw)
                if msg['type'] == 'turn' and msg['stage'] in ('done', 'failed', 'cancelled'):
                    stages.append(msg['stage'])
                    if msg.get('detail'):
                        details.append(msg['detail'])
                if msg['type'] == 'metrics' and 'turn_id' in msg:
                    metrics.append(msg)

        await send('hello', pack='general', tts='server', stt='server', camera=False)
        listen = asyncio.create_task(reader())
        await asyncio.sleep(1)
        for r in range(rounds):
            question = mix(say(TARGETS[r % len(TARGETS)]), crowd(4, seed=r), 5)
            await stream(pcm16(question))
            await asyncio.sleep(1.2)  # reply is speaking now
            for n in range(2):
                await stream(pcm16(crowd(0.8, seed=100 + r * 2 + n) * 0.05))
                await asyncio.sleep(0.5)
            await asyncio.sleep(4)
        listen.cancel()
    done = [m for m in metrics]
    print(f'turn ends: {dict((s, stages.count(s)) for s in set(stages))}; completed replies {len(done)}/{rounds}')
    for key in ('stt_ms', 'brain_first_token_ms', 'tts_ms', 'first_audio_ms', 'turn_ms'):
        values = [m[key] for m in done if key in m]
        if values:
            print(f'  {key}: median {statistics.median(values):.0f} ms [{min(values):.0f}-{max(values):.0f}]')
    if details:
        print('  status details:', sorted(set(details)))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'ws://127.0.0.1:8016/ws2'))
