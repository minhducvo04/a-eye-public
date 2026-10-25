"""Rewind and local memory separate people by the VERIFIED Access email only (server.auth sets it)."""
import hashlib
import time

import jwt
from fastapi.testclient import TestClient

from server import memory as mem
from server.app import create_app
from server.rewind import Registry
from server.test_access import access, key, token  # noqa: F401  (pytest fixtures and the JWT helper)
from server.test_memory import KEYS, Person, Words, write
from server.test_rewind import FakeTL, JPEG, fake_encoder


def person(email):
    return 'access:' + hashlib.sha256(email.encode()).hexdigest()[:16]


def forged(email):
    """An unsigned look-alike Access JWT with someone else's email."""
    return jwt.encode(dict(email=email, exp=int(time.time()) + 300), 'not-the-key', algorithm='HS256')


def app_with(tmp_path):
    app = create_app(static_dir='/nonexistent')
    app.state.memories = mem.Memories(base=tmp_path, embedder=Words(), person_check=Person())
    app.state.rewind = Registry(FakeTL(), encoder=fake_encoder, memories=app.state.memories)
    return app


def seed_keys(app, owner):
    store = app.state.memories.for_owner(owner)
    store.set_tag('keys', home='blue bowl')
    write(store, dict(caption='keys on the desk', objects=[KEYS]))
    return store


def test_token_with_a_forged_cookie_is_still_token(tmp_path, monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'test-token-value')
    app = app_with(tmp_path)
    seed_keys(app, person('victim@example.test'))
    headers = {'authorization': 'Bearer test-token-value',
               'cookie': 'CF_Authorization=' + forged('victim@example.test'),
               'cf-access-jwt-assertion': forged('victim@example.test')}
    with TestClient(app) as client:
        found = client.post('/api/memory/find', json={'query': 'where are my keys?'}, headers=headers).json()
        assert found['found'] is False                     # the token caller has its own, empty memory
        gone = client.post('/api/memory/forget', json={'scope': 'everything'}, headers=headers).json()
        assert gone['deleted'] == 0
    assert app.state.memories.for_owner(person('victim@example.test')).count() == 1
    assert not (tmp_path / mem.person_id('token')).exists()


def test_valid_access_jwt_gets_its_own_memory(tmp_path, access, key):  # noqa: F811
    app = app_with(tmp_path)
    seed_keys(app, person('synthetic@example.test'))
    mine = {'cf-access-jwt-assertion': token(key)}
    other = {'cf-access-jwt-assertion': token(key, email='other@example.test')}
    with TestClient(app) as client:
        assert client.post('/api/memory/find', json={'query': 'keys'}, headers=mine).json()['found'] is True
        assert client.post('/api/memory/find', json={'query': 'keys'}, headers=other).json()['found'] is False
        assert client.get('/api/memory/status', headers=other).json()['total'] == 0


def test_rewind_routes_reject_a_forged_owner(tmp_path, monkeypatch):
    monkeypatch.setenv('A_EYE_TOKEN', 'test-token-value')
    app = app_with(tmp_path)
    rewind = app.state.rewind.open('alice-call', on=True, owner=person('alice@example.test'))
    rewind.add_frame(JPEG)
    rewind.keyframe('first')
    headers = {'authorization': 'Bearer test-token-value', 'cookie': 'CF_Authorization=' + forged('alice@example.test')}
    with TestClient(app) as client:
        for path in ('/api/rewind/flush', '/api/rewind/recap', '/api/rewind/forget'):
            assert client.post(path, json={'session_id': 'alice-call'}, headers=headers).status_code == 404
        assert client.get('/api/rewind/status', params={'session_id': 'alice-call'}, headers=headers).json()['clips'] == 0
    assert rewind.clips                                  # nothing of Alice's call was touched
