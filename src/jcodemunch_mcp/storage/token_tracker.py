"""Persistent token savings tracker.

Records cumulative tokens saved across all tool calls by comparing
raw file sizes against actual MCP response sizes.

Stored in ~/.code-index/_savings.json — a single small JSON file.
No API calls, no file reads — only os.stat for file sizes.

Community meter: token savings are shared anonymously by default to the
global counter at https://j.gravelle.us. Only {"delta": N, "total": M,
"anon_id": "<uuid>"} is sent — never code, paths, repo names, or anything
identifying. `total` is the absolute lifetime savings; the server sets the
stored value to GREATEST(stored, total), so a dropped/failed POST self-heals
on the next successful one (the meter converges to the local total instead
of permanently undercounting from lost deltas). `delta` is kept for backward
compatibility with older server builds. Set JCODEMUNCH_SHARE_SAVINGS=0 to
disable; the endpoint is overridable via JCODEMUNCH_TELEMETRY_URL. The sender
is a single daemon thread started lazily on the first share (never at import,
and never when sharing is disabled), so a plain `import` has no background
side effect.

Performance: uses an in-memory accumulator to avoid disk read+write on every
tool call. Flushes to disk every FLUSH_INTERVAL calls (default 3), on SIGTERM/
SIGINT, and at process exit via atexit. Telemetry batches are sent at flush
time rather than per-call to avoid spawning a new thread on every tool use.
"""

import atexit
import json
import logging
import os
import queue
import signal
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import config as _config

logger = logging.getLogger(__name__)

_SAVINGS_FILE = "_savings.json"

# Days of per-day savings history kept in _savings.json's "daily" map. Two years
# covers every window a dashboard offers ("this year", year-over-year) while
# keeping the file a few KB.
_DAILY_MAX_DAYS = 750
_SESSION_STATS_FILE = "session_stats.json"
_PULSE_FILE = "_pulse.json"
_PERF_DB_FILE = "telemetry.db"
_BYTES_PER_TOKEN = 4  # ~4 bytes per token (rough but consistent)
_TELEMETRY_URL = "https://j.gravelle.us/APIs/savings/post.php"
_FLUSH_INTERVAL = 3  # flush to disk every N calls
_RESULT_CACHE_MAXSIZE = 256  # max tool-result cache entries per session
_YIELD_SERVED_MAXSIZE = 5000  # max served-symbol-id entries tracked per session
_DELIVERED_MAXSIZE = 5000  # max delivery-ledger entries tracked per session
_CALL_SIG_MAXSIZE = 2000  # max distinct (tool, args_hash) signatures tracked
_BUDGET_APPROACHING_PCT = 0.8  # _meta.budget appears at >=80% of the limit
_ESTIMATE_RATIO_MAXSIZE = 50  # closed estimate-vs-actual samples kept per session
_CALIBRATION_MIN_SAMPLES = 3  # calibration reported only at/after this floor
_DEFAULT_TOKENS_PER_CALL = 700  # cold-start per-call estimate before session data
_LATENCY_RING_DEFAULT = 512  # per-tool latency ring size
_PERF_DB_MAX_ROWS_DEFAULT = 100_000  # rolling cap on persisted perf rows
# Ids retained per ranking event. The row also carries `returned_count`, the
# TRUE size of the result set before this cap (#441) — without it a stored list
# of exactly this length was indistinguishable from a complete one.
_RETURNED_IDS_CAP = 50

def _get_stats_file_interval() -> int:
    """Read stats_file_interval from config. 0 = disabled, default 3."""
    return max(0, _config.get("stats_file_interval", _FLUSH_INTERVAL))

# Input token pricing ($ per token). Update as models reprice.
# Source: https://claude.com/pricing#api (last verified 2026-03-09)
PRICING = {
    "claude_opus_4_6":    5.00 / 1_000_000,  # Claude Opus 4.6   — $5.00 / 1M input tokens (≤200K ctx)
    "claude_sonnet_4_6":  3.00 / 1_000_000,  # Claude Sonnet 4.6 — $3.00 / 1M input tokens (≤200K ctx)
    "claude_haiku_4_5":   1.00 / 1_000_000,  # Claude Haiku 4.5  — $1.00 / 1M input tokens
    "gpt5_latest":       10.00 / 1_000_000,  # GPT-5 (latest)    — $10.00 / 1M input tokens
}


# ---------------------------------------------------------------------------
# In-memory state (process-lifetime cache)
# ---------------------------------------------------------------------------

