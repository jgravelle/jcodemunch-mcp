"""get_call_hierarchy: callers and callees for any indexed symbol, N levels deep."""

import logging
import re
import time
from typing import Optional

from ..storage import IndexStore
from ._utils import index_status_to_tool_error, resolve_repo
from .get_blast_radius import _build_reverse_adjacency, _find_symbol, with_same_package_peers
from ._call_graph import build_symbols_by_file, bfs_callers, bfs_callees, label_row_depths
from ._scip_consume import open_scip_reader, scip_meta_and_stale, scip_meta_block
from ..retrieval.verdict import (
    build_verdict,
    index_coverage_meta,
    index_changed_since_load as _index_changed_since_load,
    symbol_not_found,
)

logger = logging.getLogger(__name__)


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
                # The compiler says this symbol references the target itself,
                # so it is a direct caller even if the walk first reached it
                # through another one. `_label_depths` re-derives the rows
                # reached through it.
                if (existing.get("depth") or 1) > 1:
                    existing["depth"] = 1
                    existing.pop("via", None)
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


def _label_depths(response: dict) -> None:
    """Order each list by depth and say how many rows sit at each depth.

    One ``callers`` list held every depth under one ``caller_count``: at the
    default depth 3, 735 rows for one function here, 12 of them direct. A row
    carries its ``depth`` (and, deeper than 1, ``via``), and the response now
    also carries ``direct_caller_count`` and ``callers_by_depth`` (callees the
    same). The sort is stable, so the walk's order holds within a depth; it
    runs after compile-time evidence, which appends its direct callers last
    and can promote a row the walk reached deeper to depth 1.

    A row's depth is re-derived from its ``via`` chain (one more than the row
    it is reached through), so a promotion shortens every chain through it.
    A row with no usable depth is counted under ``"unknown"`` and sorted last;
    it is never counted as direct.
    """
    for key, direct_key, by_depth_key in (
        ("callers", "direct_caller_count", "callers_by_depth"),
        ("callees", "direct_callee_count", "callees_by_depth"),
    ):
        counts = label_row_depths(response.get(key) or [])
        response[direct_key] = counts.get("1", 0)
        response[by_depth_key] = counts


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
        A caller entry also carries ``call_lines``: the lines in its body that
        call the target (capped at CALL_LINES_CAP). The response carries
        ``call_sites``: ``file:line`` of each call by a direct caller (capped at
        CALL_SITES_CAP). With direction='callers', a name with several
        definitions answers for all of them and lists them in ``definitions``.
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
    # A bare name with several definitions: callers-only answers for all of
    # them (a call-site question names a function, not one of its homes);
    # callees still refuse, because each definition calls different things.
    definitions: list[dict] = []
    if len(matches) > 1 and direction == "callers":
        definitions = [
            {"id": s["id"], "name": s["name"], "file": s["file"], "line": s.get("line", 0)}
            for s in matches
        ]
    elif len(matches) > 1:
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
    # A Go file's package siblings call it with no import (L-262).
    reverse_adj = with_same_package_peers(
        reverse_adj, getattr(index, "file_languages", None)
    )

    callers: list[dict] = []
    callees: list[dict] = []
    depth_reached = 0

    if direction in ("callers", "both"):
        seen_ids: set[str] = set()
        for target in matches if definitions else [sym]:
            found, dr = bfs_callers(
                index, store, owner, name, target, reverse_adj, symbols_by_file, depth
            )
            for row in found:
                if row["id"] not in seen_ids:
                    seen_ids.add(row["id"])
                    callers.append(row)
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
    _label_depths(response)

    # Disclose inbound edges the callers query could not consult (jcm#423).
    # Attached AFTER SCIP, because a compiler-verified caller is a real answer
    # and must be allowed to reduce what we report as excluded.
    if direction in ("callers", "both"):
        _attach_caller_gate_disclosure(response, index, sym, reverse_adj)
        _attach_call_lines(response, index, store, owner, name)
    if definitions:
        response["definitions"] = definitions
    return response


# A caller row carried only its DEFINITION line, so "where is f called" still
# sent the agent into each caller to find the call. These bound the lines added
# per row and the flat file:line list (1.x list-field rule: bounded on arrival).
CALL_LINES_CAP = 10
CALL_SITES_CAP = 50
CALL_LINES_BASIS = "name_in_caller_body"
# Text before a match that makes it a definition, not a call.
_DEF_PREFIX = re.compile(r"\b(?:def|function|func|fn|fun|sub|proc)\s+$")


def _call_lines_in(lines: list[str], start: int, end: int, target: str) -> list[int]:
    """1-based lines in [start, end] where ``target(`` is called, as a word.

    ``obj.target(`` counts; ``def target(`` does not. Comments and strings are
    not told apart (the response's ``call_lines_basis`` says so).
    """
    if not target or start <= 0:
        return []
    pat = re.compile(r"(?<![\w$])" + re.escape(target) + r"\s*\(")
    found: list[int] = []
    for n in range(start, min(max(end, start), len(lines)) + 1):
        text = lines[n - 1]
        for m in pat.finditer(text):
            if not _DEF_PREFIX.search(text[: m.start()]):
                found.append(n)
                break
    return found


def _attach_call_lines(response: dict, index, store, owner: str, name: str) -> None:
    """Add ``call_lines`` to each caller row and a flat ``call_sites`` list."""
    callers = response.get("callers") or []
    symbol_index: dict = getattr(index, "_symbol_index", {}) or {}
    target_name = str((response.get("symbol") or {}).get("name", "")).split(".")[-1]
    file_lines: dict[str, list[str]] = {}

    def lines_of(path: str) -> list[str]:
        if path not in file_lines:
            try:
                content = store.get_file_content(owner, name, path)
            except Exception:
                logger.debug("call_lines: could not read %s", path, exc_info=True)
                content = None
            file_lines[path] = content.splitlines() if content else []
        return file_lines[path]

    sites: list[str] = []
    for row in callers:
        full = symbol_index.get(row.get("id", "")) or {}
        path = row.get("file") or full.get("file") or ""
        start = int(row.get("line") or full.get("line") or 0)
        end = int(full.get("end_line") or start)
        via = symbol_index.get(row.get("via", "")) if row.get("via") else None
        target = str(via.get("name", "")).split(".")[-1] if via else target_name
        found = _call_lines_in(lines_of(path), start, end, target) if path else []
        row["call_lines"] = found[:CALL_LINES_CAP]
        if len(found) > CALL_LINES_CAP:
            row["call_lines_total"] = len(found)
            row["call_lines_truncated"] = True
        if row.get("depth", 1) == 1:
            sites.extend(f"{path}:{n}" for n in found)
    if not callers:
        return
    response["call_sites"] = sites[:CALL_SITES_CAP]
    response["call_sites_total"] = len(sites)
    response["call_sites_truncated"] = len(sites) > CALL_SITES_CAP
    response["call_lines_basis"] = CALL_LINES_BASIS


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
