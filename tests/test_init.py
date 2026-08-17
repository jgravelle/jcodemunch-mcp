"""Tests for jcodemunch-mcp init command."""

import json
import os
import platform
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from jcodemunch_mcp.cli.init import (
    CONFIGURE_METHODS,
    MCPClient,
    _detect_clients,
    _has_jcodemunch_entry,
    _patch_mcp_config,
    _read_json,
    _write_json,
    configure_client,
    install_claude_md,
    install_cursor_rules,
    install_windsurf_rules,
    install_hooks,
    run_audit,
    run_init,
    _CLAUDE_MD_MARKER,
    _CLAUDE_MD_POLICY,
    _MCP_ENTRY,
)


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------

def test_read_json_missing(tmp_path):
    assert _read_json(tmp_path / "nope.json") == {}


def test_read_json_invalid(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not json", encoding="utf-8")
    assert _read_json(f) == {}


def test_read_write_json_roundtrip(tmp_path):
    f = tmp_path / "test.json"
    data = {"foo": "bar", "nested": {"a": 1}}
    _write_json(f, data, backup=False)
    assert _read_json(f) == data


def test_write_json_creates_backup(tmp_path):
    f = tmp_path / "cfg.json"
    f.write_text('{"old": true}', encoding="utf-8")
    _write_json(f, {"new": True}, backup=True)
    bak = f.with_suffix(".json.bak")
    assert bak.exists()
    assert json.loads(bak.read_text(encoding="utf-8")) == {"old": True}
    assert json.loads(f.read_text(encoding="utf-8")) == {"new": True}


def test_write_json_creates_parent_dirs(tmp_path):
    f = tmp_path / "a" / "b" / "c.json"
    _write_json(f, {"x": 1}, backup=False)
    assert f.exists()


# ---------------------------------------------------------------------------
# _has_jcodemunch_entry / _patch_mcp_config
# ---------------------------------------------------------------------------

def test_has_jcodemunch_entry_false():
    assert not _has_jcodemunch_entry({})
    assert not _has_jcodemunch_entry({"mcpServers": {"other": {}}})


def test_has_jcodemunch_entry_true():
    assert _has_jcodemunch_entry({"mcpServers": {"jcodemunch": {}}})


def test_patch_mcp_config_new(tmp_path):
    f = tmp_path / "mcp.json"
    msg = _patch_mcp_config(f, backup=False)
    assert "added" in msg
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["mcpServers"]["jcodemunch"] == _MCP_ENTRY


def test_patch_mcp_config_existing_servers(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    _patch_mcp_config(f, backup=False)
    data = json.loads(f.read_text(encoding="utf-8"))
    assert "other" in data["mcpServers"]
    assert "jcodemunch" in data["mcpServers"]


def test_patch_mcp_config_already_present(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {"jcodemunch": {}}}), encoding="utf-8")
    msg = _patch_mcp_config(f, backup=False)
    assert "already" in msg


def test_patch_mcp_config_dry_run(tmp_path):
    f = tmp_path / "mcp.json"
    msg = _patch_mcp_config(f, backup=False, dry_run=True)
    assert "would" in msg
    assert not f.exists()


# ---------------------------------------------------------------------------
# configure_client
# ---------------------------------------------------------------------------

def test_configure_client_json_patch(tmp_path):
    client = MCPClient("Test", tmp_path / "mcp.json", "json_patch")
    msg = configure_client(client, backup=False)
    assert "added" in msg


def test_configure_client_cli_dry_run():
    client = MCPClient("Claude Code", None, "cli")
    msg = configure_client(client, dry_run=True)
    assert "would run" in msg


@patch("jcodemunch_mcp.cli.init.subprocess.run")
@patch("jcodemunch_mcp.cli.init._claude_cli_exe", return_value="claude")
def test_configure_client_cli_success(mock_exe, mock_run):
    # Resolver is mocked so the test is deterministic whether or not `claude`
    # is actually on PATH (CI agents have no claude installed).
    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    client = MCPClient("Claude Code", None, "cli")
    msg = configure_client(client, dry_run=False)
    assert "ran" in msg
    mock_run.assert_called_once()


