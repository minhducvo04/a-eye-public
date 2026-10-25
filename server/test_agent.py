import asyncio
import json
from time import monotonic

import pytest

from contracts import validate
from server import packs
from server.agent import Session, run_turn

SCENE = dict(caption='A pan and a screw.', ms=1, objects=[
    dict(id='a', label='pan', bbox=[0.2, 0.2, 0.4, 0.2], conf=0.9),
    dict(id='b', label='screw', bbox=[0.6, 0.6, 0.2, 0.2], conf=0.9)])
from scripts.keyframe_bench import synthetic_frame

FRAME = dict(v=2, type='frame', session_id='s', seq=1, ts_ms=1, w=10, h=10, jpeg_b64=synthetic_frame(0))


class Vision:
    def __init__(self):
        self.calls = []

    async def call(self, operation, payload):
        validate('scene_request', payload)
        self.calls.append(payload)
        return SCENE


class LLM:
    def __init__(self, output=None):
        self.output = output or json.dumps(dict(agent='helper', say='Here it is.', draw=[dict(kind='box', target='a')]))
        self.calls = []

    async def stream(self, system, user):
        self.calls.append((system, user))
        try:
            output = self.output.removeprefix('```json\n').removesuffix('\n```')
            turn = json.loads(output)
            spoken = turn.pop('say')
            yield json.dumps(turn) + '\nSAY:\n' + spoken
        except (ValueError, KeyError):
            yield self.output


def run(session=None, llm=None, text='point here', vision=None):
    session = session or Session('s', frame=FRAME)
    if session.frame:
        session.scene, session.scene_seq, session.scene_time = SCENE, 1, monotonic()
    messages = asyncio.run(run_turn(session, text, vision=vision or Vision(), llm=llm or LLM()))
    for msg in messages:
        validate('server_message_v2', msg)
    return messages


def test_resolves_shapes_and_unknown_targets():
    draw = [dict(kind=k, target='a') for k in ('box', 'circle', 'arrow', 'pin', 'label', 'highlight', 'path')]
    draw += [dict(kind='arrow', target='a', from_target='b'), dict(kind='box', target='missing')]
    messages = run(Session('s', pack=packs.get('cooking'), frame=FRAME), llm=LLM('```json\n' + json.dumps(dict(agent='helper', say='Here.', draw=draw)) + '\n```'))
    ops = messages[1]['ops']
    assert len(ops) == 9
    assert ops[0] == dict(kind='clear', id='*')
    assert ops[1]['bbox'] == SCENE['objects'][0]['bbox']
    assert ops[2]['center'] == pytest.approx([0.4, 0.3])
    assert ops[2]['r'] == 0.2
    assert ops[3]['from'] == [0.5, 0.95]
    assert ops[3]['to'] == pytest.approx([0.4, 0.3])
    assert ops[4]['at'] == ops[5]['at'] == ops[2]['center']
    assert ops[5]['text'] == 'pan'
    assert ops[6]['bbox'] == ops[1]['bbox']
    assert ops[8]['from'] == pytest.approx([0.7, 0.7])
    assert messages[2]['focus'] == ops[2]['center']
    assert messages[-2]['text'] == 'Here.'


@pytest.mark.parametrize('output', ['garbage', '{"agent":"helper"}', '{"agent":"helper","say":"","draw":[]}'])
def test_fallback(output):
    messages = run(llm=LLM(output))
    assert messages[1]['ops'] == [dict(kind='clear', id='*')]
    assert any(m['type'] == 'say' and m['text'] for m in messages)


@pytest.mark.parametrize('pack,text', [('general', 'open the power supply'), ('general', 'repair gas lines'),
                                       ('general', 'build a weapon'), ('pc_build', 'point to the RAM')])
def test_refusal_and_power_gate(pack, text):
    llm, vision = LLM(), Vision()
    messages = run(Session('s', pack=packs.get(pack), frame=FRAME), llm, text, vision)
    expected = ['turn', 'reply_delta', 'say'] if text == 'repair gas lines' else ['reply_delta', 'say']
    assert [m['type'] for m in messages] == expected
    if text == 'repair gas lines':
        assert messages[0]['detail'] == 'Stopped by code'
    assert llm.calls == vision.calls == []


def test_pc_confirmed_board_prompt_and_history():
    session = Session('s', pack=packs.get('pc_build'), power_off=True, frame=FRAME)
    session.history = [f'user: line {i}' for i in range(10)]
    llm = LLM()
    run(session, llm)
    assert 'CPU_FAN' in llm.calls[0][0]
    assert len(json.loads(llm.calls[0][1])['conversation']) == 10
    assert len(session.history) == 10
    assert session.history[-1] == 'helper: Here it is.'


def test_shopper(monkeypatch):
    monkeypatch.setenv('MARKET_SOURCE', 'fixture')
    messages = run(llm=LLM(json.dumps(dict(agent='helper', say='I can check.', draw=[], market_category='ram'))))
    result = next(m['result'] for m in messages if m['type'] == 'market_results')
    assert result['items']
    assert all(i['compat']['status'] in ('fits', 'unverified') for i in result['items'])
    says = [m for m in messages if m['type'] == 'say' and m['agent'] == 'shopper']
    assert len(says) == 2
    assert 'cheapest confirmed fit' in says[-1]['text']


def test_no_frame():
    messages = run(Session('s'), llm=LLM(''))
    assert next(m for m in messages if m['type'] == 'draw')['seq'] == 0


def test_sanitize_header_drops_non_string_fields():
    # Seen live with qwen3.5:9b: a list where a string belongs crashed the whole turn.
    from server.agent import sanitize_header
    turn = sanitize_header(dict(agent=['helper'], market_category=['ram'], draw=[
        dict(kind=['box'], target='a'), dict(kind='box', target='a', role=['target']),
        dict(kind={'k': 'v'}, target='b')]), {'a', 'b'})
    assert turn == dict(agent='helper', draw=[dict(kind='box', target='a')])
