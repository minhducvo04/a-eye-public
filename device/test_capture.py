"""Privacy postconditions use real HTTP and inspect all output files, not counters alone."""
import asyncio
import base64
import io
from unittest.mock import patch

import httpx
from PIL import Image

from device.client import Device
from device.config import load
from device.test_client import server  # noqa: F401


class Camera:
    def open(self):
        self.opened = True

    open_low = open

    def read(self):
        return Image.new('RGB', (320, 240), 'white')

    def close(self):
        self.opened = False


class Person:
    available = True

    def has_person(self, image):
        return image.getpixel((0, 0)) == (255, 0, 0)


def device():
    return Device(load(overrides={'audio': {'mic': 'none', 'speaker': 'null'}}),
                  camera=Camera(), person=Person(), log=lambda *a, **k: None)


def assert_no_images(root):
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        assert path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
        raw = path.read_bytes()
        assert b'\xff\xd8\xff' not in raw and b'\x89PNG' not in raw
        assert b'/9j/' not in raw


def test_armed_no_socket_and_cap(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    d = device()
    d.cfg['trigger']['armed_cap_s'] = 0.05
    with patch('socket.socket.connect', side_effect=AssertionError('Armed socket traffic')):
        asyncio.run(d.capture.run())
    assert not d.camera.opened and d.mic.open_at is None
    assert d.capture.state == 'Off' and not d.capture.frames
    assert_no_images(tmp_path)


def test_event_real_http_person_drop_and_no_files(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = device()
    d.cfg['server']['url'] = server
    # Both consent gates must be enabled by the owner.
    url = server.replace('ws://', 'http://').replace('/ws2', '/api/memory/settings')
    httpx.put(url, json={'consent': {'person': True, 'caregiver': True}, 'remembering': True})
    d.capture.frames.append((0, Image.new('RGB', (320, 240), 'red')))
    d.camera.open()
    original = d.capture.send
    sent = []

    async def send(frames):
        sent.extend(frames)
        await original(frames)

    d.capture.send = send
    asyncio.run(d.capture.event())
    assert sent and len(sent) <= 3
    for value in sent:
        with Image.open(io.BytesIO(base64.b64decode(value))) as image:
            r, g, b = image.getpixel((0, 0))
            assert not (r > 200 and g < 20 and b < 20)
    assert not d.capture.frames
    found = httpx.post(url.replace('/settings', '/find'), json={'query': 'keys'}).json()
    assert found['found'] is True
    assert_no_images(tmp_path)


def test_event_failure_clears_ram(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    d = device()
    d.capture.frames.append((0, Image.new('RGB', (320, 240))))
    d.stop.set()
    asyncio.run(d.capture.event())
    assert not d.capture.frames
    assert_no_images(tmp_path)


def test_disk_postcondition_rejects_persisted_images(tmp_path):
    import pytest
    # Mutation control: even an image hidden behind a non-image extension must fail.
    Image.new('RGB', (64, 48)).save(tmp_path / 'cache.bin', format='JPEG')
    with pytest.raises(AssertionError):
        assert_no_images(tmp_path)


def test_event_send_failure_clears_ram(tmp_path, monkeypatch):
    import pytest
    monkeypatch.chdir(tmp_path)
    d = device()
    d.capture.frames.append((0, Image.new('RGB', (320, 240))))
    times = iter([0, 6])
    d.capture.clock = lambda: next(times)

    async def failed(frames):
        raise OSError('synthetic failure')

    d.capture.send = failed
    with pytest.raises(OSError):
        asyncio.run(d.capture.event())
    assert not d.capture.frames
    assert_no_images(tmp_path)


def test_event_unavailable_detector_never_sends():
    d = device()
    d.person.available = False
    d.capture.frames.append((0, Image.new('RGB', (320, 240))))
    times = iter([0, 6])
    d.capture.clock = lambda: next(times)

    async def forbidden(frames):
        raise AssertionError('unfiltered frames left device')

    d.capture.send = forbidden
    asyncio.run(d.capture.event())
    assert not d.capture.frames


def test_motion_opens_event_without_session_or_microphone():
    d = device()
    count = 0

    def read():
        nonlocal count
        count += 1
        return Image.new('RGB', (320, 240), 'black' if count == 1 else 'white')

    reached = []

    async def event():
        reached.append(True)
        return False

    d.camera.read = read
    d.capture.event = event
    asyncio.run(d.capture.run())
    assert reached == [True] and d.stats['calls'] == 0 and d.mic.open_at is None
    assert not d.capture.frames and not d.camera.opened


def test_gpio_state_patterns(monkeypatch):
    import itertools
    import sys
    from types import SimpleNamespace
    from device.hw import GpioLed

    class LED:
        def __init__(self, pin):
            self.value = 0
            self.source = None

        def off(self):
            self.value = 0

    monkeypatch.setitem(sys.modules, 'gpiozero', SimpleNamespace(PWMLED=LED))
    indicator = GpioLed(27)
    indicator.set('Armed')
    values = list(itertools.islice(indicator.led.source, 40))
    assert min(values) == .03 and max(values) == .15
    indicator.set('Event')
    assert indicator.led.source is None and indicator.led.value == .6
    indicator.set('Session')
    assert indicator.led.value == 1
    indicator.set('Off')
    assert indicator.led.value == 0
