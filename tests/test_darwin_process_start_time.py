"""macOS has a process creation time, so identity is not liveness alone there.

`process_locks._process_create_time` had a Windows branch and a Linux branch
and returned None everywhere else. On macOS every identity check therefore
fell back to "is this PID alive?": a parent whose PID was reused read as the
same parent (tests/test_serve_exits_when_parent_dies.py::test_a_reused_pid_is_gone),
and a month-old legacy lock read as live once its flock was released
(tests/test_legacy_lock_holder_identity.py::test_a_real_flock_on_a_month_old_legacy_lock_keeps_it_live).
Both failed on the first macOS full tier.

The darwin source is libproc's `proc_pidinfo(pid, PROC_PIDTBSDINFO)`, whose
`struct proc_bsdinfo` carries the start as epoch seconds and microseconds.
The parser is tested here on every OS from a packed struct; the live call is
checked only where it exists.
"""

from __future__ import annotations

import os
import struct
import sys
import time

import pytest

from jcodemunch_mcp.storage import process_locks as pl


def _bsdinfo(pid: int, sec: int, usec: int) -> bytes:
    # struct proc_bsdinfo, <sys/proc_info.h>: twelve 32-bit fields
    # (flags, status, xstatus, pid, ppid, uid, gid, ruid, rgid, svuid, svgid,
    # rfu_1), comm[16], name[32], six 32-bit fields (nfiles, pgid, pjobc,
    # e_tdev, e_tpgid, nice), then start_tvsec and start_tvusec as uint64.
    head = struct.pack("=12I", 0, 0, 0, pid, 1, 501, 20, 501, 20, 501, 20, 0)
    names = b"python".ljust(16, b"\0") + b"python".ljust(32, b"\0")
    tail = struct.pack("=5Ii", 3, pid, 0, 0, 0, 0) + struct.pack("=QQ", sec, usec)
    buf = head + names + tail
    assert len(buf) == pl._PROC_BSDINFO_SIZE == 136
    return buf


def test_the_start_time_is_read_from_proc_bsdinfo():
    got = pl._parse_proc_bsdinfo(_bsdinfo(4242, 1_700_000_000, 250_000), 4242)
    assert got == pytest.approx(1_700_000_000.25)


def test_a_short_buffer_is_unknown_not_a_time():
    assert pl._parse_proc_bsdinfo(_bsdinfo(4242, 1, 0)[:100], 4242) is None


def test_a_record_for_another_pid_is_unknown():
    assert pl._parse_proc_bsdinfo(_bsdinfo(1111, 1_700_000_000, 0), 4242) is None


def test_a_zero_start_is_unknown():
    assert pl._parse_proc_bsdinfo(_bsdinfo(4242, 0, 0), 4242) is None


def test_on_macos_this_process_has_a_creation_time():
    if sys.platform != "darwin":
        assert pl._darwin_process_start(os.getpid()) is None
        return
    created = pl._process_create_time(os.getpid())
    assert created is not None and created <= time.time()
    # The wall-clock reading is the same epoch value there.
    assert pl._process_wall_create_time(os.getpid()) == pytest.approx(created)


def test_the_identity_resource_reads_the_same_libproc_start(monkeypatch):
    # munch://runtime/identity had its own Windows and Linux probes and no
    # macOS one, so there it reported a self-recorded start. It now asks the
    # lock layer's reader, so one libproc call answers both.
    from jcodemunch_mcp import runtime_identity

    monkeypatch.setattr(pl, "_darwin_process_start", lambda pid: 1_700_000_000.25)
    assert (
        runtime_identity._darwin_process_start() == "2023-11-14T22:13:20.250000+00:00"
    )
    monkeypatch.setattr(pl, "_darwin_process_start", lambda pid: None)
    assert runtime_identity._darwin_process_start() is None


def test_the_identity_probe_dispatches_to_libproc_on_macos(monkeypatch):
    # The dispatch itself, on every OS: a host that is not Windows, has no
    # /proc and reports darwin must reach the libproc reader and say "os".
    from jcodemunch_mcp import runtime_identity

    monkeypatch.setattr(runtime_identity.os, "name", "posix")
    monkeypatch.setattr(runtime_identity.sys, "platform", "darwin")
    monkeypatch.setattr(runtime_identity.os.path, "exists", lambda p: False)
    monkeypatch.setattr(pl, "_darwin_process_start", lambda pid: 1_700_000_000.25)
    got = runtime_identity._probe_process_start()
    assert got == {"value": "2023-11-14T22:13:20.250000+00:00", "source": "os"}
