"""Persistent token savings tracker.

Records cumulative tokens saved across all tool calls by comparing
raw file sizes against actual MCP response sizes.

Stored in ~/.code-index/_savings.json — a single small JSON file.
No API calls, no file reads — only os.stat for file sizes.

Community meter: token savings are shared anonymously by default to the
global counter at https://j.gravelle.us/APIs/savings/post.php. Only {"delta": N, "total": M,
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
import math
import logging
import os
import queue
import re
import signal
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import config as _config
from . import savings_meter as _meter
from . import tool_counters as _tool_counters

logger = logging.getLogger(__name__)

# One identifier per entry into the registered call_tool. Set by the dispatcher,
# read by the telemetry sinks at write time. Default None so writes outside any
# dispatched call record NULL rather than inventing an identity.
_CURRENT_CALL_UID: ContextVar[Optional[str]] = ContextVar(
    "jcodemunch_current_call_uid",
    default=None,
)

_SAVINGS_FILE = "_savings.json"
# Held for the read-merge-replace of `_savings.json`, through the counters
# module's lock rule (`tool_counters.acquire_lock`), so parallel servers do not
# replace each other's batches. A batch whose lock wait runs out stays in memory.
_SAVINGS_LOCK_FILE = "_savings.lock"
_SAVINGS_LOCK_WAIT = 0.25
# A run of this many writes deferred by another server's file lock labels the
# meter stale; fewer is an ordinary moment of two servers writing.
_BUSY_DEFERRALS_BEFORE_STALE = 3
_SAVINGS_LOCK_WAIT_AT_EXIT = 3.0
# Calls a dispatcher reports (`note_call`) reach the file on the counters'
# schedule, so a session of tools that record no savings still stamps the meter
# as alive, and `savings_meter.label` can compare the two files' times.
_HEARTBEAT_FLUSH_CALLS = 20
_HEARTBEAT_FLUSH_SECONDS = 30.0
# A write that keeps failing warns once per this many seconds, then logs at debug.
_FLUSH_WARN_INTERVAL = 600.0
# How long the exit and signal paths wait for this process's own state lock.
# A signal handled while the same thread holds it would otherwise deadlock.
_STATE_LOCK_WAIT_AT_EXIT = 1.0
# A temp file a killed process left is removed at load once it is this old.
_STALE_TMP_SECONDS = 60.0
_TMP_NAME = re.compile(r"_savings\.json\.\d+\.tmp")

# Basis generation for the savings meter. Bumped when the DEFINITION of a
# tool's raw_bytes baseline changes, so a lifetime total spanning the change is
# never read as one consistent measurement. Generation 2 (2026-08-22) stopped
# summing nested symbol spans and stopped charging a file once per symbol
# selected from it; both over-counted, so pre-2 counts read HIGH. Nothing is
# rewritten — a recomputed history would be a guess wearing a measurement's
# clothes — the mixed basis is disclosed instead.
# Generation 3 (2026-11-11) divides by the measured tokenizer table
# (``savings_tokenizer.py``) instead of 4. On Claude's tokenizer bytes/4
# undercounted, so generation-2 counts read LOW; nothing recorded is rescaled.
SAVINGS_BASIS_GENERATION = 3

# Days of per-day savings history kept in _savings.json's "daily" map. Two years
# covers every window a dashboard offers ("this year", year-over-year) while
# keeping the file a few KB.
_DAILY_MAX_DAYS = 750
_SESSION_STATS_FILE = "session_stats.json"
_PULSE_FILE = "_pulse.json"
_PERF_DB_FILE = "telemetry.db"
_BYTES_PER_TOKEN = 4  # session budget only; tokens_saved reads savings_tokenizer.py


def _savings_tokenizer_name() -> str:
    try:
        from ..savings_tokenizer import active

        return active()
    except Exception:
        logger.debug("savings tokenizer name unavailable", exc_info=True)
        return "unknown"
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



def _isolate(obj):
    """Clone every CONTAINER in a tool result, sharing every leaf.

    ⚠⚠ **The result cache stored the caller's object and handed the same
    object back** (#572, @rknighton). The dispatcher then edits that object as
    a DISPLAY step -- ``_meta`` is deleted outright under ``meta_fields: []``
    (the shipped default) or under a per-call ``suppress_meta``, and replaced
    with a subset under any other ``meta_fields`` -- so a display preference
    reached into the cache and changed what every later caller was served.
    The loud symptom was ``KeyError: '_meta'`` on the second call; the quiet
    ones are worse, because one call passing ``suppress_meta`` silently
    emptied the entry for callers that had asked for metadata.

    ⚠ Containers only, and that is the whole point: leaves in a tool result
    are JSON-serialisable immutables by the time they reach here, so cloning
    them buys nothing and costs the copy. Measured against ``copy.deepcopy``
    on an 800 KB response: **4.15 ms vs 16.58 ms**, and 0.42 ms vs 1.67 ms at
    80 KB. Depth is unbounded on purpose -- a rule shaped to the containers
    the two current callers happen to use is a guard written against a
    spelling, and the point of fixing this in the cache rather than at the two
    call sites is the tool written next.

    ⚠ ``search_symbols`` keeps its OWN cache and learned this twice already
    (``_result_cache_get``: #377 item 3 for ``_meta.verdict``, then #404 for
    the rows). Both fixes were per-consumer; the shared cache never got one.
    """
    kind = type(obj)
    if kind is dict:
        return {k: _isolate(v) for k, v in obj.items()}
    if kind is list:
        return [_isolate(v) for v in obj]
    return obj


def _percentile_at(sorted_vals: "list[float]", pct: float) -> float:
    """Nearest-rank-style pick: the value at ``int(pct * n)``, clamped."""
    if not sorted_vals:
        return 0.0
    return sorted_vals[max(0, min(len(sorted_vals) - 1, int(pct * len(sorted_vals))))]


def latency_bucket(
    sorted_vals: "list[float]",
    errors: int,
    *,
    ring_capped: bool = False,
) -> dict:
    """The per-tool latency shape, from ONE place.

    ⚠⚠ **`total_ms` is the field that says where the time WENT.** p50/p95/max
    answer "how slow is one call", which is a different question and ranks
    differently: a tool at p95 900 ms called 4,000 times consumes ~60x one at
    p95 12,000 ms called three times, and a report ranked on rate alone puts the
    second at the top. Sum is free -- the durations are already in hand.

    ⚠⚠ **`p95_is_max` is MEASURED, never derived from the sample count.** The
    percentile index collapses to the last element for small n (every n <= 20 at
    the time of writing), so two published fields carry one sample. Comparing
    the computed values keeps the flag correct if the percentile ever changes.

    ⚠ `count_is_ring_capped` is not cosmetic: a share of total computed over
    capped rings UNDERSTATES the busiest tool, which is precisely the tool the
    share exists to find. Only the in-memory producer can cap; a windowed read
    of the perf db counts every row in its window.
    """
    n = len(sorted_vals)
    if not n:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0,
                "total_ms": 0.0, "errors": errors, "error_rate": 0.0}
    p95 = _percentile_at(sorted_vals, 0.95)
    bucket = {
        "count": n,
        "p50_ms": round(_percentile_at(sorted_vals, 0.5), 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(sorted_vals[-1], 2),
        "total_ms": round(sum(sorted_vals), 2),
        "errors": errors,
        "error_rate": round(errors / n, 3),
    }
    if p95 == sorted_vals[-1]:
        bucket["p95_is_max"] = True
    if ring_capped:
        bucket["count_is_ring_capped"] = True
    return bucket
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
        self._basis_first = SAVINGS_BASIS_GENERATION
        # Lifetime savings attributed per tool. LOCAL ONLY — never added to
        # the telemetry payload, which stays {delta, total, anon_id}.
        self._tool_totals: dict = {}
        self._tool_unflushed: dict = {}
        self._by_tool_since: Optional[str] = None
        self._total: int = 0          # cumulative total (disk + in-flight)
        self._unflushed: int = 0      # delta not yet written to disk
        self._encoding_total: int = 0    # cumulative MUNCH encoding savings
        self._encoding_unflushed: int = 0
        self._call_count: int = 0     # calls since last savings flush
        # The heartbeat (customer report, 1.108.407): calls not yet written to
        # `measured_days`. Separate from `_unflushed` because a call that saved
        # 0 is still a measurement, and the old writer could not tell them apart.
        self._hb_calls: int = 0
        # Dispatched calls (`note_call`), appended WITHOUT `_lock`: deque.append
        # is atomic, so the event loop never waits on the state lock.
        self._dispatch_ticks: deque = deque()
        self._hb_gate = threading.Lock()  # one heartbeat writer thread at a time
        self._hb_thread: Optional[threading.Thread] = None
        self._applied_mono: float = float("-inf")  # newest write this process applied
        self._last_flush_mono: float = time.monotonic()
        # The storage directory in force when the meter was loaded; every later
        # write goes there (Maintenance Practice 8, same rule as tool_counters).
        self._root_dir: Optional[str] = None
        # The storage directory in force when the first heartbeat call was
        # counted before the meter loaded. ⚠ The exit flush loads the meter at
        # exit, and the root in force then may not be the one the calls were
        # counted under (a test that restores CODE_INDEX_PATH before atexit
        # would write the developer's real store, Practice 8).
        self._counted_root: Optional[str] = None
        # What a reader of this process's total may be told about it.
        self._measured_through: Optional[datetime] = None  # last stamp on disk
        self._flush_failing: bool = False  # the last write attempt left counts unwritten
        self._busy_streak: int = 0  # consecutive writes deferred by another holder of the file lock
        self._load_failed: bool = False    # the file exists and its history was not read
        self._last_warn_mono: Optional[float] = None
        self._label: dict = {_meter.STATUS: "unknown", _meter.MEASURED_THROUGH: None, _meter.STALE_SINCE: None}
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
        # Counts every eviction. A caller that reads it before a tool runs and
        # again after can tell whether an index was written DURING the call:
        # the tool may then have read the old index and `cache_put` the answer
        # after the eviction, so the answer must go too (`cache_evict_if_written_since`).
        self._cache_write_epoch: int = 0
        self._cache_hits: dict = {}    # tool_name -> hit count
        self._cache_misses: dict = {}  # tool_name -> miss count
        # A hit is key-presence, which is NOT the same as a hit that still
        # describes the current index. Only the consumers that revalidate
        # against subject_state can tell, so they report back here; a hit
        # nobody validated stays UNKNOWN and is never counted as fresh.
        self._cache_hits_fresh: dict = {}  # tool_name -> validated, unchanged
        self._cache_hits_stale: dict = {}  # tool_name -> validated, subject moved
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
        # #476. Resolved-path -> rows written to THAT database since its last
        # trim. ⚠⚠ One int here counted writes across every store while the
        # trim it triggered ran on whichever store happened to make the 1000th
        # write, so with two stores alternating one `tool_calls` table was
        # never trimmed and grew past the cap. Keyed by the SAME `str(path)`
        # the connection cache uses -- a counter keyed differently from the
        # connection it guards is the same defect wearing a new key.
        self._perf_rows_since_trim: dict = {}

    def _ensure_loaded(self, base_path: Optional[str], root: Optional[str] = None) -> None:
        """Load persisted total from disk (once per process).

        *root*, when given, is the directory fixed when the pending calls were
        counted; it wins over whatever *base_path* resolves to now.
        """
        if self._loaded:
            return
        self._base_path = base_path
        self._root_dir = root or _tool_counters.storage_root(base_path)
        path = Path(self._root_dir) / _SAVINGS_FILE
        data, problem = _read_ledger(path)
        if data is None:
            # ⚠ The history exists and was not read. This process's total is
            # then only what it counts itself, so it is labeled unknown, and
            # every write is refused until the file reads (_merge_batch).
            logger.warning(
                "Savings file %s %s; its lifetime total is not loaded and nothing is written to it "
                "until it reads again: repair it, or move it aside to start a new one", path, problem,
            )
            self._load_failed = True
            data = {}
        self._measured_through = _meter.parse_stamp(data.get(_meter.LAST_FLUSH_AT))
        self._total = data.get("total_tokens_saved", 0)
        # A ledger that already holds counts but names no generation was written
        # before generations existed, so its history IS generation 1 — the
        # over-counting basis. Defaulting THAT to the current generation would
        # silently claim every historical count was taken the corrected way,
        # which is the one thing this field exists to prevent. A ledger with no
        # counts (fresh install, or a file holding only an anon_id) starts clean
        # at the current generation: there is no history to have taken wrongly.
        recorded = data.get("basis_generation_first", data.get("basis_generation"))
        if recorded is not None:
            self._basis_first = int(recorded)
        elif self._total:
            self._basis_first = 1
        else:
            self._basis_first = SAVINGS_BASIS_GENERATION
        self._encoding_total = data.get("total_encoding_tokens_saved", 0)
        self._anon_id = data.get("anon_id")
        by_tool = data.get("by_tool")
        self._tool_totals = dict(by_tool) if isinstance(by_tool, dict) else {}
        self._by_tool_since = data.get("by_tool_since")
        self._loaded = True
        _clear_stale_temp_files(Path(self._root_dir))
        self._refresh_label_locked()

    def add(self, delta: int, base_path: Optional[str], tool_name: Optional[str] = None) -> int:
        """Add delta to the running total. Returns new cumulative total."""
        due = False
        with self._lock:
            self._ensure_loaded(base_path)
            delta = max(0, delta)
            self._total += delta
            self._unflushed += delta
            self._pending_telemetry += delta
            self._session_tokens += delta
            self._session_calls += 1
            # Inside a dispatched call the dispatcher counts the call once
            # (note_call), whether or not the tool reaches the meter; outside one
            # (a CLI, a test, a direct caller) this is the only count.
            if _CURRENT_CALL_UID.get() is None:
                self._hb_calls += 1
            if tool_name:
                # Lifetime attribution, alongside the session breakdown. Keys come
                # from our own tool registry, so the map is bounded; an absent or
                # empty tool_name is skipped rather than opening a junk key.
                if self._by_tool_since is None:
                    self._by_tool_since = datetime.now().date().isoformat()
                self._tool_totals[tool_name] = self._tool_totals.get(tool_name, 0) + delta
                self._tool_unflushed[tool_name] = self._tool_unflushed.get(tool_name, 0) + delta
                self._session_tool_breakdown[tool_name] = (
                    self._session_tool_breakdown.get(tool_name, 0) + delta
                )
                self._session_tool_calls[tool_name] = (
                    self._session_tool_calls.get(tool_name, 0) + 1
                )
            self._call_count += 1
            due = self._call_count >= _FLUSH_INTERVAL
            total = self._total
        if due:
            # Outside `_lock`: the write never holds the state lock (see _flush).
            self._flush()
        return total

    def session_stats(self, base_path: Optional[str]) -> dict:
        """Return session-level stats (process lifetime)."""
        with self._lock:
            self._ensure_loaded(base_path)
            stats = self._build_stats_locked()
            item = self._session_stats_payload_locked(stats, force=True)
        self._write_session_stats_file(item)
        return stats

    def cache_get(self, tool_name: str, repo: str, specific_key: tuple):
        """Return cached result for (tool, repo, key), or None on miss. Thread-safe."""
        with self._lock:
            full_key = (tool_name, repo, specific_key)
            if full_key in self._result_cache:
                self._result_cache.move_to_end(full_key)
                self._cache_hits[tool_name] = self._cache_hits.get(tool_name, 0) + 1
                return _isolate(self._result_cache[full_key])
            self._cache_misses[tool_name] = self._cache_misses.get(tool_name, 0) + 1
            return None

    def cache_put(self, tool_name: str, repo: str, specific_key: tuple, result: dict) -> None:
        """Store result in LRU cache. Evicts oldest entry when full. Thread-safe."""
        with self._lock:
            full_key = (tool_name, repo, specific_key)
            self._result_cache[full_key] = _isolate(result)
            self._result_cache.move_to_end(full_key)
            if len(self._result_cache) > _RESULT_CACHE_MAXSIZE:
                self._result_cache.popitem(last=False)

    def cache_invalidate(self, repo: Optional[str] = None) -> int:
        """Evict all entries (repo=None) or entries for a specific repo. Returns evicted count."""
        with self._lock:
            self._cache_write_epoch += 1
            if repo is None:
                count = len(self._result_cache)
                self._result_cache.clear()
                return count
            to_delete = [k for k in self._result_cache if k[1] == repo]
            for k in to_delete:
                del self._result_cache[k]
            return len(to_delete)

    def cache_write_epoch(self) -> int:
        """The eviction count so far; compare two readings to see a write between them."""
        with self._lock:
            return self._cache_write_epoch

    def cache_evict_if_written_since(self, epoch: int) -> int:
        """Evict everything when an index write evicted the cache since ``epoch``.

        The gap it closes: a tool loads the index, a writer in another thread
        commits and evicts, then the tool ``cache_put``s the answer it computed
        from the old index. That entry survives the eviction, so the caller
        that framed the tool call evicts again when it sees the epoch moved.
        Everything goes, not one repository: the frame does not know the
        tool's cache key. Returns the number of entries evicted.
        """
        with self._lock:
            if self._cache_write_epoch == epoch:
                return 0
            count = len(self._result_cache)
            self._result_cache.clear()
            return count

    def cache_lookup_recorded(self, tool_name: str, hit: bool) -> None:
        """Count one lookup a consumer's PRIVATE cache answered (#864).

        ``cache_get`` counts the shared LRU's lookups itself. A consumer with
        its own cache (``search_symbols``) must report each lookup here, or a
        hit it later reports to ``cache_hit_validated`` lands in the validated
        buckets with no hit beside it: ``total_hits: 0`` next to a revalidated
        rate of 1.0, and ``hits_unvalidated`` short by the difference for every
        other tool.
        """
        with self._lock:
            bucket = self._cache_hits if hit else self._cache_misses
            bucket[tool_name] = bucket.get(tool_name, 0) + 1

    def cache_hit_validated(self, tool_name: str, stale: bool) -> None:
        """Record that an already-counted hit was checked against the index.

        Called by a consumer that revalidates a cached entry against
        ``subject_state.changed``. Never counts a hit — ``cache_get`` or, for a
        private cache, ``cache_lookup_recorded`` already did — so this cannot
        inflate ``hit_rate``. ⚠⚠ A caller that reports here without having
        counted the hit breaks ``validated <= hits`` (#864);
        ``tests/test_every_served_cache_hit_is_counted_once.py`` fails on it.

        ⚠ Exactly ONE of the three result-cache consumers does this today:
        ``search_symbols``. ``find_references`` and ``get_blast_radius`` serve
        cached entries with no check at all, which is why `hits_unvalidated`
        is a reported number rather than an edge case.
        """
        with self._lock:
            bucket = self._cache_hits_stale if stale else self._cache_hits_fresh
            bucket[tool_name] = bucket.get(tool_name, 0) + 1

    @staticmethod
    def _revalidated_block(h: int, fresh: int, stale: int) -> dict:
        """The three-bucket view of `h` hits. Unvalidated is UNKNOWN, not fresh."""
        validated = fresh + stale
        return {
            "hits_validated_fresh": fresh,
            "hits_validated_stale": stale,
            # ⚠ Never folded into either. Nobody checked these.
            "hits_unvalidated": max(0, h - validated),
            # None, NOT 0.0 and NOT the raw rate: with nothing validated this
            # is a could-not-establish, and a number here would be read as a
            # measurement.
            "hit_rate_revalidated": (
                round(fresh / validated, 3) if validated else None
            ),
            "validated_share": round(validated / h, 3) if h else None,
        }

    def cache_stats(self) -> dict:
        """Return cache hit/miss stats. Thread-safe.

        ⚠⚠ ``hit_rate`` is the RAW rate: a hit is key-presence in the LRU, which
        is not the same as a hit that still describes the current index. The
        cache is invalidated only by index-mutating tools **in this process**,
        so an out-of-process reindex (the PostToolUse ``index-file`` spawn, the
        watcher, a second server instance) leaves entries serving happily.
        arXiv:2608.20280 measured raw rates of 51-60% falling to 1.1-2.2% once
        validity was checked; quoting a raw rate as a performance result is the
        defect it names.

        ⚠ So the raw rate is kept (it is the right measure of how often the LRU
        answered) and the revalidated view is reported BESIDE it, never instead:
        ``hits_validated_fresh`` / ``hits_validated_stale`` / ``hits_unvalidated``.
        ⚠⚠ **Three buckets, not two.** Of the three result-cache consumers only
        ``search_symbols`` revalidates; ``find_references`` and
        ``get_blast_radius`` serve cached hits with no check at all. The
        unvalidated bucket is therefore non-empty by construction, and folding
        it into either real bucket would be inventing a measurement.
        """
        with self._lock:
            total_hits = sum(self._cache_hits.values())
            total_misses = sum(self._cache_misses.values())
            total_lookups = total_hits + total_misses
            total_fresh = sum(self._cache_hits_fresh.values())
            total_stale = sum(self._cache_hits_stale.values())
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
                    **self._revalidated_block(
                        h,
                        self._cache_hits_fresh.get(tool, 0),
                        self._cache_hits_stale.get(tool, 0),
                    ),
                }
            return {
                "total_hits": total_hits,
                "total_misses": total_misses,
                "hit_rate": round(total_hits / total_lookups, 3) if total_lookups else 0.0,
                "hit_rate_basis": "raw_key_presence",
                "cached_entries": len(self._result_cache),
                **self._revalidated_block(total_hits, total_fresh, total_stale),
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
            # A lifetime total that spans a basis change is not one measurement.
            # mixed_basis=True says the figure includes counts taken before the
            # baseline definition was corrected downward on 2026-08-22.
            "total_tokens_saved_basis": {
                "generation": SAVINGS_BASIS_GENERATION,
                "first_generation": self._basis_first,
                "mixed_basis": self._basis_first != SAVINGS_BASIS_GENERATION,
                # Which table new calls divide by; earlier calls are as recorded.
                "tokenizer": _savings_tokenizer_name(),
            },
            # Lifetime per-tool attribution, and the honest complement: what the
            # meter earned before it could attribute anything. A caller that sees
            # only `lifetime_by_tool` would read the shortfall against
            # total_tokens_saved as missing data.
            "lifetime_by_tool": dict(self._tool_totals),
            "lifetime_by_tool_since": self._by_tool_since,
            "lifetime_unattributed": max(0, self._total - sum(self._tool_totals.values())),
            # Whether total_tokens_saved is still being measured
            # (`savings_meter`): current / stale / unknown, the last time it
            # reached the disk, and the date it stopped when it did.
            **self._lifetime_label_locked(),
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
        """Compute the per-tool latency bucket from each ring. Caller holds _lock.

        ⚠ The ring is capped at ``_LATENCY_RING_DEFAULT``, so a tool past the cap
        reports a bucket over its most recent calls, not over the session. The
        bucket says so; see ``latency_bucket``.
        """
        out: dict = {}
        for tool, ring in self._tool_latencies.items():
            if not ring:
                continue
            out[tool] = latency_bucket(
                sorted(ring),
                self._tool_errors.get(tool, 0),
                ring_capped=len(ring) >= _LATENCY_RING_DEFAULT,
            )
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
                return (root / _PERF_DB_FILE).resolve()
            except Exception:
                logger.debug("Failed to resolve perf db path at %s", base_path, exc_info=True)
                return None
        if self._perf_db_path_cached is not None:
            return self._perf_db_path_cached
        try:
            root = Path(self._base_path) if self._base_path else Path.home() / ".code-index"
            root.mkdir(parents=True, exist_ok=True)
            path = (root / _PERF_DB_FILE).resolve()
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

        ⚠ Also an atexit handler, so the wait for `_lock` is bounded: an exit
        while the same thread holds it (a signal inside a locked region) hung
        the interpreter here once `flush` stopped hanging first.
        """
        if not self._lock.acquire(timeout=_STATE_LOCK_WAIT_AT_EXIT):
            logger.debug("perf connections not closed: the state lock is busy")
            return 0
        try:
            conns = list(self._perf_conns.items())
            self._perf_conns.clear()
            # #476. Same lifetime as the connections it is keyed alongside, so
            # a key cannot outlive the store it names. ⚠ The cost is bounded
            # and deliberate: a database whose connection is dropped mid-cycle
            # forgets its progress toward the next trim, so `tool_calls` can
            # carry up to ~1000 rows of slack over the cap before the next one
            # fires. The cap is already an every-1000-writes approximation, and
            # this keeps the two structures from disagreeing about which stores
            # exist -- which is the class of bug #476 itself was.
            self._perf_rows_since_trim.clear()
        finally:
            self._lock.release()
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

        Thin wrapper over :meth:`_ensure_perf_db_locked_with_key` for the two
        callers that need only the connection. ⚠ Per-database bookkeeping must
        use the with-key form: re-deriving the key by calling ``_perf_db_path``
        a second time would repeat its ``mkdir`` on every write, and #442 exists
        because per-write cost on this path was already the whole problem.
        """
        conn, _key = self._ensure_perf_db_locked_with_key(base_path)
        return conn

    def _ensure_perf_db_locked_with_key(
        self, base_path: Optional[str] = None
    ) -> tuple[Optional[sqlite3.Connection], Optional[str]]:
        """Return ``(connection, cache_key)``; the key identifies the database.

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
            return None, None
        path = self._perf_db_path(base_path)
        if path is None:
            return None, None
        cached = self._perf_conns.get(str(path))
        if cached is not None:
            if self._perf_conn_usable(cached, path):
                return cached, str(path)
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
            # #456. Correlation keys, added the same additive way returned_count was.
            #
            # The join is best-effort, by design. `tool_calls` is trimmed to a rolling cap while
            # `ranking_events` is not, so older events will outlive their `tool_calls` row.
            # Queries should use `LEFT JOIN` and expect misses. These are correlation keys, not
            # referential integrity. Aligning retention is a separate concern.
            for _table in ("tool_calls", "ranking_events"):
                self._add_column_if_missing(conn, _table, "session_uid", "TEXT")
                self._add_column_if_missing(conn, _table, "call_uid", "TEXT")
            self._perf_conns[str(path)] = conn
            return conn, str(path)
        except Exception:
            logger.debug("Failed to open perf db at %s", path, exc_info=True)
            # v1.108.188. The kill-switch covers the DEFAULT db only. One unwritable
            # caller-supplied path must not disable telemetry for every other store
            # in the process — the flag was written when there was only one path it
            # could ever mean.
            if base_path is None:
                self._perf_db_failed = True
            return None, None

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
                        " identity_hit, repo_is_stale, session_uid, call_uid, "
                        "returned_count) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                            self._session_uid,
                            _CURRENT_CALL_UID.get(),
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
        conn, db_key = self._ensure_perf_db_locked_with_key(base_path)
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT INTO tool_calls (ts, tool, duration_ms, ok, repo, session_uid, call_uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), tool, float(duration_ms), 1 if ok else 0, repo or None,
                 self._session_uid, _CURRENT_CALL_UID.get()),
            )
            # #476. Count against THIS database. The trim below runs on `conn`,
            # so the counter that triggers it has to name the same store, or a
            # second store's writes spend a trim on the first one's table.
            rows_since_trim = self._perf_rows_since_trim.get(db_key, 0) + 1
            self._perf_rows_since_trim[db_key] = rows_since_trim
            if rows_since_trim >= 1000:
                cap = max(1000, int(_config.get("perf_telemetry_max_rows", _PERF_DB_MAX_ROWS_DEFAULT)))
                conn.execute(
                    "DELETE FROM tool_calls WHERE rowid IN ("
                    "  SELECT rowid FROM tool_calls ORDER BY ts ASC LIMIT MAX(0, "
                    "    (SELECT COUNT(*) FROM tool_calls) - ?"
                    "  )"
                    ")",
                    (cap,),
                )
                self._perf_rows_since_trim[db_key] = 0
        except Exception:
            logger.debug("Failed to persist perf row for %s", tool, exc_info=True)
        finally:
            pass  # connection is process-cached (#442); see close_perf_dbs

    def _session_stats_payload_locked(self, stats: dict, force: bool = False) -> "Optional[tuple[str, str]]":
        """Decide whether session_stats.json is due. Must be called with _lock held.

        Writes are gated by stats_file_interval config (default 3 calls).
        Set to 0 to disable all session_stats.json writes.
        Pass force=True to bypass the interval check (e.g. explicit get_session_stats call).
        Returns ``(root, text)`` for `_write_session_stats_file`, which runs AFTER
        the lock is released: no disk write happens under `_lock`.
        """
        stats_file_interval = _get_stats_file_interval()
        if stats_file_interval == 0 and not force:
            return None
        self._stats_call_count += 1
        if not force and self._stats_call_count < stats_file_interval:
            return None
        self._stats_call_count = 0
        try:
            payload = {**stats, "last_updated": datetime.now(timezone.utc).isoformat()}
            return (self._root_dir or _tool_counters.storage_root(self._base_path)), json.dumps(payload, indent=2)
        except Exception:
            logger.debug("Failed to build session stats", exc_info=True)
            return None

    @staticmethod
    def _write_session_stats_file(item: "Optional[tuple[str, str]]") -> None:
        if item is None:
            return
        root, text = item
        path = None
        try:
            path = _session_stats_path(root)
            path.write_text(text, encoding="utf-8")
        except Exception:
            logger.debug("Failed to write session stats to %s", path, exc_info=True)

    def get_total(self, base_path: Optional[str]) -> int:
        with self._lock:
            self._ensure_loaded(base_path)
            return self._total

    def note_call(self) -> None:
        """Count one dispatched call toward the heartbeat.

        ⚠⚠ **Never takes `_lock` and never waits for the disk.** The dispatcher
        calls this on the event loop, and a signal handled on the main thread
        while it ran would otherwise re-enter `flush` against a lock the same
        thread holds. The count is a `deque.append` (atomic in CPython); a write
        that is due (`_HEARTBEAT_FLUSH_CALLS` calls, or the first call after
        `_HEARTBEAT_FLUSH_SECONDS`) is handed to a short-lived thread behind a
        gate taken with ``blocking=False``.
        """
        if self._root_dir is None and self._counted_root is None:
            # Fix the root these calls belong to now, not at the (exit) flush.
            try:
                self._counted_root = _tool_counters.storage_root(self._base_path)
            except Exception:
                logger.debug("savings root not resolved at count time", exc_info=True)
        self._dispatch_ticks.append(None)
        if (
            len(self._dispatch_ticks) + self._hb_calls < _HEARTBEAT_FLUSH_CALLS
            and time.monotonic() - self._last_flush_mono < _HEARTBEAT_FLUSH_SECONDS
        ):
            return
        if not self._hb_gate.acquire(blocking=False):
            return  # a heartbeat write is already running; it or the next one carries this call
        try:
            # Not a daemon: a write in progress finishes before the interpreter
            # exits, so the lock file is never left behind by a normal exit.
            thread = threading.Thread(target=self._flush_in_thread, name="jcm-savings-heartbeat")
            self._hb_thread = thread
            thread.start()
        except Exception:
            self._hb_gate.release()
            logger.debug("savings heartbeat thread not started", exc_info=True)

    def _flush_in_thread(self) -> None:
        try:
            self._flush(_SAVINGS_LOCK_WAIT)
        except Exception:
            logger.debug("savings heartbeat flush failed", exc_info=True)
        finally:
            self._hb_gate.release()

    def _acquire_state(self, wait: Optional[float]) -> bool:
        return self._lock.acquire() if wait is None else self._lock.acquire(timeout=wait)

    def _take_batch_locked(self) -> Optional[dict]:
        """Swap the pending counts out for a write. Must be called with _lock held."""
        ticks = len(self._dispatch_ticks)
        for _ in range(ticks):
            self._dispatch_ticks.popleft()
        calls = self._hb_calls + ticks
        if not (self._unflushed or calls or self._encoding_unflushed or self._tool_unflushed):
            return None
        batch = {
            "saved": self._unflushed,
            "calls": calls,
            "by_tool": self._tool_unflushed,
            "encoding": self._encoding_unflushed,
            "telemetry": self._pending_telemetry,
            "anon_id": self._anon_id,
            "by_tool_since": self._by_tool_since,
        }
        self._unflushed = 0
        self._hb_calls = 0
        self._tool_unflushed = {}
        self._encoding_unflushed = 0
        self._pending_telemetry = 0
        return batch

    def _put_back_locked(self, batch: dict) -> None:
        """A batch that was not written goes back for the next flush (`tool_counters._put_back`)."""
        self._unflushed += batch["saved"]
        self._hb_calls += batch["calls"]
        for tool, amount in batch["by_tool"].items():
            self._tool_unflushed[tool] = self._tool_unflushed.get(tool, 0) + amount
        self._encoding_unflushed += batch["encoding"]
        self._pending_telemetry += batch["telemetry"]

    def _refresh_label_locked(self) -> None:
        # Replaced whole, so `lifetime_label()` can read it without the lock.
        self._label = self._lifetime_label_locked()

    def _flush(self, lock_wait: float = _SAVINGS_LOCK_WAIT, state_wait: Optional[float] = None) -> None:
        """Write the pending counts to disk.

        ⚠⚠ `_lock` is held only to swap the batch out and to apply the outcome,
        never across the file-lock wait, the read, the write or `os.replace`. A
        batch that was not written goes back (`_put_back_locked`). *state_wait*
        bounds the wait for `_lock` itself: the exit and signal paths pass one,
        so a lock the same thread already holds (a signal handled inside a
        locked region) costs that wait instead of a deadlock.
        """
        if not self._acquire_state(state_wait):
            logger.warning("Savings not written: the meter's in-memory state is busy (exit or signal path)")
            return
        try:
            if not self._loaded:
                if not (self._hb_calls or self._dispatch_ticks):
                    return
                self._ensure_loaded(self._base_path, self._counted_root)
            self._call_count = 0
            batch = self._take_batch_locked()
            root = Path(self._root_dir or _tool_counters.storage_root(self._base_path))
            stats_item = None
            if batch is None:
                stats_item = self._session_stats_payload_locked(self._build_stats_locked())
            else:
                self._last_flush_mono = time.monotonic()
        finally:
            self._lock.release()
        if batch is None:
            self._write_session_stats_file(stats_item)
            return

        outcome = _merge_batch(root, batch, lock_wait)

        if not self._acquire_state(state_wait):
            # Exit path with the state held elsewhere: what was written is on
            # disk; what was not dies with the process either way.
            return
        send = None
        log = None
        try:
            if outcome["ok"]:
                if outcome["mono"] >= self._applied_mono:
                    # Follow the file, which also holds what other servers wrote.
                    self._applied_mono = outcome["mono"]
                    self._total = outcome["total"] + self._unflushed
                    self._encoding_total = outcome["encoding_total"] + self._encoding_unflushed
                    if outcome["by_tool"] is not None:
                        merged = dict(outcome["by_tool"])
                        for tool, amount in self._tool_unflushed.items():
                            merged[tool] = merged.get(tool, 0) + amount
                        self._tool_totals = merged
                    self._measured_through = outcome["stamp"]
                if self._anon_id is None:
                    self._anon_id = outcome["anon_id"]
                if outcome["by_tool_since"]:
                    self._by_tool_since = outcome["by_tool_since"]
                self._flush_failing = False
                self._busy_streak = 0
                self._load_failed = False
                # Send batched telemetry. We send the absolute lifetime total (just
                # written) so the server can GREATEST-converge and recover any
                # previously dropped sends; the delta rides along for legacy servers.
                if batch["telemetry"] > 0 and _config.get("share_savings", True):
                    send = (batch["telemetry"], outcome["total"], self._anon_id)
            else:
                # Not written: nothing is sent either, since the total would not
                # be one this machine holds.
                self._put_back_locked(batch)
                if outcome.get("busy"):
                    # Another server holding the file for a moment is ordinary,
                    # so one deferral is not a stopped meter; a run of them is.
                    self._busy_streak += 1
                    self._flush_failing = self._flush_failing or self._busy_streak >= _BUSY_DEFERRALS_BEFORE_STALE
                else:
                    self._flush_failing = True
                level = outcome["level"]
                if level == "warning":
                    now = time.monotonic()
                    if self._last_warn_mono is not None and now - self._last_warn_mono < _FLUSH_WARN_INTERVAL:
                        level = "debug"  # still failing; said once per interval
                    else:
                        self._last_warn_mono = now
                log = (level, outcome["msg"], outcome["args"], outcome["exc_info"])
            self._refresh_label_locked()
            stats_item = self._session_stats_payload_locked(self._build_stats_locked())
            # ⚠ Opt-in perf db (off by default), a SQLite upsert on the connection
            # `_lock` guards; it stays under the lock with record_latency's writes.
            self._persist_session_yield_locked(self._delivery_base_path)
        finally:
            self._lock.release()
        if log is not None:
            level, msg, args, exc_info = log
            (logger.warning if level == "warning" else logger.debug)(msg, *args, exc_info=exc_info)
        if send is not None:
            _share_savings(*send)
        self._write_session_stats_file(stats_item)

    def _lifetime_label_locked(self) -> dict:
        """The `savings_meter` label for THIS process's lifetime total."""
        through = _meter.format_stamp(self._measured_through)
        if self._load_failed:
            # The total leaves out a history that exists and was not read.
            return {_meter.STATUS: "unknown", _meter.MEASURED_THROUGH: through, _meter.STALE_SINCE: None}
        if self._flush_failing:
            if self._measured_through is None:
                return {_meter.STATUS: "unknown", _meter.MEASURED_THROUGH: None, _meter.STALE_SINCE: None}
            return {_meter.STATUS: "stale", _meter.MEASURED_THROUGH: through,
                    _meter.STALE_SINCE: _meter.day_of(self._measured_through)}
        return {_meter.STATUS: "current", _meter.MEASURED_THROUGH: through, _meter.STALE_SINCE: None}

    def lifetime_label(self) -> dict:
        """The label for this process's lifetime total, read WITHOUT the lock (a snapshot)."""
        return dict(self._label)

    def flush(self, wait: float = _SAVINGS_LOCK_WAIT_AT_EXIT, state_wait: float = _STATE_LOCK_WAIT_AT_EXIT) -> None:
        """Public flush — called at atexit and on SIGTERM/SIGINT.

        Both bounds apply: *wait* for another process's file lock, *state_wait*
        for this process's own state lock (see `_flush`).
        """
        self._flush(lock_wait=wait, state_wait=state_wait)


