"""Standalone token-budgeted context assembler: best-K-tokens for a query."""

import time
from fnmatch import fnmatch
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided as _cost_avoided
from ._utils import resolve_repo
from .get_context_bundle import _count_tokens
from .search_symbols import (
    _tokenize,
    _compute_bm25,
    _bm25_score,
    _NEGATIVE_EVIDENCE_THRESHOLD,
    BYTES_PER_TOKEN,
)

# Weight for PageRank when strategy="combined"
_PR_WEIGHT = 100.0

# Diversity packing parameters
_DIVERSITY_DECAY = 0.5       # penalty growth per same-file symbol
_FILE_GROUP_CAP = 3          # max symbols from a single file


def _pack_budget(
    scored_items: list[tuple[float, dict]],
    token_budget: int,
    get_tokens: callable,
    *,
    diversity: bool = True,
) -> tuple[list[tuple[float, dict, str, int]], int]:
    """Diversity-aware greedy budget packing.

    Args:
        scored_items: List of (score, sym_dict) sorted by descending score.
        token_budget: Hard cap on total tokens.
        get_tokens: Callable(sym) -> (source_str, token_count).
        diversity: Enable file-diversity penalty (default True).

    Returns:
        (packed, total_tokens) where packed is list of
        (adjusted_score, sym, source, item_tokens).
    """
    packed: list[tuple[float, dict, str, int]] = []
    total_tokens = 0
    file_counts: dict[str, int] = {}

    for score, sym in scored_items:
        sym_file = sym.get("file", "")

        # Diversity: enforce per-file cap
        if diversity and file_counts.get(sym_file, 0) >= _FILE_GROUP_CAP:
            continue

        source, item_tokens = get_tokens(sym)
        if item_tokens == 0:
            continue
        if total_tokens + item_tokens > token_budget:
            continue

        # Diversity: decay score for repeated files
        if diversity:
            n = file_counts.get(sym_file, 0)
            adjusted = score * (_DIVERSITY_DECAY ** n)
        else:
            adjusted = score

        packed.append((adjusted, sym, source, item_tokens))
        total_tokens += item_tokens
        file_counts[sym_file] = file_counts.get(sym_file, 0) + 1

    return packed, total_tokens


