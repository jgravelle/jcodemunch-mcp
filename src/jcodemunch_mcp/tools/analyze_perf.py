"""analyze_perf — surface tool latency and cache-hit telemetry.

Reads in-memory latency rings (always populated when call_tool fires) and,
if enabled, persisted rows from telemetry.db. No-op safe when no calls have
been recorded yet.

Optional ``compare_release`` parameter loads a baseline snapshot from
``benchmarks/token_baselines/v{X}.json`` (created by
``capture_token_baseline.py``) and reports per-tool deltas in tokens_saved
and latency vs the current session. A field the baseline never recorded comes
back as ``None`` with a reason in ``not_comparable`` -- never as a delta
against zero.

Two rankings, and they answer different questions: ``slowest_by_p95`` is
per-call latency, ``heaviest_by_total_ms`` is the wall-clock each tool actually
consumed. The orderings disagree whenever a fast tool is called often.

⚠ The per-tool latency shape comes from ``token_tracker.latency_bucket``; this
module holds no percentile of its own.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from ..storage import token_tracker as _tt
from ..retrieval.ledger_trust import (
    identity_label_is_trustworthy as _identity_label_is_trustworthy,
    semantic_label_is_trustworthy as _semantic_label_is_trustworthy,
)


_DEFAULT_TOP = 20


def _baseline_path(version: str) -> Path:
    """Resolve ``benchmarks/token_baselines/v{version}.json`` from repo root."""
    here = Path(__file__).resolve()
    repo_root = here.parents[3]  # tools/.. /jcodemunch_mcp/.. /src/.. /<root>
    # Walk up until we find a sibling 'benchmarks' dir (works in both
    # editable installs and a checked-out clone).
    for ancestor in [repo_root, *repo_root.parents]:
        candidate = ancestor / "benchmarks" / "token_baselines" / f"v{version}.json"
        if candidate.exists():
            return candidate
    return repo_root / "benchmarks" / "token_baselines" / f"v{version}.json"


def _diff_baseline(
    baseline: dict,
    current_latency: dict,
    current_breakdown: dict,
) -> dict:
    """Compute per-tool deltas between baseline snapshot and live session.

    ⚠⚠ **A DELTA AGAINST AN ABSENT MEASUREMENT IS `None`, NEVER THE CURRENT
    VALUE.** This read `float(b.get("p50_ms", 0.0))`, and a baseline entry may
    legitimately carry `tokens_saved` alone -- the only baseline that ships,
    `v1.108.163.json`, carries exactly that for all three of its tools. So the
    zero stood in for a measurement nobody took and the result was published as
    `p50_delta_ms`, a name that asserts a comparison happened. Measured against
    that file, a tool at p95 900 ms reported `p95_delta_ms: 900.0` -- read by
    any human as a 900 ms regression against a release that never timed it.
    ⚠ `.get(key, default)` is not a None guard either; the default never fires
    for a key that is present and null.

    ⚠ **Calls and tokens have a meaningful zero on the CURRENT side; latency
    does not.** A tool nobody called this session really did save nothing and
    really was called zero times, so those deltas stay computable. It has no
    p50, and inventing one would reintroduce the defect from the other end.

    ⚠ `not_comparable` names every field that could not be differenced, so a
    reader sees WHY a delta is missing rather than an unexplained `null`.
    """
    out: dict = {}
    base_tools = baseline.get("tools", {})
    all_tools = set(base_tools) | set(current_latency) | set(current_breakdown)
    for tool in sorted(all_tools):
        b = base_tools.get(tool, {})
        cur_lat = current_latency.get(tool, {})
        entry: dict = {"in_baseline": tool in base_tools}
        missing: dict = {}

        def _delta(name: str, base_key: str, cur_val, cast=float):
            base_val = b.get(base_key)
            if base_val is None:
                missing[base_key] = (
                    "absent_in_both" if cur_val is None else "absent_in_baseline"
                )
                entry[name] = None
            elif cur_val is None:
                missing[base_key] = "absent_in_current"
                entry[name] = None
            else:
                delta = cast(cur_val) - cast(base_val)
                entry[name] = delta if cast is int else round(delta, 2)

        _delta("tokens_saved_delta", "tokens_saved",
               int(current_breakdown.get(tool, 0)), int)
        _delta("p50_delta_ms", "p50_ms", cur_lat.get("p50_ms"))
        _delta("p95_delta_ms", "p95_ms", cur_lat.get("p95_ms"))
        _delta("calls_delta", "calls", int(cur_lat.get("count", 0)), int)

        if missing:
            entry["not_comparable"] = missing
        out[tool] = entry
    return out


def _rank_by_total(stats: dict, top: int) -> "tuple[list[dict], dict]":
    """Rank tools by the wall-clock they actually consumed, with shares.

    ⚠⚠ The companion to `slowest_by_p95`, not a replacement: one answers "how
    slow is a call", this answers "where did the time go". They disagree
    whenever a fast tool is called often, which is the ordinary case.

    ⚠⚠ A share over a zero total is UNDEFINED, not even -- it refuses. And a
    ring-capped bucket makes its own share a LOWER BOUND, so the tools that
    capped are named: the cap bites hardest on the busiest tool, which is the
    one this ranking exists to surface.
    """
    buckets = [(n, b) for n, b in stats.items() if b.get("total_ms") is not None]
    meta: dict = {"basis": "total_ms", "tools": len(buckets)}

    without_total = len(stats) - len(buckets)
    if without_total:
        meta["tools_without_total_ms"] = without_total

    capped = sorted(n for n, b in buckets if b.get("count_is_ring_capped"))
    if capped:
        meta["ring_capped_tools"] = capped
        meta["note"] = (
            "The in-memory ring holds the most recent calls per tool, so a "
            "capped tool's total and share are lower bounds."
        )

    grand = round(sum(b["total_ms"] for _, b in buckets), 2)
    if not grand:
        meta["measurable"] = False
        meta["reason"] = "no_time_recorded"
        return [], meta

    meta["measurable"] = True
    meta["total_ms"] = grand
    ranked = sorted(buckets, key=lambda kv: kv[1]["total_ms"], reverse=True)[:top]
    rows = [
        {
            "tool": name,
            "total_ms": b["total_ms"],
            "share": round(b["total_ms"] / grand, 3),
            "count": b["count"],
            "p50_ms": b.get("p50_ms"),
            **({"count_is_ring_capped": True} if b.get("count_is_ring_capped") else {}),
        }
        for name, b in ranked
    ]
    return rows, meta


def _ledger_summary(rows: list[tuple], top: int) -> dict:
    """Aggregate ranking_events rows by repo and by tool."""
    by_repo: dict = {}
    by_tool: dict = {}
    for ts, repo, tool, qh, query, returned_ids, top1, top2, conf, sem, ident, stale in rows:
        rb = by_repo.setdefault(repo or "<no-repo>", {
            "events": 0,
            "avg_confidence": 0.0,
            "_conf_total": 0.0,
            "_conf_count": 0,
            "stale_events": 0,
            "identity_hits": 0,
            "semantic_used": 0,
            # v1.108.186. Rows whose semantic_used column is not evidence — see
            # retrieval.ledger_trust. Counted separately so `semantic_used` stays a
            # count of rows that mean it, and reported (rather than dropped) because
            # a shrunken count with no explanation reads as a usage change.
            "semantic_label_unknown": 0,
            # v1.108.187. Rows that recorded no ledger features at all, so their
            # identity_hit and top-score columns are defaults rather than
            # measurements. `identity_hits` must not count a default as a miss.
            "no_ledger_features": 0,
        })
        rb["events"] += 1
        row = (ts, repo, tool, qh, query, returned_ids, top1, top2, conf, sem, ident, stale)
        if conf is not None:
            rb["_conf_total"] += float(conf)
            rb["_conf_count"] += 1
        if stale:
            rb["stale_events"] += 1
        if not _identity_label_is_trustworthy(row):
            rb["no_ledger_features"] += 1
        elif ident:
            rb["identity_hits"] += 1
        if not _semantic_label_is_trustworthy(row):
            rb["semantic_label_unknown"] += 1
        elif sem:
            rb["semantic_used"] += 1
        tb = by_tool.setdefault(tool, {"events": 0})
        tb["events"] += 1
    for repo_name, rb in by_repo.items():
        ct = rb.pop("_conf_count", 0)
        total = rb.pop("_conf_total", 0.0)
        rb["avg_confidence"] = round(total / ct, 3) if ct else 0.0
        # v1.108.186. Present only when there is something to disclose, so a caller
        # whose ledger holds no mislabelled row sees the byte-identical shape it saw
        # before. Compatibility here is pinned by a test, not asserted.
        if not rb.get("semantic_label_unknown"):
            rb.pop("semantic_label_unknown", None)
        if not rb.get("no_ledger_features"):
            rb.pop("no_ledger_features", None)
    repo_ranked = sorted(by_repo.items(), key=lambda kv: kv[1]["events"], reverse=True)[:top]
    tool_ranked = sorted(by_tool.items(), key=lambda kv: kv[1]["events"], reverse=True)
    return {
        "total_events": len(rows),
        "by_repo": [{"repo": r, **stats} for r, stats in repo_ranked],
        "by_tool": [{"tool": t, **stats} for t, stats in tool_ranked],
    }


def analyze_perf(
    window: str = "session",
    top: int = _DEFAULT_TOP,
    tool: Optional[str] = None,
    storage_path: Optional[str] = None,
    compare_release: Optional[str] = None,
    ledger: bool = False,
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
            # ⚠ One producer for this shape, in `token_tracker.latency_bucket`.
            # This was the second copy of it and the two already agreed digit for
            # digit, which is what makes a divergence later invisible.
            persisted[t_name] = _tt.latency_bucket(durs, errors.get(t_name, 0))
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

    # ⚠⚠ `slowest_by_p95` ranks how slow ONE call is. It is not, and has never
    # been, an answer to where the time went -- that is count x latency, and
    # the two orderings disagree whenever a fast tool is called often. An
    # external audit of agent runs (Revenium, 2026-08) put 46% of spend in the
    # top 1% of runs; a per-call ranking cannot see a distribution like that.
    heaviest, totals_meta = _rank_by_total(ranked_source, top)

    # Cache hit-rate ranked low → high (low rates point to cold caches)
    by_tool_cache = cache_stats.get("by_tool", {})
    coldest_caches = sorted(
        by_tool_cache.items(),
        key=lambda kv: kv[1].get("hit_rate", 0.0),
    )[:top]

    baseline_diff: Optional[dict] = None
    baseline_meta: Optional[dict] = None
    if compare_release:
        baseline_path = _baseline_path(compare_release)
        if not baseline_path.exists():
            baseline_meta = {
                "version": compare_release,
                "found": False,
                "looked_at": str(baseline_path),
            }
        else:
            try:
                baseline = json.loads(baseline_path.read_text(encoding="utf-8", errors="replace"))
                breakdown = _tt.get_session_stats(base_path=storage_path).get(
                    "tool_breakdown", {}
                )
                baseline_diff = _diff_baseline(baseline, in_memory, breakdown)
                baseline_meta = {
                    "version": baseline.get("version", compare_release),
                    "captured_at": baseline.get("captured_at"),
                    "found": True,
                    "tools_in_baseline": len(baseline.get("tools", {})),
                }
                # ⚠ Surfaced in the meta as well as per tool: a caller reading
                # only the header must not conclude the baseline covered
                # everything it is being differenced against.
                incomparable = sum(
                    1 for d in baseline_diff.values() if d.get("not_comparable")
                )
                if incomparable:
                    baseline_meta["tools_not_fully_comparable"] = incomparable
            except Exception as exc:
                baseline_meta = {
                    "version": compare_release,
                    "found": True,
                    "error": f"Failed to parse baseline: {type(exc).__name__}: {exc}",
                }

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    out = {
        "window": window,
        "tool": tool,
        "in_memory_session": in_memory,
        "persisted": persisted,
        "persisted_meta": persisted_meta,
        "slowest_by_p95": [
            {"tool": name, **stats} for name, stats in slowest
        ],
        "heaviest_by_total_ms": heaviest,
        "totals": totals_meta,
        "cache": {
            # ⚠⚠ `hit_rate` is RAW: a hit is key-presence in the session LRU
            # or in `search_symbols`' own cache (both counted since #864), not
            # a hit that still describes the current index. The cache is
            # invalidated only by index-mutating tools IN THIS PROCESS, so an
            # out-of-process reindex (the PostToolUse `index-file` spawn, the
            # watcher, a second server instance) leaves entries serving.
            # arXiv:2608.20280 measured raw rates of 51-60% falling to 1.1-2.2%
            # once validity was checked, so the raw number is published only
            # WITH the revalidated view beside it and a basis label on it.
            # ⚠ `hits_unvalidated` is UNKNOWN, never folded into either bucket:
            # of the three result-cache consumers only `search_symbols`
            # revalidates, so it is a reported number, not an edge case.
            "totals": {
                "hits": cache_stats.get("total_hits", 0),
                "misses": cache_stats.get("total_misses", 0),
                "hit_rate": cache_stats.get("hit_rate", 0.0),
                "hit_rate_basis": cache_stats.get("hit_rate_basis", "raw_key_presence"),
                "hit_rate_revalidated": cache_stats.get("hit_rate_revalidated"),
                "hits_validated_fresh": cache_stats.get("hits_validated_fresh", 0),
                "hits_validated_stale": cache_stats.get("hits_validated_stale", 0),
                "hits_unvalidated": cache_stats.get("hits_unvalidated", 0),
                "validated_share": cache_stats.get("validated_share"),
                "cached_entries": cache_stats.get("cached_entries", 0),
            },
            "coldest_by_tool": [
                {"tool": name, **stats} for name, stats in coldest_caches
            ],
        },
        "_meta": {"timing_ms": elapsed_ms},
    }
    if baseline_meta is not None:
        out["baseline_meta"] = baseline_meta
    if baseline_diff is not None:
        out["baseline_diff"] = baseline_diff

    if ledger:
        seconds_map_l = {"1h": 3600.0, "24h": 86_400.0, "7d": 7 * 86_400.0}
        window_seconds = seconds_map_l.get(window)  # None for session/all
        rows = _tt.ranking_db_query(
            base_path=storage_path,
            window_seconds=window_seconds,
            tool=tool,
            limit=10_000,
        )
        out["ranking_ledger"] = _ledger_summary(rows, top=top)

    return out
