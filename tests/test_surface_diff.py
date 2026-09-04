"""`scripts/surface_diff.py`: the name verdict and the description diff on synthetic inputs.

The script's public halves are pure functions over lists and dicts:
`verdict()` (names, changed files, CHANGELOG diff -> ok + lines) and, since
`--descriptions` (docs/workflows/FINDINGS.md W-1), `description_diff()` and
`report()`. Nothing here spawns a subprocess, adds a git worktree or imports
the server; every input is built in the test.

Each behaviour has a red arm: the assertion that the verdict FAILS, that a
changed description IS listed, that a name change still exits 1 -- because a
pass-only test of a diff tool cannot tell a working diff from an empty one
(Standing lesson 08-27, a set cannot count).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "surface_diff.py"


@pytest.fixture(scope="module")
def sd():
    spec = importlib.util.spec_from_file_location("surface_diff_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ALL_DOCS = ["README.md", "CLAUDE.md", "CHANGELOG.md", "src/jcodemunch_mcp/server.py"]


def _cl_diff(*names: str) -> str:
    body = "\n".join(f"+- new tool `{n}` does a thing" for n in names)
    return "--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n" + body + "\n"


# --------------------------------------------------------------------------- verdict


def test_verdict_no_change_passes_and_says_so(sd):
    ok, lines = sd.verdict(["a", "b"], ["b", "a"], [], "")
    assert ok is True
    assert any("no surface change" in ln for ln in lines)


def test_verdict_added_tool_with_docs_and_changelog_passes(sd):
    ok, lines = sd.verdict(["a"], ["a", "new_tool"], ALL_DOCS, _cl_diff("new_tool"))
    assert ok is True
    assert not any(ln.startswith("FAIL") for ln in lines)
    assert any(ln.startswith("PASS") for ln in lines)


def test_verdict_added_tool_without_readme_fails(sd):
    # red arm: the documentation rule refuses
    changed = [d for d in ALL_DOCS if d != "README.md"]
    ok, lines = sd.verdict(["a"], ["a", "new_tool"], changed, _cl_diff("new_tool"))
    assert ok is False
    assert any("README.md" in ln and ln.startswith("FAIL") for ln in lines)


def test_verdict_removed_tool_not_named_in_changelog_fails(sd):
    # red arm: every doc changed, but the CHANGELOG's added lines never name the tool
    ok, lines = sd.verdict(
        ["a", "old_tool"], ["a"], ALL_DOCS, _cl_diff("something_else")
    )
    assert ok is False
    assert any("`old_tool`" in ln and ln.startswith("FAIL") for ln in lines)


def test_verdict_reads_only_added_changelog_lines(sd):
    # a name that appears on a REMOVED line is not documentation of the addition
    diff = (
        "--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n-- new_tool was here\n+- unrelated\n"
    )
    ok, _ = sd.verdict(["a"], ["a", "new_tool"], ALL_DOCS, diff)
    assert ok is False


# ------------------------------------------------------------------ description diff


def test_description_diff_identical_is_empty(sd):
    base = {"a": "does a", "b": "does b"}
    assert sd.description_diff(base, dict(base)) == []


def test_description_diff_lists_each_changed_tool_sorted(sd):
    # red arm: a reworded description IS reported, one line per tool, sorted
    base = {"zeta": "old z", "alpha": "old a", "mid": "same"}
    head = {"zeta": "new z", "alpha": "new a", "mid": "same"}
    assert sd.description_diff(base, head) == [
        "description changed: alpha",
        "description changed: zeta",
    ]


def test_description_diff_ignores_one_sided_tools(sd):
    # a tool on one side only is a NAME change; verdict() owns it
    base = {"a": "does a", "gone": "x"}
    head = {"a": "does a", "added": "y"}
    assert sd.description_diff(base, head) == []


def test_description_diff_whitespace_change_counts(sd):
    # a byte-pinned surface (test_counter_surface_stability) moves on a space
    assert sd.description_diff({"a": "does a"}, {"a": "does  a"}) == [
        "description changed: a"
    ]


# ------------------------------------------------------------------------- report


def test_report_description_only_change_exits_zero_and_is_listed(sd):
    rc, lines, summary = sd.report(
        base=["a", "b"],
        head=["a", "b"],
        changed=[],
        cl_diff="",
        base_desc={"a": "old", "b": "same"},
        head_desc={"a": "new", "b": "same"},
        descriptions=True,
    )
    assert rc == 0
    assert "description changed: a" in lines
    assert "description changed: b" not in lines
    assert "## done: tool descriptions" in summary
    assert "a" in summary.split("## done: tool descriptions", 1)[1]


def test_report_without_flag_prints_no_description_lines(sd):
    # the existing output is byte-for-byte unchanged when the flag is absent
    rc, lines, summary = sd.report(
        base=["a"],
        head=["a"],
        changed=[],
        cl_diff="",
        base_desc={"a": "old"},
        head_desc={"a": "new"},
        descriptions=False,
    )
    assert rc == 0
    assert not any(ln.startswith("description changed") for ln in lines)
    assert "tool descriptions" not in summary
    assert summary.startswith("## done: tool surface documented: PASS")


def test_report_name_change_still_fails_with_flag(sd):
    # red arm: the flag never softens the name rule
    rc, lines, summary = sd.report(
        base=["a"],
        head=["a", "new_tool"],
        changed=[],
        cl_diff="",
        base_desc={"a": "x"},
        head_desc={"a": "x", "new_tool": "y"},
        descriptions=True,
    )
    assert rc == 1
    assert any(ln.startswith("FAIL") for ln in lines)
    assert "## done: tool surface documented: FAIL" in summary
    assert "## done: tool descriptions: none" in summary


def test_report_unchanged_descriptions_says_none(sd):
    rc, lines, summary = sd.report(
        base=["a"],
        head=["a"],
        changed=[],
        cl_diff="",
        base_desc={"a": "x"},
        head_desc={"a": "x"},
        descriptions=True,
    )
    assert rc == 0
    assert "## done: tool descriptions: none" in summary


def test_main_exposes_descriptions_flag(sd):
    parser = sd.build_parser()
    assert parser.parse_args(["--descriptions"]).descriptions is True
    assert parser.parse_args([]).descriptions is False
