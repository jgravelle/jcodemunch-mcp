"""Tests for the SQLite WAL storage backend."""

import sqlite3
import shutil
from pathlib import Path

from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.parser.symbols import Symbol


def _make_symbol(name: str, file: str = "main.py", kind: str = "function") -> Symbol:
    """Helper to create a test symbol."""
    return Symbol(
        id=f"{file}::{name}#{kind}",
        file=file,
        name=name,
        qualified_name=name,
        kind=kind,
        language="python",
        signature=f"def {name}()",
        line=1,
        end_line=3,
        byte_offset=0,
        byte_length=20,
    )


def test_connect_creates_schema(tmp_path):
    """_connect creates tables and sets WAL pragmas."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db_path = tmp_path / "test.db"
    conn = store._connect(db_path)

    # Check WAL mode
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"

    # Check tables exist
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert tables >= {"meta", "symbols", "files"}

    conn.close()


def test_repo_slug(tmp_path):
    """_repo_slug produces a safe filesystem slug."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    slug = store._repo_slug("local", "my-project-abc123")
    assert "/" not in slug
    assert "\\" not in slug
    assert ".." not in slug


def test_db_path(tmp_path):
    """_db_path returns {base_path}/{slug}.db."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    path = store._db_path("local", "test-abc123")
    assert path.suffix == ".db"
    assert path.parent == tmp_path


def test_save_and_load_index(tmp_path):
    """Full save → load round-trip preserves all data."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    sym = _make_symbol("greet")

    index = store.save_index(
        owner="local",
        name="test-abc123",
        source_files=["main.py"],
        symbols=[sym],
        raw_files={"main.py": "def greet(): pass"},
        file_hashes={"main.py": "abc123"},
        git_head="deadbeef",
        file_summaries={"main.py": "Entry point"},
        source_root="/tmp/proj",
        file_languages={"main.py": "python"},
        display_name="test",
        imports={"main.py": [{"specifier": "os", "names": ["path"]}]},
        file_mtimes={"main.py": 1234567890000000000},
    )

    assert index is not None
    assert index.repo == "local/test-abc123"
    assert len(index.symbols) == 1

    # Load it back
    loaded = store.load_index("local", "test-abc123")
    assert loaded is not None
    assert loaded.repo == "local/test-abc123"
    assert len(loaded.symbols) == 1
    assert loaded.symbols[0]["name"] == "greet"
    assert loaded.file_hashes == {"main.py": "abc123"}
    assert loaded.git_head == "deadbeef"
    assert loaded.file_summaries == {"main.py": "Entry point"}
    assert loaded.source_root == "/tmp/proj"
    assert loaded.display_name == "test"
    assert loaded.file_mtimes == {"main.py": 1234567890000000000}
    assert loaded.imports == {"main.py": [{"specifier": "os", "names": ["path"]}]}


def test_incremental_save(tmp_path):
    """Incremental save adds/removes symbols correctly."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    sym1 = _make_symbol("greet", "main.py")
    sym2 = _make_symbol("helper", "utils.py")

    # Full save with 2 files
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["main.py", "utils.py"],
        symbols=[sym1, sym2],
        raw_files={"main.py": "def greet(): pass", "utils.py": "def helper(): pass"},
        file_hashes={"main.py": "h1", "utils.py": "h2"},
        file_mtimes={"main.py": 100, "utils.py": 200},
    )

    # Incremental: change main.py, delete utils.py
    new_sym = _make_symbol("greet_v2", "main.py")
    updated = store.incremental_save(
        owner="local", name="test-abc123",
        changed_files=["main.py"],
        new_files=[],
        deleted_files=["utils.py"],
        new_symbols=[new_sym],
        raw_files={"main.py": "def greet_v2(): pass"},
        file_hashes={"main.py": "h1_new"},
        file_mtimes={"main.py": 300},
    )

    assert updated is not None
    assert len(updated.symbols) == 1
    assert updated.symbols[0]["name"] == "greet_v2"
    assert "utils.py" not in updated.file_hashes
    assert updated.file_hashes["main.py"] == "h1_new"


def test_detect_changes_with_mtimes(tmp_path):
    """Detects changed, new, and deleted files by mtime + hash."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["a.py", "b.py"],
        symbols=[_make_symbol("f", "a.py"), _make_symbol("g", "b.py")],
        raw_files={"a.py": "x", "b.py": "y"},
        file_hashes={"a.py": "ha", "b.py": "hb"},
        file_mtimes={"a.py": 100, "b.py": 200},
    )

    # a.py: same mtime (unchanged), b.py: different mtime + different hash (changed), c.py: new
    changed, new, deleted, hashes, mtimes = store.detect_changes_with_mtimes(
        "local", "test-abc123",
        current_mtimes={"a.py": 100, "b.py": 999, "c.py": 300},
        hash_fn=lambda fp: {"b.py": "hb_new", "c.py": "hc"}.get(fp, ""),
    )
    assert changed == ["b.py"]
    assert new == ["c.py"]
    assert deleted == []


