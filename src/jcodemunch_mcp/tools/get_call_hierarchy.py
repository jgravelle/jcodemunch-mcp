"""get_call_hierarchy: callers and callees for any indexed symbol, N levels deep."""

import time
from typing import Optional

from ..storage import IndexStore
from ._utils import index_status_to_tool_error, resolve_repo
from .get_blast_radius import _build_reverse_adjacency, _find_symbol
from ._call_graph import build_symbols_by_file, bfs_callers, bfs_callees
from ._scip_consume import open_scip_reader, scip_meta_and_stale, scip_meta_block
from ..retrieval.verdict import (
    build_verdict,
    index_coverage_meta,
    index_changed_since_load as _index_changed_since_load,
    symbol_not_found,
)


def _attach_scip_to_hierarchy(response: dict, store, owner: str, name: str) -> dict:
    """Add SCIP compiler-verified callers to a call-hierarchy response.

    Reference edges the AST call graph can't see (string dispatch, barrel
    re-exports) surface as depth-1 callers tagged ``resolution: "scip_reference"``
    + ``verification: "compiler_verified"``; an existing AST caller the compiler
    also proved gains the ``compiler_verified`` verification. ``caller_count`` and
    the resolution-tier histogram are refreshed and ``_meta.scip`` summarises.
    Honest no-op when no SCIP data; only the callers direction is touched.
    """
    if "error" in response:
        return response
    if response.get("direction") not in ("callers", "both"):
        return response
    sym = response.get("symbol") or {}
    target_id = sym.get("id") or ""
    if not target_id:
        return response
    conn = open_scip_reader(store, owner, name)
    if conn is None:
        return response
    try:
        scip_meta, stale = scip_meta_and_stale(conn)
        rows = conn.execute(
            """
            SELECT s.id AS id, s.name AS name, s.kind AS kind, s.file AS file,
                   s.line AS line, SUM(e.count) AS n
            FROM scip_edges e
            JOIN symbols s ON s.id = e.from_symbol_id
            WHERE e.kind = 'reference' AND e.to_symbol_id = ?
            GROUP BY s.id
            """,
            (target_id,),
        ).fetchall()
        # A symbol referencing itself is not a caller.
        rows = [r for r in rows if r["id"] != target_id]
        if not rows:
            return response
        callers = response.setdefault("callers", [])
        by_id = {c.get("id"): c for c in callers}
        added = 0
        for r in rows:
            existing = by_id.get(r["id"])
            if existing is not None:
                if existing.get("source") != "scip":
                    existing["verification"] = "compiler_verified"
                continue
            entry = {
                "id": r["id"],
                "name": r["name"] or "",
                "kind": r["kind"] or "",
                "file": r["file"] or "",
                "line": r["line"] or 0,
                "depth": 1,
                "resolution": "scip_reference",
                "verification": "compiler_verified",
                "source": "scip",
            }
            callers.append(entry)
            by_id[r["id"]] = entry
            added += 1
        response["caller_count"] = len(callers)
        meta = response.setdefault("_meta", {})
        tiers = meta.get("resolution_tiers")
        if isinstance(tiers, dict) and added:
            tiers["scip_reference"] = tiers.get("scip_reference", 0) + added
        # Counts derive from final list state → stable if ever re-attached.
        verified_callers = sum(
            1 for c in callers
            if c.get("source") != "scip" and c.get("verification") == "compiler_verified"
        )
        scip_only_callers = sum(1 for c in callers if c.get("source") == "scip")
        meta["scip"] = scip_meta_block(
            scip_meta, stale,
            verified_callers=verified_callers, scip_only_callers=scip_only_callers,
        )
    except Exception:
        return response
    finally:
        conn.close()
    return response


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
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if index.imports is None:
        return {
            "error": (
                "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 "
                "to enable call hierarchy analysis."
            )
        }

    matches = _find_symbol(index, symbol_id)
    if not matches:
        return symbol_not_found(symbol_id, index.symbols)
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
        getattr(index, "psr4_map", None),
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

    # Build dispatches section from dispatch edges
    ctx_meta = getattr(index, "context_metadata", None) or {}
    dispatch_edge_data = ctx_meta.get("dispatch_edges", [])
    dispatches: list[dict] = []
    if dispatch_edge_data:
        # Group by (interface_name, method_name)
        grouped: dict[tuple[str, str], list[dict]] = {}
        for de in dispatch_edge_data:
            key = (de.get("interface_name", ""), de.get("method_name", ""))
            grouped.setdefault(key, []).append(de)
        for (iface, method), impls in grouped.items():
            dispatches.append({
                "interface": iface,
                "method": method,
                "implementations": [
                    {
                        "name": imp.get("impl_name", ""),
                        "file": imp.get("impl_file", ""),
                        "line": imp.get("impl_line", 0),
                    }
                    for imp in impls
                ],
            })

    # Determine methodology based on available data
    get_callers = getattr(index, "get_callers_by_name", None)
    callers_by_name = get_callers() if get_callers else None
    has_call_data = bool(callers_by_name)
    has_lsp_data = bool(ctx_meta.get("lsp_edges"))
    has_dispatch_data = bool(dispatch_edge_data)
    if has_dispatch_data:
        methodology = "lsp_dispatch_enriched"
        confidence = "high"
        source = "lsp_bridge + dispatch_resolution + ast_call_references"
        tip = (
            "LSP dispatch-enriched: compiler-grade resolution via language servers with "
            "interface/trait dispatch resolution — concrete implementations of interface "
            "methods are resolved via textDocument/implementation. Each edge has a "
            "'resolution' field: lsp_dispatch (interface dispatch), lsp_resolved "
            "(compiler-grade), ast_resolved (direct AST), ast_inferred (import graph), "
            "or text_matched (heuristic)."
        )
    elif has_lsp_data:
        methodology = "lsp_enriched"
        confidence = "high"
        source = "lsp_bridge + ast_call_references"
        tip = (
            "LSP-enriched: compiler-grade resolution via language servers (pyright, gopls, "
            "typescript-language-server, rust-analyzer) for highest confidence, with AST "
            "call_references and text heuristic as fallback layers. Each edge has a "
            "'resolution' field: lsp_resolved (compiler-grade), ast_resolved (direct AST), "
            "ast_inferred (import graph), or text_matched (heuristic)."
        )
    elif has_call_data:
        methodology = "ast_call_references"
        confidence = "medium"
        source = "ast_call_references"
        tip = (
            "AST-based: call references extracted from tree-sitter AST during indexing. "
            "More precise than text heuristic, but still approximate for dynamic dispatch. "
            "Each edge has a 'resolution' field: ast_resolved (direct AST match), "
            "ast_inferred (resolved via import graph), or text_matched (heuristic). "
            "Enable LSP enrichment for compiler-grade resolution."
        )
    else:
        methodology = "text_heuristic"
        confidence = "low"
        source = "text_heuristic"
        tip = (
            "Text-heuristic: callers = symbols in importing files that mention this "
            "name as a word token; callees = imported symbols mentioned in this "
            "symbol's body. May have false positives for common names or dynamic "
            "dispatch. Use get_impact_preview for a transitive 'what breaks?' view."
        )

    # Summarize resolution tiers across all edges
    resolution_counts: dict[str, int] = {}
    for edge in callers + callees:
        r = edge.get("resolution", "unknown")
        resolution_counts[r] = resolution_counts.get(r, 0) + 1

    response: dict = {
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
        "dispatches": dispatches,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "methodology": methodology,
            "confidence_level": confidence,
            "source": source,
            "resolution_tiers": resolution_counts,
            "tip": tip,
        },
    }

    # Phase 2: runtime confidence — zero-cost no-op when no traces ingested.
    from ..runtime.confidence import attach_runtime_confidence as _attach_runtime
    _db_path_str = str(store._sqlite._db_path(owner, name))
    _stamped: list[dict] = [response["symbol"], *callers, *callees]
    _summary = _attach_runtime(_stamped, _db_path_str, id_field="id")
    if _summary:
        response["_meta"]["runtime_freshness"] = _summary

    # Compile-time evidence (SCIP): union compiler-verified callers the AST missed.
    # Honest no-op when no .scip was ingested.
    response = _attach_scip_to_hierarchy(response, store, owner, name)

    # Disclose inbound edges the callers query could not consult (jcm#423).
    # Attached AFTER SCIP, because a compiler-verified caller is a real answer
    # and must be allowed to reduce what we report as excluded.
    if direction in ("callers", "both"):
        _attach_caller_gate_disclosure(response, index, sym, reverse_adj)
    return response


