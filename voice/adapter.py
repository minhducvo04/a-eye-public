"""In-process voice adapter: TTS chunks out, transcripts and barge-in events in."""
from __future__ import annotations

import os
import time
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

from contracts import validate

SAMPLE_RATE = 16000


class VoiceAdapter:
    """Base adapter. Orchestrator calls these methods in process."""

    def __init__(self, on_event: Callable[[dict], None] | None = None) -> None:
        self.on_event = on_event
        self.last_event: dict | None = None
        self._t0: dict[str, float] = {}
        self._stop: dict[str, bool] = {}

    def _begin(self, utterance_id: str) -> None:
        self._t0[utterance_id] = time.monotonic()
        self._stop[utterance_id] = False

    def _stopped(self, utterance_id: str) -> bool:
        return bool(self._stop.get(utterance_id))

    def _elapsed_ms(self, utterance_id: str) -> int:
        t0 = self._t0.get(utterance_id)
        if t0 is None:
            return 0
        return max(0, int((time.monotonic() - t0) * 1000))

    def _emit(self, event: dict) -> dict:
        self.last_event = event
        if self.on_event is not None:
            self.on_event(event)
        return event

    def _emit_spoken(self, utterance_id: str) -> dict:
        event = {"type": "spoken", "utterance_id": utterance_id}
        validate("voice_spoken", event)
        return self._emit(event)

    async def say(self, utterance_id: str, text: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    def stop(self, utterance_id: str) -> dict:
        """Stop TTS for this utterance. Returns a voice_barge_in event."""
        self._stop[utterance_id] = True
        event = {
            "type": "barge_in",
            "utterance_id": utterance_id,
            "at_ms": self._elapsed_ms(utterance_id),
        }
        validate("voice_barge_in", event)
        return self._emit(event)

    async def transcribe(self, pcm16: bytes) -> dict:
        raise NotImplementedError


_health_cache = {}
_health_lock = threading.Lock()


def _kokoro_available():
    with _health_lock:
        return _check_kokoro()


def _check_kokoro():
    # make_adapter is synchronous; socket callers run it in a worker thread.
    import httpx
    from server.box_client import box_url
    from server.logs import event
    url = box_url()
    cached = _health_cache.get(url)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    try:
        with httpx.Client(timeout=1) as client:
            for attempt in range(2):
                try:
                    response = client.get(url + '/health')
                    break
                except httpx.RequestError as exc:
                    # Read-only readiness: retry one transient link failure before choosing silence.
                    if attempt:
                        raise
                    event('dependency_retry', dependency='tts_health', error=type(exc).__name__)
            response.raise_for_status()
            available = response.json().get('tts') is True
    except Exception as exc:
        event('dependency_error', dependency='tts_health', error=type(exc).__name__)
        # A failed probe is not a health report. Let the next call check again instead of caching
        # an unreachable link as "no TTS" for every caller over the next minute.
        return False
    _health_cache[url] = (time.monotonic(), available)
    return available


def make_adapter(voice: str | None = None, **kwargs: Any) -> VoiceAdapter:
    """Cartesia Sonic (WebSocket) when keyed, with Kokoro as its fallback; else Kokoro; else browser speech.

    voice is hello.voice: 'cartesia:<id>' asks for Cartesia, a Kokoro id asks for Kokoro.
    """
    from server.privacy import cartesia_allowed
    mode = os.environ.get('VOICE', 'auto')
    if not cartesia_allowed():
        # Natural voice switch off (or Private mode, LOCAL_ONLY=1): our own box only, never Cartesia.
        if _kokoro_available():
            from voice.kokoro import KokoroAdapter
            return KokoroAdapter(**kwargs)
        from voice.fake import FakeAdapter
        return FakeAdapter(**kwargs)  # the page falls back to its own browser voice
    wants_kokoro = bool(voice) and not voice.startswith('cartesia:')
    if os.environ.get('CARTESIA_API_KEY') and (mode == 'cartesia' or (mode == 'auto' and not wants_kokoro)):
        from voice.cartesia_ws import CartesiaWsAdapter
        backup = None
        if _kokoro_available():
            from voice.kokoro import KokoroAdapter
            backup = KokoroAdapter()
        return CartesiaWsAdapter(backup=backup, **kwargs)
    if mode == 'kokoro' or (mode == 'auto' and _kokoro_available()):
        from voice.kokoro import KokoroAdapter
        return KokoroAdapter(**kwargs)
    if mode in ('auto', 'cartesia') and os.environ.get('CARTESIA_API_KEY'):
        from voice.cartesia_ws import CartesiaWsAdapter  # a Kokoro voice was asked for but the box is down
        return CartesiaWsAdapter(**kwargs)
    from voice.fake import FakeAdapter
    return FakeAdapter(**kwargs)
