import asyncio
import json
from time import monotonic

from contracts import validate
from server import packs
from server.agent import Session, run_turn
from server.test_agent import SCENE, Vision


class Brain:
    async def stream(self, system, user, **kwargs):
        yield json.dumps(dict(agent='helper', draw=[dict(kind='box', target='a')])) + '\n'
        yield 'SAY:\nFirst sentence. Second sentence!'


def test_cached_stream():
    async def check():
        session = Session('s', pack=packs.get('cooking'))
        session.scene = SCENE
        session.scene_time = monotonic()
        session.scene_seq = 7
        vision = Vision()
        messages = await run_turn(session, 'point', vision=vision, llm=Brain())
        for msg in messages:
            validate('server_message_v2', msg)
        assert not vision.calls
        drawing = next(m for m in messages if m['type'] == 'draw')
        assert drawing['seq'] == 7
        assert drawing['ops'][1]['anchor']['object_id'] == 'a'
        says = [m for m in messages if m['type'] == 'say']
        assert [m['text'] for m in says] == ['First sentence.', 'Second sentence!']
        assert [m['part'] for m in says] == [0, 1]
        assert [m['final'] for m in says] == [False, True]
        assert messages.index(drawing) < messages.index(says[0])
        assert messages[-1]['type'] == 'metrics'
        assert messages[-1]['vlm_ms'] == 0
    asyncio.run(check())


def test_scene_loop_serial_interval_and_latest(monkeypatch):
    from server.ws2 import scene_loop
    from server.test_agent import FRAME
    from server.keyframes import KeyframeGate
    monkeypatch.setenv('KEYFRAME_HEARTBEAT_S', '.04')

    async def check():
        starts, messages = [], []
        active = 0
        session = Session('s', frame=FRAME, keyframe_gate=KeyframeGate(min_gap_s=.04))

        class SlowVision:
            async def call(self, operation, payload):
                nonlocal active
                active += 1
                assert active == 1
                starts.append(monotonic())
                await asyncio.sleep(0.02)
                active -= 1
                return SCENE

        async def send(msg):
            validate('server_message_v2', msg)
            messages.append(msg)

        task = asyncio.create_task(scene_loop(session, SlowVision(), send))
        await asyncio.sleep(0.005)
        session.frame = dict(FRAME, seq=2)
        await asyncio.sleep(0.04)
        session.frame = dict(FRAME, seq=3)
        await asyncio.sleep(0.08)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert len(starts) == 2
        assert starts[1] - starts[0] >= 0.04
        assert [m['seq'] for m in messages if m['type'] == 'track'] == [1, 3]
        assert len([m for m in messages if m['type'] == 'metrics']) == 2
    asyncio.run(check())


def test_cancel_closes_brain_and_audio_under_200ms():
    async def check():
        brain_closed, audio_closed, audio_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []

        class SlowBrain:
            async def stream(self, *args):
                try:
                    yield '{"agent":"helper","draw":[]}\nSAY:\nFirst. Second. Pending'
                    await asyncio.Event().wait()
                finally:
                    brain_closed.set()

        async def speak(msg):
            try:
                audio_started.set()
                await asyncio.Event().wait()
                yield None
            finally:
                audio_closed.set()

        async def emit(msg):
            validate('server_message_v2', msg)
            sent.append((monotonic(), msg))

        session = Session('s', speak=speak, emit=emit)
        task = asyncio.create_task(run_turn(session, 'point', vision=Vision(), llm=SlowBrain()))
        await asyncio.wait_for(audio_started.wait(), 1)
        start = monotonic()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert monotonic() - start < 0.2
        assert brain_closed.is_set() and audio_closed.is_set()
        count = len(sent)
        await asyncio.sleep(0.21)
        assert len(sent) == count
        assert sent[-1][0] <= start
    asyncio.run(check())


def test_audio_sent_in_sentence_order_and_measured():
    from server.agent import message

    async def check():
        spoken = []

        async def speak(msg):
            spoken.append(msg['text'])
            await asyncio.sleep(0)
            yield message('audio', agent='helper', utterance_id=msg['utterance_id'],
                          sample_rate=16000, pcm16_b64='AAA=', final=True)

        result = await run_turn(Session('s', speak=speak), 'point', vision=Vision(), llm=Brain())
        assert spoken == ['First sentence.', 'Second sentence!']
        assert result[-1]['first_audio_ms'] >= 0
        assert result[-1]['turn_ms'] >= result[-1]['first_audio_ms']
        for msg in result:
            validate('server_message_v2', msg)
    asyncio.run(check())


def test_cold_scene_wait_is_bounded_and_does_not_cancel_refresh():
    async def check():
        session = Session('s')
        session.scene_task = asyncio.create_task(asyncio.Event().wait())
        start = monotonic()
        result = await run_turn(session, 'point', vision=Vision(), llm=Brain())
        elapsed = monotonic() - start
        assert .3 <= elapsed < .5
        assert not session.scene_task.done()
        assert next(m for m in result if m['type'] == 'draw')['ops'] == [dict(kind='clear', id='*')]
        assert result[-1]['vlm_ms'] >= 300
        session.scene_task.cancel()
        await asyncio.gather(session.scene_task, return_exceptions=True)
    asyncio.run(check())


def test_stale_scene_does_not_wait_for_refresh():
    async def check():
        session = Session('s', pack=packs.get('cooking'), scene=SCENE, scene_time=monotonic() - 3)
        session.scene_task = asyncio.create_task(asyncio.Event().wait())
        result = await asyncio.wait_for(run_turn(session, 'point', vision=Vision(), llm=Brain()), 0.2)
        assert len(next(m for m in result if m['type'] == 'draw')['ops']) > 1
        assert not session.scene_task.done()
        session.scene_task.cancel()
        await asyncio.gather(session.scene_task, return_exceptions=True)
    asyncio.run(check())


