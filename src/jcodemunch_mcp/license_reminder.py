"""The work-license reminder (J, DECISIONS item 650: "plus T, 7/30").

`license_notice` says the license terms once per machine. This says them again,
rarely: 7 days after the machine's first index, then at most once every 30 days.
Design: hq `projects/jcodemunch/DESIGN-work-license-reminder.md`.

⚠⚠ A LICENSE HOLDER IS NEVER REMINDED (J's condition). A configured key
(config `license_key` or `JCODEMUNCH_LICENSE_KEY`) silences it. The key is only
READ, never validated or sent: validating would make a network call from a
reminder.

⚠⚠ ONE COMMAND SILENCES IT FOR GOOD, AND EVERY REMINDER NAMES IT:
`jcodemunch-mcp config set license_reminder false`. `JCODEMUNCH_LICENSE_REMINDER=0`
and the older `JCODEMUNCH_LICENSE_NOTICE=0` also turn it off.

Two surfaces, one clock:
- the CLI: one line on STDERR after a command a person runs and reads, only when
  stdout AND stderr are terminals and `--json` was not asked for. stdout is never
  written, so a pipe or a JSON reader sees nothing different.
- Option T: the `license_notice` field on the first successful tool answer of a
  server process, when due. Routine calls carry nothing. The text is a statement
  and names the command as something A PERSON runs; it never asks the agent to
  relay it or run anything.

⚠ Quiet on CI and remote runners (`CI` / `CLAUDE_CODE_REMOTE` true), like
`license_notice`. `CLAUDECODE=1` is NOT a CI marker.

⚠ State is one local file, `license_reminder_state.json` under the store: when an
index was first seen, when the reminder last showed, and how many times. Nothing
is sent anywhere. A check that fails says nothing.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence, TextIO

logger = logging.getLogger(__name__)

BUY_URL = "https://jcodemunch.com/buying.php"
#: A fixed source tag per copy, so a sale through the reminder can be told from
#: other sales. The same on every machine: nothing about the install is in it,
#: and nothing is sent unless a person opens the link.
_TAG = "?utm_source=cli&utm_medium={medium}&utm_campaign=license-reminder"
CLI_BUY_URL = BUY_URL + _TAG.format(medium="reminder")
TOOL_BUY_URL = BUY_URL + _TAG.format(medium="tool")
SILENCE_COMMAND = "jcodemunch-mcp config set license_reminder false"
#: The terms, in the words of the plugin README and the site (item 725).
WORK_TERMS = (
    "Work use at a for-profit company, or for client or paid work, needs a license, "
    "paid once, that includes every 1.x update"
)
CLI_TEXT = (
    f"Personal use stays free. {WORK_TERMS}: {CLI_BUY_URL} "
    f"(silence: {SILENCE_COMMAND})"
)
# The URL ends the text, so no period follows it into the link's campaign value.
TOOL_TEXT = (
    f"Personal use stays free. A person can turn these reminders off with: {SILENCE_COMMAND}. "
    f"{WORK_TERMS}: {TOOL_BUY_URL}"
)
#: The line `receipt` ends with on a machine with no license key. Its own
#: campaign, so a sale from the savings receipt can be told from the reminder's.
RECEIPT_BUY_URL = (
    BUY_URL + "?utm_source=cli&utm_medium=receipt&utm_campaign=savings-receipt"
)
RECEIPT_TEXT = (
    "Using it at a for-profit company, or for client or paid work? "
    f"That needs a license, paid once: {RECEIPT_BUY_URL}"
)
ENV_VAR = "JCODEMUNCH_LICENSE_REMINDER"
NOTICE_ENV_VAR = "JCODEMUNCH_LICENSE_NOTICE"
CONFIG_KEY = "license_reminder"
STATE_FILE = "license_reminder_state.json"
FIRST_DELAY = timedelta(days=7)
INTERVAL = timedelta(days=30)
CI_MARKERS = ("CI", "CLAUDE_CODE_REMOTE")
#: Commands a person runs by hand and reads. Never serve, watch*, hook-*,
#: index-file, config, license or init (init prints its own license line).
CLI_COMMANDS = frozenset(
    {
        "receipt",
        "digest",
        "health",
        "install-status",
        "whatsnew",
        "upgrade",
        "index",
        "refresh",
        "file-risk",
        "delivery",
        "reflect",
        "list-repos",
    }
)
#: Databases in the store that are not indexes.
NON_INDEX_DBS = frozenset({"telemetry.db", "org_savings.db"})
#: The tool path decides once per process; conftest turns it off for every test.
REMINDER_STATE = {"checked": False}

_OFF = frozenset({"0", "false", "no", "off"})
_ON = frozenset({"1", "true", "yes", "on"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def state_path() -> Path:
    """The state file, under the same store every in-answer notice uses."""
    from .surface_offer import notice_marker_path

    return notice_marker_path(STATE_FILE)


def _licence_key_configured() -> bool:
    from .org.license import licence_key_configured

    return licence_key_configured()


def _config_silenced() -> bool:
    """True when the GLOBAL config sets `license_reminder` to false.

    ⚠ Global only, deliberately: the reminder is about the machine, not a repo,
    and a CLI command or a server's first answer has no one repo to read a
    project `.jcodemunch.jsonc` from. `config set` writes the global file.
    """
    from . import config

    return config.get(CONFIG_KEY, True) is False


def _has_index(store: Path) -> bool:
    try:
        return any(p.name not in NON_INDEX_DBS for p in store.glob("*.db"))
    except OSError:
        return False


def _load_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.debug("license reminder state not written", exc_info=True)


def _parse(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def switch_reason(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Which off switch applies here, or None. No clock and no state.

    The one list of switches: `skip_reason` (the reminder) and `receipt_line`
    both read it, so a switch added here silences both.
    """
    env = os.environ if env is None else env
    for name in (ENV_VAR, NOTICE_ENV_VAR):
        if (env.get(name) or "").strip().lower() in _OFF:
            return "disabled"
    if any((env.get(name) or "").strip().lower() in _ON for name in CI_MARKERS):
        return "ci"
    if _config_silenced():
        return "silenced"
    if _licence_key_configured():
        return "licensed"
    return None


