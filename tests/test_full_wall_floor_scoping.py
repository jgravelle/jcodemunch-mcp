"""Which full-tier wall Floor a runner is judged by (L-232).

The ubuntu Python 3.10 CI leg has its own Floor, `suite.full_seconds_ci_ubuntu_py310`,
because its median sat just under the shared `suite.full_seconds` and tripped it with
every test passing. Only that leg moves: every other ubuntu leg and every non-CI run
keeps `suite.full_seconds`, and windows keeps its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness.__main__ as H

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "ci,platform,version,want",
    [
        ("true", "linux", (3, 10, 20), "suite.full_seconds_ci_ubuntu_py310"),
        ("true", "linux", (3, 11, 9), "suite.full_seconds"),
        ("true", "linux", (3, 13, 1), "suite.full_seconds"),
        ("true", "win32", (3, 10, 20), "suite.full_seconds_ci_windows"),
        ("true", "win32", (3, 13, 1), "suite.full_seconds_ci_windows"),
        (None, "linux", (3, 10, 20), "suite.full_seconds"),  # a local 3.10 run keeps the shared Floor
        (None, "win32", (3, 10, 20), "suite.full_seconds"),
        ("true", "darwin", (3, 10, 20), "suite.full_seconds"),
    ],
)
def test_each_runner_gets_its_floor(monkeypatch, ci, platform, version, want):
    if ci is None:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", ci)
    monkeypatch.setattr(H.sys, "platform", platform)
    monkeypatch.setattr(H.sys, "version_info", version)
    assert H._full_wall_floor_id() == want


def _thresholds() -> dict:
    data = json.loads((ROOT / "harness" / "thresholds.json").read_text(encoding="utf-8"))
    rows = data["thresholds"] if isinstance(data, dict) else data
    return {r["id"]: r for r in rows}


def test_the_scoped_floor_is_a_recorded_loosening_of_the_shared_one():
    t = _thresholds()
    scoped, shared = t["suite.full_seconds_ci_ubuntu_py310"], t["suite.full_seconds"]
    assert scoped["comparator"] == shared["comparator"] == "<="
    assert scoped["floor"] > shared["floor"]
    assert scoped["loosened"]["by"] and scoped["loosened"]["reason"]
    assert scoped["history"][0]["floor"] == shared["floor"]


def test_every_floor_the_selector_can_return_exists():
    t = _thresholds()
    for tid in ("suite.full_seconds", "suite.full_seconds_ci_windows", "suite.full_seconds_ci_ubuntu_py310"):
        assert tid in t, tid
        assert tid in H.DELEGATED, tid
