"""Local memory across calls: where things were put, kept on this machine only. Off by default.

Per memory id (a person, not a call) under data/private/memory/<id>/ (gitignored):
- log.jsonl: one line per CHANGED scene note (local Qwen caption, labels, tagged objects with boxes, a nearby
  object for "under the left cup", an int8 EmbeddingGemma vector). A heartbeat whose note did not change is not
  stored.
- crops/<id>.jpg: small JPEG crops (224 px long side) of caregiver-tagged objects only, never full frames. No crop
  is written when the frame shows a person (YOLO person class on our own GPU box) or when that check is
  unavailable (fail closed).
- tags.json: caregiver tags ("blue pill box", "key bowl") with an optional home place.
- videos.json: TwelveLabs video ids of this person's Rewind clips (only when Rewind cloud was on), so Forget can
  delete them too.
- owner.json: the identity (server.rewind.identity) that created the memory; other callers are refused.

At rest: encrypted with Fernet when A_EYE_MEMORY_KEY is set (a Fernet key in .env); otherwise protected by the
file system only (FileVault), and status says so. Retention: A_EYE_MEMORY_DAYS (default 7) days.
"""
import asyncio
import base64
import io
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from server.logs import event
from server import privacy
from server.memory_places import Places, path_parts

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'private' / 'memory'
CROP_SIDE = 224
GROUND_GAP_S = float(os.environ.get('A_EYE_MEMORY_GROUND_GAP_S', '3'))
_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
_PERSON = re.compile(r'\b(person|people|man|woman|boy|girl|child|face|baby)\b', re.I)
_WORD = re.compile(r'[a-z0-9]+')
_GENERIC = re.compile(r'^\s*(the\s+)?(object|thing|item)s?\s*$', re.I)
_STOP = {'the', 'my', 'a', 'an', 'your', 'our', 'of', 'where', 'are', 'is', 'did', 'i', 'put', 'leave', 'left',
         'last', 'see', 'saw', 'find', 'have', 'you', 'seen', 'what', 'happened', 'to', 'it', 'they', 'them'}


def person_id(owner):
    """One memory per signed-in person: derived from server.rewind.identity(), never chosen by the client."""
    import hashlib
    return 'p-' + hashlib.sha256((owner or 'local').encode()).hexdigest()[:16]


def root():
    return Path(os.environ.get('A_EYE_MEMORY_DIR') or ROOT)


def retention_days():
    return float(os.environ.get('A_EYE_MEMORY_DAYS', '7'))


def words(text):
    return {w.rstrip('s') if len(w) > 3 else w for w in _WORD.findall((text or '').lower())} - _STOP


class Cipher:
    def __init__(self, key=None):
        key = key if key is not None else os.environ.get('A_EYE_MEMORY_KEY', '')
        self.fernet = None
        if key:
            from cryptography.fernet import Fernet
            self.fernet = Fernet(key.encode() if isinstance(key, str) else key)

    @property
    def encrypted(self):
        return self.fernet is not None

    def seal(self, data):
        return self.fernet.encrypt(data) if self.fernet else data

    def open(self, data):
        return self.fernet.decrypt(data) if self.fernet else data


