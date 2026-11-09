"""Registration races must leave both the persisted index and future watches current."""

import asyncio
from contextlib import aclosing, closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

import pytest

from jcodemunch_mcp import watcher


def _persisted_symbols(database):
    """Persisted rows, not the full-index cache shared with the writer thread.

    The writer commits from a thread while this polls; on Windows the reader
    can see a transient lock the busy timeout does not absorb, so retry it.
    """
    for attempt in range(40):
        try:
            with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
                return connection.execute("SELECT name, file FROM symbols ORDER BY name, file").fetchall()
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) or attempt == 39:
                raise
            time.sleep(0.05)


def rewrite_past_poller_granularity(path, text):
    """Rewrite *path* so a whole-second-mtime poller is obliged to see it.

    notify's poller compares WHOLE-SECOND mtimes for a file it already knows,
    so a rewrite landing in the same second as the previous one is invisible to
    it -- the content changed and the second did not.

    This used to be ``if polling: await asyncio.sleep(1.1)`` before each such
    rewrite, which is a barrier made of wall-clock: it assumes 1.1 s of sleep
    buys a second boundary against a poller whose interval this project raises
    to 1000 ms (``JCODEMUNCH_WATCH_POLL_DELAY_MS``), leaving ~100 ms of margin
    on a loaded Windows runner. It held on this box and failed four times in
    three days on CI (FINDINGS F-26, F-28, F-19's neighbours), across three
    different tests, because a sleep cannot pin an ordering it only outlasts.

    Stamping the mtime removes the timing question entirely: the new mtime is
    at least two whole seconds past the old one whatever the machine was doing,
    so the comparison the poller makes has exactly one answer. Applied on BOTH
    backends deliberately -- a native watch fires on the write and does not care,
    and two arms that differ only in what they wait for are two code paths.
    """
    previous = path.stat().st_mtime if path.exists() else 0.0
    path.write_text(text)
    stamp = max(time.time(), previous + 2.0)
    os.utime(path, (stamp, stamp))


def test_rewrite_past_poller_granularity_crosses_a_whole_second(tmp_path):
    """The helper's whole point, pinned so it cannot decay back into a write.

    The property is NOT "the file changed" -- it is that the mtime lands in a
    LATER WHOLE SECOND, because that is the comparison notify's poller makes.
    Replacing the helper body with a plain ``path.write_text(text)`` turns this
    red on every machine, which is the guarantee the sleep it replaced never
    had: a sleep that happened to be long enough leaves no evidence when it is
    not.
    """
    path = tmp_path / "code.py"
    path.write_text("def before(): pass\n")
    before = path.stat().st_mtime
    rewrite_past_poller_granularity(path, "def after(): pass\n")
    after = path.stat().st_mtime
    assert path.read_text() == "def after(): pass\n"
    assert after >= before + 2.0
    assert int(after) > int(before)


def rename_against_scan_collision(source, destination, attempts=40, delay=0.05, _rename=None):
    """Rename a directory the polling watcher may be mid-scan over.

    Windows refuses to rename a directory while a handle is open on it, and the
    POLLING backend re-walks the tree with ``os.scandir`` on every interval, so
    a rename issued inside a scan window raises ``PermissionError`` WinError 5.
    Transient BY CONSTRUCTION: the scan holding the handle finishes in
    milliseconds, and it is not what any test in this file is about -- every one
    of them asserts what the INDEX does after a move, never whether Windows
    permitted the move on the first try.

    ⚠⚠ **This is a retry, which is the shape of papering over a defect, so the
    bound is deliberate and the exit is narrow.** Only WinError 5 is retried;
    every other ``PermissionError`` (a sharing violation, a real lock, a
    read-only tree) is re-raised on the first attempt. A collision outliving the
    ~2 s bound is not a scan window either, and re-raises. So a genuine lock
    still fails the test, which is the property that separates this from a
    sleep-until-it-works.

    ⚠ On Linux and macOS ``PermissionError`` carries no ``winerror``, so this is
    a straight passthrough there and those platforms retry nothing.

    Seen on windows 3.11 (2026-09-11, run 34644461374) and windows 3.10
    (2026-09-13, run 34730611506), both on the polling arm.
    """
    rename = _rename if _rename is not None else Path.rename
    for attempt in range(attempts):
        try:
            return rename(source, destination)
        except PermissionError as error:
            if getattr(error, "winerror", None) != 5 or attempt == attempts - 1:
                raise
            time.sleep(delay)


