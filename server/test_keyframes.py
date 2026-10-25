import asyncio
import json

import pytest

from server.keyframes import KeyframeGate, difference, signature
from scripts.keyframe_bench import synthetic_frame
from server.agent import Session, run_turn
from server.test_agent import FRAME, SCENE, LLM, Vision
from server import ws2


def test_signatures_and_jitter():
    baseline = signature(synthetic_frame(0))
    assert len(baseline) == 1024
    assert difference(baseline, baseline) == 0
    assert difference(bytes(1024), bytes([255]) * 1024) == 255
    assert max(difference(baseline, signature(synthetic_frame(i))) for i in range(100)) < 34.5
    assert difference(baseline, signature(synthetic_frame(100))) > 34.5
    assert difference(signature(synthetic_frame(100)), signature(synthetic_frame(200))) > 34.5


def test_gate_clock_and_min_gap(monkeypatch):
    monkeypatch.delenv('KEYFRAME_THRESHOLD', raising=False)
    monkeypatch.delenv('KEYFRAME_HEARTBEAT_S', raising=False)
    gate = KeyframeGate()
    a, b = bytes(1024), bytes([100]) * 1024
    assert gate.should_scan(a, 0) == (True, 'first')
    assert gate.should_scan(b, .49) == (False, 'skip')
    assert gate.should_scan(b, .5) == (True, 'change')
    assert gate.should_scan(b, 10.49) == (False, 'skip')
    assert gate.should_scan(b, 10.5) == (True, 'heartbeat')
    monkeypatch.setenv('KEYFRAME_THRESHOLD', '120')
    monkeypatch.setenv('KEYFRAME_HEARTBEAT_S', '2')
    gate = KeyframeGate()
    assert gate.should_scan(a, 0)[0]
    assert gate.should_scan(b, 1) == (False, 'skip')
    assert gate.should_scan(b, 2) == (True, 'heartbeat')


def test_memory_and_brain_message():
    async def check():
        session = Session('s', scene=SCENE)
        for i in range(10):
            session.scene_memory.add(dict(caption=str(i), objects=SCENE['objects']), i)
        session.scene_memory.add(dict(caption='9', objects=[dict(label='new')]), 11)
        entries = session.scene_memory.snapshot(12)
        assert len(entries) == 8
        assert [e['caption'] for e in entries] == list(map(str, range(9, 1, -1)))
        assert entries[0] == dict(caption='9', labels=['new'], age_s=1, now=True)
        assert all('now' not in e for e in entries[1:])
        brain = LLM()
        await run_turn(session, 'help', vision=Vision(), llm=brain)
        assert json.loads(brain.calls[0][1])['memory'][0]['now'] is True
        session.camera = False
        await run_turn(session, 'help', vision=Vision(), llm=brain)
        assert all('now' not in e for e in json.loads(brain.calls[-1][1])['memory'])
        assert Session('s').scene_memory.snapshot(12) == []
    asyncio.run(check())


def test_sixty_second_scene_loop(monkeypatch, capsys):
    async def check():
        clock = [0.0]
        monkeypatch.setattr(ws2, 'monotonic', lambda: clock[0])
        session, vision = Session('s'), Vision()
        messages = []
        async def send(msg):
            messages.append(msg)
        task = asyncio.create_task(ws2.scene_loop(session, vision, send))
        try:
            for i in range(300):
                clock[0] = i / 5
                session.frame = dict(FRAME, seq=i, jpeg_b64=synthetic_frame(i))
                session.frame_time = clock[0]
                async def consumed():
                    while session.scene_scans + session.scene_skips < i + 1:
                        await asyncio.sleep(.001)
                await asyncio.wait_for(consumed(), 1)
            assert len(vision.calls) == 6
            assert len([m for m in messages if m['type'] == 'metrics']) == 6
            assert session.scene_scans == 6
            assert session.scene_skips == 294
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r['reason'] for r in rows] == ['first', 'heartbeat', 'change', 'heartbeat', 'change', 'heartbeat']
    assert rows[-1]['scans'] == 6


@pytest.mark.parametrize('camera', [False, True])
def test_refresh_same_frame_and_camera_off(monkeypatch, camera):
    async def check():
        clock = [0.0]
        monkeypatch.setattr(ws2, 'monotonic', lambda: clock[0])
        session = Session('s', camera=camera, frame=dict(FRAME, jpeg_b64=synthetic_frame(0)))
        vision = Vision()
        async def send(msg):
            pass
        task = asyncio.create_task(ws2.scene_loop(session, vision, send))
        try:
            await asyncio.sleep(.04)
            clock[0] = 11
            session.refresh_requested.set()
            await asyncio.sleep(.04)
            assert len(vision.calls) == (2 if camera else 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_new_hello_clears_memory(monkeypatch):
    from fastapi.testclient import TestClient
    from server.test_ws2 import app, send
    from server.test_pipeline import read, finish

    async def noop(app):
        pass
    monkeypatch.setattr(ws2, 'warm_brain', noop)
    application = app()
    with TestClient(application) as client, client.websocket_connect('/ws2') as socket:
        send(socket, 'hello', pack='general')
        read(socket)
        socket.send_json(FRAME)
        while read(socket)['type'] != 'track':
            pass
        send(socket, 'user_text', text='help', final=True)
        finish(socket)
        assert json.loads(application.state.llm_v2.calls[-1][1])['memory']
        send(socket, 'hello', pack='cooking', camera=False)
        read(socket)
        send(socket, 'user_text', text='help', final=True)
        finish(socket)
        assert json.loads(application.state.llm_v2.calls[-1][1])['memory'] == []


def test_keyframe_scan_always_asks_for_a_caption():
    """Live bug: with pack queries the scene had no caption and the brain said the camera was off."""
    import inspect
    from server import ws2
    assert "caption=True" in inspect.getsource(ws2.scene_loop)
