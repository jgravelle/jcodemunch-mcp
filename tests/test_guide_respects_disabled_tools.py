"""#495: the guide advertised a tool the same process refuses to run.

`_generate_claude_md_snippet` built its `### All tools` list from a static
constant without consulting `disabled_tools`. `disabled_tools` ships as
`["test_summarizer"]`, so **at shipped defaults** — no config file, no env
overrides — the guide named a tool `call_tool` then rejected before the handler
ran. An agent reads the name, calls it, gets an error.

⚠⚠ The filtering already existed and a SECOND generator walked around it.
Commit `e086e9a` ("claude-md respects tool_profile and disabled_tools", #242)
added it to `cli/init.py`, which is why the CLI policy path filters correctly.
This function is the other generator and never received it. The fix reuses
`_get_active_tools` rather than adding a third copy — a copy is how the two
drifted apart.
"""

import re

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server


def _advertised(snippet):
    """Every tool name the guide presents as callable, ANYWHERE in it.

    ⚠⚠ #506: this used to split on `### All tools` and inspect only what
    followed, so it could not see `### Quick start` — which #495's fix left
    unfiltered, and which went on recommending a disabled tool. **A helper
    scoped to the section the report happened to name cannot catch the section
    it did not.** Scanning the whole document means a section added later is
    covered on the commit that adds it.
    """
    return set(re.findall(r"`([a-z_0-9]+)`", snippet))


def _quick_start(snippet):
    """Just the `### Quick start` block, for assertions specific to it."""
    if "### Quick start" not in snippet:
        return ""
    body = snippet.split("### Quick start", 1)[1]
    nxt = re.search(r"\n### ", body)
    return body[: nxt.start()] if nxt else body


def _mounted():
    return {t.name for t in server._build_tools_list()}


@pytest.fixture
def cfg():
    """Direct access to the global config, restored after each test."""
    c = config_module._GLOBAL_CONFIG
    keys = ("disabled_tools", "tool_profile", "tool_surface")
    original = {k: c.get(k) for k in keys}
    yield c
    for k, v in original.items():
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v


class TestTheGuideMatchesWhatDispatchAccepts:
    def test_a_disabled_tool_is_not_advertised(self, cfg):
        cfg["disabled_tools"] = ["test_summarizer"]
        cfg["tool_profile"] = "full"

        advertised = _advertised(server._generate_claude_md_snippet())

        assert "test_summarizer" not in advertised, (
            "the guide advertises a tool call_tool rejects before the handler runs"
        )

    def test_this_is_the_shipped_default_not_a_configuration(self):
        """⚠ The defect needed no configuration to reach. If this default ever
        changes the issue's premise changes with it, so pin it here rather than
        leaving the test above looking like a contrived case."""
        from jcodemunch_mcp.config import DEFAULTS

        assert DEFAULTS["disabled_tools"] == ["test_summarizer"]

    def test_no_advertised_tool_is_unmounted(self, cfg):
        """The general property, not the one instance."""
        cfg["disabled_tools"] = ["test_summarizer", "find_dead_code"]
        cfg["tool_profile"] = "full"

        ghosts = _advertised(server._generate_claude_md_snippet()) - _mounted()

        assert not ghosts, f"guide advertises unmounted tools: {sorted(ghosts)}"

    def test_an_ordinary_tool_is_filtered_too(self, cfg):
        """`test_summarizer` is disabled by default, so fixing only that name
        would pass the first test. A second, ordinary tool proves the filter is
        general."""
        cfg["disabled_tools"] = ["find_dead_code"]
        cfg["tool_profile"] = "full"

        advertised = _advertised(server._generate_claude_md_snippet())

        assert "find_dead_code" not in advertised
        assert "search_symbols" in advertised, "the filter removed too much"


class TestWhatMustNotChange:
    def test_nothing_disabled_lists_everything(self, cfg):
        """Control: with the filter satisfied, the guide is unchanged."""
        cfg["disabled_tools"] = []
        cfg["tool_profile"] = "full"

        advertised = _advertised(server._generate_claude_md_snippet())

        assert "test_summarizer" in advertised
        assert not (advertised - _mounted())

    def test_the_snippet_keeps_its_shape(self, cfg):
        cfg["disabled_tools"] = ["test_summarizer"]
        cfg["tool_profile"] = "full"

        snippet = server._generate_claude_md_snippet()

        assert snippet.startswith("## jcodemunch-mcp (v")
        assert "### Quick start" in snippet
        assert "### All tools" in snippet
        assert "Never fall back to Grep, Read, or Glob" in snippet

    def test_no_category_is_left_empty(self, cfg):
        """A bare `**Search:**` with nothing after it reads as a surface with no
        tools in it. Emptied categories are dropped whole."""
        cfg["disabled_tools"] = list(server._CANONICAL_TOOL_NAMES)
        cfg["tool_profile"] = "full"

        snippet = server._generate_claude_md_snippet()

        for line in snippet.splitlines():
            if line.startswith("**") and line.endswith(":**"):
                pytest.fail(f"category header with no tools: {line!r}")
            if re.match(r"^\*\*[^*]+:\*\*\s*$", line):
                pytest.fail(f"category header with no tools: {line!r}")


