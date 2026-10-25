#!/usr/bin/env python3
"""Bounded synthetic benchmarks. Run through Make; no media/credentials in results."""
import argparse
import asyncio
import base64
import io
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'docs/results/system-bench.json'
PROMPTS = {
    'small_talk': 'Hello! How are you today?',
    'part_frame': 'What is this part?',
    'multi_step': 'How do I wire an LED with a resistor to a 5 V battery supply? Explain the steps and resistor calculation.',
}
MODELS = ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5', 'qwen3.5:9b-q4_K_M']
RATES = dict(zip(MODELS, [(0.1, .5), (2, 10), (4, 20), (0, 0)]))
SYSTEM = 'Answer briefly, at most 100 words. Be clear about uncertainty. For circuits use only low voltage battery power.'
DATA = {}


def save():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(DATA, indent=2) + '\n')
    temporary.replace(OUT)


def reserve(amount):
    used = DATA.setdefault('budget_reserved_usd', 0)
    if used + amount > 2:
        raise RuntimeError('BudgetExhausted')
    DATA['budget_reserved_usd'] = round(used + amount, 6)
    save()


def record(section, row):
    DATA.setdefault(section, []).append(row)
    save()
    print(section, json.dumps(row), flush=True)


def summary(values):
    values = sorted(v for v in values if v is not None)
    return {'n': len(values), 'p50': round(statistics.median(values), 2),
            'p95': round(values[math.ceil(.95 * len(values)) - 1], 2)} if values else {'n': 0}


def frame():
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (320, 240), 'white')
    d = ImageDraw.Draw(im)
    d.line((20, 120, 300, 120), fill='gray', width=5)
    d.rectangle((100, 95, 220, 145), fill='tan', outline='black', width=3)
    for x, color in [(120, 'brown'), (140, 'black'), (160, 'red'), (200, 'gold')]:
        d.rectangle((x, 97, x + 7, 143), fill=color)
    b = io.BytesIO()
    im.save(b, 'JPEG')
    return base64.b64encode(b.getvalue()).decode()


def headers():
    token = os.environ.get('A_EYE_TOKEN')
    return {'Authorization': 'Bearer ' + token} if token else {}


async def direct(model, tier, run):
    import httpx
    row = dict(model=model, tier=tier, run=run, ok=False)
    started = time.perf_counter()
    first = None
    try:
        if model.startswith('claude'):
            import anthropic
            from server.llm import fast_options
            reserve(.012)  # 2k input + 192 output at Opus rates = .01184
            content = []
            if tier == 'part_frame':
                content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': frame()}})
            content.append({'type': 'text', 'text': PROMPTS[tier]})
            async with anthropic.AsyncAnthropic(max_retries=0, timeout=40) as client:
                started = time.perf_counter()
                async with client.messages.stream(model=model, max_tokens=192, system=SYSTEM,
                        messages=[{'role': 'user', 'content': content}], **fast_options(model)) as stream:
                    async for text in stream.text_stream:
                        if text and first is None:
                            first = (time.perf_counter() - started) * 1000
                    message = await stream.get_final_message()
                usage = message.usage
                inp, out = usage.input_tokens, usage.output_tokens
                rate = RATES[model]
                row.update(input_tokens=inp, output_tokens=out, stop_reason=message.stop_reason,
                           cost_usd=(inp * rate[0] + out * rate[1]) / 1e6, returned_model=message.model)
        else:
            url = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
            msg = {'role': 'user', 'content': PROMPTS[tier]}
            if tier == 'part_frame':
                msg['images'] = [frame()]
            async with httpx.AsyncClient(timeout=60) as client:
                started = time.perf_counter()
                async with client.stream('POST', url + '/api/chat', json={
                        'model': model, 'messages': [{'role': 'system', 'content': SYSTEM}, msg],
                        'think': False, 'stream': True, 'options': {'num_predict': 192, 'temperature': 0},
                        'keep_alive': '5m'}) as response:
                    response.raise_for_status()
                    done = False
                    async for line in response.aiter_lines():
                        chunk = json.loads(line)
                        if chunk.get('error'):
                            raise RuntimeError('OllamaError')
                        if chunk.get('message', {}).get('content') and first is None:
                            first = (time.perf_counter() - started) * 1000
                        if chunk.get('done'):
                            done = True
                            row.update(input_tokens=chunk.get('prompt_eval_count'), output_tokens=chunk.get('eval_count'),
                                       stop_reason=chunk.get('done_reason'), cost_usd=0)
                    if not done:
                        raise RuntimeError('IncompleteStream')
        row.update(ok=first is not None, first_ms=first, total_ms=(time.perf_counter() - started) * 1000)
    except Exception as exc:
        row.update(error=type(exc).__name__, status=getattr(exc, 'status_code', None),
                   elapsed_ms=(time.perf_counter() - started) * 1000)
    record('direct', row)


