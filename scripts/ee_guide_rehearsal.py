"""Five real localhost HTTP/WebSocket calls; scripted synthetic vision, text latency only.

Runs an isolated local server and one encrypted scratch identity. No real camera,
LLM, STT, TTS, uploads or messages to people. Never loads .env.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
from statistics import median
import sys
import tempfile
from time import monotonic, time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import uvicorn
from cryptography.fernet import Fernet
from websockets.asyncio.client import connect


class FixtureVision:
    """Grounding oracle only: this does not measure real small-part recognition."""
    async def call(self, operation, payload):
        from contracts import validate
        validate('scene_request', payload)
        names = payload.get('queries') or ['LED', 'resistor', 'pin 9']
        return dict(caption='LED, resistor, pin 9 and GND on a breadboard; resistor beside the blue tray.',
                    source='ollama', ms=0, objects=[dict(id=f'part-{i}', label=name,
                    bbox=[.1 + .2 * i, .2, .15, .2], conf=.9) for i, name in enumerate(names)])


class NoBrain:
    async def stream(self, *args, **kwargs):
        raise AssertionError('The deterministic guide must never call a brain')
        yield ''


async def rehearse(base, app):
    from scripts.keyframe_bench import synthetic_frame
    from server.rewind import registry
    from server.packs.electronics import USB, SERIES, HOT, MAINS
    frame = synthetic_frame(0)  # synthetic bytes only, never saved
    counts, latencies, passed = [], [], 0
    headers = {}
    if os.environ.get('A_EYE_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['A_EYE_TOKEN']
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=10) as http:
        response = await http.post('/api/call-owner')
        response.raise_for_status()
        http.headers['X-Aeye-Owner'] = response.json()['owner']
        headers = {k: v for k, v in http.headers.items() if k in ('authorization', 'x-aeye-owner')}
        for run in range(1, 6):
            sid = f'ee-rehearsal-{run}'
            seq = 0
            try:
                async with connect(base.replace('http', 'ws', 1) + '/ws2?mode=engineering',
                                   additional_headers=headers) as ws:
                    await ws.send(json.dumps(dict(v=2, type='hello', session_id=sid, pack='general',
                                                 camera=True, tts='browser', rewind=False)))
                    async with asyncio.timeout(10):
                        while json.loads(await ws.recv()).get('type') != 'agent_state':
                            pass
                    async def turn(text, *, step=None, expected=None, detail=None):
                        nonlocal seq
                        seq += 1
                        await ws.send(json.dumps(dict(v=2, type='frame', session_id=sid, seq=seq,
                                                     ts_ms=int(time() * 1000), w=10, h=10, jpeg_b64=frame)))
                        started, first, messages = monotonic(), None, []
                        await ws.send(json.dumps(dict(v=2, type='user_text', session_id=sid,
                                                     text=text, final=True)))
                        async with asyncio.timeout(10):
                            while True:
                                message = json.loads(await ws.recv())
                                assert message['type'] != 'error', 'Server rejected a turn'
                                messages.append(message)
                                if message['type'] == 'say' and first is None:
                                    first = (monotonic() - started) * 1000
                                if message['type'] == 'turn' and message['stage'] in ('done', 'failed', 'cancelled'):
                                    assert message['stage'] == 'done', 'Turn did not complete'
                                    break
                        said = ' '.join(m['text'] for m in messages if m['type'] == 'say')
                        if expected:
                            assert expected in said, 'Expected guide or recall text missing'
                        if detail:
                            assert any(m.get('detail') == detail for m in messages), 'Proof event missing'
                        if step is not None:
                            total = (monotonic() - started) * 1000
                            latencies.append((step, first, total))
                            first_label = f'{first:.2f}' if first is not None else 'silent'
                            print(f'EE run {run} step {step}: first_text_ms={first_label}; complete_ms={total:.2f}', flush=True)
                            if run <= 2:
                                assert said.startswith(f'Step {step}:'), 'Wrong guide step'
                                boxes = [op for m in messages if m['type'] == 'draw' for op in m['ops'] if op['kind'] == 'box']
                                assert boxes, 'Scripted fresh grounding did not produce a box'
                            else:
                                assert not said, 'Expected faded quiet guidance'
                        return messages

                    await turn('light an LED', step=1)
                    for text, line in [('rewire LED while USB is connected', USB),
                                       ('LED with no resistor in series', SERIES),
                                       ('touch a hot soldering iron tip', HOT),
                                       ('a wall outlet in the build', MAINS)]:
                        await turn(text, expected=line, detail='Stopped by code')
                    await turn('next', expected=USB)
                    await turn('USB unplugged, next', step=2)
                    await turn('next', step=3)
                    await turn('where did I put the resistor', expected='blue tray')
                    await turn('what was step 3', expected='the guide said' if run <= 2 else "can't confirm")
                    await turn('next', step=4)
                    await turn('next', step=5)
                    await turn('next', expected=SERIES)
                    await turn('resistor is in series, next', step=6)
                    await turn('next', step=7)
                    await turn('done', detail='Engineering: complete')
                    session = registry(app).get(sid).session
                    row = session.growth.rows('ee_led')[-1]
                    assert row['completed'] and row['safety_stop']
                    counts.append(row['hints'])
                    assert session.growth.store.cipher.encrypted
                    assert b'ee_led' not in (session.growth.store.dir / 'growth.json').read_bytes()
                    query = '?session_id=' + sid
                    denied = await http.post('/api/family/draft' + query,
                                             json=dict(contact='', log=dict(task='ee_led', detail=0)))
                    assert denied.status_code == 400
                    contact = await http.post('/api/family/contact' + query,
                                              json=dict(name='Fixture Friend', relation='friend', consent=True))
                    contact.raise_for_status()
                    draft = await http.post('/api/family/draft' + query,
                                            json=dict(contact=contact.json()['id'], log=dict(task='ee_led', detail=0)))
                    draft.raise_for_status()
                    assert 'Arduino' in draft.json()['text'] and 'practice' in draft.json()['text']
                    state = (await http.get('/api/family' + query)).json()
                    assert state['latest']['status'] == 'draft'  # no sending in a rehearsal
                    proof = (await http.get('/api/proof' + query)).json()
                    assert proof['pictures_saved'] == proof['cloud_ai_calls'] == proof['bytes_to_cloud'] == 0
                    passed += 1
                    print(f'EE run {run}: PASS; level={session.ee_guide.level}; hints={row["hints"]}; cloud_calls=0; note=draft', flush=True)
            except Exception as exc:
                # Do not log requests, tokens, images or provider exception bodies.
                reason = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
                print(f'EE run {run}: FAIL ({reason})', flush=True)
    for step in range(1, 8):
        values = [r[2] for r in latencies if r[0] == step]
        if values:
            print(f'EE STEP {step}: median_complete_ms={median(values):.2f}; max_ms={max(values):.2f}; n={len(values)}/5')
    faded = len(counts) == 5 and counts[0] > counts[1] > counts[2] == counts[3] == counts[4] == 0
    print(f'EE HINTS: {counts}; fading={faded}')
    print(f'EE GUIDE REHEARSAL: {passed}/5 PASS, {5-passed}/5 FAIL; scripted vision; text transport only')
    return int(passed != 5 or not faded)


async def main():
    with tempfile.TemporaryDirectory(prefix='ee-guide-') as folder:
        os.environ.update(LOCAL_ONLY='1', ROUTER='off', A_EYE_MOCK='1', YOLO_REFRESH='0',
                          TURNS_PER_MINUTE='200', A_EYE_MEMORY_DIR=str(Path(folder) / 'memory'),
                          A_EYE_MEMORY_KEY=Fernet.generate_key().decode())
        from server.app import create_app
        app = create_app()
        app.state.vision_v2, app.state.llm_v2 = FixtureVision(), NoBrain()
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        base = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, proxy_headers=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    await asyncio.sleep(.05)
            return await rehearse(base, app)
        finally:
            server.should_exit = True
            await task
            sock.close()


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
