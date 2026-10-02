"""A repo-key lookup that found nothing is remembered (#960, @ebataeva).

`config._resolve_repo_key` documented a negative cache and never wrote one: a
`repo=` value that matched no index listed every index in storage on EVERY
call, and `list_repos` opens every `.db`. Discovery asks once per file
(`is_secret_file(rel_path, repo=str(root))`), so indexing a subdirectory of a
git root cost one full listing per candidate file, scaling with how many
indexes the user has. A source root that DID match was not cached either,
because only the display name and the repo id were written back.

What is checked: the number of listings, at the resolver and at the entry
point the reporter used (`discover_local_files`); and that a remembered miss
cannot outlive the thing that makes it wrong (an index saved or deleted in
this process, or a few seconds for a save made by another process), and that
a project config loaded afterwards is answered ahead of it.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from jcodemunch_mcp import config as cfg
from jcodemunch_mcp.storage.index_store import IndexStore
from jcodemunch_mcp.tools.index_folder import discover_local_files, index_folder


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """A storage dir the resolver reads, holding one index, with listings counted."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store_dir))
    indexed = tmp_path / "indexed"
    (indexed / "pkg").mkdir(parents=True)
    (indexed / "pkg" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    res = index_folder(str(indexed), use_ai_summaries=False, storage_path=str(store_dir))
    assert "error" not in res, res

    calls = {"n": 0}
    real = IndexStore.list_repos

    def counted(self):
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(IndexStore, "list_repos", counted)
    cfg._PROJECT_CONFIGS.clear()
    # Resolved by name so the file reproduces the defect on a tree that predates the function.
    getattr(cfg, "forget_repo_resolutions", cfg._REPO_PATH_CACHE.clear)()
    return types.SimpleNamespace(dir=store_dir, indexed=indexed, repo=res["repo"], calls=calls, tmp=tmp_path)


def test_a_miss_is_listed_once(storage):
    unknown = str(storage.tmp / "nowhere")
    for _ in range(50):
        assert cfg._resolve_repo_key(unknown) is None
    assert storage.calls["n"] == 1


def test_a_source_root_that_resolves_is_listed_once(storage):
    root = str(storage.indexed.resolve())
    for _ in range(50):
        assert cfg._resolve_repo_key(root) == root
    assert storage.calls["n"] == 1


def test_a_repo_id_that_resolves_is_listed_once(storage):
    root = str(storage.indexed.resolve())
    for _ in range(50):
        assert cfg._resolve_repo_key(storage.repo) == root
    assert storage.calls["n"] == 1


def test_config_get_for_an_unindexed_root_lists_once(storage):
    unknown = str(storage.tmp / "nowhere")
    for _ in range(50):
        cfg.get("exclude_secret_patterns", [], repo=unknown)
    assert storage.calls["n"] == 1


def test_discovery_under_an_unindexed_root_does_not_list_per_file(storage):
    """The reported shape: a walk whose root matches no index."""
    sub = storage.tmp / "notes" / "Projects"
    sub.mkdir(parents=True)
    for i in range(40):
        (sub / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    files, _warnings, _skips = discover_local_files(sub.resolve())
    assert len(files) == 40
    assert storage.calls["n"] <= 2, storage.calls["n"]


def test_a_remembered_miss_does_not_outlive_an_index_saved_here(storage, monkeypatch):
    # A frozen clock: the miss cannot end on its own while the index is built.
    monkeypatch.setattr(cfg, "time", types.SimpleNamespace(monotonic=lambda: 1000.0))
    later = storage.tmp / "later"
    later.mkdir()
    (later / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    root = str(later.resolve())
    assert cfg._resolve_repo_key(root) is None
    res = index_folder(root, use_ai_summaries=False, storage_path=str(storage.dir))
    assert "error" not in res, res
    cfg._PROJECT_CONFIGS.clear()  # the lookup must come from the store, not the loaded config
    assert cfg._resolve_repo_key(root) == root
    assert cfg._resolve_repo_key(res["repo"]) == root


def test_a_resolved_key_does_not_outlive_its_index(storage):
    root = str(storage.indexed.resolve())
    assert cfg._resolve_repo_key(storage.repo) == root
    owner, name = storage.repo.split("/", 1)
    assert IndexStore(base_path=str(storage.dir)).delete_index(owner, name) is True
    assert cfg._resolve_repo_key(storage.repo) is None


def test_a_remembered_miss_expires(storage, monkeypatch):
    """A save made by another process is not announced here; the miss ends on its own."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(cfg, "time", types.SimpleNamespace(monotonic=lambda: clock["t"]))
    unknown = str(storage.tmp / "nowhere")
    assert cfg._resolve_repo_key(unknown) is None
    clock["t"] += cfg._REPO_MISS_TTL_SECONDS - 0.01
    assert cfg._resolve_repo_key(unknown) is None
    assert storage.calls["n"] == 1
    clock["t"] += 0.02
    assert cfg._resolve_repo_key(unknown) is None
    assert storage.calls["n"] == 2


def test_a_loaded_project_config_is_answered_ahead_of_a_remembered_miss(storage):
    project = storage.tmp / "project"
    project.mkdir()
    (project / ".jcodemunch.jsonc").write_text('{"max_file_size": 1234}', encoding="utf-8")
    key = str(Path(project).resolve())
    assert cfg._resolve_repo_key(key) is None
    cfg.load_project_config(str(project))
    assert cfg._resolve_repo_key(key) == key
    assert cfg.get("max_file_size", repo=key) == 1234


def test_the_remembered_misses_are_bounded(storage):
    for i in range(cfg._REPO_CACHE_MAX + 50):
        cfg._resolve_repo_key(f"no-such-repo-{i}")
    assert len(cfg._REPO_MISS_CACHE) <= cfg._REPO_CACHE_MAX
    assert len(cfg._REPO_PATH_CACHE) <= cfg._REPO_CACHE_MAX


def test_the_remembered_hits_are_bounded(storage, monkeypatch):
    many = [
        {"repo": f"local/r{i}", "display_name": f"r{i}", "source_root": str(storage.tmp / f"r{i}")}
        for i in range(cfg._REPO_CACHE_MAX)
    ]
    monkeypatch.setattr(IndexStore, "list_repos", lambda self: many)
    assert cfg._resolve_repo_key("r7") == str((storage.tmp / "r7").resolve())
    assert len(cfg._REPO_PATH_CACHE) == 2 * cfg._REPO_CACHE_MAX
    monkeypatch.setattr(IndexStore, "list_repos", lambda self: many[:3])
    assert cfg._resolve_repo_key("no-such-repo") is None
    assert len(cfg._REPO_PATH_CACHE) == cfg._REPO_CACHE_MAX
    # The oldest entries went; the newest stayed.
    assert "local/r5" not in cfg._REPO_PATH_CACHE
    assert f"r{cfg._REPO_CACHE_MAX - 1}" in cfg._REPO_PATH_CACHE


@pytest.mark.parametrize("count", [10, 400, 512, 600])
def test_a_store_larger_than_the_bound_still_lists_once(storage, monkeypatch, count):
    """Two keys per index: past 256 indexes one listing is larger than the bound."""
    many = [
        {"repo": f"local/r{i}", "display_name": f"r{i}", "source_root": str(storage.tmp / f"r{i}")}
        for i in range(count)
    ]
    listings = {"n": 0}

    def counted(self):
        listings["n"] += 1
        return many

    monkeypatch.setattr(IndexStore, "list_repos", counted)
    root = str((storage.tmp / "r7").resolve())
    for identifier in ("local/r7", "r7", root, "local/r0", f"r{count - 1}"):
        for _ in range(20):
            assert cfg._resolve_repo_key(identifier) is not None
    assert listings["n"] <= 2, listings["n"]


def test_a_listing_overtaken_by_a_save_is_not_remembered(storage, monkeypatch):
    """The interleaving, pinned: the store is listed, THEN an index is saved, THEN the lookup returns.

    A miss written after the save's forget would hide the new index for the
    whole TTL, and `config.get` would answer from global over the project's value.
    """
    monkeypatch.setattr(cfg, "time", types.SimpleNamespace(monotonic=lambda: 1000.0))
    later = storage.tmp / "race"
    later.mkdir()
    (later / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    (later / ".jcodemunch.jsonc").write_text('{"max_file_size": 1234}', encoding="utf-8")
    root = str(later.resolve())
    real = IndexStore.list_repos
    state = {"saved": False}

    def list_then_save(self):
        listed = real(self)
        if not state["saved"]:
            state["saved"] = True
            res = index_folder(root, use_ai_summaries=False, storage_path=str(storage.dir))
            assert "error" not in res, res
            state["repo"] = res["repo"]
        return listed

    monkeypatch.setattr(IndexStore, "list_repos", list_then_save)
    assert cfg._resolve_repo_key("race") is None  # the listing predates the save
    assert state["saved"] is True
    assert "race" not in cfg._REPO_MISS_CACHE
    assert cfg._resolve_repo_key("race") == root
    assert cfg.get("max_file_size", repo="race") == 1234


def test_a_listing_that_raises_is_not_remembered_as_a_miss(storage, monkeypatch):
    def boom(self):
        raise OSError("storage unreadable")

    monkeypatch.setattr(IndexStore, "list_repos", boom)
    assert cfg._resolve_repo_key(storage.repo) is None
    assert storage.repo not in cfg._REPO_MISS_CACHE
