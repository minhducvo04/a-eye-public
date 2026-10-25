"""Real local-memory run across two separate /ws2 calls: tag and see the keys in call 1, ask in call 2.

Usage: .venv/bin/python scripts/memory_two_calls.py --url http://127.0.0.1:8439
Needs a running server with real vision (VISION_URL), the GPU box for the person check, and EmbeddingGemma files.
Nothing goes to any cloud: Rewind stays off. Sends A_EYE_TOKEN when set.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_scene import table  # noqa: E402


async def call(args, session, frames, http, ask=None):
    token = os.environ.get('A_EYE_TOKEN', '')
    url = args.url.replace('http', 'ws', 1) + '/ws2' + (f'?token={token}' if token else '')
    out = {}
    async with websockets.connect(url, max_size=2 ** 24) as ws:
        await ws.send(json.dumps(dict(v=2, type='hello', session_id=session, pack='general')))

        async def until(kind, timeout=60):
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if msg['type'] == kind:
                    return msg

        await until('agent_state')
        await asyncio.sleep(0.5)
        attached = (await http.get('/api/memory/status', params=dict(session_id=session))).json()
        print(f'{session}: memory on={attached.get("on")}, count today {attached.get("count")}')
        seq = 0
        for keys_at, seconds in frames:
            for i in range(int(seconds * 2)):
                seq += 1
                await ws.send(json.dumps(dict(v=2, type='frame', session_id=session, seq=seq, ts_ms=seq * 500, w=640,
                                              h=480, jpeg_b64=table(keys_at, seed=seq))))
                await asyncio.sleep(0.5)
        if ask:
            for question in ask:
                asked = monotonic()
                await ws.send(json.dumps(dict(v=2, type='user_text', session_id=session, text=question, final=True)))
                if question.lower().startswith('forget'):
                    state = await until('memory_state')
                    print(f'"{question}" -> memory_state after {(monotonic() - asked) * 1000:.0f} ms:',
                          {k: state.get(k) for k in ('item', 'count_item', 'count', 'crops', 'encrypted')})
                    continue
                hit = await until('find_item')
                hit_ms = (monotonic() - asked) * 1000
                msgs = []
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 60))
                    msgs.append(msg)
                    if msg['type'] == 'say' and msg.get('final'):
                        break
                draw = next((m for m in msgs if m['type'] == 'draw'), None)
                print(f'"{question}" -> find_item after {hit_ms:.0f} ms:',
                      {k: v for k, v in hit.items() if k not in ('v', 'frame_b64')},
                      f"crop {len(hit.get('frame_b64', ''))} b64 chars")
                print('   arrow:', [op for op in (draw or {}).get('ops', []) if op['kind'] == 'arrow'])
                print(f"   say after {(monotonic() - asked) * 1000:.0f} ms: {msgs[-1]['text']!r}")
                out['hit'] = hit
    return out


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8439')
    args = parser.parse_args()
    token = os.environ.get('A_EYE_TOKEN', '')
    headers = {'authorization': f'Bearer {token}'} if token else {}
    async with httpx.AsyncClient(base_url=args.url, timeout=60, headers=headers) as http:
        settings = dict(consent=dict(person=True, caregiver=True), items=[dict(item='keys', home='blue bowl')],
                        rooms=['kitchen'], kept_days=7, remembering=True)
        print('settings:', (await http.put('/api/memory/settings', json=settings)).json())
        print('tag:', (await http.post('/api/memory/tag', json=dict(name='red cup', kind='place'))).json()['tag'])
        started = monotonic()
        # Call 1, about 60 s: keys move between cups, then go under the left cup.
        await call(args, 'memory-call-one', [('right', 12), ('middle', 12), ('left', 16), (None, 20)], http)
        await asyncio.sleep(2)
        status = (await http.get('/api/memory/status')).json()
        span = monotonic() - started
        print(f'after call 1 ({span:.0f} s): {status}; about {status["bytes"] / span * 3600 / 1e6:.2f} MB per hour '
              f'at this rate')
        for query in ('keys', 'where are my keys?', 'three cups on a table', 'my passport'):
            began = monotonic()
            result = (await http.post('/api/memory/find', json=dict(query=query))).json()
            print(f'/api/memory/find {query!r}: {(monotonic() - began) * 1000:.0f} ms (server {result.get("ms")} ms),'
                  f' how={result.get("how")}, place={result.get("place")}')
        # Call 2: a new session for the same person, keys hidden; ask, then forget.
        await call(args, 'memory-call-two', [(None, 6)], http, ask=['Where are my keys?', 'Forget that.'])
        print('final status:', (await http.get('/api/memory/status')).json())


if __name__ == '__main__':
    asyncio.run(main())
