"""The one writer of ``runtime_calls`` rows (#875).

Three ingest paths (``otel``, ``sql_log``, ``stack_log``) each carried a copy
of the same upsert. Each row now also records what it observed:

- ``content_hash``: the mapped symbol's ``content_hash`` in the index at
  ingest time, recorded ONLY when the symbol's file on disk still matches what
  the index read (review of #875). ``symbol_id`` survives a body edit and this
  does not, so a consumer can tell a trace of the current body from one of an
  earlier body. ⚠ It is the body the index held at INGEST, not a digest of the
  code the trace ran: a trace captured before an edit and ingested after the
  re-index records the new body. Nothing at ingest can see that case; the field
  docs name the basis. A file edited since it was indexed, a missing source
  root, or a stat that fails records NULL, which reads as ``unknown``.
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

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

PROVENANCE_COLUMNS = (("content_hash", "TEXT"), ("git_head", "TEXT"), ("ingest_id", "TEXT"))


def new_ingest_id() -> str:
    return uuid.uuid4().hex[:16]


def ensure_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add the provenance columns to a ``runtime_calls`` table that predates them."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(runtime_calls)").fetchall()}
    for name, sql_type in PROVENANCE_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE runtime_calls ADD COLUMN {name} {sql_type}")


def _meta_value(conn: sqlite3.Connection, key: str) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return (row[0] or None) if row else None


def observed_hashes(conn: sqlite3.Connection, ids: list[str]) -> dict[str, Optional[str]]:
    """The index's ``content_hash`` for each id, or None where it may not be the body on disk.

    A hash is returned only when the symbol's file on disk has the mtime the
    index recorded for it. Everything unestablished is None (UNKNOWN), never
    the index's hash: a file edited since it was indexed holds a body the
    index has not read, and a trace of it did not run the indexed body.
    """
    out: dict[str, Optional[str]] = {}
    root = _meta_value(conn, "source_root")
    unique = list(dict.fromkeys(i for i in ids if i))
    on_disk: dict[str, Optional[int]] = {}
    for start in range(0, len(unique), 500):
        batch = unique[start : start + 500]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT s.id, s.content_hash, s.file, f.mtime_ns FROM symbols s "
            f"LEFT JOIN files f ON f.path = s.file WHERE s.id IN ({placeholders})",
            batch,
        ).fetchall()
        for sid, content_hash, file_rel, indexed_ns in rows:
            if not root or not content_hash or not file_rel or indexed_ns is None:
                out[sid] = None
                continue
            if file_rel not in on_disk:
                try:
                    on_disk[file_rel] = (Path(root) / file_rel).stat().st_mtime_ns
                except OSError:
                    logger.debug("cannot stat %s for runtime provenance", file_rel, exc_info=True)
                    on_disk[file_rel] = None
            out[sid] = content_hash if on_disk[file_rel] == indexed_ns else None
    return out


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
    git_head = _meta_value(conn, "git_head")
    materialised = list(rows)
    hashes = observed_hashes(conn, [r[0] for r in materialised])
    conn.executemany(
        _UPSERT if update_latency else _UPSERT_KEEP_LATENCY,
        [
            (sid, source, count, p50, p95, now, now, hashes.get(sid), git_head, ingest_id)
            for sid, count, p50, p95 in materialised
        ],
    )


# Two whole statements, not one with a spliced clause: the evidence-table guard
# (tests/test_runtime_hit_count_column.py) compiles every statement literal.
_UPSERT = """
    INSERT INTO runtime_calls
        (symbol_id, source, count, p50_ms, p95_ms, first_seen, last_seen,
         content_hash, git_head, ingest_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(symbol_id, source) DO UPDATE SET
        count = count + excluded.count,
        last_seen = excluded.last_seen,
        content_hash = excluded.content_hash,
        git_head = excluded.git_head,
        ingest_id = excluded.ingest_id
"""
