"""The windows full-tier wall Floor admits every windows leg measured green.

`suite.full_seconds_ci_windows` failed #1402 and #1408 with every test
passing: their windows legs ran 1348.81 and 1414.92 s against a 1313 s Floor
set at a suite 18% smaller. A wall Floor that fails a leg whose tests all
passed is reporting runner speed, not a regression.

The sample is the slowest windows leg of each of the 16 pr-gate runs read on
2026-11-11 (2026-11-10 18:26Z to 2026-11-11 17:18Z), every one with all tests
passing. The Floor is set as the maximum x 1.2 (the L-232 method); this test
asserts the property that method exists for, so a later cut below the
measured legs fails here before it fails a contributor's PR. The job timeout
must also leave room for setup, or a slow leg is cancelled with no verdict
(F-38).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The slowest of the 64 windows legs in the sample; every test passed in it.
SLOWEST_GREEN_WINDOWS_LEGS_2026_11_11 = 1414.92
SETUP_SECONDS = 180  # checkout, uv install and sync, measured as about 3 min (F-38)


def _thresholds() -> dict:
    data = json.loads((ROOT / "harness" / "thresholds.json").read_text(encoding="utf-8"))
    rows = data["thresholds"] if isinstance(data, dict) else data
    return {r["id"]: r for r in rows}


def test_the_windows_wall_floor_admits_the_slowest_green_leg():
    floor = _thresholds()["suite.full_seconds_ci_windows"]["floor"]
    assert floor >= SLOWEST_GREEN_WINDOWS_LEGS_2026_11_11, (
        f"windows wall Floor {floor} s fails a leg measured at "
        f"{SLOWEST_GREEN_WINDOWS_LEGS_2026_11_11} s with every test passing"
    )


def test_the_job_timeout_leaves_room_for_the_floor_and_setup():
    t = _thresholds()
    floor = t["suite.full_seconds_ci_windows"]["floor"]
    timeout_s = t["ci.test_job_timeout_minutes"]["floor"] * 60
    assert timeout_s >= floor + SETUP_SECONDS, (
        f"a {timeout_s} s job timeout cancels a leg before the {floor} s Floor "
        "plus setup can give a verdict"
    )
