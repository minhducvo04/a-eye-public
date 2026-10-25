from eval.brain_score import is_grounded, markdown, percentile, score, sentence_count, summarize

CASE = dict(case_id='c1', tag='point', expect=dict(targets=['o2']),
            scene=dict(caption='Keys and a mug.', objects=[dict(id='o1'), dict(id='o2')]))


def facts(drawn=('o2',), speech='Your keys are here.', header=None, **extra):
    header = header if header is not None else dict(agent='helper', draw=[dict(kind='box', target=t) for t in drawn])
    return dict(dict(header=header, format_ok=True, drawn=list(drawn), speech=speech, market=None,
                     provider='local', first_token_ms=210.4, turn_ms=900.0), **extra)


def test_correct_pointing_passes():
    row = score(CASE, facts())
    assert row['format_ok'] and row['grounded'] and row['target_ok'] and row['market_ok'] and row['passed']
    assert row['first_token_ms'] == 210 and row['say'] == 'Your keys are here.'


def test_no_speech_fails():
    assert not score(CASE, facts(speech='  '))['passed']


def test_invented_ids_are_ungrounded_even_when_the_server_drops_them():
    for draw in ([dict(kind='box', target='o9')], [dict(kind='box')], [dict(kind='box', target=[0.1, 0.2])],
                 [dict(kind='arrow', target='o2', from_target='hand')], ['o2']):
        row = score(CASE, facts(header=dict(agent='helper', draw=draw)))
        assert not row['grounded'] and not row['passed']


def test_missing_header_and_clear_ops_are_grounded():
    assert is_grounded(None, {'o1'})
    assert is_grounded(dict(draw='junk'), {'o1'})
    assert is_grounded(dict(draw=[dict(kind='clear'), dict(kind='box', target='o1')]), {'o1'})


def test_format_is_reported_but_does_not_decide_pass():
    row = score(CASE, facts(format_ok=False))
    assert not row['format_ok'] and row['passed']


def test_wrong_object_and_abstain_cases():
    assert not score(CASE, facts(drawn=['o1']))['target_ok']
    assert not score(CASE, facts(drawn=[]))['target_ok']
    absent = dict(CASE, expect=dict(targets=[]))
    assert score(absent, facts(drawn=[], speech='I do not see it.'))['passed']
    assert not score(absent, facts(drawn=['o1']))['passed']


def test_unscored_targets_do_not_block_pass():
    row = score(dict(CASE, expect={}), facts(drawn=['o1']))
    assert row['target_ok'] is None and row['passed']


def test_market_category_must_match_exactly():
    shop = dict(CASE, expect=dict(market='ram'))
    assert score(shop, facts(market='ram'))['passed']
    assert not score(shop, facts())['passed']
    assert not score(CASE, facts(market='fan'))['passed']


def test_brevity():
    assert sentence_count('') == 0
    assert sentence_count('No punctuation') == 1
    assert sentence_count('One. Two! Three?') == 3
    assert sentence_count('Use 3.5 mm screws.') == 1
    row = score(CASE, facts(speech='One. Two. Three.'))
    assert not row['brief'] and row['passed']


def test_summary_and_table():
    rows = [score(CASE, facts(first_token_ms=100)),
            score(dict(CASE, tag='shop'), facts(drawn=[], speech='', provider='canned', first_token_ms=300))]
    summary = summarize(rows)
    assert summary['n'] == 2 and summary['passed'] == 0.5
    assert summary['providers'] == ['canned', 'local']
    assert summary['by_tag'] == {'point': 1.0, 'shop': 0.0}
    assert summary['first_p50_ms'] == 100 and summary['first_p90_ms'] == 300
    table = markdown({'qwen': summary})
    assert '| qwen | 2 | 50% |' in table and '| shop | 0% |' in table


def test_percentile_edges():
    assert percentile([], 50) == 0.0
    assert percentile([5], 90) == 5.0
    assert percentile(list(range(1, 11)), 90) == 9.0
