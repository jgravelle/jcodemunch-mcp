"""Get most architecturally important symbols by PageRank or in-degree centrality."""

import time
from fnmatch import fnmatch
from typing import Optional

from ..parser.symbols import REPRESENTATIVE_KIND_RANK
from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import index_status_to_tool_error, resolve_repo
from .pagerank import compute_pagerank, compute_in_out_degrees

# Kind priority for picking the representative symbol per file
#: Which symbol represents a file; the one table, derived from `KIND_ORDER` (#806).
_KIND_PRIORITY = REPRESENTATIVE_KIND_RANK


def get_symbol_importance(
    repo: str,
    top_n: int = 20,
    algorithm: str = "pagerank",
    scope: Optional[str] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the most architecturally important symbols ranked by PageRank or in-degree.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        top_n: Number of top symbols to return.
        algorithm: "pagerank" (default) or "degree" (simple in-degree count).
        scope: Limit to a subdirectory prefix or glob pattern (e.g. "src/core/**").
        storage_path: Custom storage path.

    Returns:
        Dict with ranked_symbols, algorithm, iterations_to_converge, and _meta.
    """
    start = time.perf_counter()

    if algorithm not in ("pagerank", "degree"):
        return {"error": f"Invalid algorithm '{algorithm}'. Must be 'pagerank' or 'degree'."}

    top_n = max(1, min(top_n, 200))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if not index.imports:
        return {
            "ranked_symbols": [],
            "algorithm": algorithm,
            "iterations_to_converge": 0,
            "note": "No import graph available. Re-index to build import graph.",
            "_meta": {
                "timing_ms": round((time.perf_counter() - start) * 1000, 1),
                "tokens_saved": 0,
                "total_tokens_saved": 0,
            },
        }

    # Apply scope filter to the file list used for graph computation
    source_files = index.source_files
    if scope:
        scope_prefix = scope.rstrip("/") + "/"
        source_files = [
            f for f in source_files
            if fnmatch(f, scope) or f.startswith(scope_prefix) or fnmatch(f, scope + "/**")
        ]

    _psr4 = getattr(index, "psr4_map", None)
    in_deg, out_deg = compute_in_out_degrees(index.imports, source_files, index.alias_map, _psr4)

    iterations = 0
    if algorithm == "pagerank":
        scores, iterations = compute_pagerank(index.imports, source_files, index.alias_map, psr4_map=_psr4)
    else:
        # degree: score is normalized in-degree (proportion of all imports)
        total_in = sum(in_deg.values()) or 1
        scores = {f: in_deg.get(f, 0) / total_in for f in source_files}

    # Build symbol list: for each file, pick the best representative symbol
    # "best" = highest kind priority, then largest by byte_length
    file_to_best: dict = {}
    for sym in index.symbols:
        f = sym.get("file", "")
        if f not in scores or scores[f] == 0.0:
            continue
        if scope and f not in set(source_files):
            continue
        kind_rank = _KIND_PRIORITY.get(sym.get("kind", ""), 5)
        byte_len = sym.get("byte_length", 0)
        prev = file_to_best.get(f)
        if prev is None:
            file_to_best[f] = (kind_rank, -byte_len, sym)
        else:
            if (kind_rank, -byte_len) < (prev[0], prev[1]):
                file_to_best[f] = (kind_rank, -byte_len, sym)

    # Sort by file PageRank score descending
    ranked_files = sorted(
        [(scores[f], f) for f in file_to_best],
        key=lambda x: x[0],
        reverse=True,
    )

    ranked_symbols = []
    for rank_idx, (score, f) in enumerate(ranked_files[:top_n], start=1):
        _, _, sym = file_to_best[f]
        ranked_symbols.append({
            "symbol_id": sym["id"],
            "rank": rank_idx,
            "score": round(score, 6),
            "in_degree": in_deg.get(f, 0),
            "out_degree": out_deg.get(f, 0),
            "kind": sym.get("kind", ""),
        })

    raw_bytes = sum(index.file_sizes.get(f, 0) for f in source_files)
    response_bytes = sum(len(str(s)) for s in ranked_symbols)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_symbol_importance")
    elapsed = (time.perf_counter() - start) * 1000

    return {
        "ranked_symbols": ranked_symbols,
        "algorithm": algorithm,
        "iterations_to_converge": iterations,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
