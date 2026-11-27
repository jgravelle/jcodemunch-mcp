"""#1581 (@Braxhim): a watch event under an ignored path schedules no re-index.

The watcher counted every added, modified or deleted event as relevant and
handed the batch to ``index_folder``, which skipped the ignored files only
after it had walked or classified the batch. A gitignored ``build/`` or
``data/`` folder rewritten all day therefore ran a pass per batch.

A wrong drop leaves an edit unindexed, so the tests below also pin where the
watcher must NOT drop: deletions, a .gitignore edit, rules a subfolder watch
cannot be sure of, a rule removed since the last batch, and a due retry.
"""

import asyncio
import importlib
from types import SimpleNamespace

import pytest


def _skip_service_probe(monkeypatch):
    module = importlib.import_module("jcodemunch_mcp.tools.get_watch_status")
    monkeypatch.setattr(module, "service_status", lambda: {"active": None, "status": "not_probed"})


async def _run_batches(watcher, monkeypatch, root, storage, batches, extra_ignore_patterns=None, index=None):
    """Feed ``batches`` to _watch_single; return the index_folder calls it made."""
    calls = []
    inner = index or watcher.index_folder

    def recording(**kwargs):
        calls.append(kwargs)
        return inner(**kwargs)

    reached = asyncio.Event()

    async def events(*args, **kwargs):
        for batch in batches:
            yield batch()
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(watcher, "index_folder", recording)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, extra_ignore_patterns, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    waiter = asyncio.create_task(reached.wait())
    done, _ = await asyncio.wait({task, waiter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
    try:
        assert task not in done, task.exception()
        assert waiter in done, "the watcher never finished the batches"
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


def _index(watcher, root, storage, extra=None):
    result = watcher.index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        extra_ignore_patterns=extra,
    )
    assert result["success"], result
    return result


@pytest.mark.parametrize("shape", ["gitignored_dir", "nested_gitignore", "extra_ignore_pattern"])
async def test_a_batch_of_ignored_events_runs_no_reindex(tmp_path, monkeypatch, shape):
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    storage = str(tmp_path / "store")
    extra = ["data/"] if shape == "extra_ignore_pattern" else None
    _index(watcher, root, storage, extra)

    def batch():
        if shape == "gitignored_dir":
            out = root / "build" / "out.py"
            out.write_text("def generated(): pass\n", encoding="utf-8")
            (root / "build" / "sub").mkdir()
            return {(Change.added, str(out)), (Change.added, str(root / "build" / "sub"))}
        if shape == "nested_gitignore":
            cached = root / "pkg" / "cache" / "c.py"
            cached.write_text("def cached(): pass\n", encoding="utf-8")
            return {(Change.modified, str(cached))}
        db = root / "data" / "state.py"
        db.write_text("def state(): pass\n", encoding="utf-8")
        return {(Change.modified, str(db))}

    calls = await _run_batches(watcher, monkeypatch, root, storage, [batch], extra_ignore_patterns=extra)
    assert calls == [], calls


async def test_a_mixed_batch_reindexes_only_the_kept_edit(tmp_path, monkeypatch):
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    storage = str(tmp_path / "store")
    _index(watcher, root, storage)
    code = root / "code.py"

    def batch():
        code.write_text("def after(): pass\n", encoding="utf-8")
        out = root / "build" / "out.py"
        out.write_text("def generated(): pass\n", encoding="utf-8")
        (root / "build" / "sub").mkdir()
        return {(Change.modified, str(code)), (Change.added, str(out)),
                (Change.added, str(root / "build" / "sub"))}

    calls = await _run_batches(watcher, monkeypatch, root, storage, [batch])
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
    _index(watcher, root, storage)

    def batch():
        (root / ".gitignore").write_text(".gitignore\n", encoding="utf-8")
        return {(Change.modified, str(root / ".gitignore"))}

    calls = await _run_batches(watcher, monkeypatch, root, storage, [batch])
    assert len(calls) == 1, calls


async def test_a_deletion_under_an_ignored_path_still_reaches_the_index(tmp_path, monkeypatch):
    """A file indexed before its rule existed must still leave the index when
    deleted, so a deletion is never dropped (the targeted path's own rule)."""
    from jcodemunch_mcp import watcher
    from jcodemunch_mcp.storage.index_store import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("", encoding="utf-8")
    # Not `build/`: that is a built-in skip directory, so it is never indexed.
    (root / "gen").mkdir()
    legacy = root / "gen" / "legacy.py"
    legacy.write_text("def legacy(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = _index(watcher, root, storage)
    owner, name = result["repo"].split("/", 1)
    assert "legacy" in {s["name"] for s in IndexStore(base_path=storage).load_index(owner, name).symbols}
    (root / ".gitignore").write_text("gen/\n", encoding="utf-8")

    def batch():
        legacy.unlink()
        return {(Change.deleted, str(legacy))}

    calls = await _run_batches(watcher, monkeypatch, root, storage, [batch])
    assert len(calls) == 1, calls
    assert "legacy" not in {s["name"] for s in IndexStore(base_path=storage).load_index(owner, name).symbols}


async def test_a_pattern_removed_from_the_project_config_holds_from_the_next_batch(tmp_path, monkeypatch):
    """The rules are read per batch: a pattern removed while the watcher runs
    stops dropping events without a restart."""
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    config = root / ".jcodemunch.jsonc"
    config.write_text('{"extra_ignore_patterns": ["data/"]}\n', encoding="utf-8")
    storage = str(tmp_path / "store")
    _index(watcher, root, storage)
    state = root / "data" / "state.py"

    def first():
        state.write_text("def one(): pass\n", encoding="utf-8")
        return {(Change.modified, str(state))}

    def second():
        config.write_text('{"extra_ignore_patterns": []}\n', encoding="utf-8")
        state.write_text("def two(): pass\n", encoding="utf-8")
        return {(Change.modified, str(state))}

    calls = await _run_batches(watcher, monkeypatch, root, storage, [first, second])
    assert len(calls) == 1, calls
    assert "state.py" in repr(calls[0].get("changed_paths")), calls[0]


async def test_a_due_retry_runs_under_a_stream_of_ignored_events(tmp_path, monkeypatch):
    """An all-ignored batch takes the idle branch, so a retry that is due runs
    even when ignored events arrive faster than an idle wake-up."""
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = _repo(tmp_path)
    storage = str(tmp_path / "store")
    _index(watcher, root, storage)
    code = root / "code.py"
    out = root / "build" / "out.py"
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: clock.now))
    real_index = watcher.index_folder
    attempts = []

    def flaky(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            return {"success": False, "error": "disk full"}
        return real_index(**kwargs)

    def failing_edit():
        code.write_text("def after(): pass\n", encoding="utf-8")
        return {(Change.modified, str(code))}

    def ignored_after_the_deadline():
        clock.now += 10_000
        out.write_text("def generated(): pass\n", encoding="utf-8")
        return {(Change.modified, str(out))}

    calls = await _run_batches(
        watcher, monkeypatch, root, storage, [failing_edit, ignored_after_the_deadline], index=flaky,
    )
    assert len(calls) == 2, calls
    assert calls[1].get("discover") is True, calls[1]


def test_a_subfolder_watch_below_a_git_root_drops_only_what_its_own_files_ignore(tmp_path):
    """Below a git root, a walk in git identity reads the root's .gitignore
    and matches patterns from the root, and one in local identity does
    neither. Only the .gitignore files from the watched folder down hold for
    both, so only they drop events there."""
    from jcodemunch_mcp.tools.index_folder import ignored_event_predicate

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".gitignore").write_text("gen/\n", encoding="utf-8")
    sub = repo / "sub"
    (sub / "gen").mkdir(parents=True)
    (sub / "out").mkdir()
    (sub / ".gitignore").write_text("out/\n", encoding="utf-8")
    is_ignored = ignored_event_predicate(str(sub), extra_ignore_patterns=["/gen/"])
    assert is_ignored(str(sub / "gen" / "x.py")) is False
    assert is_ignored(str(sub / "out" / "y.py")) is True
