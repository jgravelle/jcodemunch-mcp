"""Phase 4 SQL log ingest orchestrator: parse → redact → resolve → upsert.

Runs one SQL log import end-to-end:
  1. Stream queries from a pg_stat_statements CSV or generic JSON-Lines file.
  2. Optionally redact each query through ``redact_trace_record()``. The
     redaction labels (e.g., ``sql_string_literal``, ``ipv4_address``)
     are tallied for ``runtime_redaction_log``.
  3. Resolve each referenced table to one or more indexed symbols by file
     stem and exact-name match; bare-column references resolve only when
     they hit the ``dbt_columns`` metadata for one of the query's tables.
  4. Bulk-upsert per-symbol totals into ``runtime_calls`` (source='sql_log')
     and per-column totals into ``runtime_columns`` (source='sql_log').
     Unresolved tables go to ``runtime_unmapped``.
  5. Apply the same FIFO eviction the OTel ingestor uses (extended to
     also trim ``runtime_columns``).

The orchestrator never reaches the static index over the writer
connection — resolution opens a short-lived read-only connection so
the writer slot is unblocked, mirroring ``runtime/ingest.py``.

Forensic invariant: every row that lands in ``runtime_calls`` /
``runtime_columns`` for source='sql_log' is the redacted form of input
when ``redact_enabled=True``. The non-redacted SQL never reaches disk
(we only persist counts + symbol_ids), so the redaction is for
the in-memory string used to extract refs and for the redaction-log
forensic accounting.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from ..storage.generation import connect_readonly


from . import _calls_store
from .redact import redact_trace_record
from .sql_log import SqlQueryRecord, iter_sql_from_text, parse_sql_log_file

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_sql_log_file(
    *,
    db_path: str,
    file_path: str,
    redact_enabled: bool = True,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Ingest one SQL log file into the runtime tables.

    Args:
        db_path: Path to the per-repo SQLite database. Resolve from a
            higher layer via ``IndexStore._sqlite._db_path(owner, name)``.
        file_path: Path to the SQL log file. CSV → pg_stat_statements;
            JSON-Lines / .json / .log → generic SQL log; ``.gz`` is
            transparently decompressed and the inner extension dispatches.
        redact_enabled: Run each query through ``redact_trace_record()``
            before extracting tables / columns. Default True. Set False
            **only** for offline debugging on synthetic data — never on
            production logs.
        max_rows: Soft cap on rows in the runtime_* tables after this
            ingest completes. FIFO eviction in 1k-row batches once exceeded.

    Returns:
        ``{
            'records':           <queries seen>,
            'mapped':            <table refs resolved to a symbol_id>,
            'unmapped':          <table refs without a resolution>,
            'columns_recorded':  <column-pair upserts>,
            'redactions_fired':  {<label>: <count>, ...},
            'unmapped_reasons':  {'no_table_match' | 'parse_no_tables': <count>},
            'evicted':           <runtime_* rows trimmed by FIFO>,
        }``
    """
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        raise FileNotFoundError(f"Index database not found: {db_path}")
    return _ingest_sql_iter(
        db_path=db_path_obj,
        queries=parse_sql_log_file(file_path),
        redact_enabled=redact_enabled,
        max_rows=max_rows,
    )


