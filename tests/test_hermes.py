"""Tests for the Hermes Agent plugin (jcodemunch_mcp.hermes).

These tests don't require Hermes Agent itself to be installed. They
fake the minimal ``ctx`` API Hermes hands to ``register()`` and
exercise:

1. Tool discovery via ``jcodemunch_mcp.server.list_tools()``
2. Schema conversion (MCP Tool -> Hermes tool schema with jcm_ prefix)
3. Handler round-trip for a cheap tool (``list_repos``)
4. Hook registration (all six hooks)
5. Read-guard detection of raw grep/cat commands
6. Edit-guard detection of raw write_file on code files
7. Index-hook background re-index scheduling after a successful write
8. Session context injector returning expected shape on first turn
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest


# --------------------------------------------------------------------------- #
# Fake Hermes ctx                                                             #
# --------------------------------------------------------------------------- #


class FakeCtx:
    """Minimal stand-in for the Hermes plugin registration context."""

    def __init__(self) -> None:
        self.tools: List[Dict[str, Any]] = []
        self.hooks: List[Tuple[str, Any]] = []

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: Dict[str, Any],
        handler: Any,
        check_fn: Any = None,
    ) -> None:
        self.tools.append(
            {
                "name": name,
                "toolset": toolset,
                "schema": schema,
                "handler": handler,
                "check_fn": check_fn,
            }
        )

    def register_hook(self, event: str, callback: Any) -> None:
        self.hooks.append((event, callback))


@pytest.fixture
def ctx() -> FakeCtx:
    return FakeCtx()


# --------------------------------------------------------------------------- #
# 1 + 2 + 4: registration                                                     #
# --------------------------------------------------------------------------- #


def test_register_discovers_tools_and_hooks(ctx: FakeCtx) -> None:
    from jcodemunch_mcp import hermes

    hermes.register(ctx)

    # We should have discovered the full jcodemunch toolset. 40+ is a
    # conservative lower bound; the exact number depends on which
    # optional features are enabled in the install.
    assert len(ctx.tools) >= 40, (
        f"expected 40+ tools, got {len(ctx.tools)}"
    )

    # Every registered tool must have the jcm_ prefix.
    for t in ctx.tools:
        assert t["name"].startswith("jcm_"), t["name"]
        assert t["toolset"] == "jcodemunch"
        assert callable(t["handler"])

        schema = t["schema"]
        assert schema["name"] == t["name"]
        assert "description" in schema
        assert "parameters" in schema
        # Internal keys must be stripped before handing to Hermes.
        assert "_jcm_original_name" not in schema

    # A few tools we know jcodemunch always ships.
    names = {t["name"] for t in ctx.tools}
    for required in ("jcm_list_repos", "jcm_search_symbols",
                     "jcm_get_symbol_source", "jcm_index_folder"):
        assert required in names, f"{required} missing from {len(names)} tools"

    # All six hooks should be registered.
    events = [e for e, _ in ctx.hooks]
    assert events.count("pre_tool_call") == 2, events  # read_guard + edit_guard
    assert events.count("post_tool_call") == 1, events
    assert events.count("pre_llm_call") == 1, events
    assert events.count("on_session_start") == 1, events
    assert events.count("on_session_end") == 1, events


def test_register_respects_disable_hooks_env(ctx: FakeCtx, monkeypatch) -> None:
    monkeypatch.setenv("JCODEMUNCH_HERMES_DISABLE_HOOKS", "1")

    from jcodemunch_mcp import hermes

    hermes.register(ctx)

    assert len(ctx.tools) >= 40
    assert ctx.hooks == []


# --------------------------------------------------------------------------- #
# 3: handler round-trip                                                       #
# --------------------------------------------------------------------------- #


def test_jcm_list_repos_handler_roundtrip(ctx: FakeCtx) -> None:
    from jcodemunch_mcp import hermes

    hermes.register(ctx)

    target = next(t for t in ctx.tools if t["name"] == "jcm_list_repos")
    result = target["handler"]({})

    assert isinstance(result, str)
    parsed = json.loads(result)
    # jcodemunch's list_repos returns {count, repos, _meta}. Empty is
    # fine; we just need a valid shape.
    assert "repos" in parsed, parsed


# --------------------------------------------------------------------------- #
# 5: read guard                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command,expected_hit",
    [
        ("grep -r 'TODO' src/app.py", True),
        ("rg --files-with-matches 'import React' src/*.tsx", True),
        ("cat src/main.py | head -50", True),
        ("find . -name '*.py' -exec grep foo {} +", True),
        ("npm test", False),
        ("pytest tests/test_foo.py", False),
        ("git log --oneline", False),
        ("cargo build", False),
        ("docker compose up -d", False),
    ],
)
def test_read_guard_detects_raw_exploration(command: str, expected_hit: bool) -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = f"test-read-{command[:10]}"
    hooks._reset_session(session_id)

    hooks.read_guard(
        tool_name="terminal",
        args={"command": command},
        task_id=session_id,
    )

    hits = hooks._drain_read_hits(session_id)
    assert bool(hits) == expected_hit, (command, hits)


def test_read_guard_ignores_jcm_tools() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-ignore-jcm"
    hooks._reset_session(session_id)

    hooks.read_guard(
        tool_name="jcm_search_symbols",
        args={"query": "foo"},
        task_id=session_id,
    )

    assert hooks._drain_read_hits(session_id) == []


def test_read_guard_ignores_search_files_on_non_code_globs() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-ignore-non-code-search-files"
    hooks._reset_session(session_id)

    hooks.read_guard(
        tool_name="search_files",
        args={"pattern": "Hermes", "path": ".", "file_glob": "*.md"},
        task_id=session_id,
    )

    assert hooks._drain_read_hits(session_id) == []


# --------------------------------------------------------------------------- #
# 6: edit guard                                                               #
# --------------------------------------------------------------------------- #


def test_edit_guard_flags_code_writes() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-edit-code"
    hooks._reset_session(session_id)

    hooks.edit_guard(
        tool_name="write_file",
        args={"path": "/repo/src/app.py", "content": "print('x')"},
        task_id=session_id,
    )
    hooks.edit_guard(
        tool_name="patch",
        args={"path": "/repo/src/main.ts", "old_string": "a", "new_string": "b"},
        task_id=session_id,
    )

    hits = hooks._drain_edit_hits(session_id)
    assert len(hits) == 2
    assert any("src/app.py" in h for h in hits)
    assert any("src/main.ts" in h for h in hits)


def test_edit_guard_ignores_non_code_files() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-edit-non-code"
    hooks._reset_session(session_id)

    hooks.edit_guard(
        tool_name="write_file",
        args={"path": "/tmp/notes.txt", "content": "hi"},
        task_id=session_id,
    )
    hooks.edit_guard(
        tool_name="write_file",
        args={"path": "/tmp/README.md", "content": "# readme"},
        task_id=session_id,
    )

    assert hooks._drain_edit_hits(session_id) == []


def test_edit_guard_respects_allow_raw_write_env(monkeypatch) -> None:
    from jcodemunch_mcp.hermes import hooks

    monkeypatch.setenv("JCODEMUNCH_HERMES_ALLOW_RAW_WRITE", "1")
    session_id = "test-edit-allow"
    hooks._reset_session(session_id)

    hooks.edit_guard(
        tool_name="write_file",
        args={"path": "/repo/src/app.py", "content": "x"},
        task_id=session_id,
    )

    assert hooks._drain_edit_hits(session_id) == []


# --------------------------------------------------------------------------- #
# 7: index hook                                                               #
# --------------------------------------------------------------------------- #


def test_index_hook_schedules_reindex_on_code_write(monkeypatch) -> None:
    from jcodemunch_mcp.hermes import hooks

    # Write a tiny fixture file so os.path.exists is happy.
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "foo.py"
        target.write_text("def foo():\n    return 1\n")

        scheduled: List[Dict[str, Any]] = []

        def fake_run_async_background(coro: Any) -> None:
            # We don't want to actually exercise jcodemunch's async
            # indexing inside a unit test — capturing the call is
            # enough to verify the hook's dispatch logic.
            scheduled.append({"coro": coro})
            # Close the coroutine so it doesn't warn about being
            # never awaited.
            try:
                coro.close()
            except Exception:
                pass

        monkeypatch.setattr(
            hooks, "run_async_background", fake_run_async_background
        )

        hooks.index_hook(
            tool_name="write_file",
            args={"path": str(target), "content": "def foo(): return 2"},
            result=json.dumps({"bytes_written": 42}),
            task_id="test-index",
        )

        assert len(scheduled) == 1


def test_index_hook_skips_errored_writes(monkeypatch) -> None:
    from jcodemunch_mcp.hermes import hooks

    called = False

    def fake_run_async_background(coro: Any) -> None:
        nonlocal called
        called = True
        try:
            coro.close()
        except Exception:
            pass

    monkeypatch.setattr(
        hooks, "run_async_background", fake_run_async_background
    )

    hooks.index_hook(
        tool_name="write_file",
        args={"path": "/tmp/whatever.py", "content": "x"},
        result=json.dumps({"error": "permission denied"}),
        task_id="test-index-err",
    )

    assert called is False


def test_index_hook_skips_non_edit_tools(monkeypatch) -> None:
    from jcodemunch_mcp.hermes import hooks

    called = False

    def fake_run_async_background(coro: Any) -> None:
        nonlocal called
        called = True
        try:
            coro.close()
        except Exception:
            pass

    monkeypatch.setattr(
        hooks, "run_async_background", fake_run_async_background
    )

    hooks.index_hook(
        tool_name="read_file",
        args={"path": "/tmp/x.py"},
        result=json.dumps({"content": "x"}),
        task_id="test-index-noop",
    )

    assert called is False


# --------------------------------------------------------------------------- #
# 8: session context injector                                                 #
# --------------------------------------------------------------------------- #


def test_session_context_injector_first_turn_injects_guidance() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-injector"
    hooks._reset_session(session_id)

    result = hooks.session_context_injector(
        session_id=session_id,
        user_message="help me understand this repo",
        conversation_history=[],
        is_first_turn=True,
        model="anthropic/claude-sonnet-4.6",
        platform="cli",
    )

    assert isinstance(result, dict)
    ctx_text = result.get("context", "")
    # The strengthened guidance should include the PRIORITY header,
    # the substitution table, and concrete tool names.
    assert "PRIORITY INSTRUCTION" in ctx_text
    assert "jcm_search_symbols" in ctx_text
    assert "jcm_get_symbol_source" in ctx_text
    # And the non-code scope carve-out so the model doesn't try to
    # use jcm on README/Rules/*.md files.
    assert "Non-code files" in ctx_text or "README" in ctx_text


def test_session_context_injector_subsequent_turn_injects_short_reminder() -> None:
    """A subsequent turn with no violations should still inject a
    one-liner — the guidance was too easy to drop out of context on
    long sessions under the old 'first turn only' design."""
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-injector-quiet"
    hooks._reset_session(session_id)

    result = hooks.session_context_injector(
        session_id=session_id,
        user_message="what about this file",
        conversation_history=[{"role": "user", "content": "hi"}],
        is_first_turn=False,
        model="anthropic/claude-sonnet-4.6",
        platform="cli",
    )

    assert isinstance(result, dict)
    assert "jcodemunch plugin is active" in result.get("context", "")


def test_session_context_injector_first_turn_quiet_with_no_repos_still_injects() -> None:
    """Even if list_repos returns empty, first turn must still inject
    the guidance — it's the main steering mechanism."""
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-injector-first-turn"
    hooks._reset_session(session_id)

    result = hooks.session_context_injector(
        session_id=session_id,
        user_message="look at this code",
        conversation_history=[],
        is_first_turn=True,
        model="anthropic/claude-sonnet-4.6",
        platform="cli",
    )

    assert isinstance(result, dict)
    assert "PRIORITY INSTRUCTION" in result["context"]


