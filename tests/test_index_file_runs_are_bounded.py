"""`index-file` runs are bounded: one per checkout, `MAX_CONCURRENT` per machine.

The PostToolUse hook starts a detached `index-file` for every Edit or Write and
nothing counted them. Eight edits in a row held eight runs and about 800 MB at once on
a 50,000-symbol index. An agent session that edited 47 times against an index
taking minutes to build stalled the development laptop overnight (2026-11-15),
and its host ended the session by killing only the agent, so the runs outlived
it. The locks here are held by real child processes, because a lock this
process holds proves nothing about another one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest import mock

import pytest

from jcodemunch_mcp.storage import index_file_slots as slots


@pytest.fixture
def slot_dir(tmp_path, monkeypatch):
    d = tmp_path / "slots"
    monkeypatch.setattr(slots, "slot_dir", lambda: d)
    return d


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(s))
    return str(s)


def _hold_in_child(d, scope: str, target: str) -> subprocess.Popen:
    """A separate process that holds one lock until it is ended."""
    code = textwrap.dedent(
        f"""
        import time
        from jcodemunch_mcp.storage import process_locks
        assert process_locks.acquire({scope!r}, {target!r}, {str(d)!r})
        print("held", flush=True)
        time.sleep(120)
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
    )
    assert child.stdout.readline().strip() == "held"
    return child


@pytest.fixture
def every_slot_held(slot_dir):
    children = [
        _hold_in_child(slot_dir, slots.SCOPE, slots._lock_target(t))
        for t in slots._targets()
    ]
    yield children
    for c in children:
        c.kill()
        c.wait()


def _index_lock(key):
    return slots._lock_target(f"key-{slots._digest(key)}")


def _file(tmp_path, name="a.py"):
    f = tmp_path / "repo" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def f():\n    return 1\n")
    return f


def _locks_left(d):
    return sorted(p.name for p in d.glob("_indexfile*.lock")) if d.exists() else []


def test_the_slots_are_outside_every_index_store(monkeypatch, tmp_path):
    """A host that gives each session its own CODE_INDEX_PATH must still share one bound."""
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("CODE_INDEX_PATH", str(a))
    first = slots.slot_dir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(b))
    assert slots.slot_dir() == first
    assert a not in first.parents and b not in first.parents


def test_no_run_starts_while_every_slot_is_held_by_a_live_process(
    slot_dir, every_slot_held
):
    c, why = slots.claim("store|repo-a")
    assert c is None and why == "busy"


def test_a_slot_whose_holder_died_is_free_again(slot_dir, every_slot_held):
    every_slot_held[0].kill()
    every_slot_held[0].wait()
    c, why = slots.claim("store|repo-a")
    assert why is None and c.slot == slots._lock_target(slots._targets()[0])
    slots.release(c)


def test_a_second_run_for_the_same_index_is_turned_away(slot_dir):
    child = _hold_in_child(slot_dir, slots.INDEX_SCOPE, _index_lock("store|repo-a"))
    try:
        c, why = slots.claim("store|repo-a")
        assert c is None and why == "same_index_busy"
        other, why_other = slots.claim("store|repo-b")
        assert why_other is None, "a different index must not wait on this one"
        slots.release(other)
    finally:
        child.kill()
        child.wait()


def test_the_pending_list_is_taken_once_without_repeats(slot_dir):
    for p in ("x.py", "y.py", "x.py"):
        assert slots.queue("store|repo-a", p)
    assert slots.take_pending("store|repo-a") == ["x.py", "y.py"]
    assert slots.take_pending("store|repo-a") == []


def test_index_file_does_no_work_when_every_slot_is_held(
    slot_dir, store, every_slot_held, tmp_path, capsys
):
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    with mock.patch("jcodemunch_mcp.tools.index_file.index_file") as work:
        server.main(["index-file", str(target)])
    work.assert_not_called()
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] == "busy"
    assert out["queued_for_live_run"] is False


