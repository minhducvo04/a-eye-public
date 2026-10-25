import asyncio

from server.ws2 import GPUGate


def test_turn_priority_and_exclusion():
    async def check():
        gate = GPUGate()
        order = []
        async def work(name, brain):
            async with gate.hold(brain=brain):
                order.append(name)
                await asyncio.sleep(0)
        async with gate.hold():
            scene = asyncio.create_task(work('scene', False))
            turn = asyncio.create_task(work('turn', True))
            await asyncio.sleep(0)
            assert order == ['turn']
        await asyncio.gather(scene, turn)
        assert order == ['turn', 'scene']
    asyncio.run(check())

import base64
import io
import json
from time import monotonic

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from contracts import validate
from server import ws2
from server.agent import Session, run_turn
from server.app import create_app
from server.box_client import BoxClient
from server.test_agent import FRAME, SCENE, LLM
from server.test_ws2 import app, send, receive, StreamVoice


@pytest.fixture(autouse=True)
def offline_warmup(monkeypatch):
    async def noop(app):
        pass
    monkeypatch.setattr(ws2, 'warm_brain', noop)


def test_existing_scene_does_not_block_turn(monkeypatch):
    from server.keyframes import KeyframeGate
    monkeypatch.setenv('KEYFRAME_HEARTBEAT_S', '.001')
    async def check():
        gate = GPUGate()
        started, release, brain_started, brain_release = [asyncio.Event() for _ in range(4)]
        calls = []
        session = Session('s', frame=FRAME, scene=SCENE, keyframe_gate=KeyframeGate(min_gap_s=.001))
        class Vision:
            async def call(self, operation, payload):
                assert len(payload['queries']) <= 2
                calls.append('scene')
                started.set()
                await release.wait()
                return SCENE
        class Brain:
            async def stream(self, *args):
                calls.append('brain')
                brain_started.set()
                await brain_release.wait()
                yield '{"agent":"helper","draw":[]}\nSAY: Ready.'
        async def emit(msg):
            validate('server_message_v2', msg)
        async def turn():
            async with gate.hold(brain=True):
                await run_turn(session, 'point', vision=Vision(), llm=Brain())
        loop = asyncio.create_task(ws2.scene_loop(session, Vision(), emit, gate))
        await asyncio.wait_for(started.wait(), 1)
        task = asyncio.create_task(turn())
        await asyncio.wait_for(brain_started.wait(), .05)
        release.set()
        session.frame = dict(FRAME, seq=2)
        await asyncio.sleep(.03)
        assert calls == ['scene', 'brain']
        brain_release.set()
        await task
        await asyncio.sleep(.03)
        assert calls == ['scene', 'brain', 'scene']
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
    asyncio.run(check())


@pytest.mark.parametrize('down', [False, True])
def test_audio_stt_turn_and_fallback(down):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        assert request.extensions['timeout']['read'] == 5
        return httpx.Response(503 if down else 200, json=dict(text='point here', ms=1))
    application = app()
    application.state.box_v2 = BoxClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='server')
        assert receive(ws)['state'] == 'listening'
        send(ws, 'audio_in', seq=1, pcm16_b64='AAA=', final=False)
        send(ws, 'audio_in', seq=2, pcm16_b64='AQA=', final=True)
        if down:
            error = receive(ws)
            assert error['code'] == 'internal'
            assert error['message'] == 'Server speech is unavailable; using the browser.'
            send(ws, 'audio_in', seq=3, pcm16_b64='AAA=', final=True)
            send(ws, 'user_text', text='point here', final=True)
        messages = [receive(ws) for _ in range(4)]
        assert messages[-1]['type'] == 'say'
        assert json.loads(application.state.llm_v2.calls[0][1])['text'] == 'point here'
        assert calls == [dict(pcm16_b64='AAABAA==', language='en')]


