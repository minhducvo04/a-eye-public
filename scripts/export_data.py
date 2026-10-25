"""Offline, allowlisted export. See docs/data-export.md for the local snapshot contract."""
import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from jsonschema import Draft202012Validator

SCHEMA = Path(__file__).resolve().parents[1] / 'schemas' / 'export-v1.json'
ENTITIES = ('call', 'turn', 'step_progress', 'memory_event', 'tool_call', 'safety_stop', 'proof_counter')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        # Dates denote UTC midnight; ambiguous local datetimes are refused.
        if len(value) != 10:
            raise ValueError('timestamps require a timezone')
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_lines(path):
    if path.is_symlink():
        raise ValueError('symlink input refused')
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('source rows must be objects')
            result.append(row)
    return result


def load_source(source, memory_dir=None):
    """Read explicit metadata snapshots; optionally adapt native plaintext memory logs."""
    source = Path(source)
    if not source.is_dir() or source.is_symlink():
        raise ValueError('source must be a local directory')
    if not (source / 'call.ndjson').is_file():
        raise ValueError('source requires call.ndjson metadata')
    rows = {entity: read_lines(source / f'{entity}.ndjson') for entity in ENTITIES}
    if memory_dir is not None:
        memory_dir = Path(memory_dir)
        if not memory_dir.is_dir() or memory_dir.is_symlink():
            raise ValueError('memory directory unavailable')
        calls = rows['call']
        for person in sorted(memory_dir.iterdir()):
            if person.is_symlink():
                raise ValueError('symlink memory directory refused')
            if not person.is_dir():
                continue
            for record in read_lines(person / 'log.jsonl'):
                # Native Writer stores the first 12 characters of the session id.
                matches = [c for c in calls if c['id'] == record.get('call') or c['id'][:12] == record.get('call')]
                if len(matches) > 1:
                    raise ValueError('ambiguous native memory call prefix')
                if not matches:
                    continue  # unrelated memory has no exportable call metadata
                for index, tag in enumerate(record.get('tags', [])):
                    rows['memory_event'].append(dict(id=f"{person.name}:{record['id']}:{index}",
                        call_id=matches[0]['id'], item=tag['tag'], place=(tag.get('near') or {}).get('label'),
                        kind='sighting', t=record['t']))
    return rows


def select(rows, call_id=None, since=None, until=None):
    if bool(since) != bool(until) or (call_id is not None and since is not None):
        raise ValueError('choose one call or a complete date range')
    start, end = (timestamp(since), timestamp(until)) if since else (None, None)
    if start is not None and start >= end:
        raise ValueError('range end must be after start')
    calls = [c for c in rows['call'] if (call_id is None or c['id'] == call_id) and
             (start is None or (c.get('started') is not None and start <= timestamp(c['started']) < end))]
    ids = {c['id'] for c in calls}
    return {entity: calls if entity == 'call' else [r for r in rows[entity] if r['call_id'] in ids]
            for entity in ENTITIES}


def pseudonym(key, domain, value):
    if not isinstance(value, str) or not value:
        raise ValueError('identifiers must be nonempty strings')
    return hmac.new(key, json.dumps([domain, value], separators=(',', ':')).encode(), hashlib.sha256).hexdigest()


