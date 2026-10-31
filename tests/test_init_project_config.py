"""`init --project` writes MCP config a team commits (BACKLOG row 39).

`init` registers the server per user: every teammate has to run it. A config
committed to the repository reaches the whole team on their next pull. The
flag writes the project-level files the clients document (checked against
vendor docs 2026-10-31; design in the HQ note DESIGN-project-config.md) into
the git work tree's root, with the portable `uvx jcodemunch-mcp` launcher and
nothing machine-specific, and touches nothing under the home folder.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.cli import project_config as pc

ALWAYS = (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json")


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _read(root: Path, rel: str) -> dict:
    return json.loads((root / rel).read_text(encoding="utf-8"))


def _install(root, **kw):
    return pc.install_project_config(root, **kw)


def test_the_three_files_every_team_gets(repo):
    _install(repo)
    assert _read(repo, ".mcp.json")["mcpServers"]["jcodemunch"] == {
        "command": "uvx",
        "args": ["jcodemunch-mcp"],
    }
    assert _read(repo, ".cursor/mcp.json")["mcpServers"]["jcodemunch"] == {
        "type": "stdio",
        "command": "uvx",
        "args": ["jcodemunch-mcp"],
    }
    # VS Code's own file keys servers under `servers`, not `mcpServers`.
    assert _read(repo, ".vscode/mcp.json")["servers"]["jcodemunch"] == {
        "type": "stdio",
        "command": "uvx",
        "args": ["jcodemunch-mcp"],
    }


def test_a_client_folder_nobody_uses_is_not_created(repo):
    _install(repo)
    for rel in (".junie", ".gemini", ".devin", "opencode.json"):
        assert not (repo / rel).exists(), rel


def test_a_client_the_repo_already_uses_gets_its_file(repo):
    (repo / ".junie").mkdir()
    (repo / ".gemini").mkdir()
    (repo / ".devin").mkdir()
    (repo / "opencode.json").write_text("{}\n", encoding="utf-8")
    _install(repo)
    assert "jcodemunch" in _read(repo, ".junie/mcp/mcp.json")["mcpServers"]
    assert "jcodemunch" in _read(repo, ".gemini/settings.json")["mcpServers"]
    assert "jcodemunch" in _read(repo, ".devin/mcp_config.json")["mcpServers"]
    # opencode keys servers under `mcp` and takes the command as one array.
    assert _read(repo, "opencode.json")["mcp"]["jcodemunch"] == {
        "type": "local",
        "command": ["uvx", "jcodemunch-mcp"],
    }


def test_project_all_writes_every_file(repo):
    _install(repo, all_files=True)
    for rel in ALWAYS + (
        ".junie/mcp/mcp.json",
        ".gemini/settings.json",
        ".devin/mcp_config.json",
        "opencode.json",
    ):
        assert (repo / rel).is_file(), rel


def test_other_servers_and_keys_are_kept(repo):
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "extra": 1}),
        encoding="utf-8",
    )
    (repo / ".gemini").mkdir()
    (repo / ".gemini" / "settings.json").write_text(
        json.dumps({"theme": "dark"}), encoding="utf-8"
    )
    _install(repo)
    data = _read(repo, ".mcp.json")
    assert data["extra"] == 1 and "other" in data["mcpServers"]
    assert "jcodemunch" in data["mcpServers"]
    assert _read(repo, ".gemini/settings.json")["theme"] == "dark"


def test_an_existing_entry_is_kept_as_written(repo):
    """A team may have pinned it on purpose."""
    pinned = {"command": "uvx", "args": ["jcodemunch-mcp==1.2.3"]}
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"jcodemunch": pinned}}), encoding="utf-8"
    )
    _install(repo)
    assert _read(repo, ".mcp.json")["mcpServers"]["jcodemunch"] == pinned


def test_a_second_run_changes_nothing(repo):
    first = _install(repo)
    snapshot = {rel: (repo / rel).read_bytes() for rel in ALWAYS}
    second = _install(repo)
    assert {rel: (repo / rel).read_bytes() for rel in ALWAYS} == snapshot
    assert first.changed and not second.changed


def test_a_file_that_does_not_parse_is_refused_not_rewritten(repo):
    (repo / ".mcp.json").write_text("{not json", encoding="utf-8")
    result = _install(repo)
    assert (repo / ".mcp.json").read_text(encoding="utf-8") == "{not json"
    assert ".mcp.json" in " ".join(result.refused)


def test_a_file_with_comments_is_left_untouched_and_the_entry_printed(repo):
    text = '{\n  // our servers\n  "servers": {}\n}\n'
    (repo / ".vscode").mkdir()
    (repo / ".vscode" / "mcp.json").write_text(text, encoding="utf-8")
    result = _install(repo)
    assert (repo / ".vscode" / "mcp.json").read_text(encoding="utf-8") == text
    assert any("jcodemunch-mcp" in line for line in result.manual)


def test_no_written_file_names_this_machine(repo):
    _install(repo, all_files=True)
    home = str(Path.home())
    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            text = path.read_text(encoding="utf-8")
            assert home not in text and home.replace("\\", "/") not in text, path
            assert ":\\" not in text and ":/" not in text, path


def test_dry_run_writes_nothing(repo):
    result = _install(repo, dry_run=True)
    assert result.changed
    for rel in ALWAYS:
        assert not (repo / rel).exists()


def test_the_root_is_the_top_of_the_work_tree(repo):
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    assert pc.repo_root(sub) == repo.resolve()


def test_outside_a_repo_there_is_no_root(tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    assert pc.repo_root(lone) is None


def test_init_project_refuses_outside_a_repo(tmp_path, monkeypatch, capsys):
    from jcodemunch_mcp.cli import init

    lone = tmp_path / "lone"
    lone.mkdir()
    monkeypatch.chdir(lone)
    monkeypatch.setattr(pc, "repo_root", lambda start=None: None)
    assert init.run_init(project=True, yes=True) != 0
    assert not (lone / ".mcp.json").exists()


def test_init_project_writes_only_the_repo(repo, tmp_path, monkeypatch, capsys):
    """Nothing under home: no client registration, hooks or policy."""
    from jcodemunch_mcp.cli import init

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(repo)
    assert init.run_init(project=True, yes=True, no_backup=True) == 0
    assert list(home.rglob("*")) == []
    assert (repo / ".mcp.json").is_file()
    out = capsys.readouterr().out
    assert "git add" in out and ".mcp.json" in out
