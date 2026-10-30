"""Which full-tier wall Floor a runner is judged by (L-232, extended 2026-10-29).

On a GitHub ubuntu runner, the full tier's speed is set mostly by which tracer
coverage uses. Where `sys.monitoring` is usable coverage runs its `sysmon` core;
where it is not (Python 3.10 and 3.11) coverage falls back to the C tracer,
which costs far more wall time. Every C-tracer ubuntu leg is judged by
`suite.full_seconds_ci_ubuntu_ctrace`; a sysmon leg and every non-CI run keep
`suite.full_seconds`, and windows keeps its own.

The selector asks coverage which core it will pick. It does not test the Python
version, because that would be a second copy of coverage's decision, wrong the
first time coverage changes it. So these tests switch coverage's own
`sys.monitoring` flag, not `sys.version_info`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from coverage import env as cov_env

import harness.__main__ as H

ROOT = Path(__file__).resolve().parents[1]
CTRACE = "suite.full_seconds_ci_ubuntu_ctrace"


@pytest.fixture(autouse=True)
def _no_inherited_core(monkeypatch):
    monkeypatch.delenv("COVERAGE_CORE", raising=False)


def _set(monkeypatch, *, ci, platform, sysmon_usable):
    if ci is None:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", ci)
    monkeypatch.setattr(H.sys, "platform", platform)
    monkeypatch.setattr(cov_env.PYBEHAVIOR, "pep669", sysmon_usable)


@pytest.mark.parametrize(
    "ci,platform,sysmon_usable,want",
    [
        # 3.10 and 3.11: no usable sys.monitoring, so coverage uses the C tracer.
        ("true", "linux", False, CTRACE),
        # 3.12 and later: coverage uses sysmon.
        ("true", "linux", True, "suite.full_seconds"),
        ("true", "win32", False, "suite.full_seconds_ci_windows"),
        ("true", "win32", True, "suite.full_seconds_ci_windows"),
        # A local run keeps the shared Floor whatever its tracer.
        (None, "linux", False, "suite.full_seconds"),
        (None, "win32", False, "suite.full_seconds"),
        ("true", "darwin", False, "suite.full_seconds"),
    ],
)
def test_each_runner_gets_its_floor(monkeypatch, ci, platform, sysmon_usable, want):
    _set(monkeypatch, ci=ci, platform=platform, sysmon_usable=sysmon_usable)
    assert H._full_wall_floor_id() == want


def test_a_forced_c_tracer_on_a_sysmon_interpreter_is_judged_as_a_c_tracer_leg(
    monkeypatch,
):
    # `COVERAGE_CORE=ctrace` survives into the full tier (test_full_tier_coverage_core),
    # so the leg really runs the C tracer and gets that leg's Floor.
    _set(monkeypatch, ci="true", platform="linux", sysmon_usable=True)
    monkeypatch.setenv("COVERAGE_CORE", "ctrace")
    assert H._full_wall_floor_id() == CTRACE


def test_an_unknown_core_keeps_the_shared_floor(monkeypatch):
    # Could not establish the tracer: the tighter Floor applies, never the looser one.
    _set(monkeypatch, ci="true", platform="linux", sysmon_usable=False)
    monkeypatch.setattr(H, "_coverage_core", lambda: None)
    assert H._full_wall_floor_id() == "suite.full_seconds"


def test_a_core_coverage_cannot_build_reads_as_unknown(monkeypatch, capsys):
    # The producer half: when coverage's own decision raises, the selector says
    # so on stderr and answers None, which keeps the tighter Floor.
    import coverage.core

    def boom(*_a, **_k):
        raise RuntimeError("planted")

    monkeypatch.setattr(coverage.core, "Core", boom)
    _set(monkeypatch, ci="true", platform="linux", sysmon_usable=False)
    assert H._coverage_core() is None
    assert "coverage core: unknown (RuntimeError: planted)" in capsys.readouterr().err
    assert H._full_wall_floor_id() == "suite.full_seconds"


def test_nothing_changes_the_core_behind_the_selectors_back():
    # `_coverage_core` prices coverage's default config with no branch, context
    # or concurrency settings, which is what `tier_full` runs today. A coverage
    # config file or a `--cov-*` flag that changes the core (`--cov-branch`
    # stops sysmon on 3.12-3.13) would make the selector and pytest disagree,
    # and the shared Floor would fail those legs with no pointer here.
    import inspect

    for name in (".coveragerc", "setup.cfg", "tox.ini"):
        assert not (ROOT / name).exists(), (
            f"{name} may configure coverage; teach _coverage_core to read it"
        )
    assert "[tool.coverage" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    src = inspect.getsource(H.tier_full)
    for flag in ("--cov-branch", "--cov-context", "--cov-config"):
        assert flag not in src, (
            f"tier_full passes {flag}; _coverage_core must account for it"
        )


def test_the_selector_and_the_tier_ask_for_the_same_core(monkeypatch):
    # The Floor is chosen for the tracer the full tier's pytest will run. If the
    # two read the core from different places, they can disagree.
    monkeypatch.setenv("COVERAGE_CORE", "pytrace")
    assert H._coverage_core_env() == {"COVERAGE_CORE": "pytrace"}
    monkeypatch.delenv("COVERAGE_CORE")
    assert H._coverage_core_env() == {"COVERAGE_CORE": "sysmon"}


def _thresholds() -> dict:
    data = json.loads(
        (ROOT / "harness" / "thresholds.json").read_text(encoding="utf-8")
    )
    rows = data["thresholds"] if isinstance(data, dict) else data
    return {r["id"]: r for r in rows}


def test_the_scoped_floor_is_a_recorded_loosening_of_the_shared_one():
    t = _thresholds()
    scoped, shared = t[CTRACE], t["suite.full_seconds"]
    assert scoped["comparator"] == shared["comparator"] == "<="
    assert scoped["floor"] > shared["floor"]
    assert scoped["loosened"]["by"] and scoped["loosened"]["reason"]
    assert scoped["history"][0]["floor"] == shared["floor"]


def test_every_floor_the_selector_can_return_exists():
    t = _thresholds()
    for tid in ("suite.full_seconds", "suite.full_seconds_ci_windows", CTRACE):
        assert tid in t, tid
        assert tid in H.DELEGATED, tid
    assert "suite.full_seconds_ci_ubuntu_py310" not in t
