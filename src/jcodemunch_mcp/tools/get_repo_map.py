"""Query-less, token-budgeted, signature-level repo overview.

For cold-start orientation when no query exists yet. Reuses the existing
PageRank index scores; emits signatures only (no bodies) and greedy-packs
by file rank under the token budget.
"""

import time
from fnmatch import fnmatch
from typing import Optional

from ..parser.symbols import REPRESENTATIVE_KIND_RANK
from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import index_status_to_tool_error, resolve_repo
from .get_context_bundle import _count_tokens
from .pagerank import compute_pagerank, compute_in_out_degrees

# Same priority used by get_symbol_importance for picking representative symbols.
#: Which symbol represents a file; the one table, derived from `KIND_ORDER` (#806).
_KIND_PRIORITY = REPRESENTATIVE_KIND_RANK

_MAX_PER_FILE_CAP = 50
_DEFAULT_BUDGET = 2048


def _signature_or_name(sym: dict) -> str:
    """Return signature when present, otherwise fall back to name + kind."""
    sig = (sym.get("signature") or "").strip()
    if sig:
        return sig
    name = sym.get("name", "")
    kind = sym.get("kind", "")
    return f"{kind} {name}".strip() if name else ""


def get_repo_map(
    repo: str,
    token_budget: int = _DEFAULT_BUDGET,
    scope: Optional[str] = None,
    max_per_file: int = 5,
    include_kinds: Optional[list] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Build a query-less, signature-level map of a repository within a token budget.

    Groups symbols by file, ranks files by PageRank on the import graph, and
    greedy-packs signatures (not bodies) under ``token_budget``. Designed for
    cold-start orientation: "I just cloned this repo — what matters here?"

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        token_budget: Hard cap on returned tokens (default 2048).
        scope: Optional subdirectory glob to limit results (e.g. ``src/core/*``).
        max_per_file: Max signatures to include per file (default 5, capped at 50).
        include_kinds: Optional list of symbol kinds to restrict results to
            (e.g. ``['class', 'function']``). Defaults to all kinds.
        storage_path: Custom storage path.

    Returns:
        Dict with ``files`` list (each entry has path, rank, score, in_degree,
        symbols[]) plus summary fields and ``_meta``.
    """
    start = time.perf_counter()

    if token_budget < 1:
        return {"error": "token_budget must be >= 1"}

    max_per_file = max(1, min(int(max_per_file), _MAX_PER_FILE_CAP))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    # Apply scope filter to the file list used for graph computation
    source_files = index.source_files
    if scope:
        scope_prefix = scope.rstrip("/") + "/"
        source_files = [
            f for f in source_files
            if fnmatch(f, scope) or f.startswith(scope_prefix) or fnmatch(f, scope + "/**")
        ]

    if not source_files:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "files": [],
            "total_tokens": 0,
            "budget_tokens": token_budget,
            "files_included": 0,
            "files_considered": 0,
            "note": "No files match the requested scope." if scope else "Repository has no indexed files.",
            "_meta": {"timing_ms": round(elapsed, 1), "tokens_saved": 0, "total_tokens_saved": 0},
        }

    _psr4 = getattr(index, "psr4_map", None)

    # Reuse cached PageRank when available; compute and cache when not.
    cache = getattr(index, "_bm25_cache", None)
    if cache is not None and "pagerank" in cache and scope is None:
        scores = cache["pagerank"]
    elif cache is not None and scope is None:
        import threading  # noqa: PLC0415
        with getattr(index, "_bm25_lock", None) or threading.Lock():
            if "pagerank" in cache:
                scores = cache["pagerank"]
            else:
                scores, _iterations = compute_pagerank(
                    index.imports or {}, source_files, index.alias_map, psr4_map=_psr4
                )
                cache["pagerank"] = scores
    else:
        scores, _iterations = compute_pagerank(
            index.imports or {}, source_files, index.alias_map, psr4_map=_psr4
        )

    in_deg, _out_deg = compute_in_out_degrees(
        index.imports or {}, source_files, index.alias_map, _psr4
    )

    scope_set = set(source_files) if scope else None
    kinds_filter = set(include_kinds) if include_kinds else None

    # Group symbols by file, keep top-K per file by kind priority + size.
    per_file: dict[str, list[dict]] = {}
    for sym in index.symbols:
        f = sym.get("file", "")
        if not f:
            continue
        if scope_set is not None and f not in scope_set:
            continue
        if kinds_filter is not None and sym.get("kind") not in kinds_filter:
            continue
        per_file.setdefault(f, []).append(sym)

    files_considered = 0
    ranked: list[tuple[float, str, list[dict]]] = []
    for f, syms in per_file.items():
        score = scores.get(f, 0.0)
        if score <= 0.0:
            continue
        files_considered += 1
        syms.sort(
            key=lambda s: (
                _KIND_PRIORITY.get(s.get("kind", ""), 9),
                -int(s.get("byte_length", 0)),
                s.get("line", 0),
            )
        )
        ranked.append((score, f, syms[:max_per_file]))

    # Total-order tiebreak on file path: tied scores must not fall back to
    # per_file insertion order, which can shuffle across index rebuilds.
    ranked.sort(key=lambda x: (-x[0], x[1]))

    # Greedy pack under token budget; signatures only.
    files_out: list[dict] = []
    total_tokens = 0
    rank_idx = 0
    for score, f, syms in ranked:
        chosen: list[dict] = []
        file_tokens = 0
        for sym in syms:
            sig = _signature_or_name(sym)
            if not sig:
                continue
            sig_tokens = _count_tokens(sig) or 1
            if total_tokens + file_tokens + sig_tokens > token_budget:
                break
            chosen.append({
                "id": sym["id"],
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "line": sym.get("line", 0),
                "signature": sig,
                "tokens": sig_tokens,
            })
            file_tokens += sig_tokens
        if not chosen:
            # If a single signature is too large for the remaining budget, stop —
            # subsequent (lower-ranked) files won't fit either at the same cost.
            if file_tokens == 0 and total_tokens >= token_budget:
                break
            continue
        rank_idx += 1
        files_out.append({
            "path": f,
            "rank": rank_idx,
            "score": round(score, 6),
            "in_degree": in_deg.get(f, 0),
            "tokens": file_tokens,
            "symbols": chosen,
        })
        total_tokens += file_tokens
        if total_tokens >= token_budget:
            break

    # Token-savings ledger entry — compare against full-repo source bytes.
    raw_bytes = sum(index.file_sizes.get(f, 0) for f in source_files)
    response_bytes = total_tokens * 4  # signatures only; coarse byte estimate
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_repo_map")

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "files": files_out,
        "total_tokens": total_tokens,
        "budget_tokens": token_budget,
        "files_included": len(files_out),
        "files_considered": files_considered,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }

    # Helpful note when the import graph is empty (e.g. single-file repos).
    if not index.imports:
        result["note"] = (
            "No import graph available — files ranked uniformly. "
            "Re-index after the repo has cross-file imports for meaningful ranking."
        )

    return result
