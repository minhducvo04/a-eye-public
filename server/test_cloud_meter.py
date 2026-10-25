"""Transport-level counters: synthetic payloads only; no paid network calls."""
import asyncio
import json

import anthropic
import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from server import cloud_meter


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '192.168.1.9', '10.0.1.2',
                                  '172.16.2.3', '100.101.2.3', '[::1]', '[fd7a:115c:a1e0::1]',
                                  'pc.example.ts.net', 'home.local'])
def test_local_destinations(host):
    assert cloud_meter.is_local(f'http://{host}:8000/path')


def test_configured_home_and_unknown_hosts(monkeypatch):
    monkeypatch.setenv('BOX_URL', 'https://home.example:8000')
    assert cloud_meter.is_local('https://home.example/tts')
    assert not cloud_meter.is_local('https://api.anthropic.com')
    assert not cloud_meter.is_local('https://model.example')
    assert not cloud_meter.is_local('https://100.128.0.1')


def test_sdk_retry_sync_and_streaming_upload_bytes():
    async def trial():
        meter = cloud_meter.start()
        bodies = []
        async def handle(request):
            bodies.append(await request.aread())
            if len(bodies) == 1:
                return httpx.Response(429, headers={'retry-after-ms': '1'}, json={'error': {'type': 'rate_limit_error', 'message': 'fixture'}})
            return httpx.Response(200, json=dict(id='msg_fixture', type='message', role='assistant', model='fixture',
                content=[dict(type='text', text='Hello')], stop_reason='end_turn', usage=dict(input_tokens=1, output_tokens=1)))
        from anthropic import _base_client
        sdk_http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
        async def sdk_handle(request):
            bodies.append(await request.aread())
            if len(bodies) == 1:
                return sdk_http.Response(429, headers={'retry-after-ms': '1'}, json={'error': {'type': 'rate_limit_error', 'message': 'fixture'}})
            return sdk_http.Response(200, json=dict(id='msg_fixture', type='message', role='assistant', model='fixture',
                content=[dict(type='text', text='Hello')], stop_reason='end_turn', usage=dict(input_tokens=1, output_tokens=1)))
        async with sdk_http.AsyncClient(transport=sdk_http.MockTransport(sdk_handle)) as http:
            sdk = anthropic.AsyncAnthropic(api_key='fixture', http_client=http, max_retries=1)
            await sdk.messages.create(model='fixture', max_tokens=10, messages=[{'role': 'user', 'content': 'Hi'}])
        assert meter.calls == 2
        assert meter.bytes == sum(map(len, bodies))
        # A new/unknown provider and multipart uploads use the same boundary.
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
            before = meter.bytes
            request = client.build_request('POST', 'https://new-provider.example/tasks', files={'file': ('fixture', b'abc')})
            expected = len(request.read())
            client.send(request)
            assert meter.calls == 3 and meter.bytes == before + expected
        # Unbuffered request streams are counted chunk-by-chunk.
        async def chunks():
            yield b'abc'
            yield b'defg'
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            before = meter.bytes
            await client.post('https://video.example/upload', content=chunks())
            assert meter.calls == 4 and meter.bytes == before + 7
    asyncio.run(trial())


def test_concurrent_calls_and_local_exclusion():
    async def trial():
        async def call(url):
            meter = cloud_meter.start()
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as http:
                await asyncio.gather(*(http.post(url, content=b'abc') for _ in range(3)))
            return meter.snapshot()
        local, cloud = await asyncio.gather(call('http://100.100.1.1/model'), call('https://model.example/chat'))
        assert local == dict(cloud_ai_calls=0, bytes_to_cloud=0)
        assert cloud == dict(cloud_ai_calls=3, bytes_to_cloud=9)
    asyncio.run(trial())


def test_real_websocket_send_boundary(monkeypatch):
    async def trial():
        received = []
        async def sink(ws):
            async for message in ws:
                received.append(message)
                await ws.send('ack')
        async with serve(sink, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            meter = cloud_meter.start()
            async with connect(f'ws://127.0.0.1:{port}') as ws:
                await ws.send('local')
                await ws.recv()
                assert meter.snapshot() == dict(cloud_ai_calls=0, bytes_to_cloud=0)
                # Classify the synthetic loopback provider as remote, without sending outside this machine.
                monkeypatch.setattr(cloud_meter, 'is_local', lambda url: False)
                payload = json.dumps({'transcript': 'Hello'})
                await ws.send(payload)
                await ws.recv()
                assert meter.snapshot() == dict(cloud_ai_calls=1, bytes_to_cloud=len(payload.encode()))
        assert received == ['local', payload]
    asyncio.run(trial())
