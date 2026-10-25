"""Contract-checked, grounded v2 turns. Camera data stays in memory."""
import asyncio
from dataclasses import dataclass, field
import json
import re
from time import monotonic, time
from collections import deque
from statistics import median
from contextlib import aclosing
import os
from uuid import uuid4

from server.expert import RULES as EXPERT_RULES, should_escalate, mark_failed
from server.llm import TURN_USAGE, TURN_BRAIN
from server import rewind_tools
from server import pointer
from server.routing import forget_request, past_tool, rewind_callback, route_turn

from server.crops import crop_b64, pick_target
from server.privacy import claude_allowed, local_only

from contracts import schema, validate
from market.search import search
from server import packs, shopping
from server.logs import event
from server.keyframes import KeyframeGate, SceneMemory
from server.recent_memory import RecentMemory
from server.boards import load_board
from server.packs.pc_build import board_prompt

POWER_LINE = 'Switch the power off and unplug it first. Tap Power is off when done.'


@dataclass
class Session:
    session_id: str
    pack: packs.Pack = field(default_factory=lambda: packs.get('general'))
    board_id: str = 'demo-b650'
    tts: str = 'browser'
    camera: bool = True
    frame_time: float = field(default_factory=monotonic)
    power_off: bool = False
    frame: dict | None = field(default=None, repr=False)
    history: list = field(default_factory=list, repr=False)
    vision_error_sent: bool = False
    emit: object = field(default=None, repr=False)
    scene: dict | None = field(default=None, repr=False)
    scene_memory: SceneMemory = field(default_factory=SceneMemory, repr=False)
    recent_memory: RecentMemory = field(default_factory=RecentMemory, repr=False)
    keyframe_gate: KeyframeGate = field(default_factory=KeyframeGate, repr=False)
    scene_scans: int = 0
    scene_skips: int = 0
    scene_seq: int = 0
    scene_time: float = 0
    scene_task: object = field(default=None, repr=False)
    speak: object = field(default=None, repr=False)
    refresh_requested: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    voice_pending: bool = False
    user_speaking: bool = False
    expert_task: object = field(default=None, repr=False)
    expert_start: object = field(default=None, repr=False)
    expert_failures: dict = field(default_factory=dict, repr=False)
    expert_step: str = ''
    home_guide: object = field(default=None, repr=False)
    ee_guide: object = field(default=None, repr=False)
    growth: object = field(default=None, repr=False)
    step_checker: object = field(default=None, repr=False)
    private: bool = False
    checker_test_mode: bool = False
    checker_test_verdict: tuple | None = field(default=None, repr=False)
    board: dict = field(init=False)

    def __post_init__(self):
        self.board = load_board(self.board_id)
        if self.pack.id == 'home':
            from server.packs.home import HomeGuide
            self.home_guide = HomeGuide()
        if self.pack.id == 'engineering':
            from server.packs.electronics import ElectronicsGuide
            self.ee_guide = ElectronicsGuide()


def message(kind, **fields):
    result = dict(v=2, type=kind, **fields)
    validate('server_message_v2', result)
    return result


TURN_LATENCIES = deque(maxlen=5)


class Turn:
    """One input, including STT, owns exactly one terminal status."""
    def __init__(self, session, send, started=None):
        self.session = session
        self.send = send
        self.id = uuid4().hex
        self.started = monotonic() if started is None else started
        self.terminal = False
        self.spoken = False

    async def stage(self, stage, **fields):
        if self.terminal:
            return
        if stage == 'thinking' and TURN_LATENCIES:
            fields['typical_ms'] = round(median(TURN_LATENCIES))
        if stage == 'speaking':
            if self.spoken:
                return
            self.spoken = True
            TURN_LATENCIES.append((monotonic() - self.started) * 1000)
        self.terminal = stage in ('done', 'cancelled', 'failed')
        event('turn_' + stage, self.session.session_id,
              ms=(monotonic() - self.started) * 1000)
        await self.send(message('turn', turn_id=self.id, stage=stage,
                                at_ms=int(time() * 1000), **fields))


def say(text, agent='helper'):
    from server.product_rules import check
    return message('say', agent=agent, utterance_id=uuid4().hex, text=check(text))


def state(value, agent='helper', **fields):
    return message('agent_state', agent=agent, state=value, **fields)


def center(obj):
    x, y, w, h = obj['bbox']
    return [min(1, x + w / 2), min(1, y + h / 2)]