def _merge_into(data: dict, batch: dict) -> "tuple[str, datetime]":
    """Add *batch* to the parsed ledger *data* in place; return the anon id and the stamp.

    ⚠ May raise on a ledger that parses but holds the wrong type; `_merge_batch`
    refuses the file then, as it refuses one it cannot read.
    """
    anon_id = batch["anon_id"]
    if anon_id is None:
        anon_id = data.get("anon_id") or str(uuid.uuid4())
    data["anon_id"] = anon_id
    data["total_tokens_saved"] = data.get("total_tokens_saved", 0) + batch["saved"]
    # Record the first generation this file was ever written under, and the
    # current one. Equal => the lifetime total is one consistent basis.
    data.setdefault("basis_generation_first", data.get("basis_generation", SAVINGS_BASIS_GENERATION))
    data["basis_generation"] = SAVINGS_BASIS_GENERATION
    # Per-tool attribution. ⚠⚠ It starts EMPTY and can never be backfilled —
    # the meter only ever stored a scalar — so sum(by_tool) is less than
    # total_tokens_saved by however much was earned before this shipped.
    # `by_tool_since` dates the start so that gap reads as unattributed
    # history rather than as loss; session_stats reports it as a number.
    if batch["by_tool"]:
        merged = data.get("by_tool")
        merged = dict(merged) if isinstance(merged, dict) else {}
        for tool, amount in batch["by_tool"].items():
            merged[tool] = merged.get(tool, 0) + amount
        data["by_tool"] = merged
        data.setdefault("by_tool_since", batch["by_tool_since"] or datetime.now().date().isoformat())
    # Daily rollup alongside the lifetime total, so windowed views ("today",
    # "this month") can come from THIS meter — the authoritative record —
    # instead of transcript scans, which miss cleared history and model
    # savings conservatively (observed 4 orders of magnitude under the
    # meter on a heavy install). Flush batches are small (_FLUSH_INTERVAL
    # calls), so crediting the whole batch to the flush day mis-dates at
    # most one batch across midnight. Local date: "today" means today
    # where the user sits. Capped so the file stays small.
    day = datetime.now().date().isoformat()
    if batch["saved"]:
        daily = data.get("daily")
        if not isinstance(daily, dict):
            daily = {}
        daily[day] = daily.get(day, 0) + batch["saved"]
        if len(daily) > _DAILY_MAX_DAYS:
            for stale in sorted(daily)[: len(daily) - _DAILY_MAX_DAYS]:
                del daily[stale]
        data["daily"] = daily
    # ⚠⚠ The heartbeat. `daily` gains a day only when something was
    # EARNED, so a meter that measured 0 and a meter that had stopped
    # left the same file, and every reader showed a frozen total as
    # current. Every flush that carries a call stamps the time and the
    # day: a 0 in `measured_days` is a measurement, a missing day is not.
    now = datetime.now(timezone.utc)
    data[_meter.LAST_FLUSH_AT] = now.isoformat(timespec="seconds")
    measured = data.get(_meter.MEASURED_DAYS)
    measured = dict(measured) if isinstance(measured, dict) else {}
    prior = measured.get(day)
    prior = prior if isinstance(prior, dict) else {}
    measured[day] = {
        "calls": _as_count(prior.get("calls")) + batch["calls"],
        "saved": _as_count(prior.get("saved")) + batch["saved"],
    }
    if len(measured) > _DAILY_MAX_DAYS:
        for stale in sorted(measured)[: len(measured) - _DAILY_MAX_DAYS]:
            del measured[stale]
    data[_meter.MEASURED_DAYS] = measured
    if batch["encoding"]:
        data["total_encoding_tokens_saved"] = data.get("total_encoding_tokens_saved", 0) + batch["encoding"]
    return anon_id, now