async def server_turn(tier, run, scene_ready=False):
    from websockets.asyncio.client import connect
    row = dict(tier=tier, run=run, ok=False)
    feeder = None
    reserve(.02)  # allowance, not a hard provider bill cap on the existing process
    started = time.perf_counter()
    try:
        async with connect('ws://127.0.0.1:8000/ws2', additional_headers=headers(),
                           open_timeout=10, close_timeout=2, max_size=8 * 1024 * 1024) as ws:
            sid = 'system-bench-' + uuid4().hex
            async def send(kind, **kw):
                await ws.send(json.dumps(dict(v=2, type=kind, session_id=sid, **kw)))
            await send('hello', pack='general', camera=tier == 'part_frame', tts='browser',
                       cloud={'claude': True, 'cartesia': False, 'twelvelabs': False})
            async with asyncio.timeout(15):
                while json.loads(await ws.recv()).get('state') != 'listening':
                    pass
            started = time.perf_counter()
            if tier == 'part_frame':
                if scene_ready:
                    image = frame()
                    async def feed():
                        seq = 0
                        while True:
                            seq += 1
                            await send('frame', seq=seq, ts_ms=int(time.time() * 1000), w=320, h=240, jpeg_b64=image)
                            await asyncio.sleep(.2)
                    feeder = asyncio.create_task(feed())
                    async with asyncio.timeout(30):
                        while True:
                            msg = json.loads(await ws.recv())
                            if msg.get('type') == 'error':
                                raise RuntimeError('SceneUnavailable')
                            if msg.get('type') == 'track':
                                row['scene_objects'] = len(msg['objects'])
                                row['scene_prepare_ms'] = (time.perf_counter() - started) * 1000
                                started = time.perf_counter()
                                break
                else:
                    await send('frame', seq=1, ts_ms=int(time.time() * 1000), w=320, h=240, jpeg_b64=frame())
            await send('user_text', text=PROMPTS[tier], final=True)
            async with asyncio.timeout(60):
                while True:
                    msg = json.loads(await ws.recv())
                    kind = msg.get('type')
                    if kind in ('reply_delta', 'say') and msg.get('text'):
                        row.setdefault('first_ms', (time.perf_counter() - started) * 1000)
                    if kind == 'turn' and str(msg.get('detail', '')).startswith('Brain: '):
                        row['brain'] = msg['detail']
                    if kind == 'metrics':
                        for key in ('brain_first_token_ms', 'vlm_ms', 'turn_ms', 'cloud_ai_calls', 'bytes_to_cloud'):
                            if key in msg:
                                row[key] = msg[key]
                    if kind == 'error':
                        row.setdefault('errors', []).append(msg.get('code'))
                    if kind == 'turn' and msg.get('stage') in ('done', 'failed', 'cancelled'):
                        row.update(ok=msg['stage'] == 'done' and 'first_ms' in row,
                                   stage=msg['stage'], total_ms=(time.perf_counter() - started) * 1000)
                        break
    except Exception as exc:
        row.update(error=type(exc).__name__, elapsed_ms=(time.perf_counter() - started) * 1000)
    finally:
        if feeder is not None:
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
    record('server_scene' if scene_ready else 'server', row)


async def gemini_control():
    # Invoke the requested unmodified check; retain timing/model/verdict, never reply/media.
    for run in range(1):
        reserve(.025)
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(sys.executable, 'scripts/gemini_live_check.py',
            cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 65)
            fields = dict(line.split(': ', 1) for line in stdout.decode().splitlines() if ': ' in line)
            first = fields.get('first-audio ms', '')
            row = dict(run=run, ok=proc.returncode == 0, first_ms=int(first) if first.isdigit() else None,
                       model=fields.get('model'), check=fields.get('blue-image check'),
                       process_ms=(time.perf_counter() - started) * 1000, exit_code=proc.returncode)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            row = dict(run=run, ok=False, error='TimeoutError')
        record('gemini_control', row)



