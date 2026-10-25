"""Call-local text recall; selected storage only while memory consent is enabled."""
from collections import deque
from datetime import datetime, timedelta
import re
from time import monotonic, time


AGE = re.compile(r'\b(a|an|one|two|three|\d+)\s+(seconds?|minutes?) ago\b', re.I)
LONG = re.compile(r'\b(yesterday|last time|last call|previous call|last week|days? ago)\b', re.I)
STEP = re.compile(r'\bstep\s+(\d+)\b', re.I)
CURRENT_STEP = re.compile(r'\b(?:what|which) step\b', re.I)
STOP = set('what where when did do i my the a an was were is are it put leave left see saw just now ago '
           'minute minutes second seconds one two three earlier in this call happened recently please '
           'you remember last time yesterday picked up'.split())


def age_seconds(text):
    match = AGE.search(text)
    if not match:
        return None
    value = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3}.get(match[1].lower())
    value = value if value is not None else int(match[1])
    return value * (60 if match[2].lower().startswith('minute') else 1)


def source_for(text, rewind_on=False):
    """Explicit temporal scope wins over the optional semantic intent router."""
    if LONG.search(text):
        return 'long-term'
    age = age_seconds(text)
    if age is not None:
        return 'recent' if age <= 120 else 'rewind'
    if re.search(r'\b(just now|just do|just did|recently)\b', text, re.I) or (
            (STEP.search(text) or CURRENT_STEP.search(text)) and re.search(r'\b(was|did|earlier)\b', text, re.I)):
        return 'recent'
    if re.search(r'\b(earlier|before|start of (?:the|this) call)\b', text, re.I):
        return 'rewind' if rewind_on else 'recent'
    if re.search(r"\b(what did i)\b", text, re.I):
        return 'recent'
    return None


def words(text):
    return set(re.findall(r'[a-z0-9]+', text.lower())) - STOP


class RecentMemory:
    def __init__(self, clock=monotonic, started=None):
        self.clock = clock
        self.started = clock() if started is None else started
        self.started_wall = time()
        self.entries = deque(maxlen=1024)
        self.step = ''
        self.store = None
        self.call_id = None
        self.ram_only = False

    def bind(self, store, call_id):
        if self.ram_only:
            store = None
        if self.store is not None and (store is None or call_id != self.call_id):
            self._clear_persisted()
        self.store, self.call_id = store, call_id

    def _clear_persisted(self):
        from server.logs import event
        from server.memory_storage import StorageError
        try:
            self.store.clear_recent(self.call_id)
        except StorageError:
            # Privacy/offline failures must not interrupt the rest of call teardown.
            # The remote copy remains until a later successful expiry/Forget write.
            event('dependency_error', None, dependency='memory', level='error',
                  error='recent_cleanup_failed')

    def prune(self, now=None):
        now = self.clock() if now is None else now
        while self.entries and now - self.entries[0]['observed_at'] > 120:
            self.entries.popleft()

    def clear(self):
        self.entries.clear()
        self.step = ''
        if self.store is not None:
            try:
                self._clear_persisted()
            finally:
                self.store = None
                self.call_id = None

    def note_step(self, text):
        # Only numbered guide instructions establish a step; never infer completion.
        if re.match(r'^\s*step\s+\d+\s*[:.)-]', text, re.I):
            self.step = text[:1000]
            self.add(dict(caption='', objects=[]), self.clock(), self.step, kind='instruction')

    def note_transcript(self, text, role):
        if text.strip():
            if role == 'helper':
                self.note_step(text)
            self.add(dict(caption=text.strip(), objects=[]), self.clock(), kind=role)

    def add(self, scene, observed_at, step=None, kind='scene'):
        self.prune(observed_at)
        entry = dict(observed_at=observed_at, t_s=max(0, observed_at - self.started),
                                 note=str(scene.get('caption') or '')[:2000],
                                 labels=[str(o['label'])[:120] for o in scene.get('objects', [])[:64]],
                                 step=self.step if step is None else str(step)[:1000], kind=kind,
                                 wall_t=self.started_wall + max(0, observed_at - self.started))
        self.entries.append(entry)
        if self.store is not None and self.store.remembering():
            self.store.append_recent(self.call_id, entry)

    def find(self, question):
        self.prune()
        entries = list(self.entries)
        if self.store is not None and self.store.remembering():
            entries = [e for e in self.store.recent_records(self.call_id)
                       if self.clock() - e['observed_at'] <= 120]
        step = STEP.search(question)
        current_step = CURRENT_STEP.search(question)
        target = words(AGE.sub('', question))
        if step:
            entries = [e for e in entries if (m := STEP.search(e['step'])) and m[1] == step[1]]
        elif current_step:
            entries = [e for e in entries if e['kind'] == 'instruction' and e['step']]
        else:
            entries = [e for e in entries if e['kind'] == 'scene']
            if target:
                entries = [e for e in entries if target <= words(' '.join([e['note'], *e['labels']]))]
        age = age_seconds(question)
        if age is not None:
            # A sighting far away from the requested moment isn't evidence for that moment.
            entries = [e for e in entries if abs(self.clock() - e['observed_at'] - age) <= 20]
        if not entries:
            return None
        hit = (min(entries, key=lambda e: abs(self.clock() - e['observed_at'] - age))
               if age is not None else entries[-1])
        note = hit['step'] if step or current_step else hit['note'] or ', '.join(hit['labels'])
        return dict(source='recent', found=True, t_s=hit['t_s'],
                    say=f"At {int(hit['t_s']) // 60}:{int(hit['t_s']) % 60:02d} in this call, "
                        f"{'the guide said' if step or current_step else 'the scene note was'}: {note}")