@patch("jcodemunch_mcp.cli.init.subprocess.run")
@patch("jcodemunch_mcp.cli.init._claude_cli_exe", return_value="claude")
def test_configure_client_cli_already_exists(mock_exe, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stderr="Server already exists", stdout="")
    client = MCPClient("Claude Code", None, "cli")
    msg = configure_client(client, dry_run=False)
    assert "already" in msg


# ---------------------------------------------------------------------------
# install_claude_md
# ---------------------------------------------------------------------------

def test_install_claude_md_new(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    msg = install_claude_md("global")
    assert "appended" in msg
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert _CLAUDE_MD_MARKER in content


def test_install_claude_md_appends(tmp_path, monkeypatch):
    f = tmp_path / "CLAUDE.md"
    f.write_text("# Existing content\n", encoding="utf-8")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: f)
    install_claude_md("project", backup=False)
    content = f.read_text(encoding="utf-8")
    assert content.startswith("# Existing content")
    assert _CLAUDE_MD_MARKER in content


def test_install_claude_md_idempotent(tmp_path, monkeypatch):
    f = tmp_path / "CLAUDE.md"
    f.write_text(_CLAUDE_MD_POLICY, encoding="utf-8")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: f)
    msg = install_claude_md("global")
    assert "already" in msg


def test_install_claude_md_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    msg = install_claude_md("global", dry_run=True)
    assert "would" in msg
    assert not (tmp_path / "CLAUDE.md").exists()


# ---------------------------------------------------------------------------
# install_cursor_rules
# ---------------------------------------------------------------------------

def test_install_cursor_rules_new(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._cursor_rules_path", lambda: tmp_path / ".cursor" / "rules" / "jcodemunch.mdc")
    msg = install_cursor_rules(backup=False)
    assert "wrote" in msg
    content = (tmp_path / ".cursor" / "rules" / "jcodemunch.mdc").read_text(encoding="utf-8")
    assert "alwaysApply: true" in content
    assert _CLAUDE_MD_MARKER in content


def test_install_cursor_rules_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._cursor_rules_path", lambda: tmp_path / ".cursor" / "rules" / "jcodemunch.mdc")
    install_cursor_rules(backup=False)
    msg = install_cursor_rules(backup=False)
    assert "already" in msg


def test_install_cursor_rules_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._cursor_rules_path", lambda: tmp_path / ".cursor" / "rules" / "jcodemunch.mdc")
    msg = install_cursor_rules(dry_run=True)
    assert "would" in msg
    assert not (tmp_path / ".cursor" / "rules" / "jcodemunch.mdc").exists()


# ---------------------------------------------------------------------------
# install_windsurf_rules
# ---------------------------------------------------------------------------

def test_install_windsurf_rules_new(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._windsurf_rules_path", lambda: tmp_path / ".windsurfrules")
    msg = install_windsurf_rules(backup=False)
    assert "appended" in msg
    content = (tmp_path / ".windsurfrules").read_text(encoding="utf-8")
    assert _CLAUDE_MD_MARKER in content


def test_install_windsurf_rules_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._windsurf_rules_path", lambda: tmp_path / ".windsurfrules")
    install_windsurf_rules(backup=False)
    msg = install_windsurf_rules(backup=False)
    assert "already" in msg


def test_install_windsurf_rules_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._windsurf_rules_path", lambda: tmp_path / ".windsurfrules")
    msg = install_windsurf_rules(dry_run=True)
    assert "would" in msg
    assert not (tmp_path / ".windsurfrules").exists()


def test_install_windsurf_rules_appends_to_existing(tmp_path, monkeypatch):
    f = tmp_path / ".windsurfrules"
    f.write_text("# Existing rules\n", encoding="utf-8")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._windsurf_rules_path", lambda: f)
    install_windsurf_rules(backup=False)
    content = f.read_text(encoding="utf-8")
    assert content.startswith("# Existing rules\n")
    assert _CLAUDE_MD_MARKER in content


# ---------------------------------------------------------------------------
# install_hooks
# ---------------------------------------------------------------------------

def test_install_hooks_new(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: tmp_path / "settings.json")
    msg = install_hooks(backup=False)
    assert "WorktreeCreate" in msg
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "WorktreeCreate" in data["hooks"]
    assert "WorktreeRemove" in data["hooks"]


def test_install_hooks_merge_existing(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({
        "hooks": {
            "SomeOther": [{"matcher": "", "hooks": []}],
        }
    }), encoding="utf-8")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: f)
    install_hooks(backup=False)
    data = json.loads(f.read_text(encoding="utf-8"))
    assert "SomeOther" in data["hooks"]
    assert "WorktreeCreate" in data["hooks"]


def test_install_hooks_idempotent(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: f)
    install_hooks(backup=False)
    msg = install_hooks(backup=False)
    assert "already" in msg


def test_install_hooks_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: tmp_path / "settings.json")
    msg = install_hooks(dry_run=True)
    assert "would" in msg
    assert not (tmp_path / "settings.json").exists()


