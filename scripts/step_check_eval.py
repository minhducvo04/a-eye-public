#!/usr/bin/env python3
"""Local valve evaluation. No photos are copied; stdout/report contain only metrics and hashes.

python scripts/step_check_eval.py --synthetic
python scripts/step_check_eval.py ~/a-eye-private/valves --report /tmp/valve-eval.json
"""
import argparse
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.step_check import Checker, Embedding, LocalImageEncoder, Qwen, Verdict

SEED = 20261024
LABELS = ('open', 'closed')
THRESHOLDS = (.9, .95, .99)


def split(folder, seed=SEED):
    rng = random.Random(seed)
    tune, held = [], []
    seen = set()
    for label in LABELS:
        paths = sorted(p for p in (folder / label).iterdir()
                       if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and p.is_file())
        if len(paths) < 4:
            raise ValueError('need at least four photos in each of open/ and closed/')
        # Reject identical copies across either class/split before inference. Hashing is not tuning.
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in seen:
                raise ValueError('duplicate photo; use independent captures')
            seen.add(digest)
        rng.shuffle(paths)
        cut = len(paths) // 2
        tune.extend((label, p) for p in paths[:cut])
        held.extend((label, p) for p in paths[cut:])
    return tune, held


def summary(rows, threshold):
    matrix = {label: dict(open=0, closed=0, unsure=0) for label in LABELS}
    for label, result, _ in rows:
        predicted = result.state if result.confidence >= threshold else 'unsure'
        matrix[label][predicted] += 1
    latencies = sorted(row[2] for row in rows)
    # Nearest-rank p95, and ordinary median p50. Cold first call is included.
    import math
    return dict(false_closed=matrix['open']['closed'], matrix=matrix, threshold=threshold,
                p50_ms=round(statistics.median(latencies), 3),
                p95_ms=round(latencies[max(0, math.ceil(.95 * len(latencies)) - 1)], 3))


def ranking(stats):
    return (stats['false_closed'], -stats['matrix']['closed']['closed'],
            -stats['matrix']['open']['open'], stats['p95_ms'])


async def measure(method, samples, refs):
    checker = Checker(method, threshold=min(THRESHOLDS))
    rows = []
    for label, path in samples:
        raw = path.read_bytes()
        start = perf_counter()
        result = await checker.check(raw, refs['closed'], refs['open'])
        rows.append((label, result, (perf_counter() - start) * 1000))
    return rows


async def evaluate(folder, methods, *, synthetic=False):
    tune, held = split(folder)
    # Exactly one reference per state, selected only from tune. Never score a reference against itself.
    references = {label: next(p for y, p in tune if y == label) for label in LABELS}
    refs = {label: p.read_bytes() for label, p in references.items()}
    tuning = [(label, p) for label, p in tune if p not in references.values()]
    frozen, tuning_stats = {}, {}
    # Freeze every threshold before any held-out image reaches either method.
    for name, method in methods.items():
        rows = await measure(method, tuning, refs)
        options = [summary(rows, t) for t in THRESHOLDS]
        best = min(options, key=lambda s: (*ranking(s), -s['threshold']))
        frozen[name] = best['threshold']
        tuning_stats[name] = best
    measured = {}
    for name, method in methods.items():
        measured[name] = summary(await measure(method, held, refs), frozen[name])
    viable = [name for name, stats in measured.items()
              if stats['false_closed'] == 0 and stats['matrix']['closed']['closed'] > 0]
    selected = min(viable, key=lambda n: ranking(measured[n])) if viable else None
    return dict(synthetic=synthetic, seed=SEED, tune_count=len(tune), tune_scored=len(tuning),
                held_out_count=len(held), selected=selected, methods=measured, tuning=tuning_stats,
                qwen_model=getattr(methods['qwen'], 'model', ''),
                encoder_path=getattr(methods['embedding'].encoder, 'path', ''),
                split_hashes={name: [hashlib.sha256(p.read_bytes()).hexdigest() for _, p in samples]
                              for name, samples in (('tune', tune), ('held_out', held))})


def synthetic_fixture(folder):
    """Draw prop valves outside the repo; fixtures are not human photos or model evidence."""
    from PIL import Image, ImageDraw
    for label in LABELS:
        directory = folder / label
        directory.mkdir(parents=True)
        for index in range(6):
            image = Image.new('RGB', (64, 64), (20 + index * 4, 25, 30))
            draw = ImageDraw.Draw(image)
            draw.rectangle((3, 28, 61, 36), fill=(130, 130, 130))
            handle = (28, 10, 36, 54) if label == 'closed' else (10, 28, 54, 36)
            draw.rectangle(handle, fill=(240, 20, 20))
            image.save(directory / f'{index}.png')


def fixture_features(raw):
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as image:
        image = image.convert('RGB')
        points = [(x, y) for y in range(image.height) for x in range(image.width)
                  if (lambda rgb: rgb[0] > 180 and rgb[1] < 90 and rgb[2] < 90)(image.getpixel((x, y)))]
    if not points:
        return [0., 0.]
    return [float(max(p[0] for p in points) - min(p[0] for p in points)),
            float(max(p[1] for p in points) - min(p[1] for p in points))]


def fixture_methods():
    import httpx
    class Encoder:
        async def encode(self, raw):
            return fixture_features(raw)
    def reply(request):
        raw = base64.b64decode(json.loads(request.content)['messages'][0]['images'][0])
        width, height = fixture_features(raw)
        answer = dict(state='closed' if height > width else 'open', confidence=.99)
        return httpx.Response(200, json={'message': {'content': json.dumps(answer)}})
    return {'qwen': Qwen(url='http://127.0.0.1:11434', model='SYNTHETIC-STAND-IN',
                         transport=httpx.MockTransport(reply)), 'embedding': Embedding(Encoder())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, nargs='?')
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if bool(args.folder) == args.synthetic:
        parser.error('choose a labeled folder OR --synthetic')
    try:
        if args.synthetic:
            with tempfile.TemporaryDirectory(prefix='valve-fixture-') as directory:
                folder = Path(directory)
                synthetic_fixture(folder)
                report = asyncio.run(evaluate(folder, fixture_methods(), synthetic=True))
        else:
            report = asyncio.run(evaluate(args.folder, {'qwen': Qwen(), 'embedding': Embedding(LocalImageEncoder())}))
    except Exception:
        parser.exit(2, 'Evaluation failed: check the folder layout, unique photos and local configuration.\n')
    # Dangerous errors first, even before the synthetic disclaimer and confusion matrices.
    for name, stats in report['methods'].items():
        print(f"{name}: false-closed={stats['false_closed']}")
    print('SYNTHETIC PIPELINE ONLY; no model accuracy claim.' if args.synthetic else 'REAL PHOTO EVALUATION')
    print(f"seed={SEED}; tune={report['tune_count']} ({report['tune_scored']} scored + 2 references); "
          f"held-out={report['held_out_count']}")
    for name, stats in report['methods'].items():
        print(f"{name}: threshold={stats['threshold']} p50={stats['p50_ms']}ms p95={stats['p95_ms']}ms")
        print('actual \\ predicted: open closed unsure')
        for label in LABELS:
            print(label, *(stats['matrix'][label][c] for c in ('open', 'closed', 'unsure')))
    print('Measured choice:', report['selected'] or 'NONE (gate stays locked)')
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
