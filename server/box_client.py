"""Keep-alive GPU box HTTP boundary. Media is only held in memory."""
import base64
import os

import httpx


def box_url():
    return os.environ.get('BOX_URL', 'http://127.0.0.1:8090').rstrip('/')


class BoxClient:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=5)
        self.owns_client = client is None
        self.url = box_url()

    async def stt(self, pcm16, language='en'):
        response = await self.client.post(self.url + '/stt', json=dict(
            pcm16_b64=base64.b64encode(pcm16).decode(), language=language), timeout=5)
        response.raise_for_status()
        text = response.json()['text']
        if not isinstance(text, str):
            raise ValueError('Invalid transcript')
        return text

    async def detect(self, jpeg_b64):
        response = await self.client.post(self.url + '/detect', json=dict(
            jpeg_b64=jpeg_b64, imgsz=320, conf=0.6), timeout=5)
        response.raise_for_status()
        return response.json()['objects']

    async def aclose(self):
        if self.owns_client:
            await self.client.aclose()
