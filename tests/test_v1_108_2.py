"""v1.108.2 — bound GitBlameProvider history walk + short-circuit git-root probe.

Two real bugs reported by @MariusAdrian88 on issue #294:

1. ``index_folder`` called ``detect_git_root()`` unconditionally, then *only
   afterwards* checked the ``git_root_identity`` config — so operators who
   set ``git_root_identity: false`` still paid the cost of a git subprocess
   on every reindex. Fix: gate the probe on the config first.

2. ``GitBlameProvider.load()`` ran ``git log --name-only`` over the entire
   commit history with a 30-second timeout. On legacy repos with hundreds
   of thousands of commits this was the real timeout-source — the MCP
   client's 30-second request budget expired before the subprocess did.
   Fix: bound by ``-n 20000`` commits and ``--since=2.years.ago``, tighten
   the wall-clock cap to 10s, expose ``git_blame_enabled`` config to skip
   entirely.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest


# --------------------------------------------------------------------------- #
# Bug 1 — git_root_identity short-circuit                                     #
# --------------------------------------------------------------------------- #

class TestGitRootProbeShortCircuit:
    def test_probe_skipped_when_identity_false(self, tmp_path: Path, monkeypatch):
        """When git_root_identity=false, detect_git_root must NOT be called."""
        # Make sure the folder is not a git working tree at the path level —
        # the probe should be skipped regardless.
        (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")

        from jcodemunch_mcp import config as _config_module
        original = dict(_config_module._GLOBAL_CONFIG)
        try:
            _config_module._GLOBAL_CONFIG["git_root_identity"] = False
            with mock.patch(
                "jcodemunch_mcp.storage.git_root.detect_git_root",
                return_value=None,
            ) as m_probe:
                from jcodemunch_mcp.tools.index_folder import index_folder
                result = index_folder(
                    path=str(tmp_path),
                    use_ai_summaries=False,
                    incremental=False,
                )
                assert result.get("success") is True, result
                # The retarget block must NOT have called the probe.
                # (The earlier `_resolve_repo_identity` path already gates on
                # the config, so total calls should be zero on this code path.)
                assert m_probe.call_count == 0, (
                    f"detect_git_root() was called {m_probe.call_count} time(s) "
                    "even though git_root_identity is false"
                )
        finally:
            _config_module._GLOBAL_CONFIG.clear()
            _config_module._GLOBAL_CONFIG.update(original)

    def test_probe_runs_when_identity_true(self, tmp_path: Path):
        """The probe must still run when the knob is on (preserve v1.96 behavior)."""
        (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")

        with mock.patch(
            "jcodemunch_mcp.storage.git_root.detect_git_root",
            return_value=None,
        ) as m_probe:
            from jcodemunch_mcp.tools.index_folder import index_folder
            result = index_folder(
                path=str(tmp_path),
                use_ai_summaries=False,
                incremental=False,
            )
            assert result.get("success") is True, result
            # Default-on means at least one probe call (from the retarget block;
            # _resolve_repo_identity may add another).
            assert m_probe.call_count >= 1


# --------------------------------------------------------------------------- #
# Bug 2 — GitBlameProvider bounded walk                                       #
# --------------------------------------------------------------------------- #

class TestGitBlameBounds:
    def test_subprocess_uses_history_bounds(self, tmp_path: Path):
        """load() must pass -n and --since to bound the walk."""
        from jcodemunch_mcp.parser.context.git_blame import (
            GIT_BLAME_COMMIT_LIMIT,
            GIT_BLAME_SINCE,
            GIT_BLAME_TIMEOUT_S,
            GitBlameProvider,
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            GitBlameProvider().load(tmp_path)

        cmd = captured["cmd"]
        assert "-n" in cmd
        assert str(GIT_BLAME_COMMIT_LIMIT) in cmd
        assert any(arg.startswith("--since=") and GIT_BLAME_SINCE in arg for arg in cmd)
        assert captured["timeout"] == GIT_BLAME_TIMEOUT_S

    def test_timeout_handled_gracefully(self, tmp_path: Path, caplog):
        """Subprocess timeout must NOT raise; provider just emits empty blame map."""
        from jcodemunch_mcp.parser.context.git_blame import GitBlameProvider

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=10.0)

        provider = GitBlameProvider()
        with mock.patch("subprocess.run", side_effect=fake_run):
            # Should not raise
            provider.load(tmp_path)

        # Blame map empty, get_file_context returns None
        assert provider.get_file_context("any.py") is None

    def test_detect_disabled_via_config(self, tmp_path: Path):
        """git_blame_enabled=false makes detect() return False even in a git repo."""
        from jcodemunch_mcp import config as _config_module
        from jcodemunch_mcp.parser.context.git_blame import GitBlameProvider

        # Simulate a git working tree
        (tmp_path / ".git").mkdir()

        original = dict(_config_module._GLOBAL_CONFIG)
        try:
            provider = GitBlameProvider()
            # Opted in, it detects (off by default since L-189).
            _config_module._GLOBAL_CONFIG["git_blame_enabled"] = True
            assert provider.detect(tmp_path) is True

            # Flipped to false → detect returns False without spawning git
            _config_module._GLOBAL_CONFIG["git_blame_enabled"] = False
            assert provider.detect(tmp_path) is False
        finally:
            _config_module._GLOBAL_CONFIG.clear()
            _config_module._GLOBAL_CONFIG.update(original)


# --------------------------------------------------------------------------- #
# Config plumbing                                                             #
# --------------------------------------------------------------------------- #

class TestConfigPlumbing:
    def test_git_blame_enabled_in_defaults(self):
        from jcodemunch_mcp.config import DEFAULTS
        # Off by default since L-189 (jjg, 2026-10-15): blame's output reaches
        # neither ranking nor file summaries, so its `git log` runs only on request.
        assert DEFAULTS.get("git_blame_enabled") is False

    def test_git_blame_enabled_in_template(self):
        from jcodemunch_mcp.config import generate_template
        template = generate_template()
        assert "git_blame_enabled" in template

    def test_git_blame_enabled_in_typed_keys(self):
        from jcodemunch_mcp import config as _config_module
        typed = getattr(_config_module, "TYPED_KEYS", None) or getattr(
            _config_module, "_TYPED_KEYS", None
        )
        # The bool-typed table — either name; verify the key is present.
        if isinstance(typed, dict):
            assert typed.get("git_blame_enabled") is bool
