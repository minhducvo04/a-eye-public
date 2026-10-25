"""Remember this phone: a camera joins without a code only into a call of the same verified Access identity.
Synthetic RSA keys and identities only (server.test_access fixtures)."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import pairing
from server.app import create_app
from server.test_access import access, key, token  # noqa: F401  (pytest fixtures)

ME, OTHER = 'person@example.test', 'someone@example.test'


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr(pairing, 'registry', pairing.Registry())


def as_user(key, email):
    return {'Cookie': 'CF_Authorization=' + token(key, email=email)}


def start_call(ws, session='laptop'):
    ws.send_json(dict(v=2, type='hello', session_id=session, pack='general', camera=True))
    while ws.receive_json().get('state') != 'listening':
        pass


def phone_hello(ws):
    ws.send_json(dict(v=2, type='hello', session_id='phone', pack='general', role='camera', camera=True))
    return ws.receive_json()


def refused(ws):
    msg = phone_hello(ws)
    assert msg['type'] == 'pair_status' and msg['state'] == 'rejected'
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    assert exc.value.code == 4404
    return msg


def next_pair_status(ws):
    msg = ws.receive_json()
    while msg['type'] != 'pair_status':
        msg = ws.receive_json()
    return msg


def test_same_identity_joins_without_a_code(access, key, capsys):
    with TestClient(create_app()) as client, client.websocket_connect('/ws2', headers=as_user(key, ME)) as call:
        start_call(call)
        with client.websocket_connect('/ws2', headers=as_user(key, ME.upper())) as phone:
            assert phone_hello(phone)['state'] == 'joined'
            assert next_pair_status(call)['state'] == 'joined'
    assert ME not in capsys.readouterr().out


def test_other_identity_cannot_join_and_may_wait(access, key):
    with TestClient(create_app()) as client, client.websocket_connect('/ws2', headers=as_user(key, ME)) as call:
        start_call(call)
        with client.websocket_connect('/ws2', headers=as_user(key, OTHER)) as phone:
            msg = refused(phone)
            assert msg['detail'] == 'Waiting for your next call.' and msg['retry_s'] == 3


def test_token_and_open_server_never_join_without_a_code(monkeypatch):
    monkeypatch.setenv('A_EYE_MOCK', '1')
    for env in ({'A_EYE_TOKEN': 'shared-test-token'}, {}):
        monkeypatch.delenv('A_EYE_TOKEN', raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        path = '/ws2?token=shared-test-token' if env else '/ws2'
        with TestClient(create_app()) as client, client.websocket_connect(path) as call:
            start_call(call)
            with client.websocket_connect(path) as phone:
                msg = refused(phone)
                assert 'retry_s' not in msg and 'Sign in' in msg['detail']


def test_unverified_cookie_on_a_token_socket_does_not_count(monkeypatch, key):
    # A token passes auth before the cookie is read, so the cookie's email was never verified.
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.setenv('A_EYE_TOKEN', 'shared-test-token')
    with TestClient(create_app()) as client, \
            client.websocket_connect('/ws2?token=shared-test-token', headers=as_user(key, ME)) as call:
        start_call(call)
        with client.websocket_connect('/ws2?token=shared-test-token', headers=as_user(key, ME)) as phone:
            assert 'retry_s' not in refused(phone)


def test_leaving_then_a_new_call_rejoins_and_latest_call_wins(access, key):
    with TestClient(create_app()) as client:
        with client.websocket_connect('/ws2', headers=as_user(key, ME)) as call:
            start_call(call, 'first')
            with client.websocket_connect('/ws2', headers=as_user(key, ME)) as phone:
                assert phone_hello(phone)['state'] == 'joined'
                assert next_pair_status(call)['state'] == 'joined'
            assert next_pair_status(call)['state'] == 'left'
            # The same phone comes back to the same live call.
            with client.websocket_connect('/ws2', headers=as_user(key, ME)) as phone:
                assert phone_hello(phone)['state'] == 'joined'
        # That call ended: the phone waits.
        with client.websocket_connect('/ws2', headers=as_user(key, ME)) as phone:
            assert refused(phone)['retry_s'] == 3
        with client.websocket_connect('/ws2', headers=as_user(key, ME)) as old, \
                client.websocket_connect('/ws2', headers=as_user(key, ME)) as latest:
            start_call(old, 'old')
            start_call(latest, 'latest')
            with client.websocket_connect('/ws2', headers=as_user(key, ME)) as phone:
                assert phone_hello(phone)['state'] == 'joined'
                assert next_pair_status(latest)['state'] == 'joined'


def test_call_can_kick_its_phone_and_it_waits_for_the_next_call(access, key):
    with TestClient(create_app()) as client, client.websocket_connect('/ws2', headers=as_user(key, ME)) as call:
        start_call(call)
        with client.websocket_connect('/ws2', headers=as_user(key, ME)) as phone:
            assert phone_hello(phone)['state'] == 'joined'
            assert next_pair_status(call)['state'] == 'joined'
            call.send_json(dict(v=2, type='pair_request', session_id='laptop', kick=True))
            left = next_pair_status(phone)
            assert left['state'] == 'left' and left['retry_s'] == 3
        assert next_pair_status(call)['state'] == 'left'
        with client.websocket_connect('/ws2', headers=as_user(key, ME)) as again:
            assert 'disconnected this phone' in refused(again)['detail']
        # A new call takes the phone again.
        start_call(call, 'next')
        with client.websocket_connect('/ws2', headers=as_user(key, ME)) as phone:
            assert phone_hello(phone)['state'] == 'joined'


def test_code_flow_still_works_for_everyone(access, key):
    from server.test_pairing import join
    with TestClient(create_app()) as client, client.websocket_connect('/ws2', headers=as_user(key, ME)) as call:
        start_call(call)
        call.send_json(dict(v=2, type='pair_request', session_id='laptop'))
        msg = call.receive_json()
        while msg['type'] != 'pair':
            msg = call.receive_json()
        with client.websocket_connect('/ws2', headers=as_user(key, OTHER)) as phone:
            assert join(phone, msg['code'])['state'] == 'joined'
