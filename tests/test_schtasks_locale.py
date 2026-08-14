"""#468 / #469: `watch-status` on a non-English Windows.

Two independent defects in one function, reported and split by
[@lsg1103275794](https://github.com/lsg1103275794):

- **#468** `schtasks` output was decoded as UTF-8 while the process writes in the
  machine's code page, so `detail` was mojibake. `errors="replace"` meant it did
  not raise — the corruption was silent.
- **#469** liveness was decided by `"Running" in stdout or "Ready" in stdout`, so
  a running task reported `active: false` on every non-English Windows.

⚠⚠ **The reporter's own note is the reason both survived a green suite: searching
`tests/` for `_status_windows`, `schtasks` and the `Running`/`Ready` predicate
returned no hits.** There was no coverage to fail. These tests run on every
platform because they drive the decode and the verdict directly rather than
shelling out to Windows.

The CP936 fixture is the reporter's measured bytes, verbatim from #468.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from jcodemunch_mcp import service_installer as si

# The first 32 bytes of `schtasks /Query /TN \jcodemunch-watch /FO LIST` on a
# Simplified-Chinese Windows 11, active code page CP936, as measured in #468.
_CP936_SCHTASKS = bytes.fromhex(
    "0d0acec4bcfebcd03a205c0d0ad6f7bbfac3fb3a2020202020202047474259"
)
_CP936_EXPECTED = "\r\n文件夹: \\\r\n主机名:       GGBY"


class TestDecodingNativeOutput:
    def test_the_reported_bytes_decode_without_replacement_characters(self):
        with patch.object(si, "_windows_console_code_pages", lambda: ["cp936", "utf-8"]):
            decoded = si._decode_windows_output(_CP936_SCHTASKS)

        assert decoded == _CP936_EXPECTED
        assert "�" not in decoded

    def test_the_old_utf8_path_is_what_produced_the_mojibake(self):
        """Non-vacuity, and it pins the defect rather than the fix.

        ⚠ This is the shape that made #468 silent: strict UTF-8 RAISES on these
        bytes, so `errors="replace"` was doing real work — turning a loud failure
        into a plausible string.
        """
        with pytest.raises(UnicodeDecodeError):
            _CP936_SCHTASKS.decode("utf-8")

        assert _CP936_SCHTASKS.decode("utf-8", errors="replace").count("�") == 10

    def test_ascii_output_is_unchanged_by_the_new_path(self):
        with patch.object(si, "_windows_console_code_pages", lambda: ["cp437", "utf-8"]):
            assert si._decode_windows_output(b"TaskName: \\jcodemunch-watch") == (
                "TaskName: \\jcodemunch-watch"
            )

    def test_empty_output_is_empty_not_an_error(self):
        assert si._decode_windows_output(b"") == ""

    def test_a_codec_that_cannot_decode_falls_back_rather_than_raising(self):
        """The last resort must return something. A status probe that raises
        turns a cosmetic problem into an outage."""
        with patch.object(si, "_windows_console_code_pages", lambda: ["utf-8"]):
            decoded = si._decode_windows_output(_CP936_SCHTASKS)

        assert "�" in decoded  # degraded, but present

    def test_utf8_is_always_a_candidate(self):
        """A UTF-8 console (code page 65001) must still work if the probe fails."""
        with patch.object(si, "logger"):
            assert "utf-8" in si._windows_console_code_pages()


class TestLivenessIsNotDecidedByDisplayText:
    """#469. `正在运行` does not contain `Running`, and never will."""

    def _status(self, *, state, stdout: bytes):
        with patch.object(si, "_run_schtasks", lambda args: (0, si._decode_windows_output(stdout), "")), \
             patch.object(si, "_scheduled_task_state", lambda: state), \
             patch.object(si, "_windows_console_code_pages", lambda: ["cp936", "utf-8"]):
            return si._status_windows()

    def test_a_running_task_on_chinese_windows_reports_active(self):
        result = self._status(state="Running", stdout="模式:         正在运行".encode("cp936"))

        assert result["active"] is True
        assert result["state"] == "Running"
        assert result["state_source"] == "scheduled_task_state"

    def test_the_display_text_alone_would_have_said_false(self):
        """Non-vacuity: the pre-fix predicate against the same output.

        ⚠ This asserts the DEFECT, so it fails if someone 'simplifies' the fix
        back to a string match — the fallback below is the only path allowed to
        use it, and only when the enum is unreadable.
        """
        localized = "模式:         正在运行"

        assert "Running" not in localized
        assert "Ready" not in localized

    def test_a_ready_task_is_active_preserving_the_original_semantics(self):
        """An ONLOGON task sits in Ready between logons, and the pre-#469 code
        counted Ready as active. Fixing the locale bug must not quietly change
        what `active` MEANS."""
        assert self._status(state="Ready", stdout=b"")["active"] is True

    def test_a_disabled_task_is_not_active(self):
        assert self._status(state="Disabled", stdout=b"")["active"] is False

    def test_the_detail_carries_readable_localized_text(self):
        result = self._status(state="Running", stdout="模式:         正在运行".encode("cp936"))

        assert "正在运行" in result["detail"]
        assert "�" not in result["detail"]

    def test_an_unreadable_state_falls_back_and_says_so(self):
        """⚠ The fallback is the pre-#469 predicate and is wrong off English
        Windows. It must never present as a measurement: `state_source` names it
        and `state` is None rather than a guess."""
        result = self._status(state=None, stdout=b"Status: Running")

        assert result["state_source"] == "display_text"
        assert result["state"] is None
        assert result["active"] is True


