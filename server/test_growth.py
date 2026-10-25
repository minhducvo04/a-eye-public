import asyncio

from cryptography.fernet import Fernet

from server import packs, safety
from server.agent import Session, run_turn
from server.memory import Cipher, Store
from server.test_agent import LLM, Vision


def test_encrypted_log_isolated_reload_and_forget(tmp_path):
    from server.growth import GrowthLog
    cipher = Cipher(Fernet.generate_key())
    store = Store('fixture', base=tmp_path, cipher=cipher)
    log = GrowthLog(store)
    row = log.start('sink')
    log.update(row, hints=4, tries=2, safety_stop=True, completed=True, minutes=1.5)
    assert b'sink' not in (store.dir / 'growth.json').read_bytes()
    again = GrowthLog(store)
    assert again.level('sink') == 2
    assert again.level('hinge') == 1
    assert again.rows('sink')[0]['minutes'] == 1.5
    assert GrowthLog(Store('other', base=tmp_path, cipher=cipher)).rows('sink') == []
    again.forget()
    assert not (store.dir / 'growth.json').exists()
    assert again.level('sink') == 1
    assert not again.update(row, hints=99)  # an in-flight old run cannot restore forgotten rows


def test_no_plaintext_fallback(tmp_path):
    from server.growth import GrowthLog
    log = GrowthLog(Store('fixture', base=tmp_path, cipher=Cipher('')))
    log.start('sink')
    assert list(tmp_path.rglob('*')) == []


def test_three_runs_fade_and_override_keeps_gate(tmp_path):
    from server.growth import GrowthLog
    from server.packs.home import HomeGuide, TASKS
    log = GrowthLog(Store('fixture', base=tmp_path, cipher=Cipher(Fernet.generate_key())))
    hints = []
    for level in (1, 2, 3):
        guide = HomeGuide(growth=log)
        first = guide.reply('sink')
        assert guide.level == level
        if level == 3:
            assert first == ''
        guide.reply('next')
        assert 'no flow' in guide.reply('next')  # check never fades
        assert guide.reply('next') == safety.WATER_OFF_LINE
        for _ in range(len(TASKS['sink']) - 2):
            guide.water_off_passed = True
            guide.reply('next')
        assert guide.completed
        row = log.rows('sink')[-1]
        hints.append(row['hints'])
        assert row['date'] and row['tries'] == 1 and row['completed']
        assert guide.reply('done') == ''  # no duplicate completion or praise
    assert hints[0] > hints[1] > hints[2] == 0
    guide = HomeGuide(growth=log)
    guide.reply('sink')
    assert guide.reply('walk me through it').startswith('Show')
    guide.reply('next')
    guide.reply('next')
    assert guide.reply('next') == safety.WATER_OFF_LINE
    assert guide.reply('water near the outlet') in safety.LINES
    assert log.rows('sink')[-1]['safety_stop'] is True


def test_quiet_level_and_forget_never_call_model(tmp_path):
    from server.growth import GrowthLog
    log = GrowthLog(Store('fixture', base=tmp_path, cipher=Cipher('')))
    for _ in range(2):
        log.update(log.start('sink'), completed=True)
    session = Session('s', packs.get('home'), growth=log)
    llm, vision = LLM(), Vision()
    def turn(text):
        return asyncio.run(run_turn(session, text, llm=llm, vision=vision))
    assert not [m for m in turn('sink') if m['type'] == 'say']
    assert any('forgotten' in m.get('text', '') for m in turn('forget my progress'))
    assert log.rows('sink') == []
    turn('sink')
    assert session.home_guide.level == 1
    assert llm.calls == vision.calls == []


def test_authenticated_users_and_forget_over_websocket(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.auth import mint_owner
    from server.memory import Memories
    from server.test_ws2 import app
    from server.rewind import registry
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.setenv('A_EYE_TOKEN', 'fixture-token')
    monkeypatch.setenv('A_EYE_MEMORY_KEY', Fernet.generate_key().decode())
    application = app()
    application.state.memories = Memories(base=tmp_path)
    headers = {'Authorization': 'Bearer fixture-token', 'X-Aeye-Owner': mint_owner()}
    other = dict(headers, **{'X-Aeye-Owner': mint_owner()})
    with TestClient(application) as client:
        def turn(ws, sid, text):
            ws.send_json(dict(v=2, type='user_text', session_id=sid, text=text, final=True))
            messages = []
            while not messages or messages[-1].get('stage') not in ('done', 'failed'):
                messages.append(ws.receive_json())
            assert messages[-1]['stage'] == 'done'
            return messages
        for sid, identity in [('first', headers), ('second', other), ('again', headers)]:
            with client.websocket_connect('/ws2', headers=identity) as ws:
                ws.send_json(dict(v=2, type='hello', session_id=sid, pack='home', camera=False))
                assert ws.receive_json()['state'] == 'listening'
                turn(ws, sid, 'hinge')
                session = registry(application).get(sid).session
                if sid == 'first':
                    turn(ws, sid, 'next')
                    turn(ws, sid, 'next')
                    turn(ws, sid, 'done')
                    assert session.growth.level('hinge') == 2
                    first_log = session.growth
                elif sid == 'second':
                    assert session.home_guide.level == 1
                    second_log = session.growth
                    assert second_log is not first_log
                else:
                    assert session.home_guide.level == 2
                    turn(ws, sid, 'forget my progress')
                    assert session.home_guide.task == 'hinge'
                    assert first_log.rows('hinge') == []
                    assert len(second_log.rows('hinge')) == 1
                    assert not first_log.store._path('growth.json').exists()
                    turn(ws, sid, 'hinge')
                    assert session.home_guide.level == 1
        assert application.state.llm_v2.calls == []
