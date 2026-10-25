"""Three Private sink runs for one fixture user over real localhost HTTP/WS and configured home TTS/STT.

No photos, recordings, cloud calls or actual family messages. The only checker
substitute is the explicit, visibly labelled presenter mode. Audio lives in RAM.
Use --serve for the presenter UI; the normal server has no test-control routes.
"""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import socket
from statistics import median
import sys
import tempfile
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import uvicorn
from websockets.asyncio.client import connect

FIXTURE = 'Tuesday: shutoff valve under the left cabinet'


def presenter_app(folder):
    os.environ.update(LOCAL_ONLY='1', ROUTER='off', A_EYE_MOCK='0')
    from server.app import create_app
    from server.memory import Memories
    app = create_app(presenter=True)
    app.state.memories = Memories(base=folder)
    app.state.growth_logs = {}
    from cryptography.fernet import Fernet
    from server.memory import Cipher, Store, person_id
    from server.growth import GrowthLog
    cipher = Cipher(Fernet.generate_key())  # fixture key lives only in this process

    def seed(sid, owner):
        if owner not in app.state.growth_logs:
            app.state.growth_logs[owner] = GrowthLog(Store(person_id(owner), base=folder, cipher=cipher))
        memories = app.state.memories
        store = memories.for_owner(owner)
        store.set_tag('shutoff valve', home=FIXTURE)
        # Read-only fixture attachment: no remembering/crops/scene writer is enabled.
        memories.attach(sid, store.id, owner)
    app.state.presenter_seed = seed
    return app


async def collect(ws, text, sid, *, audio=False, silent=False):
    start, first, answer = monotonic(), None, None
    if not audio:
        await ws.send(json.dumps(dict(v=2, type='user_text', session_id=sid, text=text, final=True)))
    messages = []
    async with asyncio.timeout(45):
        while True:
            m = json.loads(await ws.recv())
            messages.append(m)
            if m['type'] == 'error':
                raise AssertionError('WebSocket rejected input')
            if m['type'] == 'audio' and m.get('pcm16_b64') and first is None:
                first = 1000 * (monotonic() - start)
            if m['type'] == 'say' and m.get('final'):
                answer = 1000 * (monotonic() - start)
            if m['type'] == 'turn' and m['stage'] in ('done', 'failed', 'cancelled') and m.get('turn_id') != 'presenter':
                assert m['stage'] == 'done', m['stage']
                break
    if silent:
        assert not any(m['type'] in ('say', 'audio') for m in messages), 'Expected quiet guidance'
    elif not audio:
        final = [m for m in messages if m['type'] == 'say' and m.get('final')]
        assert final, 'No final answer'
        chunks = [m for m in messages if m['type'] == 'audio' and m['utterance_id'] == final[-1]['utterance_id']]
        assert any(m.get('pcm16_b64') for m in chunks) and any(m.get('final') for m in chunks), 'Final answer audio missing'
    return messages, (first, answer, 1000 * (monotonic() - start))


async def speak(voice, text):
    start, first, size = monotonic(), None, 0
    async for chunk in voice.say('fixture-read', text):
        if chunk and first is None:
            first = 1000 * (monotonic() - start)
        size += len(chunk)
    assert size > 0, 'Local read-aloud audio missing'
    return first, None, 1000 * (monotonic() - start)