def _merge_batch(root: Path, batch: dict, lock_wait: float) -> dict:
    """Merge *batch* into `<root>/_savings.json` under `_savings.lock`. Touches no `_State`.

    Returns ``{"ok": True, ...the file's new totals}`` or ``{"ok": False,
    "level", "msg", "args", "exc_info"}`` for the caller to log after it has
    released its own lock.
    """

    def failed(level: str, msg: str, *args, exc_info: bool = False) -> dict:
        return {"ok": False, "level": level, "msg": msg, "args": args, "exc_info": exc_info}

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return failed("warning", "Savings directory %s could not be created; counts kept for the next write", root,
                      exc_info=True)
    lock = root / _SAVINGS_LOCK_FILE
    if not _tool_counters.acquire_lock(lock, lock_wait):
        out = failed("debug", "Savings file busy (%s held); counts kept for the next write", lock)
        out["busy"] = True
        return out
    path = root / _SAVINGS_FILE
    try:
        data, problem = _read_ledger(path)
        if data is None:
            # ⚠⚠ Never treat an unreadable file as empty: merging into {} and
            # writing that back erased a lifetime of history.
            return failed(
                "warning",
                "Savings file %s %s. Nothing is written to it (and nothing is sent to the "
                "community meter) until it reads again: repair it, or move it aside to start "
                "a new one. %d tokens saved over %d calls are kept in memory for the next write",
                path, problem, batch["saved"], batch["calls"],
            )
        try:
            anon_id, now = _merge_into(data, batch)
        except Exception as exc:
            # ⚠ `_read_ledger` refuses every mistyped field the merge adds to,
            # so this is reached only by a shape it does not know yet, or by a
            # defect in the merge itself. Either way the batch is kept and the
            # file left whole, and the traceback is logged: the message must
            # not leave a bug in our code looking like damage in the user's file.
            return failed(
                "warning",
                "Savings file %s could not be merged (%s: %s). Nothing is written to it "
                "(and nothing is sent to the community meter) until a write succeeds. "
                "%d tokens saved over %d calls are kept in memory for the next write",
                path, type(exc).__name__, exc, batch["saved"], batch["calls"],
                exc_info=True,
            )
        # Through a temp file named for this process and os.replace, so a
        # reader never sees a half-written file and a failed write leaves
        # the old one whole.
        tmp = root / f"{_SAVINGS_FILE}.{os.getpid()}.tmp"
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            return failed("warning", "Savings file %s could not be written; counts kept for the next write", path,
                          exc_info=True)
        mono = time.monotonic()  # taken under the file lock, so writes in this process order by it
    finally:
        try:
            lock.unlink()
        except OSError:
            logger.debug("Savings lock %s not removed", lock, exc_info=True)
    by_tool = data.get("by_tool")
    return {
        "ok": True,
        "total": data["total_tokens_saved"],
        "encoding_total": data.get("total_encoding_tokens_saved", 0),
        "by_tool": dict(by_tool) if isinstance(by_tool, dict) else None,
        "anon_id": anon_id,
        "by_tool_since": data.get("by_tool_since"),
        "stamp": now,
        "mono": mono,
    }


