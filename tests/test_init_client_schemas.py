"""Per-client config schemas, and the ways getting one wrong stays silent.

Five hosts were advertised on our GitHub topics before `init` could serve
them. Three of the five do NOT accept the `_MCP_ENTRY` / `mcpServers` shape
the rest of `cli/init.py` writes, and ⚠⚠ **no mismatch here produces an
error** — the host parses the file, registers nothing, and reports success.
The user gets an agent with no jCodeMunch tools and no reason to suspect the
config. That is what these tests exist to catch.

Three distinct JSON schemas plus one TOML:

* **Codex** — TOML at `~/.codex/config.toml`, `[mcp_servers.jcodemunch]`.
  Its rmcp transport is strict about the first JSON-RPC frame on stdout and
  uvx's cold-run install chatter poisons the handshake; the documented
  symptom is a SILENT multi-hour hang (CLIENTS.md). The writer resolves a
  real binary or REFUSES — it must never emit the uvx form.
* **opencode** — top-level `mcp`, each server needs `"type": "local"`, and
  `command` is one ARRAY carrying executable and arguments together.
* **VS Code / Copilot** — top-level `servers`, not `mcpServers`.
* **Gemini CLI** and **Cline** — the generic `mcpServers` shape, so their
  risk is not the schema but DETECTION: `~/.gemini` is shared with
  Antigravity, which reads a different file entirely.
"""

from __future__ import annotations

import json
import sys

import pytest

from jcodemunch_mcp.cli import init as init_mod


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

def test_codex_writes_the_resolved_binary(tmp_path, monkeypatch):
    exe = "/opt/tools/bin/jcodemunch-mcp"
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: exe if name == "jcodemunch-mcp" else None)
    cfg = tmp_path / "config.toml"

    msg = init_mod._patch_codex_config(cfg, backup=False)

    text = cfg.read_text(encoding="utf-8")
    assert "[mcp_servers.jcodemunch]" in text
    assert exe in text
    assert "added" in msg


def test_codex_never_writes_uvx(tmp_path, monkeypatch):
    """The whole reason this writer exists (CLIENTS.md handshake hazard)."""
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: "/usr/local/bin/jcodemunch-mcp")
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    assert "uvx" not in cfg.read_text(encoding="utf-8")


def test_codex_refuses_rather_than_falling_back_to_uvx(tmp_path, monkeypatch):
    """No resolvable binary must produce NO FILE, not a uvx entry.

    A uvx fallback here would look like a successful install and then hang on
    first use, which is strictly worse than declining with an instruction.
    """
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: None)
    cfg = tmp_path / "config.toml"

    msg = init_mod._patch_codex_config(cfg, backup=False)

    assert not cfg.exists(), "declined install must not leave a config behind"
    assert "uv tool install" in msg
    assert "skipped" in msg


def test_codex_preserves_existing_settings(tmp_path, monkeypatch):
    """config.toml is user-owned; appending must not disturb what is there."""
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: "/usr/local/bin/jcodemunch-mcp")
    cfg = tmp_path / "config.toml"
    original = '# my notes\nmodel = "o3"\n\n[mcp_servers.other]\ncommand = "other-mcp"\n'
    cfg.write_text(original, encoding="utf-8")

    init_mod._patch_codex_config(cfg, backup=False)

    text = cfg.read_text(encoding="utf-8")
    assert original in text, "existing content was rewritten rather than appended to"
    assert "# my notes" in text
    assert "[mcp_servers.other]" in text


def test_codex_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: "/usr/local/bin/jcodemunch-mcp")
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)
    first = cfg.read_text(encoding="utf-8")
    msg = init_mod._patch_codex_config(cfg, backup=False)

    assert "already configured" in msg
    assert cfg.read_text(encoding="utf-8") == first


def test_codex_windows_path_survives_as_a_literal_string(tmp_path, monkeypatch):
    r"""A Windows path is the case a TOML *basic* string would corrupt.

    ``C:\Users\j\...`` in a double-quoted string makes ``\U`` an invalid
    unicode escape, so a parser rejects the file or mangles the path. The
    writer uses a literal (single-quoted) string, which performs no escape
    processing at all.
    """
    win = r"C:\Users\jjg\AppData\Local\uv\tools\jcodemunch-mcp.exe"
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: win)
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    text = cfg.read_text(encoding="utf-8")
    assert f"'{win}'" in text, "backslash path was not written as a TOML literal string"

    if sys.version_info >= (3, 11):
        import tomllib
        parsed = tomllib.loads(text)
        assert parsed["mcp_servers"]["jcodemunch"]["command"] == win


