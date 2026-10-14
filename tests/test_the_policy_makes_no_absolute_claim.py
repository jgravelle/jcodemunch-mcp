"""The generated agent policy makes no absolute claim and demands no opening call.

The policy a user pastes into CLAUDE.md opened "Always use jCodemunch-MCP tools
for code navigation. Never fall back to Read, Grep, Glob, or Bash", required
`resolve_repo` at the start of every session and `plan_turn` as the "opening
move for any task", and printed model ids by version. Followed to the letter on
a small repository that wording costs turns: every call is another turn, and
the built-in search finds a known name in fewer.

Every surface that generates or serves that text is read here: both policy
variants, the rules files written for other editors, the installed skill, the
guide snippet and the MCP `workflow` prompt. What the text must still do is
pinned too: it names the relationship questions, in a line no tool filter can
drop, and each tool keeps a line of its own.
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
        "workflow prompt": server._WORKFLOW_PROMPT_TEXT,
        # (2026-10-14, owner's ruling) The MCP `instructions` string reaches every
        # session in every client, whatever policy is installed, and survives tool
        # deferral, so its "Prefer them over Read/Grep" outlived the policy's rewording.
        "full instructions": server._mcp_instructions("full"),
        "front-door instructions": server._mcp_instructions("counter"),
    }


SURFACES = sorted(_surfaces())

# A phrase and why it may not come back.
FORBIDDEN = {
    "an order to always use the index": re.compile(r"\bAlways use jCode", re.I),
    "a ban on the built-in tools": re.compile(r"\bNever fall back\b", re.I),
    "a ban on reading a file": re.compile(r"\bNever\s+`?Read`?\b"),
    "a call demanded at the start of every session": re.compile(r"Start any session", re.I),
    "a call demanded before any task": re.compile(r"Opening move|for any task", re.I),
    "a call demanded before any exploration": re.compile(r"Before any code exploration", re.I),
    "a call demanded before any file is opened": re.compile(r"before opening any file", re.I),
    "an order to always pass a model id": re.compile(r"always include", re.I),
    "a blanket instead-of": re.compile(r"instead of (raw )?(Grep|Read|built-in)|for all code navigation", re.I),
    "a blanket preference over the built-in tools": re.compile(r"\bPrefer (it|them|jcodemunch) over (Read|Grep)", re.I),
    "a call demanded first": re.compile(r"\bCall it first\b", re.I),
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


@pytest.mark.parametrize(
    "surface",
    [
        "full policy",
        "front-door policy",
        "skill",
        "windsurf rules",
        "cursor rules",
        "workflow prompt",
        "full instructions",
        "front-door instructions",
    ],
)
def test_the_policy_says_a_small_repository_favours_the_built_in_tools(surface):
    text = _surfaces()[surface]
    assert re.search(r"small repository", text), surface


# --- what the text must still do ------------------------------------------------ #

RELATIONSHIP_TOOLS = ["find_references", "check_references", "get_blast_radius", "get_call_hierarchy", "find_dead_code"]
LEAD = "Relationships are what the index is for"


def _bullets(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")]


@pytest.mark.parametrize("text", [policy._CLAUDE_MD_POLICY, policy._CLAUDE_MD_POLICY_COUNTER], ids=["full", "front door"])
def test_the_opening_names_the_relationship_questions(text):
    opening = text.split("**Before the first")[0]
    lead = next(b for b in _bullets(opening) if LEAD in b)
    for question in ("imports a symbol", "a name is used", "a change breaks", "calls a function", "unreachable"):
        assert question in lead, lead


@pytest.mark.parametrize("tool", RELATIONSHIP_TOOLS)
def test_the_full_policy_still_names_what_only_an_index_answers(tool):
    rows = [b for b in _bullets(policy._CLAUDE_MD_POLICY) if f"`{tool}`" in b]
    assert rows, tool
    for b in rows:
        others = [t for t in RELATIONSHIP_TOOLS if t != tool and f"`{t}`" in b]
        assert not others, f"one line names {tool} and {others}: a filter that drops one drops both"


@pytest.mark.parametrize("gone", RELATIONSHIP_TOOLS)
def test_no_tool_filter_drops_the_relationship_line(gone):
    """The filter removes any line naming an unavailable tool, so the lead names none."""
    active = set(server._CANONICAL_TOOL_NAMES) - {gone}
    filtered = policy._filter_policy_for_tools(policy._CLAUDE_MD_POLICY, active)
    assert f"`{gone}`" not in filtered
    assert LEAD in filtered
    for kept in RELATIONSHIP_TOOLS:
        if kept != gone:
            assert f"`{kept}`" in filtered, kept


def test_the_relationship_line_survives_the_core_profile(monkeypatch):
    monkeypatch.setattr(policy, "_effective_tool_surface", lambda: "full")
    monkeypatch.setattr(policy, "_get_active_tools", lambda: set(server._TOOL_TIER_CORE))
    served = policy.active_policy()
    assert LEAD in served and "built-in" in served and "small repository" in served


def test_the_usage_question_is_not_sent_to_the_import_tool():
    for text in _surfaces().values():
        for line in text.splitlines():
            if "find_references" in line and "check_references" not in line:
                assert not re.search(r"\b(references a symbol|is used|usage)\b", line), line


def test_the_opening_call_is_conditional_in_both_policies():
    for text in (policy._CLAUDE_MD_POLICY, policy._CLAUDE_MD_POLICY_COUNTER):
        heading = next(ln for ln in text.splitlines() if "resolve_repo" in ln or "Before the first" in ln)
        assert "Before the first" in heading, "the repository check is tied to the first index call, not the session"
        assert "skip it when the task needs none" in heading, heading


def test_the_skill_says_its_opening_steps_are_not_always_needed():
    text = skills._build_skill_content()
    section = text.split("### Before the first jCodemunch call")[1].split("###")[0]
    assert "Neither step is needed for a task the built-in tools already answer." in section


def test_plan_turn_is_offered_and_not_demanded():
    row = next(ln for ln in policy._CLAUDE_MD_POLICY.splitlines() if "`plan_turn {" in ln)
    assert "skip it when you already know where to look" in row, row
    assert "For a broad task" in policy._CLAUDE_MD_POLICY


def test_the_tiering_text_says_it_is_off_by_default():
    from jcodemunch_mcp import config

    assert config.DEFAULTS["adaptive_tiering"] is False
    section = policy._CLAUDE_MD_POLICY.split("## Model-Driven Tool Tiering")[1]
    assert "off unless `adaptive_tiering` is enabled" in section


def test_the_cursor_rules_written_for_a_filtered_policy_carry_the_same_frontmatter(tmp_path, monkeypatch):
    """It was written twice, and the copy used for a filtered policy was read by no test."""
    assert init._CURSOR_RULES_CONTENT == init._CURSOR_FRONTMATTER + policy._CLAUDE_MD_POLICY
    filtered = policy._filter_policy_for_tools(policy._CLAUDE_MD_POLICY, set(server._TOOL_TIER_CORE))
    assert filtered != policy._CLAUDE_MD_POLICY
    target = tmp_path / "rules" / "jcodemunch.mdc"
    monkeypatch.setattr(init, "_cursor_rules_path", lambda: target)
    monkeypatch.setattr(init, "active_policy", lambda: filtered)
    init.install_cursor_rules(backup=False)
    written = target.read_text(encoding="utf-8")
    assert written == init._CURSOR_FRONTMATTER + filtered
    for what, pattern in FORBIDDEN.items():
        assert pattern.search(written) is None, what


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
