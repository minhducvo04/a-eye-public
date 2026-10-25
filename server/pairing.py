"""Process-local, single-use camera pairing. No media or credentials are logged."""
import asyncio
import hashlib
import hmac
import json
import secrets
from time import monotonic

from anyio import CancelScope
from fastapi import WebSocketDisconnect
from jsonschema import ValidationError
from contracts import validate
from server.agent import message
from server.auth import proxied
from server.logs import event


def pairing_address(ws):
    """Proxy visitor addresses partition rate limits only; they never authorize a join.
    Run Uvicorn with --no-proxy-headers so client.host remains the TCP peer."""
    peer = ws.client.host if ws.client else ''
    if peer in ('127.0.0.1', '::1') and proxied(ws):
        return (ws.headers.get('cf-connecting-ip', '').strip()
                or ws.headers.get('x-forwarded-for', '').split(',', 1)[0].strip() or peer)
    return peer


class Registry:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.codes = {}
        self.retired = {}
        self.misses = {}
        self.address_misses = {}
        # "Remember this phone": the latest live call per verified Access identity (latest wins).
        self.owners = {}

    def drop(self, call):
        for ident, owned in list(self.owners.items()):
            if owned is call:
                del self.owners[ident]
        for code, entry in list(self.codes.items()):
            if entry[0] is call:
                self.retired[code] = entry[1]
                del self.codes[code]
                self.misses.pop(code, None)

    def issue(self, call):
        self.drop(call)
        # Expired entries remain until the next issue, allowing an explicit expired response.
        self.codes = {c: e for c, e in self.codes.items() if e[1] > self.clock()}
        self.retired = {c: expiry for c, expiry in self.retired.items() if expiry > self.clock()}
        self.misses = {c: n for c, n in self.misses.items() if c in self.codes}
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        while True:
            code = ''.join(secrets.choice(alphabet) for _ in range(8))
            if code not in self.codes and code not in self.retired:
                break
        secret = secrets.token_urlsafe(16)
        self.codes[code] = (call, self.clock() + 300, secret)
        event('pair_request', call.session.session_id)
        return message('pair', code=code, expires_in_s=300, path=f'/agent.html?pair={code}&join={secret}')

    def take(self, code, address='', secret=None):
        """Five failed joins per peer per minute; five misses retire a live code across all peers.
        A secret is required on the unauthenticated join path; authenticated callers may enter the code."""
        now = self.clock()
        self.address_misses = {a: e for a, e in self.address_misses.items() if e[1] > now}
        count, until = self.address_misses.get(address, (0, now + 60))
        if count >= 5 or (address not in self.address_misses and len(self.address_misses) >= 4096):
            return None, 'rejected'
        entry = self.codes.get(code)
        if (entry is None or entry[1] <= now or entry[0].closed or entry[0].camera is not None
                or (secret is not None and not hmac.compare_digest(secret.encode(), entry[2].encode()))):
            self.address_misses[address] = (count + 1, until)
            if entry:
                self.misses[code] = self.misses.get(code, 0) + 1
                if self.misses[code] >= 5 or entry[1] <= now:
                    self.retired[code] = entry[1]
                    del self.codes[code]
                    self.misses.pop(code, None)
            return None, 'expired' if entry and entry[1] <= now else 'rejected'
        call, expiry, _ = self.codes.pop(code)
        self.retired[code] = expiry
        self.misses.pop(code, None)
        return call, 'joined'


    def claim(self, call, ident):
        if ident:
            self.owners[ident] = call

    def take_remembered(self, ws):
        """A camera join without a code: only into the latest live call of the same verified Access identity.
        Returns (call, state, detail, retry_s); retry_s tells a remembered phone to wait and try again."""
        ident = verified_identity(ws)
        if not ident:
            event('pair_rejected', reason='no_access')
            return None, 'rejected', 'Sign in with your own account to use this phone without a code, or use a code.', None
        call = self.owners.get(ident)
        if call is None or call.closed:
            return None, 'rejected', 'Waiting for your next call.', 3
        if call.kicked:
            return None, 'rejected', 'Your call disconnected this phone. It joins your next call.', 3
        if call.camera is not None:
            return None, 'rejected', 'Your call already has a phone.', 3
        return call, 'joined', '', None


def verified_identity(ws):
    """'access:<hash>' for an Access email that server.auth verified on this socket, else ''. A shared token, an
    open server or an unverified cookie never counts."""
    email = getattr(getattr(ws, 'state', None), 'access_email', '')
    if not isinstance(email, str) or not email:
        return ''
    return 'access:' + hashlib.sha256(email.lower().encode()).hexdigest()[:16]


registry = Registry()


