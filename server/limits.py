"""Small single-worker admission limits. Keys/content never enter logs."""
from collections import defaultdict, deque
import json
import os
from threading import Lock
from time import monotonic

from starlette.requests import HTTPConnection
from server.logs import event
from server.pairing import pairing_address
from server.rewind import identity

LIMIT_LINE = 'Please pause a moment and try again; the demo is busy.'


def owner_key(connection):
    owner = getattr(connection.state, 'limit_owner', '') or identity(connection)
    return 'ip:' + pairing_address(connection) if owner == 'local' else owner


class Limits:
    def __init__(self, clock=monotonic, guest_turn=None):
        self.clock = clock
        self.guest_turn = guest_turn
        self.lock = Lock()
        self.sockets = defaultdict(int)
        self.turns = {}

    def acquire(self, ip):
        with self.lock:
            if (sum(self.sockets.values()) >= int(os.getenv('MAX_OPEN_SOCKETS', '20'))
                    or self.sockets.get(ip, 0) >= int(os.getenv('CALLS_PER_IP', '3'))):
                return False
            self.sockets[ip] += 1
            return True

    def release(self, ip):
        with self.lock:
            self.sockets[ip] -= 1
            if self.sockets[ip] <= 0:
                del self.sockets[ip]

    def turn(self, ip, owner):
        with self.lock:
            now = self.clock()
            for key, times in list(self.turns.items()):
                while times and times[0] <= now - 60:
                    times.popleft()
                if not times:
                    del self.turns[key]
            keys = [('ip', ip), ('owner', owner)]
            limit = int(os.getenv('TURNS_PER_MINUTE', '30'))
            if owner.startswith('guest:'):
                limit = min(limit, 10)
            if len(self.turns) >= 8192 or any(len(self.turns.get(k, ())) >= limit for k in keys):
                return False
            if owner.startswith('guest:') and self.guest_turn and not self.guest_turn(owner):
                return False
            for key in keys:
                self.turns.setdefault(key, deque()).append(now)
            return True


class SocketLimits:
    """Covers v1, v2 and the unauthenticated QR camera path, including early exits."""
    def __init__(self, app, limits):
        self.app, self.limits = app, limits

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'websocket':
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        ip = pairing_address(connection)
        version = 2 if scope['path'] == '/ws2' else 1

        async def refuse():
            event('limit_rejected', reason='capacity')
            await send({'type': 'websocket.send', 'text': json.dumps(dict(
                v=version, type='error', code='internal', message=LIMIT_LINE))})

        if not self.limits.acquire(ip):
            await receive()  # websocket.connect
            await send({'type': 'websocket.accept'})
            await refuse()
            await send({'type': 'websocket.close', 'code': 4429})
            return
        audio_active = audio_allowed = False

        async def limited_receive():
            nonlocal audio_active, audio_allowed
            while True:
                incoming = await receive()
                if incoming['type'] != 'websocket.receive':
                    return incoming
                try:
                    body = json.loads(incoming.get('text') or incoming.get('bytes') or '')
                except (ValueError, TypeError):
                    return incoming
                if not isinstance(body, dict):
                    return incoming
                kind = body.get('type')
                # One reservation at the start of PCM, before any Live upload, through its final marker.
                new_turn = kind == 'user_text' and (version == 1 or body.get('final') is True)
                new_turn |= kind in ('hello', 'market', 'photo', 'confirm')
                new_turn |= kind == 'audio_in' and not audio_active
                owner = owner_key(connection)
                allowed = not new_turn or self.limits.turn(ip, owner)
                if kind == 'audio_in':
                    if not audio_active:
                        audio_allowed = allowed
                    allowed = audio_allowed
                    audio_active = not body.get('final', False)
                if allowed:
                    return incoming
                if new_turn:
                    await refuse()
        try:
            await self.app(scope, limited_receive, send)
        finally:
            self.limits.release(ip)
