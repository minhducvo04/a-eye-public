import asyncio
from time import monotonic

from server.shopping import Offer, FixtureSource, recommend, shopping_turn
from server.agent import Session
from server.privacy import PRIVATE, SWITCHES
from server.test_agent import FRAME, SCENE, Vision


def offers():
    return [Offer('Slow', 20, 'USD', 14, False, 'https://slow.example/p', True, ''),
            Offer('Fast', 26, 'USD', 1, False, 'https://fast.example/p', True, ''),
            Offer('Here', 32, 'USD', None, True, 'https://here.example/p', True, '')]


def test_value_and_one_question():
    items, line = recommend('coffee mug', offers())
    assert items[0].store == 'Fast'
    assert '26 dollars' in line and 'Slow' in line and 'Here' in line
    assert line.count('?') == 1 and 'soon' in line
    _, line = recommend('USB charger', offers())
    assert line.count('?') == 1 and 'device' in line and 'soon' not in line
    _, line = recommend('coffee mug', offers()[:1])
    assert '?' not in line


def test_unavailable_and_currency_not_compared():
    assert recommend('mug', [Offer('No', 1, 'USD', 1, False, 'https://no.example', False, '')])[0] == []
    assert 'couldn\'t find' in recommend('mug', [])[1]
    items, line = recommend('mug', offers() + [Offer('Euro', 1, 'EUR', 1, False, 'https://e.example', True, '')])
    assert all(x.currency == 'USD' for x in items)
    assert 'Euro' not in line


def test_private_refuses_before_source_or_vision():
    async def check():
        private = PRIVATE.set(True)
        try:
            source = FixtureSource(offers())
            vision = Vision()
            result, line = await shopping_turn(Session('s', frame=FRAME), 'buy this', vision, source)
            assert result is None and 'cloud' in line and 'Private' in line
            assert not source.calls and not vision.calls
        finally:
            PRIVATE.reset(private)
    asyncio.run(check())


def test_fixture_live_scene_and_disabled_switch():
    async def check():
        source = FixtureSource(offers())
        session = Session('s', frame=FRAME, scene=SCENE, scene_seq=1, scene_time=monotonic())
        result, line = await shopping_turn(session, 'where can I buy this pan?', Vision(), source)
        assert result['items'][0]['selected'] is True and 'Fast' in line
        assert 'pan' in source.calls[0][0]
        token = SWITCHES.set({'claude': False})
        try:
            result, line = await shopping_turn(session, 'buy this', Vision(), source)
            assert result is None and 'Claude' in line and len(source.calls) == 1
        finally:
            SWITCHES.reset(token)
    asyncio.run(check())


def test_ws2_typed_shopping_fixture_and_voice():
    from fastapi.testclient import TestClient
    from contracts import validate
    from server.test_ws2 import app, hello, send
    from voice.adapter import VoiceAdapter

    class Voice(VoiceAdapter):
        def __init__(self):
            super().__init__()
            self.lines = []

        async def say(self, utterance_id, text):
            self.lines.append(text)
            yield b'\x00\x00'

    voice = Voice()
    application = app(voice=voice)
    source = application.state.offer_source = FixtureSource(offers())
    with TestClient(application) as client, client.websocket_connect('/ws2') as ws:
        hello(ws, tts='server')
        send(ws, 'user_text', text="Where's the best place to buy this?", final=True)
        messages = []
        while True:
            message = ws.receive_json()
            validate('server_message_v2', message)
            messages.append(message)
            if message['type'] == 'turn' and message['stage'] in ('done', 'failed'):
                assert message['stage'] == 'done'
                break
        market = next(m['result'] for m in messages if m['type'] == 'market_results')
        assert market['items'][0]['store'] == 'Fast'
        assert sum(i['selected'] for i in market['items']) == 1
        spoken = [m['text'] for m in messages if m['type'] == 'say']
        assert len(spoken) == 2 and spoken == voice.lines
        assert spoken[0] == 'Let me compare the offers.'
        assert sum(line.count('?') for line in spoken) == 1
        assert any(m['type'] == 'audio' for m in messages)
        assert len(source.calls) == 1


def test_cloud_offers_require_cited_price_and_never_network(monkeypatch):
    from types import SimpleNamespace as NS
    from server.llm import LLMClient
    from server.shopping import WebSearchSource
    from dataclasses import asdict
    import json

    calls = []
    row = asdict(offers()[0])
    citation = NS(type='web_search_result_location', url=row['url'], cited_text='Price: $20.00, free shipping')
    response = NS(stop_reason='end_turn', content=[NS(type='text', text=json.dumps([row]), citations=[citation])])

    class Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return response

    async def check():
        client = LLMClient(anthropic_client=NS(messages=Messages()))
        source = WebSearchSource(client)
        assert len(await source.search('Demo model')) == 1
        assert calls[0]['tools'][0]['name'] == 'web_search'
        citation.cited_text = 'No price available'
        assert await source.search('Demo model') == []
        token = PRIVATE.set(True)
        try:
            assert await source.search('Demo model') == []
            assert len(calls) == 2
        finally:
            PRIVATE.reset(token)
    monkeypatch.setenv('LLM_PROVIDER', 'claude')
    asyncio.run(check())


def test_stale_frame_and_timeout_do_not_invent_offers():
    async def check():
        source = FixtureSource(offers())
        session = Session('s', frame=FRAME, frame_time=monotonic() - 4)
        result, line = await shopping_turn(session, 'buy this', Vision(), source)
        assert result is None and not source.calls and 'label' in line
        session.frame_time = monotonic()

        class BrokenSource:
            async def search(self, *args):
                raise TimeoutError

        result, line = await shopping_turn(session, 'buy this', Vision(), BrokenSource())
        assert result is None and "couldn't check" in line
    asyncio.run(check())