class TestOneFilterNotThree:
    """The defect was two generators and one filter. A third copy would be the
    same bug again."""

    def test_the_guide_uses_the_cli_helper(self):
        import inspect

        source = inspect.getsource(server._generate_claude_md_snippet)
        assert "_get_active_tools" in source, (
            "the guide should reuse cli.init._get_active_tools, not re-derive "
            "the active set — a second copy is how #242's fix was missed here"
        )

    def test_the_two_generators_agree(self, cfg):
        """⚠ The reporter's diagnostic, as a test: one process, one config, and
        the two paths must not disagree about a tool's availability."""
        from jcodemunch_mcp.cli.init import active_policy

        cfg["disabled_tools"] = ["test_summarizer", "find_dead_code"]
        cfg["tool_profile"] = "full"

        advertised = _advertised(server._generate_claude_md_snippet())
        cli_text = active_policy()

        for name in ("test_summarizer", "find_dead_code"):
            in_guide = name in advertised
            in_cli = f"`{name}`" in cli_text
            assert in_guide == in_cli is False, (
                f"{name}: guide={in_guide} cli_policy={in_cli} — the two "
                "generators disagree on one config in one process"
            )


class TestQuickStartIsFilteredToo:
    """#506: `### Quick start` was six fixed strings that no filter reached.

    ⚠ Reported by @rknighton against his own #495 — that report's reproduction
    and acceptance criteria addressed `### All tools`, the fix landed there, and
    this is the section it did not test.
    """

    QUICK_START_TOOLS = (
        "list_repos", "index_folder", "index_repo",
        "search_symbols", "get_context_bundle", "search_text",
    )

    @pytest.mark.parametrize("tool", QUICK_START_TOOLS)
    def test_a_disabled_quick_start_tool_is_not_recommended(self, cfg, tool):
        """Every name Quick Start uses, not just the reported one — none of the
        six is in `_UNDISABLEABLE_TOOLS`, so any can be disabled."""
        cfg["disabled_tools"] = [tool]
        cfg["tool_profile"] = "full"

        assert f"`{tool}`" not in _quick_start(
            server._generate_claude_md_snippet()
        ), f"Quick start still instructs the caller to run {tool!r}"

    def test_nothing_disabled_leaves_quick_start_intact(self, cfg):
        cfg["disabled_tools"] = []
        cfg["tool_profile"] = "full"

        block = _quick_start(server._generate_claude_md_snippet())

        for tool in self.QUICK_START_TOOLS:
            assert f"`{tool}`" in block
        assert "1. " in block and "4. " in block

    def test_the_remaining_steps_are_renumbered(self, cfg):
        """Dropping a step must not leave a gap in the numbering."""
        cfg["disabled_tools"] = ["search_symbols"]
        cfg["tool_profile"] = "full"

        block = _quick_start(server._generate_claude_md_snippet())
        numbers = [int(m) for m in re.findall(r"^(\d+)\. ", block, re.M)]

        assert numbers == list(range(1, len(numbers) + 1)), (
            f"quick-start numbering has a gap: {numbers}"
        )

    def test_the_continuation_line_drops_only_what_is_disabled(self, cfg):
        """`index_folder` and `index_repo` share one continuation line; losing
        one must not orphan the line or take the other with it."""
        cfg["disabled_tools"] = ["index_repo"]
        cfg["tool_profile"] = "full"

        block = _quick_start(server._generate_claude_md_snippet())

        assert "`index_folder`" in block
        assert "`index_repo`" not in block
        assert "If not:" in block, "the continuation line was dropped entirely"

    def test_the_continuation_line_goes_when_both_are_disabled(self, cfg):
        cfg["disabled_tools"] = ["index_folder", "index_repo"]
        cfg["tool_profile"] = "full"

        block = _quick_start(server._generate_claude_md_snippet())

        assert "If not:" not in block, (
            "an empty 'If not:' line survived with nothing to offer"
        )
        assert "`list_repos`" in block, "the step itself should remain"