class Store(Places):
    """One person's memory folder. Every write and delete goes through here."""

    def __init__(self, memory_id, base=None, cipher=None, clock=time):
        if not _ID.match(memory_id or ''):
            raise ValueError('memory id: lowercase letters, digits and dashes, up to 40')
        self.id = memory_id
        self.dir = Path(base or root()) / memory_id
        self.cipher = cipher or Cipher()
        self.clock = clock
        self.revoked = False

    def writable(self):
        if self.revoked:
            raise PermissionError('This memory was deleted.')

    # ---- files ----
    def _path(self, name):
        return self.dir / name

    def _read_json(self, name, default):
        path = self._path(name)
        if not path.exists():
            return default
        try:
            return json.loads(self.cipher.open(path.read_bytes()))
        except Exception:
            return default

    def _write_json(self, name, value):
        self.writable()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(name + '.tmp')
        tmp.write_bytes(self.cipher.seal(json.dumps(value).encode()))
        tmp.replace(self._path(name))

    def owner(self):
        return self._read_json('owner.json', {}).get('owner')

    def claim(self, owner):
        """First caller owns the memory; anyone else is refused."""
        current = self.owner()
        if current is None:
            self._write_json('owner.json', {'owner': owner})
            return True
        return current == owner

    def records(self):
        path = self._path('log.jsonl')
        if not path.exists():
            return []
        out = []
        for line in path.read_bytes().splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(self.cipher.open(line)))
            except Exception:
                continue
        return out

    def append(self, record):
        self.writable()
        self._observe_places(record)
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self._path('log.jsonl'), 'ab') as handle:
            handle.write(self.cipher.seal(json.dumps(record, separators=(',', ':')).encode()) + b'\n')

    def _rewrite(self, records):
        self.writable()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path('log.jsonl.tmp')
        with open(tmp, 'wb') as handle:
            for record in records:
                handle.write(self.cipher.seal(json.dumps(record, separators=(',', ':')).encode()) + b'\n')
        tmp.replace(self._path('log.jsonl'))

    def save_crop(self, jpeg):
        self.writable()
        crop_id = uuid4().hex[:16]
        (self.dir / 'crops').mkdir(parents=True, exist_ok=True)
        (self.dir / 'crops' / f'{crop_id}.jpg').write_bytes(self.cipher.seal(jpeg))
        return crop_id

    def load_crop(self, crop_id):
        if not re.fullmatch(r'[0-9a-f]{16}', crop_id or ''):
            return None
        path = self.dir / 'crops' / f'{crop_id}.jpg'
        try:
            return self.cipher.open(path.read_bytes())
        except Exception:
            return None

    def tags(self):
        return self._read_json('tags.json', {})

    def set_tag(self, name, label=None, home=None, kind='object', place_path=None):
        if place_path is not None:
            place_path = path_parts(place_path)
        name = ' '.join((name or '').split())[:60]
        if not name:
            raise ValueError('empty tag')
        tags = self.tags()
        tags[name.lower()] = dict(name=name, label=(label or name)[:60], home=(home or None) and home[:60],
                                  kind=kind if kind in ('object', 'place') else 'object', created=self.clock())
        self._write_json('tags.json', tags)
        if place_path is None:
            self.ensure_thing(name, tags[name.lower()]['kind'])
        else:
            self.place(name, place_path, kind=tags[name.lower()]['kind'])
        return tags[name.lower()]

    REFUSED_ROOMS = ('bedroom', 'bathroom')

    def settings(self):
        saved = self._read_json('settings.json', {})
        tags = [dict(item=t['name'], home=t['home']) for t in self.tags().values() if t['kind'] == 'object' and t['home']]
        return dict(consent=dict(person=bool(saved.get('consent', {}).get('person')),
                                 caregiver=bool(saved.get('consent', {}).get('caregiver'))),
                    items=tags, rooms=saved.get('rooms', ['kitchen', 'living room', 'entryway']),
                    kept_days=saved.get('kept_days', retention_days()), remembering=bool(saved.get('remembering')))

    def save_settings(self, value):
        """Both consents are checked again here; bedroom and bathroom are refused (ValueError)."""
        consent = value.get('consent') or {}
        both = bool(consent.get('person')) and bool(consent.get('caregiver'))
        rooms = [str(r).strip().lower()[:30] for r in value.get('rooms') or [] if str(r).strip()]
        if any(r in self.REFUSED_ROOMS for r in rooms):
            raise ValueError('refused room')
        days = min(30, max(1, int(round(float(value.get('kept_days') or retention_days())))))
        self._write_json('settings.json', dict(consent=dict(person=bool(consent.get('person')),
                                                            caregiver=bool(consent.get('caregiver'))),
                                               rooms=sorted(set(rooms)), kept_days=days,
                                               remembering=both and bool(value.get('remembering'))))
        wanted = {}
        for entry in value.get('items') or []:
            if isinstance(entry, dict) and str(entry.get('item', '')).strip() and str(entry.get('home', '')).strip():
                wanted[str(entry['item']).strip().lower()[:60]] = str(entry['home']).strip()[:60]
        for name, tag in list(self.tags().items()):
            if tag['kind'] == 'object' and tag['home'] and name not in wanted:
                self._write_json('tags.json', {k: v for k, v in self.tags().items() if k != name})
        for name, home in wanted.items():
            self.set_tag(name, home=home)
        self.purge_old()
        return self.settings()

    def remembering(self):
        return self.settings()['remembering']

    def count_today(self):
        midnight = datetime.fromtimestamp(self.clock()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return sum(1 for r in self.records() if r.get('note') and r.get('t', 0) >= midnight)

    def videos(self):
        return self._read_json('videos.json', [])

    def add_video(self, index_id, video_id):
        videos = self.videos()
        videos.append(dict(index_id=index_id, video_id=video_id, t=self.clock()))
        self._write_json('videos.json', videos)

    # ---- counts and deletes ----
    def count(self, item=None):
        notes = [r for r in self.records() if r.get('note')]
        if item:
            notes = [r for r in notes if mentions(r, item)]
        return len(notes)

    def size_bytes(self):
        return sum(p.stat().st_size for p in self.dir.rglob('*') if p.is_file()) if self.dir.exists() else 0

    def _drop(self, keep):
        records = self.records()
        kept = [r for r in records if keep(r)]
        gone = [r for r in records if not keep(r)]
        for record in gone:
            for crop_id in (record.get('crops') or {}).values():
                path = self.dir / 'crops' / f'{crop_id}.jpg'
                if path.exists():
                    path.unlink()
        self._rewrite(kept)
        return len(gone)

    def forget_item(self, item):
        """Every note about the item (tag, label or note words), its crops, and its tag."""
        gone = self._drop(lambda r: not mentions(r, item))
        tags = {k: v for k, v in self.tags().items() if not (words(v['name']) & words(item))}
        self._write_json('tags.json', tags)
        self._delete_places(things=[t['id'] for t in self.things() if words(t['name']) & words(item)])
        self._write_json('recent.json', {})
        return gone

    def forget_since(self, since):
        gone = self._drop(lambda r: r.get('t', 0) < since)
        self._write_json('videos.json', [v for v in self.videos() if v.get('t', 0) < since])
        self._delete_places(placements=[p['id'] for p in self.placements() if p['t'] >= since])
        self._write_json('recent.json', {})
        return gone

    def forget_all(self):
        count = len(self.records())
        if self.dir.exists():
            owner = self.owner()
            shutil.rmtree(self.dir)
            if owner:
                self._write_json('owner.json', {'owner': owner})   # the person keeps their memory id
        return count

    def purge_old(self, days=None):
        if days is None:
            days = self._read_json('settings.json', {}).get('kept_days', retention_days())
        cutoff = self.clock() - 86400 * days
        self._delete_places(placements=[p['id'] for p in self.placements() if p['t'] < cutoff])
        return self._drop(lambda r: r.get('t', 0) >= cutoff)


def mentions(record, item):
    target = words(item)
    if not target:
        return False
    have = set()
    for tag in record.get('tags') or []:
        have |= words(tag.get('tag')) | words(tag.get('label'))
    have |= set().union(*(words(label) for label in record.get('labels') or [])) if record.get('labels') else set()
    have |= words(record.get('note'))
    return bool(target & have)


# ---- embeddings (EmbeddingGemma on the Mac CPU, router/embed_local.py) ----

class GemmaEmbedder:
    QUERY = 'task: search result | query: '
    DOC = 'title: none | text: '

    def embed(self, texts, query=False):
        from router.embed_local import embed_many
        prefix = self.QUERY if query else self.DOC
        return embed_many([prefix + t for t in texts], 'gemma_local')


def pack(vector):
    scale = max(1e-9, max(abs(v) for v in vector))
    data = bytes((int(round(v / scale * 127)) & 0xff) for v in vector)
    return dict(q=base64.b64encode(data).decode(), s=scale)


def unpack(packed):
    raw = base64.b64decode(packed['q'])
    return [((b - 256) if b > 127 else b) * packed['s'] / 127 for b in raw]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ---- writing ----

def center(bbox):
    x, y, w, h = bbox
    return x + w / 2, y + h / 2


def crop_jpeg(jpeg, bbox, pad=0.15):
    from PIL import Image
    with Image.open(io.BytesIO(jpeg)) as image:
        W, H = image.size
        x, y, w, h = bbox
        left, top = max(0, (x - w * pad) * W), max(0, (y - h * pad) * H)
        right, bottom = min(W, (x + w * (1 + pad)) * W), min(H, (y + h * (1 + pad)) * H)
        if right - left < 4 or bottom - top < 4:
            return None, None
        piece = image.convert('RGB').crop((int(left), int(top), int(right), int(bottom)))
        piece.thumbnail((CROP_SIDE, CROP_SIDE))
        out = io.BytesIO()
        piece.save(out, format='JPEG', quality=80)
        cw, ch = (right - left), (bottom - top)
        inner = [(x * W - left) / cw, (y * H - top) / ch, w * W / cw, h * H / ch]
        return out.getvalue(), [round(min(1, max(0, v)), 3) for v in inner]


class PersonCheck:
    """True when the frame shows a person. Our own GPU box (YOLO person class); raises when unreachable."""

    def __init__(self, box=None):
        self.box = box

    async def __call__(self, jpeg_b64, scene=None):
        if scene and any(_PERSON.search(o.get('label', '')) for o in scene.get('objects', [])):
            return True
        if scene and _PERSON.search(scene.get('caption', '') or ''):
            return True
        from server.box_client import BoxClient
        box = self.box or BoxClient()
        try:
            detections = await box.detect(jpeg_b64)
        finally:
            if self.box is None:
                await box.aclose()
        if (not isinstance(detections, list) or
                any(not isinstance(d, dict) or not isinstance(d.get('label'), str) or not d['label'].strip()
                    for d in detections)):
            raise ValueError('invalid person detector response')
        return any(d['label'].strip().casefold() == 'person' for d in detections)


class MemoryVision:
    """Local Qwen grounding, independent of the general scene backend setting."""

    async def call(self, operation, payload):
        from vision.ollama import OllamaBackend
        def local():
            backend = OllamaBackend()
            try:
                return getattr(backend, operation)(payload, 'ok')
            finally:
                backend.client.close()
        return await asyncio.to_thread(local)


class Writer:
    """Turns scans of one call into memory lines. Stores only changed notes."""

    def __init__(self, store, call_id, embedder=None, person_check=None, vision=None):
        self.store = store
        self.vision = vision        # injected local grounding client, or MemoryVision in production
        self.grounded_at = 0.0
        self.call_id = call_id
        self.embedder = embedder or GemmaEmbedder()
        self.person_check = person_check or PersonCheck()
        self.last_key = None
        self.written = 0
        self.skipped = {}
        self.purged_at = 0.0

    async def ground(self, tags, jpeg_b64, note):
        """Ask our vision host for the tagged things on every scan and activity frame (the scene loop only asks the
        pack's own queries, and a caption often leaves small things out). At most one call every GROUND_GAP_S."""
        if not tags or self.vision is None:
            return []
        now = monotonic()
        if now - self.grounded_at < GROUND_GAP_S:
            return []
        self.grounded_at = now
        items = [t['label'] for t in tags.values() if t['kind'] == 'object'][:2]
        places = [t['label'] for t in tags.values() if t['kind'] == 'place'][:1]
        queries = items + ([f"{places[0]} closest to the {items[0]}"] if places and items else places)
        try:
            scene = await self.vision.call('scene', dict(image_b64=jpeg_b64, queries=queries[:3], caption=False))
        except Exception:
            return []
        out = []
        for i, obj in enumerate(scene.get('objects', [])):
            label = obj['label']
            if places and label.startswith(places[0] + ' closest'):
                label = places[0]
            out.append(dict(obj, id=f'mem{i}', label=label))
        return out

    def _skip(self, reason):
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    async def observe(self, scene, jpeg_b64):
        """One keyframe scan: scene (caption, objects) and the frame it was made from."""
        original_scene = scene
        enriched = False
        if privacy.google_allowed():
            from vision.gemini import GeminiBackend
            queries = [t['label'] for t in self.store.tags().values()][:3]
            candidate = await asyncio.to_thread(GeminiBackend().scene,
                                                dict(image_b64=jpeg_b64, queries=queries))
            if candidate.get('caption') and candidate['caption'] != 'unsure':
                scene, enriched = candidate, True
        note = ' '.join((scene.get('caption') or '').split())[:200]
        objects = [o for o in scene.get('objects', []) if o.get('label') and not _GENERIC.match(o['label'])]
        tags = self.store.tags()
        if not enriched:
            objects += await self.ground(tags, jpeg_b64, note)
        hits = []
        for obj in objects:
            for tag in tags.values():
                if tag['kind'] != 'object':
                    continue            # places are context ("under the left red cup"), not items to crop
                if words(tag['label']) & words(obj['label']) or words(tag['name']) & words(obj['label']):
                    hits.append((tag, obj))
        seen = {t['name'] for t, _ in hits}
        for tag in tags.values():            # the caption names it but grounding gave no box: keep a text sighting
            if tag['kind'] == 'object' and tag['name'] not in seen and (words(tag['label']) | words(tag['name'])) & words(note):
                hits.append((tag, dict(id='caption', label=tag['label'], bbox=None)))
        key = (note, tuple(sorted({t['name'] for t, _ in hits})))
        if not note or key == self.last_key:
            self._skip('unchanged')
            return None
        self.last_key = key
        now = self.store.clock()
        record = dict(id=uuid4().hex[:12], t=now, call=self.call_id, note=note,
                      labels=[o['label'] for o in objects][:12], tags=[], crops={})
        for tag, obj in hits:
            near = None
            if obj['bbox'] is None:
                record['tags'].append(dict(tag=tag['name'], label=obj['label'], bbox=None, near=None, text_only=True))
                continue
            others = [o for o in objects if o is not obj]
            places = [o for o in others for t in tags.values()
                      if t['kind'] == 'place' and words(t['label']) & words(o['label'])]
            pool = places or others
            if pool:
                cx, cy = center(obj['bbox'])
                best = min(pool, key=lambda o: (center(o['bbox'])[0] - cx) ** 2 + (center(o['bbox'])[1] - cy) ** 2)
                near = dict(label=best['label'], bbox=best['bbox'])
            entry = dict(tag=tag['name'], label=obj['label'], bbox=obj['bbox'], near=near)
            if near and obj.get('conf', 0) >= .6:
                # A nearby landmark is a relation, not evidence that an item is inside it.
                entry.update(place_path=[place_words(entry, tags)], confidence=obj['conf'])
            record['tags'].append(entry)
        if hits and getattr(self.store, 'supports_crops', True):
            # Faces never stored: no crop when a person is in the frame or the check cannot run (fail closed).
            try:
                person = await self.person_check(jpeg_b64, original_scene)
            except Exception:
                person, record['skipped'] = True, 'person_check_unavailable'
            if person:
                record.setdefault('skipped', 'person')
                self._skip(record['skipped'])
            else:
                jpeg = base64.b64decode(jpeg_b64)
                for tag, obj in hits:
                    if obj['bbox'] is None:
                        continue
                    piece, inner = await asyncio.to_thread(crop_jpeg, jpeg, obj['bbox'])
                    if piece:
                        record['crops'][tag['name']] = self.store.save_crop(piece)
                        for entry in record['tags']:
                            if entry['tag'] == tag['name']:
                                entry['crop_bbox'] = inner
        text = ' '.join([note] + record['labels'] + [t['tag'] for t in record['tags']] +
                        [t['near']['label'] for t in record['tags'] if t['near']])
        try:
            record['emb'] = pack((await asyncio.to_thread(self.embedder.embed, [text]))[0])
        except Exception:
            record['emb'] = None    # still findable by tag and label
        self.store.append(record)
        self.written += 1
        if now - self.purged_at > 3600:
            self.purged_at = now
            self.store.purge_old()
        return record


# ---- search ----

def spoken_time(t, now):
    moment = datetime.fromtimestamp(t)
    today = datetime.fromtimestamp(now).date()
    clock = moment.strftime('%I:%M %p').lstrip('0')
    if moment.date() == today:
        minutes = int((now - t) // 60)
        return 'just now' if minutes < 1 else (f'{minutes} minute{"s" if minutes != 1 else ""} ago, at {clock}'
                                                if minutes < 60 else f'today at {clock}')
    if (today - moment.date()).days == 1:
        return f'yesterday at {clock}'
    return moment.strftime('%A at ') + clock


def arrived(sightings, target, same=0.08):
    """The moment the item reached where it is now: the newest sighting with a box, walked back over earlier
    boxed sightings at about the same spot (text-only sightings in between are skipped). Without any box, the
    newest sighting. Records are in write order."""
    def spot(record):
        entry = next((e for e in record.get('tags') or [] if words(e['tag']) & words(target) and e.get('bbox')), None)
        if not entry:
            return None
        near = entry.get('near') or {}
        return center(entry['bbox']) + (near.get('label'), center(near['bbox']) if near.get('bbox') else None)
    boxed = [r for r in sightings if spot(r)]
    if not boxed:
        return sightings[-1]
    best = boxed[-1]
    here = spot(best)
    for record in reversed(boxed[:-1]):
        there = spot(record)
        moved = ((there[0] - here[0]) ** 2 + (there[1] - here[1]) ** 2) ** 0.5 > same
        if moved or there[2] != here[2] or (there[3] and here[3] and
                                            ((there[3][0] - here[3][0]) ** 2 + (there[3][1] - here[3][1]) ** 2) ** 0.5 > same):
            break
        best = record
    return best


def item_from(question):
    value = re.sub(r"^\s*(hey\s+)?(a-?eye,?\s*)?", '', question or '', flags=re.I)
    value = re.sub(r"\b(where('s| is| are| was| were| did i (put|leave)| have i (put|left))|have you seen|did you see|"
                   r"find|what happened to)\b", ' ', value, flags=re.I)
    value = re.sub(r"\b(my|the|your)\b", ' ', value, flags=re.I)
    value = re.sub(r"[?!.]+", ' ', value)
    return ' '.join(value.split()) or (question or '').strip()


def live_target(scene, item, record_tag):
    """Where to point now: the item itself if in view, else the object it was last near (same label, closest)."""
    if not scene:
        return None
    for obj in scene.get('objects', []):
        if words(obj['label']) & words(item) and obj.get('conf', 1) >= 0.5:
            return dict(object_id=obj['id'], bbox=obj['bbox'], what='item')
    near = (record_tag or {}).get('near')
    if near:
        same = [o for o in scene.get('objects', []) if words(o['label']) & words(near['label'])]
        if same:
            nx, ny = center(near['bbox'])
            best = min(same, key=lambda o: (center(o['bbox'])[0] - nx) ** 2 + (center(o['bbox'])[1] - ny) ** 2)
            if ((center(best['bbox'])[0] - nx) ** 2 + (center(best['bbox'])[1] - ny) ** 2) ** 0.5 <= 0.3:
                return dict(object_id=best['id'], bbox=best['bbox'], what='place')
    return None


def place_words(tag_entry, tags=None):
    near = (tag_entry or {}).get('near')
    if not near:
        return None
    label = near['label']
    rel = ('in' if re.search(r'\b(bowl|basket|drawer|tray|box|bag|jar|pocket)\b', label, re.I) else
           'under' if re.search(r'\b(cup|mug|lid|cloth|towel|hat)\b', label, re.I) else 'by')
    place = next((t for t in (tags or {}).values() if t['kind'] == 'place' and words(t['label']) & words(label)), None)
    x = center(near['bbox'])[0]
    side = 'left ' if x < 0.38 else 'right ' if x > 0.62 else 'middle '
    return f"{rel} the {side}{place['name'] if place else label}"


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


async def item_box_is_real(box, item, places, jpeg_b64, detector=None):
    """Grounding can return a box for a hidden thing, and it is then the cup it hides under (seen live: the middle
    cup came back as "keys"). Accept the item's box only when it is small and does not coincide with a place box or
    a detector hit for something else (YOLO on our GPU box; COCO knows cups and bowls)."""
    if box[2] * box[3] > 0.15:
        return False
    if any(iou(box, p) >= 0.5 for p in places):
        return False
    try:
        from server.box_client import BoxClient
        client = detector or BoxClient()
        try:
            detections = await client.detect(jpeg_b64)
        finally:
            if detector is None:
                await client.aclose()
    except Exception:
        return not places          # without the detector, only trust a box when no place box could contradict it
    return not any(iou(box, d['bbox']) >= 0.5 and not (words(d.get('label')) & words(item)) for d in detections)


async def ground_live(vision, jpeg_b64, item, entry, caption='', detector=None):
    """Live objects for the arrow: the item itself (when the caption names it, or when its grounded box passes
    item_box_is_real), and its last place by side ("the left red cup")."""
    named = bool(words(item) & words(caption))
    queries = [item]
    place = place_words(entry)
    if place:
        queries.append(place.split(' the ', 1)[1])
    try:
        scene = await vision.call('scene', dict(image_b64=jpeg_b64, queries=queries, caption=False))
    except Exception:
        return None
    objects = []
    for i, obj in enumerate(scene.get('objects', [])):
        label = obj['label']
        if entry and entry.get('near') and label != item:
            label = entry['near']['label']
        objects.append(dict(obj, id=f'live{i}', label=label))
    places = [o['bbox'] for o in objects if o['label'] != item]
    kept = []
    for obj in objects:
        if obj['label'] == item and not named and not await item_box_is_real(obj['bbox'], item, places, jpeg_b64,
                                                                            detector):
            continue
        kept.append(obj)
    return dict(caption='', objects=kept)


async def find(store, question, embedder=None, scene=None, now=None, vision=None, live_frame=None):
    """Answer "where are my keys?" from this person's memory. JSON-safe dict plus crop bytes under 'crop'."""
    started = monotonic()
    now = now if now is not None else store.clock()
    item = item_from(question)
    records = [r for r in store.records() if r.get('note')]
    tags = store.tags()
    tag = next((t for t in tags.values() if words(t['name']) & words(item) or words(t['label']) & words(item)), None)
    target = (tag['name'] if tag else item)
    location = store.where(target) if hasattr(store, 'where') else None
    matching = [r for r in records if mentions(r, target)]
    if location and (not matching or location['t'] > max(r['t'] for r in matching)
                     or not getattr(store, 'supports_crops', True) and
                     location['t'] >= max(r['t'] for r in matching)):
        place = ' / '.join(location['place_path'])
        when = spoken_time(location['t'], now)
        return dict(found=True, item=target, how='tag' if location['source'] == 'tag' else 'placement',
                    t=location['t'], when=when, place=place, place_path=location['place_path'],
                    confidence=location['confidence'], source=location['source'],
                    record_id=location['id'], count=len(records), count_item=len(matching),
                    home=tag.get('home') if tag else None, live=None, crop=None,
                    say=f"Your {target} was last recorded at {place}, {when}.",
                    ms=round((monotonic() - started) * 1000, 1))
    hit, how = None, None
    exact = [r for r in records if any(words(e['tag']) & words(target) for e in r.get('tags') or [])]
    if exact:
        hit, how = arrived(exact, target), 'tag'
    if hit is None:
        labelled = [r for r in records if any(words(label) & words(item) for label in r.get('labels') or [])]
        if labelled:
            hit, how = labelled[max(range(len(labelled)), key=lambda i: (labelled[i]['t'], i))], 'label'
    if hit is None and records:
        try:
            query = (await asyncio.to_thread((embedder or GemmaEmbedder()).embed, [item], True))[0]
            scored = sorted(((cosine(query, unpack(r['emb'])), r) for r in records if r.get('emb')),
                            key=lambda x: -x[0])[:3]
            if scored and scored[0][0] >= float(os.environ.get('A_EYE_MEMORY_MIN_COS', '0.45')):
                top = scored[0][0]
                hit = max((r for s, r in scored if s >= top - 0.05), key=lambda r: r['t'])
                how = 'similar'
        except Exception as exc:
            event('dependency_error', None, dependency='embedder', level='error', error=type(exc).__name__)
    home = tag.get('home') if tag else None
    result = dict(found=hit is not None, item=item, how=how, count=len(records),
                  count_item=sum(1 for r in records if mentions(r, target)), home=home, live=None, ms=0)
    if hit is None:
        result['say'] = (f"I haven't seen your {item} yet." + (f" Their home is the {home}." if home else ''))
        result['ms'] = round((monotonic() - started) * 1000, 1)
        return result
    entry = next((e for e in hit.get('tags') or [] if words(e['tag']) & words(target)), None)
    place = place_words(entry, tags)
    home = tag.get('home') if tag else None
    near = (entry or {}).get('near')
    if place and home and near and words(home) & words(near['label']):
        place = place.split(' the ', 1)[0] + f' the {home}'      # at its home: "in the blue bowl"
    when = spoken_time(hit['t'], now)
    result.update(t=hit['t'], when=when, note=hit['note'], place=place, record_id=hit['id'],
                  box=(entry or {}).get('crop_bbox'), bbox=(entry or {}).get('bbox'))
    crop_id = (hit.get('crops') or {}).get(entry['tag']) if entry else None
    result['crop'] = store.load_crop(crop_id) if crop_id else None
    if vision is not None and live_frame:
        grounded = await ground_live(vision, live_frame, item, entry, (scene or {}).get('caption', ''))
        if grounded and grounded['objects']:
            scene = grounded
    result['live'] = live_target(scene, item, entry)
    result['live_scene'] = scene if result['live'] else None
    seen = (f"I last saw your {item} {place}, {when}." if place else
            f"I last saw your {item} {when}: {hit['note'].rstrip('.')}." if how in ('label', 'similar') else
            f"I last saw your {item} {when}.")
    if home and place and words(home) & words(place):
        line = f"Your {item} {'are' if item.endswith('s') else 'is'} {place}, where {'they live' if item.endswith('s') else 'it lives'}. Seen {when}."
    elif home:
        line = f"{seen} Their home is the {home}."
    else:
        line = seen
    if result['live']:
        line += " I'm pointing at it now." if result['live']['what'] == 'item' else " I'm pointing at that spot."
    result['say'] = line
    result['ms'] = round((monotonic() - started) * 1000, 1)
    return result


def hit_fields(result, call_started=None):
    """find_item answer: the rewind_hit fields (t_s, start_s, end_s, sure, frame_b64, objects, alternatives) plus
    place, when, at_ms, home, live and counts. t_s is seconds since this call started (negative: an earlier call)."""
    out = {k: v for k, v in result.items() if k not in ('crop', 'live_scene', 'box', 'bbox', 't')}
    if result.get('found'):
        t_s = round(result['t'] - call_started, 1) if call_started else 0.0
        out.update(t_s=t_s, start_s=t_s, end_s=t_s, sure=result.get('how') in ('tag', 'label'), alternatives=[],
                   at_ms=int(result['t'] * 1000))
        if result.get('crop'):
            out['frame_b64'] = base64.b64encode(result['crop']).decode()
        if result.get('box'):
            out['objects'] = [dict(id='memory-0', label=result['item'], bbox=result['box'], conf=1.0)]
    return out


# ---- per-process attachments: which call writes to which memory ----

class Memories:
    def __init__(self, base=None, embedder=None, person_check=None, vision=None):
        self.base = base
        self.vision = vision
        self.embedder = embedder
        self.person_check = person_check
        self.calls = {}           # session_id -> (store, writer)
        self.stores = {}

    def store(self, memory_id):
        if memory_id not in self.stores:
            from server.memory_storage import open_store
            self.stores[memory_id] = open_store(memory_id, base=self.base)
        return self.stores[memory_id]

    def retire(self, memory_id):
        store = self.stores.pop(memory_id, None)
        if store:
            store.revoked = True
        for session_id, (attached, _) in list(self.calls.items()):
            if attached.id == memory_id:
                self.detach(session_id)

    def for_owner(self, owner):
        store = self.store(person_id(owner))
        if not store.claim(owner):
            raise PermissionError('memory belongs to another caller')
        return store

    def auto_attach(self, rewind):
        """At call start, and when settings change: attach while the person has both consents and remembering on."""
        store = self.for_owner(rewind.owner)
        if store.remembering() and rewind.memory_writer is None:
            _, writer = self.attach(rewind.session_id, store.id, rewind.owner)
            rewind.memory_writer = writer
            if not any(c.video_id for c in rewind.clips):
                rewind.index_name, rewind.index_id = f'a-eye-{store.id}', None
            rewind.on_upload = store.add_video
        elif not store.remembering() and rewind.memory_writer is not None:
            self.detach(rewind.session_id)
            rewind.memory_writer = None
        recent = getattr(getattr(rewind, 'session', None), 'recent_memory', None)
        if recent is not None:
            recent.bind(store if rewind.memory_writer is not None else None, rewind.session_id)
        return store

    def attach(self, session_id, memory_id, owner):
        store = self.store(memory_id)
        if not store.claim(owner):
            raise PermissionError('memory belongs to another caller')
        writer = Writer(store, session_id[:12], embedder=self.embedder, person_check=self.person_check,
                        vision=self.vision)
        self.calls[session_id] = (store, writer)
        store.purge_old()
        return store, writer

    def detach(self, session_id):
        self.calls.pop(session_id, None)

    def for_call(self, session_id):
        return self.calls.get(session_id, (None, None))


def memories(app):
    if not hasattr(app.state, 'memories'):
        vision = getattr(app.state, 'vision_v2', None)
        if vision is None:
            vision = MemoryVision()
        app.state.memories = Memories(vision=vision)
    return app.state.memories


def status(store, encrypted=None):
    records = store.records()
    return dict(memory_id=store.id, count=sum(1 for r in records if r.get('note')),
                crops=sum(len(r.get('crops') or {}) for r in records), bytes=store.size_bytes(),
                encrypted=store.cipher.encrypted if encrypted is None else encrypted,
                retention_days=retention_days(), tags=sorted(t['name'] for t in store.tags().values()))


async def delete_cloud(store, since=None, client=None):
    """Delete this person's TwelveLabs videos (all, or those added since a time). Logs video ids only.
    Returns (deleted, failed). Deleting is allowed in Private mode: it only removes data."""
    from server.rewind import TwelveLabsClient
    client = client or TwelveLabsClient()
    deleted, failed, kept = 0, 0, []
    for video in store.videos():
        if since is not None and video.get('t', 0) < since:
            kept.append(video)
            continue
        try:
            await client.delete_video(video['index_id'], video['video_id'], force=True)
            deleted += 1
            print(json.dumps(dict(ts=datetime.now(timezone.utc).isoformat(), level='info',
                                  event='memory_cloud_deleted', video_id=video['video_id'])), flush=True)
        except Exception as exc:
            failed += 1
            kept.append(video)
            event('dependency_error', None, dependency='twelvelabs', level='error', error=type(exc).__name__)
    store._write_json('videos.json', kept)
    return deleted, failed