def _scan_collision(winerror=5):
    error = PermissionError("Access is denied")
    error.winerror = winerror
    return error


def test_rename_against_scan_collision_retries_a_scan_window():
    """One WinError 5, then success: the case the CI failures were."""
    calls = []

    def flaky(source, destination):
        calls.append((source, destination))
        if len(calls) == 1:
            raise _scan_collision()
        return "renamed"

    assert rename_against_scan_collision("a", "b", delay=0, _rename=flaky) == "renamed"
    assert calls == [("a", "b"), ("a", "b")]


def test_rename_against_scan_collision_reraises_anything_else_immediately():
    """A sharing violation is not a scan window and must not be retried.

    This is the assertion that stops the helper from becoming a
    sleep-until-it-works: a real lock has to fail on attempt one.
    """
    calls = []

    def denied(source, destination):
        calls.append(1)
        raise _scan_collision(winerror=32)

    with pytest.raises(PermissionError):
        rename_against_scan_collision("a", "b", delay=0, _rename=denied)
    assert len(calls) == 1


def test_rename_against_scan_collision_gives_up_rather_than_hiding_a_lock():
    """A collision that outlives the bound is re-raised, not swallowed."""
    calls = []

    def always(source, destination):
        calls.append(1)
        raise _scan_collision()

    with pytest.raises(PermissionError):
        rename_against_scan_collision("a", "b", attempts=3, delay=0, _rename=always)
    assert len(calls) == 3


class _WatchTrace:
    """What the watcher saw and did, for a timeout message (#866).

    A timeout used to report only the persisted rows, which cannot tell "the
    event never arrived" from "the reindex ran and wrote nothing". This records
    every change batch the watcher's `awatch` stream yielded (empty ones are
    counted: `yield_on_timeout` makes the stream yield about once a second, so
    they show the loop was alive), and every `index_folder` call's start and
    end, so a reindex still running at the timeout has no `end`. A call's
    scalar results are kept, `fast_path` and `message` included: two different
    no-change exits both return `changed=0` and only those name which fired.
    """

    def __init__(self, root):
        self.root = str(root)
        self.t0 = time.monotonic()
        self.batches: list = []
        self.empty = 0
        self.last_empty = None
        self.calls: list = []

    def _now(self):
        return round(time.monotonic() - self.t0, 2)

    def _rel(self, path):
        text = str(path)
        return text[len(self.root):].lstrip("\\/").replace("\\", "/") if text.startswith(self.root) else text

    def _change(self, change):
        # The watcher passes `WatcherChange(change_type, path, old_hash)`; a
        # bare path is rendered as itself.
        path = getattr(change, "path", change)
        kind = getattr(change, "change_type", None)
        old = getattr(change, "old_hash", None)
        return ":".join(str(x) for x in (kind, self._rel(path), old) if x is not None)

    def record_batch(self, changes):
        if not changes:
            self.empty += 1
            self.last_empty = self._now()
            return
        self.batches.append((self._now(), sorted(f"{c.name}:{self._rel(p)}" for c, p in changes)))

    def wrap_awatch(self, awatch):
        async def traced(*paths, **kwargs):
            async with aclosing(awatch(*paths, **kwargs)) as stream:
                async for changes in stream:
                    self.record_batch(changes)
                    yield changes
        return traced

    def wrap_index_folder(self, index_folder):
        def traced(**kwargs):
            changed = kwargs.get("changed_paths")
            entry = {
                "start": self._now(),
                "changed_paths": None if changed is None else sorted(self._change(c) for c in changed)[:8],
            }
            self.calls.append(entry)
            try:
                result = index_folder(**kwargs)
            except BaseException as error:
                entry["end"], entry["raised"] = self._now(), type(error).__name__
                raise
            entry["end"] = self._now()
            if isinstance(result, dict):
                entry["result"] = {
                    k: (v[:80] if isinstance(v, str) else v)
                    for k, v in result.items()
                    if v is None or isinstance(v, (bool, int, float, str))
                }
            return result
        return traced

    def last_progress(self):
        """When the watcher last did something observable, or None.

        A non-empty change batch and an index_folder start or end count. An
        empty batch only shows the loop is alive, so it doesn't.
        """
        marks = [t for t, _ in list(self.batches)]
        for call in [dict(c) for c in list(self.calls)]:
            marks.append(call["start"])
            if "end" in call:
                marks.append(call["end"])
        return max(marks) if marks else None

    def busy(self):
        """True while an index_folder call has started and not returned."""
        return any("end" not in dict(c) for c in list(self.calls))

    def describe(self, path=None):
        # Copies first: an index_folder call may be writing its entry from the
        # watcher's worker thread while this runs on the loop thread.
        batches, calls = list(self.batches[-8:]), [dict(c) for c in list(self.calls[-8:])]
        lines = [f"trace at {self._now()}s: {len(self.batches)} change batch(es), "
                 f"{self.empty} empty (last at {self.last_empty}s), {len(self.calls)} index_folder call(s)"]
        if path is not None:
            try:
                st = os.stat(path)
                head = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[:1]
                lines.append(f"on disk {self._rel(path)}: mtime_ns={st.st_mtime_ns} first_line={head}")
            except OSError as error:
                lines.append(f"on disk {self._rel(path)}: {type(error).__name__}")
        lines += [f"  batch @{t}s {changes[:8]}" for t, changes in batches]
        lines += [f"  index_folder {call}" for call in calls]
        return "\n".join(lines)


