import base64
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from server import cloud_meter, privacy
from vision.gemini import GeminiBackend


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.delenv('LOCAL_ONLY', raising=False)
    p = privacy.PRIVATE.set(False)
    s = privacy.SWITCHES.set(dict(privacy.DEFAULTS, google=True))
    m = cloud_meter.CURRENT.set(None)
    yield
    privacy.PRIVATE.reset(p)
    privacy.SWITCHES.reset(s)
    cloud_meter.CURRENT.reset(m)


def frame():
    buffer = BytesIO()
    Image.new('RGB', (1600, 1200), 'white').save(buffer, 'JPEG')
    return dict(image_b64=base64.b64encode(buffer.getvalue()).decode(), queries=['cup'])


def response(request):
    data = json.loads(request.content)
    raw = data['contents'][0]['parts'][1]['inline_data']['data']
    assert Image.open(BytesIO(base64.b64decode(raw))).size == (1280, 960)
    assert request.url.scheme == 'https'
    assert request.headers['x-goog-api-key'] == 'test-key'
    assert request.extensions['timeout']['read'] == 4
    answer = {'caption': 'A cup.', 'objects': [{'label': 'cup', 'box_2d': [100, 200, 500, 600]}]}
    return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(answer)}]}}]})


@pytest.mark.parametrize('blocked', ['private', 'google', 'local'])
def test_private_never_calls_gemini(context, monkeypatch, blocked):
    if blocked == 'private':
        privacy.PRIVATE.set(True)
    elif blocked == 'google':
        privacy.SWITCHES.get()['google'] = False
    else:
        monkeypatch.setenv('LOCAL_ONLY', '1')
    def forbidden(request):
        pytest.fail('Google must not receive a request')
    meter = cloud_meter.start()
    backend = GeminiBackend(transport=httpx.MockTransport(forbidden))
    assert backend.scene(frame(), 'ok')['objects'] == []
    assert meter.snapshot() == dict(cloud_ai_calls=0, bytes_to_cloud=0)


def test_request_increments_cloud_counters(context):
    meter = cloud_meter.start()
    backend = GeminiBackend(transport=httpx.MockTransport(response))
    result = backend.scene(frame(), 'ok')
    assert result['objects'][0]['bbox'] == pytest.approx([.2, .1, .4, .4])
    assert meter.calls == 1
    assert meter.bytes > 1000


