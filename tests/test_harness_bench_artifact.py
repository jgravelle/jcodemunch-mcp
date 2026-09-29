"""A bench step writes its artifact to scratch; the tracked copy moves only under
`--write-results` (harness FINDINGS F-23).

`benchmarks/self_latency/measure.py --out harness/results/self_latency.json`
rewrote a TRACKED file on every bench run, on any branch, with or without
`--write-results`. `latest.json` was already scratch-first (W-29 closed the
compare command's half); the self-latency artifact was not, so a bench run on
a PR branch left `harness/results/self_latency.json` dirty in the working
tree, and the NEXT PR's checklist read `harness/` as changed and demanded a
bench tier for a test-only diff. A step now declares its `artifact`; the
runner substitutes a scratch path in the command, reads the artifact from
there, and copies it into the tree only when asked to write results.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
hm = importlib.import_module("harness.__main__")

ARTIFACT = "harness/results/self_latency.json"


@pytest.fixture
def scratch_repo(tmp_path, monkeypatch):
    """A repo the test owns: the artifact target lives under it, never under ours."""
    (tmp_path / "harness" / "results").mkdir(parents=True)
    script = tmp_path / "measure.py"
    script.write_text(
        "import json, sys\n"
        "out = sys.argv[sys.argv.index('--out') + 1]\n"
        "json.dump({'probe': 'scratch-first', 'out': out}, open(out, 'w'))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hm, "REPO", tmp_path)
    monkeypatch.setattr(hm, "RESULTS_DIR", tmp_path / "harness" / "results")
    monkeypatch.setattr(hm, "TIERS", {"bench": [{"name": "self_latency", "cmd": [str(script), "--out", ARTIFACT], "artifact": ARTIFACT, "thresholds": []}]})
    return tmp_path


def test_a_bench_run_leaves_the_tracked_artifact_alone_and_still_reads_it(scratch_repo):
    result: dict = {"tiers": {}}
    assert hm.tier_bench(result, offline=True) is True
    assert not (scratch_repo / ARTIFACT).exists(), "the tracked path was written without --write-results"
    art = result["artifacts"]["self_latency"]
    assert art["probe"] == "scratch-first"
    assert not Path(art["out"]).resolve().is_relative_to(scratch_repo), art["out"]


def test_write_results_copies_the_artifact_into_the_tree(scratch_repo):
    result: dict = {"tiers": {}}
    assert hm.tier_bench(result, offline=True, write_results=True) is True
    tracked = json.loads((scratch_repo / ARTIFACT).read_text(encoding="utf-8"))
    assert tracked["probe"] == "scratch-first"
    assert result["artifacts"]["self_latency"]["probe"] == "scratch-first"


def test_a_failed_step_copies_nothing(scratch_repo, monkeypatch):
    bad = scratch_repo / "bad.py"
    bad.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    monkeypatch.setattr(hm, "TIERS", {"bench": [{"name": "self_latency", "cmd": [str(bad), "--out", ARTIFACT], "artifact": ARTIFACT, "thresholds": []}]})
    result: dict = {"tiers": {}}
    assert hm.tier_bench(result, offline=True, write_results=True) is False
    assert not (scratch_repo / ARTIFACT).exists()
    # The failed step's own record survives; nothing replaces it with a stale tracked file.
    assert result["artifacts"]["self_latency"]["rc"] == 3
    assert "probe" not in result["artifacts"]["self_latency"]


def test_the_shipped_self_latency_step_declares_its_artifact():
    tiers = json.loads((REPO / "harness" / "tiers.json").read_text(encoding="utf-8"))
    step = next(s for s in tiers["bench"] if s["name"] == "self_latency")
    assert step["artifact"] == ARTIFACT
    assert step["cmd"][step["cmd"].index("--out") + 1] == ARTIFACT, "the command's --out must name the declared artifact, or the substitution misses it"


def test_every_step_that_declares_an_artifact_names_it_in_its_command_and_writes_under_results():
    """The substitution is by string equality, so a step whose command spells the
    path differently would write the tracked file again with every test green
    (review round 1; Standing lesson 08-18: the ratchet over the property, not the
    instance). Also: an artifact is a results file, nothing else in the tree."""
    tiers = json.loads((REPO / "harness" / "tiers.json").read_text(encoding="utf-8"))
    declared = [s for tier in tiers.values() if isinstance(tier, list) for s in tier if isinstance(s, dict) and s.get("artifact")]
    assert declared, "the self_latency step declares an artifact; the scan found none"
    for s in declared:
        assert s["artifact"] in s["cmd"], (s["name"], s["artifact"], s["cmd"])
        assert s["artifact"].startswith("harness/results/"), (s["name"], s["artifact"])


def _failing_measure(scratch_repo, monkeypatch):
    """A step that WRITES its measurement and then fails a Floor, as measure.py does."""
    script = scratch_repo / "measure_fail.py"
    script.write_text(
        "import json, sys\n"
        "out = sys.argv[sys.argv.index('--out') + 1]\n"
        "json.dump({'probe': 'failing-run', 'latency.x_warm_p95_ms': 116.3}, open(out, 'w'))\n"
        "print('latency.x_warm_p95_ms crit 5 floor <= 27 observed 116.3 FAIL')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hm, "TIERS", {"bench": [{"name": "self_latency", "cmd": [str(script), "--out", ARTIFACT], "artifact": ARTIFACT, "thresholds": []}]})


FAILED = "harness/results/self_latency.failed.json"


def test_a_failed_step_keeps_its_own_measurement_in_the_result(scratch_repo, monkeypatch):
    """LEDGER L-72: the measurement of a red run was deleted with the scratch dir, and
    CI uploaded the TRACKED file under the failing run's name (#922: log 116.3 FAIL,
    artifact 1.6 from an older commit)."""
    _failing_measure(scratch_repo, monkeypatch)
    result: dict = {"tiers": {}}
    assert hm.tier_bench(result, offline=True) is False
    art = result["artifacts"]["self_latency"]
    assert art["rc"] == 1
    assert art["measurement"]["latency.x_warm_p95_ms"] == 116.3
    assert not (scratch_repo / ARTIFACT).exists()
    assert not (scratch_repo / FAILED).exists(), "a plain run writes nothing into the tree"


def test_write_results_puts_a_failed_measurement_beside_the_tracked_file_never_over_it(scratch_repo, monkeypatch):
    tracked = scratch_repo / ARTIFACT
    tracked.write_text(json.dumps({"probe": "last-weekly-commit"}), encoding="utf-8")
    _failing_measure(scratch_repo, monkeypatch)
    result: dict = {"tiers": {}}
    assert hm.tier_bench(result, offline=True, write_results=True) is False
    assert json.loads(tracked.read_text(encoding="utf-8"))["probe"] == "last-weekly-commit"
    failed = json.loads((scratch_repo / FAILED).read_text(encoding="utf-8"))
    assert failed["probe"] == "failing-run"


def test_a_passing_write_results_run_removes_a_stale_failed_file(scratch_repo):
    """A .failed.json left by an earlier red run must not ride along with a green one."""
    stale = scratch_repo / FAILED
    stale.write_text("{}", encoding="utf-8")
    result: dict = {"tiers": {}}
    assert hm.tier_bench(result, offline=True, write_results=True) is True
    assert not stale.exists()


def test_every_workflow_that_uploads_the_result_uploads_the_failed_measurement():
    """Over every workflow, not a named list: the first draft named pr-gate and main,
    and nightly.yml's `bench --write-results` upload kept the defect (review of L-72)."""
    uploading = []
    for wf in sorted((REPO / ".github" / "workflows").glob("*.y*ml")):
        text = wf.read_text(encoding="utf-8")
        if "harness/results/self_latency.json" in text:
            uploading.append(wf.name)
            assert FAILED in text, f"{wf.name} uploads the tracked file but not the failed run's own measurement"
    assert {"pr-gate.yml", "main.yml", "nightly.yml"} <= set(uploading), uploading
