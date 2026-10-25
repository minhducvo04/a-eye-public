"""The device client against an in-process mock server: a full turn, auto-off, the motion trigger, the person drop."""
import asyncio
import json
import math
import socket
import struct
import threading
import time
import wave

import pytest

from device import config, hw
from device.client import Device
from device.fake_turn import table_frame


class Brain:
    """Scripted brain that accepts whatever options the server passes."""

    async def stream(self, system, user, **kwargs):
        yield json.dumps(dict(agent='helper', draw=[])) + '\nSAY: Your keys are on the table.'

    def complete(self, **kwargs):
        from server.llm import LLMResult
        return LLMResult('Your keys are on the table.', 'mock', 'scripted', 0)


@pytest.fixture
def server(monkeypatch, tmp_path):
    import uvicorn
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.setenv('A_EYE_MEMORY_DIR', str(tmp_path / 'memory'))
    for name in ('A_EYE_TOKEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD'):
        monkeypatch.delenv(name, raising=False)
    from server.app import create_app
    async def safe(self, jpeg_b64, scene=None):
        return False  # Synthetic fixtures; never contact a real detector host.
    monkeypatch.setattr('server.memory.PersonCheck.__call__', safe)
    app = create_app()
    app.state.llm = app.state.llm_v2 = Brain()
    async def judge(frames):
        return {'worth': True, 'note': 'Keys placed on the table.'}
    app.state.capture_judge = judge
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f'ws://127.0.0.1:{port}/ws2'
    srv.should_exit = True
    thread.join(10)


def clip(folder, keys_from):
    folder.mkdir()
    for i in range(6):
        table_frame(i >= keys_from, arm=i == keys_from and i > 0).save(folder / f'{i:03d}.jpg')
    return folder


def tone_wav(path, seconds=1.0):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 220 * i / 16000)))
                               for i in range(int(16000 * seconds))))
    return path


def make(url, tmp_path, idle, keys_from=0, motion=False, script=None, person=None):
    cfg = config.load(overrides={
        'server': {'url': url},
        'trigger': {'button': 'none', 'idle_off_s': idle, 'cap_s': 60, 'motion': motion},
        'camera': {'source': 'images', 'path': str(clip(tmp_path / 'clip', keys_from)), 'fps': 2.0},
        'audio': {'mic': 'wav', 'speaker': 'null'},
    })
    return Device(cfg, mic=hw.WavMic(script or []), person=person, log=lambda *a, **k: None)


async def until_off(device, presses=None, timeout=30):
    runner = asyncio.create_task(device.run(presses))
    end = time.monotonic() + timeout
    while not device.stats['off_reasons'] and time.monotonic() < end:
        await asyncio.sleep(0.1)
    device.stop.set()
    await asyncio.wait_for(runner, 10)


def test_button_turn_reply_audio_then_idle_off(server, tmp_path):
    class NoPeople:
        available = True

        def has_person(self, image):
            return False

    device = make(server, tmp_path, idle=3, script=[(1.0, tone_wav(tmp_path / 'q.wav'))], person=NoPeople())
    asyncio.run(until_off(device, hw.scripted_presses(device.events, [0.1])))
    s = device.stats
    assert s['frames_sent'] > 0 and s['utterances'] == 1 and s['audio_in_bytes'] > 0
    assert any(t['stage'] == 'received' for t in s['turns'])
    assert 'Your keys are on the table.' in ' '.join(s['replies'])
    assert s['audio_out_bytes'] > 0                      # mock server voice: a tone, played on the null speaker
    assert s['off_reasons'] == ['idle'] and not s['errors']



def test_frames_with_a_person_never_leave_the_device(server, tmp_path):
    class Everyone:
        available = True

        def has_person(self, image):
            return True

    device = make(server, tmp_path, idle=2, person=Everyone())
    asyncio.run(until_off(device, hw.scripted_presses(device.events, [0.1])))
    assert device.stats['frames_sent'] == 0 and device.stats['frames_dropped_person'] > 0


def test_nothing_is_sent_while_off(server, tmp_path):
    device = make(server, tmp_path, idle=2)
    async def go():
        runner = asyncio.create_task(device.run())
        await asyncio.sleep(1.5)
        device.stop.set()
        await runner
    asyncio.run(go())
    assert device.stats['calls'] == 0 and device.stats['frames_sent'] == 0
