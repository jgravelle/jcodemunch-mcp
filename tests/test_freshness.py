"""Tests for v1.77.0 per-symbol freshness classification."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval.freshness import FreshnessProbe


def _ns(seconds: float) -> int:
    return int(seconds * 1e9)


class TestFreshnessProbeBasics:
    def test_no_source_root_classifies_fresh(self):
        probe = FreshnessProbe(source_root=None, indexed_at="", index_sha=None)
        assert probe.classify("foo.py") == "fresh"

    def test_repo_is_stale_when_sha_differs(self):
        probe = FreshnessProbe(
            source_root="/nonexistent",
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
        )
        assert probe.repo_is_stale is True
        assert probe.classify("foo.py") == "stale_index"

    def test_matching_sha_does_not_mark_stale(self):
        probe = FreshnessProbe(
            source_root=None,
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="abc",
            current_sha="abc",
        )
        assert probe.repo_is_stale is False


class TestFreshnessFromMtimes:
    def test_unedited_file_is_fresh(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        indexed_mtime_ns = _ns(f.stat().st_mtime)
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
            file_mtimes={"a.py": indexed_mtime_ns},
        )
        assert probe.classify("a.py") == "fresh"

    def test_edited_file_is_marked_uncommitted(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        # Pretend we indexed two minutes ago
        indexed_mtime_ns = _ns(f.stat().st_mtime - 120)
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
            file_mtimes={"a.py": indexed_mtime_ns},
        )
        assert probe.classify("a.py") == "edited_uncommitted"

    def test_falls_back_to_indexed_at_when_no_per_file_mtime(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        # Index timestamp two minutes before file mtime
        old_ts = datetime.fromtimestamp(f.stat().st_mtime - 120, tz=timezone.utc)
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at=old_ts.isoformat(timespec="seconds"),
            index_sha="same",
            current_sha="same",
        )
        assert probe.classify("a.py") == "edited_uncommitted"


class TestAnnotateAndSummary:
    def test_annotate_in_place(self, tmp_path):
        f = tmp_path / "ok.py"
        f.write_text("x = 1\n")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
            file_mtimes={"ok.py": _ns(f.stat().st_mtime)},
        )
        entries = [{"file": "ok.py"}, {"file": "missing.py"}]
        probe.annotate(entries)
        assert entries[0]["_freshness"] == "fresh"
        # missing file → can't stat, defaults to fresh per probe contract
        assert entries[1]["_freshness"] == "fresh"

    def test_summary_counts(self, tmp_path):
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",  # repo is stale
        )
        entries = [{"file": "a.py"}, {"file": "b.py"}]
        probe.annotate(entries)
        summary = probe.summary(entries)
        assert summary["stale_index"] == 2
        assert summary["fresh"] == 0
        assert summary["repo_is_stale"] is True


class TestSearchSymbolsCarriesFreshness:
    def test_search_symbols_returns_freshness_meta(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.search_symbols import search_symbols

        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"
        store.mkdir()
        (src / "auth.py").write_text("def authenticate():\n    pass\n")
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True
        out = search_symbols(
            repo=r["repo"],
            query="authenticate",
            storage_path=str(store),
        )
        assert "freshness" in out["_meta"]
        assert out["_meta"]["freshness"]["repo_is_stale"] is False
        for item in out["results"]:
            assert "_freshness" in item

    def test_edit_after_index_marks_results_uncommitted(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.search_symbols import search_symbols

        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"
        store.mkdir()
        f = src / "auth.py"
        f.write_text("def authenticate():\n    pass\n")
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True

        # Edit the file *after* indexing (move mtime forward by 5s)
        time.sleep(0.05)  # ensure mtime change is observable
        new_mtime = time.time() + 5
        os.utime(f, (new_mtime, new_mtime))

        out = search_symbols(
            repo=r["repo"],
            query="authenticate",
            storage_path=str(store),
        )
        markers = {item.get("_freshness") for item in out["results"]}
        assert "edited_uncommitted" in markers
        assert out["_meta"]["freshness"]["edited_uncommitted"] >= 1


class TestGetSymbolSourceCarriesFreshness:
    def test_get_symbol_source_returns_freshness(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.get_symbol import get_symbol_source

        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"
        store.mkdir()
        (src / "u.py").write_text("def util():\n    return 1\n")
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True

        out = get_symbol_source(
            symbol_id="u.py::util#function",
            repo=r["repo"],
            storage_path=str(store),
        )
        # single-mode flat shape — freshness goes in _meta.freshness summary
        assert "_meta" in out
        assert "freshness" in out["_meta"]