def _clear_stale_temp_files(root: Path) -> None:
    """Remove `_savings.json.<pid>.tmp` files a killed process left, once they are a minute old."""
    try:
        candidates = list(root.glob(f"{_SAVINGS_FILE}.*.tmp"))
    except OSError:
        return
    now = time.time()
    for tmp in candidates:
        if not _TMP_NAME.fullmatch(tmp.name):
            continue
        try:
            if now - tmp.stat().st_mtime > _STALE_TMP_SECONDS:
                tmp.unlink()
        except OSError:
            logger.debug("stale savings temp file %s not removed", tmp, exc_info=True)


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

def _as_count(value: Any) -> int:
    return int(value) if _is_whole_count(value) else 0


def _read_ledger(path: Path) -> "tuple[Optional[dict], Optional[str]]":
    """`_savings.json`'s content: ``({}, None)`` when absent, ``(None, why)`` when it exists and is not usable."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return None, f"could not be read ({type(exc).__name__})"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "is not valid JSON"
    if not isinstance(data, dict):
        return None, "does not hold a JSON object"
    # A finite non-negative number. `receipt` and `diagnose` read through here too (L-297).
    total = data.get("total_tokens_saved", 0)
    if not _is_amount(total):
        return None, "holds a total that is not a non-negative number"
    # ⚠ Every field the meter does arithmetic on, read by the loader AND the
    # merge: a string there raised TypeError inside a tool call's savings step
    # (`add`) and inside the merge, which lost the batch and left the file
    # frozen while the meter read `current`.
    if "total_encoding_tokens_saved" in data and not _is_amount(data["total_encoding_tokens_saved"]):
        return None, "holds an encoding total that is not a non-negative number"
    # A table the merge adds to is history. One that IS a table but holds a
    # mistyped value is refused for the user to repair rather than
    # overwritten, and so is a value of the wrong kind that still holds
    # counts (a list of numbers where a table belongs). Only a value that
    # holds no count at all, a stray string or null, is rebuilt by the merge,
    # as before (`test_corrupt_daily_shape_is_replaced_not_fatal`).
    for key in ("by_tool", "daily", _meter.MEASURED_DAYS):
        table = data.get(key)
        if table is None or isinstance(table, dict):
            continue
        if _holds_a_count(table):
            return None, f"holds a `{key}` that is not a table but holds counts"
    for key in ("by_tool", "daily"):
        table = data.get(key)
        if isinstance(table, dict) and not all(_is_amount(v) for v in table.values()):
            return None, f"holds a `{key}` table whose values are not all non-negative numbers"
    measured = data.get(_meter.MEASURED_DAYS)
    if isinstance(measured, dict):
        for day in measured.values():
            if not isinstance(day, dict) or not all(
                _is_whole_count(day[k]) for k in ("calls", "saved") if k in day
            ):
                return None, (
                    f"holds a `{_meter.MEASURED_DAYS}` day that is not a table of "
                    "non-negative whole counts"
                )
    for key in ("basis_generation", "basis_generation_first"):
        if key in data and not _is_whole_count(data[key]):
            return None, f"holds a `{key}` that is not a whole number"
    return data, None


def _is_whole_count(value: object) -> bool:
    """A non-negative whole number; ``2.0`` counts, ``2.5``, ``"2"`` and ``True`` do not."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer()


