"""Small owner-scoped location graph, shared by the local and Spacetime stores."""
import math
from uuid import uuid4


def path_parts(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 16 or
            any(not isinstance(p, str) or not p.strip() or len(p) > 120 for p in value)):
        raise ValueError('place_path must contain 1 to 16 nonempty names')
    return [' '.join(p.split()) for p in value]


def normalized(path):
    return [p.casefold() for p in path]


def number(value, field, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < 0 or maximum is not None and value > maximum):
        raise ValueError('invalid ' + field)
    return value


class Places:
    """Storage hooks use encrypted JSON locally and typed reducers remotely."""

    def things(self):
        return self._read_json('places.json', {}).get('things', [])

    def placements(self, thing_id=None):
        rows = self._read_json('places.json', {}).get('placements', [])
        return sorted((p for p in rows if thing_id is None or p['thing_id'] == thing_id),
                      key=lambda p: (p['t'], p['id']))

    def _write_place(self, thing, placement=None):
        graph = self._read_json('places.json', dict(things=[], placements=[]))
        graph['things'] = [r for r in graph['things'] if r['id'] != thing['id']] + [thing]
        if placement is not None:
            graph['placements'].append(placement)
        self._write_json('places.json', graph)

    def _delete_places(self, things=(), placements=()):
        graph = dict(things=[t for t in self.things() if t['id'] not in things],
                     placements=[p for p in self.placements()
                                 if p['id'] not in placements and p['thing_id'] not in things])
        self._write_json('places.json', graph)

    def _thing(self, name, kind='object', created=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('invalid thing name')
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 60:
            raise ValueError('invalid thing kind')
        name = ' '.join(name.split())
        old = next((t for t in self.things() if t['name'].casefold() == name.casefold()), None)
        return old or dict(id=uuid4().hex, owner=self.owner() or self.id, name=name, kind=kind,
                           created=number(self.clock() if created is None else created, 'created'))

    def ensure_thing(self, name, kind='object'):
        thing = self._thing(name, kind)
        self._write_place(thing)
        return thing

    def place(self, name, place_path, *, t=None, source='tag', confidence=1.0, kind='object'):
        path = path_parts(place_path)
        t = number(self.clock() if t is None else t, 't')
        confidence = number(confidence, 'confidence', 1)
        if source not in ('tag', 'capture', 'vision'):
            raise ValueError('invalid placement source')
        thing = self._thing(name, kind, t)
        placement = dict(id=uuid4().hex, thing_id=thing['id'], owner=thing['owner'], place_path=path,
                         t=t, source=source, confidence=confidence)
        self._write_place(thing, placement)
        return placement

    def where(self, name, *, start=float('-inf'), end=float('inf')):
        thing = next((t for t in self.things() if t['id'] == name or t['name'].casefold() == name.casefold()), None)
        if thing is None:
            return None
        rows = [p for p in self.placements(thing['id']) if start <= p['t'] < end]
        return dict(rows[-1], name=thing['name'], kind=thing['kind']) if rows else None

    def in_place(self, prefix):
        prefix = normalized(path_parts(prefix))
        out = []
        for thing in self.things():
            current = self.where(thing['id'])
            if current and normalized(current['place_path'][:len(prefix)]) == prefix:
                out.append(current)
        return sorted(out, key=lambda row: row['name'].casefold())

    def last_move(self, name):
        current = self.where(name)
        if current is None:
            return None
        history = self.placements(current['thing_id'])
        moves = [after['t'] for before, after in zip(history, history[1:])
                 if normalized(before['place_path']) != normalized(after['place_path'])]
        return moves[-1] if moves else None

    def _observe_places(self, record):
        # Only structured observations establish a path; never parse a caption into a claimed location.
        for entry in record.get('tags') or []:
            path = entry.get('place_path')
            if path:
                self.place(entry['tag'], path, t=record['t'], source=record.get('source', 'vision'),
                           confidence=entry.get('confidence', .6))

    def append_recent(self, call_id, entry):
        recent = self._read_json('recent.json', {})
        recent = {key: [e for e in rows if e['wall_t'] >= self.clock() - 120]
                  for key, rows in recent.items()}
        recent = {key: rows for key, rows in recent.items() if rows}
        recent.setdefault(call_id, []).append(entry)
        recent[call_id] = recent[call_id][-1024:]
        self._write_json('recent.json', recent)

    def recent_records(self, call_id):
        return [e for e in self._read_json('recent.json', {}).get(call_id, [])
                if e['wall_t'] >= self.clock() - 120]

    def clear_recent(self, call_id):
        recent = self._read_json('recent.json', {})
        recent.pop(call_id, None)
        self._write_json('recent.json', recent)
