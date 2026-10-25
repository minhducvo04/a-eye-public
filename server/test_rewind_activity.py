"""Rehearsal finding: the keys moment never became a Rewind clip, and the hit frame showed the hand, not the keys."""
import asyncio
import base64
from io import BytesIO
from shutil import which

import pytest

from PIL import Image, ImageDraw

from server import rewind
from server.keyframes import local_change, signature
from server.rewind_fake import FakeTwelveLabs


def jpeg(arm=False, keys=False, shift=0):
    img = Image.new('RGB', (480, 360), (196, 170, 132))
    d = ImageDraw.Draw(img)
    for cx in (110 + shift, 240 + shift, 370 + shift):
        d.rectangle([cx - 40, 150, cx + 40, 250], fill=(176, 52, 46))
    if keys:
        d.ellipse([214, 272, 246, 304], outline=(120, 120, 128), width=5)
    if arm:
        d.polygon([(480, 150), (480, 330), (330, 300), (300, 240)], fill=(224, 172, 140))
    out = BytesIO()
    img.save(out, format='JPEG')
    return base64.b64encode(out.getvalue()).decode()


def test_local_change_sees_an_arm_but_not_jitter():
    base = signature(jpeg())
    assert local_change(base, signature(jpeg(arm=True)))
    assert not local_change(base, signature(jpeg(shift=2)))
    assert not local_change(None, base)


@pytest.mark.skipif(which('ffmpeg') is None, reason='needs ffmpeg to encode the clip')
def test_activity_opens_a_clip_and_the_check_tries_the_settled_frame():
    class Vision:
        def __init__(self):
            self.frames = []

        async def call(self, op, body):
            self.frames.append(body['image_b64'])
            # Only the settled frame (keys, no arm) shows the keys clearly.
            found = body['image_b64'] == base64.b64encode(base64.b64decode(settled)).decode()
            return {'caption': '', 'ms': 1, 'objects': [{'id': 'o1', 'label': 'keys', 'bbox': [.4, .7, .1, .1],
                                                          'conf': .7}] if found else []}

    clock = [0.0]
    r = rewind.Rewind('s', client=FakeTwelveLabs(), clock=lambda: clock[0])
    r.set_on(True)
    settled = jpeg(keys=True)
    frames = [(t, jpeg()) for t in (0.0, 1.0, 2.0)] + [(3.0, jpeg(arm=True, keys=True)), (3.5, jpeg(arm=True, keys=True))] \
        + [(t, settled) for t in (4.0, 5.0, 6.0, 7.0)]
    for t, frame in frames:
        clock[0] = t
        r.add_frame(frame)
        r.observe(signature(frame), 'first' if t == 0.0 else None)
    clock[0] = 20.0
    r.add_frame(settled)
    asyncio.run(r.flush(wait=True))
    assert len(r.clips) == 2 and any(abs(c.key_s - 3.0) < 0.01 for c in r.clips)
    vision = Vision()
    line, hit = asyncio.run(r.answer('where are my keys?', vision=vision))
    assert hit['sure'] and abs(hit['t_s'] - 3.0) < 0.01 and len(vision.frames) >= 2


def test_past_question_without_rewind_answers_from_scene_memory():
    """Memory pack: 'where are my keys' said 'Rewind is off' even when the camera had just seen the keys."""
    from time import monotonic
    from types import SimpleNamespace
    from server.keyframes import SceneMemory
    memory = SceneMemory()
    memory.add({'caption': 'Keys in front of the middle red cup.', 'objects': [{'label': 'keys'}]}, monotonic() - 40)
    memory.add({'caption': 'Three red cups on a table.', 'objects': [{'label': 'cup'}]}, monotonic() - 5)
    session = SimpleNamespace(scene_memory=memory, camera=True, rewind=None)
    result = asyncio.run(rewind.search_past(session, 'Where are my keys?'))
    assert result['say'].startswith('I last saw your keys about 40 seconds ago') and 'middle red cup' in result['say']
    assert 'Rewind' not in result['say']
    nothing = asyncio.run(rewind.search_past(SimpleNamespace(scene_memory=SceneMemory(), camera=True, rewind=None),
                                             'where are my glasses?'))
    assert nothing['say'] == "Rewind is off, so there's no recording to check."
