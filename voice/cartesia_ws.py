"""Cartesia Sonic over the TTS WebSocket: one socket per call, one context per turn, clause-by-clause pushes.

Every clause of a turn goes into the same context_id with continue=true, so prosody flows across clauses;
flush=true marks where this clause's audio ends (flush_done). Barge-in cancels the context. Only the AI's
own sentences are sent. The key stays server-side, in a header, never in a URL or the browser.
If Cartesia fails before any audio, the clause falls back to the backup adapter (Kokoro on the box).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import websockets

from contracts import validate
from voice.adapter import SAMPLE_RATE, VoiceAdapter

WS_URL = 'wss://api.cartesia.ai/tts/websocket'
VERSION = '2025-04-16'
MODEL_ID = os.environ.get('CARTESIA_MODEL_ID', 'sonic-3.6')
# Conversational English voices from the Cartesia library (picked for a casual helper tone).
VOICES = (
    ('f786b574-daa5-4673-aa0c-cbe3e8534c02', 'Katie, friendly'),
    ('e8e5fffb-252c-436d-b842-8879b84445b6', 'Cathy, coworker'),
    ('30894953-bcce-41fe-892c-15ce19c843ff', 'Parker, supportive pal'),
)
CHUNK_TIMEOUT_S = 5


def default_voice():
    return os.environ.get('CARTESIA_VOICE_ID') or VOICES[0][0]


def request(context_id, text, voice_id):
    return dict(model_id=MODEL_ID, transcript=text, voice=dict(mode='id', id=voice_id), language='en',
                output_format=dict(container='raw', encoding='pcm_s16le', sample_rate=SAMPLE_RATE),
                context_id=context_id, max_buffer_delay_ms=0, flush=True, **{'continue': True})


class CartesiaWsAdapter(VoiceAdapter):
    def __init__(self, backup=None, connect=None, **kwargs):
        super().__init__(**kwargs)
        self.voice = None   # 'cartesia:<id>' from hello.voice
        self.turn = None    # set by ws2 before each say; clauses of one turn share a context
        self.backup = backup
        self._connect = connect or self._open
        self._ws = None
        self._reader = None
        self._waiters = {}
        self._contexts = {}
        self._lock = asyncio.Lock()

    async def _open(self):
        key = os.environ.get('CARTESIA_API_KEY', '')
        return await websockets.connect(f'{WS_URL}?cartesia_version={VERSION}', max_size=None,
                                        additional_headers={'X-API-Key': key, 'Cartesia-Version': VERSION})

    async def _socket(self):
        async with self._lock:
            if self._ws is None or self._reader is None or self._reader.done():
                self._ws = await self._connect()
                self._reader = asyncio.create_task(self._read(self._ws))
            return self._ws

    async def _read(self, ws):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                queue = self._waiters.get(msg.get('context_id'))
                if queue is not None:
                    queue.put_nowait(msg)
        except Exception:
            pass
        finally:
            for queue in self._waiters.values():
                queue.put_nowait(dict(type='error', message='socket closed'))

    def _voice_id(self):
        picked = self.voice or ''
        return picked.split(':', 1)[1] if picked.startswith('cartesia:') else default_voice()

    def stop(self, utterance_id):
        context = self._contexts.get(utterance_id)
        if context and self._ws is not None:
            try:
                asyncio.get_running_loop().create_task(
                    self._ws.send(json.dumps(dict(context_id=context, cancel=True))))
            except RuntimeError:
                pass
        queue = self._waiters.get(context)
        if queue is not None:
            queue.put_nowait(dict(type='cancelled'))
        return super().stop(utterance_id)

    async def say(self, utterance_id, text):
        validate('voice_say', dict(type='say', utterance_id=utterance_id, text=text))
        self._begin(utterance_id)
        context = (self.turn or utterance_id)[:64]
        self._contexts[utterance_id] = context
        queue = asyncio.Queue()
        self._waiters[context] = queue
        sent_audio = False
        try:
            ws = await self._socket()
            await ws.send(json.dumps(request(context, text, self._voice_id())))
            while True:
                msg = await asyncio.wait_for(queue.get(), CHUNK_TIMEOUT_S)
                kind = msg.get('type')
                if kind == 'chunk' and msg.get('data'):
                    if self._stopped(utterance_id):
                        return
                    sent_audio = True
                    yield base64.b64decode(msg['data'])
                elif kind in ('flush_done', 'done', 'cancelled'):
                    break
                elif kind == 'error':
                    raise RuntimeError('cartesia error')
            if not self._stopped(utterance_id):
                self._emit_spoken(utterance_id)
        except (Exception, asyncio.TimeoutError):
            if sent_audio or self.backup is None or self._stopped(utterance_id):
                raise
            async for chunk in self.backup.say(utterance_id, text):
                yield chunk
        finally:
            if self._waiters.get(context) is queue:
                self._waiters.pop(context, None)
            self._contexts.pop(utterance_id, None)
            self._t0.pop(utterance_id, None)
            self._stop.pop(utterance_id, None)

    async def warm(self):
        """Open the socket at hello so the first clause does not pay the TLS handshake."""
        try:
            await self._socket()
        except Exception:
            pass

    async def aclose(self):
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self.backup is not None and hasattr(self.backup, 'aclose'):
            await self.backup.aclose()
