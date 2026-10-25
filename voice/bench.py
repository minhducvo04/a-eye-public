"""Brain latency bench for voice turns: first token and full reply per model, real API.

    .venv/bin/python -m voice.bench claude-haiku-5-5 claude-sonnet-5-5

Loads .env into the process (values never printed). Prints only timings and reply length.
"""
import asyncio
import json
import os
import re
import statistics
import sys
from time import perf_counter

from server.llm import LLMClient

SYSTEM = ('You are A-eye, a calm PC-building helper on a live video call. Line 1: one JSON object with agent and draw. '
          'Next line: SAY: then one or two short spoken sentences. Start with a short clause of 4 to 8 words.')
TALKER = ('You are the talker of A-eye, a calm PC-building helper on a live video call. Reply with SAY: then one or two '
          'short spoken sentences, nothing else. A planner draws on the picture for you; never output JSON.')
USER = json.dumps(dict(caption='motherboard B650 on a desk, a 4-pin fan cable in hand', objects=[
    dict(id='o1', label='CPU_FAN header', bbox=[0.62, 0.18, 0.06, 0.03]),
    dict(id='o2', label='fan cable', bbox=[0.30, 0.55, 0.12, 0.10])], conversation=[], text='where does this cable go?'))


def load_env(path='.env'):
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


async def once(model):
    started = perf_counter()
    first = clause = None
    text = ''
    system = TALKER if os.environ.get('BENCH_ROLE') == 'talker' else SYSTEM
    async for delta in LLMClient().stream(system, USER, model=model, max_tokens=200):
        if first is None:
            first = (perf_counter() - started) * 1000
        text += delta
        spoken = text.split('SAY:', 1)[1] if 'SAY:' in text else ''
        if clause is None and re.search(r'[,.!?]', spoken):
            clause = (perf_counter() - started) * 1000
    return first, (perf_counter() - started) * 1000, 'SAY' in text, clause or first


async def main(models, runs=8):
    os.environ['LLM_PROVIDER'] = 'claude'
    os.environ['BRAIN_FIRST_TOKEN_S'] = '10'
    os.environ['OLLAMA_URL'] = 'http://127.0.0.1:9'  # no silent local fallback in the bench
    for model in models:
        rows = [await once(model) for _ in range(runs)]
        firsts = [r[0] for r in rows]
        totals = [r[1] for r in rows]
        clauses = [r[3] for r in rows]
        print(f'{model}: first token median {statistics.median(firsts):.0f} ms [{min(firsts):.0f}-{max(firsts):.0f}], '
              f'first spoken clause {statistics.median(clauses):.0f} ms [{min(clauses):.0f}-{max(clauses):.0f}], '
              f'full {statistics.median(totals):.0f} ms, SAY ok {sum(r[2] for r in rows)}/{runs}, n={runs}')


if __name__ == '__main__':
    load_env()
    asyncio.run(main(sys.argv[1:] or ['claude-haiku-5-5', 'claude-sonnet-5-5']))
