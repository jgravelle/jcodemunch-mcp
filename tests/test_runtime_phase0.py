"""Phase 0 runtime trace ingestion: schema migration + redaction + resolver."""

from __future__ import annotations

import sqlite3

import pytest

from jcodemunch_mcp.runtime import (
    VALID_SOURCES,
    redact_trace_record,
    resolve_to_symbol_id,
)
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore


# ──────────────────────────────────────────────────────────────────────
# Schema + migration
# ──────────────────────────────────────────────────────────────────────

_RUNTIME_TABLES = (
    "runtime_calls",
    "runtime_edges",
    "runtime_imports",
    "runtime_unmapped",
    "runtime_redaction_log",
)


def test_fresh_v14_schema_has_runtime_tables(tmp_path):
    """A new database created at v14 has all five runtime_* tables empty."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db_path = store._db_path("local", "fresh")
    conn = store._connect(db_path)
    try:
        for table in _RUNTIME_TABLES:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            assert row is not None, f"missing table: {table}"
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert n == 0, f"{table} should start empty"
    finally:
        conn.close()


def test_v9_database_migrates_to_v14(tmp_path):
    """Opening a v9 database adds runtime_* tables and stamps version=14."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db_path = store._db_path("local", "migrate14")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE symbols (
            id TEXT PRIMARY KEY, file TEXT NOT NULL, name TEXT NOT NULL,
            kind TEXT, signature TEXT, summary TEXT, docstring TEXT,
            line INTEGER, end_line INTEGER, byte_offset INTEGER,
            byte_length INTEGER, parent TEXT,
            qualified_name TEXT, language TEXT, decorators TEXT, keywords TEXT,
            content_hash TEXT, ecosystem_context TEXT, data TEXT,
            cyclomatic INTEGER, max_nesting INTEGER, param_count INTEGER
        );
        CREATE TABLE files (
            path TEXT PRIMARY KEY, hash TEXT, mtime_ns INTEGER,
            language TEXT, summary TEXT, blob_sha TEXT, imports TEXT,
            size_bytes INTEGER
        );
        CREATE TABLE branch_deltas (
            branch TEXT, file TEXT, action TEXT, symbol_data TEXT,
            file_hash TEXT, file_mtime_ns INTEGER, file_language TEXT,
            file_summary TEXT, file_imports TEXT, file_size_bytes INTEGER,
            PRIMARY KEY (branch, file)
        );
        CREATE TABLE branch_meta (
            branch TEXT PRIMARY KEY, git_head TEXT, indexed_at TEXT, base_head TEXT
        );
        """
    )
    conn.execute("INSERT INTO meta VALUES ('index_version', '9')")
    conn.commit()
    conn.close()

    SQLiteIndexStore._initialized_dbs.discard(str(db_path))

    conn = store._connect(db_path)
    try:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='index_version'"
        ).fetchone()["value"]
        # The ladder runs to the current head version (v17 added scip_*).
        assert version == "17"
        for table in _RUNTIME_TABLES:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            assert row is not None, f"migration didn't add {table}"
    finally:
        conn.close()


def test_v14_migration_is_idempotent(tmp_path):
    """Running the migration twice doesn't duplicate or error."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db_path = store._db_path("local", "idempotent")

    conn1 = store._connect(db_path)
    conn1.close()

    SQLiteIndexStore._initialized_dbs.discard(str(db_path))

    # Re-open — _connect would short-circuit because version is already 14,
    # so manually invoke the migration to prove it's safe to rerun.
    from jcodemunch_mcp.storage.sqlite_store import _migrate_v13_to_v14
    conn2 = sqlite3.connect(str(db_path))
    _migrate_v13_to_v14(conn2)  # Should not raise
    conn2.close()


# ──────────────────────────────────────────────────────────────────────
# Redaction chokepoint
# ──────────────────────────────────────────────────────────────────────


def test_redact_passes_structural_fields_through_unchanged():
    record = {
        "symbol_id": "src/auth.py::login#function",
        "file_path": "src/auth.py",
        "line_no": 42,
        "function_name": "login",
        "source": "otel",
        "count": 17,
        "p50_ms": 4.2,
        "p95_ms": 12.7,
    }
    out, fired = redact_trace_record(record, source="otel")
    assert out == record
    assert fired == []


def test_redact_strips_email_address():
    record = {"function_name": "x", "user_repr": "user=alice@example.com"}  # no quotes — bypass SQL literal
    out, fired = redact_trace_record(record, source="stack_log")
    assert "alice@example.com" not in out["user_repr"]
    assert "[REDACTED:email_address]" in out["user_repr"]
    assert "email_address" in fired


def test_redact_strips_quoted_email_via_sql_literal():
    """Email inside SQL-style single quotes is consumed by the SQL literal pattern."""
    record = {"function_name": "x", "query": "WHERE email = 'alice@example.com'"}
    out, fired = redact_trace_record(record, source="sql_log")
    assert "alice@example.com" not in out["query"]
    # The SQL literal pattern fires first and removes the contents wholesale —
    # downstream patterns see the redaction placeholder, not the original text.
    assert "sql_string_literal" in fired


def test_redact_strips_ipv4():
    record = {"function_name": "x", "remote_addr": "203.0.113.42"}
    out, fired = redact_trace_record(record, source="otel")
    assert "203.0.113.42" not in out["remote_addr"]
    assert "ipv4_address" in fired


def test_redact_strips_sql_string_literal():
    record = {"function_name": "x", "query": "SELECT * FROM users WHERE email = 'alice@example.com'"}
    out, fired = redact_trace_record(record, source="sql_log")
    assert "alice@example.com" not in out["query"]
    # Either the SQL literal or email pattern should fire — both are acceptable
    assert any(label in fired for label in ("sql_string_literal", "email_address"))


