import asyncio
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from contracts import ALLOWED_PARTS, validate
from server.app import create_app
from server.vision_client import VisionClient, VisionUnavailable


class FakeVision:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def call(self, operation, payload):
        validate(f'{operation}_request', payload)
        self.calls.append((operation, payload))
        if self.fail:
            raise VisionUnavailable()
        return {
            'identify': dict(item='cpu_fan_cable', conf=0.95, ms=1),
            'locate': dict(found=True, conf=0.9, bbox=[0.2, 0.3, 0.1, 0.1], ms=1,
                           others=[dict(header='SYS_FAN1', bbox=[0.5, 0.5, 0.1, 0.1], conf=0.8)]),
            'verify': {'pass': True, 'conf': 0.95, 'reason': 'Seated', 'ms': 1},
        }[operation]


def send(ws, kind, **fields):
    ws.send_json(dict(v=1, type=kind, session_id='s', **fields))


def receive(ws, count=1):
    messages = [ws.receive_json() for _ in range(count)]
    for message in messages:
        validate('server_message', message)
    return messages


def hello(ws):
    send(ws, 'hello', mode='photo')
    messages = receive(ws, 2)
    assert messages[0]['state'] == 'SAFETY'
    assert messages[1]['text'].startswith('Switch the PSU off and unplug it.')


def photo(ws):
    send(ws, 'photo', seq=1, w=10, h=10, jpeg_b64='synthetic')


def guide(ws):
    hello(ws)
    send(ws, 'confirm', what='power_off', value=True)
    assert receive(ws, 2)[0]['state'] == 'IDENTIFY'
    photo(ws)
    messages = receive(ws, 5)
    assert [m.get('state') for m in messages if m['type'] == 'step'] == ['LOCATE', 'GUIDE', 'AWAIT']
    overlay = messages[1]
    assert overlay['level'] == 'header'
    assert [b['role'] for b in overlay['boxes']] == ['target', 'other']


def test_websocket_full_flow_and_health():
    vision = FakeVision()
    with TestClient(create_app(vision)) as client:
        assert client.get('/health').json() == {'ok': True}
        with client.websocket_connect('/ws') as ws:
            guide(ws)
            assert vision.calls[0][1]['candidates'] == list(ALLOWED_PARTS)
            photo(ws)
            messages = receive(ws, 4)
            assert messages[0]['state'] == 'VERIFY'
            assert messages[1]['pass'] is True
            assert messages[2]['state'] == 'DONE'


def test_ws_safety_stale_and_invalid_messages():
    vision = FakeVision()
    with TestClient(create_app(vision, clock=lambda: 4000)) as client:
        with client.websocket_connect('/ws') as ws:
            ws.send_text('not json')
            assert receive(ws)[0]['code'] == 'bad_message'
            ws.send_json({'v': 1, 'type': 'hello'})
            assert receive(ws)[0]['code'] == 'bad_message'
            hello(ws)
            send(ws, 'frame', seq=1, ts_ms=4000, w=10, h=10, jpeg_b64='synthetic')
            assert receive(ws)[0]['code'] == 'power_off_required'
            assert vision.calls == []
            send(ws, 'user_text', text='open the power supply')
            assert "can't help" in receive(ws)[0]['text']
            send(ws, 'user_text', text='power is off')
            assert receive(ws, 2)[0]['state'] == 'IDENTIFY'
            send(ws, 'frame', seq=2, ts_ms=1000, w=10, h=10, jpeg_b64='synthetic')
            assert receive(ws)[0]['code'] == 'stale_frame'
            assert vision.calls == []


def test_vision_failure_can_retry_and_connections_are_isolated():
    vision = FakeVision()
    with TestClient(create_app(vision)) as client:
        with client.websocket_connect('/ws') as ws:
            guide(ws)
            vision.fail = True
            send(ws, 'confirm', what='seated', value=True)
            messages = receive(ws, 3)
            assert messages[1]['code'] == 'vision_unavailable'
            assert messages[2]['state'] == 'AWAIT'
            vision.fail = False
            send(ws, 'confirm', what='check_again', value=True)
            assert receive(ws, 4)[2]['state'] == 'DONE'
        with client.websocket_connect('/ws') as ws:
            hello(ws)
            photo(ws)
            assert receive(ws)[0]['code'] == 'power_off_required'


@pytest.mark.parametrize('kind,fields', [
    ('parts_scan', {'jpeg_b64': 'synthetic'}),
    ('project_open', {'project_id': 'demo'}), ('audio', {'pcm16_b64': ''}),
])
def test_unwired_hooks(kind, fields):
    with TestClient(create_app(FakeVision())) as client:
        with client.websocket_connect('/ws') as ws:
            hello(ws)
            send(ws, kind, **fields)
            assert receive(ws)[0] == dict(v=1, type='error', code='internal', message='not wired yet')


def test_static_files_do_not_shadow_routes(tmp_path):
    (tmp_path / 'index.html').write_text('<html>synthetic client</html>')
    with TestClient(create_app(FakeVision(), static_dir=tmp_path)) as client:
        assert 'synthetic client' in client.get('/').text
        assert client.get('/health').json() == {'ok': True}
        with client.websocket_connect('/ws') as ws:
            hello(ws)


@pytest.mark.parametrize('failure', [None, 'timeout', 'status', 'schema', 'json'])
def test_http_vision_boundary(failure):
    def handler(request):
        assert request.url.path == '/identify'
        assert request.extensions['timeout']['read'] == 5
        validate('identify_request', json.loads(request.content))
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        if failure == 'status':
            return httpx.Response(503)
        if failure == 'schema':
            return httpx.Response(200, json={'item': 'cpu_fan_cable'})
        if failure == 'json':
            return httpx.Response(200, text='invalid')
        return httpx.Response(200, json=dict(item='cpu_fan_cable', conf=0.9, ms=1))

    vision = VisionClient(transport=httpx.MockTransport(handler))
    async def run():
        return await vision.call('identify', {'image_b64': 'synthetic', 'candidates': list(ALLOWED_PARTS)})
    if failure:
        with pytest.raises(VisionUnavailable):
            asyncio.run(run())
    else:
        assert asyncio.run(run())['item'] == 'cpu_fan_cable'
