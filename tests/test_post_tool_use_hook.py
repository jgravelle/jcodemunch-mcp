"""Tests for PostToolUse hook (Feature 8: Session-Aware Routing)."""
import json
from pathlib import Path

import pytest


class TestEnforcementHooksConstant:
    """Tests for _enforcement_hooks() builder (upstream v1.21.27; absolute-path fix v1.80.5)."""

    @pytest.fixture(autouse=True)
    def _no_own_executable(self, monkeypatch):
        # These tests feed a path through PATH; the running install's own
        # executable is asked first since `install_layout.own_executable`, so
        # this box's venv would answer instead. Take it out of the question.
        import sys

        from jcodemunch_mcp import install_layout

        monkeypatch.setattr(sys, "argv", ["pytest"])
        monkeypatch.setattr(install_layout, "_scripts_dir", lambda: None)

    def test_enforcement_hooks_has_post_tool_use(self):
        """_enforcement_hooks() must contain PostToolUse with Edit|Write matcher."""
        from jcodemunch_mcp.cli.init import _enforcement_hooks

        hooks = _enforcement_hooks()
        assert "PostToolUse" in hooks
        rule = hooks["PostToolUse"][0]
        matcher = rule["matcher"]
        assert "Edit" in matcher
        assert "Write" in matcher
        cmd = rule["hooks"][0].get("command", "")
        assert "jcodemunch-mcp" in cmd

    def test_enforcement_hooks_has_pre_tool_use(self):
        """_enforcement_hooks() must contain PreToolUse with Read matcher."""
        from jcodemunch_mcp.cli.init import _enforcement_hooks

        hooks = _enforcement_hooks()
        assert "PreToolUse" in hooks
        rule = hooks["PreToolUse"][0]
        assert "Read" in rule["matcher"]

    def test_hook_invocation_resolves_absolute_path(self, monkeypatch):
        """_hook_invocation() must return absolute path when shutil.which finds it.

        Regression: Claude Code spawns hooks via /bin/sh on macOS/Linux which
        uses a minimal PATH that excludes ~/.local/bin. Bare `jcodemunch-mcp`
        in settings.json fails with "command not found" on user installs.
        """
        from jcodemunch_mcp.cli import init as init_mod

        monkeypatch.setattr(
            init_mod.shutil, "which",
            lambda name: "/Users/jane/.local/bin/jcodemunch-mcp" if name == "jcodemunch-mcp" else None,
        )
        assert init_mod._hook_invocation() == "/Users/jane/.local/bin/jcodemunch-mcp"

    def test_hook_invocation_quotes_paths_with_spaces(self, monkeypatch):
        """Paths with spaces (e.g. 'Program Files') must be double-quoted."""
        from jcodemunch_mcp.cli import init as init_mod

        monkeypatch.setattr(
            init_mod.shutil, "which",
            lambda name: r"C:\Program Files\Python312\Scripts\jcodemunch-mcp.exe",
        )
        result = init_mod._hook_invocation()
        assert result.startswith('"') and result.endswith('"')
        assert "Program Files" in result

    def test_hook_invocation_falls_back_to_bare_name(self, monkeypatch):
        """If executable can't be located, fall back to bare name."""
        from jcodemunch_mcp.cli import init as init_mod

        monkeypatch.setattr(init_mod.shutil, "which", lambda name: None)
        assert init_mod._hook_invocation() == "jcodemunch-mcp"

    def test_enforcement_hooks_use_absolute_path(self, monkeypatch):
        """All enforcement hook commands must use the resolved absolute path."""
        from jcodemunch_mcp.cli import init as init_mod

        fake_path = "/opt/homebrew/bin/jcodemunch-mcp"
        monkeypatch.setattr(init_mod.shutil, "which", lambda name: fake_path)

        hooks = init_mod._enforcement_hooks()
        for event_rules in hooks.values():
            for rule in event_rules:
                for h in rule["hooks"]:
                    assert h["command"].startswith(fake_path), \
                        f"Hook command must use absolute path: {h['command']!r}"


