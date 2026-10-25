"""Opt-in Gemini Live bridge. Media exists only in bounded memory buffers.

Wire reference: https://ai.google.dev/api/live
Model reference: https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket
The documented preference must also appear in the key's runtime model catalogue.
"""
import asyncio
import base64
import json
import logging
import os
import struct
from time import monotonic, time
from urllib.parse import quote
from uuid import uuid4

import httpx
from jsonschema import validate
from websockets.asyncio.client import connect

from server import cloud_meter, cloud_vision, pointer, privacy, recent_memory, rewind_tools
from server.agent import POWER_LINE, message, say, state

DOCUMENTED_MODEL = 'gemini-3.8-live'
ENDPOINT = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'
FALLBACK_LINE = 'Gemini Live is unavailable. I am switching to the normal pipeline.'
MAX_AUDIO = 24000 * 2 * 60
VIDEO_LEAD_SECONDS = .3
# websockets debug logs include the handshake URL and raw media. Never emit them.
WIRE_LOGGER = logging.Logger('aeye.live.transport')
WIRE_LOGGER.disabled = True


def live_setup(model, instruction=''):
    """Shared camera context for the bridge and the real transport probe."""
    return {'model': model,
            'generationConfig': {'responseModalities': ['AUDIO']},
            'inputAudioTranscription': {}, 'outputAudioTranscription': {},
            'realtimeInputConfig': {'turnCoverage': 'TURN_INCLUDES_ALL_INPUT'},
            'systemInstruction': {'parts': [{'text':
                "You see the user's live camera through the video frames provided to you. "
                'Answer visual questions from those frames. If no frame is available, say so; '
                'do not invent visual details. Keep spoken answers brief. ' + instruction}]}}


def require_consent():
    if not privacy.google_allowed():
        raise PermissionError('Google Live is off')


async def discover_model(client=None):
    require_consent()
    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        raise ValueError('not configured')
    if client is None:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as http:
            return await discover_model(http)
    available, token = [], None
    while True:
        require_consent()
        response = await client.get('https://generativelanguage.googleapis.com/v1beta/models',
            headers={'x-goog-api-key': key}, params={'pageToken': token} if token else {})
        response.raise_for_status()
        page = response.json()
        available.extend(m['name'] for m in page.get('models', [])
                         if 'bidiGenerateContent' in m.get('supportedGenerationMethods', []))
        token = page.get('nextPageToken')
        if not token:
            break
    override = os.environ.get('GEMINI_LIVE_MODEL', '').removeprefix('models/')
    preferred = 'models/' + (override or DOCUMENTED_MODEL)
    if preferred in available:
        return preferred
    if override or not available:
        raise ValueError('Live model not available')
    # Never synthesize an id; avoid transcription/translation-only models.
    choices = sorted(m for m in available if not any(s in m for s in ('transcribe', 'translate')))
    if not choices:
        raise ValueError('No conversational Live model')
    return choices[0]


TOOL_ARGS = {'point_at': ('label',), 'find_item': ('name',), 'tag_item': ('name', 'place'),
             'forget_memory': ('name',), 'rewind_search': ('query',), 'recent_memory': ('question',)}
TOOL_DESCRIPTIONS = {
    'point_at': 'Ground a visible object and draw a box and arrow only when sure.',
    'find_item': 'Look up an item in the user\'s local memory.',
    'tag_item': 'Remember an item and its home place locally, when the user asks.',
    'forget_memory': 'Delete local memories of the named item when the user asks.',
    'rewind_search': 'Search this call\'s Rewind clips only while Rewind is on.',
    'recent_memory': 'Recall recent steps or observations. Pass the full question, including its time scope; '
                     'routes to recent call notes, Rewind, or earlier-call memory as appropriate.',
}


def declarations(rewind_on):
    return [dict(name=name, description=TOOL_DESCRIPTIONS[name], parameters=dict(
        # Gemini's function schema is an OpenAPI subset: no additionalProperties or string length keys.
        # Tool arguments are still validated strictly in code before any tool runs.
        type='object', properties={p: dict(type='string') for p in fields},
        required=list(fields)))
        for name, fields in TOOL_ARGS.items() if name != 'rewind_search' or rewind_on]


