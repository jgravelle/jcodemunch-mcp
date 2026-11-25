"""Cross-platform login-service installer for `jcodemunch-mcp watch-all`.

- Linux: systemd --user unit at ~/.config/systemd/user/jcodemunch-watch.service
- macOS: launchd plist at ~/Library/LaunchAgents/us.gravelle.jcodemunch-watch.plist
- Windows: Task Scheduler task named `jcodemunch-watch`, registered from XML
  so it keeps running on battery and has no time limit (L-149)

The installer deliberately invokes the *same interpreter* currently running
(via `sys.executable -m jcodemunch_mcp watch-all`) so the service picks up
whatever virtualenv the user installed into. This avoids the `uvx` round-trip
that jcrefresher pays per-event and removes a whole class of PATH issues.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "jcodemunch-watch"
LAUNCHD_LABEL = "us.gravelle.jcodemunch-watch"


class InstallerError(RuntimeError):
    pass


# ── Path helpers ────────────────────────────────────────────────────────────


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _log_dir() -> Path:
    base = Path(os.environ.get("CODE_INDEX_PATH") or (Path.home() / ".code-index"))
    return base / "logs"


def _exec_cmd() -> list[str]:
    """How the service should invoke the watcher."""
    return [sys.executable, "-m", "jcodemunch_mcp", "watch-all"]


# ── systemd (Linux) ─────────────────────────────────────────────────────────


_SYSTEMD_TEMPLATE = """[Unit]
Description=jcodemunch-mcp: auto-reindex every locally-indexed repo
After=default.target

[Service]
Type=simple
ExecStart={exec_cmd}
Restart=on-failure
RestartSec=5
StandardOutput=append:{log_dir}/watch.log
StandardError=append:{log_dir}/watch.err
Environment=PYTHONUNBUFFERED=1
{env_lines}

[Install]
WantedBy=default.target
"""


def _systemd_env_lines() -> str:
    """Forward CODE_INDEX_PATH and JCODEMUNCH_* env into the unit."""
    lines = []
    for key, val in os.environ.items():
        if key == "CODE_INDEX_PATH" or key.startswith("JCODEMUNCH_"):
            lines.append(f"Environment={key}={val}")
    return "\n".join(lines)


def _install_systemd() -> dict:
    if shutil.which("systemctl") is None:
        raise InstallerError("systemctl not found — is this a systemd system?")
    unit_path = _systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    _log_dir().mkdir(parents=True, exist_ok=True)

    quoted = " ".join(_shell_quote(x) for x in _exec_cmd())
    unit_path.write_text(
        _SYSTEMD_TEMPLATE.format(
            exec_cmd=quoted,
            log_dir=str(_log_dir()),
            env_lines=_systemd_env_lines(),
        ),
        encoding="utf-8",
    )

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.service"], check=True)
    return {"platform": "systemd", "unit": str(unit_path), "status": "enabled"}


def _uninstall_systemd() -> dict:
    unit_path = _systemd_unit_path()
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"], check=False)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    removed = False
    if unit_path.exists():
        unit_path.unlink()
        removed = True
    return {"platform": "systemd", "unit": str(unit_path), "removed": removed}


# What `systemctl is-active` prints when it reached the service manager. Any
# other output (an empty stdout, a bus or permission error on stderr) means the
# query did not happen, so the service's state is UNKNOWN, never inactive.
_SYSTEMD_ACTIVE_STATES = frozenset({"active", "reloading", "refreshing"})
# `unknown` is what older systemd (219, RHEL/CentOS 7) prints for a unit it
# never loaded: a query that succeeded, settled by the unit-file check below.
_SYSTEMD_INACTIVE_STATES = frozenset(
    {"inactive", "failed", "activating", "deactivating", "maintenance", "unknown"}
)
_SYSTEMD_PROBE_TIMEOUT_SECONDS = 5.0


def _systemd_probe_error(stderr: str) -> str:
    """A stable class for a probe that did not reach the user's service manager."""
    low = stderr.lower()
    if "permission denied" in low or "access denied" in low:
        return "permission_denied"
    if (
        "dbus_session_bus_address" in low
        or "xdg_runtime_dir" in low
        or "user scope bus" in low
        or "failed to connect to bus" in low
    ):
        return "no_user_bus"
    return "probe_failed"


