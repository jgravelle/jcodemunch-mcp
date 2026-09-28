"""A warm-p95 latency Floor that fails is re-sampled once before it is
reported (#906, #911; harness FINDINGS F-19).

`benchmarks/self_latency/measure.py` gates a p95 over 20 calls, so two
preempted calls on a shared CI runner are the 95th percentile. Twelve FAILs
on `latency.*` ids read 7x to 73x the clean median with the median unmoved,
on changes that cannot touch the read path, and each needed a job re-run by
hand. No floor level separates that from a regression; the measurement does.

The property: a warm series whose p95 FAILS its Floor is measured again in the
same process, and the second series is the one reported. A tail that
reproduces still fails; one that does not passes, and the first p95 is kept
beside it so nothing is hidden. A passing series is measured once.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MEASURE = REPO / "benchmarks" / "self_latency" / "measure.py"
KEY = "latency.get_file_outline_warm_p95_ms"


def _measure():
    spec = importlib.util.spec_from_file_location("self_latency_measure", MEASURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _floor_28(key, value):
    return value <= 28


def _series(*runs):
    calls = []
    it = iter(runs)

    def sample():
        calls.append(1)
        return list(next(it))

    return sample, calls


CLEAN = [1.0] * 20
PREEMPTED = [1.0] * 18 + [70.0, 90.0]  # two slow calls are the p95 of twenty


def test_a_tail_that_does_not_reproduce_passes_and_keeps_the_first_p95():
    sample, calls = _series(PREEMPTED, CLEAN)
    rec = _measure()._warm_p95("get_file_outline", sample, _floor_28)
    assert len(calls) == 2
    assert rec[KEY] == 1.0
    assert rec["latency.get_file_outline_warm_p95_first_ms"] == 70.0
    assert rec["latency.get_file_outline_resampled"] is True


def test_a_tail_that_reproduces_still_fails():
    sample, calls = _series(PREEMPTED, PREEMPTED)
    rec = _measure()._warm_p95("get_file_outline", sample, _floor_28)
    assert len(calls) == 2
    assert not _floor_28(KEY, rec[KEY])
    assert rec["latency.get_file_outline_resampled"] is True


def test_a_passing_series_is_measured_once():
    sample, calls = _series(CLEAN)
    rec = _measure()._warm_p95("get_file_outline", sample, _floor_28)
    assert len(calls) == 1
    assert rec[KEY] == 1.0
    assert rec["latency.get_file_outline_resampled"] is False
    assert "latency.get_file_outline_warm_p95_first_ms" not in rec


def test_an_id_with_no_floor_is_measured_once():
    sample, calls = _series(PREEMPTED)
    rec = _measure()._warm_p95("get_file_outline", sample, None)
    assert len(calls) == 1
    assert rec[KEY] == 70.0


def test_every_warm_series_in_run_goes_through_the_resample():
    """`run` measures each tool through `_warm_p95`, so no fifth tool added
    later is timed by a hand-rolled loop that skips the re-sample."""
    tree = ast.parse(MEASURE.read_text(encoding="utf-8"))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    called = {
        n.func.id for n in ast.walk(run)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_warm_p95" in called
    warm_keys = [
        n.value for n in ast.walk(run)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "_warm_p95_ms" in n.value
    ]
    assert warm_keys == [], f"run() writes a warm p95 key itself: {warm_keys}"
