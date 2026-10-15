"""Regression tests for hook-command path handling on Windows.

Root-cause history (resolved in this commit):

  1. ``_hook_invocation()`` returned the native-slash form on Windows
     (e.g. ``C:\\Python314\\Scripts\\jcodemunch-mcp.EXE``). JSON-encoded
     into settings.json, the command survives as ``C:\\Python314\\...``
     and is later spawned through bash. Bash treats every ``\\`` as an
     escape character and silently eats them, so the executed path
     becomes ``C:Python314Scriptsjcodemunch-mcp.EXE`` -> "command not
     found". Fix: normalise to forward slashes on Windows.

  2. ``_merge_hooks()`` used a substring marker (``"jcodemunch-mcp
     hook-p"``) for legacy duplicate detection. Absolute-path forms
     break the substring because ``.EXE `` sits between ``jcodemunch-mcp``
     and ``hook-p``. Re-running ``init`` therefore appended a second copy
     of every hook on each invocation. Fix: extract the jcm subcommand
     from each command with a regex that survives every path-shape
     variation, and compare subcommands instead of raw strings.

Both bugs together produced the "PreToolUse:Read hook error / command
not found" loop that kept recurring even after manual settings.json
patches.
"""

from __future__ import annotations

import platform
import sys
from unittest import mock

import pytest

from jcodemunch_mcp import install_layout

from jcodemunch_mcp.cli.init import (
    _extract_jcm_subcommand,
    _hook_invocation,
    _merge_hooks,
)


# ── _hook_invocation ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_own_executable(monkeypatch):
    # These tests feed a path through PATH; the running install's own
    # executable is asked first since `install_layout.own_executable`, so
    # this box's venv would answer instead. Take it out of the question.
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(install_layout, "_scripts_dir", lambda: None)


def test_hook_invocation_uses_forward_slashes_on_windows():
    with mock.patch("jcodemunch_mcp.cli.init.platform.system", return_value="Windows"), \
         mock.patch(
             "jcodemunch_mcp.cli.init.shutil.which",
             return_value=r"C:\Python314\Scripts\jcodemunch-mcp.EXE",
         ):
        out = _hook_invocation()
    assert "\\" not in out
    assert out == "C:/Python314/Scripts/jcodemunch-mcp.EXE"


def test_hook_invocation_preserves_posix_paths():
    with mock.patch("jcodemunch_mcp.cli.init.platform.system", return_value="Linux"), \
         mock.patch(
             "jcodemunch_mcp.cli.init.shutil.which",
             return_value="/usr/local/bin/jcodemunch-mcp",
         ):
        out = _hook_invocation()
    assert out == "/usr/local/bin/jcodemunch-mcp"


def test_hook_invocation_quotes_paths_with_spaces():
    with mock.patch("jcodemunch_mcp.cli.init.platform.system", return_value="Windows"), \
         mock.patch(
             "jcodemunch_mcp.cli.init.shutil.which",
             return_value=r"C:\Program Files\Python314\Scripts\jcodemunch-mcp.EXE",
         ):
        out = _hook_invocation()
    assert out.startswith('"') and out.endswith('"')
    assert "\\" not in out


def test_hook_invocation_falls_back_to_bare_name_when_unresolved():
    with mock.patch("jcodemunch_mcp.cli.init.shutil.which", return_value=None):
        assert _hook_invocation() == "jcodemunch-mcp"


# ── _extract_jcm_subcommand ───────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,expected", [
    ("jcodemunch-mcp hook-pretooluse", "hook-pretooluse"),
    ("C:/Python314/Scripts/jcodemunch-mcp.EXE hook-pretooluse", "hook-pretooluse"),
    (r"C:\Python314\Scripts\jcodemunch-mcp.EXE hook-pretooluse", "hook-pretooluse"),
    ("/usr/local/bin/jcodemunch-mcp hook-posttooluse", "hook-posttooluse"),
    ('"C:/Program Files/jcodemunch-mcp.exe" hook-precompact', "hook-precompact"),
    ("jcodemunch-mcp hook-event create", "hook-event create"),
    ("jcodemunch-mcp hook-event remove", "hook-event remove"),
])
def test_extract_subcommand_handles_path_variations(cmd, expected):
    assert _extract_jcm_subcommand(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "",
    "python sync_memory.py",
    "node statusline.js",
    "echo 'not a jcm command'",
])
def test_extract_subcommand_returns_none_for_non_jcm(cmd):
    assert _extract_jcm_subcommand(cmd) is None


