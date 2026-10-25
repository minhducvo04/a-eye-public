"""Generate labelled router training questions with the Claude API (Sonnet 5.5).

Usage: .venv/bin/python -m router.gen --env ~/Projects/a-eye/.env [--total 3000] [--seed 13]
A fixed seed picks the batch plan (label, mode, style, context share). Writes router/data/train.jsonl and appends
token use and cost to router/data/gen_log.json. The key is read from the env file and never printed.
"""
import argparse
import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from router.data import HELDOUT, TRAIN, load
from router.labels import DESCRIPTIONS, LABELS, MODES

MODEL = 'claude-sonnet-5-5'
PRICE_IN, PRICE_OUT = 2.00, 10.00            # USD per million tokens
SHARE = {'now': 0.60, 'past': 0.25, 'expert': 0.15}
BATCH = 25
STYLES = [
    'short spoken fragments, the way people talk while their hands are busy',
    'full polite sentences',
    'casual with filler words (um, okay, wait, hey) as a speech-to-text transcript would give them',
    'a mix of very short (2 to 4 words) and medium questions',
    'slightly wrong grammar or missing words, like a rough voice transcript',
    'questions that name specific objects and parts of this task',
    'questions that use only pronouns (this, that, it, here) and rely on what is on camera or said before',
]
CONFUSERS = {
    'now': ('Include some that look like the other routes but are not: "where does this go" (now, not past), '
            '"is it plugged in" (now: check the frame), "what should I do next", "what was that word you said" '
            '(the conversation already has it), and plain questions about safety basics the pack answers in one line.'),
    'past': ('Include some that look like now but need earlier footage: "did I already ...", "where did the ... '
             'go", "what did I do before this", "when did I ...", "how many ... so far", "show me when ...", '
             '"go back to ...". Avoid questions about what the assistant said, which the transcript covers.'),
    'expert': ('Include some short ones that need context to be expert: "still nothing", "are you sure?", '
               '"that did not work either", "double check that", with 1 to 2 earlier turns that show a failed '
               'try or a risky claim. Also long symptom descriptions and real safety risks.'),
}


def load_key(path):
    for line in Path(path).expanduser().read_text().splitlines():
        if line.startswith('ANTHROPIC_API_KEY='):
            os.environ['ANTHROPIC_API_KEY'] = line.split('=', 1)[1].strip().strip('"\'')
    value = os.environ.get('ANTHROPIC_API_KEY', '')
    print(f'key: {len(value)} chars, prefix {value[:6]}...')


def plan(total, seed):
    rng = random.Random(seed)
    jobs = []
    for label in LABELS:
        want = round(total * SHARE[label])
        for start in range(0, want, BATCH):
            jobs.append({'label': label, 'n': min(BATCH, want - start), 'mode': rng.choice(MODES),
                         'style': rng.choice(STYLES), 'context_share': rng.choice([0.0, 0.2, 0.4, 0.6]),
                         'seed': rng.randrange(10**6)})
    return jobs


def prompt(job):
    routes = '\n'.join(f'- {name}: {text}' for name, text in DESCRIPTIONS.items())
    return (
        'A-eye is a live AI video call assistant. It sees the user\'s camera, talks back, and can draw on the '
        'picture. A router sends each user question to one of three routes:\n'
        f'{routes}\n\n'
        f'Write {job["n"]} different user questions that belong to the route "{job["label"]}". Task mode: '
        f'{job["mode"].replace("_", " ")} (the user is doing this on camera). Style: {job["style"]}. '
        f'{CONFUSERS[job["label"]]}\n'
        f'About {int(job["context_share"] * 100)}% of the items should carry "context": the 1 or 2 turns just '
        'before the question (assistant lines and user lines as plain strings with no speaker prefix, oldest first), chosen so the route '
        'is clear only with that context. Other items get an empty context list. Vary verbs, objects, length '
        f'and openings; no numbering, no two items alike. Batch tag {job["seed"]}.')


SPEAKER = re.compile(r'^\s*(user|assistant|a-?eye|ai)\s*:\s*', re.I)

SCHEMA = {
    'type': 'object',
    'properties': {'items': {'type': 'array', 'items': {
        'type': 'object',
        'properties': {'text': {'type': 'string'}, 'context': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['text', 'context'], 'additionalProperties': False}}},
    'required': ['items'], 'additionalProperties': False}


def run(client, job):
    response = client.messages.create(
        model=MODEL, max_tokens=8000, thinking={'type': 'between_tools'}, output_config={
            'effort': 'low', 'format': {'type': 'json_schema', 'schema': SCHEMA}},
        messages=[{'role': 'user', 'content': prompt(job)}])
    text = next(b.text for b in response.content if b.type == 'text')
    items = json.loads(text)['items']
    rows = [{'text': it['text'].strip(), 'context': [SPEAKER.sub('', c).strip() for c in it['context'] if c.strip()][-2:],
             'label': job['label'], 'mode': job['mode']} for it in items if it['text'].strip()]
    return rows, response.usage.input_tokens, response.usage.output_tokens


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', default='~/Projects/a-eye/.env')
    parser.add_argument('--total', type=int, default=3000)
    parser.add_argument('--seed', type=int, default=13)
    parser.add_argument('--out', default=str(TRAIN))
    args = parser.parse_args()
    load_key(args.env)
    import anthropic
    client = anthropic.Anthropic(max_retries=4)
    jobs = plan(args.total, args.seed)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda job: run(client, job), jobs))
    held = {r['text'].lower() for r in load(HELDOUT)}
    seen, rows = set(), []
    tokens_in = tokens_out = 0
    for batch, t_in, t_out in results:
        tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
        for row in batch:
            key = row['text'].lower()
            if key in seen or key in held:
                continue
            seen.add(key)
            rows.append({'id': f'g{len(rows) + 1:04d}', **row})
    Path(args.out).write_text(''.join(json.dumps(r) + '\n' for r in rows))
    cost = tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT
    counts = {label: sum(r['label'] == label for r in rows) for label in LABELS}
    log = {'model': MODEL, 'seed': args.seed, 'calls': len(jobs), 'rows': len(rows), 'counts': counts,
           'input_tokens': tokens_in, 'output_tokens': tokens_out, 'cost_usd': round(cost, 4)}
    Path(args.out).with_name('gen_log.json').write_text(json.dumps(log, indent=2) + '\n')
    print(json.dumps(log))


if __name__ == '__main__':
    main()
