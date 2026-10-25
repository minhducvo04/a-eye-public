"""Deterministic fake voice adapter. No network, silence PCM, fixed transcript."""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator

from contracts import validate

from voice.adapter import SAMPLE_RATE, VoiceAdapter

_MS_PER_CHAR = 40
_CHUNK_MS = 100
_MIN_CHUNKS = 3
_BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # PCM16 mono


def silence_chunks(text: str) -> list[bytes]:
    """A few silence chunks whose count grows with text length (deterministic)."""
    total_ms = max(_CHUNK_MS, len(text) * _MS_PER_CHAR)
    n_chunks = max(_MIN_CHUNKS, total_ms // _CHUNK_MS)
    chunk = bytes(_BYTES_PER_MS * _CHUNK_MS)
    return [chunk] * n_chunks


class FakeAdapter(VoiceAdapter):
    async def say(self, utterance_id: str, text: str) -> AsyncIterator[bytes]:
        validate("voice_say", {"type": "say", "utterance_id": utterance_id, "text": text})
        self._begin(utterance_id)
        for chunk in silence_chunks(text):
            if self._stopped(utterance_id):
                return
            yield chunk
            await asyncio.sleep(0)
        if not self._stopped(utterance_id):
            self._emit_spoken(utterance_id)

    async def transcribe(self, pcm16: bytes) -> dict:
        del pcm16
        event = {
            "type": "transcript",
            "text": os.environ.get("FAKE_TRANSCRIPT") or "which way does it go",
            "final": True,
            "ts_ms": int(time.time() * 1000),
        }
        validate("voice_transcript", event)
        return self._emit(event)
