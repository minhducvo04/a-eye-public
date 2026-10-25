"""Async contract-checked vision HTTP boundary. Never log image payloads."""
import os

import httpx
from jsonschema import ValidationError

from contracts import validate
from server import cloud_vision


class VisionUnavailable(Exception):
    pass


class VisionClient:
    def __init__(self, url=None, transport=None):
        self.url = url or os.environ.get('VISION_URL', 'http://127.0.0.1:8001')
        self.transport = transport

    async def call(self, operation, payload):
        validate(f'{operation}_request', payload)
        if cloud_vision.enabled():
            return await cloud_vision.call(operation, payload)
        try:
            async with httpx.AsyncClient(base_url=self.url, timeout=5,
                                         transport=self.transport) as client:
                response = await client.post(f'/{operation}', json=payload, headers=cloud_vision.request_headers())
                cloud_vision.record_usage(response)
                response.raise_for_status()
                result = response.json()
            validate(f'{operation}_response', result)
            return result
        except (httpx.HTTPError, ValueError, ValidationError):
            raise VisionUnavailable('Vision request failed') from None
