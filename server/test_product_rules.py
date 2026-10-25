import json

import pytest

from server import packs
from server.agent import Session
from server.product_rules import CLAIMS, ROLE_WORDS, FALLBACK, NEUTRAL, SERIOUS, allowed, check, vent_reply
from server.test_agent import LLM, Vision, run


@pytest.mark.parametrize('word', ROLE_WORDS)
def test_role_words_are_input_only(word):
    assert not allowed(f'I can be your {word}.')
    assert vent_reply(f'My {word} is awful') == NEUTRAL
    assert vent_reply(f'My {word} threatened me') == SERIOUS
    assert vent_reply(f'My {word} fixed the sink') is None


@pytest.mark.parametrize('claim', CLAIMS)
def test_claim_word_list_enforced(claim):
    assert check(claim.upper().replace("'", '’')) == FALLBACK


@pytest.mark.parametrize('claim', [
    "I'm really so proud of you",
    'I AM really, so PROUD!',
    'I’m—really—so—proud.',
    'ＰＲＯＵＤ of YOU!',
    'I am always right here.',
    'I care very much about you.',
    'You can always truly count on me.',
    'You do not really need anyone else.',
    'I will most certainly always help.',
    'GOOD, really GREAT JOB!',
    'I can be your PARENT!',
])
def test_padded_and_punctuated_claims_rejected(claim):
    assert not allowed(claim)
    assert check(claim) == FALLBACK


@pytest.mark.parametrize('line', [
    'Show the motherboard.', 'The transparent cover is loose.',
    'Last time you used 3 tries; today, 1.', 'The job requires a screwdriver.',
])
def test_factual_language_still_allowed(line):
    assert check(line) == line


@pytest.mark.parametrize('pack', ['general', 'home', 'cooking', 'pc_build', 'memory'])
def test_venting_fixed_before_model(pack):
    llm, vision = LLM(), Vision()
    for text, line in [('My family is awful', NEUTRAL), ('My sibling threatened me', SERIOUS)]:
        messages = run(Session('fixture', packs.get(pack)), llm, text, vision)
        assert [m['text'] for m in messages if m['type'] == 'say'] == [line]
    assert llm.calls == vision.calls == []


def test_caption_speech_and_draw_are_checked():
    blocked = ' '.join((CLAIMS[0], 'of you.'))
    llm = LLM(json.dumps(dict(agent='helper', say=blocked,
                              draw=[dict(kind='label', target='a', text=ROLE_WORDS[0])])))
    messages = run(llm=llm)
    texts = [m['text'] for m in messages if m['type'] in ('say', 'reply_delta')]
    texts += [op['text'] for m in messages if m['type'] == 'draw' for op in m['ops'] if 'text' in op]
    assert texts and all(allowed(t) for t in texts)
    assert FALLBACK in texts
    assert check('You used two hints today.') == 'You used two hints today.'
    assert check('Show the motherboard.') == 'Show the motherboard.'


def test_factual_completion_once(tmp_path):
    from server.growth import GrowthLog
    from server.memory import Store, Cipher
    from server.packs.home import HomeGuide, TASKS
    log = GrowthLog(Store('fixture', base=tmp_path, cipher=Cipher('')))
    log.update(log.start('sink'), tries=3, hints=10, completed=True)
    guide = HomeGuide(growth=log)
    guide.reply('sink')
    guide.index = len(TASKS['sink']) - 1
    guide.water_off_passed = True
    assert guide.reply('done') == 'Last time you used 3 tries; today, 1.'
    assert guide.reply('done') == ''
    assert len(log.rows('sink')) == 2
    assert guide.reply('sink') == ''
    assert guide.level == 3 and not guide.completed
    assert len(log.rows('sink')) == 3


@pytest.mark.parametrize('claim', [CLAIMS[0] + ' of you.', "I’m really, so PROUD!"])
def test_blocked_claim_split_across_stream_tokens_never_reaches_caption(claim):
    import asyncio
    from server.agent import run_turn
    class Brain:
        async def stream(self, *args):
            yield '{"draw":[]}SAY: '
            for token in claim.split():
                yield token + ' '
    messages = asyncio.run(run_turn(Session('fixture'), 'help', llm=Brain(), vision=Vision()))
    assert [m['text'] for m in messages if m['type'] == 'reply_delta'] == [FALLBACK]


@pytest.mark.parametrize('previous_tries,expected', [
    (3, 'Last time you used 3 tries; today, 1.'), (1, ''),
])
def test_completion_only_emits_code_fact_without_model(tmp_path, previous_tries, expected):
    from server.growth import GrowthLog
    from server.memory import Store, Cipher
    from server.packs.home import TASKS
    log = GrowthLog(Store('fixture', base=tmp_path, cipher=Cipher('')))
    log.update(log.start('hinge'), tries=previous_tries, completed=True)
    session = Session('fixture', packs.get('home'), growth=log)
    llm = LLM(json.dumps(dict(agent='helper', say='You are incredible!', draw=[])))
    vision = Vision()
    run(session, llm, 'hinge', vision)
    session.home_guide.index = len(TASKS['hinge']) - 1
    for text, line in [('done', expected), ('done', '')]:
        messages = run(session, llm, text, vision)
        for kind in ('say', 'reply_delta'):
            assert [m['text'] for m in messages if m['type'] == kind] == ([line] if line else [])
    assert llm.calls == vision.calls == []


def test_rewind_caption_uses_checked_speech(monkeypatch):
    import asyncio
    from server import rewind
    messages = []

    async def emit(msg):
        messages.append(msg)

    async def memory_turn(session, text, kind, speak, emit, turn_id, vision):
        await speak("I'm proud of you.", True)

    monkeypatch.setattr(rewind, 'memory_turn_kind', lambda *args: 'fixture')
    monkeypatch.setattr(rewind, 'memory_turn', memory_turn)
    asyncio.run(rewind.rewind_turn(Session('fixture', emit=emit), 'fixture', None,
                                  vision=Vision(), turn_id='fixture'))
    assert [m['text'].strip() for m in messages if m['type'] == 'reply_delta'] == [FALLBACK]
    assert [m['text'] for m in messages if m['type'] == 'say'] == [FALLBACK]
