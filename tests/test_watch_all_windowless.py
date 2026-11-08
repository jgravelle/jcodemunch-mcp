"""BACKLOG row 33, second half: under pythonw, the watcher's own children open no window.

A pythonw process has no console, so Windows gives every console program it
starts (git, python) a new, visible console window. `watch-all` started by the
sign-in task re-runs itself in a `python.exe` with CREATE_NO_WINDOW; children
of that process share its window-less console. Its stdout and stderr go to the
log file, so the watcher's warning lines are kept (pythonw has no stderr).

The live test runs only on Windows, where it starts a real pythonw and asks a
grandchild whether its console has a visible window. It is defined there and
nowhere else, so it spends no skip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp import windowless as wl


def _interpreters(tmp_path: Path, *, console: bool = True) -> Path:
    d = tmp_path / "Py"
    d.mkdir()
    (d / "pythonw.exe").write_bytes(b"")
    if console:
        (d / "python.exe").write_bytes(b"")
    return d


class TestWhoRelaunches:
    def test_pythonw_with_python_beside_it_names_the_console_interpreter(
        self, tmp_path
    ):
        d = _interpreters(tmp_path)
        assert wl.console_interpreter(str(d / "pythonw.exe")) == d / "python.exe"

    def test_the_console_interpreter_itself_does_not_relaunch(self, tmp_path):
        d = _interpreters(tmp_path)
        assert wl.console_interpreter(str(d / "python.exe")) is None

    def test_pythonw_alone_does_not_relaunch(self, tmp_path):
        d = _interpreters(tmp_path, console=False)
        assert wl.console_interpreter(str(d / "pythonw.exe")) is None

    def test_off_windows_nothing_relaunches(self, tmp_path):
        d = _interpreters(tmp_path)
        with (
            patch.object(wl.sys, "platform", "linux"),
            patch.object(wl.sys, "executable", str(d / "pythonw.exe")),
            patch.object(wl, "spawn_hidden") as spawn,
        ):
            assert wl.run_with_hidden_console(["watch-all"], tmp_path / "x.log") is None
        spawn.assert_not_called()


class TestTheChildCommand:
    @pytest.mark.parametrize(
        "argv",
        [
            ["watch-all", "--log-file", "C:/logs/w.log", "--debounce", "5"],
            ["watch-all", "--log-file=C:/logs/w.log", "--debounce", "5"],
        ],
    )
    def test_the_log_file_is_taken_off_and_everything_else_kept(self, argv):
        args, log_file = wl.child_argv(argv)

        assert args == ["watch-all", "--debounce", "5"]
        assert log_file == "C:/logs/w.log"

    def test_the_child_runs_the_same_command_with_its_output_in_the_log(self, tmp_path):
        d = _interpreters(tmp_path)
        log = tmp_path / "logs" / "watch-all.log"
        seen = {}

        class _Proc:
            def wait(self):
                return 7

        def _spawn(cmd, fh):
            seen["cmd"] = cmd
            seen["log"] = Path(fh.name)
            seen["mode"] = fh.mode
            return _Proc()

        with (
            patch.object(wl.sys, "platform", "win32"),
            patch.object(wl.sys, "executable", str(d / "pythonw.exe")),
            patch.object(wl, "spawn_hidden", side_effect=_spawn),
        ):
            code = wl.run_with_hidden_console(
                ["watch-all", "--log-file", str(log), "--debounce", "5"],
                tmp_path / "default.log",
            )

        assert code == 7
        assert seen["cmd"] == [
            str(d / "python.exe"),
            "-m",
            "jcodemunch_mcp",
            "watch-all",
            "--debounce",
            "5",
        ]
        assert seen["log"] == log and seen["mode"] == "ab"

    def test_without_a_log_file_argument_the_default_log_is_used(self, tmp_path):
        d = _interpreters(tmp_path)
        seen = {}

        class _Proc:
            def wait(self):
                return 0

        def _spawn(cmd, fh):
            seen["log"] = Path(fh.name)
            return _Proc()

        with (
            patch.object(wl.sys, "platform", "win32"),
            patch.object(wl.sys, "executable", str(d / "pythonw.exe")),
            patch.object(wl, "spawn_hidden", side_effect=_spawn),
        ):
            wl.run_with_hidden_console(["watch-all"], tmp_path / "default.log")

        assert seen["log"] == tmp_path / "default.log"

    def test_the_spawn_asks_for_no_window_and_ties_the_child_to_us(self, tmp_path):
        calls = {}
        with (
            patch.object(wl.subprocess, "Popen") as popen,
            patch.object(
                wl, "_kill_with_parent", lambda proc: calls.setdefault("tied", proc)
            ),
        ):
            with open(tmp_path / "l.log", "ab") as fh:
                proc = wl.spawn_hidden(["x"], fh)

        kwargs = popen.call_args.kwargs
        assert kwargs["creationflags"] & 0x08000000
        assert kwargs["stdout"] is kwargs["stderr"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert calls["tied"] is proc


class TestTheCliDispatch:
    def test_a_relaunched_watch_all_exits_with_the_childs_code(self):
        from jcodemunch_mcp import server

        with (
            patch("jcodemunch_mcp.windowless.run_with_hidden_console", return_value=3),
            patch("jcodemunch_mcp.watch_all.watch_all") as watch,
        ):
            with pytest.raises(SystemExit) as exc:
                server.main(["watch-all"])

        assert exc.value.code == 3
        watch.assert_not_called()

    def test_an_ordinary_watch_all_runs_in_process(self):
        from jcodemunch_mcp import server

        async def _done(**kwargs):
            return None

        with (
            patch(
                "jcodemunch_mcp.windowless.run_with_hidden_console", return_value=None
            ),
            patch("jcodemunch_mcp.watch_all.watch_all", side_effect=_done) as watch,
        ):
            server.main(["watch-all"])

        watch.assert_called_once()


_GRANDCHILD = (
    "import ctypes,json;k=ctypes.windll.kernel32;u=ctypes.windll.user32;"
    "h=k.GetConsoleWindow();"
    "print(json.dumps({'visible':bool(h and u.IsWindowVisible(h))}))"
)

# Run under pythonw: start a python.exe the way watch-all does, and have that
# process start a plain grandchild, as the watcher starts git.
# argv: <out log> <grandchild script>
_PARENT = """
import sys
from pathlib import Path
from jcodemunch_mcp import windowless as wl
out, grandchild = Path(sys.argv[1]), sys.argv[2]
child = "import subprocess,sys;r=subprocess.run([sys.executable,sys.argv[1]],capture_output=True,text=True);print(r.stdout.strip())"
with open(out, "ab") as log:
    wl.spawn_hidden([str(Path(sys.executable).with_name("python.exe")), "-c", child, grandchild], log).wait()
