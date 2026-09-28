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


def _docstrings(tree) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def test_every_warm_series_in_run_goes_through_the_resample():
    """No function but `_warm_p95` spells a warm p95 key, so a fifth tool
    timed later -- in `run` or in a helper `run` calls -- cannot write one
    that skips the re-sample (review: the first guard scanned `run` only)."""
    tree = ast.parse(MEASURE.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    owner = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_warm_p95")
    inside = {id(n) for n in ast.walk(owner)}
    stray = [
        (n.lineno, n.value) for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "_warm_p95_ms" in n.value
        and id(n) not in inside and id(n) not in docs
    ]
    assert stray == [], f"a warm p95 key written outside _warm_p95: {stray}"


def test_run_resamples_against_the_threshold_file():
    """`run` hands `_warm_p95` the Floor check built from
    harness/thresholds.json; `passes = None` there would re-sample nothing
    while every injected-series test stayed green (review)."""
    tree = ast.parse(MEASURE.read_text(encoding="utf-8"))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    bound = {
        t.id for n in ast.walk(run) if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) == "_floor_check"
        for t in n.targets if isinstance(t, ast.Name)
    }
    calls = [
        n for n in ast.walk(run)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_warm_p95"
    ]
    assert calls, "run() never calls _warm_p95"
    for c in calls:
        assert len(c.args) == 3 and isinstance(c.args[2], ast.Name) and c.args[2].id in bound, ast.dump(c)


def test_the_floor_check_reads_the_threshold_file():
    passes = _measure()._floor_check()
    assert passes(KEY, 1.0) is True
    assert passes(KEY, 10_000.0) is False
    assert passes("latency.not_a_floor_warm_p95_ms", 10_000.0) is True