def test_draw_and_first_sentence_arrive_before_brain_finishes():
    async def check():
        release = asyncio.Event()
        spoken = asyncio.Event()
        messages = []

        class PausedBrain:
            async def stream(self, *args):
                yield '{"agent":"helper","draw":[]}\nSAY:\nFirst. More'
                await release.wait()
                yield ' text.'

        async def emit(msg):
            messages.append(msg)
            if msg['type'] == 'say':
                spoken.set()

        task = asyncio.create_task(run_turn(Session('s', emit=emit), 'point', vision=Vision(), llm=PausedBrain()))
        await asyncio.wait_for(spoken.wait(), 0.2)
        assert not task.done()
        assert [m['type'] for m in messages] == ['agent_state', 'draw', 'agent_state', 'reply_delta', 'say']
        assert messages[-1]['text'] == 'First.'
        release.set()
        await task
    asyncio.run(check())


def test_header_without_draw_is_accepted_and_say_marker_stripped():
    """Seen live with gemma4: header {"agent": "helper"} without draw, then 'SAY: ...'."""
    class LooseBrain:
        async def stream(self, system, user, **kwargs):
            for chunk in ['{"agent": "helper"}\n', 'SAY: Cut it in half. ', 'Keep fingers back.']:
                yield chunk

    async def check():
        session = Session('s')
        session.scene = SCENE
        session.scene_time = monotonic()
        messages = await run_turn(session, 'cut', vision=Vision(), llm=LooseBrain())
        for msg in messages:
            validate('server_message_v2', msg)
        assert [m['text'] for m in messages if m['type'] == 'say'] == ['Cut it in half.', 'Keep fingers back.']
    asyncio.run(check())


def test_old_scene_of_the_newest_frame_is_still_used():
    """A still photo: the scene is older than 2 s but belongs to the newest frame, so the brain must see it."""
    async def check():
        session = Session('s', pack=packs.get('cooking'))
        session.scene = SCENE
        session.scene_time = monotonic() - 30
        session.scene_seq = 7
        session.frame = {'seq': 7}
        vision = Vision()
        messages = await run_turn(session, 'point', vision=vision, llm=Brain())
        drawing = next(m for m in messages if m['type'] == 'draw')
        assert drawing['seq'] == 7 and len(drawing['ops']) > 1
        assert not vision.calls
    asyncio.run(check())


def test_header_with_bent_roles_keeps_draws_and_never_speaks_json():
    """Seen live with gemma4: roles "highlight" and "label" are not in the contract."""
    class BentBrain:
        async def stream(self, system, user, **kwargs):
            yield json.dumps(dict(agent='helper', draw=[dict(kind='circle', target='a', role='highlight'),
                                                        dict(kind='label', target='a', text='here', role='label')])) + '\n'
            yield 'SAY: Cut here.'

    async def check():
        session = Session('s', pack=packs.get('cooking'))
        session.scene = SCENE
        session.scene_time = monotonic()
        messages = await run_turn(session, 'cut', vision=Vision(), llm=BentBrain())
        for msg in messages:
            validate('server_message_v2', msg)
        drawing = next(m for m in messages if m['type'] == 'draw')
        assert [op['kind'] for op in drawing['ops']] == ['clear', 'circle', 'label']
        assert [m['text'] for m in messages if m['type'] == 'say'] == ['Cut here.']
    asyncio.run(check())


def test_shopping_question_brings_in_shopper_even_if_model_forgets():
    """Seen live: the local brain answered a price question without market_category."""
    from server.agent import shopping_category
    assert shopping_category('Is there cheaper RAM that fits my board?') == 'ram'
    assert shopping_category('how much does a good GPU cost') == 'gpu'
    assert shopping_category('where does this cable go') is None

    class ForgetfulBrain:
        async def stream(self, system, user, **kwargs):
            yield json.dumps(dict(agent='helper', draw=[])) + '\n'
            yield 'SAY: I can look.'

    async def check():
        from types import SimpleNamespace
        from server.test_agent import FRAME
        from server.shopping import FixtureSource
        session = Session('s', frame=FRAME, scene_seq=1)
        session.scene = SCENE
        session.scene_time = monotonic()
        app = SimpleNamespace(state=SimpleNamespace(offer_source=FixtureSource()))
        messages = await run_turn(session, 'Is there cheaper RAM?', vision=Vision(), llm=ForgetfulBrain(), app=app)
        for msg in messages:
            validate('server_message_v2', msg)
        assert any(m['type'] == 'market_results' for m in messages)
        assert any(m['type'] == 'say' and m['agent'] == 'shopper' for m in messages)
    asyncio.run(check())


def test_leading_punctuation_after_header_is_not_spoken():
    """Seen live with qwen3.5:9b: '{...}, SAY: Since we have no view yet, ...' spoke ', Since'."""
    class CommaBrain:
        async def stream(self, system, user, **kwargs):
            yield '{"agent": "helper", "draw": [{"type": "clear", "target": null}]}, SAY: Since we have no view yet, '
            yield 'safety relies on stance and grip.'

    async def check():
        session = Session('s')
        session.scene = SCENE
        session.scene_time = monotonic()
        messages = await run_turn(session, 'knife tips', vision=Vision(), llm=CommaBrain())
        says = [m['text'] for m in messages if m['type'] == 'say']
        assert says and not says[0].startswith((',', ';', ':'))
    asyncio.run(check())
