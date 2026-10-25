"""Run against old/new servers: python scripts/keyframe_bench.py --log server.jsonl.

Only synthetic Pillow frames leave this process. Server stdout supplies exact
scene_scan reasons; older servers have no reasons, so these are reported unknown.
"""
import argparse
import asyncio
import base64
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from PIL import Image, ImageDraw, ImageEnhance, ImageChops


def synthetic_frame(index):
    scene = min(index // 100, 2)
    image = Image.new('RGB', (320, 240), (35 + scene * 85,) * 3)
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 50, 120, 150), fill=(200 - scene * 75,) * 3)
    draw.ellipse((190, 90, 260, 160), fill=(80 + scene * 60,) * 3)
    image = ImageChops.offset(image, index % 5 - 2, index % 3 - 1)
    image = ImageEnhance.Brightness(image).enhance(1 + (index % 7 - 3) * .01)
    output = io.BytesIO()
    image.save(output, format='JPEG')
    return base64.b64encode(output.getvalue()).decode()


async def run(args):
    import websockets
    session = 'keyframe-bench-' + uuid4().hex
    log_offset = args.log.stat().st_size if args.log and args.log.exists() else 0
    scans = []
    async with websockets.connect(args.url) as ws:
        async def send(kind, **fields):
            await ws.send(json.dumps(dict(v=2, type=kind, session_id=session, **fields)))
        await send('hello', pack='general', camera=True)
        async def receive():
            async for raw in ws:
                msg = json.loads(raw)
                if msg['type'] == 'metrics' and 'seq' in msg and 'vlm_ms' in msg:
                    scans.append(msg)
                if msg['type'] == 'error':
                    print('Server error:', msg['code'])
        receiver = asyncio.create_task(receive())
        started = monotonic()
        try:
            for i in range(300):
                await asyncio.sleep(max(0, started + i / 5 - monotonic()))
                await send('frame', seq=i, ts_ms=int(time() * 1000), w=320, h=240,
                           jpeg_b64=synthetic_frame(i))
            await asyncio.sleep(max(0, started + 60 - monotonic()))
            # Drain the final in-flight scan before camera-off cancels it.
            await asyncio.sleep(args.drain_s)
            await send('camera', on=False)
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
    reasons = Counter()
    if args.log:
        with args.log.open('rb') as source:
            source.seek(log_offset)
            for line in source:
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if row.get('event') == 'scene_scan' and row.get('session') == hashlib.sha256(session.encode()).hexdigest()[:12]:
                    reasons[row['reason']] += 1
    print(json.dumps(dict(session=session, clip_s=60, frames=300, scans_per_minute=len(scans),
                          scan_reasons=dict(reasons) or {'unknown': len(scans)},
                          vlm_ms=sum(m['vlm_ms'] for m in scans)), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='ws://127.0.0.1:8000/ws2')
    parser.add_argument('--log', type=Path, help='Server JSON stdout log for exact reasons')
    parser.add_argument('--drain-s', type=float, default=26, help='Wait for the last scan (server timeout is 25 s)')
    asyncio.run(run(parser.parse_args()))
