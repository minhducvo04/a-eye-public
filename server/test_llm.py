import logging
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from server.llm import LLMClient


class FakeAnthropic:
    def __init__(self, error=None, refusal=False):
        self.calls = []
        self.error = error
        self.messages = self
        self.response = SimpleNamespace(
            content=[SimpleNamespace(type='thinking'),
                     SimpleNamespace(type='text', text='First.'),
                     SimpleNamespace(type='text', text='Second.')],
            stop_reason='refusal' if refusal else 'end_turn')

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL', 'LLM_PROVIDER',
                 'LOCAL_ONLY', 'OLLAMA_URL', 'LOCAL_LLM_MODEL'):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def local():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'message': {'content': 'Local answer.'}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        yield client, calls


@pytest.mark.parametrize('key,provider', [(True, 'claude'), (False, 'local')])
def test_auto(monkeypatch, local, key, provider):
    if key:
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-key')
    fake = FakeAnthropic()
    result = LLMClient(anthropic_client=fake, httpx_client=local[0]).complete('system', 'question')
    assert result.provider == provider
    assert result.ms >= 0
    if key:
        assert result.text == 'First.\nSecond.'
        assert fake.calls == [dict(model='claude-sonnet-5-5', max_tokens=1024,
                                   system='system', messages=[{'role': 'user', 'content': 'question'}])]
        assert not local[1]
    else:
        assert not fake.calls
        assert result.model == 'qwen3.6:35b-a3b-q4_K_M'  # not the vision model


@pytest.mark.parametrize('kind', ['connection', 'rate', 'status', 'unexpected', 'refusal'])
def test_claude_fallback(monkeypatch, local, kind):
    monkeypatch.setenv('LLM_PROVIDER', 'claude')
    response = httpx.Response(429, request=httpx.Request('POST', 'https://example.test'))
    errors = {'connection': anthropic.APIConnectionError(request=response.request),
              'rate': anthropic.RateLimitError('failed', response=response, body=None),
              'status': anthropic.APIStatusError('failed', response=response, body=None),
              'unexpected': RuntimeError('failed')}
    fake = FakeAnthropic(error=errors.get(kind), refusal=kind == 'refusal')
    result = LLMClient(anthropic_client=fake, httpx_client=local[0]).complete('', 'Question?')
    assert result.provider == 'local'
    assert result.text == 'Local answer.'
    assert len(fake.calls) == len(local[1]) == 1


