"""Shared tunnel authentication; credentials never enter event logs."""
import hashlib
import hmac
import os
import re
import secrets
from threading import Lock
from time import monotonic

import httpx
import jwt

from server.logs import event


PROXY_HEADERS = (
    'cf-connecting-ip', 'x-forwarded-for', 'forwarded', 'x-real-ip',
    'tailscale-user-login', 'tailscale-user-name', 'cf-ray',
)


def proxied(connection):
    # Presence alone removes local trust, even for empty or loopback-valued headers.
    return any(header in connection.headers for header in PROXY_HEADERS)


def fetch_certs(url):
    response = httpx.get(url, timeout=3, follow_redirects=False)
    response.raise_for_status()
    return response.json()


class AccessKeys:
    """One team cache, bounded refreshes even for failures and arbitrary kids."""
    def __init__(self, fetcher=fetch_certs, clock=monotonic, *, path='/cdn-cgi/access/certs', dependency='cf_access_certs'):
        self.fetcher, self.clock = fetcher, clock
        self.path, self.dependency = path, dependency
        self.issuer = None
        self.keys = {}
        self.last_fetch = float('-inf')
        self.unavailable = False
        self.lock = Lock()

    def get(self, issuer, kid):
        with self.lock:
            if issuer != self.issuer:
                self.issuer, self.keys = issuer, {}
                self.last_fetch = float('-inf')
                self.unavailable = False
            age = self.clock() - self.last_fetch
            if (kid not in self.keys or age >= 3600) and age >= 60:
                self.last_fetch = self.clock()
                try:
                    body = self.fetcher(issuer + self.path)
                    keys = {}
                    for item in body['keys']:
                        if (item.get('kty') == 'RSA' and item.get('use', 'sig') == 'sig'
                                and item.get('alg', 'RS256') == 'RS256'):
                            keys[item['kid']] = jwt.algorithms.RSAAlgorithm.from_jwk(item)
                    if not keys:
                        raise ValueError('no signing keys')
                    self.keys, self.unavailable = keys, False
                except Exception:
                    self.keys, self.unavailable = {}, True
                    event('dependency_error', level='error', dependency=self.dependency)
            if self.unavailable:
                return None, 'certs_unavailable'
            return self.keys.get(kid), 'unknown_kid'


_access_keys = AccessKeys()
_owner_key = secrets.token_bytes(32)


def mint_owner():
    """An unguessable per-call capability; knowing the shared token cannot recreate another owner."""
    nonce = secrets.token_hex(24)
    return nonce + '.' + hmac.new(_owner_key, nonce.encode(), 'sha256').hexdigest()


def token_owner(connection):
    capability = connection.headers.get('x-aeye-owner', '') or connection.query_params.get('owner', '')
    nonce, _, signature = capability.partition('.')
    if (re.fullmatch(r'[0-9a-f]{48}', nonce)
            and re.fullmatch(r'[0-9a-f]{64}', signature)
            and hmac.compare_digest(signature, hmac.new(_owner_key, nonce.encode(), 'sha256').hexdigest())):
        return 'token:' + hashlib.sha256(capability.encode()).hexdigest()
    # Legacy clients still work, but never share a memory namespace with another connection/request.
    return 'token:' + secrets.token_hex(24)


def configured():
    return any(os.environ.get(k) for k in ('A_EYE_TOKEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD'))


def warn_unconfigured():
    if not configured():
        opened = os.environ.get('A_EYE_DEMO_OPEN') == '1'
        event('auth_explicitly_open' if opened else 'auth_loopback_only', level='warning',
              reason='No credentials: all clients allowed by A_EYE_DEMO_OPEN=1' if opened
              else 'No credentials: remote and proxied clients refused; configure authentication or A_EYE_DEMO_OPEN=1')


def rejected(reason):
    event('auth_rejected', level='warning', reason=reason)
    return False


def authorized(connection):
    # Explicit legacy credentials retain their existing precedence. Cookies never supply an owner unverified.
    from server.accounts import COOKIE, authenticate_session
    if (connection.cookies.get(COOKIE) and not connection.headers.get('authorization')
            and not connection.query_params.get('token')
            and not connection.headers.get('cf-access-jwt-assertion')
            and not connection.cookies.get('CF_Authorization')):
        return authenticate_session(connection)
    expected = os.environ.get('A_EYE_TOKEN', '')
    team = os.environ.get('CF_ACCESS_TEAM_DOMAIN', '')
    audience = os.environ.get('CF_ACCESS_AUD', '')
    if not (expected or team or audience):
        peer = connection.client.host if connection.client else ''
        # Owner decision 2026-10-24: open demo mode stays available, guarded by limits and the spend cap.
        return (os.environ.get('A_EYE_DEMO_OPEN') == '1'
                or (peer in ('127.0.0.1', '::1') and not proxied(connection))
                or rejected('unconfigured_remote'))
    header = connection.headers.get('authorization', '')
    bearer = header[7:] if header.lower().startswith('bearer ') else ''
    query = connection.query_params.get('token', '')
    if expected and any(hmac.compare_digest(value.encode(), expected.encode())
                        for value in (bearer, query)):
        connection.state.token_owner = token_owner(connection)
        return True
    encoded = (connection.headers.get('cf-access-jwt-assertion')
               or connection.cookies.get('CF_Authorization'))
    if not encoded:
        return rejected('bad_token' if bearer or query else 'no_credentials')
    domain = team.removeprefix('https://').rstrip('/')
    if not audience or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', domain):
        return rejected('bad_jwt')
    issuer = 'https://' + domain
    try:
        unverified = jwt.get_unverified_header(encoded)
        kid = unverified.get('kid')
        if unverified.get('alg') != 'RS256' or not isinstance(kid, str) or not kid:
            return rejected('bad_jwt')
        key, reason = _access_keys.get(issuer, kid)
        if key is None:
            return rejected(reason)
        claims = jwt.decode(encoded, key, algorithms=['RS256'], audience=audience,
                            issuer=issuer, options={'require': ['exp', 'iss', 'aud']}, leeway=30)
        email = claims.get('email', '')
        if not isinstance(email, str):
            return rejected('bad_jwt')
    except jwt.ExpiredSignatureError:
        return rejected('expired')
    except jwt.InvalidAudienceError:
        return rejected('wrong_aud')
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        return rejected('bad_jwt')
    event('auth_access', user=hashlib.sha256(email.encode()).hexdigest()[:12])
    try:  # The verified Access email, for "remember this phone" (pairing.verified_identity). Never logged in clear.
        connection.state.access_email = email
    except AttributeError:
        pass
    return True


async def accept_socket(ws):
    if not authorized(ws):
        await ws.close(code=4401)
        return False
    await ws.accept()
    event('connect')
    return True
