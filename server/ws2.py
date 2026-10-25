"""Independent v2 socket lifecycle, latest-turn cancellation and streaming speech."""
import asyncio
import base64
import json
import hashlib
import os
import io
import re
from contextlib import aclosing, asynccontextmanager, nullcontext
from datetime import datetime, timezone

from anyio import CancelScope
from PIL import Image
from time import monotonic, time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import httpx
from server import cloud_meter, cloud_vision
from jsonschema import ValidationError

from contracts import validate
from server.box_client import BoxClient
from server.auth import accept_socket
from server.logs import event
from server.keyframes import signature
from server import packs, pairing
from server import rewind as rewind_mod
from server import recent_memory
from server.routing import forget_request, route_turn
from server.agent import Session, Turn, message, run_turn, say, state
from server.llm import LLMClient, TURN_USAGE
from server import privacy
from server.gemini_live import LiveBridge, FALLBACK_LINE
from server.privacy import claude_allowed
from server.expert import DESK as expert_desk, cancel as cancel_expert
from voice import adapter
from voice.fake import FakeAdapter

router = APIRouter()

PARTIAL_EVERY_BYTES = int(16000 * 2 * 0.3)  # a partial transcript per 300 ms of new speech
PARTIAL_MIN_BYTES = int(16000 * 2 * 0.5)
PARTIAL_WAIT_S = 0.6          # at the final, wait this long for an in-flight partial
TAIL_MAX_BYTES = int(16000 * 2 * 0.9)


_FILLER_WORDS = {'uh', 'um', 'umm', 'hmm', 'mm', 'mhm', 'ah', 'oh', 'er', 'erm', 'huh', 'you', 'the', 'a', 'and',
                 'so', 'okay', 'ok', 'like', 'i'}
_NOISE_PHRASES = {'thank you', 'thanks for watching', 'thank you for watching', 'bye', 'you', 'thank you very much'}
_ONE_WORD = {'yes', 'no', 'yeah', 'yep', 'nope', 'help', 'next', 'stop', 'done', 'wait', 'again', 'hello', 'hi', 'sink'}


def meaningful(text):
    """Real words worth a turn: two or more non-filler words, or one known command word."""
    words = re.findall(r"[a-z0-9']+", (text or '').lower())
    if not words or ' '.join(words) in _NOISE_PHRASES:
        return False
    if len(words) == 1:
        return words[0] in _ONE_WORD
    return not all(word in _FILLER_WORDS for word in words)


# Budget per part of a voice turn; the status strip names the part that went over.
SLOW_BUDGET_MS = (('stt_ms', 'speech recognition', 500), ('brain_first_token_ms', 'brain', 900),
                  ('tts_ms', 'voice', 500))


def slow_part(metrics):
    """'Slow: brain 1.3 s' for the part furthest over its budget, or '' when every part is in budget."""
    worst, over = '', 0
    for key, name, budget in SLOW_BUDGET_MS:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and value - budget > over:
            worst, over = f'Slow: {name} {value / 1000:.1f} s', value - budget
    return worst


def cloud_brain():
    return (bool(os.environ.get('ANTHROPIC_API_KEY')) and claude_allowed()
            and os.environ.get('LLM_PROVIDER', 'auto') in ('auto', 'claude'))


def quiet_tail(pcm, upto, ratio=0.3):
    """True when the audio after the last partial is short and much quieter than the speech before it."""
    if upto <= 0 or len(pcm) - upto > TAIL_MAX_BYTES:
        return False
    from array import array
    def rms(data):
        samples = array('h', data[:len(data) - len(data) % 2])
        return (sum(x * x for x in samples) / len(samples)) ** 0.5 if samples else 0.0
    tail = pcm[upto:]
    if not tail:
        return True
    return rms(tail) <= max(200.0, rms(pcm[:upto]) * ratio)


class GPUGate:
    """Brains bypass an existing scene; new scenes wait for all reserved turns."""
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = False
        self.brain_active = False
        self.brains = 0

    @asynccontextmanager
    async def hold(self, brain=False):
        async with self.condition:
            self.brains += int(brain)
            try:
                await self.condition.wait_for(
                    lambda: not self.brain_active if brain else not self.active and not self.brains)
                if brain:
                    self.brain_active = True
                else:
                    self.active = True
            except BaseException:
                self.brains -= int(brain)
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            async with self.condition:
                if brain:
                    self.brain_active = False
                    self.brains -= 1
                else:
                    self.active = False
                self.condition.notify_all()


def gpu_gate(app):
    if not hasattr(app.state, 'gpu_gate'):
        app.state.gpu_gate = GPUGate()
    return app.state.gpu_gate


