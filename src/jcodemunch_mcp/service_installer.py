"""Cross-platform login-service installer for `jcodemunch-mcp watch-all`.

- Linux: systemd --user unit at ~/.config/systemd/user/jcodemunch-watch.service
- macOS: launchd plist at ~/Library/LaunchAgents/us.gravelle.jcodemunch-watch.plist
- Windows: Task Scheduler task named `jcodemunch-watch`

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


def _status_systemd() -> dict:
    if shutil.which("systemctl") is None:
        return {"platform": "systemd", "active": False, "reason": "systemctl not found"}
    result = subprocess.run(
        ["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    state = result.stdout.strip() or result.stderr.strip()
    return {"platform": "systemd", "active": state == "active", "state": state}


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
    result = subprocess.run(
        ["launchctl", "list", LAUNCHD_LABEL],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return {"platform": "launchd", "active": result.returncode == 0, "detail": result.stdout.strip()}


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


def _install_windows() -> dict:
    _log_dir().mkdir(parents=True, exist_ok=True)
    cmd_str = " ".join(_cmd_quote(x) for x in _exec_cmd())
    # schtasks /Create does not persist stdout redirection; rely on Python logging
    # (watcher.py writes to stderr) and inspect via Event Viewer if needed.
    args = [
        "schtasks", "/Create", "/F",
        "/TN", SERVICE_NAME,
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/TR", cmd_str,
    ]
    # ⚠ The failure message is USER-FACING, so it needs the same decoding as the
    # status path — a localized "access denied" rendered as mojibake is a support
    # ticket, not a diagnosis.
    returncode, stdout, stderr = _run_schtasks(args)
    if returncode != 0:
        raise InstallerError(f"schtasks /Create failed: {stderr.strip() or stdout.strip()}")
    subprocess.run(["schtasks", "/Run", "/TN", SERVICE_NAME], check=False, capture_output=True)
    return {"platform": "schtasks", "task": SERVICE_NAME, "status": "registered"}


def _uninstall_windows() -> dict:
    returncode, _stdout, _stderr = _run_schtasks(
        ["schtasks", "/Delete", "/F", "/TN", SERVICE_NAME]
    )
    return {"platform": "schtasks", "task": SERVICE_NAME, "removed": returncode == 0}


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
    return {
        "platform": "schtasks",
        "active": active,
        "state": state,
        "state_source": source,
        "detail": stdout.strip()[:400],
    }


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
    sys_ = platform.system()
    if sys_ == "Linux":
        return _status_systemd()
    if sys_ == "Darwin":
        return _status_launchd()
    if sys_ == "Windows":
        return _status_windows()
    return {"platform": sys_, "active": False, "reason": "unsupported"}


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