async def long_term(session, question):
    from server import memory
    rewind = getattr(session, 'rewind', None)
    writer = getattr(rewind, 'memory_writer', None)
    if writer is None or not writer.store.remembering():
        return None
    log = session.recent_memory
    end = log.started_wall
    start = float('-inf')
    if 'yesterday' in question.lower():
        today = datetime.fromtimestamp(end).replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = (today - timedelta(days=1)).timestamp(), today.timestamp()
    elif 'last week' in question.lower() or re.search(r'\bdays? ago\b', question, re.I):
        return None  # Do not relabel a newest sighting as evidence for an unsupported date.

    class PastStore:
        clock = staticmethod(writer.store.clock)
        tags = writer.store.tags
        load_crop = writer.store.load_crop

        def records(self):
            return [r for r in writer.store.records() if start <= r['t'] < end]

        def where(self, name):
            return writer.store.where(name, start=start, end=end)

    query = LONG.sub('', question)
    result = await memory.find(PastStore(), query, embedder=writer.embedder)
    return dict(result, source='long-term') if result['found'] else None


async def answer(session, question, vision=None):
    from server.privacy import local_only
    rewind = getattr(session, 'rewind', None)
    on = bool(rewind and rewind.on and not local_only() and not getattr(session, 'private', False))
    requested = source_for(question, on) or 'recent'
    if requested == 'recent':
        hit = session.recent_memory.find(question)
        if hit:
            return hit
        # An explicit recent question must never be answered with yesterday's evidence.
        return dict(source='unavailable', found=False,
                    say="I don't have a matching scene note from the last two minutes. I can't confirm that.")
    if requested == 'rewind' and on:
        try:
            spoken, hit = await rewind.answer(question, vision=vision, before_s=rewind.now_s() - 20)
            if hit and hit['sure']:
                return dict(source='rewind', found=True, say=spoken.replace(" Here's the clip.", ''), t_s=hit['t_s'])
        except Exception:
            pass  # Availability only; never expose query, frames or exception text in logs.
    try:
        hit = await long_term(session, question)
    except Exception:
        hit = None
    if hit:
        if requested == 'rewind':
            hit['say'] = "I couldn't confirm that in this call's Rewind. From an earlier call: " + hit['say']
        return hit
    scope = 'yesterday' if 'yesterday' in question.lower() else 'that time'
    return dict(source='unavailable', found=False,
                say=f"I don't have an available memory for {scope}. I can't confirm what happened.")


async def run_turn(session, question, turn_id, vision=None):
    from server.agent import message, say
    result = await answer(session, question, vision)
    if result['source'] == 'long-term':
        from server.rewind_tools import memory_hit_message
        await session.emit(memory_hit_message(result, turn_id, session.recent_memory.started_wall))
    msg = say(result['say'])
    msg.update(turn_id=turn_id, part=0, final=True)
    await session.emit(message('reply_delta', agent='helper', turn_id=turn_id, text=msg['text'] + ' '))
    await session.emit(msg)
    if session.speak:
        async for audio in session.speak(msg):
            await session.emit(audio)
    session.history.extend([f'user: {question}', f"helper: {result['say']}"])
    session.history[:] = session.history[-10:]
