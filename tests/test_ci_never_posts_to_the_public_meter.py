"""A CI runner is not a user: nothing it runs reports to the public savings total.

The savings telemetry has one gate, the `share_savings` setting, and it
defaults to on. No workflow turned it off, so every step that ran the tools
outside pytest (the benchmark tiers, the weekly Benchmark workflow, the release
smoke steps) started on a fresh runner, made a new `anon_id` and posted like an
install (docs/cicd/FINDINGS.md C-23). The suite itself never could: its
network guard refuses the connection.

Every workflow now sets `JCODEMUNCH_SHARE_SAVINGS: "0"` at the top level and
the harness sets it for every tier it spawns. The suite removes the variable
for the session, so a test of the shipped default still sees the default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VAR = "JCODEMUNCH_SHARE_SAVINGS"


def _top_level_env(text: str) -> dict:
    """The keys of a workflow's top-level `env:` block (two-space entries until the next top-level key)."""
    out: dict = {}
    inside = False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            inside = line.rstrip() == "env:"
            continue
        if inside:
            m = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def test_every_workflow_switches_sharing_off_at_the_top_level():
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert len(workflows) >= 20, "the roster is read off disk; this floor only catches an empty glob"
    missing = [w.name for w in workflows if _top_level_env(w.read_text(encoding="utf-8")).get(VAR) != '"0"']
    assert missing == [], f"these workflows can report a runner's tool calls to the public total: {missing}"


def test_a_job_or_step_cannot_switch_it_back_on():
    offenders = []
    for w in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for n, line in enumerate(w.read_text(encoding="utf-8").splitlines(), start=1):
            m = re.match(r"^\s+" + VAR + r":\s*(.*)$", line)
            if m and m.group(1).strip() != '"0"':
                offenders.append(f"{w.name}:{n}")
    assert offenders == []


def test_the_parser_reads_a_block_and_stops_at_the_next_key():
    """The check above must fail when the variable sits under a job, not at the top."""
    top = "name: x\nenv:\n  # why\n  " + VAR + ': "0"\n  UV_VERSION: "1"\n\njobs:\n  a:\n    env:\n      OTHER: "1"\n'
    assert _top_level_env(top) == {VAR: '"0"', "UV_VERSION": '"1"'}
    nested = "name: x\njobs:\n  a:\n    env:\n      " + VAR + ': "0"\n'
    assert _top_level_env(nested) == {}
    assert _top_level_env("env:\n  " + VAR + ': "1"\njobs:\n').get(VAR) == '"1"'


def test_the_harness_switches_it_off_for_every_tier_it_spawns(monkeypatch):
    import subprocess
    import sys

    sys.path.insert(0, str(ROOT))
    import harness.__main__ as h

    seen = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv(VAR, raising=False)
    h._run(["python", "-c", "pass"])
    assert seen[VAR] == "0"
    # An explicit choice by whoever runs the harness is kept.
    monkeypatch.setenv(VAR, "1")
    h._run(["python", "-c", "pass"])
    assert seen[VAR] == "1"


@pytest.mark.parametrize("value, posts", [("0", False), (None, True)], ids=["switched-off", "unset"])
def test_the_variable_stops_the_post_not_just_the_spelling(tmp_path, monkeypatch, value, posts):
    """Through the real config and a real flush: the YAML checks above pin a spelling, this pins its effect.

    The variable is the fallback when no config file sets the key (a runner has none), and it is marked for
    removal in 2.0. If that removal lands, this test is what says CI starts posting again.
    """
    from jcodemunch_mcp import config
    from jcodemunch_mcp.storage import token_tracker

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    if value is None:
        monkeypatch.delenv(VAR, raising=False)
    else:
        monkeypatch.setenv(VAR, value)
    config.load_config(str(store), create_missing=False)
    posted = []
    monkeypatch.setattr(token_tracker, "_share_savings", lambda *a: posted.append(a))
    monkeypatch.setattr(token_tracker, "_state", token_tracker._State())
    for _ in range(7):
        token_tracker.record_savings(1000, base_path=str(store), tool_name="search_symbols")
    token_tracker._state.flush()
    assert bool(posted) is posts, posted


def test_benchmark_entry_point_switches_it_off_for_a_hand_run():
    """REPRODUCING.md tells a reader to run this script directly, outside the harness and CI."""
    import ast

    tree = ast.parse((ROOT / "benchmarks" / "harness" / "run_benchmark.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    first = main.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), "the first statement of main()"
    assert ast.unparse(first) == "os.environ.setdefault('" + VAR + "', '0')"
    # And not at import time: a test that imports the module must not inherit it.
    module_level = [ast.unparse(n) for n in tree.body if isinstance(n, ast.Expr)]
    assert not [s for s in module_level if VAR in s]


def test_importing_the_benchmark_module_leaves_the_environment_alone(monkeypatch):
    import importlib.util

    monkeypatch.delenv(VAR, raising=False)
    path = ROOT / "benchmarks" / "harness" / "run_benchmark.py"
    spec = importlib.util.spec_from_file_location("_rb_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert VAR not in os.environ


def test_the_suite_still_sees_the_shipped_default():
    """The variable is the fallback when no config file sets the key, so the session removes it
    (conftest `_shipped_sharing_default`)."""
    from jcodemunch_mcp import config

    assert VAR not in os.environ
    assert config.DEFAULTS["share_savings"] is True