class _State:
    """Holds the in-memory accumulator for the current process."""
    def __init__(self):
        self._lock = threading.Lock()
        self._loaded = False
        self._total: int = 0          # cumulative total (disk + in-flight)
        self._unflushed: int = 0      # delta not yet written to disk
        self._encoding_total: int = 0    # cumulative MUNCH encoding savings
        self._encoding_unflushed: int = 0
        self._call_count: int = 0     # calls since last savings flush
        self._stats_call_count: int = 0  # calls since last session_stats.json write
        self._anon_id: Optional[str] = None
        self._base_path: Optional[str] = None
        self._pending_telemetry: int = 0  # unflushed delta for telemetry
        # Session-level tracking (process lifetime only, not persisted)
        self._session_tokens: int = 0
        self._session_calls: int = 0
        self._session_start: float = time.monotonic()
        self._session_tool_breakdown: dict = {}
        # Calls per tool, beside tokens per tool. Savings has TWO carriers and
        # this one was invisible: `baseline = actual x multiplier` is computed
        # per call, so reported savings rises monotonically with call count.
        # Without this map, a session that saved tokens per call while tripling
        # the number of calls reads as a pure win. See arXiv 2608.01347, which
        # measures tool-borne waste separately from token-borne waste.
        self._session_tool_calls: dict = {}
        # Session-level tool-result cache (LRU, evicted at _RESULT_CACHE_MAXSIZE)
        self._result_cache: OrderedDict = OrderedDict()  # (tool, repo, key) -> result
        self._cache_hits: dict = {}    # tool_name -> hit count
        self._cache_misses: dict = {}  # tool_name -> miss count
        # Per-tool latency ring (process-lifetime; cap _LATENCY_RING_DEFAULT entries)
        self._tool_latencies: dict[str, deque] = {}
        self._tool_errors: dict[str, int] = {}
        # Session budget + yield tracking (v1.108.146; process lifetime only).
        # _session_response_tokens counts response tokens SERVED (the context
        # this server injects into the agent) — distinct from tokens SAVED.
        self._session_response_tokens: int = 0
        # served symbol id -> followed-through flag (insertion-ordered, capped)
        self._yield_served: OrderedDict = OrderedDict()
        # (tool, args_hash) -> times seen (capped); repeats beyond the first
        # accumulate into _repeat_calls[tool]
        self._call_signatures: OrderedDict = OrderedDict()
        self._repeat_calls: dict[str, int] = {}
        # Cue-anchored delivery ledger (docs/prd-cue-anchored-delivery.md, P0+P1;
        # process lifetime only). symbol id -> {count, tokens, full_source}.
        # Deliberately PARALLEL to _yield_served rather than an extension of it:
        # that map's value type is a followed-through bool the yield block sums
        # over, and _call_signatures only catches byte-identical repeat calls —
        # it cannot see the same symbol re-delivered under a different query,
        # which is the shape that actually costs.
        self._delivered: OrderedDict = OrderedDict()
        self._redelivered_tokens: int = 0
        # One id per PROCESS, so the session_yield sink can upsert a single row
        # per session instead of appending a partial row on every flush. Random
        # and never transmitted; it exists only to identify the row to update.
        self._session_uid: str = uuid.uuid4().hex
        self._session_started: float = time.time()
        self._delivery_base_path: Optional[str] = None
        # Estimate-vs-actual calibration (v1.108.148; process lifetime only).
        # plan_turn opens an estimate; the next plan_turn closes it against
        # the response tokens actually served in between.
        self._response_events: int = 0
        self._open_estimate: Optional[dict] = None
        self._estimate_ratios: deque = deque(maxlen=_ESTIMATE_RATIO_MAXSIZE)
        # Perf SQLite sink (opt-in via config "perf_telemetry_enabled")
        self._perf_db_path_cached: Optional[Path] = None
        self._perf_db_failed: bool = False
        # v1.108.276 (#442). Resolved-path -> open connection. Held for the
        # process; see _ensure_perf_db_locked and close_perf_dbs.
        self._perf_conns: dict = {}
        self._perf_rows_since_trim: int = 0

    def _ensure_loaded(self, base_path: Optional[str]) -> None:
        """Load persisted total from disk (once per process)."""
        if self._loaded:
            return
        self._base_path = base_path
        path = _savings_path(base_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
        except Exception:
            logger.debug("Failed to load savings data from %s", path, exc_info=True)
            data = {}
        self._total = data.get("total_tokens_saved", 0)
        self._encoding_total = data.get("total_encoding_tokens_saved", 0)
        self._anon_id = data.get("anon_id")
        self._loaded = True

    def add(self, delta: int, base_path: Optional[str], tool_name: Optional[str] = None) -> int:
        """Add delta to the running total. Returns new cumulative total."""
        with self._lock:
            self._ensure_loaded(base_path)
            delta = max(0, delta)
            self._total += delta
            self._unflushed += delta
            self._pending_telemetry += delta
            self._session_tokens += delta
            self._session_calls += 1
            if tool_name:
                self._session_tool_breakdown[tool_name] = (
                    self._session_tool_breakdown.get(tool_name, 0) + delta
                )
                self._session_tool_calls[tool_name] = (
                    self._session_tool_calls.get(tool_name, 0) + 1
                )
            self._call_count += 1
            if self._call_count >= _FLUSH_INTERVAL:
                self._flush_locked()
            return self._total

    def session_stats(self, base_path: Optional[str]) -> dict:
        """Return session-level stats (process lifetime)."""
        with self._lock:
            self._ensure_loaded(base_path)
            stats = self._build_stats_locked()
            self._write_session_stats_locked(stats, force=True)
            return stats

    def cache_get(self, tool_name: str, repo: str, specific_key: tuple):
        """Return cached result for (tool, repo, key), or None on miss. Thread-safe."""
        with self._lock:
            full_key = (tool_name, repo, specific_key)
            if full_key in self._result_cache:
                self._result_cache.move_to_end(full_key)
                self._cache_hits[tool_name] = self._cache_hits.get(tool_name, 0) + 1
                return self._result_cache[full_key]
            self._cache_misses[tool_name] = self._cache_misses.get(tool_name, 0) + 1
            return None

    def cache_put(self, tool_name: str, repo: str, specific_key: tuple, result: dict) -> None:
        """Store result in LRU cache. Evicts oldest entry when full. Thread-safe."""
        with self._lock:
            full_key = (tool_name, repo, specific_key)
            self._result_cache[full_key] = result
            self._result_cache.move_to_end(full_key)
            if len(self._result_cache) > _RESULT_CACHE_MAXSIZE:
                self._result_cache.popitem(last=False)

    def cache_invalidate(self, repo: Optional[str] = None) -> int:
        """Evict all entries (repo=None) or entries for a specific repo. Returns evicted count."""
        with self._lock:
            if repo is None:
                count = len(self._result_cache)
                self._result_cache.clear()
                return count
            to_delete = [k for k in self._result_cache if k[1] == repo]
            for k in to_delete:
                del self._result_cache[k]
            return len(to_delete)

    def cache_stats(self) -> dict:
        """Return cache hit/miss stats. Thread-safe."""
        with self._lock:
            total_hits = sum(self._cache_hits.values())
            total_misses = sum(self._cache_misses.values())
            total_lookups = total_hits + total_misses
            by_tool = {}
            all_tools = set(self._cache_hits) | set(self._cache_misses)
            for tool in all_tools:
                h = self._cache_hits.get(tool, 0)
                m = self._cache_misses.get(tool, 0)
                t = h + m
                by_tool[tool] = {
                    "hits": h,
                    "misses": m,
                    "hit_rate": round(h / t, 3) if t else 0.0,
                }
            return {
                "total_hits": total_hits,
                "total_misses": total_misses,
                "hit_rate": round(total_hits / total_lookups, 3) if total_lookups else 0.0,
                "cached_entries": len(self._result_cache),
                "by_tool": by_tool,
            }

    # ---------------------------------------------------------------------
    # Session budget + yield (v1.108.146)
    # ---------------------------------------------------------------------

    def record_response_tokens(self, tokens: int) -> int:
        """Add served response tokens to the session counter. Thread-safe."""
        with self._lock:
            self._session_response_tokens += max(0, int(tokens))
            self._response_events += 1
            return self._session_response_tokens

    def _budget_locked(self) -> Optional[dict]:
        """Budget block, or None when no budget is configured. Lock held."""
        try:
            limit = int(_config.get("session_token_budget", 0) or 0)
        except Exception:
            limit = 0
        if limit <= 0:
            return None
        spent = self._session_response_tokens
        if spent >= limit:
            state = "over"
        elif spent >= limit * _BUDGET_APPROACHING_PCT:
            state = "approaching"
        else:
            state = "ok"
        block = {"limit": limit, "spent": spent, "state": state}
        cal = self._calibration_locked()
        if cal is not None:
            block["actual_vs_estimated"] = cal["actual_vs_estimated"]
        return block

    def budget_status(self) -> Optional[dict]:
        """Public budget snapshot ({limit, spent, state}) or None. Thread-safe."""
        with self._lock:
            return self._budget_locked()

    def _calibration_locked(self) -> Optional[dict]:
        """Calibration block, or None below the sample floor. Lock held."""
        n = len(self._estimate_ratios)
        if n < _CALIBRATION_MIN_SAMPLES:
            return None
        ratios = sorted(self._estimate_ratios)
        mid = n // 2
        # Median, not mean — per-turn consumption varies wildly and a single
        # runaway turn must not swamp the calibration signal.
        median = ratios[mid] if n % 2 else (ratios[mid - 1] + ratios[mid]) / 2
        return {"samples": n, "actual_vs_estimated": round(median, 2)}

    def record_turn_estimate(self, estimated_tokens: int) -> Optional[dict]:
        """Open a consumption estimate, closing any prior open one against
        the response tokens actually served since it opened.

        Returns the current calibration block, or None below the sample
        floor. Thread-safe.
        """
        with self._lock:
            spent = self._session_response_tokens
            prior = self._open_estimate
            if prior is not None and prior["estimated"] > 0:
                actual = spent - prior["start_spent"]
                if actual > 0:
                    self._estimate_ratios.append(actual / prior["estimated"])
            self._open_estimate = {
                "estimated": max(0, int(estimated_tokens)),
                "start_spent": spent,
            }
            return self._calibration_locked()

    def avg_response_tokens_per_call(self) -> int:
        """Mean served response tokens per tool call this session (0 = no data)."""
        with self._lock:
            if self._response_events == 0:
                return 0
            return self._session_response_tokens // self._response_events

    def note_served(self, symbol_ids) -> None:
        """Record symbol ids served by a ranking/search tool. Thread-safe."""
        with self._lock:
            for sid in symbol_ids:
                if not sid:
                    continue
                if sid not in self._yield_served:
                    self._yield_served[sid] = False
                    if len(self._yield_served) > _YIELD_SERVED_MAXSIZE:
                        self._yield_served.popitem(last=False)

    def note_fetched(self, symbol_ids) -> None:
        """Mark previously served symbol ids as followed through. Thread-safe."""
        with self._lock:
            for sid in symbol_ids:
                if sid in self._yield_served:
                    self._yield_served[sid] = True

    def served_symbol_ids(self) -> frozenset:
        """Snapshot of every symbol id served this session (yield record).

        The session retrieval record the handoff contract (#374) attests
        evidence_refs against. Thread-safe.
        """
        with self._lock:
            return frozenset(self._yield_served.keys())

    def note_edited_files(self, file_paths) -> None:
        """Mark served symbols in edited files as followed through (edit-through)."""
        norm = {str(p).replace("\\", "/").lstrip("./") for p in file_paths if p}
        if not norm:
            return
        def _touched(sid: str) -> bool:
            sym_file = sid.split("::", 1)[0].replace("\\", "/").lstrip("./")
            return any(
                sym_file == f or sym_file.endswith("/" + f) or f.endswith("/" + sym_file)
                for f in norm
            )

        with self._lock:
            for sid in self._yield_served:
                if _touched(sid):
                    self._yield_served[sid] = True
            # Delivery-ledger invalidation: an edited file means the bytes we
            # handed over are stale, so those entries can no longer claim
            # "already delivered". Evict rather than annotate a lie.
            for sid in [s for s in self._delivered if _touched(s)]:
                del self._delivered[sid]

    def note_delivered(self, entries, base_path: Optional[str] = None) -> list:
        """Record symbol deliveries; return the ids already delivered before now.

        `entries` yields ``(symbol_id, est_tokens, full_source)``. The returned
        list is the advisory ``_meta.already_delivered`` payload — the agent is
        told it is holding these bytes again, never quietly denied them
        (P1 annotates; suppression is a separate, opt-in phase).

        Only a repeat of a FULL-SOURCE delivery accrues redundant tokens: a
        signature/summary row is cheap to re-show, so re-showing one is
        reported but never priced. Thread-safe.
        """
        repeats: list = []
        with self._lock:
            # v1.108.202. Route the session_yield row to the store the CALLER
            # named, the same correction v1.108.188 made for ranking_events and
            # tool_calls: every reader takes a base path, so a writer that passes
            # none lands in ~/.code-index no matter what storage_path the tool was
            # handed, and the row is written to one database while measure.py
            # reads another.
            #
            # Last explicit base wins. The ledger itself stays session-global
            # because redelivery is a property of the SESSION, and partitioning it
            # per store would change the shipped v1.108.167 already_delivered
            # advisory. A process serving two stores therefore files its one row
            # under the most recent one; that is disclosed rather than silently
            # split.
            if base_path:
                self._delivery_base_path = base_path
            for sid, tokens, full_source in entries:
                if not sid:
                    continue
                est = max(0, int(tokens or 0))
                rec = self._delivered.get(sid)
                if rec is None:
                    self._delivered[sid] = {
                        "count": 1,
                        "tokens": est,
                        "full_source": bool(full_source),
                    }
                    if len(self._delivered) > _DELIVERED_MAXSIZE:
                        self._delivered.popitem(last=False)
                    continue
                rec["count"] += 1
                if full_source and rec["full_source"]:
                    self._redelivered_tokens += est
                elif full_source:
                    # First full-source delivery of a symbol previously seen
                    # only as a signature row — new bytes, not a redelivery.
                    rec["full_source"] = True
                    rec["tokens"] = est
                repeats.append(sid)
        return repeats

    def note_call_signature(self, tool_name: str, args_hash: str) -> None:
        """Count repeated identical (tool, args) calls. Thread-safe."""
        with self._lock:
            key = (tool_name, args_hash)
            if key in self._call_signatures:
                self._call_signatures[key] += 1
                self._repeat_calls[tool_name] = self._repeat_calls.get(tool_name, 0) + 1
            else:
                self._call_signatures[key] = 1
                if len(self._call_signatures) > _CALL_SIG_MAXSIZE:
                    self._call_signatures.popitem(last=False)

    def _yield_locked(self) -> Optional[dict]:
        """Yield block, or None when nothing was served or delivered. Lock held."""
        served = len(self._yield_served)
        delivered = len(self._delivered)
        if served == 0 and delivered == 0:
            return None
        block: dict = {}
        if served:
            followed = sum(1 for v in self._yield_served.values() if v)
            block["rate"] = round(followed / served, 3)
            block["served_results"] = served
            block["followed_through"] = followed
        if self._repeat_calls:
            block["repeated_identical_calls"] = dict(self._repeat_calls)
        if delivered:
            total = sum(r["count"] for r in self._delivered.values())
            block["redelivered_symbols"] = sum(
                1 for r in self._delivered.values() if r["count"] > 1
            )
            block["redelivery_rate"] = round((total - delivered) / total, 3)
            if self._redelivered_tokens:
                block["redelivered_tokens_est"] = self._redelivered_tokens
        return block

    def _build_stats_locked(self) -> dict:
        """Build session stats dict. Must be called with _lock held."""
        elapsed = time.monotonic() - self._session_start
        # Build cache stats inline (re-uses lock already held)
        total_hits = sum(self._cache_hits.values())
        total_misses = sum(self._cache_misses.values())
        total_lookups = total_hits + total_misses
        cache_stats = {
            "total_hits": total_hits,
            "total_misses": total_misses,
            "hit_rate": round(total_hits / total_lookups, 3) if total_lookups else 0.0,
            "cached_entries": len(self._result_cache),
        }
        stats = {
            "session_tokens_saved": self._session_tokens,
            "session_calls": self._session_calls,
            "session_duration_s": round(elapsed, 1),
            "session_response_tokens": self._session_response_tokens,
            "total_tokens_saved": self._total,
            "tool_breakdown": dict(self._session_tool_breakdown),
            # Sibling of tool_breakdown, not a replacement: tool_breakdown is
            # tokens per tool and has consumers. Reported savings scales with
            # call count by construction, so tokens alone cannot distinguish
            # "did more with less" from "made more calls".
            "tool_calls": dict(self._session_tool_calls),
            "savings_per_call": (
                round(self._session_tokens / self._session_calls, 1)
                if self._session_calls else 0.0
            ),
            "result_cache": cache_stats,
            "latency_per_tool": self._latency_stats_locked(),
        }
        budget = self._budget_locked()
        if budget is not None:
            stats["budget"] = budget
        yield_block = self._yield_locked()
        if yield_block is not None:
            stats["yield"] = yield_block
        calibration = self._calibration_locked()
        if calibration is not None:
            stats["estimate_calibration"] = calibration
        return stats

    # ---------------------------------------------------------------------
    # Latency tracking + perf SQLite sink (v1.74.0)
    # ---------------------------------------------------------------------

    def record_latency(
        self,
        tool_name: str,
        duration_ms: float,
        ok: bool = True,
        repo: Optional[str] = None,
        base_path: Optional[str] = None,
    ) -> None:
        """Record a tool-call duration. Called from server.call_tool.

        ``base_path`` routes the persisted row to the store the CALL named, the same
        fix the ranking ledger gets in v1.108.188 — ``analyze_perf`` reads BOTH
        tables through one base path, so latency rows had the same write-here /
        read-there split.
        """
        try:
            with self._lock:
                ring = self._tool_latencies.get(tool_name)
                if ring is None:
                    ring = deque(maxlen=_LATENCY_RING_DEFAULT)
                    self._tool_latencies[tool_name] = ring
                ring.append(float(duration_ms))
                if not ok:
                    self._tool_errors[tool_name] = self._tool_errors.get(tool_name, 0) + 1
                if _config.get("perf_telemetry_enabled", False):
                    self._persist_perf_locked(tool_name, duration_ms, ok, repo, base_path)
        except Exception:
            logger.debug("record_latency failed for %s", tool_name, exc_info=True)

    def _latency_stats_locked(self) -> dict:
        """Compute p50/p95 per tool from the ring. Caller must hold _lock."""
        out: dict = {}
        for tool, ring in self._tool_latencies.items():
            if not ring:
                continue
            sorted_vals = sorted(ring)
            n = len(sorted_vals)
            p50 = sorted_vals[n // 2]
            # p95 index — bisect-style lower bound
            p95_idx = max(0, min(n - 1, int(0.95 * n)))
            p95 = sorted_vals[p95_idx]
            errors = self._tool_errors.get(tool, 0)
            out[tool] = {
                "count": n,
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "max_ms": round(sorted_vals[-1], 2),
                "errors": errors,
                "error_rate": round(errors / n, 3) if n else 0.0,
            }
        return out

    def latency_stats(self) -> dict:
        """Public latency snapshot. Thread-safe."""
        with self._lock:
            return self._latency_stats_locked()

    def _perf_db_path(self, base_path: Optional[str] = None) -> Optional[Path]:
        """Resolve the perf db path, preferring an explicitly supplied base.

        ⚠ v1.108.188. ``self._base_path`` is whatever the FIRST caller of
        ``_ensure_loaded`` happened to pass, and most savings writes pass nothing —
        so it is usually ``None`` and this resolved to ``~/.code-index`` even for a
        tool that was handed a ``storage_path``. The read side
        (``ranking_db_query(base_path=)``, ``analyze_perf(storage_path=)``) has
        always been parameterised, so writes and reads could disagree about which
        database they meant. An explicit ``base_path`` wins and is NOT cached: it
        belongs to one call, not to the process.
        """
        if base_path is not None:
            try:
                root = Path(base_path)
                root.mkdir(parents=True, exist_ok=True)
                return root / _PERF_DB_FILE
            except Exception:
                logger.debug("Failed to resolve perf db path at %s", base_path, exc_info=True)
                return None
        if self._perf_db_path_cached is not None:
            return self._perf_db_path_cached
        try:
            root = Path(self._base_path) if self._base_path else Path.home() / ".code-index"
            root.mkdir(parents=True, exist_ok=True)
            path = root / _PERF_DB_FILE
            self._perf_db_path_cached = path
            return path
        except Exception:
            logger.debug("Failed to resolve perf db path", exc_info=True)
            return None

    @staticmethod
    def _perf_conn_usable(conn: sqlite3.Connection, path: Path) -> bool:
        """Whether a cached connection can still be written to THIS file.

        Two ways a process-lifetime connection goes bad, and neither announces
        itself -- both would otherwise turn telemetry off permanently while every
        write reported success:

        * **Closed.** Anything that calls ``close()`` on it leaves a dead handle
          in the cache, and every later caller gets that same dead handle. The
          old contract had callers closing after each write, so any missed call
          site or third-party caller reintroduces this.
        * **Orphaned.** If the database file is deleted or its directory removed
          (clearing ``~/.code-index``, a temp store going away), SQLite keeps
          happily writing to the unlinked inode. Rows land nowhere and nothing
          raises. Before caching, the next event simply recreated the file.

        ⚠ The ``exists`` check is what catches the second case; a liveness probe
        alone cannot, because the connection is perfectly healthy. Measured cost
        of both checks together: **0.344ms, or 2.1% of the pre-fix write**,
        against the **79%** the caching saves. Paying 2 to keep 79 is the trade,
        and the alternative failure is silent.
        """
        try:
            if not path.exists():
                return False
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    @staticmethod
    def _add_column_if_missing(
        conn: sqlite3.Connection, table: str, column: str, decl: str
    ) -> None:
        """Add ``column`` to ``table`` when absent. Idempotent, never destructive.

        SQLite has no ``ADD COLUMN IF NOT EXISTS``, and catching the resulting
        ``OperationalError`` would also swallow a genuinely broken ALTER, so the
        existing columns are read first.
        """
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except Exception:
            logger.debug("Failed adding %s.%s", table, column, exc_info=True)

    def close_perf_dbs(self) -> int:
        """Close every cached perf connection. Returns how many were closed.

        Needed because #442 made the connections process-lifetime: on Windows an
        open handle blocks removal of the directory holding the database, and a
        cached handle would otherwise outlive the store it points at.
        """
        with self._lock:
            conns = list(self._perf_conns.items())
            self._perf_conns.clear()
        closed = 0
        for _path, conn in conns:
            try:
                conn.close()
                closed += 1
            except Exception:
                logger.debug("Failed closing perf db", exc_info=True)
        return closed

    def _ensure_perf_db_locked(self, base_path: Optional[str] = None) -> Optional[sqlite3.Connection]:
        """Return an open perf SQLite db connection, creating schema on first use.

        ⚠⚠ v1.108.276 (#442). The connection is CACHED for the process and the
        caller must NOT close it -- use ``close_perf_dbs()`` instead. Every event
        used to reopen the database and replay all eight ``IF NOT EXISTS``
        statements before inserting one row.

        ⚠ **The schema replay was not the expensive part**, which matters because
        it rules out the cheap fix. Measured here across 400 interleaved events,
        splitting the cost three ways:

        ==========================================  =========
        arm                                          median
        ==========================================  =========
        connect + PRAGMA + 8 DDL + insert            15.441ms
        connect + insert, schema ensured once        15.166ms
        cached connection + insert                    3.214ms
        ==========================================  =========

        Skipping only the DDL captures **2%** of the available saving; the other
        98% is the open/close itself. So a "remember the schema is ready" flag --
        which would need no connection lifetime at all -- does essentially
        nothing, and caching the connection is the only thing that works. (The
        absolute figures are this machine's, slower than the reporter's 3.99ms /
        0.74ms; the ratio is what transfers.)

        Safety, which is the part #442 correctly left open:

        * ``check_same_thread=False`` is required because searches dispatch
          through ``asyncio.to_thread``, so a cached connection outlives the
          thread that opened it. It is SAFE because every caller of this method
          holds ``self._lock`` -- the serialisation the flag would otherwise
          provide is already there, and this comment is the reason it must stay.
        * Keyed by resolved path, because ``base_path`` selects a different
          database per call.
        * ``_perf_db_failed`` still latches for the DEFAULT path only, so one
          unwritable caller-supplied path cannot disable telemetry process-wide.
        """
        if self._perf_db_failed:
            return None
        path = self._perf_db_path(base_path)
        if path is None:
            return None
        cached = self._perf_conns.get(str(path))
        if cached is not None:
            if self._perf_conn_usable(cached, path):
                return cached
            # Poisoned or orphaned -- drop it and fall through to a fresh open
            # rather than returning something that silently discards writes.
            self._perf_conns.pop(str(path), None)
            try:
                cached.close()
            except Exception:
                pass
        try:
            conn = sqlite3.connect(
                str(path), timeout=2.0, isolation_level=None, check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_calls (
                    ts        REAL NOT NULL,
                    tool      TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    ok        INTEGER NOT NULL,
                    repo      TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS ix_tool_calls_tool ON tool_calls(tool)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_tool_calls_ts   ON tool_calls(ts)")
            # v1.78.0 — ranking ledger (data-collection only; consumed by
            # the online weight tuner in v1.79.0).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_yield (
                    session_uid            TEXT PRIMARY KEY,
                    started_at             REAL NOT NULL,
                    updated_at             REAL NOT NULL,
                    deliveries             INTEGER NOT NULL,
                    distinct_symbols       INTEGER NOT NULL,
                    redelivered_symbols    INTEGER NOT NULL,
                    redelivered_tokens_est INTEGER NOT NULL,
                    full_source_symbols    INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ranking_events (
                    ts             REAL NOT NULL,
                    repo           TEXT,
                    tool           TEXT NOT NULL,
                    query_hash     TEXT NOT NULL,
                    query          TEXT NOT NULL,
                    returned_ids   TEXT NOT NULL,    -- JSON-encoded list
                    top1_score     REAL,
                    top2_score     REAL,
                    confidence     REAL,
                    semantic_used  INTEGER NOT NULL,
                    identity_hit   INTEGER NOT NULL,
                    repo_is_stale  INTEGER NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS ix_ranking_events_repo ON ranking_events(repo)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_ranking_events_ts   ON ranking_events(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_ranking_events_qh   ON ranking_events(query_hash)")
            # v1.108.276 (#441). returned_ids keeps only the first 50, and the row
            # said nothing about how many there really were -- so a stored list of
            # exactly 50 could mean "returned 50" or "returned 500", and an
            # analysis could neither exclude the truncated rows nor say how many
            # it dropped. ⚠⚠ Added by ALTER so pre-existing rows keep NULL, which
            # means UNKNOWN and must never be read as "the count equals
            # len(returned_ids)" -- that inference is the defect, not the fix.
            # Same treatment ledger_trust gives its unseparable history.
            self._add_column_if_missing(
                conn, "ranking_events", "returned_count", "INTEGER"
            )
            self._perf_conns[str(path)] = conn
            return conn
        except Exception:
            logger.debug("Failed to open perf db at %s", path, exc_info=True)
            # v1.108.188. The kill-switch covers the DEFAULT db only. One unwritable
            # caller-supplied path must not disable telemetry for every other store
            # in the process — the flag was written when there was only one path it
            # could ever mean.
            if base_path is None:
                self._perf_db_failed = True
            return None

    def _persist_session_yield_locked(self, base_path: Optional[str] = None) -> None:
        """Upsert this session's delivery counts into the perf db. Lock held.

        Why RAW COUNTS and not just the rate: a representative redelivery figure
        pools numerators and denominators across sessions. Averaging per-session
        rates would weight a 3-delivery session the same as a 300-delivery one
        and is simply the wrong statistic, so the aggregate must be recomputable
        from these columns.

        One row per process, upserted on every flush rather than appended once at
        exit: a SIGKILL'd server still leaves its latest counts behind, and no
        session is ever represented twice.
        """
        if not _config.get("perf_telemetry_enabled", False):
            return
        if not self._delivered:
            return
        try:
            conn = self._ensure_perf_db_locked(base_path)
            if conn is None:
                return
            try:
                deliveries = sum(r["count"] for r in self._delivered.values())
                distinct = len(self._delivered)
                redelivered = sum(
                    1 for r in self._delivered.values() if r["count"] > 1
                )
                full_source = sum(
                    1 for r in self._delivered.values() if r.get("full_source")
                )
                conn.execute(
                    "INSERT INTO session_yield (session_uid, started_at, updated_at,"
                    " deliveries, distinct_symbols, redelivered_symbols,"
                    " redelivered_tokens_est, full_source_symbols)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(session_uid) DO UPDATE SET"
                    "   updated_at=excluded.updated_at,"
                    "   deliveries=excluded.deliveries,"
                    "   distinct_symbols=excluded.distinct_symbols,"
                    "   redelivered_symbols=excluded.redelivered_symbols,"
                    "   redelivered_tokens_est=excluded.redelivered_tokens_est,"
                    "   full_source_symbols=excluded.full_source_symbols",
                    (
                        self._session_uid,
                        self._session_started,
                        time.time(),
                        deliveries,
                        distinct,
                        redelivered,
                        int(self._redelivered_tokens),
                        full_source,
                    ),
                )
            finally:
                pass  # connection is process-cached (#442); see close_perf_dbs
        except Exception:
            logger.debug("session_yield persist failed", exc_info=True)

    def record_ranking_event(
        self,
        *,
        tool: str,
        repo: Optional[str],
        query: str,
        returned_ids: list[str],
        top1_score: Optional[float] = None,
        top2_score: Optional[float] = None,
        confidence: Optional[float] = None,
        semantic_used: bool = False,
        identity_hit: bool = False,
        repo_is_stale: bool = False,
        base_path: Optional[str] = None,
    ) -> None:
        """Append a ranking event to the perf db (no-op when disabled).

        v1.79.0 will use these rows to tune per-repo BM25/semantic weights.

        ⚠ v1.108.188. ``base_path`` is the store the CALLER was told to use. Without
        it these rows landed in ``~/.code-index`` whatever ``storage_path`` the tool
        was handed, while every reader (``ranking_db_query``, ``WeightTuner``,
        ``analyze_perf``) took a base path — so a non-default store wrote to one
        database and read from another, and the tuner learned from a ledger the
        searches had never written to.
        """
        if not _config.get("perf_telemetry_enabled", False):
            return
        try:
            with self._lock:
                conn = self._ensure_perf_db_locked(base_path)
                if conn is None:
                    return
                try:
                    import hashlib
                    import json as _json
                    qh = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
                    # ⚠ Materialise ONCE. returned_ids is typed as an iterable and
                    # a generator would be exhausted by len(), silently storing an
                    # empty list -- which regret and ledger_trust both read as a
                    # real "returned nothing".
                    all_ids = list(returned_ids)
                    conn.execute(
                        "INSERT INTO ranking_events "
                        "(ts, repo, tool, query_hash, query, returned_ids, "
                        " top1_score, top2_score, confidence, semantic_used, "
                        " identity_hit, repo_is_stale, returned_count) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            time.time(),
                            repo or None,
                            tool,
                            qh,
                            query,
                            _json.dumps(all_ids[:_RETURNED_IDS_CAP]),
                            float(top1_score) if top1_score is not None else None,
                            float(top2_score) if top2_score is not None else None,
                            float(confidence) if confidence is not None else None,
                            1 if semantic_used else 0,
                            1 if identity_hit else 0,
                            1 if repo_is_stale else 0,
                            # #441. The TRUE size of the result set, recorded before
                            # the cap. The stored id list stays bounded; only the
                            # count is added, so a reader can tell a complete row
                            # from a truncated one and say how many were dropped.
                            len(all_ids),
                        ),
                    )
                finally:
                    pass  # connection is process-cached (#442); see close_perf_dbs
        except Exception:
            logger.debug("record_ranking_event failed for %s", tool, exc_info=True)

    def _persist_perf_locked(
        self,
        tool: str,
        duration_ms: float,
        ok: bool,
        repo: Optional[str],
        base_path: Optional[str] = None,
    ) -> None:
        conn = self._ensure_perf_db_locked(base_path)
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT INTO tool_calls (ts, tool, duration_ms, ok, repo) VALUES (?, ?, ?, ?, ?)",
                (time.time(), tool, float(duration_ms), 1 if ok else 0, repo or None),
            )
            self._perf_rows_since_trim += 1
            if self._perf_rows_since_trim >= 1000:
                cap = max(1000, int(_config.get("perf_telemetry_max_rows", _PERF_DB_MAX_ROWS_DEFAULT)))
                conn.execute(
                    "DELETE FROM tool_calls WHERE rowid IN ("
                    "  SELECT rowid FROM tool_calls ORDER BY ts ASC LIMIT MAX(0, "
                    "    (SELECT COUNT(*) FROM tool_calls) - ?"
                    "  )"
                    ")",
                    (cap,),
                )
                self._perf_rows_since_trim = 0
        except Exception:
            logger.debug("Failed to persist perf row for %s", tool, exc_info=True)
        finally:
            pass  # connection is process-cached (#442); see close_perf_dbs

    def _write_session_stats_locked(self, stats: dict, force: bool = False) -> None:
        """Write session stats to ~/.code-index/session_stats.json. Must be called with _lock held.

        Writes are gated by stats_file_interval config (default 3 calls).
        Set to 0 to disable all session_stats.json writes.
        Pass force=True to bypass the interval check (e.g. explicit get_session_stats call).
        """
        stats_file_interval = _get_stats_file_interval()
        if stats_file_interval == 0 and not force:
            return
        self._stats_call_count += 1
        if not force and self._stats_call_count < stats_file_interval:
            return
        self._stats_call_count = 0
        path = _session_stats_path(self._base_path)
        try:
            payload = {**stats, "last_updated": datetime.now(timezone.utc).isoformat()}
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            logger.debug("Failed to write session stats to %s", path, exc_info=True)

    def get_total(self, base_path: Optional[str]) -> int:
        with self._lock:
            self._ensure_loaded(base_path)
            return self._total

    def _flush_locked(self) -> None:
        """Write accumulated total to disk. Must be called with _lock held."""
        if self._unflushed == 0 and self._loaded:
            self._call_count = 0
            self._write_session_stats_locked(self._build_stats_locked())
            return
        path = _savings_path(self._base_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
        except Exception:
            logger.debug("Failed to read savings file for flush: %s", path, exc_info=True)
            data = {}
        if self._anon_id is None:
            if "anon_id" not in data:
                data["anon_id"] = str(uuid.uuid4())
            self._anon_id = data["anon_id"]
        else:
            data["anon_id"] = self._anon_id
        data["total_tokens_saved"] = data.get("total_tokens_saved", 0) + self._unflushed
        # Daily rollup alongside the lifetime total, so windowed views ("today",
        # "this month") can come from THIS meter — the authoritative record —
        # instead of transcript scans, which miss cleared history and model
        # savings conservatively (observed 4 orders of magnitude under the
        # meter on a heavy install). Flush batches are small (_FLUSH_INTERVAL
        # calls), so crediting the whole batch to the flush day mis-dates at
        # most one batch across midnight. Local date: "today" means today
        # where the user sits. Capped so the file stays small.
        if self._unflushed:
            daily = data.get("daily")
            if not isinstance(daily, dict):
                daily = {}
            day = datetime.now().date().isoformat()
            daily[day] = daily.get(day, 0) + self._unflushed
            if len(daily) > _DAILY_MAX_DAYS:
                for stale in sorted(daily)[: len(daily) - _DAILY_MAX_DAYS]:
                    del daily[stale]
            data["daily"] = daily
        if self._encoding_unflushed:
            data["total_encoding_tokens_saved"] = (
                data.get("total_encoding_tokens_saved", 0) + self._encoding_unflushed
            )
            self._encoding_unflushed = 0
        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            logger.debug("Failed to write savings data to %s", path, exc_info=True)

        # Send batched telemetry. We send the absolute lifetime total (just
        # written above) so the server can GREATEST-converge and recover any
        # previously dropped sends; the delta rides along for legacy servers.
        if self._pending_telemetry > 0 and _config.get("share_savings", True):
            _share_savings(self._pending_telemetry, data["total_tokens_saved"], self._anon_id)
            self._pending_telemetry = 0

        self._unflushed = 0
        self._call_count = 0
        self._write_session_stats_locked(self._build_stats_locked())
        # Opt-in and local-only; no-ops entirely unless perf telemetry is on.
        self._persist_session_yield_locked(self._delivery_base_path)

    def flush(self) -> None:
        """Public flush — called at atexit."""
        with self._lock:
            if self._loaded:
                self._flush_locked()


_state = _State()
# v1.108.276 (#442). Perf connections are held for the process, so they need an
# explicit close at exit.
# ⚠ Registered BEFORE flush ON PURPOSE. atexit runs handlers LIFO, so the LAST
# registered runs FIRST -- registering the closer second would close the database
# out from under the final flush, whose _persist_session_yield_locked writes to
# it. This ordering makes flush run first and the close last.
atexit.register(_state.close_perf_dbs)
atexit.register(_state.flush)


def _signal_flush(signum, frame):
    """Flush savings to disk on SIGTERM/SIGINT, then re-raise the signal."""
    _state.flush()
    # Restore the default handler and re-raise so the process exits normally.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


# MCP servers are commonly killed via SIGTERM (pipe close, client shutdown).
# atexit does NOT run on SIGTERM, so we register explicit handlers here.
# We only install if no handler is already set (respects user overrides).
for _sig in (signal.SIGTERM, signal.SIGINT):
    try:
        if signal.getsignal(_sig) in (signal.SIG_DFL, None):
            signal.signal(_sig, _signal_flush)
    except (OSError, ValueError):
        # Signals can't be set in non-main threads; ignore safely.
        pass


# ---------------------------------------------------------------------------
# Public API (unchanged signatures)
# ---------------------------------------------------------------------------

def _savings_path(base_path: Optional[str] = None) -> Path:
    root = Path(base_path) if base_path else Path.home() / ".code-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / _SAVINGS_FILE


def _session_stats_path(base_path: Optional[str] = None) -> Path:
    root = Path(base_path) if base_path else Path.home() / ".code-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / _SESSION_STATS_FILE


# ---------------------------------------------------------------------------
# Telemetry worker (P11)
# ---------------------------------------------------------------------------
# A single long-lived daemon thread drains _telemetry_queue instead of
# spawning a new thread on every flush.  This eliminates per-flush thread
# creation overhead and prevents thread pile-up under rapid calls.
# ---------------------------------------------------------------------------

_telemetry_queue: queue.Queue = queue.Queue()
# The worker is started lazily on the first enqueue (see _ensure_telemetry_worker)
# rather than at import. Two reasons: (1) `import jcodemunch_mcp` must have no
# background-thread side effect — an import-time daemon thread is exactly the
# undisclosed-persistent-service shape that package scanners flag; (2) a process
# that has opted out (JCODEMUNCH_SHARE_SAVINGS=0) never reaches _share_savings,
# so it never spawns the thread at all.
_telemetry_worker_lock = threading.Lock()
_telemetry_worker_started = False


def _telemetry_url() -> str:
    """Community-meter endpoint, overridable via JCODEMUNCH_TELEMETRY_URL so a
    deployment can redirect it without a code change. Resolved at send time so an
    env change takes effect for an already-running process."""
    return os.environ.get("JCODEMUNCH_TELEMETRY_URL") or _TELEMETRY_URL


def _telemetry_worker() -> None:
    """Drain _telemetry_queue and POST each item. Runs for process lifetime."""
    while True:
        item = _telemetry_queue.get()
        if item is None:  # shutdown sentinel
            break
        delta, total, anon_id = item
        try:
            import httpx
            # `total` is the absolute lifetime savings; the server applies
            # GREATEST(stored, total) so a failed post self-corrects on the next
            # one. `delta` rides along for older server builds (additive upsert).
            httpx.post(
                _telemetry_url(),
                json={"delta": delta, "total": total, "anon_id": anon_id},
                timeout=3.0,
            )
        except Exception:
            logger.debug("Telemetry post failed", exc_info=True)
        finally:
            _telemetry_queue.task_done()


def _ensure_telemetry_worker() -> None:
    """Start the single telemetry worker thread once, on first use. Thread-safe
    (double-checked under a lock). Never started at import or when the user has
    opted out of sharing."""
    global _telemetry_worker_started
    if _telemetry_worker_started:
        return
    with _telemetry_worker_lock:
        if _telemetry_worker_started:
            return
        threading.Thread(
            target=_telemetry_worker, daemon=True, name="jcodemunch-telemetry"
        ).start()
        _telemetry_worker_started = True


def _share_savings(delta: int, total: int, anon_id: str) -> None:
    """Enqueue a fire-and-forget POST to the community meter. Never raises.
    `total` is the absolute lifetime savings (self-correcting); `delta` is the
    new savings since the last send (legacy additive path). Starts the worker
    thread on first use — never at import, and never when sharing is disabled
    (this function is only reached behind the share_savings opt-out gate)."""
    _ensure_telemetry_worker()
    _telemetry_queue.put((delta, total, anon_id))


def record_encoding_savings(
    tokens_saved: int,
    base_path: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> int:
    """Add tokens saved by MUNCH compact encoding. Tracked independently
    from retrieval-side savings. Returns new cumulative encoding total."""
    with _state._lock:
        _state._ensure_loaded(base_path)
        delta = max(0, tokens_saved)
        _state._encoding_total += delta
        _state._encoding_unflushed += delta
        _state._call_count += 1
        if _state._call_count >= _FLUSH_INTERVAL:
            _state._flush_locked()
        return _state._encoding_total


def get_total_encoding_saved(base_path: Optional[str] = None) -> int:
    with _state._lock:
        _state._ensure_loaded(base_path)
        return _state._encoding_total


def record_savings(tokens_saved: int, base_path: Optional[str] = None, tool_name: Optional[str] = None) -> int:
    """Add tokens_saved to the running total. Returns new cumulative total.

    Uses an in-memory accumulator; flushes to disk every FLUSH_INTERVAL calls (currently 3) and at exit.
    """
    return _state.add(tokens_saved, base_path, tool_name)


def write_pulse(tool_name: str, tokens_saved: int = 0, base_path: Optional[str] = None) -> None:
    """Write a per-call pulse file for downstream consumers (dashboards, monitors).

    Atomic write of a small JSON file to {storage}/_pulse.json containing the
    tool name, timestamp, and running counters. Only written when
    JCODEMUNCH_EVENT_LOG=1 is set.
    """
    if not os.environ.get("JCODEMUNCH_EVENT_LOG"):
        return
    try:
        root = Path(base_path) if base_path else Path.home() / ".code-index"
        pulse_path = root / _PULSE_FILE
        with _state._lock:
            calls = _state._session_calls
            session_tokens = _state._session_tokens
        data = {
            "last_call_at": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "calls_since_boot": calls,
            "session_tokens_saved": session_tokens,
            "tokens_saved": tokens_saved,
        }
        tmp = pulse_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(pulse_path)
    except Exception:
        logger.debug("Pulse write failed", exc_info=True)


def get_session_stats(base_path: Optional[str] = None) -> dict:
    """Return token savings stats for the current session (process lifetime).

    Returns session_tokens_saved, session_calls, session_duration_s,
    total_tokens_saved (all-time), tool_breakdown, and cost_avoided estimates.
    """
    stats = _state.session_stats(base_path)
    session_tokens = stats["session_tokens_saved"]
    total_tokens = stats["total_tokens_saved"]
    return {
        **stats,
        "session_cost_avoided": {
            model: round(session_tokens * rate, 4)
            for model, rate in PRICING.items()
        },
        "total_cost_avoided": {
            model: round(total_tokens * rate, 4)
            for model, rate in PRICING.items()
        },
        "runtime_signal": _runtime_signal_summary(base_path),
    }


def _runtime_signal_summary(base_path: Optional[str] = None) -> dict:
    """Cheap probe of the runtime_* tables across all per-repo databases.

    Returns ``{"rows": N, "by_source": {source: count}}``. Phase 0 always
    returns zeroes (no ingest yet); the field exists so Phase 1+ can fill
    it without changing the response shape.

    Errors are logged at DEBUG and yield zeroes — never block stats.
    """
    import sqlite3 as _sqlite3
    summary = {"rows": 0, "by_source": {}}
    try:
        root = Path(base_path) if base_path else Path.home() / ".code-index"
        if not root.exists():
            return summary
        # Skip non-repo files (telemetry.db, config.jsonc, etc.)
        non_repo = {"telemetry.db"}
        total = 0
        by_source: dict[str, int] = {}
        for db_path in root.glob("*.db"):
            if db_path.name in non_repo:
                continue
            try:
                # #398 Arc 1: this loop touches EVERY index in ~/.code-index,
                # so a plain `mode=ro` created a -wal beside each one and moved
                # every repo's mtime at once — the v1.108.185 `rebuilding`
                # defect at fleet scale.
                from .generation import connect_readonly

                conn = connect_readonly(db_path, isolation_level="")
                conn.row_factory = _sqlite3.Row
                # Skip dbs that don't have the runtime_calls table yet
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_calls'"
                ).fetchone()
                if row is None:
                    conn.close()
                    continue
                rows = conn.execute(
                    "SELECT source, COALESCE(SUM(count), 0) AS n FROM runtime_calls GROUP BY source"
                ).fetchall()
                for r in rows:
                    by_source[r["source"]] = by_source.get(r["source"], 0) + int(r["n"])
                    total += int(r["n"])
                conn.close()
            except _sqlite3.Error:
                logger.debug("Runtime signal probe failed on %s", db_path, exc_info=True)
        summary["rows"] = total
        summary["by_source"] = by_source
    except Exception:
        logger.debug("Runtime signal summary failed", exc_info=True)
    return summary


def get_total_saved(base_path: Optional[str] = None) -> int:
    """Return the current cumulative total without modifying it."""
    return _state.get_total(base_path)


def result_cache_get(tool_name: str, repo: str, specific_key: tuple):
    """Return a cached tool result, or None on miss. Updates hit/miss counters."""
    return _state.cache_get(tool_name, repo, specific_key)


def result_cache_put(tool_name: str, repo: str, specific_key: tuple, result: dict) -> None:
    """Store a tool result in the session LRU cache (max 256 entries)."""
    _state.cache_put(tool_name, repo, specific_key, result)


def result_cache_invalidate(repo: Optional[str] = None) -> int:
    """Evict cached results — all repos (default) or a specific repo. Returns evicted count."""
    return _state.cache_invalidate(repo)


def result_cache_stats() -> dict:
    """Return cache hit/miss stats for the current session."""
    return _state.cache_stats()


def record_tool_latency(
    tool_name: str,
    duration_ms: float,
    ok: bool = True,
    repo: Optional[str] = None,
    base_path: Optional[str] = None,
) -> None:
    """Record a tool-call duration for the current session (and optional perf db)."""
    _state.record_latency(tool_name, duration_ms, ok=ok, repo=repo, base_path=base_path)


def latency_stats() -> dict:
    """Return per-tool p50/p95/error_rate from the in-memory ring."""
    return _state.latency_stats()


def close_perf_dbs() -> int:
    """Close every cached perf telemetry connection. Returns how many closed.

    v1.108.276 (#442). Public because the connections are process-lifetime: on
    Windows an open handle blocks removal of the directory holding the database,
    so anything that creates a temporary store and then deletes it must call
    this. Safe to call when telemetry is off or nothing is open (returns 0).
    """
    return _state.close_perf_dbs()


def perf_db_path(base_path: Optional[str] = None) -> Path:
    """Return the perf telemetry SQLite path (creating its parent dir)."""
    root = Path(base_path) if base_path else Path.home() / ".code-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / _PERF_DB_FILE


def record_ranking_event(**kwargs) -> None:
    """Append a ranking event to telemetry.db (no-op when telemetry disabled).

    Keyword args: tool, repo, query, returned_ids, top1_score, top2_score,
    confidence, semantic_used, identity_hit, repo_is_stale, base_path.

    ⚠ Pass ``base_path`` whenever the caller was given a ``storage_path``, or the
    row lands in the default store while the readers look in the named one
    (v1.108.188).
    """
    _state.record_ranking_event(**kwargs)


def ranking_db_query(
    base_path: Optional[str] = None,
    window_seconds: Optional[float] = None,
    repo: Optional[str] = None,
    tool: Optional[str] = None,
    limit: int = 1000,
) -> list[tuple]:
    """Read recent ranking events from telemetry.db.

    Returns a list of tuples shaped exactly as the SELECT below — the
    order matches the v1.78.0 ranking_events schema. Empty when the db
    doesn't exist (telemetry disabled or never written).
    """
    path = perf_db_path(base_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        try:
            sql = (
                "SELECT ts, repo, tool, query_hash, query, returned_ids, "
                "top1_score, top2_score, confidence, semantic_used, "
                "identity_hit, repo_is_stale FROM ranking_events"
            )
            args: list = []
            clauses: list[str] = []
            if window_seconds is not None:
                clauses.append("ts >= ?")
                args.append(time.time() - float(window_seconds))
            if repo:
                clauses.append("repo = ?")
                args.append(repo)
            if tool:
                clauses.append("tool = ?")
                args.append(tool)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY ts DESC LIMIT ?"
            args.append(int(limit))
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        # Schema not yet created (telemetry was never enabled in this
        # storage dir, even though the file exists from another component).
        return []
    except Exception:
        logger.debug("ranking_db_query failed at %s", path, exc_info=True)
        return []


def perf_db_query(
    base_path: Optional[str] = None,
    window_seconds: Optional[float] = None,
    tool: Optional[str] = None,
) -> list[tuple]:
    """Read recent perf rows from telemetry.db for the analyze_perf tool.

    Returns a list of (ts, tool, duration_ms, ok, repo). Empty if the db
    doesn't exist yet (perf telemetry never enabled or never written).
    """
    path = perf_db_path(base_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        try:
            sql = "SELECT ts, tool, duration_ms, ok, repo FROM tool_calls"
            args: list = []
            clauses: list[str] = []
            if window_seconds is not None:
                clauses.append("ts >= ?")
                args.append(time.time() - float(window_seconds))
            if tool:
                clauses.append("tool = ?")
                args.append(tool)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY ts DESC"
            rows = conn.execute(sql, args).fetchall()
            return rows
        finally:
            conn.close()
    except Exception:
        logger.debug("perf_db_query failed at %s", path, exc_info=True)
        return []


def record_response_text(text: str) -> int:
    """Count a serialized tool response toward the session budget.

    Returns the cumulative session response tokens. Uses the same
    ~4-bytes-per-token estimate as the savings meter, so budget and
    savings figures share one scale.
    """
    try:
        tokens = len(text.encode("utf-8")) // _BYTES_PER_TOKEN
    except Exception:
        tokens = len(text) // _BYTES_PER_TOKEN
    return _state.record_response_tokens(tokens)


def budget_status() -> Optional[dict]:
    """Session budget snapshot ({limit, spent, state}) or None when unset."""
    return _state.budget_status()


def record_turn_estimate(estimated_tokens: int) -> Optional[dict]:
    """Open a plan_turn consumption estimate; returns calibration or None."""
    return _state.record_turn_estimate(estimated_tokens)


def avg_response_tokens_per_call() -> int:
    """Mean served response tokens per tool call this session (0 = no data)."""
    return _state.avg_response_tokens_per_call()


def note_served(symbol_ids) -> None:
    """Record symbol ids served by a ranking/search tool (yield tracking)."""
    _state.note_served(symbol_ids)


def note_fetched(symbol_ids) -> None:
    """Mark served symbol ids as followed through (fetch-through)."""
    _state.note_fetched(symbol_ids)


def served_symbol_ids() -> frozenset:
    """Every symbol id served this session — the handoff attestation record (#374)."""
    return _state.served_symbol_ids()


def note_edited_files(file_paths) -> None:
    """Mark served symbols in edited files as followed through (edit-through)."""
    _state.note_edited_files(file_paths)


def note_delivered(entries, base_path: Optional[str] = None) -> list:
    """Record symbol deliveries; return ids already delivered this session."""
    return _state.note_delivered(entries, base_path=base_path)


def note_call_signature(tool_name: str, args_hash: str) -> None:
    """Count repeated identical (tool, args) calls for the yield block."""
    _state.note_call_signature(tool_name, args_hash)


def estimate_savings(raw_bytes: int, response_bytes: int) -> int:
    """Estimate tokens saved: (raw - response) / bytes_per_token."""
    return max(0, (raw_bytes - response_bytes) // _BYTES_PER_TOKEN)


def cost_avoided(tokens_saved: int, total_tokens_saved: int) -> dict:
    """Formerly returned per-call cost breakdowns for _meta envelopes.

    Now returns an empty dict — cost detail is available via get_session_stats
    only. Removing 4-model cost tables from every per-tool _meta response
    reduces conversation-history token overhead by ~70 tokens/call.
    """
    return {}
