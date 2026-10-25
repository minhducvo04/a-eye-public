"""Consent-gated Gemini vision. Images are resized in memory and never retained."""
import base64
from io import BytesIO
import json
import math
import os
from urllib.parse import quote

import httpx
from PIL import Image

from server import cloud_meter, privacy  # installs outgoing HTTP accounting
from vision.ollama import headers_for, match_header, normalized

DEFAULT_MODEL = 'gemini-flash-latest'


def unsure(operation):
    return {
        'scene': {'caption': 'unsure', 'objects': []},
        'identify': {'item': 'unknown', 'conf': 0},
        'locate': {'found': False, 'conf': 0},
        'verify': {'pass': False, 'abstain': True, 'conf': 0, 'reason': 'unsure'},
    }[operation]


def native_box(value):
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in value):
        return None
    y1, x1, y2, x2 = value
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1 / 1000, y1 / 1000, (x2 - x1) / 1000, (y2 - y1) / 1000]


class GeminiBackend:
    def __init__(self, transport=None):
        self.transport = transport
        self.model = os.environ.get('GEMINI_VISION_MODEL') or DEFAULT_MODEL
        self.last_error = None

    def health(self):
        return {'ok': bool(os.environ.get('GEMINI_API_KEY')), 'models': [self.model],
                'gpu': 'cloud', 'fake': False}

    def request(self, body, prompt):
        self.last_error = None
        # Check at the actual send boundary, including standalone/service use.
        if not privacy.google_allowed() or not os.environ.get('GEMINI_API_KEY'):
            self.last_error = 'blocked_or_unconfigured'
            return None
        try:
            with Image.open(BytesIO(base64.b64decode(body['image_b64'], validate=True))) as original:
                image = original.convert('RGB')
                image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                image.save(buffer, format='JPEG')
            payload = {'contents': [{'parts': [{'text': prompt}, {'inline_data': {
                'mime_type': 'image/jpeg', 'data': base64.b64encode(buffer.getvalue()).decode('ascii')}}]}],
                'generationConfig': {'temperature': 0, 'maxOutputTokens': 512,
                                     'responseMimeType': 'application/json'}}
            with httpx.Client(transport=self.transport, timeout=4, follow_redirects=False) as client:
                if not privacy.google_allowed():
                    return None
                response = client.post('https://generativelanguage.googleapis.com/v1beta/models/' +
                    quote(self.model, safe='') + ':generateContent', json=payload,
                    headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']})
                response.raise_for_status()
                candidate = response.json()['candidates'][0]
                if candidate.get('finishReason') != 'STOP':
                    self.last_error = 'incomplete_response'
                    return None
                text = ''.join(p['text'] for p in candidate['content']['parts']
                               if 'text' in p and not p.get('thought'))
                return json.loads(text)
        except httpx.HTTPStatusError as exc:
            # Only expose locally generated codes, never provider text or request URLs.
            status = exc.response.status_code
            self.last_error = 'http_402_payment_required' if status == 402 else f'http_{status}'
            return None
        except httpx.TimeoutException:
            self.last_error = 'timeout'
            return None
        except httpx.HTTPError:
            self.last_error = 'network_error'
            return None
        except (ValueError, OSError, KeyError, IndexError, TypeError):
            # Never log exceptions: provider errors can contain credentials or image content.
            self.last_error = 'invalid_image_or_response'
            return None

    def scene(self, body, scenario='ok'):
        queries = body.get('queries', [])[:3]
        limit = body.get('max_objects', 8)
        prompt = ('Describe the visible frame briefly and locate only these requested objects: ' +
                  json.dumps(queries) + '. Return JSON {"caption":str,"objects":[{"label":str,'
                  '"box_2d":[y_min,x_min,y_max,x_max]}]}. Coordinates are 0 to 1000. '
                  'Use exact requested labels. If absent, blurred, obscured or uncertain, omit the object. '
                  'Never infer a location. If uncertain return {"caption":"unsure","objects":[]}.')
        answer = self.request(body, prompt)
        if not isinstance(answer, dict) or not isinstance(answer.get('objects'), list):
            return unsure('scene')
        objects = []
        for row in answer['objects']:
            if not isinstance(row, dict):
                continue
            label = row.get('label')
            box = native_box(row.get('box_2d'))
            if not isinstance(label, str) or not label.strip() or not box:
                continue
            if queries and not any(normalized(label) == normalized(q) for q in queries):
                continue
            if len(objects) >= limit:
                break
            objects.append(dict(id=f'o{len(objects) + 1}', label=label, bbox=box, conf=.7))
        caption = answer.get('caption')
        return {'caption': caption if isinstance(caption, str) else 'unsure', 'objects': objects}

    def locate(self, body, scenario='ok'):
        target, _ = match_header(body['header'], headers_for(body['board_id']))
        if target is None:
            return unsure('locate')
        label = target.get('silkscreen', target['name'])
        scene = self.scene(dict(image_b64=body['image_b64'], queries=[label], max_objects=1))
        if not scene['objects']:
            return unsure('locate')
        return dict(found=True, bbox=scene['objects'][0]['bbox'], conf=.7, source='ocr')

    def identify(self, body, scenario='ok'):
        answer = self.request(body, 'Identify the visible held part from ' + json.dumps(body['candidates']) +
                              '. Return JSON {"item":str}. If uncertain use "unknown".')
        item = answer.get('item') if isinstance(answer, dict) else None
        return {'item': item, 'conf': .7} if item in body['candidates'] else unsure('identify')

    def verify(self, body, scenario='ok'):
        # A single cloud frame cannot independently establish a safe connection.
        return unsure('verify')


class CloudFallbackBackend:
    def __init__(self, ollama, gemini=None):
        self.ollama = ollama
        self.gemini = gemini or GeminiBackend()

    def health(self):
        return self.gemini.health()

    def call(self, operation, body, scenario):
        try:
            return getattr(self.ollama, operation)(body, scenario)
        except (httpx.ConnectError, httpx.TimeoutException):
            return getattr(self.gemini, operation)(body, scenario)

    def scene(self, body, scenario):
        return self.call('scene', body, scenario)

    def identify(self, body, scenario):
        return self.call('identify', body, scenario)

    def locate(self, body, scenario):
        return self.call('locate', body, scenario)

    def verify(self, body, scenario):
        return self.call('verify', body, scenario)
