"""The Grep answer's cold cost must not be one filesystem touch per file in scope.

After a long idle gap, the first Grep answer on a Home Assistant index took
7.2 s against its 4 s budget, so it did not fire. Two stages made one ``stat``
per file in the Grep's scope (14,432 files): the freshness check on the source
tree, and the trigram filter checking that each cached copy's row is current.
On Windows a directory listing carries every entry's size and mtime, so one
listing per folder gives the same values for a fraction of the cold cost.
And opening the index decoded every file's imports JSON, which a text search
never reads.

Exactness is the point of each test pair below: the cheaper route must give
the values the old one gave, and anything it cannot see must still be
measured the ordinary way, never assumed unchanged.
"""

from __future__ import annotations

import os
import time

import pytest

from jcodemunch_mcp.cli.hooks import grep_answer
from jcodemunch_mcp.retrieval.freshness import FreshnessProbe
from jcodemunch_mcp.storage import listing_stat, sqlite_store, text_filter
from jcodemunch_mcp.storage.selective import SelectiveIndexView


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    proj = tmp_path / "proj"
    (proj / "inside" / "deep").mkdir(parents=True)
    (proj / "inside" / "a.py").write_text(
        "import json\n\ndef a():\n    return json.dumps('beacon')\n", encoding="utf-8"
    )
    (proj / "inside" / "deep" / "c.py").write_text(
        "from inside import a\n\ndef c():\n    return 'nothing here'\n",
        encoding="utf-8",
    )
    (proj / "outside.py").write_text(
        "def b():\n    return 'beacon'\n", encoding="utf-8"
    )
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    assert index_folder(str(proj), use_ai_summaries=False)["success"]
    return resolve_repo(str(proj))["repo"], proj


def _open(repo_id):
    from jcodemunch_mcp.storage import IndexStore

    sqlite_store._cache_clear()
    owner, _, name = repo_id.partition("/")
    view = IndexStore().open_selective(owner, name)
    assert isinstance(view, SelectiveIndexView)
    return view


def _full_imports(repo_id):
    from jcodemunch_mcp.storage import IndexStore

    sqlite_store._cache_clear()
    owner, _, name = repo_id.partition("/")
    return IndexStore().load_index(owner, name).imports


# -- opening a view ----------------------------------------------------------


