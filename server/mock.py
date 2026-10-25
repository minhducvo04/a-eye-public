"""Offline phone dependencies. Synthetic PCM and frames stay in memory."""
import json
import math
import struct
from time import monotonic

from contracts import validate
from server.llm import LLMResult
from vision.fake import FakeBackend
from voice.adapter import VoiceAdapter


class MockVision:
    def __init__(self):
        self.backend = FakeBackend()

    async def call(self, operation, payload):
        validate(f'{operation}_request', payload)
        started = monotonic()
        result = getattr(self.backend, operation)(payload, 'ok')
        result['ms'] = (monotonic() - started) * 1000
        validate(f'{operation}_response', result)
        return result


class MockBrain:
    async def stream(self, system, user, **kwargs):
        objects = json.loads(user)['objects']
        draw = ([dict(kind=kind, target=objects[0]['id']) for kind in ('box', 'arrow')]
                if objects else [])
        if json.loads(user)['caption'] == 'camera is off':
            yield '{"agent":"helper","draw":[]}\nSAY: The camera is off. How can I help?'
            return
        yield json.dumps(dict(agent='helper', draw=draw)) + '\nSAY: Here is the highlighted object. '
        yield 'Follow the arrow.'

    def complete(self, **kwargs):
        return LLMResult('Here is the highlighted object. Follow the arrow.', 'mock', 'scripted', 0)


class MockVoice(VoiceAdapter):
    async def say(self, utterance_id, text):
        self._begin(utterance_id)
        yield b''.join(struct.pack('<h', int(2000 * math.sin(2 * math.pi * 440 * n / 16000)))
                       for n in range(1600))
        self._emit_spoken(utterance_id)


class MockBox:
    async def stt(self, pcm, language='en'):
        return 'Show me the first object.'

    async def detect(self, jpeg_b64):
        return []

    async def aclose(self):
        pass


def install(app):
    app.state.vision = app.state.vision_v2 = MockVision()
    app.state.llm = app.state.llm_v2 = MockBrain()
    app.state.voice_factory_v2 = MockVoice
    app.state.box_v2 = MockBox()


class FirstSceneBarrier:
    """Let the first fake scene finish before consuming the next phone message.

    Live scene scheduling remains owned by ws2. Mock startup is deterministic
    even when a phone sends hello, frame and final text without waiting for track.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'websocket' or scope['path'] != '/ws2':
            return await self.app(scope, receive, send)
        import asyncio
        from jsonschema import ValidationError
        ready = asyncio.Event()
        pending = False
        initialized = False

        async def read():
            nonlocal pending, initialized
            if pending:
                try:
                    await asyncio.wait_for(ready.wait(), .5)
                except TimeoutError:
                    pass
                pending = False
            incoming = await receive()
            try:
                body = json.loads(incoming.get('text', ''))
                validate('client_message_v2', body)
                if body['type'] == 'hello':
                    initialized = False
                    ready.clear()
                elif body['type'] == 'frame' and not initialized:
                    initialized = pending = True
            except (ValueError, ValidationError, KeyError):
                pass
            return incoming

        async def write(outgoing):
            if outgoing['type'] == 'websocket.send':
                body = json.loads(outgoing.get('text', '{}'))
                if body.get('type') == 'track':
                    ready.set()
            await send(outgoing)

        await self.app(scope, read, write)