def _status_systemd() -> dict:
    """The watch service's state on systemd: `active`, `inactive`,
    `not_installed` or `unknown`.

    `active` is None when the probe could not ask. A process started without
    the user's session bus (`DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`) gets
    a bus error and a nonzero exit, and reading that as inactive told the user
    a running watcher was stopped (reported against jdocmunch-mcp#206). The
    state is read from what `is-active` printed, never from its exit code.
    """
    if shutil.which("systemctl") is None:
        # The installer refuses without systemctl, so no unit of ours can run.
        return {
            "platform": "systemd", "active": False, "status": "not_installed",
            "reason": "systemctl not found",
        }
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=_SYSTEMD_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "platform": "systemd", "active": None, "status": "unknown",
            "probe_error": "timeout",
            "detail": f"systemctl --user is-active did not answer in {_SYSTEMD_PROBE_TIMEOUT_SECONDS:g} s",
        }
    except OSError as exc:
        return {
            "platform": "systemd", "active": None, "status": "unknown",
            "probe_error": "probe_failed", "detail": str(exc),
        }
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    state = out.splitlines()[0].strip() if out else ""
    if state in _SYSTEMD_ACTIVE_STATES:
        return {"platform": "systemd", "active": True, "status": "active", "state": state}
    if state in _SYSTEMD_INACTIVE_STATES:
        installed = _systemd_unit_path().exists()
        return {
            "platform": "systemd", "active": False,
            "status": "inactive" if installed else "not_installed",
            "state": state,
        }
    return {
        "platform": "systemd", "active": None, "status": "unknown",
        "probe_error": _systemd_probe_error(err),
        "state": out or err,
        "detail": err or out or f"systemctl --user is-active exited {result.returncode} with no output",
    }


# ── launchd (macOS) ─────────────────────────────────────────────────────────


_LAUNCHD_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log_dir}/watch.log</string>
  <key>StandardErrorPath</key><string>{log_dir}/watch.err</string>
  <key>EnvironmentVariables</key>
  <dict>
{env}
  </dict>