def test_opening_a_view_decodes_no_imports(repo, monkeypatch):
    name, _ = repo
    calls = []
    real = sqlite_store.decode_imports
    monkeypatch.setattr(
        sqlite_store, "decode_imports", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    view = _open(name)
    assert calls == []
    assert view.source_files  # the rest of the view is there
    view.imports
    assert calls == [1]


def test_a_views_imports_equal_the_full_loads_and_do_not_promote(repo):
    name, _ = repo
    expected = _full_imports(name)
    assert expected  # the fixture has import edges, so equality means something
    view = _open(name)
    assert view.imports == expected
    assert view.imports is view.imports  # decoded once
    assert view._promotions == 0


def test_a_racket_index_decodes_its_imports_when_built():
    """``CodeIndex.__post_init__`` adds Racket collection edges to the decoded
    imports, so a Racket index is never deferred."""
    store = sqlite_store.SQLiteIndexStore.__new__(sqlite_store.SQLiteIndexStore)
    meta = {"repo": "o/n", "index_version": "20", "languages": '{"racket": 1}'}
    deferred: list = []
    store._content_dir_or_none = lambda owner, name: None
    store._build_index_from_rows(meta, [], [], "o", "n", defer_imports_into=deferred)
    assert deferred == []


def test_decode_imports_keeps_the_old_empty_and_corrupt_rules():
    assert sqlite_store.decode_imports({}, 3) is None
    assert sqlite_store.decode_imports({}, 4) == {}
    assert sqlite_store.decode_imports({"a.py": "[]", "b.py": "{bad"}, 20) == {}
    assert sqlite_store.decode_imports({"a.py": '[{"specifier": "x"}]'}, 20) == {
        "a.py": [{"specifier": "x"}]
    }


# -- stats from a listing ----------------------------------------------------


def test_a_listing_gives_the_stats_os_stat_gives(tmp_path):
    (tmp_path / "d").mkdir()
    for rel in ("top.py", "d/x.py", "d/y.txt"):
        (tmp_path / rel).write_text(rel, encoding="utf-8")
    ls = listing_stat.ListingStat(tmp_path)
    for rel in ("top.py", "d/x.py", "d/y.txt"):
        st, real = ls.stat(rel), os.stat(tmp_path / rel)
        assert (st.st_size, st.st_mtime_ns, st.st_mtime) == (
            real.st_size,
            real.st_mtime_ns,
            real.st_mtime,
        )


@pytest.mark.parametrize(
    "rel", ["missing.py", "d/missing.py", "nodir/x.py", "../x.py", "", "C:/x.py", "d"]
)
def test_a_listing_answers_none_for_what_it_does_not_hold(tmp_path, rel):
    (tmp_path / "d").mkdir()
    (tmp_path / "x.py").write_text("x", encoding="utf-8")
    assert listing_stat.ListingStat(tmp_path).stat(rel) is None


def test_the_trigram_filter_lists_folders_instead_of_stat_ing_files(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(listing_stat, "ENABLED", True)
    content = tmp_path / "content"
    files = {
        f"pkg{i}/m{j}.py": f"value_{i}_{j} = {i * j}\n"
        for i in range(3)
        for j in range(4)
    }
    for rel, text in files.items():
        (content / rel).parent.mkdir(parents=True, exist_ok=True)
        (content / rel).write_text(text, encoding="utf-8")
    text_filter.record(content, files.items())

    f = text_filter.Filter(content, "value_1_2")  # loads the rows: not per file
    stats = []
    real_stat = os.stat
    monkeypatch.setattr(
        text_filter.os, "stat", lambda *a, **k: stats.append(a) or real_stat(*a, **k)
    )
    verdicts = {rel: f.rules_out(rel)[0] for rel in files}
    assert stats == []
    assert verdicts["pkg1/m2.py"] is False  # the one file that holds it is read
    assert sum(verdicts.values()) >= len(files) - 3  # the rest are ruled out


def test_a_cached_copy_changed_after_its_row_is_still_read(tmp_path, monkeypatch):
    monkeypatch.setattr(listing_stat, "ENABLED", True)
    content = tmp_path / "content"
    (content / "p").mkdir(parents=True)
    target = content / "p" / "m.py"
    target.write_text("nothing to see\n", encoding="utf-8")
    text_filter.record(content, [("p/m.py", "nothing to see\n")])
    target.write_text("now holds the needle_word\n", encoding="utf-8")
    later = time.time() + 120
    os.utime(target, (later, later))
    assert text_filter.Filter(content, "needle_word").rules_out("p/m.py")[0] is False


# -- freshness from a listing ------------------------------------------------


def test_priming_caches_the_mtimes_stat_gives_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr(listing_stat, "ENABLED", True)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.py").write_text("x", encoding="utf-8")
    probe = FreshnessProbe(
        source_root=str(tmp_path), indexed_at="", index_sha=None, file_mtimes={}
    )
    probe.prime_mtimes(["d/x.py", "d/gone.py"])
    assert probe._mtime_cache == {"d/x.py": os.stat(tmp_path / "d" / "x.py").st_mtime}


def test_priming_is_a_no_op_where_listings_carry_no_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(listing_stat, "ENABLED", False)
    (tmp_path / "x.py").write_text("x", encoding="utf-8")
    probe = FreshnessProbe(
        source_root=str(tmp_path), indexed_at="", index_sha=None, file_mtimes={}
    )
    probe.prime_mtimes(["x.py"])
    assert probe._mtime_cache == {}


def test_the_freshness_check_lists_folders_and_still_sees_an_edit(repo, monkeypatch):
    monkeypatch.setattr(listing_stat, "ENABLED", True)
    name, proj = repo
    from pathlib import Path

    stats = []
    real = Path.stat
    monkeypatch.setattr(
        Path, "stat", lambda self, *a, **k: stats.append(self) or real(self, *a, **k)
    )
    assert grep_answer._index_matches_disk(name, "inside/*") is True
    assert [p for p in stats if p.name.endswith(".py")] == []

    later = time.time() + 120
    os.utime(proj / "inside" / "deep" / "c.py", (later, later))
    assert grep_answer._index_matches_disk(name, "inside/*") is False


def test_a_file_the_listing_cannot_see_still_refuses_the_answer(repo, monkeypatch):
    monkeypatch.setattr(listing_stat, "ENABLED", True)
    name, proj = repo
    (proj / "inside" / "a.py").unlink()
    assert grep_answer._index_matches_disk(name, "inside/*") is False
