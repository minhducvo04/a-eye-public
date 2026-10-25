"""In-process fake HTTP provider; no external network or paid calls."""
import asyncio
import json

import httpx
import pytest

from scripts import provider_check
from server import privacy


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for key, value in dict(BRAIN='openai_compat', OPENAI_COMPAT_BASE_URL='https://fake.invalid/v1',
                           OPENAI_COMPAT_API_KEY='test-secret', OPENAI_COMPAT_TALKER_MODEL='talk',
                           OPENAI_COMPAT_EXPERT_MODEL='expert', LOCAL_ONLY='0').items():
        monkeypatch.setenv(key, value)
    private = privacy.PRIVATE.set(False)
    switches = privacy.SWITCHES.set(None)
    yield
    privacy.PRIVATE.reset(private)
    privacy.SWITCHES.reset(switches)


def fake_server(seen):
    def handle(request):
        body = json.loads(request.content)
        seen.append((request, body))
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-secret'
        return httpx.Response(200, text=''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in [
            {'choices': [{'delta': {'content': 'Ready.'}}]},
        ]) + 'data: [DONE]\n\n')
    return handle


def test_not_configured_exits_without_traceback(monkeypatch, capsys):
    monkeypatch.delenv('OPENAI_COMPAT_BASE_URL', raising=False)
    monkeypatch.delenv('OPENAI_COMPAT_API_KEY', raising=False)
    assert provider_check.main() == 2
    output = capsys.readouterr().out
    assert 'not configured' in output
    assert 'Traceback' not in output
    assert 'test-secret' not in output


def test_probe_against_fake_server(capsys):
    seen = []

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_server(seen))) as client:
            return await provider_check.probe(httpx_client=client)

    assert asyncio.run(run()) == 0
    output = capsys.readouterr().out
    assert 'host=fake.invalid' in output
    assert 'model=talk' in output
    assert 'first_token_ms=none' not in output
    assert 'first_token_ms=' in output
    assert 'total_ms=' in output
    assert 'Traceback' not in output
    assert 'test-secret' not in output
    assert seen and seen[0][1]['model'] == 'talk'