async def rehearse(base, app):
    from voice.kokoro import KokoroAdapter
    headers = {}
    if os.environ.get('A_EYE_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['A_EYE_TOKEN']
    timings, passed, hint_counts = [], 0, []
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15) as http:
        owner = (await http.post('/api/call-owner')).json()['owner']
        http.headers['X-Aeye-Owner'] = owner
        for trial in range(1, 4):
            # One identity, separate calls: the guide level must come from the encrypted log.
            sid = f'growth-flow-{trial}'
            query = '?session_id=' + sid
            async def post(path, payload):
                r = await http.post(path, json=payload)
                r.raise_for_status()
                return r.json()
            def record(name, timing):
                timings.append((name, *timing))
                values = ['unavailable' if x is None else f'{x:.0f}ms' for x in timing]
                print(f'trial {trial} {name}: first_audio={values[0]}; answer={values[1]}; complete={values[2]}', flush=True)
            voice = KokoroAdapter()
            try:
                async with connect(base.replace('http', 'ws', 1) + '/ws2', additional_headers={k: v for k, v in http.headers.items() if k.lower() in ('authorization', 'x-aeye-owner')}) as ws:
                    await ws.send(json.dumps(dict(v=2, type='hello', session_id=sid, pack='home',
                                                 private=True, camera=False, rewind=False)))
                    async with asyncio.timeout(20):
                        while json.loads(await ws.recv()).get('type') != 'agent_state':
                            pass
                    mode = await http.get('/api/dad-demo' + query)
                    assert mode.json() == {'enabled': False}
                    async def turn(name, text, expected=None, detail=None, silent=False):
                        messages, timing = await collect(ws, text, sid, silent=silent)
                        said = ' '.join(m['text'] for m in messages if m['type'] == 'say')
                        if expected:
                            assert expected in said, f'{name}: expected speech missing'
                        if detail:
                            assert any(m.get('detail') == detail for m in messages), f'{name}: missing event {detail}'
                        record(name, timing)
                        return messages
                    await turn('1-select-sink', 'sink', 'one step at a time' if trial == 1 else None, silent=trial == 3)
                    await turn('2-recall', 'next', FIXTURE if trial == 1 else None, silent=trial == 3)
                    await turn('3-shutoff', 'next', 'Gently close')
                    await turn('4-normal-locked', 'next', 'Show me the valve again', 'Valve: unsure')
                    await post('/api/dad-demo' + query, {'enabled': True})
                    await post('/api/dad-demo' + query + '&action=verdict', {'state': 'open'})
                    await turn('5-open-locked', 'next', 'Show me the valve again', 'Checker TEST MODE')
                    async def checked(name, expected, completion=False):
                        await post('/api/dad-demo' + query + '&action=verdict', {'state': 'closed'})
                        await turn(name, 'next', expected if trial == 1 and not completion else None,
                                   'Valve: test closed', silent=(trial == 3 and not completion) or (completion and trial == 1))
                    await checked('6-checker-pass', 'dry supply connection')
                    # Deliberately arm a pass before a hazard: the stop must discard it.
                    await post('/api/dad-demo' + query + '&action=verdict', {'state': 'closed'})
                    await turn('7-outlet-stop', 'water near the outlet', 'Stop and move away', 'Stopped by code')
                    await turn('8-hazard-locked', 'next', 'Show me the valve again', 'Valve: unsure')
                    await checked('9-hand-snug', 'Only hand-snug')
                    await checked('10-tap', 'dripping tap')
                    await checked('11-dry-check', 'dry towel')
                    await checked('12-complete', None, completion=True)
                    log = next(iter(app.state.growth_logs.values()))
                    row = log.rows('sink')[-1]
                    assert row['completed'] and row['safety_stop']
                    assert len(log.rows('sink')) == trial
                    hint_counts.append(row['hints'])
                    print(f"GROWTH run {trial}: level={trial}; hints={row['hints']}; tries={row['tries']}; safety_stop={row['safety_stop']}", flush=True)
                    assert log.store.cipher.encrypted
                    assert b'sink' not in (log.store.dir / 'growth.json').read_bytes()
                    denied = await http.post('/api/family/draft' + query,
                                             json=dict(contact='', log=dict(task='sink', detail=2)))
                    assert denied.status_code == 400

                    contact = await post('/api/family/contact' + query,
                                         dict(name='Fixture Friend', relation='friend', consent=True))
                    draft = await post('/api/family/draft' + query,
                                       dict(contact=contact['id'], log=dict(task='sink', detail=2)))
                    assert FIXTURE not in draft['text'] and 'cabinet' not in draft['text']
                    record('13-note-read', await speak(voice, draft['text']))
                    await post('/api/family/heard' + query, {'id': draft['id']})
                    # Synthetic command audio from the real home TTS, through actual home STT.
                    voice.voice = 'am_michael'  # clear one-word fixture for home STT
                    pcm = bytearray()
                    async for chunk in voice.say('fixture-send', 'Send.'):
                        pcm.extend(chunk)
                    start = monotonic()
                    await ws.send(json.dumps(dict(v=2, type='audio_in', session_id=sid, seq=1,
                                                  pcm16_b64=base64.b64encode(pcm).decode(), final=True)))
                    del pcm
                    await collect(ws, '', sid, audio=True)
                    state = (await http.get('/api/family' + query)).json()
                    assert state['latest']['status'] == 'sent', 'Spoken send was not recognized/authorized'
                    record('14-spoken-send', (None, None, 1000 * (monotonic() - start)))
                    link = state['contacts'][0]['delivery_link']
                    opened = await post('/family/exchange', {'token': link.split('#', 1)[1]})
                    assert opened['text'] == draft['text']
                    start = monotonic()
                    await post('/family/reply', dict(token=opened['reply_token'], text='Nice work. Check for drips tomorrow.'))
                    replies = (await http.get('/api/family' + query)).json()['replies']
                    assert len(replies) == 1
                    record('15-reply-arrived', (None, None, 1000 * (monotonic() - start)))
                    record('16-reply-read', await speak(voice, replies[0]['text']))
                    proof = (await http.get('/api/proof' + query)).json()
                    assert proof['pictures_saved'] == proof['cloud_ai_calls'] == proof['bytes_to_cloud'] == state['pictures_sent'] == 0
                    print(f'trial {trial}: PASS; Pictures saved 0; Pictures sent 0; Cloud AI calls 0; TEST MODE', flush=True)
                    passed += 1
            except Exception as exc:
                # No provider bodies, audio, tokens or delivery links in evidence logs.
                reason = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
                print(f'trial {trial}: FAIL ({reason})', flush=True)
            finally:
                await voice.aclose()
    for name in dict.fromkeys(row[0] for row in timings):
        rows = [row for row in timings if row[0] == name]
        fields = []
        for index, label in ((1, 'first_audio'), (2, 'answer'), (3, 'complete')):
            samples = [row[index] for row in rows if row[index] is not None]
            value = f'{median(samples):.0f}ms' if samples else 'n/a'
            fields.append(f'{label}={value} (n={len(samples)}/3)')
        print('MEDIAN ' + name + ': ' + '; '.join(fields))
    decreasing = len(hint_counts) == 3 and hint_counts[0] > hint_counts[1] > hint_counts[2] == 0
    print(f'GROWTH HINTS: {hint_counts}; levels 1 -> 2 -> 3; decreasing={decreasing}')
    print(f'GROWTH FLOW REHEARSAL: {passed}/3 PASS, {3-passed}/3 FAIL; checker TEST MODE; no real-photo claim')
    return 0 if passed == 3 and decreasing else 1


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='explicit presenter-only UI; loopback, Private, test mode initially off')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='growth-flow-text-') as folder:
        app = presenter_app(folder)
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', args.port))
        base = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, proxy_headers=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    await asyncio.sleep(.05)
            if args.serve:
                print(f'PRESENTER ONLY: {base}/agent.html; select Private then Sink; checker test mode starts OFF', flush=True)
                await task
                return 0
            return await rehearse(base, app)
        finally:
            server.should_exit = True
            await task
            sock.close()


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