@pytest.mark.parametrize('failure', ['http', 'timeout', 'malformed'])
def test_local_failure(monkeypatch, failure):
    monkeypatch.setenv('LLM_PROVIDER', 'local')

    def respond(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('failed', request=request)
        return httpx.Response(503 if failure == 'http' else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = LLMClient(httpx_client=client).complete('', 'First sentence. Second sentence.')
    assert result.provider == result.model == 'canned'
    assert result.text == 'First sentence.'


@pytest.mark.parametrize('provider', ['auto', 'claude'])
@pytest.mark.parametrize('images', [None, ['YQ==']])
def test_local_only(monkeypatch, local, provider, images):
    monkeypatch.setenv('LLM_PROVIDER', provider)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-key')
    monkeypatch.setenv('LOCAL_ONLY', '1')
    fake = FakeAnthropic()
    assert LLMClient(anthropic_client=fake, httpx_client=local[0]).complete('', 'Q', images=images).provider == 'local'
    assert not fake.calls


@pytest.mark.parametrize('sizes,provider', [([400000], 'claude'), ([400004], 'local'),
                                          ([200004, 200004], 'local')])
def test_image_limit(monkeypatch, local, sizes, provider):
    monkeypatch.setenv('LLM_PROVIDER', 'claude')
    fake = FakeAnthropic()
    images = ['A' * size for size in sizes]
    result = LLMClient(anthropic_client=fake, httpx_client=local[0]).complete('', 'Q', images=images)
    assert result.provider == provider
    assert bool(fake.calls) == (provider == 'claude')


def test_local_payload(monkeypatch, local):
    import json

    monkeypatch.setenv('OLLAMA_URL', 'http://localhost:11434/')
    monkeypatch.setenv('LOCAL_LLM_MODEL', 'custom-local')
    result = LLMClient(httpx_client=local[0]).complete('System', 'Question', max_tokens=23, images=['YQ=='])
    request = local[1][0]
    assert str(request.url) == 'http://localhost:11434/api/chat'
    assert json.loads(request.content) == {
        'model': 'custom-local', 'stream': False, 'think': False, 'options': {'num_ctx': 4096, 'num_gpu': 0, 'num_thread': 8, 'num_predict': 23}, 'keep_alive': '2h',
        'messages': [{'role': 'system', 'content': 'System'},
                     {'role': 'user', 'content': 'Question', 'images': ['YQ==']}]}
    assert request.extensions['timeout']['read'] == 20
    assert result.model == 'custom-local'


def test_no_secret_output(monkeypatch, local, caplog):
    key = 'synthetic-private-key-not-for-logs'
    monkeypatch.setenv('ANTHROPIC_API_KEY', key)
    fake = FakeAnthropic(error=RuntimeError(key))
    client = LLMClient(anthropic_client=fake, httpx_client=local[0])
    with caplog.at_level(logging.DEBUG):
        result = client.complete('', 'Question')
    assert key not in caplog.text + repr(client) + repr(result)


def test_canned(monkeypatch, local):
    monkeypatch.setenv('LLM_PROVIDER', 'canned')
    fake = FakeAnthropic()
    result = LLMClient(anthropic_client=fake, httpx_client=local[0]).complete('', '  ')
    assert result.text == 'OK.'
    assert not fake.calls and not local[1]


@pytest.mark.parametrize('cloud_ok,local_ok', [(True, True), (False, True), (False, False)])
def test_stream_fallback_order(monkeypatch, cloud_ok, local_ok):
    import asyncio
    import json
    from contextlib import asynccontextmanager

    monkeypatch.setenv('LLM_PROVIDER', 'claude')
    calls = []

    class Cloud:
        @asynccontextmanager
        async def stream(self, **kwargs):
            calls.append('claude')
            assert kwargs['max_tokens'] == 512
            if not cloud_ok:
                raise RuntimeError('offline')

            async def chunks():
                yield 'Cloud '
                yield 'answer.'
            yield SimpleNamespace(text_stream=chunks())

    def handler(request):
        calls.append('local')
        payload = json.loads(request.content)
        assert payload['stream'] is True and payload['think'] is False
        return httpx.Response(200 if local_ok else 503,
                              text='{"message":{"content":"Local "}}\n'
                                   '{"message":{"content":"answer."},"done":true}\n')

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as local:
            client = LLMClient(anthropic_client=SimpleNamespace(messages=Cloud()), httpx_client=local)
            return [chunk async for chunk in client.stream('', 'Canned.')]

    chunks = asyncio.run(check())
    assert ''.join(chunks) == ('Cloud answer.' if cloud_ok else 'Local answer.' if local_ok else 'Canned.')
    assert calls == (['claude'] if cloud_ok else ['claude', 'local'])


def test_stream_local_only_and_canned(monkeypatch):
    import asyncio
    monkeypatch.setenv('LLM_PROVIDER', 'canned')
    async def check():
        return [chunk async for chunk in LLMClient().stream('', '')]
    assert asyncio.run(check()) == ['OK.']


def test_private_talker_is_never_the_vision_model_and_waits_for_a_cpu_model(monkeypatch):
    """Seen live: Private mode's talker ran on qwen3.5:9b (the vision model, +62% on grounding), and a cold
    local model answered 'The brain is unavailable' after about 11 s."""
    import asyncio
    import json
    from server import llm as llm_mod
    assert llm_mod.local_model() not in llm_mod.VISION_MODELS
    assert llm_mod.local_options(10)['num_gpu'] == 0
    monkeypatch.setenv('LLM_PROVIDER', 'local')
    monkeypatch.setenv('LOCAL_FIRST_TOKEN_S', '15')

    async def slow_body():
        await asyncio.sleep(4)        # longer than the Claude first-token timeout (3.5 s), as a cold CPU model is
        yield (json.dumps({'message': {'content': 'Medium high heat.'}}) + '\n').encode()
        yield (json.dumps({'done': True}) + '\n').encode()

    async def handler(request):
        return httpx.Response(200, content=slow_body())

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            text = ''.join([d async for d in llm_mod.LLMClient(httpx_client=http).stream('s', 'How hot to sear?')])
        assert text == 'Medium high heat.'
    asyncio.run(check())
