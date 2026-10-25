"""Synthetic Live wire frames; privacy is checked before any network work."""
import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from websockets.asyncio.server import serve

from server import cloud_meter, privacy
from server.agent import Session
from server.gemini_live import LiveBridge, discover_model, DOCUMENTED_MODEL


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (900, 600), 'blue').save(out, 'JPEG')
    return base64.b64encode(out.getvalue()).decode()


def test_fake_live_recent_step_transcripts_and_scene_stay_local(monkeypatch):
    async def trial():
        from server.ws2 import scene_loop
        from server.test_ws2 import Vision
        monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-key')
        privacy.set_call({'google': True})
        wire, emitted = [], []
        done = asyncio.Event()
        question = 'what step was I just on?'
        instruction = 'Step 3: place the resistor on row 12.'
        async def fake(ws):
            wire.append(json.loads(await ws.recv()))
            await ws.send(json.dumps({'setupComplete': {}}))
            await ws.send(json.dumps({'serverContent': {
                'inputTranscription': {'text': 'Help with the resistor.'},
                'outputTranscription': {'text': 'Step 3: place the '}}}))
            await ws.send(json.dumps({'serverContent': {
                'outputTranscription': {'text': 'resistor on row 12.'}, 'turnComplete': True}}))
            async for raw in ws:
                msg = json.loads(raw)
                wire.append(msg)
                if msg.get('realtimeInput', {}).get('text') == question:
                    await ws.send(json.dumps({'toolCall': {'functionCalls': [
                        dict(id='recent', name='recent_memory', args={'question': question})]}}))
                if 'toolResponse' in msg:
                    result = msg['toolResponse']['functionResponses'][0]['response']['result']
                    await ws.send(json.dumps({'serverContent': {
                        'outputTranscription': {'text': result}, 'turnComplete': True}}))
        async def emit(msg):
            emitted.append(msg)
            if msg.get('type') == 'say' and 'the guide said' in msg['text']:
                done.set()
        async def discover():
            return 'models/' + DOCUMENTED_MODEL
        session = Session('recent-live')
        async with serve(fake, '127.0.0.1', 0) as server:
            bridge = LiveBridge(session, Vision(), None, emit, emit, discover=discover,
                                endpoint=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}')
            # Memory settings must not attach a persistent backend to this Live log.
            session.recent_memory.bind(SimpleNamespace(remembering=lambda: True), session.session_id)
            assert session.recent_memory.store is None
            assert await bridge.start()
            scan = None
            try:
                for _ in range(100):
                    if any(m.get('text') == instruction for m in emitted):
                        break
                    await asyncio.sleep(.01)
                session.frame = dict(seq=1, jpeg_b64=jpeg())
                scan = asyncio.create_task(scene_loop(session, bridge.vision, emit))
                for _ in range(100):
                    if any(e['kind'] == 'scene' for e in session.recent_memory.entries):
                        break
                    await asyncio.sleep(.01)
                entries = list(session.recent_memory.entries)
                assert any(e['kind'] == 'scene' and e['note'] for e in entries)
                assert any(e['kind'] == 'user' and e['note'] == 'Help with the resistor.' for e in entries)
                assert any(e['kind'] == 'helper' and e['note'] == instruction for e in entries)
                await bridge.text(question)
                await asyncio.wait_for(done.wait(), 2)
                response = next(m['toolResponse']['functionResponses'][0]['response']
                                for m in wire if 'toolResponse' in m)
                assert list(response) == ['result']
                assert 'the guide said' in response['result'] and instruction in response['result']
                setup = json.dumps(wire[0])
                assert all(key not in setup for key in ('additionalProperties', 'minLength', 'maxLength'))
                assert 'observed_at' not in json.dumps(wire) and 'wall_t' not in json.dumps(wire)
            finally:
                if scan:
                    scan.cancel()
                    await asyncio.gather(scan, return_exceptions=True)
                await bridge.close()
    asyncio.run(trial())


