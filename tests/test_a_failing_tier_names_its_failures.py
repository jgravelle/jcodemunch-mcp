"""F-35: a failing tier reported a COUNT and the artifact could not say which tests.

`_Tee.summary_markdown` kept only indented lines containing `passed` or
`failed`; pytest's short-summary row is `FAILED tests/x.py::y` at column 0 and
never qualified, so `evidence/full.md` recorded `3 failed, 11889 passed` and
nothing that could reproduce it. A count is not a diagnosis.

The tiers now print each failed/errored id in ONE recognised indented form,
capped with the remainder DISCLOSED, and `summary_markdown` carries exactly
those lines -- one derivation for the console and the artifact.
"""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

hm = importlib.import_module("harness.__main__")


def _tee_with(lines):
    tee = hm._Tee(io.StringIO())
    for ln in lines:
        tee.write(ln + "\n")
    return tee


#: A realistic tail: results, a LONG coverage table (the full tier prints it
#: BEFORE the short summary), then the short summary at column 0.
_COVERAGE = "\n".join(
    f"src/jcodemunch_mcp/mod_{i}.py           {i * 7}     {i}    92%" for i in range(400)
)
_TWO_FAILURES = f"""============================= test session starts =============================
collected 11972 items
........................F.......................F.........................
{_COVERAGE}
TOTAL                                      190234  34210    82%

=========================== short test summary info ===========================
FAILED tests/test_file_summary_member_kinds.py::test_two_classes[c.cs-csharp]
FAILED tests/test_file_summary_member_kinds.py::test_two_classes[s.swift-swift]
2 failed, 11946 passed, 24 skipped, 99 warnings in 202.97s (0:03:22)
"""


def test_both_failed_ids_are_parsed_from_a_run_with_a_coverage_table():
    assert hm._failed_ids(_TWO_FAILURES) == [
        "tests/test_file_summary_member_kinds.py::test_two_classes[c.cs-csharp]",
        "tests/test_file_summary_member_kinds.py::test_two_classes[s.swift-swift]",
    ]


def test_the_artifact_lists_every_failed_id_the_tier_printed():
    """The console lines and the artifact block are ONE derivation: what the
    tier prints for a failure is what `summary_markdown` carries."""
    tee = _tee_with([
        "   2 failed, 11946 passed, 24 skipped in 202.97s",
        *hm._failed_id_lines(_TWO_FAILURES),
        "suite.full_seconds                       crit N1  floor <= 360          observed 202.97       PASS",
    ])
    md = tee.summary_markdown("harness full", ok=False)
    assert "tests/test_file_summary_member_kinds.py::test_two_classes[c.cs-csharp]" in md
    assert "tests/test_file_summary_member_kinds.py::test_two_classes[s.swift-swift]" in md
    assert "2 failed, 11946 passed" in md


def test_a_clean_run_carries_no_failure_block_at_all():
    """Absence, not an empty heading: a reader must not have to distinguish
    'no failures' from 'failures not recorded'."""
    clean = "11948 passed, 24 skipped in 199.04s\n"
    assert hm._failed_ids(clean) == []
    assert hm._failed_id_lines(clean) == []
    tee = _tee_with(["   11948 passed, 24 skipped in 199.04s"])
    md = tee.summary_markdown("harness full", ok=True)
    assert "FAILED" not in md
    assert "failures" not in md.lower()


def test_an_indented_line_containing_the_word_is_not_a_row():
    """The column-0 anchor, asserted directly: the one thing a looser regex
    would get wrong and no real run would reveal."""
    noise = (
        "    assert FAILED tests/x.py::y  # a string inside an assertion\n"
        "  FAILED tests/y.py::z\n"
        "E   AssertionError: FAILED tests/z.py::w\n"
    )
    assert hm._failed_ids(noise) == []


def test_a_raw_pytest_tail_cannot_forge_a_row_in_the_artifact():
    """The tier also prints the raw pytest tail, so `_Tee` sees indented
    traceback lines. One beginning `FAILED `, `ERROR ` or `... ` after its
    indent must not become a row: the collector keys on a MARKER only
    `_failed_id_lines` prints, never on the words."""
    tee = _tee_with([
        "   FAILED tests/forged.py::from_a_traceback",
        "    ... # a source line inside a traceback",
        "   ERROR tests/forged.py::also_from_a_traceback",
        "   1 failed, 10 passed in 1.0s",
    ])
    md = tee.summary_markdown("harness fast", ok=False)
    assert "forged" not in md
    assert "**Failed:**" not in md


def test_the_checks_tab_reads_the_same_deduplicated_rows():
    """`_annotate_failure` reads `_failed_rows`, so an id pytest printed twice
    reaches the Checks tab once, as it reaches the console once."""
    out = "FAILED tests/a.py::t - boom\nFAILED tests/a.py::t - boom\nFAILED tests/b.py::u\n"
    assert hm._failed_rows(out) == ["FAILED tests/a.py::t - boom", "FAILED tests/b.py::u"]


def test_more_than_the_cap_lists_the_cap_and_discloses_the_rest():
    """A silently shortened list is F-35's own defect one layer down."""
    many = "\n".join(f"FAILED tests/test_many.py::test_{i}" for i in range(hm._FAILED_ID_CAP + 7))
    ids = hm._failed_ids(many)
    assert len(ids) == hm._FAILED_ID_CAP + 7
    lines = hm._failed_id_lines(many)
    listed = [ln for ln in lines if "FAILED " in ln]
    assert len(listed) == hm._FAILED_ID_CAP
    assert listed[0] == hm._FAILED_ID_PREFIX + "FAILED tests/test_many.py::test_0"
    assert any("7 more" in ln for ln in lines), lines
    md = _tee_with(lines).summary_markdown("harness full", ok=False)
    assert "7 more" in md
    assert f"test_{hm._FAILED_ID_CAP + 6}" not in md


def test_error_rows_count_as_failures_and_are_listed():
    """The sdist guard errors at SETUP (F-32's shape); an ERROR is a test that
    did not run, which is worse than one that failed."""
    out = (
        "ERROR tests/test_sdist_exclusions.py::test_no_canary_survives_the_exclusion\n"
        "FAILED tests/test_x.py::test_y\n"
        "1 failed, 1 error, 10 passed in 3.0s\n"
    )
    assert hm._failed_ids(out) == [
        "tests/test_sdist_exclusions.py::test_no_canary_survives_the_exclusion",
        "tests/test_x.py::test_y",
    ]
    md = _tee_with(hm._failed_id_lines(out)).summary_markdown("harness fast", ok=False)
    assert "test_no_canary_survives_the_exclusion" in md


def test_a_repeated_id_is_listed_once():
    """pytest can print an id twice (a rerun plugin, a duplicated summary
    section); the list is deduplicated in order."""
    out = "FAILED tests/a.py::t\nFAILED tests/b.py::u\nFAILED tests/a.py::t\n"
    assert hm._failed_ids(out) == ["tests/a.py::t", "tests/b.py::u"]


def test_both_pytest_tiers_print_the_ids_on_a_non_zero_run():
    """A helper defined and called nowhere is indistinguishable from the defect
    it was written for (08-19). Asserted over the SOURCE of the two tier
    functions that run pytest, because the unit tests above exercise the
    derivation and not the call."""
    import inspect

    for tier in (hm.tier_fast, hm.tier_full):
        src = inspect.getsource(tier)
        assert "_failed_id_lines(out)" in src, tier.__name__
        assert "_annotate_failure(" in src, tier.__name__
