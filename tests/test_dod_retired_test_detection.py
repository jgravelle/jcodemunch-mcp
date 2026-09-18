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


def test_a_deletion_beside_an_unrelated_addition_is_the_disclosed_gap(dod, tmp_path):
    """⚠⚠ The fail-open this detector accepts ON PURPOSE, pinned so it is a
    decision rather than a surprise.

    Nothing in a diff separates a rename from a deletion that happens to sit
    beside an unrelated new test in the same file, so a file that lost one and
    gained one is read as a rename. `test_edit_guard` fires on every removed
    `def test_` regardless and a human verifies each -- that is the control that
    still sees this case, and the reason a second gate here would be worse:
    every ordinary rename would become an `unmet` row and `pre_pr.py` refuses a
    PR on one.

    If this row ever fails because the detector got stricter, the thing to check
    is whether renames now block PRs.
    """
    repo = _repo(tmp_path, {"tests/test_a.py": "def test_unrelated():\n    assert True\n"})
    diff = _diff(
        _removed("tests/test_a.py", "test_genuinely_retired"),
        _added("tests/test_a.py", "test_unrelated"),
    )

    assert dod.retired_test_functions(diff, repo) == []


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
    """`async def` is a definition; matching only `def` would report it retired."""
    repo = _repo(
        tmp_path, {"tests/test_a.py": "async def test_gone():\n    assert True\n"}
    )
    diff = f"--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -1 +1 @@\n-async def test_gone():\n+async def test_gone():"

    assert dod.retired_test_functions(diff, repo) == []


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
