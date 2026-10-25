"""Cartesia WebSocket adapter: one context per turn, flush ends a clause, stop cancels, errors fall back."""
import asyncio
import base64
import json

from voice.cartesia_ws import CartesiaWsAdapter


class FakeSocket:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail
        self.inbox = asyncio.Queue()

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg.get('cancel'):
            return
        if self.fail:
            await self.inbox.put(json.dumps(dict(type='error', context_id=msg['context_id'])))
            return
        pcm = base64.b64encode(b'\x01\x00' * 160).decode()
        for _ in range(2):
            await self.inbox.put(json.dumps(dict(type='chunk', data=pcm, done=False, context_id=msg['context_id'])))
        await self.inbox.put(json.dumps(dict(type='flush_done', flush_done=True, context_id=msg['context_id'])))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.inbox.get()

    async def close(self):
        pass


class Backup:
    async def say(self, utterance_id, text):
        yield b'backup'


def test_clauses_share_a_context_and_flush_ends_each():
    async def check():
        socket = FakeSocket()
        voice = CartesiaWsAdapter(connect=lambda: _ready(socket))
        voice.turn = 'turn-1'
        first = [c async for c in voice.say('u1', 'Okay, got it,')]
        second = [c async for c in voice.say('u2', 'it goes on the CPU fan header.')]
        assert len(first) == 2 and len(second) == 2
        assert [m['context_id'] for m in socket.sent] == ['turn-1', 'turn-1']
        assert all(m['continue'] is True and m['flush'] is True and m['max_buffer_delay_ms'] == 0 for m in socket.sent)
        assert socket.sent[0]['output_format'] == dict(container='raw', encoding='pcm_s16le', sample_rate=16000)
        await voice.aclose()
    asyncio.run(check())


def test_error_before_audio_falls_back_to_kokoro():
    async def check():
        voice = CartesiaWsAdapter(backup=Backup(), connect=lambda: _ready(FakeSocket(fail=True)))
        assert [c async for c in voice.say('u1', 'Hello there.')] == [b'backup']
        await voice.aclose()
    asyncio.run(check())


def test_stop_sends_cancel_for_the_context():
    async def check():
        socket = FakeSocket()
        voice = CartesiaWsAdapter(connect=lambda: _ready(socket))
        voice.turn = 'turn-9'
        stream = voice.say('u1', 'A long reply.')
        await stream.__anext__()
        voice.stop('u1')
        await asyncio.sleep(0)
        assert dict(context_id='turn-9', cancel=True) in socket.sent
        await stream.aclose()
        await voice.aclose()
    asyncio.run(check())


async def _ready(value):
    return value
