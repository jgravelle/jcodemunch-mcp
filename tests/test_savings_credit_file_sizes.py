"""A search credits the tokens it saved on a fresh install's index (LEDGER L-302).

Six read tools credit savings against `index.file_sizes`, and only a save
handed the file contents stored a size. A full `index_folder` writes contents
to the content dir and hands the save none, so every file of a first index had
no size and `search_symbols` credited 0 tokens on every call. An install that
only searched never had a total to report.

Every case goes through `index_folder` and the tool, never a hand-built index.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools._utils import distinct_file_bytes
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols


def _body(i: int) -> str:
    return "".join(
        f"def func_{i}_{j}(x):\n    '''Doc {j}.'''\n    return x + {j}\n\n\n"
        for j in range(40)
    )


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(6):
        (src / f"mod{i}.py").write_text(_body(i), encoding="utf-8")
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    return res["repo"], str(store)


def _db(store: str):
    dbs = [
        p for p in Path(store).glob("*.db") if not p.name.startswith(("_", "telemetry"))
    ]
    assert len(dbs) == 1, dbs
    return dbs[0]


def _db_sizes(store: str, repo: str) -> dict:
    conn = sqlite3.connect(str(_db(store)))
    try:
        return dict(conn.execute("SELECT path, size_bytes FROM files").fetchall())
    finally:
        conn.close()


def test_a_full_index_stores_a_size_for_every_file(fresh):
    repo, store = fresh
    sizes = _db_sizes(store, repo)
    assert len(sizes) == 6
    assert all(isinstance(v, int) and v > 0 for v in sizes.values()), sizes


def test_a_search_on_a_fresh_index_credits_the_tokens_it_saved(fresh):
    repo, store = fresh
    r = search_symbols(repo, "func_3_7", storage_path=store)
    assert r["_meta"]["tokens_saved"] > 0


def test_an_index_stored_without_sizes_credits_without_a_reindex(fresh):
    """An index built before the fix: sizes are read from its content copy, and nothing is written."""
    repo, store = fresh
    owner, name = repo.split("/", 1)
    conn = sqlite3.connect(str(_db(store)))
    conn.execute("UPDATE files SET size_bytes = NULL")
    conn.commit()
    conn.close()
    from jcodemunch_mcp.storage import result_cache_invalidate
    from jcodemunch_mcp.tools import search_symbols as ss

    result_cache_invalidate()
    ss.result_cache_invalidate_repo(repo)
    idx = IndexStore(base_path=store).load_index(owner, name)
    assert dict.__len__(idx.file_sizes) == 0
    r = search_symbols(repo, "func_2_5", storage_path=store)
    assert r["_meta"]["tokens_saved"] > 0
    assert set(_db_sizes(store, repo).values()) == {None}


def test_a_missing_size_falls_back_only_inside_the_content_dir(tmp_path):
    from jcodemunch_mcp.storage.file_sizes import ContentSizes

    (tmp_path / "a.py").write_bytes(b"x = 1\n")
    sizes = ContentSizes({}, tmp_path)
    assert sizes.get("a.py") == 6
    assert sizes["a.py"] == 6
    assert sizes.get("../a.py", 0) == 0
    outside = tmp_path.parent / f"{tmp_path.name}_outside.py"
    outside.write_bytes(b"y" * 9)
    assert sizes.get(str(outside), 0) == 0
    assert sizes.get("missing.py", 0) == 0
    with pytest.raises(KeyError):
        sizes["missing.py"]


def test_a_stored_size_is_served_as_stored(tmp_path):
    from jcodemunch_mcp.storage.file_sizes import ContentSizes

    (tmp_path / "a.py").write_bytes(b"x = 1\n")
    assert ContentSizes({"a.py": 999}, tmp_path).get("a.py") == 999


def test_a_save_fills_sizes_from_the_content_dir(tmp_path):
    from jcodemunch_mcp.storage.file_sizes import sizes_from_content

    (tmp_path / "a.py").write_bytes(b"abc")
    out = sizes_from_content({"b.py": 5}, ["a.py", "b.py", "gone.py"], tmp_path)
    assert out == {"b.py": 5, "a.py": 3}


def test_distinct_file_bytes_reads_a_size_the_map_did_not_store(tmp_path):
    from jcodemunch_mcp.storage.file_sizes import ContentSizes

    (tmp_path / "a.py").write_bytes(b"abcd")
    sizes = ContentSizes({"b.py": 10}, tmp_path)
    assert (
        distinct_file_bytes(
            sizes, [{"file": "a.py"}, {"file": "b.py"}, {"file": "a.py"}]
        )
        == 14
    )


def test_a_hook_save_on_an_index_without_sizes_keeps_the_fallback(fresh, tmp_path):
    """The mtime-only save the PostToolUse hook makes must not turn the fallback back into a plain dict."""
    import os

    from jcodemunch_mcp.storage import result_cache_invalidate
    from jcodemunch_mcp.tools import search_symbols as ss
    from jcodemunch_mcp.tools.index_file import index_file

    repo, store = fresh
    conn = sqlite3.connect(str(_db(store)))
    conn.execute("UPDATE files SET size_bytes = NULL")
    conn.commit()
    conn.close()
    result_cache_invalidate()
    ss.result_cache_invalidate_repo(repo)
    owner, name = repo.split("/", 1)
    IndexStore(base_path=store).load_index(owner, name)

    touched = tmp_path / "proj" / "mod0.py"
    st = touched.stat()
    os.utime(touched, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    res = index_file(str(touched), use_ai_summaries=False, storage_path=store)
    assert res["success"] is True, res

    result_cache_invalidate()
    ss.result_cache_invalidate_repo(repo)
    r = search_symbols(repo, "func_4_9", storage_path=store)
    assert r["_meta"]["tokens_saved"] > 0


def test_a_patched_map_keeps_the_fallback(tmp_path):
    from jcodemunch_mcp.storage.file_sizes import ContentSizes, carry_fallback

    (tmp_path / "a.py").write_bytes(b"abcde")
    out = carry_fallback(ContentSizes({}, tmp_path), {"b.py": 1})
    assert out.get("a.py") == 5 and out.get("b.py") == 1
    assert type(carry_fallback({}, {"b.py": 1})) is dict


def test_a_missing_copy_is_stated_once(tmp_path, monkeypatch):
    from jcodemunch_mcp.storage import file_sizes as fs

    calls = []
    real = fs._content_size
    monkeypatch.setattr(
        fs, "_content_size", lambda *a, **k: calls.append(a[1]) or real(*a, **k)
    )
    sizes = fs.ContentSizes({}, tmp_path)
    for _ in range(5):
        assert sizes.get("missing.py", 0) == 0
    assert calls == ["missing.py"]


def test_a_key_that_leaves_the_content_dir_reads_nothing(tmp_path):
    """Confinement is resolve_within's, so a drive-relative or symlinked key is refused too."""
    from jcodemunch_mcp.storage.file_sizes import ContentSizes

    inside = tmp_path / "content"
    inside.mkdir()
    (tmp_path / "secret.py").write_bytes(b"x" * 50)
    sizes = ContentSizes({}, inside)
    assert sizes.get("../secret.py", 0) == 0
    drive = str(tmp_path)[:2]
    if drive[1:] == ":":
        assert sizes.get(drive + "secret.py", 0) == 0
    try:
        (inside / "link.py").symlink_to(tmp_path / "secret.py")
    except (OSError, NotImplementedError):
        return
    assert sizes.get("link.py", 0) == 0
