import asyncio
import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from server.agent import Session, run_turn
from server import packs
import server.expert as expert_mod
from server.expert import ExpertDesk, OPUS, FABLE, LOCAL, TIMEOUT_LINE, cancel, choose_model, cost, mark_failed, should_escalate
from server.llm import LLMClient
from server.test_agent import Vision


class FakeStream:
    def __init__(self, refusal=False):
        self.refusal = refusal

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    @property
    def text_stream(self):
        async def chunks():
            yield 'Check the fan. '
            yield 'Then check the cable.'
        return chunks()

    async def get_final_message(self):
        return NS(stop_reason='refusal' if self.refusal else 'end_turn', content=[], usage=NS(
            input_tokens=100, output_tokens=20, cache_read_input_tokens=50,
            cache_creation_input_tokens=10, server_tool_use=NS(web_search_requests=2)))


class FakeClient:
    def __init__(self, errors=(), refusal=False):
        self.messages = self
        self.calls = []
        self.errors = list(errors)
        self.refusal = refusal

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            message = self.errors.pop(0)
            if message:
                raise anthropic.BadRequestError(message, response=httpx.Response(
                    400, request=httpx.Request('POST', 'https://example.test')), body={})
        return FakeStream(self.refusal)


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-only')
    monkeypatch.delenv('LOCAL_ONLY', raising=False)
    monkeypatch.delenv('EXPERT_MODEL', raising=False)


@pytest.mark.parametrize('question', ['think hard', 'are you sure?', 'double check this', 'double-check this'])
def test_backstops_and_safety(question):
    s = Session('s')
    assert should_escalate(s, question)
    s.pack = packs.get('pc_build')
    assert not should_escalate(s, question, True)
    s.power_off = True
    assert not should_escalate(s, 'open the PSU, think hard', True)


def test_two_failures_and_models(monkeypatch):
    s = Session('s')
    assert not should_escalate(s, 'hello')
    mark_failed(s, 'fan')
    mark_failed(s, 'ram')
    assert not should_escalate(s, 'hello')
    mark_failed(s, 'fan')
    assert should_escalate(s, 'hello')
    assert choose_model('help') == OPUS
    assert choose_model('x' * 301) == FABLE
    assert choose_model('look up specs') == FABLE
    monkeypatch.setenv('EXPERT_MODEL', OPUS)
    assert choose_model('look up specs') == OPUS
    monkeypatch.setenv('LOCAL_ONLY', '1')
    assert choose_model('look up specs') is None          # no local expert configured by default
    monkeypatch.setattr(expert_mod, 'LOCAL', 'gemma4:12b-it-q4_K_M')
    assert choose_model('look up specs') == 'gemma4:12b-it-q4_K_M'
    monkeypatch.delenv('LOCAL_ONLY')
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    assert choose_model('help') == 'gemma4:12b-it-q4_K_M'


@pytest.mark.parametrize('split', range(1, 9))
def test_split_tag_never_spoken_or_captioned(split):
    async def check():
        starts = []
        class Brain:
            async def stream(self, *args, **kwargs):
                yield '{"draw":[]}\nSAY: '
                yield '[EXPERT]'[:split]
                yield '[EXPERT]'[split:] + 'Let me get the expert. One moment.'
        s = Session('s', camera=False, expert_start=lambda *args: starts.append(args))
        messages = await run_turn(s, 'help', vision=Vision(), llm=Brain())
        texts = [m['text'] for m in messages if m['type'] in ('say', 'reply_delta')]
        assert starts and texts
        assert all('EXPERT' not in t and '[' not in t for t in texts)
        assert ''.join(m['text'] for m in messages if m['type'] == 'reply_delta').startswith('Let me')
    asyncio.run(check())


def test_provider_settings_cache_usage_and_cost():
    async def check():
        fake = FakeClient()
        llm = LLMClient(anthropic_client=fake)
        for question in ['private question one', 'private question two']:
            result = await llm.expert('stable system', question, model=OPUS, images=['AAAA'])
        assert fake.calls[0]['system'] == fake.calls[1]['system']
        assert fake.calls[0]['system'][0]['cache_control'] == {'type': 'ephemeral'}
        options = fake.calls[0]
        assert not {'thinking', 'temperature', 'top_p', 'top_k'} & options.keys()
        assert options['output_config'] == {'effort': 'medium'}
        assert options['tool_choice'] == {'type': 'auto'}
        assert options['tools'] == [{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 3}]
        assert options['messages'][0]['content'][0]['type'] == 'image'
        metrics = result['metrics']
        assert metrics['cache_read_tokens'] == 50 and metrics['web_searches'] == 2
        assert metrics['cost_usd'] == pytest.approx((400 + 400 + 10 + 50) / 1e6)
        assert metrics['search_cost'] == 'UNVERIFIED'
        assert metrics['first_token_ms'] <= metrics['total_ms']
    asyncio.run(check())


