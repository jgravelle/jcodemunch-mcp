"""The cold-index Floor is judged against another timing of the same job.

`index.cold_self_seconds` is a wall clock, so it measures the runner as much
as the code: across the 22 CI readings recorded in
`harness/results/self_latency_ci_runs_2026-11-06.json` it ran from 2.9 s to
5.3 s, and three of them, at two commits, failed the 5.2 s floor (LEDGER L-314).
Each red reading on `main` stopped the release pre-flight until someone re-ran
the job. Divided by `index.one_file_reindex_ms` from the same job, the same
readings stay in a narrow band, because both timings slow down together on a
slow runner.

So the cold index is judged as that ratio. It still catches a change that
slows cold indexing more than one-file reindexing. It gives up a change that
slows both by the same factor; the absolute row stays as a backstop with a
looser floor, recorded in its `loosened` block.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import thresholds as T  # noqa: E402

RATIO = "index.cold_over_one_file_ratio"
COLD = "index.cold_self_seconds"
REINDEX = "index.one_file_reindex_ms"
RECORD = REPO / "harness" / "results" / "self_latency_ci_runs_2026-11-06.json"


def _measure():
    spec = importlib.util.spec_from_file_location(
        "self_latency_measure", REPO / "benchmarks" / "self_latency" / "measure.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runs() -> list[dict]:
    runs = json.loads(RECORD.read_text(encoding="utf-8"))["runs"]
    assert len(runs) >= 20, "the record the band was set from is missing or short"
    return runs


def _ratio(run: dict, cold_factor: float = 1.0, reindex_factor: float = 1.0) -> float:
    return _measure().cold_over_one_file_ratio(
        run[COLD] * cold_factor, run[REINDEX] * reindex_factor
    )


def test_the_ratio_is_a_floor_and_not_a_wall_clock():
    entry = T.get(RATIO)
    assert entry["comparator"] == "<="
    # Not a timing unit: the point of the row is that runner speed divides
    # out, so it is judged wherever it is measured, not deferred to CI.
    assert entry["unit"] == "ratio"


def test_every_recorded_ci_reading_passes_the_ratio():
    failing = [(r["date"], _ratio(r)) for r in _runs() if not T.passes(RATIO, _ratio(r))]
    assert failing == [], f"recorded CI readings fail {RATIO}: {failing}"


def test_the_record_holds_the_readings_the_old_floor_failed():
    """Non-vacuity: the record is the one the defect showed up in."""
    old = T.get(COLD)["history"][-1]["floor"]
    slow = [r[COLD] for r in _runs() if r[COLD] > old]
    assert slow, "no recorded reading exceeds the old absolute floor; the record cannot show the defect"


def test_a_cold_only_slowdown_fails_the_ratio_on_every_reading():
    """What the ratio still catches: cold indexing slowing on its own."""
    passing = [r["date"] for r in _runs() if T.passes(RATIO, _ratio(r, cold_factor=2.0))]
    assert passing == [], f"a 2x cold-only slowdown passed {RATIO} on: {passing}"


def test_a_cold_only_step_the_size_of_f44_fails_from_the_highest_reading():
    """F-44 (#1005) was a 1.3x cold-only step; the band is set so it fails even
    from the highest normal reading, not only from the median."""
    highest = max(_runs(), key=_ratio)
    assert not T.passes(RATIO, _ratio(highest, cold_factor=1.3))


def test_a_slowdown_of_both_passes_the_ratio():
    """What the ratio gives up, stated as a test so the PR text cannot drift."""
    assert all(T.passes(RATIO, _ratio(r, 2.0, 2.0)) for r in _runs())


def test_the_absolute_row_is_a_loosened_backstop():
    entry = T.get(COLD)
    assert entry.get("loosened"), "the absolute row moved without a `loosened` block"
    prev = entry["history"][-1]["floor"]
    assert entry["floor"] > prev
    # The backstop still fails a run twice as slow as the slowest recorded one.
    slowest = max(r[COLD] for r in _runs())
    assert not T.passes(COLD, slowest * 2.0)


def test_every_recorded_ci_reading_passes_the_backstop():
    """A backstop set below a recorded reading brings the L-314 flake back."""
    failing = [(r["date"], r[COLD]) for r in _runs() if not T.passes(COLD, r[COLD])]
    assert failing == [], f"recorded CI readings fail the {COLD} backstop: {failing}"


def test_a_one_file_speedup_moves_the_ratio_up():
    """The denominator's cost, stated so the band is re-set when it bites: a
    one-file reindex 1.3x faster fails the ratio from the highest reading."""
    highest = max(_runs(), key=_ratio)
    assert not T.passes(RATIO, _ratio(highest, reindex_factor=1 / 1.3))


def test_a_one_file_regression_is_judged_by_its_own_floor():
    """A slower one-file reindex lowers the ratio, so its own Floor must exist
    and still be judged by the same bench step."""
    assert T.get(REINDEX)["comparator"] == "<="
    tiers = json.loads((REPO / "harness" / "tiers.json").read_text(encoding="utf-8"))
    step = next(s for s in tiers["bench"] if s["name"] == "self_latency")
    assert REINDEX in step["thresholds"]


def test_measure_writes_every_index_floor_the_step_declares():
    """The bench step declares the Floors; `run()` must write each `index.*`
    one. Deleting the ratio's assignment passed every other test."""
    import ast

    tiers = json.loads((REPO / "harness" / "tiers.json").read_text(encoding="utf-8"))
    step = next(s for s in tiers["bench"] if s["name"] == "self_latency")
    declared = {t for t in step["thresholds"] if t.startswith("index.")}
    tree = ast.parse((REPO / "benchmarks" / "self_latency" / "measure.py").read_text(encoding="utf-8"))
    run = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run")
    written = {
        t.slice.value
        for n in ast.walk(run) if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "out"
        and isinstance(t.slice, ast.Constant)
    }
    assert declared - written == set(), f"declared but never written by run(): {declared - written}"


def test_a_step_that_prints_verdicts_but_skips_a_declared_floor_fails(tmp_path, monkeypatch):
    """One layer down: the bench tier fails a step that judges by verdict
    lines and printed none for a Floor it declares."""
    import importlib

    hm = importlib.import_module("harness.__main__")
    script = tmp_path / "measure.py"
    script.write_text(
        f"print({COLD!r} + '  crit 3  floor <= 9  observed 3.1  PASS')\nprint('done')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hm, "REPO", tmp_path)
    monkeypatch.setattr(
        hm, "TIERS", {"bench": [{"name": "self_latency", "cmd": [str(script)], "thresholds": [COLD, RATIO]}]}
    )
    assert hm.tier_bench({"tiers": {}}, offline=True) is False


def test_the_bench_step_judges_the_ratio():
    tiers = json.loads((REPO / "harness" / "tiers.json").read_text(encoding="utf-8"))
    steps = [s for tier in tiers.values() if isinstance(tier, list) for s in tier if isinstance(s, dict)]
    step = next(s for s in steps if s.get("name") == "self_latency")
    assert RATIO in step["thresholds"]


def test_measure_divides_the_two_timings_of_one_job():
    ratio = _measure().cold_over_one_file_ratio
    assert ratio(4.4, 100.0) == 44.0
    assert ratio(5.237, 119.8) == round(5237 / 119.8, 1)
