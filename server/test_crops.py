import base64
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from server.agent import planner_crop
from server.crops import MAX_CROP_SIDE, crop_b64, pick_target


def frame_b64(w=1280, h=960):
    out = BytesIO()
    Image.new('RGB', (w, h), (120, 30, 30)).save(out, format='JPEG')
    return base64.b64encode(out.getvalue()).decode()


def test_crop_is_small_and_never_the_full_frame():
    data = crop_b64(frame_b64(), [0.4, 0.4, 0.1, 0.1])
    with Image.open(BytesIO(base64.b64decode(data))) as img:
        assert max(img.size) <= MAX_CROP_SIDE and img.size[0] < 1280
    assert crop_b64(frame_b64(), [0.0, 0.0, 1.0, 1.0]) is None  # a whole-frame "crop" is refused


def test_pick_target_prefers_the_named_object():
    objs = [{'id': 'o1', 'label': 'knife', 'bbox': [0, 0, .1, .1]}, {'id': 'o2', 'label': 'red tomato', 'bbox': [.5, .5, .1, .1]}]
    assert pick_target(objs, 'where do I cut the tomato?')['id'] == 'o2'
    assert pick_target(objs, 'what now?')['id'] == 'o1'


def test_planner_gets_a_crop_only_when_allowed(monkeypatch):
    scene = {'objects': [{'id': 'o1', 'label': 'tomato', 'bbox': [.4, .4, .1, .1]}]}
    session = SimpleNamespace(camera=True, frame={'seq': 7, 'jpeg_b64': frame_b64()})
    extra = planner_crop(session, scene, 7, 'the tomato')
    assert len(extra['images']) == 1 and len(extra['images'][0]) < 200_000
    assert planner_crop(session, scene, 6, 'x') == {}                        # scene of an older frame
    assert planner_crop(SimpleNamespace(camera=False, frame=session.frame), scene, 7, 'x') == {}
    monkeypatch.setenv('LOCAL_ONLY', '1')
    assert planner_crop(session, scene, 7, 'x') == {}


def test_router_puts_crops_before_text_and_drops_oversized_images():
    from server.llm import _content
    blocks = _content('what is this?', ['AAAA'])
    assert blocks[0]['type'] == 'image' and blocks[-1] == {'type': 'text', 'text': 'what is this?'}
    assert _content('hi', None) == 'hi'


def test_planner_gets_no_crop_in_private_mode():
    """Private mode (hello.private) must block crops like LOCAL_ONLY does."""
    from server.privacy import PRIVATE
    scene = {'objects': [{'id': 'o1', 'label': 'tomato', 'bbox': [.4, .4, .1, .1]}]}
    session = SimpleNamespace(camera=True, frame={'seq': 7, 'jpeg_b64': frame_b64()})
    token = PRIVATE.set(True)
    try:
        assert planner_crop(session, scene, 7, 'the tomato') == {}
    finally:
        PRIVATE.reset(token)
    assert planner_crop(session, scene, 7, 'the tomato') != {}
