"""MCP readOnlyHint annotations for Claude Code plan mode.

Claude Code's plan mode prompts for approval on every tool it cannot prove is
read-only. jcm is read-only by charter, so every tool now advertises
ToolAnnotations(readOnlyHint=...): the query tools are True (plan mode runs them
silently) and the handful that mutate index/session/config state are False (they
still prompt). This is a regression guard for that fix.

The write-set is derived from the authoritative counter.STATE_CHANGING_ACTIONS
(plus index_dependency, order, route) so it can't silently drift from source and
let a mutating tool masquerade as read-only.
"""

import jcodemunch_mcp.server as server
from jcodemunch_mcp import counter


# Minimum set of mutating tools the annotation logic MUST mark non-read-only.
# Driven off the authoritative counter.STATE_CHANGING_ACTIONS (which includes
# index_dependency as of v1.108.104) so this can't drift from source.
_EXPECTED_MIN_WRITE = counter.STATE_CHANGING_ACTIONS


def test_non_readonly_set_covers_authoritative_write_actions():
    """The annotation write-set must cover every state-changing action plus
    index_dependency. If counter.STATE_CHANGING_ACTIONS grows, this fails until
    _NON_READONLY_TOOLS is updated — a mutating tool must never default to
    read-only (which would make plan mode run it silently)."""
    assert _EXPECTED_MIN_WRITE <= server._NON_READONLY_TOOLS


def test_every_tool_has_a_readonly_hint():
    """Every tool surfaced by tools/list carries an explicit readOnlyHint so
    plan mode never has to guess."""
    tools = server._build_tools_list()
    assert tools, "expected a non-empty tool list"
    missing = [
        t.name
        for t in tools
        if t.annotations is None or t.annotations.read_only_hint is None
    ]
    assert not missing, f"tools missing readOnlyHint: {missing}"


def test_readonly_hint_matches_write_set():
    """readOnlyHint is True exactly for tools NOT in the write-set, and False
    for those in it. Guards against a future manual annotation diverging from
    _NON_READONLY_TOOLS."""
    tools = server._build_tools_list()
    for tool in tools:
        expected_readonly = tool.name not in server._NON_READONLY_TOOLS
        assert tool.annotations.read_only_hint is expected_readonly, (
            f"{tool.name}: readOnlyHint={tool.annotations.read_only_hint} "
            f"but expected {expected_readonly}"
        )


def test_representative_read_and_write_tools():
    """Spot-check named tools that must be present on the default surface:
    query tools read-only, index/edit tools mutating."""
    by_name = {t.name: t for t in server._build_tools_list()}

    for read_tool in ("get_symbol_source", "search_symbols", "search_text", "plan_turn"):
        assert read_tool in by_name, f"{read_tool} unexpectedly absent"
        assert by_name[read_tool].annotations.read_only_hint is True, (
            f"{read_tool} should be read-only"
        )

    # check_embedding_drift is annotation-only non-read-only (force=true re-pins
    # the canary) — a dual-mode tool NOT in STATE_CHANGING_ACTIONS but still marked
    # mutating for plan mode, matching jdoc/jdata (v1.108.110).
    for write_tool in (
        "index_folder", "register_edit", "index_dependency",
        "set_tool_tier", "check_embedding_drift",
    ):
        assert write_tool in by_name, f"{write_tool} unexpectedly absent"
        assert by_name[write_tool].annotations.read_only_hint is False, (
            f"{write_tool} should be marked mutating"
        )


def test_annotation_only_writers_not_in_state_changing_actions():
    """The annotation-only writers must stay OUT of counter.STATE_CHANGING_ACTIONS
    so the order() dispatcher doesn't force allow_state_change on their default
    read path, while still being marked non-read-only for plan mode."""
    assert server._ANNOTATION_ONLY_WRITERS <= server._NON_READONLY_TOOLS
    assert not (server._ANNOTATION_ONLY_WRITERS & counter.STATE_CHANGING_ACTIONS)