def test_index_file_for_an_index_with_a_live_run_queues_and_does_no_work(
    slot_dir, store, tmp_path, capsys
):
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    key = slots.index_key(str(target), store)
    child = _hold_in_child(slot_dir, slots.INDEX_SCOPE, _index_lock(key))
    try:
        with mock.patch("jcodemunch_mcp.tools.index_file.index_file") as work:
            server.main(["index-file", str(target)])
        work.assert_not_called()
        out = json.loads(capsys.readouterr().out)
        assert (
            out["skipped"] == "same_index_busy" and out["queued_for_live_run"] is True
        )
        assert slots.take_pending(key) == [str(target)]
    finally:
        child.kill()
        child.wait()


def test_the_live_run_indexes_what_was_queued_and_gives_both_locks_back(
    slot_dir, store, tmp_path, capsys
):
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    later = _file(tmp_path, "b.py")
    key = slots.index_key(str(target), store)
    done = []

    def fake_index_file(path, **kwargs):
        if not done:
            # Another edit arrives while the first run has its index loaded.
            assert slots.queue(key, str(later))
            assert len(_locks_left(slot_dir)) == 2, (
                "the run did not hold one slot and its index lock"
            )
        done.append(path)
        return {"success": True}

    with mock.patch(
        "jcodemunch_mcp.tools.index_file.index_file", side_effect=fake_index_file
    ):
        server.main(["index-file", str(target)])
    assert done == [str(target), str(later)]
    assert json.loads(capsys.readouterr().out)["coalesced_paths"] == 1
    assert _locks_left(slot_dir) == []


def test_both_locks_are_given_back_when_the_run_fails(slot_dir, store, tmp_path):
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    with mock.patch(
        "jcodemunch_mcp.tools.index_file.index_file", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(RuntimeError):
            server.main(["index-file", str(target)])
    assert _locks_left(slot_dir) == []


def test_the_hook_still_starts_a_run_when_every_slot_is_held(
    slot_dir, every_slot_held, tmp_path
):
    """The hook does not read the slots: importing the lock layer costs every hook
    0.66 s, and the command that it starts enforces the bound itself."""
    from jcodemunch_mcp.cli.hooks import reindex

    with mock.patch("jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen") as popen:
        reindex._spawn_index_file(str(tmp_path / "a.py"))
    popen.assert_called_once()


def _bound_violations(held: list[list]) -> list[str]:
    """What a burst's claims say about the bound, from ``[slot, claimed, released]`` rows.

    A row's times are taken inside the hold: after ``claim`` returned and before
    ``release`` ran. A later run that took the slot after its release therefore
    starts after the earlier row ends, so only a real overlap is reported. A
    count of claims over the whole burst cannot tell the two apart.
    """
    out = []
    events = sorted(
        [(claimed, 1) for _slot, claimed, _released in held]
        + [(released, -1) for _slot, _claimed, released in held],
        key=lambda e: (e[0], e[1]),
    )
    now = most = 0
    for _at, step in events:
        now += step
        most = max(most, now)
    if most > slots.MAX_CONCURRENT:
        out.append(f"{most} runs worked at once, bound {slots.MAX_CONCURRENT}")
    by_slot: dict = {}
    for slot, claimed, released in held:
        by_slot.setdefault(slot, []).append((claimed, released))
    for spans in by_slot.values():
        spans.sort()
        if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
            out.append("two live processes held one slot")
            break
    return out


def test_a_slot_reused_after_its_release_is_within_the_bound():
    """main CI on f23bfb98 (#1519): slot1 was claimed again after its first holder let go.

    One run started more than the 5 s hold late on a loaded runner, found slot1
    free, and the burst test failed `assert 3 <= 2` with two runs ever working
    at once. Only claims that overlap in time are over the bound.
    """
    sequential = [
        ["slot0", 100.0, 105.0],
        ["slot1", 100.1, 105.1],
        ["slot1", 106.0, 111.0],
    ]
    assert _bound_violations(sequential) == []


def test_claims_that_overlap_past_the_bound_are_reported():
    over = [
        ["slot0", 100.0, 105.0],
        ["slot1", 100.1, 105.1],
        ["slot1", 104.0, 109.0],
    ]
    found = _bound_violations(over)
    assert any("at once" in f for f in found)
    assert any("one slot" in f for f in found)


def test_a_burst_of_real_runs_never_works_more_than_the_bound_at_once(slot_dir):
    """Real processes on different indexes, each holding its claim for a while."""
    code = textwrap.dedent(
        """
        import json, sys, time
        from pathlib import Path
        from jcodemunch_mcp.storage import index_file_slots as s
        s.slot_dir = lambda: Path(sys.argv[1])
        c, why = s.claim(sys.argv[2])
        if c is None:
            print(json.dumps(None))
        else:
            claimed = time.time()
            time.sleep(5)
            released = time.time()
            s.release(c)
            print(json.dumps([c.slot, claimed, released]))
        """
    )
    burst = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(slot_dir), f"store|repo-{i}"],
            stdout=subprocess.PIPE,
            text=True,
        )
        for i in range(slots.MAX_CONCURRENT + 4)
    ]
    started = time.monotonic()
    claimed = [json.loads(p.communicate(timeout=60)[0]) for p in burst]
    assert time.monotonic() - started < 60
    held = [c for c in claimed if c is not None]
    assert held, "no run worked"
    assert _bound_violations(held) == []


