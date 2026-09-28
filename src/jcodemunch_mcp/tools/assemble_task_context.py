"""Task-aware single-call context orchestrator.

Given a task description, auto-classifies into one of six intents
(explore / debug / refactor / extend / audit / review) and runs the
appropriate sequence of underlying tools, then greedy-packs the results
into a single source-attributed capsule under a token budget.

Key properties of the orchestration:
  - Intent classification is explainable (returns ``intent_detected`` +
    ``intent_confidence`` + matched keywords)
  - Each capsule entry tagged with ``source_tool`` so the agent can see
    which tool produced what
  - Runtime evidence woven in when Phase 7 traces exist
  - Suite-aware: when ``include`` mentions cross_repo or the task names
    multiple repos, ``get_group_contracts`` is layered on
  - Token-budget end-to-end greedy packing across all sub-tools
  - User can override intent and `include` to force specific signals
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import symbol_span_bytes
from ._utils import index_status_to_tool_error, resolve_repo
from .get_context_bundle import _count_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "review": (
        "review", "pr ", "pull request", "diff", "commit", "merge",
        "changed", "what changed", "this pr", "regression", "pre-merge",
        "blast radius", "impact of", "what breaks",
    ),
    "debug": (
        "debug", "bug", "broken", "fail", "error", "crash", "exception",
        "traceback", "stack trace", "why isn't", "why doesn't",
        "regression", "not working", "throws", "incorrect",
    ),
    "refactor": (
        "refactor", "rename", "move", "extract", "inline", "consolidate",
        "duplicate", "deduplicate", "clean up", "simplify", "split",
        "merge function", "decompose",
    ),
    "extend": (
        "add ", "new feature", "implement", "extend", "support for",
        "new endpoint", "new handler", "new route", "new method",
        "another impl", "additional",
    ),
    "audit": (
        "audit", "risk", "review risk", "review safety", "dead code",
        "unused", "untested", "coverage gap", "security", "tech debt",
        "code smell", "find similar",
    ),
    "explore": (
        "what does", "how does", "explore", "orient", "overview",
        "show me", "find", "where is", "where are", "tour", "explain",
        "structure", "architecture", "what is this",
    ),
}


def _classify_intent(task: str) -> tuple[str, float, list[str]]:
    """Return (intent, confidence, matched_keywords)."""
    text = task.lower()
    scores: dict[str, list[str]] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        matched = [k for k in keywords if k in text]
        if matched:
            scores[intent] = matched

    if not scores:
        return ("explore", 0.5, [])

    # Score by number of matches + weight longer phrases higher
    def _weight(intent: str) -> float:
        kws = scores[intent]
        return sum(1.0 + len(k) / 30 for k in kws)

    best = max(scores, key=_weight)
    matched = scores[best]
    # Confidence: more matches → higher confidence; cap at 0.95
    confidence = min(0.95, 0.55 + 0.10 * len(matched))
    return (best, round(confidence, 2), matched)


# ---------------------------------------------------------------------------
# Anchor resolution
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
_STOP_IDENTS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "what",
    "which", "where", "when", "why", "how", "does", "should",
    "could", "would", "find", "show", "give", "tell", "function",
    "method", "class", "module", "file", "code", "fix", "add",
    "new", "test", "tests",
})


def _extract_anchors_from_task(task: str, index) -> list[str]:
    """Extract candidate symbol names from the task, restricted to indexed ones."""
    candidates = [m.group(1) for m in _IDENT_RE.finditer(task or "")]
    candidates = [c for c in candidates if c.lower() not in _STOP_IDENTS]
    if not candidates:
        return []
    # Restrict to names actually present in the index
    name_set: set[str] = set()
    for sym in index.symbols:
        n = sym.get("name", "")
        if n:
            name_set.add(n)
    matched: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if c in name_set and c not in seen:
            matched.append(c)
            seen.add(c)
    return matched[:5]


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_INTENT_STRATEGY = {
    "explore":   ("orientation", "hotspots", "tectonic"),
    "debug":     ("anchor", "callers", "callees", "blast", "runtime"),
    "refactor":  ("anchor", "rename_safe", "delete_safe", "implementations", "similar"),
    "extend":    ("anchor", "implementations", "similar", "decorators"),
    "audit":     ("anchor", "risk", "blast", "dead_code_hint", "untested"),
    "review":    ("changed", "blast", "risk", "similar_changed"),
}

# Per-stage token budget allocation (proportions of total)
_STAGE_BUDGET_WEIGHT = {
    "orientation": 0.35,  "hotspots": 0.20,    "tectonic": 0.20,
    "anchor": 0.30,        "callers": 0.15,    "callees": 0.15,
    "blast": 0.15,         "runtime": 0.10,    "rename_safe": 0.05,
    "delete_safe": 0.10,   "implementations": 0.15, "similar": 0.15,
    "decorators": 0.05,    "risk": 0.15,       "dead_code_hint": 0.10,
    "untested": 0.10,      "changed": 0.20,    "similar_changed": 0.15,
}


def assemble_task_context(
    repo: str,
    task: str,
    symbols: Optional[list] = None,
    intent: Optional[str] = None,
    token_budget: int = 8000,
    include: Optional[list] = None,
    cross_repo: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Assemble a task-tailored context capsule in one call.

    Auto-classifies the task into one of six intents and runs the appropriate
    sub-tools, packing results under ``token_budget`` with per-entry source
    attribution. Caller may override ``intent`` and ``include`` to force
    specific signals.

    Args:
        repo: Repository identifier.
        task: Natural-language task description.
        symbols: Optional list of anchor symbol IDs/names; auto-extracted from
            the task when omitted.
        intent: Optional override; auto-detected from the task when omitted.
            Recognised: explore, debug, refactor, extend, audit, review.
        token_budget: End-to-end hard cap on returned tokens (default 8000).
        include: Optional whitelist of stages to run, intersected with the
            intent strategy (e.g. ["anchor", "blast", "runtime"]). When omitted,
            the full intent strategy runs.
        cross_repo: When True, also layer cross-repo signals
            (find_importers cross_repo + get_group_contracts when applicable).
        storage_path: Custom storage path.

    Returns:
        Dict with ``entries`` list (each attributed to a ``source_tool``),
        ``intent_detected``, ``intent_confidence``, ``strategy_applied``,
        ``stages_run`` list, ``anchors``, and ``_meta``.
    """
    start = time.perf_counter()

    if token_budget < 1:
        return {"error": "token_budget must be >= 1"}

    valid_intents = set(_INTENT_STRATEGY)
    if intent is not None and intent not in valid_intents:
        return {
            "error": f"Invalid intent '{intent}'. Must be one of: {sorted(valid_intents)}"
        }

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    # ── Intent classification ──────────────────────────────────────────
    if intent is None:
        intent, conf, matched_kw = _classify_intent(task or "")
    else:
        conf = 1.0
        matched_kw = ["user_override"]

    strategy = list(_INTENT_STRATEGY[intent])
    if include:
        include_set = set(include)
        strategy = [s for s in strategy if s in include_set]
        # Allow user to add stages outside the default strategy
        for extra in include:
            if extra not in strategy and extra in _STAGE_BUDGET_WEIGHT:
                strategy.append(extra)

    # ── Anchor resolution ──────────────────────────────────────────────
    anchors: list[str] = []
    if symbols:
        for s in symbols:
            anchors.append(s)
    elif task:
        anchors = _extract_anchors_from_task(task, index)

    # Resolve anchor names to symbol dicts
    sym_by_name: dict[str, list[dict]] = {}
    for s in index.symbols:
        n = s.get("name", "")
        if n:
            sym_by_name.setdefault(n, []).append(s)
    sym_by_id: dict[str, dict] = {s["id"]: s for s in index.symbols}

    anchor_syms: list[dict] = []
    for a in anchors:
        if a in sym_by_id:
            anchor_syms.append(sym_by_id[a])
        else:
            candidates = sym_by_name.get(a, [])
            if candidates:
                # Prefer non-import, larger
                candidates_sorted = sorted(
                    candidates,
                    key=lambda s: (s.get("kind") == "import",
                                   -int(s.get("byte_length", 0) or 0),
                                   s.get("id", "")),
                )
                anchor_syms.append(candidates_sorted[0])

    # ── Run stages ──────────────────────────────────────────────────────
    entries: list[dict] = []
    stages_run: list[str] = []
    total_tokens = 0
    repo_id = f"{owner}/{name}"

    def _add_entry(stage: str, tool: str, payload: dict, label: str = "",
                   tokens_est: Optional[int] = None) -> bool:
        """Append a capsule entry; return False if the budget is exhausted."""
        nonlocal total_tokens
        # Estimate token cost over the entire payload (not just signature/body)
        # — coarse but reflects the real on-wire size.
        if tokens_est is None:
            try:
                import json as _json  # noqa: PLC0415
                serialized = _json.dumps(payload, default=str, separators=(",", ":"))
            except Exception:  # noqa: BLE001
                serialized = str(payload)
            text_blob = serialized + (f" {label}" if label else "")
            tokens_est = _count_tokens(text_blob) or max(1, len(text_blob) // 4)
        if total_tokens + tokens_est > token_budget and entries:
            return False
        entry = {
            "stage": stage,
            "source_tool": tool,
            "tokens": tokens_est,
            **payload,
        }
        if label:
            entry["label"] = label
        entries.append(entry)
        total_tokens += tokens_est
        return True

    def _budget_remaining() -> int:
        return max(0, token_budget - total_tokens)

    # Helpers to invoke each stage. Each stage handles its own errors and
    # contributes 0..N entries.

    def _stage_orientation() -> None:
        try:
            from .digest import digest  # noqa: PLC0415
            out = digest(repo_id, storage_path=storage_path)
            if isinstance(out, dict) and "error" not in out:
                _add_entry("orientation", "digest", {
                    "summary": out.get("summary", ""),
                    "changed_files": out.get("changed_files", []) or [],
                    "hotspots": out.get("hotspots", []) or [],
                    "dead_code": out.get("dead_code", []) or [],
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: digest skipped: %s", exc, exc_info=True)

    def _stage_hotspots() -> None:
        try:
            from .get_hotspots import get_hotspots  # noqa: PLC0415
            out = get_hotspots(repo_id, top_n=5, storage_path=storage_path)
            if isinstance(out, dict) and "error" not in out:
                _add_entry("hotspots", "get_hotspots", {
                    "hotspots": out.get("hotspots", [])[:5],
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: get_hotspots skipped: %s", exc, exc_info=True)

    def _stage_tectonic() -> None:
        try:
            from .get_tectonic_map import get_tectonic_map  # noqa: PLC0415
            out = get_tectonic_map(repo_id, storage_path=storage_path)
            if isinstance(out, dict) and "error" not in out:
                # Compact: top 5 plates by file_count
                plates = sorted(
                    (out.get("plates", []) or []),
                    key=lambda p: (-int(p.get("file_count", 0) or 0),
                                   str(p.get("anchor", ""))),
                )
                _add_entry("tectonic", "get_tectonic_map", {
                    "plates": [
                        {
                            "anchor": p.get("anchor", ""),
                            "file_count": int(p.get("file_count", 0) or 0),
                            "cohesion": p.get("cohesion", 0),
                            "directory": p.get("majority_directory", ""),
                            "nexus_alert": bool(p.get("nexus_alert", False)),
                        }
                        for p in plates[:5]
                    ],
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: tectonic_map skipped: %s", exc, exc_info=True)

    def _stage_anchor() -> None:
        # For each anchor, attach the signature + summary (no full body) so
        # downstream stages have something to reference cheaply.
        for sym in anchor_syms:
            _add_entry("anchor", "search_symbols", {
                "symbol_id": sym["id"],
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "file": sym.get("file", ""),
                "line": sym.get("line", 0),
                "signature": (sym.get("signature") or "").strip(),
                "summary": sym.get("summary", "") or "",
            })

    def _stage_callers() -> None:
        try:
            from .get_call_hierarchy import get_call_hierarchy  # noqa: PLC0415
            for sym in anchor_syms[:2]:
                out = get_call_hierarchy(
                    repo_id, symbol_id=sym["id"],
                    direction="callers", depth=2, storage_path=storage_path,
                )
                if isinstance(out, dict) and "error" not in out:
                    callers = out.get("callers", []) or []
                    _add_entry("callers", "get_call_hierarchy", {
                        "of_symbol": sym["id"],
                        "callers": [
                            {"id": c.get("id", ""), "file": c.get("file", ""),
                             "line": c.get("line", 0), "depth": c.get("depth", 1)}
                            for c in callers[:8]
                        ],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: callers skipped: %s", exc, exc_info=True)

    def _stage_callees() -> None:
        try:
            from .get_call_hierarchy import get_call_hierarchy  # noqa: PLC0415
            for sym in anchor_syms[:2]:
                out = get_call_hierarchy(
                    repo_id, symbol_id=sym["id"],
                    direction="callees", depth=2, storage_path=storage_path,
                )
                if isinstance(out, dict) and "error" not in out:
                    callees = out.get("callees", []) or []
                    _add_entry("callees", "get_call_hierarchy", {
                        "of_symbol": sym["id"],
                        "callees": [
                            {"id": c.get("id", ""), "file": c.get("file", ""),
                             "line": c.get("line", 0), "depth": c.get("depth", 1)}
                            for c in callees[:8]
                        ],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: callees skipped: %s", exc, exc_info=True)

    def _stage_blast() -> None:
        try:
            from .get_blast_radius import get_blast_radius  # noqa: PLC0415
            for sym in anchor_syms[:2]:
                out = get_blast_radius(
                    repo=repo_id, symbol=sym.get("name", ""),
                    cross_repo=cross_repo, storage_path=storage_path,
                )
                if isinstance(out, dict) and "error" not in out:
                    confirmed = out.get("confirmed", []) or out.get("confirmed_callers", []) or []
                    payload = {
                        "of_symbol": sym.get("name", ""),
                        "confirmed_count": len(confirmed),
                        "impact_by_depth": out.get("impact_by_depth", {}) or {},
                        "top_confirmed": [
                            {"file": c.get("file", ""), "depth": c.get("depth", 1),
                             "has_test_reach": c.get("has_test_reach", False)}
                            for c in confirmed[:8]
                        ],
                    }
                    # The cross-repo channel this stage asked for: dropping it
                    # left `confirmed_count: 0` for a symbol whose only
                    # importers live in another repository (#877).
                    cross = out.get("cross_repo_confirmed") or []
                    if cross:
                        payload["cross_repo_confirmed_count"] = len(cross)
                        payload["top_cross_repo"] = [
                            {"file": c.get("file", ""), "source_repo": c.get("source_repo", "")}
                            for c in cross[:8]
                        ]
                    _add_entry("blast", "get_blast_radius", payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: blast_radius skipped: %s", exc, exc_info=True)

    def _stage_runtime() -> None:
        try:
            from .find_hot_paths import find_hot_paths  # noqa: PLC0415
            # Scope to anchor names when available
            name_filter = anchor_syms[0].get("name", "") if anchor_syms else None
            out = find_hot_paths(
                repo_id, query=name_filter, top_n=5, storage_path=storage_path,
            )
            if isinstance(out, dict) and "error" not in out:
                hot = out.get("results", []) or []
                if hot:
                    _add_entry("runtime", "find_hot_paths", {
                        "hot_paths": [
                            {"symbol_id": h.get("symbol_id", ""),
                             "hits": h.get("runtime_count", 0),
                             "p95_ms": h.get("p95_ms", 0)}
                            for h in hot[:5]
                        ],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: hot_paths skipped: %s", exc, exc_info=True)

    def _stage_rename_safe() -> None:
        # Only meaningful when caller has indicated a rename target — skip
        # without a clear rename hint. Surface as a hint instead.
        if not anchor_syms:
            return
        _add_entry("rename_safe", "check_rename_safe", {
            "hint": (
                f"Call check_rename_safe(repo, '{anchor_syms[0]['id']}', new_name=...) "
                f"before renaming to detect collisions."
            ),
        }, tokens_est=40)

    def _stage_delete_safe() -> None:
        try:
            from .check_delete_safe import check_delete_safe  # noqa: PLC0415
            for sym in anchor_syms[:1]:
                out = check_delete_safe(
                    repo=repo_id, symbol=sym["id"],
                    cross_repo=cross_repo, storage_path=storage_path,
                )
                if isinstance(out, dict) and "error" not in out:
                    _add_entry("delete_safe", "check_delete_safe", {
                        "of_symbol": sym["id"],
                        "verdict": out.get("verdict", ""),
                        "confidence": out.get("confidence", 0.0),
                        "recommended_action": out.get("recommended_action", ""),
                        "top_blockers": out.get("blockers", [])[:3],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: check_delete_safe skipped: %s", exc, exc_info=True)

    def _stage_implementations() -> None:
        try:
            from .find_implementations import find_implementations  # noqa: PLC0415
            for sym in anchor_syms[:1]:
                out = find_implementations(
                    repo=repo_id, symbol=sym["id"],
                    cross_repo=cross_repo, max_results=8,
                    storage_path=storage_path,
                )
                if isinstance(out, dict) and "error" not in out:
                    impls = out.get("implementations", []) or []
                    if impls:
                        _add_entry("implementations", "find_implementations", {
                            "of_symbol": sym["id"],
                            "count": len(impls),
                            "top": [
                                {"id": i.get("symbol_id", ""), "kind": i.get("relationship", ""),
                                 "confidence": i.get("confidence", 0)}
                                for i in impls[:6]
                            ],
                        })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: find_implementations skipped: %s", exc, exc_info=True)

    def _stage_similar() -> None:
        try:
            from .find_similar_symbols import find_similar_symbols  # noqa: PLC0415
            out = find_similar_symbols(
                repo_id, threshold=0.75, max_clusters=5,
                token_budget=min(1500, _budget_remaining()),
                storage_path=storage_path,
            )
            if isinstance(out, dict) and "error" not in out:
                clusters = out.get("clusters", []) or []
                if clusters:
                    _add_entry("similar", "find_similar_symbols", {
                        "clusters": [
                            {"verdict": c.get("verdict", ""),
                             "size": c.get("size", 0),
                             "canonical": c.get("canonical", {}).get("symbol_id", ""),
                             "avg_similarity": c.get("avg_similarity", 0)}
                            for c in clusters[:3]
                        ],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: find_similar_symbols skipped: %s", exc, exc_info=True)

    def _stage_decorators() -> None:
        # Surface the anchor's decorators when present
        decorated: list[dict] = []
        for sym in anchor_syms[:3]:
            decs = sym.get("decorators") or []
            if decs:
                decorated.append({
                    "symbol_id": sym["id"],
                    "decorators": decs[:4] if isinstance(decs, list) else [str(decs)],
                })
        if decorated:
            _add_entry("decorators", "search_symbols", {
                "decorated_anchors": decorated,
            }, tokens_est=80)

    def _stage_risk() -> None:
        try:
            from .get_pr_risk_profile import get_pr_risk_profile  # noqa: PLC0415
            out = get_pr_risk_profile(repo=repo_id, storage_path=storage_path)
            if isinstance(out, dict) and "error" not in out:
                _add_entry("risk", "get_pr_risk_profile", {
                    "composite_risk": out.get("composite_risk", 0),
                    "verdict": out.get("verdict", ""),
                    "signals": out.get("signals", {}),
                    "recommendations": (out.get("recommendations", []) or [])[:3],
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: pr_risk_profile skipped: %s", exc, exc_info=True)

    def _stage_dead_code_hint() -> None:
        try:
            from .find_dead_code import find_dead_code  # noqa: PLC0415
            out = find_dead_code(
                repo_id, granularity="symbol", min_confidence=0.8,
                storage_path=storage_path,
            )
            if isinstance(out, dict) and "error" not in out:
                dead = out.get("dead_symbols", []) or out.get("results", []) or []
                if dead:
                    _add_entry("dead_code_hint", "find_dead_code", {
                        "count": len(dead),
                        "top": [
                            {"id": d.get("symbol_id", ""),
                             "confidence": d.get("confidence", 0)}
                            for d in dead[:5]
                        ],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: find_dead_code skipped: %s", exc, exc_info=True)

    def _stage_untested() -> None:
        try:
            from .get_untested_symbols import get_untested_symbols  # noqa: PLC0415
            out = get_untested_symbols(
                repo_id, min_confidence=0.5, max_results=8, storage_path=storage_path,
            )
            if isinstance(out, dict) and "error" not in out:
                # ⚠⚠ The key is `symbols` (#559). Both spellings tried here
                # were invented; neither has ever been in the response, so the
                # `audit` intent's untested stage emitted nothing at all.
                untested = out.get("symbols", []) or []
                if untested:
                    _add_entry("untested", "get_untested_symbols", {
                        # Repo-wide, not the page length (#559): `untested`
                        # here is capped at max_results=8, so `len()` would
                        # report 8 for a repo with thousands.
                        "count": out.get("untested_count", len(untested)),
                        "shown": len(untested[:5]),
                        "top": [
                            {"id": u.get("symbol_id", "") or u.get("id", ""),
                             "confidence": u.get("confidence", 0)}
                            for u in untested[:5]
                        ],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: untested skipped: %s", exc, exc_info=True)

    def _stage_changed() -> None:
        try:
            from .get_changed_symbols import get_changed_symbols  # noqa: PLC0415
            out = get_changed_symbols(
                repo=repo_id, include_blast_radius=False,
                storage_path=storage_path,
            )
            if isinstance(out, dict) and "error" not in out:
                _add_entry("changed", "get_changed_symbols", {
                    "added": (out.get("added", []) or [])[:5],
                    "modified": (out.get("modified", []) or [])[:5],
                    "removed": (out.get("removed", []) or [])[:5],
                    "renamed": (out.get("renamed", []) or [])[:5],
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: changed_symbols skipped: %s", exc, exc_info=True)

    def _stage_similar_changed() -> None:
        # In review mode, surface consolidation candidates among recently-touched
        # files so reviewers see "look, this PR re-implements X" hints.
        try:
            from .find_similar_symbols import find_similar_symbols  # noqa: PLC0415
            out = find_similar_symbols(
                repo_id, threshold=0.85, max_clusters=3,
                token_budget=min(1000, _budget_remaining()),
                storage_path=storage_path,
            )
            if isinstance(out, dict) and "error" not in out:
                clusters = out.get("clusters", []) or []
                if clusters:
                    _add_entry("similar_changed", "find_similar_symbols", {
                        "clusters": [
                            {"verdict": c.get("verdict", ""),
                             "canonical": c.get("canonical", {}).get("symbol_id", ""),
                             "size": c.get("size", 0)}
                            for c in clusters[:3]
                        ],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: similar_changed skipped: %s", exc, exc_info=True)

    # Dispatch
    stage_handlers = {
        "orientation": _stage_orientation,
        "hotspots": _stage_hotspots,
        "tectonic": _stage_tectonic,
        "anchor": _stage_anchor,
        "callers": _stage_callers,
        "callees": _stage_callees,
        "blast": _stage_blast,
        "runtime": _stage_runtime,
        "rename_safe": _stage_rename_safe,
        "delete_safe": _stage_delete_safe,
        "implementations": _stage_implementations,
        "similar": _stage_similar,
        "decorators": _stage_decorators,
        "risk": _stage_risk,
        "dead_code_hint": _stage_dead_code_hint,
        "untested": _stage_untested,
        "changed": _stage_changed,
        "similar_changed": _stage_similar_changed,
    }

    for stage in strategy:
        handler = stage_handlers.get(stage)
        if handler is None:
            continue
        if total_tokens >= token_budget:
            break
        handler()
        stages_run.append(stage)

    # ── Optional cross-repo layer ──────────────────────────────────────
    if cross_repo:
        try:
            from .get_cross_repo_map import get_cross_repo_map  # noqa: PLC0415
            xr_out = get_cross_repo_map(repo=repo_id, storage_path=storage_path)
            if isinstance(xr_out, dict) and "error" not in xr_out:
                repos = xr_out.get("repos", []) or []
                if repos and total_tokens < token_budget:
                    _add_entry("cross_repo", "get_cross_repo_map", {
                        "depends_on": [r for r in repos if r.get("repo") == repo_id][:1]
                        + [{"repo": e.get("to_repo", ""), "package": e.get("package_name", "")}
                           for e in (xr_out.get("cross_repo_edges", []) or [])[:5]],
                    })
                    stages_run.append("cross_repo")
        except Exception as exc:  # noqa: BLE001
            logger.debug("assemble_task_context: cross_repo skipped: %s", exc, exc_info=True)

    # Token-savings ledger
    raw_bytes = symbol_span_bytes(index.symbols)
    response_bytes = total_tokens * 4
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="assemble_task_context")

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "entries": entries,
        "intent_detected": intent,
        "intent_confidence": conf,
        "intent_keywords_matched": matched_kw,
        "strategy_applied": list(_INTENT_STRATEGY[intent]),
        "stages_run": stages_run,
        "anchors": [s["id"] for s in anchor_syms],
        "anchors_resolved_from_task": [s["id"] for s in anchor_syms] if not symbols else [],
        "total_tokens": total_tokens,
        "budget_tokens": token_budget,
        "entry_count": len(entries),
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    return result
