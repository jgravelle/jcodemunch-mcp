"""Runtime confidence probe (Phase 2).

Mirrors the FreshnessProbe pattern in ``retrieval/freshness.py``:
construct once per tool invocation, hand it the result list, get
per-result annotation + a summary block for ``_meta``.

Per-result field: ``_runtime_confidence`` ∈
  - ``confirmed``     — at least one row in ``runtime_calls`` for this symbol_id
  - ``declared_only`` — symbol is in the graph but has no runtime evidence
  - ``unmapped``      — runtime data points at a symbol the graph does not
                        contain (reflective dispatch, missed AST extract).
                        Phase 2 surfaces use ``confirmed`` / ``declared_only``;
                        ``unmapped`` is reserved for ``find_unused_paths`` and
                        peers in Phase 3.

Summary block (placed under ``_meta.runtime_freshness``):
  ``{
      'sources': sorted list of trace sources contributing
                 (e.g. ['otel'] today; ['otel', 'sql_log'] in Phase 4),
      'last_seen': ISO-8601 most-recent ``last_seen`` across the result set,
      'coverage_pct': integer % of returned symbols with runtime evidence,
  }``

**Zero-cost when no traces ingested.** The probe checks for any row in
``runtime_calls`` at construction; if absent, ``annotate()`` and
``summary()`` become no-ops and no field is added to results — the
response shape is identical to the pre-Phase-2 contract.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable, Optional

from ..storage import generation as _generation

logger = logging.getLogger(__name__)

_CONFIRMED = "confirmed"
_DECLARED = "declared_only"
_UNMAPPED = "unmapped"


class RuntimeConfidenceProbe:
    """Per-call probe that stamps runtime confidence onto result entries.

    Construct once per tool invocation. Pass an open SQLite connection on
    the per-repo index database — typically taken from the existing
    storage helpers used by the surrounding tool.

    The probe issues at most two queries per ``annotate()`` call:
      1. Existence check on ``runtime_calls`` (cached for the lifetime of
         the probe).
      2. One bulk lookup over the symbol_ids in the result set (or
         ``WHERE symbol_id IN (?, ?, ...)``).

    A probe instance is safe to reuse across multiple ``annotate()``
    calls within one tool invocation, but should not be reused across
    tool invocations.
    """

    def __init__(self, conn: Optional[sqlite3.Connection]) -> None:
        self._conn = conn
        self._has_runtime: Optional[bool] = None

    @property
    def has_runtime(self) -> bool:
        """True iff the per-repo database has at least one row in ``runtime_calls``."""
        if self._has_runtime is None:
            self._has_runtime = self._probe_has_runtime()
        return self._has_runtime

    def _probe_has_runtime(self) -> bool:
        if self._conn is None:
            return False
        try:
            row = self._conn.execute("SELECT 1 FROM runtime_calls LIMIT 1").fetchone()
            return row is not None
        except sqlite3.Error:
            # Schema missing (pre-v14 DB) — treat as no runtime
            logger.debug("runtime_calls probe failed; treating as no-runtime", exc_info=True)
            return False

    # ----- annotation -------------------------------------------------

    def annotate(
        self,
        entries: list[dict],
        *,
        id_field: str = "id",
    ) -> list[dict]:
        """Stamp ``_runtime_confidence`` on each entry.

        No-op when ``has_runtime`` is False (zero-cost contract).

        Args:
            entries: Result list from the calling tool. Each entry should
                expose its ``symbol_id`` under the ``id_field`` key.
            id_field: Key under which each entry stores its symbol_id.
                ``find_references`` returns ``symbol_id`` directly; most
                others use ``id``.

        Returns:
            The same list (chaining-friendly).
        """
        if not self.has_runtime or not entries or self._conn is None:
            return entries
        ids = [e.get(id_field) for e in entries if isinstance(e, dict) and e.get(id_field)]
        if not ids:
            return entries
        confirmed_ids = self._lookup_confirmed(ids)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            sid = entry.get(id_field)
            if sid and sid in confirmed_ids:
                entry["_runtime_confidence"] = _CONFIRMED
            else:
                entry["_runtime_confidence"] = _DECLARED
        return entries

    def _lookup_confirmed(self, ids: Iterable[str]) -> set[str]:
        unique = list({i for i in ids if i})
        if not unique:
            return set()
        # Chunk to stay under the SQLite expression-tree depth limit
        # (~1000 in mainline, lower on some platforms — chunk at 500 to
        # leave a comfortable margin and let the planner cache a single plan).
        confirmed: set[str] = set()
        chunk = 500
        for start in range(0, len(unique), chunk):
            batch = unique[start : start + chunk]
            placeholders = ",".join("?" * len(batch))
            rows = self._conn.execute(
                f"SELECT DISTINCT symbol_id FROM runtime_calls WHERE symbol_id IN ({placeholders})",
                batch,
            ).fetchall()
            confirmed.update(r[0] for r in rows)
        return confirmed

    # ----- summary ----------------------------------------------------

    def summary(self, entries: list[dict]) -> dict:
        """Build the ``_meta.runtime_freshness`` block.

        Returns an empty dict when no runtime data exists — callers
        should gate on ``if probe.has_runtime`` and only attach the
        block when truthy, so absent runtime data leaves the response
        shape unchanged.
        """
        if not self.has_runtime or self._conn is None:
            return {}
        confirmed_ids = [
            e.get("id") or e.get("symbol_id")
            for e in entries
            if isinstance(e, dict) and e.get("_runtime_confidence") == _CONFIRMED
        ]
        confirmed_ids = [i for i in confirmed_ids if i]
        if not confirmed_ids:
            return {
                "sources": [],
                "last_seen": "",
                "coverage_pct": 0,
            }
        # One query for sources + max(last_seen) across the confirmed set.
        chunk = 500
        sources: set[str] = set()
        last_seen = ""
        for start in range(0, len(confirmed_ids), chunk):
            batch = confirmed_ids[start : start + chunk]
            placeholders = ",".join("?" * len(batch))
            rows = self._conn.execute(
                f"""
                SELECT source, MAX(last_seen) AS last_seen
                FROM runtime_calls
                WHERE symbol_id IN ({placeholders})
                GROUP BY source
                """,
                batch,
            ).fetchall()
            for r in rows:
                sources.add(r[0])
                if r[1] and r[1] > last_seen:
                    last_seen = r[1]
        total = sum(1 for e in entries if isinstance(e, dict))
        coverage_pct = round(100 * len(confirmed_ids) / max(1, total))
        return {
            "sources": sorted(sources),
            "last_seen": last_seen,
            "coverage_pct": coverage_pct,
        }


def attach_runtime_confidence(
    entries: list[dict],
    db_path: Optional[str],
    *,
    id_field: str = "id",
) -> dict:
    """One-call helper for tools that don't already hold a connection.

    Opens a short-lived read-only connection, runs the probe, returns
    the summary dict (empty when no runtime data; caller decides whether
    to attach to ``_meta``).
    """
    if not db_path:
        return {}
    try:
        # A read-only open must not create -shm/-wal, which would bump the
        # index db's mtime and invalidate the CodeIndex LRU cache
        # (test_register_edit::test_clears_bm25_cache). #398 Arc 1: it must
        # ALSO read the WAL when one is already there — `has_runtime` below is
        # an existence check, so an invisible un-checkpointed table drops the
        # runtime channel entirely rather than degrading it.
        conn = _generation.connect_readonly(db_path, isolation_level="")
    except sqlite3.Error:
        return {}
    try:
        probe = RuntimeConfidenceProbe(conn)
        if not probe.has_runtime:
            return {}
        probe.annotate(entries, id_field=id_field)
        return probe.summary(entries)
    finally:
        conn.close()


def attach_runtime_confidence_by_file(
    entries: list[dict],
    db_path: Optional[str],
    *,
    file_field: str = "file",
) -> dict:
    """File-level confidence variant for tools that return file references
    (find_references, find_importers) where per-symbol confidence does
    not apply. Stamps ``_runtime_confidence`` = ``confirmed`` when **any**
    indexed symbol in the file has at least one row in ``runtime_calls``.

    Same zero-cost contract: when ``runtime_calls`` is empty, the field
    is not added.
    """
    if not db_path:
        return {}
    try:
        # See attach_runtime_confidence: same sidecar-aware read contract, same
        # reason. #398 Arc 1.
        conn = _generation.connect_readonly(db_path, isolation_level="")
    except sqlite3.Error:
        return {}
    try:
        probe = RuntimeConfidenceProbe(conn)
        if not probe.has_runtime:
            return {}
        files = [e.get(file_field) for e in entries if isinstance(e, dict) and e.get(file_field)]
        if not files:
            return {}
        confirmed_files = _files_with_runtime(conn, files)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            f = entry.get(file_field)
            if f and f in confirmed_files:
                entry["_runtime_confidence"] = _CONFIRMED
            else:
                entry["_runtime_confidence"] = _DECLARED
        # Build summary keyed on file confirmations.
        confirmed_count = sum(
            1 for e in entries
            if isinstance(e, dict) and e.get("_runtime_confidence") == _CONFIRMED
        )
        # Collect sources + max(last_seen) for the confirmed set
        sources, last_seen = _summary_for_files(conn, sorted(confirmed_files))
        coverage_pct = round(100 * confirmed_count / max(1, sum(1 for e in entries if isinstance(e, dict))))
        return {
            "sources": sorted(sources),
            "last_seen": last_seen,
            "coverage_pct": coverage_pct,
        }
    finally:
        conn.close()


def _files_with_runtime(conn: sqlite3.Connection, files: list[str]) -> set[str]:
    """Return the subset of ``files`` that have at least one symbol with runtime hits."""
    unique = list({f for f in files if f})
    if not unique:
        return set()
    confirmed: set[str] = set()
    chunk = 500
    for start in range(0, len(unique), chunk):
        batch = unique[start : start + chunk]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"""
            SELECT DISTINCT s.file
            FROM symbols s
            JOIN runtime_calls rc ON rc.symbol_id = s.id
            WHERE s.file IN ({placeholders})
            """,
            batch,
        ).fetchall()
        confirmed.update(r[0] for r in rows)
    return confirmed


def _summary_for_files(
    conn: sqlite3.Connection, confirmed_files: list[str]
) -> tuple[set[str], str]:
    if not confirmed_files:
        return set(), ""
    sources: set[str] = set()
    last_seen = ""
    chunk = 500
    for start in range(0, len(confirmed_files), chunk):
        batch = confirmed_files[start : start + chunk]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"""
            SELECT rc.source, MAX(rc.last_seen) AS last_seen
            FROM runtime_calls rc
            JOIN symbols s ON s.id = rc.symbol_id
            WHERE s.file IN ({placeholders})
            GROUP BY rc.source
            """,
            batch,
        ).fetchall()
        for r in rows:
            sources.add(r[0])
            if r[1] and r[1] > last_seen:
                last_seen = r[1]
    return sources, last_seen


def symbol_hit_count(db_path, symbol_id: str) -> Optional[int]:
    """Total observed hits for one symbol across every trace source.

    THE ONE READER of ``runtime_calls.count`` for a single symbol (#717).
    ``check_delete_safe`` and ``get_group_contracts`` each carried a copy that
    asked for ``hit_count``, a column the table never had; both swallowed the
    error at DEBUG, so ingested evidence read as none and a delete preflight
    could certify a symbol with live traffic.

    Returns None for "no hits recorded" AND for "could not ask"; callers treat
    None as no runtime evidence, never as proof of absence. A query the schema
    rejects is logged at WARNING: that is drift in this package, not a
    condition of the user's data, and DEBUG is how it stayed hidden.
    """
    try:
        if not db_path.exists():
            return None
        conn = _generation.connect_readonly(db_path, isolation_level="")
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM runtime_calls WHERE symbol_id = ?",
                (symbol_id,),
            ).fetchone()
            return int(row[0]) if row and row[0] else None
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such column" in str(exc):
            logger.warning("runtime hit count query rejected by the schema: %s", exc)
        else:
            logger.debug("runtime hit count skipped: %s", exc, exc_info=True)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime hit count skipped: %s", exc, exc_info=True)
        return None
