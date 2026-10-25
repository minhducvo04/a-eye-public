"""Ten in-memory fixture frames through the REAL local vision service; no camera data is saved.

Run: python -m server.probe_pointer --url http://127.0.0.1:8001
The scripted talker isolates pointer cost. This is not a talker or physical-camera accuracy benchmark.
"""
import argparse
import asyncio
import base64
from io import BytesIO
import json
from statistics import median
from time import monotonic

from PIL import Image, ImageDraw

from server.agent import Session, run_turn
from server.ws2 import SceneClient
from vision.probe_ollama import iou


def fixture(index):
    image = Image.new('RGB', (1280, 960), '#ddd4c5')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 680, 1280, 960), fill='#ac8965')
    colours = [('blue', '#1965d1'), ('red', '#cf3333'), ('green', '#32894c')]
    target_index = index % 3
    target = None
    for j, (name, colour) in enumerate(colours):
        x, y = 100 + j * 390 + (index * 17) % 45, 330 + (index * 23 + j * 29) % 90
        w, h = 180 + index % 3 * 15, 230
        draw.ellipse((x + w - 25, y + 45, x + w + 65, y + 175), outline=colour, width=23)
        draw.rounded_rectangle((x, y, x + w, y + h), radius=25, fill=colour)
        draw.ellipse((x, y - 15, x + w, y + 40), fill=colour, outline='#333333', width=4)
        draw.ellipse((x + 12, y - 5, x + w - 12, y + 25), fill='#433325')
        if j == target_index:
            target = [x / 1280, (y - 15) / 960, (w + 65) / 1280, (h + 15) / 960]
    stream = BytesIO()
    image.save(stream, format='JPEG')
    return base64.b64encode(stream.getvalue()).decode('ascii'), colours[target_index][0] + ' mug', target


async def bench(url, cached=True):
    vision = SceneClient(url=url)
    rows = []
    for index in range(10):
        jpeg, name, target = fixture(index)
        scene = await vision.call('scene', dict(image_b64=jpeg)) if cached else None
        if scene is not None and scene.get('source') != 'ollama':
            raise RuntimeError('Live benchmark requires Ollama, not fake or cloud vision')
        frame = dict(seq=index + 1, jpeg_b64=jpeg)
        session = Session('pointer-bench', frame=frame, scene=scene, scene_seq=index + 1)

        class Talker:
            async def stream(self, *args, **kwargs):
                yield '{"agent":"helper","draw":[]}\nSAY: '
                yield f'Use the {name}. [POINT: {name}]'

        said_at = None
        async def emit(msg):
            nonlocal said_at
            if msg['type'] == 'say' and said_at is None:
                said_at = monotonic()
        session.emit = emit
        start = monotonic()
        messages = await run_turn(session, 'Which mug?', vision=vision, llm=Talker(), route='now')
        elapsed = (monotonic() - start) * 1000
        ops = [op for msg in messages if msg['type'] == 'draw' for op in msg['ops']]
        box = next((op['bbox'] for op in ops if op['kind'] == 'box'), None)
        score = iou(box, target) if box else 0
        row = dict(frame=index + 1, target=name, drawn=box is not None, iou=round(score, 4),
                   hit=score >= .5, added_ms=round((monotonic() - said_at) * 1000, 1),
                   first_say_ms=round((said_at - start) * 1000, 1), turn_ms=round(elapsed, 1))
        rows.append(row)
        print(json.dumps(row), flush=True)
    print(json.dumps(dict(summary=True, frames=10, hits=sum(r['hit'] for r in rows),
                          drawn=sum(r['drawn'] for r in rows),
                          wrong=sum(r['drawn'] and not r['hit'] for r in rows),
                          cached_caption=cached,
                          median_added_ms=median(r['added_ms'] for r in rows),
                          max_added_ms=max(r['added_ms'] for r in rows))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    parser.add_argument('--uncached', action='store_true', help='Include fresh caption inference in the budget')
    args = parser.parse_args()
    asyncio.run(bench(args.url, cached=not args.uncached))
