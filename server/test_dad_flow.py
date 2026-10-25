import asyncio

import pytest
from fastapi.testclient import TestClient
from contracts import validate
from server.app import create_app
from server.agent import Session, run_turn
from server import packs, safety
from server.packs.home import HomeGuide, TASKS


def test_home_contract():
    validate('client_message_v2', dict(v=2, type='hello', session_id='sink-test', pack='home'))


def test_sink_selection_and_completion():
    session = Session('sink-test', packs.get('general'))
    messages = asyncio.run(run_turn(session, 'sink', vision=None, llm=None))
    assert session.home_guide is not None
    assert any('one step at a time' in m.get('text', '') for m in messages)
    guide = HomeGuide(introduced=True, index=len(TASKS['sink']) - 1)
    assert guide.reply('done') == safety.WATER_OFF_LINE
    guide.water_off_passed = True
    assert guide.reply('done') == ''
    assert guide.completed


def test_normal_server_has_no_presenter_controls(monkeypatch):
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.setenv('LOCAL_ONLY', '1')
    with TestClient(create_app(), client=('127.0.0.1', 1234)) as client:
        assert client.post('/api/dad-demo?session_id=s', json={'enabled': True}).status_code in (404, 405)


def test_presenter_requires_private_process(monkeypatch):
    monkeypatch.delenv('LOCAL_ONLY', raising=False)
    with pytest.raises(ValueError):
        create_app(presenter=True)


def test_presenter_verdict_is_single_use_and_hazard_revokes():
    from types import SimpleNamespace
    from time import monotonic
    from server.step_check import RETRY_LINE
    session = Session('sink-test', packs.get('home'))
    session.home_guide = HomeGuide(introduced=True, index=2)
    session.checker_test_mode = True
    app = SimpleNamespace(state=SimpleNamespace(presenter=True))
    def turn(text):
        return asyncio.run(run_turn(session, text, vision=None, llm=None, app=app))
    session.checker_test_verdict = ('closed', monotonic())
    assert any(m.get('detail') == 'Valve: test closed' for m in turn('next'))
    assert session.home_guide.index == 3
    assert any(m.get('text') == RETRY_LINE for m in turn('next'))
    assert session.home_guide.index == 3
    session.checker_test_verdict = ('closed', monotonic())
    assert any(m.get('detail') == 'Stopped by code' for m in turn('water near the outlet'))
    assert session.checker_test_verdict is None
    assert any(m.get('text') == RETRY_LINE for m in turn('next'))
    session.checker_test_verdict = ('closed', monotonic() - 16)
    assert any(m.get('text') == RETRY_LINE for m in turn('next'))


def test_presenter_api_off_by_default_owner_private_and_peer_gates(monkeypatch, tmp_path):
    from server.rewind import registry
    from server.memory import Memories
    monkeypatch.setenv('LOCAL_ONLY', '1')
    monkeypatch.setenv('A_EYE_MOCK', '1')
    for name in ('A_EYE_TOKEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD'):
        monkeypatch.delenv(name, raising=False)
    app = create_app(presenter=True)
    app.state.memories = Memories(base=tmp_path)
    with TestClient(app, client=('127.0.0.1', 1234)) as client:
        # Same trusted-loopback identity as the authenticated production API.
        from server.rewind import identity
        from starlette.requests import Request
        owner = identity(Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1234),
                                  'state': {}}))
        call = registry(app).open('demo', on=False, owner=owner)
        call.session = Session('demo', packs.get('home'), private=True)
        url = '/api/dad-demo?session_id=demo'
        assert client.get(url).json() == {'enabled': False}
        assert client.post(url + '&action=verdict', json={'state': 'closed'}).status_code == 400
        assert client.post(url, json={'enabled': True}).json() == {'enabled': True}
        assert client.post(url + '&action=verdict', json={'state': 'closed'}).status_code == 200
        assert call.session.checker_test_verdict[0] == 'closed'
        assert client.post(url, json={'enabled': False}).status_code == 200
        assert call.session.checker_test_verdict is None
        assert client.post(url, json={'enabled': 'true'}).status_code == 400
        assert client.post(url, json={'enabled': True, 'extra': True}).status_code == 400
        assert client.post(url, json={'enabled': True}, headers={'origin': 'https://elsewhere.test'}).status_code == 403
        for header in ('x-forwarded-for', 'cf-connecting-ip', 'tailscale-user-login'):
            assert client.get(url, headers={header: '127.0.0.1'}).status_code in (401, 404)
        call.session.private = False
        assert client.post(url, json={'enabled': True}).status_code == 404
        call.session.private = True
        call.owner = 'someone-else'
        assert client.post(url, json={'enabled': True}).status_code == 404


def test_presenter_websocket_hello_and_reconnect_reset(monkeypatch, tmp_path):
    from scripts.dad_flow_rehearsal import presenter_app
    for key, value in dict(LOCAL_ONLY='1', ROUTER='off', A_EYE_MOCK='0').items():
        monkeypatch.setenv(key, value)
    app = presenter_app(tmp_path)
    app.state.mock = True
    # The live rehearsal checks the real adapter. This contract probe needs no network.
    from voice.fake import FakeAdapter
    app.state.voice_factory_v2 = lambda: FakeAdapter()
    with TestClient(app, client=('127.0.0.1', 1234)) as client:
        with client.websocket_connect('/ws2') as ws:
            hello = dict(v=2, type='hello', session_id='presenter-contract', pack='home', private=True, camera=False)
            ws.send_json(hello)
            assert ws.receive_json()['state'] == 'listening'
            assert ws.receive_json()['detail'] == 'Presenter available'
            url = '/api/dad-demo?session_id=presenter-contract'
            assert client.post(url, json={'enabled': True}).json()['enabled']
            ws.send_json(hello)
            assert ws.receive_json()['state'] == 'listening'
            assert ws.receive_json()['detail'] == 'Presenter available'
            assert client.get(url).json()['enabled'] is False
