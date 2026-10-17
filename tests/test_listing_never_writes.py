"""LEDGER L-144: listing or inspecting the index store never writes to a file.

`list_repos` used to open every `*.db` in the storage directory through
`SQLiteIndexStore._connect`, the indexer's connection. On a file with no
`meta` table that runs the whole schema script, and on a file whose `meta`
has no `index_version` row it runs every version migration. So a foreign
SQLite file gained 16 index tables and a half-built index had a version
stamped into it, on every server start (`cleanup_orphan_indexes` and
`heal_pack_index_paths` both list). A two-name denylist (`telemetry.db`,
`org_savings.db`) was the only guard, and `token_tracker` kept a one-name copy.

Whether a file is an index is now decided by what is in it
(`sqlite_store.classify_index_db`), and every listing read opens read-only.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import sqlite_store
from jcodemunch_mcp.storage.generation import connect_readonly
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.storage.token_tracker import _runtime_signal_summary


def _tables(path: Path) -> list[str]:
    # Reads the WAL when one exists: an immutable read skips it and would
    # report no tables over a schema that is sitting there uncheckpointed.
    conn = connect_readonly(path)
    try:
        return sorted(
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        )
    finally:
        conn.close()


def _fingerprint(path: Path) -> tuple:
    """Bytes of the file and whether any sidecar exists."""
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        Path(str(path) + "-wal").exists(),
        Path(str(path) + "-shm").exists(),
    )


def _foreign_db(path: Path) -> None:
    """A SQLite file of another kind, e.g. the parse cache."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE entries (key TEXT PRIMARY KEY, blob BLOB)")
        conn.execute("INSERT INTO entries VALUES ('k', x'00')")
        conn.commit()
    finally:
        conn.close()


def _half_built_index(path: Path) -> None:
    """The first two statements of the real schema script, nothing more."""
    statements = sqlite_store._SCHEMA_SQL.split(";")
    conn = sqlite3.connect(str(path))
    try:
        for stmt in statements[:2]:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _make_symbol(name: str) -> Symbol:
    return Symbol(
        id=f"a.py::{name}#function",
        file="a.py",
        name=name,
        qualified_name=name,
        kind="function",
        language="python",
        signature=f"def {name}():",
        byte_offset=0,
        byte_length=10,
    )


def _real_index(store: SQLiteIndexStore, name: str) -> Path:
    store.save_index(
        owner="local", name=name,
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "def f(): pass\n"},
    )
    store.checkpoint_db(store._db_path("local", name))
    return store._db_path("local", name)


@pytest.fixture(autouse=True)
def _fresh_init_cache():
    """`_connect` skips migration for paths it has seen; start clean."""
    saved = set(SQLiteIndexStore._initialized_dbs)
    SQLiteIndexStore._initialized_dbs.clear()
    yield
    SQLiteIndexStore._initialized_dbs.clear()
    SQLiteIndexStore._initialized_dbs.update(saved)


def _every_listing_path(base: Path) -> list:
    sqlite = SQLiteIndexStore(base_path=str(base))
    store = IndexStore(base_path=str(base))
    sqlite.list_source_roots()
    sqlite.cleanup_orphan_indexes()
    sqlite.heal_pack_index_paths()
    return [sqlite.list_repos(), store.list_repos()]


@pytest.mark.parametrize("filename", ["parse_cache.db", "local-foreign.db"])
def test_listing_does_not_write_a_foreign_database(tmp_path, filename):
    foreign = tmp_path / filename
    _foreign_db(foreign)
    before_tables = _tables(foreign)
    before = _fingerprint(foreign)

    listings = _every_listing_path(tmp_path)

    assert _tables(foreign) == before_tables, (
        f"listing created tables in {filename}: {_tables(foreign)}"
    )
    assert _fingerprint(foreign) == before
    stem = foreign.stem
    for repos in listings:
        assert not any(stem in (r.get("repo") or "") for r in repos), repos


def test_listing_does_not_stamp_a_half_built_index(tmp_path):
    half = tmp_path / "local-half.db"
    _half_built_index(half)
    before_tables = _tables(half)
    before = _fingerprint(half)

    listings = _every_listing_path(tmp_path)

    conn = connect_readonly(half)
    try:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='index_version'"
        ).fetchone()
    finally:
        conn.close()
    assert version is None, f"listing stamped index_version={version[0]}"
    assert _tables(half) == before_tables
    assert _fingerprint(half) == before
    # Not a finished index: nothing to list.
    for repos in listings:
        assert not any("half" in (r.get("repo") or "") for r in repos), repos


def test_inspect_index_does_not_write(tmp_path):
    foreign = tmp_path / "local-foreign.db"
    _foreign_db(foreign)
    before = (_tables(foreign), _fingerprint(foreign))

    SQLiteIndexStore(base_path=str(tmp_path)).inspect_index("local", "foreign")

    assert (_tables(foreign), _fingerprint(foreign)) == before


def test_foreign_store_is_skipped_by_content_under_any_name(tmp_path):
    """The org-rollup schema named like an index is still not an index."""
    org = tmp_path / "local-org.db"
    conn = sqlite3.connect(str(org))
    try:
        conn.execute("CREATE TABLE org_savings (org_id TEXT, seat_id TEXT, day TEXT)")
        conn.commit()
    finally:
        conn.close()

    for repos in _every_listing_path(tmp_path):
        assert not any("org" in (r.get("repo") or "") for r in repos), repos