WAIT_QUIET_SECONDS = 10.0
WAIT_CAP_SECONDS = 120.0


async def _wait_until(done, trace, task, *, quiet=WAIT_QUIET_SECONDS, cap=WAIT_CAP_SECONDS, poll=0.05):
    """Wait for `done()`, failing only when the watcher has stopped making progress.

    A fixed 10 s deadline failed on Windows runners while the watcher was
    mid-reindex (L-307): one index_folder call on a four-file tree took 7 to
    11 s there. This waits while an index_folder call is in flight, and gives
    up `quiet` seconds after the watcher's last change batch or index_folder
    start or end (or after the wait began, if later), so a missed event still
    fails after the same 10 s. `cap` bounds the whole wait. Returns None when
    `done()` holds, or the reason the wait gave up.
    """
    start = trace._now()
    while not done():
        if task is not None and task.done():
            task.result()
            pytest.fail("Watcher stopped before updating the index")
        now = trace._now()
        if now - start > cap:
            reason = f"no result within the {cap:g} s cap"
        elif not trace.busy() and now - max(start, trace.last_progress() or start) > quiet:
            reason = f"no watcher progress for {quiet:g} s"
        else:
            await asyncio.sleep(poll)
            continue
        # A write that lands as the window closes is a pass, not a miss.
        return None if done() else reason
    return None


def _run_wait(done, trace, **kwargs):
    # The trace clock rounds to 0.01 s, so the elapsed-time bounds below carry
    # 0.05 s of slack; each mutant they guard against misses by 0.25 s or more.
    async def run():
        t0 = time.monotonic()
        reason = await _wait_until(done, trace, None, **kwargs)
        return reason, time.monotonic() - t0
    return asyncio.run(run())


def test_wait_keeps_going_while_a_reindex_is_in_flight(tmp_path):
    """L-307: an index_folder slower than the quiet window is not a miss.

    On the Windows runners one index_folder call on a four-file tree took 7 to
    11 s, so a fixed 10 s deadline failed with the edit observed and the reindex
    still running. The wait counts a call in flight as progress.
    """
    trace = _WatchTrace(tmp_path)
    call = {"start": trace._now()}
    trace.calls.append(call)
    finished = []

    def done():
        if trace._now() >= 0.6:
            call["end"] = trace._now()
            finished.append(True)
        return bool(finished)

    reason, elapsed = _run_wait(done, trace, quiet=0.2, cap=5)
    assert reason is None
    assert elapsed >= 0.55


def test_wait_fails_after_the_quiet_window_when_nothing_happens(tmp_path):
    """A missed event still fails: no batch and no reindex since the wait began."""
    trace = _WatchTrace(tmp_path)
    reason, elapsed = _run_wait(lambda: False, trace, quiet=0.3, cap=5)
    assert reason is not None and "no watcher progress" in reason
    assert 0.25 <= elapsed < 2


