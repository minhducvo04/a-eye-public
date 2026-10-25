import asyncio
import base64
import io
import json
from time import monotonic

import httpx
import pytest
from PIL import Image

from server.step_check import Checker, Embedding, Qwen, Verdict, parse_verdict


def photo():
    out = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(out, 'JPEG')
    return out.getvalue()


@pytest.mark.parametrize('raw', [
    '{"state":"closed","confidence":true}',
    '{"state":"closed","confidence":NaN}',
    '{"state":"closed","confidence":1.1}',
    '{"state":"closed","confidence":1,"extra":"guess"}',
    '{"state":"closed","state":"open","confidence":1}',
    '```json\n{"state":"closed","confidence":1}\n```',
])
def test_malformed_model_output_cannot_pass(raw):
    with pytest.raises(ValueError):
        parse_verdict(raw)


def test_strict_local_qwen_request_and_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    def reply(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': '{"state":"closed","confidence":0.99}'}})
    checker = Checker(Qwen(url='http://127.0.0.1:11434', transport=httpx.MockTransport(reply)))
    result = asyncio.run(checker.check(photo(), closed_reference=photo()))
    assert result.passed and result.state == 'closed'
    assert len(seen[0]['messages'][0]['images']) == 2
    assert seen[0]['stream'] is False and seen[0]['format']['additionalProperties'] is False
    assert list(tmp_path.iterdir()) == []


def test_public_endpoint_never_receives_images():
    sent = []
    qwen = Qwen(url='https://example.com', transport=httpx.MockTransport(lambda r: sent.append(r)))
    assert asyncio.run(Checker(qwen).check(photo())) == Verdict()
    assert not sent


def test_errors_timeout_stale_and_low_confidence_fail_closed():
    class Method:
        async def classify(self, *args):
            return Verdict('closed', .89)
    assert not asyncio.run(Checker(Method()).check(photo())).passed
    class Slow:
        async def classify(self, *args):
            await asyncio.sleep(.05)
            return Verdict('closed', 1)
    assert asyncio.run(Checker(Slow(), timeout=.001).check(photo())) == Verdict()
    assert asyncio.run(Checker(Method()).check(photo(), captured_at=monotonic() - 2)) == Verdict()
    assert asyncio.run(Checker(Method()).check(b'not an image')) == Verdict()


def test_embedding_needs_both_references_and_abstains_on_ties():
    class Encoder:
        async def encode(self, image):
            return {'frame': [1., 0.], 'closed': [1., 0.], 'open': [0., 1.]}[image]
    method = Embedding(Encoder())
    assert asyncio.run(method.classify('frame', 'closed', None)) == Verdict()
    assert asyncio.run(method.classify('frame', 'closed', 'open')).state == 'closed'
    assert asyncio.run(method.classify('frame', 'closed', 'closed')) == Verdict()


def test_home_turn_uses_checker_and_rechecks_before_repair():
    from server.agent import Session, run_turn
    from server import packs
    class Method:
        answer = Verdict('closed', .99)
        calls = 0
        async def classify(self, *args):
            self.calls += 1
            return self.answer
    method = Method()
    session = Session('valve', pack=packs.get('home'))
    session.step_checker = Checker(method)
    session.home_guide.introduced = True
    session.home_guide.index = 2
    session.frame = {'seq': 1, 'jpeg_b64': base64.b64encode(photo()).decode()}
    session.frame_time = monotonic()
    result = asyncio.run(run_turn(session, 'next', vision=None, llm=None))
    assert session.home_guide.index == 3
    assert [m['detail'] for m in result if m['type'] == 'turn'] == ['Valve: checking', 'Valve: closed']
    method.answer = Verdict('open', .99)
    result = asyncio.run(run_turn(session, 'next', vision=None, llm=None))
    assert session.home_guide.index == 3 and not session.home_guide.water_off_passed
    assert next(m['text'] for m in result if m['type'] == 'say') == 'Show me the valve again, closer.'
    before = method.calls
    asyncio.run(run_turn(session, 'water near the outlet, next', vision=None, llm=None))
    assert method.calls == before and not session.home_guide.water_off_passed


def test_reference_consent_encryption_isolation_and_picture_accounting(tmp_path):
    from cryptography.fernet import Fernet
    from server.memory import Cipher, Store
    from server.proof_wall import picture_count
    from server.step_check_api import load_references, save_reference
    store = Store('owner', base=tmp_path, cipher=Cipher(Fernet.generate_key()))
    for consent in (None, False, 'true', 1):
        with pytest.raises(ValueError):
            save_reference(store, 'closed', photo(), consent)
    with pytest.raises(ValueError):
        save_reference(Store('plain', base=tmp_path, cipher=Cipher('')), 'closed', photo(), True)
    assert picture_count(tmp_path) == 0
    save_reference(store, 'closed', photo(), True)
    saved = (store.dir / 'valve-closed.jpg').read_bytes()
    assert not saved.startswith(b'\xff\xd8') and photo() not in saved
    assert load_references(store)[0].startswith(b'\xff\xd8')
    assert load_references(Store('other', base=tmp_path, cipher=store.cipher)) == (None, None)
    assert picture_count(tmp_path) == 1  # explicit saving must not be disguised as zero
    store.forget_all()
    assert picture_count(tmp_path) == 0


def test_http_api_runtime_boundary_and_errors_do_not_save_pictures(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.app import create_app
    from server.proof_wall import picture_count
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.setenv('A_EYE_MEMORY_DIR', str(tmp_path))
    monkeypatch.delenv('A_EYE_TOKEN', raising=False)
    class Method:
        async def classify(self, *args):
            return Verdict('closed', .99)
    app = create_app()
    app.state.step_checker = Checker(Method())
    with TestClient(app) as client:
        result = client.post('/api/step-check', json={'image_b64': base64.b64encode(photo()).decode()})
        assert result.json() == {'state': 'closed', 'confidence': .99}
        assert result.headers['cache-control'] == 'no-store'
        assert client.post('/api/step-check', json={'image_b64': 'bad'}).json()['state'] == 'unsure'
        assert client.post('/api/step-check', content='bad').json()['state'] == 'unsure'
    assert picture_count(tmp_path) == 0


def test_cancellation_revokes_pass_and_late_closed_is_rejected():
    class Slow:
        async def classify(self, *args):
            await asyncio.sleep(.03)
            return Verdict('closed', 1)
    assert asyncio.run(Checker(Slow()).check(photo(), captured_at=monotonic() - 1.49)) == Verdict()
    from server import packs
    from server.agent import Session, run_turn
    async def scenario():
        session = Session('cancel', pack=packs.get('home'))
        session.home_guide.introduced = True
        session.home_guide.index = 3
        session.home_guide.water_off_passed = True
        session.step_checker = Checker(Slow())
        session.frame = {'seq': 1, 'jpeg_b64': base64.b64encode(photo()).decode()}
        task = asyncio.create_task(run_turn(session, 'next', vision=None, llm=None))
        await asyncio.sleep(.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.home_guide.index == 3 and not session.home_guide.water_off_passed
    asyncio.run(scenario())


def test_real_loopback_api_to_qwen_protocol_keeps_pictures_zero(tmp_path, monkeypatch, capsys):
    """Real HTTP twice, through the app and local model boundary; model response is a synthetic stand-in."""
    import socket
    import threading
    import time
    import uvicorn
    from fastapi import Request
    from server.app import create_app
    from server.proof_wall import picture_count
    monkeypatch.setenv('A_EYE_MOCK', '1')
    monkeypatch.setenv('A_EYE_MEMORY_DIR', str(tmp_path))
    for name in ('A_EYE_TOKEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD'):
        monkeypatch.delenv(name, raising=False)
    app = create_app()
    requests = []
    @app.post('/api/chat')
    async def local_model(request: Request):
        payload = await request.json()
        requests.append(len(payload['messages'][0]['images']))
        return {'message': {'content': '{"state":"closed","confidence":0.99}'}}
    # The production app mounts static files at / last; put this test provider before that mount.
    app.router.routes.insert(0, app.router.routes.pop())
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(8)
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        app.state.step_checker = Checker(Qwen(url=url))
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 5
            while not server.started and thread.is_alive() and monotonic() < deadline:
                time.sleep(.01)
            assert server.started
            encoded = base64.b64encode(photo()).decode()
            with httpx.Client(trust_env=False) as client:
                response = client.post(url + '/api/step-check', json={'image_b64': encoded})
            assert response.json() == {'state': 'closed', 'confidence': .99}
            assert requests == [1] and picture_count(tmp_path) == 0
            captured = capsys.readouterr()
            assert encoded not in captured.out + captured.err
        finally:
            server.should_exit = True
            thread.join(5)
        assert not thread.is_alive()


def test_activation_requires_real_positive_evidence_and_safe_threshold(tmp_path, monkeypatch):
    from server.step_check import configured_checker
    path = tmp_path / 'report.json'
    monkeypatch.setenv('STEP_CHECK_CONFIG', str(path))
    report = dict(synthetic=False, held_out_count=20, selected='qwen', qwen_model='local-test',
                  methods={'qwen': dict(false_closed=0, threshold=.99, matrix={'closed': {'closed': 9}})})
    path.write_text(json.dumps(report))
    assert configured_checker().method.model == 'local-test'
    for field, value in [('false_closed', 1), ('threshold', .8)]:
        changed = json.loads(json.dumps(report))
        changed['methods']['qwen'][field] = value
        path.write_text(json.dumps(changed))
        assert configured_checker().method is None
