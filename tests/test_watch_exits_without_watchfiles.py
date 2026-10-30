"""`watch` and `watch-all` exit when they cannot watch (backlog 28).

With the optional `watchfiles` package missing, standalone `jcodemunch-mcp
watch` printed FATAL for each folder from inside that folder's task, after the
manager had started, and then kept running with nothing watched. A user who did
not read the console believed the index was being kept fresh. Both commands now
check once, before starting anything, print the install line for the user's
install method, and exit 1, as the server's `--watcher` flag already did.

The server's config-enabled watcher still only warns: it embeds the same code,
and the server must keep answering without it. Not changed here.
"""

from __future__ import annotations

import builtins

import pytest

from jcodemunch_mcp import server, watcher
from jcodemunch_mcp.cli import upgrade


@pytest.fixture
def no_watchfiles(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "watchfiles" or name.startswith("watchfiles."):
            raise ImportError("No module named 'watchfiles'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)


@pytest.fixture
def nothing_starts(monkeypatch):
    """Fail the test if a watch loop is entered at all."""
    import jcodemunch_mcp.watch_all as watch_all_mod

    async def refuse(*a, **k):
        pytest.fail("a watch loop started without watchfiles")

    monkeypatch.setattr(watcher, "watch_folders", refuse)
    monkeypatch.setattr(watch_all_mod, "watch_all", refuse)


@pytest.mark.parametrize("argv", [["watch", "{tmp}"], ["watch-all"]])
def test_watch_without_watchfiles_exits_1_with_the_install_line(argv, tmp_path, monkeypatch, capsys,
                                                                 no_watchfiles, nothing_starts):
    monkeypatch.setattr(upgrade, "detect_install_mechanism", lambda: ("pipx", "x"))
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    with pytest.raises(SystemExit) as exc:
        server.main([a.replace("{tmp}", str(tmp_path)) for a in argv])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"jcodemunch-mcp {argv[0]}: not started" in err
    assert "Install it with: pipx inject jcodemunch-mcp watchfiles" in err


def test_watch_install_refuses_before_registering_a_service(monkeypatch, capsys, no_watchfiles):
    """The service runs watch-all and restarts it on failure, so registering
    it without watchfiles would start an endless exit-and-restart loop."""
    import jcodemunch_mcp.service_installer as si

    monkeypatch.setattr(si, "install_service", lambda: pytest.fail("a service was registered"))
    monkeypatch.setattr(upgrade, "detect_install_mechanism", lambda: ("pipx", "x"))
    with pytest.raises(SystemExit) as exc:
        server.main(["watch-install"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "jcodemunch-mcp watch-install: not started" in err
    assert "Install it with: pipx inject jcodemunch-mcp watchfiles" in err


def test_the_message_names_the_install_command_for_this_install(monkeypatch):
    monkeypatch.setattr(upgrade, "detect_install_mechanism", lambda: ("pipx", "x"))
    msg = watcher._watchfiles_missing_msg()
    assert msg.endswith("Install it with: pipx inject jcodemunch-mcp watchfiles")


def test_serve_watcher_flag_gives_the_same_install_line(monkeypatch, capsys, no_watchfiles):
    """--watcher used to hardcode the pip line; it asks the same helper now."""
    monkeypatch.setattr(upgrade, "detect_install_mechanism", lambda: ("pipx", "x"))
    with pytest.raises(SystemExit) as exc:
        server.main(["serve", "--watcher"])
    assert exc.value.code == 1
    assert "Install it with: pipx inject jcodemunch-mcp watchfiles" in capsys.readouterr().err


def test_only_the_helper_spells_the_pip_fallback():
    """One helper owns the install line; no other module hardcodes the pip form.

    Reads string constants from the AST, so a line split across adjacent
    literals is joined before the check, and docstrings are skipped.
    """
    import ast
    import pathlib

    src = pathlib.Path(server.__file__).parent
    hits = set()
    for p in src.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        docs = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(getattr(n.body[0], "value", None), ast.Constant)
        }
        for n in ast.walk(tree):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in docs and "jcodemunch-mcp[watch]" in n.value):
                hits.add(p.relative_to(src).as_posix())
    assert sorted(hits) == ["cli/upgrade.py", "watcher.py"], sorted(hits)


def test_watch_once_still_runs_without_watchfiles(tmp_path, monkeypatch, no_watchfiles):
    """`watch --once` is a one-shot sync and never needed watchfiles; the check
    must stay below its branch."""
    ran = []

    async def fake_sync(**kwargs):
        ran.append(kwargs["paths"])

    monkeypatch.setattr(watcher, "sync_folders", fake_sync)
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    server.main(["watch", "--once", str(tmp_path)])
    assert ran == [[str(tmp_path)]]


def test_the_check_passes_when_watchfiles_is_present():
    pytest.importorskip("watchfiles")
    watcher.exit_unless_watchfiles("watch")  # returns, does not exit
