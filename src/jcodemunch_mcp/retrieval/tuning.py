"""Online weight tuning (v1.79.0).

Consumes the v1.78.0 ``ranking_events`` ledger and writes per-repo
retrieval-weight overrides to ``~/.code-index/tuning.jsonc``. The
tuner is *additive* — it suggests deltas to the defaults when there's
statistical evidence that a signal helps; default behavior is preserved
when there isn't enough evidence.

One signal is learned:
  * ``semantic_weight``  — bumped if events with ``semantic_used=1`` had
                           higher mean confidence than events with
                           ``semantic_used=0``.

It is bounded; the tuner won't overshoot and won't apply learning
without enough events (default 50). Learning reads a recency window of
the ledger (default 90 days) rather than the lifetime history, so old
events can't anchor weights to a query distribution that no longer
exists (``max_age_days=0`` restores the lifetime read).

v1.108.186: rows whose ``semantic_used`` label is not evidence are excluded
from both groups and disclosed as ``events_semantic_label_unknown`` — see
``retrieval.ledger_trust``. Unknown is a third answer here, not a vote for
``semantic_used=0``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from ..storage import token_tracker as _tt
from .ledger_trust import semantic_label_is_trustworthy as _semantic_label_is_trustworthy

logger = logging.getLogger(__name__)

_TUNING_FILE = "tuning.jsonc"

# Defaults (current behavior baseline)
_DEFAULT_SEMANTIC_WEIGHT = 0.5

# Bounds — never let learning leave these intervals
_SEMANTIC_BOUNDS = (0.1, 0.8)

# Step size — single tuning pass moves a weight by at most this much
_LEARN_STEP = 0.05

# Minimum sample to trust a learned delta
_DEFAULT_MIN_EVENTS = 50

# Recency window for ledger reads, in days. A repo's query distribution
# drifts as the codebase and the agent's habits change; lifetime events
# act as stale anchors that drag learned weights toward old behavior.
# 0 disables the window (lifetime ledger, pre-v1.108.53 behavior).
_DEFAULT_MAX_AGE_DAYS = 90

# Minimum confidence delta (between groups) before we treat a signal
# as actually helpful. Below this we leave the default alone.
_CONFIDENCE_DELTA_THRESHOLD = 0.05


# Cache of (repo → overrides) loaded from disk; invalidated on write.
_cache: dict[str, dict] = {}
_cache_lock = Lock()
_cache_loaded_from: Optional[str] = None


def _tuning_path(base_path: Optional[str] = None) -> Path:
    # L-285: the index store's root rule (`CODE_INDEX_PATH`, else
    # `~/.code-index`), never a second copy of it. Imported here so this
    # module does not load the store at import.
    from ..storage.sqlite_store import _default_base_path

    root = Path(base_path) if base_path else Path(_default_base_path())
    root.mkdir(parents=True, exist_ok=True)
    return root / _TUNING_FILE


def _strip_jsonc(text: str) -> str:
    # Tolerate // line comments and /* */ block comments. The hatchling
    # build picks up the file as plain JSON so we keep the parser simple.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    return text


def _load(path: Path) -> dict:
    if not path.exists():
        return {"repos": {}}
    try:
        raw = _strip_jsonc(path.read_text(encoding="utf-8", errors="replace"))
        data = json.loads(raw) if raw.strip() else {"repos": {}}
        if "repos" not in data or not isinstance(data["repos"], dict):
            data["repos"] = {}
        return data
    except Exception:
        logger.debug("Failed to parse tuning file at %s", path, exc_info=True)
        return {"repos": {}}


def _ensure_cache(base_path: Optional[str]) -> dict:
    global _cache, _cache_loaded_from
    with _cache_lock:
        path = _tuning_path(base_path)
        path_key = str(path)
        if _cache_loaded_from != path_key:
            _cache = _load(path).get("repos", {})
            _cache_loaded_from = path_key
        return _cache


def get_overrides(repo: str, base_path: Optional[str] = None) -> dict:
    """Return ``{semantic_weight?, learned_from_events, captured_at}`` for
    ``repo`` (empty dict when unlearned)."""
    cache = _ensure_cache(base_path)
    return dict(cache.get(repo, {}))


def get_semantic_weight(
    repo: str,
    explicit: Optional[float] = None,
    base_path: Optional[str] = None,
) -> float:
    """Resolve the active semantic_weight for ``repo``.

    If the caller passed an explicit value (i.e. ``semantic_weight=`` was
    supplied to the tool), it always wins. Otherwise we consult the
    learned overrides, then fall back to the default.
    """
    if explicit is not None:
        return float(explicit)
    overrides = get_overrides(repo, base_path)
    val = overrides.get("semantic_weight")
    if val is None:
        return _DEFAULT_SEMANTIC_WEIGHT
    return _clamp(val, _SEMANTIC_BOUNDS)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return max(lo, min(hi, float(value)))


def _mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


class WeightTuner:
    """Per-repo regression on the ranking ledger.

    Each call to ``learn`` reads the ledger, computes confidence means
    grouped by signal, and proposes a delta from the current overrides
    (or defaults). Apply the result with ``apply``.
    """

    def __init__(self, base_path: Optional[str] = None):
        self._base_path = base_path

    def _load_events(self, repo: str, max_age_days: int = _DEFAULT_MAX_AGE_DAYS) -> list[tuple]:
        # Pull the most recent N rows for this repo, bounded by the
        # recency window so stale events can't anchor the proposal.
        window = float(max_age_days) * 86_400 if max_age_days > 0 else None
        return _tt.ranking_db_query(
            base_path=self._base_path,
            repo=repo,
            window_seconds=window,
            limit=10_000,
        )

    def _propose(
        self,
        events: list[tuple],
        existing: dict,
    ) -> tuple[Optional[float], dict]:
        """Return ``(new_semantic_weight, signals)``.

        ``signals`` carries the means/correlations used for the proposal
        so callers can ``--explain`` the decision.
        """
        if not events:
            return None, {}

        confidences_with_sem: list[float] = []
        confidences_without_sem: list[float] = []
        # v1.108.186. A THIRD bucket, because the answer to "was semantic used on
        # this row" can be "this row cannot say". Pre-fix `get_ranked_context_fusion`
        # rows recorded `semantic_used=1` from a hardcoded argument while building no
        # similarity channel, and the ledger keeps them for the whole recency window.
        # They are not evidence that semantic was ON, and folding them into the OFF
        # group would be the same error mirrored — so they are counted, disclosed,
        # and excluded from both means.
        unknown_semantic_label = 0

        # Schema (column index): 0 ts, 1 repo, 2 tool, 3 query_hash,
        # 4 query, 5 returned_ids, 6 top1, 7 top2, 8 confidence,
        # 9 semantic_used, 10 identity_hit, 11 repo_is_stale.
        for row in events:
            conf = row[8]
            if conf is None:
                continue
            if not _semantic_label_is_trustworthy(row):
                unknown_semantic_label += 1
                continue
            sem_used = bool(row[9])
            (confidences_with_sem if sem_used else confidences_without_sem).append(float(conf))

        mean_sem_on = _mean(confidences_with_sem)
        mean_sem_off = _mean(confidences_without_sem)

        signals = {
            "events_with_confidence": len(confidences_with_sem) + len(confidences_without_sem),
            "mean_confidence_semantic_on": _round(mean_sem_on),
            "mean_confidence_semantic_off": _round(mean_sem_off),
        }
        if unknown_semantic_label:
            # Disclosed on every proposal that had to drop rows, including the ones
            # that then decline to move a weight — a silent exclusion reads as "the
            # ledger simply had nothing to say".
            signals["events_semantic_label_unknown"] = unknown_semantic_label
            signals["semantic_label_unknown_note"] = (
                "Rows recorded before v1.108.186 by get_ranked_context's fusion exit "
                "carry semantic_used=1 from a hardcoded argument, not from a "
                "similarity channel. They are excluded from both groups rather than "
                "counted as semantic-off, and age out with the recency window."
            )

        new_sem = None
        if mean_sem_on is not None and mean_sem_off is not None:
            current = float(
                existing.get("semantic_weight", _DEFAULT_SEMANTIC_WEIGHT)
            )
            delta_conf = mean_sem_on - mean_sem_off
            if abs(delta_conf) >= _CONFIDENCE_DELTA_THRESHOLD:
                step = _LEARN_STEP if delta_conf > 0 else -_LEARN_STEP
                new_sem = _clamp(current + step, _SEMANTIC_BOUNDS)
                signals["semantic_step"] = step

        return new_sem, signals

    def learn(
        self,
        repo: str,
        *,
        dry_run: bool = False,
        min_events: int = _DEFAULT_MIN_EVENTS,
        max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    ) -> dict:
        """Learn (and optionally apply) updated weights for ``repo``."""
        events = self._load_events(repo, max_age_days=max_age_days)
        before = get_overrides(repo, self._base_path)
        if len(events) < min_events:
            return {
                "repo": repo,
                "applied": False,
                "reason": f"insufficient_events ({len(events)} < {min_events})",
                "events": len(events),
                "max_age_days": max_age_days,
                "before": before,
                "after": before,
            }
        new_sem, signals = self._propose(events, before)
        after: dict = dict(before)
        changed = False
        if new_sem is not None and new_sem != before.get("semantic_weight"):
            after["semantic_weight"] = round(new_sem, 4)
            changed = True
        if not changed:
            return {
                "repo": repo,
                "applied": False,
                "reason": "no_significant_signal",
                "events": len(events),
                "max_age_days": max_age_days,
                "before": before,
                "after": before,
                "signals": signals,
            }
        after["learned_from_events"] = len(events)
        after["captured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not dry_run:
            self._persist(repo, after)
        return {
            "repo": repo,
            "applied": (not dry_run),
            "events": len(events),
            "max_age_days": max_age_days,
            "before": before,
            "after": after,
            "signals": signals,
        }

    def _persist(self, repo: str, overrides: dict) -> None:
        path = _tuning_path(self._base_path)
        data = _load(path)
        data.setdefault("repos", {})[repo] = overrides
        header = (
            "// Auto-generated by jcodemunch-mcp v1.79.0+\n"
            "// Per-repo retrieval-weight overrides learned from the\n"
            "// ranking_events telemetry ledger. Edit by hand at your own\n"
            "// risk — `tune_weights` will overwrite per-repo entries on\n"
            "// the next run.\n"
        )
        path.write_text(header + json.dumps(data, indent=2) + "\n", encoding="utf-8")
        # Invalidate cache so the next get_overrides reads fresh data.
        with _cache_lock:
            global _cache_loaded_from
            _cache_loaded_from = None


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 3)


def list_repos(base_path: Optional[str] = None) -> list[str]:
    """Return the set of repos that appear in the ranking ledger."""
    rows = _tt.ranking_db_query(base_path=base_path, limit=10_000)
    seen: set[str] = set()
    for row in rows:
        repo = row[1]
        if repo:
            seen.add(repo)
    return sorted(seen)
