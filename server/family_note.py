"""Task-scoped, text-only family notes. No model, media, disk writes or outbound delivery.

Contacts are explicitly entered into process-local data. Links are bearer capabilities:
exchange once, then submit one reply before the original 24-hour deadline.
"""
import hashlib
import re
import secrets
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from server.rewind import identity, registry

router = APIRouter()
TTL = 24 * 60 * 60
# Controlled, current-task facts: never free-form scene/history/memory extraction.
TASKS = {
    'ee_led': {
        'fixed': 'I finished an Arduino LED practice guide.',
        'details': ['I practiced the series resistor wiring.', 'I practiced checking LED polarity.',
                    'I practiced uploading Blink for pin 9.'],
        'question': 'Would you like to see the circuit?',
    },
    'sink': {
        'fixed': 'I fixed the sink leak.',
        'details': ['I tightened the fitting.', 'I replaced the washer.', 'I checked for drips.',
                    'I dried the fitting.', 'I used an adjustable wrench.', 'I checked the seal.',
                    'I replaced the seal.', 'I tested the tap.'],
        'question': 'How do you keep a sink fitting from loosening again?',
    },
    'battery': {
        'fixed': 'I replaced the smoke alarm battery.',
        'details': ['I tested the alarm.', 'I checked the battery type.', 'I closed the battery cover.',
                    'I checked the test button.', 'I followed the alarm instructions.', 'I checked the battery contacts.'],
        'question': 'How do you remember when to replace an alarm battery?',
    },
    'chair': {
        'fixed': 'I fixed the loose chair.',
        'details': ['I tightened the screws.', 'I checked the joints.', 'I used a screwdriver.',
                    'I checked all four legs.', 'I replaced a screw.', 'I tested for wobble.'],
        'question': 'How do you keep chair screws from working loose?',
    },
}


def build_note(log):
    if not isinstance(log, dict) or set(log) != {'task', 'detail'}:
        raise ValueError('Choose facts from this completed task only.')
    task = TASKS.get(log['task']) if isinstance(log['task'], str) else None
    index = log['detail']
    if not task or type(index) is not int or not 0 <= index < len(task['details']):
        raise ValueError('Unknown task fact.')
    return ' '.join((task['fixed'], task['details'][index], task['question']))


def validate_note(payload, log):
    if not isinstance(payload, dict) or set(payload) != {'text'}:
        raise ValueError('Text only; no media or additional fields.')
    text = payload['text']
    if not isinstance(text, str) or len(text) > 500:
        raise ValueError('Note is too long.')
    sentences = re.findall(r'[^.!?]+[.!?]', text)
    if not 1 <= len(sentences) <= 3 or text.count('?') != 1 or not text.endswith('?'):
        raise ValueError('At most three sentences, ending with one question.')
    # Exact allowed provenance is stronger than trying to detect private data in free text.
    if text != build_note(log):
        raise ValueError('Use only the confirmed facts from this task.')


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def short_text(value, limit):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and not any(ord(c) < 32 for c in value)


class Store:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.contacts = {}
        self.drafts = {}
        self.links = {}
        self.sessions = {}
        self.limits = {}

    def prune(self):
        now = self.clock()
        for table in (self.drafts, self.links, self.sessions):
            for key, row in list(table.items()):
                if row['expires'] <= now:
                    del table[key]
        for key, rows in list(self.limits.items()):
            if not rows or rows[-1] <= now - 60:
                del self.limits[key]

    def limit(self, key):
        self.prune()
        if key not in self.limits and len(self.limits) >= 2048:
            raise HTTPException(429, 'Try again later.')
        rows = self.limits.setdefault(key, deque())
        now = self.clock()
        while rows and rows[0] <= now - 60:
            rows.popleft()
        if len(rows) >= 60:
            raise HTTPException(429, 'Try again in a minute.')
        rows.append(now)

    def add_contact(self, owner, body):
        if (set(body) != {'name', 'relation', 'consent'} or body.get('consent') is not True
                or not short_text(body.get('name'), 60) or not short_text(body.get('relation'), 40)):
            raise ValueError('Enter a name, relation and explicit consent.')
        contacts = self.contacts.setdefault(owner, {})
        if len(contacts) >= 20 or len(self.contacts) > 1000:
            raise ValueError('Contact limit reached.')
        contact = dict(id=secrets.token_urlsafe(16), name=body['name'].strip(),
                       relation=body['relation'].strip(), delivery_link='')
        contacts[contact['id']] = contact
        return contact.copy()

    def remove_contact(self, owner, contact_id):
        self.contacts.get(owner, {}).pop(contact_id, None)
        for table in (self.drafts, self.links, self.sessions):
            for key, row in list(table.items()):
                if row['owner'] == owner and row['contact'] == contact_id:
                    del table[key]

    def draft(self, owner, log, contact_id):
        self.prune()
        contact = self.contacts.get(owner, {}).get(contact_id)
        if not contact:
            raise ValueError('Choose a consenting contact.')
        text = build_note(log)
        validate_note({'text': text}, log)
        if len(self.drafts) >= 1000:
            raise ValueError('Note limit reached.')
        # Any new draft invalidates the previous pending confirmation for this call.
        for row in self.drafts.values():
            if row['owner'] == owner and row['status'] == 'draft':
                row['status'] = 'cancelled'
        row = dict(id=secrets.token_urlsafe(16), owner=owner, contact=contact_id,
                   name=contact['name'], log=log.copy(), text=text, heard=False,
                   status='draft', expires=self.clock() + TTL, reply='')
        self.drafts[row['id']] = row
        return dict(id=row['id'], text=text, pictures_sent=0, cloud_ai_calls=0)

    def owned(self, owner, draft_id):
        self.prune()
        row = self.drafts.get(draft_id)
        if not row or row['owner'] != owner or row['status'] != 'draft':
            raise ValueError('Draft is unavailable; create a new one.')
        return row

    def heard(self, owner, draft_id):
        self.owned(owner, draft_id)['heard'] = True

    def send(self, owner, draft_id, confirmation):
        row = self.owned(owner, draft_id)
        # Anything except the closed-set confirmation cancels, including silence/error in the UI.
        if confirmation != 'send' or not row['heard']:
            row['status'] = 'cancelled'
            raise ValueError('Cancelled. Read the note aloud, then say send or tap Send.')
        validate_note({'text': row['text']}, row['log'])
        token = secrets.token_urlsafe(32)
        row['status'] = 'sent'
        row['expires'] = self.clock() + TTL
        self.links[digest(token)] = row
        link = '/family-reply.html#' + token  # fragment never enters access logs/referrers
        self.contacts[owner][row['contact']]['delivery_link'] = link
        return dict(delivery_link=link, pictures_sent=0, cloud_ai_calls=0)

    def exchange(self, token):
        self.prune()
        row = self.links.pop(digest(token), None)
        if not row:
            raise ValueError('This private link is invalid, used or expired.')
        session = secrets.token_urlsafe(32)
        self.sessions[digest(session)] = row
        return dict(text=row['text'], reply_token=session)

    def reply(self, token, text):
        self.prune()
        row = self.sessions.get(digest(token))
        if not row or row['reply']:
            raise ValueError('Reply session is invalid, used or expired.')
        if not short_text(text, 500):
            raise ValueError('Reply with 1 to 500 characters of text.')
        row['reply'] = text.strip()
        del self.sessions[digest(token)]

    def replies(self, owner):
        self.prune()
        return [dict(id=r['id'], name=r['name'], text=r['reply']) for r in self.drafts.values()
                if r['owner'] == owner and r['reply']]

    def consume_spoken(self, owner, text):
        self.prune()
        pending = next((r for r in self.drafts.values()
                        if r['owner'] == owner and r['status'] == 'draft'), None)
        if not pending:
            return False
        try:
            self.send(owner, pending['id'], str(text or '').strip().lower().rstrip('.!?').strip())
        except ValueError:
            pass  # Cancellation is polled by the call UI; never forward it to a brain.
        return True