# ---------------------------------------------------------------------------
# run_init (non-interactive --yes mode)
# ---------------------------------------------------------------------------

def test_run_init_dry_run_yes(tmp_path, monkeypatch, capsys):
    """Full dry-run with --yes should print actions without modifying anything."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("TestClient", tmp_path / "mcp.json", "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: tmp_path / "settings.json")

    rc = run_init(dry_run=True, yes=True)
    assert rc == 0

    out = capsys.readouterr().out
    assert "would" in out
    assert "Dry run" in out
    # No files should be created
    assert not (tmp_path / "mcp.json").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_run_init_full_yes(tmp_path, monkeypatch, capsys):
    """Full run with --yes should configure everything."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("TestClient", tmp_path / "mcp.json", "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: tmp_path / "settings.json")

    rc = run_init(yes=True, no_backup=True)
    assert rc == 0

    # MCP config created
    assert (tmp_path / "mcp.json").exists()
    data = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert "jcodemunch" in data["mcpServers"]

    # CLAUDE.md created
    assert (tmp_path / "CLAUDE.md").exists()
    assert _CLAUDE_MD_MARKER in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_run_init_explicit_client_none(tmp_path, monkeypatch, capsys):
    """--client none should skip client configuration."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("TestClient", tmp_path / "mcp.json", "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")

    rc = run_init(clients=["none"], claude_md="global", yes=True, no_backup=True)
    assert rc == 0
    assert not (tmp_path / "mcp.json").exists()


# ---------------------------------------------------------------------------
# run_init with Cursor/Windsurf rules
# ---------------------------------------------------------------------------

def test_run_init_yes_cursor_writes_rules(tmp_path, monkeypatch, capsys):
    """--yes with a Cursor client should install .cursor/rules/jcodemunch.mdc."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("Cursor", tmp_path / "mcp.json", "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._cursor_rules_path", lambda: tmp_path / ".cursor" / "rules" / "jcodemunch.mdc")

    rc = run_init(yes=True, no_backup=True)
    assert rc == 0

    mdc = tmp_path / ".cursor" / "rules" / "jcodemunch.mdc"
    assert mdc.exists()
    content = mdc.read_text(encoding="utf-8")
    assert "alwaysApply: true" in content
    assert _CLAUDE_MD_MARKER in content


def test_run_init_yes_windsurf_writes_rules(tmp_path, monkeypatch, capsys):
    """--yes with a Windsurf client should install .windsurfrules."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("Windsurf", tmp_path / "windsurf_mcp.json", "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._windsurf_rules_path", lambda: tmp_path / ".windsurfrules")

    rc = run_init(yes=True, no_backup=True)
    assert rc == 0

    rules = tmp_path / ".windsurfrules"
    assert rules.exists()
    assert _CLAUDE_MD_MARKER in rules.read_text(encoding="utf-8")


def test_run_init_demo_cursor_reports_benefit(tmp_path, monkeypatch, capsys):
    """--demo with Cursor should include rules benefit in summary."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("Cursor", tmp_path / "mcp.json", "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._cursor_rules_path", lambda: tmp_path / ".cursor" / "rules" / "jcodemunch.mdc")

    rc = run_init(demo=True, yes=True)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Cursor rules" in out
    assert "subagents" in out
    assert not (tmp_path / ".cursor" / "rules" / "jcodemunch.mdc").exists()