def test_noise_audio_does_not_cancel_speech_but_barge_in_does():
    # Loud hall (the team, 2026-10-24): hall noise opened utterances that cut the reply. Server audio alone
    # no longer cancels a speaking turn; the client's barge_in (sustained speech) or real words do.
    voice = StreamVoice()
    with TestClient(app(voice=voice)) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='server', tts='server')
        assert receive(ws)['state'] == 'listening'
        send(ws, 'user_text', text='point', final=True)
        assert [receive(ws)['type'] for _ in range(5)][-1] == 'audio'
        send(ws, 'audio_in', seq=1, pcm16_b64='AAA=', final=False)
        assert not voice.cancelled.wait(0.3)
        send(ws, 'barge_in', utterance_id='u', at_ms=100)
        assert voice.cancelled.wait(1)
        while receive(ws).get('state') != 'listening':
            pass
        ws.send_json({})
        assert receive(ws)['code'] == 'bad_message'


def test_yolo_limit_mapping_and_downscale():
    async def check():
        output = io.BytesIO()
        Image.new('RGB', (640, 480)).save(output, format='JPEG')
        session = Session('s', scene=SCENE, frame=dict(FRAME, jpeg_b64=base64.b64encode(output.getvalue()).decode()))
        active = 0
        peak = 0
        release = asyncio.Event()
        messages = []
        class Box:
            async def detect(self, encoded):
                nonlocal active, peak
                with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                    assert image.size == (320, 240)
                active += 1
                peak = max(peak, active)
                try:
                    await release.wait()
                    return [dict(label='metal pan', bbox=[.21, .2, .4, .2], conf=.8),
                            dict(label='unknown', bbox=[0, 0, .1, .1], conf=.9)]
                finally:
                    active -= 1
        async def emit(msg):
            validate('server_message_v2', msg)
            messages.append(msg)
        task = asyncio.create_task(ws2.yolo_loop(session, Box(), emit))
        for seq in range(2, 9):
            await asyncio.sleep(.025)
            session.frame = dict(session.frame, seq=seq)
        assert active == peak == 3
        release.set()
        await asyncio.sleep(.06)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert messages and peak == 3
        assert all([obj['id'] for obj in msg['objects']] == ['a'] for msg in messages)
        assert messages[-1]['seq'] == 8
    asyncio.run(check())
    scene = dict(objects=[dict(SCENE['objects'][0], id='far', bbox=[0, 0, .1, .1]), SCENE['objects'][0]])
    assert ws2.match_detections(scene, [dict(label='pan', bbox=[.2, .2, .4, .2], conf=.9)])[0]['id'] == 'a'


def test_startup_does_not_wait_for_warmup(monkeypatch):
    async def check():
        started, stopped = asyncio.Event(), asyncio.Event()
        async def warm(app):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        monkeypatch.setattr(ws2, 'warm_brain', warm)
        application = create_app()
        async with application.router.lifespan_context(application):
            await asyncio.wait_for(started.wait(), .2)
            assert not stopped.is_set()
        assert stopped.is_set()
    asyncio.run(check())


def test_empty_transcript_leaves_the_speaking_reply_alone():
    class Box:
        async def stt(self, pcm, language='en'):
            return ''
        async def aclose(self):
            pass
    voice = StreamVoice()
    application = app(voice=voice)
    application.state.box_v2 = Box()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='server', tts='server')
        assert receive(ws)['state'] == 'listening'
        send(ws, 'user_text', text='point', final=True)
        assert [receive(ws)['type'] for _ in range(5)][-1] == 'audio'
        send(ws, 'audio_in', seq=1, pcm16_b64='AAA=', final=False)
        send(ws, 'audio_in', seq=2, pcm16_b64='AAA=', final=True)
        stages = []
        while 'done' not in stages:
            msg = ws.receive_json()
            if msg['type'] == 'turn':
                stages.append(msg['stage'])
        assert 'failed' not in stages and 'cancelled' not in stages
        assert not voice.cancelled.wait(0.3)