def test_delete_index(tmp_path):
    """delete_index removes .db and content dir."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["a.py"], symbols=[], raw_files={"a.py": "x"},
    )
    assert store.has_index("local", "test-abc123")
    assert store.delete_index("local", "test-abc123")
    assert not store.has_index("local", "test-abc123")


def test_delete_and_recreate_same_process(tmp_path):
    """delete_index clears _initialized_dbs so a recreate succeeds in the same process.

    Without the discard, _connect skips the table_info check and the second save
    fails with 'no such table' because the fresh DB was never schema-initialised.
    """
    store = SQLiteIndexStore(base_path=str(tmp_path))

    store.save_index(
        owner="local", name="test-abc123",
        source_files=["a.py"], symbols=[], raw_files={"a.py": "x"},
    )
    assert store.has_index("local", "test-abc123")

    store.delete_index("local", "test-abc123")
    assert not store.has_index("local", "test-abc123")

    # Recreating the same repo must succeed — this would raise
    # sqlite3.OperationalError: no such table: symbols
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["b.py"], symbols=[], raw_files={"b.py": "y"},
    )
    assert store.has_index("local", "test-abc123")

    index = store.load_index("local", "test-abc123")
    assert index is not None
    assert index.source_files == ["b.py"]


def test_list_repos(tmp_path):
    """list_repos finds all indexed repos."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="proj-a",
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "x"}, display_name="Project A",
    )
    repos = store.list_repos()
    assert len(repos) >= 1
    assert any(r["repo"] == "local/proj-a" for r in repos)


def test_get_symbol_content(tmp_path):
    """get_symbol_content reads by byte offset from content cache."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    content = "def greet():\n    print('hello')\n"
    sym = Symbol(
        id="main.py::greet#function", file="main.py", name="greet",
        qualified_name="greet", kind="function", language="python",
        signature="def greet()", line=1, end_line=2,
        byte_offset=0, byte_length=len(content.encode("utf-8")),
    )
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["main.py"], symbols=[sym],
        raw_files={"main.py": content},
    )
    result = store.get_symbol_content("local", "test-abc123", "main.py::greet#function")
    assert result == content


def test_get_file_content(tmp_path):
    """get_file_content reads full file from content cache."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    content = "def greet(): pass\n"
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["main.py"], symbols=[_make_symbol("greet")],
        raw_files={"main.py": content},
    )
    result = store.get_file_content("local", "test-abc123", "main.py")
    assert result == content


def test_has_index_false_before_save(tmp_path):
    """has_index returns False when no index exists."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    assert not store.has_index("local", "nonexistent-abc123")


def test_detect_changes_from_hashes(tmp_path):
    """Detects changes using precomputed hashes."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["a.py", "b.py"],
        symbols=[], raw_files={"a.py": "x", "b.py": "y"},
        file_hashes={"a.py": "ha", "b.py": "hb"},
    )
    changed, new, deleted = store.detect_changes_from_hashes(
        "local", "test-abc123",
        current_hashes={"a.py": "ha", "b.py": "hb_new", "c.py": "hc"},
    )
    assert changed == ["b.py"]
    assert new == ["c.py"]
    assert deleted == []


def test_get_symbol_by_id(tmp_path):
    """get_symbol_by_id returns one symbol without loading full index."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["main.py"],
        symbols=[_make_symbol("greet"), _make_symbol("farewell")],
        raw_files={"main.py": "x"},
    )
    sym = store.get_symbol_by_id("local", "test-abc123", "main.py::greet#function")
    assert sym is not None
    assert sym["name"] == "greet"

    missing = store.get_symbol_by_id("local", "test-abc123", "main.py::nonexistent#function")
    assert missing is None


def test_has_file(tmp_path):
    """has_file checks file existence without loading full index."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["main.py"], symbols=[], raw_files={"main.py": "x"},
    )
    assert store.has_file("local", "test-abc123", "main.py")
    assert not store.has_file("local", "test-abc123", "missing.py")


