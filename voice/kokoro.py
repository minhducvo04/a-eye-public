"""Cancellable PCM16/16 kHz mono speech from the local GPU box."""
import asyncio
import os

import httpx

from contracts import validate
from server.box_client import BoxClient
from server.logs import event
from voice.adapter import VoiceAdapter


# Most human of the box voices in a blind proxy listen test (docs/voice-options.md, voice/voice_pick.py).
KOKORO_VOICES = ('af_kore', 'af_sarah', 'af_heart', 'bf_emma', 'am_fenrir', 'am_puck', 'am_michael', 'bm_george', 'af_bella')


def default_voice():
    return os.environ.get('KOKORO_VOICE', 'af_kore')


class KokoroAdapter(VoiceAdapter):
    def __init__(self, client=None, **kwargs):
        super().__init__(**kwargs)
        self.box = BoxClient(client)
        self.tasks = {}
        self.voice = None  # per session, from hello.voice

    def stop(self, utterance_id):
        task = self.tasks.get(utterance_id)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        return super().stop(utterance_id)

    async def say(self, utterance_id, text):
        validate('voice_say', dict(type='say', utterance_id=utterance_id, text=text))
        self._begin(utterance_id)
        self.tasks[utterance_id] = asyncio.current_task()
        try:
            for attempt in range(2):
                try:
                    async with self.box.client.stream('POST', self.box.url + '/tts', json=dict(
                            text=text, voice=self.voice if self.voice in KOKORO_VOICES else default_voice()), timeout=5) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes(2048):
                            if self._stopped(utterance_id):
                                return
                            if chunk:
                                yield chunk
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    # No request reached Kokoro: one new connection is safe. Never replay a failed read
                    # or partial stream, which could duplicate speech or leave synthesis running.
                    if attempt:
                        raise
                    event('dependency_retry', dependency='tts', error=type(exc).__name__)
            if not self._stopped(utterance_id):
                self._emit_spoken(utterance_id)
        finally:
            self.tasks.pop(utterance_id, None)
            self._t0.pop(utterance_id, None)
            self._stop.pop(utterance_id, None)

    async def aclose(self):
        await self.box.aclose()
