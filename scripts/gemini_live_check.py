#!/usr/bin/env python3
"""One real Live session with a synthetic blue frame; no media is written to disk."""
import asyncio
import base64
import io
import json
import os
import re
from pathlib import Path
import sys
from time import perf_counter
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def check():
    from PIL import Image
    from websockets.asyncio.client import connect
    from server import privacy
    from server.gemini_live import discover_model, live_setup, ENDPOINT, WIRE_LOGGER

    if privacy.local_only():
        print('Private: Google blocked')
        return 2
    privacy.set_call({'google': True})
    try:
        model = await discover_model()
        buffer = io.BytesIO()
        Image.new('RGB', (320, 240), 'blue').save(buffer, 'JPEG')
        async with connect(ENDPOINT + '?key=' + quote(os.environ['GEMINI_API_KEY'], safe=''),
                           open_timeout=10, close_timeout=2, logger=WIRE_LOGGER) as ws:
            await ws.send(json.dumps({'setup': live_setup(model)}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if 'setupComplete' not in ready:
                raise ValueError('setup failed')
            await ws.send(json.dumps({'realtimeInput': {'video': {'mimeType': 'image/jpeg',
                'data': base64.b64encode(buffer.getvalue()).decode()}}}))
            # Pre-roll the camera before asking; no expected color in the prompt.
            await asyncio.sleep(1)
            started = perf_counter()
            await ws.send(json.dumps({'realtimeInput': {'text': 'what do you see?'}}))
            first_audio, reply = None, []
            async with asyncio.timeout(40):
                while True:
                    response = json.loads(await ws.recv())
                    if 'error' in response:
                        raise ValueError('provider error')
                    content = response.get('serverContent', {})
                    if first_audio is None and any('inlineData' in p for p in content.get('modelTurn', {}).get('parts', [])):
                        first_audio = round((perf_counter() - started) * 1000)
                    text = content.get('outputTranscription', {}).get('text')
                    if text:
                        reply.append(text)
                    if content.get('turnComplete'):
                        break
            print('model: ' + model)
            print('first-audio ms: ' + (str(first_audio) if first_audio is not None else 'none'))
            print('reply text: ' + ''.join(reply))
            passed = first_audio is not None and sees_blue(''.join(reply))
            print('blue-image check: ' + ('PASS' if passed else 'FAIL'))
            return 0 if passed else 1
    except Exception:
        # Provider exceptions may include the key in their request URL. Never print them.
        print('Live check failed (discovery, connection or reply unavailable)')
        return 1


def sees_blue(reply):
    return bool(re.search(r'\bblue\b', reply, re.I)) and not re.search(
        r"\b(?:can't|cannot|don't|do not|unable to)\s+(?:see|have eyes)|\bnot blue\b", reply, re.I)


def main():
    if not os.environ.get('GEMINI_API_KEY'):
        print('not configured')
        return 2
    return asyncio.run(check())


if __name__ == '__main__':
    raise SystemExit(main())
