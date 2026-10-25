"""Owner-scoped wake mailbox. Single-process like the existing call registry; no media/model calls."""
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal

from server.memory import Store, memories, person_id
from server.rewind import identity, registry

router = APIRouter(prefix='/api/trigger')


class Trigger(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    kind: Literal['presence', 'door', 'schedule', 'shortcut']
    zone: str = Field(min_length=1, max_length=80)


class Client(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    ack: str = ''
    listening: bool = False


class Presence(BaseModel):
    zone: str = Field(min_length=1, max_length=80)
    quiet_s: float = Field(default=30, ge=1, le=300)


def context(request):
    owner = identity(request)
    if owner == 'local':  # Unlike ordinary dev endpoints, a wake always requires credentials.
        raise HTTPException(401, 'Credentials required')
    if not hasattr(request.app.state, 'triggers'):
        request.app.state.triggers = {}
    entries = request.app.state.triggers
    now = monotonic()
    for key, value in list(entries.items()):
        if now - value['seen'] > 60:
            del entries[key]
    return owner, entries, now


def status(entry, now):
    if not entry or now - entry['seen'] > 5:
        return dict(state='OFF')
    awake = entry['until'] > now
    return dict(state=('SESSION' if entry['listening'] else 'EVENT') if awake else 'ARMED',
                camera_on=entry['camera'])


def zone_line(app, owner, zone):
    # Existing place tags provide explicit local zone reminders. Read-only: do not claim/create a store.
    store = Store(person_id(owner), base=memories(app).base)
    if store.owner() != owner:
        return 'I’m here. How can I help?'
    tag = store.tags().get(zone.strip().lower(), {})
    if tag.get('kind') == 'place' and tag.get('label') != tag.get('name'):
        return ' '.join(tag['label'].split())[:160]
    return 'I’m here. How can I help?'


@router.post('/client')
async def client_poll(body: Client, request: Request):
    owner, entries, now = context(request)
    call = registry(request.app).get(body.session_id, owner)
    if call is None or call.session is None:
        raise HTTPException(409, 'Open your agent call first')
    entry = entries.get(owner)
    if entry is None or entry['session'] != body.session_id:
        if len(entries) >= 256:
            raise HTTPException(503, 'Wake registry full')
        entry = entries[owner] = dict(session=body.session_id, seen=now, until=0, last=-100,
                                      wake=None, listening=False, camera=False, zone='')
    entry.update(seen=now, camera=call.session.camera)
    if entry['wake'] and body.ack == entry['wake']['id']:
        entry['listening'] = body.listening
        entry['wake'] = None
    elif entry['listening']:
        entry['listening'] = body.listening
    return dict(**status(entry, now), wake=entry['wake'] if entry['until'] > now else None)


@router.get('/status')
async def get_status(request: Request):
    owner, entries, now = context(request)
    return status(entries.get(owner), now)


@router.post('')
async def trigger(body: Trigger, request: Request):
    owner, entries, now = context(request)
    entry = entries.get(owner)
    if status(entry, now)['state'] == 'OFF':
        raise HTTPException(409, 'No live owner client')
    if now - entry['last'] < 5:
        raise HTTPException(429, 'Trigger cooldown', headers={'Retry-After': '5'})
    entry['last'] = now  # source/zone rotation cannot evade the owner limit
    if entry['until'] <= now:
        entry['listening'] = False
        entry['wake'] = dict(id=uuid4().hex, line=zone_line(request.app, owner, body.zone))
    entry.update(until=now + 30, zone=body.zone)
    return status(entry, now)


@router.post('/presence')
async def presence(body: Presence, request: Request):
    owner, entries, now = context(request)
    entry = entries.get(owner)
    if (status(entry, now)['state'] == 'OFF' or entry['zone'] != body.zone
            or entry['until'] <= now):
        raise HTTPException(409, 'Trigger first')
    entry['until'] = now + body.quiet_s
    return status(entry, now)
