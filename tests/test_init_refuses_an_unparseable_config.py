"""`init` never rewrites a config file it could not read (2026-11-27).

`_read_json` returned `{}` for a file that existed and did not parse, so a
client config with one trailing comma read as empty and the next write
replaced it with our entry alone: the user's other MCP servers were gone, and
with `--no-backup` there was no copy. Repro (through the real CLI):
`~/.cursor/mcp.json` holding `{"mcpServers": {"other": {...}},}` and
`jcodemunch-mcp install cursor` printed "added jcodemunch" with no warning.

The property:
- a config file that exists is read as a JSON object or not at all; a parse
  error, an unreadable file, a top level that is not an object, or a server
  table that is not an object raises `ConfigUnreadable` naming the file (and
  the line, for a parse error), and the file is left byte-for-byte untouched,
  with or without a backup;
- `run_init` reports the refused file, carries on with the other targets and
  exits non-zero;
- VS Code reads `mcp.json` as JSONC, so comments and trailing commas there are
  parsed, not refused; a rewrite does not keep the comments, so it needs the
  backup (it refuses under `--no-backup`);
- a missing file is still created, and an empty one still counts as empty.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcodemunch_mcp.cli import init as init_mod
from jcodemunch_mcp.cli.init import ConfigUnreadable, MCPClient, configure_client

OTHER = {"command": "other-server", "args": []}

# Every client init patches through the generic `mcpServers` writer.
JSON_PATCH_CLIENTS = ["Claude Desktop", "Cursor", "Windsurf", "Continue", "Gemini CLI", "Cline"]

BROKEN = {
    "trailing comma": '{"mcpServers": {"other": {"command": "other-server", "args": []}},}\n',
    "unclosed object": '{"mcpServers": {"other": {"command": "other-server", "args": []}}\n',
    "top level is a list": '[{"mcpServers": {}}]\n',
    "mcpServers is a list": '{"mcpServers": [{"command": "other-server"}]}\n',
    "mcpServers is a string": '{"mcpServers": "other"}\n',
}


def _assert_untouched(path: Path, before: bytes) -> None:
    assert path.read_bytes() == before
    assert not init_mod._backup_path(path).exists(), "a refused file must not be backed up and rewritten"


@pytest.mark.parametrize("client", JSON_PATCH_CLIENTS)
@pytest.mark.parametrize("case", sorted(BROKEN))
@pytest.mark.parametrize("backup", [True, False], ids=["backup", "no-backup"])
def test_a_broken_client_config_is_refused_and_left_untouched(tmp_path, client, case, backup):
    path = tmp_path / "mcp.json"
    path.write_text(BROKEN[case], encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ConfigUnreadable) as err:
        configure_client(MCPClient(client, path, "json_patch"), backup=backup)
    assert str(path) in str(err.value)
    _assert_untouched(path, before)


def test_a_parse_error_names_the_line(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text('{\n  "mcpServers": {\n    "other": {}\n  },\n}\n', encoding="utf-8")
    with pytest.raises(ConfigUnreadable) as err:
        configure_client(MCPClient("Cursor", path, "json_patch"))
    assert "line 5" in str(err.value), str(err.value)


def test_an_unreadable_file_is_refused_not_read_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": OTHER}}), encoding="utf-8")
    before = path.read_bytes()
    real = Path.read_text

    def locked(self, *a, **k):
        if self == path:
            raise PermissionError(13, "locked")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(ConfigUnreadable):
        configure_client(MCPClient("Cursor", path, "json_patch"))
    monkeypatch.undo()
    _assert_untouched(path, before)


def test_a_valid_config_keeps_the_other_servers(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": OTHER}, "theme": "dark"}), encoding="utf-8")
    configure_client(MCPClient("Cursor", path, "json_patch"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"] == OTHER and "jcodemunch" in data["mcpServers"]
    assert data["theme"] == "dark"


@pytest.mark.parametrize("text", [None, "", "  \n"], ids=["missing", "empty", "blank"])
def test_a_missing_or_empty_file_is_still_written(tmp_path, text):
    path = tmp_path / "sub" / "mcp.json"
    if text is not None:
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
    configure_client(MCPClient("Cursor", path, "json_patch"))
    assert "jcodemunch" in json.loads(path.read_text(encoding="utf-8"))["mcpServers"]


def test_run_init_reports_the_refusal_and_exits_non_zero(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "cursor" / "mcp.json"
    good = tmp_path / "windsurf" / "mcp_config.json"
    bad.parent.mkdir()
    good.parent.mkdir()
    bad.write_text(BROKEN["trailing comma"], encoding="utf-8")
    before = bad.read_bytes()
    monkeypatch.setattr(
        init_mod, "_detect_clients",
        lambda: [MCPClient("Cursor", bad, "json_patch"), MCPClient("Windsurf", good, "json_patch")],
    )
    rc = init_mod.run_init(clients=["cursor", "windsurf"], yes=True, minimal=True, no_backup=True)
    out = capsys.readouterr().out
    assert rc != 0
    assert str(bad) in out and "line 1" in out, out
    assert bad.read_bytes() == before
    assert "jcodemunch" in json.loads(good.read_text(encoding="utf-8"))["mcpServers"]


# --- VS Code reads mcp.json as JSONC --------------------------------------

VSCODE_JSONC = """{
  // servers this user added by hand
  "servers": {
    "other": {"command": "other-server", "args": [],},
  },
}
"""


def test_vscode_jsonc_is_parsed_and_its_servers_kept(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(VSCODE_JSONC, encoding="utf-8")
    msg = configure_client(MCPClient("VS Code", path, "json_vscode"), backup=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["servers"]["other"] == OTHER and "jcodemunch" in data["servers"]
    assert init_mod._backup_path(path).read_text(encoding="utf-8") == VSCODE_JSONC
    assert "comment" in msg, msg


def test_vscode_jsonc_is_refused_without_a_backup(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(VSCODE_JSONC, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ConfigUnreadable) as err:
        configure_client(MCPClient("VS Code", path, "json_vscode"), backup=False)
    assert "--no-backup" in str(err.value)
    _assert_untouched(path, before)


@pytest.mark.parametrize("text", ['{"servers": {"other": {}}\n', '["servers"]\n', '{"servers": []}\n'])
def test_vscode_broken_jsonc_is_refused(tmp_path, text):
    path = tmp_path / "mcp.json"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ConfigUnreadable):
        configure_client(MCPClient("VS Code", path, "json_vscode"), backup=True)
    _assert_untouched(path, before)


@pytest.mark.parametrize("text", ['{"mcp": {"other": {}},}\n', '{"mcp": []}\n'])
def test_opencode_broken_config_is_refused(tmp_path, text):
    path = tmp_path / "opencode.json"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ConfigUnreadable):
        configure_client(MCPClient("OpenCode", path, "json_opencode"), backup=True)
    _assert_untouched(path, before)


# --- ~/.claude/settings.json (hooks) ---------------------------------------

@pytest.mark.parametrize(
    "text",
    ['{"permissions": {"allow": ["Bash"]},}\n', '{"hooks": []}\n', '["hooks"]\n'],
    ids=["trailing comma", "hooks is a list", "top level is a list"],
)
@pytest.mark.parametrize("installer", ["install_hooks", "install_enforcement_hooks"])
def test_a_broken_settings_json_is_refused_by_the_hook_installers(text, installer):
    path = init_mod._settings_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ConfigUnreadable):
        getattr(init_mod, installer)(backup=True)
    _assert_untouched(path, before)


def test_the_read_only_and_uninstall_paths_do_not_crash_on_a_broken_file(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(BROKEN["trailing comma"], encoding="utf-8")
    before = path.read_bytes()
    settings = init_mod._settings_json_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"hooks": {},}\n', encoding="utf-8")
    settings_before = settings.read_bytes()
    monkeypatch.setattr(init_mod, "_detect_clients", lambda: [MCPClient("Cursor", path, "json_patch")])
    init_mod.install_status()
    msg = init_mod.unconfigure_client(MCPClient("Cursor", path, "json_patch"), backup=True)
    assert "untouched" in msg, msg
    assert "untouched" in init_mod.uninstall_hooks(backup=True)
    assert path.read_bytes() == before and settings.read_bytes() == settings_before


def test_no_reader_in_cli_turns_a_parse_error_into_an_empty_config():
    """The ratchet: an `except` that catches a JSON parse error in `cli/` may
    skip or refuse, but never hand back an empty config to write over."""
    import ast

    root = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "cli"
    found = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            names = {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")
                     for n in ast.walk(node.type)}
            if not names & {"JSONDecodeError", "ValueError"}:
                continue
            for sub in ast.walk(node):
                value = getattr(sub, "value", None) if isinstance(sub, (ast.Return, ast.Assign)) else None
                if isinstance(value, ast.Dict) and not value.keys:
                    found.append(f"{path.name}:{sub.lineno}")
    assert found == [], found


def test_install_status_names_an_unreadable_client_config(tmp_path, monkeypatch, capsys):
    path = tmp_path / "mcp.json"
    path.write_text(BROKEN["trailing comma"], encoding="utf-8")
    monkeypatch.setattr(init_mod, "_detect_clients", lambda: [MCPClient("Cursor", path, "json_patch")])
    report = init_mod.install_status()
    entry = next(c for c in report["clients"] if c["name"] == "Cursor")
    assert entry["configured"] is False and "line 1" in entry["unreadable"]
    init_mod.print_status(report)
    out = capsys.readouterr().out
    assert "[?] Cursor" in out and "line 1" in out, out
