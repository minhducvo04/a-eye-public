"""Presenter-only, loopback controls. Not mounted by the normal application.

Only the explicit presenter launcher enables this router. Test mode starts off for
every socket; queued synthetic verdicts expire and are consumed by the next turn.
"""
from time import monotonic

from fastapi import APIRouter, HTTPException, Request
from server.family_note import body_fields, response
from server.auth import proxied
from server.rewind import identity, registry

router = APIRouter(prefix='/api/dad-demo')


def session_for(request, session_id):
    if (not getattr(request.app.state, 'presenter', False) or not request.client
            or request.client.host not in ('127.0.0.1', '::1')
            or proxied(request)):
        raise HTTPException(404)
    call = registry(request.app).get(session_id, identity(request))
    session = getattr(call, 'session', None)
    if not session or not session.private or session.pack.id != 'home':
        raise HTTPException(404)
    return session


@router.get('')
async def status(request: Request, session_id: str):
    session = session_for(request, session_id)
    return response({'enabled': session.checker_test_mode})


@router.post('')
async def configure(request: Request, session_id: str, action: str = "mode"):
    session = session_for(request, session_id)
    # Shared reader enforces bounded text and same-origin; two exact payload shapes.
    if action not in ('mode', 'verdict'):
        raise HTTPException(404)
    body = await body_fields(request, {'enabled'} if action == 'mode' else {'state'})
    if set(body) == {'enabled'} and type(body['enabled']) is bool:
        session.checker_test_mode = body['enabled']
        session.checker_test_verdict = None
        session.home_guide.water_off_passed = False
    elif set(body) == {'state'} and body['state'] in ('closed', 'open', 'unsure') and session.checker_test_mode:
        session.checker_test_verdict = (body['state'], monotonic())
    else:
        raise HTTPException(400, 'Enable presenter test mode first; choose a simulated verdict.')
    return response({'enabled': session.checker_test_mode})
