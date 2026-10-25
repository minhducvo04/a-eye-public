import asyncio
from time import monotonic

import pytest

from contracts import validate
from server import packs
from server.agent import Session, run_turn
from server.test_agent import SCENE, Vision


LIVE = [
    '{"agent":"helper","draw":[{"kind":"circle","target":"o1","from_target":"n/a","text":"","role":"info"}]," SAY:"Place the knife on the cutting board for safety." }',
    '{"agent": "helper", "draw": [{"type": "circle", "target": "o1", "role": "tool"}]}SAY: I\'ll guide you to use the knife carefully. Where is the tomato?',
    '{"agent": "helper", "draw": [{"type": "array", "items": [{"kind": "circle", "target": "o1", "role": "draw_role"}]}]} SAY: Please hold the knife firmly ...',
]


@pytest.mark.parametrize('text', LIVE)
@pytest.mark.parametrize('chunk_size', [1, 1000])
def test_live_formats(text, chunk_size):
    async def check():
        class Brain:
            async def stream(self, *args):
                for i in range(0, len(text), chunk_size):
                    yield text[i:i + chunk_size]
        scene = dict(SCENE, objects=[dict(SCENE['objects'][0], id='o1')])
        result = await run_turn(Session('s', pack=packs.get('cooking'), scene=scene, scene_time=monotonic()),
                                'help', vision=Vision(), llm=Brain())
        for msg in result:
            validate('server_message_v2', msg)
        drawing = next(m for m in result if m['type'] == 'draw')
        assert drawing['ops'][1]['kind'] == 'circle'
        assert drawing['ops'][1]['anchor']['object_id'] == 'o1'
        speech = ' '.join(m['text'] for m in result if m['type'] == 'say')
        assert speech and not any(c in speech for c in '{}[]"')
        assert 'SAY:' not in speech
    asyncio.run(check())


def test_first_clause_starts_audio_without_more_tokens():
    async def check():
        spoken = asyncio.Event()
        release = asyncio.Event()
        class Brain:
            async def stream(self, *args):
                yield '{"draw":[]}SAY: Put the knife down,'
                await release.wait()
                yield ' then find the tomato.'
        async def speak(msg):
            assert msg['text'] == 'Put the knife down,'
            spoken.set()
            if False:
                yield
        task = asyncio.create_task(run_turn(Session('s', speak=speak), 'help', vision=Vision(), llm=Brain()))
        try:
            await asyncio.wait_for(spoken.wait(), .2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


@pytest.mark.parametrize('input_kind', ['audio_in', 'user_text'])
def test_speech_refresh_and_final_reservation(monkeypatch, input_kind):
    import threading
    import time
    from fastapi.testclient import TestClient
    from server import ws2
    from server.test_agent import FRAME
    from server.test_ws2 import app, send, receive

    async def warm(app):
        pass
    monkeypatch.setattr(ws2, 'warm_brain', warm)
    monkeypatch.setenv('KEYFRAME_HEARTBEAT_S', '.5')
    refresh = threading.Event()
    brain_started = threading.Event()
    brain_release = threading.Event()
    class SlowVision:
        calls = 0
        async def call(self, *args):
            self.calls += 1
            if self.calls == 2:
                refresh.set()
                await asyncio.sleep(1)
            return SCENE
    class Brain:
        async def stream(self, *args):
            brain_started.set()
            while not brain_release.is_set():
                await asyncio.sleep(.005)
            yield '{"draw":[]}SAY: Ready.'
    class Box:
        async def stt(self, pcm):
            await asyncio.sleep(.08)
            return 'help'
        async def aclose(self):
            pass
    vision = SlowVision()
    application = app(vision)
    application.state.llm_v2 = Brain()
    application.state.box_v2 = Box()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='server')
        receive(ws)
        ws.send_json(FRAME)
        while ws.receive_json()['type'] != 'track':
            pass
        time.sleep(.51)
        fields = dict(seq=1, pcm16_b64='AAA=') if input_kind == 'audio_in' else dict(text='help')
        send(ws, input_kind, **fields, final=False)
        # Speech refreshes a cached view older than the configured heartbeat.
        ws.send_json(dict(FRAME, seq=2))
        assert refresh.wait(.2)
        start = monotonic()
        send(ws, input_kind, **fields, final=True)
        assert brain_started.wait(.2 if input_kind == 'audio_in' else .05)
        if input_kind == 'user_text':
            assert monotonic() - start < .05
        ws.send_json(dict(FRAME, seq=3))
        time.sleep(1.05)
        assert vision.calls == 2
        brain_release.set()
        while True:
            msg = ws.receive_json()
            validate('server_message_v2', msg)
            if msg['type'] == 'metrics' and 'turn_id' in msg:
                break
        if input_kind == 'audio_in':
            assert msg['turn_ms'] >= 1130


def test_final_audio_metrics_include_stt(monkeypatch):
    from fastapi.testclient import TestClient
    from server import ws2
    from server.test_ws2 import app, send
    from voice.adapter import VoiceAdapter

    async def warm(app):
        pass
    monkeypatch.setattr(ws2, 'warm_brain', warm)
    class Box:
        async def stt(self, pcm):
            await asyncio.sleep(.08)
            return 'help'
        async def aclose(self):
            pass
    class Voice(VoiceAdapter):
        async def say(self, *args):
            yield b'\x00\x00'
    application = app(voice=Voice())
    application.state.box_v2 = Box()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='server', tts='server')
        ws.receive_json()
        send(ws, 'audio_in', seq=1, pcm16_b64='AAA=', final=True)
        while True:
            msg = ws.receive_json()
            validate('server_message_v2', msg)
            if msg['type'] == 'metrics' and 'turn_id' in msg:
                assert msg['turn_ms'] >= msg['first_audio_ms'] >= 80
                # Timeline split for the status strip (2026-10-24): STT wait is its own metric now.
                assert msg['first_audio_ms'] >= msg['stt_ms'] >= 80 and 'track_ms' not in msg
                break


@pytest.mark.parametrize('text', ['{"draw":[', '{"agent":"helper"}', 'Plain helpful text.',
                                 '{"draw":[]}Plain helpful text.',
                                 '{\n"draw": []\n} "SAY:"Plain helpful text."'])
def test_safe_fallbacks(text):
    async def check():
        class Brain:
            async def stream(self, *args):
                yield text
        result = await run_turn(Session('s'), 'help', vision=Vision(), llm=Brain())
        for msg in result:
            validate('server_message_v2', msg)
            if msg['type'] == 'say':
                assert not any(c in msg['text'] for c in '{}[]"')
    asyncio.run(check())
