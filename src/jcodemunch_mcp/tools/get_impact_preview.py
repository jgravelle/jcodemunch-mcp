"""get_impact_preview: transitive impact analysis for removal or rename of a symbol."""

import time
from collections import defaultdict
from typing import Optional

from ..storage import IndexStore
from ._utils import resolve_repo
from .get_blast_radius import _build_reverse_adjacency, _find_symbol
from ._call_graph import build_symbols_by_file, find_direct_callers

# Max traversal depth to bound compute on pathological graphs
_MAX_DEPTH = 5


def get_impact_preview(
    repo: str,
    symbol_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Show what breaks if a symbol is removed or renamed.

    Walks the call graph transitively from the target symbol outward — who
    calls it, who calls those callers, and so on — returning all affected
    symbols grouped by file, with call-chain paths showing how each affected
    symbol is reached.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol_id: Symbol name or full ID to analyse. Use search_symbols to find IDs.
        storage_path: Custom storage path.

    Returns:
        Dict with:
          - symbol: the target symbol
          - affected_files: count of distinct files containing affected symbols
          - affected_symbols: flat list of {id, name, kind, file, line, call_chain}
          - affected_by_file: affected symbols grouped by file path
          - call_chains: list of {symbol_id, chain} where chain is
            [target_id, ..., caller_id] showing the path
          - _meta
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    if index.imports is None:
        return {
            "error": (
                "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 "
                "to enable impact preview analysis."
            )
        }

    matches = _find_symbol(index, symbol_id)
    if not matches:
        return {"error": f"Symbol not found: '{symbol_id}'. Try search_symbols first."}
    if len(matches) > 1:
        ambiguous = [{"name": s["name"], "file": s["file"], "id": s["id"]} for s in matches]
        return {
            "error": (
                f"Ambiguous symbol '{symbol_id}': found {len(matches)} definitions. "
                "Use the symbol 'id' field to disambiguate."
            ),
            "candidates": ambiguous,
        }

    sym = matches[0]
    sym_id = sym.get("id", "")

    symbols_by_file = build_symbols_by_file(index)
    reverse_adj = _build_reverse_adjacency(
        index.imports,
        frozenset(index.source_files),
        getattr(index, "alias_map", None),
    )
    symbol_index: dict[str, dict] = getattr(index, "_symbol_index", {})

    # DFS collecting call chains.
    # visited maps symbol_id → chain that reached it (shortest first-seen).
    # chain = [sym_id (target), ..., caller_id]
    visited: dict[str, list[str]] = {sym_id: [sym_id]}
    affected_symbols: list[dict] = []

    # Stack entries: (sym_dict, chain_up_to_this_sym)
    stack: list[tuple[dict, list[str]]] = [(sym, [sym_id])]

    while stack:
        curr_sym, curr_chain = stack.pop()

        if len(curr_chain) > _MAX_DEPTH:
            continue

        callers = find_direct_callers(
            index, store, owner, name, curr_sym, reverse_adj, symbols_by_file
        )

        for caller in callers:
            cid = caller["id"]
            if cid in visited:
                continue
            new_chain = curr_chain + [cid]
            visited[cid] = new_chain

            affected_symbols.append({
                "id": cid,
                "name": caller["name"],
                "kind": caller["kind"],
                "file": caller["file"],
                "line": caller["line"],
                "call_chain": new_chain,
            })

            caller_full = symbol_index.get(cid)
            if caller_full:
                stack.append((caller_full, new_chain))

    # Group by file
    by_file: dict[str, list[dict]] = defaultdict(list)
    for entry in affected_symbols:
        by_file[entry["file"]].append(entry)

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "symbol": {
            "id": sym.get("id", ""),
            "name": sym.get("name", ""),
            "kind": sym.get("kind", ""),
            "file": sym.get("file", ""),
            "line": sym.get("line", 0),
        },
        "affected_files": len(by_file),
        "affected_symbol_count": len(affected_symbols),
        "affected_symbols": affected_symbols,
        "affected_by_file": {
            f: [
                {"id": s["id"], "name": s["name"], "kind": s["kind"], "line": s["line"]}
                for s in syms
            ]
            for f, syms in sorted(by_file.items())
        },
        "call_chains": [
            {"symbol_id": s["id"], "chain": s["call_chain"]}
            for s in affected_symbols
        ],
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "source": "ast",
            "tip": (
                "Shows every symbol that transitively calls this one. "
                "call_chain = [target_id, intermediate..., caller_id]. "
                "Use get_call_hierarchy for a structured caller/callee tree."
            ),
        },
    }
