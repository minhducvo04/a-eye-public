"""Per-question router: now (live scene talker), past (Rewind search) or expert (escalate). Universal across modes.

    route(text, recent_turns=None) -> {'label': 'now'|'past'|'expert'|'unsure', 'confidence': float,
                                       'probs': {...}, 'ms': float, 'backend': str}

recent_turns: the 1 to 2 turns just before the question, oldest first, as strings or {role, text} dicts; only
the last 2 are used. 'unsure' means the top probability is under the trained threshold, or the embedder failed
('error' is then set): the server should fall back to its Haiku tool choice. Never raises.
"""
import asyncio
import json
import math
from functools import lru_cache
from pathlib import Path
from time import perf_counter

from router.data import turn_text

MODEL = Path(__file__).resolve().parent / 'model.json'


@lru_cache(maxsize=4)
def load_model(path=str(MODEL)):
    return json.loads(Path(path).read_text())


def classify(features, model):
    """Pure-Python forward pass of the exported head (logistic regression, or MLP with ReLU)."""
    values = features
    layers = model['layers']
    for index, layer in enumerate(layers):
        values = [sum(w * v for w, v in zip(row, values)) + b for row, b in zip(layer['w'], layer['b'])]
        if index < len(layers) - 1:
            values = [max(0.0, v) for v in values]
    peak = max(values)
    exps = [math.exp(v - peak) for v in values]
    total = sum(exps)
    return {label: e / total for label, e in zip(model['labels'], exps)}


def decide(probs, model, started, error=None):
    label, confidence = max(probs.items(), key=lambda kv: kv[1]) if probs else ('unsure', 0.0)
    result = {'label': label if confidence >= model['threshold'] else 'unsure', 'confidence': round(confidence, 4),
              'probs': {k: round(v, 4) for k, v in probs.items()}, 'ms': round((perf_counter() - started) * 1000, 1),
              'backend': model['backend']}
    if error:
        result['error'] = error
    return result


def _context(recent_turns):
    return [t for t in (turn_text(x).strip() for x in (recent_turns or [])[-2:]) if t]


def _embedder(backend):
    if backend == 'gemma':
        from router import embed
        return embed.inputs, lambda texts: embed.embed_many(texts, timeout=2.0)
    from router import embed_local
    return (lambda text, context: embed_local.inputs(text, context, backend)), \
        (lambda texts: embed_local.embed_many(texts, backend))


def route(text, recent_turns=None, *, model_path=str(MODEL), embed=None):
    started = perf_counter()
    model = load_model(model_path)
    try:
        inputs, call = _embedder(model['backend'])
        vectors = (embed or call)(inputs(str(text or '').strip(), _context(recent_turns)))
        return decide(classify(vectors[0] + vectors[1], model), model, started)
    except Exception as exc:          # embedder down or model files missing: let the server fall back
        return decide({}, model, started, error=type(exc).__name__)


async def aroute(text, recent_turns=None, **kwargs):
    """Same as route() without blocking the event loop."""
    return await asyncio.to_thread(route, text, recent_turns, **kwargs)
