"""Phase 1 ingest orchestrator: parse → redact → resolve → upsert.

Drives one ingest run end-to-end:
  1. Parse the trace file (currently OTel JSON / JSON-Lines via otel.py).
  2. For each span, redact non-structural fields via redact_trace_record().
  3. Resolve (file_path, line_no, function_name) → symbol_id.
  4. Aggregate per (symbol_id, source) — count, p50/p95 latency, first/last seen.
  5. Bulk-upsert to runtime_calls; record unmapped spans in runtime_unmapped;
     record redaction labels in runtime_redaction_log.
  6. Apply ``runtime_max_rows`` FIFO eviction once writes complete.

Idempotency: each call is **additive**. Re-importing the same file re-adds
counts. A future ``replace=True`` flag can no-op identical re-imports — for
Phase 1 the contract is "each ingest reports the spans you handed in".

p50 / p95: computed over the spans in the current batch only and overwrite
the previous values. A full streaming-quantile merge across batches is
deliberately deferred — Phase 4+ when the SQL log + stack log channels
land and the quantiles need to be merge-correct.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from typing import Iterable
from ..storage.generation import connect_readonly

from .otel import OtelSpan, iter_otel_from_text, parse_otel_file
from . import _calls_store
from .redact import redact_trace_record
from .resolve import resolve_to_symbol_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_otel_file(
    *,
    db_path: str,
    file_path: str,
    redact_enabled: bool = True,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Ingest one OTel JSON/JSON-Lines file into the runtime tables.

    Args:
        db_path: Path to the per-repo SQLite database file (resolve via
            ``IndexStore._db_path(owner, name)`` from a higher layer).
        file_path: Path to the OTel trace file (.json, .jsonl, or .gz).
        redact_enabled: Run records through ``redact_trace_record()``
            before any storage write. Default True. Set False **only**
            for offline debugging on synthetic data.
        max_rows: Soft cap on rows in ``runtime_calls`` after this
            ingest completes. FIFO eviction in 1k batches by ``last_seen``
            once exceeded.

    Returns:
        ``{
            'records':            <total spans seen>,
            'mapped':             <spans resolved to a symbol_id>,
            'unmapped':           <spans without a resolution>,
            'redactions_fired':   {<label>: <count>, ...},
            'unmapped_reasons':   {'no_code_attrs' | 'no_match' : <count>, ...},
            'evicted':            <runtime_calls rows trimmed by FIFO>,
        }``
    """
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        raise FileNotFoundError(f"Index database not found: {db_path}")
    return _ingest_otel_iter(
        db_path=db_path_obj,
        spans=parse_otel_file(file_path),
        redact_enabled=redact_enabled,
        max_rows=max_rows,
    )