def test_a_lock_error_runs_the_work_and_says_so(
    slot_dir, store, tmp_path, capsys, monkeypatch
):
    """UNKNOWN is not busy: a lock layer that cannot be used must not turn every re-index off."""
    from jcodemunch_mcp import server
    from jcodemunch_mcp.storage import process_locks

    def broken(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(process_locks, "acquire", broken)
    target = _file(tmp_path)
    with mock.patch(
        "jcodemunch_mcp.tools.index_file.index_file", return_value={"success": True}
    ) as work:
        server.main(["index-file", str(target)])
    work.assert_called_once()
    assert "read-only" in json.loads(capsys.readouterr().out)["slot_error"]


def test_the_stored_mtime_is_never_from_a_later_edit_than_the_stored_content(
    tmp_path, monkeypatch
):
    """An edit landing between the read and the stat must leave the file looking changed,
    or refresh_on_read (which compares mtimes) can never repair a skipped run."""
    import builtins
    import os

    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools import index_file as mod
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "src"
    src.mkdir()
    f = src / "calc.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    store = str(tmp_path / "store")
    assert index_folder(str(src), use_ai_summaries=False, storage_path=store)["success"]
    f.write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )

    real_open = builtins.open
    edited = {}

    def open_then_edit(path, *a, **k):
        handle = real_open(path, *a, **k)
        if os.path.abspath(str(path)) == os.path.abspath(str(f)) and not edited:
            text = handle.read()
            handle.close()
            later = os.stat(f).st_mtime_ns + 5_000_000_000
            with real_open(f, "a", encoding="utf-8") as w:
                w.write("\ndef mul(a, b):\n    return a * b\n")
            os.utime(f, ns=(later, later))
            edited["mtime"] = later
            import io

            return io.StringIO(text)
        return handle

    monkeypatch.setattr(mod, "open", open_then_edit, raising=False)
    assert mod.index_file(path=str(f), use_ai_summaries=False, storage_path=store)[
        "success"
    ]
    stored = IndexStore(base_path=store)
    idx = next(iter(stored.list_repos()), None)
    assert idx is not None
    owner, name = idx["repo"].split("/", 1)
    loaded = stored.load_index(owner, name)
    stored_ns = loaded.file_mtimes.get("calc.py")
    assert stored_ns is not None
    assert stored_ns < edited["mtime"], (
        "the stored mtime belongs to an edit whose content was not stored"
    )


