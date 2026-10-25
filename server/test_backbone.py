"""Phone boundary checks use only in-process synthetic dependencies."""
import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from contracts import validate
from server import api
from server.app import create_app
from server.logs import protect_transport_logs
from server.test_agent import FRAME
from server.test_ws2 import send


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.delenv('A_EYE_TOKEN', raising=False)
    async def noop(app):
        pass
    monkeypatch.setattr('server.ws2.warm_brain', noop)


@pytest.mark.parametrize('path', ['/ws', '/ws2'])
@pytest.mark.parametrize('suffix', ['', '?token=wrong'])
def test_reject_before_processing(monkeypatch, path, suffix):
    monkeypatch.setenv('A_EYE_TOKEN', 'private-secret')
    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(path + suffix) as ws:
                ws.send_json({'anything': 'must not run'})
                ws.receive_json()
        assert exc.value.code == 4401


@pytest.mark.parametrize('path', ['/ws', '/ws2'])
@pytest.mark.parametrize('auth', ['query', 'header', 'open'])
def test_socket_authorized(monkeypatch, path, auth):
    if auth != 'open':
        monkeypatch.setenv('A_EYE_TOKEN', 'private-secret')
    suffix = '?token=private-secret' if auth == 'query' else ''
    headers = {'Authorization': 'Bearer private-secret'} if auth == 'header' else {}
    with TestClient(create_app()) as client, client.websocket_connect(path + suffix, headers=headers) as ws:
        ws.send_json(dict(v=2 if path == '/ws2' else 1, type='hello', session_id='s',
                          **({'pack': 'general'} if path == '/ws2' else {'mode': 'photo'})))
        assert ws.receive_json()['type'] != 'error'


@pytest.mark.parametrize('token', [False, True])
def test_rest(monkeypatch, token):
    if token:
        monkeypatch.setenv('A_EYE_TOKEN', 'private-secret')
    with TestClient(create_app()) as client:
        assert client.get('/health').json() == {'ok': True}
        if token:
            for path in ('scene', 'market', 'status', 'future'):
                response = client.get('/api/' + path)
                assert response.status_code == 401
                assert response.json() == {'error': 'unauthorized'}
            client.headers['Authorization'] = 'Bearer private-secret'
        response = client.post('/api/scene', json={'image_b64': 'synthetic', 'queries': []})
        assert response.status_code == 200
        validate('scene_response', response.json())
        response = client.post('/api/market', json={'v': 1, 'category': 'ram', 'owned_parts': []})
        assert response.status_code == 200
        validate('market_response', response.json())
        for path in ('scene', 'market'):
            assert client.post('/api/' + path, json={}).status_code == 422
            assert client.post('/api/' + path, content='bad').status_code == 422
        report = client.get('/api/status').json()
        assert report['mode'] == 'mock' and report['ok']
        assert report['version'] == {'contracts': '2.2'}
        assert set(report['deps']) == {'vision', 'boxsvc', 'brain', 'tts', 'stt'}
        assert all(set(dep) == {'ok', 'ms', 'detail'} for dep in report['deps'].values())


@pytest.mark.parametrize('audio', [False, True])
def test_mock_full_turn(audio, capsys):
    with TestClient(create_app()) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', tts='server', stt='server')
        assert ws.receive_json()['state'] == 'listening'
        ws.send_json(FRAME)
        while ws.receive_json()['type'] != 'track':
            pass
        if audio:
            send(ws, 'audio_in', seq=1, pcm16_b64='AQABAA==', final=True)
        else:
            send(ws, 'user_text', text='private transcript', final=True)
        messages = []
        while True:
            msg = ws.receive_json()
            validate('server_message_v2', msg)
            messages.append(msg)
            if msg['type'] == 'metrics' and 'turn_id' in msg:
                break
        drawing = next(m for m in messages if m['type'] == 'draw')
        assert drawing['ops'] == [dict(kind='clear', id='*')]  # Fake grounding never points in General.
        assert len([m for m in messages if m['type'] == 'say']) == 2
        assert any(m['type'] == 'audio' and m['pcm16_b64'] for m in messages)
    output = capsys.readouterr().out
    for secret in ('private transcript', 'AQABAA==', 'synthetic', 'Show me the first object.'):
        assert secret not in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert {'connect', 'hello', 'turn_start', 'turn_end'} <= {row['event'] for row in rows}
    assert all({'ts', 'level', 'event', 'session'} <= row.keys() for row in rows)


def test_live_status_parallel_cached(monkeypatch):
    monkeypatch.setenv('A_EYE_MOCK', '0')
    application = create_app()
    calls = []
    async def check(name):
        calls.append(name)
        await asyncio.sleep(.01)
        assert len(calls) == 5
        return name != 'vision', 'test'
    application.state.dependency_check = check
    with TestClient(application) as client:
        first = client.get('/api/status').json()
        assert first['mode'] == 'live' and not first['ok']
        assert client.get('/api/status').json() == first
        assert len(calls) == 5
        application.state.status_cache = (0, first)
        calls.clear()
        client.get('/api/status')
        assert len(calls) == 5


def test_invalid_dependency_response(monkeypatch):
    monkeypatch.setattr(api, 'search', lambda body: {})
    with TestClient(create_app()) as client:
        assert client.post('/api/market', json=dict(v=1, category='ram', owned_parts=[])).status_code == 422


def test_transport_token_redacted():
    protect_transport_logs()
    record = logging.LogRecord('uvicorn.error', 20, '', 0, 'WebSocket %s',
                               ('/ws2?token=private-secret',), None)
    logging.getLogger('uvicorn.error').filter(record)
    assert 'private-secret' not in record.getMessage()


def test_access_log_keeps_args_for_uvicorn_formatter():
    from uvicorn.logging import AccessFormatter
    protect_transport_logs()
    record = logging.LogRecord('uvicorn.access', 20, '', 0, '%s - "%s %s HTTP/%s" %d',
                               ('127.0.0.1:5000', 'GET', '/voice.js?token=private-secret', '1.1', 200), None)
    logging.getLogger('uvicorn.access').filter(record)
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert 'GET /voice.js?token=[redacted]' in line and 'private-secret' not in line


def test_status_timeout_is_bounded_and_cached(monkeypatch):
    monkeypatch.setenv('A_EYE_MOCK', '0')
    application = create_app()
    async def stalled(name):
        await asyncio.Event().wait()
    application.state.dependency_check = stalled
    from time import monotonic
    with TestClient(application) as client:
        started = monotonic()
        report = client.get('/api/status').json()
        assert monotonic() - started < 3
        assert all(not dep['ok'] and dep['detail'] == 'unavailable' for dep in report['deps'].values())
        assert client.get('/api/status').json() == report


def test_immediate_frame_then_text():
    with TestClient(create_app()) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general')
        ws.receive_json()
        ws.send_json(FRAME)
        send(ws, 'user_text', text='point here', final=True)
        while True:
            msg = ws.receive_json()
            if msg['type'] == 'draw':
                assert msg['ops'] == [dict(kind='clear', id='*')]
                break


def test_client_files_are_never_served_stale():
    """Mixed cached and fresh ES modules broke the call page after an update."""
    from fastapi.testclient import TestClient
    from server.app import create_app
    client = TestClient(create_app())
    response = client.get('/agent.js')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-cache'