def test_store_polluted_by_an_earlier_release_is_not_listed(tmp_path):
    """The shape this box's telemetry.db has: its own tables plus the empty
    index tables and two meta rows an earlier `_connect` wrote into it."""
    polluted = tmp_path / "local-polluted.db"
    conn = sqlite3.connect(str(polluted))
    try:
        conn.execute("CREATE TABLE tool_calls (ts REAL, tool TEXT)")
        conn.executescript(sqlite_store._SCHEMA_SQL)
        conn.execute("INSERT INTO meta VALUES ('call_refs_missing', '1')")
        conn.execute("INSERT INTO meta VALUES ('index_version', '17')")
        conn.commit()
    finally:
        conn.close()

    for repos in _every_listing_path(tmp_path):
        assert not any("polluted" in (r.get("repo") or "") for r in repos), repos
    assert sqlite_store.classify_index_db(polluted) == "not_index"


def test_a_real_index_is_read_whatever_its_file_is_called(tmp_path):
    """Content, not name: an index renamed to telemetry.db is still an index."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db = _real_index(store, "proj")
    renamed = tmp_path / "telemetry.db"
    db.rename(renamed)

    repos = store.list_repos()
    assert any(r["repo"] == "local/proj" for r in repos), repos
    assert sqlite_store.classify_index_db(renamed) == "index"


def test_damaged_index_stays_visible(tmp_path):
    """A real index whose meta rows were lost is still listed for repair."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db = _real_index(store, "broken")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM meta")
        conn.commit()
    finally:
        conn.close()
    SQLiteIndexStore._initialized_dbs.clear()

    broken = next(r for r in store.list_repos() if r["repo"] == "local/broken")
    assert broken["status"] == "sqlite_missing_meta"
    assert sqlite_store.classify_index_db(db) == "damaged"


@pytest.mark.parametrize(
    "damage,status",
    [
        ("DROP TABLE meta", "sqlite_missing_meta"),
        ("DELETE FROM meta WHERE key = 'repo'; DROP TABLE files", "sqlite_corrupt"),
    ],
    ids=["meta_table_dropped", "repo_row_and_files_table_gone"],
)
def test_index_with_a_lost_table_stays_visible(tmp_path, damage, status):
    """Rows in `symbols` vouch for an index whatever else was lost; the
    review's two shapes, both listed on main."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db = _real_index(store, "broken")
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(damage)
    finally:
        conn.close()
    SQLiteIndexStore._initialized_dbs.clear()

    for repos in (store.list_repos(), IndexStore(base_path=str(tmp_path)).list_repos()):
        broken = [r for r in repos if r["repo"] == "local/broken"]
        assert len(broken) == 1, repos
        assert broken[0]["status"] == status, broken


def test_unreadable_db_beside_legacy_json_is_listed_once(tmp_path):
    """The review's probe: a `.db` that is not SQLite plus a legacy JSON of
    the same slug. Pass 2 must not list the JSON a second time."""
    import json

    store = IndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local", name="x",
        source_files=["a.py"], symbols=[_make_symbol("f")],
        raw_files={"a.py": "def f(): pass\n"},
    )
    entry = next(r for r in store.list_repos() if r["repo"] == "local/x")
    assert entry
    db = tmp_path / "local-x.db"
    legacy = {
        "repo": "local/x", "owner": "local", "name": "x",
        "indexed_at": "2026-01-01T00:00:00", "index_version": 3,
        "source_files": ["a.py"], "languages": {"python": 1},
        "symbols": [], "file_hashes": {},
    }
    store.delete_index("local", "x")
    (tmp_path / "local-x.json").write_text(json.dumps(legacy), encoding="utf-8")
    db.write_bytes(b"this is not a sqlite database at all" * 4)

    repos = IndexStore(base_path=str(tmp_path)).list_repos()
    assert len([r for r in repos if r["repo"] == "local/x"]) == 1, repos


def test_runtime_signal_counts_only_indexes(tmp_path):
    """`token_tracker` kept its own one-name denylist that omitted
    org_savings.db. A non-index file carrying a `runtime_calls` table must
    not be counted, whatever it is called."""
    for name in ("org_savings.db", "local-other.db"):
        conn = sqlite3.connect(str(tmp_path / name))
        try:
            conn.execute("CREATE TABLE runtime_calls (source TEXT, count INTEGER)")
            conn.execute("INSERT INTO runtime_calls VALUES ('otel', 5)")
            conn.commit()
        finally:
            conn.close()

    assert _runtime_signal_summary(str(tmp_path))["rows"] == 0


def test_runtime_signal_still_counts_a_real_index(tmp_path):
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db = _real_index(store, "traced")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runtime_calls (symbol_id, source, count) VALUES ('a.py::f#function', 'otel', 3)"
        )
        conn.commit()
    finally:
        conn.close()
    store.checkpoint_db(db)

    summary = _runtime_signal_summary(str(tmp_path))
    assert summary["rows"] == 3, summary


def test_schema_script_is_one_transaction(tmp_path, monkeypatch):
    """A failure partway through leaves no index table behind, so no second
    connection can ever see a half-created schema."""
    monkeypatch.setattr(
        sqlite_store, "_SCHEMA_SQL",
        sqlite_store._SCHEMA_SQL + "\nCREATE TABLE broken (;\n",
    )
    db = tmp_path / "local-new.db"
    store = SQLiteIndexStore(base_path=str(tmp_path))

    with pytest.raises(sqlite3.Error):
        store._connect(db)

    assert _tables(db) == []


def test_runtime_signal_feeds_nothing_that_is_sent():
    """L-144 edits `_runtime_signal_summary`; J's rule is no telemetry change
    without his ruling. Its one reader is `get_session_stats`, a local read,
    so the savings totals and every sent payload are untouched by it. A new
    caller (a sender included) fails here and needs that ruling first."""
    import ast

    src = Path(sqlite_store.__file__).resolve().parents[1]
    callers = []
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Name) and node.id == "_runtime_signal_summary":
                    callers.append((path.name, fn.name))
    assert callers == [("token_tracker.py", "get_session_stats")], callers