def _holds_a_count(value: object) -> bool:
    """True when *value*, of any shape, contains a number anywhere."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, dict):
        return any(_holds_a_count(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_a_count(v) for v in value)
    return False


def _is_amount(value: object) -> bool:
    """A finite non-negative number: how `_read_ledger` reads a total, for the meter, `receipt` and `diagnose`.

    ``json.loads`` accepts ``Infinity`` and ``NaN``; neither is a count, and
    writing one back produces a file other JSON readers refuse.
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _savings_path(base_path: Optional[str] = None) -> Path:
    # The shared root rule: honors CODE_INDEX_PATH like the counters,
    # `receipt` and `diagnose`, which all read this file.
    root = Path(_tool_counters.storage_root(base_path))
    root.mkdir(parents=True, exist_ok=True)
    return root / _SAVINGS_FILE


def _session_stats_path(base_path: Optional[str] = None) -> Path:
    root = Path(_tool_counters.storage_root(base_path))
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
        due = _state._call_count >= _FLUSH_INTERVAL
        total = _state._encoding_total
    if due:
        _state._flush()
    return total


def lifetime_label() -> dict:
    """`lifetime_status` / `lifetime_measured_through` / `lifetime_stale_since` for
    this process's lifetime total. Lock-free (a snapshot), so the dispatcher can
    label `_meta.total_tokens_saved` on the event loop."""
    return _state.lifetime_label()


