import asyncio
from time import monotonic

import pytest

from server import packs
from server.agent import Session, run_turn
from server.test_agent import FRAME


def turn(session, text, vision=None):
    return asyncio.run(run_turn(session, text, vision=vision, llm=None))


def spoken(messages):
    return ' '.join(m['text'] for m in messages if m['type'] == 'say')


@pytest.mark.parametrize('text,line', [
    ('rewire the LED while USB is connected', 'Unplug the USB first'),
    ('LED with no resistor in series', 'Unplug the USB first. Use a 220 ohm resistor in series with the LED before connecting USB.'),
    ('touch the hot soldering iron tip', 'Do not touch the hot soldering iron tip; leave it in its stand.'),
    ('use a wall outlet in this build', 'Stop; mains power and wall outlets are outside this USB-only build.'),
])
def test_coded_stops_before_model(text, line):
    session = Session('ee', packs.get('engineering'))
    result = turn(session, text)
    assert spoken(result) == line
    assert any(m.get('detail') == 'Stopped by code' for m in result)
    assert session.ee_guide.index == 0


def test_sequence_power_series_gates_and_recall():
    session = Session('ee', packs.get('engineering'))
    assert spoken(turn(session, 'light an LED')).startswith('Step 1:')
    assert spoken(turn(session, 'next')) == 'Unplug the USB first'
    assert spoken(turn(session, 'USB unplugged, next')).startswith('Step 2:')
    assert spoken(turn(session, 'next')).startswith('Step 3:')
    before = session.ee_guide.index
    assert 'short leg' in spoken(turn(session, 'what was step 3'))
    assert session.ee_guide.index == before
    session.recent_memory.add(dict(caption='The resistor is beside the blue tray.', objects=[]), monotonic())
    assert 'blue tray' in spoken(turn(session, 'where did I put the resistor'))
    assert spoken(turn(session, 'next')).startswith('Step 4:')
    assert spoken(turn(session, 'next')).startswith('Step 5:')
    assert 'resistor in series' in spoken(turn(session, 'next'))
    assert spoken(turn(session, 'resistor is in series, next')).startswith('Step 6:')
    assert spoken(turn(session, 'rewire the LED')) == 'Unplug the USB first'
    assert spoken(turn(session, 'next')).startswith('Step 7:')
    result = turn(session, 'done')
    assert any(m.get('detail') == 'Engineering: complete' for m in result)
    assert not turn(session, 'done')


def test_power_report_revokes_unplugged_and_missing_resistor_blocks_upload():
    session = Session('ee', packs.get('engineering'))
    turn(session, 'light an LED')
    turn(session, 'USB unplugged, next')
    assert spoken(turn(session, 'USB power is on')) == 'Unplug the USB first'
    assert spoken(turn(session, 'next')) == 'Unplug the USB first'
    turn(session, 'USB unplugged, next')
    turn(session, 'next')
    turn(session, 'next')
    turn(session, 'resistor is in series, next')
    assert session.ee_guide.index == 5
    turn(session, 'the LED has no resistor')
    assert not spoken(turn(session, 'next')).startswith('Step 7:')
    assert not session.ee_guide.completed


def test_fresh_visible_mains_blocks_ordinary_next():
    session = Session('ee', packs.get('engineering'), frame=FRAME)
    turn(session, 'light an LED')
    session.scene = dict(caption='A wall outlet powers this build.', objects=[], source='ollama')
    session.scene_seq = FRAME['seq']
    session.frame_time = monotonic()
    assert 'mains power' in spoken(turn(session, 'USB unplugged, next'))
    assert session.ee_guide.index == 0
    session.scene['caption'] = 'USB is connected to the UNO.'
    assert spoken(turn(session, 'USB unplugged, next')) == 'Unplug the USB first'
    assert session.ee_guide.index == 0


