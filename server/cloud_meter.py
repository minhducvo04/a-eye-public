"""Per-call outgoing request evidence; no payloads or credentials are retained.

All HTTPX sends are metered centrally (including SDK retries and redirects), so adding
an HTTP provider cannot silently omit accounting. Conservatively includes other public
HTTP requests made by the call. Bytes are request bodies / WebSocket message payloads,
not responses, headers, TLS or IP overhead. Failed attempts still count as calls.
"""
from contextvars import ContextVar
from contextlib import closing
from dataclasses import dataclass, field
from functools import wraps
import ipaddress
import os
import sqlite3
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import ClientConnection

CURRENT = ContextVar('cloud_meter', default=None)
LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '100.64.0.0/10', '169.254.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10'))


class CloudCapReached(RuntimeError):
    pass


CAP_LINE = "Today's cloud budget is used up. I'll keep helping in local mode."


class DailyBudget:
    """Only UTC dates and dollar estimates persist; SQLite serializes concurrent charges."""
    def __init__(self, path=None, day=None):
        self.path = path
        self.day = day or (lambda: datetime.now(timezone.utc).date().isoformat())

    def _update(self, amount=0, admit=False):
        path = self.path or os.getenv('CLOUD_BUDGET_PATH', f'/tmp/aeye-cloud-budget-{os.getuid()}.sqlite3')
        cap = max(0, float(os.getenv('DAILY_CLOUD_CAP_USD', '5')))
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, usd REAL, stopped INTEGER)')
            db.execute('BEGIN IMMEDIATE')
            day = self.day()
            row = db.execute('SELECT usd, stopped FROM spend WHERE day=?', (day,)).fetchone()
            total, stopped = row or (0, 0)
            if admit and (stopped or total >= cap):
                raise CloudCapReached(CAP_LINE)
            total += max(0, amount)
            stopped = int(bool(stopped or total >= cap))
            if row != (total, stopped):
                db.execute('INSERT OR REPLACE INTO spend VALUES (?, ?, ?)', (day, total, stopped))
            return bool(stopped)

    def exhausted(self):
        return self._update()

    def charge(self, amount, admit=False):
        return self._update(amount, admit)


BUDGET = DailyBudget()


def charge(amount, admit=False):
    BUDGET.charge(amount, admit)
    meter = CURRENT.get()
    if meter is not None:
        with meter.lock:
            meter.estimated_usd += amount


def request_cost(url):
    name = 'TWELVELABS_REQUEST_USD' if 'twelvelabs' in (urlsplit(str(url)).hostname or '') else 'CLOUD_REQUEST_USD'
    return max(0, float(os.getenv(name, '0.10' if name.startswith('TWELVE') else '0.01')))


def record_tokens(counts):
    # Budget weights, not a provider invoice; cache creation and searches are included.
    inp = counts.get('input_tokens', 0) + counts.get('cache_creation_tokens', 0) * 1.25
    out = counts.get('output_tokens', 0)
    read = counts.get('cache_read_tokens', 0)
    charge((inp * float(os.getenv('CLOUD_INPUT_USD_PER_MILLION', '5'))
            + out * float(os.getenv('CLOUD_OUTPUT_USD_PER_MILLION', '25'))
            + read * float(os.getenv('CLOUD_CACHE_USD_PER_MILLION', '0.5'))) / 1_000_000
           + counts.get('web_searches', 0) * float(os.getenv('CLOUD_SEARCH_USD', '0.01')))


def is_local(url):
    host = (urlsplit(str(url)).hostname or '').lower().rstrip('.')
    # These are explicitly operator-configured home services, not hosted-model URLs.
    home = {(urlsplit(os.environ.get(key, '')).hostname or '').lower().rstrip('.')
            for key in ('OLLAMA_URL', 'BOX_URL', 'VISION_URL', 'ROUTER_OLLAMA_URL')}
    if host and (host in home or host == 'localhost' or host.endswith(('.localhost', '.local', '.ts.net'))):
        return True
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in LOCAL_NETWORKS)
    except ValueError:
        return False  # unknown destinations are never silently treated as local


