"""A stdio server whose client is gone exits, even if its stdin stays open.

`serve` exited only on stdin EOF. When the client that started it dies while
something else holds the pipe open (a proxy, an inherited handle), the server
kept running with nobody to answer. The watch is in `parent_watch.py`; these
tests drive it directly and through a real server process.

The integration tests own every process they start: the server runs with
CODE_INDEX_PATH in a temp directory (Practice 8), and both processes are killed
in a `finally` whatever the outcome.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from jcodemunch_mcp import parent_watch
from jcodemunch_mcp.storage.process_locks import _is_pid_alive


# -- the setting ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, parent_watch.DEFAULT_SECONDS),
        ("2.5", 2.5),
        ("0", 0.0),
        ("-1", 0.0),
        ("abc", parent_watch.DEFAULT_SECONDS),  # a typo keeps the default
        ("", parent_watch.DEFAULT_SECONDS),
    ],
)
def test_the_interval_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("JCODEMUNCH_PARENT_WATCH_SECONDS", raising=False)
    else:
        monkeypatch.setenv("JCODEMUNCH_PARENT_WATCH_SECONDS", raw)
    assert parent_watch.interval_seconds() == expected


# -- the check -----------------------------------------------------------------


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_a_live_parent_is_not_gone():
    w = parent_watch.ParentWatch.of(os.getppid())
    assert w is not None
    assert w.gone() is False


def test_a_dead_parent_is_gone(monkeypatch):
    pid = _dead_pid()
    monkeypatch.setattr(parent_watch.os, "getppid", lambda: pid)
    # Recorded while alive in real use; here the anchor is supplied directly.
    w = parent_watch.ParentWatch(pid, anchor=None, started_ppid=pid)
    assert w.gone() is True


def test_a_reused_pid_is_gone(monkeypatch):
    """The parent's pid now names another process: its creation time differs."""
    me = os.getpid()
    monkeypatch.setattr(parent_watch.os, "getppid", lambda: me)
    w = parent_watch.ParentWatch(me, anchor=1.0, started_ppid=me)
    assert w.gone() is True


def test_a_reparented_process_sees_its_parent_gone(monkeypatch):
    """Unix gives an orphan a new parent (init or a subreaper)."""
    w = parent_watch.ParentWatch.of(os.getppid())
    assert w is not None
    monkeypatch.setattr(parent_watch.os, "getppid", lambda: 1)
    assert w.gone() is True


def test_a_parent_already_dead_at_start_is_gone():
    """The client died while the server was still importing."""
    w = parent_watch.ParentWatch.of(_dead_pid())
    assert w is not None and w.gone() is True


def test_a_parent_younger_than_us_is_a_reused_pid(monkeypatch):
    """A parent always starts before its child, so a younger process holding
    the parent's PID is not our parent."""
    me = os.getpid()
    monkeypatch.setattr(
        parent_watch, "_process_create_time", lambda pid: 100.0 if pid == me else 200.0
    )
    w = parent_watch.ParentWatch.of(os.getppid())
    assert w is not None and w.gone() is True


