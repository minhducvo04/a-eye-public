"""Rewind HTTP routes under /api (token-checked by the app middleware). The TwelveLabs key stays server-side."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import cloud_meter
from server.rewind import NOTICE, identity, registry

_rewind = APIRouter(prefix='/api/rewind')


def public(hit):
    return {k: hit[k] for k in ('t_s', 'start_s', 'end_s', 'rank') if k in hit}


@_rewind.get('/status')
async def status(request: Request, session_id: str = ''):
    rewind = registry(request.app).for_question(session_id or None, identity(request))
    return dict(rewind.status() if rewind else dict(on=False, clips=0, ready=0, indexing=0, pending=0),
                notice=NOTICE)


async def body(request):
    try:
        value = await request.json()
    except ValueError:
        return None
    if isinstance(value, dict):
        call = registry(request.app).get(value.get('session_id'), identity(request))
        cloud_meter.CURRENT.set(getattr(call, 'cloud_meter', None))
        return value
    return None


@_rewind.post('/search')
async def search(request: Request):
    value = await body(request)
    query = value.get('query') if value else None
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        return JSONResponse({'error': 'invalid_body'}, status_code=422)
    rewind = registry(request.app).for_question(value.get('session_id'), identity(request))
    if rewind is None:
        return {'hits': []}
    try:
        hits = await rewind.search(query.strip())
    except Exception:
        return JSONResponse({'error': 'dependency_unavailable'}, status_code=503)
    return {'hits': [public(h) for h in hits]}


@_rewind.post('/flush')
async def flush(request: Request):
    value = await body(request)
    rewind = registry(request.app).get(value.get('session_id'), identity(request)) if value else None
    if rewind is None:
        return JSONResponse({'error': 'unknown_session'}, status_code=404)
    try:
        sent = await rewind.flush()
    except Exception:
        return JSONResponse({'error': 'dependency_unavailable'}, status_code=503)
    return dict(rewind.status(), uploaded=len(sent))


@_rewind.post('/recap')
async def recap(request: Request):
    """Recap of a call (also after it ended). Waits for the end-of-call upload, then asks Pegasus."""
    import asyncio
    from server.rewind import recap as make_recap, recap_line
    value = await body(request)
    rewind = registry(request.app).get(value.get('session_id'), identity(request)) if value else None
    if rewind is None:
        return JSONResponse({'error': 'unknown_session'}, status_code=404)
    for _ in range(20):         # the end-of-call recap can arrive just before the socket close is handled
        if rewind.end_task is not None or not rewind.on:
            break
        await asyncio.sleep(0.1)
    if rewind.end_task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(rewind.end_task), 60)
        except Exception:
            pass
    elif rewind.on and any(c.status == 'pending' for c in rewind.clips):
        await rewind.flush()
    try:
        result = await make_recap(rewind)
    except Exception:
        return JSONResponse({'error': 'dependency_unavailable'}, status_code=503)
    return dict(steps=result['steps'], skipped=result['skipped'], say=recap_line(result), ms=result['ms'],
                mode=result['mode'])


@_rewind.post('/forget')
async def forget(request: Request):
    """Forget this call: delete its TwelveLabs videos and every local clip and frame."""
    value = await body(request)
    rewind = registry(request.app).get(value.get('session_id'), identity(request)) if value else None
    if rewind is None:
        return JSONResponse({'error': 'unknown_session'}, status_code=404)
    rewind.set_on(False)
    return await rewind.forget(local=True)


# The app includes one router from this module; local memory rides along so app.py needs no change.
from server import memory_api  # noqa: E402

router = APIRouter()
router.include_router(_rewind)
router.include_router(memory_api.router)
