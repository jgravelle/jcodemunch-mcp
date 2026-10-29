"""Codex gets the bare command name only when PATH finds this same install.

Codex on Windows starts an MCP server named by its bare command through
PATH, and Windows is the only platform where that was tested, so the bare
name is written there only. A bare name keeps working after the install moves (a
`uv tool` reinstall, a new venv path), so init writes it when, and only
when, PATH resolves `jcodemunch-mcp` to the executable init would otherwise
write. If PATH finds a different install, or none, the absolute path stays:
a bare name there would start a different version, or nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp import install_layout
from jcodemunch_mcp.cli import init as init_mod

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_own_executable(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(install_layout, "_scripts_dir", lambda: None)
    monkeypatch.setattr(install_layout, "_bare_name_platform", lambda: True)


def _command(text: str) -> str:
    m = re.search(r"^command = (.+)$", text, re.MULTILINE)
    assert m, text
    return m.group(1)


def test_bare_name_when_path_finds_this_install(tmp_path, monkeypatch):
    exe = "/home/u/.local/bin/jcodemunch-mcp"
    monkeypatch.setattr(init_mod, "_codex_command", lambda: exe)
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: exe if name == "jcodemunch-mcp" else None)
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    assert _command(cfg.read_text(encoding="utf-8")) == "'jcodemunch-mcp'"


def test_bare_name_matches_a_windows_path_in_another_case(tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("case-insensitive paths are a Windows property")
    exe = r"C:\Users\u\.local\bin\jcodemunch-mcp.exe"
    monkeypatch.setattr(init_mod, "_codex_command", lambda: exe)
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: exe.upper())
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    assert _command(cfg.read_text(encoding="utf-8")) == "'jcodemunch-mcp'"


def test_absolute_path_when_path_finds_another_install(tmp_path, monkeypatch):
    mine = "/work/venv/bin/jcodemunch-mcp"
    theirs = "/usr/local/bin/jcodemunch-mcp"
    monkeypatch.setattr(init_mod, "_codex_command", lambda: mine)
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: theirs)
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    assert _command(cfg.read_text(encoding="utf-8")) == f"'{mine}'"


def test_absolute_path_when_path_finds_nothing(tmp_path, monkeypatch):
    mine = "/work/venv/bin/jcodemunch-mcp"
    monkeypatch.setattr(init_mod, "_codex_command", lambda: mine)
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: None)
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    assert _command(cfg.read_text(encoding="utf-8")) == f"'{mine}'"


def test_absolute_path_off_the_tested_platform(tmp_path, monkeypatch):
    """A client started from a GUI or a non-login shell can have a narrower
    PATH than init, so off the tested platform the working absolute entry is
    kept even when init's PATH finds the same install."""
    exe = "/home/u/.local/bin/jcodemunch-mcp"
    monkeypatch.setattr(install_layout, "_bare_name_platform", lambda: False)
    monkeypatch.setattr(init_mod, "_codex_command", lambda: exe)
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: exe)
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    assert _command(cfg.read_text(encoding="utf-8")) == f"'{exe}'"


def test_a_current_directory_hit_is_not_a_path_entry(tmp_path, monkeypatch):
    """Windows `which` before Python 3.12 looks in the current directory
    first. Running init from the scripts directory must not write a bare
    name that PATH itself cannot resolve."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    exe = scripts / "jcodemunch-mcp"
    exe.write_text("", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(scripts)
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setattr(init_mod, "_codex_command", lambda: str(exe))
    monkeypatch.setattr(init_mod.shutil, "which", lambda name: "." + "/" + name)
    cfg = tmp_path / "config.toml"

    init_mod._patch_codex_config(cfg, backup=False)

    assert _command(cfg.read_text(encoding="utf-8")) == f"'{exe}'"


def _codex_details() -> str:
    text = (ROOT / "CLIENTS.md").read_text(encoding="utf-8")
    start = text.index("<summary>Codex CLI config</summary>")
    return text[start:text.index("</details>", start)]


def test_the_uvx_example_raises_codex_startup_timeout():
    """Codex waits 10 s for a server to start by default (its MCP docs); a
    cold uvx run filled 251 MB of uv cache before its first reply. The example that uses uvx sets 30 s and
    cites the Codex page that states the default."""
    block = _codex_details()
    uvx = block[block.index('command = "uvx"'):]
    assert "startup_timeout_sec = 30" in uvx
    assert "learn.chatgpt.com/docs/extend/mcp" in block


def test_the_codex_docs_name_uv_tool_install():
    assert "uv tool install jcodemunch-mcp" in _codex_details()
