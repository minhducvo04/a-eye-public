"""Gemini Live speech-to-speech probe: first audio after end of speech, barge-in, live scene notes, tool calls.

    .venv/bin/python -m voice.gemini_live [model] [runs]

Synthetic speech only (macOS `say`), never a real mic. The key goes in a header, never a URL, and is never
printed. Scene notes are injected mid-session as text, the way the A-eye scene feed would.
"""
import asyncio
import base64
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import wave

import websockets

from voice.bench import load_env

URL = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'
RATE = 16000
CHUNK = RATE // 50  # 20 ms

SYSTEM = ('You are A-eye, a calm PC-building helper on a live video call. Answer in one or two short sentences. '
          'Lines that start with "view now:" describe what the camera sees right now; trust the newest one. '
          'When you name a part that is in the view, call the draw tool with its object id.')
DRAW_TOOL = dict(function_declarations=[dict(
    name='draw', description='Highlight a scene object on the live camera picture.',
    parameters=dict(type='OBJECT', properties=dict(
        target=dict(type='STRING', description='scene object id, for example o1'),
        kind=dict(type='STRING', enum=['box', 'arrow', 'circle', 'label'])), required=['target', 'kind']))])


def speech(text):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'q.wav')
        subprocess.run(['say', '-o', path, '--data-format=LEI16@16000', text], check=True)
        with wave.open(path) as source:
            return source.readframes(source.getnframes())


async def stream_audio(ws, pcm, silence_s=0.0):
    data = pcm + b'\0\0' * int(RATE * silence_s)
    started = time.monotonic()
    for i in range(0, len(data), CHUNK * 2):
        await ws.send(json.dumps(dict(realtimeInput=dict(audio=dict(
            data=base64.b64encode(data[i:i + CHUNK * 2]).decode(), mimeType=f'audio/pcm;rate={RATE}')))))
        await asyncio.sleep(max(0, started + (i // 2 + CHUNK) / RATE - time.monotonic()))
    return started + len(pcm) / 2 / RATE  # wall time the speech itself ended


async def run(model, runs):
    key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY') or ''
    question = speech('Where does this fan cable go?')
    cut_in = speech('Wait, stop. Which one is the RAM slot?')
    firsts, barges, tools, used_scene = [], [], 0, 0
    async with websockets.connect(URL, additional_headers={'x-goog-api-key': key}, max_size=None) as ws:
        await ws.send(json.dumps(dict(setup=dict(
            model=f'models/{model}', generationConfig=dict(responseModalities=['AUDIO']),
            systemInstruction=dict(parts=[dict(text=SYSTEM)]), tools=[DRAW_TOOL],
            outputAudioTranscription={}))))
        first = json.loads(await ws.recv())
        if 'setupComplete' not in first:
            raise SystemExit(f'setup failed: {str(first)[:200]}')
        inbox = asyncio.Queue()

        async def reader():
            async for raw in ws:
                await inbox.put((time.monotonic(), json.loads(raw)))

        listen = asyncio.create_task(reader())
        for n in range(runs):
            # Scene feed: a fresh view note, as the Qwen2.5-VL loop would write once a second.
            header = 'CPU_FAN' if n % 2 == 0 else 'SYS_FAN1'
            await ws.send(json.dumps(dict(clientContent=dict(turns=[dict(role='user', parts=[dict(
                text=f'view now: o1 fan cable lower left; o2 {header} header top right; o3 DDR5 slots right side')])],
                turnComplete=False))))
            while not inbox.empty():
                inbox.get_nowait()
            ended = await stream_audio(ws, question, silence_s=0)
            silence = asyncio.create_task(stream_audio(ws, b'', silence_s=3))
            got_audio = None
            transcript = ''
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    at, msg = await asyncio.wait_for(inbox.get(), 8)
                except TimeoutError:
                    break
                if 'toolCall' in msg:
                    tools += 1
                    calls = msg['toolCall'].get('functionCalls', [])
                    await ws.send(json.dumps(dict(toolResponse=dict(functionResponses=[
                        dict(id=c.get('id'), name=c.get('name'), response=dict(ok=True)) for c in calls]))))
                content = msg.get('serverContent', {})
                transcript += content.get('outputTranscription', {}).get('text', '')
                parts = content.get('modelTurn', {}).get('parts', [])
                if got_audio is None and any('inlineData' in p for p in parts):
                    got_audio = at
                    firsts.append((at - ended) * 1000)
                    break
            await silence
            # Barge-in: talk over the reply and time the server's interrupted signal.
            await asyncio.sleep(0.4)
            cut = asyncio.create_task(stream_audio(ws, cut_in, silence_s=2.5))
            onset = time.monotonic()
            interrupted = None
            while time.monotonic() < onset + 6:
                try:
                    at, msg = await asyncio.wait_for(inbox.get(), 6)
                except TimeoutError:
                    break
                content = msg.get('serverContent', {})
                transcript += content.get('outputTranscription', {}).get('text', '')
                if content.get('interrupted'):
                    interrupted = (at - onset) * 1000
                    barges.append(interrupted)
                    break
                if 'toolCall' in msg:
                    tools += 1
                    calls = msg['toolCall'].get('functionCalls', [])
                    await ws.send(json.dumps(dict(toolResponse=dict(functionResponses=[
                        dict(id=c.get('id'), name=c.get('name'), response=dict(ok=True)) for c in calls]))))
            await cut
            used_scene += header.lower() in transcript.lower().replace(' ', '_')
            print(f'run {n}: first audio {firsts[-1]:.0f} ms' if got_audio else f'run {n}: no audio',
                  f'| interrupted {interrupted:.0f} ms after cut-in' if interrupted else '| no interrupt signal',
                  f'| said: {transcript.strip()[:120]}')
            await asyncio.sleep(3)
        listen.cancel()
    if firsts:
        print(f'{model}: first audio after end of speech median {statistics.median(firsts):.0f} ms '
              f'[{min(firsts):.0f}-{max(firsts):.0f}] n={len(firsts)}')
    if barges:
        print(f'interrupt signal after cut-in onset median {statistics.median(barges):.0f} ms n={len(barges)}')
    print(f'tool calls {tools}; answers naming the newest scene header {used_scene}/{runs}')


if __name__ == '__main__':
    load_env()
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else 'gemini-3.8-live', int(sys.argv[2]) if len(sys.argv) > 2 else 4))
