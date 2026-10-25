"""20 synthetic EE questions; real local lookup, stubbed Rewind service latency.
Run: python -m scripts.recent_memory_bench (no keys, downloads or real user data).
"""
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace

from server.memory import Store, Cipher
from server.recent_memory import RecentMemory, answer, source_for

QUESTIONS = [
    ('What did I just do?', True, 'recent', 'Soldering iron'),
    ('Where did I put the red wire a minute ago?', True, 'recent', 'row 12'),
    ('What was step 3?', True, 'recent', 'place resistor'),
    ('Where was the multimeter two minutes ago?', True, 'recent', 'left tray'),
    ('Where was the resistor 30 seconds ago?', True, 'recent', 'row 12'),
    ('Where was the soldering iron just now?', False, 'recent', 'picked up'),
    ('What did I do 60 seconds ago?', False, 'recent', 'Red wire'),
    ('Where was the resistor earlier in this call?', False, 'recent', 'row 12'),
    ('What was step 4?', False, 'recent', 'connect wire'),
    ('Where was the multimeter 120 seconds ago?', False, 'recent', 'left tray'),
    ('Where was the resistor earlier in this call?', True, 'rewind', '0:30'),
    ('Where was the wire 3 minutes ago?', True, 'rewind', '0:30'),
    ('Where was the soldering iron earlier?', True, 'rewind', '0:30'),
    ('Where was the multimeter at the start of this call?', True, 'rewind', '0:30'),
    ('Where was the breadboard before?', True, 'rewind', '0:30'),
    ('Where was the resistor yesterday?', True, 'long-term', 'parts bin'),
    ('Where was the red wire last time?', False, 'long-term', 'parts bin'),
    ('Where was the multimeter last call?', True, 'long-term', 'parts bin'),
    ('Where was the soldering iron yesterday?', False, 'long-term', 'parts bin'),
    ('Where was the breadboard in the previous call?', False, 'long-term', 'parts bin'),
]


class FixtureRewind:
    on = True
    calls = 0

    def now_s(self):
        return 240

    async def answer(self, question, vision=None, before_s=None):
        assert before_s == 220
        self.calls += 1
        return 'At 0:30 in this call, on the workbench.', dict(sure=True, t_s=30)


def fixture(base):
    log = RecentMemory(clock=lambda: 240, started=0)
    for at, note, labels, step in [
        (120, 'Multimeter in left tray', ['multimeter'], 'Step 2: measure voltage'),
        (150, 'Resistor placed on breadboard row 12', ['resistor', 'breadboard'], 'Step 3: place resistor'),
        (180, 'Red wire on breadboard row 12', ['red wire', 'breadboard'], 'Step 4: connect wire'),
        (210, 'Resistor on breadboard row 12', ['resistor', 'breadboard'], 'Step 4: connect wire'),
        (239, 'Soldering iron picked up', ['soldering iron'], 'Step 5: solder joint'),
    ]:
        log.add(dict(caption=note, objects=[dict(label=x) for x in labels]), at, step)
    store = Store('fixture', base=base, cipher=Cipher(''))
    store.save_settings(dict(consent=dict(person=True, caregiver=True), remembering=True))
    yesterday = datetime.fromtimestamp(log.started_wall).replace(hour=12, minute=0, second=0) - timedelta(days=1)
    store.append(dict(id='old', t=yesterday.timestamp(), note='Tools and components in parts bin',
                      labels=['resistor', 'red wire', 'multimeter', 'soldering iron', 'breadboard']))
    # A newer current-call record must never win a yesterday/last-call query.
    store.append(dict(id='current', t=log.started_wall + 1, note='Current call on desk', labels=['resistor']))
    rewind = FixtureRewind()
    rewind.memory_writer = SimpleNamespace(store=store, embedder=None)
    return SimpleNamespace(recent_memory=log, rewind=rewind, private=False)


async def benchmark():
    with TemporaryDirectory(prefix='a-eye-recent-') as folder:
        session = fixture(Path(folder))
        rows = []
        for question, on, expected, content in QUESTIONS:
            session.rewind.on = on
            started = perf_counter()
            result = await answer(session, question)
            ms = (perf_counter() - started) * 1000
            rows.append(dict(question=question, expected=expected, selected=source_for(question, on),
                             source=result['source'], ms=round(ms, 3),
                             correct=result['source'] == expected, answer_ok=content in result['say']))
        groups = {}
        for source in ('recent', 'rewind', 'long-term'):
            times = [r['ms'] for r in rows if r['expected'] == source]
            groups[source] = dict(n=len(times), p50_ms=round(median(times), 3), max_ms=max(times))
        return dict(right_source=sum(r['correct'] for r in rows), total=len(rows),
                    answer_checks=sum(r['answer_ok'] for r in rows), latency=groups, rows=rows,
                    limitation='Rewind adapter is a deterministic stub; no TwelveLabs/network/TTS latency measured.')


if __name__ == '__main__':
    print(json.dumps(asyncio.run(benchmark()), indent=2))
