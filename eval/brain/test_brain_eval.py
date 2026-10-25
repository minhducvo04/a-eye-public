import json

from eval.brain import run


def test_prompts_match_the_app_shapes():
    data = run.cases()
    assert len(data['talker']) == 20 and len(data['expert']) == 10
    system, user = run.talker_prompt(data['talker'][0])
    assert 'You are the TALKER' in system and 'Board header names' in system
    assert set(json.loads(user)) == {'caption', 'objects', 'memory', 'conversation', 'text'}
    off = next(c for c in data['talker'] if c['caption'] == 'camera is off')
    assert 'camera is off' in run.talker_prompt(off)[0].lower()
    system, user = run.expert_prompt(data['expert'][0])
    assert 'You are the expert' in system
    assert json.loads(user)['question'].startswith('Think hard')


def test_style_checks():
    assert run.style('SAY: Plug it into the CPU fan header. It clicks.') == dict(
        say=True, words=9, sentences=2, symbols=False)
    assert run.style('- step one\n**bold**')['symbols'] is True