# ---------------------------------------------------------------------------
# opencode
# ---------------------------------------------------------------------------

def test_opencode_uses_its_own_schema(tmp_path):
    cfg = tmp_path / "opencode.json"

    init_mod._patch_opencode_config(cfg, backup=False)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "mcpServers" not in data, "wrote the generic key opencode does not read"
    entry = data["mcp"]["jcodemunch"]
    assert entry["type"] == "local"
    assert entry["command"] == ["uvx", "jcodemunch-mcp"]
    assert "args" not in entry, "opencode folds args into the command array"


def test_opencode_command_is_an_array_not_a_string(tmp_path):
    cfg = tmp_path / "opencode.json"
    init_mod._patch_opencode_config(cfg, backup=False)
    entry = json.loads(cfg.read_text(encoding="utf-8"))["mcp"]["jcodemunch"]
    assert isinstance(entry["command"], list)


def test_opencode_preserves_unrelated_config(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "theme": "tokyonight",
        "mcp": {"other": {"type": "local", "command": ["other-mcp"]}},
    }), encoding="utf-8")

    init_mod._patch_opencode_config(cfg, backup=False)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "tokyonight"
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert "other" in data["mcp"], "clobbered a sibling server"
    assert "jcodemunch" in data["mcp"]


def test_opencode_is_idempotent(tmp_path):
    cfg = tmp_path / "opencode.json"
    init_mod._patch_opencode_config(cfg, backup=False)
    first = cfg.read_text(encoding="utf-8")
    msg = init_mod._patch_opencode_config(cfg, backup=False)
    assert "already configured" in msg
    assert cfg.read_text(encoding="utf-8") == first


# ---------------------------------------------------------------------------
# VS Code / GitHub Copilot
# ---------------------------------------------------------------------------

def test_vscode_uses_the_servers_key(tmp_path):
    """Third distinct schema, and like opencode's it fails silently."""
    cfg = tmp_path / "mcp.json"

    init_mod._patch_vscode_mcp_config(cfg, backup=False)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "mcpServers" not in data, "wrote the key VS Code does not read"
    assert data["servers"]["jcodemunch"]["command"] == "uvx"
    assert data["servers"]["jcodemunch"]["args"] == ["jcodemunch-mcp"]


def test_vscode_preserves_sibling_servers(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({
        "inputs": [{"id": "tok", "type": "promptString"}],
        "servers": {"playwright": {"command": "npx", "args": ["-y", "x"]}},
    }), encoding="utf-8")

    init_mod._patch_vscode_mcp_config(cfg, backup=False)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "playwright" in data["servers"], "clobbered a sibling server"
    assert "jcodemunch" in data["servers"]
    assert data["inputs"][0]["id"] == "tok", "dropped an unrelated top-level key"


def test_vscode_is_idempotent(tmp_path):
    cfg = tmp_path / "mcp.json"
    init_mod._patch_vscode_mcp_config(cfg, backup=False)
    first = cfg.read_text(encoding="utf-8")
    msg = init_mod._patch_vscode_mcp_config(cfg, backup=False)
    assert "already configured" in msg
    assert cfg.read_text(encoding="utf-8") == first


def test_vscode_does_not_mutate_the_shared_entry_template(tmp_path):
    """`servers[...] = _MCP_ENTRY` would alias the module-level dict.

    Every other client writes that same object, so a later mutation through
    one config would silently change what every subsequent client receives.
    """
    before = dict(init_mod._MCP_ENTRY)
    cfg = tmp_path / "mcp.json"
    init_mod._patch_vscode_mcp_config(cfg, backup=False)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["servers"]["jcodemunch"]["args"].append("--tampered")

    assert init_mod._MCP_ENTRY == before


# ---------------------------------------------------------------------------
# Detection + dispatch
# ---------------------------------------------------------------------------

