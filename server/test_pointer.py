"""Pointer acceptance: streamed metadata, local grounding, abstention and deadline."""
import asyncio
import json
from time import monotonic

import httpx
import pytest

from contracts import validate
from server.agent import Session, run_turn
from server.test_agent import FRAME


LABEL = 'blue mug on the left'
BOX = [.1, .2, .2, .3]
CAPTION = 'A blue mug on the left and a red mug on the right.'


class Brain:
    def __init__(self, text=None, chunk=1):
        self.text = text or f'Use the {LABEL}. [POINT: {LABEL}]'
        self.chunk = chunk

    async def stream(self, system, user, **kwargs):
        yield '{"agent":"helper","draw":[{"kind":"box","target":"old"}]}\nSAY: '
        for i in range(0, len(self.text), self.chunk):
            yield self.text[i:i + self.chunk]


class Vision:
    def __init__(self, *, conf=.7, source='ollama', caption=CAPTION, delay=0):
        self.calls = []
        self.delay = delay
        self.cancelled = False
        self.result = dict(caption=caption, source=source, ms=1, objects=[
            dict(id='o1', label=LABEL, bbox=BOX, conf=conf)])

    async def call(self, operation, payload):
        validate('scene_request', payload)
        self.calls.append(payload)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


def session(**kwargs):
    return Session('pointer', frame=dict(FRAME, seq=10), **kwargs)


def shapes(messages):
    return [op for msg in messages if msg['type'] == 'draw' for op in msg['ops'] if op['kind'] != 'clear']


@pytest.mark.parametrize('chunk', [1, 7, 1000])
def test_tag_never_reaches_speech_caption_or_history_and_draw_is_contract_valid(chunk):
    async def check():
        spoken = []

        async def speak(msg):
            spoken.append(msg['text'])
            if False:
                yield None

        s, vision = session(speak=speak), Vision()
        messages = await run_turn(s, 'Which mug?', vision=vision, llm=Brain(chunk=chunk), route='now')
        for msg in messages:
            validate('server_message_v2', msg)
        assert spoken == [f'Use the {LABEL}.']
        assert '[POINT' not in json.dumps(messages) + str(s.history)
        ops = shapes(messages)
        assert [op['kind'] for op in ops] == ['box', 'arrow']
        assert ops[0]['bbox'] == BOX
        assert ops[1]['to'] == pytest.approx([.2, .35])
        assert all(op['role'] == 'target' and 0 < op['ttl_ms'] <= 3000 for op in ops)
        drawing = next(m for m in messages if m['type'] == 'draw' and len(m['ops']) > 1)
        assert drawing['seq'] == 10
        assert vision.calls[0]['queries'] == [LABEL]
        assert len(vision.calls) == 1
    asyncio.run(check())


@pytest.mark.parametrize('options', [dict(conf=.59), dict(conf=float('nan')), dict(source='fake'), dict(source='claude'),
                                    dict(caption='An empty table.'), dict(caption='The blue mug is not visible.'),
                                    dict(caption='A blurry blue mug on the left with glare.')])
def test_unsure_means_no_draw(options):
    messages = asyncio.run(run_turn(session(), 'Which mug?', vision=Vision(**options), llm=Brain(), route='now'))
    assert not shapes(messages)


@pytest.mark.parametrize('suffix', ['', '[POINT: blue mug', '[POINT: ' + 'mug ' * 100 + ']',
                                   '[POINT ' + 'mug ' * 100 + ']',
                                   f'[POINT: {LABEL}][POINT: red mug]', '[POINT: red mug]'])
def test_missing_malformed_multiple_or_unspoken_target_abstains(suffix):
    vision = Vision()
    messages = asyncio.run(run_turn(session(), 'Which mug?', vision=vision,
                                   llm=Brain(f'Use the {LABEL}. {suffix}'), route='now'))
    assert not shapes(messages)
    assert not vision.calls
    assert [m['text'] for m in messages if m['type'] == 'say'] == [f'Use the {LABEL}.']