@pytest.mark.parametrize('args', [{}, {'question': ''}, {'question': 'x' * 301},
                                 {'question': 3}, {'question': 'step', 'extra': 'no'}])
def test_recent_tool_strict_arguments(args):
    async def trial():
        privacy.set_call({'google': True})
        async def emit(msg):
            pass
        bridge = LiveBridge(Session('s'), None, None, emit, emit)
        assert await bridge.tool(dict(name='recent_memory', args=args)) == 'Invalid tool arguments.'
    asyncio.run(trial())


def test_recent_tool_temporal_routes_and_local_result_cards(tmp_path):
    async def trial():
        from scripts.recent_memory_bench import fixture
        privacy.set_call({'google': True, 'twelvelabs': True})
        evidence = fixture(tmp_path)
        session = Session('s', recent_memory=evidence.recent_memory)
        session.rewind = evidence.rewind
        emitted = []
        async def emit(msg):
            emitted.append(msg)
        bridge = LiveBridge(session, None, None, emit, emit)
        async def recall(question):
            return await bridge.tool(dict(name='recent_memory', args={'question': question}))
        assert 'row 12' in await recall('Where was the red wire a minute ago?')
        assert session.rewind.calls == 0
        assert '0:30' in await recall('Where was the resistor earlier in this call?')
        assert session.rewind.calls == 1
        assert 'parts bin' in await recall('Where was the resistor yesterday?')
        assert any(m['type'] == 'find_item' for m in emitted)
        session.rewind.on = False
        assert 'earlier call' in await recall('Where was the resistor 3 minutes ago?')
        assert session.rewind.calls == 1
        assert "can't confirm" in await recall('Where was the blue wire just now?')
        privacy.update('google', False)
        with pytest.raises(PermissionError):
            await recall('What was step 3?')
    asyncio.run(trial())


def test_camera_precedes_realtime_question():
    async def trial():
        from time import monotonic
        privacy.set_call({'google': True})
        wire = []
        async def send(raw):
            wire.append((monotonic(), json.loads(raw)))
        async def emit(msg):
            pass
        session = Session('s', frame=dict(seq=1, jpeg_b64=jpeg()))
        bridge = LiveBridge(session, None, None, emit, emit)
        bridge.active = True
        bridge.ws = SimpleNamespace(send=send)
        assert await bridge.text('what do you see?')
        assert len(wire) == 2
        assert wire[0][1]['realtimeInput']['video']['mimeType'] == 'image/jpeg'
        assert wire[1][1] == {'realtimeInput': {'text': 'what do you see?'}}
        assert wire[1][0] - wire[0][0] >= .25
    asyncio.run(trial())


@pytest.mark.parametrize('reply,expected', [
    ('I see a solid blue image.', True),
    ("I don't have eyes, so I can't see anything.", False),
    ('I cannot see a blue image.', False),
    ('A red image.', False),
])
def test_probe_requires_blue_visual_reply(reply, expected):
    from scripts.gemini_live_check import sees_blue
    assert bool(sees_blue(reply)) is expected


@pytest.mark.parametrize('private,google,local', [(True, True, False), (False, False, False), (False, True, True)])
def test_privacy_before_discovery_and_connection(monkeypatch, private, google, local):
    async def trial():
        monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-key')
        monkeypatch.setenv('LOCAL_ONLY', '1' if local else '0')
        privacy.set_call({'google': google}, private=private)
        calls = []
        async def forbidden(*args, **kwargs):
            calls.append(True)
            raise AssertionError('network attempted')
        bridge = LiveBridge(Session('s'), None, None, forbidden, forbidden, discover=forbidden)
        assert not await bridge.start()
        assert calls == []
    asyncio.run(trial())


