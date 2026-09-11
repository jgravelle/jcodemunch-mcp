"""The `mcp` upper bound in pyproject.toml carries the measurement that justifies it.

#609 (@kecsap, split from #574) asked why `mcp` is pinned below 2.0. The answer
was measured on 2026-09-11 against mcp 2.2.0 in a scratch venv: the low-level
`Server` no longer has the decorator registration (`@server.list_tools()` and
five siblings) this dispatcher is built on, so the server fails at import and a
stdio `initialize` never completes. A pin whose reason lives only in an issue
rots: the next person to bump it re-discovers the reason in CI. This test keeps
the reason ON the pin line, and makes a bump re-measure: the comment must name
the mcp major it excludes, a version it was measured against, and the date.
"""

from __future__ import annotations

import re
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _mcp_pin_lines() -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if re.match(r'\s*"mcp[><=~!]', ln)]


def test_the_mcp_pin_is_the_one_we_document():
    lines = _mcp_pin_lines()
    assert len(lines) == 1, lines
    assert '"mcp>=1.10.0,<2.0.0"' in lines[0], lines[0]


def test_the_pin_line_carries_a_measured_reason():
    (line,) = _mcp_pin_lines()
    comment = line.split("#", 1)[1] if "#" in line else ""
    assert comment, f"the mcp pin has no reason beside it: {line!r}"
    assert re.search(r"\bmeasured\b", comment), comment
    assert re.search(r"\bmcp 2\.\d+\.\d+\b", comment), (
        "the reason must name the 2.x version the tree was run against"
    )
    assert re.search(r"\b20\d\d-\d\d-\d\d\b", comment), "the reason must carry its date"
    assert re.search(r"#609", comment), "the reason must point at the issue holding the probe"


def test_the_reason_names_what_actually_breaks():
    """The reason is a fact about THIS tree, not a preference: it names the
    API shape that 2.x removed, so a reader can check it against server.py."""
    (line,) = _mcp_pin_lines()
    comment = line.split("#", 1)[1]
    assert "decorator" in comment or "on_call_tool" in comment, comment
