"""The ubuntu C-tracer wall Floor admits every C-tracer leg measured green.

`suite.full_seconds_ci_ubuntu_ctrace` (450 s) failed nightly run 43152986559
(#1417) at 459.54 s and PR #1418 at 450.86 s, each with every test passing.
It was set on 2026-10-21 at a 345.4 s median; the C-tracer legs of the last 30
green pr-gate runs now have a 374.275 s median, in the harness's own observed
unit. A wall Floor that fails a leg whose tests all passed is reporting runner
speed, not a regression.

The Floor is set as the maximum x 1.2 (the L-232 method). This test asserts
the property that method exists for, so a later cut below the measured legs
fails here before it fails a contributor's PR.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The slowest C-tracer leg measured green, in the harness's observed unit:
# nightly run 43152986559 (2026-11-12 UTC), ubuntu 3.10.
SLOWEST_GREEN_CTRACE_LEG_2026_11_12 = 459.54


def test_the_ctrace_wall_floor_admits_the_slowest_green_leg():
    data = json.loads(
        (ROOT / "harness" / "thresholds.json").read_text(encoding="utf-8")
    )
    rows = data["thresholds"] if isinstance(data, dict) else data
    floor = next(
        r["floor"] for r in rows if r["id"] == "suite.full_seconds_ci_ubuntu_ctrace"
    )
    assert floor >= SLOWEST_GREEN_CTRACE_LEG_2026_11_12, (
        f"ubuntu C-tracer wall Floor {floor} s fails a leg measured at "
        f"{SLOWEST_GREEN_CTRACE_LEG_2026_11_12} s with every test passing"
    )
