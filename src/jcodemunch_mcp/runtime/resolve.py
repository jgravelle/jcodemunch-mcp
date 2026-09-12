"""Symbol resolution for runtime trace records.

Trace records arrive shaped as ``(file_path, line_no, function_name)`` —
the OTel `code.filepath` / `code.lineno` / `code.function` triple, or the
file/line/name extracted from a stack-frame line.  This module maps that
triple back to the indexed `symbol_id` it refers to.

Resolution strategy (best-effort, ordered):
  1. Exact (file, line) range match: smallest enclosing symbol whose
     ``line <= line_no <= COALESCE(end_line, line)``. Strongest signal.
  2. Exact (file, function_name) match when line is missing or the line
     range lookup misses (e.g., minified files, inlined dispatch).
  3. Suffix match on file path — accommodates trace records whose paths
     are absolute or rooted at a different prefix than the index. Falls
     through to a name-only match within the candidate file set.

A miss returns ``None`` and the ingest layer is responsible for recording
the unresolved record in ``runtime_unmapped`` for diagnostics. Phase 0 ships
the contract; Phase 1 wires it into an actual ingest pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_to_symbol_id(
    conn: sqlite3.Connection,
    file_path: str,
    line_no: Optional[int] = None,
    function_name: Optional[str] = None,
) -> Optional[str]:
    """Resolve a trace record's ``(file, line, function)`` to a symbol_id.

    Args:
        conn: Open SQLite connection on the per-repo index database.
        file_path: Path the trace record reports — absolute, repo-relative,
            or partial. Normalisation is best-effort: exact match first,
            then suffix match.
        line_no: Optional line number within ``file_path``. Strongest
            signal when present.
        function_name: Optional function/method name from the trace.
            Required as a fallback when line_no is missing or out of range.

    Returns:
        ``symbol_id`` (matching the ``symbols.id`` column) on a hit, else None.
    """
    if not file_path:
        return None

    # 1. Exact (file, line) range match
    if line_no is not None:
        row = conn.execute(
            """
            SELECT id FROM symbols
            WHERE file = ?
              AND line <= ?
              AND (end_line IS NULL OR end_line >= ?)
            ORDER BY (COALESCE(end_line, line) - line) ASC
            LIMIT 1
            """,
            (file_path, line_no, line_no),
        ).fetchone()
        if row is not None:
            return row["id"]

    # 2. Exact (file, name) match
    if function_name:
        row = conn.execute(
            "SELECT id FROM symbols WHERE file = ? AND name = ? LIMIT 1",
            (file_path, function_name),
        ).fetchone()
        if row is not None:
            return row["id"]

    # 3. Suffix match on file path — handles absolute trace paths against
    # repo-relative index paths. Strip leading segments until LIKE matches
    # something. Cap the iterations to bound worst-case.
    suffix = file_path.replace("\\", "/").lstrip("/")
    candidates: list[str] = []
    cut = suffix
    # Bounded by the segment count, not a constant: a checker on Windows emits
    # C:/Users/<u>/AppData/Local/Temp/... paths well past eight segments, and a
    # cap of 8 left every one of them unmapped (found by the diagnostics ingest).
    for _ in range(256):
        if not cut:
            break
        rows = conn.execute(
            "SELECT DISTINCT file FROM symbols WHERE file LIKE ? LIMIT 16",
            (f"%{cut}",),
        ).fetchall()
        candidates = [r["file"] for r in rows]
        if candidates:
            break
        cut = _strip_one_segment(cut)
    if not candidates:
        return None

    # Among the candidate files, retry the line-range / name lookup
    if line_no is not None:
        placeholders = ",".join("?" * len(candidates))
        row = conn.execute(
            f"""
            SELECT id FROM symbols
            WHERE file IN ({placeholders})
              AND line <= ?
              AND (end_line IS NULL OR end_line >= ?)
            ORDER BY (COALESCE(end_line, line) - line) ASC
            LIMIT 1
            """,
            (*candidates, line_no, line_no),
        ).fetchone()
        if row is not None:
            return row["id"]

    if function_name:
        placeholders = ",".join("?" * len(candidates))
        row = conn.execute(
            f"SELECT id FROM symbols WHERE file IN ({placeholders}) AND name = ? LIMIT 1",
            (*candidates, function_name),
        ).fetchone()
        if row is not None:
            return row["id"]

    return None


def _strip_one_segment(path: str) -> str:
    """Drop the leading path segment, returning '' once exhausted."""
    if "/" not in path:
        return ""
    return path.split("/", 1)[1]
