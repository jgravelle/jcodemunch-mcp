"""Is the lifetime savings figure in `_savings.json` still being measured?

⚠⚠ **A frozen meter read exactly like a live one.** A long-time customer's
`_savings.json` stopped taking new days while `_tool_counters.json` and
`_session_live.json` kept moving, and every surface that shows the lifetime
total showed the frozen number as current. Two causes, both in the writer: a
batch that earned 0 wrote nothing (so "unmeasured" and "earned 0" were the
same file), and a write that failed was logged at debug only.

The writer now stamps every flush that carried a call: `last_flush_at` (ISO
8601, UTC) and `measured_days[day] = {"calls": n, "saved": m}`. A 0 there is a
measurement; a missing day is not. This module reads that stamp and says what
a reader may claim about the total beside it:

- ``current``: the stamp exists, and nothing counted calls after it (the
  per-tool counters file, written by every server for every call, is the
  witness; a missing counters file is no evidence against the stamp).
- ``stale``: calls were counted after the last stamp, or the file has no
  stamp at all (written before the stamp existed, or by an older server).
  `lifetime_stale_since` is the last date the meter measured.
- ``unknown``: it cannot be told -- the savings file or the counters file
  exists and could not be read, or a stale file names no date. ⚠ UNKNOWN is
  never reported as ``current``.
- ``absent``: there is no savings file.

A leaf: stdlib only, and no network import, because `diagnose` reads it.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

# Fields the writer adds to `_savings.json`.
LAST_FLUSH_AT = "last_flush_at"
MEASURED_DAYS = "measured_days"

# Fields every lifetime display carries beside the total.
STATUS = "lifetime_status"
MEASURED_THROUGH = "lifetime_measured_through"
STALE_SINCE = "lifetime_stale_since"
STATUSES = ("current", "stale", "unknown", "absent")

# The counters file is written every 20 calls or on the first call after 30
# seconds; the savings writer stamps on a matching schedule. A gap under this
# is the two schedules disagreeing, not a meter that stopped.
STALE_TOLERANCE_SECONDS = 300.0

COUNTERS_FILE = "_tool_counters.json"  # the name `tool_counters.FILE` writes
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_stamp(value: object) -> Optional[datetime]:
    """`last_flush_at` as an aware UTC datetime, or None when it is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc)


def format_stamp(stamp: Optional[datetime]) -> Optional[str]:
    """One spelling for a measured-through time: `YYYY-MM-DDTHH:MM:SSZ`."""
    if stamp is None:
        return None
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(stamp: datetime) -> str:
    # The local date, the calendar `daily` and `measured_days` are keyed by.
    return stamp.astimezone().date().isoformat()


def counted_activity(root: "str | Path") -> "tuple[str, Optional[datetime]]":
    """When the per-tool counters last wrote: ``("ok", t)``, ``("none", None)`` or ``("unknown", None)``."""
    try:
        mtime = os.stat(Path(root) / COUNTERS_FILE).st_mtime
    except FileNotFoundError:
        return "none", None
    except OSError:
        return "unknown", None
    return "ok", datetime.fromtimestamp(mtime, tz=timezone.utc)


def label(
    data: "Optional[Mapping]",
    activity: "tuple[str, Optional[datetime]]" = ("none", None),
) -> dict:
    """The three label fields for a savings file's content.

    *data* is the parsed file, ``{}`` when there is no file, and ``None`` when
    the file exists and could not be read or parsed. *activity* is
    `counted_activity` for the same storage directory.
    """
    if data is None:
        return {STATUS: "unknown", MEASURED_THROUGH: None, STALE_SINCE: None}
    if not data:
        return {STATUS: "absent", MEASURED_THROUGH: None, STALE_SINCE: None}
    raw = data.get(LAST_FLUSH_AT)
    if raw is None:
        # No stamp at all: nothing says this total was measured recently.
        daily = data.get("daily")
        days = (
            sorted(k for k in daily if isinstance(k, str) and _DAY.fullmatch(k))
            if isinstance(daily, Mapping)
            else []
        )
        if days:
            return {STATUS: "stale", MEASURED_THROUGH: None, STALE_SINCE: days[-1]}
        return {STATUS: "unknown", MEASURED_THROUGH: None, STALE_SINCE: None}
    stamp = parse_stamp(raw)
    if stamp is None:
        return {STATUS: "unknown", MEASURED_THROUGH: None, STALE_SINCE: None}
    through = format_stamp(stamp)
    state, last = activity
    if state == "unknown":
        return {STATUS: "unknown", MEASURED_THROUGH: through, STALE_SINCE: None}
    if (
        state == "ok"
        and last is not None
        and (last - stamp).total_seconds() > STALE_TOLERANCE_SECONDS
    ):
        return {STATUS: "stale", MEASURED_THROUGH: through, STALE_SINCE: day_of(stamp)}
    return {STATUS: "current", MEASURED_THROUGH: through, STALE_SINCE: None}


def label_for_root(root: "str | Path", data: "Optional[Mapping]") -> dict:
    """`label` with the counters file of *root* as the witness."""
    return label(data, counted_activity(root))


def describe(fields: Mapping) -> Optional[str]:
    """The phrase a human surface prints beside the total, or None when it is current."""
    status = fields.get(STATUS)
    if status == "current" or status is None:
        return None
    if status == "stale":
        since = fields.get(STALE_SINCE)
        return f"unmeasured since {since}" if since else "unmeasured (date unknown)"
    if status == "absent":
        return "no savings file"
    return "measurement state unknown"
