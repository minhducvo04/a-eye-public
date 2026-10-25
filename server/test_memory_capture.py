import asyncio
import base64

import pytest
from PIL import Image

from device.hw import encode_jpeg
from device.test_capture import assert_no_images
from server.memory import Store, find
from server.memory_capture import capture


@pytest.fixture(autouse=True)
def safe_person_check(monkeypatch):
    from server.memory import PersonCheck
    original = PersonCheck.__call__
    async def safe(self, jpeg_b64, scene=None):
        return False
    monkeypatch.setattr('server.memory.PersonCheck.__call__', safe)
    return original


@pytest.mark.parametrize('detections', [{}, '', [{}], []])
def test_event_validates_real_person_checker_result(tmp_path, monkeypatch, safe_person_check, detections):
    monkeypatch.setattr('server.memory.PersonCheck.__call__', safe_person_check)

    async def detect(self, jpeg_b64):
        return detections

    monkeypatch.setattr('server.box_client.BoxClient.detect', detect)
    store = Store('capture-test', base=tmp_path)
    store.save_settings({'consent': {'person': True, 'caregiver': True}, 'remembering': True})
    frames = [base64.b64encode(encode_jpeg(Image.new('RGB', (320, 240)))[0]).decode()]
    judged = []

    async def judge(images):
        judged.append(True)
        return {'worth': True, 'note': 'Keys placed on the table.'}

    answer = asyncio.run(capture(store, frames, judge))
    assert answer['noted'] is (detections == [])
    assert bool(judged) == bool(store.records()) == (detections == [])
    assert not frames
    assert_no_images(tmp_path)


@pytest.mark.parametrize('result', ['person', 'error', 'unknown', 'safe'])
def test_event_checks_every_still_before_inference(tmp_path, monkeypatch, result):
    store = Store('capture-test', base=tmp_path)
    store.save_settings({'consent': {'person': True, 'caregiver': True}, 'remembering': True})
    frames = [base64.b64encode(encode_jpeg(Image.new('RGB', (320, 240), color))[0]).decode()
              for color in ('white', 'red')]
    checked, judged = [], []

    async def check(self, jpeg_b64, scene=None):
        checked.append(jpeg_b64)
        if len(checked) == 1 or result == 'safe':
            return False
        if result == 'error':
            raise OSError('detector unavailable')
        return None if result == 'unknown' else True

    async def judge(images):
        judged.append(list(images))
        return {'worth': True, 'note': 'Keys placed on the table.'}

    monkeypatch.setattr('server.memory.PersonCheck.__call__', check)
    answer = asyncio.run(capture(store, frames, judge))
    assert len(checked) == 2
    assert answer['noted'] is (result == 'safe')
    assert bool(judged) == bool(store.records()) == (result == 'safe')
    assert not frames
    assert_no_images(tmp_path)


def test_text_only_note_findable_and_buffers_deleted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = Store('capture-test', base=tmp_path)
    store.save_settings({'consent': {'person': True, 'caregiver': True}, 'remembering': True})
    frames = [base64.b64encode(encode_jpeg(Image.new('RGB', (320, 240)))[0]).decode()]

    async def judge(images):
        assert images
        return {'worth': True, 'note': 'Keys placed on the table.'}

    assert asyncio.run(capture(store, frames, judge)) == {'noted': True}
    assert not frames and len(store.records()) == 1
    assert asyncio.run(find(store, 'keys'))['found']
    assert_no_images(tmp_path)


def test_consent_refusal_and_failure_clear(tmp_path):
    store = Store('capture-test', base=tmp_path)
    frames = ['unused']
    assert asyncio.run(capture(store, frames))['reason'] == 'consent_required'
    assert frames == [] and store.records() == []


def test_model_failure_releases_frames_without_note(tmp_path):
    import pytest
    store = Store('capture-test', base=tmp_path)
    store.save_settings({'consent': {'person': True, 'caregiver': True}, 'remembering': True})
    frames = [base64.b64encode(encode_jpeg(Image.new('RGB', (320, 240)))[0]).decode()]

    async def fail(images):
        raise OSError('local model down')

    with pytest.raises(OSError):
        asyncio.run(capture(store, frames, fail))
    assert not frames and not store.records()
    assert_no_images(tmp_path)
