"""A number in `benchmarks/schema_baseline.json` must never be copied elsewhere.

⚠⚠ **The failure this closes was green for seven months and then fired on the one
event that proved nothing was wrong.** `test_v1_108_183.py` read `core_compact`
out of the baseline file and asserted it equalled `3996` — a copy of itself
written into the test. The baseline was captured in 2026-07 and the tool surface
drifted underneath it release after release, with the assertion passing
throughout, because both sides of it were the same frozen artifact. It failed for
the first time on 2026-08-14 when the capture was re-run. **A guardrail that can
only fire when its own baseline is regenerated is measuring the wrong object.**

The budget itself is guarded in `tests/test_schema_budget.py`, against a live
`_build_tools_list()`. This file guards the transcription, which is the same
shape as maintenance practice #4 (never hand-type a benchmark number) and the
same shape as `test_counter_saving_is_read_not_typed` one file over — that one
was written after `run_route_recall.py` asserted `~98%` for two months while the
measured figure was 95.9%.

⚠ Scanned as bare integer literals in `tests/` and `benchmarks/`, prose included.
A stale number in a docstring is the version that survives longest, because
nothing executes it: two of the five sites this was written for were comments
claiming `core_compact sits at 3996`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "benchmarks" / "schema_baseline.json"

# Below this, a baseline value collides with ordinary integers (loop bounds,
# byte sizes, years) often enough that the guard would cost more than it saves.
# The six profile arms are all four-digit or larger; the counter arms are not,
# and are deliberately out of scope rather than guarded badly.
_MIN_GUARDED_VALUE = 1000

_SCANNED_DIRS = ("tests", "benchmarks")
_SCANNED_SUFFIXES = (".py", ".md")

# This file necessarily names the historical values in its own docstring.
_EXEMPT = {Path(__file__).name}


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for directory in _SCANNED_DIRS:
        root = REPO_ROOT / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if (
                path.suffix in _SCANNED_SUFFIXES
                and path.name not in _EXEMPT
                and "__pycache__" not in path.parts
            ):
                files.append(path)
    return files


@pytest.mark.skipif(not BASELINE.is_file(), reason="benchmarks/schema_baseline.json missing")
def test_no_baseline_value_is_transcribed_into_the_tree():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    guarded = {
        value: key
        for key, value in baseline.items()
        if isinstance(value, int) and value >= _MIN_GUARDED_VALUE
    }
    assert guarded, "baseline carried no value large enough to guard — check the file"

    pattern = re.compile(r"(?<![\d.])(" + "|".join(str(v) for v in guarded) + r")(?![\d.])")

    offenders: list[str] = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            value = int(match.group(1))
            offenders.append(
                f"{path.relative_to(REPO_ROOT).as_posix()}:{line_no} "
                f"copies {guarded[value]}={value}"
            )

    assert not offenders, (
        "A benchmarks/schema_baseline.json value is transcribed into the tree. "
        "Read it from the file, or describe the property without the number — a "
        "copy goes stale silently and asserts nothing about the live surface. "
        f"Sites: {offenders}"
    )


@pytest.mark.skipif(not BASELINE.is_file(), reason="benchmarks/schema_baseline.json missing")
def test_the_guard_would_fire_on_a_transcription():
    """Non-vacuity. A guard that cannot fire reads as coverage and is worse than none.

    Proves the matcher finds a real baseline value in text, and that its
    boundaries do not fire on a longer number that merely contains one.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    value = max(v for v in baseline.values() if isinstance(v, int))
    pattern = re.compile(r"(?<![\d.])(" + str(value) + r")(?![\d.])")

    assert pattern.search(f"assert baseline['full_full'] == {value}")
    assert pattern.search(f"# the full surface sits at {value} tokens today")
    assert not pattern.search(f"9{value}9")
    assert not pattern.search(f"1.{value}")
