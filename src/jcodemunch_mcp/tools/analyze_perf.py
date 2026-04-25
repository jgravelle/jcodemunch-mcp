"""analyze_perf — surface tool latency and cache-hit telemetry.

Reads in-memory latency rings (always populated when call_tool fires) and,
if enabled, persisted rows from telemetry.db. No-op safe when no calls have
been recorded yet.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import token_tracker as _tt


_DEFAULT_TOP = 20


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(pct * len(sorted_vals))))
    return sorted_vals[idx]


def analyze_perf(
    window: str = "session",
    top: int = _DEFAULT_TOP,
    tool: Optional[str] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Return per-tool latency + cache-hit telemetry for the current session
    (and the persisted perf db if perf_telemetry_enabled is set).

    Args:
        window: ``session`` (in-memory ring), ``1h``, ``24h``, ``7d``, or ``all``.
                Anything other than ``session`` reads the perf SQLite db.
        top:    Cap on how many slowest tools to return (default 20).
        tool:   Restrict the analysis to a single tool name.
        storage_path: Optional override for the index storage root.
    """
    t0 = time.perf_counter()

    cache_stats = _tt.result_cache_stats()
    in_memory = _tt.latency_stats()
    if tool:
        in_memory = {k: v for k, v in in_memory.items() if k == tool}

    persisted: dict = {}
    persisted_meta: dict = {"source": "in_memory_only", "rows": 0}
    if window != "session":
        seconds_map = {
            "1h": 3600.0,
            "24h": 86_400.0,
            "7d": 7 * 86_400.0,
            "all": None,
        }
        if window not in seconds_map:
            return {
                "error": (
                    f"Invalid window {window!r}. Use one of: session, 1h, 24h, 7d, all."
                )
            }
        rows = _tt.perf_db_query(
            base_path=storage_path,
            window_seconds=seconds_map[window],
            tool=tool,
        )
        persisted_meta = {"source": "telemetry.db", "rows": len(rows), "window": window}
        # Aggregate by tool
        by_tool: dict[str, list[float]] = {}
        errors: dict[str, int] = {}
        for ts, t_name, dur, ok, _repo in rows:
            by_tool.setdefault(t_name, []).append(float(dur))
            if not ok:
                errors[t_name] = errors.get(t_name, 0) + 1
        for t_name, durs in by_tool.items():
            durs.sort()
            n = len(durs)
            persisted[t_name] = {
                "count": n,
                "p50_ms": round(_percentile(durs, 0.5), 2),
                "p95_ms": round(_percentile(durs, 0.95), 2),
                "max_ms": round(durs[-1], 2),
                "errors": errors.get(t_name, 0),
                "error_rate": round(errors.get(t_name, 0) / n, 3) if n else 0.0,
            }
        if not _tt._state and persisted_meta["rows"] == 0:  # type: ignore[attr-defined]
            persisted_meta["note"] = (
                "No persisted rows. Set config 'perf_telemetry_enabled': true "
                "or env JCODEMUNCH_PERF_TELEMETRY=1 to enable the SQLite sink."
            )

    # Pick the dataset to rank
    ranked_source = persisted if window != "session" else in_memory
    slowest = sorted(
        ranked_source.items(),
        key=lambda kv: kv[1].get("p95_ms", 0.0),
        reverse=True,
    )[:top]

    # Cache hit-rate ranked low → high (low rates point to cold caches)
    by_tool_cache = cache_stats.get("by_tool", {})
    coldest_caches = sorted(
        by_tool_cache.items(),
        key=lambda kv: kv[1].get("hit_rate", 0.0),
    )[:top]

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    return {
        "window": window,
        "tool": tool,
        "in_memory_session": in_memory,
        "persisted": persisted,
        "persisted_meta": persisted_meta,
        "slowest_by_p95": [
            {"tool": name, **stats} for name, stats in slowest
        ],
        "cache": {
            "totals": {
                "hits": cache_stats.get("total_hits", 0),
                "misses": cache_stats.get("total_misses", 0),
                "hit_rate": cache_stats.get("hit_rate", 0.0),
                "cached_entries": cache_stats.get("cached_entries", 0),
            },
            "coldest_by_tool": [
                {"tool": name, **stats} for name, stats in coldest_caches
            ],
        },
        "_meta": {"timing_ms": elapsed_ms},
    }
