"""An in-process stand-in for TwelveLabs, for rehearsals and tests (REWIND_FAKE_TL=1). Nothing leaves the
machine. Uploads are 'indexed' at once; search returns every clip of the call, newest first, so our own
vision check (rewind.confirm) decides whether the object is really in the frame. Deletes are counted."""
import itertools


class FakeTwelveLabs:
    def __init__(self):
        self.videos = {}          # video_id -> metadata
        self.deleted = []         # video ids, in order
        self.searches = 0
        self._ids = itertools.count(1)

    async def ensure_index(self, name=None):
        return 'fake-index'

    async def upload(self, index_id, path, metadata):
        n = next(self._ids)
        self.videos[f'video-{n}'] = dict(metadata)
        return f'task-{n}', f'video-{n}'

    async def task(self, task_id):
        return {'status': 'ready'}

    async def search(self, index_id, query, video_ids=None, limit=5):
        self.searches += 1
        rows = []
        # No semantics here: newest clip first (for "where did I put X" the latest change is the best guess).
        # Our own vision check (rewind.confirm) still decides whether the object is in the frame.
        ids = [v for v in self.videos if not video_ids or v in video_ids]
        ids.sort(key=lambda v: float(self.videos[v].get('start_s', 0)), reverse=True)
        for rank, video_id in enumerate(ids):
            meta = self.videos[video_id]
            rows.append({'video_id': video_id, 'rank': rank + 1, 'start': 0.0,
                         'end': max(0.1, float(meta.get('end_s', 0)) - float(meta.get('start_s', 0)))})
        return rows[:limit]

    async def delete_video(self, index_id, video_id):
        self.videos.pop(video_id, None)
        self.deleted.append(video_id)

    async def analyze(self, mp4_bytes, prompt, schema=None, model=None, max_tokens=800):
        return '{"steps": [], "skipped": []}', {}