def pcm16(data, rate):
    """Downsample mono little-endian PCM to the existing 16 kHz wire contract."""
    if len(data) % 2 or rate not in (16000, 24000):
        raise ValueError('Invalid Live audio')
    if rate == 16000:
        return data
    samples = struct.unpack('<' + 'h' * (len(data) // 2), data)
    # 24 -> 16 kHz, averaging each output sample's covered source interval.
    output = []
    for i in range(len(samples) * 2 // 3):
        pos = i * 3 // 2
        a, b = samples[pos], samples[min(pos + 1, len(samples) - 1)]
        output.append(round((2 * a + b) / 3) if i % 2 == 0 else round((a + 2 * b) / 3))
    return struct.pack('<' + 'h' * len(output), *output)


class LiveBridge:
    def __init__(self, session, vision, app, send, fixed_say, *, discover=discover_model, endpoint=ENDPOINT):
        self.session, self.vision, self.app = session, vision, app
        # Live transcripts and scene notes must stay in this call's RAM log,
        # including when memory settings refresh the storage binding later.
        session.recent_memory.ram_only = True
        session.recent_memory.bind(None, session.session_id)
        self.send, self.fixed_say = send, fixed_say
        self.discover, self.endpoint = discover, endpoint
        self.ws = None
        self.task = None
        self.active = False
        self.closed = False
        self.reconnects = 0
        self.lock = asyncio.Lock()
        self.video_lock = asyncio.Lock()
        self.last_video = -float('inf')
        self.last_seq = None
        self.model = None
        self._reset_reply()

    def _reset_reply(self):
        self.turn_id = uuid4().hex
        self.received = False
        self.output = ''
        self.question = ''
        self.audio_buffer = bytearray()
        self.rate = 24000
        self.blocked = False

    async def stage(self, stage, **fields):
        await self.send(message('turn', turn_id=self.turn_id, stage=stage,
                                at_ms=int(time() * 1000), **fields))

    async def receive_text(self, text):
        if not self.received:
            self.received = True
            await self.stage('received', text=text)
        await self.stage('thinking', text=text)

    def rewind_on(self):
        return bool(getattr(getattr(self.session, 'rewind', None), 'on', False)
                    and privacy.twelvelabs_allowed())

    def stop_line(self, text, output=False):
        if self.session.pack.requires_power_off and not self.session.power_off:
            return POWER_LINE
        line = self.session.pack.refuse(text)
        if line:
            return line
        if output and self.session.pack.guard:
            checked = self.session.pack.guard(text, self.question, True)
            if checked != text:
                return checked
        return None

    async def _open(self):
        require_consent()
        cloud_meter.charge(float(os.getenv('GEMINI_LIVE_USD_PER_MINUTE', '0.10')), admit=True)
        self.budget_minute = monotonic()
        url = self.endpoint + '?key=' + quote(os.environ['GEMINI_API_KEY'], safe='')
        # Never log the URL or provider exceptions (both can contain the key/media).
        self.ws = await connect(url, open_timeout=10, close_timeout=2, max_size=4 * 1024 * 1024,
                                logger=WIRE_LOGGER)
        try:
            setup = live_setup(self.model, self.session.pack.system_prompt +
                    ' Use point_at to point at visible objects. Use local memory tools only when requested. '
                    'Use recent_memory for questions about previous steps or observations, preserving the full time scope. '
                    'Never claim a memory is saved or deleted before a tool confirms it.')
            setup['tools'] = [{'functionDeclarations': declarations(self.rewind_on())}]
            await self._send({'setup': setup})
            response = json.loads(await asyncio.wait_for(self.ws.recv(), 10))
            if 'setupComplete' not in response:
                raise ValueError('Live setup rejected')
            self.last_video, self.last_seq = -float('inf'), None
        except BaseException:
            await self.ws.close()
            self.ws = None
            raise

    async def start(self):
        if not privacy.google_allowed() or not os.environ.get('GEMINI_API_KEY'):
            from server.logs import event
            event('live_start_refused', level='warning',
                  reason='google_consent' if not privacy.google_allowed() else 'no_key')
            return False
        try:
            self.model = await self.discover()
            await self._open()
        except Exception as error:
            from server.logs import event
            # Class name only: provider messages may contain the key or media.
            rcvd = getattr(error, 'rcvd', None)
            key = os.environ.get('GEMINI_API_KEY', '')
            reason = (getattr(rcvd, 'reason', '') or '')[:200].replace(key, '***') if key else ''
            event('live_start_failed', level='warning', error=type(error).__name__,
                  code=getattr(rcvd, 'code', None), reason=reason)
            return False
        self.active = True
        self.task = asyncio.create_task(self._run())
        return True

    async def _send(self, payload):
        require_consent()  # every frame, audio chunk, text and tool response
        async with self.lock:
            require_consent()
            await asyncio.wait_for(self.ws.send(json.dumps(payload)), 5)

    async def _input(self, payload):
        if not self.active or not privacy.google_allowed():
            return False
        try:
            await self._send(payload)
            return True
        except Exception:
            # The reader owns the single reconnect; closing wakes it on a write failure.
            if self.ws:
                await self.ws.close()
            return False

    async def audio(self, encoded, final=False):
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) % 2 or len(raw) > 16000 * 2 * 60:
            raise ValueError('Invalid audio')
        if raw:
            if self.blocked:
                self._reset_reply()
            await self._input({'realtimeInput': {'audio': {'mimeType': 'audio/pcm;rate=16000', 'data': encoded}}})
        if final:
            await self._input({'realtimeInput': {'audioStreamEnd': True}})

    async def text(self, text):
        if not self.active or not privacy.google_allowed():
            return False
        if self.blocked:
            self._reset_reply()
        self.question = text
        await self.receive_text(text)
        line = self.stop_line(text)
        if line:
            await self.stop(line)
            return True
        await self.video()
        # Realtime streams have no cross-modality acknowledgement. Give a newly
        # sent frame a short lead; keep text on the same realtime input path.
        await asyncio.sleep(max(0, VIDEO_LEAD_SECONDS - (monotonic() - self.last_video)))
        return await self._input({'realtimeInput': {'text': text}})

    async def video(self):
        async with self.video_lock:
            await self._video()

    async def _video(self):
        frame = self.session.frame
        if (not self.active or not privacy.google_allowed() or not self.session.camera or not frame
                or monotonic() - self.session.frame_time > 1.5
                or frame['seq'] == self.last_seq or monotonic() - self.last_video < 1):
            return
        from server.ws2 import small_jpeg
        try:
            image = await asyncio.to_thread(small_jpeg, frame['jpeg_b64'])
        except (ValueError, OSError):
            return
        if not self.session.camera or self.session.frame is not frame:
            return
        if await self._input({'realtimeInput': {'video': {'mimeType': 'image/jpeg', 'data': image}}}):
            self.last_video, self.last_seq = monotonic(), frame['seq']
            meter = cloud_meter.CURRENT.get()
            if meter:
                meter.add(pictures=1)

    async def interrupt(self):
        self.audio_buffer.clear()
        self.output = ''
        self.blocked = True
        await self._input({'clientContent': {'turns': [], 'turnComplete': False}})
        await self.send(state('listening'))

    async def stop(self, line):
        await self.interrupt()
        await self.stage('speaking', detail='Stopped by code')
        await self.fixed_say(line)
        await self.stage('done')

    async def tool(self, call):
        name, args = call.get('name'), call.get('args', {})
        tool = next((d for d in declarations(self.rewind_on()) if d['name'] == name), None)
        if not tool:
            return 'Tool unavailable.'
        try:
            strict = dict(tool['parameters'], additionalProperties=False, properties={
                key: dict(type='string', minLength=1, maxLength=300) for key in tool['parameters']['properties']})
            validate(args, strict)
        except Exception:
            return 'Invalid tool arguments.'
        line = self.stop_line(' '.join(args.values()))
        if line:
            await self.stop(line)
            return line
        if self.blocked:
            return 'Stopped by code.'
        require_consent()
        if name == 'point_at':
            draw = await pointer.ground(self.session, self.vision, args['label'],
                                        allow_cloud=cloud_vision.enabled() and privacy.google_allowed())
            if draw and privacy.google_allowed():
                await self.send(draw)
            return 'Pointed at ' + args['label'] if draw else 'Unsure; nothing drawn.'
        if name == 'recent_memory':
            result = await recent_memory.answer(self.session, args['question'], self.vision)
            cards = ([rewind_tools.memory_hit_message(result, self.turn_id,
                      self.session.recent_memory.started_wall)] if result['source'] == 'long-term' else [])
        elif name == 'rewind_search':
            result, cards = await rewind_tools.run_tool('search_past', args, self.session,
                                                       vision=self.vision, app=self.app)
        else:
            mapped = {'find_item': {'item': args['name']},
                      'tag_item': {'name': args['name'], 'home': args.get('place')},
                      'forget_memory': {'scope': 'item', 'item': args['name']}}[name]
            result, cards = await rewind_tools.run_memory_tool(name, mapped, self.session, self.app)
        for card in cards:
            await self.send(card)
        # Allowlist one textual result; never forward crops, embeddings or the memory log.
        return str(result.get('say') or 'No result.')[:4000]

    async def handle(self, response):
        require_consent()
        content = response.get('serverContent', {})
        if content.get('interrupted'):
            self._reset_reply()
            await self.send(state('listening'))
            return
        for field, output in (('inputTranscription', False), ('outputTranscription', True)):
            text = content.get(field, {}).get('text', '')
            if text:
                attr = 'output' if output else 'question'
                setattr(self, attr, getattr(self, attr) + text)
                if len(getattr(self, attr)) > 16000:
                    raise ValueError('Live transcript too long')
                line = self.stop_line(getattr(self, attr), output)
                if line and not self.blocked:
                    await self.stop(line)
                if not output:
                    await self.receive_text(self.question)
        if not self.blocked:
            for part in content.get('modelTurn', {}).get('parts', []):
                blob = part.get('inlineData')
                if blob:
                    mime = blob.get('mimeType', '')
                    if mime not in ('audio/pcm;rate=24000', 'audio/pcm;rate=16000'):
                        raise ValueError('Unsupported Live audio')
                    rate = int(mime.rsplit('=', 1)[1])
                    if self.audio_buffer and rate != self.rate:
                        raise ValueError('Live rate changed')
                    self.rate = rate
                    self.audio_buffer.extend(base64.b64decode(blob['data'], validate=True))
                    if len(self.audio_buffer) > MAX_AUDIO:
                        raise ValueError('Live reply too long')
        for call in response.get('toolCall', {}).get('functionCalls', []):
            try:
                result = await self.tool(call)
            except Exception:
                result = 'Tool unavailable.'
            await self._send({'toolResponse': {'functionResponses': [dict(
                id=call['id'], name=call['name'], response={'result': result})]}})
        if content.get('turnComplete'):
            if not self.blocked:
                self.session.recent_memory.note_transcript(self.question, 'user')
            if not self.blocked and self.output.strip():
                line = self.stop_line(self.output, True)
                if line:
                    await self.stop(line)
                else:
                    # Complete transcript first: audio can precede transcription on the wire.
                    msg = say(self.output.strip())
                    self.session.recent_memory.note_transcript(msg['text'], 'helper')
                    msg['turn_id'] = self.turn_id
                    await self.stage('speaking', detail='Brain: Google / ' + self.model)
                    await self.send(state('speaking'))
                    await self.send(msg)
                    pcm = pcm16(bytes(self.audio_buffer), self.rate)
                    for offset in range(0, max(1, len(pcm)), 32000):
                        if self.blocked or self.closed:
                            break
                        await self.send(message('audio', agent='helper', utterance_id=msg['utterance_id'],
                            sample_rate=16000, pcm16_b64=base64.b64encode(pcm[offset:offset + 32000]).decode(),
                            final=offset + 32000 >= len(pcm)))
            await self.stage('done')
            self._reset_reply()
            # Clients finish queued PCM themselves. A listening event would cancel
            # playback immediately on the paired-phone route.

    async def _run(self):
        try:
            while not self.closed:
                try:
                    while not self.closed:
                        if monotonic() - self.budget_minute >= 60:
                            cloud_meter.charge(float(os.getenv('GEMINI_LIVE_USD_PER_MINUTE', '0.10')), admit=True)
                            self.budget_minute = monotonic()
                        require_consent()
                        await self.video()
                        try:
                            raw = await asyncio.wait_for(self.ws.recv(), .1)
                        except asyncio.TimeoutError:
                            continue
                        response = json.loads(raw)
                        if 'goAway' in response or 'error' in response:
                            raise ConnectionError('Live ended')
                        await self.handle(response)
                except Exception:
                    self._reset_reply()
                    if self.ws:
                        await self.ws.close()
                    if self.closed:
                        return
                    if self.reconnects == 0 and privacy.google_allowed():
                        self.reconnects += 1
                        try:
                            await self._open()
                            continue
                        except Exception:
                            pass
                    self.active = False
                    if not cloud_meter.BUDGET.exhausted():
                        await self.fixed_say(FALLBACK_LINE)
                    return
        finally:
            self.active = False
            self.audio_buffer.clear()

    async def close(self):
        self.closed = True
        self.active = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.ws:
            await self.ws.close()
        self._reset_reply()
