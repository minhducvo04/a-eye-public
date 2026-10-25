#!/usr/bin/env python3
"""Run the existing 16 public-image cases; retain only scores and timings in stdout."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if not os.environ.get('GEMINI_API_KEY'):
        print('not configured')
        return 2
    from server import privacy
    from eval.backends.run import load_cases, image_b64, iou, pct, DATA
    from vision.gemini import GeminiBackend, native_box
    if privacy.local_only():
        print('Private: Google blocked')
        return 2
    cases = load_cases()
    if len(cases) != 16 or any(not (DATA / c['image']).is_file() for c in cases):
        print('benchmark fixtures unavailable: run eval/backends/fetch_images.py')
        return 2
    privacy.set_call({'google': True})
    backend = GeminiBackend()
    timings, hits, failures = [], 0, 0
    for case in cases:
        encoded, _ = image_b64(case)
        prompt = ('Conversation: ' + json.dumps(case['history']) + '\nQuestion: ' + case['question'] +
                  '\nLocate the single object the question refers to. Return JSON '
                  '{"label":str,"box_2d":[y_min,x_min,y_max,x_max]} in 0 to 1000 units. '
                  'If absent or uncertain return {}. Do not guess.')
        start = time.perf_counter()
        answer = backend.request({'image_b64': encoded}, prompt)
        elapsed = time.perf_counter() - start
        timings.append(elapsed)
        box = native_box(answer.get('box_2d')) if isinstance(answer, dict) else None
        overlap = iou([box[0], box[1], box[0] + box[2], box[1] + box[3]], case['box']) if box else 0
        hits += overlap >= .3
        failures += answer is None
        error = f' error={backend.last_error}' if backend.last_error else ''
        print(f"{case['id']}: hit={overlap >= .3} iou={overlap:.3f} latency={elapsed:.3f}s{error}")
    print(f'hits={hits}/16 failures={failures} median={pct(timings, .5):.3f}s p95={pct(timings, .95):.3f}s')
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
