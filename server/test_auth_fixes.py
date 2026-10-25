"""Security regressions with synthetic credentials/media and real ASGI boundaries."""
import asyncio
import logging
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import pairing
from server.logs import TokenFilter
from server.test_ws2 import app, send, receive
from server.test_identity import app_with, seed_keys
from server.test_rewind import make, feed


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for key in ('A_EYE_TOKEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD', 'A_EYE_OPEN'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.setattr(pairing, 'registry', pairing.Registry())


@pytest.mark.parametrize('open_flag', ['', '1'])
def test_unconfigured_remote_denied_and_loopback_works(monkeypatch, capsys, open_flag):
    monkeypatch.setenv('A_EYE_OPEN', open_flag)
    for host, allowed in [('192.0.2.10', False), ('127.0.0.1', True), ('::1', True)]:
        with TestClient(app(), client=(host, 1234)) as client:
            assert client.get('/api/status').status_code == (200 if allowed else 401)
            for path in ('/ws', '/ws2'):
                if allowed:
                    with client.websocket_connect(path):
                        pass
                else:
                    with pytest.raises(WebSocketDisconnect):
                        with client.websocket_connect(path):
                            pass
    assert 'auth_loopback_only' in capsys.readouterr().out


@pytest.mark.parametrize('header', [
    'CF-Connecting-IP', 'X-Forwarded-For', 'Forwarded', 'X-Real-IP',
    'Tailscale-User-Login', 'Tailscale-User-Name', 'CF-Ray',
])
@pytest.mark.parametrize('host', ['127.0.0.1', '::1'])
@pytest.mark.parametrize('value', ['', '127.0.0.1'])
def test_unconfigured_proxy_headers_never_grant_access(header, host, value, monkeypatch):
    headers = {header: value}
    with TestClient(app(), client=(host, 1234)) as client:
        assert client.get('/api/status', headers=headers).status_code == 401
        for path in ('/ws', '/ws2'):
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect(path, headers=headers):
                    pass
            assert closed.value.code == 4401
        monkeypatch.setenv('A_EYE_OPEN', '1')
        assert client.get('/api/status', headers=headers).status_code == 401
        monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-master')
        assert client.get('/api/status', headers=headers).status_code == 401
        assert client.get('/api/status', headers=dict(headers, authorization='Bearer synthetic-master')).status_code == 200


def test_remote_forwarding_headers_cannot_grant_access():
    with TestClient(app(), client=('192.0.2.10', 1234)) as client:
        assert client.get('/api/status', headers={'X-Forwarded-For': '127.0.0.1'}).status_code == 401


def test_unconfigured_tunnel_cannot_join_with_code_or_secret():
    with TestClient(app()) as client, client.websocket_connect('/ws2') as call:
        send(call, 'hello', pack='general')
        receive(call)
        send(call, 'pair_request')
        msg = receive(call)
        headers = {'CF-Connecting-IP': '192.0.2.10'}
        for path in ('/ws2', '/ws2?' + urlsplit(msg['path']).query):
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect(path, headers=headers) as camera:
                    camera.send_json(dict(v=2, type='hello', session_id='phone', pack='general',
                                          role='camera', pair_code=msg['code']))
                    receive(camera)
            assert closed.value.code == 4401
        # Refused tunnel requests must not consume the local call's pairing capability.
        with client.websocket_connect('/ws2?' + urlsplit(msg['path']).query) as camera:
            camera.send_json(dict(v=2, type='hello', session_id='phone', pack='general',
                                  role='camera', pair_code=msg['code']))
            assert receive(camera)['state'] == 'joined'
            assert receive(call)['state'] == 'joined'


@pytest.mark.parametrize('mode', ['secret', 'code'])
@pytest.mark.parametrize('header', ['CF-Connecting-IP', 'X-Forwarded-For'])
def test_proxy_pairing_limits_separate_visitors(mode, header, monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-master')
    with TestClient(app()) as client, client.websocket_connect('/ws2?token=synthetic-master') as call:
        send(call, 'hello', pack='general')
        receive(call)
        send(call, 'pair_request')
        msg = receive(call)
        query = urlsplit(msg['path']).query

        def join(code, address):
            # CF takes precedence over XFF; XFF uses its first hop, not the common proxy.
            headers = ({header: address, 'X-Forwarded-For': '198.51.100.1'}
                       if header == 'CF-Connecting-IP' else {header: f' {address}, 198.51.100.1'})
            path = '/ws2?' + (query if code == msg['code'] else 'pair=ZZZZZZZZ&join=wrong')
            if mode == 'code':
                path = '/ws2?token=synthetic-master'
            return client.websocket_connect(path, headers=headers)

        for _ in range(5):
            if mode == 'secret':
                with pytest.raises(WebSocketDisconnect):
                    with join('ZZZZZZZZ', '192.0.2.10'):
                        pass
            else:
                with join('ZZZZZZZZ', '192.0.2.10') as bad:
                    bad.send_json(dict(v=2, type='hello', session_id='phone', pack='general',
                                       role='camera', pair_code='ZZZZZZZZ'))
                    assert receive(bad)['state'] == 'rejected'
        if mode == 'secret':
            with pytest.raises(WebSocketDisconnect):
                with join(msg['code'], '192.0.2.10'):
                    pass
        else:
            with join(msg['code'], '192.0.2.10') as bad:
                bad.send_json(dict(v=2, type='hello', session_id='phone', pack='general',
                                   role='camera', pair_code=msg['code']))
                assert receive(bad)['state'] == 'rejected'
        with join(msg['code'], '192.0.2.11') as camera:
            camera.send_json(dict(v=2, type='hello', session_id='phone', pack='general',
                                  role='camera', pair_code=msg['code']))
            assert receive(camera)['state'] == 'joined'
            assert receive(call)['state'] == 'joined'


def test_non_loopback_pairing_limits_ignore_spoofed_headers(monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-master')
    with TestClient(app(), client=('192.0.2.10', 1234)) as client:
        for n in range(5):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect('/ws2?pair=ZZZZZZZZ&join=wrong',
                                              headers={'CF-Connecting-IP': f'198.51.100.{n}',
                                                       'X-Forwarded-For': f'198.51.100.{n}'}):
                    pass
        assert pairing.registry.address_misses['192.0.2.10'][0] == 5
        assert len(pairing.registry.address_misses) == 1


@pytest.mark.parametrize('settings,allowed', [
    ({}, False), ({'A_EYE_OPEN': '1'}, False),
    ({'CF_ACCESS_TEAM_DOMAIN': 'access.example.test'}, False),
    ({'A_EYE_TOKEN': 'synthetic-master'}, True),
    ({'CF_ACCESS_TEAM_DOMAIN': 'access.example.test', 'CF_ACCESS_AUD': 'synthetic-aud'}, True),
])
def test_phone_requires_credentials_before_suggesting_tunnels(settings, allowed, tmp_path):
    settings = dict.fromkeys(('A_EYE_TOKEN', 'A_EYE_OPEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD'), '') | settings
    result = subprocess.run(['make', '-f', str(Path(__file__).resolve().parents[1] / 'Makefile'),
                             'phone', 'SERVER_PORT=8123', *[f'{k}={v}' for k, v in settings.items()]],
                            cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode == 0) == allowed
    assert 'A_EYE_TOKEN' in result.stdout
    assert 'synthetic-master' not in result.stdout + result.stderr
    assert ('cloudflared tunnel' in result.stdout) == allowed
    assert ('tailscale serve' in result.stdout) == allowed
    if allowed:
        assert result.stdout.index('A_EYE_TOKEN') < result.stdout.index('cloudflared tunnel')
        assert 'http://localhost:8123' in result.stdout


@pytest.mark.parametrize('target', ['server', 'mock'])
def test_server_launch_preserves_tcp_peer(target, tmp_path):
    result = subprocess.run(['make', '-n', '-f', str(Path(__file__).resolve().parents[1] / 'Makefile'),
                             target], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert '--no-proxy-headers' in result.stdout


def issued(registry):
    call = SimpleNamespace(session=SimpleNamespace(session_id='call'), closed=False, camera=None)
    return call, registry.issue(call)


def test_code_entropy_limits_and_retirement():
    now = [0]
    registry = pairing.Registry(clock=lambda: now[0])
    call, msg = issued(registry)
    assert re.fullmatch(r'[A-HJ-NP-Z2-9]{8,}', msg['code'])
    secret = parse_qs(urlsplit(msg['path']).query)['join'][0]
    for n in range(5):
        assert registry.take(msg['code'], address=f'peer-{n}', secret='wrong')[0] is None
    assert registry.take(msg['code'], address='fresh', secret=secret)[0] is None
    call, msg = issued(registry)
    secret = parse_qs(urlsplit(msg['path']).query)['join'][0]
    for n in range(5):
        assert registry.take(f'WRONG{n}', address='attacker', secret='wrong')[0] is None
    assert registry.take(msg['code'], address='attacker', secret=secret)[0] is None
    now[0] = 61
    assert registry.take(msg['code'], address='attacker', secret=secret)[0] is call
    assert registry.take(msg['code'], address='other', secret=secret)[0] is None
    _, expired = issued(registry)
    now[0] += 301
    secret = parse_qs(urlsplit(expired['path']).query)['join'][0]
    assert registry.take(expired['code'], address='fresh', secret=secret) == (None, 'expired')


def test_join_secret_is_single_use_camera_only_and_not_api_auth(monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-master')
    with TestClient(app(), client=('192.0.2.10', 1234)) as client, client.websocket_connect('/ws2?token=synthetic-master') as call:
        send(call, 'hello', pack='general')
        receive(call)
        send(call, 'pair_request')
        msg = receive(call)
        query = urlsplit(msg['path']).query
        assert 'join=' in query and 'token=' not in query
        assert client.get('/api/status?' + query).status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws?' + query):
                pass
        with client.websocket_connect('/ws2?' + query) as camera:
            camera.send_json(dict(v=2, type='hello', session_id='phone', pack='general', role='camera', pair_code=msg['code']))
            assert receive(camera)['state'] == 'joined'
            assert receive(call)['state'] == 'joined'
        receive(call)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws2?' + query):
                pass
        send(call, 'pair_request')
        msg = receive(call)
        with client.websocket_connect('/ws2?' + urlsplit(msg['path']).query) as intruder:
            send(intruder, 'hello', pack='general')
            with pytest.raises(WebSocketDisconnect):
                intruder.receive_json()


def test_token_owners_cannot_find_or_forget_each_others_memory(tmp_path, monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-master')
    application = app_with(tmp_path)
    headers = {'authorization': 'Bearer synthetic-master'}
    with TestClient(application) as client:
        a = client.post('/api/call-owner', headers=headers).json()['owner']
        b = client.post('/api/call-owner', headers=headers).json()['owner']
        assert a != b
        own = dict(headers, **{'x-aeye-owner': a})
        other = dict(headers, **{'x-aeye-owner': b})
        # Obtain the actual server-bound owner through a call, then seed synthetic memory.
        with client.websocket_connect('/ws2?token=synthetic-master&owner=' + a) as ws:
            send(ws, 'hello', pack='general')
            receive(ws)
            owner = application.state.rewind.calls['s'].owner
            store = seed_keys(application, owner)
            assert client.post('/api/memory/find', headers=own, json={'query': 'keys'}).json()['found']
            for attacker in (other, headers, dict(headers, **{'x-aeye-owner': owner})):
                assert not client.post('/api/memory/find', headers=attacker, json={'query': 'keys', 'session_id': 's'}).json()['found']
                assert client.post('/api/memory/forget', headers=attacker, json={'scope': 'everything', 'session_id': 's'}).json()['deleted'] == 0
                assert client.post('/api/rewind/forget', headers=attacker, json={'session_id': 's'}).status_code == 404
            assert store.count() == 1
            assert client.post('/api/memory/forget', headers=own, json={'scope': 'everything'}).json()['deleted'] == 1


def test_pair_and_join_and_owner_redacted_in_uvicorn_args():
    record = logging.LogRecord('uvicorn.access', 20, '', 0, '%s %s %s %s %s',
                               ('peer', 'GET', '/agent.html?pair=ABCDEFGH&join=secret&owner=private&token=master', '1.1', 200), None)
    TokenFilter().filter(record)
    assert not any(value in record.getMessage() for value in ('ABCDEFGH', 'secret', 'private', 'master'))
    assert len(record.args) == 5
    record.msg, record.args = '/ws2?%70air=code&%6aoin=hidden&owner=private', ()
    TokenFilter().filter(record)
    assert 'code' not in record.msg and 'hidden' not in record.msg and 'private' not in record.msg


def test_failed_camera_joins_are_limited_at_socket_boundary(monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-master')
    with TestClient(app()) as client, client.websocket_connect('/ws2?token=synthetic-master') as call:
        send(call, 'hello', pack='general')
        receive(call)
        send(call, 'pair_request')
        msg = receive(call)
        query = urlsplit(msg['path']).query
        for n in range(5):
            with TestClient(client.app, client=(f'192.0.2.{n}', 1234)) as attacker:
                with pytest.raises(WebSocketDisconnect):
                    with attacker.websocket_connect('/ws2?pair=' + msg['code'] + '&join=wrong'):
                        pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws2?' + query):
                pass
        # Per-address lockout also applies to authenticated, code-only camera joins.
        for _ in range(5):
            with client.websocket_connect('/ws2?token=synthetic-master') as bad:
                bad.send_json(dict(v=2, type='hello', session_id='phone', pack='general', role='camera', pair_code='ZZZZZZZZ'))
                assert receive(bad)['state'] == 'rejected'
        send(call, 'pair_request')
        msg = receive(call)
        with client.websocket_connect('/ws2?token=synthetic-master') as blocked:
            blocked.send_json(dict(v=2, type='hello', session_id='phone', pack='general', role='camera', pair_code=msg['code']))
            assert receive(blocked)['state'] == 'rejected'


@pytest.mark.parametrize('failure', ['timeout', 'index_api', 'task_api', 'upload'])
def test_rewind_failures_say_search_unavailable(failure):
    rewind, clock = make()
    rewind.set_on(True)
    feed(rewind, clock, 0, 12, keyframes=[3])

    async def down(*args, **kwargs):
        raise RuntimeError('synthetic outage')

    async def run():
        if failure == 'index_api':
            rewind.client.ensure_index = down
        elif failure == 'upload':
            rewind.client.upload = down
        elif failure == 'task_api':
            rewind.client.task = down
        try:
            await rewind.flush(wait=True, timeout=0 if failure == 'timeout' else 0.01)
        except RuntimeError:
            pass
        text, hit = await rewind.answer('where did I put the screw?')
        assert 'not available right now' in text
        assert 'still indexing' not in text and "didn't find" not in text and hit is None
        assert rewind.clips[0].status == 'failed'

    asyncio.run(run())
