"""Watch the app's vision while a brain benchmark runs: one grounding call every N seconds plus /api/ps.

Writes eval/brain/out/vision_watch.jsonl rows {at, ms, box_ms, loaded, evicted}. --stop-file is created when a
vision model is missing or the median of the last 3 calls is over the baseline by more than --slow (default 20%),
so the benchmark runner can stop the big model.

  python -m eval.brain.vision_watch --every 30 --baseline-ms 600 --stop-file /tmp/stop
"""
import argparse
import base64
import json
import os
import statistics
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
IMAGE = HERE.parents[0] / 'backends' / 'data' / 'desk_mug.jpg'
OLLAMA = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
APP = ('qwen3.5:9b-q4_K_M', 'qwen2.5vl:7b-q4_K_M')
PROMPT = 'Locate only "mug". Return one JSON {"label":str,"bbox_2d":[x1,y1,x2,y2]} in 0 to 1000. Use the exact label. If absent return {}.'


def probe(client, image):
    started = time.perf_counter()
    r = client.post(OLLAMA + '/api/chat', timeout=30, json={
        'model': APP[0], 'stream': False, 'think': False, 'keep_alive': '30m',
        'options': {'num_predict': 60, 'num_ctx': 4096},
        'messages': [{'role': 'user', 'content': PROMPT, 'images': [image]}]})
    data = r.json()
    loaded = [m['name'] for m in client.get(OLLAMA + '/api/ps', timeout=10).json().get('models', [])]
    return dict(ms=(time.perf_counter() - started) * 1000, box_ms=data.get('total_duration', 0) / 1e6,
                loaded=loaded, evicted=[m for m in APP if m not in loaded])


def main():
    if not os.environ.get("OLLAMA_URL"):
        raise SystemExit("Set OLLAMA_URL to your local service URL before running this script")
    parser = argparse.ArgumentParser()
    parser.add_argument('--every', type=float, default=30)
    parser.add_argument('--count', type=int, default=0, help='0 = until killed')
    parser.add_argument('--baseline-ms', type=float, default=0)
    parser.add_argument('--slow', type=float, default=.20)
    parser.add_argument('--stop-file')
    parser.add_argument('--tag', default='')
    args = parser.parse_args()
    image = base64.b64encode(IMAGE.read_bytes()).decode()
    out = HERE / 'out'
    out.mkdir(exist_ok=True)
    recent, n = [], 0
    with httpx.Client() as client:
        while not args.count or n < args.count:
            n += 1
            try:
                row = probe(client, image)
            except Exception as exc:
                row = dict(error=type(exc).__name__, evicted=[])
            row.update(at=time.strftime('%H:%M:%S'), tag=args.tag)
            if 'box_ms' in row:
                recent = (recent + [row['box_ms']])[-3:]
            slow = args.baseline_ms and len(recent) == 3 and statistics.median(recent) > args.baseline_ms * (1 + args.slow)
            row['stop'] = bool(row['evicted'] or slow)
            with (out / 'vision_watch.jsonl').open('a') as f:
                f.write(json.dumps(row) + '\n')
            print(json.dumps(row), flush=True)
            if row['stop'] and args.stop_file:
                Path(args.stop_file).write_text(json.dumps(row))
            time.sleep(args.every)


if __name__ == '__main__':
    main()