def test_session_context_injector_surfaces_read_hits_next_turn() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-injector-hits"
    hooks._reset_session(session_id)

    # Simulate a read-guard hit during the previous turn.
    hooks.read_guard(
        tool_name="terminal",
        args={"command": "grep -r TODO src/app.py"},
        task_id=session_id,
    )

    result = hooks.session_context_injector(
        session_id=session_id,
        user_message="next question",
        conversation_history=[{"role": "user", "content": "hi"}],
        is_first_turn=False,
        model="anthropic/claude-sonnet-4.6",
        platform="cli",
    )

    assert isinstance(result, dict)
    ctx_text = result.get("context", "")
    assert "jcm_search_symbols" in ctx_text or "jcm_search_text" in ctx_text
    assert "grep" in ctx_text


def test_session_context_injector_surfaces_read_hits_when_task_id_differs_from_session_id() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-injector-read-task-mismatch"
    task_id = "aa97471d-d837-49bd-8921-ef840282fa9c"
    hooks._reset_session(session_id)
    hooks.on_session_start(
        session_id=session_id, model="gpt-5.3-codex", platform="cli"
    )

    hooks.read_guard(
        tool_name="terminal",
        args={"command": "grep -r TODO src/app.py"},
        task_id=task_id,
    )

    result = hooks.session_context_injector(
        session_id=session_id,
        user_message="next question",
        conversation_history=[{"role": "user", "content": "hi"}],
        is_first_turn=False,
        model="gpt-5.3-codex",
        platform="cli",
    )

    assert isinstance(result, dict)
    ctx_text = result.get("context", "")
    assert "jcm_search_symbols" in ctx_text or "jcm_search_text" in ctx_text
    assert "grep" in ctx_text

    hooks.on_session_end(
        session_id=session_id, completed=True, interrupted=False,
        model="gpt-5.3-codex", platform="cli",
    )


