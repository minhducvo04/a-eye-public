"""One short hosted-brain probe through the openai_compat client. Prints host and latency."""
import asyncio
import os
import sys
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.brain_openai_compat import OpenAICompatBrain, configured


def missing():
    return [name for name in ('OPENAI_COMPAT_BASE_URL', 'OPENAI_COMPAT_API_KEY')
            if not os.environ.get(name, '').strip()]


def host():
    parsed = urlparse(os.environ['OPENAI_COMPAT_BASE_URL'])
    return parsed.netloc or os.environ['OPENAI_COMPAT_BASE_URL']


async def probe(httpx_client=None):
    started = perf_counter()
    first = None
    brain = OpenAICompatBrain(httpx_client=httpx_client)
    async for delta in brain.stream('Reply with one word.', 'Say ready.', max_tokens=8):
        if first is None and isinstance(delta, str) and delta:
            first = (perf_counter() - started) * 1000
    total = (perf_counter() - started) * 1000
    model = os.environ.get('OPENAI_COMPAT_TALKER_MODEL', '').strip()
    first_s = 'none' if first is None else f'{first:.0f}'
    print(f'host={host()} model={model} first_token_ms={first_s} total_ms={total:.0f}')
    return 0 if first is not None else 1


def main(httpx_client=None):
    if missing() or not configured():
        print('not configured: set OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY and OPENAI_COMPAT_TALKER_MODEL')
        return 2
    try:
        return asyncio.run(probe(httpx_client=httpx_client))
    except Exception:
        print('hosted brain request failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
