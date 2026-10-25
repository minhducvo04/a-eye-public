"""Local vision event gate; only a short text record can reach the memory store."""
import asyncio
import base64
import io
import json
import os
from uuid import uuid4

import httpx
from PIL import Image

from server.memory import PersonCheck
from server import privacy


async def worth_noting(frames):
    # HTTP capture has no call consent and stays local. Call tasks inherit consent.
    if privacy.google_allowed() and frames:
        from vision.gemini import GeminiBackend
        answer = await asyncio.to_thread(
            GeminiBackend().request, {'image_b64': frames[-1]},
            'Describe the visible object and where it is. Return JSON '
            '{"worth":true/false,"note":"at most 12 words"}. '
            'Use worth=false if uncertain; do not infer motion from this still.')
        if isinstance(answer, dict) and isinstance(answer.get('worth'), bool):
            return answer
    # Explicit home-box endpoint for missing consent or failed Gemini requests.
    url = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        response = await client.post(url + '/api/chat', json={
            'model': os.environ.get('VISION_CAPTION_MODEL', 'qwen2.5vl:7b-q4_K_M'),
            'stream': False, 'format': 'json',
            'messages': [{'role': 'user', 'content': 'Worth noting? An object placed, picked up, moved, or a step '
                          'finished? Return JSON {"worth":true/false,"note":"at most 12 words"}.',
                          'images': frames}]})
        response.raise_for_status()
        return json.loads(response.json()['message']['content'])


async def capture(store, frames, judge=worth_noting):
    try:
        if not store.remembering():
            return {'noted': False, 'reason': 'consent_required'}
        if not isinstance(frames, list) or not 1 <= len(frames) <= 3:
            raise ValueError('invalid frames')
        for value in frames:
            if not isinstance(value, str) or len(value) > 200000:
                raise ValueError('invalid frame')
            raw = base64.b64decode(value, validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'JPEG' or max(image.size) > 640:
                    raise ValueError('invalid image')
                image.verify()
        check = PersonCheck()
        for value in frames:
            try:
                person = await check(value)
            except Exception:
                return {'noted': False, 'reason': 'person_check_unavailable'}
            if person is not False:
                return {'noted': False, 'reason': 'person_or_unknown'}
        answer = await judge(frames)
        note = answer.get('note')
        if answer.get('worth') is not True or not isinstance(note, str):
            return {'noted': False}
        note = ' '.join(note.split()[:12])[:200]
        # Recheck consent after inference, before the sole text-only write.
        if not note or not store.remembering():
            return {'noted': False}
        store.purge_old()
        store.append(dict(id=uuid4().hex[:12], t=store.clock(), call='capture', source='capture', note=note,
                          labels=note.split(), tags=[], crops={}, emb=None))
        return {'noted': True}
    finally:
        if isinstance(frames, list):
            frames.clear()
