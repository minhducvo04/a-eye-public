import base64
from io import BytesIO
import json

import httpx
from PIL import Image

from server import privacy


def test_openai_recorded_response(monkeypatch):
    from vision.app import select_backend
    monkeypatch.setenv('VISION_BACKEND', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.delenv('OPENAI_VISION_MODEL', raising=False)
    monkeypatch.delenv('LOCAL_ONLY', raising=False)
    recorded = {'choices': [{'finish_reason': 'stop', 'message': {'content':
        '{"caption":"A cup.","objects":[{"label":"cup","box_2d":[100,200,500,600]}]}'}}]}
    calls = []

    def response(request):
        calls.append(request.url)
        assert str(request.url) == 'https://api.openai.com/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-key'
        payload = json.loads(request.content)
        assert payload['model'] == 'gpt-4.1-mini'
        assert payload['response_format'] == {'type': 'json_object'}
        content = payload['messages'][0]['content']
        raw = content[1]['image_url']['url'].split(',', 1)[1]
        assert Image.open(BytesIO(base64.b64decode(raw))).size == (1280, 960)
        return httpx.Response(200, json=recorded)

    buffer = BytesIO()
    Image.new('RGB', (1600, 1200), 'white').save(buffer, 'JPEG')
    body = {'image_b64': base64.b64encode(buffer.getvalue()).decode(), 'queries': ['cup']}
    backend = select_backend()
    backend.transport = httpx.MockTransport(response)
    token = privacy.PRIVATE.set(False)
    try:
        assert backend.scene(body) == {'caption': 'A cup.', 'objects': [
            {'id': 'o1', 'label': 'cup', 'bbox': [.2, .1, .4, .4], 'conf': .7}]}
        privacy.PRIVATE.set(True)
        assert backend.scene(body)['objects'] == []
        privacy.PRIVATE.set(False)
        monkeypatch.setenv('LOCAL_ONLY', '1')
        assert backend.scene(body)['objects'] == []
        assert len(calls) == 1
    finally:
        privacy.PRIVATE.reset(token)
