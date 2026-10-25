from server import packs, safety
from server.agent import Session
from server.memory import Store
from server.test_agent import LLM, Vision, run


def test_home_replay_and_water_gate(tmp_path):
    from server.packs.home import HomeGuide, INTRO, TASKS
    store = Store('home-test', base=tmp_path)
    store.set_tag('shutoff valve', home='under the kitchen sink')
    guide = HomeGuide()
    lines = [guide.reply('fix a dripping tap', store)]
    assert lines[0].startswith(INTRO)
    lines.append(guide.reply('next', store))
    assert 'under the kitchen sink' in lines[-1]
    lines.append(guide.reply('next', store))
    assert guide.reply('next', store) == safety.WATER_OFF_LINE
    assert guide.reply('the water is off, next', store) == safety.WATER_OFF_LINE
    guide.water_off_passed = True  # trusted checker integration; never parsed from speech
    lines.append(guide.reply('next', store))
    assert lines[-1] == TASKS['sink'][3]
    assert sum(line.count(INTRO) for line in lines) == 1
    assert all(len(line.split('. ')) <= 2 for line in lines)
    guide.reply('tighten a loose cabinet hinge')
    guide.reply('fix a dripping sink')
    assert guide.water_off_passed is False


def test_all_tasks_short_and_camera_directed():
    from server.packs.home import TASKS
    assert set(TASKS) == {'sink', 'breaker', 'hinge'}
    for steps in TASKS.values():
        for step in steps:
            assert len(step.split('. ')) <= 2
            assert len(step.split()) <= 45
            assert 'show' in step.lower() or 'camera' in step.lower()


def test_home_agent_no_model_and_once_only_intro():
    from server.packs.home import INTRO
    session = Session('s', pack=packs.get('home'))
    llm, vision = LLM(), Vision()
    first = run(session, llm, 'fix a dripping sink', vision)
    second = run(session, llm, 'next', vision)
    spoken = [m['text'] for m in first + second if m['type'] == 'say']
    assert sum(t.count(INTRO) for t in spoken) == 1
    assert llm.calls == vision.calls == []


def test_hazard_blocks_progress_and_new_session_has_no_memory():
    from server.packs.home import INTRO
    s = Session('s', pack=packs.get('home'))
    run(s, text='fix a dripping sink')
    messages = run(s, text='Water near the outlet, next')
    assert next(m['text'] for m in messages if m['type'] == 'say') in safety.LINES
    assert s.home_guide.index == 0
    other = Session('other', pack=packs.get('home'))
    assert next(m['text'] for m in run(other, text='cabinet hinge') if m['type'] == 'say').startswith(INTRO)


def test_full_replay_each_task():
    from server.packs.home import HomeGuide, TASKS
    for task, question in [('sink', 'sink'), ('hinge', 'hinge'), ('breaker', 'breaker')]:
        guide = HomeGuide()
        guide.reply(question)
        for index in range(1, len(TASKS[task])):
            if task == 'sink' and index >= 3:
                guide.water_off_passed = True
            assert guide.reply('next') == TASKS[task][index]
        final = guide.reply('next')
        assert final == '' and guide.completed


def test_hazard_revokes_water_check():
    from server.packs.home import HomeGuide
    guide = HomeGuide(introduced=True, index=2, water_off_passed=True)
    assert guide.reply('water is still on') == safety.WATER_OFF_LINE
    assert guide.water_off_passed is False
    assert guide.reply('continue') == safety.WATER_OFF_LINE


def test_only_literal_checker_pass_unlocks():
    for value in (None, False, 'true', 1, {'pass': True}):
        assert safety.plumbing_stop(value) == safety.WATER_OFF_LINE
    assert safety.plumbing_stop(True) is None


def test_agent_reads_only_attached_local_memory(tmp_path):
    from types import SimpleNamespace
    import asyncio
    from server.agent import run_turn
    from server.memory import Memories
    memories = Memories(base=tmp_path)
    store, _ = memories.attach('s', 'home-test', 'owner')
    store.set_tag('shutoff valve', home='under the utility sink')
    app = SimpleNamespace(state=SimpleNamespace(memories=memories))
    session = Session('s', pack=packs.get('home'))
    asyncio.run(run_turn(session, 'sink', app=app, vision=Vision(), llm=LLM()))
    result = asyncio.run(run_turn(session, 'next', app=app, vision=Vision(), llm=LLM()))
    assert 'under the utility sink' in next(m['text'] for m in result if m['type'] == 'say')
    other = Session('other', pack=packs.get('home'))
    asyncio.run(run_turn(other, 'sink', app=app, vision=Vision(), llm=LLM()))
    result = asyncio.run(run_turn(other, 'next', app=app, vision=Vision(), llm=LLM()))
    assert 'under the utility sink' not in next(m['text'] for m in result if m['type'] == 'say')


def test_repeat_repair_cannot_bypass_revoked_check():
    from server.packs.home import HomeGuide
    guide = HomeGuide(introduced=True, index=4, water_off_passed=True)
    guide.reply('water is still on')
    assert guide.reply('repeat that') == safety.WATER_OFF_LINE
