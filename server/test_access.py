"""Access boundary regressions: synthetic RSA keys and no network."""
import base64
import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import auth
from server.app import create_app


@pytest.fixture(scope='module')
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def access(monkeypatch, key):
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.delenv('A_EYE_TOKEN', raising=False)
    monkeypatch.setenv('CF_ACCESS_TEAM_DOMAIN', 'team.example.test')
    monkeypatch.setenv('CF_ACCESS_AUD', 'application')
    calls, now = [], [0.0]
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid='known', alg='RS256', use='sig')
    def fetch(url):
        calls.append(url)
        return {'keys': [jwk]}
    cache = auth.AccessKeys(fetcher=fetch, clock=lambda: now[0])
    monkeypatch.setattr(auth, '_access_keys', cache)
    return calls, now, cache


def token(key, **overrides):
    claims = dict(iss='https://team.example.test', aud=['application'],
                  exp=int(time.time()) + 300, iat=int(time.time()), email='synthetic@example.test')
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'known'})


@pytest.mark.parametrize('source', ['header', 'cookie'])
@pytest.mark.parametrize('scheme', ['', 'https://'])
def test_valid(access, key, source, scheme, monkeypatch, capsys):
    monkeypatch.setenv('CF_ACCESS_TEAM_DOMAIN', scheme + 'team.example.test')
    encoded = token(key)
    headers = {'Cf-Access-Jwt-Assertion': encoded} if source == 'header' else {'Cookie': 'CF_Authorization=' + encoded}
    with TestClient(create_app()) as client:
        assert client.get('/api/status', headers=headers).status_code == 200
        for path in ('/ws', '/ws2'):
            with client.websocket_connect(path, headers=headers):
                pass
    output = capsys.readouterr().out
    assert encoded not in output and 'synthetic@example.test' not in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert any(row.get('user') == hashlib.sha256(b'synthetic@example.test').hexdigest()[:12]
               and row['event'] == 'auth_access' for row in rows)
    assert access[0] == ['https://team.example.test/cdn-cgi/access/certs']


@pytest.mark.parametrize('case,reason', [('aud', 'wrong_aud'), ('iss', 'bad_jwt'),
    ('expired', 'expired'), ('signature', 'bad_jwt'), ('none', 'bad_jwt'),
    ('hs256', 'bad_jwt'), ('kid', 'unknown_kid'), ('exp', 'bad_jwt'),
    ('iat', 'bad_jwt'), ('nbf', 'bad_jwt')])
def test_invalid(access, key, case, reason, capsys):
    changes = {'aud': {'aud': 'other'}, 'iss': {'iss': 'https://other.test'},
               'expired': {'exp': int(time.time()) - 60}, 'iat': {'iat': int(time.time()) + 120},
               'nbf': {'nbf': int(time.time()) + 120}}
    encoded = token(key, **changes.get(case, {}))
    claims = jwt.decode(encoded, options={'verify_signature': False})
    if case == 'signature':
        encoded = token(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    elif case == 'none':
        encoded = jwt.encode(claims, '', algorithm='none', headers={'kid': 'known'})
    elif case == 'hs256':
        public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        def b64(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=')
        signing = b64(b'{"alg":"HS256","kid":"known"}') + b'.' + b64(json.dumps(claims).encode())
        encoded = (signing + b'.' + b64(hmac.digest(public, signing, 'sha256'))).decode()
    elif case == 'kid':
        encoded = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'unknown'})
    elif case == 'exp':
        del claims['exp']
        encoded = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'known'})
    with TestClient(create_app()) as client:
        headers = {'Cf-Access-Jwt-Assertion': encoded}
        assert client.get('/api/status', headers=headers).status_code == 401
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect('/ws2', headers=headers):
                pass
        assert exc.value.code == 4401
    output = capsys.readouterr().out
    assert encoded not in output and 'synthetic@example.test' not in output
    assert any(json.loads(line).get('reason') == reason for line in output.splitlines())