def test_get_file_languages(tmp_path):
    """get_file_languages returns path→language without loading full index."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="test-abc123",
        source_files=["a.py", "b.js"], symbols=[],
        raw_files={"a.py": "x", "b.js": "y"},
        file_languages={"a.py": "python", "b.js": "javascript"},
    )
    fl = store.get_file_languages("local", "test-abc123")
    assert fl == {"a.py": "python", "b.js": "javascript"}


def test_load_index_cache_hit(tmp_path):
    """Second load_index call returns cached result without DB access."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="cache-test",
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "x"},
    )
    # First load — cold cache
    idx1 = store.load_index("local", "cache-test")
    assert idx1 is not None

    # Second load — should be cache hit (same object)
    idx2 = store.load_index("local", "cache-test")
    assert idx2 is idx1  # exact same object from cache


def test_load_index_cache_invalidated_on_save(tmp_path):
    """save_index invalidates the cache by updating mtime and pre-warming."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="cache-test",
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "x"},
    )
    idx1 = store.load_index("local", "cache-test")
    assert len(idx1.symbols) == 1

    # Re-save with different symbols
    import time; time.sleep(0.01)  # ensure mtime changes
    store.save_index(
        owner="local", name="cache-test",
        source_files=["a.py"], symbols=[_make_symbol("f"), _make_symbol("g")],
        raw_files={"a.py": "x"},
    )
    idx2 = store.load_index("local", "cache-test")
    assert len(idx2.symbols) == 2
    assert idx2 is not idx1


def test_load_index_cache_cleared_on_delete(tmp_path):
    """delete_index evicts from cache."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="cache-test",
        source_files=["a.py"], symbols=[], raw_files={"a.py": "x"},
    )
    idx1 = store.load_index("local", "cache-test")
    assert idx1 is not None

    store.delete_index("local", "cache-test")
    idx2 = store.load_index("local", "cache-test")
    assert idx2 is None


def test_cache_key_uses_safe_name_after_save(tmp_path):
    """save_index pre-warms cache with safe_name so load_index hits it.

    Regression: save_index cached with raw name ("my project (v2)") but
    load_index looked up with safe_name ("my-project-v2"), causing permanent
    cache misses (~110ms per tool call instead of <0.1ms).
    """
    store = SQLiteIndexStore(base_path=str(tmp_path))
    raw_name = "my project (v2)"

    store.save_index(
        owner="local", name=raw_name,
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "x"},
    )

    idx1 = store.load_index("local", raw_name)
    assert idx1 is not None

    idx2 = store.load_index("local", raw_name)
    assert idx2 is idx1, "Cache miss: load_index did not find the pre-warmed entry from save_index"


def test_cache_key_uses_safe_name_after_incremental_save(tmp_path):
    """incremental_save pre-warms cache with safe_name so load_index hits it.

    Regression: same mismatch as test_cache_key_uses_safe_name_after_save but
    for the incremental (file-watcher) code path.
    """
    import time

    store = SQLiteIndexStore(base_path=str(tmp_path))
    raw_name = "my project (v2)"

    store.save_index(
        owner="local", name=raw_name,
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "x"},
    )

    time.sleep(0.01)  # ensure mtime changes
    store.incremental_save(
        owner="local", name=raw_name,
        changed_files=[], new_files=["b.py"], deleted_files=[],
        new_symbols=[_make_symbol("g", file="b.py")],
        raw_files={"b.py": "y"},
    )

    idx1 = store.load_index("local", raw_name)
    assert idx1 is not None
    assert len(idx1.symbols) == 2

    idx2 = store.load_index("local", raw_name)
    assert idx2 is idx1, "Cache miss: load_index did not find the pre-warmed entry from incremental_save"


def test_cache_evict_uses_safe_name_on_delete(tmp_path):
    """delete_index evicts cache using safe_name so stale entries don't persist.

    Regression: delete_index evicted with raw name but cache was stored with
    safe_name, leaving a stale entry that could be served after deletion.
    """
    store = SQLiteIndexStore(base_path=str(tmp_path))
    raw_name = "my project (v2)"

    store.save_index(
        owner="local", name=raw_name,
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "x"},
    )

    idx1 = store.load_index("local", raw_name)
    assert idx1 is not None

    store.delete_index("local", raw_name)
    idx2 = store.load_index("local", raw_name)
    assert idx2 is None, "Stale cache entry survived delete_index"


