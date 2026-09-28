"""Phase 5 stack-frame ingest orchestrator: parse → redact → resolve → upsert.

Drives one stack-log import end-to-end:
  1. Parse the file via ``parse_stack_log_file()`` (plain-text or JSON-Lines).
  2. For each frame on each event, redact the trace's ``message`` field
     through the standard chokepoint and tally any redaction labels.
  3. Resolve ``(file_path, line_no, function_name)`` → ``symbol_id`` via
     the existing OTel resolver — same suffix-match fallback for absolute
     trace paths against repo-relative index file paths.
  4. Aggregate per-symbol counts both **with** severity (for
     ``runtime_stack_events``) and **without** severity (for the
     existing ``runtime_calls`` rollup so confidence-stamping on the
     existing tools still treats the symbol as confirmed-to-have-run).
  5. Bulk-upsert. FIFO eviction is applied across all four tables that
     ``runtime_calls`` / ``runtime_columns`` / ``runtime_unmapped`` /
     ``runtime_stack_events`` write to.

The runtime_calls counts under source='stack_log' deliberately do
**not** carry p50/p95: stack frames have no duration, only a count.
The columns are NULL (matches the schema's nullable design).

Idempotency: re-importing the same log re-adds counts. Future
``replace=True`` flag would no-op identical re-imports — deferred
for now.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from typing import Iterable
from ..storage.generation import connect_readonly

from . import _calls_store
from .redact import redact_trace_record
from .resolve import resolve_to_symbol_id
from .stack_log import StackEvent, iter_stack_from_text, parse_stack_log_file

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_stack_log_file(
    *,
    db_path: str,
    file_path: str,
    redact_enabled: bool = True,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Ingest one stack-frame log into the runtime tables.

    Args:
        db_path: Per-repo SQLite database path.
        file_path: Path to the stack log. ``.jsonl`` / ``.json`` /
            ``.ndjson`` use the structured-record parser; everything
            else uses the plain-text scanner. ``.gz`` transparent.
        redact_enabled: Run each event's ``message`` field through the
            redaction chokepoint. Default True.
        max_rows: Soft cap on rows in each runtime_* table after this
            ingest completes. FIFO eviction in 1k batches.

    Returns:
        ``{
            'records':           <stack events seen>,
            'frames':            <total frames across all events>,
            'mapped':            <frames resolved to a symbol_id>,
            'unmapped':          <frames without a resolution>,
            'severity_counts':   {'error': N, 'warn': M, 'info': K},
            'redactions_fired':  {<label>: <count>, ...},
            'unmapped_reasons':  {'no_match': <count>, ...},
            'evicted':           <runtime_* rows trimmed by FIFO>,
        }``
    """
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        raise FileNotFoundError(f"Index database not found: {db_path}")
    return _ingest_stack_iter(
        db_path=db_path_obj,
        events=parse_stack_log_file(file_path),
        redact_enabled=redact_enabled,
        max_rows=max_rows,
    )


