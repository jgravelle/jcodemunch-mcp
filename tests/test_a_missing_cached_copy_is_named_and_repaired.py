"""A file whose cached copy is gone is named as such and a one-file re-index
repairs it (a buyer report on 1.108.417).

With one file's cached text deleted, `index_file`,
an incremental `index_folder` and `index_folder(paths=[file])` all reported
the file unchanged and repaired nothing, because the unchanged check asked
only whether the hash matched. `get_symbol_source` meanwhile said "Re-index
the repo to rebuild it", a whole-repo remedy for a one-file loss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.get_file_content import get_file_content
from jcodemunch_mcp.tools.get_symbol import get_symbol_source
from jcodemunch_mcp.tools.index_file import index_file
from jcodemunch_mcp.tools.index_folder import index_folder


@pytest.fixture
def repo(tmp_path, monkeypatch):
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(3):
        (src / f"m{i}.py").write_text(
            f"def f{i}():\n    return {i}\n", encoding="utf-8"
        )
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    owner, name = r["repo"].split("/", 1)
    content_dir = IndexStore(base_path=str(store))._sqlite._content_dir(owner, name)
    cached = content_dir / "m0.py"
    assert cached.exists()
    cached.unlink()
    return src, str(store), r["repo"], cached


def _sym_id(repo_id, store):
    owner, name = repo_id.split("/", 1)
    index = IndexStore(base_path=store).load_index(owner, name)
    return next(s["id"] for s in index.symbols if s["name"] == "f0")


def test_the_missing_copy_is_named_with_a_one_file_repair(repo):
    src, store, repo_id, _ = repo
    r = get_symbol_source(
        repo=repo_id, symbol_id=_sym_id(repo_id, store), storage_path=store
    )
    reason = r["source_unavailable_reason"]
    assert r["source_status"] == "content_cache_missing"
    assert "cached copy of m0.py is missing" in reason and "index_file" in reason
    assert "Re-index the repo" not in reason and "not a stale index" not in reason
    note = r["_meta"]["verdict"]["note"]
    assert "Re-index the repo" not in note and "index_file" in note, (
        "two remedies in one answer"
    )
    fc = get_file_content(repo_id, "m0.py", storage_path=store)
    assert (
        fc["source_status"] == "content_cache_missing" and "index_file" in fc["error"]
    )


def test_index_file_restores_a_missing_copy_of_an_unchanged_file(repo):
    src, store, repo_id, cached = repo
    r = index_file(str(src / "m0.py"), use_ai_summaries=False, storage_path=store)
    assert r.get("success") and r.get("message") != "File unchanged", r
    assert cached.exists()
    got = get_symbol_source(
        repo=repo_id, symbol_id=_sym_id(repo_id, store), storage_path=store
    )
    assert "return 0" in got["source"]


def test_paths_restores_only_the_listed_missing_copy_and_keeps_the_rest(repo):
    src, store, repo_id, cached = repo
    r = index_folder(
        str(src), use_ai_summaries=False, storage_path=store, paths=["m0.py"]
    )
    assert r.get("success") and r.get("message") != "No changes detected", r
    assert cached.exists()
    owner, name = repo_id.split("/", 1)
    index = IndexStore(base_path=store).load_index(owner, name)
    assert sorted(index.source_files) == ["m0.py", "m1.py", "m2.py"]


def test_an_unchanged_file_with_its_copy_is_still_unchanged(repo):
    """Non-vacuity of the cost side: a present copy forces nothing."""
    src, store, repo_id, _ = repo
    r = index_file(str(src / "m1.py"), use_ai_summaries=False, storage_path=store)
    assert r.get("message") == "File unchanged", r


def test_metadata_only_keeps_no_copy_so_none_is_missing(repo, monkeypatch):
    src, store, repo_id, _ = repo
    from jcodemunch_mcp import config as _cfg

    real = _cfg.get
    monkeypatch.setattr(
        _cfg,
        "get",
        lambda k, d=None, **kw: (
            "metadata_only" if k == "cache_mode" else real(k, d, **kw)
        ),
    )
    owner, name = repo_id.split("/", 1)
    assert (
        IndexStore(base_path=store).missing_cached_copies(owner, name, ["m0.py"]) == []
    )


def test_the_context_bundle_names_the_missing_copy_too(repo):
    from jcodemunch_mcp.tools.get_context_bundle import get_context_bundle

    src, store, repo_id, _ = repo
    r = get_context_bundle(
        repo=repo_id, symbol_id=_sym_id(repo_id, store), storage_path=store
    )
    entry = r["symbols"][0] if "symbols" in r else r
    assert entry["source_status"] == "content_cache_missing"
    assert "index_file" in entry["source_unavailable_reason"]


def test_refresh_with_reset_restores_after_a_finished_campaign(repo):
    """The printed remedy names --reset: a finished campaign re-parses nothing."""
    from jcodemunch_mcp.tools import refresh

    src, store, repo_id, cached = repo
    first = refresh.run(str(src), storage_path=store)
    assert first.get("success", True) and cached.exists()
    cached.unlink()
    refresh.run(str(src), storage_path=store)
    assert not cached.exists(), (
        "non-vacuity: without --reset a finished campaign restores nothing"
    )
    refresh.run(str(src), storage_path=store, reset=True)
    assert cached.exists()


def test_a_content_folder_that_cannot_be_named_forces_nothing(repo, monkeypatch):
    """UNKNOWN is not missing: index_file stays a no-op on an unchanged file."""
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    src, store, repo_id, _ = repo
    monkeypatch.setattr(
        SQLiteIndexStore, "_content_dir_or_none", lambda self, o, n: None
    )
    r = index_file(str(src / "m0.py"), use_ai_summaries=False, storage_path=store)
    assert r.get("message") == "File unchanged", r


def test_a_check_that_raises_forces_nothing(repo, monkeypatch):
    src, store, repo_id, _ = repo
    real = Path.exists

    def exists(self, *a, **k):
        if self.name == "m0.py" and "proj" not in self.parts:
            raise PermissionError(13, "denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "exists", exists)
    owner, name = repo_id.split("/", 1)
    assert (
        IndexStore(base_path=store).missing_cached_copies(owner, name, ["m0.py"]) == []
    )


def test_the_printed_remedy_names_the_flags_that_make_it_work():
    """Each remedy restores nothing without its flag: refresh without --reset
    after a finished campaign, index_repo without incremental=False on an
    unchanged tree. The text must carry both."""
    from jcodemunch_mcp.retrieval.verdict import CONTENT_COPY_REPAIR

    assert "refresh <folder> --reset" in CONTENT_COPY_REPAIR
    assert "index_repo with incremental=False" in CONTENT_COPY_REPAIR
