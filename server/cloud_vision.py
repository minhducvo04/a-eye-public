"""Cloud routes execute in the call context so consent and byte meters are shared."""
import asyncio
from contextlib import nullcontext
import os
from time import perf_counter

import httpx

from contracts import validate
from server import cloud_meter, privacy
from vision.gemini import GeminiBackend, unsure
from vision.ollama import OllamaBackend


def enabled():
    return os.environ.get('VISION_BACKEND') in {'gemini', 'cloud_fallback'}


async def call(operation, payload, gate=None):
    started = perf_counter()
    result = None
    if os.environ.get('VISION_BACKEND') == 'cloud_fallback':
        # Release the home GPU reservation before starting a cloud request.
        async with gate.hold() if gate is not None else nullcontext():
            local = OllamaBackend()
            local.timeout = 4
            local.ground_fallback = False
            try:
                result = await asyncio.to_thread(getattr(local, operation), payload, 'ok')
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            except (httpx.HTTPError, ValueError, OSError):
                result = unsure(operation)
            finally:
                local.client.close()
    if result is None:
        result = await asyncio.to_thread(getattr(GeminiBackend(), operation), payload, 'ok')
    result['ms'] = (perf_counter() - started) * 1000
    validate(operation + '_response', result)
    return result


def request_headers():
    return {'X-Aeye-Private': '1' if privacy.local_only() else '0',
            'X-Aeye-Google': '1' if privacy.google_allowed() else '0'}


def record_usage(response):
    # A separately running vision service reports its own outgoing attempts.
    meter = cloud_meter.CURRENT.get()
    if meter is not None:
        try:
            calls = int(response.headers.get('X-Aeye-Cloud-Calls', '0'))
            size = int(response.headers.get('X-Aeye-Cloud-Bytes', '0'))
        except ValueError:
            return
        if calls >= 0 and size >= 0:
            meter.add(calls=calls, size=size)
            cloud_meter.charge(calls * cloud_meter.request_cost('https://vision-provider.invalid'))
