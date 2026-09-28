"""The one writer of ``runtime_calls`` rows (#875).

Three ingest paths (``otel``, ``sql_log``, ``stack_log``) each carried a copy
of the same upsert. Each row now also records what it observed:

- ``content_hash``: the mapped symbol's ``content_hash`` in the index at
  ingest time. ``symbol_id`` survives a body edit and this does not, so a
  consumer can tell a trace of the current body from one of an earlier body.
- ``git_head``: the index's ``git_head`` at ingest.
- ``ingest_id``: the ingest run that last touched the row.

All three describe the LATEST observation. ``count`` stays cumulative across
bodies, so the history is kept and the provenance says which body it last saw.

⚠ The columns are added by ``ensure_provenance_columns`` at ingest, not by an
INDEX_VERSION bump: a bump would invalidate every index for columns that stay
empty until someone ingests a trace (the diagnostics snapshot's rule). A row
written before the columns existed carries NULLs, which readers treat as
UNKNOWN, never as current.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Iterable, Optional

PROVENANCE_COLUMNS = (("content_hash", "TEXT"), ("git_head", "TEXT"), ("ingest_id", "TEXT"))


def new_ingest_id() -> str:
    return uuid.uuid4().hex[:16]


def ensure_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add the provenance columns to a ``runtime_calls`` table that predates them."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(runtime_calls)").fetchall()}
    for name, sql_type in PROVENANCE_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE runtime_calls ADD COLUMN {name} {sql_type}")


def _index_git_head(conn: sqlite3.Connection) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'git_head'").fetchone()
    except sqlite3.OperationalError:
        return None
    return (row[0] or None) if row else None


def upsert_calls(
    conn: sqlite3.Connection,
    source: str,
    rows: Iterable[tuple],
    *,
    now: str,
    ingest_id: str,
    update_latency: bool = True,
) -> None:
    """Upsert ``(symbol_id, count, p50_ms, p95_ms)`` rows for one source.

    ``update_latency=False`` keeps an existing row's latencies (the stack-log
    path records none). Runs inside the caller's transaction.
    """
    ensure_provenance_columns(conn)
    git_head = _index_git_head(conn)
    conn.executemany(
        _UPSERT if update_latency else _UPSERT_KEEP_LATENCY,
        [
            (sid, source, count, p50, p95, now, now, sid, git_head, ingest_id)
            for sid, count, p50, p95 in rows
        ],
    )


# Two whole statements, not one with a spliced clause: the evidence-table guard
# (tests/test_runtime_hit_count_column.py) compiles every statement literal.
_UPSERT = """
    INSERT INTO runtime_calls
        (symbol_id, source, count, p50_ms, p95_ms, first_seen, last_seen,
         content_hash, git_head, ingest_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT content_hash FROM symbols WHERE id = ?), ?, ?)
    ON CONFLICT(symbol_id, source) DO UPDATE SET
        count = count + excluded.count,
        p50_ms = excluded.p50_ms,
        p95_ms = excluded.p95_ms,
        last_seen = excluded.last_seen,
        content_hash = excluded.content_hash,
        git_head = excluded.git_head,
        ingest_id = excluded.ingest_id
"""

_UPSERT_KEEP_LATENCY = """
    INSERT INTO runtime_calls
        (symbol_id, source, count, p50_ms, p95_ms, first_seen, last_seen,
         content_hash, git_head, ingest_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT content_hash FROM symbols WHERE id = ?), ?, ?)
    ON CONFLICT(symbol_id, source) DO UPDATE SET
        count = count + excluded.count,
        last_seen = excluded.last_seen,
        content_hash = excluded.content_hash,
        git_head = excluded.git_head,
        ingest_id = excluded.ingest_id
"""
