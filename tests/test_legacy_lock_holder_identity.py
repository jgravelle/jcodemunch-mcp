"""#728 (@Matt-hew93): a lock with no `create_time` was trusted on PID existence alone.

jcm#450 made holder identity `pid + create_time`. A lock written BEFORE that
carries neither `create_time` nor `client_id`, and `_is_live_holder` kept the
old behaviour for it: "there is nothing to compare against". There is. Every
lock records `started_at`, and a process cannot hold a lock written before the
process existed. The reporter's lock was written 2026-08-17; the PID it named
belonged to an `OpenConsole.exe` created 2026-09-16, and the repo read as
`watching` for a month with nothing watching it.

The behavioural tests use THIS process as the holder and the real OS clock:
no mock supplies the creation time, because a mocked creation time is the
contract the defect lacked.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from jcodemunch_mcp.storage import process_locks

SCOPE = "watcher"


def _write_legacy_lock(tmp_path, target, started_at, **extra):
    """The pre-#450 shape the reporter's machine held: pid, folder, started_at."""
    lock_fp = process_locks.lock_path(SCOPE, target, str(tmp_path))
    lock_fp.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "folder": target, "started_at": started_at, **extra}
    lock_fp.write_text(json.dumps(payload), encoding="utf-8")
    return lock_fp


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _can_read_wall_clock_creation() -> bool:
    reader = getattr(process_locks, "_process_wall_create_time", None)
    return reader is not None and reader(os.getpid()) is not None


def test_a_lock_older_than_the_process_holding_its_pid_is_stale(tmp_path):
    """The report: the lock predates the live process by weeks."""
    target = str(tmp_path / "repo")
    _write_legacy_lock(tmp_path, target, _iso(timedelta(days=-30)))
    if not _can_read_wall_clock_creation() and hasattr(process_locks, "_process_wall_create_time"):
        return  # platform with no creation-time source: liveness-only is all there is
    assert process_locks.inspect(SCOPE, target, str(tmp_path)) is None


def test_a_stale_legacy_lock_does_not_block_a_new_watcher(tmp_path):
    """The impact line: "a new watcher may decline to claim a folder"."""
    target = str(tmp_path / "repo")
    _write_legacy_lock(tmp_path, target, _iso(timedelta(days=-30)))
    if not _can_read_wall_clock_creation() and hasattr(process_locks, "_process_wall_create_time"):
        return
    try:
        assert process_locks.acquire(SCOPE, target, str(tmp_path)) is True
    finally:
        process_locks.release(SCOPE, target, str(tmp_path))


def test_a_legacy_lock_written_after_the_process_started_is_still_live(tmp_path):
    """The contrast case in the report: a genuine holder must keep its lock.
    A fix that expires every legacy lock would pass the two tests above."""
    target = str(tmp_path / "repo")
    _write_legacy_lock(tmp_path, target, _iso(timedelta(seconds=0)))
    holder = process_locks.inspect(SCOPE, target, str(tmp_path))
    assert holder is not None and holder.pid == os.getpid()


# --------------------------------------------------------------------------- #
# The rule, alone. Times are injected here ONLY to reach the boundaries the    #
# real clock cannot: the behaviour above never mocks.                         #
# --------------------------------------------------------------------------- #

LOCK_WRITTEN = datetime(2026, 8, 17, 21, 55, 27, tzinfo=timezone.utc)


@pytest.fixture
def _alive(monkeypatch):
    monkeypatch.setattr(process_locks, "_is_pid_alive", lambda pid: True)


def _created(monkeypatch, when):
    value = None if when is None else when.timestamp()
    monkeypatch.setattr(process_locks, "_process_wall_create_time", lambda pid: value, raising=False)


@pytest.mark.parametrize("offset, live", [
    (timedelta(days=30), False),      # the report
    (timedelta(hours=1), False),
    (timedelta(seconds=-5), True),    # created before the lock: the normal case
    (timedelta(seconds=30), True),    # inside the margin: clock skew, never identity
])
def test_created_after_the_lock_was_written_means_recycled(monkeypatch, _alive, offset, live):
    _created(monkeypatch, LOCK_WRITTEN + offset)
    assert process_locks._is_live_holder(4242, None, LOCK_WRITTEN.isoformat()) is live