def test_wait_counts_the_quiet_window_from_the_last_reindex_end(tmp_path):
    """A reindex that ends without the symbol fails one quiet window later."""
    trace = _WatchTrace(tmp_path)
    call = {"start": trace._now()}
    trace.calls.append(call)

    def done():
        if "end" not in call and trace._now() >= 0.5:
            call["end"] = trace._now()
        return False

    reason, elapsed = _run_wait(done, trace, quiet=0.3, cap=5)
    assert reason is not None and "no watcher progress" in reason
    assert elapsed >= 0.75


def test_wait_counts_a_change_batch_as_progress(tmp_path):
    """A non-empty batch restarts the quiet window; an empty one doesn't."""
    from collections import namedtuple

    trace = _WatchTrace(tmp_path)
    modified = namedtuple("Change", "name")("modified")
    fed = []

    def done():
        now = trace._now()
        if not fed and now >= 0.2:
            trace.record_batch({(modified, str(tmp_path / "code.py"))})
            fed.append(now)
        trace.record_batch(set())  # the loop is alive, which is not progress
        return False

    reason, elapsed = _run_wait(done, trace, quiet=0.3, cap=5)
    assert reason is not None and "no watcher progress" in reason
    assert 0.45 <= elapsed < 2


def test_wait_stops_at_the_cap_while_a_reindex_never_ends(tmp_path):
    trace = _WatchTrace(tmp_path)
    trace.calls.append({"start": trace._now()})
    reason, elapsed = _run_wait(lambda: False, trace, quiet=0.1, cap=0.5)
    assert reason is not None and "cap" in reason
    assert 0.45 <= elapsed < 2


def test_watch_trace_names_what_the_watcher_saw_and_did(tmp_path):
    """The timeout message must be able to tell a missed event from an empty reindex."""
    from collections import namedtuple

    root = tmp_path / "project"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def seen(): pass\n", encoding="utf-8")
    trace = _WatchTrace(root)
    modified = namedtuple("Change", "name")("modified")
    inner_closed = []

    async def fake_awatch(*paths, **kwargs):
        try:
            yield set()
            yield {(modified, str(code))}
            yield {(modified, str(code))}
        finally:
            inner_closed.append(True)

    async def take_two():
        stream = trace.wrap_awatch(fake_awatch)(str(root))
        got = [await stream.__anext__(), await stream.__anext__()]
        await stream.aclose()
        # Checked here, not after asyncio.run: its shutdown finalises every
        # open async generator and would close the inner stream anyway.
        closed_at_aclose = list(inner_closed)
        return got, closed_at_aclose

    got, closed_at_aclose = asyncio.run(take_two())
    assert len(got) == 2
    assert closed_at_aclose == [True], "closing the traced stream must close the watcher's stream"

    # The producer's shape: `WatcherChange(change_type, path, old_hash)`.
    WatcherChange = namedtuple("WatcherChange", "change_type path old_hash")
    seen_while_running = []

    def fast_exit(**kw):
        seen_while_running.append(trace.describe())
        return {"success": True, "changed": 0, "fast_path": True, "message": "No changes detected", "files": [1]}

    trace.wrap_index_folder(fast_exit)(
        path=str(root), changed_paths=[WatcherChange("modified", str(code), "abc")]
    )
    assert "'end'" not in seen_while_running[0], "a call still running must show no end"

    def boom(**kw):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        trace.wrap_index_folder(boom)(path=str(root), changed_paths=None)

    text = trace.describe(code)
    assert "1 change batch(es), 1 empty" in text
    assert "modified:code.py" in text
    assert "'changed_paths': ['modified:code.py:abc']" in text
    assert "'fast_path': True" in text and "'message': 'No changes detected'" in text
    assert "'files'" not in text
    finished = next(line for line in text.splitlines() if "'fast_path': True" in line)
    assert "'end':" in finished, "a finished call must show its end"
    assert "'raised': 'RuntimeError'" in text
    assert "2 index_folder call(s)" in text
    assert "on disk code.py: mtime_ns=" in text and "first_line=['def seen(): pass']" in text

    code.unlink()
    assert "on disk code.py: FileNotFoundError" in trace.describe(code)


