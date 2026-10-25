"""Checks for benchmark evidence math and the spend guard; no paid calls."""
import pytest
from scripts import system_bench as bench


def test_nearest_rank_p95_and_missing_samples():
    assert bench.summary([None, 10, 2, 1, 3, 4, 5, 6, 7, 8, 9]) == {'n': 10, 'p50': 5.5, 'p95': 10}
    assert bench.summary([]) == {'n': 0}
    assert bench.summary(list(range(1, 21)))['p95'] == 19


def test_budget_refuses_before_side_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, 'OUT', tmp_path / 'evidence.json')
    monkeypatch.setattr(bench, 'DATA', {'budget_reserved_usd': 1.98})
    bench.reserve(.02)
    assert bench.DATA['budget_reserved_usd'] == 2
    before = bench.OUT.read_bytes()
    with pytest.raises(RuntimeError, match='BudgetExhausted'):
        bench.reserve(.001)
    assert bench.OUT.read_bytes() == before

def test_private_mode_blocks_paid_phase_before_read_or_request(monkeypatch):
    import argparse
    import asyncio
    monkeypatch.setenv('LOCAL_ONLY', '1')
    monkeypatch.setattr(bench, 'OUT', None)
    with pytest.raises(SystemExit, match='Private mode'):
        asyncio.run(bench.main(argparse.Namespace(phase='direct')))