@pytest.mark.parametrize("started_at", [None, "", "not a date", 17])
def test_an_unreadable_started_at_is_unknown_never_stale(monkeypatch, _alive, started_at):
    """UNKNOWN is not a verdict: a live holder is never declared dead on a parse failure."""
    _created(monkeypatch, LOCK_WRITTEN + timedelta(days=30))
    assert process_locks._is_live_holder(4242, None, started_at) is True


def test_an_unreadable_creation_time_is_unknown_never_stale(monkeypatch, _alive):
    _created(monkeypatch, None)
    assert process_locks._is_live_holder(4242, None, LOCK_WRITTEN.isoformat()) is True


def test_a_naive_and_a_z_suffixed_timestamp_both_parse(monkeypatch, _alive):
    """The reporter's `watcher_holder` rendered `2026-08-17T21:55:27` with no offset."""
    _created(monkeypatch, LOCK_WRITTEN + timedelta(days=30))
    assert process_locks._is_live_holder(4242, None, "2026-08-17T21:55:27") is False
    assert process_locks._is_live_holder(4242, None, "2026-08-17T21:55:27Z") is False


def test_a_naive_timestamp_is_utc_not_local():
    """`acquire` has always written UTC, so a naive `started_at` is UTC.

    ⚠ On a host whose zone IS UTC (every CI runner) a local-time reading agrees
    with this by coincidence, so only a developer box can see that defect. The
    assertion is still exact everywhere; the 30-day cases above cannot see it
    anywhere, which is why this one exists.
    """
    assert process_locks._parse_started_at("2026-08-17T21:55:27") == LOCK_WRITTEN.timestamp()
    assert process_locks._parse_started_at("2026-08-17T16:55:27-05:00") == LOCK_WRITTEN.timestamp()


def test_a_recorded_create_time_still_decides_first(monkeypatch, _alive):
    """#450's exact comparison outranks the one-directional legacy rule."""
    monkeypatch.setattr(process_locks, "_process_create_time", lambda pid: 1000.0)
    _created(monkeypatch, LOCK_WRITTEN + timedelta(days=30))
    assert process_locks._is_live_holder(4242, 1000.0, LOCK_WRITTEN.isoformat()) is True
    assert process_locks._is_live_holder(4242, 5000.0, LOCK_WRITTEN.isoformat()) is False


def test_a_dead_pid_is_dead_whatever_the_lock_says(monkeypatch):
    monkeypatch.setattr(process_locks, "_is_pid_alive", lambda pid: False)
    assert process_locks._is_live_holder(4242, None, _iso(timedelta(0))) is False


# --------------------------------------------------------------------------- #
# The second reader. #450's own forensics were REGISTRY rows ("two-week-old    #
# rows resolving to a Chrome renderer"), and a pre-#450 row has no create_time #
# either.                                                                     #
# --------------------------------------------------------------------------- #

def _write_legacy_registry_row(tmp_path, started_at):
    from jcodemunch_mcp.storage import process_registry

    directory = process_registry._registry_dir(str(tmp_path))
    directory.mkdir(parents=True, exist_ok=True)
    row = directory / f"{os.getpid()}.json"
    row.write_text(json.dumps({
        "pid": os.getpid(), "transport": "stdio", "version": "1.108.100",
        "client_id": "legacy", "started_at": started_at,
    }), encoding="utf-8")
    return row


def test_a_registry_row_older_than_the_process_holding_its_pid_is_pruned(tmp_path):
    from jcodemunch_mcp.storage import process_registry

    row = _write_legacy_registry_row(tmp_path, _iso(timedelta(days=-30)))
    if not _can_read_wall_clock_creation():
        return
    assert process_registry.live_processes(str(tmp_path)) == []
    assert not row.exists()


def test_a_registry_row_written_by_this_process_survives(tmp_path):
    from jcodemunch_mcp.storage import process_registry

    _write_legacy_registry_row(tmp_path, _iso(timedelta(0)))
    assert [e.pid for e in process_registry.live_processes(str(tmp_path))] == [os.getpid()]


def test_every_caller_hands_over_started_at():
    """A parameter a caller omits is the defect again, for that caller only
    (CLAUDE.md 08-19: "a parameter that is present and does nothing"). Every
    call of `_is_live_holder` in src/ passes three arguments."""
    import ast
    from pathlib import Path

    src = Path(process_locks.__file__).resolve().parents[1]
    calls = []
    for path in sorted(src.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_is_live_holder":
                calls.append((path.name, node.lineno, len(node.args) + len(node.keywords)))
    assert len(calls) >= 3, calls
    assert all(count == 3 for _, _, count in calls), calls
