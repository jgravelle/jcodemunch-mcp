"""A no-change re-index answers from metadata, and its root is resolved once.

BACKLOG 31 profiled a no-change re-index of Home Assistant (24,347 files):

- `index_folder`'s full path loaded the whole base index twice (8.1 of 31.9 s),
  only to read `git_root`, `source_root`, `branch` and the file hashes. The
  watcher's fast path had already moved to `open_selective` for the same
  reason (#557, tests/test_fast_path_no_hydration.py).
- the discovery pass called `validate_path` once per file, and each call
  resolved the walk root again.

⚠ The hydration tests assert the OUTCOME, that no view promotes, not that
`open_selective` is called. A view that promotes is still correct, so only an
outcome test catches the speed regression. The process index cache is cleared
first, because a warm cache hands back the full index and would prove nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jcodemunch_mcp import security
from jcodemunch_mcp.storage.selective import SelectiveIndexView


def _index(tmp_path: Path, storage: Path) -> dict:
    from jcodemunch_mcp.tools.index_folder import index_folder

    return index_folder(
        path=str(tmp_path),
        use_ai_summaries=False,
        storage_path=str(storage),
        incremental=True,
        context_providers=False,
    )


@pytest.fixture
def indexed(tmp_path: Path):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n")
    (src / "b.py").write_text("class B:\n    def m(self):\n        return 2\n")
    storage = tmp_path / ".code-index"
    r = _index(src, storage)
    assert r["success"], r
    return src, storage


def _cold_cache():
    from jcodemunch_mcp.storage import sqlite_store

    sqlite_store._index_cache.clear()


def test_a_no_change_reindex_hydrates_no_index(indexed, monkeypatch):
    from jcodemunch_mcp.storage import index_store as store_mod

    src, storage = indexed
    views: list = []
    full_loads: list = []
    real_sel = store_mod.IndexStore.open_selective
    real_load = store_mod.IndexStore.load_index

    def spy_sel(self, owner, name, **kw):
        v = real_sel(self, owner, name, **kw)
        views.append(v)
        return v

    def spy_load(self, owner, name, *a, **kw):
        full_loads.append((owner, name, a, kw))
        return real_load(self, owner, name, *a, **kw)

    monkeypatch.setattr(store_mod.IndexStore, "open_selective", spy_sel)
    monkeypatch.setattr(store_mod.IndexStore, "load_index", spy_load)
    _cold_cache()
    r = _index(src, storage)
    assert r.get("message") == "No changes detected", r
    assert r.get("fast_path") is not True, "the fast path ran; this test is about the full path"
    sel = [v for v in views if isinstance(v, SelectiveIndexView)]
    assert sel, "the full path took no selective read of the base index"
    for v in sel:
        assert not v.promoted, f"a no-change re-index hydrated the index for {v.promoted_by!r}"
    assert not full_loads, f"a no-change re-index still called load_index: {full_loads}"


def test_a_change_after_the_selective_read_is_still_indexed(indexed):
    """The view promotes when a later step needs symbols, so a real change
    still reaches the index."""
    src, storage = indexed
    (src / "a.py").write_text("def a():\n    return 1\n\ndef added():\n    return 3\n")
    _cold_cache()
    r = _index(src, storage)
    assert r["success"], r
    assert r.get("message") != "No changes detected"
    from jcodemunch_mcp.storage import IndexStore

    _cold_cache()
    store = IndexStore(base_path=str(storage))
    (owner, name) = next((x["repo"].split("/", 1) for x in store.list_repos()), (None, None))
    idx = store.load_index(owner, name)
    assert any(s.get("name") == "added" for s in idx.symbols), "the new function is missing"


def test_the_discovery_pass_resolves_its_root_once(indexed, monkeypatch):
    from jcodemunch_mcp.tools import index_folder as mod

    src, storage = indexed
    calls: list = []
    real = security.validate_path

    def spy(root, target, **kw):
        calls.append(kw.get("root_resolved"))
        return real(root, target, **kw)

    monkeypatch.setattr(mod, "validate_path", spy)
    _cold_cache()
    _index(src, storage)
    assert calls, "the discovery pass called validate_path on no file"
    assert all(c is not None for c in calls), "validate_path resolved the root again per file"
    assert len(set(calls)) == 1


@pytest.mark.parametrize(
    "rel",
    ["a.py", "sub/c.py", "../outside.py", "sub/../../outside.py", "."],
)
def test_a_cached_root_gives_the_same_answer(tmp_path, rel):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.py").write_text("")
    (root / "sub" / "c.py").write_text("")
    (tmp_path / "outside.py").write_text("")
    target = root / rel
    cached = str(root.resolve())
    assert security.validate_path(root, target, root_resolved=cached) == security.validate_path(
        root, target
    )


def test_a_symlink_out_of_the_root_is_refused_with_a_cached_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("")
    link = root / "escape.py"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges on this host")
    assert security.validate_path(root, link, root_resolved=str(root.resolve())) is False