def ingest_stack_log_stream(
    *,
    db_path: str,
    text: str,
    fmt: str = "auto",
    redact_enabled: bool = True,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Ingest an in-memory stack-log payload (Phase 6 HTTP route entrypoint).

    Same contract as :func:`ingest_stack_log_file`. ``fmt`` selects the
    parser dialect: ``'auto'`` (default), ``'plain'``, or ``'jsonl'``.
    """
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        raise FileNotFoundError(f"Index database not found: {db_path}")
    return _ingest_stack_iter(
        db_path=db_path_obj,
        events=iter_stack_from_text(text, fmt=fmt),
        redact_enabled=redact_enabled,
        max_rows=max_rows,
    )


def _ingest_stack_iter(
    *,
    db_path: Path,
    events: Iterable[StackEvent],
    redact_enabled: bool,
    max_rows: int,
) -> dict[str, Any]:
    """Shared consumer: drive resolve→aggregate→persist for stack events."""
    aggregator = _BatchAggregator()
    unmapped_reasons: dict[str, int] = {}
    redactions_fired: dict[str, int] = {}
    severity_counts: dict[str, int] = {"error": 0, "warn": 0, "info": 0}
    records = 0
    total_frames = 0

    for event in events:
        records += 1
        severity = event.severity if event.severity in ("error", "warn", "info") else "info"
        severity_counts[severity] += 1

        if redact_enabled and event.message:
            redacted, fired_labels = redact_trace_record(
                {"message": event.message}, source="stack_log"
            )
            for label in fired_labels:
                redactions_fired[label] = redactions_fired.get(label, 0) + 1
            del redacted  # never persisted; redaction is forensic accounting

        for frame in event.frames:
            total_frames += 1
            if not frame.file_path and not frame.function_name:
                unmapped_reasons["no_code_attrs"] = unmapped_reasons.get("no_code_attrs", 0) + 1
                aggregator.unmapped_inc(frame)
                continue
            sid = _resolve_with_conn(db_path, frame.file_path or "", frame.line_no, frame.function_name)
            if sid is None:
                unmapped_reasons["no_match"] = unmapped_reasons.get("no_match", 0) + 1
                aggregator.unmapped_inc(frame)
                continue
            aggregator.mapped_inc(symbol_id=sid, severity=severity)

    now = _utc_now()
    ingest_id = _calls_store.new_ingest_id()
    evicted = _persist(
        db_path,
        aggregator,
        redactions_fired,
        now=now,
        ingest_id=ingest_id,
        max_rows=max_rows,
    )

    return {
        "records": records,
        "frames": total_frames,
        "mapped": aggregator.mapped_total,
        "unmapped": aggregator.unmapped_total,
        "severity_counts": severity_counts,
        "redactions_fired": redactions_fired,
        "unmapped_reasons": unmapped_reasons,
        "evicted": evicted,
        "ingest_id": ingest_id,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _BatchAggregator:
    """Per-batch tally for runtime_calls, runtime_stack_events, and runtime_unmapped."""

    def __init__(self) -> None:
        # symbol_id → total count (severity-agnostic) for runtime_calls
        self._calls: dict[str, int] = {}
        # (symbol_id, severity) → count for runtime_stack_events
        self._stack: dict[tuple[str, str], int] = {}
        # (file_path, line_no, function_name) → count for runtime_unmapped
        self._unmapped: dict[tuple[str, Optional[int], str], int] = {}

    def mapped_inc(self, *, symbol_id: str, severity: str) -> None:
        self._calls[symbol_id] = self._calls.get(symbol_id, 0) + 1
        key = (symbol_id, severity)
        self._stack[key] = self._stack.get(key, 0) + 1

    def unmapped_inc(self, frame) -> None:
        key = (frame.file_path or "", frame.line_no, frame.function_name or "")
        self._unmapped[key] = self._unmapped.get(key, 0) + 1

    @property
    def mapped_total(self) -> int:
        return sum(self._calls.values())

    @property
    def unmapped_total(self) -> int:
        return sum(self._unmapped.values())

    def iter_calls(self) -> list[tuple[str, int]]:
        return list(self._calls.items())

    def iter_stack_events(self) -> list[tuple[str, str, int]]:
        return [(sid, sev, n) for (sid, sev), n in self._stack.items()]

    def iter_unmapped(self) -> list[tuple[str, Optional[int], str, int]]:
        return [(f, l, n, c) for (f, l, n), c in self._unmapped.items()]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_with_conn(
    db_path: Path,
    file_path: str,
    line_no: Optional[int],
    function_name: Optional[str],
) -> Optional[str]:
    """Short-lived read-only resolver connection (mirrors otel/sql ingestors)."""
    conn = connect_readonly(db_path, isolation_level="")
    conn.row_factory = sqlite3.Row
    try:
        return resolve_to_symbol_id(conn, file_path, line_no, function_name)
    finally:
        conn.close()


def _persist(
    db_path: Path,
    aggregator: _BatchAggregator,
    redactions_fired: dict[str, int],
    *,
    now: str,
    max_rows: int,
    ingest_id: str,
) -> int:
    """Bulk-write the aggregator. Returns total FIFO-evicted rows."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")

        # runtime_calls (severity-agnostic rollup so confidence-stamping fires)
        _calls_store.upsert_calls(
            conn,
            "stack_log",
            ((sid, count, None, None) for sid, count in aggregator.iter_calls()),
            now=now,
            ingest_id=ingest_id,
            update_latency=False,
        )

        # runtime_stack_events (severity-tagged)
        conn.executemany(
            """
            INSERT INTO runtime_stack_events (symbol_id, source, severity, count, first_seen, last_seen)
            VALUES (?, 'stack_log', ?, ?, ?, ?)
            ON CONFLICT(symbol_id, source, severity) DO UPDATE SET
                count = count + excluded.count,
                last_seen = excluded.last_seen
            """,
            [
                (sid, sev, n, now, now)
                for sid, sev, n in aggregator.iter_stack_events()
            ],
        )

        # runtime_unmapped — same shape as OTel's
        conn.executemany(
            """
            INSERT INTO runtime_unmapped (file_path, line_no, function_name, source, count, last_seen)
            VALUES (?, ?, ?, 'stack_log', ?, ?)
            ON CONFLICT(file_path, line_no, function_name, source) DO UPDATE SET
                count = count + excluded.count,
                last_seen = excluded.last_seen
            """,
            [
                (f, l, n, count, now)
                for f, l, n, count in aggregator.iter_unmapped()
            ],
        )

        # Redaction-log accounting
        conn.executemany(
            """
            INSERT INTO runtime_redaction_log (source, pattern, redaction_count, last_redacted)
            VALUES ('stack_log', ?, ?, ?)
            ON CONFLICT(source, pattern) DO UPDATE SET
                redaction_count = redaction_count + excluded.redaction_count,
                last_redacted = excluded.last_redacted
            """,
            [(label, count, now) for label, count in redactions_fired.items()],
        )

        evicted = _apply_fifo_eviction(conn, max_rows)
        conn.execute("COMMIT")
        return evicted
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _apply_fifo_eviction(conn: sqlite3.Connection, max_rows: int) -> int:
    """Trim runtime_calls / runtime_unmapped / runtime_stack_events down to max_rows."""
    if max_rows <= 0:
        return 0
    evicted = 0
    for table in ("runtime_calls", "runtime_unmapped", "runtime_stack_events"):
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        if n <= max_rows:
            continue
        excess = n - max_rows
        cur = conn.execute(
            f"""
            DELETE FROM {table}
            WHERE rowid IN (
                SELECT rowid FROM {table}
                ORDER BY last_seen ASC, rowid ASC
                LIMIT ?
            )
            """,
            (excess,),
        )
        evicted += cur.rowcount or 0
    return evicted
