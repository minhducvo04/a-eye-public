"""Local memory HTTP routes under /api/memory (behind the /api auth check).

The memory is the caller's own: its id comes from server.rewind.identity(request), never from the client, so one
person can never read or delete another's. Contract shared with client-web/memory.js:
  GET  /api/memory/status?session_id=   -> {on, count, kept_days, bytes, encrypted, crops, total, remembering}
  POST /api/memory/forget {session_id, scope: "last"|"today"|"item"|"everything", item?} -> {count, deleted, ...}
  GET  /api/memory/settings             -> {consent: {person, caregiver}, items: [{item, home}], rooms, kept_days,
                                            remembering}
  PUT  /api/memory/settings  settings   -> settings (both consents re-checked; bedroom and bathroom refused, 422)
  POST /api/memory/find {query, session_id?} -> find_item answer (rewind_hit fields plus place)
  POST /api/memory/tag {name, home?, label?, kind?} -> tag
"""
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import memory as mem
from server.memory import hit_fields
from server.rewind import identity, registry

router = APIRouter(prefix='/api/memory')


async def body(request):
    try:
        value = await request.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def mine(request):
    return mem.memories(request.app).for_owner(identity(request))


def own_call(request, session_id):
    return registry(request.app).get(session_id, identity(request)) if session_id else None


@router.get('/status')
async def status(request: Request, session_id: str = ''):
    store = mine(request)
    rewind = own_call(request, session_id)
    info = mem.status(store)
    return dict(on=bool(rewind is not None and rewind.memory_writer is not None), count=store.count_today(),
                total=info['count'], kept_days=store.settings()['kept_days'], bytes=info['bytes'],
                crops=info['crops'], encrypted=info['encrypted'], remembering=store.remembering())


@router.get('/settings')
async def get_settings(request: Request):
    return mine(request).settings()


@router.put('/settings')
async def put_settings(request: Request):
    store = mine(request)
    try:
        saved = store.save_settings(await body(request))
    except (ValueError, TypeError):
        return JSONResponse({'error': 'refused_room'}, status_code=422)
    for rewind in list(registry(request.app).calls.values()):   # this person's live calls follow the switch now
        if rewind.owner == identity(request):
            mem.memories(request.app).auto_attach(rewind)
    return saved


@router.post('/forget')
async def forget(request: Request):
    """Real deletion. last: the item just discussed in this call (else the last 5 minutes); today; item;
    everything. TwelveLabs videos of this person go too for today and everything."""
    value = await body(request)
    store = mine(request)
    scope = value.get('scope')
    rewind = own_call(request, value.get('session_id'))
    session = getattr(rewind, 'session', None)
    cloud = (0, 0)
    item = value.get('item') if isinstance(value.get('item'), str) else None
    if scope == 'last':
        item = item or getattr(session, 'memory_last_item', None)
        deleted = store.forget_item(item) if item else store.forget_since(store.clock() - 300)
        if rewind is not None and rewind.clips:
            await rewind.forget(local=True)       # "forget that" also clears this call's Rewind clips
    elif scope == 'item' and item and item.strip():
        deleted = store.forget_item(item)
    elif scope == 'today':
        since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        cloud = await mem.delete_cloud(store, since=since)
        deleted = store.forget_since(since)
    elif scope == 'everything':
        cloud = await mem.delete_cloud(store)
        deleted = store.forget_all()
    else:
        return JSONResponse({'error': 'invalid_body'}, status_code=422)
    out = dict(count=store.count_today(), total=store.count(), deleted=deleted, cloud_deleted=cloud[0],
               cloud_failed=cloud[1])
    if item:
        out.update(item=item, count_item=store.count(item))
    return out


@router.post('/find')
async def find(request: Request):
    value = await body(request)
    query = value.get('query')
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        return JSONResponse({'error': 'invalid_body'}, status_code=422)
    rewind = own_call(request, value.get('session_id'))
    session = getattr(rewind, 'session', None)
    memories = mem.memories(request.app)
    frame = getattr(session, 'frame', None) if getattr(session, 'camera', False) else None
    result = await mem.find(mine(request), query, embedder=memories.embedder, scene=getattr(session, 'scene', None),
                            vision=memories.vision if frame else None, live_frame=frame and frame['jpeg_b64'])
    if session is not None:
        session.memory_last_item = result.get('item')
    return hit_fields(result, getattr(rewind, 'started_wall', None))


@router.post('/tag')
async def tag(request: Request):
    """Caregiver tag: an object ("blue pill box") or a place ("key bowl"), with an optional home place."""
    value = await body(request)
    store = mine(request)
    try:
        saved = store.set_tag(value.get('name'), value.get('label'), value.get('home'), value.get('kind', 'object'),
                              place_path=value.get('place_path'))
    except ValueError:
        return JSONResponse({'error': 'invalid_body'}, status_code=422)
    return dict(tag=saved, **mem.status(store))


@router.post('/event')
async def capture_event(request: Request):
    """Bounded RAM-only still burst. Same owner and consent gates as local memory."""
    from server.memory_capture import capture, worth_noting
    import asyncio
    chunks = bytearray()
    value = {}
    try:
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > 610000:
                return JSONResponse({'error': 'too_large'}, status_code=413)
        import json
        value = json.loads(chunks)
        chunks.clear()
        if not isinstance(value, dict):
            raise ValueError('invalid body')
        judge = getattr(request.app.state, 'capture_judge', worth_noting)
        return await asyncio.wait_for(capture(mine(request), value.get('frames'), judge), 9)
    except (ValueError, TypeError):
        return JSONResponse({'error': 'invalid_event'}, status_code=422)
    except Exception:
        return JSONResponse({'error': 'local_capture_unavailable'}, status_code=503)
    finally:
        chunks.clear()
        if isinstance(value, dict):
            frames = value.pop('frames', None)
            if isinstance(frames, list):
                frames.clear()