async def gemini_tiers():
    from urllib.parse import quote
    from websockets.asyncio.client import connect
    from server import privacy
    from server.gemini_live import discover_model, live_setup, ENDPOINT, WIRE_LOGGER
    await gemini_control()
    privacy.set_call({'google': True})
    model = await discover_model()
    for run in range(10):
        for tier in PROMPTS:
            reserve(.025)
            row = dict(run=run, tier=tier, model=model, ok=False)
            try:
                async with connect(ENDPOINT + '?key=' + quote(os.environ['GEMINI_API_KEY'], safe=''),
                        open_timeout=10, close_timeout=2, logger=WIRE_LOGGER) as ws:
                    await ws.send(json.dumps({'setup': live_setup(model, SYSTEM)}))
                    ready = json.loads(await asyncio.wait_for(ws.recv(), 15))
                    if 'setupComplete' not in ready:
                        raise ValueError('SetupFailed')
                    if tier == 'part_frame':
                        await ws.send(json.dumps({'realtimeInput': {'video': {
                            'mimeType': 'image/jpeg', 'data': frame()}}}))
                        await asyncio.sleep(1)
                    started = time.perf_counter()
                    await ws.send(json.dumps({'realtimeInput': {'text': PROMPTS[tier]}}))
                    audio_bytes = 0
                    async with asyncio.timeout(40):
                        while True:
                            response = json.loads(await ws.recv())
                            if 'error' in response:
                                raise ValueError('ProviderError')
                            content = response.get('serverContent', {})
                            for part in content.get('modelTurn', {}).get('parts', []):
                                inline = part.get('inlineData', {})
                                if inline.get('mimeType', '').startswith('audio/') and inline.get('data'):
                                    row.setdefault('first_ms', (time.perf_counter() - started) * 1000)
                                    audio_bytes += len(base64.b64decode(inline['data']))
                            if content.get('turnComplete'):
                                row.update(ok='first_ms' in row, total_ms=(time.perf_counter() - started) * 1000,
                                           audio_bytes=audio_bytes)
                                break
            except Exception as exc:
                row['error'] = type(exc).__name__
            record('gemini', row)


