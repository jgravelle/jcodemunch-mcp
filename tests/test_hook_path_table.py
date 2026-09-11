"""The three hook path lists derive from ONE table, with a purpose per row.

`_common.TIER_PATHS` (what the full-tier stamp covers), `pre_commit.CODE_ROOTS`
(what needs a fast tier before a commit) and `dod_checklist.CODE_ROOTS` (what
needs a red/green pair) grew separately with three memberships; a hooks-only
commit skipped the fast tier while the checklist called the same edit a code
change, and a hook edit after the full tier left the stamp valid
(docs/workflows/FINDINGS.md W-43). Each list still answers its own question;
the table is where a path is admitted to each, in one place, and this test
is where a path present in one list is present in or deliberately excluded
from the others.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


def _load(name: str):
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location(f"_wf_{name}", HOOKS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


@pytest.fixture(scope="module")
def common():
    return _load("_common")


@pytest.fixture(scope="module")
def pre_commit():
    return _load("pre_commit")


@pytest.fixture(scope="module")
def dod():
    return _load("dod_checklist")


def test_each_list_is_the_table_projected_on_its_question(common, pre_commit, dod):
    assert tuple(common.TIER_PATHS) == common.paths_for("stamp")
    assert tuple(pre_commit.CODE_ROOTS) == common.paths_for("fast")
    assert tuple(dod.CODE_ROOTS) == common.paths_for("redgreen")


def test_every_table_row_answers_at_least_one_question(common):
    for path, questions in common.PATH_TABLE.items():
        assert questions and questions <= common.QUESTIONS, (path, questions)


def test_the_hooks_move_the_stamp_and_need_red_green_but_do_not_trigger_the_fast_tier(common):
    # The fast tier's file list (harness/tiers.json) carries no hook test, so a
    # fast-tier run on a hooks-only commit would judge nothing about the change;
    # the full tier runs tests/test_workflow_hooks.py over them, so the stamp
    # must move with a hook edit, and a hook change needs its red/green pair.
    q = common.PATH_TABLE[".claude/hooks/"]
    assert {"stamp", "redgreen"} <= q and "fast" not in q


def test_the_fast_trigger_is_narrower_than_the_stamp_under_benchmarks(common):
    # The fast tier reads benchmarks/harness/ and the frozen Floor artifacts
    # (FLOOR_INPUTS in pre_commit), not the whole tree under benchmarks/; the
    # stamp and the red/green rule cover the whole tree.
    assert "fast" in common.PATH_TABLE["benchmarks/harness/"]
    assert "fast" not in common.PATH_TABLE["benchmarks/"]
    assert {"stamp", "redgreen"} <= common.PATH_TABLE["benchmarks/"]


def test_the_bench_trigger_is_the_tables_bench_column(common, dod):
    # DoD item 10: the bench tier is required for benchmarks/, harness/ and the
    # dispatcher; the checklist used to hold that as a fourth list of its own.
    assert set(common.paths_for("bench")) == {"benchmarks/", "harness/", "src/jcodemunch_mcp/server.py"}
    src = (HOOKS / "dod_checklist.py").read_text(encoding="utf-8")
    assert 'touched(*paths_for("bench"))' in src


def test_the_packaging_files_move_the_stamp_only(common):
    for p in ("pyproject.toml", "uv.lock"):
        assert common.PATH_TABLE[p] == {"stamp"}, p


def test_no_list_in_the_three_modules_is_a_second_literal(common):
    """A path tuple typed beside the table is the drift the table exists to stop."""
    import re

    for name in ("pre_commit", "dod_checklist"):
        src = (HOOKS / f"{name}.py").read_text(encoding="utf-8")
        m = re.search(r"^CODE_ROOTS\s*=\s*(.+)$", src, re.M)
        assert m and "paths_for(" in m.group(1), (name, m.group(0) if m else None)
    src = (HOOKS / "_common.py").read_text(encoding="utf-8")
    m = re.search(r"^TIER_PATHS\s*=\s*(.+)$", src, re.M)
    assert m and "paths_for(" in m.group(1), m.group(0) if m else None
