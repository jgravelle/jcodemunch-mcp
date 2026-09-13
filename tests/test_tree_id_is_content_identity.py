"""The D5 stamp's tree identity names CONTENT, so committing it does not move it (#675).

`_common.tree_id` hashed `ls-tree HEAD` + `git diff HEAD` + the untracked
listing. Committing moves a change from the second string into the first:
same content, different strings, different hash. `pre_pr` then refused a
stamp for the very tree it had just been run against, on a clean working
tree, and the natural order (tier, commit, PR) paid for two full tiers.
The docstring had claimed the invariance since W-21.

Every test drives a scratch repository the test owns; the real checkout's
index and stamp are never touched.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / ".claude" / "hooks" / "_common.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "core.autocrlf", "false")
    (r / "src").mkdir()
    (r / "docs").mkdir()
    (r / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (r / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")
    (r / "docs" / "notes.md").write_text("notes\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    spec = importlib.util.spec_from_file_location("_common_675", COMMON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "REPO", r)
    return r, mod


def test_committing_a_modification_keeps_the_identity(repo):
    r, common = repo
    (r / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    before = common.tree_id()
    _git(r, "commit", "-q", "-am", "change")
    assert common.tree_id() == before


def test_staging_does_not_move_the_identity_either(repo):
    r, common = repo
    (r / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    unstaged = common.tree_id()
    _git(r, "add", "src/a.py")
    assert common.tree_id() == unstaged


def test_committing_a_new_file_keeps_the_identity(repo):
    r, common = repo
    (r / "src" / "new.py").write_text("N = 1\n", encoding="utf-8")
    before = common.tree_id()
    _git(r, "add", "src/new.py")
    _git(r, "commit", "-q", "-m", "add")
    assert common.tree_id() == before


def test_committing_a_deletion_keeps_the_identity(repo):
    r, common = repo
    (r / "src" / "b.py").unlink()
    before = common.tree_id()
    _git(r, "commit", "-q", "-am", "delete")
    assert common.tree_id() == before


def test_a_content_change_under_a_tier_path_still_moves_it(repo):
    r, common = repo
    before = common.tree_id()
    (r / "src" / "a.py").write_text("A = 3\n", encoding="utf-8")
    assert common.tree_id() != before


def test_an_untracked_file_under_a_tier_path_still_counts(repo):
    r, common = repo
    before = common.tree_id()
    (r / "src" / "untracked.py").write_text("U = 1\n", encoding="utf-8")
    assert common.tree_id() != before


def test_a_deletion_under_a_tier_path_still_moves_it(repo):
    r, common = repo
    before = common.tree_id()
    (r / "src" / "b.py").unlink()
    assert common.tree_id() != before


def test_docs_and_the_harness_footprint_do_not_count(repo):
    r, common = repo
    before = common.tree_id()
    (r / "docs" / "notes.md").write_text("edited\n", encoding="utf-8")
    (r / ".coverage.host.123").write_text("x", encoding="utf-8")
    assert common.tree_id() == before
    _git(r, "add", "docs/notes.md")
    _git(r, "commit", "-q", "-m", "docs")
    assert common.tree_id() == before


def test_the_real_index_is_not_touched(repo):
    r, common = repo
    (r / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    (r / "src" / "untracked.py").write_text("U = 1\n", encoding="utf-8")
    status = _git(r, "status", "--porcelain")
    common.tree_id()
    assert _git(r, "status", "--porcelain") == status


def test_commit_invariance_holds_in_a_worktree(repo, tmp_path, monkeypatch):
    # A worktree's `.git` is a FILE and its index lives under the main repo's
    # `.git/worktrees/<name>/`; the throwaway index must start from THAT one.
    r, common = repo
    wt = tmp_path / "wt"
    _git(r, "worktree", "add", "-q", "-b", "probe", str(wt))
    monkeypatch.setattr(common, "REPO", wt)
    (wt / "src" / "a.py").write_text("A = 9\n", encoding="utf-8")
    before = common.tree_id()
    _git(wt, "commit", "-q", "-am", "wt change")
    assert common.tree_id() == before


def test_an_unreadable_tree_never_matches_a_stamp(repo, monkeypatch):
    # Fail closed: two failed reads must not certify each other.
    r, common = repo
    monkeypatch.setattr(common, "REPO", r / "not-a-repo")
    first, second = common.tree_id(), common.tree_id()
    assert first != second


def test_a_racily_clean_edit_is_read_not_trusted_from_the_stat_cache(repo):
    # Same size, same mtime as the index entry, entry as new as the index
    # file: git's racy-clean rule is the ONLY thing that re-reads it. A
    # throwaway index copied with a fresh mtime defeats that rule and names
    # the old content (1 run in 12 on the first draft of the fix).
    import os
    r, common = repo
    target = r / "src" / "a.py"
    # Re-stage under a mtime well in the past, so the entry is recorded clean
    # and NOT smudged (git smudges an entry as new as the index it writes,
    # which would force a re-read and let this test pass on the defect).
    stamp = target.stat().st_mtime_ns - 10_000_000_000
    os.utime(target, ns=(stamp, stamp))
    _git(r, "add", "src/a.py")
    before = common.tree_id()
    target.write_text("A = 7\n", encoding="utf-8")  # same length as "A = 1\n"
    os.utime(target, ns=(stamp, stamp))
    index = r / ".git" / "index"
    os.utime(index, ns=(stamp, stamp))
    after = common.tree_id()
    # git itself sees the edit (checked AFTER: status may rewrite the index)
    assert " M src/a.py" in _git(r, "status", "--porcelain")
    assert after != before