def test_hard_budget_and_speech_does_not_wait_for_grounding(monkeypatch):
    from server import pointer
    monkeypatch.setattr(pointer, 'BUDGET_S', .04)

    async def check():
        sent = []
        async def emit(msg):
            sent.append((monotonic(), msg))
        vision = Vision(delay=10)
        start = monotonic()
        messages = await run_turn(session(emit=emit), 'Which mug?', vision=vision, llm=Brain(), route='now')
        assert .04 <= monotonic() - start < .2
        assert vision.cancelled and not shapes(messages)
        assert next(at for at, msg in sent if msg['type'] == 'say') - start < .04
    asyncio.run(check())


def test_stale_frame_or_camera_off_never_queries():
    for s in (session(frame_time=monotonic() - 2), session(camera=False)):
        vision = Vision()
        messages = asyncio.run(run_turn(s, 'Which mug?', vision=vision, llm=Brain(), route='now'))
        assert not shapes(messages) and not vision.calls


def test_plural_caption_still_grounds_full_colour_and_side_query():
    vision = Vision(caption='Three colorful mugs with coffee on a beige background.')
    messages = asyncio.run(run_turn(session(), 'Which mug?', vision=vision, llm=Brain(), route='now'))
    assert len(shapes(messages)) == 2
    assert vision.calls[0]['queries'] == [LABEL]


def test_duo_uses_talker_target_without_a_second_planner_call():
    class Talker:
        async def stream(self, system, user, **kwargs):
            assert '[POINT:' in system
            yield f'SAY: Use the {LABEL}. [POINT: {LABEL}]'

    class NoPlanner:
        async def stream(self, *args, **kwargs):
            raise AssertionError('General pointer must not run a separate planner')
            yield ''

    messages = asyncio.run(run_turn(session(), 'Which mug?', vision=Vision(), llm=Talker(),
                                   planner=NoPlanner(), route='now'))
    assert len(shapes(messages)) == 2


def test_cancellation_leaves_no_late_pointer():
    async def check():
        vision = Vision(delay=10)
        sent = []
        async def emit(msg):
            sent.append(msg)
        task = asyncio.create_task(run_turn(session(emit=emit), 'Which mug?', vision=vision,
                                            llm=Brain(), route='now'))
        while not vision.calls:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert vision.cancelled and not shapes(sent)
    asyncio.run(check())


def test_plain_local_reply_streams_before_the_target_tag_arrives():
    async def check():
        release, spoken = asyncio.Event(), asyncio.Event()

        class Plain:
            async def stream(self, *args, **kwargs):
                yield f'Use the {LABEL}. More'
                await release.wait()
                yield f' details. [POINT: {LABEL}]'

        async def emit(msg):
            if msg['type'] == 'say':
                spoken.set()

        task = asyncio.create_task(run_turn(session(emit=emit), 'Which mug?', vision=Vision(),
                                            llm=Plain(), route='now'))
        try:
            await asyncio.wait_for(spoken.wait(), .1)
            assert not task.done()
            release.set()
            assert len(shapes(await task)) == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_private_uses_real_local_talker_path_and_local_grounding(monkeypatch):
    from server.llm import LLMClient
    from server.privacy import PRIVATE
    monkeypatch.setenv('LLM_PROVIDER', 'claude')
    seen = []

    class NoCloud:
        @property
        def messages(self):
            raise AssertionError('Private mode attempted Claude')

    def handler(request):
        assert request.url.path == '/api/chat'
        seen.append(json.loads(request.content))
        text = '{"agent":"helper","draw":[]}\nSAY: ' + Brain().text
        return httpx.Response(200, text=json.dumps(dict(message=dict(content=text), done=True)) + '\n')

    async def check():
        token = PRIVATE.set(True)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                llm = LLMClient(httpx_client=client, anthropic_client=NoCloud())
                vision = Vision()
                messages = await run_turn(session(private=True), 'Which mug?', vision=vision, llm=llm, route='now')
                assert len(shapes(messages)) == 2 and len(seen) == len(vision.calls) == 1
        finally:
            PRIVATE.reset(token)
    asyncio.run(check())