@pytest.mark.parametrize('failure', ['timeout', 'http', 'malformed'])
def test_failure_draws_nothing(context, failure):
    def fail(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        return httpx.Response(503 if failure == 'http' else 200, json={})
    backend = GeminiBackend(transport=httpx.MockTransport(fail))
    result = backend.scene(frame(), 'ok')
    assert result['caption'] == 'unsure'
    assert result['objects'] == []
    assert backend.last_error == {'timeout': 'timeout', 'http': 'http_503',
                                  'malformed': 'invalid_image_or_response'}[failure]


def test_recorded_payment_failure_is_reported_and_cleared(context, capsys):
    # Recorded from generateContent on 2026-10-24; no request or image retained.
    sample = json.loads((Path(__file__).parent / 'fixtures/gemini_payment_required.json').read_text())
    replies = [httpx.Response(402, json=sample), None]

    def handler(request):
        return replies.pop(0) or response(request)

    backend = GeminiBackend(transport=httpx.MockTransport(handler))
    assert backend.scene(frame()) == {'caption': 'unsure', 'objects': []}
    assert backend.last_error == 'http_402_payment_required'
    assert backend.scene(frame())['objects'][0]['bbox'] == pytest.approx([.2, .1, .4, .4])
    assert backend.last_error is None
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_service_requires_explicit_consent(context, monkeypatch):
    from fastapi.testclient import TestClient
    from vision import app
    backend = GeminiBackend(transport=httpx.MockTransport(response))
    monkeypatch.setattr(app, 'backend', backend)
    client = TestClient(app.app)
    for headers in ({}, {'X-Aeye-Private': '1', 'X-Aeye-Google': '1'},
                    {'X-Aeye-Private': '0', 'X-Aeye-Google': '0'}):
        result = client.post('/scene', json=frame(), headers=headers)
        assert result.status_code == 200
        assert result.json()['objects'] == []
        assert result.headers['X-Aeye-Cloud-Calls'] == '0'
    result = client.post('/scene', json=frame(), headers={'X-Aeye-Private': '0', 'X-Aeye-Google': '1'})
    assert result.status_code == 200
    assert result.json()['objects']
    assert result.headers['X-Aeye-Cloud-Calls'] == '1'


def test_orchestrator_routes_meter_and_releases_gate(context, monkeypatch):
    import asyncio
    from server import cloud_vision
    from server.ws2 import SceneClient, GPUGate
    monkeypatch.setenv('VISION_BACKEND', 'cloud_fallback')
    gate = GPUGate()
    calls = []
    class Local:
        client = httpx.Client()
        def scene(self, body, scenario):
            calls.append('home')
            assert gate.active
            raise httpx.ConnectError('unavailable')
    def handler(request):
        calls.append('google')
        assert not gate.active
        return response(request)
    monkeypatch.setattr(cloud_vision, 'OllamaBackend', Local)
    monkeypatch.setattr(cloud_vision, 'GeminiBackend', lambda: GeminiBackend(transport=httpx.MockTransport(handler)))
    async def run():
        meter = cloud_meter.start()
        client = SceneClient()
        client.gate = gate
        result = await client.call('scene', frame())
        assert result['objects']
        assert meter.calls == 1
        assert meter.bytes > 1000
        privacy.PRIVATE.set(True)
        assert (await client.call('scene', frame()))['objects'] == []
        assert meter.calls == 1
    asyncio.run(run())
    assert calls == ['home', 'google', 'home']


@pytest.mark.parametrize('error', [httpx.ReadTimeout, httpx.ConnectError, httpx.HTTPStatusError])
def test_fallback_only_for_connection_errors(context, error):
    from vision.gemini import CloudFallbackBackend
    calls = []
    class Local:
        def scene(self, body, scenario):
            if error is httpx.HTTPStatusError:
                raise error('bad status', request=httpx.Request('POST', 'http://localhost'), response=httpx.Response(500))
            raise error('unavailable')
    class Cloud:
        def scene(self, body, scenario):
            calls.append(True)
            return {'caption': 'unsure', 'objects': []}
    backend = CloudFallbackBackend(Local(), Cloud())
    if error is httpx.HTTPStatusError:
        with pytest.raises(error):
            backend.scene(frame(), 'ok')
        assert not calls
    else:
        backend.scene(frame(), 'ok')
        assert calls == [True]


def test_remote_service_consent_and_meter_bridge(context, monkeypatch):
    import asyncio
    from server.ws2 import SceneClient
    monkeypatch.setenv('VISION_BACKEND', 'ollama')
    seen = []
    def service(request):
        seen.append((request.headers['X-Aeye-Private'], request.headers['X-Aeye-Google']))
        return httpx.Response(200, json={'caption': 'unsure', 'objects': [], 'ms': 1},
                              headers={'X-Aeye-Cloud-Calls': '1', 'X-Aeye-Cloud-Bytes': '123'})
    async def run():
        meter = cloud_meter.start()
        client = SceneClient(transport=httpx.MockTransport(service))
        await client.call('scene', frame())
        assert meter.snapshot() == {'cloud_ai_calls': 1, 'bytes_to_cloud': 123}
        privacy.PRIVATE.set(True)
        await client.call('scene', frame())
    asyncio.run(run())
    assert seen == [('0', '1'), ('1', '0')]


def test_gemini_bypasses_reserved_gpu(context, monkeypatch):
    import asyncio
    from server import cloud_vision
    from server.ws2 import SceneClient, GPUGate
    monkeypatch.setenv('VISION_BACKEND', 'gemini')
    monkeypatch.setattr(cloud_vision, 'GeminiBackend', lambda: GeminiBackend(transport=httpx.MockTransport(response)))
    async def run():
        gate = GPUGate()
        client = SceneClient()
        client.gate = gate
        async with gate.hold(brain=True):
            result = await asyncio.wait_for(client.call('scene', frame()), timeout=1)
            assert result['objects']
    asyncio.run(run())