async def network():
    hosts = ['https://api.anthropic.com', 'https://generativelanguage.googleapis.com',
             'https://api.cartesia.ai', 'https://api.twelvelabs.io',
             os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')]
    # Each curl is a fresh TCP/TLS connection, no authentication, no redirect following.
    for host in hosts:
        parsed = urlsplit(host)
        safe = f'{parsed.scheme}://{parsed.hostname}' + (f':{parsed.port}' if parsed.port else '')
        for run in range(20):
            proc = await asyncio.create_subprocess_exec('curl', '-s', '-I', '-o', '/dev/null',
                '--connect-timeout', '4', '--max-time', '8', '-w',
                '%{http_code} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total}',
                safe, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await proc.communicate()
            parts = stdout.decode().split()
            row = dict(host=parsed.hostname, run=run, ok=proc.returncode == 0, exit_code=proc.returncode)
            if len(parts) == 5:
                row.update(status=int(parts[0]), connect_ms=float(parts[1]) * 1000,
                    tls_ms=float(parts[2]) * 1000, first_ms=float(parts[3]) * 1000, total_ms=float(parts[4]) * 1000)
            record('network', row)


async def load():
    import httpx
    from websockets.asyncio.client import connect
    # Increase this process's soft FD ceiling only; inherited by our child server.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(8192, hard), hard))
    env = dict(os.environ, A_EYE_MOCK='1', LOCAL_ONLY='1', ROUTER='off', YOLO_REFRESH='0',
               REWIND_FAKE_TL='1')
    proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'uvicorn', 'server.app:app',
        '--host', '127.0.0.1', '--port', '18080', '--no-access-log', '--log-level', 'error',
        env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    base = 'http://127.0.0.1:18080'
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(100):
                try:
                    if (await client.get(base + '/')).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if proc.returncode is not None:
                    raise RuntimeError('LoadServerExited')
                await asyncio.sleep(.1)
            else:
                raise RuntimeError('LoadServerNotReady')

        async with connect('ws://127.0.0.1:18080/ws2', additional_headers=headers(),
                           open_timeout=5, close_timeout=1) as ws:
            sid = 'bench-mock-control'
            async def send(kind, **kw):
                await ws.send(json.dumps(dict(v=2, type=kind, session_id=sid, **kw)))
            await send('hello', pack='general', camera=True, tts='browser')
            await send('frame', seq=1, ts_ms=int(time.time() * 1000), w=320, h=240, jpeg_b64=frame())
            await send('user_text', text='What is this part?', final=True)
            said, tracked, done = False, False, False
            async with asyncio.timeout(10):
                while not done:
                    msg = json.loads(await ws.recv())
                    said |= msg.get('type') == 'say' and bool(msg.get('text'))
                    tracked |= msg.get('type') == 'track'
                    done = msg.get('type') == 'turn' and msg.get('stage') == 'done'
            if not (said and tracked):
                raise RuntimeError('MockControlFailed')
            DATA['load_control'] = {'fake_vision_track': tracked, 'fake_brain_reply': said, 'turn_done': done}
            save()

        for target in (50, 200, 1000):
            sockets, handshakes, failures = [], [], []
            async def open_one():
                start = time.perf_counter()
                try:
                    ws = await connect('ws://127.0.0.1:18080/ws2', additional_headers=headers(),
                                       open_timeout=20, close_timeout=1, ping_interval=None)
                    # Handshake only, deliberately no application session/hello.
                    sockets.append(ws)
                    handshakes.append((time.perf_counter() - start) * 1000)
                except Exception as exc:
                    failures.append(type(exc).__name__)
            await asyncio.gather(*(open_one() for _ in range(target)))
            samples, http_times, http_errors, http_late = [], [], 0, 0
            deadline = time.perf_counter() + 30
            async def http_worker(client):
                nonlocal http_errors, http_late
                while time.perf_counter() < deadline:
                    start = time.perf_counter()
                    try:
                        r = await client.get(base + '/')
                        if time.perf_counter() > deadline:
                            http_late += 1
                        elif r.status_code != 200:
                            http_errors += 1
                        else:
                            http_times.append((time.perf_counter() - start) * 1000)
                    except Exception:
                        if time.perf_counter() > deadline:
                            http_late += 1
                        else:
                            http_errors += 1
                    # 1 request/s per simulated client, avoids an unbounded CPU spin.
                    await asyncio.sleep(max(0, 1 - (time.perf_counter() - start)))
            async def monitor():
                while time.perf_counter() < deadline:
                    ps = await asyncio.create_subprocess_exec('ps', '-p', str(proc.pid),
                        '-o', '%cpu=', '-o', 'rss=', stdout=asyncio.subprocess.PIPE)
                    output, _ = await ps.communicate()
                    parts = output.decode().split()
                    if len(parts) == 2:
                        samples.append(dict(at_s=30 - (deadline - time.perf_counter()),
                            cpu_pct=float(parts[0]), rss_mib=int(parts[1]) / 1024,
                            open_connections=sum(ws.state.name == 'OPEN' for ws in sockets)))
                    await asyncio.sleep(1)
            async with httpx.AsyncClient(timeout=5, limits=httpx.Limits(max_connections=target,
                    max_keepalive_connections=target)) as client:
                await asyncio.gather(monitor(), *(http_worker(client) for _ in range(target)))
            alive = 0
            for offset in range(0, len(sockets), 100):
                async def ping(ws):
                    try:
                        pong = await ws.ping()
                        await asyncio.wait_for(pong, 3)
                        return 1
                    except Exception:
                        return 0
                alive += sum(await asyncio.gather(*(ping(ws) for ws in sockets[offset:offset + 100])))
            row = dict(target=target, duration_s=30, established=len(sockets), alive_at_end=alive,
                       sustained_min=min([s['open_connections'] for s in samples] + [alive]),
                       handshake_errors=len(failures), handshake_ms=summary(handshakes),
                       http_ok=len(http_times), http_errors=http_errors, http_late=http_late,
                       http_rps=len(http_times) / 30, http_ms=summary(http_times),
                       cpu_pct=summary([s['cpu_pct'] for s in samples]),
                       rss_mib=summary([s['rss_mib'] for s in samples]),
                       cpu_peak_pct=max((s['cpu_pct'] for s in samples), default=0),
                       rss_peak_mib=max((s['rss_mib'] for s in samples), default=0), samples=samples)
            record('load', row)
            await asyncio.gather(*(ws.close() for ws in sockets))
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:
            proc.kill()
            await proc.wait()



def report():
    from collections import Counter
    def pair(rows, key, scale=1000):
        stats = summary([r.get(key) / scale for r in rows if r.get('ok') and r.get(key) is not None])
        return f"{stats['p50']:.2f} / {stats['p95']:.2f}" if stats['n'] else 'unavailable'
    lines = ['# System benchmark: measured limits for judges', '',
        'Measured on 2026-10-24. These are synthetic microbenchmarks, not a million-user capacity claim.',
        'The running server uses its existing routing (including Opus and local fallbacks); the model notes do not describe every observed turn.',
        '', '## Provenance and method', '',
        f"- Benchmark checkout: `{DATA['revision']}`; Python {DATA['python']}; {DATA['platform']}.",
        '- Host: Mac17,6, 18 logical CPUs, 36 GiB RAM. Load client and server share this Mac.',
        '- Existing port 8000 process: PID 71644, cwd $HOME/Projects/a-eye; checkout HEAD 806ae12c6cb983053332f4e5676b13e3928172cd at inspection. Running process code identity is not cryptographically verified.',
        '- Raw timings, failures, token counts and resource samples: [system-bench.json](system-bench.json). No frames, audio, credentials or generated reply bodies are retained.',
        '- p50 = median; p95 = nearest rank ceil(0.95*n). With ten samples p95 is the maximum; tails are noisy. Failed trials are excluded from completion quantiles and counted explicitly; observed Gemini first audio is retained even if completion times out.',
        '- Serial trials, no benchmark load running alongside latency trials. Direct model order shuffled with seed 42; cold calls retained. Repeated identical prompts and image can benefit from local prefix/vision caching. Unrelated machine/home-GPU activity was not controlled.',
        '', '## Prompts and timing boundaries', '']
    for tier, prompt in PROMPTS.items():
        lines.append(f'- **{tier}:** {prompt}')
    lines += ['', 'Frame: deterministic 320x240 drawing of an axial resistor, generated only in RAM. Direct Claude gets that small synthetic crop; Qwen gets the same image. This is not a real camera accuracy test.',
        '', 'Direct first = first nonempty text delta; total = streamed response end, including HTTP connection setup but excluding Python client construction. Every direct request has a **192-output-token cap**, low Claude effort using the existing fast_options(), Qwen thinking off and temperature 0. Cap hits are censored answer lengths: total is time to the cap, not time to a complete answer. Qwen may report stop even at exactly 192 tokens; the cap column therefore uses output_tokens >= 192.',
        '', 'Server first = first reply_delta/say after user_text; total = turn done, browser TTS selected (no STT, server TTS or physical playback timing). A fresh WebSocket/hello precedes each turn and is outside the timer. Server-side brain-first-token is a separate internal clock. No server restart or model override was performed.',
        '', '## Direct model latency', '', 'Seconds, p50 / p95. Success means transport returned text, not that the answer is correct or complete.', '',
        '| Model | Tier | Text returned / attempted | First text | Total (capped) | At token cap |',
        '|---|---|---:|---:|---:|---:|']
    for model in MODELS:
        for tier in PROMPTS:
            rows = [r for r in DATA.get('direct', []) if r['model'] == model and r['tier'] == tier]
            lines.append(f"| {model} | {tier} | {sum(r['ok'] for r in rows)}/{len(rows)} | {pair(rows, 'first_ms')} | {pair(rows, 'total_ms')} | {sum((r.get('output_tokens') or 0) >= 192 for r in rows)} |")
    lines += ['', '## Running server on 127.0.0.1:8000', '',
        '| Trial | Done / attempted | First text p50 / p95 s | Total p50 / p95 s | Internal brain first p50 / p95 s |',
        '|---|---:|---:|---:|---:|']
    for tier in PROMPTS:
        rows = [r for r in DATA.get('server', []) if r['tier'] == tier]
        label = tier + (' (frame sent immediately; scene not confirmed)' if tier == 'part_frame' else '')
        lines.append(f"| {label} | {sum(r['ok'] for r in rows)}/{len(rows)} | {pair(rows, 'first_ms')} | {pair(rows, 'total_ms')} | {pair(rows, 'brain_first_token_ms')} |")
    rows = DATA.get('server_scene', [])
    lines.append(f"| part_frame (scene confirmed, continuous 5 Hz synthetic feed) | {sum(r['ok'] for r in rows)}/{len(rows)} | {pair(rows, 'first_ms')} | {pair(rows, 'total_ms')} | {pair(rows, 'brain_first_token_ms')} |")
    combined = [dict(r, first_ms=r.get('scene_prepare_ms', 0) + r.get('first_ms', 0),
                     total_ms=r.get('scene_prepare_ms', 0) + r.get('total_ms', 0)) for r in rows]
    lines += ['', f"Scene preparation p50/p95: **{pair(rows, 'scene_prepare_ms')} s**. From first frame to first answer: **{pair(combined, 'first_ms')} s**; from first frame to turn done: **{pair(combined, 'total_ms')} s**.",
        '', 'The original immediate-frame trials returned turn metrics with zero vision wait. Code inspection shows a first-scene race: the turn only briefly waits if a scene task already exists. Those trials do not establish visual understanding. The additional ten trials wait for a track message and keep sending fresh identical frames; preparation is measured separately. A track message confirms scene delivery, not visual accuracy or a non-fake backend.',
        '', 'Observed brain labels (all server trials): `' + str(dict(Counter(r.get('brain', 'not reported') for r in DATA.get('server', []) + rows))) + '`.',
        '', '## Gemini Live audio', '',
        'Uses the same discovery, setup, transport logger and synthetic-video mechanism as scripts/gemini_live_check.py. The original unmodified blue-frame check is run once as a control; then ten trials per requested tier use its shared production helpers. First audio means first received nonempty audio payload, not speaker playback. Audio total and capped text total are not comparable workloads. Setup/discovery are excluded; the frame tier has a one-second camera pre-roll outside the timer. Total ends at turnComplete; 40-second timeout, no retries.',
        '', '| Tier | First audio / completed / attempted | First audio p50 / p95 s | Turn complete p50 / p95 s |',
        '|---|---:|---:|---:|']
    for tier in PROMPTS:
        rows = [r for r in DATA.get('gemini', []) if r.get('tier') == tier]
        audio_rows = [dict(r, ok=r.get('first_ms') is not None) for r in rows]
        lines.append(f"| {tier} | {sum(r['ok'] for r in audio_rows)} / {sum(r['ok'] for r in rows)} / {len(rows)} | {pair(audio_rows, 'first_ms')} | {pair(rows, 'total_ms')} |")
    lines += ['', 'First-audio quantiles include every observed first-audio event, including turns whose completion timed out. Total quantiles include only completed turns; the missing completions are right-censored at 40 s and make the successful-only totals optimistic.',
        '', 'Original blue-frame control: `' + str(DATA.get('gemini_control', [])) + '`.',
        '', '## Network round trips', '',
        'Twenty unauthenticated HEAD requests per host, each with a new curl/TCP/TLS connection; no redirects followed. HTTP 4xx/3xx still count as a network response. HEAD total includes DNS, TCP, TLS and HTTP service time; it is **not ICMP RTT** and cannot be subtracted directly from pooled model latency. Connect includes DNS; TLS is cumulative time_appconnect. Home box uses HTTP over the configured private network.',
        '', '| Host | Responses / attempts | HEAD total p50 / p95 ms | TCP connect p50 / p95 ms | HTTP statuses |',
        '|---|---:|---:|---:|---|']
    for host in dict.fromkeys(r['host'] for r in DATA.get('network', [])):
        rows = [r for r in DATA['network'] if r['host'] == host]
        lines.append(f"| {host} | {sum(r['ok'] for r in rows)}/{len(rows)} | {pair(rows, 'total_ms', 1)} | {pair(rows, 'connect_ms', 1)} | {dict(Counter(r.get('status') for r in rows))} |")
    lines += ['', '## Local HTTP and WebSocket load', '',
        'Separate single-worker uvicorn process on 127.0.0.1:18080, A_EYE_MOCK=1, LOCAL_ONLY=1, fake vision/brain/voice, router off. A real synthetic frame/answer preflight verifies both fake dependencies. Each tier opens all WebSockets, then holds them for 30 seconds while one HTTP worker per simulated client requests / at up to one request/second. HTTP errors and throttling are retained. Requests completing after the 30-second boundary are counted separately as http_late and excluded from throughput/latency. End-of-window ping/pong verifies surviving sockets. No model inference, hello/session state or media fan-out in the timed handshake hold; this is an optimistic transport baseline.',
        '', 'CPU is sampled ps %cpu for the server PID (100% = one core; OS-smoothed, not interval CPU accounting). RSS is server-only resident memory. Client and unrelated processes are excluded. Connection ramp and cleanup are outside the 30-second window. The process soft FD limit is raised to at most 8192; system limits are unchanged.',
        '', '| Target | Established / alive at end | Min sustained | HTTP successes / errors / late | HTTP req/s | HTTP p50 / p95 ms | CPU p50 / p95 % | RSS p50 / p95 MiB |',
        '|---:|---:|---:|---:|---:|---:|---:|---:|']
    def statpair(s):
        return f"{s['p50']:.2f} / {s['p95']:.2f}" if s.get('n') else 'unavailable'
    for row in DATA.get('load', []):
        lines.append(f"| {row['target']} | {row['established']} / {row['alive_at_end']} | {row['sustained_min']} | {row['http_ok']} / {row['http_errors']} / {row['http_late']} | {row['http_rps']:.1f} | {statpair(row['http_ms'])} | {statpair(row['cpu_pct'])} | {statpair(row['rss_mib'])} |")
    for row in DATA.get('load', []):
        lines += ['', f"Target {row['target']}: handshake p50/p95 **{statpair(row['handshake_ms'])} ms**; server peak CPU **{row['cpu_peak_pct']:.1f}%**, peak RSS **{row['rss_peak_mib']:.2f} MiB**, {len(row['samples'])} resource samples."]
    lines += ['', 'At 1,000 clients the shared load-generator event loop was heavily delayed: only six resource samples arrived instead of approximately 30, and successful HTTP requests took about 5.6 seconds. This is an observed limit of this single-Mac test arrangement, not an isolated server saturation result. The low server CPU samples do not establish spare application capacity. Use a separate load-generator host for a throughput ceiling. Errors are counted, but this harness does not retain their individual HTTP status/exception breakdown.']
    maximum = max((r['sustained_min'] for r in DATA.get('load', [])), default=None)
    maximum = 'not yet measured' if maximum is None else str(maximum)
    lines += ['', f"**Maximum demonstrated sustained connections: {maximum} for 30 s**, not a measured saturation limit or active video-call capacity. Handshake p50/p95 and all resource samples are in the JSON. Mock preflight: `{DATA.get('load_control')}`.",
        '', '## Existing accuracy evidence (collected, not rerun)', '',
        '| Claim | Supported result | Source and limit |',
        '|---|---|---|',
        '| Router | 96/100 = 96%; Haiku reference 97/100 | [router-train.md](router-train.md): same-agent-written held-out questions; 3 unsure count as wrong. Expert 17/20; not independent user distribution. |',
        '| Vision | Qwen3.5 15/16 boxes (93.75%); Qwen2.5-VL 12/16 (75%) | [backend-benchmarks.md](../backend-benchmarks.md), measured 16-image set; [vision-route.md](vision-route.md) repeats baseline. Small benchmark, not camera-wide accuracy. |',
        '| Pointer requested 10/10 | **Not substantiated in this checkout or pointer document history.** Available result: 3/10 cached, 0/10 uncached; zero wrong pointers, other cases abstain | [pointer.md](pointer.md), [pointer-benchmark.json](pointer-benchmark.json): real local grounding, synthetic scenes, scripted talker, 1.5 s freshness deadline. Do not present 10/10. |',
        '| Recent memory | 20/20 source selection and 20/20 answer-content checks | [recent-memory.md](recent-memory.md): synthetic acceptance fixture; Rewind stubbed; not fresh service accuracy. Loopback text 3.78 ms. |',
        '| Rewind rehearsal | Latest documented batch 5/5; earlier batch 4/5 | [demo-rehearsal.md](demo-rehearsal.md): synthetic camera/mic, real vision, TwelveLabs stand-in. Latest first audio range .34-.52 s, card 1.02-1.70 s. |',
        '| Memory rehearsal | Later integrated batch 4/5 all checks; 5/5 sure with box | [demo-rehearsal.md](demo-rehearsal.md): one timing-window miss; [memory-rehearsal.md](memory-rehearsal.md) earlier 5/5 memory checks but 0/5 router-labelled-past checks. Different scopes, do not pool. |',
        '| Private proof wall | Prior final three runs: 15/15 trials, 30 answers with final audio | [proof-wall.md](proof-wall.md): later execution reports audio unavailable; preserve its run-specific qualifications, not a blanket current-device claim. |',
        '', '## Capacity model for one million users — ESTIMATES', '',
        'Registered users are not concurrent calls. Assume 1M daily active users, each using a 10-minute call/day, 4 user turns/minute. That is 10M session-min/day, 40M turns/day, 6,944 mean simultaneous calls across 24 h. Concentrating use into 8 h gives 20,833 mean concurrent calls; a 2x peak gives **41,667**. A literal 1M simultaneous calls is a different target: **66,667 user turns/s**.',
        '', 'Illustrative uncached prompt: 1,000 input + 200 output tokens/request. At current official standard prices, Haiku $0.0002/request, Sonnet $0.004, Opus $0.008. A Haiku talker every turn plus an additional Opus request on 5% of turns costs **$0.0006/turn**, or **$0.024 per 10-minute call**. Adding a Sonnet planner on 20% of turns gives **$0.0014/turn / $0.056 per call**. The latter is a planning scenario, not the current General pack behavior. At 1M calls/day: **$24k-$56k/day ($720k-$1.68M/30 days)** for these text-model requests alone.',
        '', 'The observed running server often selects Opus; if every turn were Opus at the same assumed token counts, the text-only estimate would instead be **$0.32 per 10-minute call**. Real context, prompt caches, thinking/output lengths, retries and fallback routing change this. Excludes TTS/STT, vision GPU rental/electricity, Rewind, network egress, storage, servers and tax. Local inference has no API invoice but is not economically free. Gemini native audio is an alternative architecture and is not added to the Claude total.',
        '', 'Price sources checked 2026-10-24: [Anthropic standard token pricing](https://platform.claude.com/docs/en/about-claude/pricing) (Haiku $0.10/$0.50, Sonnet $2/$10, Opus $4/$20 per million input/output tokens); [Gemini Live pricing](https://ai.google.dev/gemini-api/docs/pricing) (3.8 Live text $0.75/$4.50, audio $3/$12, image/video input $1 per million tokens). These are list prices, not a billing export.',
        '', '| Layer | Bottleneck and required change (estimate) |',
        '|---|---|',
        '| Home GPU | First likely bottleneck in the existing local-perception architecture. At 6 scans/min/session and an assumed .5-2 GPU-seconds/scan, each session consumes .05-.20 GPU equivalents. At 70% target utilization that is only 3.5-14 sessions/GPU, before voice/local-brain contention. A 41,667-call peak needs roughly 3,000-12,000 equivalent GPUs; 1M simultaneous needs about 71,000-286,000. Service time, batching and concurrency are assumptions, not established by this handshake test. The .5 s optimistic endpoint is informed by the five synthetic repeats in vision-route.md; not a production capacity measurement. |',
        '| Cloud APIs | At the 41,667-call peak, 4 turns/min means 166,668 talker requests/min, plus assumed 8,333 expert requests/min and optionally 33,334 planner requests/min. Talker alone needs about 166.7M input and 33.3M output tokens/min at assumed token sizes. Provider RPM/TPM/concurrent-stream quotas and regional reservations must be negotiated; no quota headroom was verified. Moving perception to cloud transfers this bottleneck and changes privacy/cost. |',
        '| HTTP/WS server | Horizontally scale event-driven workers behind a WebSocket-aware load balancer, with admission control, per-call routing and graceful draining. Even an illustrative 1,000 transport connections/worker would imply 42 workers at the daily peak, 1,000 for 1M simultaneous, before redundancy/headroom. This is arithmetic, not a recommendation: active sessions need a new media/inference soak test and distributed session ownership. |',
        '| Queue/state | Use a managed bounded queue for GPU/expert jobs, per-call priority/cancellation, coalesced latest frames, deadlines, backpressure and tenant quotas. Do not queue every video frame. Move reconnect/session metadata to a shared store; retain privacy boundaries and opt-in ephemeral media. Queueing cannot create GPU capacity and must not hide stale-frame failures. |',
        '', 'Scaling order: instrument real active-call service time and quotas; separate inference from socket workers; add GPU replicas and managed queue admission; distribute session state; then run multi-host active-call and failure-recovery tests. No million-user deployment is proven here.',
        '', '## Spend and verification', '']
    cost = sum(r.get('cost_usd', 0) for r in DATA.get('direct', []))
    lines += [f"Direct Claude usage-derived list-price cost: **${cost:.6f}**. Budget ledger including conservative per-call allowances: **${DATA.get('budget_reserved_usd', 0):.6f} of $2**. Server and Gemini billing totals were not exposed by these paths; their reserved allowances are estimates, not measured charges. The client blocks new requests when reservations exceed $2, but cannot enforce a provider-side dollar ceiling inside the already-running server. No retries are issued by the direct benchmark.",
        '', 'Targeted checks: `make -s -f Makefile -f scripts/system_bench.mk system-bench-test`; real runs of direct, server, server_scene, gemini, network and load phases; git diff --check. App code, contracts and dependencies are unchanged. No full app-suite, physical microphone/camera, device playback, WAN-scale load or cloud billing reconciliation claim.',
        '', 'Reproduce with Make (the extra file loads the main checkout .env only when this worktree has no .env):',
        '', '```sh', 'make -s -f Makefile -f scripts/system_bench.mk system-bench PHASE=network',
        '# Then direct, server, server_scene, gemini, load, report, one phase at a time.',
        '# Existing phase results are refused; archive the JSON before a fresh paid run.',
        '```', '', 'All task changes are uncommitted.', '']
    (ROOT / 'docs/results/system-bench.md').write_text('\n'.join(lines))
    print('Report written: docs/results/system-bench.md', flush=True)



async def main(args):
    global DATA
    if os.environ.get('LOCAL_ONLY') == '1' and args.phase not in ('load', 'report'):
        raise SystemExit('Private mode: network benchmark phases blocked')
    DATA = json.loads(OUT.read_text()) if OUT.exists() else {
        'date': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'platform': platform.platform(),
        'python': platform.python_version(), 'revision': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'prompts': PROMPTS, 'max_output_tokens': 192}
    if args.phase == 'report':
        report()
        return
    if args.phase != 'direct' and len(DATA.get('direct', [])) == 120 and not DATA.get('direct_reconciled'):
        refund = sum(.012 - r['cost_usd'] for r in DATA['direct'] if r['model'].startswith('claude') and 'cost_usd' in r)
        DATA['budget_reserved_usd'] = round(DATA['budget_reserved_usd'] - refund, 6)
        DATA['direct_reconciled'] = True
        save()
    if DATA.get(args.phase):
        raise SystemExit('Phase already has results; preserve evidence or choose a fresh output file.')
    if args.phase == 'direct':
        jobs = [(m, t, r) for r in range(10) for t in PROMPTS for m in MODELS]
        random.Random(42).shuffle(jobs)
        for model, tier, run in jobs:
            await direct(model, tier, run)
    elif args.phase == 'server':
        for run in range(10):
            for tier in PROMPTS:
                await server_turn(tier, run)
    elif args.phase == 'server_scene':
        for run in range(10):
            await server_turn('part_frame', run, scene_ready=True)
    elif args.phase == 'gemini':
        await gemini_tiers()
    else:
        await globals()[args.phase]()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=['direct', 'server', 'gemini', 'network', 'load', 'server_scene', 'report'])
    import fcntl
    import tempfile
    with open(Path(tempfile.gettempdir()) / 'a-eye-system-bench.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another system benchmark owns the budget ledger')
        asyncio.run(main(parser.parse_args()))
