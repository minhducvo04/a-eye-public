"""Memory pack: the fixed pill line both ways, no certainty about unseen things, repeat-friendly, home first."""
import asyncio
import re
from time import monotonic

from server import packs
from server.agent import Session, run_turn
from server.packs import memory
from server.test_agent import SCENE, Vision

PILL = "I can't tell if you took it."


def test_pill_questions_get_the_fixed_line_whatever_was_seen():
    for question in ('Did I take my pill?', 'have I taken my medicine today', 'did I already take my meds',
                     'My pills, did I take them?'):
        line = memory.PACK.refuse(question)
        assert line and PILL in line and 'check' in line, question
    assert memory.pill_line('8:05').startswith('I saw the pill box opened at 8:05.')
    assert PILL in memory.pill_line('8:05') and PILL in memory.pill_line()


def test_never_says_you_took_or_you_did_not_take_it():
    for spoken in ('You took your pill at eight.', "You didn't take your medicine yet.",
                   "You've already taken your meds today.", 'You have not taken the tablet.'):
        out = memory.guard(spoken, 'did I take it')
        assert out == memory.pill_line(), spoken
        assert not re.search(r"\byou (took|didn't take|did not take|have taken) (your|the|a)\b", out, re.I)


def test_dosing_questions_get_no_advice():
    for question in ('Should I take another pill?', 'how many tablets can I take', 'is it ok to double my dose',
                     'I missed my medicine, should I take two?'):
        assert memory.PACK.refuse(question) == memory.MEDICAL_LINE, question
    assert memory.PACK.refuse('where are my keys?') is None


def test_no_certainty_and_no_you_already_asked():
    out = memory.guard("I'm sure your keys are definitely in the bowl. You already asked me that.", 'keys?')
    low = out.lower()
    for banned in ('definitely', "i'm sure", 'already asked'):
        assert banned not in low
    assert 'keys' in low
    assert memory.guard('You forgot again? Your glasses are on the table.', 'glasses?') == \
        'Your glasses are on the table.'


def test_where_answers_start_with_the_items_home_once():
    out = memory.guard('I saw them there two minutes ago.', 'Where are my keys?', first=True)
    assert out == 'Your keys live in the blue bowl by the door. I saw them there two minutes ago.'
    said = memory.guard('In the blue bowl by the door, where your keys live.', 'where are my keys', first=True)
    assert said.count('blue bowl') == 1
    assert memory.guard('I saw them there.', 'Where are my keys?', first=False) == 'I saw them there.'


def test_pack_registered_with_warm_cartesia_voice():
    pack = packs.get('memory')
    assert pack.voice.startswith('cartesia:') and pack.guard('You took your pill.', 'pill?') == memory.pill_line()
    assert 'already asked' in pack.system_prompt and 'pill' in pack.system_prompt


def test_turn_post_condition_rewrites_a_pill_claim_from_the_model():
    class Brain:
        async def stream(self, system, user, **kwargs):
            yield '{"agent":"helper","draw":[]}\nSAY: You definitely took your pill this morning.'

    async def check():
        session = Session('s', packs.get('memory'))
        session.scene, session.scene_time = SCENE, monotonic()
        messages = await run_turn(session, 'was the pill box opened', vision=Vision(), llm=Brain())
        spoken = ' '.join(m['text'] for m in messages if m['type'] == 'say')
        assert spoken == memory.pill_line()
    asyncio.run(check())