def test_redact_strips_sql_numeric_param():
    record = {"function_name": "x", "query": "SELECT * FROM orders WHERE id = 42"}
    out, fired = redact_trace_record(record, source="sql_log")
    assert "= 42" not in out["query"]
    assert "sql_numeric_param" in fired


def test_redact_strips_python_locals_block():
    record = {
        "function_name": "x",
        "frame": "kwargs={'password': 'hunter2', 'user_id': 42}",
    }
    out, fired = redact_trace_record(record, source="stack_log")
    assert "hunter2" not in out["frame"]
    assert "python_locals_block" in fired


def test_redact_recurses_into_nested_dicts():
    record = {
        "function_name": "x",
        "metadata": {"client_email": "alice@example.com"},
    }
    out, fired = redact_trace_record(record, source="otel")
    assert "alice@example.com" not in out["metadata"]["client_email"]
    assert "email_address" in fired


def test_redact_recurses_into_lists():
    record = {
        "function_name": "x",
        "stack": [
            {"frame": "user=bob@example.com"},  # unquoted so SQL literal doesn't eat it
            "192.0.2.1 connection",
        ],
    }
    out, fired = redact_trace_record(record, source="stack_log")
    assert "bob@example.com" not in str(out["stack"])
    assert "192.0.2.1" not in str(out["stack"])
    assert "email_address" in fired
    assert "ipv4_address" in fired


def test_redact_handles_non_dict_input():
    out, fired = redact_trace_record("not a dict", source="otel")  # type: ignore[arg-type]
    assert out == "not a dict"
    assert fired == []


def test_redact_strips_jwt():
    record = {
        "function_name": "x",
        "auth": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    }
    out, fired = redact_trace_record(record, source="otel")
    assert "eyJhbGc" not in out["auth"] or "[REDACTED:" in out["auth"]
    assert any(label in fired for label in ("jwt", "bearer_token"))


# ──────────────────────────────────────────────────────────────────────
# Resolver
# ──────────────────────────────────────────────────────────────────────


def _seed_symbols(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT INTO symbols (id, file, name, kind, line, end_line) VALUES
            ('src/auth.py::login#function', 'src/auth.py', 'login', 'function', 10, 25),
            ('src/auth.py::logout#function', 'src/auth.py', 'logout', 'function', 30, 38),
            ('src/users.py::User#class', 'src/users.py', 'User', 'class', 1, 100),
            ('src/users.py::User.save#method', 'src/users.py', 'save', 'method', 50, 60);
        """
    )
    conn.commit()


def test_resolve_exact_file_line_picks_smallest_enclosing(tmp_path):
    store = SQLiteIndexStore(base_path=str(tmp_path))
    conn = store._connect(store._db_path("local", "resolve"))
    try:
        _seed_symbols(conn)
        # Line 55 is inside both User (1-100) and User.save (50-60) — should pick save
        result = resolve_to_symbol_id(conn, "src/users.py", line_no=55, function_name=None)
        assert result == "src/users.py::User.save#method"
    finally:
        conn.close()


def test_resolve_falls_back_to_function_name(tmp_path):
    store = SQLiteIndexStore(base_path=str(tmp_path))
    conn = store._connect(store._db_path("local", "resolve2"))
    try:
        _seed_symbols(conn)
        # Line is way out of range — fall back to name match
        result = resolve_to_symbol_id(conn, "src/auth.py", line_no=9999, function_name="logout")
        assert result == "src/auth.py::logout#function"
    finally:
        conn.close()


def test_resolve_suffix_match_for_absolute_paths(tmp_path):
    store = SQLiteIndexStore(base_path=str(tmp_path))
    conn = store._connect(store._db_path("local", "resolve3"))
    try:
        _seed_symbols(conn)
        # Trace records arrive with absolute path; index has repo-relative path
        result = resolve_to_symbol_id(
            conn, "/var/app/checkout/src/auth.py", line_no=12, function_name="login"
        )
        assert result == "src/auth.py::login#function"
    finally:
        conn.close()


def test_resolve_returns_none_on_complete_miss(tmp_path):
    store = SQLiteIndexStore(base_path=str(tmp_path))
    conn = store._connect(store._db_path("local", "resolve4"))
    try:
        _seed_symbols(conn)
        result = resolve_to_symbol_id(
            conn, "src/nonexistent.py", line_no=1, function_name="missing"
        )
        assert result is None
    finally:
        conn.close()


def test_resolve_handles_empty_path(tmp_path):
    store = SQLiteIndexStore(base_path=str(tmp_path))
    conn = store._connect(store._db_path("local", "resolve5"))
    try:
        _seed_symbols(conn)
        assert resolve_to_symbol_id(conn, "", line_no=1, function_name="x") is None
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────


def test_valid_sources_frozen():
    # 'diagnostics' (2026-09): a checker's own output file, the fifth source.
    assert VALID_SOURCES == frozenset({"otel", "sql_log", "stack_log", "diagnostics", "apm"})


# ──────────────────────────────────────────────────────────────────────
# Session-stats integration
# ──────────────────────────────────────────────────────────────────────


def test_get_session_stats_includes_runtime_signal_zero(tmp_path):
    """Session stats include runtime_signal field that reads zero in Phase 0."""
    from jcodemunch_mcp.storage.token_tracker import get_session_stats
    stats = get_session_stats(base_path=str(tmp_path))
    assert "runtime_signal" in stats
    assert stats["runtime_signal"]["rows"] == 0
    assert stats["runtime_signal"]["by_source"] == {}
