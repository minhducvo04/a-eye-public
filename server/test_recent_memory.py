import asyncio
from types import SimpleNamespace

from server.recent_memory import RecentMemory, source_for, answer


def scene(note, *labels):
    return dict(caption=note, objects=[dict(label=x) for x in labels], image_b64='never retain')


def test_window_step_and_specific_time():
    log = RecentMemory(clock=lambda: 180, started=0)
    log.add(scene('Red wire beside multimeter', 'red wire', 'multimeter'), 60, 'Step 3: place wire')
    log.add(scene('Red wire on breadboard row 12', 'red wire', 'breadboard'), 120, 'Step 4: connect wire')
    log.add(scene('Soldering iron picked up', 'soldering iron'), 179)
    assert 'row 12' in log.find('where did I put the red wire a minute ago?')['say']
    assert 'Step 3' in log.find('what was step 3?')['say']
    assert 'Soldering iron' in log.find('what did I just do?')['say']
    assert log.find('where did I put the blue wire?') is None
    assert 'image_b64' not in repr(log.entries)
    log.prune(181)
    assert len(log.entries) == 2
    log.clear()
    assert not log.entries


def test_temporal_routes():
    for text, on, want in [
        ('what did I just do?', True, 'recent'),
        ('where did I put the wire a minute ago?', True, 'recent'),
        ('what was step 3?', False, 'recent'),
        ('what step was I just on?', True, 'recent'),
        ('what happened earlier in this call?', True, 'rewind'),
        ('what happened earlier in this call?', False, 'recent'),
        ('where was the resistor yesterday?', True, 'long-term'),
        ('what did I do last time?', False, 'long-term'),
        ('where was the wire 3 minutes ago?', True, 'rewind'),
        ('where does this resistor go?', True, None),
    ]:
        assert source_for(text, on) == want


def test_recent_never_calls_cloud_and_no_wrong_time_fallback():
    async def check():
        log = RecentMemory(clock=lambda: 100, started=0)
        log.add(scene('Resistor placed on breadboard row 12', 'resistor'), 99)
        session = SimpleNamespace(recent_memory=log, rewind=None)
        result = await answer(session, 'what did I just do?')
        assert result['source'] == 'recent' and 'row 12' in result['say']
        result = await answer(session, 'where was the resistor yesterday?')
        assert result['source'] == 'unavailable' and 'yesterday' in result['say']
    asyncio.run(check())


def test_rewind_age_filter_and_off_switch(monkeypatch):
    from server.rewind import Rewind, Clip

    class Client:
        async def search(self, index_id, query, video_ids, limit):
            assert video_ids == ['old']
            return [dict(video_id='new', start=0, end=4, rank=1),
                    dict(video_id='old', start=0, end=4, rank=2)]

    async def check():
        rewind = Rewind('fixture', client=Client(), clock=lambda: 100)
        rewind.clips = [Clip(10, 15, video_id='old', status='ready'),
                        Clip(85, 90, video_id='new', status='ready')]
        hits = await rewind.search('resistor', before_s=80)
        assert [h['video_id'] for h in hits] == ['old']
        log = RecentMemory(clock=lambda: 100, started=0)
        session = SimpleNamespace(recent_memory=log, rewind=rewind)
        async def forbidden(*args, **kwargs):
            raise AssertionError('cloud must not run')
        rewind.answer = forbidden
        result = await answer(session, 'where was the resistor 3 minutes ago?')
        assert result['source'] == 'unavailable'
        rewind.on = True
        monkeypatch.setenv('LOCAL_ONLY', '1')
        result = await answer(session, 'where was the resistor 3 minutes ago?')
        assert result['source'] == 'unavailable'
    asyncio.run(check())


def test_ws_recent_answer_without_brain_and_cleanup(monkeypatch):
    from fastapi.testclient import TestClient
    from server import ws2
    from server.test_ws2 import app, send, hello
    from server.test_pipeline import finish

    sessions = []
    original = ws2.Session
    def capture(*args, **kwargs):
        session = original(*args, **kwargs)
        sessions.append(session)
        return session
    monkeypatch.setattr(ws2, 'Session', capture)
    application = app()
    with TestClient(application) as client, client.websocket_connect('/ws2') as socket:
        hello(socket)
        assert sessions[0].recent_memory.entries
        send(socket, 'user_text', text='what did I just do?', final=True)
        messages = finish(socket)
        assert any(m['type'] == 'say' and 'scene note' in m['text'] for m in messages)
        assert not application.state.llm_v2.calls
        send(socket, 'hello', pack='general', camera=False)
        from server.test_pipeline import read
        read(socket)
        assert not sessions[0].recent_memory.entries
    assert all(not session.recent_memory.entries for session in sessions)


def test_fixture_sources_and_honest_fallback(tmp_path, monkeypatch):
    from scripts.recent_memory_bench import benchmark, fixture

    async def check():
        monkeypatch.delenv('LOCAL_ONLY', raising=False)
        report = await benchmark()
        assert report['right_source'] == report['answer_checks'] == 20
        assert report['latency']['recent']['max_ms'] < 1000
        session = fixture(tmp_path)
        async def unavailable(*args, **kwargs):
            return 'Indexing', None
        session.rewind.answer = unavailable
        result = await answer(session, 'Where was the resistor earlier in this call?')
        assert result['source'] == 'long-term'
        assert 'earlier call' in result['say'] and 'parts bin' in result['say']
        session.rewind.memory_writer.store.save_settings(dict(remembering=False))
        result = await answer(session, 'Where was the resistor yesterday?')
        assert result['source'] == 'unavailable'
    asyncio.run(check())


def test_step_instruction_not_claimed_as_completed_action():
    log = RecentMemory(clock=lambda: 100, started=0)
    log.note_step('Step 3: place resistor on row 12.')
    assert 'guide said' in log.find('what was step 3?')['say']
    assert log.find('what did I just do?') is None
    log.add(scene('Red wire on tray', 'red wire'), 100)
    assert log.entries[-1]['step'].startswith('Step 3:')


def test_live_transcripts_are_not_scene_evidence_and_steps_expire():
    now = [100]
    log = RecentMemory(clock=lambda: now[0], started=0)
    log.note_transcript('I placed the resistor.', 'user')
    assert log.find('what step was I just on?') is None
    log.note_transcript('Step 3: place resistor on row 12.', 'helper')
    assert 'guide said' in log.find('what step was I just on?')['say']
    assert log.find('what did I just do?') is None
    now[0] = 221
    assert log.find('what step was I just on?') is None
    assert not log.entries