def resolve(turn, scene, seq):
    objects = {obj['id']: obj for obj in scene['objects']}
    ops = [dict(kind='clear', id='*')]
    focus = None
    for index, draw in enumerate(turn['draw']):
        obj = objects.get(draw.get('target'))
        if obj is None or draw['kind'] == 'clear':
            continue
        kind = draw['kind']
        origin = objects.get(draw.get('from_target'))
        if 'from_target' in draw and origin is None:
            continue
        at = center(obj)
        op = dict(kind=kind, id=f'draw-{index}', anchor=dict(object_id=obj['id'], bbox=obj['bbox']))
        if 'role' in draw:
            op['role'] = draw['role']
        if kind in ('box', 'highlight'):
            op['bbox'] = obj['bbox']
        elif kind == 'circle':
            op.update(center=at, r=max(obj['bbox'][2:]) / 2)
        elif kind in ('pin', 'label'):
            op['at'] = at
            if kind == 'label':
                op['text'] = draw.get('text') or obj['label'] or 'Object'
                from server.product_rules import allowed
                if not allowed(op['text']):
                    op['text'] = 'Object'
        elif kind == 'arrow':
            op.update({'from': center(origin) if origin else [0.5, 0.95], 'to': at})
        elif kind == 'path':
            x, y, w, h = obj['bbox']
            right, bottom = min(1, x + w), min(1, y + h)
            op['points'] = [[x, y], [right, y], [right, bottom], [x, bottom], [x, y]]
        ops.append(op)
        if focus is None:
            focus = at
    result = message('draw', agent=turn['agent'], seq=seq, ops=ops)
    validate('v2_draw', result)
    return result, focus


_DRAW_KINDS = {'box', 'highlight', 'circle', 'arrow', 'label', 'pin', 'path', 'clear'}
_ROLES = {'target', 'info', 'warn', 'ok'}
_CATEGORIES = {'cpu', 'board', 'ram', 'ssd', 'gpu', 'psu', 'cooler', 'case', 'fan'}


def _one_of(value, allowed):
    # Models sometimes send a list or object where a string belongs; those are unhashable.
    return isinstance(value, str) and value in allowed


_SHOPPING = re.compile(r"\b(cheap\w*|price\w*|cost\w*|buy\w*|deal\w*|afford\w*|budget)\b", re.I)
_CATEGORY_WORDS = [('ram', r'\b(ram|memory|dimm|ddr\d?)\b'), ('ssd', r'\b(ssd|nvme|m\.2|drive|storage)\b'),
                   ('gpu', r'\b(gpu|graphics|video card)\b'), ('cpu', r'\b(cpu|processor)\b'),
                   ('psu', r'\b(psu|power supply)\b'), ('cooler', r'\bcooler\b'), ('case', r'\bcase\b'),
                   ('fan', r'\bfans?\b'), ('board', r'\b(motherboard|mainboard|board)\b')]


def shopping_category(text):
    """Return the part category when the user asks about buying or prices, else None."""
    if not _SHOPPING.search(text or ''):
        return None
    for category, pattern in _CATEGORY_WORDS:
        if re.search(pattern, text, re.I):
            return category
    return None


def sanitize_header(turn, scene_ids=None):
    """Small models bend the format (seen live: role "highlight" or "label", missing draw).
    Keep what is usable instead of throwing the whole turn away."""
    draws = []
    def items(value):
        if isinstance(value, list):
            for item in value:
                yield from items(item)
        elif isinstance(value, dict):
            yield value
            yield from items(value.get('items'))

    for item in items(turn.get('draw')):
        item = dict(item, kind=item.get('kind', item.get('type')))
        if not _one_of(item['kind'], _DRAW_KINDS):
            continue
        clean = {k: item[k] for k in ('kind', 'target', 'from_target', 'text') if isinstance(item.get(k), str)}
        clean['kind'] = item['kind']
        if not clean.get('text'):
            clean.pop('text', None)
        if scene_ids is not None and clean.get('from_target') not in scene_ids:
            clean.pop('from_target', None)
        if _one_of(item.get('role'), _ROLES):
            clean['role'] = item['role']
        draws.append(clean)
    out = {'agent': turn.get('agent') if turn.get('agent') in ('helper', 'shopper') else 'helper', 'draw': draws}
    if _one_of(turn.get('market_category'), _CATEGORIES):
        out['market_category'] = turn['market_category']
    return out


def parse_turn(text):
    start = text.find('{')
    if start < 0:
        raise ValueError('No JSON object')
    turn, _ = json.JSONDecoder().raw_decode(text[start:])
    validate('brain_turn', turn)
    if not turn['say'].strip():
        raise ValueError('Empty speech')
    return turn


_SAY = re.compile(r'\bSAY\s*:', re.I)


