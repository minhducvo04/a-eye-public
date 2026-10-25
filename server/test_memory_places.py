"""Synthetic location graph and storage contract tests; no real credentials or media."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from server import memory, privacy
from server.memory_storage import SpacetimeStore, StorageError, open_store
from server.recent_memory import RecentMemory


class FakeSpacetime:
    """HTTP-shaped fake: typed reducers, private tables and caller-filtered views."""
    def __init__(self):
        self.rows = {name: {} for name in ('owners', 'things', 'placements', 'documents', 'records')}
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        owner = request.headers['authorization'].removeprefix('Bearer ')
        path = request.url.path
        if path.endswith('/sql'):
            query = request.content.decode()
            columns, view = query.removeprefix('SELECT ').removesuffix(';').split(' FROM ')
            view, _, condition = view.partition(' WHERE id = ')
            names = columns.split(', ')
            rows = [r for r in self.rows[view.removeprefix('my_')].values() if r['owner'] == owner]
            if condition:
                rows = [r for r in rows if r['id'] == condition.strip("'")]
            return httpx.Response(200, json=[{'schema': {'elements': []},
                                             'rows': [[r[n] for n in names] for r in rows]}])
        reducer = path.rsplit('/', 1)[-1]
        args = json.loads(request.content)
        if reducer == 'claim_memory':
            subject, = args
            old = self.rows['owners'].get(owner)
            if old and old['subject'] != subject:
                return httpx.Response(403, text='private details must not escape')
            self.rows['owners'][owner] = dict(owner=owner, subject=subject)
            return httpx.Response(200)
        if reducer in ('put_thing', 'place_thing'):
            thing_id, name, kind, created, *rest = args
            old = self.rows['things'].get(thing_id)
            if old and old['owner'] != owner:
                return httpx.Response(403)
            self.rows['things'][thing_id] = dict(id=thing_id, owner=owner, name=name, kind=kind,
                                                created=old['created'] if old else created)
            if rest:
                pid, place_path, t, source, confidence = rest
                self.rows['placements'][pid] = dict(id=pid, thing_id=thing_id, owner=owner,
                                                    place_path=place_path, t=t, source=source,
                                                    confidence=confidence)
        elif reducer in ('put_document', 'put_record'):
            row_id, body = args
            table = 'documents' if reducer == 'put_document' else 'records'
            self.rows[table][(owner, row_id)] = dict(id=row_id, owner=owner, body=body)
        elif reducer == 'delete_memory_rows':
            things, placements, records, documents = args
            for table, ids in zip(('things', 'placements', 'records', 'documents'),
                                  (things, placements, records, documents)):
                self.rows[table] = {k: r for k, r in self.rows[table].items()
                                    if not (r['owner'] == owner and (r['id'] in ids or
                                            table == 'placements' and r['thing_id'] in things))}
        else:
            return httpx.Response(404)
        return httpx.Response(200)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('A_EYE_MEMORY_KEY', Fernet.generate_key().decode())
    monkeypatch.delenv('LOCAL_ONLY', raising=False)
    monkeypatch.setenv('SPACETIMEDB_URI', 'https://synthetic.invalid')
    monkeypatch.setenv('SPACETIMEDB_MODULE', 'synthetic')
    monkeypatch.setenv('SPACETIMEDB_TOKEN', 'fake-owner-a')
    token = privacy.PRIVATE.set(False)
    fake = FakeSpacetime()
    client = httpx.Client(transport=httpx.MockTransport(fake.handle))
    yield tmp_path, fake, client
    client.close()
    privacy.PRIVATE.reset(token)


@pytest.fixture(params=['local', 'spacetime'])
def store(request, setup):
    base, fake, client = setup
    if request.param == 'local':
        result = memory.Store('synthetic', base=base, clock=lambda: 1000)
    else:
        result = SpacetimeStore('synthetic', base=base, clock=lambda: 1000, client=client)
    assert result.claim('synthetic-owner')
    return result


def test_history_current_prefix_and_last_actual_move(store):
    first = store.place('keys', ['desk', 'drawer', 'blue bin'], t=100, source='tag', confidence=1)
    store.place('keys', ['shelf'], t=300, source='vision', confidence=.8)
    store.place('keys', ['desk'], t=200, source='capture', confidence=.9)  # late arrival
    store.place('keys', ['shelf'], t=400, source='vision', confidence=.7)
    store.place('pen', ['desktop'], t=450)
    assert store.where('keys')['place_path'] == ['shelf']
    assert store.where(first['thing_id'])['t'] == 400
    assert len(store.placements(first['thing_id'])) == 4
    assert store.last_move('keys') == 300
    assert store.in_place(['desk']) == []  # no historical or string-prefix matches
    assert [r['name'] for r in store.in_place(['shelf'])] == ['keys']
    assert store.where('missing') is None
    assert store.last_move('pen') is None  # first sighting is not evidence of movement


def test_nested_prefix_and_owner_isolation(store, setup):
    store.place('keys', ['desk', 'drawer', 'blue bin'])
    assert len(store.in_place(['desk', 'drawer'])) == 1
    base, fake, _ = setup
    if isinstance(store, SpacetimeStore):
        client = httpx.Client(transport=httpx.MockTransport(fake.handle))
        other = SpacetimeStore('other', base=base, client=client, token='fake-owner-b')
    else:
        other = memory.Store('other', base=base)
    assert other.claim('other-owner')
    assert other.things() == [] and other.in_place(['desk']) == []


@pytest.mark.parametrize('kwargs', [dict(place_path=[]), dict(place_path='desk'),
                                   dict(place_path=['desk', '']), dict(confidence=1.1),
                                   dict(confidence=float('nan')), dict(t=float('inf')),
                                   dict(source='model-guess')])
def test_invalid_placements_never_write(store, kwargs):
    args = dict(place_path=['desk'], confidence=.8, t=100, source='tag')
    args.update(kwargs)
    with pytest.raises(ValueError):
        store.place('keys', **args)
    assert store.things() == [] and store.placements() == []


def test_tags_find_and_no_invented_home_location(store):
    store.set_tag('keys', home='bowl')
    assert store.things()[0]['name'] == 'keys'
    assert store.where('keys') is None
    store.set_tag('keys', place_path=['desk', 'drawer'])
    result = asyncio.run(memory.find(store, 'where are my keys?'))
    assert result['found'] and result['place'] == 'desk / drawer'
    assert result['how'] == 'tag' and result['t'] == 1000


def test_recent_memory_roundtrip_and_clear(store):
    store.save_settings(dict(consent=dict(person=True, caregiver=True), remembering=True))
    recent = RecentMemory(clock=lambda: 10, started=0)
    recent.bind(store, 'synthetic-call')
    recent.add(dict(caption='keys on the desk', objects=[]), 10)
    recent.entries.clear()  # force read through storage, not the in-memory cache
    assert recent.find('where are my keys just now?')['found']
    recent.clear()
    assert not store.recent_records('synthetic-call')


def test_recent_private_cleanup_never_breaks_call_teardown(setup, monkeypatch):
    base, fake, client = setup
    store = SpacetimeStore('synthetic', base=base, client=client)
    store.claim('synthetic-owner')
    recent = RecentMemory()
    recent.bind(store, 'synthetic-call')
    before = len(fake.requests)
    monkeypatch.setenv('LOCAL_ONLY', '1')
    recent.clear()
    assert recent.store is None and not recent.entries
    assert len(fake.requests) == before


def test_forget_and_retention_cover_graph(store):
    store.place('keys', ['desk'], t=10)
    store.place('keys', ['bowl'], t=900)
    store.forget_since(800)
    assert store.where('keys')['place_path'] == ['desk']
    store.purge_old(days=.001)
    assert store.placements() == []
    store.place('keys', ['desk'])
    store.forget_item('keys')
    assert store.things() == []
    store.place('pen', ['desk'])
    store.forget_all()
    assert store.things() == [] and store.placements() == []


def test_remote_encrypts_all_personal_fields_and_refuses_shared_token(setup):
    base, fake, client = setup
    store = SpacetimeStore('synthetic', base=base, client=client)
    assert store.claim('synthetic-owner')
    store.place('secret keys', ['secret drawer'])
    store.append(dict(id='synthetic-record', t=1000, note='secret note', labels=['secret keys'],
                      tags=[], crops={'keys': 'DO-NOT-UPLOAD'}, frame_b64='DO-NOT-UPLOAD', emb={'q': 'NO'}))
    payloads = b' '.join(r.content for r in fake.requests)
    assert not any(v in payloads for v in (b'secret keys', b'secret drawer', b'synthetic-owner',
                                         b'secret note', b'DO-NOT-UPLOAD'))
    assert store.records()[0]['note'] == 'secret note'
    assert not store.records()[0].get('crops') and not store.records()[0].get('emb')
    second = SpacetimeStore('other', base=base, client=client)
    with pytest.raises(PermissionError):
        second.claim('another-owner')


def test_switch_and_private_guard(setup, monkeypatch):
    base, fake, client = setup
    monkeypatch.delenv('STORE', raising=False)
    assert type(open_store('synthetic', base=base)) is memory.Store
    monkeypatch.setenv('STORE', 'spacetime')
    store = open_store('synthetic', base=base, client=client)
    assert isinstance(store, SpacetimeStore)
    store.claim('synthetic-owner')
    before = len(fake.requests)
    monkeypatch.setenv('LOCAL_ONLY', '1')
    with pytest.raises(StorageError):
        store.place('keys', ['desk'])
    assert len(fake.requests) == before
    assert type(open_store('synthetic', base=base)) is memory.Store
    monkeypatch.delenv('LOCAL_ONLY')
    token = privacy.PRIVATE.set(True)
    try:
        with pytest.raises(StorageError):
            store.records()
        assert len(fake.requests) == before
    finally:
        privacy.PRIVATE.reset(token)
    monkeypatch.setenv('STORE', 'typo')
    with pytest.raises(ValueError):
        open_store('synthetic', base=base)


def test_remote_requires_key_and_redacts_failures(setup, monkeypatch):
    base, fake, client = setup
    monkeypatch.delenv('A_EYE_MEMORY_KEY')
    with pytest.raises(StorageError):
        SpacetimeStore('synthetic', base=base, client=client).claim('synthetic-owner')
    assert not fake.requests
    monkeypatch.setenv('A_EYE_MEMORY_KEY', Fernet.generate_key().decode())
    failing = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500, text='SECRET')))
    with pytest.raises(StorageError) as error:
        SpacetimeStore('synthetic', base=base, client=failing).claim('synthetic-owner')
    assert 'SECRET' not in str(error.value)


def test_tools_use_selected_store(store):
    from server.rewind_tools import run_memory_tool
    memories = SimpleNamespace(for_call=lambda _: (store, None), embedder=None, vision=None)
    app = SimpleNamespace(state=SimpleNamespace(memories=memories))
    session = SimpleNamespace(session_id='synthetic-call', camera=False, scene=None)
    asyncio.run(run_memory_tool('tag_item', dict(name='keys', place_path=['desk']), session, app))
    result, _ = asyncio.run(run_memory_tool('find_item', dict(item='keys'), session, app))
    assert result['found'] and result['place'] == 'desk'


def test_live_probe_writes_reads_and_deletes_only_synthetic_rows(setup, capsys):
    from server.places_probe import run
    _, fake, client = setup
    # No owner binding or personal-row reads are necessary for this probe.
    fake.rows['things']['existing'] = dict(id='existing', owner='fake-owner-a', name='PRIVATE',
                                          kind='PRIVATE', created='PRIVATE')
    assert run(client=client) == 0
    assert list(fake.rows['things']) == ['existing']
    assert not fake.rows['placements'] and not fake.rows['owners']
    assert 'cleanup: PASS' in capsys.readouterr().out
    assert all('WHERE id = ' in r.content.decode() for r in fake.requests if r.url.path.endswith('/sql'))


def test_live_probe_cleanup_after_committed_response_failure(setup, capsys):
    from server.places_probe import run
    _, fake, _ = setup
    def fail_after_commit(request):
        response = fake.handle(request)
        return httpx.Response(500) if request.url.path.endswith('/place_thing') else response
    client = httpx.Client(transport=httpx.MockTransport(fail_after_commit))
    with pytest.raises(StorageError):
        run(client=client)
    assert not fake.rows['things'] and not fake.rows['placements']
    assert 'cleanup: PASS' in capsys.readouterr().out


def test_ciphertext_substitution_and_wrong_user_key_rejected(setup):
    base, fake, client = setup
    a = SpacetimeStore('synthetic', base=base, client=client)
    a.claim('owner-a')
    a.place('keys', ['desk'])
    first = next(iter(fake.rows['things'].values()))
    first['kind'] = first['name']
    with pytest.raises(StorageError, match='authenticated'):
        a.things()
    b = SpacetimeStore('other', base=base, client=client, token='fake-owner-b')
    b.claim('owner-b')
    with pytest.raises(StorageError, match='authenticated'):
        b._open(first['id'], 'name', first['name'])


def test_vision_writer_builds_graph_without_remote_media(store):
    from server.test_memory import Words, Person, FRAME, KEYS, CUPS
    store.set_tag('keys')
    writer = memory.Writer(store, 'synthetic-call', embedder=Words(), person_check=Person())
    asyncio.run(writer.observe(dict(caption='keys beside the left cup', objects=[KEYS] + CUPS), FRAME))
    current = store.where('keys')
    assert current['source'] == 'vision' and current['confidence'] == .8
    assert current['place_path'] == ['under the left red cup']
    assert asyncio.run(memory.find(store, 'keys'))['found']
    if isinstance(store, SpacetimeStore):
        assert not store.dir.exists()
        assert all(not r['crops'] and r['emb'] is None for r in store.records())


def test_long_term_graph_respects_time_bounds(store):
    from server.recent_memory import long_term
    store.save_settings(dict(consent=dict(person=True, caregiver=True), remembering=True))
    store.place('keys', ['earlier place'], t=100)
    store.place('keys', ['current place'], t=300)
    log = RecentMemory()
    log.started_wall = 200
    session = SimpleNamespace(recent_memory=log, rewind=SimpleNamespace(
        memory_writer=SimpleNamespace(store=store, embedder=None)))
    result = asyncio.run(long_term(session, 'where were keys last time'))
    assert result['place'] == 'earlier place'


def test_auto_attach_binds_recent_only_with_consent(tmp_path, monkeypatch):
    monkeypatch.setenv('STORE', 'local')
    memories = memory.Memories(base=tmp_path)
    rewind = SimpleNamespace(owner='synthetic-owner', session_id='synthetic-call', memory_writer=None,
                             clips=[], session=SimpleNamespace(recent_memory=RecentMemory()))
    store = memories.auto_attach(rewind)
    assert rewind.session.recent_memory.store is None
    store.save_settings(dict(consent=dict(person=True, caregiver=True), remembering=True))
    memories.auto_attach(rewind)
    assert rewind.session.recent_memory.store is not None
    store.save_settings(dict(remembering=False))
    memories.auto_attach(rewind)
    assert rewind.session.recent_memory.store is None


@pytest.mark.parametrize('response', [httpx.Response(302, headers={'Location': 'https://other.invalid'}),
                                     httpx.Response(200, json=[{'rows': [{}]}])])
def test_remote_rejects_redirects_and_malformed_results(setup, response):
    base, _, _ = setup
    client = httpx.Client(transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(StorageError):
        SpacetimeStore('synthetic', base=base, client=client).claim('synthetic-owner')