def store(request):
    if not hasattr(request.app.state, 'family_notes'):
        request.app.state.family_notes = Store()
    return request.app.state.family_notes


def response(body):
    return JSONResponse(body, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                      'X-Content-Type-Options': 'nosniff'})


async def body_fields(request, fields):
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Same-origin requests only.')
    # Bound streamed input too, not just an attacker-controlled Content-Length.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4096:
            raise HTTPException(413, 'Text only, maximum 4096 bytes.')
    import json
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, 'Invalid JSON.') from None
    if not isinstance(body, dict) or set(body) != set(fields):
        raise HTTPException(400, 'Unexpected fields; text only.')
    return body


def owner_for(request, session_id):
    if not registry(request.app).get(session_id, identity(request)):
        raise HTTPException(404, 'Start your call first.')
    # Current call only, including on trusted localhost.
    return identity(request) + ':' + session_id


@router.get('/api/family')
async def status(request: Request, session_id: str):
    owner = owner_for(request, session_id)
    s = store(request)
    s.limit('owner:' + owner)
    latest = next((r for r in reversed(list(s.drafts.values())) if r['owner'] == owner), None)
    return response(dict(contacts=list(s.contacts.get(owner, {}).values()), tasks=TASKS,
                         replies=s.replies(owner), pictures_sent=0, cloud_ai_calls=0,
                         latest=({k: latest[k] for k in ('id', 'status', 'contact')} if latest else None)))


@router.post('/api/family/{action}')
async def action(request: Request, action: str, session_id: str):
    owner = owner_for(request, session_id)
    s = store(request)
    s.limit('owner:' + owner)
    fields = {'contact': {'name', 'relation', 'consent'}, 'remove': {'contact'},
              'draft': {'log', 'contact'}, 'heard': {'id'}, 'send': {'id', 'confirmation'}}
    if action not in fields:
        raise HTTPException(404)
    body = await body_fields(request, fields[action])
    try:
        if action == 'contact':
            result = s.add_contact(owner, body)
        elif action == 'remove':
            s.remove_contact(owner, body['contact'])
            result = {'ok': True}
        elif action == 'draft':
            result = s.draft(owner, body['log'], body['contact'])
        elif action == 'heard':
            s.heard(owner, body['id'])
            result = {'ok': True}
        else:
            result = s.send(owner, body['id'], body['confirmation'])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, 'Check consent and task facts; cancelled notes need a new draft.') from None
    return response(result)


@router.post('/family/{action}')
async def parent(request: Request, action: str):
    s = store(request)
    # Ignore forwarded headers: the actual peer is the rate-limit boundary.
    s.limit('parent:' + (request.client.host if request.client else 'unknown'))
    if action not in ('exchange', 'reply'):
        raise HTTPException(404)
    body = await body_fields(request, {'token'} if action == 'exchange' else {'token', 'text'})
    if not isinstance(body['token'], str) or len(body['token']) > 100:
        raise HTTPException(403, 'Invalid private link.')
    try:
        if action == 'exchange':
            result = s.exchange(body['token'])
        else:
            s.reply(body['token'], body['text'])
            result = {'ok': True}
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from None
    return response(result)