def test_a_path_queued_after_the_last_read_and_before_the_release_is_still_indexed(
    slot_dir, store, tmp_path, capsys
):
    """The interleaving is pinned: the queue write happens inside the live run's
    release, after drain's last take_pending and before the locks are free."""
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    late = _file(tmp_path, "late.py")
    key = slots.index_key(str(target), store)
    done = []
    real_release = slots.release
    releases = []

    def release_with_a_late_edit(c):
        if not releases:
            assert slots.queue(
                key, str(late)
            )  # the lock is still held: this is the stranded case
        releases.append(c)
        real_release(c)

    def fake_index_file(path, **kwargs):
        done.append(path)
        return {"success": True}

    with (
        mock.patch.object(slots, "release", side_effect=release_with_a_late_edit),
        mock.patch(
            "jcodemunch_mcp.tools.index_file.index_file", side_effect=fake_index_file
        ),
    ):
        server.main(["index-file", str(target)])
    assert done == [str(target), str(late)], "the late path was stranded"
    assert json.loads(capsys.readouterr().out)["coalesced_paths"] == 1
    assert not slots.has_pending(key) and _locks_left(slot_dir) == []


def test_one_queued_path_failing_is_counted_and_never_stops_the_rest(
    slot_dir, store, tmp_path, capsys
):
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    bad, good = _file(tmp_path, "bad.py"), _file(tmp_path, "good.py")
    key = slots.index_key(str(target), store)
    assert slots.queue(key, str(bad)) and slots.queue(key, str(good))
    done = []

    def fake_index_file(path, **kwargs):
        if path == str(bad):
            raise RuntimeError("parse failed")
        done.append(path)
        return {"success": True}

    with mock.patch(
        "jcodemunch_mcp.tools.index_file.index_file", side_effect=fake_index_file
    ):
        server.main(["index-file", str(target)])
    out = json.loads(capsys.readouterr().out)
    assert done == [str(target), str(good)]
    assert out["coalesced_paths"] == 1 and out["coalesced_failures"] == 1
    assert _locks_left(slot_dir) == []


def _st(mode, uid=1000):
    return mock.Mock(st_mode=mode, st_uid=uid)


def test_a_slot_directory_is_used_only_when_it_is_private_to_this_user():
    import stat

    assert slots._check_private(_st(stat.S_IFDIR | 0o700), 1000) is None
    assert slots._check_private(_st(stat.S_IFLNK | 0o777), 1000) == "is a symlink"
    assert slots._check_private(_st(stat.S_IFREG | 0o600), 1000) == "is not a directory"
    assert (
        slots._check_private(_st(stat.S_IFDIR | 0o700, uid=0), 1000)
        == "is owned by uid 0"
    )
    assert (
        slots._check_private(_st(stat.S_IFDIR | 0o755), 1000) == "has mode 755, not 700"
    )
    # a mode the owner itself cannot use is not private either
    assert (
        slots._check_private(_st(stat.S_IFDIR | 0o500), 1000) == "has mode 500, not 700"
    )
    # Windows: the temp directory is per user, so only the type is checked.
    assert slots._check_private(_st(stat.S_IFDIR | 0o777, uid=0), None) is None


def test_a_refused_slot_directory_runs_nothing_and_says_so(
    slot_dir, store, tmp_path, capsys, monkeypatch
):
    """A slot folder that is a symlink or another user's could hold every slot
    or none: no run trusts it, and the edit is left to refresh_on_read."""
    from jcodemunch_mcp import server

    monkeypatch.setattr(slots, "_check_private", lambda st, uid: "is a symlink")
    target = _file(tmp_path)
    with mock.patch(
        "jcodemunch_mcp.tools.index_file.index_file", return_value={"success": True}
    ) as m:
        server.main(["index-file", str(target)])
    assert m.call_count == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] == "slot_dir_refused" and out["indexed"] is False
    assert slots.queue("k", "p") is False  # nothing is written into a refused directory


