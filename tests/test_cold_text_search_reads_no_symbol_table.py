"""A one-shot text search reads the files table and the matched files' symbols, not every symbol.

The Grep redirect hook runs in a new process for every Grep, so it never has a
warm index. It loaded the full index (every symbol row) to check freshness and
again inside `search_text`, which needs only the file list, the mtimes, and the
symbols of the files that matched (to name the enclosing symbol). After a
reboot that full read comes from disk, and hooked Greps ran past the answer
budget. The hook now opens the selective view for both, and asks `search_text`
for the same with `selective_load=True`. A server leaves that off, so its
first search loads and caches the full index as before. The hook also logs how
long each stage took, at debug level.
"""

from __future__ import annotations

import logging

import pytest

from jcodemunch_mcp.cli.hooks import grep_answer
from jcodemunch_mcp.storage import sqlite_store
from jcodemunch_mcp.storage.selective import SelectiveIndexView
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_text import search_text

ENGINE = '''class Engine:
    def start(self):
        return "beacon in start"

    def stop(self):
        return "beacon in stop"


def helper():
    return "beacon in helper"
'''

OTHER = '''def unrelated():
    return 1


def also_unrelated():
    return 2
'''


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "engine.py").write_text(ENGINE, encoding="utf-8")
    (src / "other.py").write_text(OTHER, encoding="utf-8")
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    return res["repo"], str(store), src


@pytest.fixture
def full_loads(monkeypatch):
    """Count full index loads, the read a one-shot text search should not need."""
    calls: list = []
    real = sqlite_store.SQLiteIndexStore.load_index

    def counting(self, owner, name, branch=""):
        calls.append((owner, name))
        return real(self, owner, name, branch)

    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "load_index", counting)
    return calls


@pytest.fixture
def narrow_reads(monkeypatch):
    """Count selective reads that ask for named files' symbols."""
    calls: list = []
    real = sqlite_store.SQLiteIndexStore.open_selective

    def counting(self, owner, name, **kwargs):
        if kwargs.get("files"):
            calls.append(list(kwargs["files"]))
        return real(self, owner, name, **kwargs)

    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "open_selective", counting)
    return calls


def _cold():
    # A new process has no hydrated index; index_folder left one cached here.
    sqlite_store._cache_clear()


def _answer(res: dict) -> dict:
    meta = res.get("_meta") or {}
    return {
        **{k: res.get(k) for k in ("results", "result_count", "sources", "sources_total", "more_files")},
        "verdict": (meta.get("verdict") or {}).get("state"),
    }


def _enclosing_names(res: dict) -> set:
    ids = {(m.get("in") or {}).get("id") for group in res["results"] for m in group["matches"]}
    assert all(ids), ids
    return {i.split("::", 1)[1].split("#", 1)[0].rsplit(".", 1)[-1] for i in ids}


def test_a_selective_search_does_not_load_the_full_index(indexed, full_loads):
    repo, store, _ = indexed
    _cold()
    res = search_text(repo, "beacon", storage_path=store, selective_load=True)
    assert res.get("results"), res
    assert full_loads == [], "a one-shot search_text loaded every symbol row"


def test_a_selective_search_still_names_the_enclosing_symbols(indexed):
    repo, store, _ = indexed
    _cold()
    res = search_text(repo, "beacon", storage_path=store, selective_load=True)
    assert {"start", "stop", "helper"} <= _enclosing_names(res)


def test_the_selective_answer_equals_the_full_load(indexed):
    repo, store, _ = indexed
    _cold()
    selective = _answer(search_text(repo, "beacon", storage_path=store, selective_load=True))
    _cold()
    full = _answer(search_text(repo, "beacon", storage_path=store))
    assert selective == full


def test_a_server_search_loads_and_caches_the_full_index(indexed, narrow_reads, monkeypatch):
    # The default path is unchanged: the first cold call hydrates and caches,
    # so the next one is a memory read and no narrow read is ever made.
    # load_index runs on both calls; only the first builds rows, so count builds.
    repo, store, _ = indexed
    builds: list = []
    real = sqlite_store.SQLiteIndexStore._build_index_from_rows

    def counting(self, *args, **kwargs):
        builds.append(True)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "_build_index_from_rows", counting)
    _cold()
    search_text(repo, "beacon", storage_path=store)
    search_text(repo, "beacon", storage_path=store)
    assert len(builds) == 1, f"{len(builds)} hydrations for two server searches"
    assert narrow_reads == []


def test_a_warm_index_is_used_as_is(indexed, full_loads, narrow_reads):
    repo, store, _ = indexed
    _cold()
    search_text(repo, "beacon", storage_path=store)  # warms the cache
    res = search_text(repo, "beacon", storage_path=store, selective_load=True)
    assert len(full_loads) == 1, "the warm index was loaded again"
    assert narrow_reads == [], "a warm index made a narrow read"
    assert {"start", "stop", "helper"} <= _enclosing_names(res)


@pytest.mark.parametrize("moved", ["generation", "mtime"])
def test_a_narrow_read_from_another_index_falls_back_to_the_full_index(
    indexed, full_loads, monkeypatch, moved
):
    # Simulate a save landing between the two reads: the second read reports a
    # different indexed_at, or a different database mtime. Its rows must not be
    # used; the full index answers instead.
    repo, store, _ = indexed
    real = sqlite_store.SQLiteIndexStore.open_selective
    used: list = []

    # SelectiveIndexView has __slots__, so the method is wrapped on the class.
    real_method = SelectiveIndexView.selected_symbols

    def recording(self):
        used.append(True)
        return real_method(self)

    monkeypatch.setattr(SelectiveIndexView, "selected_symbols", recording)

    def drifting_slots(self, owner, name, **kwargs):
        view = real(self, owner, name, **kwargs)
        if kwargs.get("files") and isinstance(view, SelectiveIndexView):
            if moved == "generation":
                object.__setattr__(view, "_generation", "2000-01-01T00:00:00")
            else:
                object.__setattr__(view, "_loaded_mtime_ns", (view._loaded_mtime_ns or 0) + 1)
        return view

    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "open_selective", drifting_slots)
    _cold()
    res = search_text(repo, "beacon", storage_path=store, selective_load=True)
    assert used == [], f"rows from a read with a different {moved} were used"
    assert len(full_loads) == 1, full_loads
    assert {"start", "stop", "helper"} <= _enclosing_names(res)


def test_the_hook_freshness_check_does_not_load_the_full_index(indexed, full_loads):
    repo, _, _ = indexed
    _cold()
    assert grep_answer._index_matches_disk(repo) is True
    assert full_loads == []


def test_the_hook_answer_does_not_load_the_full_index(indexed, full_loads, monkeypatch):
    _, _, src = indexed
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER", "1")
    _cold()
    assert grep_answer.answer_grep({"pattern": "beacon"}, str(src), "s-cold")
    assert full_loads == []


def test_the_hook_logs_each_stage_at_debug(indexed, monkeypatch, caplog):
    _, _, src = indexed
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER", "1")
    _cold()
    with caplog.at_level(logging.DEBUG, logger=grep_answer.logger.name):
        out = grep_answer.answer_grep({"pattern": "beacon"}, str(src), "s-stage")
    assert out, "the hook did not answer"
    lines = [r.getMessage() for r in caplog.records if "grep-answer stages" in r.getMessage()]
    assert len(lines) == 1, [r.getMessage() for r in caplog.records]
    for stage in ("resolve", "freshness", "search", "total"):
        assert f"{stage}=" in lines[0], lines[0]