def ingest_otel_stream(
    *,
    db_path: str,
    text: str,
    redact_enabled: bool = True,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Ingest an in-memory OTLP/JSON payload (Phase 6 HTTP route entrypoint).

    Same contract as :func:`ingest_otel_file`; the file vs stream split is
    purely the source of the iterator. PII redaction, resolution, and
    runtime_* upserts are identical.
    """
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        raise FileNotFoundError(f"Index database not found: {db_path}")
    return _ingest_otel_iter(
        db_path=db_path_obj,
        spans=iter_otel_from_text(text),
        redact_enabled=redact_enabled,
        max_rows=max_rows,
    )


def _ingest_otel_iter(
    *,
    db_path: Path,
    spans: Iterable[OtelSpan],
    redact_enabled: bool,
    max_rows: int,
) -> dict[str, Any]:
    """Shared consumer: drive the resolve→aggregate→persist pipeline.

    Used by both the file-based ``ingest_otel_file`` and the HTTP-based
    ``ingest_otel_stream``. Behaviour is identical so the wire format and
    the file format remain bit-for-bit interchangeable.
    """
    aggregator = _BatchAggregator()
    unmapped_reasons: dict[str, int] = {}
    redactions_fired: dict[str, int] = {}
    records = 0

    for span in spans:
        records += 1
        if redact_enabled and span.extra:
            redacted_extra, fired_labels = redact_trace_record(
                {"extra": span.extra}, source="otel"
            )
            for label in fired_labels:
                redactions_fired[label] = redactions_fired.get(label, 0) + 1
            del redacted_extra
        if not span.file_path and not span.function_name:
            unmapped_reasons["no_code_attrs"] = unmapped_reasons.get("no_code_attrs", 0) + 1
            aggregator.unmapped_inc(span)
            continue
        symbol_id = _resolve_with_conn(
            db_path,
            span.file_path or "",
            span.line_no,
            span.function_name,
        )
        if symbol_id is None:
            unmapped_reasons["no_match"] = unmapped_reasons.get("no_match", 0) + 1
            aggregator.unmapped_inc(span)
            continue
        aggregator.mapped_inc(symbol_id, span.duration_ms)

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
        "mapped": aggregator.mapped_count,
        "unmapped": aggregator.unmapped_count,
        "redactions_fired": redactions_fired,
        "unmapped_reasons": unmapped_reasons,
        "evicted": evicted,
        "ingest_id": ingest_id,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _BatchAggregator:
    """Per-batch tally: per-symbol count + duration list, plus unmapped list."""

    def __init__(self) -> None:
        # symbol_id → list[duration_ms]
        self._durations: dict[str, list[float]] = {}
        # symbol_id → count (also = len(durations) when all spans had a duration)
        self._counts: dict[str, int] = {}
        # (file_path, line_no, function_name) → count
        self._unmapped: dict[tuple[str, Optional[int], str], int] = {}

    def mapped_inc(self, symbol_id: str, duration_ms: Optional[float]) -> None:
        self._counts[symbol_id] = self._counts.get(symbol_id, 0) + 1
        if duration_ms is not None:
            self._durations.setdefault(symbol_id, []).append(duration_ms)

    def unmapped_inc(self, span: OtelSpan) -> None:
        key = (span.file_path or "", span.line_no, span.function_name or "")
        self._unmapped[key] = self._unmapped.get(key, 0) + 1

    @property
    def mapped_count(self) -> int:
        return sum(self._counts.values())

    @property
    def unmapped_count(self) -> int:
        return sum(self._unmapped.values())

    def iter_calls(self) -> list[tuple[str, int, Optional[float], Optional[float]]]:
        """Yield (symbol_id, count, p50_ms, p95_ms) tuples."""
        out: list[tuple[str, int, Optional[float], Optional[float]]] = []
        for sid, count in self._counts.items():
            durations = self._durations.get(sid, [])
            p50, p95 = _percentiles(durations)
            out.append((sid, count, p50, p95))
        return out

    def iter_unmapped(self) -> list[tuple[str, Optional[int], str, int]]:
        return [(f, l, n, c) for (f, l, n), c in self._unmapped.items()]


def _percentiles(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    """Compute p50 / p95 from a duration list. Returns (None, None) if empty.

    Uses nearest-rank for simplicity and predictability on small batches.
    """
    if not values:
        return None, None
    sorted_v = sorted(values)
    n = len(sorted_v)
    p50 = sorted_v[max(0, (n - 1) // 2)]
    p95_idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    p95 = sorted_v[p95_idx]
    return float(p50), float(p95)


def _utc_now() -> str:
    """ISO-8601 UTC timestamp for the *_seen columns."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_with_conn(
    db_path: Path,
    file_path: str,
    line_no: Optional[int],
    function_name: Optional[str],
) -> Optional[str]:
    """Open a short-lived read-only connection just for resolution.

    A long-lived writer connection runs in ``_persist``; for the
    resolve loop we use a short-lived read-only connection so the
    persist step can take the writer slot without contention.
    """
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
    """Bulk-write the aggregator output. Returns the FIFO-eviction count."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        # Upsert mapped calls
        _calls_store.upsert_calls(
            conn, "otel", aggregator.iter_calls(), now=now, ingest_id=ingest_id
        )

        # Record unmapped spans for diagnostics
        conn.executemany(
            """
            INSERT INTO runtime_unmapped (file_path, line_no, function_name, source, count, last_seen)
            VALUES (?, ?, ?, 'otel', ?, ?)
            ON CONFLICT(file_path, line_no, function_name, source) DO UPDATE SET
                count = count + excluded.count,
                last_seen = excluded.last_seen
            """,
            [
                (file_path, line_no, function_name, count, now)
                for file_path, line_no, function_name, count in aggregator.iter_unmapped()
            ],
        )

        # Record redaction labels (forensic accounting)
        conn.executemany(
            """
            INSERT INTO runtime_redaction_log (source, pattern, redaction_count, last_redacted)
            VALUES ('otel', ?, ?, ?)
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
    """Trim runtime_calls + runtime_unmapped down to ``max_rows`` each.

    Eviction policy: oldest ``last_seen`` first. Trims in 1,000-row
    batches per the perf-telemetry convention (see token_tracker.py).
    Returns the number of rows evicted across both tables.
    """
    if max_rows <= 0:
        return 0
    evicted = 0
    for table in ("runtime_calls", "runtime_unmapped"):
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        if n <= max_rows:
            continue
        # Trim down to max_rows by deleting oldest rows
        excess = n - max_rows
        if table == "runtime_calls":
            cur = conn.execute(
                """
                DELETE FROM runtime_calls
                WHERE rowid IN (
                    SELECT rowid FROM runtime_calls
                    ORDER BY last_seen ASC, rowid ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
        else:
            cur = conn.execute(
                """
                DELETE FROM runtime_unmapped
                WHERE rowid IN (
                    SELECT rowid FROM runtime_unmapped
                    ORDER BY last_seen ASC, rowid ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
        evicted += cur.rowcount or 0
    return evicted