class _St:
    def __init__(self, mode, uid):
        self.st_mode, self.st_uid = mode, uid


def _fake_fd_ops(monkeypatch, mode, uid):
    """The descriptor operations _check_posix_dir uses, on a fake folder."""
    import stat

    state = {"mode": stat.S_IFDIR | mode, "uid": uid, "fchmods": [], "closed": 0}
    monkeypatch.setattr(slots, "_owner_uid", lambda: 501)
    monkeypatch.setattr(slots, "_open_dir_nofollow", lambda d: 99)
    monkeypatch.setattr(slots, "_fstat", lambda fd: _St(state["mode"], state["uid"]))

    def fchmod(fd, m):
        assert fd == 99
        state["fchmods"].append(m)
        state["mode"] = stat.S_IFDIR | m

    monkeypatch.setattr(slots, "_fchmod", fchmod)
    monkeypatch.setattr(
        slots, "_close", lambda fd: state.__setitem__("closed", state["closed"] + 1)
    )
    return state


def test_our_own_slot_directory_with_another_mode_is_tightened_not_refused(
    slot_dir, monkeypatch
):
    """The lock layer makes a missing folder with the default umask (0755), and
    on macOS and Linux a check that refused it turned the bound off (the
    2026-11-15 Mac run of this branch). The change goes through the descriptor
    the check read, never through the path a second time."""
    slot_dir.mkdir()
    state = _fake_fd_ops(monkeypatch, 0o755, 501)
    assert slots._private_dir() == slot_dir
    assert state["fchmods"] == [0o700] and state["closed"] == 1


def test_another_users_slot_directory_is_refused_and_left_alone(slot_dir, monkeypatch):
    slot_dir.mkdir()
    state = _fake_fd_ops(monkeypatch, 0o777, 0)
    with pytest.raises(slots.SlotDirRefused, match="owned by uid 0"):
        slots._private_dir()
    assert state["fchmods"] == [] and state["closed"] == 1


def test_a_slot_directory_that_cannot_be_opened_without_following_a_link_is_refused(
    slot_dir, monkeypatch
):
    slot_dir.mkdir()
    monkeypatch.setattr(slots, "_owner_uid", lambda: 501)

    def eloop(d):
        raise OSError(40, "Too many levels of symbolic links")

    monkeypatch.setattr(slots, "_open_dir_nofollow", eloop)
    with pytest.raises(slots.SlotDirRefused, match="without following a link"):
        slots._private_dir()


def test_a_real_folder_is_kept_and_a_non_folder_is_refused(slot_dir, tmp_path):
    """On POSIX a 0500 folder becomes 0700 and a symlink is refused; Windows
    checks the folder type only, so there a file in its place is refused.
    One test for both, so neither platform counts a skip."""
    import stat

    if os.name == "nt":
        slot_dir.mkdir()
        assert slots._private_dir() == slot_dir
        os.rmdir(slot_dir)
        slot_dir.write_text("not a folder", encoding="utf-8")
        with pytest.raises(slots.SlotDirRefused):
            slots._private_dir()
        return
    # Any mode other than 0700 is tightened; 0500 (owner only) shows it with
    # no group or other bit in the test.
    slot_dir.mkdir(mode=0o700)
    os.chmod(slot_dir, 0o500)
    assert stat.S_IMODE(os.stat(slot_dir).st_mode) == 0o500, "non-vacuity"
    assert slots._private_dir() == slot_dir
    assert stat.S_IMODE(os.stat(slot_dir).st_mode) == 0o700
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    os.rmdir(slot_dir)
    os.symlink(target, slot_dir)
    with pytest.raises(slots.SlotDirRefused):
        slots._private_dir()


