"""Shared Astro parsing helpers used by both extractor and imports."""

from __future__ import annotations

import re
from typing import Optional

from . import parse_budget


_ASTRO_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def split_astro_frontmatter(text: str) -> tuple[Optional[str], str, int, int]:
    """Return (frontmatter, remainder, frontmatter_start_line, remainder_start_line)."""
    src = text[1:] if text.startswith("\ufeff") else text
    lines = src.splitlines(keepends=True)

    i = 0
    while i < len(lines) and lines[i].strip() == "":
        parse_budget.checkpoint()
        i += 1

    if i >= len(lines) or lines[i].strip() != "---":
        return None, src, 1, 1

    start = i + 1
    j = start
    while j < len(lines):
        parse_budget.checkpoint()
        if lines[j].strip() == "---":
            frontmatter = "".join(lines[start:j])
            remainder = "".join(lines[j + 1:])
            return frontmatter, remainder, start + 1, j + 2
        j += 1

    # Malformed frontmatter fence: treat whole file as template.
    return None, src, 1, 1


def mask_html_comments_keep_offsets(text: str) -> str:
    """Replace HTML comment content with spaces while preserving offsets."""

    def _comment_repl(match: re.Match[str]) -> str:
        block = match.group(0)
        return "".join("\n" if ch == "\n" else " " for ch in block)

    return _ASTRO_HTML_COMMENT_RE.sub(_comment_repl, text)