def test_discovery_filters_methods_paginates_and_validates_override(monkeypatch):
    async def trial():
        privacy.set_call({'google': True})
        monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-key')
        monkeypatch.delenv('GEMINI_LIVE_MODEL', raising=False)
        def handle(request):
            assert request.headers['x-goog-api-key'] == 'synthetic-key'
            assert 'key=' not in str(request.url)
            if not request.url.params.get('pageToken'):
                return httpx.Response(200, json={'models': [{'name': 'models/text-only', 'supportedGenerationMethods': ['generateContent']}], 'nextPageToken': 'next'})
            return httpx.Response(200, json={'models': [{'name': 'models/' + DOCUMENTED_MODEL, 'supportedGenerationMethods': ['bidiGenerateContent']}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert await discover_model(client) == 'models/' + DOCUMENTED_MODEL
            monkeypatch.setenv('GEMINI_LIVE_MODEL', 'text-only')
            with pytest.raises(ValueError):
                await discover_model(client)
    asyncio.run(trial())


def test_fake_live_wire_audio_tools_captions_and_revocation(monkeypatch):
    async def trial():
        monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-key')
        privacy.set_call({'google': True})
        meter = cloud_meter.start()
        received, emitted, fixed = [], [], []
        sent = asyncio.Event()
        async def fake(ws):
            received.append(json.loads(await ws.recv()))
            await ws.send(json.dumps({'setupComplete': {}}))
            async for raw in ws:
                msg = json.loads(raw)
                received.append(msg)
                if 'text' in msg.get('realtimeInput', {}):
                    await ws.send(json.dumps({'serverContent': {'inputTranscription': {'text': 'What is here?'}}}))
                    await ws.send(json.dumps({'toolCall': {'functionCalls': [{'id': 't1', 'name': 'find_item', 'args': {'name': 'keys'}}]}}))
                if 'toolResponse' in msg:
                    # Audio arrives before its transcript: do not play until checked.
                    await ws.send(json.dumps({'serverContent': {'modelTurn': {'parts': [{'inlineData': {'mimeType': 'audio/pcm;rate=24000', 'data': base64.b64encode(b'\x00\x00' * 240).decode()}}]}}}))
                    await ws.send(json.dumps({'serverContent': {'outputTranscription': {'text': 'A blue board.'}, 'turnComplete': True}}))
                    sent.set()
        async def emit(msg):
            emitted.append(msg)
        async def fixed_say(text):
            fixed.append(text)
        async def discover():
            return 'models/' + DOCUMENTED_MODEL
        async with serve(fake, '127.0.0.1', 0) as server:
            endpoint = 'ws://127.0.0.1:' + str(server.sockets[0].getsockname()[1])
            # Count the synthetic remote transport, not a real cloud request.
            monkeypatch.setattr(cloud_meter, 'is_local', lambda url: False)
            session = Session('s')
            session.frame = dict(seq=1, jpeg_b64=jpeg())
            bridge = LiveBridge(session, None, SimpleNamespace(state=SimpleNamespace()), emit, fixed_say,
                                discover=discover, endpoint=endpoint)
            assert await bridge.start()
            setup = received[0]['setup']
            assert setup['generationConfig']['responseModalities'] == ['AUDIO']
            assert setup['realtimeInputConfig']['turnCoverage'] == 'TURN_INCLUDES_ALL_INPUT'
            assert 'live camera' in setup['systemInstruction']['parts'][0]['text']
            await bridge.audio(base64.b64encode(b'\x00\x00' * 160).decode(), True)
            await bridge.video()
            await bridge.video()  # at most one picture per second
            await bridge.text('What is here?')
            await asyncio.wait_for(sent.wait(), 2)
            for _ in range(100):
                if any(m['type'] == 'audio' for m in emitted):
                    break
                await asyncio.sleep(.01)
            assert any(m['type'] == 'say' and m['text'] == 'A blue board.' for m in emitted)
            audio = next(m for m in emitted if m['type'] == 'audio')
            assert audio['sample_rate'] == 16000 and len(base64.b64decode(audio['pcm16_b64'])) == 320
            assert not any(m.get('state') == 'listening' for m in emitted[emitted.index(audio):])
            assert meter.pictures_sent == 1 and meter.calls >= 1 and meter.bytes > 0
            wire = json.dumps(received)
            assert 'audio/pcm;rate=16000' in wire and 'Memory is off' in wire
            image = next(m['realtimeInput']['video']['data'] for m in received if 'video' in m.get('realtimeInput', {}))
            assert max(Image.open(io.BytesIO(base64.b64decode(image))).size) <= 320
            privacy.update('google', False)
            count = len(received)
            assert not await bridge.text('must stay local')
            await bridge.close()
            assert len(received) == count
    asyncio.run(trial())


def test_reconnect_once_then_fallback_and_close(monkeypatch):
    async def trial():
        from server.gemini_live import FALLBACK_LINE
        monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-key')
        privacy.set_call({'google': True})
        connections, fixed = [], []
        async def fake(ws):
            connections.append(json.loads(await ws.recv()))
            await ws.send('{"setupComplete": {}}')
            await asyncio.sleep(.02)
            await ws.close()
        async def emit(msg):
            pass
        async def fixed_say(text):
            fixed.append(text)
        async def discover():
            return 'models/' + DOCUMENTED_MODEL
        async with serve(fake, '127.0.0.1', 0) as server:
            bridge = LiveBridge(Session('s'), None, None, emit, fixed_say, discover=discover,
                                endpoint=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}')
            assert await bridge.start()
            await asyncio.wait_for(bridge.task, 2)
            assert len(connections) == 2 and not bridge.active
            assert fixed == [FALLBACK_LINE]
            await bridge.close()
            assert bridge.task.done()
    asyncio.run(trial())


def test_split_safety_transcript_suppresses_early_audio_and_tools():
    async def trial():
        from server.safety import before
        privacy.set_call({'google': True})
        emitted, fixed = [], []
        async def emit(msg):
            emitted.append(msg)
        async def speak(text):
            fixed.append(text)
        bridge = LiveBridge(Session('s'), None, None, emit, speak)
        blob = dict(mimeType='audio/pcm;rate=24000', data=base64.b64encode(b'\x00\x00' * 240).decode())
        await bridge.handle({'serverContent': {'modelTurn': {'parts': [{'inlineData': blob}]}}})
        assert not emitted
        await bridge.handle({'serverContent': {'inputTranscription': {'text': 'open the electrical '}}})
        await bridge.handle({'serverContent': {'inputTranscription': {'text': 'panel'}}})
        assert fixed == [before('open the electrical panel')]
        assert await bridge.tool(dict(name='tag_item', args={'name': 'keys', 'place': 'bowl'})) == 'Stopped by code.'
        await bridge.handle({'serverContent': {'outputTranscription': {'text': 'Here is a step.'}, 'turnComplete': True}})
        assert not any(m['type'] == 'audio' for m in emitted)
        assert not bridge.audio_buffer
    asyncio.run(trial())


def test_tools_local_memory_and_no_media_result(tmp_path, monkeypatch):
    async def trial():
        from server import memory
        from server.test_memory import Words, Person, FRAME, KEYS
        privacy.set_call({'google': True})
        store = memory.Store('live-test', base=tmp_path, cipher=memory.Cipher(''))
        registry = SimpleNamespace(for_call=lambda call: (store, None), embedder=Words(), vision=None)
        monkeypatch.setattr(memory, 'memories', lambda app: registry)
        emitted = []
        async def emit(msg):
            emitted.append(msg)
        bridge = LiveBridge(Session('s'), None, None, emit, emit)
        tag = await bridge.tool(dict(name='tag_item', args={'name': 'keys', 'place': 'blue bowl'}))
        assert 'remember your keys' in tag and store.tags()['keys']['home'] == 'blue bowl'
        writer = memory.Writer(store, 's', embedder=Words(), person_check=Person())
        await writer.observe(dict(caption='keys on the table', objects=[KEYS]), FRAME)
        result = await bridge.tool(dict(name='find_item', args={'name': 'keys'}))
        assert 'keys' in result and 'base64' not in result and FRAME not in result
        assert any(m['type'] == 'find_item' and m.get('frame_b64') for m in emitted)
        result = await bridge.tool(dict(name='forget_memory', args={'name': 'keys'}))
        assert 'Forgotten' in result and store.count('keys') == 0 and 'keys' not in store.tags()
        assert await bridge.tool(dict(name='rewind_search', args={'query': 'keys'})) == 'Tool unavailable.'
    asyncio.run(trial())


@pytest.mark.parametrize('source,cloud,conf,expected', [('ollama', False, .8, True),
    (None, True, .7, True), ('fake', True, .9, False), (None, True, .2, False)])
def test_point_uses_configured_backend_and_abstains(monkeypatch, source, cloud, conf, expected):
    async def trial():
        from server.test_pointer import Vision, LABEL
        if cloud:
            monkeypatch.setenv('VISION_BACKEND', 'gemini')
        privacy.set_call({'google': True})
        session = Session('s', frame=dict(seq=1, jpeg_b64=jpeg()))
        vision = Vision(source=source, conf=conf)
        if source is None:
            vision.result.pop('source')
        emitted = []
        async def emit(msg):
            emitted.append(msg)
        bridge = LiveBridge(session, vision, None, emit, emit)
        await bridge.tool(dict(name='point_at', args={'label': LABEL}))
        assert bool(emitted) == expected
        if expected:
            assert [o['kind'] for o in emitted[0]['ops']] == ['clear', 'box', 'arrow']
        session.frame_time -= 2
        emitted.clear()
        await bridge.tool(dict(name='point_at', args={'label': LABEL}))
        assert not emitted
    asyncio.run(trial())


def test_tool_hazard_and_pc_power_gate():
    async def trial():
        from server import packs
        from server.agent import POWER_LINE
        privacy.set_call({'google': True})
        lines = []
        async def emit(msg):
            pass
        async def fixed(line):
            lines.append(line)
        bridge = LiveBridge(Session('s', pack=packs.get('pc_build')), None, None, emit, fixed)
        await bridge.tool(dict(name='point_at', args={'label': 'CPU fan'}))
        assert lines == [POWER_LINE]
        bridge = LiveBridge(Session('s'), None, None, emit, fixed)
        await bridge.tool(dict(name='tag_item', args={'name': 'gas line', 'place': 'wall'}))
        assert 'gas line' in lines[-1]
    asyncio.run(trial())


def test_output_safety_and_next_safe_question():
    async def trial():
        privacy.set_call({'google': True})
        emitted, fixed, wire = [], [], []
        async def emit(msg):
            emitted.append(msg)
        async def speak(text):
            fixed.append(text)
        async def send(raw):
            wire.append(json.loads(raw))
        bridge = LiveBridge(Session('s'), None, None, emit, speak)
        bridge.active = True
        bridge.model = DOCUMENTED_MODEL
        bridge.ws = SimpleNamespace(send=send)
        await bridge.handle({'serverContent': {'outputTranscription': {'text': 'Open the electrical '}}})
        await bridge.handle({'serverContent': {'outputTranscription': {'text': 'panel.'}}})
        assert fixed and bridge.blocked
        assert not any(m['type'] == 'say' for m in emitted)
        # Local refusals need not produce a provider turnComplete. The next safe turn works.
        assert await bridge.text('Describe the blue board')
        await bridge.handle({'serverContent': {'outputTranscription': {'text': 'A blue board.'}, 'turnComplete': True}})
        assert any(m['type'] == 'say' and m['text'] == 'A blue board.' for m in emitted)
        assert any('clientContent' in m for m in wire)
    asyncio.run(trial())


def test_rewind_tool_requires_live_consent_and_only_returns_text(monkeypatch):
    async def trial():
        from server.gemini_live import declarations
        from server import rewind_tools
        privacy.set_call({'google': True, 'twelvelabs': True})
        session = Session('s')
        session.rewind = SimpleNamespace(on=True)
        calls, emitted = [], []
        async def run(name, args, current, **kwargs):
            calls.append((name, args, current))
            return {'say': 'Keys were on the table.', 'frame_b64': 'private-crop'}, []
        async def emit(msg):
            emitted.append(msg)
        monkeypatch.setattr(rewind_tools, 'run_tool', run)
        bridge = LiveBridge(session, None, None, emit, emit)
        assert 'rewind_search' in [d['name'] for d in declarations(bridge.rewind_on())]
        call = dict(name='rewind_search', args={'query': 'keys'})
        assert await bridge.tool(call) == 'Keys were on the table.'
        assert calls == [('search_past', {'query': 'keys'}, session)]
        session.rewind.on = False
        assert await bridge.tool(call) == 'Tool unavailable.'
        session.rewind.on = True
        privacy.update('twelvelabs', False)
        assert await bridge.tool(call) == 'Tool unavailable.'
        assert len(calls) == 1
    asyncio.run(trial())


def test_ws2_live_selection_routing_and_default_fallback(monkeypatch):
    from fastapi.testclient import TestClient
    from server.test_ws2 import app, send, receive
    from server.gemini_live import FALLBACK_LINE
    bridges = []
    class FakeBridge:
        def __init__(self, session, vision, app, send, fixed):
            self.active = False
            self.calls = []
            self.closed = False
            self.fixed = fixed
            bridges.append(self)
        async def start(self):
            self.active = privacy.google_allowed()
            return self.active
        async def text(self, text):
            self.calls.append(('text', text))
        async def audio(self, data, final):
            self.calls.append(('audio', data, final))
        async def interrupt(self):
            self.calls.append(('interrupt',))
        async def close(self):
            self.active = False
            self.closed = True
    application = app()
    application.state.live_factory_v2 = FakeBridge
    with TestClient(application) as client:
        with client.websocket_connect('/ws2') as ws:
            send(ws, 'hello', pack='general', camera=False)
            assert receive(ws)['state'] == 'listening'
            assert bridges == []
        with client.websocket_connect('/ws2?mode=gemini-live&google=1') as ws:
            send(ws, 'hello', pack='general', camera=False, stt='server')
            assert receive(ws)['state'] == 'listening'
            send(ws, 'user_text', text='What is here?', final=True)
            send(ws, 'audio_in', seq=1, pcm16_b64='AAA=', final=True)
            ws.send_json({})
            assert receive(ws)['code'] == 'bad_message'  # processing barrier
            assert bridges[-1].calls == [('text', 'What is here?'), ('audio', 'AAA=', True)]
            send(ws, 'hello', pack='general', camera=False, private=True)
            assert receive(ws)['text'] == FALLBACK_LINE
            assert receive(ws)['state'] == 'listening'
            assert bridges[-2].closed and not bridges[-1].active
        assert bridges[-1].closed
    # Actual unconfigured Live falls through to the untouched normal text pipeline.
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with TestClient(app()) as client, client.websocket_connect('/ws2?mode=gemini-live&google=1') as ws:
        send(ws, 'hello', pack='general', camera=False)
        assert receive(ws)['text'] == FALLBACK_LINE
        assert receive(ws)['state'] == 'listening'
        send(ws, 'user_text', text='Hello there', final=True)
        assert receive(ws)['state'] == 'thinking'
        for _ in range(10):
            if receive(ws).get('type') == 'say':
                break
        else:
            pytest.fail('normal fallback did not answer')
