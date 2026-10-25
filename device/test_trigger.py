"""Trigger rules, auto-off and voice activity: pure logic with a fake clock."""
import math
import struct

import pytest

from device import config
from device.fake_turn import table_frame
from device.trigger import CAP_S, IDLE_OFF_S, Motion, Power, Speech


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_defaults_are_90_s_idle_and_15_min_cap():
    assert (IDLE_OFF_S, CAP_S) == (90, 900)
    cfg = config.load()
    assert (cfg['trigger']['idle_off_s'], cfg['trigger']['cap_s']) == (90, 900)


def test_off_until_triggered_and_press_toggles():
    clock = Clock()
    p = Power(clock=clock)
    assert not p.on and p.tick() is None
    assert p.press() and p.on and p.reason == 'button'
    assert p.press() and not p.on and p.reason == 'button'


def test_idle_off_after_90_s_without_change():
    clock = Clock()
    p = Power(clock=clock)
    p.turn_on('button')
    clock.t += 89.9
    assert p.tick() is None and p.on
    clock.t += 0.2
    assert p.tick() == 'idle' and not p.on and p.reason == 'idle'


def test_change_restarts_idle_timer():
    clock = Clock()
    p = Power(clock=clock)
    p.turn_on('motion')
    clock.t += 80
    p.change()
    clock.t += 80
    assert p.tick() is None
    clock.t += 10
    assert p.tick() == 'idle'


def test_cap_wins_even_with_constant_change():
    clock = Clock()
    p = Power(clock=clock)
    p.turn_on('button')
    for _ in range(899):
        clock.t += 1
        p.change()
        assert p.tick() is None
    clock.t += 1
    p.change()
    assert p.tick() == 'cap' and not p.on


def test_change_while_off_does_not_turn_on():
    p = Power(clock=Clock())
    p.change()
    assert not p.on and p.tick() is None


def test_motion_ignores_noise_and_jitter_but_sees_a_hand_reach_in():
    from PIL import Image
    m = Motion()
    empty = table_frame(False)
    assert not m.changed(empty)                            # first look: nothing to compare
    assert not m.changed(empty.point(lambda v: min(255, v + 3)))   # sensor noise
    jitter = empty.transform(empty.size, Image.AFFINE, (1, 0, 2, 0, 1, 0), fillcolor=(196, 170, 132))
    assert not m.changed(jitter)                           # 2 px handheld shift
    assert m.changed(table_frame(True, arm=True))          # an arm reaches in and puts the keys down
    assert m.changed(table_frame(True))                    # and leaves again


def pcm(seconds, amp):
    n = int(16000 * seconds)
    return b''.join(struct.pack('<h', int(amp * math.sin(2 * math.pi * 220 * i / 16000))) for i in range(n))


def feed(vad, data):
    out = []
    for i in range(0, len(data), 3200):
        out += vad.feed(data[i:i + 3200])
    return out


def test_speech_start_end_with_preroll():
    vad = Speech()
    events = feed(vad, pcm(1.0, 0) + pcm(1.0, 8000) + pcm(1.0, 0))
    kinds = [k for k, _ in events]
    assert kinds[0] == 'start' and kinds.count('start') == 1 and kinds[-1] == 'end'
    assert len(events[0][1]) == int(0.3 * 16000) * 2       # 300 ms pre-roll handed over at the start
    sent = sum(len(d) for k, d in events if k == 'chunk')
    assert 1.6 * 32000 <= sent <= 1.9 * 32000              # the speech plus about 0.7 s hang


def test_quiet_room_never_starts():
    assert feed(Speech(), pcm(3.0, 200)) == []


def test_long_speech_is_cut_at_max():
    events = feed(Speech(max_s=2.0), pcm(5.0, 8000))
    assert [k for k, _ in events].count('end') >= 2


def test_private_by_default_and_cloud_switches():
    cfg = config.load()
    assert cfg.hello_privacy() == {'private': True}
    cfg = config.load(overrides={'privacy': {'private': False, 'claude': True}})
    assert cfg.hello_privacy() == {'private': False,
                                   'cloud': {'claude': True, 'cartesia': False, 'twelvelabs': False}}


def test_credentials_only_from_env():
    cfg = config.load()
    assert cfg.headers({}) == {}
    h = cfg.headers({'A_EYE_TOKEN': 'x' * 8, 'CF_ACCESS_CLIENT_ID': 'id', 'CF_ACCESS_CLIENT_SECRET': 's'})
    assert set(h) == {'Authorization', 'CF-Access-Client-Id', 'CF-Access-Client-Secret'}
    with pytest.raises(ValueError):
        config.load(overrides={'server': {'token': 'nope'}})


def test_example_config_loads():
    from pathlib import Path
    cfg = config.load(Path(__file__).with_name('device.example.toml'))
    assert cfg.hello_privacy() == {'private': True}
