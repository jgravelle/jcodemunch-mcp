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
import re
import sqlite3
from pathlib import Path

from jcodemunch_mcp.runtime import ingest_otel_file
from jcodemunch_mcp.runtime.confidence import attach_runtime_confidence
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

RUN = "src/app.py::run#function"
IDLE = "src/app.py::idle#function"


def _seed(tmp_path: Path) -> Path:
    """An index whose `files` row matches the file on disk, as after `index_folder`."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    app = root / "src" / "app.py"
    app.write_text("def run():\n    return 0\n", encoding="utf-8")
    store = SQLiteIndexStore(base_path=str(tmp_path / "store"))
    db_path = store._db_path("local", "rt875")
    conn = store._connect(db_path)
    try:
        conn.execute(
            "INSERT INTO symbols (id, file, name, kind, line, end_line, content_hash) VALUES "
            "(?, 'src/app.py', 'run', 'function', 1, 10, 'h1'), "
            "(?, 'src/app.py', 'idle', 'function', 12, 20, 'i1')",
            (RUN, IDLE),
        )
        conn.execute("INSERT INTO files (path, mtime_ns) VALUES ('src/app.py', ?)", (app.stat().st_mtime_ns,))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('git_head', 'aaaa1111')")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('source_root', ?)", (str(root),))
        conn.commit()
    finally:
        conn.close()
    return db_path


def _edit_on_disk(db_path: Path) -> None:
    """The file changes after it was indexed; the index has not re-read it."""
    import os

    conn = sqlite3.connect(str(db_path))
    try:
        root = conn.execute("SELECT value FROM meta WHERE key = 'source_root'").fetchone()[0]
    finally:
        conn.close()
    app = Path(root) / "src" / "app.py"
    app.write_text("def run():\n    return 2\n", encoding="utf-8")
    st = app.stat()
    os.utime(app, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


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


def test_a_trace_of_a_file_edited_since_it_was_indexed_is_unknown(tmp_path):
    """Review: the index's hash is not the body the trace ran when the file moved on."""
    db_path = _seed(tmp_path)
    _edit_on_disk(db_path)
    ingest_otel_file(db_path=str(db_path), file_path=str(_trace(tmp_path)))
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT content_hash FROM runtime_calls WHERE symbol_id = ?", (RUN,)).fetchone()[0] is None
    finally:
        conn.close()
    entries = _entries()
    summary = attach_runtime_confidence(entries, str(db_path), id_field="id")
    assert entries[0]["_runtime_body"] == "unknown", "an unread edit was certified as the observed body"
    assert summary["body_basis"] == "index_body_at_ingest"


def test_blast_radius_publishes_the_same_body_counts(tmp_path):
    """Review: `get_blast_radius` assembles its own `runtime_freshness` and dropped `body`."""
    from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "engine.py").write_text("def run():\n    return 0\n", encoding="utf-8")
    (root / "app" / "main.py").write_text("from app.engine import run\n\ndef main():\n    return run()\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage, identity_mode="local")
    db_path = SQLiteIndexStore(base_path=storage)._db_path(*res["repo"].split("/", 1))
    envelope = json.loads(_trace(tmp_path).read_text(encoding="utf-8"))
    attrs = envelope["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    attrs[0]["value"]["stringValue"] = "app/engine.py"
    attrs[1]["value"]["intValue"] = "1"
    engine_trace = tmp_path / "engine_trace.json"
    engine_trace.write_text(json.dumps(envelope), encoding="utf-8")
    assert ingest_otel_file(db_path=str(db_path), file_path=str(engine_trace))["mapped"] == 1

    out = get_blast_radius(repo=res["repo"], symbol="run", storage_path=storage)
    rf = out["_meta"]["runtime_freshness"]
    assert rf["body"]["current"] >= 1, rf
    assert rf["body_basis"] == "index_body_at_ingest"


def test_no_traces_leaves_the_response_shape_unchanged(tmp_path):
    db_path = _seed(tmp_path)
    entries = _entries()
    assert attach_runtime_confidence(entries, str(db_path), id_field="id") == {}
    assert all("_runtime_body" not in e for e in entries)


_WRITE = re.compile(r"\b(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+runtime_calls\b", re.IGNORECASE)


def _runtime_calls_writers(root: Path) -> list[str]:
    """Every string literal in src/ that writes rows into runtime_calls, in any spelling."""
    hits = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _WRITE.search(node.value):
                hits.append(path.relative_to(root).as_posix())
    return sorted(set(hits))


def test_the_writer_scan_sees_every_spelling(tmp_path):
    (tmp_path / "a.py").write_text('Q = "INSERT OR REPLACE INTO runtime_calls VALUES (1)"\n', encoding="utf-8")
    (tmp_path / "b.py").write_text('Q = """replace into\n  runtime_calls VALUES (1)"""\n', encoding="utf-8")
    (tmp_path / "c.py").write_text('Q = "INSERT INTO runtime_edges VALUES (1)"\n', encoding="utf-8")
    assert _runtime_calls_writers(tmp_path) == ["a.py", "b.py"]


def test_every_writer_goes_through_the_one_upsert():
    """Three writers (otel, sql_log, stack_log) carried three copies of the upsert;
    a fourth would silently record no provenance."""
    src = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
    assert _runtime_calls_writers(src) == ["runtime/_calls_store.py"]
