"""The ubuntu sysmon wall Floor admits every sysmon leg measured green.

`main: harness full (ubuntu, 3.12)` failed `suite.full_seconds` on three of
its last eight runs with every test passing: 383.71 s (b05a549f), 362.74 s
(4873e318) and 363.02 s (832374f0), against the 360 s Floor set on
2026-09-03 for this box's full tier (188.43 s). A sysmon ubuntu leg on CI was
still judged by that local Floor. The same job measured 352.69 s and 240.0 s
on two commits one test apart (acc10f67, 52a48d7d), so the failures report
runner speed, not a regression.

A sysmon ubuntu leg on CI now has its own Floor, set by the L-232 method
(max x 1.2, rounded up to 10 s), as the C-tracer and windows legs have. These
tests pin the property that method exists for, and the ordering the job
timeout test relies on.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The slowest sysmon ubuntu leg measured green, in the harness's observed
# unit: main.yml run 44840843004 (2026-11-21 UTC), ubuntu 3.12, 21,459 passed.
SLOWEST_GREEN_SYSMON_LEG_2026_11_21 = 383.71

SYSMON = "suite.full_seconds_ci_ubuntu_sysmon"
CTRACE = "suite.full_seconds_ci_ubuntu_ctrace"


def _floors() -> dict:
    data = json.loads(
        (ROOT / "harness" / "thresholds.json").read_text(encoding="utf-8")
    )
    rows = data["thresholds"] if isinstance(data, dict) else data
    return {r["id"]: r for r in rows}


def test_the_sysmon_wall_floor_admits_the_slowest_green_leg():
    floor = _floors()[SYSMON]["floor"]
    assert floor >= SLOWEST_GREEN_SYSMON_LEG_2026_11_21, (
        f"ubuntu sysmon wall Floor {floor} s fails a leg measured at "
        f"{SLOWEST_GREEN_SYSMON_LEG_2026_11_21} s with every test passing"
    )


def test_the_sysmon_floor_stays_under_the_ctrace_floor():
    # test_every_full_tier_job_has_room_for_its_floors sizes every ubuntu job
    # by the C-tracer Floor as the larger of the two; that holds only while
    # the sysmon Floor is not above it.
    t = _floors()
    assert t[SYSMON]["floor"] <= t[CTRACE]["floor"]


def test_the_local_floor_is_unchanged_and_tighter():
    # Every non-CI run, the measuring box included, keeps the shared Floor.
    t = _floors()
    assert t["suite.full_seconds"]["floor"] < t[SYSMON]["floor"]
    assert t[SYSMON]["history"][0]["floor"] == t["suite.full_seconds"]["floor"]
    assert t[SYSMON]["loosened"]["by"] and t[SYSMON]["loosened"]["reason"]
