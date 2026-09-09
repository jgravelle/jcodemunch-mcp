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
    assert "self_latency" not in result["artifacts"] or "probe" not in result["artifacts"]["self_latency"]


def test_the_shipped_self_latency_step_declares_its_artifact():
    tiers = json.loads((REPO / "harness" / "tiers.json").read_text(encoding="utf-8"))
    step = next(s for s in tiers["bench"] if s["name"] == "self_latency")
    assert step["artifact"] == ARTIFACT
    assert step["cmd"][step["cmd"].index("--out") + 1] == ARTIFACT, "the command's --out must name the declared artifact, or the substitution misses it"