# ---------------------------------------------------------------------------
# _detect_clients smoke test
# ---------------------------------------------------------------------------

def test_detect_clients_returns_list():
    """Detection should return a list (may be empty in CI)."""
    result = _detect_clients()
    assert isinstance(result, list)
    for c in result:
        assert isinstance(c, MCPClient)
        assert c.name
        assert c.method in CONFIGURE_METHODS


# ---------------------------------------------------------------------------
# run_audit
# ---------------------------------------------------------------------------

def test_run_audit_dry_run():
    lines = run_audit(dry_run=True)
    assert any("would" in l for l in lines)


def test_run_audit_with_config(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# My policy\nUse tools.", encoding="utf-8")
    lines = run_audit(project_path=str(tmp_path))
    assert any("scanned" in l for l in lines)


def test_run_audit_no_config(tmp_path):
    lines = run_audit(project_path=str(tmp_path))
    # May find global configs but project dir has none
    assert isinstance(lines, list)


# ---------------------------------------------------------------------------
# run_init with --audit
# ---------------------------------------------------------------------------

def test_run_init_yes_includes_audit(tmp_path, monkeypatch, capsys):
    """--yes mode should run audit by default."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    # Create a config file so audit has something to scan
    (tmp_path / "CLAUDE.md").write_text("# Test policy", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    rc = run_init(yes=True, no_backup=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Audit" in out


def test_run_init_dry_run_audit(tmp_path, monkeypatch, capsys):
    """--dry-run --yes should show audit would run."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")

    rc = run_init(dry_run=True, yes=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "would audit" in out


# ---------------------------------------------------------------------------
# run_init --demo
# ---------------------------------------------------------------------------

def test_run_init_demo_makes_no_changes(tmp_path, monkeypatch, capsys):
    """--demo should make no file changes and print the summary."""
    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("TestClient", tmp_path / "mcp.json", "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: tmp_path / "settings.json")

    rc = run_init(demo=True, yes=True)
    assert rc == 0

    # No files written
    assert not (tmp_path / "mcp.json").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "settings.json").exists()

    out = capsys.readouterr().out
    assert "DEMO MODE" in out
    assert "Demo complete" in out
    assert "Had this NOT been a demo" in out
    assert "Benefit:" in out


def test_run_init_demo_nothing_to_do(tmp_path, monkeypatch, capsys):
    """--demo with everything already configured prints the 'nothing to do' message."""
    # Pre-write mcp.json with jcodemunch already present
    mcp_cfg = tmp_path / "mcp.json"
    mcp_cfg.write_text(
        json.dumps({"mcpServers": {"jcodemunch": {"command": "uvx", "args": ["jcodemunch-mcp"]}}}),
        encoding="utf-8",
    )
    # Pre-write CLAUDE.md with policy already present
    md = tmp_path / "CLAUDE.md"
    md.write_text(_CLAUDE_MD_MARKER, encoding="utf-8")

    monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [
        MCPClient("TestClient", mcp_cfg, "json_patch"),
    ])
    monkeypatch.setattr("jcodemunch_mcp.cli.init._claude_md_path", lambda scope: md)
    monkeypatch.setattr("jcodemunch_mcp.cli.init._settings_json_path", lambda: tmp_path / "settings.json")

    # Explicit flags bypass the yes-mode defaults so audit isn't force-enabled
    rc = run_init(demo=True, clients=["auto"], claude_md="global", hooks=False, index=False, audit=False)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Nothing to do" in out