def stream_header(value):
    """First balanced object, or a recoverable header before a bent SAY field."""
    start = value.find('{')
    if start < 0:
        return None
    depth, quoted, escaped = 0, False, False
    end = None
    for i in range(start, len(value)):
        char = value[i]
        if escaped:
            escaped = False
        elif char == '\\' and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            depth += (char == '{') - (char == '}')
            if depth == 0:
                end = i + 1
                break
    marker = _SAY.search(value)
    if marker and (end is None or marker.start() < end):
        raw = value[start:marker.start()].rstrip(' \n\t",') + '}'
        rest = value[marker.end():]
    elif end is not None:
        raw, rest = value[start:end], value[end:]
    else:
        return None
    try:
        turn = json.loads(raw)
    except ValueError:
        turn = {}
    return turn, rest


def safe_speech(value):
    value = value.replace('[EXPERT]', '')
    value = _SAY.sub('', value).strip().strip('"` \n\t')
    # Wrapper punctuation from a quoted SAY field is not spoken.
    value = re.sub(r'["}\]\s]+$', '', value).strip()
    if any(c in value for c in '{}[]') or re.search(r'"\s*:', value):
        return ''
    return value


TALKER_RULES = (' You are the TALKER: you speak first and fast, out loud, like a friend leaning over the desk. '
                 'Reply with SAY: then one or two short spoken sentences, nothing else, never JSON. Write for the ear: '
                 'contractions, everyday words, under 20 words a sentence, no lists, no markdown, no symbols, no '
                 'parentheses. Say part names the way people say them ("the CPU fan header"). A planner reads the same '
                 'view and draws on the picture for you; lines from "planner" in the conversation are its notes, trust '
                 'them. Only when you truly need a closer look than the view note gives, say a short natural filler '
                 'such as "One sec, looking." and stop.')
PLANNER_RULES = (' You are the PLANNER behind a fast talker who is already answering out loud. Output only one JSON '
                 'object: agent, draw, optional market_category, note, speak. draw targets and from_target must be '
                 'scene object IDs. note: at most 15 words the talker should say next (the concrete fact, for example '
                 'which header the cable goes in). speak: true only when the note adds a new concrete fact the caption '
                 'alone does not give; false when it would repeat the obvious (for example that the camera is off or '
                 'the view is unclear), and then just draw. Include market_category only when asked about buying, '
                 'prices or cheaper options.')
_STOP = {'the', 'a', 'an', 'it', 'its', "it's", 'is', 'to', 'and', 'so', 'i', 'you', 'me', 'on', 'in', 'of', 'that',
         'this', 'can', "can't", 'cant', 'and', 'or', 'show', 'see', 'be', 'your', 'my', 'turn', 'just', 'now'}


def repeats(note, said):
    """True when most of the note's content words were already spoken this turn (e.g. 'camera is off' twice)."""
    def bag(text):
        return {re.sub(r"'s$", '', w).strip("'") for w in re.findall(r"[a-z0-9']+", text.lower())}
    words = bag(note) - _STOP
    if not words:
        return True
    return len(words & bag(said)) / len(words) >= 0.5


_FILLER = re.compile(r"\b(let me (look|check|see)|one (sec|second|moment)|hang on|looking)\b", re.I)  # filler means the note is spoken



def planner_crop(session, scene, seq, text):
    """Escalation to the cloud planner sees a CROP of the object in question, never the full frame (AGENTS.md).
    Only when the camera is on, the scene belongs to the newest frame, and PLANNER_CROP is not 0."""
    frame = getattr(session, 'frame', None)
    if (os.environ.get('PLANNER_CROP', '1') == '0' or not claude_allowed() or not session.camera
            or not frame or frame.get('seq') != seq or not scene.get('objects')):
        return {}
    target = pick_target(scene['objects'], text)
    try:
        crop = crop_b64(frame['jpeg_b64'], target['bbox']) if target else None
    except Exception:
        crop = None
    return {'images': [crop]} if crop else {}

async def plan_scene(session, planner, base_system, user, scene, seq, text, emit):
    planner_system = base_system + PLANNER_RULES + ' Draw item schema: ' + json.dumps(
        schema()['$defs']['brain_turn']['properties']['draw'])
    raw = ''
    async with aclosing(planner.stream(planner_system, user, max_tokens=400,
                                       model=os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-5-5'),
                                       **planner_crop(session, scene, seq, text))) as stream:
        async for delta in stream:
            raw += delta
    parsed = stream_header(raw)
    head = parsed[0] if parsed else {}
    clean = sanitize_header(head, {obj['id'] for obj in scene['objects']})
    validate('brain_turn', dict(clean, say='plan'))
    if session.camera and clean['draw']:
        await emit(resolve(clean, scene, seq)[0])
    note = head.get('note') if isinstance(head.get('note'), str) else ''
    return clean, safe_speech(note)[:160], head.get('speak') is True