# ── _merge_hooks duplicate detection ──────────────────────────────────────────

def test_merge_hooks_does_not_duplicate_across_path_shapes():
    """The exact scenario that caused the recurring hook error: settings
    already contains the bare-name form, then a re-run resolves to an
    absolute path. Both invoke the same jcm subcommand -> must not duplicate.
    The existing rule's stale command converges to the shipped one (reported
    as an update, never as an addition) — leaving the bare name in place was
    the minimal-PATH death this same fix batch diagnosed."""
    data = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Read",
                "hooks": [{"type": "command", "command": "jcodemunch-mcp hook-pretooluse"}],
            }],
        },
    }
    new_def = {
        "PreToolUse": [{
            "matcher": "Read",
            "hooks": [{
                "type": "command",
                "command": "C:/Python314/Scripts/jcodemunch-mcp.EXE hook-pretooluse",
            }],
        }],
    }
    added, updated = _merge_hooks(data, new_def, "jcodemunch-mcp hook-p")
    assert (added, updated) == ([], ["PreToolUse"])  # converged, not added
    assert len(data["hooks"]["PreToolUse"]) == 1
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == (
        "C:/Python314/Scripts/jcodemunch-mcp.EXE hook-pretooluse"
    )


def test_merge_hooks_dedupes_backslash_path_against_bare_name():
    """The exact failing form that bash silently corrupts."""
    data = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Read",
                "hooks": [{"type": "command", "command": "jcodemunch-mcp hook-pretooluse"}],
            }],
        },
    }
    new_def = {
        "PreToolUse": [{
            "matcher": "Read",
            "hooks": [{
                "type": "command",
                "command": r"C:\Python314\Scripts\jcodemunch-mcp.EXE hook-pretooluse",
            }],
        }],
    }
    added, updated = _merge_hooks(data, new_def, "jcodemunch-mcp hook-p")
    assert (added, updated) == ([], ["PreToolUse"])  # converged, not duplicated
    assert len(data["hooks"]["PreToolUse"]) == 1


def test_merge_hooks_preserves_unrelated_hooks():
    """User-added third-party hooks must survive the merge."""
    data = {
        "hooks": {
            "PostToolUse": [{
                "matcher": "Edit|Write",
                "hooks": [{"type": "command", "command": "python /home/me/sync.py"}],
            }],
        },
    }
    new_def = {
        "PostToolUse": [{
            "matcher": "Edit|Write",
            "hooks": [{"type": "command", "command": "jcodemunch-mcp hook-posttooluse"}],
        }],
    }
    added, _updated = _merge_hooks(data, new_def, "jcodemunch-mcp hook-p")
    assert added == ["PostToolUse"]
    rules = data["hooks"]["PostToolUse"]
    assert len(rules) == 2
    commands = [h["command"] for r in rules for h in r["hooks"]]
    assert "python /home/me/sync.py" in commands
    assert "jcodemunch-mcp hook-posttooluse" in commands


def test_merge_hooks_repeated_calls_idempotent():
    """Running init twice with the same hook_defs must not double up."""
    data: dict = {}
    hook_def = {
        "PreToolUse": [{
            "matcher": "Read",
            "hooks": [{"type": "command", "command": "jcodemunch-mcp hook-pretooluse"}],
        }],
    }
    _merge_hooks(data, hook_def, "jcodemunch-mcp hook-p")
    _merge_hooks(data, hook_def, "jcodemunch-mcp hook-p")
    assert len(data["hooks"]["PreToolUse"]) == 1
