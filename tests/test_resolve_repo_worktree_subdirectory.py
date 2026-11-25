"""L-235: `resolve_repo` on a SUBDIRECTORY of a linked worktree.

The worktree root of an indexed checkout came back with `canonical_candidates`
(#277, #303), but a folder inside that worktree did not: the common-dir read
looked only at `<path>/.git`, so the subdirectory had no git identity, missed
both fast paths, and reached `git rev-parse` to answer `indexed: false` with no
hint. The nearest enclosing `.git` decides now, the way git itself decides.
"""

import os
import subprocess
from pathlib import Path

from jcodemunch_mcp.tools import resolve_repo as rr
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.resolve_repo import _git_common_dir_cheap, resolve_repo


def _git(*args, cwd):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _canonical(tmp_path):
    canonical = tmp_path / "canonical"
    (canonical / "src" / "pkg").mkdir(parents=True)
    _git("init", "-b", "main", cwd=canonical)
    (canonical / "src" / "pkg" / "mod.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    _git("add", "-A", cwd=canonical)
    _git("commit", "-m", "initial", cwd=canonical)
    store = str(tmp_path / "store")
    index_folder(str(canonical), use_ai_summaries=False, storage_path=store, identity_mode="local")
    return canonical, store


def _no_git_subprocess(monkeypatch):
    calls = []
    real = subprocess.run

    def tracking(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git":
            calls.append(list(cmd))
        return real(*args, **kwargs)

    monkeypatch.setattr(rr.subprocess, "run", tracking)
    return calls


def _assert_candidate(result, canonical_root):
    assert result["found"] is True
    assert result["indexed"] is False
    assert "canonical_candidates" in result, result
    cand = result["canonical_candidates"]
    assert len(cand) == 1
    assert cand[0]["repo"].startswith("local/canonical-")
    assert cand[0]["rationale"] == "shared --git-common-dir"
    assert Path(cand[0]["source_root"]).resolve() == canonical_root.resolve()
    assert result["_meta"]["match_path"] == "canonical_candidate_fast"


def test_a_subdirectory_of_a_sibling_worktree_gets_the_canonical_candidate(tmp_path, monkeypatch):
    canonical, store = _canonical(tmp_path)
    worktree = tmp_path / "wt-feature"
    _git("worktree", "add", "-b", "feature", str(worktree), cwd=canonical)
    calls = _no_git_subprocess(monkeypatch)
    result = resolve_repo(str(worktree / "src" / "pkg"), storage_path=store)
    _assert_candidate(result, canonical)
    assert calls == [], f"the fast path spawned git: {calls}"


def test_a_subdirectory_of_a_nested_worktree_gets_the_canonical_candidate(tmp_path, monkeypatch):
    canonical, store = _canonical(tmp_path)
    worktree = canonical / ".worktrees" / "feature"
    _git("worktree", "add", "-b", "feature", str(worktree), cwd=canonical)
    calls = _no_git_subprocess(monkeypatch)
    result = resolve_repo(str(worktree / "src" / "pkg"), storage_path=store)
    _assert_candidate(result, canonical)
    assert calls == [], f"the fast path spawned git: {calls}"


def test_the_worktree_root_still_gets_the_candidate(tmp_path):
    canonical, store = _canonical(tmp_path)
    worktree = tmp_path / "wt-feature"
    _git("worktree", "add", "-b", "feature", str(worktree), cwd=canonical)
    _assert_candidate(resolve_repo(str(worktree), storage_path=store), canonical)


def test_the_common_dir_of_a_subdirectory_is_its_worktrees(tmp_path):
    canonical = tmp_path / "canonical"
    canonical_git = canonical / ".git"
    gitdir = canonical_git / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    (worktree / "a" / "b").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert rr._enclosing_git_common_dir(worktree / "a" / "b") == canonical_git.resolve()
    assert rr._enclosing_git_common_dir(worktree) == canonical_git.resolve()
    # The per-folder read is unchanged: it still answers for `<path>/.git` only.
    assert _git_common_dir_cheap(worktree / "a" / "b") is None


def test_the_nearest_git_wins_over_an_enclosing_worktree(tmp_path):
    """An independent repo inside a worktree is its own repo, as git sees it."""
    canonical = tmp_path / "canonical"
    gitdir = canonical / ".git" / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    inner = worktree / "vendor" / "lib"
    (inner / "src").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    (inner / ".git").mkdir()
    assert rr._enclosing_git_common_dir(inner / "src") == (inner / ".git").resolve()


def test_a_folder_under_no_git_has_no_common_dir(tmp_path):
    (tmp_path / "plain" / "deep").mkdir(parents=True)
    assert rr._enclosing_git_common_dir(tmp_path / "plain" / "deep", stop=tmp_path) is None


def test_a_subdirectory_of_an_unrelated_repo_gets_no_candidate(tmp_path):
    _canonical(tmp_path)
    store = str(tmp_path / "store")
    other = tmp_path / "other"
    (other / "sub").mkdir(parents=True)
    _git("init", "-b", "main", cwd=other)
    result = resolve_repo(str(other / "sub"), storage_path=store)
    assert result["indexed"] is False
    assert "canonical_candidates" not in result


def test_an_indexed_subtree_of_the_canonical_is_not_offered(tmp_path):
    """Only a checkout root is a candidate: the source-root side is not walked,
    so an index of `canonical/src` is not offered as the repo for a worktree."""
    canonical = tmp_path / "canonical"
    (canonical / "src").mkdir(parents=True)
    _git("init", "-b", "main", cwd=canonical)
    (canonical / "src" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git("add", "-A", cwd=canonical)
    _git("commit", "-m", "initial", cwd=canonical)
    store = str(tmp_path / "store")
    index_folder(str(canonical / "src"), use_ai_summaries=False, storage_path=store, identity_mode="local")
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-b", "feature", str(worktree), cwd=canonical)
    result = resolve_repo(str(worktree / "src"), storage_path=store)
    assert "canonical_candidates" not in result
