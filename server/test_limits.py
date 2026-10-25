"""Limits apply to authenticated tunnels and local callers."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from server import cloud_meter, privacy
from server.app import create_app


def test_turn_windows_and_owners(monkeypatch):
    from server.limits import Limits
    monkeypatch.setenv('TURNS_PER_MINUTE', '2')
    now = [0]
    limits = Limits(clock=lambda: now[0])
    assert limits.turn('ip1', 'owner')
    assert limits.turn('ip2', 'owner')
    assert not limits.turn('ip3', 'owner')
    assert limits.turn('ip1', 'other')
    assert not limits.turn('ip1', 'third')
    now[0] = 60
    assert limits.turn('ip1', 'owner')


def test_authenticated_tunnel_socket_limits_and_release(monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic-token')
    monkeypatch.setenv('CALLS_PER_IP', '1')
    monkeypatch.setenv('MAX_OPEN_SOCKETS', '2')
    with TestClient(create_app(), headers={'authorization': 'Bearer synthetic-token'}) as client:
        with client.websocket_connect('/ws2', headers={'cf-connecting-ip': 'visitor1'}):
            with client.websocket_connect('/ws', headers={'cf-connecting-ip': 'visitor1'}) as rejected:
                assert rejected.receive_json()['message'].startswith('Please')
            with client.websocket_connect('/ws2', headers={'cf-connecting-ip': 'visitor2'}):
                with client.websocket_connect('/ws2?join=x', headers={'cf-connecting-ip': 'visitor3'}) as rejected:
                    assert rejected.receive_json()['message'].startswith('Please')
        with client.websocket_connect('/ws2', headers={'cf-connecting-ip': 'visitor1'}) as ws:
            ws.send_json({})
            assert ws.receive_json()['code'] == 'bad_message'


def test_budget_rollover_persistence_and_privacy(monkeypatch, tmp_path):
    from server.cloud_meter import DailyBudget
    monkeypatch.setenv('DAILY_CLOUD_CAP_USD', '0.05')
    day = ['2026-10-24']
    path = str(tmp_path / 'budget.sqlite')
    budget = DailyBudget(path, day=lambda: day[0])
    monkeypatch.setattr(cloud_meter, 'BUDGET', budget)
    assert not budget.exhausted()
    budget.charge(.05)
    assert budget.exhausted()
    assert DailyBudget(path, day=lambda: day[0]).exhausted()
    privacy.set_call({'google': True, 'twelvelabs': True})
    assert privacy.local_only()
    assert not any(privacy.allowed(name) for name in privacy.DEFAULTS)
    day[0] = '2026-10-25'
    assert not budget.exhausted()
    assert privacy.claude_allowed()


def test_transport_cutoff_and_local_control(monkeypatch, tmp_path):
    from server.cloud_meter import DailyBudget
    monkeypatch.setenv('DAILY_CLOUD_CAP_USD', '0.01')
    monkeypatch.setattr(cloud_meter, 'BUDGET', DailyBudget(str(tmp_path / 'budget.sqlite')))
    seen = []
    meter = cloud_meter.start()
    with httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(r.url.host) or httpx.Response(200)))) as client:
        client.post('https://cloud.example', content=b'synthetic')
        with pytest.raises(cloud_meter.CloudCapReached):
            client.post('https://cloud.example', content=b'not sent')
        client.post('http://127.0.0.1:8001', content=b'local')
    assert seen == ['cloud.example', '127.0.0.1']
    assert meter.calls == 1


def test_token_cost_is_recorded_without_turn_metrics(monkeypatch, tmp_path):
    from server.cloud_meter import DailyBudget
    from server.llm import usage_counts
    budget = DailyBudget(str(tmp_path / 'budget.sqlite'))
    monkeypatch.setattr(cloud_meter, 'BUDGET', budget)
    monkeypatch.setenv('DAILY_CLOUD_CAP_USD', '0.01')
    usage_counts(SimpleNamespace(usage=SimpleNamespace(input_tokens=10000, output_tokens=1000)))
    assert budget.exhausted()


def test_text_rate_boundary_and_content_free_log(monkeypatch, capsys):
    from server.test_ws2 import app, receive, send
    monkeypatch.setenv('TURNS_PER_MINUTE', '1')
    with TestClient(app()) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', camera=False)
        assert receive(ws)['state'] == 'listening'
        send(ws, 'user_text', text='synthetic secret phrase', final=True)
        assert receive(ws)['message'].startswith('Please')
        ws.send_json({})
        assert receive(ws)['code'] == 'bad_message'
    assert 'synthetic secret phrase' not in capsys.readouterr().out


def test_cap_notice_once_and_local_turn(monkeypatch):
    from server.test_ws2 import app, receive, send
    monkeypatch.setenv('DAILY_CLOUD_CAP_USD', '0')
    with TestClient(app()) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', camera=False)
        assert receive(ws)['state'] == 'listening'
        assert receive(ws)['text'] == cloud_meter.CAP_LINE
        send(ws, 'user_text', text='hello there', final=True)
        while True:
            msg = receive(ws)
            if msg['type'] == 'say':
                assert msg['text'] != cloud_meter.CAP_LINE
                break
        ws.send_json({})
        assert receive(ws)['code'] == 'bad_message'


def test_http_rate_boundary(monkeypatch):
    monkeypatch.setenv('TURNS_PER_MINUTE', '1')
    with TestClient(create_app()) as client:
        assert client.post('/api/call-owner').status_code == 200
        response = client.post('/api/call-owner')
        assert response.status_code == 429
        assert response.json()['message'].startswith('Please')
        assert client.get('/health').status_code == 200


def test_budget_atomic_admission(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from server.cloud_meter import DailyBudget, CloudCapReached
    monkeypatch.setenv('DAILY_CLOUD_CAP_USD', '.01')
    budget = DailyBudget(str(tmp_path / 'budget.sqlite'))
    def attempt(_):
        try:
            budget.charge(.01, admit=True)
            return True
        except CloudCapReached:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(8))) == 1


def test_active_call_receives_cap_notice(monkeypatch):
    from server.test_ws2 import app, receive, send
    with TestClient(app()) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', camera=False)
        assert receive(ws)['state'] == 'listening'
        cloud_meter.BUDGET.charge(5)
        assert receive(ws)['text'] == cloud_meter.CAP_LINE
        ws.send_json({})
        assert receive(ws)['code'] == 'bad_message'


def test_live_minutes_stop_idle_connection(monkeypatch):
    from websockets.asyncio.server import serve
    from server.agent import Session
    from server.gemini_live import LiveBridge
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic')
    monkeypatch.setenv('DAILY_CLOUD_CAP_USD', '.15')
    async def trial():
        privacy.set_call({'google': True})
        async def sink(ws):
            await ws.recv()
            await ws.send('{"setupComplete": {}}')
            await ws.wait_closed()
        async def ignore(*args):
            pass
        async def discover():
            return 'models/synthetic'
        async with serve(sink, '127.0.0.1', 0) as server:
            bridge = LiveBridge(Session('s'), None, None, ignore, ignore, discover=discover,
                                endpoint=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}')
            assert await bridge.start()
            bridge.budget_minute -= 61
            await asyncio.wait_for(bridge.task, 2)
            assert not bridge.active
            assert cloud_meter.BUDGET.exhausted()
            await bridge.close()
    asyncio.run(trial())


def test_owner_cannot_evade_by_changing_forwarded_ip(monkeypatch):
    from server.auth import mint_owner
    from server.test_ws2 import app, receive, send
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic')
    monkeypatch.setenv('TURNS_PER_MINUTE', '1')
    path = '/ws2?token=synthetic&owner=' + mint_owner()
    with TestClient(app()) as client:
        with client.websocket_connect(path, headers={'cf-connecting-ip': 'one'}) as first:
            send(first, 'hello', pack='general', camera=False)
            assert receive(first)['state'] == 'listening'
            with client.websocket_connect(path, headers={'cf-connecting-ip': 'two'}) as second:
                send(second, 'hello', pack='general', camera=False)
                assert receive(second)['message'].startswith('Please')
