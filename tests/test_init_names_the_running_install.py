"""Hooks and server entries written by `init` name the install that ran it.

`init` resolved the executable it writes into hook commands, the Codex server
entry and the post-upgrade hook refresh with `shutil.which("jcodemunch-mcp")`,
the first copy on PATH. A user with an older install on PATH who ran a newer
one by full path, from a venv or a `uv tool` environment, got hooks that call
the older version. The fourth reader, the reindex hook's re-invocation, used
`argv[0]`. All four now ask `install_layout.own_executable()`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp import install_layout
from jcodemunch_mcp.cli import init, upgrade
from jcodemunch_mcp.cli.hooks import reindex

_EXE = "jcodemunch-mcp.exe" if sys.platform == "win32" else "jcodemunch-mcp"


def _make_install(root: Path) -> Path:
    scripts = root / ("Scripts" if sys.platform == "win32" else "bin")
    scripts.mkdir(parents=True)
    exe = scripts / _EXE
    exe.write_text("")
    return exe


@pytest.fixture
def two_installs(tmp_path, monkeypatch):
    """The running install is a venv; an older install is first on PATH."""
    running = _make_install(tmp_path / "new venv")
    on_path = _make_install(tmp_path / "old")
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: running.parent, raising=False
    )
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(shutil, "which", lambda name: str(on_path))
    return running, on_path


def _norm(p: str) -> str:
    return p.strip('"').replace("\\", "/").lower()


def test_hook_commands_name_the_running_install(two_installs):
    running, on_path = two_installs
    assert _norm(init._hook_invocation()) == _norm(str(running))


def test_every_written_hook_names_the_running_install(two_installs):
    running, _ = two_installs
    for group in (init._worktree_hooks(), init._enforcement_hooks()):
        for rules in group.values():
            for rule in rules:
                for h in rule["hooks"]:
                    assert _norm(str(running)) in _norm(h["command"]), h["command"]


def test_the_codex_entry_names_the_running_install(two_installs):
    running, _ = two_installs
    assert _norm(init._codex_command()) == _norm(str(running))


def test_the_post_upgrade_hook_refresh_runs_the_running_install(
    two_installs, monkeypatch
):
    running, _ = two_installs
    seen = []

    class _R:
        returncode = 0

    monkeypatch.setattr(
        upgrade.subprocess, "run", lambda cmd, check=False: seen.append(cmd) or _R()
    )
    upgrade._refresh_hooks(yes=True)
    assert _norm(seen[0][0]) == _norm(str(running))


def test_the_reindex_hook_reinvokes_the_running_install(two_installs):
    running, _ = two_installs
    assert _norm(reindex._self_invocation()[0]) == _norm(str(running))


def test_a_launch_path_wins_over_the_scripts_directory(tmp_path, monkeypatch):
    """`uv tool` launches through a shim beside the venv; the path launched is the one to keep."""
    shim = _make_install(tmp_path / "shims")
    venv = _make_install(tmp_path / "tool venv")
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: venv.parent, raising=False
    )
    monkeypatch.setattr(sys, "argv", [str(shim)])
    assert _norm(install_layout.own_executable()) == _norm(str(shim))


def test_a_relative_launch_path_is_never_trusted(tmp_path, monkeypatch):
    """A relative argv[0] would resolve against the hook's cwd, an untrusted checkout."""
    venv = _make_install(tmp_path / "venv")
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: venv.parent, raising=False
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / _EXE).write_text("")
    monkeypatch.setattr(sys, "argv", [_EXE])
    assert _norm(install_layout.own_executable()) == _norm(str(venv))


def test_an_ephemeral_uvx_environment_is_not_written_into_hooks(tmp_path, monkeypatch):
    """uvx runs from uv's cache, which `uv cache clean` deletes; hooks there would break later."""
    cached = _make_install(tmp_path / "uv" / "cache" / "archive-v0" / "abc123")
    on_path = _make_install(tmp_path / "old")
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: cached.parent, raising=False
    )
    monkeypatch.setattr(sys, "argv", [str(cached)])
    monkeypatch.setattr(shutil, "which", lambda name: str(on_path))
    assert install_layout.own_executable() is None
    assert _norm(init._hook_invocation()) == _norm(str(on_path))


def test_no_executable_of_our_own_falls_back_as_before(tmp_path, monkeypatch):
    """Run from source with no console script installed: PATH, then the bare name."""
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: tmp_path / "nothing", raising=False
    )
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert install_layout.own_executable() is None
    assert init._hook_invocation() == "jcodemunch-mcp"
    assert init._codex_command() is None
    assert reindex._self_invocation()[1:] == ["-m", "jcodemunch_mcp"]


def test_no_reader_resolves_our_executable_from_path_directly():
    """A fifth reader would reproduce the defect; the authority is `own_executable`."""
    import re

    root = Path(init.__file__).resolve().parents[1]
    offenders = []
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in re.finditer(
            r"""shutil\.which\(\s*["']jcodemunch-mcp["']\s*\)""", text
        ):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{py.relative_to(root)}:{line}")
    allowed = {"install_layout.py"}
    offenders = [
        o
        for o in offenders
        if o.split(":")[0].replace("\\", "/").split("/")[-1] not in allowed
    ]
    assert not offenders, offenders
