"""Cartesia TTS adapter. Only the AI's own sentences are sent. No STT here."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx

from contracts import validate

from voice.adapter import SAMPLE_RATE, VoiceAdapter

TTS_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2026-08-14"
DEFAULT_MODEL_ID = "sonic-3.6"
_CHUNK_SIZE = 4096


def tts_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Cartesia-Version": CARTESIA_VERSION,
        "Content-Type": "application/json",
    }


def tts_body(text: str, voice_id: str, model_id: str = DEFAULT_MODEL_ID) -> dict:
    return {
        "model_id": model_id,
        "transcript": text,
        "voice": voice_id,
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": SAMPLE_RATE,
        },
    }


class CartesiaAdapter(VoiceAdapter):
    def __init__(self, client: httpx.AsyncClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = client

    def _api_key(self) -> str:
        key = os.environ.get("CARTESIA_API_KEY") or ""
        if not key:
            raise RuntimeError("CARTESIA_API_KEY is not set")
        return key

    def _voice_id(self) -> str:
        picked = getattr(self, "voice", None) or ""
        voice_id = picked.split(":", 1)[1] if picked.startswith("cartesia:") else os.environ.get("CARTESIA_VOICE_ID") or ""
        if not voice_id:
            raise RuntimeError("CARTESIA_VOICE_ID is not set")
        return voice_id

    async def say(self, utterance_id: str, text: str) -> AsyncIterator[bytes]:
        validate("voice_say", {"type": "say", "utterance_id": utterance_id, "text": text})
        self._begin(utterance_id)
        headers = tts_headers(self._api_key())
        body = tts_body(text, self._voice_id())
        client = self._client or httpx.AsyncClient()
        owns = self._client is None
        finished = False
        try:
            async with client.stream("POST", TTS_URL, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(f"Cartesia TTS failed with HTTP {resp.status_code}")
                async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    if self._stopped(utterance_id):
                        break
                    if chunk:
                        yield chunk
                else:
                    finished = not self._stopped(utterance_id)
        finally:
            if owns:
                await client.aclose()
        if finished:
            self._emit_spoken(utterance_id)

    async def transcribe(self, pcm16: bytes) -> dict:
        del pcm16
        raise NotImplementedError("STT runs on the GPU host; see TODO")
