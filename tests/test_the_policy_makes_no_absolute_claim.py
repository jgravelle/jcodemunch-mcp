"""The generated agent policy makes no absolute claim and demands no opening call.

The policy a user pastes into CLAUDE.md opened "Always use jCodemunch-MCP tools
for code navigation. Never fall back to Read, Grep, Glob, or Bash", required
`resolve_repo` at the start of every session and `plan_turn` as the "opening
move for any task", and printed model ids one generation old. Followed to the
letter on a small repository it costs more than the built-in tools it forbids:
every call is another turn, and the built-in search finds a known name in
fewer.

Every surface that carries that text is read here: both policy variants, the
rules files written for other editors, the installed skill and the guide
snippet. What the text must still do is pinned too: it names the questions an
index answers and a text search does not.
"""

from __future__ import annotations

import re

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.cli import init, policy, skills


def _surfaces() -> dict[str, str]:
    return {
        "full policy": policy._CLAUDE_MD_POLICY,
        "front-door policy": policy._CLAUDE_MD_POLICY_COUNTER,
        "cursor rules": init._CURSOR_RULES_CONTENT,
        "windsurf rules": init._WINDSURF_RULES_CONTENT,
        "skill": skills._build_skill_content(),
        "guide snippet": server._generate_claude_md_snippet(),
    }


SURFACES = sorted(_surfaces())

# A phrase and why it may not come back.
FORBIDDEN = {
    "an order to always use the index": re.compile(r"\bAlways use jCode", re.I),
    "a ban on the built-in tools": re.compile(r"\bNever fall back\b", re.I),
    "a ban on reading a file": re.compile(r"\bNever\s+`?Read`?\b"),
    "a call demanded at the start of every session": re.compile(r"Start any session", re.I),
    "a call demanded before any task": re.compile(r"Opening move", re.I),
    "a call demanded before any exploration": re.compile(r"Before any code exploration", re.I),
    "an order to always pass a model id": re.compile(r"always include", re.I),
    "a blanket instead-of": re.compile(r"instead of (raw )?(Grep|Read)", re.I),
    "a versioned model id": re.compile(r"\b(claude-(opus|sonnet|haiku)-\d|gpt-\d|gemini-\d)", re.I),
}


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("what", sorted(FORBIDDEN))
def test_no_surface_carries_the_absolute_wording(surface, what):
    text = _surfaces()[surface]
    found = FORBIDDEN[what].search(text)
    assert found is None, f"{surface} holds {what}: {text[max(0, found.start() - 60):found.end() + 60]!r}"


@pytest.mark.parametrize("surface", SURFACES)
def test_every_surface_names_the_built_in_tools_as_a_route(surface):
    assert "built-in" in _surfaces()[surface]


@pytest.mark.parametrize("surface", ["full policy", "front-door policy", "skill", "windsurf rules", "cursor rules"])
def test_the_policy_says_a_small_repository_favours_the_built_in_tools(surface):
    text = _surfaces()[surface]
    assert re.search(r"small repository", text), surface


@pytest.mark.parametrize("tool", ["find_references", "get_blast_radius", "get_call_hierarchy", "find_dead_code"])
def test_the_full_policy_still_names_what_only_an_index_answers(tool):
    opening = policy._CLAUDE_MD_POLICY.split("**Finding code:**")[0]
    assert f"`{tool}`" in opening, opening


def test_the_opening_call_is_conditional_in_both_policies():
    for text in (policy._CLAUDE_MD_POLICY, policy._CLAUDE_MD_POLICY_COUNTER):
        assert "resolve_repo" in text
        assert "Before the first" in text, "the repository check is tied to the first index call, not the session"


def test_plan_turn_is_offered_and_not_demanded():
    text = policy._CLAUDE_MD_POLICY
    assert "plan_turn" in text
    assert "For a broad task" in text


def test_the_marker_and_the_section_headings_are_unchanged():
    # `init` finds an installed block by the marker, and the drift check ends
    # the block at the first heading that is not one of these.
    assert policy.POLICY_MARKER == "## Code Exploration Policy"
    assert policy._policy_headings() == {
        "## Code Exploration Policy", "## Session-Aware Routing", "## Model-Driven Tool Tiering",
    }


def test_an_installed_copy_of_the_old_policy_reads_as_different():
    old = (
        "## Code Exploration Policy\n\nAlways use jCodemunch-MCP tools for code navigation. "
        "Never fall back to Read, Grep, Glob, or Bash for code exploration.\n"
    )
    verdict = policy.installed_policy_drift(old)
    assert verdict and verdict["state"] == "differs"