def note_call() -> None:
    """Count one dispatched call toward the savings heartbeat. The dispatcher calls it once per client call."""
    try:
        _state.note_call()
    except Exception:
        logger.debug("savings heartbeat not counted", exc_info=True)


def get_total_encoding_saved(base_path: Optional[str] = None) -> int:
    with _state._lock:
        _state._ensure_loaded(base_path)
        return _state._encoding_total


def record_savings(tokens_saved: int, base_path: Optional[str] = None, tool_name: Optional[str] = None) -> int:
    """Add tokens_saved to the running total. Returns new cumulative total.

    Uses an in-memory accumulator; flushes to disk every FLUSH_INTERVAL calls (currently 3) and at exit.

    ⚠ L-180: a text-search-style tool (``savings_credit.TEXT_SEARCH_STYLE_TOOLS``)
    is credited ZERO here, whatever its caller computed. The call is still
    counted. Refused at this layer so an indirect caller (the deletion
    investigator runs ``search_text``) and a site written later inherit it.
    """
    from ..savings_credit import earns_credit

    if not earns_credit(tool_name):
        tokens_saved = 0
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
        root = Path(_tool_counters.storage_root(base_path))
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
        root = Path(_tool_counters.storage_root(base_path))
        if not root.exists():
            return summary
        # ⚠ Which files are indexes is sqlite_store's one content rule
        # (L-144). This loop kept its own one-name denylist, which omitted
        # org_savings.db and every other store that is not an index.
        from .sqlite_store import INDEX_DB_NOT_INDEX, _classify_index_conn

        total = 0
        by_source: dict[str, int] = {}
        for db_path in root.glob("*.db"):
            try:
                # #398 Arc 1: this loop touches EVERY index in ~/.code-index,
                # so a plain `mode=ro` created a -wal beside each one and moved
                # every repo's mtime at once — the v1.108.185 `rebuilding`
                # defect at fleet scale.
                from .generation import connect_readonly

                conn = connect_readonly(db_path, isolation_level="")
                try:
                    conn.row_factory = _sqlite3.Row
                    if _classify_index_conn(conn) == INDEX_DB_NOT_INDEX:
                        continue
                    # Skip dbs that don't have the runtime_calls table yet
                    row = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_calls'"
                    ).fetchone()
                    if row is None:
                        continue
                    rows = conn.execute(
                        "SELECT source, COALESCE(SUM(count), 0) AS n FROM runtime_calls GROUP BY source"
                    ).fetchall()
                finally:
                    conn.close()
                for r in rows:
                    by_source[r["source"]] = by_source.get(r["source"], 0) + int(r["n"])
                    total += int(r["n"])
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


