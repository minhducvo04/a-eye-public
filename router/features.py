"""Embed rows into features: [embed(question), embed(context + question)]. Cached as .npy in a cache dir.

Usage: python -m router.features --backend gemma_local|gemma|minilm --cache DIR
gemma = EmbeddingGemma on the PC's Ollama; gemma_local and minilm run on the Mac CPU (router/embed_local.py).
"""
import argparse
import hashlib
import json
from pathlib import Path

from router.data import TRAIN, heldout, load


def rows_key(rows):
    return hashlib.sha1(json.dumps([(r['text'], r['context']) for r in rows]).encode()).hexdigest()[:12]


def featurize(rows, backend, batch=32):
    if backend == 'gemma':
        from router.embed import embed_many, inputs
        import httpx
        client = httpx.Client(timeout=120)
        call = lambda texts: embed_many(texts, client=client)
        texts = [t for r in rows for t in inputs(r['text'], r['context'])]
    else:
        from router import embed_local
        call = lambda texts: embed_local.embed_many(texts, backend)
        texts = [t for r in rows for t in embed_local.inputs(r['text'], r['context'], backend)]
    vectors = []
    for start in range(0, len(texts), batch):
        vectors += call(texts[start:start + batch])
    return [vectors[2 * i] + vectors[2 * i + 1] for i in range(len(rows))]


def cached(rows, backend, cache):
    import numpy as np
    path = Path(cache) / f'{backend}-{rows_key(rows)}.npy'
    if path.exists():
        return np.load(path)
    matrix = np.array(featurize(rows, backend), dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, matrix)
    return matrix


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', default='gemma')
    parser.add_argument('--cache', required=True)
    args = parser.parse_args()
    for name, rows in (('train', load(TRAIN)), ('heldout', heldout())):
        print(name, cached(rows, args.backend, args.cache).shape)
