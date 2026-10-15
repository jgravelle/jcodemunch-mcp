"""Hooks and server entries written by `init` name the install that ran it.

`init` resolved the executable it writes into hook commands, the Codex server
entry and the post-upgrade hook refresh with `shutil.which("jcodemunch-mcp")`,
the first copy on PATH. A user with an older install on PATH who ran a newer
one by full path, from a venv or a `uv tool` environment, got hooks that call
the older version. The fourth reader, the reindex hook's re-invocation, used
`argv[0]`. All four now ask `install_layout.own_executable()`.
"""

from __future__ import annotations

import ast
import os
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
    exe.chmod(0o755)
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
    """uvx runs from uv's cache, which `uv cache clean` deletes; hooks there would break later.

    uvx also puts that cache directory FIRST on PATH, so the PATH lookup is
    real here: a mocked `which` would supply the lasting install the real
    one does not return.
    """
    cached = _make_install(tmp_path / "uv" / "cache" / "archive-v0" / "abc123")
    lasting = _make_install(tmp_path / "old")
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: cached.parent, raising=False
    )
    monkeypatch.setattr(sys, "argv", [str(cached)])
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(cached.parent), str(lasting.parent)])
    )
    assert _norm(shutil.which("jcodemunch-mcp")) == _norm(str(cached)), "uvx's order"
    assert install_layout.own_executable() is None
    assert _norm(init._hook_invocation()) == _norm(str(lasting))
    assert _norm(init._codex_command()) == _norm(str(lasting))


def test_only_uvx_on_path_writes_no_cached_path(tmp_path, monkeypatch):
    cached = _make_install(tmp_path / "uv" / "cache" / "archive-v0" / "abc123")
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: cached.parent, raising=False
    )
    monkeypatch.setattr(sys, "argv", [str(cached)])
    monkeypatch.setenv("PATH", str(cached.parent))
    assert init._hook_invocation() == "jcodemunch-mcp"
    assert init._codex_command() is None


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


_LOOKUPS = {"which", "_find_executable"}


def _names_our_executable(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "jcodemunch-mcp" in node.value
    if isinstance(node, ast.JoinedStr):
        return any(_names_our_executable(v) for v in node.values)
    if isinstance(node, ast.Name):
        return node.id == "_EXECUTABLE_NAME"
    return False


def _path_lookups_of_our_executable(source: str) -> list[int]:
    """Lines calling a PATH lookup (`shutil.which`, a bare `which`, a wrapper) for our name."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        )
        if name in _LOOKUPS and _names_our_executable(node.args[0]):
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize(
    "spelling",
    [
        'shutil.which("jcodemunch-mcp")',
        'shutil.which(\n    "jcodemunch-mcp"\n)',
        'which("jcodemunch-mcp")',
        'shutil.which("jcodemunch-mcp.exe")',
        'shutil.which(f"jcodemunch-mcp")',
        "shutil.which(_EXECUTABLE_NAME)",
        '_find_executable("jcodemunch-mcp")',
    ],
)
def test_the_scan_sees_every_spelling_of_the_lookup(spelling):
    assert _path_lookups_of_our_executable(f"x = {spelling}\n"), spelling


def test_the_scan_ignores_lookups_of_other_programs():
    assert not _path_lookups_of_our_executable(
        'shutil.which("claude")\n_find_executable("codex")\n'
    )


def test_no_reader_resolves_our_executable_from_path_directly():
    """A fifth reader would reproduce the defect; the authority is `install_layout`."""
    root = Path(init.__file__).resolve().parents[1]
    offenders = [
        f"{py.relative_to(root)}:{line}"
        for py in root.rglob("*.py")
        if py.name != "install_layout.py"
        for line in _path_lookups_of_our_executable(py.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_init_says_which_install_the_hooks_call_under_uvx(tmp_path, monkeypatch):
    """The substitute may be another version; saying nothing would hide that."""
    cached = _make_install(tmp_path / "uv" / "cache" / "archive-v0" / "abc123")
    lasting = _make_install(tmp_path / "old")
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: cached.parent, raising=False
    )
    monkeypatch.setattr(sys, "argv", [str(cached)])
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(cached.parent), str(lasting.parent)])
    )
    note = install_layout.substitution_note()
    assert note and str(lasting) in note and "uv tool install" in note
    monkeypatch.setenv("PATH", str(cached.parent))
    assert "bare name" in install_layout.substitution_note()


def test_no_note_when_the_hooks_call_the_install_that_ran(two_installs):
    assert install_layout.substitution_note() is None


def test_a_copy_in_the_current_directory_never_shadows_path(tmp_path, monkeypatch):
    """Windows `which` before 3.12 looks in the cwd first; the cwd is an untrusted checkout."""
    cached = _make_install(tmp_path / "uv" / "cache" / "archive-v0" / "abc123")
    lasting = _make_install(tmp_path / "old")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / _EXE).write_text("")
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(
        install_layout, "_scripts_dir", lambda: cached.parent, raising=False
    )
    monkeypatch.setattr(sys, "argv", [str(cached)])
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(cached.parent), str(lasting.parent)])
    )
    assert _norm(install_layout.executable_for_commands()) == _norm(str(lasting))


@pytest.mark.parametrize(
    "path,absolute",
    [
        ("/usr/local/bin/jcodemunch-mcp", True),
        ("C:" + chr(92) + "Program Files" + chr(92) + "jcodemunch-mcp.exe", True),
        ("C:/Python314/Scripts/jcodemunch-mcp.EXE", True),
        ("." + chr(92) + "jcodemunch-mcp.EXE", False),
        ("./jcodemunch-mcp", False),
        ("jcodemunch-mcp", False),
    ],
)
def test_a_cwd_hit_is_refused_and_a_drive_path_is_not_on_any_host(path, absolute):
    """The same answer on Linux CI and on Windows: the flavour is the path's, not the host's."""
    assert install_layout._is_absolute(path) is absolute
