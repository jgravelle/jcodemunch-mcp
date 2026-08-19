"""#507: the generated policies reconstructed the active tool set instead of asking.

`_get_active_tools` rebuilt the answer from `tool_profile` and the baked
`_PROFILE_TIERS`, while `tools/list` is built by `_build_tools_list()` from three
further inputs it never read:

1. the **session tier override** — `set_tool_tier`, and also `announce_model` via
   `resolve_model_to_tier`, so an agent that announces a small model and then
   reads the guide diverges without configuring anything;
2. `tool_tier_bundles`, which lets a user redefine what a tier contains;
3. the `languages` gate, which drops `search_columns` when SQL is off.

Two generators depend on it — `jcodemunch_guide` and the CLAUDE.md `init` writes
— so both could name tools `tools/list` does not carry. The `init` half is
written into the user's file and stays there.

⚠ The fix is to ask the builder rather than reproduce its logic. This is the
third instance of that shape in as many days: #495 (a second generator with its
own copy of the filter), #509 (a second call site with its own containment
check), and now a second *derivation* of the tool set.
"""

import re

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server
from jcodemunch_mcp.cli.init import _get_active_tools, active_policy


def _mounted():
    return {t.name for t in server._build_tools_list()}


def _guide_catalogue():
    snippet = server._generate_claude_md_snippet()
    if "### All tools" not in snippet:
        return set()
    return set(re.findall(r"`([a-z_0-9]+)`", snippet.split("### All tools", 1)[1]))


def _policy_names():
    policy = active_policy()
    return {n for n in server._CANONICAL_TOOL_NAMES if f"`{n}`" in policy}


@pytest.fixture
def cfg():
    c = config_module._GLOBAL_CONFIG
    keys = ("tool_profile", "tool_surface", "disabled_tools",
            "tool_tier_bundles", "languages")
    original = {k: c.get(k) for k in keys}
    yield c
    for k, v in original.items():
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v
    server._reset_session_tiers()


class TestNeitherGeneratorNamesAnUnmountedTool:
    """One property, four states. The generators must agree with `tools/list`
    in each, whatever decided it."""

    def test_a_baseline_is_the_control(self, cfg):
        cfg["tool_profile"] = "full"
        assert not (_guide_catalogue() - _mounted())
        assert not (_policy_names() - _mounted())

    def test_b_a_session_tier_override(self, cfg):
        """⚠ Needs no configuration at all. `announce_model` writes the session
        tier through `resolve_model_to_tier`, so an agent that announces a small
        model and then reads the guide arrives here without ever calling
        `set_tool_tier`. `jcodemunch_guide` is in `_ALWAYS_PRESENT_TOOLS`, so it
        stays reachable at every tier."""
        cfg["tool_profile"] = "full"
        server._set_session_tier("core")

        ghosts = _guide_catalogue() - _mounted()

        assert not ghosts, (
            f"the guide names {len(ghosts)} tools tools/list does not carry "
            f"under a session tier override: {sorted(ghosts)[:4]}"
        )

    def test_c_a_custom_tool_tier_bundle(self, cfg):
        """A user may redefine what a tier contains; the baked `_PROFILE_TIERS`
        does not know that."""
        cfg["tool_profile"] = "core"
        cfg["tool_tier_bundles"] = {"core": ["list_repos", "search_symbols"]}

        assert not (_guide_catalogue() - _mounted())
        assert not (_policy_names() - _mounted()), (
            "the CLAUDE.md written into the user's project names unmounted tools"
        )

    def test_d_the_languages_gate(self, cfg):
        """`search_columns` leaves the tool list when SQL is gated off, and no
        tier or disabled_tools entry says so."""
        cfg["tool_profile"] = "full"
        cfg["languages"] = ["python"]

        mounted = _mounted()
        assert "search_columns" not in mounted, "precondition: the gate applies"
        assert "search_columns" not in _guide_catalogue()
        assert "search_columns" not in _policy_names()


class TestTheSafeDirection:
    """⚠ Filtering is a subtraction, so a wrong answer here removes guidance.
    An unanswerable question must not empty the policy."""

    def test_an_empty_build_does_not_filter_everything(self, cfg, monkeypatch):
        monkeypatch.setattr(server, "_build_tools_list", lambda: [])
        assert _get_active_tools() is None, (
            "an empty tool list filtered the policy instead of leaving it alone"
        )

    def test_a_failing_build_does_not_filter_everything(self, cfg, monkeypatch):
        def boom():
            raise RuntimeError("no")

        monkeypatch.setattr(server, "_build_tools_list", boom)
        assert _get_active_tools() is None

    def test_the_policy_still_has_a_workflow_when_the_build_fails(
        self, cfg, monkeypatch
    ):
        """`None` means 'do not filter', so the policy survives whole."""
        monkeypatch.setattr(server, "_build_tools_list", lambda: [])
        assert "`search_symbols`" in active_policy()


class TestItAsksRatherThanReconstructs:
    def test_the_helper_reads_the_builder(self):
        """⚠ Checked over the AST, not the source text.

        The first version of this test matched the literal string
        `_PROFILE_TIERS` and failed on the COMMENT that explains why the helper
        must not use it — a guard that cannot tell prose from code. The same
        mistake the `src.` twin-import guard had to fix. `ast` does not see
        comments at all, so the question it answers is the intended one:
        does this function REFERENCE that table?
        """
        import ast
        import inspect
        import textwrap

        from jcodemunch_mcp.cli import init as init_mod

        tree = ast.parse(
            textwrap.dedent(inspect.getsource(init_mod._get_active_tools))
        )
        referenced = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        assert "_build_tools_list" in referenced, (
            "the active set is being reconstructed rather than read from the "
            "function that builds tools/list"
        )
        assert "_PROFILE_TIERS" not in referenced, (
            "reconstructing from the baked tier table is what #507 was: it "
            "cannot see session overrides, custom bundles or the language gate"
        )
