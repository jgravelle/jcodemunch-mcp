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
import subprocess
import sys
import textwrap
import time
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


def test_a_burst_of_real_runs_never_works_more_than_the_bound_at_once(slot_dir):
    """Real processes on different indexes, each holding its claim for a while."""
    code = textwrap.dedent(
        """
        import json, sys, time
        from pathlib import Path
        from jcodemunch_mcp.storage import index_file_slots as s
        s.slot_dir = lambda: Path(sys.argv[1])
        c, why = s.claim(sys.argv[2])
        if c is not None:
            time.sleep(5)
            s.release(c)
        print(json.dumps(c.slot if c else None))
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
    worked = [c for c in claimed if c is not None]
    assert 1 <= len(worked) <= slots.MAX_CONCURRENT
    assert len(set(worked)) == len(worked), "two live processes held one slot"


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
    assert "open to other users" in slots._check_private(
        _st(stat.S_IFDIR | 0o777), 1000
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


def test_our_own_slot_directory_open_to_others_is_tightened_not_refused(
    slot_dir, monkeypatch
):
    """The lock layer makes a missing folder with the default umask (0755), and
    on macOS and Linux a check that refused it turned the bound off (the
    2026-11-15 Mac run of this branch)."""
    import stat

    modes = {"m": stat.S_IFDIR | 0o755}
    chmods = []
    slot_dir.mkdir()
    monkeypatch.setattr(slots, "_owner_uid", lambda: 501)
    monkeypatch.setattr(slots.os, "lstat", lambda p: _St(modes["m"], 501))

    def chmod(p, mode):
        chmods.append(mode)
        modes["m"] = stat.S_IFDIR | mode

    monkeypatch.setattr(slots.os, "chmod", chmod)
    assert slots._private_dir() == slot_dir and chmods == [0o700]


def test_another_users_slot_directory_is_refused_and_left_alone(slot_dir, monkeypatch):
    import stat

    chmods = []
    slot_dir.mkdir()
    monkeypatch.setattr(slots, "_owner_uid", lambda: 501)
    monkeypatch.setattr(slots.os, "lstat", lambda p: _St(stat.S_IFDIR | 0o777, 0))
    monkeypatch.setattr(slots.os, "chmod", lambda p, m: chmods.append(m))
    with pytest.raises(slots.SlotDirRefused, match="owned by uid 0"):
        slots._private_dir()
    assert chmods == []


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


def test_a_pending_list_held_open_briefly_is_taken_after_a_retry(slot_dir, monkeypatch):
    key = "store|repo"
    assert slots.queue(key, "a.py")
    real = slots.os.replace
    refused = []

    def replace(src, dst):
        if str(src).endswith(".txt") and len(refused) < 2:
            refused.append(src)
            raise PermissionError(32, "being used by another process")
        return real(src, dst)

    monkeypatch.setattr(slots.os, "replace", replace)
    monkeypatch.setattr(slots, "TAKE_WAIT_SECONDS", 0)
    assert slots.take_pending(key) == ["a.py"] and len(refused) == 2


def test_a_pending_list_held_open_through_every_retry_is_left_in_place(
    slot_dir, monkeypatch
):
    key = "store|repo"
    assert slots.queue(key, "a.py")

    def replace(src, dst):
        raise PermissionError(32, "being used by another process")

    real = slots.os.replace
    monkeypatch.setattr(slots.os, "replace", replace)
    monkeypatch.setattr(slots, "TAKE_WAIT_SECONDS", 0)
    assert slots.take_pending(key) is None
    monkeypatch.setattr(slots.os, "replace", real)
    assert slots.take_pending(key) == ["a.py"], "the paths were lost, not left"