class SceneClient:
    def __init__(self, url=None, transport=None):
        self.url = url or os.environ.get('VISION_URL', 'http://127.0.0.1:8001')
        self.transport = transport

    async def call(self, operation, payload):
        validate('scene_request', payload)
        if cloud_vision.enabled():
            return await cloud_vision.call(operation, payload, getattr(self, 'gate', None))
        async with httpx.AsyncClient(base_url=self.url, timeout=25, transport=self.transport) as client:
            response = await client.post('/scene', json=payload, headers=cloud_vision.request_headers())
            cloud_vision.record_usage(response)
            response.raise_for_status()
            scene = response.json()
        validate('scene_response', scene)
        return scene


async def scene_loop(session, vision, send, gate=None):
    """Select changed frames and heartbeats; serialize scans behind reserved turns."""
    gate = gate or GPUGate()
    keyframes = session.keyframe_gate
    previous_seq = None
    while True:
        session.recent_memory.prune()
        frame = session.frame
        requested = session.refresh_requested.is_set()
        if (not session.camera or session.voice_pending or frame is None
                or (frame['seq'] == previous_seq and not requested)):
            await asyncio.sleep(0.01)
            continue
        async with nullcontext() if cloud_vision.enabled() else gate.hold():
            if not session.camera or session.voice_pending or session.frame is None:
                continue
            frame = session.frame
            observed_at = session.frame_time
            requested = session.refresh_requested.is_set()
            session.refresh_requested.clear()
            if frame['seq'] == previous_seq and not (
                    requested and monotonic() - session.scene_time >= keyframes.heartbeat_s):
                continue
            previous_seq = frame['seq']
            try:
                # Decode/resize off the event loop, including larger camera frames.
                sig = await asyncio.to_thread(signature, frame['jpeg_b64'])
            except (ValueError, OSError):
                session.scene_skips += 1
                continue
            # A turn or camera change can arrive during the Pillow worker.
            if not session.camera or session.voice_pending or (gate.brains and not cloud_vision.enabled()):
                previous_seq = None
                continue
            scan, reason = keyframes.should_scan(sig, monotonic())
            if getattr(session, 'rewind', None):
                # Rewind cuts clips on local activity (a hand, an object put down), not only on scene cuts.
                session.rewind.observe(sig, reason if scan else None)
            if not scan:
                session.scene_skips += 1
                continue
            started = monotonic()
            session.scene_scans += 1

            async def refresh():
                try:
                    scene = await vision.call('scene', dict(image_b64=frame['jpeg_b64'],
                                                           queries=list(session.pack.default_queries)[:2], caption=True))
                    validate('scene_response', scene)
                    if not session.camera:
                        return
                    session.scene = scene
                    session.scene_seq = frame['seq']
                    session.scene_time = monotonic()
                    session.scene_memory.add(scene, observed_at)
                    session.recent_memory.add(scene, observed_at)
                    await send(message('track', seq=frame['seq'], objects=scene['objects']))
                except Exception:
                    event('dependency_error', session.session_id, dependency='vision', level='error')
                    if not session.vision_error_sent:
                        await send(message('error', code='vision_unavailable', message='Vision is unavailable.'))
                        session.vision_error_sent = True
                finally:
                    elapsed = (monotonic() - started) * 1000
                    # Fixed metadata only: the shared logger does not allow these scene fields.
                    print(json.dumps(dict(ts=datetime.now(timezone.utc).isoformat(),
                        level='info', event='scene_scan',
                        session=hashlib.sha256(session.session_id.encode()).hexdigest()[:12],
                        seq=frame['seq'], reason=reason, scans=session.scene_scans,
                        skips=session.scene_skips, vlm_ms=elapsed)), flush=True)
                await send(message('metrics', seq=frame['seq'], vlm_ms=elapsed))

            session.scene_task = asyncio.create_task(refresh())
            try:
                await session.scene_task
            finally:
                session.scene_task = None
        frame = None


