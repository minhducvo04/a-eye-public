"""Home first: no cloud call is made when its switch is off (per switch, and for Private mode).

Every real network path is patched to a recorder: the Anthropic SDK clients, Cartesia's WebSocket, and httpx's
network transports (which also blocks the GPU box and Ollama, so nothing leaves the test). A full /ws2 call
runs a typed turn with the server voice and the Rewind toggle, then the recorder must show zero calls to the
provider whose switch was off.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server import privacy
from server.llm import LLMClient
from server.rewind import TwelveLabsClient
from server.test_ws2 import send
from server.app import create_app
from server.test_agent import Vision

HOSTS = {'api.anthropic.com': 'claude', 'api.cartesia.ai': 'cartesia', 'api.twelvelabs.io': 'twelvelabs'}


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for name in ('ANTHROPIC_API_KEY', 'CARTESIA_API_KEY', 'TWELVELABS_API_KEY'):
        monkeypatch.setenv(name, 'synthetic')

    class Refused:
        def __init__(self, *args, **kwargs):
            seen.append('claude')
            self.messages = self

        def stream(self, **kwargs):
            raise RuntimeError('recorded')

        def create(self, **kwargs):
            raise RuntimeError('recorded')

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def is_closed(self):
            return False

    import server.llm as llm
    monkeypatch.setattr(llm.anthropic, 'AsyncAnthropic', Refused)
    monkeypatch.setattr(llm.anthropic, 'Anthropic', Refused)
    llm._ANTHROPIC.clear()

    import voice.cartesia_ws as cartesia_ws

    async def connect(*args, **kwargs):
        seen.append('cartesia')
        raise OSError('recorded')
    monkeypatch.setattr(cartesia_ws.websockets, 'connect', connect)

    def record(request):
        seen.append(HOSTS.get(request.url.host, 'other:' + request.url.host))
        return httpx.Response(503, request=request)

    async def handle_async(self, request):
        return record(request)

    def handle(self, request):
        return record(request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', handle_async)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    from voice import adapter
    adapter._health_cache.clear()
    return seen


def call(cloud=None, private=False):
    application = create_app()
    application.state.vision_v2 = Vision()
    application.state.llm_v2 = LLMClient()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        hello = dict(pack='general', tts='server', stt='browser', camera=False)
        if cloud is not None:
            hello['cloud'] = cloud
        if private:
            hello['private'] = True
        send(ws, 'hello', **hello)
        ws.receive_json()
        send(ws, 'rewind', on=True)
        send(ws, 'user_text', text='what is this part?', final=True)
        while True:
            msg = ws.receive_json()
            if msg['type'] == 'turn' and msg['stage'] in ('done', 'failed', 'cancelled'):
                break


ALL_ON = dict(claude=True, cartesia=True, twelvelabs=True)


def test_all_switches_on_reaches_claude_and_cartesia(calls):
    call(ALL_ON)
    assert 'claude' in calls and 'cartesia' in calls


@pytest.mark.parametrize('switch', ['claude', 'cartesia', 'twelvelabs'])
def test_a_switch_that_is_off_gets_zero_calls(calls, switch):
    call(dict(ALL_ON, **{switch: False}))
    assert switch not in calls, calls


def test_private_mode_makes_no_cloud_call_at_all(calls):
    call(ALL_ON, private=True)
    assert not [c for c in calls if c in ('claude', 'cartesia', 'twelvelabs')], calls


def test_defaults_are_claude_cartesia_on_twelvelabs_google_off():
    assert privacy.set_call(None) == dict(claude=True, cartesia=True, twelvelabs=False, google=False)
    assert privacy.set_call(None, rewind=True)['twelvelabs'] is True
    assert set(privacy.set_call(ALL_ON, private=True).values()) == {False}
    assert set(privacy.WHAT_LEAVES) == {'claude', 'cartesia', 'twelvelabs', 'google'}


def test_twelvelabs_client_refuses_when_its_switch_is_off(calls):
    privacy.set_call(dict(ALL_ON, twelvelabs=False))
    with pytest.raises(RuntimeError):
        TwelveLabsClient(key='synthetic')._headers()
    privacy.set_call(ALL_ON)
    assert TwelveLabsClient(key='synthetic')._headers() == {'x-api-key': 'synthetic'}
    privacy.set_call(None)


def test_planner_crop_needs_the_claude_switch():
    from server.agent import Session, planner_crop
    from server.test_agent import SCENE, FRAME
    session = Session('s')
    session.frame = dict(FRAME, seq=1)
    privacy.set_call(dict(ALL_ON, claude=False))
    assert planner_crop(session, SCENE, 1, 'this one') == {}
    privacy.set_call(None)


def test_switch_lines_are_plain(calls):
    for line in privacy.WHAT_LEAVES.values():
        assert 'sends' in line or 'uploads' in line
    assert json.dumps(privacy.WHAT_LEAVES)
