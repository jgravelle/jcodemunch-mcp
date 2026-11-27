"""#1581 (@Braxhim): a watch event under an ignored path schedules no re-index.

The watcher counted every added, modified or deleted event as relevant and
handed the batch to ``index_folder``, which skipped the ignored files only
after it had walked or classified the batch. A gitignored ``build/`` or
``data/`` folder rewritten all day therefore ran a pass per batch.
"""

import asyncio
import importlib

import pytest


def _skip_service_probe(monkeypatch):
    module = importlib.import_module("jcodemunch_mcp.tools.get_watch_status")
    monkeypatch.setattr(module, "service_status", lambda: {"active": None, "status": "not_probed"})


async def _run_one_batch(watcher, monkeypatch, root, storage, batch, extra_ignore_patterns=None):
    """Feed one batch to _watch_single; return the index_folder calls it made."""
    calls = []
    real_index = watcher.index_folder

    def index(**kwargs):
        calls.append(kwargs)
        return real_index(**kwargs)

    reached = asyncio.Event()

    async def events(*args, **kwargs):
        yield batch()
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, extra_ignore_patterns, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    waiter = asyncio.create_task(reached.wait())
    done, _ = await asyncio.wait({task, waiter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
    try:
        assert task not in done, task.exception()
        assert waiter in done, "the watcher never finished the batch"
    finally:
        task.cancel()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    return calls


def _repo(tmp_path):
    root = tmp_path / "repo"
    (root / "build").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "pkg").mkdir()
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    # A nested .gitignore: the chain from the root down is the rule, not the root's file alone.
    (root / "pkg" / ".gitignore").write_text("cache/\n", encoding="utf-8")
    (root / "pkg" / "cache").mkdir()
    (root / "code.py").write_text("def before(): pass\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("shape", ["gitignored_dir", "nested_gitignore", "extra_ignore_pattern"])
async def test_a_batch_of_ignored_events_runs_no_reindex(tmp_path, monkeypatch, shape):
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    storage = str(tmp_path / "store")
    extra = ["data/"] if shape == "extra_ignore_pattern" else None
    result = watcher.index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        extra_ignore_patterns=extra,
    )
    assert result["success"], result

    def batch():
        if shape == "gitignored_dir":
            out = root / "build" / "out.py"
            out.write_text("def generated(): pass\n", encoding="utf-8")
            (root / "build" / "sub").mkdir()
            return {(Change.added, str(out)), (Change.added, str(root / "build" / "sub")),
                    (Change.deleted, str(root / "build" / "old.py"))}
        if shape == "nested_gitignore":
            cached = root / "pkg" / "cache" / "c.py"
            cached.write_text("def cached(): pass\n", encoding="utf-8")
            return {(Change.modified, str(cached)), (Change.deleted, str(root / "pkg" / "cache" / "gone.py"))}
        db = root / "data" / "state.py"
        db.write_text("def state(): pass\n", encoding="utf-8")
        return {(Change.modified, str(db))}

    calls = await _run_one_batch(watcher, monkeypatch, root, storage, batch, extra_ignore_patterns=extra)
    assert calls == [], calls


async def test_a_mixed_batch_reindexes_only_the_kept_edit(tmp_path, monkeypatch):
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    storage = str(tmp_path / "store")
    result = watcher.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    code = root / "code.py"

    def batch():
        code.write_text("def after(): pass\n", encoding="utf-8")
        out = root / "build" / "out.py"
        out.write_text("def generated(): pass\n", encoding="utf-8")
        (root / "build" / "sub").mkdir()
        return {(Change.modified, str(code)), (Change.added, str(out)),
                (Change.added, str(root / "build" / "sub"))}

    calls = await _run_one_batch(watcher, monkeypatch, root, storage, batch)
    assert len(calls) == 1, calls
    # The ignored directory event would have made this a walk.
    assert calls[0].get("discover") is False, calls[0]
    named = repr(calls[0].get("changed_paths"))
    assert "code.py" in named and "build" not in named, named


async def test_an_edit_to_a_gitignore_is_never_dropped(tmp_path, monkeypatch):
    """The .gitignore decides what is ignored, so its own event always passes."""
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("build/\n.gitignore\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = watcher.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result

    def batch():
        (root / ".gitignore").write_text(".gitignore\n", encoding="utf-8")
        return {(Change.modified, str(root / ".gitignore"))}

    calls = await _run_one_batch(watcher, monkeypatch, root, storage, batch)
    assert len(calls) == 1, calls