def skip_reason(
    env: Optional[Mapping[str, str]] = None, now: Optional[datetime] = None
) -> Optional[str]:
    """Why the reminder is not due here, or None when it is. Cheapest checks first.

    The first sighting of an index records `first_index_at` and returns
    `too_early`, so the 7 days count from the first time an index was seen.
    """
    now = now or _now()
    reason = switch_reason(env)
    if reason is not None:
        return reason
    state = _load_state()
    first = _parse(state.get("first_index_at"))
    if first is None:
        if not _has_index(state_path().parent):
            return "no_index"
        state["first_index_at"] = now.isoformat()
        _save_state(state)
        return "too_early"
    if now - first < FIRST_DELAY:
        return "too_early"
    last = _parse(state.get("last_shown"))
    if last is not None and now - last < INTERVAL:
        return "recent"
    return None


def receipt_line(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The license line for the end of `receipt`, or None.

    Every receipt carries it, with no 7/30-day clock: the receipt is the page
    where a person reads what the tool saved them. The reminder's off switches
    all apply (both environment variables, `config set license_reminder false`,
    a CI or remote runner) and a configured license key silences it. A check
    that fails says nothing.
    """
    try:
        if switch_reason(env) is not None:
            return None
        return RECEIPT_TEXT
    except Exception:
        logger.debug("receipt license line skipped", exc_info=True)
        return None


def mark_shown(now: Optional[datetime] = None) -> None:
    """Record a reminder: its time and the running count, nothing else."""
    state = _load_state()
    state["last_shown"] = (now or _now()).isoformat()
    count = state.get("count")
    state["count"] = (count if isinstance(count, int) else 0) + 1
    _save_state(state)


def _is_tty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except Exception:
        return False


def cli_reminder(
    argv: Sequence[str],
    *,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """After a listed command, say the reminder on stderr when due. Returns what it said."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        command = next((a for a in argv if not a.startswith("-")), None)
        if command not in CLI_COMMANDS or "--json" in argv:
            return None
        if not (_is_tty(stdout) and _is_tty(stderr)):
            return None
        if skip_reason(now=now) is not None:
            return None
        mark_shown(now)
        print(CLI_TEXT, file=stderr, flush=True)
        return CLI_TEXT
    except Exception:
        logger.debug("license reminder (cli) skipped", exc_info=True)
        return None


def tool_reminder_for(
    result: object, *, now: Optional[datetime] = None
) -> Optional[str]:
    """The reminder for this tool answer, or None. Checked once per server process.

    Only a successful dict answer can carry it; anything else leaves the check
    for the next answer instead of using it up.
    """
    if not isinstance(result, dict) or "error" in result:
        return None
    if REMINDER_STATE["checked"]:
        return None
    REMINDER_STATE["checked"] = True
    try:
        if skip_reason(now=now or _now()) is not None:
            return None
        mark_shown(now or _now())
        return TOOL_TEXT
    except Exception:
        logger.debug("license reminder (tool) skipped", exc_info=True)
        return None
