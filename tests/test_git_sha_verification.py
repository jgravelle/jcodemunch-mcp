"""Tests for git-SHA externally-attested cache verification (P1.6).

Covers the new ``verify_against`` parameter on ``get_symbol_source``. The
default ``"cache"`` mode is unchanged (self-referential hash check); the
new ``"git_sha"`` mode compares the cached source against the working-tree
git HEAD slice of the same file.

Direct unit tests of ``_verify_against_git_sha`` run against a real
ephemeral git repo so the shell-out and the line-range arithmetic are
exercised together. The MCP-tool-level integration is covered by the
existing ``get_symbol_source`` tests (no schema-breaking shape changes).
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_symbol import (
    _verify_against_git_sha as _verify_impl,
)

def _verify_against_git_sha(*args, **kwargs) -> str:
    """Status-only wrapper over the real verifier.

    v1.108.227 (#402) changed the return to ``(status, rev_used)`` so a verdict
    can never be silently about a different commit than the reader assumes.
    Every assertion in this file is about the STATUS, so they read against this
    wrapper; `tests/test_v1_108_227.py` is where the rev half is pinned.
    """
    return _verify_impl(*args, **kwargs)[0]



def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


@pytest.fixture
def git_repo():
    """Create a temporary git repo with a known committed file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subprocess.run(["git", "-C", str(root), "init", "--quiet"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.name", "Test"],
            check=True,
        )
        target = root / "module.py"
        target.write_text(
            "def hello():\n"
            "    return 'world'\n"
            "\n"
            "def goodbye():\n"
            "    return 'farewell'\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(root), "add", "module.py"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "commit", "--quiet", "-m", "initial"],
            check=True,
        )
        yield root


class TestGitShaVerification:
    def test_match_when_cache_equals_head_slice(self, git_repo):
        # Lines 1-2 of module.py are the hello function (1-indexed inclusive).
        result = _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(git_repo),
            file_path="module.py",
            line=1,
            end_line=2,
        )
        assert result == "git_sha_match"

    def test_mismatch_when_cache_differs(self, git_repo):
        result = _verify_against_git_sha(
            cached_source="def hello():\n    return 'tampered'",
            source_root=str(git_repo),
            file_path="module.py",
            line=1,
            end_line=2,
        )
        assert result == "git_sha_mismatch"

    def test_unavailable_when_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "module.py").write_text(
                "def hello():\n    return 'world'\n", encoding="utf-8"
            )
            result = _verify_against_git_sha(
                cached_source="def hello():\n    return 'world'",
                source_root=tmp,
                file_path="module.py",
                line=1,
                end_line=2,
            )
            assert result == "git_unavailable"

    def test_unavailable_when_file_not_in_head(self, git_repo):
        # Untracked new file — git show HEAD:newfile.py returns non-zero.
        (git_repo / "newfile.py").write_text("x = 1\n", encoding="utf-8")
        result = _verify_against_git_sha(
            cached_source="x = 1",
            source_root=str(git_repo),
            file_path="newfile.py",
            line=1,
            end_line=1,
        )
        assert result == "git_unavailable"

    def test_unavailable_when_source_root_none(self):
        result = _verify_against_git_sha(
            cached_source="anything",
            source_root=None,
            file_path="module.py",
            line=1,
            end_line=2,
        )
        assert result == "git_unavailable"

    def test_mismatch_when_line_range_out_of_bounds(self, git_repo):
        # module.py is 5 lines; asking for line 100 should mismatch rather than
        # match a phantom empty slice.
        result = _verify_against_git_sha(
            cached_source="anything",
            source_root=str(git_repo),
            file_path="module.py",
            line=100,
            end_line=101,
        )
        assert result == "git_sha_mismatch"

    def test_git_show_redirects_stdin_to_devnull(self, git_repo):
        """Regression (v1.108.49): the ``git show HEAD:<file>`` spawn must pass
        ``stdin=subprocess.DEVNULL``. Over the MCP stdio transport the server's
        stdin is the JSON-RPC pipe; a git child inheriting it deadlocks Git for
        Windows forever (its cmd\\git.exe wrapper holds the handle even for
        commands that don't read stdin). ``get_symbol_source`` is a hot read
        tool, so this redirect must never regress."""
        from unittest import mock

        # v1.108.224 (#400): bytes, not str. The capture dropped `text=True`, so
        # this mock is catching up with the real capture mode. The test's own
        # subject — the DEVNULL redirect — is untouched and still asserted.
        fake = mock.MagicMock(returncode=0, stdout=b"def hello():\n    return 'world'\n")
        with mock.patch(
            "jcodemunch_mcp.tools.get_symbol.subprocess.run", return_value=fake
        ) as run:
            _verify_against_git_sha(
                cached_source="def hello():\n    return 'world'",
                source_root=str(git_repo),
                file_path="module.py",
                line=1,
                end_line=2,
            )
        assert run.call_count == 1
        _, kwargs = run.call_args
        assert kwargs.get("stdin") == subprocess.DEVNULL, (
            "git show must redirect stdin to DEVNULL (Windows stdio-MCP deadlock guard)"
        )