</dict></plist>
"""


def _launchd_env_xml() -> str:
    out = []
    for key, val in os.environ.items():
        if key == "CODE_INDEX_PATH" or key == "PATH" or key.startswith("JCODEMUNCH_"):
            out.append(f"    <key>{_xml_escape(key)}</key><string>{_xml_escape(val)}</string>")
    return "\n".join(out)


def _install_launchd() -> dict:
    plist = _launchd_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    _log_dir().mkdir(parents=True, exist_ok=True)
    args_xml = "\n".join(f"    <string>{_xml_escape(a)}</string>" for a in _exec_cmd())
    plist.write_text(
        _LAUNCHD_TEMPLATE.format(
            label=LAUNCHD_LABEL,
            args=args_xml,
            log_dir=str(_log_dir()),
            env=_launchd_env_xml(),
        ),
        encoding="utf-8",
    )
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    subprocess.run(["launchctl", "load", str(plist)], check=True)
    return {"platform": "launchd", "plist": str(plist), "status": "loaded"}


def _uninstall_launchd() -> dict:
    plist = _launchd_plist_path()
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    removed = False
    if plist.exists():
        plist.unlink()
        removed = True
    return {"platform": "launchd", "plist": str(plist), "removed": removed}


def _status_launchd() -> dict:
    """`active` keeps its meaning (the job is loaded); `status` is read from
    what `launchctl` said, never from its exit code alone. A label launchd
    does not know is `inactive` when our plist exists and `not_installed`
    when it does not; any other failure is `unknown`."""
    try:
        result = subprocess.run(
            ["launchctl", "list", LAUNCHD_LABEL],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=_SYSTEMD_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "platform": "launchd", "active": None, "status": "unknown",
            "probe_error": "timeout",
            "detail": f"launchctl list did not answer in {_SYSTEMD_PROBE_TIMEOUT_SECONDS:g} s",
        }
    except OSError as exc:
        return {
            "platform": "launchd", "active": None, "status": "unknown",
            "probe_error": "probe_failed", "detail": str(exc),
        }
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode == 0:
        # Loaded. A job with no `"PID" =` line is loaded but not running (it
        # exited or crashed), so it is `inactive` though `active` keeps meaning
        # "loaded".
        running = '"PID" =' in out
        return {
            "platform": "launchd", "active": True,
            "status": "active" if running else "inactive", "detail": out,
        }
    if "could not find service" in (err + " " + out).lower():
        installed = _launchd_plist_path().exists()
        return {
            "platform": "launchd", "active": False,
            "status": "inactive" if installed else "not_installed",
            "detail": err or out,
        }
    return {
        "platform": "launchd", "active": None, "status": "unknown",
        "probe_error": "permission_denied" if "permission" in err.lower() else "probe_failed",
        "detail": err or out or f"launchctl list exited {result.returncode} with no output",
    }


# ── Task Scheduler (Windows) ────────────────────────────────────────────────


def _windows_console_code_pages() -> list[str]:
    """Codecs to try for native Windows console output, best first.

    `schtasks.exe` writes in the machine's code page, not UTF-8. Which one
    depends on the process: a redirected child writes in the CONSOLE OUTPUT
    code page, which is not always the ANSI code page — on a Simplified-Chinese
    install both are 936, but on a Western European one they are 850 and 1252.
    Both are asked of Windows and tried in that order.

    ⚠ `locale.getpreferredencoding()` is deliberately NOT used. Under
    `PYTHONUTF8=1` it reports utf-8 while the child still writes CP936, which is
    exactly the case #468 warned about — the answer would look principled and be
    wrong only for the users this exists for.
    """
    pages: list[str] = []
    try:
        import ctypes  # noqa: PLC0415 — Windows-only, not imported on other platforms

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        for fn in (kernel32.GetConsoleOutputCP, kernel32.GetACP):
            try:
                cp = int(fn())
            except Exception:  # pragma: no cover - defensive
                logger.debug("code page probe failed", exc_info=True)
                continue
            # 0 means "no console attached"; 65001 is UTF-8 and is tried anyway.
            if cp and f"cp{cp}" not in pages:
                pages.append(f"cp{cp}")
    except Exception:
        logger.debug("ctypes unavailable for code page probe", exc_info=True)
    pages.append("utf-8")
    return pages


def _decode_windows_output(raw: bytes) -> str:
    """Decode native Windows tool output without inventing characters.

    ⚠⚠ Every call site here used `encoding="utf-8", errors="replace"`, which does
    not raise — so CP936 bytes became 40 U+FFFD characters and the corruption was
    SILENT (#468). `errors="replace"` on a wrong codec is worse than a crash: it
    produces a plausible string.

    The ORDER is the answer: the first candidate is what Windows says the child
    writes. Strict decoding is a net under it, and only a partial one — a
    multi-byte page like cp936 rejects a wrong guess, while cp437 and cp1252 map
    nearly every byte and cannot fail. Do not read a successful decode as
    confirmation that the codec was right.
    """
    if not raw:
        return ""
    candidates = _windows_console_code_pages()
    for codec in candidates:
        try:
            return raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode(candidates[0], errors="replace")


def _run_schtasks(args: list[str]) -> tuple[int, str, str]:
    """Run a `schtasks` command and decode its output in the machine's code page."""
    result = subprocess.run(args, capture_output=True, check=False)
    return (
        result.returncode,
        _decode_windows_output(result.stdout or b""),
        _decode_windows_output(result.stderr or b""),
    )


# Task Scheduler's own state enum, which is stable across display languages.
# Matches the pre-#469 semantics exactly: a task that exists and is enabled is
# "active" whether or not an instance is executing right now, because the
# installed task is ONLOGON and sits in Ready between logons.
_ACTIVE_TASK_STATES = frozenset({"Running", "Ready"})


def _scheduled_task_state() -> Optional[str]:
    """The task's `State` enum from Windows, or None if it could not be read.

    ⚠⚠ The display text cannot answer this. `schtasks` prints the state in the
    installed display language (`正在运行` for Running), so the pre-#469 test
    `"Running" in stdout or "Ready" in stdout` reported `active: false` on every
    non-English Windows while the watcher was running normally. `/FO CSV` does
    not help — its headers AND values are localized too.

    `Get-ScheduledTask` returns a `ScheduledTaskState` enum whose `ToString()` is
    invariant, which is the same source Windows' own tooling reads.
    """
    script = (
        "$ErrorActionPreference='Stop';"
        f"(Get-ScheduledTask -TaskName '{SERVICE_NAME}').State.ToString()"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, check=False, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Get-ScheduledTask probe failed", exc_info=True)
        return None
    if result.returncode != 0:
        logger.debug("Get-ScheduledTask returned %s", result.returncode)
        return None
    state = _decode_windows_output(result.stdout or b"").strip()
    return state or None


def _watch_task_registered() -> Optional[bool]:
    """Whether the watch task is registered: True, False, or None when that
    could not be asked. The answer is a fixed token the script prints, so it
    does not depend on the display language (the reason `State` is read from
    the enum above)."""
    # `absent` only for Get-ScheduledTask's own not-found error id, which is
    # invariant across display languages. A missing cmdlet is also category
    # ObjectNotFound (CommandNotFoundException), so the category alone is not
    # enough. An access, CIM or module-load failure prints `error` and reads
    # as unknown, never as a task that is not there.
    script = (
        f"try {{ $null = Get-ScheduledTask -TaskName '{SERVICE_NAME}' -ErrorAction Stop; 'present' }}"
        " catch { if ($_.FullyQualifiedErrorId -like '*_NotFound_*,Get-ScheduledTask') { 'absent' } else { 'error' } }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, check=False, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Get-ScheduledTask existence probe failed", exc_info=True)
        return None
    if result.returncode != 0:
        return None
    word = _decode_windows_output(result.stdout or b"").strip()
    if word == "present":
        return True
    if word == "absent":
        return False
    return None


# ⚠⚠ L-149: `schtasks /Create` with switches takes Task Scheduler's defaults for
# everything it has no switch for, and three of those defaults kill a watcher on
# a laptop: it does not START on battery, it is STOPPED when the machine goes on
# battery, and it is ENDED after 72 hours. No switch exists for any of them, so
# the task is registered from this definition. Everything the switch form set is
# kept: a logon trigger, the limited run level, the running interpreter as the
# action, one instance at a time. The principal names no UserId, so `schtasks`
# registers it for the calling user, as the switch form did. The trigger is the
# one deliberate difference from the switch form (#1564, next).
# ⚠⚠ #1564 (@rknighton): the LOGON TRIGGER must name the user. A trigger with
# no UserId fires at ANY user's logon, and registering that from a shell that
# is not elevated is refused with "Access is denied", so `watch-install` worked
# only from an elevated shell. The reporter measured both halves: a UserId in
# the trigger registers without elevation, and one in the principal alone does
# not. An elevated shell registers either form, so it cannot show the defect.
_WINDOWS_TASK_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>jcodemunch-mcp: auto-reindex every locally-indexed repo</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>{trigger_user}
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _windowless(interpreter: str) -> Optional[Path]:
    """`pythonw.exe` beside a console `python.exe`, or None when there is none."""
    path = Path(interpreter)
    if path.name.lower() != "python.exe":
        return None
    windowless = path.with_name("pythonw.exe")
    return windowless if windowless.is_file() else None


def _windows_exec_cmd() -> list[str]:
    """The Windows task's command: no console window, and a log file to write to.

    `python.exe` is a console program, so a logon task that starts it opens a
    console window at every sign-in and keeps it open while the watcher runs.
    `pythonw.exe` opens none, but it has no stderr either, so the log goes to a
    file under the log dir. Under pythonw, `watch-all` re-runs itself in a
    console-less `python.exe` (`windowless.py`), so the git processes it starts
    open no window either. Without a `pythonw.exe` beside the interpreter the
    console one is kept: a visible window beats a task that cannot start.
    """
    exe, *rest = _exec_cmd()
    windowless = _windowless(exe)
    if windowless is not None:
        exe = str(windowless)
    return [exe, *rest, "--log-file", str(_log_dir() / "watch-all.log")]


_NAME_SAM_COMPATIBLE = 2  # EXTENDED_NAME_FORMAT: DOMAIN\user


def _windows_user_id() -> Optional[str]:
    """The calling user as ``DOMAIN\\user``, or None when it cannot be read.

    `GetUserNameExW` answers for the process token, so it names the account
    `schtasks` registers the task for even when the environment was changed.
    The environment is the fallback. None leaves the trigger without a UserId,
    the pre-#1564 form, which still registers from an elevated shell.
    """
    try:
        import ctypes

        secur32 = ctypes.windll.secur32  # type: ignore[attr-defined]
        size = ctypes.c_ulong(0)
        secur32.GetUserNameExW(_NAME_SAM_COMPATIBLE, None, ctypes.byref(size))
        if size.value:
            buf = ctypes.create_unicode_buffer(size.value)
            if secur32.GetUserNameExW(_NAME_SAM_COMPATIBLE, buf, ctypes.byref(size)) and buf.value:
                return buf.value
    except Exception:
        logger.debug("GetUserNameExW failed; falling back to the environment", exc_info=True)
    user = os.environ.get("USERNAME", "").strip()
    domain = os.environ.get("USERDOMAIN", "").strip()
    if user:
        return f"{domain}\\{user}" if domain else user
    return None


def _windows_task_xml() -> str:
    """The task definition `_install_windows` registers (L-149, #1564)."""
    exe, *rest = _windows_exec_cmd()
    user_id = _windows_user_id()
    trigger_user = f"\n      <UserId>{_xml_escape(user_id)}</UserId>" if user_id else ""
    return _WINDOWS_TASK_TEMPLATE.format(
        command=_xml_escape(_cmd_quote(exe)),
        arguments=_xml_escape(" ".join(_cmd_quote(x) for x in rest)),
        trigger_user=trigger_user,
    )


def _install_windows() -> dict:
    _log_dir().mkdir(parents=True, exist_ok=True)
    # The task cannot redirect stdout, and pythonw has none, so the command passes
    # --log-file into this directory (`_windows_exec_cmd`).
    # ⚠ The file is written in the encoding its declaration names; schtasks
    # rejects a document whose bytes disagree with it.
    fd, xml_path = tempfile.mkstemp(prefix=f"{SERVICE_NAME}-", suffix=".xml")
    try:
        try:
            fh = os.fdopen(fd, "w", encoding="utf-16")
        except Exception:
            os.close(fd)
            raise
        with fh:
            fh.write(_windows_task_xml())
        args = [
            "schtasks", "/Create", "/F",
            "/TN", SERVICE_NAME,
            "/XML", xml_path,
        ]
        # ⚠ The failure message is USER-FACING, so it needs the same decoding as the
        # status path — a localized "access denied" rendered as mojibake is a support
        # ticket, not a diagnosis.
        returncode, stdout, stderr = _run_schtasks(args)
    finally:
        try:
            os.unlink(xml_path)
        except OSError:
            logger.debug("could not remove %s", xml_path, exc_info=True)
    if returncode != 0:
        raise InstallerError(f"schtasks /Create failed: {stderr.strip() or stdout.strip()}")
    subprocess.run(["schtasks", "/Run", "/TN", SERVICE_NAME], check=False, capture_output=True)
    return {"platform": "schtasks", "task": SERVICE_NAME, "status": "registered"}


def _uninstall_windows() -> dict:
    returncode, _stdout, _stderr = _run_schtasks(
        ["schtasks", "/Delete", "/F", "/TN", SERVICE_NAME]
    )
    return {"platform": "schtasks", "task": SERVICE_NAME, "removed": returncode == 0}


# The values L-149's definition registers. A task installed before it keeps
# Task Scheduler's defaults (True, True, PT72H) until `watch-install` is re-run.
_WINDOWS_TASK_SETTINGS = {
    "disallow_start_if_on_batteries": False,
    "stop_if_going_on_batteries": False,
    "execution_time_limit": "PT0S",
}
_PS_BOOLS = {"True": True, "False": False}


def _scheduled_task_settings() -> Optional[dict]:
    """The installed task's battery and time-limit settings, or None if unread.

    Read from `Get-ScheduledTask`, like the state: a bool's `ToString()` and the
    ISO-8601 duration do not change with the display language.
    """
    script = (
        "$ErrorActionPreference='Stop';"
        f"$s=(Get-ScheduledTask -TaskName '{SERVICE_NAME}').Settings;"
        "'{0}|{1}|{2}' -f $s.DisallowStartIfOnBatteries,$s.StopIfGoingOnBatteries,$s.ExecutionTimeLimit"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, check=False, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Get-ScheduledTask settings probe failed", exc_info=True)
        return None
    if result.returncode != 0:
        logger.debug("Get-ScheduledTask settings returned %s", result.returncode)
        return None
    parts = _decode_windows_output(result.stdout or b"").strip().split("|")
    if len(parts) != 3 or parts[0] not in _PS_BOOLS or parts[1] not in _PS_BOOLS:
        return None
    return {
        "disallow_start_if_on_batteries": _PS_BOOLS[parts[0]],
        "stop_if_going_on_batteries": _PS_BOOLS[parts[1]],
        # ⚠ An empty read is UNKNOWN, never mapped to the healthy PT0S.
        "execution_time_limit": parts[2] or None,
    }


def _scheduled_task_command() -> Optional[str]:
    """The program the installed task starts, or None if it could not be read."""
    script = (
        "$ErrorActionPreference='Stop';"
        f"(Get-ScheduledTask -TaskName '{SERVICE_NAME}').Actions[0].Execute"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, check=False, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Get-ScheduledTask action probe failed", exc_info=True)
        return None
    if result.returncode != 0:
        logger.debug("Get-ScheduledTask action returned %s", result.returncode)
        return None
    command = _decode_windows_output(result.stdout or b"").strip()
    return command or None


def _status_windows() -> dict:
    _rc, stdout, _stderr = _run_schtasks(
        ["schtasks", "/Query", "/TN", SERVICE_NAME, "/FO", "LIST"]
    )
    state = _scheduled_task_state()
    if state is not None:
        active = state in _ACTIVE_TASK_STATES
        source = "scheduled_task_state"
    else:
        # Fallback only. ⚠ This is the pre-#469 predicate and it is WRONG on any
        # non-English Windows; `state_source` says so rather than letting a
        # confident `false` pass for a measurement.
        active = "Running" in stdout or "Ready" in stdout
        source = "display_text"
    if state is None:
        # The display-text fallback is not a measurement (non-English Windows
        # reads every task as not running), so it never earns `inactive`. A
        # task that is not registered at all is a successful answer, read
        # without display text.
        status = "not_installed" if _watch_task_registered() is False else "unknown"
    else:
        status = "active" if active else "inactive"
    out: dict = {
        "platform": "schtasks",
        "active": active,
        "status": status,
        "state": state,
        "state_source": source,
        # ⚠ `active` keeps #469's meaning (the task exists and is enabled). This
        # answers L-149's question: is the watcher the task starts running now?
        # The action IS the watcher, so the task reads Running exactly while it
        # runs. None when the state is unreadable: unknown, never stopped.
        "watcher_running": None if state is None else state == "Running",
        "detail": stdout.strip()[:400],
    }
    warnings: list[str] = []
    if state is not None and state != "Running":
        remedy = (
            "re-run `jcodemunch-mcp watch-install` (a disabled task cannot be started)"
            if state == "Disabled"
            else f"start it with `schtasks /Run /TN {SERVICE_NAME}` or log on again"
        )
        warnings.append(
            f"task '{SERVICE_NAME}' exists (state {state}) but no watcher is "
            f"running from it, so indexes are not kept fresh; {remedy}"
        )
    settings = _scheduled_task_settings()
    if settings is not None:
        out["settings"] = settings
        old = [
            k for k, v in _WINDOWS_TASK_SETTINGS.items()
            if settings.get(k) is not None and settings.get(k) != v
        ]
        if old:
            warnings.append(
                "task carries Task Scheduler defaults that stop the watcher on "
                f"battery or after 72 hours ({', '.join(old)}); re-run "
                "`jcodemunch-mcp watch-install` to replace it"
            )
    command = _scheduled_task_command()
    if command is not None:
        out["command"] = command
        if _windowless(command.strip().strip('"')) is not None:
            warnings.append(
                "task starts the console interpreter (python.exe), which opens "
                "a console window at every sign-in; re-run "
                "`jcodemunch-mcp watch-install` to register pythonw.exe instead"
            )
    if warnings:
        out["warnings"] = warnings
    return out


# ── Public dispatch ─────────────────────────────────────────────────────────


def install_service() -> dict:
    sys_ = platform.system()
    if sys_ == "Linux":
        return _install_systemd()
    if sys_ == "Darwin":
        return _install_launchd()
    if sys_ == "Windows":
        return _install_windows()
    raise InstallerError(f"Unsupported platform: {sys_}")


def uninstall_service() -> dict:
    sys_ = platform.system()
    if sys_ == "Linux":
        return _uninstall_systemd()
    if sys_ == "Darwin":
        return _uninstall_launchd()
    if sys_ == "Windows":
        return _uninstall_windows()
    raise InstallerError(f"Unsupported platform: {sys_}")


def service_status() -> dict:
    """The watch service's state. Every answer carries `status`: `active`,
    `inactive`, `not_installed` or `unknown`, read from what the probe said
    and never from an exit code alone. `active` keeps each platform's
    existing meaning: None where the systemd or launchd probe could not ask,
    and the display-text fallback on Windows (named by `state_source`). A
    reader that needs the classification reads `status`."""
    sys_ = platform.system()
    if sys_ == "Linux":
        out = _status_systemd()
    elif sys_ == "Darwin":
        out = _status_launchd()
    elif sys_ == "Windows":
        out = _status_windows()
    else:
        out = {"platform": sys_, "active": False, "status": "not_installed", "reason": "unsupported"}
    out.setdefault("status", "unknown")
    return out


# ── escaping helpers ────────────────────────────────────────────────────────


def _shell_quote(s: str) -> str:
    if not s or any(c in s for c in ' \t"\''):
        return "'" + s.replace("'", "'\\''") + "'"
    return s


def _cmd_quote(s: str) -> str:
    if " " in s or "\t" in s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )
