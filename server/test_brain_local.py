import asyncio
import json

import httpx

from server import brain_local
from server.brain_local import LocalBrain, make_llm
from server.llm import LLMClient


def ndjson(*chunks):
    return '\n'.join(json.dumps(chunk) for chunk in chunks) + '\n'


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def collect(brain, *args, **kwargs):
    return [delta async for delta in brain.stream(*args, **kwargs)]


def test_switch(monkeypatch):
    monkeypatch.delenv('BRAIN', raising=False)
    assert type(make_llm()) is LLMClient
    monkeypatch.setenv('BRAIN', 'local')
    assert isinstance(make_llm(), LocalBrain)


def test_model_roles(monkeypatch):
    for key in ('BRAIN_TALKER_MODEL', 'BRAIN_PLANNER_MODEL', 'BRAIN_EXPERT_MODEL'):
        monkeypatch.delenv(key, raising=False)
    assert brain_local.local_name(None) == brain_local.TALKER
    monkeypatch.setenv('BRAIN_TALKER_MODEL', 't')
    monkeypatch.setenv('BRAIN_EXPERT_MODEL', 'e')
    assert brain_local.local_name('claude-haiku-5-5') == 't'
    assert brain_local.local_name('claude-sonnet-5-5') == 't'  # planner defaults to the talker
    monkeypatch.setenv('BRAIN_PLANNER_MODEL', 'p')
    assert brain_local.local_name('claude-sonnet-5-5') == 'p'
    assert brain_local.local_name('claude-opus-5-5', 'expert') == 'e'
    assert brain_local.local_name('gemma4:31b-it-q4_K_M') == 'gemma4:31b-it-q4_K_M'


def test_stream_talks_to_ollama_only(monkeypatch):
    monkeypatch.setenv('BRAIN_TALKER_MODEL', 'qwen-test')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'x' * 10)  # must not be used
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, text=ndjson({'message': {'content': 'SAY: Hi'}},
                                               {'message': {'content': ' there.'}, 'done': True}))

    out = asyncio.run(collect(LocalBrain(httpx_client=client(handler)), 'sys', 'hello',
                              model='claude-haiku-5-5'))
    assert out == ['SAY: Hi', ' there.']
    path, body = seen[0]
    assert path == '/api/chat'
    assert body['model'] == 'qwen-test' and body['think'] is False and body['stream'] is True
    assert body['messages'][0] == {'role': 'system', 'content': 'sys'}


def test_stream_retries_without_images_then_cans(monkeypatch):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append('images' in body['messages'][1])
        if 'images' in body['messages'][1]:
            return httpx.Response(400, json={'error': 'model does not support images'})
        return httpx.Response(200, text=ndjson({'message': {'content': 'SAY: Ok.'}, 'done': True}))

    out = asyncio.run(collect(LocalBrain(httpx_client=client(handler)), 's', 'u', images=['abc']))
    assert calls == [True, False] and out == ['SAY: Ok.']

    def down(request):
        raise httpx.ConnectError('down')

    out = asyncio.run(collect(LocalBrain(httpx_client=client(down)), 's', json.dumps({'text': 'x'})))
    assert out == ['{"agent":"helper","draw":[]}\nSAY: The brain is unavailable. Please try again.']


def test_expert_shape_and_think_fallback(monkeypatch):
    monkeypatch.setenv('BRAIN_EXPERT_MODEL', 'big')
    thinks = []

    def handler(request):
        body = json.loads(request.content)
        thinks.append(body['think'])
        if body['think']:
            return httpx.Response(400, json={'error': 'does not support thinking'})
        return httpx.Response(200, text=ndjson(
            {'message': {'content': 'Use the '}},
            {'message': {'content': 'CPU_FAN header.'}, 'done': True, 'prompt_eval_count': 50, 'eval_count': 6}))

    result = asyncio.run(LocalBrain(httpx_client=client(handler)).expert('s', 'q', model='claude-opus-5-5'))
    assert thinks == [True, False]
    assert result['text'] == 'Use the CPU_FAN header.'
    metrics = result['metrics']
    assert metrics['model'] == 'big' and metrics['cost_usd'] == 0.0
    assert metrics['input_tokens'] == 50 and metrics['output_tokens'] == 6
    assert metrics['first_token_ms'] is not None


def test_options_keep_shared_context_and_offload(monkeypatch):
    for key in ('BRAIN_NUM_CTX', 'BRAIN_NUM_GPU', 'BRAIN_NUM_THREAD', 'VISION_GROUND_MODEL'):
        monkeypatch.delenv(key, raising=False)
    big = brain_local._payload('big', 's', 'u', None, 10, False)['options']
    assert big == {'num_ctx': 4096, 'num_gpu': 0, 'num_thread': 8, 'num_predict': 10}
    # The app's vision model keeps exactly the app's options, or Ollama reloads it.
    assert brain_local._payload('qwen3.5:9b-q4_K_M', 's', 'u', None, 10, False)['options'] == {
        'num_ctx': 4096, 'num_predict': 10}
    monkeypatch.setenv('BRAIN_NUM_GPU', 'auto')
    monkeypatch.setenv('BRAIN_NUM_THREAD', '12')
    assert brain_local._payload('big', 's', 'u', None, 0, False)['options'] == {'num_ctx': 4096, 'num_thread': 12}


def test_expert_refuses_the_vision_model(monkeypatch):
    monkeypatch.setenv('BRAIN_EXPERT_MODEL', 'qwen3.5:9b-q4_K_M')
    monkeypatch.delenv('VISION_GROUND_MODEL', raising=False)

    def handler(request):
        raise AssertionError('must not call Ollama')

    result = asyncio.run(LocalBrain(httpx_client=client(handler)).expert('s', 'q'))
    assert result['metrics']['reason'] == 'vision_model'
