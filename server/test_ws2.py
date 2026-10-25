import asyncio
import json
import threading

from fastapi.testclient import TestClient
import httpx
import pytest

from contracts import validate
from server.app import create_app
from server.test_agent import FRAME, LLM, SCENE, Vision
from server.ws2 import SceneClient
from voice.adapter import VoiceAdapter


def app(vision=None, voice=None):
    result = create_app()
    result.state.vision_v2 = vision or Vision()
    result.state.llm_v2 = LLM()
    if voice:
        result.state.voice_factory_v2 = lambda: voice
    return result


def send(ws, kind, **fields):
    ws.send_json(dict(v=2, type=kind, session_id='s', **fields))


def receive(ws):
    while True:
        msg = ws.receive_json()
        validate('server_message_v2', msg)
        if msg['type'] not in ('track', 'metrics', 'turn', 'reply_delta'):
            return msg


def hello(ws, tts='browser'):
    send(ws, 'hello', pack='general', tts=tts)
    assert receive(ws)['state'] == 'listening'
    ws.send_json(FRAME)
    # Wait for the first background refresh before testing a cached turn.
    while True:
        msg = ws.receive_json()
        validate('server_message_v2', msg)
        if msg['type'] == 'track':
            break


def test_ws2_flow_and_invalid_input():
    vision = Vision()
    with TestClient(app(vision)) as client, client.websocket_connect('/ws2') as ws:
        ws.send_text('bad json')
        assert receive(ws)['code'] == 'bad_message'
        send(ws, 'user_text', text='early', final=True)
        assert receive(ws)['code'] == 'bad_message'
        hello(ws)
        ws.send_json(dict(FRAME, seq=0, jpeg_b64='older'))
        send(ws, 'user_text', text='interim', final=False)
        send(ws, 'user_text', text='point here', final=True)
        messages = [receive(ws) for _ in range(4)]
        assert messages[0]['state'] == 'thinking'
        assert messages[1]['type'] == 'draw'
        assert messages[-1]['type'] == 'say'
        assert len(vision.calls) == 1
        assert vision.calls[0]['image_b64'] == FRAME['jpeg_b64']


def test_second_final_cancels_first_turn():
    class SlowBrain:
        def __init__(self):
            self.started = threading.Event()
            self.cancelled = threading.Event()

        async def stream(self, *args):
            if not self.started.is_set():
                self.started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()
            yield '{"agent":"helper","draw":[]}\nSAY:\nDone.'

    brain = SlowBrain()
    application = app()
    application.state.llm_v2 = brain
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        hello(ws)
        send(ws, 'user_text', text='first', final=True)
        assert receive(ws)['state'] == 'thinking'
        assert brain.started.wait(2)
        send(ws, 'user_text', text='second', final=True)
        assert receive(ws)['state'] == 'thinking'
        assert brain.cancelled.wait(0.2)
        assert [receive(ws)['type'] for _ in range(3)] == ['draw', 'agent_state', 'say']


class StreamVoice(VoiceAdapter):
    def __init__(self):
        super().__init__()
        self.cancelled = threading.Event()

    async def say(self, utterance_id, text):
        try:
            yield b'\x00\x00'
            yield b'\x01\x00'
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


def test_barge_in_cancels_tts_without_later_audio():
    voice = StreamVoice()
    with TestClient(app(voice=voice)) as client, client.websocket_connect('/ws2') as ws:
        hello(ws, tts='server')
        send(ws, 'user_text', text='point', final=True)
        messages = [receive(ws) for _ in range(5)]
        audio = messages[-1]
        assert audio['type'] == 'audio' and audio['final'] is False
        send(ws, 'barge_in', utterance_id=audio['utterance_id'], at_ms=0)
        assert receive(ws)['state'] == 'listening'
        assert voice.cancelled.wait(2)
        # A socket round trip is a barrier: no old audio may appear after listening.
        ws.send_json({})
        assert receive(ws)['code'] == 'bad_message'


def test_server_audio_final_chunk_and_fake_adapter_silence():
    class FiniteVoice(VoiceAdapter):
        async def say(self, utterance_id, text):
            yield b'\x00\x00'
    from voice.fake import FakeAdapter
    for voice in (FiniteVoice(), FakeAdapter()):
        with TestClient(app(voice=voice)) as client, client.websocket_connect('/ws2') as ws:
            hello(ws, tts='server')
            send(ws, 'user_text', text='point', final=True)
            assert [receive(ws)['type'] for _ in range(4)] == ['agent_state', 'draw', 'agent_state', 'say']
            if isinstance(voice, FiniteVoice):
                audio = receive(ws)
                assert audio['type'] == 'audio' and audio['final'] is True
                assert audio['pcm16_b64'] == 'AAA='
            ws.send_json({})
            assert receive(ws)['code'] == 'bad_message'


@pytest.mark.parametrize('failure', [False, True])
def test_scene_http_boundary(failure):
    def handler(request):
        assert request.url.path == '/scene'
        assert request.extensions['timeout']['read'] == 25
        validate('scene_request', json.loads(request.content))
        return httpx.Response(503 if failure else 200, json=SCENE)
    vision = SceneClient(transport=httpx.MockTransport(handler))
    async def call():
        return await vision.call('scene', dict(image_b64='synthetic', queries=['the pan']))
    if failure:
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(call())
    else:
        assert asyncio.run(call()) == SCENE