def ingest_sql_log_stream(
    *,
    db_path: str,
    text: str,
    fmt: str = "auto",
    redact_enabled: bool = True,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Ingest an in-memory SQL log payload (Phase 6 HTTP route entrypoint).

    Same contract as :func:`ingest_sql_log_file`. ``fmt`` selects the
    parser dialect: ``'auto'`` (default), ``'csv'`` (pg_stat_statements),
    or ``'jsonl'`` (generic SQL JSON-Lines).
    """
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        raise FileNotFoundError(f"Index database not found: {db_path}")
    return _ingest_sql_iter(
        db_path=db_path_obj,
        queries=iter_sql_from_text(text, fmt=fmt),
        redact_enabled=redact_enabled,
        max_rows=max_rows,
    )


def _ingest_sql_iter(
    *,
    db_path: Path,
    queries: Iterable[SqlQueryRecord],
    redact_enabled: bool,
    max_rows: int,
) -> dict[str, Any]:
    """Shared consumer: drive resolve→aggregate→persist for SQL queries.

    Used by both ``ingest_sql_log_file`` and ``ingest_sql_log_stream``.
    """
    aggregator = _BatchAggregator()
    unmapped_reasons: dict[str, int] = {}
    redactions_fired: dict[str, int] = {}
    records = 0

    # Read-only metadata snapshot (model_files map + dbt_columns) used by the
    # resolver. One snapshot per ingest is fine — the index is never edited
    # while an ingest runs (single-writer).
    metadata = _load_resolver_metadata(db_path)

    for query in queries:
        records += 1
        sql_text = query.sql
        if redact_enabled:
            redacted, fired_labels = redact_trace_record({"sql": sql_text}, source="sql_log")
            for label in fired_labels:
                redactions_fired[label] = redactions_fired.get(label, 0) + 1
            # The redacted SQL is what gets re-parsed for tables/columns.
            sql_text_red = redacted.get("sql") if isinstance(redacted, dict) else sql_text
            if isinstance(sql_text_red, str):
                # Re-parse the redacted text so any redaction-induced ref
                # changes (rare; redaction targets values, not idents) are
                # reflected. Cheap regex pass.
                from .sql_log import _build_record  # type: ignore
                requery = _build_record(
                    sql=sql_text_red,
                    calls=query.calls,
                    total_ms=query.total_ms,
                    mean_ms=query.mean_ms,
                    timestamp=query.timestamp,
                )
                tables = requery.tables
                columns = requery.columns
            else:
                tables = query.tables
                columns = query.columns
        else:
            tables = query.tables
            columns = query.columns

        if not tables:
            unmapped_reasons["parse_no_tables"] = unmapped_reasons.get("parse_no_tables", 0) + 1
            continue

        # Resolve each referenced table to one or more symbol_ids
        any_table_resolved = False
        resolved_models: list[str] = []
        for table in tables:
            sids = _resolve_table_to_symbol_ids(metadata, table)
            if sids:
                any_table_resolved = True
                resolved_models.append(table)
                for sid in sids:
                    aggregator.mapped_inc(
                        symbol_id=sid,
                        calls=query.calls,
                        mean_ms=query.mean_ms,
                    )
            else:
                aggregator.unmapped_inc(table=table, count=query.calls)

        if not any_table_resolved:
            unmapped_reasons["no_table_match"] = unmapped_reasons.get("no_table_match", 0) + 1

        # Record column references — only those that the dbt_columns
        # metadata recognises against a resolved model. Unqualified columns
        # try every resolved model in order; first hit wins.
        for table_alias, col in columns:
            model_target = _resolve_column_target(
                metadata=metadata,
                table_alias=table_alias,
                col=col,
                query_models=resolved_models,
            )
            if model_target is not None:
                aggregator.column_inc(
                    model_name=model_target,
                    column_name=col,
                    count=query.calls,
                )

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
        "mapped": aggregator.mapped_call_total,
        "unmapped": aggregator.unmapped_call_total,
        "columns_recorded": aggregator.column_total,
        "redactions_fired": redactions_fired,
        "unmapped_reasons": unmapped_reasons,
        "evicted": evicted,
        "ingest_id": ingest_id,
    }


# ---------------------------------------------------------------------------
# Resolver metadata snapshot
# ---------------------------------------------------------------------------


class _ResolverMetadata:
    """Lightweight snapshot of just what the SQL resolver needs.

    Three lookups:
      * ``stem_to_symbol_ids[<file_stem>]`` → list of symbol IDs for the
        SQL file whose path ends in ``<file_stem>.sql``. dbt models name
        their compiled output by file stem, so this is the canonical map.
      * ``name_to_symbol_ids[<symbol_name>]`` → list of symbol IDs whose
        ``name`` exactly equals the queried table. Catches CREATE TABLE /
        CREATE VIEW symbols extracted from a non-dbt SQL repo.
      * ``dbt_columns[<model>]`` → set of column names declared for that
        model in the dbt_columns context metadata.
    """

    __slots__ = ("stem_to_symbol_ids", "name_to_symbol_ids", "dbt_columns")

    def __init__(self) -> None:
        self.stem_to_symbol_ids: dict[str, list[str]] = {}
        self.name_to_symbol_ids: dict[str, list[str]] = {}
        self.dbt_columns: dict[str, set[str]] = {}


def _load_resolver_metadata(db_path: Path) -> _ResolverMetadata:
    """Build the three resolver lookups from the index DB."""
    meta = _ResolverMetadata()
    conn = connect_readonly(db_path, isolation_level="")
    conn.row_factory = sqlite3.Row
    try:
        # File-stem → symbol_ids for every .sql file
        for row in conn.execute(
            "SELECT id, name, file FROM symbols WHERE file LIKE '%.sql'"
        ):
            file_path = row["file"] or ""
            stem = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            if stem:
                meta.stem_to_symbol_ids.setdefault(stem, []).append(row["id"])
            sym_name = row["name"] or ""
            if sym_name:
                meta.name_to_symbol_ids.setdefault(sym_name, []).append(row["id"])

        # Catch CREATE TABLE / CREATE VIEW in non-SQL parsers (vanishingly rare,
        # but keeps the contract honest): also index every kind in {table, view, model}.
        for row in conn.execute(
            "SELECT id, name FROM symbols WHERE kind IN ('table','view','model')"
        ):
            sym_name = row["name"] or ""
            if sym_name and row["id"] not in meta.name_to_symbol_ids.get(sym_name, []):
                meta.name_to_symbol_ids.setdefault(sym_name, []).append(row["id"])

        # dbt_columns from context_metadata
        meta_row = conn.execute(
            "SELECT value FROM meta WHERE key = 'context_metadata'"
        ).fetchone()
        if meta_row and meta_row["value"]:
            try:
                ctx = json.loads(meta_row["value"])
            except (TypeError, ValueError):
                ctx = {}
            if isinstance(ctx, dict):
                # Any provider that ends in _columns (dbt_columns, sqlmesh_columns, ...)
                for key, value in ctx.items():
                    if key.endswith("_columns") and isinstance(value, dict):
                        for model_name, cols in value.items():
                            if not isinstance(cols, dict):
                                continue
                            existing = meta.dbt_columns.setdefault(model_name, set())
                            existing.update(cols.keys())
    finally:
        conn.close()
    return meta


def _resolve_table_to_symbol_ids(meta: _ResolverMetadata, table: str) -> list[str]:
    """Map a referenced table name to one or more indexed symbol IDs.

    Order: file-stem match wins over exact-name match. We intentionally
    union when the same table maps to multiple symbol IDs — different
    parsers can extract more than one symbol per .sql file (e.g., a
    CTE alongside the model body); attributing the call to all of them
    is correct because they all live in the file the query touched.
    """
    out: list[str] = []
    for sid in meta.stem_to_symbol_ids.get(table, []):
        if sid not in out:
            out.append(sid)
    for sid in meta.name_to_symbol_ids.get(table, []):
        if sid not in out:
            out.append(sid)
    return out


def _resolve_column_target(
    *,
    metadata: _ResolverMetadata,
    table_alias: str,
    col: str,
    query_models: list[str],
) -> Optional[str]:
    """Pick the model a column reference belongs to, or None.

    Priority:
      1. ``alias.col`` where alias matches a dbt_columns model and col is declared.
      2. Bare ``col`` against any of this query's resolved models, first match wins.
    Returns the model name (the runtime_columns key) or None when no model in
    the query declares that column.
    """
    if table_alias and table_alias in metadata.dbt_columns and col in metadata.dbt_columns[table_alias]:
        return table_alias
    for m in query_models:
        if m in metadata.dbt_columns and col in metadata.dbt_columns[m]:
            return m
    return None


# ---------------------------------------------------------------------------
# Aggregator + persistence
# ---------------------------------------------------------------------------


class _BatchAggregator:
    """Per-batch tally for runtime_calls + runtime_columns + runtime_unmapped."""

    def __init__(self) -> None:
        # symbol_id → call count
        self._calls: dict[str, int] = {}
        # symbol_id → list of mean_ms (for an end-of-batch p50/p95 estimate)
        self._call_durations: dict[str, list[float]] = {}
        # (model_name, column_name) → count
        self._columns: dict[tuple[str, str], int] = {}
        # table_name → count
        self._unmapped: dict[str, int] = {}

    def mapped_inc(self, *, symbol_id: str, calls: int, mean_ms: Optional[float]) -> None:
        self._calls[symbol_id] = self._calls.get(symbol_id, 0) + calls
        if mean_ms is not None:
            self._call_durations.setdefault(symbol_id, []).append(mean_ms)

    def column_inc(self, *, model_name: str, column_name: str, count: int) -> None:
        key = (model_name, column_name)
        self._columns[key] = self._columns.get(key, 0) + count

    def unmapped_inc(self, *, table: str, count: int) -> None:
        self._unmapped[table] = self._unmapped.get(table, 0) + count

    @property
    def mapped_call_total(self) -> int:
        return sum(self._calls.values())

    @property
    def unmapped_call_total(self) -> int:
        return sum(self._unmapped.values())

    @property
    def column_total(self) -> int:
        return sum(self._columns.values())

    def iter_calls(self) -> list[tuple[str, int, Optional[float], Optional[float]]]:
        out: list[tuple[str, int, Optional[float], Optional[float]]] = []
        for sid, count in self._calls.items():
            durations = self._call_durations.get(sid, [])
            p50, p95 = _percentiles(durations)
            out.append((sid, count, p50, p95))
        return out

    def iter_columns(self) -> list[tuple[str, str, int]]:
        return [(m, c, n) for (m, c), n in self._columns.items()]

    def iter_unmapped(self) -> list[tuple[str, int]]:
        return list(self._unmapped.items())


def _percentiles(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    if not values:
        return None, None
    s = sorted(values)
    n = len(s)
    p50 = s[max(0, (n - 1) // 2)]
    p95_idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    return float(p50), float(s[p95_idx])


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

        # runtime_calls upsert
        _calls_store.upsert_calls(
            conn, "sql_log", aggregator.iter_calls(), now=now, ingest_id=ingest_id
        )

        # runtime_columns upsert
        conn.executemany(
            """
            INSERT INTO runtime_columns (model_name, column_name, source, count, first_seen, last_seen)
            VALUES (?, ?, 'sql_log', ?, ?, ?)
            ON CONFLICT(model_name, column_name, source) DO UPDATE SET
                count = count + excluded.count,
                last_seen = excluded.last_seen
            """,
            [
                (model, col, n, now, now)
                for model, col, n in aggregator.iter_columns()
            ],
        )

        # runtime_unmapped: store unresolved tables as ``(file=NULL, line=NULL,
        # function=table_name, source='sql_log')`` — the unmapped table reuses
        # the OTel-shaped schema but we pack the table reference into
        # function_name so the row still satisfies the PK.
        conn.executemany(
            """
            INSERT INTO runtime_unmapped (file_path, line_no, function_name, source, count, last_seen)
            VALUES ('', NULL, ?, 'sql_log', ?, ?)
            ON CONFLICT(file_path, line_no, function_name, source) DO UPDATE SET
                count = count + excluded.count,
                last_seen = excluded.last_seen
            """,
            [(table, count, now) for table, count in aggregator.iter_unmapped()],
        )

        # Redaction-log accounting
        conn.executemany(
            """
            INSERT INTO runtime_redaction_log (source, pattern, redaction_count, last_redacted)
            VALUES ('sql_log', ?, ?, ?)
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
    """Trim runtime_calls / runtime_columns / runtime_unmapped down to max_rows."""
    if max_rows <= 0:
        return 0
    evicted = 0
    for table in ("runtime_calls", "runtime_columns", "runtime_unmapped"):
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
