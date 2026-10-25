import json
import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import pairing
from server.test_ws2 import app, send, receive, StreamVoice
from server.test_agent import FRAME, Vision


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setenv('A_EYE_MOCK', '1')
    instance = pairing.Registry()
    monkeypatch.setattr(pairing, 'registry', instance)
    monkeypatch.delenv('A_EYE_TOKEN', raising=False)
    return instance


def request(ws, tts='browser'):
    send(ws, 'hello', pack='general', camera=True, tts=tts)
    assert receive(ws)['state'] == 'listening'
    send(ws, 'pair_request')
    msg = receive(ws)
    assert re.fullmatch(r'[A-HJ-NP-Z2-9]{8}', msg['code'])
    assert msg['expires_in_s'] == 300
    assert msg['path'].startswith('/agent.html?pair=' + msg['code'] + '&join=')
    return msg['code']


def join(ws, code):
    ws.send_json(dict(v=2, type='hello', session_id='phone', pack='general', role='camera', pair_code=code))
    return receive(ws)


def phone(ws, kind, **fields):
    ws.send_json(dict(v=2, type=kind, session_id='phone', **fields))


def test_pair_flow_scene_isolation_and_fallback(registry, capsys):
    vision = Vision()
    with TestClient(app(vision, StreamVoice())) as client, client.websocket_connect('/ws2') as call:
        code = request(call, tts='server')
        binding = registry.codes[code][0]
        with client.websocket_connect('/ws2') as camera:
            assert join(camera, code)['state'] == 'joined'
            assert receive(call) == dict(v=2, type='pair_status', state='joined', role='call', camera_on=True, source='camera')
            camera.send_json(dict(FRAME, session_id='phone', seq=42))
            while call.receive_json()['type'] != 'track':
                pass
            assert binding.session.frame['seq'] == 42
            assert binding.session.frame['jpeg_b64'] == FRAME['jpeg_b64']
            assert vision.calls[-1]['image_b64'] == FRAME['jpeg_b64']
            # Call frames cannot replace the phone source.
            call.send_json(dict(FRAME, seq=100))
            send(call, 'user_text', text='point', final=True)
            while receive(call)['type'] != 'say':
                pass
            assert receive(call)['type'] == 'audio'
            phone(camera, 'user_text', text='speak', final=True)
            assert receive(camera)['code'] == 'bad_message'  # No call say/draw/audio queued here.
            assert binding.session.frame['seq'] == 42
            for on in (False, True):
                phone(camera, 'camera', on=on)
                assert receive(camera)['camera_on'] is on
                msg = receive(call)
                while msg['type'] == 'audio':
                    msg = receive(call)
                assert msg['camera_on'] is on
            with client.websocket_connect('/ws2') as reused:
                assert join(reused, code)['state'] == 'rejected'
                with pytest.raises(WebSocketDisconnect) as exc:
                    reused.receive_json()
                assert exc.value.code == 4404
        assert receive(call)['state'] == 'left'
        call.send_json(dict(FRAME, seq=1))
        while call.receive_json()['type'] != 'track':
            pass
        assert binding.session.frame['seq'] == 1
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    pair_rows = [r for r in rows if r['event'].startswith('pair_')]
    assert {'pair_request', 'pair_joined', 'pair_rejected', 'pair_left'} <= {r['event'] for r in pair_rows}
    assert all(set(r) == {'ts', 'level', 'event', 'session'} for r in pair_rows)
    assert all(r['session'] in (None, '043a718774c5') for r in pair_rows)


def test_expired_unknown_replacement_and_call_close(registry):
    now = [0]
    registry.clock = lambda: now[0]
    with TestClient(app()) as client, client.websocket_connect('/ws2') as call:
        code = request(call)
        now[0] = 301
        with client.websocket_connect('/ws2') as camera:
            assert join(camera, code)['state'] == 'expired'
            with pytest.raises(WebSocketDisconnect) as exc:
                camera.receive_json()
            assert exc.value.code == 4404
        with client.websocket_connect('/ws2') as camera:
            assert join(camera, '99999999')['state'] == 'rejected'
            with pytest.raises(WebSocketDisconnect):
                camera.receive_json()
        send(call, 'pair_request'); old = receive(call)['code']
        send(call, 'pair_request'); code = receive(call)['code']
        assert len(registry.codes) == 1
        assert old != code
        with client.websocket_connect('/ws2') as replaced:
            assert join(replaced, old)['state'] == 'rejected'
            with pytest.raises(WebSocketDisconnect):
                replaced.receive_json()
        with client.websocket_connect('/ws2') as camera:
            assert join(camera, code)['state'] == 'joined'
            assert receive(call)['state'] == 'joined'
            call.close()
            assert receive(camera)['state'] == 'left'
            with pytest.raises(WebSocketDisconnect):
                camera.receive_json()
        assert not registry.codes


def test_camera_requires_token(monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'test-token')
    with TestClient(app()) as client, client.websocket_connect('/ws2?token=test-token') as call:
        code = request(call)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect('/ws2'):
                pass
        assert exc.value.code == 4401
        with client.websocket_connect('/ws2?token=test-token') as camera:
            assert join(camera, code)['state'] == 'joined'
            assert receive(call)['state'] == 'joined'


def test_phone_mic_audio_becomes_the_calls_turn(registry):
    """Phone camera + mic: audio_in and barge_in on the camera socket are the call's speech (server STT)."""
    import httpx
    from server.box_client import BoxClient
    stt = []
    def handler(request):
        stt.append(json.loads(request.content))
        return httpx.Response(200, json=dict(text='point here', ms=1))
    application = app()
    application.state.box_v2 = BoxClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(application) as client, client.websocket_connect('/ws2') as call:
        code = request(call)  # browser STT on the laptop; the phone's audio still goes to server STT
        with client.websocket_connect('/ws2') as camera:
            assert join(camera, code)['state'] == 'joined'
            assert receive(call)['state'] == 'joined'
            phone(camera, 'barge_in', utterance_id='phone', at_ms=0)
            assert receive(call) == dict(v=2, type='agent_state', agent='helper', state='listening')
            phone(camera, 'audio_in', seq=1, sample_rate=16000, pcm16_b64='AAA=', final=False)
            phone(camera, 'audio_in', seq=2, sample_rate=16000, pcm16_b64='AQA=', final=True)
            stages, msg = [], call.receive_json()
            while msg['type'] != 'say':
                if msg['type'] == 'turn':
                    stages.append(msg['stage'])
                msg = call.receive_json()
            assert stages[:3] == ['received', 'transcribing', 'thinking']
            assert stt == [dict(pcm16_b64='AAABAA==', language='en')]
            assert registry  # the code stays single use
            phone(camera, 'user_text', text='hi', final=True)
            assert receive(camera)['code'] == 'bad_message'
