"""The policy block's boundaries follow Markdown's own fence and heading rules (LEDGER L-204).

`policy_block_span` decides which lines `uninstall` deletes and
`claude-md --refresh` rewrites. It toggled "inside a fence" on any line opening
with three backticks or three tildes, and it stripped indentation before asking
whether a line was a heading. In a user's own text that meant:

- a tilde line inside a backtick fence closed the fence early, so a `#` comment
  after it ended the block there;
- a shorter run inside a longer fence closed it, and so did a fence line
  carrying an info string, which CommonMark says can only open a fence;
- a line indented four spaces (an indented code block) read as a heading or a
  fence.

Each of those moves the end of the block, which moves what a refresh replaces.
A closing fence is now the opener's character, at least as long, with nothing
after it; a line indented four or more columns is code, never a heading or a
fence (CommonMark 4.2, 4.4, 4.5).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.cli import policy

BLOCK = (
    "## Code Exploration Policy\n"
    "\n"
    "Use jCodemunch-MCP tools for code navigation.\n"
    "\n"
)
TICKS = "`" * 3
TILDES = "~" * 3


def _span(text: str) -> tuple[list[str], tuple[int, int] | None]:
    lines = text.splitlines()
    return lines, policy.policy_block_span(lines)


def _ends_at_mine(body: str) -> None:
    lines, span = _span(BLOCK + body + "\n# Mine\n")
    assert span == (0, lines.index("# Mine")), (span, lines)


def test_a_tilde_line_does_not_close_a_backtick_fence():
    _ends_at_mine(f"{TICKS}\n{TILDES}\n# a comment\n{TICKS}\n")


def test_a_backtick_line_does_not_close_a_tilde_fence():
    _ends_at_mine(f"{TILDES}\n{TICKS}\n# a comment\n{TILDES}\n")


def test_a_shorter_run_does_not_close_a_longer_fence():
    _ends_at_mine(f"{'`' * 4}markdown\n{TICKS}\n# a comment\n{'`' * 4}\n")


def test_a_line_with_an_info_string_does_not_close_a_fence():
    _ends_at_mine(f"{TICKS}\n{TICKS}python\n# a comment\n{TICKS}\n")


def test_a_longer_closing_run_does_close_the_fence():
    """The twin: a closer may be longer than its opener."""
    lines, span = _span(BLOCK + f"{TICKS}\n# a comment\n{'`' * 5}\n# Mine\n")
    assert span == (0, lines.index("# Mine"))


def test_an_indented_code_line_is_not_a_heading():
    _ends_at_mine("Example:\n\n    # not a heading\n    echo hi\n")


def test_an_indented_fence_line_is_not_a_fence():
    """Four spaces of indent make `~~~` code; it must not open a fence that hides `# Mine`."""
    lines, span = _span(BLOCK + f"    {TILDES}\n# Mine\n")
    assert span == (0, lines.index("# Mine"))


def test_a_tab_indented_line_is_not_a_heading():
    _ends_at_mine("Example:\n\n\t# not a heading\n")


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
def test_a_heading_indented_up_to_three_spaces_still_ends_the_block(indent):
    """The twin: CommonMark allows up to three spaces before a heading."""
    lines, span = _span(BLOCK + f"{indent}# Mine\n")
    assert span == (0, lines.index(f"{indent}# Mine"))


@pytest.mark.parametrize("indent", ["", "   "])
def test_a_fence_indented_up_to_three_spaces_still_hides_a_heading(indent):
    _ends_at_mine(f"{indent}{TICKS}\n# a comment\n{indent}{TICKS}\n")


def test_a_marker_inside_a_mixed_fence_is_not_the_start():
    """The start rule reads fences the same way: a tilde line does not close a backtick fence."""
    text = f"{TICKS}\n{TILDES}\n## Code Exploration Policy\n{TICKS}\n\n" + BLOCK + "# Mine\n"
    lines, span = _span(text)
    starts = [i for i, ln in enumerate(lines) if ln == "## Code Exploration Policy"]
    assert span == (starts[1], lines.index("# Mine")), (span, lines)


def test_an_indented_marker_is_not_the_start():
    text = "Example:\n\n    ## Code Exploration Policy\n\n" + BLOCK + "# Mine\n"
    lines, span = _span(text)
    starts = [i for i, ln in enumerate(lines) if ln == "## Code Exploration Policy"]
    assert span == (starts[0], lines.index("# Mine")), (span, lines)


def test_an_unclosed_fence_runs_to_the_end_of_the_file():
    """Unchanged: a fence never closed hides every heading after it."""
    lines, span = _span(BLOCK + f"{TICKS}\n# a comment\n# Mine\n")
    assert span == (0, len(lines))
