"""get_call_hierarchy: callers and callees for any indexed symbol, N levels deep."""

import time
from typing import Optional

from ..storage import IndexStore
from ._utils import resolve_repo
from .get_blast_radius import _build_reverse_adjacency, _find_symbol
from ._call_graph import build_symbols_by_file, bfs_callers, bfs_callees


def get_call_hierarchy(
    repo: str,
    symbol_id: str,
    direction: str = "both",
    depth: int = 3,
    storage_path: Optional[str] = None,
) -> dict:
    """Return incoming callers and outgoing callees for a symbol, N levels deep.

    Uses AST-derived call detection — no LSP required. Callers are found by
    scanning symbols in files that import the target's module; callees are found
    by matching imported-symbol names against the target's source body.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol_id: Symbol name or full ID to analyse. Use search_symbols to find IDs.
        direction: 'callers' | 'callees' | 'both'. Default 'both'.
        depth: Maximum hops to traverse (1–5). Default 3.
        storage_path: Custom storage path.

    Returns:
        Dict with symbol info, callers list, callees list, depth_reached, and _meta.
        Each caller/callee entry includes {id, name, kind, file, line, depth}.
    """
    depth = max(1, min(depth, 5))
    if direction not in ("callers", "callees", "both"):
        direction = "both"
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
                "to enable call hierarchy analysis."
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
    symbols_by_file = build_symbols_by_file(index)
    reverse_adj = _build_reverse_adjacency(
        index.imports,
        frozenset(index.source_files),
        getattr(index, "alias_map", None),
    )

    callers: list[dict] = []
    callees: list[dict] = []
    depth_reached = 0

    if direction in ("callers", "both"):
        callers, dr = bfs_callers(
            index, store, owner, name, sym, reverse_adj, symbols_by_file, depth
        )
        depth_reached = max(depth_reached, dr)

    if direction in ("callees", "both"):
        callees, dr = bfs_callees(
            index, store, owner, name, sym, symbols_by_file, depth
        )
        depth_reached = max(depth_reached, dr)

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
        "direction": direction,
        "depth": depth,
        "depth_reached": depth_reached,
        "caller_count": len(callers),
        "callee_count": len(callees),
        "callers": callers,
        "callees": callees,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "source": "ast",
            "tip": (
                "AST-derived: callers = symbols in importing files that mention this "
                "name; callees = imported symbols mentioned in this symbol's body. "
                "May have false positives for aliased names or dynamic dispatch. "
                "Use get_impact_preview for a transitive 'what breaks?' view."
            ),
        },
    }