def test_an_unqueryable_parent_disables_the_watch(monkeypatch):
    assert parent_watch.ParentWatch.of(0) is None
    assert parent_watch.ParentWatch.of(1) is None
    # Cannot be opened, and the OS does not say it is gone: access denied.
    monkeypatch.setattr(parent_watch, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(parent_watch, "_no_such_process", lambda pid: False)
    assert parent_watch.ParentWatch.of(os.getppid()) is None


def test_a_failed_liveness_poll_is_not_gone(monkeypatch):
    """Review round 2: on Windows `_is_pid_alive` also reads False for access
    denied or a failed OpenProcess. While polling, that is uncertainty, and
    uncertainty never exits."""
    w = parent_watch.ParentWatch.of(os.getppid())
    assert w is not None
    monkeypatch.setattr(parent_watch, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(parent_watch, "_no_such_process", lambda pid: False)
    assert w.gone() is False


class _Recorder:
    def __init__(self):
        self.exit_codes = []
        self.handler_runs = 0
        self.gates = []


def _forced_exit_env(monkeypatch, rec):
    import atexit

    from jcodemunch_mcp import server

    # Never touch the test process's real exit handlers.
    monkeypatch.setattr(
        atexit, "register", lambda fn, *a, **k: rec.gates.append(fn) or fn
    )

    def _run():
        rec.handler_runs += 1
        for gate in rec.gates:
            gate()

    monkeypatch.setattr(atexit, "_run_exitfuncs", _run)
    return server


class _NoLoop:
    def call_soon_threadsafe(self, fn):
        fn()


class _Task:
    cancelled = False

    def cancel(self):
        self.cancelled = True


def test_a_broken_stderr_does_not_stop_the_exit(monkeypatch):
    """Review round 2: the client that died may have held the read end of the
    server's stderr. The notice then raises, and the watch thread swallowed
    the raise, so the server never exited."""
    import threading

    rec = _Recorder()
    server = _forced_exit_env(monkeypatch, rec)

    class _Broken:
        def write(self, s):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(sys, "stderr", _Broken())
    task, event = _Task(), threading.Event()
    server._exit_for_gone_parent(
        task, _NoLoop(), event, sleep=lambda s: None, hard_exit=rec.exit_codes.append
    )
    assert event.is_set() and task.cancelled
    assert rec.exit_codes == [0]
    assert rec.handler_runs == 1


def test_the_exit_handlers_run_once_when_shutdown_races_the_forced_exit(monkeypatch):
    """Review round 2: stdin EOF inside the grace sends the main thread into its
    own shutdown, which runs the same handlers. The gate it reaches first
    makes the forced path stand down."""
    import threading

    rec = _Recorder()
    server = _forced_exit_env(monkeypatch, rec)

    def _main_thread_shuts_down(_seconds):
        t = threading.Thread(target=lambda: [g() for g in rec.gates])
        t.start()
        t.join(5)

    server._exit_for_gone_parent(
        None,
        _NoLoop(),
        threading.Event(),
        sleep=_main_thread_shuts_down,
        hard_exit=rec.exit_codes.append,
    )
    assert rec.handler_runs == 0
    assert rec.exit_codes == []


def test_the_launcher_walk_never_crosses_a_client(monkeypatch):
    """Only a console-script shim, a venv redirector or a pass-through runner
    is stepped over; a Python client running on the base interpreter is not.

    The walk runs only on Windows; every Windows call is faked here, so the
    Windows path runs on every platform rather than skipping (a skip counts
    against the CI skip ceiling)."""
    images = {10: r"C:\venv\Scripts\jcodemunch-mcp.exe", 9: r"C:\Python\python.exe"}
    parents = {10: 9, 9: 8}
    monkeypatch.setattr(parent_watch.os, "getppid", lambda: 10)
    monkeypatch.setattr(parent_watch, "_win_image_path", lambda pid: images.get(pid))
    monkeypatch.setattr(parent_watch, "_win_parent_pid", lambda pid: parents.get(pid))
    monkeypatch.setattr(
        parent_watch,
        "_launcher_images",
        lambda: {parent_watch._norm(r"C:\venv\Scripts\jcodemunch-mcp.exe")},
    )
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        walked = parent_watch.watched_parent()
    assert walked == 9


def test_a_check_that_cannot_be_made_counts_as_alive(monkeypatch):
    w = parent_watch.ParentWatch.of(os.getppid())
    assert w is not None

    def boom(_pid):
        raise OSError("no access")

    monkeypatch.setattr(parent_watch, "_is_pid_alive", boom)
    assert w.gone() is False


def test_start_does_nothing_when_disabled(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARENT_WATCH_SECONDS", "0")
    assert parent_watch.start(lambda: None) is None


def test_start_calls_back_once_the_parent_is_gone(monkeypatch):
    pid = _dead_pid()
    monkeypatch.setattr(
        parent_watch.ParentWatch,
        "of",
        classmethod(lambda cls, ppid: cls(pid, anchor=None, started_ppid=pid)),
    )
    calls = []
    t = parent_watch.start(lambda: calls.append(1), interval=0.05)
    assert t is not None and t.daemon
    t.join(timeout=5)
    assert calls == [1]


def test_a_parent_changed_since_import_is_gone(monkeypatch):
    """Unix reparents an orphan; a parent other than the one recorded at
    import means the client died before the watch started."""
    monkeypatch.setattr(parent_watch, "ORIGIN_PPID", os.getppid() + 100000)
    calls = []
    t = parent_watch.start(lambda: calls.append(1), interval=0.05)
    assert t is not None
    t.join(timeout=5)
    assert calls == [1]


def test_the_disclosed_interval_is_the_default():
    """SECURITY.md and CLI-AND-ENV.md quote the default; bind them to it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    n = f"{parent_watch.DEFAULT_SECONDS:g}"
    assert f"every {n} seconds" in (root / "SECURITY.md").read_text(encoding="utf-8")
    env_md = (root / "CLI-AND-ENV.md").read_text(encoding="utf-8")
    assert f"| `{parent_watch.ENV}` | {n} |" in env_md


# -- a real server ---------------------------------------------------------------

# The parent starts the server with ITS OWN stdin, which is a pipe whose write
# end the test holds. Killing the parent therefore leaves the server's stdin
# open: no EOF ever arrives, so only the parent watch can end it.
_PARENT = textwrap.dedent(
    """
    import subprocess, sys, time
    child = subprocess.Popen(
        [sys.executable, "-m", "jcodemunch_mcp", "serve"],
        stdout=subprocess.DEVNULL, stderr=open(sys.argv[1], "w"),
    )
    print(child.pid, flush=True)
    time.sleep(600)
    """
)


def _wait_until(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def _kill(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def _start_parent(tmp_path, interval: str):
    env = dict(os.environ)
    env.update(
        CODE_INDEX_PATH=str(tmp_path / "index"),
        JCODEMUNCH_PARENT_WATCH_SECONDS=interval,
        JCODEMUNCH_SHARE_SAVINGS="0",
        JCODEMUNCH_HANDSHAKE_TIMEOUT="0",
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT, str(tmp_path / "server.err")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=str(tmp_path),
        env=env,
    )
    line = parent.stdout.readline().decode().strip()
    assert line.isdigit(), f"parent printed {line!r}"
    return parent, int(line)


def _presence_files(tmp_path) -> list:
    d = tmp_path / "index" / "_processes"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def _err(tmp_path) -> str:
    try:
        return (tmp_path / "server.err").read_text()
    except OSError:
        return ""


def test_a_server_whose_parent_is_killed_exits(tmp_path):
    parent, server = _start_parent(tmp_path, "0.5")
    try:
        # Ready: the server has registered its presence file, which happens
        # after its imports and before the stdio loop. No fixed sleep.
        assert _wait_until(lambda: _presence_files(tmp_path), 60), _err(tmp_path)
        assert _is_pid_alive(server), _err(tmp_path)
        parent.kill()
        parent.wait(timeout=10)
        # parent.stdin is still open here: the server sees no EOF.
        exited = _wait_until(lambda: not _is_pid_alive(server), 20)
        assert exited, "the server outlived its parent with stdin still open"
        # The exit ran the exit handlers: the presence file is gone.
        assert _presence_files(tmp_path) == [], _err(tmp_path)
    finally:
        _kill(parent.pid)
        _kill(server)
        try:
            parent.stdin.close()
        except Exception:
            pass


def test_a_server_whose_parent_dies_during_its_startup_exits(tmp_path):
    """Review round 1: on Unix the orphan is reparented before the watch
    starts, so the watch must compare against the parent recorded at import.

    The parent dies once the server has written its presence file: the server
    has imported `parent_watch` (so it has recorded its parent) and may not
    have started the watch yet. Killing it any earlier races the interpreter's
    own startup, before any of our code runs, and on Linux the orphan then
    belongs to init or a subreaper before there is anything to record."""
    parent, server = _start_parent(tmp_path, "0.5")
    try:
        assert _wait_until(lambda: _presence_files(tmp_path), 60), _err(tmp_path)
        parent.kill()
        parent.wait(timeout=10)
        exited = _wait_until(lambda: not _is_pid_alive(server), 60)
        assert exited, "a server whose client died during its startup lingered"
    finally:
        _kill(parent.pid)
        _kill(server)
        try:
            parent.stdin.close()
        except Exception:
            pass


def test_a_server_whose_parent_lives_keeps_running(tmp_path):
    parent, server = _start_parent(tmp_path, "0.5")
    try:
        assert _wait_until(lambda: _presence_files(tmp_path), 60), _err(tmp_path)
        time.sleep(3)  # six poll intervals
        assert _is_pid_alive(server), _err(tmp_path)
    finally:
        _kill(server)
        _kill(parent.pid)
        try:
            parent.stdin.close()
        except Exception:
            pass
