"""Standalone token-budgeted context assembler: best-K-tokens for a query."""

import threading
import time
from fnmatch import fnmatch
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided as _cost_avoided
from ._utils import index_status_to_tool_error, ledger_base_path as _ledger_base_path, resolve_repo
from .get_context_bundle import _count_tokens
from .search_symbols import (
    _tokenize,
    _bm25_score,
    _NEGATIVE_EVIDENCE_THRESHOLD,
    BYTES_PER_TOKEN,
    ensure_bm25_cache,
)

# Weight for PageRank when strategy="combined"
_PR_WEIGHT = 100.0

# Diversity packing parameters
_DIVERSITY_DECAY = 0.5       # penalty growth per same-file symbol
_FILE_GROUP_CAP = 3          # max symbols from a single file

# Max chars for the compact-row summary so packed rows stay cheap (#354).
_COMPACT_SUMMARY_MAX = 160

# Keystone-protected compression (opt-in `compress=True`). A large symbol body is
# pruned toward a per-item soft cap so more relevant symbols fit the same budget.
_COMPRESS_TARGET_ITEMS = 8       # aim to leave room for ~this many items
_COMPRESS_MIN_ITEM_TOKENS = 120  # never squeeze a single item below this


def _compressing_get_tokens(base_get_tokens, token_budget: int, registry: dict) -> callable:
    """Wrap a ``get_tokens`` fn so oversized bodies are keystone-pruned (opt-in).

    Records each pruned symbol's ``PruneResult`` in ``registry`` (keyed by symbol
    id) so the item-assembly loop can surface honest elision metadata. The prune
    is a labeled read-only view — the source is never edited on disk.
    """
    from ..retrieval.entropy_prune import prune_source

    per_item = max(_COMPRESS_MIN_ITEM_TOKENS, token_budget // _COMPRESS_TARGET_ITEMS)

    def _wrapped(sym):
        source, _ = base_get_tokens(sym)
        if source:
            pr = prune_source(source, per_item, _count_tokens)
            if pr.is_pruned:
                registry[sym["id"]] = pr
                source = pr.text
        tokens = _count_tokens(source) if source else max(1, sym.get("byte_length", 0) // BYTES_PER_TOKEN)
        return source, tokens

    return _wrapped


def _prune_fields(registry: dict, sym_id: str) -> dict:
    """Honest per-item metadata when a symbol's source was compressed."""
    pr = registry.get(sym_id)
    if not pr:
        return {}
    return {
        "source_pruned": True,
        "source_kept_lines": pr.kept_lines,
        "source_elided_lines": pr.elided_lines,
        "source_total_lines": pr.total_lines,
        "source_is_pruned_view": True,
    }


def _compact_fields(sym: dict, score: float, token_cost: int) -> dict:
    """Display fields the rc1 compact encoder declares (#354).

    The fusion/non-fusion producers key their rows by ``symbol_id`` /
    ``combined_score`` / ``tokens``, but the compact schema reads
    ``id``/``name``/``kind``/``file``/``line``/``score``/``token_cost``/``summary``.
    Emitting both keeps the JSON shape backward-compatible while giving the
    compact path real cells instead of blank rows. ``source`` is intentionally
    not surfaced here — it stays JSON-only so compact rows remain cheap.
    """
    summary = sym.get("summary") or sym.get("signature") or ""
    if len(summary) > _COMPACT_SUMMARY_MAX:
        summary = summary[:_COMPACT_SUMMARY_MAX].rstrip() + "…"
    return {
        "id": sym.get("id"),
        "name": sym.get("name"),
        "kind": sym.get("kind"),
        "file": sym.get("file"),
        "line": sym.get("line"),
        "score": round(score, 4),
        "token_cost": token_cost,
        "summary": summary,
    }


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


# Verdict floor credited when an exact-name seed matched (raw-BM25 scale, vs
# the 0.5 negative-evidence threshold): an exact symbol-name hit is never a
# low-confidence result.
_EXACT_SEED_VERDICT_SCORE = 1.0

_SEED_MAX_PER_TOKEN = 3   # exact matches pinned per source-shaped token
_SEED_MAX_TOTAL = 5       # exact matches pinned per query


def _name_map(index) -> dict:
    """name → [symbol dict] lookup, cached beside the BM25 corpus."""
    cache = index._bm25_cache
    if "name_map" not in cache:
        with getattr(index, "_bm25_lock", None) or threading.Lock():
            if "name_map" not in cache:
                m: dict[str, list] = {}
                for sym in index.symbols:
                    m.setdefault(sym.get("name", ""), []).append(sym)
                cache["name_map"] = m
    return cache["name_map"]


def _exact_seed_symbols(
    index,
    shaped: list[dict],
    include_kinds,
    scope,
    pagerank: dict,
) -> list[dict]:
    """Resolve source-shaped query tokens to exact-name symbol matches.

    For each shaped token, symbols whose ``name`` equals the token's identifier
    (case-sensitive first, case-insensitive fallback) are candidates; a
    qualified token's parent segment narrows them when it matches. Candidates
    are ranked by PageRank and capped per token and per query. Honors the
    caller's ``include_kinds``/``scope`` filters.
    """
    nm = _name_map(index)
    lower_index: dict[str, list] = {}
    seeded: list[dict] = []
    seen_ids: set[str] = set()
    for tok in shaped:
        cands = nm.get(tok["name"])
        if not cands:
            if not lower_index:
                for k, v in nm.items():
                    lower_index.setdefault(k.lower(), []).extend(v)
            cands = lower_index.get(tok["name"].lower(), [])
        cands = [
            s for s in cands
            if (not include_kinds or s.get("kind") in include_kinds)
            and (not scope or fnmatch(s.get("file", ""), scope))
        ]
        if tok.get("parent"):
            want = f"{tok['parent']}.{tok['name']}".lower()
            narrowed = [
                s for s in cands
                if want in s.get("id", "").lower()
                or tok["parent"].lower() in (s.get("parent") or "").lower()
            ]
            if narrowed:
                cands = narrowed
        # Total-order tiebreak on symbol id: tied PageRank must not fall back
        # to index storage order, which can shuffle across rebuilds.
        cands.sort(key=lambda s: (-pagerank.get(s.get("file", ""), 0.0), s.get("id", "")))
        for s in cands[:_SEED_MAX_PER_TOKEN]:
            if s["id"] not in seen_ids:
                seen_ids.add(s["id"])
                seeded.append(s)
            if len(seeded) >= _SEED_MAX_TOTAL:
                return seeded
    return seeded


def get_ranked_context(
    repo: str,
    query: str,
    token_budget: int = 4000,
    strategy: str = "combined",
    include_kinds: Optional[list] = None,
    scope: Optional[str] = None,
    fusion: bool = False,
    compress: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Assemble the best-fit context for a query within a token budget.

    Ranks all symbols by relevance (BM25) and/or centrality (PageRank),
    loads source for the top candidates, and packs greedily until
    ``token_budget`` is exhausted.

    Source-shaped query tokens (``Store.get`` / ``FreshnessProbe`` /
    ``get_ranked_context``) additionally resolve to exact-name symbol matches
    pinned ahead of the ranked results (items carry
    ``match_channel: "exact_name"``; ``_meta.query_shape`` reports what was
    detected). Pure natural-language queries are unaffected.

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
        compress: When True, keystone-protected structural compression prunes
            each oversized symbol body toward a per-item soft cap so MORE
            relevant symbols fit the same budget. Low-signal lines are elided
            (control-flow, returns, signatures, and decision/constraint lines
            are always kept); pruned items carry ``source_pruned`` +
            kept/elided/total line counts and the source is a labeled view.
            Model-free and local. Default False = byte-identical to prior output.
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
        return index_status_to_tool_error(store.inspect_index(owner, name))

    # #377 item 6: identity before the scan, compared after it.
    from ..retrieval import subject_state as _subject
    _state_before = _subject.capture(index)

    # BM25 corpus — cached on CodeIndex
    query_terms = _tokenize(query) or [query.lower()]
    # Guard: empty string in query_terms causes "" to match every filename
    query_terms = [t for t in query_terms if t]
    # Single-flight: concurrent cold callers must not each build the
    # full-corpus BM25 state (#370), nor observe a half-published one (#490).
    cache = ensure_bm25_cache(index)
    idf = cache["idf"]
    avgdl = cache["avgdl"]
    inverted = cache["inverted"]

    # PageRank — computed when strategy requires it
    pagerank: dict = {}
    if strategy in ("centrality", "combined"):
        if "pagerank" not in cache:
            with getattr(index, "_bm25_lock", None) or threading.Lock():
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
            compress=compress,
            start=start,
            # #377 item 6 reaches this exit too (v1.108.185).
            state_before=_state_before,
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

    # Source-shaped exact seeding: identifiers the caller pasted verbatim
    # (Store.get / FreshnessProbe / get_ranked_context) resolve to exact-name
    # symbol matches pinned ahead of the ranked results. Pure-prose queries
    # yield no shaped tokens and this is a no-op.
    from ..retrieval.query_shape import source_shaped_tokens as _source_shaped_tokens
    shaped = _source_shaped_tokens(query)
    seeded = _exact_seed_symbols(index, shaped, include_kinds, scope, pagerank) if shaped else []
    exact_ids = {s["id"] for s in seeded}

    if not raw_scores and not seeded:
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
        # This path asserts "Do not claim this feature exists" in prose, and
        # until #377 item 6 it did so with none of the machinery that decides
        # whether such a claim is allowed: no verdict, no freshness, no
        # coverage, no gates. It gets the same verdict every other retrieval
        # answer gets, so a genuine no-candidate result is citable and a scan
        # over a stale, partial, rebuilding or moving subject is refused.
        from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
        from ..retrieval.verdict import build_verdict as _bv
        from ..retrieval.verdict import index_changed_since_load as _icsl
        from ..retrieval.verdict import index_coverage_meta as _icm
        _p = _FreshnessProbe(
            source_root=getattr(index, "source_root", "") or None,
            indexed_at=getattr(index, "indexed_at", ""),
            index_sha=getattr(index, "git_head", None),
            file_mtimes=getattr(index, "file_mtimes", None),
        )
        result["_meta"]["verdict"] = _bv(
            result_count=0,
            scanned_symbols=len(candidates),
            scanned_files=len(index.source_files),
            query_terms=query_terms,
            source_files=index.source_files,
            index_stale=_p.repo_is_stale,
            freshness=_p.repo_freshness,
            index_changed=_icsl(index),
            coverage=_icm(index),
            moved_during_scan=_subject.moved_during_scan(
                _state_before, index, result_count=0
            ),
            working_tree=_subject.working_tree_state(
                index, scope=scope, freshness=_p.repo_freshness
            ),
        )["verdict"]
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

    # Total-order tiebreak on symbol id: tied scores must not fall back to
    # index storage order, which can shuffle across rebuilds.
    scored.sort(key=lambda x: (-x[0], x[3].get("id", "")))

    if seeded:
        # Pin exact matches ahead of the ranked tail: drop any ranked
        # occurrence of a seeded symbol, then prepend seeds in token order
        # with scores strictly above the ranked maximum (honest per-channel
        # scores still surface via relevance_score/centrality_score).
        top = scored[0][0] if scored else 0.0
        ranked_by_id = {sym["id"]: (b, p) for _, b, p, sym in scored}
        scored = [t for t in scored if t[3]["id"] not in exact_ids]
        norm_pr = max(pagerank.values()) if pagerank else 1.0
        pinned = []
        for i, sym in enumerate(seeded):
            bm25_norm, pr_norm = ranked_by_id.get(
                sym["id"],
                (0.0, pagerank.get(sym.get("file", ""), 0.0) / (norm_pr or 1.0)),
            )
            pinned.append((top + (len(seeded) - i) * 1e-4, bm25_norm, pr_norm, sym))
        scored = pinned + scored

    items_considered = len(scored)

    # Build score lookup for BM25/PR per symbol
    _score_lookup: dict[str, tuple[float, float]] = {}
    for combined_score, bm25_norm, pr_norm, sym in scored:
        _score_lookup[sym["id"]] = (bm25_norm, pr_norm)

    def _get_tokens(sym):
        source = store.get_symbol_content(owner, name, sym["id"], _index=index) or ""
        tokens = _count_tokens(source) if source else max(1, sym.get("byte_length", 0) // BYTES_PER_TOKEN)
        return source, tokens

    _prune_registry: dict = {}
    _pack_get_tokens = (
        _compressing_get_tokens(_get_tokens, token_budget, _prune_registry)
        if compress else _get_tokens
    )

    packed, total_tokens = _pack_budget(
        [(combined_score, sym) for combined_score, _, _, sym in scored],
        token_budget,
        _pack_get_tokens,
    )

    context_items: list[dict] = []
    for adj_score, sym, source, item_tokens in packed:
        bm25_norm, pr_norm = _score_lookup.get(sym["id"], (0.0, 0.0))
        context_items.append({
            "symbol_id": sym["id"],
            **({"match_channel": "exact_name"} if sym["id"] in exact_ids else {}),
            "relevance_score": round(bm25_norm, 4),
            "centrality_score": round(pr_norm, 4),
            "combined_score": round(adj_score, 4),
            "tokens": item_tokens,
            "source": source,
            # Compact-encoder display fields (#354): the rc1 schema declares
            # id/name/kind/file/line/score/token_cost/summary. Without these the
            # producer dict had none of them, so every compact row encoded blank
            # while items_included reported a nonzero count.
            **_compact_fields(sym, adj_score, item_tokens),
            **_prune_fields(_prune_registry, sym["id"]),
        })

    # Retrieval verdict threshold. The verdict itself is computed after the
    # freshness probe below so it can report index staleness.
    _ne_threshold = _NEGATIVE_EVIDENCE_THRESHOLD
    try:
        from .. import config as _cfg
        _ne_threshold = _cfg.get("negative_evidence_threshold", _NEGATIVE_EVIDENCE_THRESHOLD)
    except Exception:
        pass
    negative_evidence = None

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
    if shaped:
        result["_meta"]["query_shape"] = {
            "source_shaped": [t["token"] for t in shaped],
            "exact_seeded": len(exact_ids),
        }
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    from ..storage.token_tracker import record_ranking_event as _record_ranking_event
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(context_items)
    result["_meta"]["freshness"] = _probe.summary(context_items)
    _attach_confidence(result, context_items, is_stale=_probe.repo_is_stale)
    from ..retrieval.verdict import build_verdict as _build_verdict
    from ..retrieval.verdict import index_changed_since_load as _index_changed_since_load
    from ..retrieval.verdict import index_coverage_meta as _index_coverage_meta
    _vres = _build_verdict(
        result_count=len(context_items),
        # #377 hardening item 8: a candidate set that could not be packed into
        # the context budget is not a no-match. The zero-candidate case returns
        # earlier, so anything reaching here really did match something.
        matches_before_packing=items_considered,
        scanned_symbols=items_considered,
        scanned_files=len(set(s.get("file", "") for _, _, _, s in scored)),
        best_score=max(max_bm25, _EXACT_SEED_VERDICT_SCORE) if exact_ids else max_bm25,
        threshold=_ne_threshold,
        query_terms=query_terms,
        source_files=index.source_files,
        semantic_requested=False,
        index_stale=_probe.repo_is_stale,
        freshness=_probe.repo_freshness,
        index_changed=_index_changed_since_load(index),
        coverage=_index_coverage_meta(index),
        moved_during_scan=_subject.moved_during_scan(
            _state_before, index, result_count=len(context_items)
        ),
        working_tree=(
            _subject.working_tree_state(
                index, scope=scope, freshness=_probe.repo_freshness
            )
            if not context_items else None
        ),
    )
    negative_evidence = _vres["negative_evidence"]
    result["_meta"]["verdict"] = _vres["verdict"]
    _record_ranking_event(
        # v1.108.188: the store this call was told to use, not whichever one the
        # first savings write of the process happened to pin.
        base_path=_ledger_base_path(store),
        tool="get_ranked_context",
        repo=f"{owner}/{name}",
        query=query,
        returned_ids=[c.get("symbol_id", "") for c in context_items],
        confidence=result["_meta"].get("confidence"),
        semantic_used=False,
        repo_is_stale=_probe.repo_is_stale,
    )
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
    compress: bool = False,
    state_before: Optional[dict] = None,
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
        # v1.108.185. The scope selected nothing, so not a byte was read — and
        # this returned a bare empty result, which reads like a completed search
        # that found nothing (#377 hardening item 9). Same shape as the
        # no-candidate early return the non-fusion path got a verdict for in
        # v1.108.179; this is the exit that was missed.
        from ..retrieval.verdict import retrieval_verdict_for_index as _rv
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "context_items": [],
            "total_tokens": 0,
            "budget_tokens": token_budget,
            "items_included": 0,
            "items_considered": 0,
            "_meta": {
                "timing_ms": round(elapsed, 1),
                "tokens_saved": 0,
                "total_tokens_saved": 0,
                "fusion": True,
                "search_mode": "fusion",
                "verdict": _rv(
                    index,
                    result_count=0,
                    query_terms=query_terms,
                    scope=scope,
                    state_before=state_before,
                    incomplete={
                        "reason": "empty_scope",
                        "files_eligible": 0,
                        "note": (
                            "No indexed symbol matched this scope, so nothing was "
                            "scanned. An empty eligible set proves nothing about "
                            "the corpus."
                        ),
                    },
                )["verdict"],
            },
        }

    # Centrality for BM25 tiebreaker
    cache = index._bm25_cache
    if "centrality" not in cache:
        with getattr(index, "_bm25_lock", None) or threading.Lock():
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

    _prune_registry: dict = {}
    _pack_get_tokens = (
        _compressing_get_tokens(_get_tokens_fusion, token_budget, _prune_registry)
        if compress else _get_tokens_fusion
    )

    packed, total_tokens = _pack_budget(
        fused_scored, token_budget, _pack_get_tokens,
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
            # Compact-encoder display fields (#354) — see non-fusion path.
            **_compact_fields(sym, adj_score, item_tokens),
            **_prune_fields(_prune_registry, sym["id"]),
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
            "search_mode": "fusion",
            "channels": [ch.name for ch in channels],
        },
    }
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    from ..storage.token_tracker import record_ranking_event as _record_ranking_event
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
    # v1.108.187. This exit passed NO ledger features, so `top1_score`, `top2_score`
    # and `identity_hit` were recorded as their defaults on every row it has ever
    # written — three of regret's six signals read those columns, and a default is
    # indistinguishable from a measurement once it is in the ledger.
    #
    # Identity comes from the channel's own `raw_scores` rather than being left to
    # default: this exit BUILDS the identity channel, so unlike its sibling it knows
    # per symbol whether identity matched. `build_identity_channel` only admits
    # symbols scoring above zero, so membership is the match.
    # ⚠ The ledger input is SEPARATE from the confidence input on purpose.
    # `compute_confidence` sniffs the same `identity` key when the caller passes no
    # `has_identity_match`, and scores it 1.0 known-true / 0.7 unknown / 0.6
    # known-false — so feeding identity to `attach_confidence` would MOVE the
    # confidence of every fusion search (measured: 0.742 -> 0.783 on one fixture).
    # That is a ranking-adjacent behaviour change, not "record the feature", so the
    # confidence call keeps the input it has always had.
    # A WRR score tops out at sum(weights)/(k+1) — ~0.049 here, not the tens
    # BM25 reaches. Grading it on the BM25 curve reported near-zero strength
    # for a perfect fused hit; see retrieval/confidence.py.
    from ..retrieval.signal_fusion import fused_score_ceiling as _fused_ceiling
    _attach_confidence(
        fusion_result,
        [{"score": item.get("fusion_score")} for item in context_items],
        is_stale=_probe.repo_is_stale,
        score_ceiling=_fused_ceiling(channels, smoothing=smoothing, weights=weights),
    )
    from ..retrieval.confidence import extract_ledger_features as _ledger_feats
    _id_raw = getattr(id_ch, "raw_scores", None) or {}
    _feat = _ledger_feats([
        {
            "score": item.get("fusion_score"),
            "identity": _id_raw.get(item.get("symbol_id", ""), 0.0),
        }
        for item in context_items
    ])
    # v1.108.186. This was a hardcoded `semantic_used=True` while the channel list
    # above builds lexical, identity and structural — never similarity — so every
    # row this exit has ever written claimed a channel that did not run. Derived
    # from the channels actually built rather than hardcoded False: if anyone adds a
    # similarity channel here, the ledger follows without a second edit. Same shape
    # as `search_symbols_fusion`, which records its real `similarity_used` flag.
    _semantic_used = any(ch.name == "similarity" for ch in channels)
    _record_ranking_event(
        base_path=_ledger_base_path(store),
        tool="get_ranked_context_fusion",
        repo=f"{owner}/{name}",
        query=query,
        returned_ids=[c.get("symbol_id", "") for c in context_items],
        confidence=fusion_result["_meta"].get("confidence"),
        semantic_used=_semantic_used,
        repo_is_stale=_probe.repo_is_stale,
        **_feat,
    )

    # v1.108.185. The last exit in this tool without a verdict. The non-fusion
    # no-candidate return got one in v1.108.179 and the main path has had one since
    # v1.108.166; this branch was simply never revisited.
    from ..retrieval.verdict import retrieval_verdict_for_index as _rv
    _vres = _rv(
        index,
        result_count=len(context_items),
        # A candidate set that could not be packed into the budget is not a
        # no-match (#377 hardening item 8), and `_pack_budget` can empty it.
        matches_before_packing=len(fused),
        scanned_symbols=len(candidates),
        scanned_files=len(set(s.get("file", "") for s in candidates)),
        best_score=max((fr.score for fr in fused), default=None),
        query_terms=query_terms,
        scope=scope,
        state_before=state_before,
        # v1.108.186. Same derivation as the ledger row above, so the response and
        # the ledger cannot disagree about the same call again — which is exactly
        # what they did while this defaulted to `off` and the ledger said True.
        semantic_channel="ok" if _semantic_used else "off",
    )
    fusion_result["_meta"]["verdict"] = _vres["verdict"]
    if _vres["negative_evidence"] is not None:
        fusion_result["negative_evidence"] = _vres["negative_evidence"]
        if _vres["negative_evidence"]["verdict"] == "no_implementation_found":
            fusion_result["⚠ warning"] = (
                f"No implementation found for '{query[:80]}'. "
                f"Do not claim this feature exists."
            )
        else:
            fusion_result["⚠ warning"] = (
                f"Low-confidence matches for '{query[:80]}' "
                f"(best score: {_vres['negative_evidence']['best_match_score']}). "
                f"Verify before claiming this feature exists."
            )
    return fusion_result