class Call:
    def __init__(self, session, send, switch, ident=''):
        self.session, self.send, self.switch = session, send, switch
        self.limit_owner = ''
        self.local_on = session.camera
        self.camera = None
        self.closed = False
        self.kicked = False  # the call disconnected its phone: no remembered rejoin into this call
        registry.claim(self, ident)
        # Phone mic: audio_in and barge_in from the camera socket, read by the call's own message loop.
        self.inbox = asyncio.Queue()

    async def status(self, state, source='camera'):
        fields = dict(state=state, camera_on=self.session.camera, source=source)
        if not self.closed:
            await self.send(message('pair_status', role='call', **fields))
        if self.camera:
            await self.camera.send_json(message('pair_status', role='camera', **fields))

    async def kick(self):
        """The call disconnects its phone. A remembered phone waits for the next call instead of rejoining."""
        self.kicked = True
        camera = self.camera
        if camera:
            event('pair_kicked', self.session.session_id)
            try:
                await camera.send_json(message('pair_status', state='left', role='camera',
                                               detail='Your call disconnected this phone.', retry_s=3))
                await camera.close()
            except (RuntimeError, WebSocketDisconnect):
                pass

    async def close(self):
        self.closed = True
        registry.drop(self)
        camera, self.camera = self.camera, None
        if camera:
            event('pair_left', self.session.session_id)
            try:
                await camera.send_json(message('pair_status', state='left', role='camera', detail='Call ended.', retry_s=3))
                await camera.close()
            except (RuntimeError, WebSocketDisconnect):
                pass


async def join_socket(ws):
    """A QR capability can enter only this one camera handler, never the call loop or HTTP API."""
    from server.auth import authorized, configured
    if not configured() and not authorized(ws):
        await ws.close(code=4401)
        return
    code = ws.query_params.get('pair', '')
    expiry = registry.codes.get(code, (None, 0))[1]
    call, _ = registry.take(code, address=pairing_address(ws),
                            secret=ws.query_params.get('join', ''))
    if call is None:
        await ws.close(code=4401)
        return
    call.camera = ws
    # Consume atomically before accepting/awaiting: duplicate sockets cannot claim this call.
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 10)
        validate('client_message_v2', hello)
        if (hello['type'] != 'hello' or hello.get('role') != 'camera' or hello.get('pair_code') != code
                or registry.clock() >= expiry or call.closed):
            raise ValueError()
    except (ValueError, ValidationError, KeyError, TimeoutError, WebSocketDisconnect):
        if call.camera is ws:
            call.camera = None
        await ws.close(code=4401)
        return
    await camera_socket(ws, hello, claimed=call)


async def camera_socket(ws, hello, claimed=None):
    if claimed is not None:
        call, state, detail, retry = claimed, 'joined', '', None
    elif 'pair_code' in hello:
        call, state = registry.take(hello['pair_code'], address=pairing_address(ws))
        detail, retry = ('Code expired.' if state == 'expired' else 'Code unavailable. Request a new code.'), None
    else:
        call, state, detail, retry = registry.take_remembered(ws)
    if call is None:
        event('pair_rejected')
        await ws.send_json(message('pair_status', state=state, role='camera', detail=detail,
                                   **({'retry_s': retry} if retry else {})))
        await ws.close(code=4404)
        return
    ws.state.limit_owner = call.limit_owner
    call.camera = ws  # Reserve before the first await: only one camera can join.
    event('pair_joined', call.session.session_id)
    try:
        await call.switch(hello.get('camera', True), reset=True)
        if call.closed:
            return
        await call.status('joined')
        while True:
            try:
                incoming = await ws.receive_json()
                validate('client_message_v2', incoming)
                if (incoming['type'] not in ('frame', 'camera', 'audio_in', 'barge_in')
                        or incoming['session_id'] != hello['session_id']):
                    raise ValueError()
            except (json.JSONDecodeError, ValidationError, KeyError, ValueError):
                await ws.send_json(message('error', code='bad_message',
                                           message='Camera sockets accept only frame, camera, audio_in and barge_in.'))
                continue
            if call.closed:
                break
            if incoming['type'] in ('audio_in', 'barge_in'):
                await call.inbox.put(dict(incoming, session_id=call.session.session_id))
            elif incoming['type'] == 'camera':
                await call.switch(incoming['on'])
                await call.status('camera')
            elif call.session.camera:
                frame = call.session.frame
                if frame is None or incoming['seq'] > frame['seq']:
                    call.session.frame = dict(incoming, session_id=call.session.session_id)
                    call.session.frame_time = monotonic()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if call.camera is ws:
            call.camera = None
            event('pair_left', call.session.session_id)
            if not call.closed:
                with CancelScope(shield=True):
                    await call.switch(call.local_on, reset=True)
                    await call.status('left', source='call')
