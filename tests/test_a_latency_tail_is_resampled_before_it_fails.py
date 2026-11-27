"""A warm-p95 latency Floor that fails is re-sampled before it is reported
(#906, #911; harness FINDINGS F-19), later and more than once since PR 1580.

`benchmarks/self_latency/measure.py` gated a p95 over 20 calls, so two
preempted calls on a shared CI runner were the 95th percentile. Twelve FAILs
on `latency.*` ids read 7x to 73x the clean median with the median unmoved,
on changes that cannot touch the read path. F-19 re-sampled a failing series
once, at once, and gated the second series; PR 1580 (a LEDGER-only diff)
still failed two Floors, because a runner contended for one series is often
contended for the next.

The property now:
- a warm series is 40 calls, so two slow calls are not its p95;
- no re-sample runs until every tool's first series has run, and each round
  waits a pause first;
- up to two rounds re-sample only the series still failing, and the lowest
  p95 of a tool's series is reported, with the first p95 and every series'
  p95 beside it;
- a tail that reproduces in every series still fails, and a passing series,
  or an id with no Floor, is measured once.
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


CLEAN = [1.0] * 40
TWO_SLOW = [1.0] * 38 + [70.0, 90.0]     # two preempted calls in forty
TAIL = [1.0] * 37 + [70.0, 80.0, 90.0]   # three: the p95 of forty


class _Log:
    """Injected samplers and a pause that record the order of events."""

    def __init__(self, runs: dict[str, list[list[float]]]):
        self.events: list[str] = []
        self._it = {label: iter(series) for label, series in runs.items()}

    def sampler(self, label):
        def sample():
            self.events.append(label)
            return list(next(self._it[label]))
        return sample

    def pause(self, _seconds):
        self.events.append("pause")


def _run(runs, passes=_floor_28):
    log = _Log(runs)
    m = _measure()
    series = m._warm_series({k: log.sampler(k) for k in runs}, passes, sleep=log.pause)
    return m, series, log


def test_a_warm_series_is_forty_calls():
    assert _measure().WARM_N == 40


def test_two_slow_calls_in_forty_pass_without_a_resample():
    m, series, log = _run({"get_file_outline": [TWO_SLOW]})
    rec = m._warm_p95("get_file_outline", series["get_file_outline"])
    assert log.events == ["get_file_outline"]
    assert rec[KEY] == 1.0 and rec["latency.get_file_outline_resampled"] is False


def test_no_resample_runs_before_every_tool_has_its_first_series():
    m, series, log = _run({
        "get_symbol_source": [TAIL, CLEAN],
        "get_file_outline": [CLEAN],
    })
    assert log.events == ["get_symbol_source", "get_file_outline", "pause", "get_symbol_source"]


def test_the_lowest_p95_of_the_series_is_reported_not_the_last():
    m, series, log = _run({"get_file_outline": [TAIL, TAIL, CLEAN]})
    rec = m._warm_p95("get_file_outline", series["get_file_outline"])
    assert log.events == ["get_file_outline", "pause", "get_file_outline", "pause", "get_file_outline"]
    assert rec[KEY] == 1.0
    assert rec["latency.get_file_outline_warm_p95_first_ms"] == 70.0
    assert rec["latency.get_file_outline_warm_p95_series_ms"] == [70.0, 70.0, 1.0]
    assert rec["latency.get_file_outline_resampled"] is True


def test_a_series_that_passes_on_resample_stops_resampling():
    m, series, log = _run({"get_file_outline": [TAIL, CLEAN, TAIL]})
    assert log.events == ["get_file_outline", "pause", "get_file_outline"]
    rec = m._warm_p95("get_file_outline", series["get_file_outline"])
    assert rec[KEY] == 1.0


def test_a_tail_that_reproduces_in_every_series_still_fails():
    m, series, log = _run({"get_file_outline": [TAIL, TAIL, TAIL, CLEAN]})
    rec = m._warm_p95("get_file_outline", series["get_file_outline"])
    assert log.events.count("get_file_outline") == 3
    assert not _floor_28(KEY, rec[KEY])


def test_an_id_with_no_floor_is_measured_once():
    m, series, log = _run({"get_file_outline": [TAIL, CLEAN]}, passes=None)
    rec = m._warm_p95("get_file_outline", series["get_file_outline"])
    assert log.events == ["get_file_outline"]
    assert rec[KEY] == 70.0
    assert "latency.get_file_outline_warm_p95_first_ms" not in rec


def _docstrings(tree) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def test_every_warm_series_in_run_goes_through_the_resample():
    """Only the resample path (`_warm_series` and `_warm_p95`) spells a warm
    p95 key, so a fifth tool timed later -- in `run` or in a helper `run`
    calls -- cannot write one that skips the re-sample."""
    tree = ast.parse(MEASURE.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    owners = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"_warm_p95", "_warm_series"}]
    assert len(owners) == 2
    inside = {id(n) for o in owners for n in ast.walk(o)}
    stray = [
        (n.lineno, n.value) for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "_warm_p95_ms" in n.value
        and id(n) not in inside and id(n) not in docs
    ]
    assert stray == [], f"a warm p95 key written outside the resample path: {stray}"


def test_run_resamples_against_the_threshold_file():
    """`run` hands `_warm_series` the Floor check built from
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
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_warm_series"
    ]
    assert len(calls) == 1, "run() must take every warm series through one _warm_series call"
    c = calls[0]
    assert len(c.args) >= 2 and isinstance(c.args[1], ast.Name) and c.args[1].id in bound, ast.dump(c)


