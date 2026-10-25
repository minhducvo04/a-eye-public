"""Talker + thinker turns: the talker speaks plain SAY text, the planner draws and adds a note."""
import asyncio
import json
from time import monotonic

from contracts import validate
from server import packs
from server.agent import Session, run_turn
from server.llm import fast_options, turn_model
from server.test_agent import SCENE, Vision


class Talker:
    async def stream(self, system, user, **kwargs):
        assert 'TALKER' in system
        yield 'SAY: Let me look,'
        yield ' at that cable.'


class Planner:
    def __init__(self, delay=0.0, speak=True):
        self.delay, self.speak, self.models = delay, speak, []

    async def stream(self, system, user, **kwargs):
        assert 'PLANNER' in system
        self.models.append(kwargs.get('model'))
        await asyncio.sleep(self.delay)
        yield json.dumps(dict(agent='helper', draw=[dict(kind='box', target='a')],
                              note='It goes in the CPU fan header.', speak=self.speak))


def session():
    s = Session('s', pack=packs.get('pc_build'), power_off=True)
    s.scene, s.scene_time, s.scene_seq = SCENE, monotonic(), 3
    return s


def test_talker_speaks_first_then_planner_draws_and_adds_note():
    async def check():
        planner = Planner(delay=0.05)
        messages = await run_turn(session(), 'where does it go', vision=Vision(), llm=Talker(), planner=planner)
        for msg in messages:
            validate('server_message_v2', msg)
        says = [m for m in messages if m['type'] == 'say']
        assert [m['text'] for m in says] == ['Let me look, at that cable.', 'It goes in the CPU fan header.']
        assert [m['part'] for m in says] == [0, 1]
        drawing = next(m for m in messages if m['type'] == 'draw')
        assert messages.index(says[0]) < messages.index(drawing)  # speech is not held for the draw ops
        assert drawing['ops'][1]['anchor']['object_id'] == 'a'
        assert planner.models == ['claude-sonnet-5-5']
    asyncio.run(check())


def test_note_not_spoken_when_it_adds_nothing_and_talker_did_not_stall():
    class Plain(Talker):
        async def stream(self, system, user, **kwargs):
            yield 'SAY: It goes in the CPU fan header.'

    async def check():
        s = session()
        messages = await run_turn(s, 'where', vision=Vision(), llm=Plain(), planner=Planner(speak=False))
        assert [m['text'] for m in messages if m['type'] == 'say'] == ['It goes in the CPU fan header.']
        assert not any(line.startswith('planner:') for line in s.history)
    asyncio.run(check())


def test_cancelled_turn_cancels_planner():
    async def check():
        planner = Planner(delay=10)
        task = asyncio.create_task(run_turn(session(), 'where', vision=Vision(), llm=Talker(), planner=planner))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert all(t.done() for t in asyncio.all_tasks() if t is not asyncio.current_task())
    asyncio.run(check())


def test_talker_model_defaults_to_haiku_with_thinking_off():
    assert turn_model() == 'claude-haiku-5-5'
    assert fast_options('claude-haiku-5-5') == dict(thinking={'type': 'disabled'}, output_config={'effort': 'low'})


def test_planner_note_that_repeats_the_talker_is_not_spoken():
    # Live replay 2026-10-24: talker said the camera is off, planner said it again.
    from server.agent import repeats

    class CameraOff(Talker):
        async def stream(self, system, user, **kwargs):
            yield "SAY: I can't see anything right now, since the camera is off."

    class SamePoint(Planner):
        async def stream(self, system, user, **kwargs):
            yield json.dumps(dict(agent='helper', draw=[], note="Camera's off, so turn it on and show me the board.",
                                  speak=True))

    async def check():
        messages = await run_turn(session(), 'where does it go', vision=Vision(), llm=CameraOff(), planner=SamePoint())
        assert [m['text'] for m in messages if m['type'] == 'say'] == [
            "I can't see anything right now,", 'since the camera is off.']
    asyncio.run(check())
    assert not repeats('It goes in the CPU fan header.', "Let me look, at that cable.")
