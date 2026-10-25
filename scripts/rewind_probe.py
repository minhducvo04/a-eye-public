"""One real Rewind run: 2 synthetic scenes -> keyframe gate -> 3 tiny clips -> TwelveLabs -> search.

Usage: .venv/bin/python scripts/rewind_probe.py --env ~/Projects/a-eye/.env --query "a red ball"
Uploads cost credits: this makes at most 2 clips of about 4 to 5 seconds each. The key is read, never printed.
"""
import argparse
import asyncio
import base64
import io
import os
import random
import sys
from pathlib import Path
from time import monotonic

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.keyframes import KeyframeGate, signature  # noqa: E402
from server.rewind import Rewind  # noqa: E402


def load_key(path):
    for line in Path(path).expanduser().read_text().splitlines():
        if line.startswith('TWELVELABS_API_KEY='):
            os.environ['TWELVELABS_API_KEY'] = line.split('=', 1)[1].strip().strip('"\'')
    value = os.environ.get('TWELVELABS_API_KEY', '')
    print(f'key: {len(value)} chars, prefix {value[:4]}...')


def scene(kind, t):
    image = Image.new('RGB', (640, 480), (182, 140, 96))      # wooden desk colour
    draw = ImageDraw.Draw(image)
    for y in range(0, 480, 24):
        draw.line([(0, y), (640, y + 6)], fill=(165, 124, 84), width=3)
    dx = int(6 * ((t * 3) % 2 - 1))                            # small hand-held wobble
    if kind == 'ball':
        draw.ellipse([110 + dx, 30, 530 + dx, 450], fill=(210, 20, 25), outline=(120, 0, 0), width=6)
        draw.ellipse([200 + dx, 90, 290 + dx, 170], fill=(250, 140, 140))
    elif kind == 'mug':
        draw.rectangle([0, 0, 640, 480], fill=(235, 235, 230))
        draw.rectangle([170 + dx, 60, 430 + dx, 440], fill=(30, 80, 200))
        draw.arc([380 + dx, 140, 560 + dx, 340], -90, 90, fill=(30, 80, 200), width=34)
        draw.ellipse([170 + dx, 30, 430 + dx, 90], fill=(60, 30, 10))
    for _ in range(200):                                       # sensor noise
        draw.point((random.randrange(640), random.randrange(480)), fill=(150, 110, 70))
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=80)
    return base64.b64encode(out.getvalue()).decode()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', default='~/Projects/a-eye/.env')
    parser.add_argument('--query', action='append', default=None)
    args = parser.parse_args()
    load_key(args.env)
    clock = [0.0]
    rewind = Rewind('rewind-probe', clock=lambda: clock[0])
    rewind.set_on(True)
    gate = KeyframeGate(heartbeat_s=1000)
    timeline = [(0, 'ball'), (8, 'mug')]
    for step in range(int(14 * 4)):
        t = step / 4
        kind = [k for start, k in timeline if t >= start][-1]
        jpeg = scene(kind, t)
        clock[0] = t
        rewind.add_frame(jpeg)
        scan, reason = gate.should_scan(signature(jpeg), t)
        if scan:
            rewind.keyframe(reason)
            print(f'keyframe t={t:.2f}s reason={reason}')
    rewind.close()
    print('clips:', [(c.start_s, c.end_s, len(c.frames)) for c in rewind.clips])
    started = monotonic()
    await rewind.flush(wait=False)
    upload_s = monotonic() - started
    print(f'index: {rewind.index_id}; uploads: {[(t, round(ms)) for t, ms in rewind.uploads]}; '
          f'upload total {upload_s:.1f}s')
    await asyncio.gather(*rewind.waits)
    index_s = monotonic() - started - upload_s
    print(f'indexed: {[(c.task_id, c.status) for c in rewind.clips]}; indexing wait {index_s:.1f}s')
    for query in args.query or ['a red ball', 'a blue coffee mug']:
        started = monotonic()
        rows = await rewind.client.search(rewind.index_id, query, video_ids=[c.video_id for c in rewind.clips])
        hits = await rewind.search(query)
        print(f'search {query!r}: {(monotonic() - started) * 1000:.0f} ms (two calls); raw rows:',
              [(r.get('video_id'), r.get('start'), r.get('end'), r.get('rank'), r.get('score')) for r in rows])
        print('  raw first row:', rows[:1])
        print('  call-time hits:', [{k: v for k, v in h.items() if k != 'frame'} for h in hits])
        print('  answer:', (await rewind.answer(f'when did I put down {query}?'))[0])


if __name__ == '__main__':
    asyncio.run(main())
