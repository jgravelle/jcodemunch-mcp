"""v1.108.224 — `verify_against="git_sha"` compares like with like.

@rknighton, [#400](https://github.com/jgravelle/jcodemunch-mcp/issues/400) and
[#401](https://github.com/jgravelle/jcodemunch-mcp/issues/401). Two independent
defects in `_verify_against_git_sha`, each of which alone made the
externally-attested mode report failure for most of what it was asked about:

**#400 — the capture mode.** `text=True` newline-translated and locale-decoded
git's output before comparing it against a cached side read as raw bytes and
decoded as UTF-8. On a Windows clone under the installer-default
`core.autocrlf=true` that is *every symbol in every file*. Worse, `text=True`
decodes on a `subprocess` reader thread, so an undecodable byte raised where
this frame's `except` cannot see it — `stdout` came back unset and a decoding
problem acquired the `git_unavailable` verdict, which means "git is
unreachable".

**#401 — the units.** The cached side is a byte range starting at the
declaration's first token; this side sliced whole lines. So every symbol whose
declaration is indented — the majority of symbols in most codebases — compared
an unindented body against an indented one and reported divergence.

⚠ The existing `tests/test_git_sha_verification.py` fixture is `def hello():`
at line 1, column 1, ASCII, LF. It is structurally incapable of exercising
either defect, which is why both shipped. Every test here varies exactly one of
those four properties.

The `TestDivergenceStillDetected` class is the load-bearing half: two fixes that
loosen a comparison can always be "passed" by loosening it into always-match.
Those tests pass before AND after, which is what shows these fixes are targeted.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_symbol import (
    _normalize_eol,
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



pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

NESTED = b"class Outer:\n    def method(self):\n        return 42\n"


def _commit(root: Path, name: str, data: bytes) -> None:
    """Commit exactly `data`.

    ⚠ `core.autocrlf=false` is pinned so the committed blob is the bytes given
    on every platform. Without it a Windows runner rewrites the fixture and the
    line-ending tests below silently stop testing line endings.
    """
    subprocess.run(["git", "-C", str(root), "init", "--quiet"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True
    )
    (root / name).write_bytes(data)
    subprocess.run(["git", "-C", str(root), "add", name], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--quiet", "-m", "initial"], check=True
    )


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


# ── #400: the capture mode ────────────────────────────────────────────────


class TestLineEndings:
    def test_crlf_committed_matches_when_unchanged(self, repo):
        _commit(repo, "module.py", b"def hello():\r\n    return 'world'\r\n")
        assert _verify_against_git_sha(
            cached_source="def hello():\r\n    return 'world'",
            source_root=str(repo), file_path="module.py", line=1, end_line=2,
        ) == "git_sha_match"

    def test_lf_blob_with_crlf_working_tree_matches(self, repo):
        """The default state of a Windows clone: `core.autocrlf=true` means the
        blob is LF and the working tree the cache was built from is CRLF.
        Nothing is wrong, and before #400 every symbol in the repo said
        otherwise."""
        _commit(repo, "module.py", b"def hello():\n    return 'world'\n")
        assert _verify_against_git_sha(
            cached_source="def hello():\r\n    return 'world'",
            source_root=str(repo), file_path="module.py", line=1, end_line=2,
        ) == "git_sha_match"


class TestEncoding:
    def test_non_ascii_source_matches_when_unchanged(self, repo):
        _commit(
            repo, "module.py",
            "def hello():\n    return 'naïve résumé'\n".encode("utf-8"),
        )
        assert _verify_against_git_sha(
            cached_source="def hello():\n    return 'naïve résumé'",
            source_root=str(repo), file_path="module.py", line=1, end_line=2,
        ) == "git_sha_match"

    def test_byte_undecodable_in_locale_codec_is_not_reported_unavailable(self, repo):
        """U+0401 encodes as D0 81, and 0x81 is undefined in cp1252.

        Under `text=True` the decode raised on subprocess's reader thread —
        which this frame's `except` clause cannot catch and does not name — so
        the symbol came back `git_unavailable` while git was perfectly
        reachable and the file was tracked. That verdict sends an operator to
        check the three things that were all fine, and makes genuine
        `git_unavailable` counts silently wrong.
        """
        _commit(
            repo, "module.py",
            "# Ё\ndef hello():\n    return 'world'\n".encode("utf-8"),
        )
        assert _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="module.py", line=2, end_line=3,
        ) == "git_sha_match"

    def test_symmetric_replace_is_the_accepted_cost(self, repo):
        """The stated cost of `errors="replace"` on both sides, measured.

        Two byte sequences that differ ONLY where neither is decodable as UTF-8
        compare equal, because both collapse to U+FFFD. This is the price of
        decoding both sides identically, and identical decoding is what stops
        false mismatches. Asserted so the trade-off is a pinned fact rather than
        a sentence in a docstring.
        """
        _commit(repo, "module.py", b"def hello():\n    return '\xff'\n")
        assert _verify_against_git_sha(
            cached_source=b"def hello():\n    return '\xfe'".decode(
                "utf-8", errors="replace"
            ),
            source_root=str(repo), file_path="module.py", line=1, end_line=2,
        ) == "git_sha_match"


class TestNormalizeEol:
    @pytest.mark.parametrize("raw, want", [
        ("a\r\nb", "a\nb"),
        ("a\rb", "a\nb"),
        ("a\nb", "a\nb"),
        ("a\r\n\rb", "a\n\nb"),
    ])
    def test_collapses_every_form(self, raw, want):
        assert _normalize_eol(raw) == want


# ── #401: byte range vs line range ────────────────────────────────────────


class TestIndentedSymbols:
    def test_indented_method_matches_when_unchanged(self, repo):
        _commit(repo, "module.py", NESTED)
        assert _verify_against_git_sha(
            cached_source="def method(self):\n        return 42",
            source_root=str(repo), file_path="module.py", line=2, end_line=3,
        ) == "git_sha_match"

    def test_indented_method_matches_in_a_brace_language(self, repo):
        _commit(
            repo, "Example.java",
            b"public class Example {\n    public int value() {\n"
            b"        return 1;\n    }\n}\n",
        )
        assert _verify_against_git_sha(
            cached_source="public int value() {\n        return 1;\n    }",
            source_root=str(repo), file_path="Example.java", line=2, end_line=4,
        ) == "git_sha_match"

    def test_token_after_the_symbol_on_its_last_line_is_ignored(self, repo):
        _commit(
            repo, "module.py",
            b"class Outer:\n    def method(self): return 42  # trailing\n",
        )
        assert _verify_against_git_sha(
            cached_source="def method(self): return 42",
            source_root=str(repo), file_path="module.py", line=2, end_line=2,
        ) == "git_sha_match"

    def test_column_1_symbol_still_matches(self, repo):
        """The control: the only shape the pre-existing suite covered."""
        _commit(repo, "module.py", b"def hello():\n    return 'world'\n")
        assert _verify_against_git_sha(
            cached_source="def hello():\n    return 'world'",
            source_root=str(repo), file_path="module.py", line=1, end_line=2,
        ) == "git_sha_match"


# ── The guard: neither fix may degrade into always-match ──────────────────


class TestDivergenceStillDetected:
    def test_interior_indentation_change_is_still_divergence(self, repo):
        """Indentation is semantic in Python. Realigning the FIRST line must not
        become a whitespace-insensitive compare of the rest."""
        _commit(repo, "module.py", NESTED)
        assert _verify_against_git_sha(
            cached_source="def method(self):\n            return 42",
            source_root=str(repo), file_path="module.py", line=2, end_line=3,
        ) == "git_sha_mismatch"

    @pytest.mark.parametrize("cached, why", [
        ("def method(self):\n        return 99", "changed literal"),
        ("def method(self, extra):\n        return 42", "changed signature"),
        ("def method(self):\n        log()\n        return 42", "added statement"),
    ])
    def test_real_changes_still_mismatch(self, cached, why):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _commit(root, "module.py", NESTED)
            assert _verify_against_git_sha(
                cached_source=cached,
                source_root=str(root), file_path="module.py", line=2, end_line=3,
            ) == "git_sha_mismatch", why

    def test_crlf_normalisation_is_not_content_insensitive(self, repo):
        _commit(repo, "module.py", b"def hello():\r\n    return 'world'\r\n")
        assert _verify_against_git_sha(
            cached_source="def hello():\r\n    return 'CHANGED'",
            source_root=str(repo), file_path="module.py", line=1, end_line=2,
        ) == "git_sha_mismatch"

    def test_a_truncated_cache_is_still_divergence(self, repo):
        """⚠⚠ Not from either issue — found writing these tests, and it is the
        reason the shipped fix is not the submitted patch verbatim.

        Realigning by slicing HEAD to `len(cached_slice)` makes the comparison a
        PREFIX match, so a cache holding only the first part of a symbol — a bad
        `byte_length`, a partial write, precisely the corruption this mode
        exists to catch — would be attested as matching on the part it holds.
        The line-count check is what closes it, and it is compatible with the
        one legitimate shortfall (trailing text on the last line).
        """
        _commit(repo, "module.py", NESTED)
        assert _verify_against_git_sha(
            cached_source="def method(self):",
            source_root=str(repo), file_path="module.py", line=2, end_line=3,
        ) == "git_sha_mismatch"

    def test_a_cache_truncated_inside_its_last_line_is_still_divergence(self, repo):
        """#412: the hole the line-count guard does NOT close.

        Truncating INSIDE the final line preserves the newline count, so the
        length-bounded slice stays a prefix match and the cache is attested as
        matching the commit on the part it does hold.
        """
        _commit(repo, "module.py", NESTED)
        assert _verify_against_git_sha(
            cached_source="def method(self):\n        return 4",
            source_root=str(repo), file_path="module.py", line=2, end_line=3,
        ) == "git_sha_mismatch"

    def test_a_cache_missing_its_last_line_is_still_divergence(self, repo):
        """The same hole one line up: truncation at a line boundary."""
        _commit(
            repo, "module.py",
            b"class Outer:\n    def method(self):\n        log()\n        return 42\n",
        )
        assert _verify_against_git_sha(
            cached_source="def method(self):\n        log()",
            source_root=str(repo), file_path="module.py", line=2, end_line=4,
        ) == "git_sha_mismatch"
