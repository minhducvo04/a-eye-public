import asyncio

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.boards import load_board
from server.gates import REFUSAL
from server.llm import LLMResult
from server.test_ws import FakeVision, guide, hello, photo, receive, send


class FakeLLM:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def complete(self, system, user):
        # A blocking provider must execute outside the socket's event loop.
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        self.calls.append((system, user))
        if self.fail:
            raise RuntimeError('provider unavailable')
        return LLMResult('Check CPU_FAN on your board.', 'fake', 'fake', 0)


@pytest.mark.parametrize('budget', [100, 0])
def test_market_in_safety(monkeypatch, budget):
    monkeypatch.setenv('MARKET_SOURCE', 'fixture')
    with TestClient(create_app(FakeVision())) as client:
        with client.websocket_connect('/ws') as ws:
            hello(ws)
            send(ws, 'market', category='ram', budget_usd=budget)
            result = receive(ws)[0]
            assert result['type'] == 'market_results'
            assert result['result']['query_key'] == 'ram:DDR5:DIMM'
            items = result['result']['items']
            assert all(i['compat']['status'] in {'fits', 'unverified'} for i in items)
            assert all(i['price_usd'] + i.get('shipping_usd', 0) <= budget for i in items)
            speech = receive(ws)[0]
            assert speech['type'] == 'say'
            if budget:
                top = next(i for i in items if i['compat']['status'] == 'fits')
                assert speech['text'] == (f"Cheapest that fits: {top['title']}, "
                                          f"${top['price_usd']:.2f} at {top['retailer']}.")
            else:
                assert items == []
                assert speech['text'] == 'I found nothing that I can confirm fits.'
            photo(ws)
            assert receive(ws)[0]['code'] == 'power_off_required'
            send(ws, 'confirm', what='power_off', value=True)
            assert receive(ws, 2)[0]['state'] == 'IDENTIFY'


def test_question_context_and_safety_bypass():
    llm = FakeLLM()
    with TestClient(create_app(FakeVision(), llm=llm)) as client:
        with client.websocket_connect('/ws') as ws:
            guide(ws)
            send(ws, 'user_text', text='Where does this fan go?')
            assert receive(ws)[0]['text'] == 'Check CPU_FAN on your board.'
            system, user = llm.calls[0]
            assert 'one or two plain sentences' in system
            assert 'check the manual page' in system
            for context in ('AWAIT', 'cpu_fan_cable', 'CPU_FAN', 'SYS_FAN1',
                            'DIMM_A2', 'demo-b650', 'Where does this fan go?'):
                assert context in user
            send(ws, 'user_text', text='How do I open the PSU?')
            assert receive(ws)[0]['text'] == REFUSAL
            assert len(llm.calls) == 1
            send(ws, 'user_text', text='power is off')
            send(ws, 'user_text', text='How do I open the PSU?')
            assert receive(ws)[0]['text'] == REFUSAL
            assert len(llm.calls) == 1


def test_router_failure_falls_back_and_socket_survives():
    llm = FakeLLM(fail=True)
    with TestClient(create_app(FakeVision(), llm=llm)) as client:
        with client.websocket_connect('/ws') as ws:
            hello(ws)
            send(ws, 'user_text', text='What now?')
            reply = receive(ws)[0]
            assert reply['type'] == 'say'
            assert 'check the manual page' in reply['text']
            send(ws, 'user_text', text='power is off')
            assert receive(ws, 2)[0]['state'] == 'IDENTIFY'
            assert len(llm.calls) == 1


def test_unverified_market_preserves_active_step(monkeypatch):
    monkeypatch.setenv('MARKET_SOURCE', 'fixture')
    board = load_board('demo-b650')
    board.pop('specs')
    monkeypatch.setattr('server.app.load_board', lambda board_id: board)
    with TestClient(create_app(FakeVision())) as client:
        with client.websocket_connect('/ws') as ws:
            guide(ws)
            send(ws, 'market', category='ram')
            result, speech = receive(ws, 2)
            assert result['type'] == 'market_results'
            assert result['result']['items']
            assert all(i['compat']['status'] == 'unverified' for i in result['result']['items'])
            assert speech['text'] == 'I found nothing that I can confirm fits.'
            send(ws, 'confirm', what='seated', value=True)
            messages = receive(ws, 4)
            assert messages[0]['state'] == 'VERIFY'
            assert messages[2]['state'] == 'DONE'