async def run_turn(session, text, *, vision, llm, started_at=None, turn_id=None, planner=None, stt_ms=None,
                   app=None, route=None):
    """One voice turn. With planner set (talker + thinker mode), llm only talks and planner draws in parallel."""
    usage = []
    TURN_USAGE.set(usage)
    brain = {'owner': asyncio.current_task()}
    TURN_BRAIN.set(brain)
    messages = []
    owner = asyncio.current_task()

    async def emit(msg):
        if owner.cancelling():
            raise asyncio.CancelledError
        messages.append(msg)
        if session.emit is not None:
            await session.emit(msg)

    def remember():
        session.history.extend([f'user: {text}'] + [
            f"{m['agent']}: {m['text']}" for m in messages if m['type'] == 'say'])
        session.history[:] = session.history[-10:]

    turn_id = turn_id or uuid4().hex
    async def proof(detail):
        await emit(message('turn', turn_id=turn_id, stage='speaking', detail=detail,
                           at_ms=int(time() * 1000)))

    # Closed-set task selection is deterministic, including the spoken single word "sink".
    if session.pack.id == 'general' and re.fullmatch(r'(?:fix (?:the |a )?)?sink[.!]?', text.strip(), re.I):
        from server.packs.home import HomeGuide
        session.pack = packs.get('home')
        session.home_guide = HomeGuide()
        await proof('Home: sink')
        if app is not None and getattr(app.state, 'presenter', False) and session.private:
            await proof('Presenter available')
    forgetting = bool(re.fullmatch(r'forget my progress[.!]?', text.strip(), re.I))
    if session.ee_guide is not None:
        from server.packs.electronics import ElectronicsGuide, LINES as EE_LINES, refuse as ee_refuse
        from server import recent_memory
        guide = session.ee_guide
        guide.growth = session.growth
        stop = ee_refuse(text) or session.pack.refuse(text)
        # Only a fresh caption of this exact frame may supply an observed stop.
        if (not stop and session.camera and session.frame and session.scene
                and session.scene_seq == session.frame['seq']
                and monotonic() - session.frame_time < pointer.BUDGET_S
                and session.scene.get('source') == 'ollama'):
            from server.packs.electronics import USB
            observed = ee_refuse(session.scene.get('caption', ''))
            if observed == USB:
                guide.usb_off = False
                if guide.task == 'ee_led' and guide.index < 5:
                    stop = observed
            else:
                stop = observed
        if forgetting:
            if session.growth:
                session.growth.forget()
            session.ee_guide = ElectronicsGuide(task=guide.task, growth=session.growth, usb_off=guide.usb_off)
            answer = 'Your progress has been forgotten.'
        elif not stop and (recent_memory.source_for(text) or re.search(
                r'\bwhere did i (put|leave)\b', text, re.I)):
            answer = (await recent_memory.answer(session, text, vision))['say']
        elif stop:
            answer = guide.stop(stop)
        else:
            was_completed = guide.completed
            answer = guide.reply(text)
            if guide.completed and not was_completed:
                await proof('Engineering: complete' if guide.task == 'ee_led' else 'Engineering: practice complete')
        from server.safety import LINES
        if answer in EE_LINES or answer in LINES:
            await proof('Stopped by code')
        if answer:
            msg = say(answer)
            msg.update(turn_id=turn_id, part=0, final=True)
            session.recent_memory.note_step(answer)
            await emit(message('reply_delta', agent='helper', turn_id=turn_id, text=answer))
            await emit(msg)
            if session.speak:
                async for audio in session.speak(msg):
                    await emit(audio)
            if answer.startswith('Step ') and guide.target:
                drawing = await pointer.ground(session, vision, guide.target)
                if drawing:
                    await emit(drawing)
        remember()
        return messages
    if forgetting:
        if session.growth:
            session.growth.forget()
        if session.home_guide:
            from server.packs.home import HomeGuide
            session.home_guide = HomeGuide(task=session.home_guide.task, growth=session.growth)
    refusal = 'Your progress has been forgotten.' if forgetting else session.pack.refuse(text)
    if session.home_guide is not None and not forgetting:
        session.home_guide.growth = session.growth
        was_completed = session.home_guide.completed
        store = None
        if app is not None and hasattr(app.state, 'memories'):
            store, _ = app.state.memories.for_call(session.session_id)
        testing = bool(app is not None and getattr(app.state, 'presenter', False)
                       and session.checker_test_mode)
        fixture = session.checker_test_verdict
        session.checker_test_verdict = None  # every turn, including hazards, consumes it
        if testing:
            await proof('Checker TEST MODE')
        # A previous turn's pass never authorizes this turn, even when repeating a repair.
        session.home_guide.water_off_passed = False
        refusal = session.home_guide.reply(text, store)
        from server.safety import WATER_OFF_LINE
        if refusal == WATER_OFF_LINE and not session.pack.refuse(text):
            from server.step_check import Verdict, configured_checker, RETRY_LINE
            from server.step_check_api import load_references
            await emit(message('turn', turn_id=turn_id, stage='thinking', detail='Valve: checking',
                               at_ms=int(time() * 1000)))
            result = Verdict()
            try:
                if testing:
                    if fixture and monotonic() - fixture[1] <= 15:
                        result = Verdict(fixture[0], 1.0)
                elif session.camera and session.frame:
                    import base64
                    checker = session.step_checker
                    if checker is None:
                        checker = session.step_checker = configured_checker()
                    result = await checker.check(base64.b64decode(session.frame['jpeg_b64'], validate=True),
                                                 *load_references(store), captured_at=session.frame_time)
                if not session.camera and not testing:
                    result = Verdict()
            except Exception:
                result = Verdict()
            # No await between granting the gate and consuming it. Cancellation cannot leave a pass.
            try:
                session.home_guide.water_off_passed = result.passed
                if result.passed:
                    refusal = session.home_guide.reply(text, store)
                else:
                    session.home_guide.stopped(retry=True)
                    refusal = RETRY_LINE
            finally:
                session.home_guide.water_off_passed = False
            await emit(message('turn', turn_id=turn_id, stage='thinking',
                               detail=('Valve: test closed' if testing else 'Valve: closed') if result.passed else 'Valve: unsure',
                               at_ms=int(time() * 1000)))
        from server.safety import LINES
        if session.home_guide.completed and not was_completed and refusal not in LINES:
            await proof('Home: complete')
    if session.home_guide is not None and not refusal:
        # Silence is an intentional guide result, never permission to ask a model.
        remember()
        return messages
    if refusal or (session.pack.requires_power_off and not session.power_off):
        msg = say(refusal or POWER_LINE)
        msg.update(turn_id=turn_id, part=0, final=True)
        from server.safety import LINES
        if refusal in LINES:
            await proof('Stopped by code')
        await emit(message('reply_delta', agent='helper', turn_id=turn_id, text=msg['text']))
        await emit(msg)
        if session.speak:
            async for audio in session.speak(msg):
                await emit(audio)
        remember()
        return messages
    if shopping.INTENT.search(text):
        await emit(state('thinking', 'shopper'))
        shopping_started = monotonic()
        source = getattr(app.state, 'offer_source', None) if app is not None else None
        lookup = asyncio.create_task(shopping.shopping_turn(session, text, vision, source))
        part = 0
        try:
            if claude_allowed() and session.camera and session.frame:
                ack = say('Let me compare the offers.', 'shopper')
                ack.update(turn_id=turn_id, part=0, final=False)
                await emit(ack)
                part = 1
                if session.speak:
                    async for audio in session.speak(ack):
                        await emit(audio)
            result, answer = await lookup
        finally:
            if not lookup.done():
                lookup.cancel()
                await asyncio.gather(lookup, return_exceptions=True)
        if result is not None:
            await emit(message('market_results', agent='shopper', result=result))
        if session.pack.guard:
            answer = session.pack.guard(answer, text, True)
        await emit(state('speaking', 'shopper'))
        msg = say(answer, 'shopper')
        msg.update(turn_id=turn_id, part=part, final=True)
        await emit(msg)
        if session.speak:
            async for audio in session.speak(msg):
                await emit(audio)
        remember()
        await emit(message('metrics', turn_id=turn_id, turn_ms=(monotonic() - shopping_started) * 1000))
        return messages
    await emit(state('thinking'))
    started = started_at if started_at is not None else monotonic()
    vlm_ms = 0
    scene = dict(caption='I do not have a camera view yet.', objects=[], ms=0)
    seq = 0
    fresh = session.camera and monotonic() - session.frame_time <= 3
    if not session.camera:
        scene['caption'] = 'camera is off'
    if fresh and session.scene is None and session.scene_task is not None:
        wait_started = monotonic()
        try:
            await asyncio.wait_for(asyncio.shield(session.scene_task), timeout=0.3)
        except Exception:
            pass
        finally:
            vlm_ms = (monotonic() - wait_started) * 1000
    # Cached grounding avoids putting a slow refresh on the voice critical path.
    if fresh and session.scene is not None:
        scene, seq = session.scene, session.scene_seq
    system = session.pack.system_prompt + (
        " memory lists earlier views, oldest last; only the entry marked now is what the camera shows.")
    duo = planner is not None
    pointing = session.pack.id == 'general'
    if not session.camera:
        system += ' The camera is off. Answer by voice only; do not draw.'
    if session.pack.id == 'pc_build':
        system += board_prompt(session.board)
    base_system = system
    if re.search(r"\b(failed|didn.t work|still not working)\b", text, re.I) and session.expert_step:
        mark_failed(session, session.expert_step)
    system += TALKER_RULES if duo else (
               ' Line 1: {"agent":"helper","draw":[]}. Next line: SAY: then one or two short spoken sentences. '
               'Start with a short first clause of 4 to 8 words, ending in a comma or period.'
               if pointing else ' Line 1: one JSON object with agent, draw and optional market_category. '
               'Do not include say in that object. Next line: SAY: then spoken text. '
               'Start speech with a short first clause of 4 to 8 words, ending in a comma or period. '
               'Draw targets and from_target must be scene object IDs, never coordinates. '
               'Include market_category only when asked about buying, prices or cheaper options. '
               'Draw item schema: ' + json.dumps(schema()['$defs']['brain_turn']['properties']['draw'])
               + ' agent: helper or shopper; market_category: cpu, board, ram, ssd, gpu, psu, cooler, case, fan.')
    system += "\n" + EXPERT_RULES
    if pointing:
        system += pointer.RULES
    user = json.dumps(dict(caption=scene['caption'], objects=[
        {k: obj[k] for k in ('id', 'label', 'bbox')} for obj in scene['objects']],
        memory=session.scene_memory.snapshot(monotonic(), current=bool(fresh and session.scene is not None)),
        conversation=session.history[-10:], text=text))
    planner_task = None

    route = route if route is not None else await route_turn(session, text)   # ws2 may have routed already
    private_expert = route == 'expert' and local_only()
    if private_expert:
        route = 'now'
    if duo and not pointing and route not in ('past', 'expert'):
        planner_task = asyncio.create_task(plan_scene(session, planner, base_system, user, scene, seq, text, emit))
    queue = asyncio.Queue()
    first_audio = None
    first_say_at = None
    tts_ms = None

    async def audio_worker():
        nonlocal first_audio
        while True:
            msg = await queue.get()
            try:
                if msg is None:
                    return
                async for audio in session.speak(msg):
                    await emit(audio)
                    if first_audio is None:
                        first_audio = (monotonic() - started) * 1000
                        tts_ms = (monotonic() - first_say_at) * 1000 if first_say_at else None
            finally:
                queue.task_done()

    worker = asyncio.create_task(audio_worker()) if session.speak else None
    part = 0
    reported_brain = False
    reply_text = ''

    async def speech(value, final=False, agent='helper'):
        nonlocal part, first_say_at, reported_brain, reply_text
        value = safe_speech(value)
        if value and session.pack.guard is not None:
            value = session.pack.guard(value, text, part == 0)  # pack post-condition (memory: pill line, no certainty)
        from server.safety import LINES
        if value in LINES:
            await proof('Stopped by code')
        if not value:
            return
        msg = say(value, agent)
        await emit(message('reply_delta', agent=agent, turn_id=turn_id,
                           text=(' ' if reply_text else '') + msg['text']))
        if 'provider' in brain and not reported_brain:
            # A first token may only contain drawing metadata. Announce speech when a line is ready.
            await proof('Brain: ' + brain['provider'] + ' / ' + brain['model'])
            reported_brain = True
        reply_text += ' ' + msg['text']
        msg.update(turn_id=turn_id, part=part, final=final)
        if first_say_at is None:
            first_say_at = monotonic()
        part += 1
        await emit(msg)
        if worker:
            await queue.put(msg)

    async def tool_first_line(value):
        await speech(value)
        if worker:
            # Wait for this first line on the same ordered audio path as normal speech.
            drained = asyncio.create_task(queue.join())
            try:
                await asyncio.wait((drained, worker), return_when=asyncio.FIRST_COMPLETED)
                if worker.done():
                    await worker
                await drained
            finally:
                drained.cancel()
                await asyncio.gather(drained, return_exceptions=True)

    memory_tools = rewind_tools.memory_tools_for(session, app) if app is not None else []
    # Local memory first (find_item is preferred for objects), then the Rewind recording tools.
    offered_tools = memory_tools + rewind_tools.tools_for(session)
    on_tool = rewind_callback(session, emit, vision=vision, app=app, turn_id=turn_id, speak=tool_first_line)
    tool_options = dict(tools=offered_tools, on_tool=on_tool) if offered_tools else {}

    turn = dict(agent='helper', draw=[])
    buffer = ''
    header = duo
    marker = False
    expert_tag = False
    pending = None
    first_token = None
    brain_started = monotonic()
    target_tag = pointer.TargetTag()
    try:
        if forget_request(text) and getattr(session, 'rewind', None) is not None:
            # Spoken "forget that": the same deletion as the Forget button (TwelveLabs copies and local frames).
            from server.rewind import state_message
            result = await session.rewind.forget(local=True)
            await emit(state_message(session.rewind))
            n = result['deleted']
            await speech(f"Done. I deleted {n} clip{'' if n == 1 else 's'} of this call." if not result['failed']
                         else "I couldn't delete every clip. Try the Forget button.", final=True)
            if worker:
                await queue.put(None)
                await worker
            remember()
            return messages
        if route == 'past' or (route == 'expert' and session.expert_start):
            if route == 'past':
                tool = past_tool(text)
                result = None
                if tool == 'search_past' and memory_tools:
                    # An object question asks this person's local memory first; the recording is the fallback.
                    result = await on_tool('find_item', {'item': text})
                    if not result.get('found'):
                        result = None
                if result is None:
                    result = await on_tool(tool, {'query': text} if tool == 'search_past' else {})
                await speech(result['say'], final=True)
            else:
                await tool_first_line('Let me get the expert on this, one moment.')
                notice = session.expert_start(text, base_system, scene, seq, turn_id, planner)
                if notice:
                    await speech(notice, final=True)
            if worker:
                await queue.put(None)
                await worker
            remember()
            return messages
        if private_expert:
            await speech('Private mode: I will answer with the local model.')
        async with aclosing(llm.stream(system, user, **tool_options)) as stream:
            async for delta in stream:
                if first_token is None:
                    first_token = (monotonic() - brain_started) * 1000
                    if duo:
                        if pointing and session.camera:
                            await emit(message('draw', agent='helper', seq=(session.frame or {}).get('seq', seq),
                                               ops=[dict(kind='clear', id='*')]))
                        await emit(state('speaking'))
                buffer += target_tag.feed(delta) if pointing else delta
                if not header:
                    parsed = stream_header(buffer)
                    if parsed is None:
                        # The local talker sometimes omits its header. Stream plain General
                        # speech immediately, but keep buffering an actual partial JSON header.
                        if not pointing or not buffer.strip() or buffer.lstrip().startswith(('{', '`')):
                            continue
                        raw = {}
                    else:
                        raw, buffer = parsed
                    turn = sanitize_header(raw, {obj['id'] for obj in scene['objects']})
                    if pointing:
                        turn['draw'] = []  # General pointers must pass fresh local grounding below.
                    validate('brain_turn', dict(turn, say='stream'))
                    draw_seq = (session.frame or {}).get('seq', seq) if pointing else seq
                    drawing, focus = resolve(turn, scene, draw_seq)
                    if session.camera:
                        await emit(drawing)
                    await emit(state('speaking', turn['agent'], **({'focus': focus} if focus else {})))
                    header = True
                if not marker:
                    buffer = buffer.lstrip(' \n\t"')
                    if len(buffer) < 4:
                        continue
                    match = _SAY.search(buffer)
                    if match:
                        buffer = buffer[match.end():].lstrip(' \n\t"')
                    if '[EXPERT]'.startswith(buffer) and len(buffer) < len('[EXPERT]'):
                        continue
                    expert_tag = buffer.startswith('[EXPERT]')
                    if expert_tag:
                        buffer = buffer[len('[EXPERT]'):].lstrip()
                    marker = True
                while True:
                    match = re.search(r'[.!?\n]', buffer)
                    if part == 0:
                        clause = next((m for m in re.finditer(r'[,;]', buffer)
                                       if len(buffer[:m.end()].split()) >= 4), None)
                        if clause and (match is None or clause.end() < match.end()):
                            match = clause
                    end = match.end() if match and match.end() <= 120 else (120 if len(buffer) >= 120 else 0)
                    if not end:
                        break
                    sentence, buffer = safe_speech(buffer[:end]), buffer[end:]
                    if sentence.startswith('SAY:'):
                        sentence = sentence[4:].strip()
                    # Seen live: '}, SAY: ...' left a leading ', ' on the first spoken chunk.
                    sentence = sentence.lstrip(' ,;:').strip()
                    if sentence.startswith(('{', '[', '"')) or '"kind"' in sentence:
                        continue  # never speak JSON

                    if sentence:
                        if pending:
                            await speech(pending, agent=turn['agent'])
                        if part == 0 and len(sentence.split()) >= 4:
                            await speech(sentence, agent=turn['agent'])
                            pending = None
                        else:
                            pending = sentence
                # Once more spoken text exists, the preceding sentence cannot be final.
                if pending and buffer.strip():
                    await speech(pending, agent=turn['agent'])
                    pending = None
        if not header:
            turn = dict(agent='helper', draw=[])
            if session.camera:
                await emit(resolve(turn, scene, seq)[0])
            await emit(state('speaking'))
        tail = safe_speech(buffer)
        if tail:
            if pending:
                await speech(pending, agent=turn['agent'])
            pending = tail
        # Hard rule in code: a shopping question always brings in the shopper, even if the model forgot.
        if not turn.get('market_category'):
            category = shopping_category(text)
            if category:
                turn['market_category'] = category
        if pending or part == 0:
            await speech(pending or 'Please tell me what you need.',
                         final=not bool(turn.get('market_category')), agent=turn['agent'])
        if pointing:
            pointer_started = monotonic()
            drawing = await pointer.ground(session, vision, target_tag.target(reply_text))
            if drawing:
                await emit(drawing)
            event('pointer', session.session_id, ms=(monotonic() - pointer_started) * 1000,
                  reason='drawn' if drawing else 'abstained')
        if planner_task is not None:
            # The thinker's note is spoken only when it adds a fact, or when the talker stalled with a filler.
            planned_at = monotonic()
            try:
                planned, note, speak_note = await asyncio.wait_for(
                    asyncio.shield(planner_task), float(os.environ.get('PLANNER_WAIT_S', '5')))
            except Exception:
                planned, note, speak_note = None, '', False
            stalled = bool(_FILLER.search(reply_text))
            event('planner', session.session_id, ms=(monotonic() - planned_at) * 1000)
            if planned and planned.get('market_category') and not turn.get('market_category'):
                turn['market_category'] = planned['market_category']
            # Repeating the talker is worse than silence: then the planner only draws.
            if note and (speak_note or stalled) and not repeats(note, reply_text):
                session.history.append(f'planner: {note}')
                await speech(note, final=not bool(turn.get('market_category')))
        if turn.get('market_category') and not claude_allowed():
            await speech('Shopping needs the Claude cloud switch enabled.', final=True, agent='shopper')
        elif turn.get('market_category'):
            await emit(state('thinking', 'shopper'))
            await speech('Let me check prices that fit.', agent='shopper')
            query = dict(v=1, category=turn['market_category'], owned_parts=[dict(
                category='board', model=session.board['model'], specs=session.board.get('specs', {}),
                confirmed=True, source='fixture')])
            try:
                result = await asyncio.to_thread(search, query)
                validate('market_response', result)
                await emit(message('market_results', agent='shopper', result=result))
                fits = [item for item in result['items'] if item['compat']['status'] == 'fits']
                cheapest = min(fits, key=lambda i: i['price_usd'] + i.get('shipping_usd', 0)) if fits else None
                answer = (f"{cheapest['title']} is the cheapest confirmed fit at "
                          f"${cheapest['price_usd'] + cheapest.get('shipping_usd', 0):.2f} including shipping."
                          if cheapest else "I couldn't confirm anything fits.")
            except Exception:
                answer = "I couldn't confirm anything fits."
            await emit(state('speaking', 'shopper'))
            await speech(answer, final=True, agent='shopper')
        if worker:
            await queue.put(None)
            await worker
    finally:
        if planner_task is not None and not planner_task.done():
            planner_task.cancel()
            await asyncio.gather(planner_task, return_exceptions=True)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    if not local_only() and session.expert_start and should_escalate(session, text, expert_tag):
        notice = session.expert_start(text, base_system, scene, seq, turn_id, planner)
        if notice or not expert_tag:
            msg = say(notice or "Let me get the expert on this, one moment.")
            msg.update(turn_id=turn_id, part=part, final=True)
            await emit(msg)
            if session.speak:
                async for audio in session.speak(msg):
                    await emit(audio)
    if not re.search(r"\b(failed|didn.t work|still not working)\b", text, re.I):
        session.expert_step = next((m['text'] for m in reversed(messages) if m['type'] == 'say'), session.expert_step)
    remember()
    timings = dict(turn_id=turn_id, vlm_ms=vlm_ms,
                   brain_first_token_ms=first_token or 0, turn_ms=(monotonic() - started) * 1000)
    if first_audio is not None:
        timings['first_audio_ms'] = first_audio
    if tts_ms is not None:
        timings['tts_ms'] = tts_ms
    if stt_ms is not None:
        timings['stt_ms'] = stt_ms
    await emit(message('metrics', **timings))
    return messages