def test_session_context_injector_surfaces_edit_hits_next_turn() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-injector-edit-hits"
    hooks._reset_session(session_id)

    hooks.edit_guard(
        tool_name="write_file",
        args={"path": "/repo/src/app.py", "content": "x"},
        task_id=session_id,
    )

    result = hooks.session_context_injector(
        session_id=session_id,
        user_message="next question",
        conversation_history=[{"role": "user", "content": "hi"}],
        is_first_turn=False,
        model="anthropic/claude-sonnet-4.6",
        platform="cli",
    )

    assert isinstance(result, dict)
    ctx_text = result.get("context", "")
    assert "jcm_get_blast_radius" in ctx_text
    assert "src/app.py" in ctx_text


def test_session_context_injector_surfaces_edit_hits_when_task_id_differs_from_session_id() -> None:
    from jcodemunch_mcp.hermes import hooks

    session_id = "test-injector-edit-task-mismatch"
    task_id = "cc56789f-1111-4222-bbbb-444455556666"
    hooks._reset_session(session_id)
    hooks.on_session_start(
        session_id=session_id, model="gpt-5.3-codex", platform="cli"
    )

    hooks.edit_guard(
        tool_name="write_file",
        args={"path": "/repo/src/app.py", "content": "x"},
        task_id=task_id,
    )

    result = hooks.session_context_injector(
        session_id=session_id,
        user_message="next question",
        conversation_history=[{"role": "user", "content": "hi"}],
        is_first_turn=False,
        model="gpt-5.3-codex",
        platform="cli",
    )

    assert isinstance(result, dict)
    ctx_text = result.get("context", "")
    assert "jcm_get_blast_radius" in ctx_text
    assert "src/app.py" in ctx_text

    hooks.on_session_end(
        session_id=session_id, completed=True, interrupted=False,
        model="gpt-5.3-codex", platform="cli",
    )


