"""Memory cloud consent, local fallback, and encrypted storage regressions."""
import asyncio
import contextvars

import pytest
from cryptography.fernet import Fernet

from server import memory, privacy
from server.test_memory import FRAME, KEYS, Words, Person


@pytest.mark.parametrize('private,google,local,cloud', [
    (True, True, False, False), (False, False, False, False),
    (False, True, True, False), (False, True, False, True),
])
def test_memory_caption_and_tags_consent(tmp_path, monkeypatch, private, google, local, cloud):
    monkeypatch.setenv('LOCAL_ONLY', '1' if local else '0')
    monkeypatch.setattr(privacy, 'PRIVATE', contextvars.ContextVar('private', default=False))
    monkeypatch.setattr(privacy, 'SWITCHES', contextvars.ContextVar('switches', default=None))
    privacy.set_call({'google': google}, private=private)
    calls = []

    def scene(self, body, scenario='ok'):
        calls.append(body)
        return {'caption': 'keys on the blue bowl', 'objects': [KEYS]}

    monkeypatch.setattr('vision.gemini.GeminiBackend.scene', scene)
    store = memory.Store('test', base=tmp_path, cipher=memory.Cipher(Fernet.generate_key()))
    store.set_tag('keys')
    writer = memory.Writer(store, 'call', embedder=Words(), person_check=Person())
    result = asyncio.run(writer.observe({'caption': 'local keys', 'objects': [KEYS]}, FRAME))
    assert bool(calls) is cloud
    assert result['note'] == ('keys on the blue bowl' if cloud else 'local keys')
    assert result['tags'][0]['label'] == 'keys'
    assert b'keys' not in (store.dir / 'log.jsonl').read_bytes()
    assert store.load_crop(result['crops']['keys']).startswith(b'\xff\xd8')
    assert not next((store.dir / 'crops').iterdir()).read_bytes().startswith(b'\xff\xd8')


@pytest.mark.parametrize('private,google,local', [(False, False, False), (True, True, False),
                                                (False, True, True), (False, True, False)])
def test_capture_note_consent(monkeypatch, private, google, local):
    monkeypatch.setenv('LOCAL_ONLY', '1' if local else '0')
    allowed = google and not private and not local
    from server.memory_capture import worth_noting
    import httpx
    calls = []
    def request(self, body, prompt):
        calls.append('google')
        return {'worth': True, 'note': 'keys on bowl'}
    async def post(self, url, **kwargs):
        calls.append('local')
        return httpx.Response(200, json={'message': {'content': '{"worth":true,"note":"local keys"}'}},
                              request=httpx.Request('POST', url))
    monkeypatch.setattr('vision.gemini.GeminiBackend.request', request)
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    async def run():
        privacy.set_call({'google': google}, private=private)
        return await worth_noting([FRAME])
    answer = asyncio.run(run())
    assert calls == (['google'] if allowed else ['local'])
    assert answer['worth'] is True


def test_local_grounding_ignores_global_cloud_backend(monkeypatch):
    monkeypatch.setenv('VISION_BACKEND', 'gemini')
    calls = []
    def scene(self, body, scenario):
        calls.append('qwen')
        return {'caption': 'local keys', 'objects': [KEYS]}
    def forbidden(*args, **kwargs):
        pytest.fail('Gemini must not run for local grounding')
    monkeypatch.setattr('vision.ollama.OllamaBackend.scene', scene)
    monkeypatch.setattr('vision.gemini.GeminiBackend.scene', forbidden)
    result = asyncio.run(memory.MemoryVision().call('scene', {'image_b64': FRAME}))
    assert calls == ['qwen'] and result['objects'] == [KEYS]


def test_failed_gemini_keeps_local_caption(tmp_path, monkeypatch):
    monkeypatch.delenv('LOCAL_ONLY', raising=False)
    monkeypatch.setattr('vision.gemini.GeminiBackend.scene',
                        lambda *args: {'caption': 'unsure', 'objects': []})
    store = memory.Store('test', base=tmp_path)
    writer = memory.Writer(store, 'call', embedder=Words(), person_check=Person())
    async def run():
        privacy.set_call({'google': True})
        return await writer.observe({'caption': 'local keys', 'objects': [KEYS]}, FRAME)
    assert asyncio.run(run())['note'] == 'local keys'
