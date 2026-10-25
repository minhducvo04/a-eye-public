"""Regressions for the independent capture review; synthetic images only."""
import asyncio
import itertools
import json
import sys
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from device import hw
from device.client import Device
from device.config import load
from device.test_capture import Camera, Person, device


@pytest.mark.parametrize('mode', ['missing', 'unavailable', 'error', 'unknown', 'person', 'safe', 'disabled'])
def test_session_requires_successful_person_check(mode):
    class Detector:
        available = mode != 'unavailable'

        def has_person(self, image):
            if mode == 'error':
                raise OSError('detector failed')
            return None if mode == 'unknown' else mode == 'person'

    d = device()
    d.person = None if mode in ('missing', 'disabled') else Detector()
    if mode == 'disabled':
        d = Device(load(overrides={'trigger': {'drop_person_frames': False},
                                   'audio': {'mic': 'none', 'speaker': 'null'}}),
                   camera=Camera(), log=lambda *a, **k: None)
    d.sid = 'privacy-test'
    d.cfg['camera']['fps'] = 100
    sent = []

    async def send(raw):
        sent.append(json.loads(raw))

    async def run():
        task = asyncio.create_task(d.send_frames(SimpleNamespace(send=send)))
        await asyncio.sleep(.1)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)

    asyncio.run(run())
    assert bool(sent) == (mode == 'safe')
    assert d.stats['frames_sent'] == len(sent)
    if mode != 'safe':
        assert d.stats['frames_dropped_person'] > 0
    assert not d.camera.opened


@pytest.mark.parametrize('mode', ['unavailable', 'error', 'safe'])
def test_person_filter_fails_closed(mode):
    detector = hw.PersonFilter.__new__(hw.PersonFilter)
    detector.available = mode != 'unavailable'

    def detect(*a, **kw):
        if mode == 'error':
            raise RuntimeError('HOG failed')
        return [], []

    detector.hog = SimpleNamespace(detectMultiScale=detect)
    # Keep the test independent of optional numpy/OpenCV installation.
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setitem(sys.modules, 'numpy', SimpleNamespace(asarray=lambda image: image))
        with Image.new('RGB', (320, 240)) as image:
            assert detector.has_person(image) is (mode != 'safe')


@pytest.mark.parametrize('result', ['unknown', 'error', 'safe', 'disabled'])
def test_event_requires_successful_person_check(result):
    d = device()
    if result == 'disabled':
        d.person = None
    else:
        def check(image):
            if result == 'error':
                raise OSError('detector failed')
            return False if result == 'safe' else None
        d.person.has_person = check
    d.capture.frames.append((0, Image.new('RGB', (320, 240))))
    times = iter([0, 6])
    d.capture.clock = lambda: next(times, 6)
    sent = []

    async def send(payload):
        sent.extend(payload)

    d.capture.send = send
    asyncio.run(d.capture.event())
    assert bool(sent) == (result == 'safe')
    assert not d.capture.frames


def test_gpio_never_dark_with_camera_on(monkeypatch):
    observations = []
    camera = Camera()
    camera.opened = False
    indicator = None

    def record():
        observations.append((camera.opened, indicator.state, indicator.led.value))

    class LED:
        def __init__(self, pin):
            self._value = 0
            self.source = None

        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, value):
            self._value = value
            record()

        def off(self):
            self.value = 0

    monkeypatch.setitem(sys.modules, 'gpiozero', SimpleNamespace(PWMLED=LED))
    indicator = hw.GpioLed(27)
    d = Device(load(overrides={'audio': {'mic': 'none', 'speaker': 'null'}}),
               camera=camera, indicator=indicator, person=Person(), log=lambda *a, **k: None)
    opens = []

    def open_camera(state):
        camera.opened = True
        opens.append((state, indicator.state))
        record()
        if indicator.led.source is not None:
            for value in itertools.islice(indicator.led.source, 40):
                indicator.led.value = value

    def close_camera():
        camera.opened = False
        record()

    camera.open_low = lambda: open_camera('Armed')
    camera.open = lambda: open_camera('Event')
    camera.close = close_camera

    async def event():
        d.capture.signal('Event')
        d.capture.signal('Armed')  # No dark glitch even if the camera stays open.
        return False

    d.capture.event = event
    asyncio.run(d.capture.run(immediate=True))
    assert opens == [('Armed', 'Armed'), ('Event', 'Event')]
    assert all(not opened or (state != 'Off' and value > 0)
               for opened, state, value in observations)
    assert indicator.state == 'Off' and indicator.led.value == 0


