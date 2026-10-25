import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.auth import mint_owner
from server.memory import Memories
from server.rewind import registry
from server.trigger import zone_line


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-test-token')
    monkeypatch.setenv('A_EYE_MOCK', '1')
    app = create_app(static_dir='/nonexistent')
    app.state.memories = Memories(base=tmp_path)
    headers = {'Authorization': 'Bearer synthetic-test-token', 'X-Aeye-Owner': mint_owner()}
    with TestClient(app) as client:
        yield app, client, headers


def register(app, client, headers, camera=False):
    # Real ws2 hello creates the existing owner-bound call; no mocked trigger delivery.
    ws = client.websocket_connect('/ws2', headers=headers)
    ws.__enter__()
    ws.send_json(dict(v=2, type='hello', session_id='trigger-test', pack='general',
                      camera=camera, tts='browser'))
    assert ws.receive_json()['state'] == 'listening'
    response = client.post('/api/trigger/client', headers=headers, json={'session_id': 'trigger-test'})
    assert response.status_code == 200
    return ws


BODY = dict(source='test', kind='presence', zone='entryway')


def test_auth_refused_and_valid_control(setup):
    app, client, headers = setup
    assert client.post('/api/trigger', json=BODY).status_code == 401
    assert client.post('/api/trigger', headers=headers, json=BODY).status_code == 409


def test_wake_rate_limit_owner_isolation_and_camera_off(setup):
    app, client, headers = setup
    ws = register(app, client, headers)
    try:
        entry = next(iter(app.state.triggers.values()))
        owner = next(iter(app.state.triggers))
        app.state.memories.for_owner(owner).set_tag('entryway', label='Keys are in the bowl.', kind='place')
        assert client.post('/api/trigger', headers=headers, json=BODY).json()['state'] == 'EVENT'
        assert client.post('/api/trigger', headers=headers, json=dict(BODY, source='rotated')).status_code == 429
        other = dict(headers, **{'X-Aeye-Owner': mint_owner()})
        assert client.get('/api/trigger/status', headers=other).json()['state'] == 'OFF'
        assert client.post('/api/trigger/client', headers=other, json={'session_id': 'trigger-test'}).status_code == 409
        wake = client.post('/api/trigger/client', headers=headers, json={'session_id': 'trigger-test'}).json()
        assert wake['wake']['line'] == 'Keys are in the bowl.'
        result = client.post('/api/trigger/client', headers=headers, json={
            'session_id': 'trigger-test', 'ack': wake['wake']['id'], 'listening': True}).json()
        assert result['state'] == 'SESSION'
        assert result['camera_on'] is False
        assert registry(app).get('trigger-test', owner).session.camera is False
        entry['until'] = 0
        assert client.get('/api/trigger/status', headers=headers).json()['state'] == 'ARMED'
        entry['seen'] -= 6
        assert client.get('/api/trigger/status', headers=headers).json()['state'] == 'OFF'
    finally:
        ws.__exit__(None, None, None)


def test_memory_missing_zone_does_not_invent_fact(setup):
    app, _, _ = setup
    assert zone_line(app, 'token:other', 'entryway') == 'I’m here. How can I help?'
