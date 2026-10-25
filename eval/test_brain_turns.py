import argparse
import json

import pytest

from contracts import schema, validate
from eval.brain_turns import CASES_PATH, evaluate, header_facts, message_facts, parse_model
from server import packs

CASES = json.loads(CASES_PATH.read_text())
CATEGORIES = set(schema()['$defs']['part_category']['enum'])


@pytest.fixture(autouse=True)
def restore_env(monkeypatch):
    # evaluate() sets these for the run; monkeypatch puts them back afterwards.
    for name in ('LLM_PROVIDER', 'LOCAL_LLM_MODEL', 'ANTHROPIC_TURN_MODEL'):
        monkeypatch.delenv(name, raising=False)


class Perfect:
    """Streams exactly what each case expects, in the header + SAY format."""

    def __init__(self):
        self.calls = []

    async def stream(self, system, user, **kwargs):
        self.calls.append((system, user))
        case = next(c for c in CASES if c['text'] == json.loads(user)['text'])
        expect = case['expect']
        header = dict(agent='helper', draw=[dict(kind='box', target=t) for t in expect.get('targets', [])[:1]])
        if expect.get('market'):
            header['market_category'] = expect['market']
        if case['pack'] == 'general' and expect.get('targets'):
            name = next(obj['label'] for obj in case['scene']['objects'] if obj['id'] == expect['targets'][0])
            header['draw'] = []
            yield json.dumps(header) + f'\nSAY: Use the {name}. [POINT: {name}]'
            return
        yield json.dumps(header)
        yield '\nSAY: Here it is, right there.'


class Fixed:
    def __init__(self, text):
        self.text = text

    async def stream(self, system, user, **kwargs):
        yield self.text


@pytest.mark.parametrize('case', CASES, ids=[c['case_id'] for c in CASES])
def test_case_is_well_formed(case):
    validate('scene_response', case['scene'])
    packs.get(case['pack'])
    ids = {obj['id'] for obj in case['scene']['objects']}
    assert set(case['expect'].get('targets', [])) <= ids
    assert case['expect'].get('market') in CATEGORIES | {None}


def test_case_ids_and_texts_are_unique():
    assert len({c['case_id'] for c in CASES}) == len(CASES)
    assert len({c['text'] for c in CASES}) == len(CASES)


def test_perfect_brain_passes_every_case_and_writes_report(tmp_path):
    client = Perfect()
    summary = evaluate(CASES, [('local', 'fake-model')], tmp_path, client=client)['local:fake-model']
    assert summary['n'] == len(CASES) and summary['passed'] == 1.0 and summary['format_ok'] == 1.0
    assert len(client.calls) == len(CASES) + 1  # one warmup call
    assert 'CPU_FAN' in next(system for system, user in client.calls if 'GPU power' in user)
    assert '| local:fake-model |' in (tmp_path / 'brain.md').read_text()
    assert (tmp_path / 'brain-local_fake-model.csv').read_text().count('\n') == len(CASES) + 1


def test_unavailable_brain_is_flagged_and_fails_pointing():
    canned = Fixed('{"agent":"helper","draw":[]}\nSAY: The brain is unavailable. Please try again.')
    pointing = [c for c in CASES if c['expect'].get('targets')][:3]
    summary = evaluate(pointing, [('local', 'm')], None, client=canned, warmup=False)['local:m']
    assert summary['providers'] == ['canned'] and summary['passed'] == 0.0


def test_prose_without_header_still_speaks_but_draws_nothing():
    summary = evaluate(CASES[:1], [('local', 'm')], None, client=Fixed('Your keys are on the right.'),
                       warmup=False)['local:m']
    assert summary['format_ok'] == 0.0 and summary['target_ok'] == 0.0


def test_header_facts():
    assert header_facts('no json here') == dict(header=None, format_ok=False)
    assert header_facts('{"agent":"helper","draw":[]}\nSAY: Hi there.')['format_ok']
    assert not header_facts('{"agent":"helper","draw":[],"say":"Hi"}\nSAY: Hi there.')['format_ok']
    assert not header_facts('{"agent":"helper","draw":[]}')['format_ok']


def test_parse_model():
    assert parse_model('local:qwen3.5:9b-q4_K_M') == ('local', 'qwen3.5:9b-q4_K_M')
    assert parse_model('claude:claude-haiku-5-5') == ('claude', 'claude-haiku-5-5')
    for bad in ('qwen', 'openai:gpt', 'local:'):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_model(bad)


def test_speech_from_a_shopper_agent_counts_but_code_shopper_lines_do_not():
    messages = [dict(type='agent_state', agent='shopper', state='speaking'),
                dict(type='say', agent='shopper', text='That cooler is cheaper.'),
                dict(type='agent_state', agent='shopper', state='thinking'),
                dict(type='say', agent='shopper', text='Let me check prices that fit.')]
    assert message_facts(messages)['speech'] == 'That cooler is cheaper.'
