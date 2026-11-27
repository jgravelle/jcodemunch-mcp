"""The PR-gate bench jobs skip-pass a diff that touches no stamp code root.

PR 1580 changed only docs/workflows/LEDGER.md and failed the PR bench tier on
two latency readings, then waited on a rerun. The maintainer's ruling (
2026-11-27, row 1623): the PR-gate bench skip-passes when a diff touches no
stamp code root, and the post-merge bench on main is unchanged.

The property:
- the roots are the stamp's own list (`.claude/hooks/_common.paths_for("stamp")`),
  never a second copy;
- a diff with one path under a root runs the bench, and a diff with none
  skips it, naming the rule;
- a diff that cannot be read runs the bench (unknown is not "docs only");
- every step of both PR bench jobs after the scope step is gated on it, so a
  skipped job still reports its required check by name, and main.yml runs
  the bench with no such gate.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "bench_gate_scope.py"
GATE = "steps.scope.outputs.run == 'true'"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scope():
    return _load(SCRIPT, "bench_gate_scope")


def test_the_roots_are_the_stamps_own_list():
    common = _load(REPO / ".claude" / "hooks" / "_common.py", "hooks_common_for_scope")
    assert _scope().code_roots() == common.paths_for("stamp")


def test_a_docs_only_diff_skips_and_names_the_rule():
    run, reason = _scope().decide(["docs/workflows/LEDGER.md", "CHANGELOG.md", "README.md"])
    assert run is False
    assert "stamp code root" in reason and "main" in reason


def test_one_path_under_a_root_runs_the_bench():
    for path in ("src/jcodemunch_mcp/server.py", "benchmarks/self_latency/measure.py",
                 ".github/workflows/pr-gate.yml", "uv.lock", ".claude/hooks/_common.py"):
        run, reason = _scope().decide(["docs/x.md", path])
        assert run is True, path
        assert path in reason


def test_an_unreadable_diff_runs_the_bench():
    run, reason = _scope().decide(None)
    assert run is True
    assert "could not" in reason


def test_a_path_that_only_shares_a_prefix_is_not_under_a_root():
    """`srcfoo/x.py` is not under `src/`; `uv.lock.bak` is not `uv.lock`."""
    run, _ = _scope().decide(["srcfoo/x.py", "uv.lock.bak", "pyproject.toml.orig"])
    assert run is False


def test_the_cli_writes_the_output_and_prints_the_rule(tmp_path):
    out = tmp_path / "out.txt"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--paths", "docs/a.md"],
        env=dict(os.environ, GITHUB_OUTPUT=str(out)), capture_output=True, text=True, cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.read_text(encoding="utf-8").strip() == "run=false"
    assert "bench gate: skipped" in proc.stdout


def _jobs(name: str) -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))["jobs"]


def test_every_pr_bench_step_after_the_scope_step_is_gated():
    jobs = _jobs("pr-gate.yml")
    for job in ("bench", "bench-tokens"):
        steps = jobs[job]["steps"]
        ids = [s.get("id") for s in steps]
        assert "scope" in ids, f"{job} has no scope step"
        after = steps[ids.index("scope") + 1:]
        assert after, job
        for s in after:
            assert GATE in str(s.get("if", "")), (job, s.get("name") or s.get("uses"))


def test_main_runs_the_bench_with_no_scope_gate():
    text = (REPO / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
    assert "bench_gate_scope" not in text and "steps.scope" not in text
    assert "harness bench" in text
