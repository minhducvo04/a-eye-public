import asyncio
import json
from time import monotonic

from fastapi.testclient import TestClient

from contracts import validate
from server.test_ws2 import app, send
from server.test_agent import FRAME, SCENE, Vision, LLM
from server.agent import Session, run_turn
from server.ws2 import scene_loop


def read(ws):
    msg = ws.receive_json()
    validate('server_message_v2', msg)
    return msg


def test_ack_and_stages():
    with TestClient(app()) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', camera=False)
        read(ws)
        send(ws, 'user_text', text='hello', final=True)
        first = read(ws)
        assert first['type'] == 'turn' and first['stage'] == 'received'
        messages = [first]
        while messages[-1].get('stage') not in ('done', 'failed'):
            messages.append(read(ws))
        assert [m['stage'] for m in messages if m['type'] == 'turn'] == ['received', 'thinking', 'speaking', 'done']
        assert len({m['turn_id'] for m in messages if 'turn_id' in m}) == 1
        delta = next(m for m in messages if m['type'] == 'reply_delta')
        assert '{' not in delta['text']
        assert messages.index(delta) < next(i for i, m in enumerate(messages) if m['type'] == 'say')


def test_no_rescan_requested_frame(monkeypatch):
    monkeypatch.setenv('SCENE_INTERVAL_MS', '1')
    async def check():
        session, vision = Session('s', frame=FRAME), Vision()
        async def emit(msg):
            validate('server_message_v2', msg)
        task = asyncio.create_task(scene_loop(session, vision, emit))
        await asyncio.sleep(.03)
        session.refresh_requested.set()
        await asyncio.sleep(.03)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert len(vision.calls) == 1
    asyncio.run(check())


import pytest
from server import ws2
from server.agent import Turn, TURN_LATENCIES
from server.llm import LLMClient


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def noop(app):
        pass
    monkeypatch.setattr(ws2, 'warm_brain', noop)
    monkeypatch.setenv('A_EYE_MOCK', '0')
    monkeypatch.delenv('A_EYE_TOKEN', raising=False)


def finish(ws):
    messages = []
    while not messages or messages[-1].get('stage') not in ('done', 'failed', 'cancelled'):
        messages.append(read(ws))
    return messages


@pytest.mark.parametrize('text', ['', 'Thank you.', 'uh um', 'hello'])
def test_server_stt(text):
    class Box:
        async def stt(self, pcm):
            return text
        async def aclose(self):
            pass
    application = app()
    application.state.box_v2 = Box()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', stt='server', camera=False)
        read(ws)
        send(ws, 'audio_in', seq=1, pcm16_b64='AAA=', final=True)
        messages = finish(ws)
        stages = [m['stage'] for m in messages if m['type'] == 'turn']
        # Empty, hallucinated or filler-only transcripts end silently (no "Didn't catch that").
        real = text == 'hello'
        assert stages == (['received', 'transcribing', 'thinking', 'speaking', 'done'] if real else
                          ['received', 'transcribing', 'done'])
        assert not any(m.get('detail') == "Didn't catch that" for m in messages)


def test_timeout_backup_same_turn(monkeypatch):
    import httpx
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    monkeypatch.setenv('LLM_PROVIDER', 'claude')
    monkeypatch.setenv('BRAIN_FIRST_TOKEN_S', '.02')
    monkeypatch.delenv('LOCAL_ONLY', raising=False)
    calls = []
    class Cloud:
        @asynccontextmanager
        async def stream(self, **kwargs):
            calls.append('cloud')
            try:
                await asyncio.Event().wait()
                yield
            finally:
                calls.append('closed')
    def local(request):
        calls.append('local')
        return httpx.Response(200, text=json.dumps({'message': {'content':
            '{"draw":[]}SAY: This is the backup reply.'}, 'done': True}) + '\n')
    application = app()
    local_client = httpx.AsyncClient(transport=httpx.MockTransport(local))
    application.state.llm_v2 = LLMClient(anthropic_client=SimpleNamespace(messages=Cloud()), httpx_client=local_client)
    try:
        with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
            send(ws, 'hello', pack='general', camera=False)
            read(ws)
            send(ws, 'user_text', text='help', final=True)
            messages = finish(ws)
            assert [m['stage'] for m in messages if m['type'] == 'turn'] == ['received', 'thinking', 'backup', 'speaking', 'done']
            speaking = next(m for m in messages if m.get('stage') == 'speaking')
            assert speaking['detail'].startswith('Brain: local / ')
            # Model metadata must not announce speech before reply text is ready.
            delta = next(m for m in messages if m['type'] == 'reply_delta')
            reply = next(m for m in messages if m['type'] == 'say')
            assert messages.index(delta) < messages.index(speaking) < messages.index(reply)
            assert len({m['turn_id'] for m in messages if 'turn_id' in m}) == 1
            assert next(m['text'] for m in messages if m['type'] == 'say') == 'This is the backup reply.'
            assert calls == ['cloud', 'closed', 'local']
    finally:
        asyncio.run(local_client.aclose())


def test_deltas_wait_for_checked_speech_instead_of_exposing_unchecked_tokens():
    async def check():
        sent = []
        finished = []
        class Brain:
            async def stream(self, *args):
                yield '{"draw":[]}SAY: Hello'
                for word in [' there', ' from', ' the', ' streaming', ' brain.']:
                    await asyncio.sleep(.01)
                    if word.endswith('.'):
                        finished.append(monotonic())
                    yield word
        async def emit(msg):
            validate('server_message_v2', msg)
            sent.append((monotonic(), msg))
        await run_turn(Session('s', emit=emit), 'help', vision=Vision(), llm=Brain())
        deltas = [(t, m['text']) for t, m in sent if m['type'] == 'reply_delta']
        assert ''.join(text for _, text in deltas) == 'Hello there from the streaming brain.'
        assert len(deltas) == 1
        assert deltas[0][0] >= finished[0], 'no unchecked prefix may reach the screen'
        assert all('{' not in text for _, text in deltas)
    asyncio.run(check())