def test_rate_limit_and_failure(access, key, capsys):
    calls, now, cache = access
    with TestClient(create_app()) as client:
        assert client.get('/api/status', headers={'Cf-Access-Jwt-Assertion': token(key)}).status_code == 200
        for instant in (0, 1, 59, 60, 61, 119):
            now[0] = instant
            unknown = jwt.encode(dict(exp=time.time() + 300), key, algorithm='RS256', headers={'kid': str(instant)})
            assert client.get('/api/status', headers={'Cf-Access-Jwt-Assertion': unknown}).status_code == 401
        assert len(calls) == 2
        def fail(url):
            raise OSError('sensitive dependency details')
        cache.fetcher = fail
        now[0] = 120
        for _ in range(3):
            assert client.get('/api/status', headers={'Cf-Access-Jwt-Assertion': unknown}).status_code == 401
    output = capsys.readouterr().out
    assert 'sensitive dependency details' not in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert sum(row['event'] == 'dependency_error' for row in rows) == 1
    assert any(row.get('reason') == 'certs_unavailable' for row in rows)


@pytest.mark.parametrize('mode', ['open', 'token', 'access', 'both', 'partial_team', 'partial_aud'])
def test_modes(access, key, monkeypatch, mode, capsys):
    if mode in ('open', 'token', 'partial_aud'):
        monkeypatch.delenv('CF_ACCESS_TEAM_DOMAIN')
    if mode in ('open', 'token', 'partial_team'):
        monkeypatch.delenv('CF_ACCESS_AUD')
    if mode in ('token', 'both'):
        monkeypatch.setenv('A_EYE_TOKEN', 'private-test-secret')
    with TestClient(create_app()) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/status').status_code == (200 if mode == 'open' else 401)
        if mode in ('token', 'both'):
            assert client.get('/api/status?token=private-test-secret').status_code == 200
            assert client.get('/api/status', headers={'Authorization': 'Bearer private-test-secret'}).status_code == 200
            assert client.get('/api/status?token=wrong').status_code == 401
        if mode in ('access', 'both'):
            assert client.get('/api/status', headers={'Cf-Access-Jwt-Assertion': token(key)}).status_code == 200
    assert 'private-test-secret' not in capsys.readouterr().out