@pytest.mark.asyncio
@pytest.mark.parametrize("polling", [False, True], ids=["native", "polling"])
@pytest.mark.parametrize("operation", ["initial", "delete", "rename", "new_tree", "replace", "hidden", "live_tree", "root_replace", "move_out", "move_out_all"])
async def test_registration_race_updates_persisted_symbols(tmp_path, monkeypatch, operation, polling):
    watchfiles = pytest.importorskip("watchfiles")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true" if polling else "false")
    root = tmp_path / "project"
    child = root / "child"
    child.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    source_root = child if operation == "move_out_all" else root
    target = source_root / "code.py"
    target_rel = target.relative_to(root).as_posix()
    target.write_text("def before_arm(): pass\n")
    (child / "nested.py").write_text("def nested_symbol(): pass\n")
    if operation in ("move_out", "move_out_all"):
        (child / "deep").mkdir()
        (child / "deep" / "code.py").write_text("def deep_symbol(): pass\n")
    hidden = source_root / ".github" / "hook.py"
    hidden_rel = hidden.relative_to(root).as_posix()
    hidden.parent.mkdir()
    hidden.write_text("def hidden_before(): pass\n")
    storage = str(tmp_path / "index")
    result = watcher.index_folder(
        path=str(root), storage_path=storage, use_ai_summaries=False,
        context_providers=False,
    )
    assert result["success"]
    store = watcher.IndexStore(base_path=storage)
    owner, name = result["repo"].split("/", 1)
    database = store.load_index(owner, name)._db_path

    def symbols():
        return _persisted_symbols(database)

    before = symbols()
    assert ("before_arm", target_rel) in before
    assert ("hidden_before", hidden_rel) in before
    trace = _WatchTrace(root)
    native_awatch = trace.wrap_awatch(watchfiles.awatch)
    monkeypatch.setattr(watcher, "index_folder", trace.wrap_index_folder(watcher.index_folder))
    attempts, closed = [], []

    async def race(*paths, **kwargs):
        attempt = len(attempts)
        if attempt:
            assert attempt - 1 in closed
        attempts.append(sorted(str_path.removeprefix(str(root)) or "." for str_path in paths))
        if attempt == 0:
            # Exactly one edit after indexing/enumeration, before native registration.
            target.write_text("def during_arm(): pass\n")
            if operation == "delete":
                shutil.rmtree(child)
            elif operation == "rename":
                rename_against_scan_collision(child, root / "renamed")
            elif operation == "replace":
                rename_against_scan_collision(child, tmp_path / "moved_out")
                child.mkdir()
                (child / "nested.py").write_text("def replacement(): pass\n")
            elif operation == "new_tree":
                (child / "new" / "nested").mkdir(parents=True)
                (child / "new" / "nested" / "late.py").write_text("def late_before(): pass\n")
        try:
            async with aclosing(native_awatch(*paths, **kwargs)) as stream:
                async for changes in stream:
                    yield changes
        finally:
            closed.append(attempt)

    monkeypatch.setattr(watchfiles, "awatch", race)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))

    async def wait_for_symbol(symbol, file=target_rel, present=True):
        reason = await _wait_until(lambda: ((symbol, file) in symbols()) == present, trace, task)
        if reason:
            raise AssertionError(
                f"Timed out ({reason}) waiting for {(symbol, file)} present={present}; "
                f"persisted={symbols()}; arms={attempts}\n{trace.describe(root / file)}"
            )
        return symbols()

    try:
        reconciled = await wait_for_symbol("during_arm")
        assert ("before_arm", target_rel) not in reconciled
        if operation == "delete":
            assert not any(symbol == "nested_symbol" for symbol, _ in reconciled)
        elif operation == "rename":
            assert ("nested_symbol", "renamed/nested.py") in reconciled
            assert ("nested_symbol", "child/nested.py") not in reconciled
        elif operation == "replace":
            assert ("replacement", "child/nested.py") in reconciled
            assert ("nested_symbol", "child/nested.py") not in reconciled
        elif operation == "new_tree":
            assert ("late_before", "child/new/nested/late.py") in reconciled

        rewrite_past_poller_granularity(target, "def after_recovery(): pass\n")
        subsequent = await wait_for_symbol("after_recovery")
        assert ("during_arm", target_rel) not in subsequent
        if operation == "new_tree":
            (child / "new" / "nested" / "late.py").write_text("def late_after(): pass\n")
            await wait_for_symbol("late_after", "child/new/nested/late.py")
        elif operation == "hidden":
            hidden.write_text("def hidden_after(): pass\n")
            await wait_for_symbol("hidden_after", ".github/hook.py")
        elif operation in ("move_out", "move_out_all"):
            rename_against_scan_collision(child, tmp_path / "moved_out")
            remaining = await wait_for_symbol("nested_symbol", "child/nested.py", present=False)
            assert ("deep_symbol", "child/deep/code.py") not in remaining
            if operation == "move_out_all":
                assert remaining == []
            else:
                assert ("after_recovery", "code.py") in remaining
                assert ("hidden_before", ".github/hook.py") in remaining
        elif operation == "root_replace":
            rename_against_scan_collision(root, tmp_path / "old_root")
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            target.write_text("def root_replaced(): pass\n")
            await wait_for_symbol("root_replaced")
            rewrite_past_poller_granularity(target, "def root_still_watched(): pass\n")
            await wait_for_symbol("root_still_watched")
        elif operation == "live_tree":
            late = child / "new" / "deep" / "late.py"
            late.parent.mkdir(parents=True)
            late.write_text("def live_before(): pass\n")
            await wait_for_symbol("live_before", "child/new/deep/late.py")
            rewrite_past_poller_granularity(late, "def live_after(): pass\n")
            await wait_for_symbol("live_after", "child/new/deep/late.py")
            rename_against_scan_collision((child / "new"), root / "moved")
            moved = await wait_for_symbol("live_after", "moved/deep/late.py")
            assert ("live_after", "child/new/deep/late.py") not in moved
            rename_against_scan_collision((root / "moved"), tmp_path / "moved_out")
            (root / "moved" / "deep").mkdir(parents=True)
            (root / "moved" / "deep" / "late.py").write_text("def replaced_live(): pass\n")
            await wait_for_symbol("replaced_live", "moved/deep/late.py")
            shutil.rmtree(root / "moved")
            await wait_for_symbol("replaced_live", "moved/deep/late.py", present=False)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert sorted(closed) == list(range(len(attempts)))
    print(json.dumps({
        "scenario": operation, "polling": polling, "native_registration_sets": attempts,
        "persisted_symbols_before": before,
        "persisted_symbols_after_registration": reconciled,
        "persisted_symbols_after_one_subsequent_edit": subsequent,
        "persisted_symbols_final": symbols(),
        "all_native_streams_closed": True,
    }, sort_keys=True))