def test_camera_off_and_resume():
    vision = Vision()
    application = app(vision)
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', camera=False)
        read(ws)
        ws.send_json(FRAME)
        send(ws, 'user_text', text='help', final=True)
        messages = finish(ws)
        assert not vision.calls and not any(m['type'] == 'draw' for m in messages)
        system, user = application.state.llm_v2.calls[-1]
        assert 'camera is off' in system
        assert json.loads(user)['objects'] == []
        assert json.loads(user)['caption'] == 'camera is off'
        send(ws, 'camera', on=True)
        ws.send_json(dict(FRAME, seq=2))
        while read(ws)['type'] != 'track':
            pass
        send(ws, 'camera', on=False)
        send(ws, 'user_text', text='help', final=True)
        finish(ws)
        assert len(vision.calls) == 1
        assert json.loads(application.state.llm_v2.calls[-1][1])['objects'] == []


def test_stale_frame_not_used():
    async def check():
        brain = LLM()
        session = Session('s', scene=SCENE, frame=FRAME, frame_time=monotonic() - 4)
        messages = await run_turn(session, 'help', vision=Vision(), llm=brain)
        assert json.loads(brain.calls[0][1])['objects'] == []
        assert next(m for m in messages if m['type'] == 'draw')['ops'] == [{'kind': 'clear', 'id': '*'}]
    asyncio.run(check())


def test_typical_last_five_and_private_logs(monkeypatch, capsys):
    async def check():
        sent = []
        async def emit(msg):
            sent.append(msg)
            validate('server_message_v2', msg)
        monkeypatch.setattr('server.agent.monotonic', lambda: 10)
        TURN_LATENCIES.clear()
        try:
            for ms in [999, 10, 20, 30, 40, 100]:
                turn = Turn(Session('s'), emit, 10 - ms / 1000)
                await turn.stage('received', text='private transcript')
                await turn.stage('thinking')
                await turn.stage('speaking')
                await turn.stage('done')
                await turn.stage('cancelled')
            await Turn(Session('another'), emit).stage('thinking')
            assert sent[-1]['typical_ms'] == 30
            assert len([m for m in sent if m['stage'] in ('done', 'cancelled')]) == 6
        finally:
            TURN_LATENCIES.clear()
    asyncio.run(check())
    output = capsys.readouterr().out
    assert 'private transcript' not in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert all('ms' in row for row in rows)
    assert {'turn_received', 'turn_thinking', 'turn_speaking', 'turn_done'} <= {r['event'] for r in rows}


@pytest.mark.parametrize('action', ['barge_in', 'user_text', 'error'])
def test_terminal_status(action):
    import threading
    started = threading.Event()
    class Brain:
        async def stream(self, *args):
            if action == 'error':
                raise RuntimeError('private error')
            if not started.is_set():
                started.set()
                await asyncio.Event().wait()
            yield '{"draw":[]}SAY: Finished.'
    application = app()
    application.state.llm_v2 = Brain()
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='general', camera=False)
        read(ws)
        send(ws, 'user_text', text='first', final=True)
        first = read(ws)
        if action == 'error':
            messages = finish(ws)
            assert messages[-1]['stage'] == 'failed'
            assert messages[-1]['detail'] == 'Brain failed'
        else:
            assert started.wait(1)
            if action == 'barge_in':
                send(ws, 'barge_in', utterance_id='any', at_ms=0)
            else:
                send(ws, 'user_text', text='second', final=True)
            messages = finish(ws)
            assert messages[-1]['stage'] == 'cancelled'
            assert messages[-1]['turn_id'] == first['turn_id']
            if action == 'user_text':
                assert any(m.get('stage') == 'received' for m in messages)
                assert finish(ws)[-1]['stage'] == 'done'


def test_power_gate_keeps_server_speech():
    from voice.adapter import VoiceAdapter
    class Voice(VoiceAdapter):
        async def say(self, utterance_id, text):
            yield b'\x00\x00'
    application = app(voice=Voice())
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        send(ws, 'hello', pack='pc_build', tts='server', camera=False)
        read(ws)
        send(ws, 'user_text', text='help', final=True)
        messages = finish(ws)
        assert [m['stage'] for m in messages if m['type'] == 'turn'] == ['received', 'thinking', 'speaking', 'done']
        assert any(m['type'] == 'audio' and m['final'] for m in messages)
        assert not application.state.llm_v2.calls


def test_local_timeout_to_canned(monkeypatch):
    import httpx
    monkeypatch.setenv('LLM_PROVIDER', 'local')
    monkeypatch.setenv('BRAIN_FIRST_TOKEN_S', '.01')
    async def handler(request):
        await asyncio.Event().wait()
    async def check():
        backups = []
        async def backup():
            backups.append(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            chunks = [chunk async for chunk in LLMClient(httpx_client=client).stream(
                '', json.dumps({'text': 'private input'}), on_backup=backup)]
        assert backups == [True]
        assert 'SAY:' in ''.join(chunks)
        assert 'private input' not in ''.join(chunks)
    asyncio.run(check())
