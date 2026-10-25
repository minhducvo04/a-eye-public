"""Allowlisted metadata only. Never pass application payloads to logging."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from urllib.parse import unquote_plus

_FIELDS = {'turn_id', 'model', 'first_token_ms', 'total_ms', 'input_tokens', 'output_tokens',
           'cache_read_tokens', 'cache_creation_tokens', 'web_searches', 'web_search_errors',
           'cost_usd', 'search_cost', 'search_cost_excluded', 'pack', 'tts', 'stt', 'dependency', 'to', 'ms', 'vlm_ms',
           'brain_first_token_ms', 'first_audio_ms', 'turn_ms', 'error', 'where', 'user', 'reason'}


def event(name, session=None, level='info', **fields):
    row = dict(ts=datetime.now(timezone.utc).isoformat(), level=level, event=name,
               session=hashlib.sha256(session.encode()).hexdigest()[:12] if session else None)
    row.update({k: v for k, v in fields.items() if k in _FIELDS})
    print(json.dumps(row, separators=(',', ':')), flush=True)


_TOKEN = re.compile(r'([?&])([^=&\s]+)=([^&\s"\']*)')


def _redact(value):
    def replace(match):
        if unquote_plus(match[2]).lower() in {'token', 'pair', 'join', 'owner'}:
            return match[1] + match[2] + '=[redacted]'
        return match[0]
    return _TOKEN.sub(replace, value) if isinstance(value, str) else value


class TokenFilter(logging.Filter):
    """Redact URL credentials in place; uvicorn's AccessFormatter needs the original args tuple shape."""
    def filter(self, record):
        record.msg = _redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact(arg) for key, arg in record.args.items()}
        return True


def protect_transport_logs():
    for name in ('uvicorn.access', 'uvicorn.error'):
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenFilter) for f in logger.filters):
            logger.addFilter(TokenFilter())
