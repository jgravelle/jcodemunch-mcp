"""Runtime evidence tells a trace of the current body from a trace of an earlier one (#875).

Split from #718 (finding 3), reported by @Torolosko. `runtime_calls` is
cumulative and keyed by `symbol_id`, which survives a body edit while the
symbol's `content_hash` changes, so a trace of the old body kept counting as
evidence the current body runs. Useful as history; not proof of the present.

Each row now records what it observed: the mapped symbol's `content_hash` and
the index's `git_head` at ingest, and the ingest run's id. A consumer compares
the stored hash with the symbol's hash now: equal is `current`, different is
`earlier`, and a row written before this change is `unknown` -- never
`current` (the diagnostics snapshot's rule: no data is absent, not zero).
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

from jcodemunch_mcp.runtime import ingest_otel_file
from jcodemunch_mcp.runtime.confidence import attach_runtime_confidence
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

RUN = "src/app.py::run#function"
IDLE = "src/app.py::idle#function"


def _seed(tmp_path: Path) -> Path:
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db_path = store._db_path("local", "rt875")
    conn = store._connect(db_path)
    try:
        conn.executescript(
            f"""
            INSERT INTO symbols (id, file, name, kind, line, end_line, content_hash) VALUES
                ('{RUN}', 'src/app.py', 'run', 'function', 1, 10, 'h1'),
                ('{IDLE}', 'src/app.py', 'idle', 'function', 12, 20, 'i1');
            INSERT OR REPLACE INTO meta (key, value) VALUES ('git_head', 'aaaa1111');
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _trace(tmp_path: Path, name: str = "trace.json") -> Path:
    span = {
        "traceId": "t", "spanId": "s", "name": "run",
        "startTimeUnixNano": "1000000000000000000", "endTimeUnixNano": "1000000000001000000",
        "attributes": [
            {"key": "code.filepath", "value": {"stringValue": "src/app.py"}},
            {"key": "code.lineno", "value": {"intValue": "2"}},
            {"key": "code.function", "value": {"stringValue": "run"}},
        ],
    }
    p = tmp_path / name
    p.write_text(json.dumps({"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"scope": {"name": "t"}, "spans": [span]}]}]}), encoding="utf-8")
    return p


def _set_hash(db_path: Path, sid: str, h: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE symbols SET content_hash = ? WHERE id = ?", (h, sid))
        conn.commit()
    finally:
        conn.close()


def _entries() -> list[dict]:
    return [{"id": RUN}, {"id": IDLE}]


def test_a_row_records_the_body_the_revision_and_the_ingest(tmp_path):
    db_path = _seed(tmp_path)
    out = ingest_otel_file(db_path=str(db_path), file_path=str(_trace(tmp_path)))
    assert out["mapped"] == 1
    assert out.get("ingest_id"), "the ingest names itself so its rows can be told apart"
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT content_hash, git_head, ingest_id FROM runtime_calls WHERE symbol_id = ?", (RUN,)
        ).fetchone()
    finally:
        conn.close()
    assert row == ("h1", "aaaa1111", out["ingest_id"])


def test_a_trace_of_the_current_body_reads_current(tmp_path):
    db_path = _seed(tmp_path)
    ingest_otel_file(db_path=str(db_path), file_path=str(_trace(tmp_path)))
    entries = _entries()
    summary = attach_runtime_confidence(entries, str(db_path), id_field="id")
    assert entries[0]["_runtime_confidence"] == "confirmed"
    assert entries[0]["_runtime_body"] == "current"
    assert "_runtime_body" not in entries[1], "no evidence has no body to describe"
    assert summary["body"] == {"current": 1, "earlier": 0, "unknown": 0}


def test_a_trace_of_an_earlier_body_reads_earlier_and_keeps_its_history(tmp_path):
    """The reported shape: the id survived a body edit, the evidence did not."""
    db_path = _seed(tmp_path)
    ingest_otel_file(db_path=str(db_path), file_path=str(_trace(tmp_path)))
    _set_hash(db_path, RUN, "h2")  # the body was rewritten and re-indexed

    entries = _entries()
    summary = attach_runtime_confidence(entries, str(db_path), id_field="id")
    assert entries[0]["_runtime_confidence"] == "confirmed", "history stays: it did run"
    assert entries[0]["_runtime_body"] == "earlier", "a trace of the old body read as evidence the new one runs"
    assert summary["body"] == {"current": 0, "earlier": 1, "unknown": 0}

    # A new trace of the new body makes it current again; the count is cumulative.
    ingest_otel_file(db_path=str(db_path), file_path=str(_trace(tmp_path, "t2.json")))
    entries = _entries()
    attach_runtime_confidence(entries, str(db_path), id_field="id")
    assert entries[0]["_runtime_body"] == "current"
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT count FROM runtime_calls WHERE symbol_id = ?", (RUN,)).fetchone()[0] == 2
    finally:
        conn.close()


def test_a_row_written_before_the_change_is_unknown_never_current(tmp_path):
    """A pre-#875 table has no provenance columns; the probe must not guess."""
    db_path = _seed(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            f"""
            DROP TABLE runtime_calls;
            CREATE TABLE runtime_calls (
                symbol_id TEXT NOT NULL, source TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
                p50_ms REAL, p95_ms REAL, first_seen TEXT, last_seen TEXT,
                PRIMARY KEY (symbol_id, source));
            INSERT INTO runtime_calls VALUES ('{RUN}', 'otel', 3, NULL, NULL, '2026-01-01', '2026-01-01');
            """
        )
        conn.commit()
    finally:
        conn.close()

    entries = _entries()
    summary = attach_runtime_confidence(entries, str(db_path), id_field="id")
    assert entries[0]["_runtime_confidence"] == "confirmed"
    assert entries[0]["_runtime_body"] == "unknown"
    assert summary["body"] == {"current": 0, "earlier": 0, "unknown": 1}

    # And the first ingest into that old table adds the columns rather than failing.
    out = ingest_otel_file(db_path=str(db_path), file_path=str(_trace(tmp_path)))
    assert out["mapped"] == 1
    entries = _entries()
    attach_runtime_confidence(entries, str(db_path), id_field="id")
    assert entries[0]["_runtime_body"] == "current"


def test_no_traces_leaves_the_response_shape_unchanged(tmp_path):
    db_path = _seed(tmp_path)
    entries = _entries()
    assert attach_runtime_confidence(entries, str(db_path), id_field="id") == {}
    assert all("_runtime_body" not in e for e in entries)


def _runtime_calls_writers(root: Path) -> list[str]:
    """Every string literal in src/ that inserts into runtime_calls."""
    hits = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "INSERT INTO runtime_calls" in node.value:
                hits.append(path.relative_to(root).as_posix())
    return sorted(set(hits))


def test_every_writer_goes_through_the_one_upsert():
    """Three writers (otel, sql_log, stack_log) carried three copies of the upsert;
    a fourth would silently record no provenance."""
    src = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
    assert _runtime_calls_writers(src) == ["runtime/_calls_store.py"]
