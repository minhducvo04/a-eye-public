import asyncio
import json

import httpx
import pytest
from websockets.asyncio.server import serve

from scripts import ready


@pytest.fixture(autouse=True)
def clean_auth(monkeypatch):
    for name in ('A_EYE_TOKEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD'):
        monkeypatch.delenv(name, raising=False)


def test_live_pair_request_on_fake_socket():
    async def run():
        async def handler(ws):
            hello = json.loads(await ws.recv())
            assert hello['private'] is True and hello['camera'] is False
            request = json.loads(await ws.recv())
            assert request['type'] == 'pair_request'
            await ws.send(json.dumps(dict(type='pair', code='1234', path='/agent.html?pair=1234')))
        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            assert await ready.pairing(f'http://127.0.0.1:{port}', {})
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['pass', 'fail', 'auth'])
def test_network_table_and_secret_redaction(monkeypatch, capsys, mode):
    monkeypatch.setenv('A_EYE_TOKEN', 'never-print-token')
    seen = []
    def handler(request):
        seen.append(request)
        if mode == 'fail':
            return httpx.Response(500, text='never-print-token')
        if mode == 'auth' and request.url.path == '/api/status':
            return httpx.Response(401)
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': m} for m in ready.required_models()]})
        if request.url.path == '/api/status':
            return httpx.Response(200, json={'features': {'memory': True}})
        if request.url.path == '/agent.html':
            return httpx.Response(200, text='<script src="agent.js"></script>')
        return httpx.Response(200, json={'ok': True})
    original = httpx.AsyncClient
    monkeypatch.setattr(ready.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    async def paired(*args):
        return mode != 'fail'
    monkeypatch.setattr(ready, 'pairing', paired)
    report = ready.Report()
    asyncio.run(ready.network(report))
    output = capsys.readouterr().out
    assert 'never-print-token' not in output
    assert report.failed == (mode == 'fail')
    assert ('SKIP' in output) == (mode == 'auth')
    assert all('Fix:' in line for line in output.splitlines() if line.startswith('FAIL'))
    assert all('authorization' not in r.headers for r in seen if r.url.path != '/api/status')


def test_access_skips_pairing_and_missing_models_fail(monkeypatch, capsys):
    monkeypatch.setenv('CF_ACCESS_TEAM_DOMAIN', 'secret-domain')
    original = httpx.AsyncClient
    monkeypatch.setattr(ready.httpx, 'AsyncClient', lambda **kw: original(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'ok': True, 'models': []})), **kw))
    async def forbidden(*args):
        pytest.fail('must not open an Access-only socket without credentials')
    monkeypatch.setattr(ready, 'pairing', forbidden)
    report = ready.Report()
    asyncio.run(ready.network(report))
    output = capsys.readouterr().out
    assert 'secret-domain' not in output
    assert 'FAIL | Ollama required models' in output
    assert 'SKIP | Phone pair_request' in output


def test_local_probes_only_read_settings(monkeypatch, capsys):
    monkeypatch.setenv('CF_ACCESS_TEAM_DOMAIN', 'secret-domain')
    monkeypatch.setenv('CARTESIA_API_KEY', 'secret-key')
    monkeypatch.setattr(ready.shutil, 'which', lambda name: '/fake/' + name)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return type('Result', (), {'returncode': 0, 'stdout': b'{"BackendState":"Running"}'})()
    monkeypatch.setattr(ready.subprocess, 'run', run)
    report = ready.Report()
    ready.local_checks(report)
    output = capsys.readouterr().out
    assert not report.failed
    assert 'secret-' not in output
    assert calls[0] == ['/fake/tailscale', 'status', '--json']
    assert calls[1][-3:] == ['-m', 'device.fake_turn', '--mock']
    assert 'xrsimulator' in calls[2] and 'CODE_SIGNING_ALLOWED=NO' in calls[2]
