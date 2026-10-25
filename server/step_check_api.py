"""Explicit, encrypted valve references in the caller's existing local memory.

Live checking never calls save_reference. Image filenames intentionally remain countable by the
proof strip: a user who opts to save a reference has saved a picture, unlike the default RAM flow.
"""
import asyncio
import base64
import json
from time import monotonic

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server.memory_api import mine
from server.step_check import Verdict, configured_checker, image_bytes

router = APIRouter(prefix='/api/step-check')


@router.post('')
async def check_frame(request: Request):
    """RAM-only {image_b64, closed_reference_b64?, open_reference_b64?} -> {state, confidence}.

    This inspection endpoint cannot set a session's water flag. The home flow checks its own current
    camera frame again. The request deadline includes upload time and image validation.
    """
    started = monotonic()
    result = Verdict()
    chunks = bytearray()
    try:
        async with asyncio.timeout(8):
            async for chunk in request.stream():
                chunks.extend(chunk)
                if len(chunks) > 16_100_000:
                    raise ValueError('too large')
            value = json.loads(chunks)
            frame = base64.b64decode(value['image_b64'], validate=True)
            references = [base64.b64decode(value[key], validate=True) if value.get(key) is not None else None
                          for key in ('closed_reference_b64', 'open_reference_b64')]
            checker = getattr(request.app.state, 'step_checker', None) or configured_checker()
            result = await checker.check(frame, *references, captured_at=started)
    except Exception:
        pass
    finally:
        chunks.clear()
    return JSONResponse(dict(state=result.state, confidence=result.confidence),
                        headers={'Cache-Control': 'no-store'})


def save_reference(store, state, raw, consent):
    if consent is not True or not store.cipher.encrypted:
        raise ValueError('explicit consent and memory encryption required')
    if state not in ('closed', 'open'):
        raise ValueError('invalid reference state')
    jpeg = image_bytes(raw)
    store.dir.mkdir(parents=True, exist_ok=True)
    path = store.dir / ('valve-' + state + '.jpg')
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_bytes(store.cipher.seal(jpeg))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_references(store):
    if store is None or not store.cipher.encrypted:
        return None, None
    images = []
    for state in ('closed', 'open'):
        path = store.dir / ('valve-' + state + '.jpg')
        if not path.exists():
            images.append(None)
        else:
            # Corruption raises; the caller must abstain, not silently switch to zero-shot.
            images.append(image_bytes(store.cipher.open(path.read_bytes())))
    return tuple(images)


@router.put('/reference')
async def put_reference(request: Request):
    chunks = bytearray()
    try:
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > 5_400_000:
                return JSONResponse({'error': 'too_large'}, status_code=413)
        value = json.loads(chunks)
        if not isinstance(value, dict) or value.get('consent') is not True:
            raise ValueError('consent required')
        raw = base64.b64decode(value['image_b64'], validate=True)
        save_reference(mine(request), value.get('state'), raw, value['consent'])
        return {'saved': True, 'encrypted': True}
    except Exception:
        return JSONResponse({'error': 'reference_not_saved'}, status_code=422)
    finally:
        chunks.clear()


@router.delete('/reference')
async def delete_reference(request: Request):
    store = mine(request)
    for state in ('closed', 'open'):
        (store.dir / ('valve-' + state + '.jpg')).unlink(missing_ok=True)
    return {'saved': False}
