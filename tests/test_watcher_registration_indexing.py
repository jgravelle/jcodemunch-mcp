"""Registration races must leave both the persisted index and future watches current."""

import asyncio
from contextlib import aclosing, closing
import json
import shutil
import sqlite3
import subprocess

import pytest

from jcodemunch_mcp import watcher


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
        # Assert persisted rows, not the full-index cache shared with the writer.
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
            return connection.execute("SELECT name, file FROM symbols ORDER BY name, file").fetchall()

    before = symbols()
    assert ("before_arm", target_rel) in before
    assert ("hidden_before", hidden_rel) in before
    native_awatch = watchfiles.awatch
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
                child.rename(root / "renamed")
            elif operation == "replace":
                child.rename(tmp_path / "moved_out")
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
        async def observe():
            while ((symbol, file) in symbols()) != present:
                if task.done():
                    task.result()
                    pytest.fail("Watcher stopped before updating the index")
                await asyncio.sleep(0.05)
        try:
            await asyncio.wait_for(observe(), 10)
        except asyncio.TimeoutError as error:
            raise AssertionError(
                f"Timed out waiting for {(symbol, file)} present={present}; "
                f"persisted={symbols()}; arms={attempts}"
            ) from error
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

        if polling:
            # notify's poller compares whole-second mtimes for existing files.
            await asyncio.sleep(1.1)
        target.write_text("def after_recovery(): pass\n")
        subsequent = await wait_for_symbol("after_recovery")
        assert ("during_arm", target_rel) not in subsequent
        if operation == "new_tree":
            (child / "new" / "nested" / "late.py").write_text("def late_after(): pass\n")
            await wait_for_symbol("late_after", "child/new/nested/late.py")
        elif operation == "hidden":
            hidden.write_text("def hidden_after(): pass\n")
            await wait_for_symbol("hidden_after", ".github/hook.py")
        elif operation in ("move_out", "move_out_all"):
            child.rename(tmp_path / "moved_out")
            remaining = await wait_for_symbol("nested_symbol", "child/nested.py", present=False)
            assert ("deep_symbol", "child/deep/code.py") not in remaining
            if operation == "move_out_all":
                assert remaining == []
            else:
                assert ("after_recovery", "code.py") in remaining
                assert ("hidden_before", ".github/hook.py") in remaining
        elif operation == "root_replace":
            root.rename(tmp_path / "old_root")
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            target.write_text("def root_replaced(): pass\n")
            await wait_for_symbol("root_replaced")
            if polling:
                await asyncio.sleep(1.1)
            target.write_text("def root_still_watched(): pass\n")
            await wait_for_symbol("root_still_watched")
        elif operation == "live_tree":
            late = child / "new" / "deep" / "late.py"
            late.parent.mkdir(parents=True)
            late.write_text("def live_before(): pass\n")
            await wait_for_symbol("live_before", "child/new/deep/late.py")
            if polling:
                await asyncio.sleep(1.1)
            late.write_text("def live_after(): pass\n")
            await wait_for_symbol("live_after", "child/new/deep/late.py")
            (child / "new").rename(root / "moved")
            moved = await wait_for_symbol("live_after", "moved/deep/late.py")
            assert ("live_after", "child/new/deep/late.py") not in moved
            (root / "moved").rename(tmp_path / "moved_out")
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
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
            return connection.execute("SELECT name, file FROM symbols ORDER BY name, file").fetchall()

    batches: asyncio.Queue = asyncio.Queue()

    async def injected(*paths, **kwargs):
        while True:
            yield await batches.get()

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

        (root / "child").rename(tmp_path / "moved_out")
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
