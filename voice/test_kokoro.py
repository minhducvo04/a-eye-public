import asyncio
import json

import httpx
import pytest

from voice import adapter
from voice.kokoro import KokoroAdapter
from voice.cartesia import CartesiaAdapter
from voice.fake import FakeAdapter


@pytest.mark.parametrize('error', [httpx.ConnectTimeout, httpx.ConnectError])
def test_retry_connection_failure_before_speech(error):
    async def check():
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise error('synthetic connect failure', request=request)
            return httpx.Response(200, content=b'\x01\x00' * 2048)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            voice = KokoroAdapter(client=client)
            chunks = [chunk async for chunk in voice.say('u', 'Hello.')]
            assert b''.join(chunks) == b'\x01\x00' * 2048
            assert len(calls) == 2
    asyncio.run(check())


@pytest.mark.parametrize('error, attempts', [(httpx.ConnectTimeout, 2), (httpx.ReadTimeout, 1)])
def test_retry_is_bounded_and_never_replays_reads(error, attempts):
    async def check():
        calls = []
        def handler(request):
            calls.append(request)
            raise error('synthetic transport failure', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            voice = KokoroAdapter(client=client)
            with pytest.raises(error):
                _ = [chunk async for chunk in voice.say('u', 'Hello.')]
            assert len(calls) == attempts
            assert voice.tasks == {}
    asyncio.run(check())


@pytest.mark.parametrize('failures', [1, 2])
def test_transient_health_failure_does_not_disable_voice_for_a_minute(monkeypatch, failures):
    monkeypatch.setenv('BOX_URL', 'http://health-recovery.test')
    monkeypatch.setattr(adapter, '_health_cache', {})
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) <= failures:
            raise httpx.ConnectTimeout('synthetic health timeout', request=request)
        return httpx.Response(200, json=dict(tts=True))
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    assert adapter._kokoro_available() is (failures == 1)
    assert adapter._kokoro_available() is True
    assert len(calls) == failures + 1


@pytest.mark.parametrize('stop', [False, True])
def test_stream_and_stop(stop):
    async def check():
        def handler(request):
            assert request.url.path == '/tts'
            assert json.loads(request.content)['text'] == 'Hello.'
            return httpx.Response(200, content=b'\x01\x00' * 4096)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            voice = KokoroAdapter(client=client)
            chunks = []
            async for chunk in voice.say('u', 'Hello.'):
                chunks.append(chunk)
                if stop:
                    voice.stop('u')
            assert len(b''.join(chunks)) == (2048 if stop else 8192)
            assert voice.last_event['type'] == ('barge_in' if stop else 'spoken')
    asyncio.run(check())


def test_cancel_blocked_stream_closes_response():
    async def check():
        started, closed = asyncio.Event(), asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b'\x00\x00'
            async def aclose(self):
                closed.set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Stream()))) as client:
            voice = KokoroAdapter(client=client)
            async def speak():
                return [chunk async for chunk in voice.say('u', 'Hello.')]
            task = asyncio.create_task(speak())
            await asyncio.wait_for(started.wait(), 1)
            voice.stop('u')
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), .2)
            assert closed.is_set() and task.cancelled()
    asyncio.run(check())


def test_auto_order_and_health_cache(monkeypatch):
    monkeypatch.setenv('VOICE', 'auto')
    monkeypatch.setenv('BOX_URL', 'http://box.test')
    monkeypatch.setenv('CARTESIA_API_KEY', 'synthetic')
    calls = []
    available = True
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=dict(tts=available))
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    adapter._health_cache.clear()
    from voice.cartesia_ws import CartesiaWsAdapter
    voice = adapter.make_adapter()  # Cartesia when keyed, with Kokoro as its fallback
    assert isinstance(voice, CartesiaWsAdapter) and isinstance(voice.backup, KokoroAdapter)
    asyncio.run(voice.aclose())
    picked = adapter.make_adapter(voice='af_kore')  # a Kokoro voice asks for Kokoro
    assert isinstance(picked, KokoroAdapter)
    asyncio.run(picked.aclose())
    assert adapter._kokoro_available()
    assert len(calls) == 1
    available = False
    adapter._health_cache.clear()
    fallbackless = adapter.make_adapter()
    assert isinstance(fallbackless, CartesiaWsAdapter) and fallbackless.backup is None
    monkeypatch.delenv('CARTESIA_API_KEY')
    assert isinstance(adapter.make_adapter(), FakeAdapter)
    assert len(calls) == 2
    adapter._health_cache.clear()