def get_ranked_context(
    repo: str,
    query: str,
    token_budget: int = 4000,
    strategy: str = "combined",
    include_kinds: Optional[list] = None,
    scope: Optional[str] = None,
    fusion: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Assemble the best-fit context for a query within a token budget.

    Ranks all symbols by relevance (BM25) and/or centrality (PageRank),
    loads source for the top candidates, and packs greedily until
    ``token_budget`` is exhausted.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Natural language or identifier describing the task.
        token_budget: Hard cap on returned tokens (default 4000).
        strategy: Ranking strategy.
            'combined' (default) = BM25 + PageRank weighted sum.
            'bm25' = pure BM25 text relevance.
            'centrality' = PageRank only (filtered to symbols with BM25 > 0).
        include_kinds: Optional list of symbol kinds to restrict results to
            (e.g. ['class', 'function']).
        scope: Optional subdirectory glob to limit search (e.g. 'src/core/*').
        fusion: Enable multi-signal fusion (Weighted Reciprocal Rank) for
            ranking. When True, ``strategy`` maps to channel weight presets.
        storage_path: Custom storage path.

    Returns:
        Dict with ``context_items`` list and summary fields.
    """
    _MAX_QUERY_LEN = 500
    if len(query) > _MAX_QUERY_LEN:
        return {"error": f"Query too long ({len(query)} chars, max {_MAX_QUERY_LEN})"}

    if strategy not in ("combined", "bm25", "centrality"):
        return {"error": f"Invalid strategy '{strategy}'. Must be 'combined', 'bm25', or 'centrality'."}

    if token_budget < 1:
        return {"error": "token_budget must be >= 1"}

    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # BM25 corpus — cached on CodeIndex
    query_terms = _tokenize(query) or [query.lower()]
    # Guard: empty string in query_terms causes "" to match every filename
    query_terms = [t for t in query_terms if t]
    cache = index._bm25_cache
    if "idf" not in cache:
        from .search_symbols import _compute_centrality  # noqa: PLC0415
        cache["idf"], cache["avgdl"], cache["inverted"] = _compute_bm25(index.symbols)
        cache["centrality"] = _compute_centrality(index.symbols, index.imports, index.alias_map, getattr(index, "psr4_map", None))
    idf = cache["idf"]
    avgdl = cache["avgdl"]
    inverted = cache["inverted"]

    # PageRank — computed when strategy requires it
    pagerank: dict = {}
    if strategy in ("centrality", "combined"):
        if "pagerank" not in cache:
            from .pagerank import compute_pagerank  # noqa: PLC0415
            pr_scores, _ = compute_pagerank(
                index.imports or {}, index.source_files, index.alias_map, psr4_map=getattr(index, "psr4_map", None)
            )
            cache["pagerank"] = pr_scores
        pagerank = cache["pagerank"]

    # ── Fusion path ─────────────────────────────────────────────────────
    if fusion:
        return _get_ranked_context_fusion(
            index=index,
            store=store,
            owner=owner,
            name=name,
            query=query,
            query_terms=query_terms,
            idf=idf,
            avgdl=avgdl,
            pagerank=pagerank,
            token_budget=token_budget,
            include_kinds=include_kinds,
            scope=scope,
            start=start,
        )

    # Normalize PageRank to [0,1] for score combination
    max_pr = max(pagerank.values()) if pagerank else 1.0

    # Candidate narrowing via inverted index
    candidate_indices: set[int] = set()
    for term in query_terms:
        posting = inverted.get(term)
        if posting:
            candidate_indices.update(posting)
    candidates = (
        [index.symbols[i] for i in sorted(candidate_indices)]
        if candidate_indices
        else index.symbols
    )

    # Score and filter candidates
    scored: list[tuple[float, float, float, dict]] = []  # (combined, bm25_norm, pr_norm, sym)
    max_bm25 = 0.0
    raw_scores: list[tuple[float, float, dict]] = []  # (bm25, pr_raw, sym)

    for sym in candidates:
        if include_kinds and sym.get("kind") not in include_kinds:
            continue
        if scope and not fnmatch(sym.get("file", ""), scope):
            continue

        bm25 = _bm25_score(sym, query_terms, idf, avgdl, raw_query=query)
        if bm25 <= 0 and strategy != "centrality":
            continue
        pr_raw = pagerank.get(sym.get("file", ""), 0.0)
        if bm25 > max_bm25:
            max_bm25 = bm25
        raw_scores.append((bm25, pr_raw, sym))

    if not raw_scores:
        elapsed = (time.perf_counter() - start) * 1000
        # Negative evidence: signal that nothing matched
        related_existing = [
            f for f in index.source_files
            if any(t in f.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                   for t in query_terms)
        ][:5]
        ne = {
            "verdict": "no_implementation_found",
            "scanned_symbols": len(candidates),
            "scanned_files": len(index.source_files),
            "best_match_score": 0.0,
        }
        if related_existing:
            ne["related_existing"] = related_existing
        result = {
            "context_items": [],
            "total_tokens": 0,
            "budget_tokens": token_budget,
            "items_included": 0,
            "items_considered": 0,
            "negative_evidence": ne,
            "\u26a0 warning": (
                f"No implementation found for '{query[:80]}'. "
                f"Do not claim this feature exists."
            ),
            "_meta": {
                "timing_ms": round(elapsed, 1),
                "tokens_saved": 0,
                "total_tokens_saved": 0,
            },
        }
        return result

    # Normalize and compute combined score
    norm_bm25_denom = max_bm25 if max_bm25 > 0 else 1.0
    norm_pr_denom = max_pr if max_pr > 0 else 1.0

    for bm25, pr_raw, sym in raw_scores:
        bm25_norm = bm25 / norm_bm25_denom
        pr_norm = pr_raw / norm_pr_denom
        if strategy == "bm25":
            combined = bm25_norm
        elif strategy == "centrality":
            combined = pr_norm
        else:  # combined
            combined = 0.5 * bm25_norm + 0.5 * pr_norm
        scored.append((combined, bm25_norm, pr_norm, sym))

    scored.sort(key=lambda x: x[0], reverse=True)

    items_considered = len(scored)

    # Build score lookup for BM25/PR per symbol
    _score_lookup: dict[str, tuple[float, float]] = {}
    for combined_score, bm25_norm, pr_norm, sym in scored:
        _score_lookup[sym["id"]] = (bm25_norm, pr_norm)

    def _get_tokens(sym):
        source = store.get_symbol_content(owner, name, sym["id"], _index=index) or ""
        tokens = _count_tokens(source) if source else max(1, sym.get("byte_length", 0) // BYTES_PER_TOKEN)
        return source, tokens

    packed, total_tokens = _pack_budget(
        [(combined_score, sym) for combined_score, _, _, sym in scored],
        token_budget,
        _get_tokens,
    )

    context_items: list[dict] = []
    for adj_score, sym, source, item_tokens in packed:
        bm25_norm, pr_norm = _score_lookup.get(sym["id"], (0.0, 0.0))
        context_items.append({
            "symbol_id": sym["id"],
            "relevance_score": round(bm25_norm, 4),
            "centrality_score": round(pr_norm, 4),
            "combined_score": round(adj_score, 4),
            "tokens": item_tokens,
            "source": source,
        })

    # Negative evidence for low-confidence or empty results
    _ne_threshold = _NEGATIVE_EVIDENCE_THRESHOLD
    try:
        from .. import config as _cfg
        _ne_threshold = _cfg.get("negative_evidence_threshold", _NEGATIVE_EVIDENCE_THRESHOLD)
    except Exception:
        pass

    negative_evidence = None
    if not context_items or max_bm25 < _ne_threshold:
        verdict = "no_implementation_found" if not context_items else "low_confidence_matches"
        related_existing = [
            f for f in index.source_files
            if any(t in f.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                   for t in query_terms)
        ][:5]
        negative_evidence = {
            "verdict": verdict,
            "scanned_symbols": items_considered,
            "scanned_files": len(set(s.get("file", "") for _, _, _, s in scored)),
            "best_match_score": round(max_bm25, 3),
        }
        if related_existing:
            negative_evidence["related_existing"] = related_existing

    # Token savings estimate
    raw_bytes = sum(
        index.file_sizes.get(sym.get("file", ""), 0)
        for _, _, _, sym in scored[:items_considered]
    )
    response_bytes = total_tokens * BYTES_PER_TOKEN
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_ranked_context")

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "context_items": context_items,
        "total_tokens": total_tokens,
        "budget_tokens": token_budget,
        "items_included": len(context_items),
        "items_considered": items_considered,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **_cost_avoided(tokens_saved, total_saved),
        },
    }
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(context_items)
    result["_meta"]["freshness"] = _probe.summary(context_items)
    _attach_confidence(result, context_items, is_stale=_probe.repo_is_stale)
    if negative_evidence is not None:
        result["negative_evidence"] = negative_evidence
        if negative_evidence["verdict"] == "no_implementation_found":
            result["\u26a0 warning"] = (
                f"No implementation found for '{query[:80]}'. "
                f"Do not claim this feature exists."
            )
        else:
            result["\u26a0 warning"] = (
                f"Low-confidence matches for '{query[:80]}' "
                f"(best score: {negative_evidence['best_match_score']}). "
                f"Verify before claiming this feature exists."
            )
    return result


def _get_ranked_context_fusion(
    *,
    index,
    store,
    owner: str,
    name: str,
    query: str,
    query_terms: list[str],
    idf: dict,
    avgdl: float,
    pagerank: dict,
    token_budget: int,
    include_kinds,
    scope,
    start: float,
) -> dict:
    """Fusion-based ranked context: WRR across channels, greedy budget packing."""
    from ..retrieval.signal_fusion import (
        fuse,
        build_lexical_channel,
        build_structural_channel,
        build_identity_channel,
        load_fusion_weights,
    )
    from .search_symbols import _compute_centrality

    # Filter candidates
    candidates = index.symbols
    if include_kinds or scope:
        candidates = [
            sym for sym in candidates
            if (not include_kinds or sym.get("kind") in include_kinds)
            and (not scope or fnmatch(sym.get("file", ""), scope))
        ]

    if not candidates:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "context_items": [],
            "total_tokens": 0,
            "budget_tokens": token_budget,
            "items_included": 0,
            "items_considered": 0,
            "_meta": {"timing_ms": round(elapsed, 1), "tokens_saved": 0, "total_tokens_saved": 0},
        }

    # Centrality for BM25 tiebreaker
    cache = index._bm25_cache
    if "centrality" not in cache:
        cache["centrality"] = _compute_centrality(
            index.symbols, index.imports, index.alias_map,
            getattr(index, "psr4_map", None),
        )
    centrality = cache["centrality"]

    weights, smoothing = load_fusion_weights()

    channels = []
    lex_ch = build_lexical_channel(candidates, query_terms, idf, avgdl, centrality)
    channels.append(lex_ch)

    id_ch = build_identity_channel(candidates, query)
    channels.append(id_ch)

    if pagerank:
        candidate_ids = set(lex_ch.ranked_ids) | set(id_ch.ranked_ids)
        struct_ch = build_structural_channel(candidates, pagerank, candidate_ids)
        channels.append(struct_ch)

    fused = fuse(channels, smoothing=smoothing, weights=weights)

    # Diversity-aware budget packing
    sym_by_id = {sym["id"]: sym for sym in candidates}

    # Build fused score lookup for channel contributions
    _fused_lookup: dict[str, object] = {fr.symbol_id: fr for fr in fused}

    # Filter to valid symbols and pass to packer
    fused_scored = []
    for fr in fused:
        sym = sym_by_id.get(fr.symbol_id)
        if sym:
            fused_scored.append((fr.score, sym))

    def _get_tokens_fusion(sym):
        source = store.get_symbol_content(owner, name, sym["id"], _index=index) or ""
        tokens = _count_tokens(source) if source else max(1, sym.get("byte_length", 0) // BYTES_PER_TOKEN)
        return source, tokens

    packed, total_tokens = _pack_budget(
        fused_scored, token_budget, _get_tokens_fusion,
    )

    context_items = []
    for adj_score, sym, source, item_tokens in packed:
        fr = _fused_lookup.get(sym["id"])
        context_items.append({
            "symbol_id": sym["id"],
            "fusion_score": round(adj_score, 6),
            "channels": {k: round(v, 6) for k, v in fr.channel_contributions.items()} if fr else {},
            "tokens": item_tokens,
            "source": source,
        })

    raw_bytes = sum(
        index.file_sizes.get(sym.get("file", ""), 0)
        for _, sym, _, _ in packed
    )
    response_bytes = total_tokens * BYTES_PER_TOKEN
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_ranked_context")
    elapsed = (time.perf_counter() - start) * 1000

    fusion_result = {
        "context_items": context_items,
        "total_tokens": total_tokens,
        "budget_tokens": token_budget,
        "items_included": len(context_items),
        "items_considered": len(fused),
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **_cost_avoided(tokens_saved, total_saved),
            "fusion": True,
            "channels": [ch.name for ch in channels],
        },
    }
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    # Fusion context_items expose only ``symbol_id`` (e.g. ``path/to/file.py::Name#kind``)
    # — derive the file path from each id rather than the raw id string.
    for item in context_items:
        sid = item.get("symbol_id", "")
        file_rel = sid.split("::", 1)[0] if "::" in sid else ""
        item["_freshness"] = _probe.classify(file_rel)
    fusion_result["_meta"]["freshness"] = _probe.summary(context_items)
    _attach_confidence(
        fusion_result,
        [{"score": item.get("fusion_score")} for item in context_items],
        is_stale=_probe.repo_is_stale,
    )
    return fusion_result