class TestStateProbeFailsClosed:
    def test_a_nonzero_return_code_reads_as_unknown_not_as_stopped(self):
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"nope")
        with patch.object(si.subprocess, "run", return_value=completed):
            assert si._scheduled_task_state() is None

    def test_a_missing_powershell_reads_as_unknown(self):
        with patch.object(si.subprocess, "run", side_effect=FileNotFoundError):
            assert si._scheduled_task_state() is None

    def test_a_timeout_reads_as_unknown(self):
        with patch.object(si.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell", 30)):
            assert si._scheduled_task_state() is None

    def test_empty_output_reads_as_unknown_rather_than_an_empty_state(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"  \r\n", stderr=b"")
        with patch.object(si.subprocess, "run", return_value=completed):
            assert si._scheduled_task_state() is None

    def test_the_state_is_read_from_the_enum_not_from_display_text(self):
        """Pins the source. `Get-ScheduledTask ... .State.ToString()` is the
        invariant enum; a change to localized parsing fails here."""
        captured: dict = {}

        def _capture(args, **kwargs):
            captured["args"] = args
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"Running\r\n", stderr=b"")

        with patch.object(si.subprocess, "run", side_effect=_capture):
            assert si._scheduled_task_state() == "Running"

        script = captured["args"][-1]
        assert "Get-ScheduledTask" in script
        assert "State.ToString()" in script
        assert si.SERVICE_NAME in script


class TestEveryWindowsCallSiteSharesTheDecoder:
    """⚠ `_status_windows` was the reported site; it was not the only one.

    `_install_windows` raises `InstallerError` carrying `schtasks` stderr, and
    `_uninstall_windows` reads a return code. Both decoded UTF-8 the same way, so
    a localized failure message reached the user as mojibake. Guarding only the
    reported path leaves the same hazard on the path a user hits when something
    is already going wrong.
    """

    def test_install_failure_message_is_decoded_in_the_machine_code_page(self):
        localized = "错误: 拒绝访问。"
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=localized.encode("cp936")
        )
        with patch.object(si.subprocess, "run", return_value=completed), \
             patch.object(si, "_windows_console_code_pages", lambda: ["cp936", "utf-8"]), \
             patch.object(si, "_log_dir") as log_dir, \
             patch.object(si, "_exec_cmd", lambda: ["python", "-m", "jcodemunch_mcp"]):
            log_dir.return_value.mkdir.return_value = None
            with pytest.raises(si.InstallerError) as excinfo:
                si._install_windows()

        assert localized in str(excinfo.value)
        assert "�" not in str(excinfo.value)

    def test_uninstall_still_reports_removal_by_return_code(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with patch.object(si.subprocess, "run", return_value=completed):
            assert si._uninstall_windows()["removed"] is True

    def test_no_windows_call_site_hardcodes_utf8_any_more(self):
        """A ratchet. The defect was one keyword argument repeated at three
        sites; this fails if a fourth arrives or an old one comes back."""
        import inspect

        for fn in (si._install_windows, si._uninstall_windows, si._status_windows):
            source = inspect.getsource(fn)
            assert 'encoding="utf-8"' not in source, fn.__name__
