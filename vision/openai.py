"""OpenAI vision with Gemini's normalized box mapping and abstention behavior."""
import base64
from io import BytesIO
import json
import os

import httpx
from PIL import Image

from server import privacy
from vision.gemini import GeminiBackend

DEFAULT_MODEL = 'gpt-4.1-mini'


class OpenAIBackend(GeminiBackend):
    url = 'https://api.openai.com/v1/chat/completions'
    key_env = 'OPENAI_API_KEY'
    model_env = 'OPENAI_VISION_MODEL'
    default_model = DEFAULT_MODEL

    def __init__(self, transport=None):
        self.transport = transport
        self.model = os.environ.get(self.model_env) or self.default_model

    def health(self):
        return {'ok': bool(os.environ.get(self.key_env)), 'models': [self.model],
                'gpu': 'cloud', 'fake': False}

    def request(self, body, prompt):
        if privacy.local_only() or not os.environ.get(self.key_env):
            return None
        try:
            with Image.open(BytesIO(base64.b64decode(body['image_b64'], validate=True))) as original:
                image = original.convert('RGB')
                image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                image.save(buffer, format='JPEG')
            payload = {'model': self.model, 'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt + ' Return strict JSON only.'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' +
                    base64.b64encode(buffer.getvalue()).decode('ascii')}}]}],
                'temperature': 0, 'max_tokens': 512, 'response_format': {'type': 'json_object'}}
            with httpx.Client(transport=self.transport, timeout=4, follow_redirects=False) as client:
                if privacy.local_only():
                    return None
                response = client.post(self.url, json=payload,
                    headers={'Authorization': 'Bearer ' + os.environ[self.key_env]})
                response.raise_for_status()
                choice = response.json()['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    return None
                return json.loads(choice['message']['content'])
        except (httpx.HTTPError, ValueError, OSError, KeyError, IndexError, TypeError):
            # Provider errors may contain credentials or image content; never log them.
            return None


class OpenRouterBackend(OpenAIBackend):
    """Same OpenAI-compatible call through OpenRouter, so one key can try many vision models."""
    url = 'https://openrouter.ai/api/v1/chat/completions'
    key_env = 'OPENROUTER_API_KEY'
    model_env = 'OPENROUTER_VISION_MODEL'
    default_model = 'google/gemini-2.5-flash'