def _make_symbol_with_hash(name, file="main.py", content_hash=""):
    """Helper to create a test symbol with content_hash."""
    return Symbol(
        id=f"{file}::{name}#function", file=file, name=name,
        qualified_name=name, kind="function", language="python",
        signature=f"def {name}()", line=1, end_line=3,
        byte_offset=0, byte_length=20, content_hash=content_hash,
    )


def test_incremental_save_carries_forward_token_bags(tmp_path):
    """incremental_save preserves _tokens on unchanged symbols.

    After a search populates _tokens on symbol dicts, an incremental_save
    that doesn't change those symbols should carry the token bags forward
    to the new CodeIndex, avoiding re-tokenization on the next search.
    Requires content_hash on both sides to verify content identity.
    """
    import time

    store = SQLiteIndexStore(base_path=str(tmp_path))
    sym_a = _make_symbol_with_hash("func_a", file="a.py", content_hash="hash_a")
    sym_b = _make_symbol_with_hash("func_b", file="b.py", content_hash="hash_b")

    store.save_index(
        owner="local", name="token-carry",
        source_files=["a.py", "b.py"],
        symbols=[sym_a, sym_b],
        raw_files={"a.py": "x", "b.py": "y"},
    )

    # Load and simulate token caching (as search_symbols would do)
    idx1 = store.load_index("local", "token-carry")
    for s in idx1.symbols:
        s["_tokens"] = ["fake", "tokens", s["name"]]

    # Incremental save — adds c.py but doesn't change a.py or b.py
    time.sleep(0.01)
    sym_c = _make_symbol_with_hash("func_c", file="c.py", content_hash="hash_c")
    store.incremental_save(
        owner="local", name="token-carry",
        changed_files=[], new_files=["c.py"], deleted_files=[],
        new_symbols=[sym_c],
        raw_files={"c.py": "z"},
    )

    idx2 = store.load_index("local", "token-carry")
    assert len(idx2.symbols) == 3

    # Unchanged symbols should have _tokens carried forward
    carried = {s["name"]: s.get("_tokens") for s in idx2.symbols}
    assert carried["func_a"] == ["fake", "tokens", "func_a"], "func_a tokens not carried"
    assert carried["func_b"] == ["fake", "tokens", "func_b"], "func_b tokens not carried"
    # New symbol should NOT have _tokens (no carryforward source)
    assert carried["func_c"] is None, "func_c should not have carried tokens"


def test_no_carryforward_without_content_hash(tmp_path):
    """Tokens are NOT carried forward when content_hash is missing."""
    import time

    store = SQLiteIndexStore(base_path=str(tmp_path))
    # No content_hash — carryforward should be skipped
    sym_a = _make_symbol("func_a", file="a.py")

    store.save_index(
        owner="local", name="no-hash",
        source_files=["a.py"], symbols=[sym_a],
        raw_files={"a.py": "x"},
    )

    idx1 = store.load_index("local", "no-hash")
    idx1.symbols[0]["_tokens"] = ["should", "not", "carry"]

    time.sleep(0.01)
    store.incremental_save(
        owner="local", name="no-hash",
        changed_files=[], new_files=[], deleted_files=[],
        new_symbols=[], raw_files={},
    )

    idx2 = store.load_index("local", "no-hash")
    assert idx2.symbols[0].get("_tokens") is None, \
        "Tokens carried forward without content_hash — unsafe"


def test_token_carryforward_skips_changed_symbols(tmp_path):
    """Tokens are NOT carried forward when content_hash changes."""
    import time

    store = SQLiteIndexStore(base_path=str(tmp_path))
    sym_a = Symbol(
        id="a.py::func_a#function", file="a.py", name="func_a",
        qualified_name="func_a", kind="function", language="python",
        signature="def func_a()", line=1, end_line=3,
        byte_offset=0, byte_length=20, content_hash="hash_v1",
    )

    store.save_index(
        owner="local", name="token-change",
        source_files=["a.py"], symbols=[sym_a],
        raw_files={"a.py": "x"},
    )

    idx1 = store.load_index("local", "token-change")
    idx1.symbols[0]["_tokens"] = ["old", "stale", "tokens"]

    # Incremental save with same symbol id but different content_hash
    time.sleep(0.01)
    sym_a_v2 = Symbol(
        id="a.py::func_a#function", file="a.py", name="func_a",
        qualified_name="func_a", kind="function", language="python",
        signature="def func_a(x)", line=1, end_line=5,
        byte_offset=0, byte_length=30, content_hash="hash_v2",
    )
    store.incremental_save(
        owner="local", name="token-change",
        changed_files=["a.py"], new_files=[], deleted_files=[],
        new_symbols=[sym_a_v2],
        raw_files={"a.py": "x_modified"},
    )

    idx2 = store.load_index("local", "token-change")
    assert idx2.symbols[0].get("_tokens") is None, \
        "Stale tokens carried forward despite content_hash change"


