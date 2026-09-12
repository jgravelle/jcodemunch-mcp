"""Shared reader for the ``diagnostics`` snapshot (compiler diagnostics mapped
to symbols). One honest-empty, staleness-aware entry point so each consumer
(check_edit_safe, get_changed_symbols, get_pr_risk_profile,
get_symbol_provenance) writes only its own rendering, the ``_scip_consume``
pattern.

Three rules every consumer inherits from here:

* ``None`` from ``load_symbol_diagnostics`` means NO DATA (table absent on a
  database that predates it, or empty because nothing was ingested). A
  consumer omits its block; it never renders ``errors: 0`` for a symbol no
  checker ran on. A symbol the checker DID run on and found clean gets a real
  zero, because the table has rows and this symbol has none.
* ``diagnostics_currency`` is tri-state. ``None`` is could-not-establish and
  is never ``False``: an ingest with no readable HEAD, or a source root whose
  HEAD cannot be read now, is UNKNOWN, not stale (``freshness.py``'s rule).
* Every reader is read-only and byte-identical no-op when there is no data.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from ..storage.generation import connect_readonly


def diagnostics_currency(as_of: Optional[str], live_head: Optional[str]) -> Optional[bool]:
    """True when the snapshot HEAD equals the live HEAD, False when both are
    known and differ, None when either side is unknown."""
    if not as_of or not live_head:
        return None
    return as_of == live_head


def live_git_head(source_root: Optional[str]) -> Optional[str]:
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
    return p.stdout.strip() or None


def _open(db_path) -> Optional[sqlite3.Connection]:
    try:
        if not Path(db_path).exists():
            return None
        conn = connect_readonly(Path(db_path), isolation_level="")
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT 1 FROM diagnostics LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return None
    if row is None:
        conn.close()
        return None
    return conn


def diagnostics_snapshot(db_path) -> Optional[dict]:
    """``{as_of, ingested_at, tools}`` for the stored snapshot, or None when
    there is no data. ``as_of`` is the newest ingest's HEAD (None if unknown)."""
    conn = _open(db_path)
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT tool, MAX(ingested_at) AS at, MAX(git_head) AS head FROM diagnostics GROUP BY tool"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    tools = sorted(r["tool"] for r in rows)
    newest = max(rows, key=lambda r: r["at"] or "")
    return {
        "as_of": newest["head"] or None,
        "ingested_at": newest["at"],
        "tools": tools,
    }


def load_symbol_diagnostics(db_path, symbol_ids: list[str]) -> Optional[dict[str, dict]]:
    """Per-symbol rollup for ``symbol_ids``, or None when there is NO DATA.

    A symbol with data but no rows is present with zeros: the checker ran and
    found nothing there. Shape per symbol::

        {"errors": n, "warnings": n, "infos": n, "tools": [...],
         "codes": [{"tool", "code", "severity", "count", "sample_message"}, ...]}
    """
    conn = _open(db_path)
    if conn is None:
        return None
    out: dict[str, dict] = {}
    try:
        ids = [s for s in symbol_ids if s]
        for i in range(0, len(ids), 900):  # SQLITE_MAX_VARIABLE_NUMBER
            chunk = ids[i : i + 900]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT symbol_id, tool, severity, code, count, sample_message "
                f"FROM diagnostics WHERE symbol_id IN ({placeholders}) "
                f"ORDER BY symbol_id, severity, tool, code",
                tuple(chunk),
            ).fetchall()
            for r in rows:
                slot = out.setdefault(
                    r["symbol_id"],
                    {"errors": 0, "warnings": 0, "infos": 0, "tools": [], "codes": []},
                )
                sev = r["severity"]
                key = {"error": "errors", "warning": "warnings"}.get(sev, "infos")
                slot[key] += int(r["count"] or 0)
                if r["tool"] not in slot["tools"]:
                    slot["tools"].append(r["tool"])
                slot["codes"].append(
                    {
                        "tool": r["tool"],
                        "code": r["code"],
                        "severity": sev,
                        "count": int(r["count"] or 0),
                        "sample_message": r["sample_message"],
                    }
                )
    finally:
        conn.close()
    for sid in symbol_ids:
        if sid and sid not in out:
            out[sid] = {"errors": 0, "warnings": 0, "infos": 0, "tools": [], "codes": []}
    for v in out.values():
        v["tools"].sort()
    return out


def summarise(entry: dict) -> str:
    """``mypy arg-type ×2, pyright reportArgumentType ×1`` — the detail string
    a blocker or a recommendation carries so the reader knows WHICH checker
    said what without opening the tool's output."""
    parts = []
    for c in entry.get("codes", []):
        if c["severity"] != "error":
            continue
        label = f"{c['tool']} {c['code']}".strip()
        parts.append(f"{label} ×{c['count']}" if c["count"] > 1 else label)
    return ", ".join(parts)