def result_cache_write_epoch() -> int:
    """How many evictions so far. Read before a tool runs; pass to the next function after."""
    return _state.cache_write_epoch()


def result_cache_evict_if_written_since(epoch: int) -> int:
    """Evict everything if an index write evicted the cache since ``epoch`` was read.

    Closes the window where a tool's ``cache_put`` lands AFTER the eviction a
    concurrent index write ran, with an answer computed from the index before
    that write. See ``_TokenState.cache_evict_if_written_since``.
    """
    return _state.cache_evict_if_written_since(epoch)


def result_cache_record_lookup(tool_name: str, hit: bool) -> None:
    """Count one lookup a tool's PRIVATE result cache answered (#864).

    The shared LRU counts its own lookups in ``result_cache_get``; a tool that
    keeps its own cache calls this for every lookup, hit or miss, so its hits
    reach ``total_hits``, ``by_tool`` and ``hit_rate`` like everyone else's.
    """
    _state.cache_lookup_recorded(tool_name, hit)


def result_cache_hit_validated(tool_name: str, stale: bool) -> None:
    """Report that a served cache hit was checked against the current index.

    ``stale=True`` means the subject moved since the entry was filled — the
    entry may still be served (the v1.108.178 cached-positive policy), but it
    must not be counted as a fresh hit.
    """
    _state.cache_hit_validated(tool_name, stale)


