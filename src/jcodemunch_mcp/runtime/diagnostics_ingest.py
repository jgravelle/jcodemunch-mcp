"""Compiler diagnostics ingest: parse → redact → resolve → REPLACE.

Same pipeline shape as ``stack_ingest.py`` with one deliberate difference in
the last step. Traces ACCUMULATE, because a call that happened stays true. A
type error that was fixed must DISAPPEAR, or the table lies forever about a
symbol that is now clean. So an ingest replaces every row for the same
``tool`` with the new file's rows, and the table is not named ``runtime_*``:
it is a snapshot of one tree state, stamped with the HEAD it was taken at.

Mapping goes through ``resolve_to_symbol_id`` (innermost enclosing span, then
suffix-of-path fallback for the absolute paths pyright and tsc emit). A line
that resolves to nothing is recorded in ``runtime_unmapped`` under
``source='diagnostics:<tool>'`` so ``get_runtime_coverage`` can show it.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..storage.generation import connect_readonly
from .diagnostics_log import Diagnostic, parse_diagnostics_file
from .redact import redact_trace_record
from .resolve import resolve_to_symbol_id, suffix_candidates

logger = logging.getLogger(__name__)

DIAGNOSTICS_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS diagnostics (
    symbol_id      TEXT NOT NULL,
    tool           TEXT NOT NULL,
    severity       TEXT NOT NULL,
    code           TEXT NOT NULL DEFAULT '',
    count          INTEGER NOT NULL DEFAULT 0,
    sample_message TEXT,
    git_head       TEXT,
    ingested_at    TEXT,
    PRIMARY KEY (symbol_id, tool, severity, code)
);
CREATE INDEX IF NOT EXISTS idx_diagnostics_tool ON diagnostics(tool);
CREATE INDEX IF NOT EXISTS idx_diagnostics_severity ON diagnostics(severity);
"""


def ensure_diagnostics_table(conn: sqlite3.Connection) -> None:
    """Create the table on a database that predates it. Idempotent. Deliberately
    NOT an INDEX_VERSION bump: a version bump invalidates every user's index
    for a table that stays empty until they run an ingest."""
    conn.executescript(DIAGNOSTICS_SCHEMA_SQL)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_source_root(db_path: Path) -> Optional[str]:
    try:
        conn = connect_readonly(db_path, isolation_level="")
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'source_root'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    v = row[0]
    return str(v) if v else None


def git_head_of(source_root: Optional[str]) -> Optional[str]:
    """HEAD of the source root, or None when it cannot be established. None is
    UNKNOWN and is never rendered as a value downstream. THE ONE reader of
    "what is HEAD here" for the diagnostics feature: the ingest stamps with it
    and ``tools/_diagnostics_consume`` compares against it."""
    if not source_root or not Path(source_root).is_dir():
        return None
    try:
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source_root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    head = p.stdout.strip()
    return head or None


def _file_is_indexed(conn: sqlite3.Connection, file_path: str) -> bool:
    """Same suffix walk the resolver uses, over ``files.path``, so the
    unmapped REASON agrees with the resolver about which files exist."""
    return bool(suffix_candidates(conn, file_path, table="files", column="path", limit=1))