@pytest.mark.parametrize('error,expected,tools', [('retention required', OPUS, True), ('web_search tool type rejected', FABLE, False)])
def test_fable_retries(error, expected, tools):
    async def check():
        fake = FakeClient([error])
        result = await LLMClient(anthropic_client=fake).expert('system', 'question', model=FABLE)
        assert len(fake.calls) == 2
        assert fake.calls[0]['output_config'] == {'effort': 'high'}
        assert fake.calls[1]['model'] == expected
        assert ('tools' in fake.calls[1]) == tools
        assert result['metrics']['model'] == expected
    asyncio.run(check())


def test_refusal_and_search_errors():
    async def check():
        fake = FakeClient(refusal=True)
        result = await LLMClient(anthropic_client=fake).expert('system', 'question', model=OPUS)
        assert result['text'] == "I can't help with that request."
        assert 'fan' not in result['text']
    asyncio.run(check())


def test_local_guard_and_no_tools(monkeypatch):
    monkeypatch.setenv('LOCAL_ONLY', '1')
    monkeypatch.setattr(expert_mod, 'LOCAL', 'gemma4:12b-it-q4_K_M')
    async def check():
        fake = FakeClient()
        def handler(request):
            payload = json.loads(request.content)
            assert payload['model'] == 'gemma4:12b-it-q4_K_M'
            assert 'tools' not in payload and 'images' not in str(payload)
            return httpx.Response(200, text=json.dumps({'message': {'content': 'Local answer.'}, 'done': True}) + '\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await LLMClient(anthropic_client=fake, httpx_client=http).expert('system', 'question', model=OPUS, images=['PRIVATE'])
        assert not fake.calls and result['metrics']['model'] == 'gemma4:12b-it-q4_K_M'
    asyncio.run(check())


def test_desk_busy_cooldown_timeout_cancel_and_private_logs(capsys):
    async def check():
        now = [100.0]
        desk = ExpertDesk(clock=lambda: now[0])
        sent = []
        async def send(msg):
            sent.append(msg)
        ready = asyncio.Event()
        class Slow:
            async def expert(self, *args, **kwargs):
                ready.set()
                await asyncio.Event().wait()
        scene = dict(caption='off', objects=[])
        s = Session('s', camera=False)
        desk.start(s, 'PRIVATE', 'base', scene, 0, Slow(), send, 'turn')
        await ready.wait()
        assert 'still on' in desk.start(Session('other'), 'q', 'base', scene, 0, Slow(), send, 'other')
        await cancel(s)
        assert sent[-1]['stage'] == 'expert_done' and sent[-1]['detail'] == 'cancelled'
        assert 'moment' in desk.start(s, 'q', 'base', scene, 0, Slow(), send, 'turn')
        now[0] += 20
        async def timeout(coro, seconds):
            assert seconds == 45
            coro.close()
            raise TimeoutError
        desk.wait_for = timeout
        desk.start(s, 'PRIVATE', 'base', scene, 0, Slow(), send, 'turn')
        await s.expert_task
        assert any(m.get('text') == TIMEOUT_LINE for m in sent)
        assert sent[-1]['detail'] == 'timeout'
        now[0] += 20
        desk.wait_for = asyncio.wait_for
        desk.start(s, 'PRIVATE', 'base', scene, 0, LLMClient(anthropic_client=FakeClient()), send, 'turn')
        await s.expert_task
        assert any(m.get('text', '').startswith('The expert says:') for m in sent)
        assert not desk.busy
    asyncio.run(check())
    output = capsys.readouterr().out
    assert 'PRIVATE' not in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert any(row['event'] == 'expert_call' and (row['cost_usd'] or 0) > 0 for row in rows)


@pytest.mark.parametrize('action', ['barge_in', 'stop'])
def test_socket_expert_survives_next_turn_and_cancels(monkeypatch, action):
    from fastapi.testclient import TestClient
    from server.test_ws2 import app, send
    from server.test_pipeline import read, finish
    from server import ws2
    async def warm(app):
        pass
    monkeypatch.setattr(ws2, 'warm_brain', warm)
    monkeypatch.setattr(ws2, 'expert_desk', ExpertDesk())
    class Brain:
        async def stream(self, *args, **kwargs):
            yield '{"draw":[]}\nSAY: Let me check that for you.'
        async def expert(self, *args, **kwargs):
            await asyncio.Event().wait()
    application = app()
    application.state.llm_v2 = Brain()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', camera=False)
        read(ws)
        send(ws, 'user_text', text='think hard', final=True)
        messages = []
        while not any(m.get('stage') == 'expert' for m in messages) or not any(m.get('stage') == 'done' for m in messages):
            messages.append(read(ws))
        send(ws, 'user_text', text='hello', final=True)
        messages = finish(ws)
        assert not any(m.get('stage') == 'expert_done' for m in messages)
        if action == 'barge_in':
            send(ws, 'barge_in', utterance_id='any', at_ms=0)
        else:
            send(ws, 'user_text', text='stop', final=True)
        while True:
            msg = read(ws)
            if msg.get('stage') == 'expert_done':
                assert msg['detail'] == 'cancelled'
                break


def test_cancel_immediately_releases_reservation():
    async def check():
        desk = ExpertDesk()
        sent = []
        async def send(msg):
            sent.append(msg)
        class Brain:
            async def expert(self, *a, **kw):
                await asyncio.Event().wait()
        s = Session('s', camera=False)
        desk.start(s, 'q', 'system', {'objects': []}, 0, Brain(), send, 't')
        await cancel(s)
        assert not desk.busy
        assert sent[-1]['detail'] == 'cancelled'
    asyncio.run(check())


def test_search_result_errors_are_metadata(monkeypatch):
    async def final(self):
        return NS(stop_reason='end_turn', content=[NS(type='web_search_tool_result', content=NS(
            type='web_search_tool_result_error', error_code='unavailable'))], usage=None)
    monkeypatch.setattr(FakeStream, 'get_final_message', final)
    result = asyncio.run(LLMClient(anthropic_client=FakeClient()).expert('system', 'question', model=OPUS))
    assert result['metrics']['web_search_errors'] == 1


def test_stream_caches_stable_prompt_and_records_cache_reads(monkeypatch):
    from server.llm import TURN_USAGE
    monkeypatch.setenv('LLM_PROVIDER', 'claude')
    async def check():
        counts = []
        token = TURN_USAGE.set(counts)
        try:
            fake = FakeClient()
            llm = LLMClient(anthropic_client=fake)
            for user in ['first question', 'second question']:
                assert ''.join([delta async for delta in llm.stream('stable', user)])
            assert fake.calls[0]['system'] == fake.calls[1]['system']
            assert fake.calls[0]['system'][0]['cache_control'] == {'type': 'ephemeral'}
            assert sum(c['cache_read_tokens'] for c in counts) == 100
        finally:
            TURN_USAGE.reset(token)
    asyncio.run(check())


def test_expert_crop_and_planner_draw(monkeypatch):
    from server.test_crops import frame_b64
    from server.agent import message
    async def check():
        sent, spoken, requests = [], [], []
        scene = {'caption': 'a fan', 'objects': [{'id': 'fan', 'label': 'fan', 'bbox': [.4, .4, .1, .1], 'conf': .9}], 'ms': 0}
        async def send(msg):
            sent.append(msg)
        async def speech(msg):
            spoken.append(msg['text'])
            yield message('audio', agent='helper', utterance_id=msg['utterance_id'], sample_rate=16000, pcm16_b64='', final=True)
        class Brain:
            async def expert(self, system, user, **kw):
                requests.append(kw)
                assert 'transcript' in json.loads(user)
                return {'text': 'Check the fan.', 'metrics': {'model': OPUS}}
            async def stream(self, *args, **kwargs):
                yield '{"agent":"helper","draw":[{"kind":"box","target":"fan"}]}'
        s = Session('s', frame={'seq': 1, 'jpeg_b64': frame_b64()}, scene=scene, scene_seq=1, speak=speech)
        desk = ExpertDesk()
        brain = Brain()
        desk.start(s, 'fan', 'base', scene, 1, brain, send, 'turn', brain)
        await s.expert_task
        assert requests[0]['images'][0] != s.frame['jpeg_b64']
        assert spoken == ['The expert says: Check the fan.']
        assert any(m['type'] == 'draw' and len(m['ops']) == 2 for m in sent)
        assert sent[-1]['stage'] == 'expert_done'
        s.camera = False
        desk.finished = float('-inf')
        desk.start(s, 'fan', 'base', scene, 1, brain, send, 'turn', brain)
        await s.expert_task
        assert 'images' not in requests[-1]
    asyncio.run(check())


def test_local_expert_uses_the_real_ollama_tag_and_checks_it(monkeypatch):
    """Seen by the local-brain builder: 'qwen3.5:9b' does not exist on the box; the tag is qwen3.5:9b-q4_K_M."""
    from server.expert import check_local_model
    monkeypatch.setattr(expert_mod, 'LOCAL', 'qwen3.5:9b-q4_K_M')
    monkeypatch.setattr(expert_mod, 'VISION_MODELS', set())
    assert check_local_model(lambda url: {'models': [{'name': 'qwen3.5:9b-q4_K_M'}]}) is True
    assert check_local_model(lambda url: {'models': [{'name': 'qwen3.5:9b'}]}) is False
    assert check_local_model(lambda url: (_ for _ in ()).throw(OSError('down'))) is None


def test_local_expert_is_never_the_vision_model(monkeypatch):
    """Seen live: the local expert on qwen3.5:9b slowed vision grounding from 0.73 s to 11 to 24 s for minutes."""
    from server.expert import local_expert, OFFLINE_LINE
    monkeypatch.setattr(expert_mod, 'LOCAL', 'qwen3.5:9b-q4_K_M')
    assert local_expert() is None                      # the default ground model is a vision model
    monkeypatch.setattr(expert_mod, 'LOCAL', '')
    assert local_expert() is None                      # unset means no local expert
    monkeypatch.setattr(expert_mod, 'LOCAL', 'gemma4:12b-it-q4_K_M')
    assert local_expert() == 'gemma4:12b-it-q4_K_M'
    assert 'cloud' in OFFLINE_LINE
