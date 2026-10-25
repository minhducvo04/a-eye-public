"""Shared Store interface and official SpacetimeDB HTTP adapter.

One remote token is bound to one application owner. Personal fields are encrypted
before HTTP. There is no silent local fallback after a remote failure.
"""
import base64
import hashlib
import hmac
import json
import os
from urllib.parse import quote, urlsplit

import httpx

from server import privacy
from server.memory import Cipher, Store


class StorageError(RuntimeError):
    """Safe error: no response bodies, URLs, tokens or personal data."""


def open_store(memory_id, base=None, **kwargs):
    backend = os.environ.get('STORE', 'local')
    if backend not in ('local', 'spacetime'):
        raise ValueError('STORE must be local or spacetime')
    if backend == 'local' or privacy.local_only():
        kwargs.pop('client', None)
        return Store(memory_id, base=base, **kwargs)
    return SpacetimeStore(memory_id, base=base, **kwargs)


class SpacetimeStore(Store):
    """Read my_* views; write typed reducers. Media/embeddings never upload."""
    supports_crops = False
    DOCUMENTS = {'tags.json', 'settings.json', 'videos.json', 'recent.json'}
    COLUMNS = {
        'owners': ('subject',),
        'things': ('id', 'name', 'kind', 'created'),
        'placements': ('id', 'thing_id', 'place_path', 't', 'source', 'confidence'),
        'documents': ('id', 'body'), 'records': ('id', 'body'),
    }

    def __init__(self, memory_id, base=None, clock=None, client=None, token=None):
        super().__init__(memory_id, base=base, cipher=Cipher(''), **({'clock': clock} if clock else {}))
        uri = os.environ.get('SPACETIMEDB_URI', '').rstrip('/')
        module = os.environ.get('SPACETIMEDB_MODULE', '')
        self._token = token if token is not None else os.environ.get('SPACETIMEDB_TOKEN', '')
        parts = urlsplit(uri)
        if (not module or not self._token or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or parts.path not in ('', '/') or
                not (parts.scheme == 'https' or parts.scheme == 'http' and
                     parts.hostname in ('localhost', '127.0.0.1', '::1'))):
            raise StorageError('SpacetimeDB configuration missing or invalid (HTTPS required)')
        self._url = uri + '/v1/database/' + quote(module, safe='')
        self._client = client
        self._owner = None
        self._subject = None

    def _request(self, path, *, args=None, query=None):
        if privacy.local_only():
            raise StorageError('SpacetimeDB blocked by Private or LOCAL_ONLY mode')
        headers = {'Authorization': 'Bearer ' + self._token}
        options = dict(headers=headers, timeout=10, follow_redirects=False)
        if query is not None:
            options.update(content=query, headers=dict(headers, **{'Content-Type': 'text/plain'}))
        else:
            options['json'] = args
        try:
            if self._client is not None:
                response = self._client.post(self._url + path, **options)
            else:
                with httpx.Client() as client:
                    response = client.post(self._url + path, **options)
        except Exception:
            raise StorageError('SpacetimeDB request failed') from None
        if response.status_code >= 300:
            raise StorageError(f'SpacetimeDB request refused (HTTP {response.status_code})') from None
        return response

    def _call(self, reducer, *args):
        self.writable()
        self._request('/call/' + reducer, args=list(args))

    def _rows(self, table):
        columns = self.COLUMNS[table]
        response = self._request('/sql', query=f"SELECT {', '.join(columns)} FROM my_{table};")
        try:
            results = response.json()
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError
            rows = results[0]['rows']
            if not isinstance(rows, list) or any(not isinstance(r, list) or len(r) != len(columns) for r in rows):
                raise ValueError
            return [dict(zip(columns, row)) for row in rows]
        except (ValueError, TypeError, KeyError):
            raise StorageError('SpacetimeDB returned an invalid query result') from None

    def claim(self, owner):
        if not isinstance(owner, str) or not owner:
            raise PermissionError('memory requires an authenticated owner')
        try:
            master = base64.urlsafe_b64decode(os.environ.get('A_EYE_MEMORY_KEY', ''))
            if len(master) != 32:
                raise ValueError
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'a-eye-places-v1',
                           info=owner.encode()).derive(master)
        except Exception:
            raise StorageError('SpacetimeDB requires a valid A_EYE_MEMORY_KEY') from None
        subject = hmac.new(master, b'a-eye-owner-v1\0' + owner.encode(), hashlib.sha256).hexdigest()
        owners = self._rows('owners')
        if owners and (len(owners) != 1 or owners[0]['subject'] != subject):
            raise PermissionError('SpacetimeDB token is bound to another application owner')
        self._call('claim_memory', subject)
        self._owner, self._subject = owner, subject
        self.cipher = Cipher(base64.urlsafe_b64encode(derived))
        return True

    def _require_owner(self):
        self.writable()
        if self._owner is None:
            raise PermissionError('claim memory before reading or writing')

    def owner(self):
        self._require_owner()
        return self._owner

    def _seal(self, row_id, field, value):
        self._require_owner()
        envelope = [self._subject, row_id, field, value]
        return self.cipher.seal(json.dumps(envelope, allow_nan=False).encode()).decode()

    def _open(self, row_id, field, value):
        self._require_owner()
        try:
            envelope = json.loads(self.cipher.open(value.encode()))
            if envelope[:3] != [self._subject, row_id, field] or len(envelope) != 4:
                raise ValueError
            return envelope[3]
        except Exception:
            raise StorageError('SpacetimeDB memory could not be authenticated') from None

    def _read_json(self, name, default):
        self._require_owner()
        if name not in self.DOCUMENTS:
            raise ValueError('unsupported memory document')
        row = next((r for r in self._rows('documents') if r['id'] == name), None)
        return self._open(name, 'body', row['body']) if row else default

    def _write_json(self, name, value):
        if name not in self.DOCUMENTS:
            raise ValueError('unsupported memory document')
        self._call('put_document', name, self._seal(name, 'body', value))

    def things(self):
        self._require_owner()
        return [dict(id=r['id'], owner=self._owner,
                     **{f: self._open(r['id'], f, r[f]) for f in ('name', 'kind', 'created')})
                for r in self._rows('things')]

    def placements(self, thing_id=None):
        self._require_owner()
        rows = [dict(id=r['id'], thing_id=r['thing_id'], owner=self._owner,
                     **{f: self._open(r['id'], f, r[f]) for f in ('place_path', 't', 'source', 'confidence')})
                for r in self._rows('placements') if thing_id is None or r['thing_id'] == thing_id]
        return sorted(rows, key=lambda p: (p['t'], p['id']))

    def _write_place(self, thing, placement=None):
        args = [thing['id'], *(self._seal(thing['id'], f, thing[f]) for f in ('name', 'kind', 'created'))]
        if placement is not None:
            args += [placement['id'], *(self._seal(placement['id'], f, placement[f])
                                      for f in ('place_path', 't', 'source', 'confidence'))]
        self._call('place_thing' if placement else 'put_thing', *args)

    def _delete_places(self, things=(), placements=()):
        self._require_owner()
        if things or placements:
            self._call('delete_memory_rows', list(things), list(placements), [], [])

    def _record_id(self, value):
        self._require_owner()
        return hmac.new(self._subject.encode(), str(value).encode(), hashlib.sha256).hexdigest()

    def records(self):
        self._require_owner()
        return sorted((self._open(r['id'], 'body', r['body']) for r in self._rows('records')),
                      key=lambda r: (r['t'], r['id']))

    def append(self, record):
        clean = dict(id=str(record['id'])[:128], t=float(record['t']),
                     call=str(record.get('call', ''))[:128], note=str(record.get('note', ''))[:2000],
                     labels=[str(v)[:120] for v in record.get('labels', [])[:64]], tags=[], crops={}, emb=None)
        for entry in record.get('tags', [])[:64]:
            tag = {k: str(entry[k])[:120] for k in ('tag', 'label') if k in entry}
            if isinstance(entry.get('place_path'), list):
                from server.memory_places import path_parts
                tag['place_path'] = path_parts(entry['place_path'])
                tag['confidence'] = entry.get('confidence', .6)
            tag.update(bbox=None, near=None)
            clean['tags'].append(tag)
        self._observe_places(dict(clean, source=record.get('source', 'vision')))
        row_id = self._record_id(clean['id'])
        self._call('put_record', row_id, self._seal(row_id, 'body', clean))

    def _rewrite(self, records):
        keep = {self._record_id(r['id']) for r in records}
        gone = [r['id'] for r in self._rows('records') if r['id'] not in keep]
        if gone:
            self._call('delete_memory_rows', [], [], gone, [])

    def save_crop(self, jpeg):
        return None

    def load_crop(self, crop_id):
        return None

    def size_bytes(self):
        self._require_owner()
        return sum(len(str(value).encode()) for table in ('things', 'placements', 'documents', 'records')
                   for row in self._rows(table) for value in row.values())

    def forget_all(self):
        self._require_owner()
        rows = {table: self._rows(table) for table in ('things', 'placements', 'records', 'documents')}
        self._call('delete_memory_rows', *([r['id'] for r in rows[table]] for table in rows))
        return len(rows['records'])
