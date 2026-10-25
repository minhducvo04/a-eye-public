"""In-process fake HTTP provider; no external network or paid calls."""
import asyncio
import json

import httpx
import pytest

from server import brain_openai_compat as compat, privacy
from server.brain_local import make_llm
from server.expert import choose_model


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for key, value in dict(BRAIN='openai_compat', OPENAI_COMPAT_BASE_URL='https://fake.invalid/v1',
                           OPENAI_COMPAT_API_KEY='test-secret', OPENAI_COMPAT_TALKER_MODEL='talk',
                           OPENAI_COMPAT_EXPERT_MODEL='expert', LOCAL_ONLY='0').items():
        monkeypatch.setenv(key, value)
    private = privacy.PRIVATE.set(False)
    switches = privacy.SWITCHES.set(None)
    yield
    privacy.PRIVATE.reset(private)
    privacy.SWITCHES.reset(switches)


def provider(seen):
    def handle(request):
        body = json.loads(request.content)
        seen.append((request, body))
        if request.url.path == '/api/chat':
            if not body['stream']:
                return httpx.Response(200, json={'message': {'content': 'Home.'}})
            return httpx.Response(200, text=json.dumps({'message': {'content': 'Home.'}, 'done': True}) + '\n')
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-secret'
        if not body['stream']:
            return httpx.Response(200, json={'choices': [{'message': {'content': 'Complete.'}}]})
        return httpx.Response(200, text=': heartbeat\n\n' + ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in [
            {'choices': [{'delta': {'role': 'assistant'}}]},
            {'choices': [{'delta': {'content': 'Hello '}}]},
            {'choices': [{'delta': {'content': 'world.'}}]},
            {'choices': [], 'usage': {'prompt_tokens': 8, 'completion_tokens': 2}},
        ]) + 'data: [DONE]\n\n')
    return handle


async def collect(brain, **kwargs):
    return [delta async for delta in brain.stream('system', 'question', **kwargs)]


def test_factory_roles_stream_complete_and_expert():
    seen = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider(seen))) as client:
            brain = make_llm(httpx_client=client)
            assert isinstance(brain, compat.OpenAICompatBrain)
            assert await collect(brain, model='claude-haiku-5-5', images=['private-crop']) == ['Hello ', 'world.']
            assert await collect(brain, model='claude-sonnet-5-5') == ['Hello ', 'world.']
            result = await brain.expert('s', 'q', model='claude-opus-5-5')
            assert result['text'] == 'Hello world.'
            assert result['metrics']['input_tokens'] == 8
            assert result['metrics']['cost_usd'] is None
    asyncio.run(run())
    with httpx.Client(transport=httpx.MockTransport(provider(seen))) as client:
        assert make_llm(httpx_client=client).complete('s', 'q').text == 'Complete.'
    assert [b['model'] for _, b in seen] == ['talk', 'talk', 'expert', 'talk']
    assert 'private-crop' not in str([b for _, b in seen])
    assert choose_model('think hard') == 'expert'


@pytest.mark.parametrize('guard', ['private', 'switch', 'local_only', 'missing_key'])
def test_all_interfaces_block_cloud(guard, monkeypatch):
    if guard == 'private':
        privacy.PRIVATE.set(True)
    elif guard == 'switch':
        privacy.SWITCHES.set(dict(privacy.DEFAULTS, claude=False))
    else:
        monkeypatch.setenv('LOCAL_ONLY' if guard == 'local_only' else 'OPENAI_COMPAT_API_KEY',
                           '1' if guard == 'local_only' else '')
    seen = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider(seen))) as client:
            brain = make_llm(httpx_client=client)
            assert await collect(brain) == ['Home.']
            assert (await brain.expert('s', 'q'))['text'] == 'Home.'
    asyncio.run(run())
    with httpx.Client(transport=httpx.MockTransport(provider(seen))) as client:
        assert make_llm(httpx_client=client).complete('s', 'q').text == 'Home.'
    assert len(seen) == 3
    assert all(r.url.path == '/api/chat' and 'authorization' not in r.headers for r, _ in seen)


def test_timeout_and_provider_error_do_not_leak(monkeypatch, capsys):
    monkeypatch.setattr(compat, 'TIMEOUT', .01)
    monkeypatch.setattr(compat, 'EXPERT_TIMEOUT', .01)
    async def slow(request):
        await asyncio.sleep(1)
        raise AssertionError('should time out')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            brain = make_llm(httpx_client=client)
            assert await collect(brain) == ['question']
            with pytest.raises(RuntimeError, match='Hosted expert unavailable'):
                await brain.expert('s', 'q')
    asyncio.run(run())
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text='test-secret'))) as client:
        assert make_llm(httpx_client=client).complete('s', 'q').provider == 'canned'
    assert 'test-secret' not in capsys.readouterr().out


def test_application_uses_factory(monkeypatch):
    monkeypatch.setenv('A_EYE_MOCK', '0')
    from server.app import create_app
    app = create_app()
    assert isinstance(app.state.llm_v2, compat.OpenAICompatBrain)
    assert app.state.llm is app.state.llm_v2