@router.websocket('/ws2')
async def websocket(ws: WebSocket):
    if 'join' in ws.query_params:
        await pairing.join_socket(ws)
        return
    if not await accept_socket(ws):
        return
    session = None
    budget_task = None
    hello_ready = False
    paired_call = None
    turn_task = None
    current_turn = None
    pending_turn = None
    audio_tasks = {}
    scene_loop_task = None
    voice = None
    active_audio = set()
    stt_mode = 'browser'
    stt_failed = False
    live = None
    audio_buffer = bytearray()
    stt_task = None
    # Incremental STT: partial transcripts of the growing utterance while the user talks, so the final
    # can reuse the last partial when everything after it is quiet (the VAD hang or a release pause).
    partial = dict(gen=0, task=None, upto=0, text='')
    yolo_task = None
    rewind_task = None
    box = getattr(ws.app.state, 'box_v2', None) or BoxClient()
    gate = gpu_gate(ws.app)
    vision = getattr(ws.app.state, 'vision_v2', None) or SceneClient()
    if isinstance(vision, SceneClient):
        vision.gate = gate
    llm = getattr(ws.app.state, 'llm_v2', None) or LLMClient()
    send_lock = asyncio.Lock()

    async def send(msg):
        validate('server_message_v2', msg)
        if (session and session.ee_guide is None and msg['type'] == 'say' and msg.get('agent') == 'helper'
                and not (live and live.active)):
            session.recent_memory.note_step(msg['text'])
        if msg['type'] == 'metrics' and 'turn_id' in msg:
            event('turn_end', session.session_id if session else None,
                  cache_read_tokens=sum(x['cache_read_tokens'] for x in (TURN_USAGE.get() or [])),
                  **{k: v for k, v in msg.items() if k.endswith('_ms')})
        async with send_lock:
            await ws.send_json(msg)

    async def speech_audio(msg):
        active_audio.add(msg['utterance_id'])
        try:
            voice.turn = msg.get('turn_id')  # Cartesia keeps one context (prosody) per turn
            pending = None
            try:
                async for chunk in voice.say(msg['utterance_id'], msg['text']):
                    if pending is not None:
                        yield message('audio', agent=msg['agent'], utterance_id=msg['utterance_id'],
                                      sample_rate=16000, pcm16_b64=base64.b64encode(pending).decode(), final=False)
                    pending = chunk
            except httpx.HTTPError as exc:
                # A failed "Let me think back" must not abort the lookup and final text answer.
                event('dependency_error', session.session_id, dependency='tts', error=type(exc).__name__)
                if msg.get('turn_id'):
                    await send(message('turn', turn_id=msg['turn_id'], stage='speaking',
                                       at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                                       detail=f'Voice unavailable ({type(exc).__name__})'))
            yield message('audio', agent=msg['agent'], utterance_id=msg['utterance_id'],
                          sample_rate=16000, pcm16_b64=base64.b64encode(pending or b'').decode(), final=True)
        finally:
            active_audio.discard(msg['utterance_id'])
            voice.stop(msg['utterance_id'])

    async def audio(msg):
        try:
            async for chunk in speech_audio(msg):
                await send(chunk)
        finally:
            audio_tasks.pop(msg['utterance_id'], None)

    async def emit(msg):
        if (msg['type'] == 'turn' and msg['stage'] == 'speaking' and current_turn
                and msg['turn_id'] == current_turn.id and not current_turn.spoken):
            # Proof details accompany the first speech transition; do not announce it again at say.
            await current_turn.stage('speaking', detail=msg['detail'])
            return
        if msg['type'] == 'say' and current_turn and not current_turn.terminal:
            msg.setdefault('turn_id', current_turn.id)
            await current_turn.stage('speaking')
        await send(msg)
        if msg['type'] == 'say' and 'turn_id' not in msg and session.speak:
            audio_tasks[msg['utterance_id']] = asyncio.create_task(audio(msg))

    async def cancel_scene():
        nonlocal scene_loop_task
        if scene_loop_task:
            scene_loop_task.cancel()
            await asyncio.gather(scene_loop_task, return_exceptions=True)
            scene_loop_task = None

    async def cancel_turn():
        nonlocal turn_task
        if turn_task is not None:
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)
            turn_task = None
        if current_turn:
            await current_turn.stage('cancelled')

    async def cancel_audio(utterance=None):
        selected = [(key, task) for key, task in list(audio_tasks.items())
                    if utterance is None or utterance == key]
        for key, task in selected:
            task.cancel()
            if voice is not None:
                voice.stop(key)
        await asyncio.gather(*(task for _, task in selected), return_exceptions=True)

    class TurnBrain:
        async def stream(self, *args, **kwargs):
            try:
                # Only a local brain needs the GPU; a Claude turn must not hold the scene feed back.
                hold = gate.hold(brain=True) if not cloud_brain() else nullcontext()
                async with hold:
                    options = {}
                    if isinstance(llm, LLMClient):
                        async def backup():
                            await current_turn.stage('backup', detail='brain slow, trying backup')
                        options['on_backup'] = backup
                    async with aclosing(llm.stream(*args, **options, **kwargs)) as stream:
                        async for delta in stream:
                            yield delta
            finally:
                if not asyncio.current_task().cancelling():
                    session.voice_pending = False

    async def turn(text, lifecycle):
        event('turn_start', session.session_id)
        try:
            await lifecycle.stage('thinking', text=text)
            # Talker + thinker: a Haiku-class talker speaks while the planner (no GPU gate) draws in parallel.
            duo = (isinstance(llm, LLMClient) and os.environ.get('BRAIN_MODE', 'duo') == 'duo'
                   and bool(os.environ.get('ANTHROPIC_API_KEY')) and claude_allowed())
            # ROUTER=on: the router decides first (and logs its label); the regex is the fallback only when the
            # router is off or unsure. Past/object questions and spoken forget go to memory and Rewind.
            home_turn = session.home_guide is not None or (session.pack.id == 'general' and
                re.fullmatch(r'(?:fix (?:the |a )?)?sink[.!]?', text.strip(), re.I))
            fixed_turn = home_turn or session.ee_guide is not None or bool(session.pack.refuse(text)) or bool(
                re.fullmatch(r'forget my progress[.!]?', text.strip(), re.I))
            temporal = recent_memory.source_for(text, bool(getattr(session, 'rewind', None) and session.rewind.on))
            if not fixed_turn and not forget_request(text) and temporal:
                await recent_memory.run_turn(session, text, lifecycle.id, vision)
                await lifecycle.stage('done')
                return
            label = (await route_turn(session, text)
                     if not fixed_turn and os.environ.get('ROUTER', 'off') == 'on' else None)
            memory_or_rewind = not fixed_turn and rewind_mod.wants_rewind(session, text)
            if fixed_turn:
                use_rewind_turn = False
            elif forget_request(text):
                # With memory attached its forget also clears this call's clips; without it run_turn's spoken
                # forget deletes the Rewind clips (the plain rehearsal failed 5 of 5 when this went to rewind_turn).
                use_rewind_turn = bool(rewind_mod.memory_turn_kind(session, text))
            elif label == 'past':
                use_rewind_turn = memory_or_rewind or getattr(session, 'rewind', None) is not None
            else:
                use_rewind_turn = label in (None, 'unsure') and memory_or_rewind
            if use_rewind_turn:
                await rewind_mod.rewind_turn(session, text, session.rewind, vision=vision, turn_id=lifecycle.id)
            else:
                messages = await run_turn(session, text, vision=vision, llm=TurnBrain(),
                                          started_at=lifecycle.started, turn_id=lifecycle.id,
                                          planner=llm if duo else None, stt_ms=getattr(lifecycle, 'stt_ms', None),
                                          app=ws.app, route=label)
                slow = slow_part(next((m for m in reversed(messages or []) if m['type'] == 'metrics'), {}))
                if slow:
                    await lifecycle.stage('done', detail=slow)
                    return
            await lifecycle.stage('done')
        except asyncio.CancelledError:
            await lifecycle.stage('cancelled')
            raise
        except Exception as exc:
            # Class name and the innermost frame only: never message text, which could hold user words.
            tb = exc.__traceback__
            while tb and tb.tb_next:
                tb = tb.tb_next
            where = f'{tb.tb_frame.f_code.co_filename.rsplit("/", 1)[-1]}:{tb.tb_lineno}' if tb else ''
            event('dependency_error', session.session_id, dependency='turn', level='error',
                  error=type(exc).__name__, where=where)
            await lifecycle.stage('failed', detail='Brain failed')
        finally:
            if not asyncio.current_task().cancelling():
                session.voice_pending = False

    async def cancel_stt():
        nonlocal stt_task
        if stt_task:
            stt_task.cancel()
            await asyncio.gather(stt_task, return_exceptions=True)
            stt_task = None

    async def start_text(text, lifecycle):
        nonlocal turn_task
        if re.fullmatch(r'\s*stop[.!]?\s*', text, re.I):
            await cancel_expert(session)
        session.expert_start = lambda question, system, scene, seq, turn_id, planner: expert_desk.start(
            session, question, system, scene, seq, llm, send, turn_id, planner, vision=vision, app=ws.app)
        session.voice_pending = True
        turn_task = asyncio.create_task(turn(text, lifecycle))

    def reset_audio():
        audio_buffer.clear()
        partial['gen'] += 1
        task = partial['task']
        if task is not None and not task.done():
            task.cancel()
        partial.update(task=None, upto=0, text='')

    async def run_partial(snapshot, gen):
        try:
            text = await box.stt(snapshot)
        except Exception:
            return
        if gen == partial['gen'] and len(snapshot) > partial['upto']:
            partial.update(upto=len(snapshot), text=text)

    def maybe_partial():
        if partial['task'] is not None and not partial['task'].done():
            return
        if len(audio_buffer) < PARTIAL_MIN_BYTES or len(audio_buffer) - partial['upto'] < PARTIAL_EVERY_BYTES:
            return
        partial['task'] = asyncio.create_task(run_partial(bytes(audio_buffer), partial['gen']))

    async def transcribe(pcm, lifecycle, guess=None):
        nonlocal stt_failed, stt_mode, current_turn
        try:
            await lifecycle.stage('transcribing')
            text = None
            if guess is not None:
                task, gen = guess
                if task is not None and not task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(task), PARTIAL_WAIT_S)
                    except Exception:
                        pass
                if gen == partial['gen'] and partial['text'].strip() and quiet_tail(pcm, partial['upto']):
                    text = partial['text']
                    event('stt_partial_used', session.session_id, ms=(len(pcm) - partial['upto']) / 32)
                # The next utterance starts its own partials.
                partial['gen'] += 1
                partial.update(task=None, upto=0, text='')
            if text is None:
                text = await box.stt(pcm)
            from server.family_note import store as family_store
            family_owner = rewind_mod.identity(ws) + ':' + session.session_id
            if family_store(ws).consume_spoken(family_owner, text):
                session.voice_pending = False
                await lifecycle.stage('done')
                return
            if not meaningful(text):
                # Silent: no "Didn't catch that", and the reply that is speaking is left alone.
                event('stt_noise', session.session_id, ms=len(pcm) / 32)
                session.voice_pending = False
                await lifecycle.stage('done')
                return
            await cancel_turn()
            await cancel_audio()
            current_turn = lifecycle
            lifecycle.stt_ms = (monotonic() - lifecycle.started) * 1000
            await start_text(text, lifecycle)
        except asyncio.CancelledError:
            await lifecycle.stage('cancelled')
            raise
        except Exception:
            session.voice_pending = False
            from server.family_note import store as family_store
            family_store(ws).consume_spoken(rewind_mod.identity(ws) + ':' + session.session_id, '')
            await lifecycle.stage('failed', detail="Didn't catch that")
            stt_mode = 'browser'
            event('fallback_used', session.session_id, dependency='stt', to='browser')
            if not stt_failed:
                stt_failed = True
                await send(message('error', code='internal',
                                   message='Server speech is unavailable; using the browser.'))

    async def close_voice():
        if voice is not None and hasattr(voice, 'aclose'):
            await voice.aclose()

    async def close_live():
        nonlocal live
        if live:
            await live.close()
            live = None

    async def live_fixed_line(text):
        # A fresh caption cancels the previous client utterance; normal server TTS speaks the fixed line.
        await cancel_audio()
        await emit(say(text))

    async def open_live():
        nonlocal live
        await close_live()
        factory = getattr(ws.app.state, 'live_factory_v2', LiveBridge)
        live = factory(session, vision, ws.app, send, live_fixed_line)
        if not await live.start() and not cloud_meter.BUDGET.exhausted():
            await live_fixed_line(FALLBACK_LINE)

    async def watch_budget():
        nonlocal voice
        while not hello_ready or not cloud_meter.BUDGET.exhausted():
            await asyncio.sleep(0.25)
        await close_live()
        await cancel_turn()
        await cancel_expert(session)
        await cancel_audio()
        await close_voice()
        candidate = await asyncio.to_thread(adapter.make_adapter)
        voice = None if isinstance(candidate, FakeAdapter) else candidate
        session.speak = speech_audio if voice else None
        notice = say(cloud_meter.CAP_LINE)
        await send(notice)
        if voice:
            audio_tasks[notice['utterance_id']] = asyncio.create_task(audio(notice))

    async def end_rewind():
        nonlocal rewind_task
        if rewind_task:
            rewind_task.cancel()
            await asyncio.gather(rewind_task, return_exceptions=True)
            rewind_task = None
        if session is not None:
            session.recent_memory.clear()
            session.scene_memory.entries.clear()
        if session is not None and getattr(session, 'rewind', None):
            rewind_mod.registry(ws.app).end(session.session_id)   # uploads the rest after the call

    async def cancel_yolo():
        nonlocal yolo_task
        if yolo_task:
            yolo_task.cancel()
            await asyncio.gather(yolo_task, return_exceptions=True)
            yolo_task = None

    async def switch_camera(on, reset=False):
        nonlocal scene_loop_task, yolo_task
        session.camera = on
        if reset or not on:
            await cancel_scene()
            await cancel_yolo()
            session.frame = session.scene = None
            session.keyframe_gate.previous = None
            session.scene_seq = 0
        if on:
            if scene_loop_task is None:
                scene_loop_task = asyncio.create_task(scene_loop(session, vision, send, gate))
            if yolo_task is None and os.environ.get('YOLO_REFRESH') == '1':
                yolo_task = asyncio.create_task(yolo_loop(session, box, send))

    ws_read = phone_read = None

    async def next_message():
        """The call socket's next message, or phone mic audio from the paired camera socket (pairing.Call.inbox)."""
        nonlocal ws_read, phone_read, stt_mode
        if ws_read is None:
            ws_read = asyncio.ensure_future(ws.receive_json())
        if paired_call and (phone_read is None or phone_read.inbox is not paired_call.inbox):
            if phone_read is not None:
                phone_read.cancel()
            phone_read = asyncio.ensure_future(paired_call.inbox.get())
            phone_read.inbox = paired_call.inbox
        await asyncio.wait({ws_read} | ({phone_read} if phone_read else set()), return_when=asyncio.FIRST_COMPLETED)
        if phone_read is not None and phone_read.done():
            done, phone_read = phone_read, None
            stt_mode = 'server'  # phone speech is always streamed PCM for server STT
            return done.result()
        done, ws_read = ws_read, None
        return done.result()

    budget_task = asyncio.create_task(watch_budget())
    try:
        while True:
            try:
                incoming = await next_message()
                validate('client_message_v2', incoming)
            except (json.JSONDecodeError, ValidationError, KeyError):
                await send(message('error', code='bad_message', message='Invalid client message.'))
                continue
            received_at = monotonic()
            kind = incoming['type']
            if kind == 'hello':
                if incoming.get('role') == 'camera':
                    if session is not None:
                        await send(message('error', code='bad_message', message='Open a new socket for the camera.'))
                        continue
                    await pairing.camera_socket(ws, incoming)
                    return
                try:
                    pack_id = ('engineering' if ws.query_params.get('mode') == 'engineering'
                               and incoming['pack'] == 'general' else incoming['pack'])
                    new_session = Session(incoming['session_id'], packs.get(pack_id),
                                          incoming.get('board_id', 'demo-b650'), incoming.get('tts', 'browser'))
                except (OSError, ValueError, ValidationError, KeyError):
                    await send(message('error', code='bad_message', message='Unknown or invalid board or pack.'))
                    continue
                await close_live()
                await cancel_expert(session)
                await cancel_stt()
                await cancel_turn()
                await cancel_audio()
                await cancel_scene()
                await cancel_yolo()
                await close_voice()
                await end_rewind()
                reset_audio()
                # Private mode (hello.private): local brain, Kokoro, server STT (browser STT may send audio to
                # Google), no crops, no Rewind cloud. Every task started from here on inherits it.
                # Repeated hello changes settings; it must not erase this call's evidence.
                old_call = rewind_mod.registry(ws.app).get(new_session.session_id, rewind_mod.identity(ws))
                meter = getattr(old_call, 'cloud_meter', None) or cloud_meter.Meter()
                cloud_meter.CURRENT.set(meter)
                private = bool(incoming.get('private', False))
                new_session.private = private
                # Home first: this call's cloud switches (hello.cloud; Private mode or LOCAL_ONLY=1 = all off).
                switches = incoming.get('cloud')
                # Query settings keep the frozen hello schema unchanged. Explicit Google consent only.
                if ws.query_params.get('mode') == 'gemini-live':
                    switches = dict(switches or {}, google=ws.query_params.get('google') == '1')
                privacy.set_call(switches, private=private, rewind=incoming.get('rewind', False))
                stt_mode = 'server' if private else incoming.get('stt', 'browser')
                if private:
                    new_session.tts = 'server'
                stt_failed = False
                if paired_call:
                    await paired_call.close()
                session = new_session
                session.camera = incoming.get('camera', True)
                paired_call = pairing.Call(session, send, switch_camera, ident=pairing.verified_identity(ws))
                from server.limits import owner_key
                paired_call.limit_owner = owner_key(ws)
                current_turn = None
                event('hello', session.session_id, pack=session.pack.id, tts=session.tts, stt=stt_mode)
                session.emit = emit
                voice = None
                if session.tts == 'server':
                    factory = getattr(ws.app.state, 'voice_factory_v2', adapter.make_adapter)
                    picked = incoming.get('voice') or new_session.pack.voice or None
                    candidate = await asyncio.to_thread(
                        factory, **({'voice': picked} if factory is adapter.make_adapter else {}))
                    if not isinstance(candidate, FakeAdapter):
                        voice = candidate
                        voice.voice = picked  # schema-checked id; the adapter checks its own kind
                        if hasattr(voice, 'warm'):
                            await voice.warm()
                    else:
                        event('fallback_used', session.session_id, dependency='tts', to='browser')
                session.speak = speech_audio if voice else None
                scene_loop_task = asyncio.create_task(scene_loop(session, vision, send, gate))
                if os.environ.get('YOLO_REFRESH') == '1':
                    yolo_task = asyncio.create_task(yolo_loop(session, box, send))
                session.rewind = rewind_mod.registry(ws.app).open(
                    session.session_id, on=incoming.get('rewind', False), owner=rewind_mod.identity(ws))
                session.rewind.cloud_meter = meter
                session.rewind.session = session
                if getattr(ws.app.state, 'presenter', False) and private:
                    seed = getattr(ws.app.state, 'presenter_seed', None)
                    if seed:
                        seed(session.session_id, rewind_mod.identity(ws))
                from server.growth import for_owner as growth_for_owner
                session.growth = growth_for_owner(ws.app, rewind_mod.identity(ws))
                rewind_task = asyncio.create_task(session.rewind.follow(session))
                if session.rewind.on:
                    await send(rewind_mod.state_message(session.rewind))
                if ws.query_params.get('mode') == 'gemini-live':
                    stt_mode = 'server'
                    await open_live()
                hello_ready = True
                await send(state('listening'))
                if getattr(ws.app.state, 'presenter', False) and private and session.pack.id == 'home':
                    await send(message('turn', turn_id='presenter', stage='done', detail='Presenter available',
                                       at_ms=int(time() * 1000)))
                continue
            if session is None or incoming['session_id'] != session.session_id:
                await send(message('error', code='bad_message', message='Send hello with the matching session first.'))
                continue
            if kind == 'pair_request':
                try:
                    if incoming.get('kick'):
                        await paired_call.kick()
                        continue
                    await send(pairing.registry.issue(paired_call))
                except ValueError as exc:
                    await send(message('error', code='internal', message=str(exc)))
                continue
            if kind == 'camera':
                paired_call.local_on = incoming['on']
            if kind in ('camera', 'frame') and paired_call.camera is not None:
                continue
            if live and live.active and kind in ('audio_in', 'user_text', 'barge_in'):
                if kind == 'audio_in':
                    try:
                        await live.audio(incoming['pcm16_b64'], incoming.get('final', False))
                    except ValueError:
                        await send(message('error', code='bad_message', message='Invalid audio.'))
                elif kind == 'user_text' and incoming['final']:
                    await live.text(incoming['text'])
                elif kind == 'barge_in':
                    await live.interrupt()
                continue
            server_audio = kind == 'audio_in' and stt_mode == 'server'
            final_input = (kind == 'user_text' or server_audio) and incoming.get('final', False)
            if final_input:
                lifecycle = Turn(session, send, received_at)
                await lifecycle.stage('received', **({'text': incoming['text']} if kind == 'user_text' else {}))
                await cancel_stt()
                if server_audio:
                    # Hall noise opens utterances that Whisper returns empty: the turn that is speaking
                    # keeps going until this transcript turns out to be real words (see transcribe).
                    pending_turn = lifecycle
                else:
                    await cancel_turn()
                    await cancel_audio()
                    current_turn = lifecycle
            speech_start = ((kind == 'audio_in' and not incoming.get('final', False)) or
                            (kind == 'user_text' and not incoming['final']))
            if speech_start and not session.user_speaking:
                if not server_audio:
                    # Server audio barges only through the client's barge_in (sustained speech) or real words.
                    was_speaking = bool(active_audio)
                    await cancel_stt()
                    await cancel_turn()
                    await cancel_audio()
                    if was_speaking:
                        await send(state('listening'))
                    session.voice_pending = False
                session.user_speaking = True
                if monotonic() - session.scene_time > .5 and session.scene_task is None:
                    session.refresh_requested.set()
            if (kind == 'user_text' or (kind == 'audio_in' and stt_mode == 'server')) and incoming.get('final', False):
                session.user_speaking = False
                session.voice_pending = True
            if kind == 'camera':
                await switch_camera(incoming['on'])
            elif kind == 'frame':
                if not session.camera:
                    continue
                if session.frame is None or incoming['seq'] > session.frame['seq']:
                    session.frame = incoming
                    session.frame_time = monotonic()
                    await asyncio.sleep(0)
            elif kind == 'audio_in' and stt_mode == 'server':
                try:
                    chunk = base64.b64decode(incoming['pcm16_b64'], validate=True)
                    if len(chunk) % 2 or len(audio_buffer) + len(chunk) > 16000 * 2 * 60:
                        raise ValueError('Invalid audio size')
                    audio_buffer.extend(chunk)
                except ValueError:
                    reset_audio()
                    session.voice_pending = False
                    session.user_speaking = False
                    if final_input:
                        await pending_turn.stage('failed', detail="Didn't catch that")
                    await send(message('error', code='bad_message', message='Invalid audio.'))
                    continue
                if incoming.get('final', False):
                    pcm = bytes(audio_buffer)
                    guess = (partial['task'], partial['gen'])
                    audio_buffer.clear()  # the partial state stays until transcribe has read it
                    session.voice_pending = True
                    stt_task = asyncio.create_task(transcribe(pcm, pending_turn, guess))
                else:
                    maybe_partial()
            elif kind == 'user_text' and incoming['final']:
                await cancel_stt()
                reset_audio()
                await start_text(incoming['text'], current_turn)
            elif kind == 'confirm' and incoming['what'] == 'power_off':
                await cancel_stt()
                reset_audio()
                await cancel_turn()
                await cancel_audio()
                session.voice_pending = False
                session.user_speaking = False
                session.power_off = incoming['value']
                if session.power_off:
                    await emit(say('Thanks. What are we working on?'))
            elif kind == 'rewind':
                privacy.update(privacy.TWELVELABS, incoming['on'])  # the Rewind toggle is the video-search consent
                session.rewind.set_on(incoming['on'])
                session.rewind.keep = incoming.get('keep', session.rewind.keep)
                await send(rewind_mod.state_message(session.rewind))
                if live and live.active:
                    # Tool declarations are fixed at setup; replace, never overlap, sessions.
                    await open_live()
            elif kind == 'barge_in':
                await cancel_expert(session)
                await cancel_stt()
                reset_audio()
                await cancel_turn()
                await cancel_audio()
                session.voice_pending = False
                session.user_speaking = False
                await send(state('listening'))
    except WebSocketDisconnect:
        pass
    finally:
        # Shielded: a closing socket can cancel this task mid-cleanup (seen with the extra phone-mic read).
        with CancelScope(shield=True):
            await close_live()
            for pending in (ws_read, phone_read):
                if pending is not None:
                    pending.cancel()
            if budget_task:
                budget_task.cancel()
                await asyncio.gather(budget_task, return_exceptions=True)
            await cancel_expert(session)
            if paired_call:
                with CancelScope(shield=True):
                    await paired_call.close()
            await cancel_stt()
            reset_audio()
            await cancel_yolo()
            await cancel_turn()
            await cancel_audio()
            await cancel_scene()
            await close_voice()
            await end_rewind()
            await box.aclose()
            if session:
                session.scene = None
                session.frame = None
                session.history.clear()


