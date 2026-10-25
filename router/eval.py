"""Compare routers on the held-out set: the trained router, Haiku tool choice (one real API call per question) and
the current Rewind regex in server/rewind.py.

Usage: python -m router.eval [--env ~/Projects/a-eye/.env] [--skip-haiku] [--out router/results/heldout_eval.json]
The key is read from the env file and never printed.
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from router.data import heldout
from router.labels import DESCRIPTIONS, LABELS
from router.route import route

HAIKU = 'claude-haiku-5-5'
PRICE_IN, PRICE_OUT = 0.10, 0.50
TOOLS = {'now': 'answer_from_live_scene', 'past': 'search_rewind_memory', 'expert': 'escalate_to_expert'}
SYSTEM = ('You route one user question in a live AI video call (camera on, any task: cooking, assembly, PC '
          'building, repairs, general). Call exactly one tool. Do not answer the question.')
OUT = Path(__file__).resolve().parent / 'results' / 'heldout_eval.json'


def regex_route(text, context):
    from server.rewind import is_rewind_question
    return 'past' if is_rewind_question(text) else 'now'


def haiku_route(client, text, context):
    tools = [{'name': TOOLS[k], 'description': DESCRIPTIONS[k],
              'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}} for k in LABELS]
    turns = ''.join(f'Earlier turn: {t}\n' for t in context)
    started = perf_counter()
    response = client.messages.create(
        model=HAIKU, max_tokens=200, system=SYSTEM, tools=tools, tool_choice={'type': 'auto'},
        thinking={'type': 'disabled'}, output_config={'effort': 'low'},
        messages=[{'role': 'user', 'content': f'{turns}Question: {text}'}])
    ms = (perf_counter() - started) * 1000
    names = [b.name for b in response.content if b.type == 'tool_use']
    label = next((k for k, v in TOOLS.items() if names and names[0] == v), 'none')
    return label, ms, response.usage.input_tokens, response.usage.output_tokens


def summary(rows, key):
    labels = list(LABELS) + ['unsure', 'none']
    matrix = {g: {p: 0 for p in labels} for g in LABELS}
    for r in rows:
        matrix[r['label']][r[key]] += 1
    per_class = {g: round(matrix[g][g] / max(1, sum(matrix[g].values())), 3) for g in LABELS}
    correct = sum(r[key] == r['label'] for r in rows)
    return {'acc': round(correct / len(rows), 3), 'correct': correct, 'n': len(rows), 'recall': per_class,
            'matrix': {g: {p: n for p, n in m.items() if n or p in LABELS} for g, m in matrix.items()}}


def pct(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', default='~/Projects/a-eye/.env')
    parser.add_argument('--skip-haiku', action='store_true')
    parser.add_argument('--out', default=str(OUT))
    args = parser.parse_args()
    rows = heldout()
    route(rows[0]['text'])                       # warm the embedder once
    for r in rows:
        result = route(r['text'], r['context'])
        r['router'], r['router_raw'] = result['label'], max(result['probs'], key=result['probs'].get, default='none')
        r['router_conf'], r['router_ms'] = result['confidence'], result['ms']
        r['regex'] = regex_route(r['text'], r['context'])
    report = {'n': len(rows), 'router': summary(rows, 'router'), 'router_forced': summary(rows, 'router_raw'),
              'regex': summary(rows, 'regex'),
              'router_ms': {'p50': pct([r['router_ms'] for r in rows], .5), 'p90': pct([r['router_ms'] for r in rows], .9)}}
    unsure = [r for r in rows if r['router'] == 'unsure']
    report['unsure'] = {'count': len(unsure), 'of_which_forced_wrong': sum(r['router_raw'] != r['label'] for r in unsure)}
    if not args.skip_haiku:
        from router.gen import load_key
        load_key(args.env)
        import anthropic
        client = anthropic.Anthropic(max_retries=3)
        with ThreadPoolExecutor(4) as pool:
            out = list(pool.map(lambda r: haiku_route(client, r['text'], r['context']), rows))
        for r, (label, ms, t_in, t_out) in zip(rows, out):
            r['haiku'], r['haiku_ms'], r['haiku_in'], r['haiku_out'] = label, round(ms, 1), t_in, t_out
        tokens_in, tokens_out = sum(r['haiku_in'] for r in rows), sum(r['haiku_out'] for r in rows)
        report['haiku'] = summary(rows, 'haiku')
        report['haiku_ms'] = {'p50': pct([r['haiku_ms'] for r in rows], .5), 'p90': pct([r['haiku_ms'] for r in rows], .9)}
        report['haiku_cost'] = {'input_tokens': tokens_in, 'output_tokens': tokens_out,
                                'usd': round(tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT, 5)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({'report': report, 'rows': rows}, indent=1) + '\n')
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
