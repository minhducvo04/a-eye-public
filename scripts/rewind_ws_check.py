"""Rewind end to end through a running server: /ws2 call with Rewind on -> 1 clip -> TwelveLabs -> typed question.

Usage: .venv/bin/python scripts/rewind_ws_check.py --url http://127.0.0.1:8437 --question "When did I put down the red ball?"
Uploads exactly one clip of about 5 seconds (synthetic frames, no audio). Needs TWELVELABS_API_KEY on the server; uses A_EYE_TOKEN when set.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from time import monotonic

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rewind_probe import scene  # noqa: E402


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8437')
    parser.add_argument('--question', default='When did I put down the red ball?')
    args = parser.parse_args()
    token = os.environ.get('A_EYE_TOKEN', '')
    ws_url = args.url.replace('http', 'ws', 1) + '/ws2' + (f'?token={token}' if token else '')
    headers = {'authorization': f'Bearer {token}'} if token else {}
    session = f'rewind-check-{int(monotonic())}'
    async with websockets.connect(ws_url, max_size=2 ** 24) as ws, httpx.AsyncClient(base_url=args.url, timeout=30, headers=headers) as http:
        await ws.send(json.dumps(dict(v=2, type='hello', session_id=session, pack='general', rewind=True)))

        async def until(kind, timeout=60):
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if msg['type'] == kind:
                    return msg

        state = await until('rewind_state')
        print('rewind_state:', {k: v for k, v in state.items() if k != 'v'})
        for step in range(28):                  # 7 s of a red ball at 4 fps
            t = step / 4
            await ws.send(json.dumps(dict(v=2, type='frame', session_id=session, seq=step + 1, ts_ms=int(t * 1000),
                                          w=640, h=480, jpeg_b64=scene('ball', t))))
            await asyncio.sleep(0.25)
        status = (await http.get('/api/rewind/status', params={'session_id': session})).json()
        print('status after frames:', status)
        started = monotonic()
        flushed = (await http.post('/api/rewind/flush', json={'session_id': session})).json()
        print(f'flush: {flushed} in {(monotonic() - started) * 1000:.0f} ms')
        while True:
            status = (await http.get('/api/rewind/status', params={'session_id': session})).json()
            if status['ready'] or monotonic() - started > 300:
                break
            await asyncio.sleep(2)
        print(f'indexed: {status} after {monotonic() - started:.1f} s')
        asked = monotonic()
        await ws.send(json.dumps(dict(v=2, type='user_text', session_id=session, text=args.question, final=True)))
        hit = await until('rewind_hit')
        hit_ms = (monotonic() - asked) * 1000
        said = await until('say')
        print(f'rewind_hit after {hit_ms:.0f} ms:', {k: v for k, v in hit.items() if k not in ('v', 'frame_b64')},
              f"frame_b64 {len(hit.get('frame_b64', ''))} chars")
        print(f"say after {(monotonic() - asked) * 1000:.0f} ms: {said['text']!r}")


if __name__ == '__main__':
    asyncio.run(main())
