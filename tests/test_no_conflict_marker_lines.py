"""No tracked text file carries a merge-conflict marker line (LEDGER L-279).

A separator line reached `main`'s CHANGELOG.md through a merge: the
resolver compared lines to the marker exactly, and the checkout's CRLF
endings left the marker with a trailing carriage return, so it matched
nothing and stayed. Rendered Markdown reads that line as a setext
underline, so the paragraph above it turned into a heading. Nothing failed.

The rule here strips trailing whitespace, CR included, before matching,
so the spelling that escaped the resolver is the first one it catches.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Built from repetition so this file never holds a marker line itself.
# Git writes a label after the three side markers, never after the separator.
_LABELLED = ("<" * 7, "|" * 7, ">" * 7)
_SEPARATOR = "=" * 7
_MARKER_LINE = re.compile(
    r"^(?:(?:"
    + "|".join(re.escape(m) for m in _LABELLED)
    + r")(?: .*)?|"
    + re.escape(_SEPARATOR)
    + r")$"
)


def marker_lines(text: str) -> list[int]:
    """1-based numbers of lines that are exactly a conflict marker."""
    return [
        n
        for n, line in enumerate(text.splitlines(), 1)
        if _MARKER_LINE.match(line.rstrip())
    ]


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        # A checkout whose git cannot run must fail, not read as a skip.
        if (ROOT / ".git").exists():
            raise
        pytest.skip("not a git checkout")
    return [ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("a\n" + "=" * 7 + "\nb\n", [2]),
        ("a\r\n" + "=" * 7 + "\r\nb\r\n", [2]),  # the L-279 spelling
        ("<" * 7 + " HEAD\nx\n" + ">" * 7 + " origin/main\n", [1, 3]),
        ("|" * 7 + " merged common ancestors\n", [1]),
        ("=" * 7 + "   \n", [1]),
    ],
)
def test_marker_lines_are_found(text, expected):
    assert marker_lines(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "=" * 8 + "\n",  # a longer rule is a heading underline, not a marker
        "=" * 6 + "\n",
        "x " + "=" * 7 + "\n",
        "<" * 7 + "HEAD\n",  # git always writes a space before the label
        "=" * 7
        + " x\n",  # git never labels the separator; a pytest banner can start this way
        "",
    ],
)
def test_ordinary_lines_are_not_markers(text):
    assert marker_lines(text) == []


def test_no_tracked_text_file_has_a_marker_line():
    hits = []
    files = _tracked_files()
    assert files, "git ls-files returned nothing"
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue  # deleted in the working tree
        if b"\0" in data:
            continue  # binary
        lines = marker_lines(data.decode("utf-8", "replace"))
        if lines:
            hits.append(f"{path.relative_to(ROOT).as_posix()}:{lines}")
    assert not hits, "conflict-marker lines in tracked files: " + "; ".join(hits)
