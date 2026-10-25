import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from scripts import export_data as export


def fixture(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for entity, rows in export.sample().items():
        (source / f'{entity}.ndjson').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return source


def read_rows(output, entity):
    return [json.loads(line) for line in (output / f'{entity}.ndjson').read_text().splitlines()]


def test_schema_and_sample(tmp_path):
    schema = json.loads(export.SCHEMA.read_text())
    Draft202012Validator.check_schema(schema)
    for entity in export.ENTITIES:
        for field in schema['$defs'][entity]['properties'].values():
            assert field['description'] and field['unit']
            assert field['privacy'] in {'public', 'personal', 'never-export'}
    output = tmp_path / 'out'
    manifest = export.export_data(export.sample(), output, b'x' * 32)
    assert manifest['counts'] == {entity: 1 for entity in export.ENTITIES}
    assert manifest['schema_sha256'] == hashlib.sha256(export.SCHEMA.read_bytes()).hexdigest()
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for entity in export.ENTITIES:
        validator.validate(read_rows(output, entity)[0])


def test_privacy_hashes_and_text(tmp_path):
    rows = export.sample()
    rows['call'][0]['user_id'] = 'user-secret'
    for records in rows.values():
        records[0].update(frames='FRAME_SECRET', audio='AUDIO_SECRET', crops={'text': 'CROP_SECRET'},
                          raw_transcripts='OTHER_SECRET', unexpected={'user_id': 'user-secret'})
    rows['turn'][0].update(text='OTHER_SECRET', text_reviewed=False)
    manifest = export.export_data(rows, tmp_path / 'out', b'x' * 32)
    payload = ''.join(p.read_text() for p in (tmp_path / 'out').iterdir())
    for secret in ('user-secret', 'FRAME_SECRET', 'AUDIO_SECRET', 'CROP_SECRET', 'OTHER_SECRET', 'raw_transcripts'):
        assert secret not in payload
    call = read_rows(tmp_path / 'out', 'call')[0]
    assert len(call['user_id']) == 64
    assert call['user_id'] != hashlib.sha256(b'user-secret').hexdigest()
    assert read_rows(tmp_path / 'out', 'turn')[0]['text'] is None
    assert read_rows(tmp_path / 'out', 'turn')[0]['call_id'] == call['id']
    assert manifest['counts']['call'] == 1


def test_private_calls_and_selection(tmp_path):
    rows = export.sample()
    rows['call'].append(dict(rows['call'][0], id='private', private=True))
    rows['turn'].append(dict(rows['turn'][0], call_id='private', text='PRIVATE_SECRET'))
    selected = export.select(rows, since='2026-10-24', until='2026-10-25')
    export.export_data(selected, tmp_path / 'out', b'x' * 32)
    assert len(read_rows(tmp_path / 'out', 'call')) == 1
    assert len(read_rows(tmp_path / 'out', 'turn')) == 1
    assert export.select(rows, call_id='absent')['call'] == []
    assert export.select(rows, since='2026-10-25', until='2026-10-26')['call'] == []
    with pytest.raises(ValueError):
        export.select(rows, since='2026-10-25', until='2026-10-24')


def test_invalid_rows_do_not_publish_partial_output(tmp_path):
    rows = export.sample()
    rows['tool_call'][0]['ms'] = -1
    with pytest.raises(ValidationError):
        export.export_data(rows, tmp_path / 'out', b'x' * 32)
    assert not (tmp_path / 'out').exists()


def test_source_and_native_memory(tmp_path):
    source = fixture(tmp_path)
    memory = tmp_path / 'memory' / 'p-synthetic'
    memory.mkdir(parents=True)
    record = dict(id='m1', call='sample-call', t=1792843201, note='DO_NOT_EXPORT', crops={},
                  tags=[dict(tag='keys', near={'label': 'bowl'}, relation='in')])
    (memory / 'log.jsonl').write_text(json.dumps(record) + '\n')
    rows = export.load_source(source, memory.parent)
    assert rows['memory_event'][-1]['item'] == 'keys'
    export.export_data(rows, tmp_path / 'out', b'x' * 32)
    assert 'DO_NOT_EXPORT' not in (tmp_path / 'out' / 'memory_event.ndjson').read_text()


def test_duplicates_or_orphans_fail(tmp_path):
    rows = export.sample()
    rows['turn'][0]['call_id'] = 'missing'
    with pytest.raises(ValueError):
        export.export_data(rows, tmp_path / 'out', b'x' * 32)
    rows = export.sample()
    rows['call'].append(rows['call'][0])
    with pytest.raises(ValueError):
        export.export_data(rows, tmp_path / 'out', b'x' * 32)


def test_schema_rejects_forbidden_and_unknown_fields(tmp_path):
    export.export_data(export.sample(), tmp_path / 'out', b'x' * 32)
    row = read_rows(tmp_path / 'out', 'turn')[0]
    validator = Draft202012Validator(json.loads(export.SCHEMA.read_text()))
    for field in ('frames', 'audio', 'crops', 'raw_transcripts', 'frame_b64'):
        with pytest.raises(ValidationError):
            validator.validate(dict(row, **{field: 'forbidden'}))


def test_hmac_is_stable_and_key_scoped(tmp_path):
    for name, key in (('one', b'x' * 32), ('two', b'x' * 32), ('three', b'y' * 32)):
        export.export_data(export.sample(), tmp_path / name, key)
    one = read_rows(tmp_path / 'one', 'call')[0]
    two = read_rows(tmp_path / 'two', 'call')[0]
    three = read_rows(tmp_path / 'three', 'call')[0]
    assert one == two
    assert one['id'] != three['id'] and one['user_id'] != three['user_id']


def test_cli_real_source_and_private_error_output(tmp_path, monkeypatch, capsys):
    source = fixture(tmp_path)
    monkeypatch.setenv('A_EYE_EXPORT_HASH_KEY', 'x' * 32)
    assert export.main(['--source', str(source), '--call-id', 'sample-call', '--output', str(tmp_path / 'out')]) == 0
    assert len(read_rows(tmp_path / 'out', 'call')) == 1
    (source / 'tool_call.ndjson').write_text('{"ms": "PRIVATE_BAD_VALUE"}\n')
    assert export.main(['--source', str(source), '--call-id', 'sample-call', '--output', str(tmp_path / 'bad')]) == 1
    captured = capsys.readouterr()
    assert 'PRIVATE_BAD_VALUE' not in captured.err + captured.out
    assert not (tmp_path / 'bad').exists()