"""

# Run under pythonw: start a long-lived hidden child the way watch-all does, and
# wait on it, as the watch-all parent does. argv: <out log> <child pid file>
_LONG_PARENT = """
import sys
from pathlib import Path
from jcodemunch_mcp import windowless as wl
out, pid_file = Path(sys.argv[1]), sys.argv[2]
child = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(300)"
with open(out, "ab") as log:
    wl.spawn_hidden([str(Path(sys.executable).with_name("python.exe")), "-c", child, pid_file], log).wait()
"""

_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102


# The BASE interpreter's pythonw, never a venv's: a venv pythonw.exe is a
# launcher that puts its own child in a job, which would end the watcher on its
# own and let the job test pass without ours. windowless.py imports only the
# standard library, so the base interpreter can run it from src/.
_BASE_PYTHONW = Path(getattr(sys, "_base_executable", sys.executable)).with_name(
    "pythonw.exe"
)

if sys.platform == "win32" and _BASE_PYTHONW.is_file():

    def test_a_grandchild_of_the_pythonw_task_has_no_visible_window(tmp_path):
        """The property the task exists for, measured: git started by the
        watcher must not open a console window. Not one assertion here reads
        our own code; Windows reports the window."""
        script = tmp_path / "parent.py"
        script.write_text(_PARENT, encoding="utf-8")
        grandchild = tmp_path / "grandchild.py"
        grandchild.write_text(_GRANDCHILD, encoding="utf-8")
        out = tmp_path / "out.log"
        src = Path(__file__).resolve().parents[1] / "src"
        pythonw = _BASE_PYTHONW
        done = subprocess.run(
            [str(pythonw), str(script), str(out), str(grandchild)],
            env={**os.environ, "PYTHONPATH": str(src)},
            timeout=120,
            check=False,
        )

        assert done.returncode == 0
        lines = [
            ln
            for ln in out.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
        assert json.loads(lines[-1]) == {"visible": False}, lines

    def test_ending_the_pythonw_parent_ends_the_watcher_child(tmp_path):
        """Ending the task kills the pythonw process. The watcher is its child,
        so it must end too, or it runs on with no task to stop it."""
        import ctypes

        script = tmp_path / "parent.py"
        script.write_text(_LONG_PARENT, encoding="utf-8")
        pid_file = tmp_path / "child.pid"
        src = Path(__file__).resolve().parents[1] / "src"
        pythonw = _BASE_PYTHONW
        parent = subprocess.Popen(
            [str(pythonw), str(script), str(tmp_path / "out.log"), str(pid_file)],
            env={**os.environ, "PYTHONPATH": str(src)},
        )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = None
        try:
            for _ in range(600):
                if pid_file.is_file() and pid_file.read_text().strip():
                    break
                assert parent.poll() is None, (
                    "the parent exited before starting the child"
                )
                time.sleep(0.1)
            child_pid = int(pid_file.read_text().strip())
            handle = kernel32.OpenProcess(_SYNCHRONIZE, False, child_pid)
            assert handle, "the child is not running"
            assert (
                kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0)
                == _WAIT_TIMEOUT
            )

            parent.kill()
            parent.wait(timeout=30)

            assert (
                kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 15000)
                == _WAIT_OBJECT_0
            )
        finally:
            if handle:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
            if parent.poll() is None:
                parent.kill()