def test_the_floor_check_reads_the_threshold_file():
    passes = _measure()._floor_check()
    assert passes(KEY, 1.0) is True
    assert passes(KEY, 10_000.0) is False
    assert passes("latency.not_a_floor_warm_p95_ms", 10_000.0) is True


def test_the_resample_is_deferred_and_bounded_by_its_constants():
    """A zero pause re-samples at once, the F-19 rule this replaced; the
    defaults of `_warm_series` must be the module's constants (review)."""
    import inspect

    m = _measure()
    assert m.RESAMPLES == 2 and m.RESAMPLE_PAUSE_S > 0
    sig = inspect.signature(m._warm_series)
    assert sig.parameters["resamples"].default == m.RESAMPLES
    assert sig.parameters["pause"].default == m.RESAMPLE_PAUSE_S


def test_run_sizes_each_warm_series_by_warm_n():
    """`run` takes `warm_n` and the warm sampler loops over it, not over `n`
    (a parameter that is present and does nothing)."""
    tree = ast.parse(MEASURE.read_text(encoding="utf-8"))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    assert [a.arg for a in run.args.args][:2] == ["n", "warm_n"]
    sample = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == "sample")
    loops = [n for n in ast.walk(sample) if isinstance(n, ast.For)]
    assert loops and all(
        isinstance(f.iter, ast.Call) and getattr(f.iter.func, "id", None) == "range"
        and isinstance(f.iter.args[0], ast.Name) and f.iter.args[0].id == "warm_n"
        for f in loops
    ), ast.dump(sample)


DETECTION = REPO / "benchmarks" / "self_latency" / "detection_power.txt"


def test_the_detection_table_is_what_its_script_prints():
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, str(REPO / "benchmarks" / "self_latency" / "detection_power.py")],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out == DETECTION.read_text(encoding="utf-8")


def test_every_probability_in_the_metric_history_is_in_the_detection_table():
    """The 2026-11-27 history entries quote only numbers the committed table prints."""
    import json
    import re

    table = DETECTION.read_text(encoding="utf-8")
    rows = json.loads((REPO / "harness" / "thresholds.json").read_text(encoding="utf-8"))["thresholds"]
    seen = 0
    for t in rows:
        if t["id"].startswith("latency.") and t["id"].endswith("_warm_p95_ms"):
            entry = next(h for h in t["history"] if h["date"] == "2026-11-27")
            for num in re.findall(r"\b0\.\d{4}\b", entry["reason"]):
                assert num in table, (t["id"], num)
                seen += 1
    assert seen >= 4 * 6