def _attach_caller_gate_disclosure(
    response: dict, index, sym: dict, reverse_adj: dict
) -> dict:
    """Say when the callers query could not see edges that exist (jcm#423).

    The callers direction only considers files the import graph says import this
    symbol's file. The callees direction consults no such gate, so an edge can be
    returned in one direction and missing in the other, and the response gave no
    way to tell that from "nothing calls this".

    ⚠ Deliberately NOT gated on ``caller_count == 0``. The report's second case
    came back with 16 callers -- every one a test -- while the single production
    caller was excluded. A non-zero count can be just as incomplete as a zero,
    and only the zero case was ever going to be noticed.
    """
    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    if not sym_name or not sym_file:
        return response

    get_callers = getattr(index, "get_callers_by_name", None)
    callers_by_name = get_callers() if get_callers else None
    if not callers_by_name:
        return response

    reachable = set(reverse_adj.get(sym_file, [])) | {sym_file}
    returned = {c.get("file", "") for c in (response.get("callers") or [])}
    excluded: set[str] = set()
    for (caller_file, called_name), ids in callers_by_name.items():
        if called_name != sym_name or not ids:
            continue
        if caller_file in reachable or caller_file in returned:
            continue
        excluded.add(caller_file)
    if not excluded:
        return response

    meta = response.setdefault("_meta", {})
    meta["caller_graph_incomplete"] = {
        "excluded_files": sorted(excluded)[:10],
        "excluded_file_count": len(excluded),
        "reason": "import_graph_did_not_reach",
        "detail": (
            f"{len(excluded)} file(s) contain a call to '{sym_name}' but do not "
            "resolve an import edge to its file, so the callers direction never "
            "considered them. The callees direction applies no such gate, which "
            "is why an edge can appear there and not here."
        ),
    }
    # An answer that could not search part of the graph cannot prove absence.
    if not response.get("callers"):
        meta["verdict"] = build_verdict(
            result_count=0,
            scanned_files=len(getattr(index, "source_files", []) or []),
            coverage=index_coverage_meta(index),
            # An empty answer measured while the .db was being rewritten under
            # this call cannot prove absence either, and that gate outranks ours
            # because it names something the caller can retry.
            # `test_absence_wiring_guard` enforces this on every build_verdict
            # call site, and caught this one being added without it.
            index_changed=_index_changed_since_load(index),
            incomplete=meta["caller_graph_incomplete"],
        )["verdict"]
    return response