def small_jpeg(encoded):
    with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as image:
        image.thumbnail((320, 320))
        output = io.BytesIO()
        image.convert('RGB').save(output, format='JPEG')
        return base64.b64encode(output.getvalue()).decode()


def match_detections(scene, detections):
    remaining = list(scene['objects'])
    matched = []
    for detection in detections:
        words = set(re.findall(r'\w+', detection['label'].lower()))
        candidates = [obj for obj in remaining
                      if words.intersection(re.findall(r'\w+', obj['label'].lower()))]
        if not candidates:
            continue
        def distance(obj):
            a, b = obj['bbox'], detection['bbox']
            return sum((a[i] + a[i + 2] / 2 - b[i] - b[i + 2] / 2) ** 2 for i in (0, 1))
        obj = min(candidates, key=distance)
        remaining.remove(obj)
        matched.append(dict(obj, bbox=detection['bbox'], conf=detection['conf']))
    return matched


async def yolo_loop(session, box, send):
    pending = set()
    previous = None
    sent_seq = -1

    async def refresh(frame):
        nonlocal sent_seq
        try:
            jpeg = await asyncio.to_thread(small_jpeg, frame['jpeg_b64'])
            detections = await box.detect(jpeg)
            if session.scene is None or frame['seq'] <= sent_seq or frame['seq'] < session.scene_seq:
                return
            objects = match_detections(session.scene, detections)
            if objects:
                await send(message('track', seq=frame['seq'], objects=objects))
                sent_seq = frame['seq']
        except Exception:
            pass  # Optional re-anchoring must not interrupt the browser tracker.

    try:
        while True:
            frame = session.frame
            if frame and frame['seq'] != previous and session.scene and len(pending) < 3:
                previous = frame['seq']
                task = asyncio.create_task(refresh(frame))
                pending.add(task)
                task.add_done_callback(pending.discard)
            await asyncio.sleep(0.01)
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def warm_brain(app):
    """Warm only the local model, using the same GPU gate as socket turns."""
    from server.llm import LOCAL_KEEP_ALIVE, local_model, local_options
    try:
        # Same options as real turns (or Ollama reloads); a cold CPU load of the 35B takes tens of seconds.
        # Seen live: without a warm model a Private call said "The brain is unavailable" after about 11 s.
        from contextlib import nullcontext
        from server.llm import VISION_MODELS
        # A CPU talker does not need the GPU gate; holding it during a long cold load would stall vision scans.
        hold = gpu_gate(app).hold(brain=True) if local_model() in VISION_MODELS else nullcontext()
        async with hold:
            async with httpx.AsyncClient(timeout=float(os.environ.get('LOCAL_WARM_TIMEOUT_S', '180'))) as client:
                response = await client.post(
                    os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/') + '/api/chat',
                    json=dict(model=local_model(), stream=False, think=False, options=local_options(1),
                              keep_alive=LOCAL_KEEP_ALIVE, messages=[dict(role='user', content='Hi')]))
                response.raise_for_status()
    except Exception:
        pass
