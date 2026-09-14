"""DoD row 1 grades only a red/green pair stamped for THIS change (#671).

The stamp closes an INHERITED pair; it cannot prove pytest wrote the pair on
the pre-change tree (docs/workflows/FINDINGS.md W-45 residual).

`dod_checklist.py` resolved `evidence/red.txt` and `evidence/green.txt` by path
alone, so a later change on the same box inherited them: #669 (one docstring,
two docs files) graded row 1 `met` from #666's artifacts written that morning.
The neighbouring stamp already had the pattern — `pre_pr.py` refuses a full-tier
stamp for a different tree — and the row asserting the strongest property in
the Standard lacked it.

`dod_checklist.py --stamp red|green` writes `<kind>.stamp.json` beside the run:
the branch, the tier-path tree id (`_common.tree_id`, the full-tier stamp's
identity) and a hash of the output. Row 1 grades a pair only when both stamps
name this branch, still hash the files on disk, the green run's tree is the
tree now, and the red run's tree differs from green's (a red run on the fixed
tree did not fail on the pre-change one). A pair that is not bound is `unmet`
when a code root changed, and ignored as `n.a.` when none did, with the reason
named either way.

What each test pins (for docs/harness/ARCHAEOLOGY.md): the #669 shape (a pair
stamped on another branch) is not `met`; each binding clause fails alone; an
unstamped pair is not `met`; a stale pair on a diff with no code-root change is
`n.a.`, not `met` and not `unmet`; a bound pair is still graded by the
unchanged `row1_verdict`; `--stamp` writes a stamp the grader accepts.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"

RED = "collected 3 items\n3 failed in 1.0s\nEXIT=1\n"
GREEN = "3 passed in 1.0s\nEXIT=0\n"
BRANCH = "fix/671-x"
RED_TREE = "a" * 40
GREEN_TREE = "b" * 40


@pytest.fixture(scope="module")
def dod():
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location("_wf_dod_bind", HOOKS / "dod_checklist.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


def _bound(dod):
    return (
        dod.make_stamp("red", RED, BRANCH, RED_TREE),
        dod.make_stamp("green", GREEN, BRANCH, GREEN_TREE),
    )


def _row1(dod, changed, red_stamp, green_stamp, *, red=RED, green=GREEN, branch=BRANCH, tree=GREEN_TREE):
    return dod.row1(changed, red, green, red_stamp, green_stamp, branch=branch, tree=tree)


def test_a_bound_pair_is_graded_met(dod):
    rs, gs = _bound(dod)
    verdict, ev = _row1(dod, ["src/jcodemunch_mcp/x.py", "tests/test_x.py"], rs, gs)
    assert verdict == "met", ev


def test_the_669_shape_a_pair_from_another_branch_is_not_met(dod):
    rs = dod.make_stamp("red", RED, "fix/666-other", RED_TREE)
    gs = dod.make_stamp("green", GREEN, "fix/666-other", "c" * 40)
    verdict, ev = _row1(dod, ["src/jcodemunch_mcp/tools/x.py", "docs/a.md"], rs, gs)
    assert verdict == "unmet", ev
    assert "fix/666-other" in ev, ev


@pytest.mark.parametrize(
    "clause",
    [
        "green_tree_moved",
        "red_same_tree_as_green",
        "red_rewritten",
        "green_rewritten",
        "red_other_branch",
        "green_other_branch",
        "red_tree_unreadable",
        "tree_now_unreadable",
    ],
)
def test_each_binding_clause_fails_alone(dod, clause):
    rs, gs = _bound(dod)
    kw = {}
    if clause == "green_tree_moved":
        kw["tree"] = "d" * 40
    elif clause == "red_same_tree_as_green":
        rs = dod.make_stamp("red", RED, BRANCH, GREEN_TREE)
    elif clause == "red_rewritten":
        kw["red"] = RED.replace("3 failed", "2 failed")
    elif clause == "green_rewritten":
        kw["green"] = GREEN.replace("3 passed", "4 passed")
    elif clause == "red_other_branch":
        rs = dod.make_stamp("red", RED, "fix/elsewhere", RED_TREE)
    elif clause == "green_other_branch":
        gs = dod.make_stamp("green", GREEN, "fix/elsewhere", GREEN_TREE)
    elif clause == "red_tree_unreadable":
        # tree_id's fail-closed id is random, so it always "differs" from green's.
        rs = dod.make_stamp("red", RED, BRANCH, dod.UNREADABLE_PREFIX + "0123abcd")
    elif clause == "tree_now_unreadable":
        unreadable = dod.UNREADABLE_PREFIX + "0123abcd"
        gs = dod.make_stamp("green", GREEN, BRANCH, unreadable)
        kw["tree"] = unreadable
    verdict, ev = _row1(dod, ["src/jcodemunch_mcp/x.py"], rs, gs, **kw)
    assert verdict == "unmet", (clause, ev)


def test_an_unstamped_pair_is_not_met(dod):
    for rs, gs in ((None, None), (_bound(dod)[0], None), (None, _bound(dod)[1])):
        verdict, ev = _row1(dod, ["src/jcodemunch_mcp/x.py"], rs, gs)
        assert verdict == "unmet", ev
        assert "--stamp" in ev, ev


def test_a_stale_pair_on_a_diff_with_no_code_root_change_is_na(dod):
    rs = dod.make_stamp("red", RED, "fix/666-other", RED_TREE)
    gs = dod.make_stamp("green", GREEN, "fix/666-other", "c" * 40)
    verdict, ev = _row1(dod, ["docs/workflows/FINDINGS.md", "CHANGELOG.md"], rs, gs)
    assert verdict == "n.a.", ev
    assert "not this change" in ev, ev


def test_a_bound_pair_is_still_graded_by_its_content(dod):
    passing_red = "3 passed\nEXIT=0\n"
    rs = dod.make_stamp("red", passing_red, BRANCH, RED_TREE)
    gs = dod.make_stamp("green", GREEN, BRANCH, GREEN_TREE)
    verdict, _ = _row1(dod, ["src/jcodemunch_mcp/x.py"], rs, gs, red=passing_red)
    assert verdict == "unmet"


def test_no_pair_keeps_the_existing_verdicts(dod):
    assert _row1(dod, ["docs/a.md"], None, None, red=None, green=None)[0] == "n.a."
    assert _row1(dod, ["src/jcodemunch_mcp/x.py"], None, None, red=None, green=None)[0] == "unmet"


def test_stamp_writes_what_the_grader_accepts(dod, tmp_path, monkeypatch):
    monkeypatch.setattr(dod, "EVIDENCE", tmp_path)
    monkeypatch.setattr(dod, "current_branch", lambda: BRANCH)
    trees = iter([RED_TREE, GREEN_TREE])
    monkeypatch.setattr(dod, "tree_id", lambda: next(trees))
    (tmp_path / "red.txt").write_text(RED, encoding="utf-8")
    (tmp_path / "green.txt").write_text(GREEN, encoding="utf-8")
    assert dod.write_stamp("red") == 0
    assert dod.write_stamp("green") == 0
    rs = json.loads((tmp_path / "red.stamp.json").read_text(encoding="utf-8"))
    gs = json.loads((tmp_path / "green.stamp.json").read_text(encoding="utf-8"))
    verdict, ev = _row1(dod, ["src/jcodemunch_mcp/x.py"], rs, gs)
    assert verdict == "met", ev


def test_stamp_refuses_an_unreadable_tree(dod, tmp_path, monkeypatch):
    monkeypatch.setattr(dod, "EVIDENCE", tmp_path)
    monkeypatch.setattr(dod, "current_branch", lambda: BRANCH)
    monkeypatch.setattr(dod, "tree_id", lambda: dod.UNREADABLE_PREFIX + "feedface")
    (tmp_path / "red.txt").write_text(RED, encoding="utf-8")
    assert dod.write_stamp("red") != 0
    assert not (tmp_path / "red.stamp.json").exists()


def test_stamp_refuses_without_the_output(dod, tmp_path, monkeypatch):
    monkeypatch.setattr(dod, "EVIDENCE", tmp_path)
    monkeypatch.setattr(dod, "current_branch", lambda: BRANCH)
    monkeypatch.setattr(dod, "tree_id", lambda: RED_TREE)
    assert dod.write_stamp("red") != 0
    assert not (tmp_path / "red.stamp.json").exists()
