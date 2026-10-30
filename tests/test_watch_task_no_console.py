"""BACKLOG row 33: the Windows watcher task opens no console window at sign-in.

`watch-install` registered `python.exe -m jcodemunch_mcp watch-all` as the
`jcodemunch-watch` logon task. `python.exe` is a console program, so a console
window opened at every sign-in and stayed open while the watcher ran. The task
now names `pythonw.exe` when it sits beside the interpreter, and passes
`--log-file`, because a windowless process has no stderr and the watcher's log
would otherwise go nowhere. `watch-status` names a task installed before this,
since only re-running `watch-install` replaces it.

Every probe of the real Task Scheduler is patched; nothing here registers or
reads a real task.
"""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp import service_installer as si

_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _interpreter_dir(tmp_path: Path, *, windowless: bool) -> Path:
    d = tmp_path / "Python 3"
    d.mkdir()
    (d / "python.exe").write_bytes(b"")
    if windowless:
        (d / "pythonw.exe").write_bytes(b"")
    return d


def _exec(interpreter: Path) -> list[str]:
    return [str(interpreter), "-m", "jcodemunch_mcp", "watch-all"]


class TestTheTaskCommand:
    def test_pythonw_is_used_when_it_sits_beside_the_interpreter(self, tmp_path):
        d = _interpreter_dir(tmp_path, windowless=True)
        with (
            patch.object(si, "_exec_cmd", lambda: _exec(d / "python.exe")),
            patch.object(si, "_log_dir", lambda: tmp_path / "logs"),
        ):
            cmd = si._windows_exec_cmd()

        assert cmd[0] == str(d / "pythonw.exe")
        assert cmd[1:4] == ["-m", "jcodemunch_mcp", "watch-all"]

    def test_the_console_interpreter_is_kept_when_there_is_no_pythonw(self, tmp_path):
        d = _interpreter_dir(tmp_path, windowless=False)
        with (
            patch.object(si, "_exec_cmd", lambda: _exec(d / "python.exe")),
            patch.object(si, "_log_dir", lambda: tmp_path / "logs"),
        ):
            cmd = si._windows_exec_cmd()

        assert cmd[0] == str(d / "python.exe")

    def test_the_log_goes_to_a_file_in_the_log_dir(self, tmp_path):
        d = _interpreter_dir(tmp_path, windowless=True)
        logs = tmp_path / "my logs"
        with (
            patch.object(si, "_exec_cmd", lambda: _exec(d / "python.exe")),
            patch.object(si, "_log_dir", lambda: logs),
        ):
            cmd = si._windows_exec_cmd()

        assert cmd[-2:] == ["--log-file", str(logs / "watch-all.log")]

    def test_the_task_definition_carries_pythonw_and_a_quoted_log_path(self, tmp_path):
        d = _interpreter_dir(tmp_path, windowless=True)
        logs = tmp_path / "my logs"
        with (
            patch.object(si, "_exec_cmd", lambda: _exec(d / "python.exe")),
            patch.object(si, "_log_dir", lambda: logs),
        ):
            root = ET.fromstring(si._windows_task_xml().encode("utf-16"))
        exec_ = root.find("t:Actions/t:Exec", _NS)

        assert exec_.findtext("t:Command", namespaces=_NS) == f'"{d / "pythonw.exe"}"'
        assert exec_.findtext("t:Arguments", namespaces=_NS) == (
            f'-m jcodemunch_mcp watch-all --log-file "{logs / "watch-all.log"}"'
        )

    def test_the_linux_and_macos_command_is_unchanged(self):
        assert si._exec_cmd()[1:] == ["-m", "jcodemunch_mcp", "watch-all"]


class TestStatusNamesAConsoleTask:
    _FIXED = {
        "disallow_start_if_on_batteries": False,
        "stop_if_going_on_batteries": False,
        "execution_time_limit": "PT0S",
    }

    def _status(self, command):
        with (
            patch.object(
                si,
                "_run_schtasks",
                lambda args: (0, "TaskName: \\jcodemunch-watch", ""),
            ),
            patch.object(si, "_scheduled_task_state", lambda: "Running"),
            patch.object(si, "_scheduled_task_settings", lambda: dict(self._FIXED)),
            patch.object(si, "_scheduled_task_command", lambda: command),
        ):
            return si._status_windows()

    def test_a_task_that_starts_python_exe_is_named_with_its_repair(self, tmp_path):
        d = _interpreter_dir(tmp_path, windowless=True)
        result = self._status(f'"{d / "python.exe"}"')

        assert any("console" in w and "watch-install" in w for w in result["warnings"])

    def test_a_task_that_starts_pythonw_carries_no_warning(self, tmp_path):
        d = _interpreter_dir(tmp_path, windowless=True)
        result = self._status(str(d / "pythonw.exe"))

        assert "warnings" not in result

    def test_no_repair_is_offered_when_there_is_no_pythonw_to_switch_to(self, tmp_path):
        d = _interpreter_dir(tmp_path, windowless=False)
        result = self._status(str(d / "python.exe"))

        assert "warnings" not in result

    def test_an_unreadable_command_is_unknown_and_warns_nothing(self):
        assert "warnings" not in self._status(None)


class TestCommandProbe:
    def _probe(self, stdout: bytes, returncode: int = 0):
        completed = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=b""
        )
        with patch.object(si.subprocess, "run", return_value=completed) as run:
            return si._scheduled_task_command(), run

    def test_the_action_command_is_read_from_the_task(self):
        result, run = self._probe(b"C:\\Python\\python.exe\r\n")

        assert result == "C:\\Python\\python.exe"
        script = run.call_args.args[0][-1]
        assert (
            "Get-ScheduledTask" in script
            and si.SERVICE_NAME in script
            and "Execute" in script
        )

    @pytest.mark.parametrize("stdout,returncode", [(b"", 1), (b"\r\n", 0)])
    def test_a_failed_or_empty_probe_is_unknown(self, stdout, returncode):
        assert self._probe(stdout, returncode)[0] is None

    def test_a_missing_powershell_is_unknown(self):
        with patch.object(si.subprocess, "run", side_effect=FileNotFoundError):
            assert si._scheduled_task_command() is None
