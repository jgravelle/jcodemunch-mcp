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


def test_the_message_names_the_install_command_for_this_install(no_watchfiles):
    msg = watcher._watchfiles_missing_msg()
    assert "Install it with:" in msg


def test_serve_watcher_flag_gives_the_same_install_line(monkeypatch, capsys, no_watchfiles):
    """--watcher used to hardcode the pip line; it asks the same helper now."""
    monkeypatch.setattr(upgrade, "detect_install_mechanism", lambda: ("pipx", "x"))
    with pytest.raises(SystemExit) as exc:
        server.main(["serve", "--watcher"])
    assert exc.value.code == 1
    assert "Install it with: pipx inject jcodemunch-mcp watchfiles" in capsys.readouterr().err


def test_only_the_helper_spells_the_pip_fallback():
    """One helper owns the install line; no other module hardcodes the pip form."""
    import pathlib

    src = pathlib.Path(server.__file__).parent
    hits = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "install 'jcodemunch-mcp[watch]'\"" in p.read_text(encoding="utf-8")
    ]
    assert sorted(hits) == ["cli/upgrade.py", "watcher.py"], hits


def test_the_check_passes_when_watchfiles_is_present():
    pytest.importorskip("watchfiles")
    watcher.exit_unless_watchfiles("watch")  # returns, does not exit
