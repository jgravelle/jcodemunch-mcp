"""A substring search opens only the files whose trigram bitmap allows a match.

A cold `search_text` opened and read every cached file in scope, so on a few
thousand files it took seconds, too slow to answer a Grep from a hook. Each
cached file now has a bitmap of its lowered text's trigrams, written when the
index writes the file. A file whose current bitmap lacks a trigram of the
query is counted as searched without being opened. A bitmap may say "maybe"
wrongly, never "no": a file whose stat no longer matches its row, or that has
no row, is read, and the read repairs its row.
"""

from __future__ import annotations

import builtins
import os
import random
import string

import pytest

from jcodemunch_mcp.storage import text_filter as tf
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_text import search_text


def test_a_bitmap_never_rules_out_text_it_holds():
    rng = random.Random(7)
    alphabet = string.ascii_letters + string.digits + "_ .(){}:=\u00e9\u03a3"
    for _ in range(200):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 400)))
        nbits, bits = tf.build(text)
        lowered = text.lower()
        for _ in range(10):
            # search_text's own test is `query.lower() in line.lower()`, so
            # any query it matches is a substring of the lowered text. (Not
            # the same as lowering a substring: a final sigma lowers by
            # context.)
            i = rng.randint(0, len(lowered) - 3)
            j = rng.randint(i + 3, len(lowered))
            grams = tf.query_grams(lowered[i:j])
            assert tf.may_contain(nbits, bits, grams), (text, lowered[i:j])


def test_a_short_or_multiline_query_has_no_filter():
    assert tf.query_grams("ab") is None
    assert tf.query_grams(None) is None
    assert tf.query_grams("a\nbc") is None


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(30):
        (src / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    (src / "target.py").write_text(
        "def needle_function():\n    return 'Needle'\n", encoding="utf-8"
    )
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"]
    return src, res["repo"], str(store)


def _opened(monkeypatch):
    seen = []
    real = builtins.open

    def spy(path, *a, **kw):
        seen.append(os.path.basename(str(path)))
        return real(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    return seen


def test_indexing_writes_a_row_per_cached_file(repo):
    _, rid, store = repo
    from jcodemunch_mcp.storage import IndexStore

    owner, name = rid.split("/", 1)
    rows = tf.load(IndexStore(base_path=store)._content_dir(owner, name))
    assert "target.py" in rows and len(rows) == 31


def test_a_search_opens_only_the_files_that_can_match(repo, monkeypatch):
    _, rid, store = repo
    seen = _opened(monkeypatch)
    res = search_text(rid, "needle_func", storage_path=store)
    assert [r["file"] for r in res["results"]] == ["target.py"]
    assert "m3.py" not in seen
    assert "target.py" in seen


def test_case_is_ignored_through_the_filter(repo):
    _, rid, store = repo
    res = search_text(rid, "NEEDLE_FUNCTION", storage_path=store)
    assert [r["file"] for r in res["results"]] == ["target.py"]


def test_a_cached_file_changed_after_indexing_is_read_not_trusted(repo, monkeypatch):
    _, rid, store = repo
    from jcodemunch_mcp.storage import IndexStore

    owner, name = rid.split("/", 1)
    cdir = IndexStore(base_path=store)._content_dir(owner, name)
    cached = cdir / "m3.py"
    cached.write_text("def f3():\n    return 'brand_new_text'\n", encoding="utf-8")
    os.utime(cached, ns=(1, 1))  # a stamp no row holds
    res = search_text(rid, "brand_new_text", storage_path=store)
    assert [r["file"] for r in res["results"]] == ["m3.py"]


def test_a_file_with_no_row_is_read_and_its_row_repaired(repo):
    _, rid, store = repo
    from jcodemunch_mcp.storage import IndexStore

    owner, name = rid.split("/", 1)
    cdir = IndexStore(base_path=store)._content_dir(owner, name)
    tf.forget(cdir, ["target.py"])
    assert "target.py" not in tf.load(cdir)
    res = search_text(rid, "needle_function", storage_path=store)
    assert [r["file"] for r in res["results"]] == ["target.py"]
    assert "target.py" in tf.load(cdir)


def test_without_a_sidecar_the_search_answers_the_same(repo):
    _, rid, store = repo
    from jcodemunch_mcp.storage import IndexStore

    owner, name = rid.split("/", 1)
    cdir = IndexStore(base_path=store)._content_dir(owner, name)
    with_filter = search_text(rid, "return", storage_path=store)
    tf.remove(cdir)
    without = search_text(rid, "return", storage_path=store)
    assert with_filter["results"] == without["results"]
    assert with_filter["result_count"] == without["result_count"]


def test_a_path_that_could_leave_the_content_dir_is_never_ruled_out(tmp_path):
    assert tf.content_stat(tmp_path, "../x.py") is None
    assert tf.content_stat(tmp_path, "/etc/passwd") is None
    assert tf.content_stat(tmp_path, "C:/x.py") is None


def test_deleting_the_index_deletes_the_sidecar(repo):
    _, rid, store = repo
    from jcodemunch_mcp.storage import IndexStore

    owner, name = rid.split("/", 1)
    st = IndexStore(base_path=store)
    side = tf.sidecar_path(st._content_dir(owner, name))
    assert side.exists()
    st.delete_index(owner, name)
    assert not side.exists()


def test_metadata_only_writes_no_row_for_text_it_did_not_cache(tmp_path, monkeypatch):
    """Under cache_mode metadata_only the body is not written, so an older
    cached file may remain. A row built from the new text and stamped with the
    old file's stat would let a search skip a match the old file holds."""
    from jcodemunch_mcp import config
    from jcodemunch_mcp.storage import text_filter

    old = tmp_path / "a.py"
    old.write_text("needle_old = 1\n", encoding="utf-8")
    real_get = config.get
    monkeypatch.setattr(
        config, "get",
        lambda key, *a, **kw: "metadata_only" if key == "cache_mode" else real_get(key, *a, **kw),
    )
    assert text_filter.row_for(tmp_path, "a.py", "something_else = 2\n") is None
