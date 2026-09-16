"""Plan the next turn — recommend symbols/files based on query."""

import heapq
import time
from typing import Optional

from ._utils import load_repo_index_or_error
from .search_symbols import (
    _tokenize,
    _bm25_score,
    ensure_bm25_cache,
)

# Confidence thresholds
_HIGH_THRESHOLD = 2.0
_MEDIUM_THRESHOLD = 0.5


def plan_turn(
    repo: str,
    query: str,
    max_recommended: int = 5,
    storage_path: Optional[str] = None,
) -> dict:
    """Plan the next turn by analyzing query against the codebase.

    Returns confidence level, recommended symbols/files, and guidance.

    Args:
        repo: Repository identifier.
        query: What the AI is looking for (task description or symbol name).
        max_recommended: Maximum number of symbols to recommend.
        storage_path: Custom storage path.

    Returns:
        Dict with:
            - confidence: "high", "medium", or "low"
            - recommended_symbols: List of {id, name, file, line, score}
            - recommended_files: List of unique file paths
            - gap_analysis: String explaining what's missing
            - max_supplementary_reads: Suggested read limit based on confidence
            - session_overlap: Files from journal that appear in recommended_files
            - _meta: {timing_ms}
    """
    start = time.perf_counter()

    # Validate query length
    if len(query) > 500:
        return {"error": f"Query too long ({len(query)} chars, max 500)"}

    index, error, _status = load_repo_index_or_error(repo, storage_path)
    if error:
        return error
    owner, name = index.owner, index.name

    # Get BM25 cache
    # Single-flight: concurrent cold callers must not each build the
    # full-corpus BM25 state (#370), nor observe a half-published one (#490).
    cache = ensure_bm25_cache(index)
    idf = cache["idf"]
    avgdl = cache["avgdl"]
    centrality = cache["centrality"]
    inverted = cache["inverted"]

    # Tokenize query
    query_terms = _tokenize(query) or [query.lower()]
    # Guard: empty string in query_terms causes "" to match every filename
    query_terms = [t for t in query_terms if t]

    # Score symbols using inverted index
    candidate_indices: set = set()
    for term in query_terms:
        posting = inverted.get(term)
        if posting:
            candidate_indices.update(posting)

    if candidate_indices:
        candidates = [index.symbols[i] for i in sorted(candidate_indices)]
    else:
        candidates = index.symbols

    # Score and rank
    heap: list[tuple[float, str, dict]] = []
    max_score = 0.0
    hits = 0

    for sym in candidates:
        score = _bm25_score(sym, query_terms, idf, avgdl, centrality, raw_query=query)
        if score <= 0:
            continue
        hits += 1
        if score > max_score:
            max_score = score

        entry = {
            "id": sym["id"],
            "name": sym["name"],
            "file": sym["file"],
            "line": sym["line"],
            "score": round(score, 3),
        }

        if len(heap) < max_recommended:
            heapq.heappush(heap, (score, entry["id"], entry))
        elif score > heap[0][0]:
            heapq.heapreplace(heap, (score, entry["id"], entry))

    # Sort by score descending
    recommended_symbols = [entry for score, _, entry in sorted(heap, key=lambda x: x[0], reverse=True)]

    # Determine confidence (config-driven thresholds)
    try:
        from .. import config as _cfg
        high_t = _cfg.get("plan_turn_high_threshold", _HIGH_THRESHOLD)
        med_t = _cfg.get("plan_turn_medium_threshold", _MEDIUM_THRESHOLD)
    except Exception:
        high_t, med_t = _HIGH_THRESHOLD, _MEDIUM_THRESHOLD
    if max_score >= high_t and hits >= 3:
        confidence = "high"
    elif max_score >= med_t and hits >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    # Deduplicate files
    recommended_files = list({sym["file"] for sym in recommended_symbols})

    # Build gap analysis
    if confidence == "low":
        gap_analysis = (
            f"No symbols matching '{query}' found in {len(index.symbols)} indexed symbols. "
            f"This feature likely needs to be created from scratch."
        )
    elif confidence == "medium":
        gap_analysis = (
            f"Partial matches found. Related code exists but may not directly implement '{query}'."
        )
    else:
        gap_analysis = (
            f"Strong matches found. Existing implementation likely covers '{query}'."
        )

    # Max supplementary reads based on confidence
    max_supplementary_reads = {"high": 2, "medium": 5, "low": 10}[confidence]

    # Check session overlap + load journal context for sub-features
    session_overlap: list[str] = []
    journal_ctx: Optional[dict] = None
    journal = None  # bound below; stays None if the journal is unavailable
    accessed_files: set = set()
    try:
        from .session_journal import get_journal
        journal = get_journal()
        journal_ctx = journal.get_context()
        accessed_files = {f["file"] for f in journal_ctx.get("files_accessed", [])}
        session_overlap = [f for f in recommended_files if f in accessed_files]
    except Exception:
        pass

    # --- Sub-feature: Prior negative evidence check (#205, repo-scoped since #711) ---
    # Two conditions, and BOTH are required:
    #   1. the producer published an absence for THIS repository and this query
    #      (`citable_absence` is the authority; a bare zero count is not one), and
    #   2. this plan found nothing either.
    # Condition 2 is not belt-and-braces. A filter, a token budget or a reindex
    # between the two calls all leave a stale miss in the log, and none of them
    # makes the symbols on this page disappear -- asserting absence over them
    # produced a response that returned an implementation and denied it existed.
    prior_evidence = None
    try:
        if journal is not None and not recommended_symbols:
            # The log holds the repo string the SEARCH was called with, which
            # need not be the one this call used: `load_repo_index_or_error`
            # resolves a path or a bare name to `owner/name`. Both spellings are
            # offered. ⚠ STATED GAP: a third spelling on the recording side
            # (`repo="."`) still will not match, and the miss fails CLOSED --
            # the stop signal does not fire, no false claim is made.
            absence = journal.citable_absence(
                repo, query, aliases=(f"{owner}/{name}",)
            )
            if absence is not None:
                times = absence.get("times_recorded", 1)
                prior_evidence = {
                    "previously_searched": True,
                    "times_searched": times,
                    "recommendation": (
                        f"A search for this exact query published an absence finding "
                        f"for this repository {times} time(s), and this plan found no "
                        f"match either. Re-running the same terms here will not change "
                        f"the answer. This says nothing about any other repository."
                    ),
                }
                confidence = "none"
                max_supplementary_reads = 0
    except Exception:
        pass

    # --- Sub-feature: Insertion point recommendation (when confidence is low/none) ---
    insertion_candidates = None
    if confidence in ("low", "none"):
        try:
            from .pagerank import compute_pagerank
            if "pagerank" not in cache:
                pr_scores, _ = compute_pagerank(
                    index.imports or {}, index.source_files, index.alias_map
                )
                cache["pagerank"] = pr_scores
            pr_scores = cache["pagerank"]

            # Find files whose names partially match query terms
            name_matches = []
            for f in index.source_files:
                fname = f.rsplit("/", 1)[-1].lower() if "/" in f else f.lower()
                if any(t in fname for t in query_terms):
                    name_matches.append((f, pr_scores.get(f, 0.0)))

            # Fall back to top PageRank files if no name match
            candidates_for_insert = name_matches if name_matches else [
                (f, s) for f, s in sorted(pr_scores.items(), key=lambda x: x[1], reverse=True)[:20]
            ]
            candidates_for_insert.sort(key=lambda x: x[1], reverse=True)

            insertion_candidates = [
                {"file": f, "centrality_score": round(s, 4)}
                for f, s in candidates_for_insert[:3]
            ]

            if insertion_candidates:
                locations = ", ".join(
                    f"{c['file']} (centrality {c['centrality_score']})"
                    for c in insertion_candidates
                )
                gap_analysis += f" Suggested location(s): {locations}."
        except Exception:
            pass

    # --- Sub-feature: Smart budget advisor (when turn budget >60% used) ---
    budget_advisor = None
    try:
        from .turn_budget import get_turn_budget
        tb = get_turn_budget()
        if tb.is_enabled():
            pct = tb.percent_used()
            if pct > 0.6:
                if "pagerank" not in cache:
                    from .pagerank import compute_pagerank
                    pr_scores, _ = compute_pagerank(
                        index.imports or {}, index.source_files, index.alias_map
                    )
                    cache["pagerank"] = pr_scores
                pr_scores = cache.get("pagerank", {})
                already_read = accessed_files if accessed_files else set()
                unexplored = sorted(
                    [(f, pr_scores.get(f, 0.0)) for f in index.source_files if f not in already_read],
                    key=lambda x: x[1], reverse=True,
                )[:5]
                budget_advisor = {
                    "turn_budget_percent_used": round(pct * 100, 1),
                    "highest_value_unexplored": [
                        {"file": f, "centrality_score": round(s, 4)} for f, s in unexplored
                    ],
                    "recommendation": (
                        f"Budget {round(pct * 100)}% used. "
                        f"Top unexplored files by architectural importance listed above. "
                        f"Focus remaining reads on these."
                    ),
                }
    except Exception:
        pass

    # --- Sub-feature: Consumption estimate + calibration (v1.108.148) ---
    # Price the recommended route in tokens and reconcile the previous
    # plan's estimate against what the session actually served since.
    consumption_estimate = None
    try:
        from ..storage.token_tracker import (
            _DEFAULT_TOKENS_PER_CALL,
            avg_response_tokens_per_call,
            record_turn_estimate,
        )
        expected_calls = len(recommended_symbols) + max_supplementary_reads
        session_avg = avg_response_tokens_per_call()
        per_call = session_avg if session_avg > 0 else _DEFAULT_TOKENS_PER_CALL
        estimated = expected_calls * per_call
        calibration = record_turn_estimate(estimated)
        if expected_calls > 0:
            consumption_estimate = {
                "estimated_tokens": estimated,
                "expected_calls": expected_calls,
                "basis": "session_avg" if session_avg > 0 else "default",
            }
            if calibration is not None:
                ratio = calibration["actual_vs_estimated"]
                consumption_estimate["actual_vs_estimated"] = ratio
                consumption_estimate["calibrated_tokens"] = int(estimated * ratio)
    except Exception:
        pass

    elapsed = (time.perf_counter() - start) * 1000

    result: dict = {
        "confidence": confidence,
        "recommended_symbols": recommended_symbols,
        "recommended_files": recommended_files,
        "gap_analysis": gap_analysis,
        "max_supplementary_reads": max_supplementary_reads,
        "session_overlap": session_overlap,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "total_symbols": len(index.symbols),
            "candidates_scored": hits,
        },
    }
    if prior_evidence is not None:
        result["prior_evidence"] = prior_evidence
    if insertion_candidates:
        result["insertion_candidates"] = insertion_candidates
    if budget_advisor is not None:
        result["budget_advisor"] = budget_advisor
    if consumption_estimate is not None:
        result["consumption_estimate"] = consumption_estimate
    if confidence in ("low", "none"):
        result["action"] = "STOP_AND_REPORT_GAP"
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.confidence import extract_ledger_features as _ledger_feats
    from ..storage.token_tracker import record_ranking_event as _record_ranking_event
    _attach_confidence(result, recommended_symbols)
    _feat = _ledger_feats(recommended_symbols)
    _record_ranking_event(
        # v1.108.188: this tool takes storage_path directly, so it can name the
        # store without going through a store object.
        base_path=storage_path,
        tool="plan_turn",
        repo=f"{owner}/{name}",
        query=query,
        returned_ids=[r.get("id", r.get("symbol_id", "")) for r in recommended_symbols],
        confidence=result["_meta"].get("confidence"),
        semantic_used=False,
        **_feat,
    )
    return result
