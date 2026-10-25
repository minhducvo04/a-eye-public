import asyncio
import time

from scripts.offline_check import seed
from server import memory


def test_yesterday_fixture_is_text_only_and_found(tmp_path):
    store = memory.Store('fixture', base=tmp_path, cipher=memory.Cipher(''))
    seed(store)
    records = store.records()
    assert len(records) == 1
    assert abs(time.time() - records[0]['t'] - 86400) < 5
    result = asyncio.run(memory.find(store, 'Where did I leave my keys yesterday?'))
    assert result['found'] and 'yesterday' in result['say'].lower()
    assert result['crop'] is None
    assert [path.name for path in store.dir.iterdir()] == ['log.jsonl']