# --------------------------------------------------------------------------- #
# 9: file-based debug logging                                                 #
# --------------------------------------------------------------------------- #


def test_register_creates_debug_log_file(ctx: FakeCtx, tmp_path, monkeypatch) -> None:
    """The plugin must create ~/.hermes/plugins/jcodemunch/logs/debug.log
    (or the JCODEMUNCH_HERMES_LOG_DIR override) on registration so
    operators can confirm hooks are firing without digging through
    Hermes' central log."""
    from jcodemunch_mcp.hermes import _logging as plugin_logging
    from jcodemunch_mcp import hermes

    monkeypatch.setenv("JCODEMUNCH_HERMES_LOG_DIR", str(tmp_path))
    # Force a reconfigure because tests may share logging state.
    plugin_logging.configure_plugin_logging(force=True)

    hermes.register(ctx)

    log_file = tmp_path / "debug.log"
    assert log_file.exists(), f"expected debug.log at {log_file}"
    content = log_file.read_text(encoding="utf-8")
    assert "register() entered" in content
    assert "register_hooks: done" in content


def test_hook_fires_are_logged(tmp_path, monkeypatch) -> None:
    """Every hook entry should leave a visible line in the log so we
    can tell WHICH hooks Hermes is actually calling and when."""
    from jcodemunch_mcp.hermes import _logging as plugin_logging
    from jcodemunch_mcp.hermes import hooks

    monkeypatch.setenv("JCODEMUNCH_HERMES_LOG_DIR", str(tmp_path))
    plugin_logging.configure_plugin_logging(force=True)

    session_id = "test-logging-hooks"
    hooks._reset_session(session_id)

    hooks.on_session_start(
        session_id=session_id, model="anthropic/claude", platform="cli"
    )
    hooks.session_context_injector(
        session_id=session_id, user_message="hi",
        conversation_history=[], is_first_turn=True,
        model="anthropic/claude", platform="cli",
    )
    hooks.read_guard(
        tool_name="terminal",
        args={"command": "grep -r TODO src/app.py"},
        task_id=session_id,
    )
    hooks.edit_guard(
        tool_name="write_file",
        args={"path": "/repo/src/app.py", "content": "x"},
        task_id=session_id,
    )
    hooks.on_session_end(
        session_id=session_id, completed=True, interrupted=False,
        model="anthropic/claude", platform="cli",
    )

    # Flush the FileHandler so we can read it.
    import logging as _py_logging
    for h in _py_logging.getLogger("jcodemunch_mcp.hermes").handlers:
        try:
            h.flush()
        except Exception:
            pass

    log = (tmp_path / "debug.log").read_text(encoding="utf-8")
    assert "on_session_start fired" in log
    assert "pre_llm_call fired" in log
    assert "pre_llm_call INJECTED" in log
    assert "read_guard fired" in log
    assert "read_guard FLAGGED" in log
    assert "edit_guard fired" in log
    assert "edit_guard FLAGGED" in log
    assert "on_session_end fired" in log


