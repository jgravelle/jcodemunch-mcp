"""Aggregate collected session_yield rows into the P0 redelivery artifact.

The number this produces decides F-15 P2 under a rule that was pre-registered
before any data existed (docs/prd-cue-anchored-delivery.md §3/§7):

    < 10%   P2 does not ship. Annotation alone is the product, and we say so.
    10-25%  P2 ships opt-in.
    > 25%   opt-in-by-default becomes a live question for a later release.

Usage:

    PYTHONPATH=src python benchmarks/anchor/measure.py
    PYTHONPATH=src python benchmarks/anchor/measure.py --db ~/.code-index/telemetry.db
    PYTHONPATH=src python benchmarks/anchor/measure.py --out benchmarks/anchor/results.json

Rows come from real sessions, and only when `perf_telemetry_enabled` is on --
there is no synthetic corpus here on purpose. Redelivery measures whether an
agent re-fetches what it already holds; a scripted session would encode the
answer in the script, which is why the harness reads observed sessions instead
of generating them.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# A pooled rate over very few deliveries is noise wearing a decimal point. The
# threshold is a floor for REPORTING a verdict, not a target to collect toward.
MIN_DELIVERIES = 200
MIN_SESSIONS = 5


def _default_db() -> Path:
    return Path.home() / ".code-index" / "telemetry.db"


def collect(db: Path) -> dict:
    """Pool counts across sessions. Returns the artifact dict."""
    if not db.exists():
        return {"error": f"no telemetry db at {db}", "sessions": 0, "deliveries": 0}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        try:
            rows = conn.execute(
                "SELECT session_uid, started_at, updated_at, deliveries,"
                " distinct_symbols, redelivered_symbols, redelivered_tokens_est,"
                " full_source_symbols FROM session_yield"
            ).fetchall()
        except sqlite3.OperationalError:
            return {
                "error": "session_yield table absent -- nothing collected yet",
                "sessions": 0,
                "deliveries": 0,
            }
    finally:
        conn.close()

    if not rows:
        return {"error": "session_yield is empty", "sessions": 0, "deliveries": 0}

    deliveries = sum(r[3] for r in rows)
    distinct = sum(r[4] for r in rows)
    redelivered_symbols = sum(r[5] for r in rows)
    redelivered_tokens = sum(r[6] for r in rows)
    full_source = sum(r[7] for r in rows)

    # POOLED, not the mean of per-session rates: a 3-delivery session must not
    # weigh the same as a 300-delivery one.
    rate = (deliveries - distinct) / deliveries if deliveries else 0.0

    art = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions": len(rows),
        "deliveries": deliveries,
        "distinct_symbols": distinct,
        "redelivered_symbols": redelivered_symbols,
        "redelivered_tokens_est": redelivered_tokens,
        "full_source_symbols": full_source,
        "redelivery_rate": round(rate, 4),
        "span": {
            "first": datetime.fromtimestamp(
                min(r[1] for r in rows), timezone.utc
            ).strftime("%Y-%m-%d"),
            "last": datetime.fromtimestamp(
                max(r[2] for r in rows), timezone.utc
            ).strftime("%Y-%m-%d"),
        },
    }

    sufficient = deliveries >= MIN_DELIVERIES and len(rows) >= MIN_SESSIONS
    art["sufficient"] = sufficient
    art["thresholds"] = {"min_deliveries": MIN_DELIVERIES, "min_sessions": MIN_SESSIONS}
    if not sufficient:
        # Absence of enough data is its own state -- NOT a verdict of "low".
        art["verdict"] = "insufficient_data"
        art["verdict_detail"] = (
            f"{deliveries} deliveries over {len(rows)} sessions; need "
            f"{MIN_DELIVERIES} over {MIN_SESSIONS}. No P2 decision is licensed yet."
        )
    elif rate < 0.10:
        art["verdict"] = "kill_p2"
        art["verdict_detail"] = "< 10%: P2 does not ship; annotation alone is the product."
    elif rate <= 0.25:
        art["verdict"] = "ship_p2_opt_in"
        art["verdict_detail"] = "10-25%: P2 ships opt-in."
    else:
        art["verdict"] = "opt_in_by_default_live"
        art["verdict_detail"] = "> 25%: opt-in-by-default is a live question for a later release."
    return art


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="telemetry db (default ~/.code-index/telemetry.db)")
    ap.add_argument("--out", default=None, help="write the artifact JSON here")
    args = ap.parse_args()

    art = collect(Path(args.db).expanduser() if args.db else _default_db())
    text = json.dumps(art, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