def test_session_partial_camera_open_closes_before_off():
    d = device()

    def fail_open():
        d.camera.opened = True
        raise OSError('camera start failed after opening')

    d.camera.open = fail_open
    with pytest.raises(OSError):
        asyncio.run(d.send_frames(None))
    assert not d.camera.opened


def test_event_does_not_start_after_armed_deadline():
    d = device()
    now = [0]
    d.capture.clock = lambda: now[0]
    d.cfg['trigger']['armed_cap_s'] = 1

    def read():
        now[0] = 2
        d.events.put_nowait(('remember', 'test'))
        return Image.new('RGB', (320, 240))

    started = []

    async def event():
        started.append(True)
        return False

    d.camera.read = read
    d.events.put_nowait(('remember', 'test'))
    d.capture.event = event
    asyncio.run(d.capture.run())
    assert not started and not d.camera.opened and d.capture.state == 'Off'


@pytest.mark.parametrize('stage', ['burst', 'send'])
def test_armed_deadline_cancels_event_and_clears_ram(stage):
    d = device()
    d.cfg['trigger']['armed_cap_s'] = .05
    sent = []
    cancelled = []
    if stage == 'send':
        # A finished burst reaches transport immediately; use the real transport cancellation loop.
        original = d.capture.event

        async def ready_event():
            d.capture.frames.append((0, Image.new('RGB', (320, 240))))
            times = iter([0, 6])
            d.capture.clock = lambda: next(times, 6)
            return await original()

        d.capture.event = ready_event

    async def slow_send(payload):
        sent.append(True)
        try:
            await asyncio.sleep(.4)
        finally:
            cancelled.append(True)

    d.capture.send = slow_send

    async def run():
        start = time.monotonic()
        try:
            await asyncio.wait_for(d.capture.run(immediate=True), .2)
        except asyncio.TimeoutError:
            pytest.fail('Event outlived the Armed cap')
        assert time.monotonic() - start < .2

    asyncio.run(run())
    assert bool(sent) == (stage == 'send')
    assert cancelled == sent
    assert not d.capture.frames and not d.camera.opened and d.capture.state == 'Off'


@pytest.mark.parametrize('stage', ['camera', 'detector'])
def test_blocking_event_work_does_not_delay_camera_off(stage):
    d = device()
    d.cfg['trigger']['armed_cap_s'] = .05
    released = []

    if stage == 'camera':
        def read():
            time.sleep(.3)
            image = Image.new('RGB', (320, 240))
            close = image.close

            def record_close():
                released.append(True)
                close()

            image.close = record_close
            return image

        d.camera.read = read
    else:
        def check(image):
            time.sleep(.3)
            # Cancellation must not close the worker's image while it is still inspecting it.
            image.getpixel((0, 0))
            released.append(True)
            return False

        d.person.has_person = check
        original = d.capture.event

        async def ready_event():
            d.capture.frames.append((0, Image.new('RGB', (320, 240))))
            times = iter([0, 6])
            d.capture.clock = lambda: next(times, 6)
            return await original()

        d.capture.event = ready_event

    async def run():
        start = time.monotonic()
        await d.capture.run(immediate=True)
        assert time.monotonic() - start < .2
        assert not d.camera.opened and d.capture.state == 'Off' and not d.capture.frames
        await asyncio.sleep(.35)  # Let the worker finish; verify its late result is released.

    asyncio.run(run())
    assert released
