"""Integrator: run against a live /ws2 server; read metadata from its JSON log.

.venv/bin/python scripts/expert_run.py --log /tmp/a-eye-server.log
Use A_EYE_TOKEN if the server requires its development socket token.
No camera, audio, prompts or answers are written to disk by this script.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from urllib.parse import urlencode
from uuid import uuid4

import websockets

QUESTIONS = [
    ('easy', 'Hello, what can you help me with?'),
    ('hard', 'Think hard: a fan spins but the machine does not start. How should I narrow down the cause safely?'),
    ('easy', 'What is a screwdriver used for?'),
    ('hard', 'Double check the sources and look up the specifications that distinguish DDR4 from DDR5 memory.'),
    ('easy', 'What does RAM stand for?'),
    ('hard', 'Are you sure? The label says CPU fan, but the scene called it a system fan. How do we resolve that?'),
]


def log_metrics(path, session, turn_id):
    rows = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('event') == 'expert_call' and row.get('session') == session and row.get('turn_id') == turn_id:
            rows.append(row)
    return rows[-1] if rows else {}


async def run(args):
    session = 'expert-run-' + uuid4().hex
    hashed = hashlib.sha256(session.encode()).hexdigest()[:12]
    url = args.url
    if os.environ.get('A_EYE_TOKEN'):
        url += ('&' if '?' in url else '?') + urlencode({'token': os.environ['A_EYE_TOKEN']})
    async with websockets.connect(url) as ws:
        async def send(kind, **fields):
            await ws.send(json.dumps(dict(v=2, type=kind, session_id=session, **fields)))
        await send('hello', pack='general', camera=False, tts='browser')
        await ws.recv()
        for index, (difficulty, question) in enumerate(QUESTIONS, 1):
            start = monotonic()
            await send('user_text', text=question, final=True)
            first = None
            turn_id = None
            escalated = done = expert_done = False
            model = None
            # Expert stage follows normal turn completion. Allow a short grace for that stage.
            async with asyncio.timeout(args.timeout):
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 1 if done and not escalated else args.timeout)
                    except TimeoutError:
                        if done and not escalated:
                            break
                        raise
                    msg = json.loads(raw)
                    if msg.get('stage') == 'received':
                        turn_id = msg['turn_id']
                    if msg.get('type') in ('reply_delta', 'say') and first is None:
                        first = round((monotonic() - start) * 1000, 1)
                    stage = msg.get('stage')
                    if stage == 'expert':
                        escalated, model = True, msg.get('detail')
                    if stage == 'expert_done':
                        expert_done = True
                    if stage in ('done', 'failed', 'cancelled'):
                        done = True
                    if done and expert_done:
                        break
            metrics = log_metrics(args.log, hashed, turn_id)
            print(json.dumps(dict(question=index, difficulty=difficulty, escalated=escalated,
                talker_first_text_ms=first, expert_model=metrics.get('model', model),
                expert_first_token_ms=metrics.get('first_token_ms'), expert_total_ms=metrics.get('total_ms'),
                cost_usd=metrics.get('cost_usd'), search_cost='UNVERIFIED')), flush=True)
            if escalated:
                await asyncio.sleep(args.cooldown)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='ws://127.0.0.1:8000/ws2')
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--cooldown', type=float, default=21, help='Must exceed server EXPERT_COOLDOWN_S')
    parser.add_argument('--timeout', type=float, default=90)
    asyncio.run(run(parser.parse_args()))
