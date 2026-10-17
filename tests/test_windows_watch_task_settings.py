"""L-149: the Windows watcher task must survive the charger being unplugged.

`watch-install` registered `jcodemunch-watch` with `schtasks /Create` and no
settings, so Task Scheduler's defaults applied: do not start on battery, stop
when the machine goes on battery, end after 72 hours. `schtasks /Create` has no
switch for any of the three, so the task is registered from an XML definition.

⚠ Nothing here runs the real `schtasks`: every test asserts on the generated
XML or on the command line handed to `subprocess.run`, which is patched.

`watch-status` must also say when the task exists and nothing is running,
because the defect's whole cost was that the watcher was gone with no symptom.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp import service_installer as si

_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
_EXEC = [r"C:\Program Files\Python 3\python.exe", "-m", "jcodemunch_mcp", "watch-all"]


def _parsed(xml_text: str) -> ET.Element:
    return ET.fromstring(xml_text.encode("utf-16"))


class TestTaskXml:
    def _xml(self) -> ET.Element:
        with patch.object(si, "_exec_cmd", lambda: list(_EXEC)):
            return _parsed(si._windows_task_xml())

    def test_battery_settings_are_off_and_there_is_no_time_limit(self):
        settings = self._xml().find("t:Settings", _NS)

        assert settings.findtext("t:DisallowStartIfOnBatteries", namespaces=_NS) == "false"
        assert settings.findtext("t:StopIfGoingOnBatteries", namespaces=_NS) == "false"
        assert settings.findtext("t:ExecutionTimeLimit", namespaces=_NS) == "PT0S"

    def test_the_existing_trigger_and_run_level_are_kept(self):
        root = self._xml()

        assert root.find("t:Triggers/t:LogonTrigger", _NS) is not None
        principal = root.find("t:Principals/t:Principal", _NS)
        assert principal.findtext("t:RunLevel", namespaces=_NS) == "LeastPrivilege"
        assert principal.findtext("t:LogonType", namespaces=_NS) == "InteractiveToken"

    def test_the_action_is_the_running_interpreter_quoted_and_escaped(self):
        exec_ = self._xml().find("t:Actions/t:Exec", _NS)

        assert exec_.findtext("t:Command", namespaces=_NS) == f'"{_EXEC[0]}"'
        assert exec_.findtext("t:Arguments", namespaces=_NS) == "-m jcodemunch_mcp watch-all"

    def test_markup_in_the_interpreter_path_cannot_break_the_document(self):
        with patch.object(si, "_exec_cmd", lambda: [r"C:\a&b<c>\python.exe", "-m", "jcodemunch_mcp", "watch-all"]):
            exec_ = _parsed(si._windows_task_xml()).find("t:Actions/t:Exec", _NS)

        assert exec_.findtext("t:Command", namespaces=_NS) == r"C:\a&b<c>\python.exe"

    def test_the_declared_encoding_matches_how_the_file_is_written(self):
        """Task Scheduler rejects a document whose declaration disagrees with
        its bytes; the file is written UTF-16, so the declaration says so."""
        assert si._windows_task_xml().startswith('<?xml version="1.0" encoding="UTF-16"?>')


class TestInstallCommandLine:
    def _install(self, returncode: int = 0, stderr: bytes = b""):
        calls: list[list[str]] = []
        seen_xml: dict = {}

        def _run(args, **kwargs):
            calls.append(list(args))
            if "/XML" in args:
                path = Path(args[args.index("/XML") + 1])
                seen_xml["exists"] = path.is_file()
                seen_xml["path"] = path
                seen_xml["text"] = path.read_text(encoding="utf-16")
                return subprocess.CompletedProcess(args, returncode, b"", stderr)
            return subprocess.CompletedProcess(args, 0, b"", b"")

        with patch.object(si.subprocess, "run", side_effect=_run), \
             patch.object(si, "_exec_cmd", lambda: list(_EXEC)), \
             patch.object(si, "_log_dir") as log_dir, \
             patch.object(si, "_windows_console_code_pages", lambda: ["utf-8"]):
            log_dir.return_value.mkdir.return_value = None
            try:
                result = si._install_windows()
            except si.InstallerError as exc:
                result = exc
        return calls, seen_xml, result

    def test_create_registers_from_xml_and_drops_the_switch_form(self):
        calls, seen, result = self._install()
        create = calls[0]

        assert create[:3] == ["schtasks", "/Create", "/F"]
        assert create[create.index("/TN") + 1] == si.SERVICE_NAME
        assert "/XML" in create
        for gone in ("/SC", "/RL", "/TR"):
            assert gone not in create
        assert seen["exists"] is True
        settings = _parsed(seen["text"]).find("t:Settings", _NS)
        assert settings.findtext("t:StopIfGoingOnBatteries", namespaces=_NS) == "false"
        assert result["status"] == "registered"

    def test_the_task_is_still_started_immediately(self):
        calls, _seen, _result = self._install()

        assert calls[1] == ["schtasks", "/Run", "/TN", si.SERVICE_NAME]

    def test_the_xml_file_is_removed_after_success(self):
        _calls, seen, _result = self._install()

        assert not seen["path"].exists()

    def test_the_xml_file_is_removed_after_failure_and_the_error_is_raised(self):
        _calls, seen, result = self._install(returncode=1, stderr=b"ERROR: Access is denied.")

        assert isinstance(result, si.InstallerError)
        assert "Access is denied" in str(result)
        assert not seen["path"].exists()


class TestStatusSaysWhenNothingRuns:
    def _status(self, *, state, settings=None):
        with patch.object(si, "_run_schtasks", lambda args: (0, "TaskName: \\jcodemunch-watch", "")), \
             patch.object(si, "_scheduled_task_state", lambda: state), \
             patch.object(si, "_scheduled_task_settings", lambda: settings):
            return si._status_windows()

    _FIXED = {
        "disallow_start_if_on_batteries": False,
        "stop_if_going_on_batteries": False,
        "execution_time_limit": "PT0S",
    }

    def test_a_ready_task_reports_no_watcher_and_says_so(self):
        result = self._status(state="Ready", settings=dict(self._FIXED))

        assert result["active"] is True  # #469's meaning is unchanged
        assert result["watcher_running"] is False
        assert any("no watcher" in w for w in result["warnings"])

    def test_a_running_task_carries_no_warning(self):
        result = self._status(state="Running", settings=dict(self._FIXED))

        assert result["watcher_running"] is True
        assert "warnings" not in result

    def test_an_unreadable_state_is_unknown_not_stopped(self):
        result = self._status(state=None)

        assert result["watcher_running"] is None
        assert "warnings" not in result

    @pytest.mark.parametrize(
        "key,value",
        [
            ("disallow_start_if_on_batteries", True),
            ("stop_if_going_on_batteries", True),
            ("execution_time_limit", "PT72H"),
        ],
    )
    def test_a_task_installed_with_the_old_defaults_is_named_with_its_repair(self, key, value):
        settings = dict(self._FIXED)
        settings[key] = value
        result = self._status(state="Running", settings=settings)

        assert result["settings"] == settings
        assert any("watch-install" in w and key in w for w in result["warnings"])

    def test_unreadable_settings_are_omitted_not_guessed(self):
        result = self._status(state="Running", settings=None)

        assert "settings" not in result
        assert "warnings" not in result


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true",
    reason="registers a real scheduled task: CI's Windows runner only, never a developer box",
)
def test_task_scheduler_accepts_the_definition_and_keeps_the_settings():
    """Every other test here asserts on the XML we write. Only Task Scheduler
    can say it ACCEPTS it, and a malformed definition would make
    `watch-install` fail for every Windows user. Registered under a throwaway
    name, read back, deleted. Never started: its action only runs at logon."""
    name = f"{si.SERVICE_NAME}-ci-{uuid.uuid4().hex[:8]}"
    xml_path = Path(tempfile.mkdtemp()) / "task.xml"
    xml_path.write_text(si._windows_task_xml(), encoding="utf-16")
    try:
        created = subprocess.run(
            ["schtasks", "/Create", "/F", "/TN", name, "/XML", str(xml_path)],
            capture_output=True, check=False,
        )
        assert created.returncode == 0, si._decode_windows_output(created.stderr or created.stdout)
        queried = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/XML"], capture_output=True, check=False,
        )
        assert queried.returncode == 0
        text = si._decode_windows_output(queried.stdout)
        root = ET.fromstring(text[text.index("<Task"):])
        settings = root.find("t:Settings", _NS)
        assert settings.findtext("t:DisallowStartIfOnBatteries", namespaces=_NS) == "false"
        assert settings.findtext("t:StopIfGoingOnBatteries", namespaces=_NS) == "false"
        assert settings.findtext("t:ExecutionTimeLimit", namespaces=_NS) == "PT0S"
        assert root.find("t:Triggers/t:LogonTrigger", _NS) is not None
    finally:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", name], capture_output=True, check=False)
        xml_path.unlink(missing_ok=True)


class TestSettingsProbe:
    def _probe(self, stdout: bytes, returncode: int = 0):
        completed = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")
        with patch.object(si.subprocess, "run", return_value=completed) as run:
            return si._scheduled_task_settings(), run

    def test_the_settings_are_parsed_from_the_invariant_values(self):
        result, run = self._probe(b"True|True|PT72H\r\n")

        assert result == {
            "disallow_start_if_on_batteries": True,
            "stop_if_going_on_batteries": True,
            "execution_time_limit": "PT72H",
        }
        script = run.call_args.args[0][-1]
        assert "Get-ScheduledTask" in script and si.SERVICE_NAME in script

    def test_a_failed_probe_is_unknown(self):
        assert self._probe(b"", returncode=1)[0] is None

    def test_malformed_output_is_unknown(self):
        assert self._probe(b"Running\r\n")[0] is None

    def test_a_missing_powershell_is_unknown(self):
        with patch.object(si.subprocess, "run", side_effect=FileNotFoundError):
            assert si._scheduled_task_settings() is None