@pytest.mark.asyncio
async def test_unknown_deletion_burst_keeps_fast_path_until_an_indexed_tree_is_gone(tmp_path, monkeypatch):
    watchfiles = pytest.importorskip("watchfiles")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "false")
    root = tmp_path / "project"
    (root / "child" / "deep").mkdir(parents=True)
    (root / "childish").mkdir()
    (root / "code.py").write_text("def root_symbol(): pass\n")
    (root / "child" / "nested.py").write_text("def nested_symbol(): pass\n")
    (root / "child" / "deep" / "code.py").write_text("def deep_symbol(): pass\n")
    (root / "childish" / "other.py").write_text("def boundary_symbol(): pass\n")
    storage = str(tmp_path / "index")
    result = watcher.index_folder(
        path=str(root), storage_path=storage, use_ai_summaries=False,
        context_providers=False, identity_mode="local",
    )
    assert result["success"]
    store = watcher.IndexStore(base_path=storage)
    owner, name = result["repo"].split("/", 1)
    database = store.load_index(owner, name)._db_path

    def symbols():
        return _persisted_symbols(database)

    batches: asyncio.Queue = asyncio.Queue()

    async def injected(*paths, rust_timeout=None, yield_on_timeout=False, **kwargs):
        while True:
            try:
                yield await asyncio.wait_for(batches.get(), rust_timeout / 1000)
            except asyncio.TimeoutError:
                if yield_on_timeout:
                    yield set()

    calls: list = []
    real_index_folder = watcher.index_folder

    def recording_index_folder(**kwargs):
        result = real_index_folder(**kwargs)
        calls.append(kwargs["changed_paths"])
        return result

    monkeypatch.setattr(watchfiles, "awatch", injected)
    monkeypatch.setattr(watcher, "index_folder", recording_index_folder)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))

    async def wait_for_calls(count):
        async def observe():
            while len(calls) < count:
                if task.done():
                    task.result()
                    pytest.fail("Watcher stopped before indexing")
                await asyncio.sleep(0.05)
        await asyncio.wait_for(observe(), 30)

    deleted = watchfiles.Change.deleted
    try:
        await batches.put(set())
        await wait_for_calls(1)
        assert calls[0] is None  # root reconciliation after registration

        burst = {(deleted, str(root / "target" / f"{n}.o")) for n in range(20000)}
        burst |= {(deleted, str(root / "chil")), (deleted, str(root / "childis")),
                  (deleted, str(root / "child" / "deep" / "missing.o"))}
        started = asyncio.get_running_loop().time()
        await batches.put(burst)
        await wait_for_calls(2)
        elapsed = asyncio.get_running_loop().time() - started
        assert isinstance(calls[1], list) and len(calls[1]) == len(burst)
        assert len(symbols()) == 4

        rename_against_scan_collision((root / "child"), tmp_path / "moved_out")
        await batches.put({(deleted, str(root / "child"))})
        await wait_for_calls(3)
        assert calls[2] is None
        remaining = symbols()
        assert ("nested_symbol", "child/nested.py") not in remaining
        assert ("deep_symbol", "child/deep/code.py") not in remaining
        assert ("boundary_symbol", "childish/other.py") in remaining
        assert ("root_symbol", "code.py") in remaining
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    print(json.dumps({"burst_events": len(burst), "burst_seconds": round(elapsed, 3),
                      "changed_paths_per_call": [None if c is None else len(c) for c in calls]}))