def test_v4_to_v5_migration(tmp_path):
    """Opening a v4 database auto-migrates to v5 with promoted columns."""
    import json as _json

    store = SQLiteIndexStore(base_path=str(tmp_path))
    db_path = store._db_path("local", "migrate-test")

    # Create a v4 database manually
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""\
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE symbols (
            id TEXT PRIMARY KEY, file TEXT NOT NULL, name TEXT NOT NULL,
            kind TEXT, signature TEXT, summary TEXT, docstring TEXT,
            line INTEGER, end_line INTEGER, byte_offset INTEGER,
            byte_length INTEGER, parent TEXT, data TEXT
        );
        CREATE TABLE files (
            path TEXT PRIMARY KEY, hash TEXT, mtime_ns INTEGER,
            language TEXT, summary TEXT, blob_sha TEXT, imports TEXT
        );
    """)
    conn.execute("INSERT INTO meta VALUES ('index_version', '4')")
    conn.execute("INSERT INTO meta VALUES ('repo', 'local/migrate-test')")
    conn.execute("INSERT INTO meta VALUES ('owner', 'local')")
    conn.execute("INSERT INTO meta VALUES ('name', 'migrate-test')")
    conn.execute("INSERT INTO meta VALUES ('indexed_at', '2025-01-01')")
    conn.execute("INSERT INTO meta VALUES ('languages', '{}')")
    conn.execute("INSERT INTO meta VALUES ('context_metadata', '{}')")
    # Insert a v4 symbol with data JSON
    data_json = _json.dumps({
        "qualified_name": "my_func",
        "language": "python",
        "decorators": ["@staticmethod"],
        "keywords": ["async"],
        "content_hash": "abc123",
        "ecosystem_context": "",
    })
    conn.execute(
        "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("main.py::my_func#function", "main.py", "my_func", "function",
         "def my_func()", "", "", 1, 3, 0, 20, None, data_json),
    )
    conn.execute(
        "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("main.py", "hash1", None, "python", "", "", "[]"),
    )
    conn.commit()
    conn.close()

    # Clear _initialized_dbs so _connect will re-check
    SQLiteIndexStore._initialized_dbs.discard(str(db_path))

    # Now load — should trigger v4→v5 migration
    idx = store.load_index("local", "migrate-test")
    assert idx is not None
    sym = idx.symbols[0]
    assert sym["qualified_name"] == "my_func"
    assert sym["language"] == "python"
    assert sym["decorators"] == ["@staticmethod"]
    assert sym["keywords"] == ["async"]
    assert sym["content_hash"] == "abc123"

    # Verify data column is now NULL
    conn2 = sqlite3.connect(str(db_path))
    row = conn2.execute("SELECT data FROM symbols LIMIT 1").fetchone()
    assert row[0] is None
    conn2.close()


def test_v5_schema_no_json_in_data(tmp_path):
    """New indexes written with v5 schema have data=NULL and new columns populated."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    sym = Symbol(
        id="main.py::hello#function", file="main.py", name="hello",
        qualified_name="hello", kind="function", language="python",
        signature="def hello()", line=1, end_line=2,
        byte_offset=0, byte_length=10, content_hash="xyz",
        decorators=["@app.route"], keywords=["flask"],
    )
    store.save_index(
        owner="local", name="v5-test",
        source_files=["main.py"], symbols=[sym],
        raw_files={"main.py": "def hello(): pass"},
    )

    # Check raw DB: data should be NULL, new columns should be populated
    db_path = store._db_path("local", "v5-test")
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT * FROM symbols LIMIT 1").fetchone()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM symbols LIMIT 0").description]
    row_dict = dict(zip(cols, row))
    assert row_dict["data"] is None
    assert row_dict["qualified_name"] == "hello"
    assert row_dict["language"] == "python"
    assert row_dict["content_hash"] == "xyz"
    conn.close()

    # Also verify round-trip through load_index
    idx = store.load_index("local", "v5-test")
    s = idx.symbols[0]
    assert s["qualified_name"] == "hello"
    assert s["decorators"] == ["@app.route"]
    assert s["keywords"] == ["flask"]
    assert s["content_hash"] == "xyz"

