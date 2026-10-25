import asyncio
import json

import pytest

from scripts.step_check_eval import evaluate, fixture_methods, split, synthetic_fixture
from server.step_check import Verdict, configured_checker


def test_fixed_split_freezes_tuning_before_held_out_and_never_uses_held_refs(tmp_path):
    synthetic_fixture(tmp_path)
    tune, held = split(tmp_path)
    assert split(tmp_path) == (tune, held)
    assert not {p for _, p in tune} & {p for _, p in held}
    # Audit every inference, including refs. Checker normalizes image bytes before invoking methods.
    from server.step_check import image_bytes
    held_bytes = {image_bytes(p.read_bytes()) for _, p in held}
    order = []
    class Audit:
        def __init__(self, name):
            self.name = name
            self.encoder = type('Encoder', (), {'path': ''})()
        async def classify(self, frame, closed, opened):
            assert closed not in held_bytes and opened not in held_bytes
            order.append((self.name, frame in held_bytes))
            return Verdict()
    result = asyncio.run(evaluate(tmp_path, {n: Audit(n) for n in ('qwen', 'embedding')}, synthetic=True))
    first_held = next(i for i, (_, held) in enumerate(order) if held)
    assert {n for n, _ in order[:first_held]} == {'qwen', 'embedding'}
    assert all(held for _, held in order[first_held:])
    assert result['selected'] is None  # abstaining on everything is not a viable method


def test_synthetic_pipeline_cannot_activate_runtime(tmp_path, monkeypatch):
    synthetic_fixture(tmp_path)
    report = asyncio.run(evaluate(tmp_path, fixture_methods(), synthetic=True))
    assert report['held_out_count'] == 6
    assert all(s['false_closed'] == 0 and s['matrix']['closed']['closed'] == 3
               for s in report['methods'].values())
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    monkeypatch.setenv('STEP_CHECK_CONFIG', str(path))
    assert configured_checker().method is None


def test_duplicate_photo_refused_before_split(tmp_path):
    synthetic_fixture(tmp_path)
    (tmp_path / 'closed' / 'copy.png').write_bytes((tmp_path / 'open' / '0.png').read_bytes())
    with pytest.raises(ValueError, match='duplicate'):
        split(tmp_path)
