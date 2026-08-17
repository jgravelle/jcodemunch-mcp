"""v1.108.227 — `git_sha` verification answers the question it was asked.

@rknighton, [#402](https://github.com/jgravelle/jcodemunch-mcp/issues/402). The
index is built from the WORKING TREE at one moment and verified against live
`HEAD` at another, so a cache that is a byte-exact record of what it indexed
reported `git_sha_mismatch`. The report offered two readings and explicitly left
the routing to us. Measuring both turned out to matter, because **the fix the
report proposed does not fix the case the report reproduces**:

* **Form A — a commit landed after indexing and touched this file.** Live HEAD
  mismatches; the recorded `index.git_head` matches. The proposed `rev`
  parameter fixes this one completely.
* **Form B — the working tree was DIRTY at index time**, which is the report's
  headline reproduction. Measured: `index.git_head == live HEAD`, so passing the
  recorded revision changes *nothing*. **No commit anywhere contains those
  bytes, because the bytes were never committed.** Shipping the `rev` parameter
  alone would have looked like a fix while leaving the headline case broken.

Form B is answered by asking a second question instead of inventing a third
revision: does the cache match the WORKING TREE? If it does, the cache is
faithful and the commit is behind it — `git_sha_uncommitted`, not divergence.

`rev_used` is returned alongside the status so a verdict can never be silently
about a different commit than the reader assumes, including the rebased-away
fallback to `HEAD`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_symbol import _verify_against_git_sha


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

ORIGINAL = b"def hello():\n    return 'world'\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _git(root, "init", "--quiet")
        _git(root, "config", "user.email", "test@example.com")
        _git(root, "config", "user.name", "Test")
        _git(root, "config", "core.autocrlf", "false")
        (root / "mod.py").write_bytes(ORIGINAL)
        _git(root, "add", "-A")
        _git(root, "commit", "--quiet", "-m", "initial")
        yield root


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


class TestFormAHeadMovedAfterIndexing:
    """A commit landed after indexing and changed this file. The cache still
    describes the commit it was built at; live HEAD is simply a later one."""

    def test_live_head_reports_divergence(self, repo):
        indexed_at = _head(repo)
        (repo / "mod.py").write_bytes(b"def hello():\n    return 'CHANGED'\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "--quiet", "-m", "change")
        assert indexed_at != _head(repo)

        status, rev = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
        )
        assert (status, rev) == ("git_sha_mismatch", "HEAD")

    def test_the_recorded_revision_matches(self, repo):
        indexed_at = _head(repo)
        (repo / "mod.py").write_bytes(b"def hello():\n    return 'CHANGED'\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "--quiet", "-m", "change")

        status, rev = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
            rev=indexed_at,
        )
        assert status == "git_sha_match"
        assert rev == indexed_at


class TestFormBDirtyTreeAtIndexTime:
    """The report's headline reproduction, and the reason a `rev` parameter
    alone is not the fix."""

    def test_the_recorded_revision_is_the_same_commit(self, repo):
        """⚠ The measurement that redirected this fix. HEAD never moved, so
        there is no other revision to compare against — the bytes were never
        committed anywhere."""
        indexed_at = _head(repo)
        (repo / "mod.py").write_bytes(b"def hello():\n    return 'edited'\n")
        assert indexed_at == _head(repo)

        for rev in (None, indexed_at):
            status, _ = _verify_against_git_sha(
                cached_source="def hello():\n    return 'edited'",
                source_root=str(repo), file_path="mod.py", line=1, end_line=2,
                rev=rev,
            )
            assert status == "git_sha_uncommitted", rev

    def test_it_is_not_reported_as_divergence(self, repo):
        (repo / "mod.py").write_bytes(b"def hello():\n    return 'edited'\n")
        status, _ = _verify_against_git_sha(
            cached_source="def hello():\n    return 'edited'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
        )
        assert status != "git_sha_mismatch"


class TestGenuineDivergenceIsStillReported:
    """The guard. `git_sha_uncommitted` is a softer verdict, so it must not
    become a place for real corruption to hide."""

    def test_a_cache_matching_neither_side_is_a_mismatch(self, repo):
        """Tampered cache on a CLEAN tree: matches neither the commit nor the
        checkout."""
        status, _ = _verify_against_git_sha(
            cached_source="def hello():\n    return 'TAMPERED'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
        )
        assert status == "git_sha_mismatch"

    def test_a_cache_matching_neither_side_on_a_dirty_tree_is_a_mismatch(self, repo):
        """The sharp case: the tree IS dirty, so the softer verdict is
        available — but this cache matches the working tree no better than it
        matches the commit, so it must not receive it."""
        (repo / "mod.py").write_bytes(b"def hello():\n    return 'edited'\n")
        status, _ = _verify_against_git_sha(
            cached_source="def hello():\n    return 'TAMPERED'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
        )
        assert status == "git_sha_mismatch"

    def test_an_unchanged_cache_on_a_clean_tree_still_matches(self, repo):
        status, _ = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
        )
        assert status == "git_sha_match"

    def test_a_truncated_cache_is_not_rescued_by_the_working_tree(self, repo):
        """The .224 truncation guard must survive the new second comparison —
        both paths run through the same `_slice_matches`."""
        status, _ = _verify_against_git_sha(
            cached_source="def hello():",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
        )
        assert status == "git_sha_mismatch"


class TestRevUsedIsAlwaysReported:
    def test_a_resolvable_revision_is_echoed(self, repo):
        sha = _head(repo)
        _, rev = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2, rev=sha,
        )
        assert rev == sha

    def test_no_revision_means_head(self, repo):
        _, rev = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
        )
        assert rev == "HEAD"

    def test_an_unreachable_recorded_revision_falls_back_and_says_so(self, repo):
        """⚠ The recorded revision can be gone — rebased away, force-pushed
        over, absent from a shallow clone. Failing there would make this mode
        WORSE than before for those repositories, so it falls back to HEAD —
        and `rev_used` is what stops that being a silent change of subject.
        """
        status, rev = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2,
            rev="0" * 40,
        )
        assert rev == "HEAD"
        assert status == "git_sha_match"

    def test_an_empty_recorded_revision_is_treated_as_head(self, repo):
        _, rev = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="mod.py", line=1, end_line=2, rev="   ",
        )
        assert rev == "HEAD"

    def test_unavailable_still_reports_a_rev(self, repo):
        status, rev = _verify_against_git_sha(
            cached_source="x", source_root=None, file_path="mod.py",
            line=1, end_line=1, rev="abc123",
        )
        assert (status, rev) == ("git_unavailable", "abc123")


class TestEndToEndThroughTheTool:
    """The user's entry point, not the layer that was edited."""

    @pytest.fixture
    def indexed(self):
        from jcodemunch_mcp.tools.index_folder import index_folder

        root = Path(tempfile.mkdtemp(prefix="jcm-402-"))
        storage = tempfile.mkdtemp(prefix="jcm-402-store-")
        try:
            _git(root, "init", "--quiet")
            _git(root, "config", "user.email", "t@e.com")
            _git(root, "config", "user.name", "T")
            _git(root, "config", "core.autocrlf", "false")
            (root / "mod.py").write_bytes(ORIGINAL)
            _git(root, "add", "-A")
            _git(root, "commit", "--quiet", "-m", "initial")
            # Dirty the tree, THEN index: the cache records uncommitted bytes.
            (root / "mod.py").write_bytes(b"def hello():\n    return 'edited'\n")
            res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
            if isinstance(res, str):
                res = json.loads(res)
            yield root, storage, (res.get("repo") or res.get("repo_id"))
        finally:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(storage, ignore_errors=True)

    def test_a_dirty_index_is_not_reported_as_divergence(self, indexed):
        from jcodemunch_mcp.tools.get_symbol import get_symbol_source

        _, storage, repo_id = indexed
        out = get_symbol_source(
            repo=repo_id, symbol_id="mod.py::hello#function",
            verify=True, verify_against="git_sha", storage_path=storage,
        )
        if isinstance(out, str):
            out = json.loads(out)
        assert out["git_sha_verification"] == "git_sha_uncommitted"

    def test_the_response_names_the_revision_it_compared_against(self, indexed):
        from jcodemunch_mcp.tools.get_symbol import get_symbol_source

        root, storage, repo_id = indexed
        out = get_symbol_source(
            repo=repo_id, symbol_id="mod.py::hello#function",
            verify=True, verify_against="git_sha", storage_path=storage,
        )
        if isinstance(out, str):
            out = json.loads(out)
        assert out["git_sha_rev"] == _head(root)

    def test_verify_against_is_hidden_under_compact_but_still_honoured(self, indexed):
        """⚠ `verify_against` joined `_COMPACT_STRIP_PARAMS` in v1.108.227.

        Adding `git_sha_rev` to its description broke the core_compact ceiling,
        and the measurement was the point: core_compact had **4 tokens** of
        headroom, so any core-tier description gaining a clause would have. The
        budget's own guidance is to strip an advanced param rather than shave
        words, so attestation left the minimal surface.

        Hidden is not removed. `_DECLARED_ARG_KEYS` is snapshotted BEFORE the
        strip precisely so a hidden-but-honoured param cannot read as a caller
        mistake (the v1.108.177 class, one layer down) — asserted here, and the
        call below proves the handler still acts on it.
        """
        from jcodemunch_mcp import server

        assert "verify_against" in server._COMPACT_STRIP_PARAMS["get_symbol_source"]

        from jcodemunch_mcp.tools.get_symbol import get_symbol_source

        _, storage, repo_id = indexed
        out = get_symbol_source(
            repo=repo_id, symbol_id="mod.py::hello#function",
            verify=True, verify_against="git_sha", storage_path=storage,
        )
        if isinstance(out, str):
            out = json.loads(out)
        assert "git_sha_verification" in out, (
            "a stripped param must still be honoured by the handler"
        )

    def test_cache_mode_is_untouched(self, indexed):
        """`verify_against="cache"` must not grow a git field."""
        from jcodemunch_mcp.tools.get_symbol import get_symbol_source

        _, storage, repo_id = indexed
        out = get_symbol_source(
            repo=repo_id, symbol_id="mod.py::hello#function",
            verify=True, storage_path=storage,
        )
        if isinstance(out, str):
            out = json.loads(out)
        assert "git_sha_verification" not in out
        assert "git_sha_rev" not in out
        assert out["content_verified"] is True
