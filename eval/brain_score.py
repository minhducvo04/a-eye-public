"""Score one brain turn against hand-written expectations. Standard library only.

The runner turns a raw reply plus the messages run_turn emitted into `facts`:
    header:    the JSON header the model wrote, or None
    format_ok: the header strictly matches brain_turn and SAY speech follows it
    drawn:     scene ids the server actually drew on
    speech:    the helper's spoken text, before any shopper lines
    market:    the market category the turn searched, or None

A case's `expect` may hold:
    targets: ids the reply may draw on. [] means draw nothing (the thing is not visible,
             or the part is unsupported). Missing means pointing is not scored.
    market:  the expected market category, or null for none. Missing means null.
"""
import math
import re

SENTENCE_END = re.compile(r'[.!?]+(?=\s|$)')
MAX_SENTENCES = 2
CHECKS = ('format_ok', 'grounded', 'target_ok', 'market_ok', 'brief')


def sentence_count(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    return max(1, len(SENTENCE_END.findall(text)))


def header_draws(header: dict | None) -> list:
    draws = header.get('draw') if isinstance(header, dict) else None
    if not isinstance(draws, list):
        return []
    return [d for d in draws if not (isinstance(d, dict) and d.get('kind') == 'clear')]


def is_grounded(header: dict | None, scene_ids: set) -> bool:
    """The model named only real scene ids: no invented ids, coordinates or targetless draws."""
    def known(value):
        return isinstance(value, str) and value in scene_ids

    for draw in header_draws(header):
        if not isinstance(draw, dict) or not known(draw.get('target')):
            return False
        if 'from_target' in draw and not known(draw['from_target']):
            return False
    return True


def targets_ok(drawn: list, allowed: list) -> bool:
    if not allowed:
        return not drawn
    return bool(drawn) and all(t in allowed for t in drawn)


def score(case: dict, facts: dict) -> dict:
    """One result row. A pass needs speech, grounding, the right targets and the right market."""
    expect = case.get('expect', {})
    scene_ids = {obj['id'] for obj in case['scene']['objects']}
    speech = facts.get('speech', '')
    row = dict(case_id=case['case_id'], tag=case.get('tag', ''), provider=facts.get('provider', ''),
               first_token_ms=round(facts.get('first_token_ms', 0)), turn_ms=round(facts.get('turn_ms', 0)),
               format_ok=bool(facts.get('format_ok')), grounded=is_grounded(facts.get('header'), scene_ids),
               target_ok=targets_ok(facts.get('drawn', []), expect['targets']) if 'targets' in expect else None,
               market_ok=facts.get('market') == expect.get('market'),
               brief=0 < sentence_count(speech) <= MAX_SENTENCES, say=speech, error=facts.get('error', ''))
    row['passed'] = bool(speech.strip()) and row['grounded'] and row['market_ok'] and row['target_ok'] is not False
    return row


def percentile(values: list, pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return float(ordered[index])


def rate(rows: list, key: str) -> float | None:
    scored = [r[key] for r in rows if r[key] is not None]
    return sum(scored) / len(scored) if scored else None


def summarize(rows: list) -> dict:
    first = [r['first_token_ms'] for r in rows]
    summary = dict(n=len(rows), providers=sorted({r['provider'] for r in rows}),
                   **{k: rate(rows, k) for k in CHECKS}, passed=rate(rows, 'passed'),
                   first_p50_ms=percentile(first, 50), first_p90_ms=percentile(first, 90),
                   turn_p50_ms=percentile([r['turn_ms'] for r in rows], 50))
    tags = sorted({r['tag'] for r in rows})
    summary['by_tag'] = {t: rate([r for r in rows if r['tag'] == t], 'passed') for t in tags}
    return summary


def pct(value: float | None) -> str:
    return '-' if value is None else f'{value * 100:.0f}%'


def markdown(results: dict) -> str:
    """results maps a model label to its summarize() output."""
    lines = ['| model | n | pass | format | grounded | target | market | brief '
             '| first token p50 | p90 | turn p50 | provider |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for label, s in results.items():
        lines.append(f"| {label} | {s['n']} | {pct(s['passed'])} | {pct(s['format_ok'])} | {pct(s['grounded'])} "
                     f"| {pct(s['target_ok'])} | {pct(s['market_ok'])} | {pct(s['brief'])} "
                     f"| {s['first_p50_ms']:.0f} ms | {s['first_p90_ms']:.0f} ms | {s['turn_p50_ms']:.0f} ms "
                     f"| {', '.join(s['providers'])} |")
    tags = sorted({t for s in results.values() for t in s['by_tag']})
    lines += ['', '| pass by tag | ' + ' | '.join(results) + ' |', '|---' * (len(results) + 1) + '|']
    for tag in tags:
        lines.append(f'| {tag} | ' + ' | '.join(pct(s['by_tag'].get(tag)) for s in results.values()) + ' |')
    return '\n'.join(lines) + '\n'