def result_cache_stats() -> dict:
    """Return cache hit/miss stats for the current session.

    ``hit_rate`` is RAW (key-presence). Read it beside `hit_rate_revalidated`
    and `hits_unvalidated` — see ``_TokenState.cache_stats``.
    """
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


def begin_call_context() -> Token:
    """Bind a fresh dispatcher call identifier to the current execution context (#456).

    ⚠ Reset with the returned token in a ``finally``. A skipped reset leaves the
    inner entry's identity visible to the outer one after it returns, so the join
    attributes rows to the wrong call while every write still reports success. That
    is the failure the re-entrancy note at ``call_tool`` describes, reached by
    forgetting the reset rather than by writing ``set(None)``.

    A second entry point into the dispatcher would need this wrapper, for the reason
    ``call_tool`` stays the single registered entry: the invariant is held by there
    being one door, not by this helper.
    """
    return _CURRENT_CALL_UID.set(uuid.uuid4().hex)


def end_call_context(token: Token) -> None:
    """Restore the execution context that preceded begin_call_context."""
    _CURRENT_CALL_UID.reset(token)


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


def ranking_db_inflation_rows(
    base_path: Optional[str] = None,
    window_seconds: Optional[float] = None,
    repo: Optional[str] = None,
    limit: int = 10_000,
) -> "Optional[list[tuple]]":
    """Rows for the retrieval-inflation ratio: ``(session_uid, query_hash, tool,
    query, ts, repo_is_stale)``, newest first.

    ⚠⚠ **Returns ``None`` for could-not-ask, never ``[]``.** ``session_uid`` was
    added by ALTER in #456, so a ``telemetry.db`` whose ``ranking_events`` table
    predates it and has not since been opened by a writer does not carry the
    column. An empty list would read as "no inflation", which is the one answer
    this must never invent -- same asymmetry as ``_paths_changed_between`` and
    ``freshness.classify``.

    ⚠ Deliberately a SECOND query rather than a wider ``ranking_db_query``. That
    function's 12-tuple is a documented contract read positionally by
    ``regret``, ``tuning``, ``ledger_trust`` and ``analyze_perf``, and it opens
    the db directly rather than through ``_ensure_perf_db`` -- so selecting a
    column that may not exist would raise ``OperationalError``, hit that
    function's catch-all, and return ``[]`` for **every** ledger consumer. One
    missing column would silently disable all six regret signals.
    """
    path = perf_db_path(base_path)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        try:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(ranking_events)")}
            if not columns or "session_uid" not in columns:
                return None
            sql = (
                "SELECT session_uid, query_hash, tool, query, ts, repo_is_stale "
                "FROM ranking_events"
            )
            args: list = []
            clauses: list[str] = []
            if window_seconds is not None:
                clauses.append("ts >= ?")
                args.append(time.time() - float(window_seconds))
            if repo:
                clauses.append("repo = ?")
                args.append(repo)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY ts DESC LIMIT ?"
            args.append(int(limit))
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    except Exception:
        logger.debug("ranking_db_inflation_rows failed at %s", path, exc_info=True)
        return None


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


def estimate_savings(raw_bytes: int, response_bytes: int, language: Optional[str] = None) -> int:
    """Tokens saved: (raw - response) / the measured bytes-per-token figure.

    The divisor comes from ``savings_tokenizer`` (the configured tokenizer's
    table): ``language``'s row where it was measured, else the table's overall
    figure. It was a flat 4, which undercounted on Claude's tokenizer.
    """
    from ..savings_tokenizer import bytes_per_token

    return max(0, int((raw_bytes - response_bytes) / bytes_per_token(language)))


def cost_avoided(tokens_saved: int, total_tokens_saved: int) -> dict:
    """Formerly returned per-call cost breakdowns for _meta envelopes.

    Now returns an empty dict — cost detail is available via get_session_stats
    only. Removing 4-model cost tables from every per-tool _meta response
    reduces conversation-history token overhead by ~70 tokens/call.
    """
    return {}
