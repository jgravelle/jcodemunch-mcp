"""LEDGER L-137: the read-only URI names the file the path names.

`storage.generation.readonly_uri` built `file:{path}?mode=ro` from the raw
path. SQLite reads that string as a URI: `#` starts a fragment, `?` starts the
query, and `%HH` is an escape. So under a storage path holding one of them the
read-only open named ANOTHER file. With `#` the query string fell into the
fragment, SQLite opened the path up to the `#` read-WRITE and created an empty
file there, and the read failed on a missing table; every reader built on
`connect_readonly` then answered as if nothing were stored (`has_any()` False
over a store holding vectors).

The path is escaped where the URI is built, the one place every read-only
opener in the tree goes through.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import generation as gen
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

NL = chr(10)

#: Directory names, each holding a character a URI reads specially, plus two
#: ordinary ones that must keep working. `?` cannot be in a Windows file name.
NAMES = [
    "plain",
    "sp ace",
    "c#proj",
    "a%20b",
    "pct%41",
    "100%",
    "amp&eq=1",
    "h#a%23b",
    "caf" + chr(233),
] + ([] if sys.platform == "win32" else ["what?now", "q?mode=rwc"])


def _make_db(directory: Path, wal_open: bool):
    """A database with one row; with `wal_open` a writer stays open so the
    sidecars exist and the row is un-checkpointed."""
    directory.mkdir()
    db = directory / "index.db"
    w = sqlite3.connect(str(db), isolation_level=None)
    w.execute("PRAGMA journal_mode = WAL")
    w.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
    w.execute("INSERT INTO t VALUES ('live')")
    if wal_open:
        return db, w
    w.close()
    return db, None


def _tree(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize("wal_open", [False, True], ids=["no_sidecars", "open_wal"])
@pytest.mark.parametrize("name", NAMES)
def test_the_read_only_open_reads_the_file_the_path_names(tmp_path, name, wal_open):
    db, writer = _make_db(tmp_path / name, wal_open)
    try:
        before = _tree(tmp_path)
        conn = gen.connect_readonly(db)
        try:
            assert conn.execute("SELECT k FROM t").fetchall() == [("live",)]
        finally:
            conn.close()
        # Nothing created beside it: with `#` the open made an empty file at
        # the path cut short.
        assert _tree(tmp_path) == before
    finally:
        if writer is not None:
            writer.close()


@pytest.mark.parametrize("name", NAMES)
def test_the_connection_is_read_only(tmp_path, name):
    """With the query in the fragment the open was read-WRITE."""
    db, _ = _make_db(tmp_path / name, False)
    conn = gen.connect_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO t VALUES ('written')")
    finally:
        conn.close()
    check = sqlite3.connect(str(db))
    try:
        assert check.execute("SELECT COUNT(*) FROM t").fetchone() == (1,)
    finally:
        check.close()


@pytest.mark.parametrize("name", NAMES)
def test_the_immutable_fallback_names_the_same_file(tmp_path, name, monkeypatch):
    """`connect_readonly` retries immutably when the WAL-reading open fails."""
    db, writer = _make_db(tmp_path / name, True)
    try:
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        real = sqlite3.connect
        seen = []

        def first_open_fails(target, *args, **kwargs):
            seen.append(target)
            if len(seen) == 1:
                raise sqlite3.OperationalError("unable to open database file")
            return real(target, *args, **kwargs)

        monkeypatch.setattr(gen.sqlite3, "connect", first_open_fails)
        conn = gen.connect_readonly(db)
        try:
            assert conn.execute("SELECT k FROM t").fetchall() == [("live",)]
        finally:
            conn.close()
        assert len(seen) == 2 and seen[1].endswith("mode=ro&immutable=1")
    finally:
        writer.close()


def test_a_relative_path_still_opens(tmp_path, monkeypatch):
    _make_db(tmp_path / "c#proj", False)
    monkeypatch.chdir(tmp_path)
    conn = gen.connect_readonly(os.path.join("c#proj", "index.db"))
    try:
        assert conn.execute("SELECT k FROM t").fetchall() == [("live",)]
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["c#proj", "a%20b"])
def test_the_readers_see_what_is_stored_under_such_a_path(tmp_path, name, monkeypatch):
    """At the tools: the two directory names the ledger row measured."""
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        (src / f"m{i}.py").write_text(f"def handler_{i}():{NL}    return {i}{NL}")
    store_dir = tmp_path / name
    store_dir.mkdir()
    indexed = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
    assert indexed["success"] is True, indexed
    owner, repo_name = indexed["repo"].split("/", 1)
    store = IndexStore(base_path=str(store_dir))
    db_path = store._sqlite._db_path(owner, repo_name)

    embeddings = EmbeddingStore(db_path)
    embeddings.set_dimension(4, "model-a")
    embeddings.set_task_type("")
    embeddings.set_many({"sym-%d" % i: [0.1] * 4 for i in range(3)})

    before = _tree(tmp_path)
    assert embeddings.has_any() is True
    assert len(embeddings.iter_raw()) == 3
    assert embeddings.read_meta() == {
        "has_vectors": True, "dimension": 4, "model": "model-a", "task_type": "",
    }
    assert store._sqlite.list_source_roots() == [str(src)]

    found = search_symbols(repo=indexed["repo"], query="handler_1", storage_path=str(store_dir))
    assert [row["name"] for row in found["results"]][:1] == ["handler_1"], found

    stray = [p for p in _tree(tmp_path) if p not in before and not p.endswith(("-wal", "-shm"))]
    assert stray == [], stray
