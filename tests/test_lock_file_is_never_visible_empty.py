"""A lock file is never on disk without the metadata that names its holder.

`process_locks.acquire` created the lock file with O_EXCL and wrote the
metadata after. A rival reading it in between found an empty file, took the
"corrupted" branch, unlinked it with no liveness check and created its own.
On Linux both processes then believed they held the lock. main CI on
f23bfb98 (#1519) saw `slot1` claimed twice in a burst of `index-file` runs
bounded to two slots. The `indexwrite` scope serialises index saves through
the same function, so the same window could let two saves write at once.

The metadata is now written to a private temporary file and linked into
place, which fails if the lock already exists. A reader sees no file or a
whole one.
"""

from __future__ import annotations

import json
import os

from jcodemunch_mcp.storage import process_locks


def test_no_reader_can_see_the_lock_before_its_metadata(tmp_path, monkeypatch):
    fp = process_locks.lock_path("burst", "slot1", str(tmp_path))
    seen: list = []
    real_write = os.write

    def write(fd, data):
        # The instant the holder writes its metadata, what would a rival read?
        if not seen:
            seen.append(fp.read_bytes() if fp.exists() else None)
        return real_write(fd, data)

    monkeypatch.setattr(process_locks.os, "write", write)
    assert process_locks.acquire("burst", "slot1", str(tmp_path))
    monkeypatch.setattr(process_locks.os, "write", real_write)
    try:
        assert seen, "acquire wrote no metadata"
        assert seen[0] is None, (
            f"the lock file was visible as {seen[0]!r} before its metadata"
        )
        assert json.loads(fp.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        process_locks.release("burst", "slot1", str(tmp_path))


def test_acquire_leaves_no_temporary_file_behind(tmp_path):
    before = set(os.listdir(tmp_path))
    assert process_locks.acquire("burst", "slot0", str(tmp_path))
    process_locks.release("burst", "slot0", str(tmp_path))
    assert process_locks.acquire("burst", "slot0", str(tmp_path))
    assert not process_locks.acquire("burst", "slot0", str(tmp_path))
    process_locks.release("burst", "slot0", str(tmp_path))
    assert set(os.listdir(tmp_path)) == before


def test_an_empty_lock_left_by_a_crash_is_still_reclaimed(tmp_path):
    fp = process_locks.lock_path("burst", "slot0", str(tmp_path))
    fp.write_bytes(b"")
    try:
        assert process_locks.acquire("burst", "slot0", str(tmp_path))
        assert json.loads(fp.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        process_locks.release("burst", "slot0", str(tmp_path))


def test_a_filesystem_without_hard_links_still_takes_the_lock(tmp_path, monkeypatch):
    def no_link(*a, **k):
        raise OSError("hard links not supported")

    monkeypatch.setattr(process_locks.os, "link", no_link)
    try:
        assert process_locks.acquire("burst", "slot0", str(tmp_path))
        assert not process_locks.acquire("burst", "slot0", str(tmp_path))
    finally:
        process_locks.release("burst", "slot0", str(tmp_path))
