"""Personal web accounts. No provider credentials, email, name or media are stored here."""
import asyncio
from contextlib import contextmanager, suppress
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
from time import time
import zipfile

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import HTTPConnection

from server.auth import AccessKeys
from server import memory
from server.growth import GrowthLog, for_owner as growth_for_owner
from server.memory_storage import SpacetimeStore, StorageError
from server.rewind import identity, registry

COOKIE = 'aeye_session'
CHALLENGE = 'aeye_login'
DAY = 86400
router = APIRouter()


def secret():
    value = os.environ.get('SESSION_SECRET', '')
    if len(value.encode()) < 32:
        raise HTTPException(503, 'Accounts need a SESSION_SECRET of at least 32 random bytes.')
    return value


class GoogleKeys(AccessKeys):
    def __init__(self, **kwargs):
        super().__init__(path='/oauth2/v3/certs', dependency='google_certs', **kwargs)


google_keys = GoogleKeys()


class Database:
    def __init__(self, path, clock=time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS owners (
                    owner TEXT PRIMARY KEY, kind TEXT NOT NULL, expires REAL,
                    deleting INTEGER NOT NULL DEFAULT 0, turns INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    sid TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS used_logins (
                    nonce TEXT PRIMARY KEY, expires REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA secure_delete=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def consume_login(self, db, challenge):
        now = time()
        if challenge['exp'] <= now:
            raise HTTPException(403, 'Reload the sign-in page.')
        db.execute('DELETE FROM used_logins WHERE expires<=?', (now,))
        if db.execute('INSERT OR IGNORE INTO used_logins VALUES (?, ?)',
                      (digest(challenge['nonce']), challenge['exp'])).rowcount != 1:
            raise HTTPException(403, 'Reload the sign-in page.')

    def issue(self, owner, kind, challenge=None):
        now = self.clock()
        duration = DAY if kind == 'guest' else 30 * DAY
        sid = secrets.token_urlsafe(32)
        with self.connect() as db:
            if challenge is not None:
                self.consume_login(db, challenge)
            db.execute('INSERT OR IGNORE INTO owners(owner, kind, expires) VALUES (?, ?, ?)',
                       (owner, kind, now + duration if kind == 'guest' else None))
            row = db.execute('SELECT * FROM owners WHERE owner=?', (owner,)).fetchone()
            if row['deleting']:
                raise HTTPException(409, 'Deletion is still pending. Try again shortly.')
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (digest(sid), owner, now + duration))
        return jwt.encode(dict(sid=sid, exp=int(now + duration), aud='aeye-session', iss='aeye'),
                          secret(), algorithm='HS256'), duration

    def lookup(self, sid):
        with self.connect() as db:
            row = db.execute('''SELECT owners.*, sessions.expires AS session_expires FROM sessions
                JOIN owners USING(owner) WHERE sid=? AND sessions.expires>? AND deleting=0
                AND (owners.expires IS NULL OR owners.expires>?)''',
                             (digest(sid), self.clock(), self.clock())).fetchone()
        return dict(row) if row else None

    def guest_turn(self, owner):
        with self.connect() as db:
            return db.execute('''UPDATE owners SET turns=turns+1 WHERE owner=? AND kind='guest'
                AND turns<100 AND expires>? AND deleting=0''', (owner, self.clock())).rowcount == 1


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def database(app):
    if not hasattr(app.state, 'accounts'):
        base = Path(memory.memories(app).base or memory.root())
        app.state.accounts = Database(base.parent / 'accounts.sqlite3')
    return app.state.accounts


def decode(value, audience):
    return jwt.decode(value, secret(), algorithms=['HS256'], audience=audience, issuer='aeye',
                      options={'require': ['exp', 'aud', 'iss']})


def same_origin(connection):
    # TLS may terminate at a reverse proxy. Compare the public Host, never trust forwarded identity headers.
    origin = connection.headers.get('origin', '')
    return origin == 'https://' + connection.headers.get('host', '')


def authenticate_session(connection):
    try:
        claims = decode(connection.cookies.get(COOKIE, ''), 'aeye-session')
        row = database(connection.app).lookup(claims['sid'])
        if row is None:
            return False
        if connection.scope['type'] == 'websocket' and not same_origin(connection):
            return False
        connection.state.account_owner = row['owner']
        connection.state.account = row
        connection.state.account_sid = claims['sid']
        return True
    except (jwt.InvalidTokenError, HTTPException, KeyError, ValueError, TypeError):
        return False


def response(value, status=200):
    return JSONResponse(value, status_code=status, headers={'Cache-Control': 'no-store'})


def set_cookie(reply, name, value, duration):
    reply.set_cookie(name, value, max_age=duration, secure=True, httponly=True, samesite='lax', path='/')


def clear_cookie(reply, name):
    reply.delete_cookie(name, secure=True, httponly=True, samesite='lax', path='/')


def check_login(request):
    if not same_origin(request):
        raise HTTPException(403, 'Use this site to sign in.')
    try:
        claims = decode(request.cookies.get(CHALLENGE, ''), 'aeye-login')
        nonce = claims['nonce']
        if not hmac.compare_digest(nonce, request.headers.get('x-aeye-csrf', '')):
            raise ValueError()
        with database(request.app).connect() as db:
            if db.execute('SELECT 1 FROM used_logins WHERE nonce=?', (digest(nonce),)).fetchone():
                raise ValueError()
        request.state.login_challenge = claims
        return nonce
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise HTTPException(403, 'Reload the sign-in page.') from None


@router.get('/auth/config')
async def config():
    nonce = secrets.token_urlsafe(32)
    try:
        value = jwt.encode(dict(nonce=nonce, exp=int(time()) + 600, aud='aeye-login', iss='aeye'),
                           secret(), algorithm='HS256')
    except HTTPException:
        return response({'enabled': False, 'google_client_id': '', 'nonce': ''})
    reply = response({'enabled': True, 'google_client_id': os.getenv('GOOGLE_CLIENT_ID', '')
                      if os.getenv('LOCAL_ONLY') != '1' else '', 'nonce': nonce})
    set_cookie(reply, CHALLENGE, value, 600)
    return reply


def signed_in(request, owner, kind):
    encoded, duration = database(request.app).issue(owner, kind, request.state.login_challenge)
    reply = response({'kind': kind, 'expires_in': duration})
    set_cookie(reply, COOKIE, encoded, duration)
    clear_cookie(reply, CHALLENGE)
    return reply


@router.post('/auth/google')
async def google(request: Request):
    nonce = check_login(request)
    audience = os.getenv('GOOGLE_CLIENT_ID', '')
    if not audience or os.getenv('LOCAL_ONLY') == '1':
        raise HTTPException(503, 'Google sign-in is unavailable. Use Guest or existing access.')
    if not request.app.state.limits.turn('login:' + request.client.host, 'login:' + request.client.host):
        raise HTTPException(429, 'Try signing in again in a minute.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            raise HTTPException(413, 'Sign-in request too large.')
    try:
        encoded = json.loads(raw)['credential']
        header = jwt.get_unverified_header(encoded)
        if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
            raise ValueError()
        key, _ = await asyncio.to_thread(google_keys.get, 'https://www.googleapis.com', header['kid'])
        if key is None:
            raise ValueError()
        claims = jwt.decode(encoded, key, algorithms=['RS256'], audience=audience,
                            issuer=['accounts.google.com', 'https://accounts.google.com'],
                            options={'require': ['exp', 'iss', 'aud', 'sub', 'nonce']})
        sub = claims['sub']
        if not isinstance(sub, str) or not 0 < len(sub) <= 255 or claims['nonce'] != nonce:
            raise ValueError()
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(401, 'Google sign-in could not be verified.') from None
    return signed_in(request, 'google:' + digest(sub), 'google')


@router.post('/auth/guest')
async def guest(request: Request):
    check_login(request)
    if authenticate_session(request):
        db = database(request.app)
        with db.connect() as conn:
            db.consume_login(conn, request.state.login_challenge)
        return response({'kind': request.state.account['kind'], 'already_signed_in': True})
    ip = 'login:' + request.client.host
    if not request.app.state.limits.turn(ip, ip):
        raise HTTPException(429, 'Try Guest again in a minute.')
    return signed_in(request, 'guest:' + secrets.token_hex(32), 'guest')


@router.post('/auth/logout')
async def logout(request: Request):
    if not same_origin(request):
        raise HTTPException(403, 'Use this site to sign out.')
    if authenticate_session(request):
        with database(request.app).connect() as db:
            db.execute('DELETE FROM sessions WHERE sid=?', (digest(request.state.account_sid),))
        await stop_sockets(request.app, request.state.account_owner)
    reply = response({'signed_out': True})
    clear_cookie(reply, COOKIE)
    return reply


def personal_owner(request):
    owner = identity(request)
    if owner == 'local':
        raise HTTPException(401, 'Sign in or choose Guest to get your own data space.')
    return owner


def stores(app, owner):
    memories = memory.memories(app)
    base = Path(memories.base or memory.root())
    found = {memory.person_id(owner): memories.store(memory.person_id(owner))}
    local = []
    if base.exists():
        for path in base.iterdir():
            if path.is_dir() and not path.is_symlink() and memory._ID.fullmatch(path.name):
                if path.name in found and not isinstance(found[path.name], SpacetimeStore):
                    continue
                # These are legacy local folders, not additional remote token bindings.
                store = memory.Store(path.name, base=base)
                if store.owner() == owner:
                    local.append(store)
    return list(found.values()) + local


def family_owned(key, owner):
    return key == owner or key.startswith(owner + ':')


def my_data(app, owner):
    owned = stores(app, owner)
    for store in owned:
        if not store.claim(owner):
            raise PermissionError('memory belongs to another caller')
    growth = growth_for_owner(app, owner)
    family = getattr(app.state, 'family_notes', None)
    result = dict(owner=owner, memory=[dict(id=s.id, settings=s.settings(), tags=s.tags(),
        records=[{k: v for k, v in r.items() if k not in ('emb', 'crops')} for r in s.records()],
        videos=s.videos(), growth=GrowthLog(memory.Store(s.id, base=s.dir.parent)).entries,
        files=[dict(name=p.name, bytes=p.stat().st_size) for p in s.dir.rglob('*') if p.is_file()]) for s in owned],
        growth=growth.entries if growth else [],
        calls=[dict(id=c.session_id, status=c.status()) for c in registry(app).calls.values() if c.owner == owner],
        family_contacts={}, family_notes=[],
        export_limits='Media and embeddings are excluded. Complete call history is not recorded.')
    if family:
        result['family_contacts'] = {k: {i: {a: b for a, b in c.items() if a != 'delivery_link'}
                                       for i, c in v.items()}
                                     for k, v in family.contacts.items() if family_owned(k, owner)}
        result['family_notes'] = [r for r in family.drafts.values() if family_owned(r['owner'], owner)]
    return result


@router.get('/api/me')
async def me(request: Request):
    owner = personal_owner(request)
    value = my_data(request.app, owner)
    account = getattr(request.state, 'account', None)
    value['account'] = {k: account[k] for k in ('kind', 'expires', 'turns')} if account else {'kind': 'legacy'}
    return response(value)


@router.get('/api/me/export')
async def export(request: Request):
    from scripts.export_data import ENTITIES, export_data
    owner = personal_owner(request)
    # No archive producer exists yet. Export only actual retained calls with known privacy provenance.
    rows = {entity: [] for entity in ENTITIES}
    for call in registry(request.app).calls.values():
        session = getattr(call, 'session', None)
        if call.owner != owner or session is None or type(getattr(session, 'private', None)) is not bool:
            continue
        rows['call'].append(dict(id=call.session_id, user_id=owner, private=session.private))
    personal = my_data(request.app, owner)
    # A private response for the person, not a teammate snapshot; schema files retain exporter exclusions.
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix='aeye-personal-') as temporary:
        output = Path(temporary) / 'export'
        if not hasattr(request.app.state, 'personal_export_key'):
            request.app.state.personal_export_key = os.getenv('SESSION_SECRET', '').encode() or secrets.token_bytes(32)
        key = hmac.new(request.app.state.personal_export_key, b'aeye-personal-export', 'sha256').digest()
        export_data(rows, output, key)
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output.iterdir()):
                archive.write(path, path.name)
            archive.writestr('my-data.json', json.dumps(personal, ensure_ascii=False))
    return Response(buffer.getvalue(), media_type='application/zip', headers={
        'Cache-Control': 'no-store', 'Content-Disposition': 'attachment; filename="a-eye-my-data.zip"'})


async def delete_owner(app, owner):
    db = database(app)
    with db.connect() as conn:
        conn.execute('INSERT OR IGNORE INTO owners(owner, kind) VALUES (?, ?)', (owner, 'legacy'))
        conn.execute('UPDATE owners SET deleting=1 WHERE owner=?', (owner,))
        conn.execute('DELETE FROM sessions WHERE owner=?', (owner,))
    # Stop all account sockets and wait for their normal cleanup before removing their stores.
    await stop_sockets(app, owner)
    memories = memory.memories(app)
    try:
        owned = stores(app, owner)
        for store in owned:
            if not store.claim(owner):
                raise PermissionError('memory belongs to another caller')
    except StorageError:
        return 1  # sessions are revoked; the deleting owner remains for the reaper
    failed = 0
    for call in list(registry(app).calls.values()):
        if call.owner != owner:
            continue
        call.set_on(False)
        call.keep = False
        call.memory_writer = None
        call.on_upload = None
        end_task = getattr(call, 'end_task', None)
        if end_task:
            await asyncio.gather(end_task, return_exceptions=True)
        async with call.lock:
            for clip in call.clips:
                if clip.video_id and call.index_id:
                    # Retain a retry manifest before discarding live state.
                    try:
                        owned[0].add_video(call.index_id, clip.video_id)
                    except StorageError:
                        return 1  # keep the live manifest for the reaper as well
            await call.aclose()
            call.ring.clear()
            call.clips.clear()
        registry(app).calls.pop(call.session_id, None)
        memories.detach(call.session_id)
    growth = getattr(app.state, 'growth_logs', {}).pop(owner, None)
    if growth:
        growth.forget()
        growth.store.revoked = True
    family = getattr(app.state, 'family_notes', None)
    if family:
        for key in list(family.contacts):
            if family_owned(key, owner):
                del family.contacts[key]
        for table in (family.drafts, family.links, family.sessions):
            for key, row in list(table.items()):
                if family_owned(row['owner'], owner):
                    del table[key]
        for key in list(family.limits):
            if family_owned(key.removeprefix('owner:'), owner):
                del family.limits[key]
    local_dirs = {s.dir for s in owned if not isinstance(s, SpacetimeStore)}
    for store in owned:
        try:
            _, remaining = await memory.delete_cloud(store, client=getattr(app.state, 'rewind_client', None))
            pending = store.videos()
            failed += remaining
            # Keep the remote retry manifest until its videos are gone. forget_all also deletes it.
            if not (pending and isinstance(store, SpacetimeStore)):
                store.forget_all()
            # A migrated local store owns its directory and any old video retry manifest.
            if store.dir.exists() and (not isinstance(store, SpacetimeStore) or store.dir not in local_dirs):
                shutil.rmtree(store.dir)
            if pending and not isinstance(store, SpacetimeStore):
                store.claim(owner)
                store._write_json('videos.json', pending)
        except StorageError:
            failed += 1
        finally:
            memories.retire(store.id)
    with app.state.limits.lock:
        app.state.limits.turns.pop(('owner', owner), None)
    if not failed:
        with db.connect() as conn:
            conn.execute('DELETE FROM owners WHERE owner=?', (owner,))
    return failed


@router.delete('/api/me')
async def delete(request: Request):
    failed = await delete_owner(request.app, personal_owner(request))
    reply = response({'deleted': not failed, 'cloud_pending': failed}, 202 if failed else 200)
    clear_cookie(reply, COOKIE)
    return reply


async def reap(app):
    if not hasattr(app.state, 'accounts') and not os.getenv('SESSION_SECRET'):
        return
    db = database(app)
    with db.connect() as conn:
        owners = conn.execute('SELECT owner FROM owners WHERE deleting=1 OR expires<=?', (db.clock(),)).fetchall()
        conn.execute('DELETE FROM sessions WHERE expires<=?', (db.clock(),))
    for row in owners:
        async with owner_lock(app, row['owner']):
            await delete_owner(app, row['owner'])


async def cleanup_loop(app):
    while True:
        try:
            await reap(app)
        except Exception:
            from server.logs import event
            event('dependency_error', level='error', dependency='account_cleanup')
        await asyncio.sleep(1)


def owner_lock(app, owner):
    if not hasattr(app.state, 'account_locks'):
        app.state.account_locks = {}
    return app.state.account_locks.setdefault(owner, asyncio.Lock())


async def stop_sockets(app, owner):
    running = list(getattr(app.state, 'account_sockets', {}).get(owner, {}).items())
    for task, _ in running:
        task.cancel()
    await asyncio.gather(*(done.wait() for _, done in running))


class AccountSockets:
    """Track signed-in socket tasks so expiry/deletion stops calls, including idle ones."""
    def __init__(self, app, application):
        self.app, self.application = app, application

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'websocket':
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        app = self.application
        if not hasattr(app.state, 'account_sockets'):
            app.state.account_sockets = {}
        owner, owned, timer = '', None, None
        done = asyncio.Event()

        async def expire(delay):
            await asyncio.sleep(max(0, delay))
            task.cancel()

        async def tracked_send(message):
            nonlocal owner, owned, timer
            if message['type'] == 'websocket.accept':
                owner = identity(connection)
                if owner != 'local':
                    owned = app.state.account_sockets.setdefault(owner, {})
                    owned[task] = done
                    account = getattr(connection.state, 'account', None)
                    if account:
                        timer = asyncio.create_task(expire(account['session_expires'] - database(app).clock()))
            await send(message)

        task = asyncio.current_task()
        try:
            await self.app(scope, receive, tracked_send)
        except asyncio.CancelledError:
            with suppress(Exception):
                await send({'type': 'websocket.close', 'code': 4401})
        finally:
            if timer:
                timer.cancel()
            if owned is not None:
                owned.pop(task, None)
                if not owned:
                    app.state.account_sockets.pop(owner, None)
            done.set()
