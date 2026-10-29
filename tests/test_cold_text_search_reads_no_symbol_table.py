"""A cold text search reads the files table and the matched files' symbols, not every symbol.

The Grep redirect hook runs in a new process for every Grep, so it never has a
warm index. It loaded the full index (every symbol row) to check freshness and
again inside `search_text`, which needs only the file list, the mtimes, and the
symbols of the files that matched (to name the enclosing symbol). After a
reboot that full read is disk-bound, and 23 hooked Greps ran past the answer
budget. Both now open the selective view: metadata and the files table, plus
the symbol rows of the matched files only. A warm cache is still used as is.
The hook also logs how long each stage took, at debug level.
"""

from __future__ import annotations

import logging

import pytest

from jcodemunch_mcp.cli.hooks import grep_answer
from jcodemunch_mcp.storage import sqlite_store
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
    """Count full index loads, the read a cold text search should not need."""
    calls: list = []
    real = sqlite_store.SQLiteIndexStore.load_index

    def counting(self, owner, name, branch=""):
        calls.append((owner, name))
        return real(self, owner, name, branch)

    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "load_index", counting)
    return calls


def _cold():
    # A new process has no hydrated index; index_folder left one cached here.
    sqlite_store._cache_clear()


def _answer(res: dict) -> dict:
    return {k: res.get(k) for k in ("results", "sources", "sources_total", "more_files")}


def test_a_cold_search_text_does_not_load_the_full_index(indexed, full_loads):
    repo, store, _ = indexed
    _cold()
    res = search_text(repo, "beacon", storage_path=store)
    assert res.get("results"), res
    assert full_loads == [], "a cold search_text loaded every symbol row"


def test_a_cold_search_still_names_the_enclosing_symbols(indexed):
    repo, store, _ = indexed
    _cold()
    res = search_text(repo, "beacon", storage_path=store)
    ids = {(m.get("in") or {}).get("id") for group in res["results"] for m in group["matches"]}
    assert all(ids), ids
    names = {i.split("::", 1)[1].split("#", 1)[0].rsplit(".", 1)[-1] for i in ids}
    assert {"start", "stop", "helper"} <= names, ids


def test_the_selective_answer_equals_the_full_load(indexed, monkeypatch):
    repo, store, _ = indexed
    _cold()
    selective = _answer(search_text(repo, "beacon", storage_path=store))

    _cold()
    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "open_selective", lambda self, *a, **k: None)
    full = _answer(search_text(repo, "beacon", storage_path=store))
    assert selective == full


def test_the_hook_freshness_check_does_not_load_the_full_index(indexed, full_loads):
    repo, _, _ = indexed
    _cold()
    assert grep_answer._index_matches_disk(repo) is True
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