def test_on_session_end_aggregates_counts_when_task_id_differs_from_session_id() -> None:
    """Regression test for the bug observed with gpt-5.3-codex: Hermes
    passes a per-tool-call UUID as ``task_id`` to pre_tool_call hooks,
    which is NOT the same value as the ``session_id`` passed to
    on_session_start/end. Without the aggregator, on_session_end was
    reporting total_tools=0 even though the guards had fired multiple
    times during the session."""
    from jcodemunch_mcp.hermes import hooks

    session_id = "20260405_210025_abc123"
    # Hermes gives read_guard/edit_guard a DIFFERENT identifier in
    # some providers — here we simulate the UUID that gpt-5.3-codex
    # actually passes.
    task_uuid_1 = "aa97471d-d837-49bd-8921-ef840282fa9c"
    task_uuid_2 = "bb12345e-ffff-4000-aaaa-111122223333"

    hooks._reset_session(session_id)
    hooks.on_session_start(
        session_id=session_id, model="gpt-5.3-codex", platform="cli"
    )

    # Three read_guard fires under the task UUIDs — these should be
    # attributed to the active session via the task→session mapping.
    hooks.read_guard(
        tool_name="jcm_find_references", args={}, task_id=task_uuid_1,
    )
    hooks.read_guard(
        tool_name="jcm_search_text", args={}, task_id=task_uuid_1,
    )
    hooks.read_guard(
        tool_name="jcm_search_symbols", args={}, task_id=task_uuid_2,
    )

    # The aggregator should sum across both task_ids for this session.
    counts = hooks._aggregate_counts_for_session(session_id)
    assert sum(counts.values()) == 3, f"expected 3 total, got {dict(counts)}"
    assert counts["jcm_find_references"] == 1
    assert counts["jcm_search_text"] == 1
    assert counts["jcm_search_symbols"] == 1

    # on_session_end should pick up the same totals.
    hooks.on_session_end(
        session_id=session_id, completed=True, interrupted=False,
        model="gpt-5.3-codex", platform="cli",
    )

    # After session end, state for this session must be fully cleared.
    assert hooks._aggregate_counts_for_session(session_id) == {}


def test_on_session_end_works_when_task_id_equals_session_id() -> None:
    """Original case: providers that pass the session identifier
    directly as task_id. The aggregator must still work in that
    simpler scenario so we don't regress existing behaviour."""
    from jcodemunch_mcp.hermes import hooks

    session_id = "simple-session"
    hooks._reset_session(session_id)
    hooks.on_session_start(
        session_id=session_id, model="claude", platform="cli"
    )

    # task_id == session_id (older-style providers)
    hooks.read_guard(
        tool_name="terminal",
        args={"command": "grep -r TODO src/app.py"},
        task_id=session_id,
    )
    hooks.read_guard(
        tool_name="jcm_search_symbols", args={}, task_id=session_id,
    )

    counts = hooks._aggregate_counts_for_session(session_id)
    assert sum(counts.values()) == 2
    assert counts["jcm_search_symbols"] == 1

    hooks.on_session_end(
        session_id=session_id, completed=True, interrupted=False,
        model="claude", platform="cli",
    )
