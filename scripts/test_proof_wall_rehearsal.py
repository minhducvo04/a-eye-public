from scripts.proof_wall_rehearsal import pictures_result, turn_result


def test_pictures_has_its_own_verdict_and_reason():
    assert pictures_result(0) == (True, 'PASS')
    assert pictures_result(2) == (False, 'FAIL (expected 0 saved pictures)')
    assert pictures_result(None) == (False, 'FAIL (picture count unavailable or invalid)')
    assert pictures_result(False)[0] is False


def test_turn_requires_final_and_completed_answer_audio():
    messages = [dict(type='find_item', found=True),
                dict(type='say', text='Seen yesterday.', final=True, utterance_id='answer'),
                dict(type='audio', utterance_id='preface', pcm16_b64='AA==', final=True)]
    valid, audible, reasons = turn_result('yesterday', messages, 'done')
    assert valid and not audible
    assert 'answer audio unavailable or incomplete' in reasons
    messages.append(dict(type='audio', utterance_id='answer', pcm16_b64='AA==', final=True))
    assert turn_result('yesterday', messages, 'done') == (True, True, [])
    messages[1]['final'] = False
    valid, audible, reasons = turn_result('yesterday', messages, 'failed')
    assert not valid
    assert 'no final answer' in reasons and 'turn failed' in reasons


def test_voice_failure_is_reported_even_if_text_survives():
    messages = [dict(type='find_item', found=True),
                dict(type='say', text='Seen yesterday.', final=True, utterance_id='answer'),
                dict(type='turn', detail='Voice unavailable (ReadTimeout)')]
    valid, audible, reasons = turn_result('yesterday', messages, 'done')
    assert not valid and not audible
    assert 'Voice unavailable (ReadTimeout)' in reasons