def ingest_diagnostics_file(
    *,
    db_path: str,
    file_path: str,
    redact_enabled: bool = True,
    max_rows: int = 100_000,
    fmt: str = "auto",
) -> dict[str, Any]:
    """Ingest one checker-output file into the ``diagnostics`` snapshot table.

    Returns ``{tool, records, mapped, unmapped, unmapped_reasons, replaced,
    severity_counts, redactions_fired, git_head, evicted}``. ``replaced`` is the
    number of rows the previous snapshot for this tool held.
    """
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(f"index database not found: {db_path}")
    fmt_used, diags = parse_diagnostics_file(file_path, fmt=fmt)
    # The tool label comes from the rows (generic rows name their own tool);
    # an empty explicit-format file labels itself after the format.
    tool = diags[0].tool if diags else (fmt_used if fmt_used != "generic" else "generic")

    source_root = _read_source_root(db)
    git_head = git_head_of(source_root)
    now = _utc_now()

    # Resolve on a read-only connection, aggregate in memory.
    agg: dict[tuple[str, str, str], dict[str, Any]] = {}
    unmapped: Counter = Counter()
    unmapped_rows: dict[tuple[str, Optional[int], str], int] = {}
    redactions: Counter = Counter()
    sev_counts: Counter = Counter()
    mapped = 0

    rconn = connect_readonly(db, isolation_level="")
    rconn.row_factory = sqlite3.Row
    try:
        for d in diags:
            sev_counts[d.severity] += 1
            file_norm = d.file.replace("\\", "/")
            sid = resolve_to_symbol_id(rconn, file_norm, d.line, None)
            if sid is None:
                reason = "no_enclosing_symbol" if _file_is_indexed(rconn, file_norm) else "file_not_indexed"
                unmapped[reason] += 1
                key = (file_norm, d.line, reason)
                unmapped_rows[key] = unmapped_rows.get(key, 0) + 1
                continue
            mapped += 1
            message = d.message
            if redact_enabled:
                rec, fired = redact_trace_record({"symbol_id": sid, "message": message}, "diagnostics")
                message = str(rec.get("message") or "")
                for label in fired:
                    redactions[label] += 1
            k = (sid, d.severity, d.code)
            slot = agg.get(k)
            if slot is None:
                agg[k] = {"count": 1, "sample": message}
            else:
                slot["count"] += 1
    finally:
        rconn.close()

    wconn = sqlite3.connect(str(db), isolation_level=None)
    try:
        ensure_diagnostics_table(wconn)
        wconn.execute("BEGIN")
        replaced = wconn.execute("SELECT COUNT(*) FROM diagnostics WHERE tool = ?", (tool,)).fetchone()[0]
        wconn.execute("DELETE FROM diagnostics WHERE tool = ?", (tool,))
        wconn.executemany(
            """
            INSERT INTO diagnostics (symbol_id, tool, severity, code, count, sample_message, git_head, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (sid, tool, sev, code, v["count"], v["sample"], git_head, now)
                for (sid, sev, code), v in agg.items()
            ],
        )
        src_label = f"diagnostics:{tool}"
        # The unmapped table is also a snapshot for this tool's source label.
        wconn.execute("DELETE FROM runtime_unmapped WHERE source = ?", (src_label,))
        wconn.executemany(
            """
            INSERT INTO runtime_unmapped (file_path, line_no, function_name, source, count, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path, line_no, function_name, source) DO UPDATE SET
                count = count + excluded.count, last_seen = excluded.last_seen
            """,
            [(f, ln, reason, src_label, n, now) for (f, ln, reason), n in unmapped_rows.items()],
        )
        wconn.executemany(
            """
            INSERT INTO runtime_redaction_log (source, pattern, redaction_count, last_redacted)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, pattern) DO UPDATE SET
                redaction_count = redaction_count + excluded.redaction_count,
                last_redacted = excluded.last_redacted
            """,
            [(src_label, label, n, now) for label, n in redactions.items()],
        )
        evicted = 0
        if max_rows > 0:
            n = wconn.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]
            if n > max_rows:
                evicted = n - max_rows
                wconn.execute(
                    "DELETE FROM diagnostics WHERE rowid IN ("
                    "SELECT rowid FROM diagnostics ORDER BY ingested_at ASC, rowid ASC LIMIT ?)",
                    (evicted,),
                )
        wconn.execute("COMMIT")
    except sqlite3.Error:
        wconn.execute("ROLLBACK")
        raise
    finally:
        wconn.close()

    return {
        "tool": tool,
        "format": fmt_used,
        "records": len(diags),
        "mapped": mapped,
        "unmapped": sum(unmapped.values()),
        "unmapped_reasons": dict(unmapped),
        "replaced": int(replaced),
        "severity_counts": dict(sev_counts),
        "redactions_fired": dict(redactions),
        "git_head": git_head,
        "evicted": evicted,
    }


__all__ = ["Diagnostic", "ingest_diagnostics_file", "ensure_diagnostics_table", "DIAGNOSTICS_SCHEMA_SQL", "git_head_of"]