def test_five_runs_fade_and_keep_stops(tmp_path):
    from server.growth import GrowthLog
    from server.memory import Store, Cipher
    log = GrowthLog(Store('ee', base=tmp_path, cipher=Cipher('')))
    counts = []
    for _ in range(5):
        session = Session('ee', packs.get('engineering'), growth=log)
        for text in ('light an LED', 'USB unplugged, next', 'next', 'next', 'next',
                     'resistor is in series, next', 'next', 'done'):
            turn(session, text)
        assert session.ee_guide.completed
        counts.append(log.rows('ee_led')[-1]['hints'])
        assert spoken(turn(session, 'touch hot soldering iron tip')).startswith('Do not touch')
    assert counts[0] > counts[1] > counts[2] == counts[3] == counts[4] == 0
    assert 'Step 1:' in spoken(turn(session, 'light an LED')) or session.ee_guide.level == 3
    assert 'Step 1:' in spoken(turn(session, 'walk me through it'))
    turn(session, 'forget my progress')
    assert log.rows('ee_led') == []


def test_extra_tasks_short_and_no_model():
    from server.packs.electronics import TASKS
    for text, task in [('read resistor color bands', 'ee_resistor'), ('check LED polarity', 'ee_polarity')]:
        session = Session('ee', packs.get('engineering'))
        lines = [spoken(turn(session, text))]
        for _ in range(len(TASKS[task]) - 1):
            lines.append(spoken(turn(session, 'next')))
        assert all('Show' in line and len(line.split('. ')) <= 2 for line in lines)
        assert ('red red brown' if task == 'ee_resistor' else 'long leg') in ' '.join(lines).lower()


def test_pointer_current_part_and_freshness():
    class Grounding:
        def __init__(self):
            self.names = []

        async def call(self, method, body):
            name = body['queries'][0]
            self.names.append(name)
            return dict(caption=f'{name} is visible.', objects=[dict(id='part', label=name,
                        bbox=[.1, .2, .2, .2], conf=.9)], ms=1, source='ollama')
    session = Session('ee', packs.get('engineering'), frame=FRAME)
    vision = Grounding()
    for text, target in [('light an LED', 'LED'), ('USB unplugged, next', 'LED'),
                         ('next', 'resistor'), ('next', 'pin 9')]:
        session.frame_time = monotonic()
        messages = turn(session, text, vision)
        assert vision.names[-1] == target
        assert any(m['type'] == 'draw' and any(op['kind'] == 'box' for op in m['ops']) for m in messages), target
    session.frame_time = monotonic() - 2
    assert not any(m['type'] == 'draw' for m in turn(session, 'repeat', Grounding()))


def test_ws_engineering_mode_without_contract_change(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.test_ws2 import app
    from server.memory import Memories
    monkeypatch.setenv('ROUTER', 'off')
    application = app()
    application.state.memories = Memories(base=tmp_path)
    with TestClient(application) as client, client.websocket_connect('/ws2?mode=engineering') as ws:
        ws.send_json(dict(v=2, type='hello', session_id='ee', pack='general', camera=False))
        assert ws.receive_json()['state'] == 'listening'
        for text in ('light an LED', 'where did I put the resistor'):
            ws.send_json(dict(v=2, type='user_text', session_id='ee', text=text, final=True))
            messages = []
            while not messages or messages[-1].get('stage') not in ('done', 'failed'):
                messages.append(ws.receive_json())
            assert messages[-1]['stage'] == 'done'
            assert ('Step 1:' if text == 'light an LED' else "can't confirm") in spoken(messages)
    assert application.state.llm_v2.calls == []


def test_ee_note_uses_practice_facts_and_requires_contact():
    from server.family_note import Store
    store = Store()
    with pytest.raises(ValueError):
        store.draft('ee', dict(task='ee_led', detail=0), '')
    contact = store.add_contact('ee', dict(name='Fixture', relation='friend', consent=True))
    note = store.draft('ee', dict(task='ee_led', detail=0), contact['id'])
    assert 'Arduino' in note['text'] and 'practice' in note['text']
    assert store.drafts[note['id']]['status'] == 'draft'