def test_browser_socket_url():
    # Execute the actual URL builder without booting camera/mic UI; keep package ownership intact.
    source = Path('client-web/agent.js').read_text()
    start = source.index('  function wsUrl()')
    function = source[start:source.index('\n  }', start) + 4]
    subprocess.run(['node', '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
import { ownerQuery } from './client-web/call-owner.js';
import { liveSettings } from './client-web/gemini-live.js';
globalThis.location = {protocol: 'https:', host: 'demo.test', search: ''};
let pack = 'general', cloud = {google: false}, privateMode = false;
''' + function + '''
assert.equal(wsUrl(), 'wss://demo.test/ws2');
location.search = '?token=a%26b';
assert.equal(new URL(wsUrl()).searchParams.get('token'), 'a&b');
pack = 'gemini-live'; cloud.google = true;
assert.equal(new URL(wsUrl()).searchParams.get('mode'), 'gemini-live');
assert.equal(new URL(wsUrl()).searchParams.get('google'), '1');
assert.equal(new URL(wsUrl()).searchParams.get('token'), 'a&b');
privateMode = true;
assert.equal(new URL(wsUrl()).searchParams.get('google'), null);
assert.equal(new URL(wsUrl()).searchParams.get('mode'), null);
privateMode = false; pack = 'general';
assert.equal(new URL(wsUrl()).searchParams.get('mode'), 'gemini-live');
cloud.google = false;
assert.equal(new URL(wsUrl()).searchParams.get('mode'), null);
'''], check=True, capture_output=True)


def test_access_phone_pair(access, key):
    from server.test_pairing import request, join
    headers = {'Cf-Access-Jwt-Assertion': token(key)}
    with TestClient(create_app()) as client, client.websocket_connect('/ws2', headers=headers) as call:
        code = request(call)
        with client.websocket_connect('/ws2', headers={'Cookie': 'CF_Authorization=' + token(key)}) as camera:
            assert join(camera, code)['state'] == 'joined'
            assert call.receive_json()['state'] == 'joined'


def test_initial_certs_failure(access, key, capsys):
    def fail(url):
        raise OSError('offline')
    access[2].fetcher = fail
    with TestClient(create_app()) as client:
        for _ in range(3):
            assert client.get('/api/status', headers={'Cf-Access-Jwt-Assertion': token(key)}).status_code == 401
    assert capsys.readouterr().out.count('dependency_error') == 1


def test_httpx_fetch_timeout(monkeypatch):
    calls = []
    class Response:
        def raise_for_status(self):
            calls.append('checked')
        def json(self):
            return {'keys': []}
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr(auth.httpx, 'get', get)
    assert auth.fetch_certs('https://team.test/cdn-cgi/access/certs') == {'keys': []}
    assert calls == [('https://team.test/cdn-cgi/access/certs', {'timeout': 3, 'follow_redirects': False}), 'checked']


def test_certs_redirect_is_refused_and_direct_response_works(monkeypatch):
    import httpx
    requests = []
    def handle(request):
        requests.append(str(request.url))
        if request.url.path == '/redirect':
            return httpx.Response(302, headers={'Location': 'https://other.invalid/keys'})
        return httpx.Response(200, json={'keys': []})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(auth.httpx, 'get', client.get)
        with pytest.raises(httpx.HTTPStatusError):
            auth.fetch_certs('https://team.test/redirect')
        assert requests == ['https://team.test/redirect']
        assert auth.fetch_certs('https://team.test/certs') == {'keys': []}


def test_4401_stops_retries_and_offers_reload():
    source = Path('client-web/agent.js').read_text()
    start = source.index('    ws.addEventListener("close", (event) => {')
    handler = source[start:source.index('\n    });', start) + 7]
    subprocess.run(['node', '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
let close, button, problem, reloaded = false;
let valveCleared = false;
const valveCard = {reset() { valveCleared = true; }};
const ws = {addEventListener: (_, fn) => { close = fn; }};
let socket = ws, ended = false, retryTimer = 1, reconnectDelay = 500;
const pipelineTimer = 2, linkEl = {}, connStatusEl = {};
const window = {clearTimeout() {}, clearInterval() {}, setTimeout() { throw Error('must not retry'); }};
const location = {reload() { reloaded = true; }};
const document = {getElementById: () => ({}), createElement: () => ({addEventListener(_, fn) { this.click = fn; }})};
const errorEl = {append(value) { button = value; }};
const setLink = () => {}, turnEvent = () => {}, showProblem = value => { problem = value; };
let wait = null; // agent.js close handler also clears the wait card (web-wait merge)
''' + handler + '''
close({code: 4401});
assert.equal(problem, 'Please sign in again');
assert.equal(ended, true);
assert.equal(valveCleared, true);
assert.equal(button.textContent, 'Reload to sign in');
button.click();
assert.equal(reloaded, true);
'''], check=True, capture_output=True)


def test_browser_smoke_url_redaction(monkeypatch):
    # Load only imports/helpers: Playwright is an optional smoke-runner dependency.
    import ast
    from urllib.parse import parse_qs, urlsplit
    tree = ast.parse(Path('scripts/browser_smoke.py').read_text())
    tree.body = [node for node in tree.body if
                 (isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom)
                  and node.module != 'playwright.sync_api') or
                 isinstance(node, ast.FunctionDef) and node.name in ('redact', 'page_url')]
    namespace = {'BASE': 'https://demo.test'}
    exec(compile(tree, 'scripts/browser_smoke.py', 'exec'), namespace)
    monkeypatch.delenv('A_EYE_TOKEN', raising=False)
    assert namespace['page_url']() == 'https://demo.test/agent.html'
    monkeypatch.setenv('A_EYE_TOKEN', 'synthetic & secret')
    url = namespace['page_url']()
    assert parse_qs(urlsplit(url).query)['token'] == ['synthetic & secret']
    output = namespace['redact'](url + ' synthetic & secret')
    assert 'synthetic' not in output
    assert '[redacted]' in output
    assert namespace['redact']('?pair=ABCD2345&j%6fin=private&owner=capability') == (
        '?pair=[redacted]&j%6fin=[redacted]&owner=[redacted]')