@pytest.mark.asyncio
@pytest.mark.parametrize("polling", [False, True], ids=["native", "polling"])
@pytest.mark.parametrize("layout", ["git_subdir", "git_root", "local"])
async def test_subdirectory_watch_reconciles_against_index_root_keys(tmp_path, monkeypatch, layout, polling):
    pytest.importorskip("watchfiles")
    import watchfiles

    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true" if polling else "false")
    root = tmp_path / "repo"
    foo = root / "packages" / "foo"
    (foo / "child" / "deep").mkdir(parents=True)
    (root / "packages" / "bar").mkdir()
    if layout != "local":
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    target = foo / "code.py"
    target.write_text("def before_watch(): pass\n")
    (foo / "child" / "nested.py").write_text("def nested_symbol(): pass\n")
    (foo / "child" / "deep" / "code.py").write_text("def deep_symbol(): pass\n")
    (root / "packages" / "bar" / "lib.py").write_text("def sibling_symbol(): pass\n")
    storage = str(tmp_path / "index")
    result = watcher.index_folder(
        path=str(root), storage_path=storage, use_ai_summaries=False, context_providers=False,
    )
    assert result["success"]
    owner, name = result["repo"].split("/", 1)
    database = watcher.IndexStore(base_path=storage).load_index(owner, name)._db_path
    watched = foo if layout == "git_subdir" else root

    def symbols():
        return _persisted_symbols(database)

    assert ("before_watch", "packages/foo/code.py") in symbols()
    trace = _WatchTrace(root)
    monkeypatch.setattr(watchfiles, "awatch", trace.wrap_awatch(watchfiles.awatch))
    monkeypatch.setattr(watcher, "index_folder", trace.wrap_index_folder(watcher.index_folder))
    task = asyncio.create_task(watcher._watch_single(
        str(watched), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))

    async def wait_for(symbol, file, present=True):
        reason = await _wait_until(lambda: ((symbol, file) in symbols()) == present, trace, task)
        if reason:
            raise AssertionError(
                f"Timed out ({reason}) waiting for {(symbol, file)} present={present}; "
                f"persisted={symbols()}\n{trace.describe(root / file)}"
            )
        return symbols()

    try:
        target.write_text("def ready(): pass\n")
        await wait_for("ready", "packages/foo/code.py")
        rename_against_scan_collision((foo / "child"), tmp_path / "moved_out")
        remaining = await wait_for("nested_symbol", "packages/foo/child/nested.py", present=False)
        assert ("deep_symbol", "packages/foo/child/deep/code.py") not in remaining
        assert ("sibling_symbol", "packages/bar/lib.py") in remaining
        assert ("ready", "packages/foo/code.py") in remaining
        rewrite_past_poller_granularity(target, "def after_move(): pass\n")
        edited = await wait_for("after_move", "packages/foo/code.py")
        assert ("ready", "packages/foo/code.py") not in edited
        target.unlink()
        final = await wait_for("after_move", "packages/foo/code.py", present=False)
        assert ("sibling_symbol", "packages/bar/lib.py") in final
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    print(json.dumps({"layout": layout, "polling": polling, "watched_is_index_root": watched == root,
                      "persisted_symbols_final": symbols()}))
