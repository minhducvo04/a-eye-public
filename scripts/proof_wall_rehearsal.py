"""Five real loopback WS/HTTP trials with temporary text-only yesterday memory.

Uses the production server and configured LAN services, never a user's memory. No media is saved.
Missing Kokoro produces SKIP, not fabricated first-audio timing. Internet stays connected; assert zero cloud AI calls and payload bytes.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
from statistics import median
import sys
import tempfile
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import uvicorn
from websockets.asyncio.client import connect

from scripts.offline_check import seed


def pictures_result(count):
    if type(count) is not int or count < 0:
        return False, 'FAIL (picture count unavailable or invalid)'
    if count:
        return False, 'FAIL (expected 0 saved pictures)'
    return True, 'PASS'


def turn_result(name, messages, terminal):
    from server import safety
    reasons = []
    said = ' '.join(m['text'] for m in messages if m['type'] == 'say')
    final = [m for m in messages if m['type'] == 'say' and m.get('final')]
    if not final:
        reasons.append('no final answer')
    if terminal != 'done':
        reasons.append(f'turn {terminal}')
    content_ok = (any(m['type'] == 'find_item' and m.get('found') for m in messages)
                  and 'yesterday' in said.lower()) if name == 'yesterday' else (
                  any(m.get('detail') == 'Stopped by code' for m in messages)
                  and any(line in said for line in safety.LINES))
    if not content_ok:
        reasons.append('memory evidence missing' if name == 'yesterday' else 'safety evidence missing')
    reasons.extend(dict.fromkeys(m['detail'] for m in messages
                                if m.get('detail', '').startswith('Voice unavailable')))
    valid = not reasons
    # A preface alone is not an audible answer. Require payload and stream completion for the final say.
    audio = [m for m in messages if m['type'] == 'audio' and final
             and m['utterance_id'] == final[-1]['utterance_id']]
    audible = any(m.get('pcm16_b64') for m in audio) and any(m.get('final') for m in audio)
    if not audible:
        reasons.append('answer audio unavailable or incomplete')
    return valid, audible, reasons


def print_medians(timings):
    for name in ('yesterday', 'safety', 'all'):
        selected = [row for row in timings if name == 'all' or row[0] == name]
        values = []
        for index, label in ((1, 'first_audio'), (2, 'answer')):
            samples = [row[index] for row in selected if row[index] is not None]
            value = f'{median(samples):.0f}ms' if samples else 'unavailable'
            values.append(f'{label}={value} (n={len(samples)}/{len(selected)})')
        print(f'MEDIAN {name}: ' + '; '.join(values), flush=True)


async def main():
    os.environ.update(LOCAL_ONLY='1', A_EYE_TOKEN=secrets.token_urlsafe(32), ROUTER='off', A_EYE_MOCK='0')
    from server.app import create_app
    from server.auth import mint_owner
    from server.memory import Memories
    app = create_app()
    owner = mint_owner()
    headers = {'Authorization': 'Bearer ' + os.environ['A_EYE_TOKEN'], 'X-Aeye-Owner': owner}
    with tempfile.TemporaryDirectory(prefix='proof-rehearsal-') as folder:
        app.state.memories = Memories(base=folder)
        store = app.state.memories.for_owner('token:' + hashlib.sha256(owner.encode()).hexdigest())
        store.save_settings(dict(consent=dict(person=True, caregiver=True), remembering=True,
                                 items=[dict(item='keys', home='blue tray')], rooms=['kitchen'], kept_days=7))
        seed(store)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        passed = skipped = 0
        timings = []
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(.05)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', headers=headers) as http:
                for trial in range(1, 6):
                    sid = f'proof-{trial}'
                    async with connect(f'ws://127.0.0.1:{port}/ws2', additional_headers=headers) as ws:
                        await ws.send(json.dumps(dict(v=2, type='hello', session_id=sid, private=True,
                                                      pack='general', camera=False, rewind=False)))
                        async with asyncio.timeout(15):
                            while json.loads(await ws.recv()).get('type') != 'agent_state':
                                pass
                        ok, audible = True, True
                        trial_reasons = []
                        for name, question in [('yesterday', 'Where did I leave my keys yesterday?'),
                                               ('safety', 'My power strip is smoking. Is it safe?')]:
                            began, first, answer, messages = monotonic(), None, None, []
                            await ws.send(json.dumps(dict(v=2, type='user_text', session_id=sid,
                                                          text=question, final=True)))
                            terminal = 'timeout (45s)'
                            try:
                                async with asyncio.timeout(45):
                                    while True:
                                        msg = json.loads(await ws.recv())
                                        messages.append(msg)
                                        if msg['type'] == 'audio' and msg.get('pcm16_b64') and first is None:
                                            first = (monotonic() - began) * 1000
                                        if msg['type'] == 'say' and msg.get('final'):
                                            answer = (monotonic() - began) * 1000
                                        if msg['type'] == 'turn' and msg['stage'] in ('done', 'failed', 'cancelled'):
                                            terminal = msg['stage']
                                            break
                            except TimeoutError:
                                pass
                            valid, heard, reasons = turn_result(name, messages, terminal)
                            ok &= valid
                            audible &= heard
                            trial_reasons.extend(f'{name}: {reason}' for reason in reasons)
                            timings.append((name, first, answer))
                            timing = f'{first:.0f}ms' if first is not None else 'SKIP (Kokoro audio unavailable)'
                            answer_time = f'{answer:.0f}ms' if answer is not None else 'unavailable'
                            status = 'FAIL' if not valid else 'PASS' if heard else 'SKIP audio'
                            print(f'trial {trial} {name}: {status}; first_audio={timing}; answer={answer_time}'
                                  + ('; ' + '; '.join(reasons) if reasons else ''), flush=True)
                            if terminal.startswith('timeout'):
                                # Close this socket below; never consume its late messages as the next question.
                                break
                        response = await http.get('/api/proof', params={'session_id': sid})
                        response.raise_for_status()
                        proof = response.json()
                        cloud_ok = proof.get('cloud_ai_calls') == 0 and proof.get('bytes_to_cloud') == 0
                        ok &= cloud_ok
                        if not cloud_ok:
                            trial_reasons.append('cloud counters nonzero or unavailable')
                        print(f'trial {trial} Cloud AI calls: {proof.get("cloud_ai_calls")}; '
                              f'Bytes to cloud: {proof.get("bytes_to_cloud")}; '
                              f'{"PASS" if cloud_ok else "FAIL"}', flush=True)
                        count = proof.get('pictures_saved')
                        pictures_ok, picture_status = pictures_result(count)
                        ok &= pictures_ok
                        print(f'trial {trial} pictures: {count}; {picture_status}', flush=True)
                        if not pictures_ok:
                            trial_reasons.append('pictures: ' + picture_status)
                        status = 'FAIL' if not ok else 'PASS' if audible else 'SKIP audio'
                        print(f'trial {trial} result: {status}'
                              + ('; ' + '; '.join(trial_reasons) if trial_reasons else ''), flush=True)
                        passed += int(ok and audible)
                        skipped += int(ok and not audible)
        finally:
            server.should_exit = True
            await task
            sock.close()
        print_medians(timings)
        print(f'REHEARSAL: {passed}/5 PASS, {skipped}/5 SKIP (missing local audio), '
              f'{5 - passed - skipped}/5 FAIL; text fixture removed')
        return 0 if passed == 5 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
