"""Incremental STT: the last partial stands in for the final only when the tail after it is quiet."""
from array import array

from server.ws2 import quiet_tail


def pcm(level, ms):
    return array('h', [level if i % 2 else -level for i in range(16 * ms)]).tobytes()


def test_quiet_tail_reuses_partial_after_silence_only():
    speech = pcm(3000, 1000)
    assert quiet_tail(speech + pcm(50, 400), len(speech))
    assert not quiet_tail(speech + pcm(3000, 400), len(speech))  # words after the partial
    assert not quiet_tail(speech + pcm(0, 1200), len(speech))     # tail too long to trust
    assert not quiet_tail(speech, 0)


def test_slow_part_names_the_worst_overrun_and_meaningful_drops_noise():
    from server.ws2 import meaningful, slow_part
    assert slow_part(dict(stt_ms=1477, brain_first_token_ms=1274, tts_ms=300)) == 'Slow: speech recognition 1.5 s'
    assert slow_part(dict(stt_ms=40, brain_first_token_ms=600, tts_ms=200)) == ''
    assert not meaningful('') and not meaningful('Thank you.') and not meaningful('uh um') and not meaningful('the')
    assert meaningful('next') and meaningful('where does it go')