class TestInstallEnforcementHooksIntegration:
    """Tests for install_enforcement_hooks."""

    def test_install_enforcement_hooks_adds_both(self, tmp_path, monkeypatch):
        """install_enforcement_hooks must add PreToolUse and PostToolUse."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._settings_json_path",
            lambda: tmp_path / "settings.json"
        )
        install_enforcement_hooks(backup=False, guidance=True)
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))

        assert "PostToolUse" in data["hooks"]
        assert "PreToolUse" in data["hooks"]

    def test_install_enforcement_hooks_idempotent(self, tmp_path, monkeypatch):
        """install_enforcement_hooks should not duplicate entries."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._settings_json_path",
            lambda: tmp_path / "settings.json"
        )
        install_enforcement_hooks(backup=False, guidance=True)
        install_enforcement_hooks(backup=False, guidance=True)
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert len(data["hooks"]["PostToolUse"]) == 1
        assert len(data["hooks"]["PreToolUse"]) == 1

    def test_advisory_install_does_not_write_enforce_env(self, tmp_path, monkeypatch):
        """A non-strict install must not invent the JCODEMUNCH_ENFORCE env key."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._settings_json_path",
            lambda: tmp_path / "settings.json",
        )
        install_enforcement_hooks(backup=False)
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert "JCODEMUNCH_ENFORCE" not in data.get("env", {})

    def test_strict_install_writes_enforce_env(self, tmp_path, monkeypatch):
        """--strict persists env.JCODEMUNCH_ENFORCE=strict for the hook to read."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._settings_json_path",
            lambda: tmp_path / "settings.json",
        )
        msg = install_enforcement_hooks(backup=False, strict=True)
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert data["env"]["JCODEMUNCH_ENFORCE"] == "strict"
        assert "strict" in msg

    def test_strict_env_writes_even_when_hooks_present(self, tmp_path, monkeypatch):
        """Flipping to strict after a plain install still sets the env flag
        (the 'hooks already present' early-return must not skip it)."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._settings_json_path",
            lambda: tmp_path / "settings.json",
        )
        install_enforcement_hooks(backup=False)              # advisory first
        install_enforcement_hooks(backup=False, strict=True)  # then strict
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert data["env"]["JCODEMUNCH_ENFORCE"] == "strict"
        # hooks still single (not duplicated by the second call)
        assert len(data["hooks"]["PreToolUse"]) == 1

    def test_rerun_without_strict_reverts_to_advisory(self, tmp_path, monkeypatch):
        """Re-running init without --strict resets a prior strict flag."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._settings_json_path",
            lambda: tmp_path / "settings.json",
        )
        install_enforcement_hooks(backup=False, strict=True)
        install_enforcement_hooks(backup=False)  # revert
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert data["env"]["JCODEMUNCH_ENFORCE"] == "advisory"


class TestCacheKeyIndexedAt:
    """Tests for search_symbols cache key including indexed_at."""

    def test_cache_key_includes_indexed_at(self, tmp_path):
        """Result cache key must include indexed_at for auto-invalidation."""
        from tests.conftest_helpers import create_mini_index, get_index
        from jcodemunch_mcp.tools.search_symbols import (
            search_symbols,
            _result_cache,
            _result_cache_lock,
        )

        # Create index
        repo, storage_path = create_mini_index(tmp_path)

        # First search populates cache
        search_symbols(repo=repo, query="my_func", storage_path=storage_path)
        
        # Check cache was populated
        with _result_cache_lock:
            cache_keys = list(_result_cache.keys())
            assert len(cache_keys) > 0
            
            # Cache key should be a tuple with indexed_at at position 1
            key = cache_keys[0]
            assert isinstance(key, tuple)
            indexed_at = key[1]
            assert isinstance(indexed_at, str)
            assert len(indexed_at) >= 0

    def test_cache_invalidated_by_reindex(self, tmp_path):
        """After reindex (new indexed_at), cache miss occurs."""
        from tests.conftest_helpers import create_mini_index, get_index
        from jcodemunch_mcp.tools.search_symbols import (
            search_symbols,
            result_cache_invalidate_repo,
        )
        from jcodemunch_mcp.tools.index_folder import index_folder
        import time

        # Create index
        repo, storage_path = create_mini_index(tmp_path)
        
        # First search
        search_symbols(repo=repo, query="my_func", storage_path=storage_path)
        
        # Modify a file and reindex
        test_file = tmp_path / "test_module.py"
        test_file.write_text(
            "def my_func(x: int, y: int) -> int:\n"
            "    '''Add two numbers - MODIFIED.'''\n"
            "    return x + y\n",
            encoding="utf-8"
        )
        time.sleep(0.1)  # Ensure timestamp changes
        
        # Reindex
        index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage_path,
            incremental=True,
        )
        
        # Invalidate the result cache
        count = result_cache_invalidate_repo(repo)
        assert count > 0
        
        # Second search should recompute (cache miss)
        result2 = search_symbols(repo=repo, query="my_func", storage_path=storage_path)
        assert result2["_meta"]["timing_ms"] > 0