def _claim_in_child(d, key, cwd) -> subprocess.Popen:
    """A separate process, started in ``cwd``, that takes a real claim for ``key`` and holds it."""
    code = textwrap.dedent(
        f"""
        import time
        from pathlib import Path
        from jcodemunch_mcp.storage import index_file_slots as slots
        slots.slot_dir = lambda: Path({str(d)!r})
        c, why = slots.claim({key!r})
        assert c is not None and c.lock_error is None, why
        print("held", flush=True)
        time.sleep(120)
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, cwd=str(cwd)
    )
    assert child.stdout.readline().strip() == "held"
    return child


def test_runs_started_in_different_folders_share_the_same_locks(
    slot_dir, tmp_path, monkeypatch
):
    """process_locks resolves a target as a path, so a relative target names a
    different lock in every working directory. The hook spawns without cwd=,
    so runs for one checkout start wherever the agent's shell was."""
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    same = _claim_in_child(slot_dir, "<default>|C:/repo", there)
    other = _claim_in_child(slot_dir, "<default>|C:/other", there)
    try:
        monkeypatch.chdir(here)
        assert slots.claim("<default>|C:/repo") == (None, "same_index_busy")
        assert slots.claim("<default>|C:/third") == (None, "busy")
    finally:
        for c in (same, other):
            c.kill()
            c.wait()


def test_a_queued_path_that_index_file_rejects_is_counted_as_failed(
    slot_dir, store, tmp_path, capsys
):
    """index_file reports a missing or unreadable file by returning success false, not by raising."""
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    gone = tmp_path / "repo" / "gone.py"
    key = slots.index_key(str(target), store)
    assert slots.queue(key, str(gone))

    def fake_index_file(path, **kwargs):
        if path == str(gone):
            return {"success": False, "error": "File not found"}
        return {"success": True}

    with mock.patch(
        "jcodemunch_mcp.tools.index_file.index_file", side_effect=fake_index_file
    ):
        server.main(["index-file", str(target)])
    out = json.loads(capsys.readouterr().out)
    assert out["coalesced_failures"] == 1 and "coalesced_paths" not in out


def test_an_unfinished_queue_write_is_neither_read_nor_removed(slot_dir):
    """A queued path is written under a temporary name and renamed into place,
    so a run taking the list mid-write sees nothing and deletes nothing."""
    key = "store|repo"
    assert slots.queue(key, "a.py")
    in_progress = slot_dir / f"{slots._pending_prefix(key)}{'9' * 20}_1_deadbeef.tmp"
    in_progress.write_text("b.py")
    assert slots.take_pending(key) == ["a.py"]
    assert in_progress.exists()
    in_progress.rename(in_progress.with_suffix(".path"))  # the writer finishes
    assert slots.take_pending(key) == ["b.py"]


def test_a_queued_entry_that_cannot_be_read_is_left_for_the_next_take(
    slot_dir, monkeypatch
):
    key = "store|repo"
    assert slots.queue(key, "a.py") and slots.queue(key, "b.py")
    real = slots._read_entry
    refused = []

    def read_entry(f):
        if not refused:
            refused.append(f.name)
            raise PermissionError(13, "denied")
        return real(f)

    monkeypatch.setattr(slots, "_read_entry", read_entry)
    assert slots.take_pending(key) == ["b.py"]
    monkeypatch.setattr(slots, "_read_entry", real)
    assert slots.take_pending(key) == ["a.py"], (
        "the unreadable entry was lost, not left"
    )


def test_entries_queued_within_one_clock_tick_keep_their_order(slot_dir, monkeypatch):
    """time.time_ns() repeats within a tick on Windows before Python 3.13; the
    per-process sequence keeps one process's entries in the order queued."""
    monkeypatch.setattr(slots.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    names = [f"f{i}.py" for i in range(12)]
    for n in names:
        assert slots.queue("store|repo", n)
    assert slots.take_pending("store|repo") == names


def test_an_entry_that_is_read_but_cannot_be_removed_is_not_returned(
    slot_dir, monkeypatch
):
    """Returned and left in place, it would be indexed again on every round."""
    key = "store|repo"
    assert slots.queue(key, "a.py")
    real = Path.unlink

    def unlink(self, *a, **k):
        if self.suffix == ".path":
            raise PermissionError(32, "being used by another process")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert slots.take_pending(key) == []
    monkeypatch.setattr(Path, "unlink", real)
    assert slots.take_pending(key) == ["a.py"]


def test_a_failed_queue_write_leaves_no_temporary_file(slot_dir, monkeypatch):
    def replace(src, dst):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(slots.os, "replace", replace)
    assert slots.queue("store|repo", "a.py") is False
    assert list(slot_dir.glob("_pending_*")) == []


def test_a_temporary_file_a_dead_writer_left_is_swept_by_the_next_take(slot_dir):
    key = "store|repo"
    assert slots.queue(key, "a.py")
    stale = slot_dir / f"{slots._pending_prefix(key)}{'1' * 20}_00000000_1_deadbeef.tmp"
    fresh = slot_dir / f"{slots._pending_prefix(key)}{'2' * 20}_00000000_1_cafecafe.tmp"
    stale.write_text("x.py")
    fresh.write_text("y.py")
    old = time.time() - slots.STALE_TMP_SECONDS - 5
    os.utime(stale, (old, old))
    assert slots.take_pending(key) == ["a.py"]
    assert not stale.exists() and fresh.exists()


def test_a_path_queued_after_the_live_run_released_is_indexed_by_the_queuer(
    slot_dir, store, tmp_path, capsys
):
    """The interleaving is pinned: the run is turned away while the live run
    holds the checkout, and the live run finishes and releases before the
    turned-away run's queue write appears. Write-then-recheck indexes it."""
    from jcodemunch_mcp import server

    target = _file(tmp_path)
    key = slots.index_key(str(target), store)
    child = _hold_in_child(slot_dir, slots.INDEX_SCOPE, _index_lock(key))
    real_queue = slots.queue
    done = []

    def queue_after_the_holder_left(k, path):
        child.kill()  # the live run releases between our claim and our write
        child.wait()
        return real_queue(k, path)

    def fake_index_file(path, **kwargs):
        done.append(path)
        return {"success": True}

    try:
        with (
            mock.patch.object(slots, "queue", side_effect=queue_after_the_holder_left),
            mock.patch(
                "jcodemunch_mcp.tools.index_file.index_file",
                side_effect=fake_index_file,
            ),
        ):
            server.main(["index-file", str(target)])
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert done == [str(target)], "the queued path was stranded"
    out = json.loads(capsys.readouterr().out)
    assert out["queued_for_live_run"] is True and out["coalesced_paths"] == 1
    assert out["indexed"] is True and out["success"] is True
    assert not slots.has_pending(key) and _locks_left(slot_dir) == []


def test_an_entry_that_is_not_text_is_left_and_never_stops_the_rest(slot_dir):
    key = "store|repo"
    assert slots.queue(key, "a.py")
    bad = slot_dir / f"{slots._pending_prefix(key)}{'0' * 20}_00000000_1_badbadba.path"
    bad.write_bytes(b"\xff\xfe\xfa not utf-8")
    assert slots.take_pending(key) == ["a.py"]
    assert bad.exists(), "an entry this module did not write is left, not deleted"
    assert slots.drain(key, lambda p: {"success": True}) == (0, 0)


def test_a_path_that_is_not_valid_utf8_is_queued_and_read_back_unchanged(slot_dir):
    """POSIX hands such a name over with lone surrogates (surrogateescape)."""
    odd = "src/caf\udce9.py"
    key = slots.index_key(odd, None)
    assert slots.queue(key, odd)
    assert slots.take_pending(key) == [odd]
    assert list(slot_dir.glob("_pending_*")) == []