def export_data(rows, output, key):
    if len(key) < 32:
        raise ValueError('hash key must contain at least 32 bytes')
    output = Path(output)
    if output.exists():
        raise ValueError('output must not already exist')
    schema_bytes = SCHEMA.read_bytes()
    schema = json.loads(schema_bytes)
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    calls = {}
    for call in rows['call']:
        if call['id'] in calls:
            raise ValueError('duplicate call id')
        if type(call.get('private')) is not bool:
            raise ValueError('call privacy must be explicitly known')
        calls[call['id']] = call
    clean = {entity: [] for entity in ENTITIES}
    for entity in ENTITIES:
        seen = set()
        props = schema['$defs'][entity]['properties']
        for row in rows[entity]:
            parent = row['id'] if entity == 'call' else row['call_id']
            if parent not in calls:
                raise ValueError('orphan entity row')
            if calls[parent]['private']:
                continue
            result = {name: row.get(name) for name, field in props.items() if field['privacy'] != 'never-export'}
            result.update(v=1, entity=entity)
            result['id'] = pseudonym(key, entity, row['id'] if entity == 'call' else json.dumps([parent, row['id']]))
            if result['id'] in seen:
                raise ValueError('duplicate entity id')
            seen.add(result['id'])
            if entity == 'call':
                result['user_id'] = pseudonym(key, 'user', row['user_id']) if row.get('user_id') is not None else None
                for name in ('started', 'ended'):
                    if result[name] is not None:
                        result[name] = timestamp(result[name]).isoformat().replace('+00:00', 'Z')
                if result['started'] and result['ended'] and timestamp(result['ended']) < timestamp(result['started']):
                    raise ValueError('call ends before it starts')
            else:
                result['call_id'] = pseudonym(key, 'call', parent)
            if entity == 'turn' and row.get('text_reviewed') is not True:
                result['text'] = None
            validator.validate(result)
            clean[entity].append(result)
    manifest = dict(v=1, schema_sha256=hashlib.sha256(schema_bytes).hexdigest(),
                    counts={e: len(clean[e]) for e in ENTITIES},
                    excluded_private_calls=sum(c['private'] for c in calls.values()),
                    files={e: f'{e}.ndjson' for e in ENTITIES})
    # Validate everything before creating any output; publish a complete directory.
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.aeye-export-', dir=output.parent))
    try:
        for entity, records in clean.items():
            path = staging / f'{entity}.ndjson'
            path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in records))
            path.chmod(0o600)
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (staging / 'manifest.json').chmod(0o600)
        if output.exists():
            raise ValueError('output appeared during export')
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def sample():
    """Entirely synthetic; never reads application data or credentials."""
    return {
        'call': [dict(id='sample-call', started='2026-10-24T12:00:00Z', ended='2026-10-24T12:01:00Z', mode='pc', private=False, user_id='synthetic-user')],
        'turn': [dict(id='turn-1', call_id='sample-call', t_ms=1000, role='assistant', text='Switch the PSU off and unplug it.', text_reviewed=True, router_route='local', brain_label='synthetic', stt_ms=0, vlm_ms=12, brain_first_token_ms=20, first_audio_ms=30, turn_ms=100)],
        'step_progress': [dict(id='step-1', call_id='sample-call', task='pc', step='safety', hints_used=1, level='confirmed')],
        'memory_event': [dict(id='memory-1', call_id='sample-call', item='keys', place='bowl', kind='sighting', t=1792843201)],
        'tool_call': [dict(id='tool-1', call_id='sample-call', name='find_item', ok=True, ms=10)],
        'safety_stop': [dict(id='stop-1', call_id='sample-call', rule_id='power_off_required', t=1792843202)],
        'proof_counter': [dict(id='proof-1', call_id='sample-call', t=1792843260, pictures_saved=0, pictures_sent=0, cloud_ai_calls=0, bytes_to_cloud=0)],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--memory-dir', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--call-id')
    parser.add_argument('--since', help='UTC date or timezone-qualified datetime, inclusive')
    parser.add_argument('--until', help='UTC date or timezone-qualified datetime, exclusive')
    args = parser.parse_args(argv)
    try:
        if args.sample:
            if any((args.source, args.memory_dir, args.call_id, args.since, args.until)):
                raise ValueError('sample cannot be combined with real source or selectors')
            rows, key = sample(), b'a-eye-synthetic-sample-only-key-v1'
        else:
            if not args.source or not (args.call_id or (args.since and args.until)):
                raise ValueError('source and one call or complete date range required')
            key = os.environ.get('A_EYE_EXPORT_HASH_KEY', '').encode()
            rows = select(load_source(args.source, args.memory_dir), args.call_id, args.since, args.until)
        manifest = export_data(rows, args.output, key)
    except Exception:
        # Validation/decryption exceptions can embed private source values. Never echo them.
        print('Export failed: check source format, selection, privacy flags, hash key and unused output path.', file=sys.stderr)
        return 1
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
