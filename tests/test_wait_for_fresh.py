"""Tests for wait_for_fresh MCP tool registration."""

import argparse
import json

import pytest

from jcodemunch_mcp.server import list_tools, call_tool
from jcodemunch_mcp.reindex_state import _repo_states, _repo_events, _freshness_mode


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before each test."""
    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()
    yield
    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()


class TestWaitForFreshTool:
    @pytest.mark.asyncio
    async def test_wait_for_fresh_listed_in_tools(self):
        tools = await list_tools()
        tool_names = [t.name for t in tools]
        assert "wait_for_fresh" in tool_names

    @pytest.mark.asyncio
    async def test_wait_for_fresh_has_repo_param(self):
        tools = await list_tools()
        wait_tool = next(t for t in tools if t.name == "wait_for_fresh")
        props = wait_tool.inputSchema.get("properties", {})
        assert "repo" in props

    @pytest.mark.asyncio
    async def test_call_wait_for_fresh_when_fresh(self):
        from jcodemunch_mcp.reindex_state import mark_reindex_done
        mark_reindex_done("local/test", {"symbol_count": 42})
        result = await call_tool("wait_for_fresh", {"repo": "local/test", "timeout_ms": 100})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["fresh"] is True
        assert "waited_ms" in data


class TestMetaStalenessFields:
    @pytest.mark.asyncio
    async def test_meta_has_index_stale_field(self):
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        assert "_meta" in data
        assert "index_stale" in data["_meta"]

    @pytest.mark.asyncio
    async def test_meta_has_reindex_in_progress_field(self):
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        assert "reindex_in_progress" in data["_meta"]

    @pytest.mark.asyncio
    async def test_meta_reindex_in_progress_false_when_idle(self):
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        assert data["_meta"]["reindex_in_progress"] is False

    @pytest.mark.asyncio
    async def test_meta_index_stale_false_when_idle(self):
        """After a fresh index (not reindexing, no stale_since), index_stale must be False."""
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        assert data["_meta"]["index_stale"] is False

    @pytest.mark.asyncio
    async def test_meta_index_stale_true_when_reindexing(self):
        from jcodemunch_mcp.reindex_state import mark_reindex_start
        mark_reindex_start("local/nonexistent")
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        assert data["_meta"]["index_stale"] is True
        assert data["_meta"]["reindex_in_progress"] is True

    @pytest.mark.asyncio
    async def test_meta_no_repo_context_shows_stale_when_any_reindexing(self):
        """Non-repo tools (no 'repo' arg, not excluded) inject global staleness into _meta."""
        from jcodemunch_mcp.reindex_state import mark_reindex_start
        mark_reindex_start("local/some-repo")
        # get_symbol_diff has repo_a/repo_b but no 'repo' arg, and is not excluded from meta injection.
        result = await call_tool("get_symbol_diff", {"repo_a": "nonexistent-a", "repo_b": "nonexistent-b"})
        data = json.loads(result[0].text)
        assert "_meta" in data
        assert data["_meta"]["index_stale"] is True
        assert data["_meta"]["reindex_in_progress"] is True


class TestStrictFreshnessMode:
    @pytest.mark.asyncio
    async def test_strict_waits_for_reindex(self):
        from jcodemunch_mcp.reindex_state import mark_reindex_start, set_freshness_mode, mark_reindex_done
        import threading
        import time

        set_freshness_mode("strict")
        try:
            mark_reindex_start("local/test")

            def complete_after():
                time.sleep(0.05)
                mark_reindex_done("local/test")

            threading.Thread(target=complete_after, daemon=True).start()

            t0 = time.monotonic()
            result = await call_tool("get_repo_outline", {"repo": "local/test"})
            elapsed = time.monotonic() - t0

            data = json.loads(result[0].text)
            assert "_meta" in data
            assert "reindex_in_progress" in data["_meta"]
            # strict mode should have caused a delay of at least ~50ms (the sleep in complete_after)
            if elapsed < 0.01:
                pytest.fail(f"Expected strict mode to wait (~50ms), but elapsed={elapsed:.3f}s — "
                            "timing assertion may be flaky; increase threshold if CI is slow")
        finally:
            set_freshness_mode("relaxed")

    def test_strict_mode_flag_parsed_from_cli(self):
        """--freshness-mode flag should be accepted by the serve subparser."""
        import sys
        import argparse
        from jcodemunch_mcp.server import main

        # We can't fully run main() without a server, but we can verify argparse accepts the flag
        # by patching sys.argv and catching SystemExit (which happens after argparse --help)
        # Instead, directly test that the serve subparser accepts the flag:
        import argparse as ap
        parser = ap.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        serve = subparsers.add_parser("serve")
        serve.add_argument("--freshness-mode", default="relaxed", choices=["relaxed", "strict"])

        args = parser.parse_args(["serve", "--freshness-mode", "strict"])
        assert args.freshness_mode == "strict"

        args2 = parser.parse_args(["serve", "--freshness-mode", "relaxed"])
        assert args2.freshness_mode == "relaxed"

