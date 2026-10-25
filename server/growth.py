"""Numeric progress only. Uses the local-memory cipher; never writes plaintext.

Without a configured memory key, progress lasts only for this server process.
Identity comes from the authenticated connection, never a message field.
"""
from datetime import datetime, timezone
from uuid import uuid4

from server.memory import Store, memories, person_id


class GrowthLog:
    def __init__(self, store):
        self.store = store
        self.entries = store._read_json('growth.json', []) if store.cipher.encrypted else []
        self.epoch = 0

    def rows(self, task):
        return [row.copy() for row in self.entries if row['task'] == task]

    def level(self, task):
        return min(3, 1 + sum(row['completed'] for row in self.rows(task)))

    def save(self):
        if self.store.cipher.encrypted:
            self.store._write_json('growth.json', self.entries)

    def start(self, task):
        row = dict(id=uuid4().hex, task=task, date=datetime.now(timezone.utc).isoformat(),
                   tries=1, minutes=0.0, hints=0, safety_stop=False, completed=False)
        self.entries.append(row)
        self.save()
        return row['id']

    def update(self, run_id, **values):
        row = next((r for r in self.entries if r['id'] == run_id), None)
        if row is None:
            return False
        row.update(values)
        self.save()
        return True

    def forget(self):
        self.entries.clear()
        self.epoch += 1
        self.store._path('growth.json').unlink(missing_ok=True)

    def comparison(self, task, run_id):
        rows = [r for r in self.rows(task) if r['completed']]
        if len(rows) < 2 or rows[-1]['id'] != run_id:
            return ''
        previous, current = rows[-2:]
        if current['tries'] < previous['tries']:
            return f"Last time you used {previous['tries']} tries; today, {current['tries']}."
        if current['hints'] < previous['hints']:
            return f"Last time you used {previous['hints']} hints; today, {current['hints']}."
        return ''


def for_owner(app, owner):
    if not hasattr(app.state, 'growth_logs'):
        app.state.growth_logs = {}
    if owner not in app.state.growth_logs:
        # Growth is local encrypted numeric progress, independent of the place-graph backend.
        app.state.growth_logs[owner] = GrowthLog(Store(person_id(owner), base=memories(app).base))
    return app.state.growth_logs[owner]
