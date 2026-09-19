"""DoD item 11 grades a retired test FUNCTION, keyed `file::name`.

`dod_checklist.py` decided whether item 11 applied with
`git diff --diff-filter=D -- tests/`, which lists deleted FILES. DoD 11 and
`harness/retired.json`'s own note are both explicit that the unit is
`file::test_name`, so a PR deleting test functions from a file that survives was
graded `n.a.` -- the item was never evaluated by the machine at all. PR #748/#749
deleted exactly two and the checklist printed "no test file deleted"; review
caught it and the checklist did not.

⚠⚠ **The first fix keyed on the NAME and was fail-open.** It concatenated every
file under `tests/` and asked whether `def <name>(` appeared anywhere, so
retiring one of the many test names defined in more than one file read as a
rename, item 11 reverted to `n.a.`, and the ledger was never demanded -- the
exact grade the detector exists to stop. That is
`tests/test_key_files_split.py`'s recorded bug (basename collapsed
`runtime/redact.py` with `redact.py`; **a name is not an identity**) reproduced
inside a guard written against a neighbouring miss, and `test_a_name_defined_in_two_files`
is the row that pins it.

What each test pins, for `docs/harness/ARCHAEOLOGY.md`: a function removed from a
surviving file is detected; a name that survives IN ANOTHER FILE is still
detected there; a rename in place is not; a move to another file in the same diff
is not; a mere MENTION of the name does not count as a definition; and the repo
root is taken from the argument, never from the process CWD.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


@pytest.fixture(scope="module")
def dod():
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location(
            "_wf_dod_retired", HOOKS / "dod_checklist.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


#: A real newline, spelled so no editing pass can turn it into a literal.
NL = chr(10)


def _diff(*hunks: str) -> str:
    """A `git diff -U0` body: each hunk is a `+++ b/<path>` header plus lines."""
    return "\n".join(hunks)


def _removed(path: str, name: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +0,0 @@\n-def {name}():\n-    assert True"


def _added(path: str, name: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n+def {name}():\n+    assert True"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_function_removed_from_a_surviving_file_is_retired(dod, tmp_path):
    """The defect the detector was written for: the FILE survives, the test does not."""
    repo = _repo(tmp_path, {"tests/test_a.py": "def test_kept():\n    assert True\n"})

    assert dod.retired_test_functions(_removed("tests/test_a.py", "test_gone"), repo) == [
        "tests/test_a.py::test_gone"
    ]


def test_a_rename_in_place_is_not_a_retirement(dod, tmp_path):
    """⚠ `test_edit_guard` fires on a rename too, and a human clears it every time.

    The detector must make the same judgement, or every rename demands a ledger
    entry and `pre_pr.py` refuses the PR.
    """
    repo = _repo(tmp_path, {"tests/test_a.py": "def test_new_name():\n    assert True\n"})
    diff = _diff(
        _removed("tests/test_a.py", "test_old_name"),
        _added("tests/test_a.py", "test_new_name"),
    )

    assert dod.retired_test_functions(diff, repo) == []


def test_a_replacement_rewritten_in_the_same_file_is_still_a_retirement(dod, tmp_path):
    """⚠⚠ The case a FILE-LEVEL rename rule swallowed, and it is the dominant one.

    The first version excluded every removal in a file that gained any test, so
    it reported NOTHING on `b9dfcb19` -- the only retirement in
    `harness/retired.json` -- because the replacement was added to the same file
    beside eleven other new tests. The ledger schema makes that the normal
    shape: entry 0's `path` and `replacement` name one file. Measured in review;
    the detector missed 1 of 1 historical retirements, and this PR was caught
    only because its replacements went into a NEW file.

    ⚠ Bodies are compared instead. Both real cases were measured:
    `b9dfcb19` scores 0.571 against its nearest in-file replacement and is
    REPORTED; #753's rename (`4093364f`) scores 0.851 and is NOT. Both commits
    were re-run through the detector to confirm the verdicts.
    """
    repo = _repo(tmp_path, {"tests/test_a.py": "def test_rewritten():\n    assert compute() == 7\n"})
    diff = _diff(
        f"--- a/tests/test_a.py{chr(10)}+++ b/tests/test_a.py{chr(10)}@@ -1 +0,0 @@{chr(10)}-def test_gone():{chr(10)}-    subprocess.run(['gh', 'issue', 'comment']){chr(10)}-    assert called == []",
        f"--- a/tests/test_a.py{chr(10)}+++ b/tests/test_a.py{chr(10)}@@ -0,0 +1 @@{chr(10)}+def test_rewritten():{chr(10)}+    assert compute() == 7",
    )

    assert dod.retired_test_functions(diff, repo) == ["tests/test_a.py::test_gone"]


def test_a_rename_below_the_threshold_is_an_accepted_false_positive(dod, tmp_path):
    """⚠⚠ A KNOWN false positive, pinned so nobody "fixes" it by lowering the
    constant.

    `_RENAME_BODY_SIMILARITY` is a deliberate bias, not a margin. A sweep of the
    last 800 commits touching `tests/` scored **517** removal/addition pairs:
    only **15** clear 0.75 and **64** sit inside the 0.571-0.851 band between
    the two calibration samples. Genuine renames measured at 0.735, 0.696 and
    0.689 -- so the rename class straddles the threshold and some renames are
    reported as retirements.

    ⚠ That is the direction to be wrong in: a missed retirement loses the lesson
    silently and forever, a reported rename costs a human one look. The cost is
    ledger NOISE, not blocked work -- an author can satisfy the row with an
    entry, which the ledger test accepts because the old name is gone.

    ⚠⚠ **If this row fails, the constant moved.** Re-run the sweep before
    deciding; do not nudge the number against a single case.
    """
    body_old = [
        "assert compute(1) == 1",
        "assert compute(2) == 2",
        "assert compute(3) == 3",
        "assert dedupe([%s.%s, %s./%s]) == 1" % (chr(39), chr(39), chr(39), chr(39)),
    ]
    body_new = ["assert dedupe() == 1"]
    repo = _repo(
        tmp_path,
        {"tests/test_a.py": "def test_renamed():%s    assert dedupe() == 1%s" % (NL, NL)},
    )
    diff = _diff(
        "--- a/tests/test_a.py" + NL + "+++ b/tests/test_a.py" + NL + "@@ -1 +0,0 @@" + NL
        + "-def test_original():" + NL
        + NL.join("-    " + line for line in body_old),
        "--- a/tests/test_a.py" + NL + "+++ b/tests/test_a.py" + NL + "@@ -0,0 +1 @@" + NL
        + "+def test_renamed():" + NL
        + NL.join("+    " + line for line in body_new),
    )

    assert dod.retired_test_functions(diff, repo) == ["tests/test_a.py::test_original"], (
        "a rename whose body shrank below the threshold is reported as a "
        "retirement. That is the accepted bias -- see _RENAME_BODY_SIMILARITY."
    )


def test_a_test_in_a_DELETED_file_is_keyed_to_that_file(dod, tmp_path):
    """⚠⚠ git writes `+++ /dev/null` for a deleted file, so the path must come
    from `--- a/` as well.

    Reading only `+++ b/` left `current` on the PREVIOUS file, and every test
    removed with a deleted file was reported under that file's name -- measured
    as `tests/test_a.py::test_from_the_deleted_file`. The row was still counted,
    so the grade survived, but the evidence named a `file::name` that does not
    exist and both the survival check and the rename comparison consulted the
    wrong file. Found in review.
    """
    repo = _repo(tmp_path, {"tests/test_a.py": "def test_kept():" + NL + "    assert 2" + NL})
    diff = _diff(
        "--- a/tests/test_a.py" + NL + "+++ b/tests/test_a.py" + NL + "@@ -1 +1 @@" + NL
        + "-def test_kept():" + NL + "-    assert 1" + NL
        + "+def test_kept():" + NL + "+    assert 2",
        "--- a/tests/test_b.py" + NL + "+++ /dev/null" + NL + "@@ -1 +0,0 @@" + NL
        + "-def test_from_the_deleted_file():" + NL + "-    assert True",
    )

    assert dod.retired_test_functions(diff, repo) == [
        "tests/test_b.py::test_from_the_deleted_file"
    ]


def test_a_move_to_another_file_in_the_same_diff_is_not_a_retirement(dod, tmp_path):
    """A test that leaves one file and arrives in another has not been retired."""
    repo = _repo(tmp_path, {"tests/test_b.py": "def test_moved():\n    assert True\n"})
    diff = _diff(
        _removed("tests/test_a.py", "test_moved"),
        _added("tests/test_b.py", "test_moved"),
    )

    assert dod.retired_test_functions(diff, repo) == []


def test_a_name_defined_in_two_files(dod, tmp_path):
    """⚠⚠ The fail-open case, and the reason this file exists.

    `test_empty` and friends are defined in many files here. A survival check
    that concatenates the tree sees the OTHER file's definition, calls the
    retirement a rename, and grades item 11 `n.a.` -- so the ledger is never
    demanded for exactly the names most likely to be retired. Keyed
    `file::name`, the surviving twin is irrelevant.
    """
    repo = _repo(
        tmp_path,
        {
            "tests/test_a.py": "def test_kept():\n    assert True\n",
            "tests/test_b.py": "def test_empty():\n    assert True\n",
        },
    )

    assert dod.retired_test_functions(_removed("tests/test_a.py", "test_empty"), repo) == [
        "tests/test_a.py::test_empty"
    ]


def test_a_mention_of_the_name_is_not_a_definition(dod, tmp_path):
    """⚠⚠ A raw substring scan is answered by the test DOCUMENTING the retirement.

    PR #748/#749 ships `test_the_retired_gap_tests_are_gone_from_the_short_function_file`,
    which asserts on the literal `"def test_a_macro_is_a_known_separate_gap"`.
    Under a substring check that string is a surviving definition, so the guard
    written to record a retirement would suppress the detector for it. The first
    version was saved only by the absent `(`, which is one character of luck.
    """
    repo = _repo(
        tmp_path,
        {
            "tests/test_a.py": 'ROSTER = ["def test_gone"]\n# def test_gone is retired\n',
        },
    )

    assert dod.retired_test_functions(_removed("tests/test_a.py", "test_gone"), repo) == [
        "tests/test_a.py::test_gone"
    ]


def test_an_async_definition_counts_as_surviving(dod, tmp_path):
    """`async def` is a definition; matching only `def` would report it retired.

    ⚠⚠ The first version of this row was VACUOUS for the reason it is named:
    its diff removed AND re-added the name, so the move exclusion decided it and
    `_defined_in` never ran -- it stayed green with `async` dropped from the
    survival regex. Removal only now, so the survival scan is the deciding
    check. Found in review.
    """
    repo = _repo(
        tmp_path, {"tests/test_a.py": "async def test_gone():\n    assert True\n"}
    )

    assert dod.retired_test_functions(_removed("tests/test_a.py", "test_gone"), repo) == []


def test_a_name_that_is_a_prefix_of_a_surviving_one_is_still_retired(dod, tmp_path):
    """⚠⚠ The row that catches a survival check loosened to a substring.

    `test_foo` and `test_foo_bar` are different tests. A `.startswith` or a bare
    `in` would read the longer name as the shorter one surviving and drop the
    ledger demand silently.

    ⚠ It also catches the corruption that actually happened while writing this
    detector: the `\nb` in the survival regex was written as a literal BACKSPACE
    (0x08), which compiles, runs, lints clean and matches nothing -- the exact
    0x08-for-\nb defect CLAUDE.md records from the nesting-depth opener. Every
    other row here passed with the corrupt regex; this one does not.
    """
    repo = _repo(
        tmp_path, {"tests/test_b.py": "def test_foo_bar():\n    assert True\n"}
    )

    assert dod.retired_test_functions(_removed("tests/test_b.py", "test_foo"), repo) == [
        "tests/test_b.py::test_foo"
    ]


def test_the_repo_root_comes_from_the_argument_not_the_process_cwd(dod, tmp_path, monkeypatch):
    """⚠⚠ The hook's CWD is not the repo root, which is why every other read here
    passes `cwd=REPO`.

    An unanchored `Path("tests")` yields nothing from anywhere else, so every
    removed name reads as retired and `pre_pr.py` refuses the PR on an ordinary
    rename. Asserted by running from a directory that has no `tests/` at all.
    """
    repo = _repo(tmp_path, {"tests/test_a.py": "def test_gone():\n    assert True\n"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert dod.retired_test_functions(_removed("tests/test_a.py", "test_gone"), repo) == []


def test_nothing_removed_is_nothing_retired(dod, tmp_path):
    """The quiet case: a diff that adds tests demands no ledger entry."""
    repo = _repo(tmp_path, {"tests/test_a.py": "def test_new():\n    assert True\n"})

    assert dod.retired_test_functions(_added("tests/test_a.py", "test_new"), repo) == []


# ---------------------------------------------------------------------------
# Row 11 grades COVERAGE, not a touched file
# ---------------------------------------------------------------------------

def _ledger(tmp_path: Path, paths: list[str]) -> Path:
    (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / "harness" / "retired.json").write_text(
        json.dumps({"schema": "jcm-harness-retired/v1", "retired": [
            {"path": p, "lesson": "x", "replacement": "f.py::t", "commit": "abc", "date": "2026-01-01"}
            for p in paths
        ]}),
        encoding="utf-8",
    )
    return tmp_path


def test_a_retirement_with_no_entry_of_its_own_is_uncovered(dod, tmp_path):
    """The defect this closes: row 11 asked only whether harness/retired.json
    appeared in the diff, so filing ONE entry for THREE retirements graded met.
    """
    repo = _ledger(tmp_path, ["tests/a.py::test_one"])
    assert dod.ledger_uncovered(
        ["tests/a.py::test_one", "tests/a.py::test_two", "tests/b.py::test_three"], repo
    ) == ["tests/a.py::test_two", "tests/b.py::test_three"]


def test_every_retirement_covered_is_empty(dod, tmp_path):
    repo = _ledger(tmp_path, ["tests/a.py::test_one", "tests/a.py::test_two"])
    assert dod.ledger_uncovered(["tests/a.py::test_one", "tests/a.py::test_two"], repo) == []


def test_a_deleted_file_is_covered_by_a_file_entry_or_a_test_inside_it(dod, tmp_path):
    """Both spellings are legal in the ledger's own note."""
    assert dod.ledger_uncovered(["tests/a.py"], _ledger(tmp_path, ["tests/a.py"])) == []
    assert dod.ledger_uncovered(["tests/a.py"], _ledger(tmp_path, ["tests/a.py::test_one"])) == []


def test_an_entry_for_one_test_does_not_cover_a_DIFFERENT_file(dod, tmp_path):
    """`startswith` without the `::` separator would let tests/a.py cover
    tests/a_extra.py -- the prefix trap this detector already paid for once.
    """
    repo = _ledger(tmp_path, ["tests/a.py::test_one"])
    assert dod.ledger_uncovered(["tests/a_extra.py"], repo) == ["tests/a_extra.py"]


def test_an_unreadable_ledger_covers_nothing(dod, tmp_path):
    """UNKNOWN blocks: an unparseable ledger records nothing."""
    (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / "harness" / "retired.json").write_text("{not json", encoding="utf-8")
    assert dod.ledger_uncovered(["tests/a.py::test_one"], tmp_path) == ["tests/a.py::test_one"]
    assert dod.ledger_uncovered(["tests/a.py::test_one"], tmp_path / "absent") == ["tests/a.py::test_one"]
