"""Private mode and LOCAL_ONLY: nothing leaves our machines, and transcripts never reach the logs."""
import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from server.llm import LLMClient
from server.privacy import PRIVATE, local_only
from server.test_ws2 import app, send
from voice import adapter
from voice.fake import FakeAdapter
from voice.kokoro import KokoroAdapter


def test_local_only_never_picks_cartesia(monkeypatch):
    monkeypatch.setenv('CARTESIA_API_KEY', 'synthetic')
    monkeypatch.setenv('LOCAL_ONLY', '1')
    monkeypatch.setattr(adapter, '_kokoro_available', lambda: True)
    voice = adapter.make_adapter(voice='cartesia:f786b574-daa5-4673-aa0c-cbe3e8534c02')
    assert isinstance(voice, KokoroAdapter)
    asyncio.run(voice.aclose())
    monkeypatch.setattr(adapter, '_kokoro_available', lambda: False)
    assert isinstance(adapter.make_adapter(), FakeAdapter)  # the page speaks with its own voice


def test_private_flag_blocks_cloud_brain_and_voice(monkeypatch):
    monkeypatch.setenv('CARTESIA_API_KEY', 'synthetic')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic')
    monkeypatch.setattr(adapter, '_kokoro_available', lambda: False)

    class Cloud:
        def stream(self, **kwargs):
            raise AssertionError('Claude called in private mode')

    def local(request):
        return httpx.Response(200, text=json.dumps({'message': {'content': 'SAY: local reply'}, 'done': True}) + '\n')

    async def run():
        token = PRIVATE.set(True)
        try:
            assert local_only()
            assert isinstance(await asyncio.to_thread(adapter.make_adapter), FakeAdapter)
            client = LLMClient(anthropic_client=SimpleNamespace(messages=Cloud()),
                               httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(local)))
            return ''.join([d async for d in client.stream('sys', 'user')])
        finally:
            PRIVATE.reset(token)

    assert asyncio.run(run()) == 'SAY: local reply'
    assert not local_only()


def test_private_hello_forces_server_stt_and_rewind_cloud_off(monkeypatch):
    from server.rewind import TwelveLabsClient as TwelveLabs
    seen = {}

    class Box:
        async def stt(self, pcm, language='en'):
            seen['private'] = local_only()
            return 'where does it go'

        async def aclose(self):
            pass

    application = app()
    application.state.box_v2 = Box()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='browser', tts='browser', private=True)
        ws.receive_json()
        send(ws, 'audio_in', seq=1, pcm16_b64='AAA=', final=True)  # accepted: STT is server-side now
        while True:
            msg = ws.receive_json()
            if msg['type'] == 'turn' and msg['stage'] in ('done', 'failed', 'cancelled'):
                break
    assert seen['private'] is True
    token = PRIVATE.set(True)
    try:
        try:
            TwelveLabs(key='synthetic')._headers()
            raise AssertionError('Rewind cloud allowed in private mode')
        except RuntimeError as exc:
            assert 'locked off' in str(exc)
    finally:
        PRIVATE.reset(token)


def test_server_never_logs_transcripts(capsys, caplog):
    secret_typed = 'zebra transcript typed 4821'
    secret_heard = 'violet transcript heard 7730'

    class Box:
        async def stt(self, pcm, language='en'):
            return secret_heard

        async def aclose(self):
            pass

    caplog.set_level(logging.DEBUG)
    application = app()
    application.state.box_v2 = Box()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='server', tts='browser')
        ws.receive_json()
        for kind, fields in (('user_text', dict(text=secret_typed, final=True)),
                             ('audio_in', dict(seq=1, pcm16_b64='AAA=', final=True))):
            send(ws, kind, **fields)
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'turn' and msg['stage'] in ('done', 'failed', 'cancelled'):
                    break
    logged = capsys.readouterr().out + capsys.readouterr().err + caplog.text
    for secret in (secret_typed, secret_heard, 'zebra', 'violet'):
        assert secret not in logged
