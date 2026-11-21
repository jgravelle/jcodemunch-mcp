"""A CI fast-tier job is judged by a Floor set from CI legs, not from one local run.

`suite.fast_seconds` (90 s) was set on 2026-09-03 from one 49.35 s fast tier on
the measuring box. Every CI fast-tier job was judged by it too.
Across 53 CI legs from 2026-11-08 to 2026-11-21 (14 nightly, 39 pr-gate) the
median was 69.66 s and the max 93.43 s. Two legs failed it with pytest exit 0
and 1470 passed (nightly 52a48d7d at 93.27, #1502; pr-gate cbd2862d at
93.43), and 14 more sat between 85 and 90. On 2026-11-19 alone, 17 legs read
48.76 to 93.43 s.

A CI fast-tier job now has its own Floor, set by the L-232 method (max x 1.2,
rounded up to 10 s). Every local run, the Mac stamp included, keeps 90.
"""

from __future__ import annotations

import json
from pathlib import Path

import harness.__main__ as hm

ROOT = Path(__file__).resolve().parents[1]

# The slowest CI fast-tier leg measured green, in the harness's observed unit:
# pr-gate.yml on cbd2862d, `fast: harness fast tier`, 2026-11-19.
SLOWEST_GREEN_CI_FAST_LEG_2026_11_21 = 93.43

CI = "suite.fast_seconds_ci"
LOCAL = "suite.fast_seconds"


def _floors() -> dict:
    data = json.loads(
        (ROOT / "harness" / "thresholds.json").read_text(encoding="utf-8")
    )
    return {r["id"]: r for r in data["thresholds"]}


def test_the_ci_fast_floor_admits_the_slowest_green_leg():
    floor = _floors()[CI]["floor"]
    assert floor >= SLOWEST_GREEN_CI_FAST_LEG_2026_11_21, (
        f"CI fast-tier Floor {floor} s fails a leg measured at "
        f"{SLOWEST_GREEN_CI_FAST_LEG_2026_11_21} s with every test passing"
    )


def test_the_local_fast_floor_is_unchanged_and_tighter():
    t = _floors()
    assert t[LOCAL]["floor"] < t[CI]["floor"]
    assert t[CI]["history"][0]["floor"] == t[LOCAL]["floor"]
    assert t[CI]["loosened"]["by"] and t[CI]["loosened"]["reason"]


def test_a_ci_run_is_judged_by_the_ci_floor(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert hm._fast_wall_floor_id() == CI


def test_a_local_run_is_judged_by_the_local_floor(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert hm._fast_wall_floor_id() == LOCAL


def test_the_ci_floor_is_named_in_the_delegated_map():
    assert CI in hm.DELEGATED