def test_gemini_cli_is_not_detected_from_the_antigravity_directory(tmp_path, monkeypatch):
    """~/.gemini exists for Antigravity users who have no Gemini CLI.

    Antigravity reads ~/.gemini/config/mcp_config.json; Gemini CLI reads
    ~/.gemini/settings.json. Keying detection on the shared DIRECTORY would
    offer to configure a client the user does not have and write a
    settings.json nothing reads.
    """
    monkeypatch.setattr(init_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(init_mod, "_find_executable", lambda n: None)
    monkeypatch.setattr(init_mod.Path, "cwd", staticmethod(lambda: tmp_path / "proj"))
    (tmp_path / ".gemini" / "config").mkdir(parents=True)
    (tmp_path / ".gemini" / "config" / "mcp_config.json").write_text("{}", encoding="utf-8")

    names = {c.name for c in init_mod._detect_clients()}

    assert "Gemini CLI" not in names


@pytest.mark.parametrize("name,rel", [
    ("Gemini CLI", ".gemini/settings.json"),
    ("Cline", ".cline/mcp.json"),
])
def test_generic_clients_detected_from_their_own_file(tmp_path, monkeypatch, name, rel):
    monkeypatch.setattr(init_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(init_mod, "_find_executable", lambda n: None)
    monkeypatch.setattr(init_mod.Path, "cwd", staticmethod(lambda: tmp_path / "proj"))
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")

    found = {c.name: c for c in init_mod._detect_clients()}

    assert name in found
    assert found[name].method == "json_patch"


def test_vscode_requires_an_existing_dot_vscode_directory(tmp_path, monkeypatch):
    """`code` on PATH is true almost everywhere; .vscode/ is the real signal.

    Detecting on the executable would CREATE .vscode/mcp.json in whatever
    directory init happened to be run from.
    """
    monkeypatch.setattr(init_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(init_mod, "_find_executable", lambda n: "/usr/bin/" + n)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(init_mod.Path, "cwd", staticmethod(lambda: proj))

    assert "VS Code (Copilot)" not in {c.name for c in init_mod._detect_clients()}

    (proj / ".vscode").mkdir()
    found = {c.name: c for c in init_mod._detect_clients()}
    assert found["VS Code (Copilot)"].method == "json_vscode"



@pytest.mark.parametrize("name,method", [("Codex", "toml_codex"), ("opencode", "json_opencode")])
def test_detected_when_config_dir_exists(tmp_path, monkeypatch, name, method):
    monkeypatch.setattr(init_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(init_mod, "_find_executable", lambda n: None)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)

    found = {c.name: c for c in init_mod._detect_clients()}

    assert name in found
    assert found[name].method == method


def test_detected_by_executable_before_the_config_dir_exists(tmp_path, monkeypatch):
    """A fresh install has the binary on PATH and no config directory yet."""
    monkeypatch.setattr(init_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(init_mod, "_find_executable", lambda n: "/usr/bin/" + n if n in ("codex", "opencode") else None)

    names = {c.name for c in init_mod._detect_clients()}

    assert {"Codex", "opencode"} <= names


@pytest.mark.parametrize("method,marker", [
    ("toml_codex", "[mcp_servers.jcodemunch]"),
    ("json_opencode", '"mcp"'),
    ("json_vscode", '"servers"'),
])
def test_configure_client_dispatches_each_method(tmp_path, monkeypatch, method, marker):
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: "/usr/local/bin/jcodemunch-mcp")
    cfg = tmp_path / "cfg"
    client = init_mod.MCPClient("X", cfg, method)

    msg = init_mod.configure_client(client, backup=False)

    assert "unknown method" not in msg
    assert marker in cfg.read_text(encoding="utf-8")


@pytest.mark.parametrize("method", sorted(init_mod.CONFIGURE_METHODS))
def test_every_declared_method_has_a_dispatch_branch(tmp_path, monkeypatch, method):
    """A method in the set with no branch falls through to "unknown method".

    Asserts the PROPERTY rather than a list of the methods that exist today,
    which is the failure this replaced: `test_detect_clients_returns_list`
    carried its own hardcoded tuple of methods and went red the moment a
    fourth was added. Parametrizing over the set means a new method is
    covered the moment it is declared, with no second place to update.
    """
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: "/usr/local/bin/jcodemunch-mcp")
    monkeypatch.setattr(init_mod, "_configure_claude_code", lambda *, dry_run=False: "  ran (stubbed)")
    client = init_mod.MCPClient("X", tmp_path / "cfg", method)

    msg = init_mod.configure_client(client, backup=False, dry_run=True)

    assert "unknown method" not in msg, f"{method} is declared but never dispatched"
