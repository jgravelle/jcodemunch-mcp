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
@pytest.mark.parametrize("operation", ["initial", "delete", "rename", "new_tree", "replace", "hidden", "live_tree", "root_replace"])
async def test_registration_race_updates_persisted_symbols(tmp_path, monkeypatch, operation, polling):
    watchfiles = pytest.importorskip("watchfiles")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true" if polling else "false")
    root = tmp_path / "project"
    child = root / "child"
    child.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    target = root / "code.py"
    target.write_text("def before_arm(): pass\n")
    (child / "nested.py").write_text("def nested_symbol(): pass\n")
    hidden = root / ".github" / "hook.py"
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
    assert ("before_arm", "code.py") in before
    assert ("hidden_before", ".github/hook.py") in before
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

    async def wait_for_symbol(symbol, file="code.py", present=True):
        async def observe():
            while ((symbol, file) in symbols()) != present:
                if task.done():
                    task.result()
                    pytest.fail("Watcher stopped before updating the index")
                await asyncio.sleep(0.05)
        try:
            await asyncio.wait_for(observe(), 10)
        except asyncio.TimeoutError:
            print({"missing": (symbol, file), "symbols": symbols(), "arms": attempts})
            raise
        return symbols()

    try:
        reconciled = await wait_for_symbol("during_arm")
        assert ("before_arm", "code.py") not in reconciled
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
        assert ("during_arm", "code.py") not in subsequent
        if operation == "new_tree":
            (child / "new" / "nested" / "late.py").write_text("def late_after(): pass\n")
            await wait_for_symbol("late_after", "child/new/nested/late.py")
        elif operation == "hidden":
            hidden.write_text("def hidden_after(): pass\n")
            await wait_for_symbol("hidden_after", ".github/hook.py")
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
        "all_native_streams_closed": True,
    }, sort_keys=True))
