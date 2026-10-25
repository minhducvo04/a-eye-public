"""Local memory with fakes: temp folder, bag-of-words embedder, fake person check. No network, no ONNX."""
import asyncio
import base64
import hashlib
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import memory as mem
from server.memory import Store, Writer, find


def jpeg(color=(180, 140, 100)):
    out = io.BytesIO()
    Image.new('RGB', (320, 240), color).save(out, format='JPEG')
    return base64.b64encode(out.getvalue()).decode()


FRAME = jpeg()
KEYS = dict(id='k', label='keys', bbox=[0.12, 0.5, 0.1, 0.1], conf=0.8)
CUPS = [dict(id=f'c{i}', label='red cup', bbox=[0.1 + 0.3 * i, 0.45, 0.15, 0.2], conf=0.8) for i in range(3)]


class Words:
    """Deterministic stand-in for EmbeddingGemma: hashed bag of words."""
    calls = 0

    def embed(self, texts, query=False):
        Words.calls += 1
        out = []
        for text in texts:
            vec = [0.0] * 64
            for w in mem.words(text):
                vec[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(vec)
        return out


class Person:
    def __init__(self, result=False, fail=False):
        self.result, self.fail, self.calls = result, fail, 0

    async def __call__(self, jpeg_b64, scene=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError('box down')
        return self.result


@pytest.fixture
def store(tmp_path):
    clock = [1_800_000_000.0]
    s = Store('me', base=tmp_path, cipher=mem.Cipher(''), clock=lambda: clock[0])
    s.clock_ref = clock
    return s


def write(store, scene, person=None):
    writer = Writer(store, 'call-1', embedder=Words(), person_check=person or Person())
    return writer, asyncio.run(writer.observe(scene, FRAME))


def test_only_changed_notes_with_one_embedding_each(store):
    writer = Writer(store, 'call-1', embedder=Words(), person_check=Person())
    scene = dict(caption='a desk with three red cups', objects=CUPS)
    Words.calls = 0
    for _ in range(5):                                   # heartbeats with the same note
        asyncio.run(writer.observe(scene, FRAME))
    asyncio.run(writer.observe(dict(scene, caption='keys next to the left cup'), FRAME))
    records = store.records()
    assert len(records) == 2 and writer.skipped == {'unchanged': 4}
    assert Words.calls == 2 and all(r['emb'] for r in records)       # one embedding per stored note


def test_crops_only_for_tagged_objects(store):
    store.set_tag('keys', home='blue bowl')
    _, record = write(store, dict(caption='keys by the left cup', objects=[KEYS] + CUPS))
    assert list(record['crops']) == ['keys'] and len(list((store.dir / 'crops').iterdir())) == 1
    entry = record['tags'][0]
    assert entry['near']['label'] == 'red cup' and entry['near']['bbox'] == CUPS[0]['bbox']
    with Image.open(io.BytesIO(store.load_crop(record['crops']['keys']))) as crop:
        assert max(crop.size) <= mem.CROP_SIDE           # a small crop, never the full frame
    _, untagged = write(store, dict(caption='a stapler on the desk', objects=[dict(KEYS, label='stapler')]))
    assert untagged['crops'] == {}


@pytest.mark.parametrize('person,reason', [(Person(result=True), 'person'),
                                           (Person(fail=True), 'person_check_unavailable')])
def test_face_skip_never_stores_a_crop_with_a_person(store, person, reason):
    store.set_tag('keys')
    _, record = write(store, dict(caption='hand holding keys', objects=[KEYS]), person)
    assert record['crops'] == {} and record['skipped'] == reason
    assert not (store.dir / 'crops').exists() or not list((store.dir / 'crops').iterdir())


def test_person_label_in_scene_skips_without_asking_the_box(store):
    store.set_tag('keys')
    check = mem.PersonCheck(box=object())                # would fail if called
    writer = Writer(store, 'c', embedder=Words(), person_check=check)
    record = asyncio.run(writer.observe(dict(caption='a man holding keys',
                                             objects=[KEYS, dict(KEYS, id='p', label='person')]), FRAME))
    assert record['crops'] == {} and record['skipped'] == 'person'


@pytest.mark.parametrize('detections', [{}, '', [{}], [{'label': ''}], None])
def test_person_check_rejects_unknown_detector_response(detections):
    class Box:
        async def detect(self, jpeg_b64):
            return detections

    with pytest.raises(ValueError, match='person detector'):
        asyncio.run(mem.PersonCheck(box=Box())(FRAME))


@pytest.mark.parametrize('detections,expected', [([], False), ([{'label': 'keys'}], False),
                                               ([{'label': 'person'}], True)])
def test_person_check_accepts_valid_detector_response(detections, expected):
    class Box:
        async def detect(self, jpeg_b64):
            return detections

    assert asyncio.run(mem.PersonCheck(box=Box())(FRAME)) is expected


def test_post_condition_no_stored_crop_came_from_a_person_frame(store):
    store.set_tag('keys')
    seen = []

    class Recording(Person):
        async def __call__(self, jpeg_b64, scene=None):
            person = 'person' in (scene or {}).get('caption', '')
            seen.append(person)
            return person

    writer = Writer(store, 'c', embedder=Words(), person_check=Recording())
    for i, caption in enumerate(['keys on desk', 'person picks up keys', 'keys in bowl', 'person waves']):
        asyncio.run(writer.observe(dict(caption=caption, objects=[dict(KEYS, bbox=[0.1 + i / 10, 0.5, 0.1, 0.1])]),
                                   FRAME))
    for record in store.records():
        assert bool(record['crops']) == ('person' not in record['note'])


def test_encrypted_at_rest(tmp_path):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    store = Store('me', base=tmp_path, cipher=mem.Cipher(key))
    store.set_tag('keys')
    write(store, dict(caption='keys under the left cup', objects=[KEYS] + CUPS))
    raw = (store.dir / 'log.jsonl').read_bytes() + (store.dir / 'tags.json').read_bytes()
    assert b'keys' not in raw and b'cup' not in raw
    crop = next((store.dir / 'crops').iterdir()).read_bytes()
    assert not crop.startswith(b'\xff\xd8')              # not a readable JPEG on disk
    assert store.records()[0]['note'] == 'keys under the left cup'
    assert mem.status(store)['encrypted'] is True
    assert mem.status(Store('me', base=tmp_path, cipher=mem.Cipher('')))['encrypted'] is False


def test_find_names_place_home_time_and_live_arrow(store):
    store.set_tag('keys', home='blue bowl')
    write(store, dict(caption='keys in the blue bowl', objects=[KEYS, dict(id='b', label='blue bowl',
                                                                           bbox=[0.1, 0.45, 0.2, 0.2], conf=0.9)]))
    store.clock_ref[0] += 3600
    write(store, dict(caption='keys next to the left cup', objects=[KEYS] + CUPS))
    store.clock_ref[0] += 600
    live = dict(caption='three red cups', objects=[dict(c, id=f'live{i}', bbox=[c['bbox'][0] + 0.02] + c['bbox'][1:])
                                                   for i, c in enumerate(CUPS)])
    result = asyncio.run(find(store, 'Where are my keys?', embedder=Words(), scene=live))
    assert result['found'] and result['how'] == 'tag' and result['place'] == 'under the left red cup'
    assert result['crop'] and result['box'] and result['live'] == dict(object_id='live0', bbox=live['objects'][0]['bbox'],
                                                                       what='place')
    assert result['say'] == ("I last saw your keys under the left red cup, 10 minutes ago, at "
                             f"{time.strftime('%I:%M %p', time.localtime(store.clock_ref[0] - 600)).lstrip('0')}. "
                             "Their home is the blue bowl. I'm pointing at that spot.")
    assert result['count'] == 2 and result['count_item'] == 2


def test_find_home_first_when_it_is_there_and_fuzzy_fallback(store):
    store.set_tag('keys', home='blue bowl')
    store.set_tag('blue bowl', kind='place')
    write(store, dict(caption='keys in the bowl by the door',
                      objects=[KEYS, dict(id='b', label='blue bowl', bbox=[0.1, 0.45, 0.2, 0.2], conf=0.9)]))
    result = asyncio.run(find(store, 'where did I put my keys', embedder=Words()))
    assert result['say'].startswith('Your keys are in the blue bowl, where they live.')
    write(store, dict(caption='glasses folded on the bookshelf', objects=[]))
    fuzzy = asyncio.run(find(store, 'have you seen my glasses?', embedder=Words()))
    assert fuzzy['found'] and fuzzy['how'] == 'similar' and 'bookshelf' in fuzzy['say']
    none = asyncio.run(find(store, 'where is my passport?', embedder=Words()))
    assert none['found'] is False and "haven't seen" in none['say']


def test_forget_item_today_everything_and_retention(store):
    store.set_tag('keys')
    write(store, dict(caption='keys on the desk', objects=[KEYS]))
    write(store, dict(caption='a mug on the desk', objects=[dict(KEYS, label='mug')]))
    assert store.count() == 2 and store.count('keys') == 1
    assert store.forget_item('keys') == 1
    assert store.count('keys') == 0 and store.count() == 1 and 'keys' not in store.tags()
    assert not list((store.dir / 'crops').iterdir())     # the crop file is really gone
    store.clock_ref[0] += 8 * 86400
    write(store, dict(caption='a plant on the sill', objects=[]))
    assert store.count() == 1                            # 7-day default retention: the old note was purged
    assert store.forget_since(store.clock_ref[0] - 10) == 1 and store.count() == 0
    write(store, dict(caption='a lamp', objects=[]))
    store.claim('local')
    assert store.forget_all() == 1 and store.count() == 0 and store.owner() == 'local'
    assert [p.name for p in store.dir.iterdir()] == ['owner.json']


def test_forget_everything_deletes_the_persons_cloud_videos(store):
    class Cloud:
        def __init__(self):
            self.deleted = []

        async def delete_video(self, index_id, video_id, force=False):
            assert force                                 # allowed in Private mode too
            self.deleted.append((index_id, video_id))

    store.add_video('idx-me', 'v1')
    store.clock_ref[0] += 100
    store.add_video('idx-me', 'v2')
    cloud = Cloud()
    assert asyncio.run(mem.delete_cloud(store, since=store.clock_ref[0] - 10, client=cloud)) == (1, 0)
    assert cloud.deleted == [('idx-me', 'v2')] and [v['video_id'] for v in store.videos()] == ['v1']
    assert asyncio.run(mem.delete_cloud(store, client=cloud)) == (1, 0) and store.videos() == []


def test_memory_ids_and_owners(tmp_path):
    with pytest.raises(ValueError):
        Store('../etc', base=tmp_path)
    store = Store('me', base=tmp_path)
    assert store.claim('access:alice') and not store.claim('access:bob')


def test_two_calls_tag_in_one_ask_in_the_other(tmp_path, monkeypatch):
    """Caregiver settings first; call 1 sees the keys; call 2 (new session, same person) asks, then forgets."""
    from contracts import validate
    from server.app import create_app
    from server.rewind import Registry
    from server.test_agent import LLM
    from server.test_rewind import FakeTL

    monkeypatch.setenv('A_EYE_MEMORY_DIR', str(tmp_path))
    scenes = [dict(caption='keys next to the left cup', objects=[KEYS] + CUPS, ms=1),
              dict(caption='three red cups', objects=CUPS, ms=1)]

    class Vision:
        def __init__(self):
            self.scene = scenes[0]

        async def call(self, operation, payload):
            return self.scene

    vision = Vision()
    app = create_app(static_dir='/nonexistent')
    app.state.vision_v2 = vision
    app.state.llm_v2 = LLM()
    app.state.memories = mem.Memories(base=tmp_path, embedder=Words(), person_check=Person())
    app.state.rewind = Registry(FakeTL(), memories=app.state.memories)

    def until(ws, kind):
        while True:
            msg = ws.receive_json()
            validate('server_message_v2', msg)
            if msg['type'] == kind:
                return msg

    def frame(session, seq):
        return dict(v=2, type='frame', session_id=session, seq=seq, ts_ms=seq, w=320, h=240,
                    jpeg_b64=jpeg((seq * 40 % 255, 90, 90)))

    settings = dict(consent=dict(person=True, caregiver=True), items=[dict(item='keys', home='blue bowl')],
                    rooms=['kitchen'], kept_days=7, remembering=True)
    with TestClient(app) as client:
        assert client.put('/api/memory/settings', json=dict(settings, rooms=['bedroom'])).status_code == 422
        refused = client.put('/api/memory/settings', json=dict(settings, consent=dict(person=True))).json()
        assert refused['remembering'] is False                      # both consents are re-checked on the server
        assert client.put('/api/memory/settings', json=settings).json()['items'] == [dict(item='keys', home='blue bowl')]
        client.post('/api/memory/tag', json={'name': 'red cup', 'kind': 'place'})
        assert client.get('/api/status').json()['features'] == {'memory': True}
        with client.websocket_connect('/ws2') as ws:
            ws.send_json(dict(v=2, type='hello', session_id='call-one', pack='general'))
            until(ws, 'agent_state')
            ws.send_json(frame('call-one', 1))
            until(ws, 'track')
            for _ in range(100):
                if client.get('/api/memory/status').json()['count']:
                    break
                time.sleep(0.02)
            assert client.get('/api/memory/status', params={'session_id': 'call-one'}).json()['on'] is True
        status = client.get('/api/memory/status').json()
        assert status['count'] == 1 and status['crops'] == 1 and status['kept_days'] == 7
        vision.scene = scenes[1]
        with client.websocket_connect('/ws2') as ws:
            ws.send_json(dict(v=2, type='hello', session_id='call-two', pack='general'))
            until(ws, 'agent_state')
            ws.send_json(frame('call-two', 2))
            until(ws, 'track')
            ws.send_json(dict(v=2, type='user_text', session_id='call-two', text='Where are my keys?', final=True))
            hit = until(ws, 'find_item')
            arrow = until(ws, 'draw')
            said = until(ws, 'say')
            while said.get('final') is not True:
                said = until(ws, 'say')
            assert hit['found'] and hit['frame_b64'] and hit['objects'][0]['label'] == 'keys'
            assert hit['place'] == 'under the left red cup' and hit['t_s'] <= 0 and hit['sure'] is True
            assert hit['live']['bbox'] == CUPS[0]['bbox'] and hit['home'] == 'blue bowl'
            assert any(op['kind'] == 'arrow' for op in arrow['ops'])
            assert said['text'].startswith('I last saw your keys under the left red cup')
            found = client.post('/api/memory/find', json={'query': 'my keys', 'session_id': 'call-two'}).json()
            assert found['found'] and found['place'] == 'under the left red cup' and found['frame_b64']
            gone = client.post('/api/memory/forget', json={'session_id': 'call-two', 'scope': 'last'}).json()
            assert gone['item'] == 'keys' and gone['count_item'] == 0
        assert client.post('/api/memory/find', json={'query': 'keys'}).json()['found'] is False


def test_each_caller_has_their_own_memory(tmp_path, monkeypatch):
    from server import memory_api
    from server.app import create_app
    monkeypatch.setenv('A_EYE_MEMORY_DIR', str(tmp_path))
    app = create_app(static_dir='/nonexistent')
    app.state.memories = mem.Memories(base=tmp_path, embedder=Words(), person_check=Person())
    who = ['access:alice']
    monkeypatch.setattr(memory_api, 'identity', lambda request: who[0])
    with TestClient(app) as client:
        client.post('/api/memory/tag', json={'name': 'keys', 'home': 'blue bowl'})
        alice = app.state.memories.for_owner('access:alice')
        write(alice, dict(caption='keys on the desk', objects=[KEYS]))
        assert client.post('/api/memory/find', json={'query': 'keys'}).json()['found'] is True
        who[0] = 'access:bob'
        assert client.post('/api/memory/find', json={'query': 'keys'}).json()['found'] is False
        assert client.get('/api/memory/settings').json()['items'] == []
        assert client.post('/api/memory/forget', json={'scope': 'everything'}).json()['deleted'] == 0
        who[0] = 'access:alice'
        assert client.get('/api/memory/status').json()['total'] == 1
    assert mem.person_id('access:alice') != mem.person_id('access:bob')


def test_memory_tool_schemas():
    from server import rewind_tools
    names = [t['name'] for t in rewind_tools.MEMORY_TOOLS]
    assert names == ['find_item', 'tag_item', 'forget_memory']
    assert all(set(t) == {'name', 'description', 'input_schema'} for t in rewind_tools.MEMORY_TOOLS)
    json.dumps(rewind_tools.MEMORY_TOOLS)


def test_grounds_every_scan_and_keeps_caption_sightings(store, monkeypatch):
    monkeypatch.setattr(mem, 'GROUND_GAP_S', 0)
    store.set_tag('keys', home='blue bowl')

    class Ground:
        def __init__(self):
            self.calls, self.objects = 0, []

        async def call(self, operation, payload):
            self.calls += 1
            return dict(caption='', objects=self.objects, ms=1)

    vision = Ground()
    writer = Writer(store, 'c', embedder=Words(), person_check=Person(), vision=vision)
    scene = dict(caption='three red cups on a table', objects=[])
    asyncio.run(writer.observe(scene, FRAME))
    vision.objects = [dict(KEYS, label='keys')]             # same caption, but now the keys are there
    record = asyncio.run(writer.observe(scene, FRAME))
    assert vision.calls == 2 and record and record['tags'][0]['bbox'] == KEYS['bbox'] and record['crops']
    vision.objects = []                                     # grounding misses, the caption names it
    text = asyncio.run(writer.observe(dict(caption='a yellow key by the middle cup', objects=[]), FRAME))
    assert text['tags'] == [dict(tag='keys', label='keys', bbox=None, near=None, text_only=True)]
    result = asyncio.run(find(store, 'where are my keys?', embedder=Words()))
    # A boxed sighting (with its crop) is preferred over a newer text-only one.
    assert result['how'] == 'tag' and result['box'] and result['say'] == 'I last saw your keys just now. Their home is the blue bowl.'
    only_text = Store('text', base=store.dir.parent, cipher=mem.Cipher(''))
    only_text.set_tag('keys')
    Writer(only_text, 'c', embedder=Words(), person_check=Person())
    asyncio.run(Writer(only_text, 'c', embedder=Words(), person_check=Person()).observe(
        dict(caption='a yellow key by the middle cup', objects=[]), FRAME))
    assert asyncio.run(find(only_text, 'where are my keys?', embedder=Words()))['found']


def test_live_item_box_guard():
    class Detector:
        def __init__(self, boxes=None, fail=False):
            self.boxes, self.fail = boxes or [], fail

        async def detect(self, jpeg_b64):
            if self.fail:
                raise RuntimeError('box down')
            return [dict(label='cup', bbox=b, conf=0.9) for b in self.boxes]

    cup = [0.42, 0.43, 0.17, 0.33]
    keys = [0.45, 0.78, 0.1, 0.1]
    run = lambda box, places, det: asyncio.run(mem.item_box_is_real(box, 'keys', places, FRAME, det))  # noqa: E731
    assert run(keys, [], Detector([cup])) is True             # small, not a cup: the keys are in view
    assert run(cup, [], Detector([cup])) is False             # the "keys" box is really the middle cup
    assert run(cup, [cup], Detector()) is False               # it is the place box itself
    assert run([0, 0, 0.6, 0.6], [], Detector()) is False     # too big to be a small object
    assert run(keys, [], Detector(fail=True)) is True and run(keys, [cup], Detector(fail=True)) is False


def test_forget_that_also_clears_this_calls_rewind_clips(tmp_path, monkeypatch):
    from server.app import create_app
    from server.rewind import Registry
    from server.test_rewind import FakeTL, JPEG, fake_encoder
    app = create_app(static_dir='/nonexistent')
    app.state.memories = mem.Memories(base=tmp_path, embedder=Words(), person_check=Person())
    app.state.rewind = Registry(FakeTL(), encoder=fake_encoder, memories=app.state.memories)
    rewind = app.state.rewind.open('s', on=True)
    rewind.add_frame(JPEG)
    rewind.keyframe('first')
    store = app.state.memories.for_owner('local')
    store.set_tag('keys')
    write(store, dict(caption='keys on the desk', objects=[KEYS]))
    with TestClient(app) as client:
        out = client.post('/api/memory/forget', json={'session_id': 's', 'scope': 'last', 'item': 'keys'}).json()
    assert out['count_item'] == 0 and rewind.clips == []


def test_hit_time_is_when_the_item_arrived_at_its_place(store):
    store.set_tag('keys')
    write(store, dict(caption='keys by the right cup', objects=[dict(KEYS, bbox=[0.7, 0.5, 0.1, 0.1])] + CUPS))
    store.clock_ref[0] += 30
    arrived_at = store.clock_ref[0]
    for _ in range(3):                                   # put down by the left cup, then seen there again and again
        write(store, dict(caption=f'keys by the left cup {_}', objects=[KEYS] + CUPS))
        store.clock_ref[0] += 10
    result = asyncio.run(find(store, 'where are my keys?', embedder=Words()))
    assert result['t'] == arrived_at and result['place'] == 'under the left red cup'
