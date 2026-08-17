"""Tests for ``jcodemunch-mcp init --minimal`` (P1.7).

Covers the contract that ``--minimal`` writes only MCP server registration
and skips every other side-effect channel:
- No CLAUDE.md policy paste.
- No Cursor / Windsurf rules.
- No AGENTS.md.
- No worktree hooks, enforcement hooks, or Copilot hooks.
- No index step, no audit step.

We exercise ``run_init(minimal=True, ...)`` directly with the various
install helpers monkey-patched to record calls, then assert that none of
the channel-modifying helpers were invoked.
"""

from unittest.mock import MagicMock

import pytest

from jcodemunch_mcp.cli import init as init_module


@pytest.fixture
def fake_install_helpers(monkeypatch):
    """Monkey-patch every channel-writing helper so we can assert it wasn't called."""
    spies = {
        "install_claude_md": MagicMock(return_value="  (would install)"),
        "install_cursor_rules": MagicMock(return_value="  (would install)"),
        "install_windsurf_rules": MagicMock(return_value="  (would install)"),
        "install_agents_md": MagicMock(return_value="  (would install)"),
        "install_hooks": MagicMock(return_value="  (would install)"),
        "install_enforcement_hooks": MagicMock(return_value="  (would install)"),
        "install_copilot_hooks": MagicMock(return_value="  (would install)"),
        "run_index": MagicMock(return_value="  (would index)"),
        "run_audit": MagicMock(return_value=iter(["  (would audit)"])),
    }
    for name, mock in spies.items():
        monkeypatch.setattr(init_module, name, mock)
    return spies


@pytest.fixture
def fake_client_detection(monkeypatch):
    """Pretend Claude Code is detected, and configure_client is a no-op spy."""
    fake_client = MagicMock()
    fake_client.name = "Claude Code"
    fake_client.config_path = None
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [fake_client])
    monkeypatch.setattr(init_module, "configure_client", MagicMock(return_value="  configured"))


class TestMinimalMode:
    def test_minimal_skips_all_channels_except_mcp_registration(
        self, fake_install_helpers, fake_client_detection
    ):
        rc = init_module.run_init(
            clients=None,        # detect (we mocked detection to claude-code)
            claude_md=None,      # not explicitly set; minimal should force skip
            hooks=False,
            copilot_hooks=False,
            index=False,
            audit=False,
            dry_run=True,        # so any helper that did fire wouldn't write
            yes=True,            # simulate the install <agent> flow
            minimal=True,
        )

        assert rc == 0

        for name, spy in fake_install_helpers.items():
            assert not spy.called, (
                f"--minimal must not invoke {name}, but it was called "
                f"{spy.call_count} time(s)"
            )

    def test_minimal_under_yes_does_not_default_anything_on(
        self, fake_install_helpers, fake_client_detection
    ):
        # Simulate the exact code path that `jcodemunch-mcp install claude-code --minimal`
        # takes: yes=True, hooks=True (the install subcommand's hardcoded default),
        # but minimal=True should still suppress everything.
        rc = init_module.run_init(
            clients=["auto"],
            claude_md="global",       # install <agent> hardcodes this; minimal should override
            hooks=True,               # install <agent> hardcodes this; minimal should override
            copilot_hooks=False,
            index=False,
            audit=False,
            yes=True,
            minimal=True,
        )

        assert rc == 0
        assert not fake_install_helpers["install_claude_md"].called
        assert not fake_install_helpers["install_hooks"].called
        assert not fake_install_helpers["install_enforcement_hooks"].called
        assert not fake_install_helpers["install_agents_md"].called
        assert not fake_install_helpers["install_copilot_hooks"].called
        assert not fake_install_helpers["run_index"].called
        assert not fake_install_helpers["run_audit"].called

    def test_non_minimal_still_invokes_channels_under_yes(
        self, fake_install_helpers, fake_client_detection
    ):
        """Regression test: without --minimal, the existing --yes flow still
        invokes the channels it always invoked. P1.7 must not break the default."""
        rc = init_module.run_init(
            clients=["auto"],
            claude_md="global",
            hooks=True,
            copilot_hooks=False,
            index=False,         # index is opt-in even under --yes
            audit=False,         # audit is opt-in even under --yes
            yes=True,
            minimal=False,       # explicitly non-minimal
        )

        assert rc == 0
        # claude_md=global and hooks=True passthrough means these MUST fire.
        assert fake_install_helpers["install_claude_md"].called
        assert fake_install_helpers["install_hooks"].called
        assert fake_install_helpers["install_enforcement_hooks"].called
        # AGENTS.md defaults on under --yes when not minimal.
        assert fake_install_helpers["install_agents_md"].called