@dataclass
class Meter:
    estimated_usd: float = 0
    calls: int = 0
    bytes: int = 0
    pictures_sent: int = 0
    lock: Lock = field(default_factory=Lock, repr=False)

    def add(self, calls=0, size=0, pictures=0):
        with self.lock:
            self.calls += calls
            self.bytes += size
            self.pictures_sent += pictures

    def snapshot(self):
        with self.lock:
            return dict(cloud_ai_calls=self.calls, bytes_to_cloud=self.bytes)


def start():
    meter = Meter()
    CURRENT.set(meter)
    return meter


class SyncBody(httpx.SyncByteStream):
    def __init__(self, stream, meter):
        self.stream, self.meter = stream, meter

    def __iter__(self):
        for chunk in self.stream:
            self.meter.add(size=len(chunk))
            yield chunk

    def close(self):
        self.stream.close()


class AsyncBody(httpx.AsyncByteStream):
    def __init__(self, stream, meter):
        self.stream, self.meter = stream, meter

    async def __aiter__(self):
        async for chunk in self.stream:
            self.meter.add(size=len(chunk))
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


def _install(library):
    # One shared send boundary, below SDK retries and HTTP redirects, above transports.
    sync_body, async_body = SyncBody, AsyncBody
    if library is not httpx:
        sync_body = type('SyncBody2', (SyncBody, library.SyncByteStream), {})
        async_body = type('AsyncBody2', (AsyncBody, library.AsyncByteStream), {})
    for cls, body in ((library.Client, sync_body), (library.AsyncClient, async_body)):
        original = cls._send_single_request
        if getattr(original, '_cloud_meter', False):
            continue
        def wrap(original, body):
            def prepare(request):
                # Redirects may reuse a wrapped body, including a redirect back home.
                while isinstance(request.stream, (SyncBody, AsyncBody)):
                    request.stream = request.stream.stream
                meter = CURRENT.get()
                if not is_local(request.url):
                    # Deletion must remain available for Rewind privacy cleanup after cutoff.
                    auth_keys = (request.url.path == '/cdn-cgi/access/certs' or
                                 (request.url.host == 'www.googleapis.com' and request.url.path == '/oauth2/v3/certs'))
                    if request.method != 'DELETE' and not auth_keys:
                        charge(request_cost(request.url), admit=True)
                if meter is not None and not is_local(request.url):
                    meter.add(calls=1)
                    try:
                        meter.add(size=len(request.content))
                        return
                    except library.RequestNotRead:
                        pass
                    request.stream = body(request.stream, meter)
            if issubclass(body, AsyncBody):
                @wraps(original)
                async def send(self, request):
                    prepare(request)
                    return await original(self, request)
            else:
                @wraps(original)
                def send(self, request):
                    prepare(request)
                    return original(self, request)
            send._cloud_meter = True
            return send
        cls._send_single_request = wrap(original, body)


def _install_ws():
    original = ClientConnection.send
    if getattr(original, '_cloud_meter', False):
        return

    @wraps(original)
    async def send(self, message, *args, **kwargs):
        meter = CURRENT.get()
        uri = self.protocol.uri
        remote = not is_local(f'wss://[{uri.host}]:{uri.port}' if ':' in uri.host else f'wss://{uri.host}:{uri.port}')
        if remote:
            if uri.host == 'generativelanguage.googleapis.com':
                if BUDGET.exhausted():
                    raise CloudCapReached(CAP_LINE)
            else:
                charge(request_cost(f'https://{uri.host}'), admit=True)
        if meter is not None and remote:
            meter.add(calls=1)
            def count(chunk):
                meter.add(size=len(chunk.encode('utf-8') if isinstance(chunk, str) else chunk))
                return chunk
            if isinstance(message, (str, bytes, bytearray, memoryview)):
                count(message)
            elif hasattr(message, '__aiter__'):
                async def chunks(source):
                    async for chunk in source:
                        yield count(chunk)
                message = chunks(message)
            else:
                message = map(count, message)
        return await original(self, message, *args, **kwargs)
    send._cloud_meter = True
    ClientConnection.send = send


_install(httpx)
try:
    import httpx2  # newer Anthropic SDK; older installations still use HTTPX
except ImportError:
    pass
else:
    _install(httpx2)
_install_ws()
