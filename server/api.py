"""One-origin phone API and bounded, cached dependency checks."""
import asyncio
import os
from time import monotonic

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jsonschema import ValidationError

from contracts import validate
from market.search import search
from server.box_client import box_url
from server.logs import event
from server.ws2 import SceneClient

router = APIRouter(prefix='/api')


async def checked(request, request_schema, response_schema, operation):
    try:
        body = await request.json()
        validate(request_schema, body)
    except (ValueError, ValidationError, KeyError):
        return JSONResponse({'error': 'invalid_body'}, status_code=422)
    try:
        result = await operation(body)
        validate(response_schema, result)
        return result
    except (ValidationError, ValueError, KeyError):
        return JSONResponse({'error': 'invalid_response'}, status_code=422)
    except Exception:
        event('dependency_error', level='error', dependency=request_schema)
        return JSONResponse({'error': 'dependency_unavailable'}, status_code=503)


@router.post('/scene')
async def scene(request: Request):
    vision = getattr(request.app.state, 'vision_v2', None) or SceneClient()
    return await checked(request, 'scene_request', 'scene_response',
                         lambda body: vision.call('scene', body))


@router.post('/market')
async def market(request: Request):
    return await checked(request, 'market_query', 'market_response',
                         lambda body: asyncio.to_thread(search, body))


async def live_check(name):
    async with httpx.AsyncClient(timeout=2) as client:
        async def get(url, **kwargs):
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response.json()

        if name == 'vision':
            body = await get(os.environ.get('VISION_URL', 'http://127.0.0.1:8001').rstrip('/') + '/health')
            return body.get('ok') is True, 'fake' if body.get('fake') else 'gpu'
        if name in ('boxsvc', 'stt', 'tts'):
            mode = os.environ.get('VOICE', 'auto')
            if name == 'tts' and mode not in ('auto', 'kokoro', 'cartesia'):
                return True, 'browser'
            body = {}
            try:
                body = await get(box_url() + '/health')
            except Exception:
                if name != 'tts' or mode == 'kokoro':
                    raise
            if name == 'boxsvc':
                return body.get('ok') is True, 'boxsvc'
            if name == 'stt':
                return body.get('stt') is True, 'boxsvc'
            if mode in ('auto', 'kokoro') and body.get('tts') is True:
                return True, 'kokoro'
            if mode in ('auto', 'cartesia') and os.environ.get('CARTESIA_API_KEY'):
                await get('https://api.cartesia.ai/voices', headers={
                    'X-API-Key': os.environ['CARTESIA_API_KEY'], 'Cartesia-Version': '2024-06-10'})
                return True, 'cartesia'
            return mode != 'kokoro', 'browser'
        provider = os.environ.get('LLM_PROVIDER', 'auto')
        if provider not in ('auto', 'claude', 'local'):
            return True, 'canned'
        if (provider == 'claude' or (provider == 'auto' and os.environ.get('ANTHROPIC_API_KEY'))) and os.environ.get('LOCAL_ONLY') != '1':
            try:
                await get('https://api.anthropic.com/v1/models', headers={
                    'x-api-key': os.environ.get('ANTHROPIC_API_KEY', ''), 'anthropic-version': '2023-06-01'})
                return True, 'claude'
            except Exception:
                event('fallback_used', dependency='brain', to='local')
        body = await get(os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/') + '/api/tags')
        from server.llm import LOCAL_DEFAULT
        model = os.environ.get('LOCAL_LLM_MODEL', LOCAL_DEFAULT)
        return any(m.get('name') == model for m in body.get('models', [])), 'local'


async def dependency(app, name):
    started = monotonic()
    try:
        if app.state.mock:
            ok, detail = True, {'vision': 'fake', 'brain': 'scripted', 'tts': 'tone',
                                'stt': 'fixed', 'boxsvc': 'mock'}[name]
        else:
            check = getattr(app.state, 'dependency_check', live_check)
            ok, detail = await asyncio.wait_for(check(name), timeout=2)
        if not ok:
            event('dependency_error', dependency=name, level='warning')
    except Exception:
        ok, detail = False, 'unavailable'
        event('dependency_error', dependency=name, level='warning')
    return dict(ok=ok, ms=round((monotonic() - started) * 1000, 2), detail=detail)


@router.get('/voices')
async def voices():
    """Server voices the picker can offer, most natural first. Cartesia appears only when keyed."""
    from voice.kokoro import KOKORO_VOICES, default_voice
    cartesia = bool(os.environ.get('CARTESIA_API_KEY'))
    listed = [dict(id=v, label=f'Kokoro {v}', engine='kokoro', default=not cartesia and v == default_voice())
              for v in KOKORO_VOICES]
    if cartesia:
        from voice import cartesia_ws
        listed = [dict(id='cartesia:' + vid, label=f'Cartesia {name}', engine='cartesia',
                       default=vid == cartesia_ws.default_voice()) for vid, name in cartesia_ws.VOICES] + listed
    return dict(voices=listed)


@router.get('/voice_sample')
async def voice_sample(voice: str = ''):
    """A short Kokoro sample for the Test voice button: raw PCM16 16 kHz, base64 in JSON."""
    import base64
    from voice.kokoro import KOKORO_VOICES, default_voice
    line = "Hey, I can hear you. Show me what you're working on."
    if voice.startswith('cartesia:') and os.environ.get('CARTESIA_API_KEY'):
        from voice.cartesia_ws import CartesiaWsAdapter
        speaker = CartesiaWsAdapter()
        speaker.voice = voice
        try:
            pcm = b''.join([chunk async for chunk in speaker.say('sample', line)])
        except Exception:
            return JSONResponse(dict(error='voice_unavailable'), status_code=503)
        finally:
            await speaker.aclose()
        return dict(voice=voice, sample_rate=16000, pcm16_b64=base64.b64encode(pcm).decode())
    picked = voice if voice in KOKORO_VOICES else default_voice()
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            response = await client.post(box_url() + '/tts', json=dict(
                text=line, voice=picked))
            response.raise_for_status()
    except Exception:
        return JSONResponse(dict(error='voice_unavailable'), status_code=503)
    return dict(voice=picked, sample_rate=16000, pcm16_b64=base64.b64encode(response.content).decode())


@router.get('/status')
async def status(request: Request):
    app = request.app
    async with app.state.status_lock:
        cached = app.state.status_cache
        if cached and monotonic() - cached[0] < 10:
            return cached[1]
        names = ('vision', 'boxsvc', 'brain', 'tts', 'stt')
        results = await asyncio.gather(*(dependency(app, name) for name in names))
        report = dict(ok=all(item['ok'] for item in results),
                      mode='mock' if app.state.mock else 'live', version={'contracts': '2.2'},
                      deps=dict(zip(names, results)), features=dict(memory=True))
        app.state.status_cache = (monotonic(), report)
        return report
