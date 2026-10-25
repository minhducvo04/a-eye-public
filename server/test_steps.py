import pytest

from contracts import validate
from server.boards import load_board
from server.steps import Session


def message(kind, **fields):
    return dict(v=1, type=kind, session_id='s', **fields)


def photo(**fields):
    return message('photo', seq=1, w=10, h=10, jpeg_b64='synthetic', **fields)


def session():
    return Session('s', 'demo-b650', load_board('demo-b650'))


def checked(result):
    messages, request = result
    for msg in messages:
        validate('server_message', msg)
    return messages, request


def located(pin_ok=False, **fields):
    return dict(found=True, conf=0.9, ms=1, bbox=[0.1, 0.1, 0.2, 0.2],
                pins=dict(pin_ok=pin_ok, grid=[[0.1, 0.1]], pin1=[0.1, 0.1],
                          residual=0, keyed_gap=True), **fields)


def ready():
    s = session()
    checked(s.handle(message('confirm', what='power_off', value=True), 1000))
    checked(s.handle(photo(), 1000))
    checked(s.accept('identify', dict(item='cpu_fan_cable', conf=0.9, ms=1), 1000))
    checked(s.accept('locate', located(), 1000))
    assert s.state == 'AWAIT'
    return s


def test_safety_cannot_be_skipped():
    s = session()
    for msg in [photo(), message('confirm', what='power_off', value=False),
                message('confirm', what='seated', value=True)]:
        _, request = checked(s.handle(msg, 1000))
        assert request is None
        assert s.state == 'SAFETY'
    checked(s.handle(message('user_text', text="it's off"), 1000))
    assert s.state == 'IDENTIFY'


@pytest.mark.parametrize('pin_ok', [False, True])
def test_pin_gate(pin_ok):
    s = ready()
    s.state = 'LOCATE'
    messages, _ = checked(s.accept('locate', located(pin_ok), 1000))
    overlay = messages[0]
    assert overlay['level'] == ('pin' if pin_ok else 'header')
    assert ('pins' in overlay['boxes'][0]) == pin_ok


@pytest.mark.parametrize('fields', [{'blur': True}, {'glare': True}, {'conf': 0.59}])
def test_no_overlay_when_uncertain(fields):
    s = ready()
    s.state = 'LOCATE'
    result = located()
    result.update(fields)
    messages, _ = checked(s.accept('locate', result, 1000))
    assert all(m['type'] != 'overlay' for m in messages)
    assert s.state == 'LOCATE'


@pytest.mark.parametrize('passed,conf,abstain,state', [
    (True, 0.79, False, 'AWAIT'), (True, 0.8, False, 'DONE'),
    (False, 0.9, False, 'LOCATE'), (True, 0.99, True, 'AWAIT'),
])
def test_verdict_gate(passed, conf, abstain, state):
    s = ready()
    _, request = checked(s.handle(message('confirm', what='seated', value=True), 1000))
    assert request[0] == 'verify'
    messages, _ = checked(s.accept('verify', {
        'pass': passed, 'conf': conf, 'abstain': abstain, 'reason': 'check', 'ms': 1,
    }, 1000))
    assert messages[0]['pass'] == (passed and conf >= 0.8 and not abstain)
    assert s.state == state


def test_outside_allowlist_and_missing_header():
    for item in ['psu', 'sata_cable', 'unknown']:
        s = session()
        s.handle(message('confirm', what='power_off', value=True), 1000)
        s.handle(photo(), 1000)
        messages, request = checked(s.accept('identify', dict(item=item, conf=0.99, ms=1), 1000))
        assert s.state == 'IDENTIFY'
        assert request is None
        assert any(m['type'] == 'say' for m in messages)


def test_stale_ingress_cached_frame_and_delayed_result():
    s = ready()
    frame = photo()
    frame.update(type='frame', ts_ms=1000)
    messages, request = checked(s.handle(frame, 3000))
    assert messages[0]['code'] == 'stale_frame'
    assert request is None
    s.handle(frame, 1000)
    messages, request = checked(s.handle(message('confirm', what='seated', value=True), 3000))
    assert request is None
    assert messages[0]['code'] == 'stale_frame'
    s.handle(message('confirm', what='seated', value=True), 1000)
    messages, _ = checked(s.accept('verify', {'pass': True, 'conf': 1, 'reason': '', 'ms': 1}, 3000))
    assert messages[0]['code'] == 'stale_frame'
    assert s.state == 'AWAIT'


def test_voice_match_is_closed_set():
    s = session()
    s.handle(message('user_text', text="I think it's off"), 1000)
    assert s.state == 'SAFETY'


def test_board_id_cannot_escape_directory():
    with pytest.raises(ValueError):
        load_board('../contracts/schema')
