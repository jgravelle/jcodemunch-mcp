"""get_signal_chains — discover entry-point-to-leaf pathways through the call graph.

A **signal chain** traces how an external signal (HTTP request, CLI command,
scheduled task, event) propagates through the codebase via the call graph.
Each chain starts at a **gateway** — a symbol that receives external input —
and follows BFS callees to the leaves.

Two modes:
  - **Discovery** (symbol omitted): find all gateways, trace chains, return
    the full map with summary stats and orphan detection.
  - **Lookup** (symbol provided): return which chains a specific symbol
    participates in — "this function sits on POST /api/users and cli:seed-db".
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

from ..retrieval.verdict import symbol_not_found
from ..storage import IndexStore
from ._utils import resolve_repo
from ._call_graph import _CalleeNameIndex, build_symbols_by_file, find_direct_callees
from .flow_edges import resolve_flow_edges


# ---------------------------------------------------------------------------
# Gateway kind classifiers — order matters (first match wins)
# ---------------------------------------------------------------------------

_HTTP_RE = re.compile(
    r"@(?:app|router|blueprint|api|bp|flask_app)\."
    r"(?:route|get|post|put|delete|patch|head|options|websocket)"
    r"|@(?:Get|Post|Put|Delete|Patch|Request)Mapping\b"
    r"|@(?:Get|Post|Put|Patch|Delete|Head|Options|All)\s*\(",
    re.IGNORECASE,
)

_CLI_RE = re.compile(
    r"@(?:cli|app)\.command"
    r"|@click\.(?:command|group)"
    r"|typer\..*command",
    re.IGNORECASE,
)

_EVENT_RE = re.compile(
    r"@on_event\b"
    r"|@event_handler\b"
    r"|@(?:app|router)\.websocket\b"
    r"|@receiver\b"
    r"|@signal\b",
    re.IGNORECASE,
)

_TASK_RE = re.compile(
    r"@(?:celery|huey|dramatiq|rq)\."
    r"|@task\b"
    r"|@shared_task\b"
    r"|@periodic_task\b",
    re.IGNORECASE,
)

_MAIN_GUARD_RE = re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']')

_MAIN_FILENAMES = frozenset({
    "__main__.py", "manage.py", "wsgi.py", "asgi.py",
    "app.py", "main.py", "run.py", "cli.py",
})

_TEST_PREFIXES = ("test_",)

_KIND_ORDER = [
    ("http", _HTTP_RE),
    ("cli", _CLI_RE),
    ("event", _EVENT_RE),
    ("task", _TASK_RE),
]


# ---------------------------------------------------------------------------
# Gateway detection
# ---------------------------------------------------------------------------

def _classify_gateway(sym: dict, file_content: Optional[str] = None) -> Optional[str]:
    """Return the gateway kind for a symbol, or None if it's not a gateway."""
    decorators = sym.get("decorators") or []
    decorator_text = " ".join(str(d) for d in decorators)

    # Check decorator-based kinds
    for kind, pattern in _KIND_ORDER:
        if decorator_text and pattern.search(decorator_text):
            return kind

    # Main guard: check if file has `if __name__ == "__main__"` and symbol is
    # at module level (function, not method)
    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    filename = sym_file.replace("\\", "/").rsplit("/", 1)[-1]

    if filename in _MAIN_FILENAMES and sym.get("kind") in ("function", "class"):
        return "main"

    # Test gateway (must be opted in)
    if sym_name.startswith("test_") and sym.get("kind") == "function":
        return "test"

    return None


def _extract_label(sym: dict, kind: str) -> str:
    """Build a human-readable label for a gateway symbol."""
    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    decorators = sym.get("decorators") or []
    decorator_text = " ".join(str(d) for d in decorators)

    if kind == "http":
        # Try to extract verb + path from decorator
        # Flask/FastAPI: @app.get("/users")
        m = re.search(
            r"@(?:app|router|blueprint|api|bp)\s*\.\s*"
            r"(route|get|post|put|delete|patch|head|options)\s*\(\s*"
            r"[\"']([^\"']+)[\"']",
            decorator_text, re.IGNORECASE,
        )
        if m:
            verb = m.group(1).upper()
            if verb == "ROUTE":
                verb = "GET"
            path = m.group(2)
            return f"{verb} {path}"
        # Spring: @GetMapping("/users")
        m = re.search(
            r"@(Get|Post|Put|Delete|Patch|Request)Mapping"
            r"(?:\s*\(\s*(?:value\s*=\s*)?[\"']([^\"']*)[\"'])?",
            decorator_text, re.IGNORECASE,
        )
        if m:
            verb = m.group(1).upper()
            path = m.group(2) or "/"
            return f"{verb} {path}"
        # NestJS: @Get("/users")
        m = re.search(
            r"@(Get|Post|Put|Patch|Delete|Head|Options|All)\s*\(\s*"
            r"[\"']([^\"']*)[\"']",
            decorator_text, re.IGNORECASE,
        )
        if m:
            verb = m.group(1).upper()
            path = m.group(2) or "/"
            return f"{verb} {path}"
        return f"http:{sym_name}"

    if kind == "cli":
        # @click.command or @app.command — extract command name from decorator
        m = re.search(
            r"@(?:cli|app|click)\.(?:command|group)\s*\(\s*"
            r"(?:name\s*=\s*)?[\"']([^\"']+)[\"']",
            decorator_text, re.IGNORECASE,
        )
        if m:
            return f"cli:{m.group(1)}"
        return f"cli:{sym_name}"

    if kind == "task":
        m = re.search(
            r"@(?:celery|huey|dramatiq|rq|shared_task|task)\s*"
            r"(?:\.\s*task\s*)?\(\s*(?:name\s*=\s*)?[\"']([^\"']+)[\"']",
            decorator_text, re.IGNORECASE,
        )
        if m:
            return f"task:{m.group(1)}"
        return f"task:{sym_name}"

    if kind == "event":
        return f"event:{sym_name}"

    if kind == "main":
        filename = sym_file.replace("\\", "/").rsplit("/", 1)[-1]
        return f"main:{filename}"

    if kind == "test":
        return f"test:{sym_name}"

    return sym_name


# ---------------------------------------------------------------------------
# BFS chain tracing
# ---------------------------------------------------------------------------

def _bfs_chain(
    index,
    store: IndexStore,
    owner: str,
    repo_name: str,
    gateway_sym: dict,
    symbols_by_file: dict[str, list[dict]],
    max_depth: int,
    callee_index=None,
) -> tuple[list[dict], int]:
    """BFS forward from a gateway through callees.

    Returns (chain_symbols, max_depth_reached) where each chain_symbol is
    {id, name, kind, file, line, depth}.
    """
    from collections import deque

    sym_id = gateway_sym.get("id", "")
    visited: set[str] = {sym_id}
    queue: deque[tuple[dict, int]] = deque()
    chain: list[dict] = []
    depth_reached = 0
    symbol_index: dict[str, dict] = getattr(index, "_symbol_index", {})

    # Depth-1 callees
    for c in find_direct_callees(
        index, store, owner, repo_name, gateway_sym, symbols_by_file,
        None, callee_index,
    ):
        if c["id"] not in visited:
            visited.add(c["id"])
            chain.append({**c, "depth": 1})
            depth_reached = 1
            if max_depth > 1:
                queue.append((c, 1))

    while queue:
        curr_dict, curr_depth = queue.popleft()
        if curr_depth >= max_depth:
            continue
        curr_full = symbol_index.get(curr_dict["id"])
        if not curr_full:
            continue
        for c in find_direct_callees(
            index, store, owner, repo_name, curr_full, symbols_by_file,
            None, callee_index,
        ):
            if c["id"] not in visited:
                visited.add(c["id"])
                new_depth = curr_depth + 1
                chain.append({**c, "depth": new_depth})
                depth_reached = max(depth_reached, new_depth)
                if new_depth < max_depth:
                    queue.append((c, new_depth))

    return chain, depth_reached


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def get_signal_chains(
    repo: str,
    symbol: Optional[str] = None,
    kind: Optional[str] = None,
    max_depth: int = 5,
    include_tests: bool = False,
    storage_path: Optional[str] = None,
    include_flow_edges: bool = True,
) -> dict:
    """Discover entry-point-to-leaf pathways through the call graph.

    Args:
        repo:           Repository identifier (owner/repo or bare name).
        symbol:         Optional symbol name or ID for lookup mode.
                        When provided, returns only chains containing that symbol.
        kind:           Filter gateways by kind: http, cli, event, task, main, test.
        max_depth:      BFS depth limit per chain (1–8, default 5).
        include_tests:  Include test_* functions as gateways (default false).
        storage_path:   Custom storage path.
        include_flow_edges: Resolve framework flow edges the call graph is blind
                        to — string-dispatched handlers (Django ``path()``,
                        Express ``router.get(p, h)``, Flask ``add_url_rule``,
                        Rails ``to:``) surface as http gateways, and templates a
                        chain renders attach as a per-chain ``views`` list.
                        Default true; set false for pure call-graph behavior.

    Returns:
        Discovery mode: {chains, gateway_count, orphan_symbols, orphan_symbol_pct, ...}
        Lookup mode:    {symbol, chain_count, chains, on_no_chain, ...}
    """
    t0 = time.perf_counter()
    max_depth = max(1, min(8, max_depth))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    if not index.imports:
        return {"error": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0."}

    source_files = frozenset(index.source_files)
    symbols_by_file = build_symbols_by_file(index)
    # Built ONCE for the whole run, not per gateway: _bfs_chain runs its own BFS
    # loop rather than going through bfs_callees, so a cache scoped inside the
    # traversal would rebuild the map for every gateway this repo has.
    callee_index = _CalleeNameIndex(symbols_by_file)

    # ------------------------------------------------------------------
    # Phase 1: detect all gateways
    # ------------------------------------------------------------------
    gateways: list[tuple[dict, str, str]] = []  # (sym, kind, label)

    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        gw_kind = _classify_gateway(sym)
        if gw_kind is None:
            continue
        if gw_kind == "test" and not include_tests:
            continue
        if kind and gw_kind != kind:
            continue
        label = _extract_label(sym, gw_kind)
        gateways.append((sym, gw_kind, label))

    # ------------------------------------------------------------------
    # Phase 1b: framework flow edges the call graph can't see.
    #   route->handler: string-dispatched handlers become http gateways.
    #   render->view:   rendered templates indexed for per-chain annotation.
    # ------------------------------------------------------------------
    flow_edges: list[dict] = []
    renders_by_symbol: dict[str, list[str]] = {}
    flow_summary = {"route_gateways": 0, "unresolved_routes": 0, "render_views": 0}
    if include_flow_edges:
        try:
            flow_edges = resolve_flow_edges(
                index, store, owner, name, symbols_by_file=symbols_by_file,
            )
        except Exception:
            logger.debug("flow-edge resolution failed", exc_info=True)
            flow_edges = []

        symbol_index_fe: dict[str, dict] = getattr(index, "_symbol_index", {})
        existing_gateway_ids = {gw_sym.get("id", "") for gw_sym, _, _ in gateways}
        seen_route_ids: set[str] = set()
        for e in flow_edges:
            etype = e.get("type")
            if etype == "route->handler":
                dst_id = e.get("dst_id")
                if not dst_id:
                    flow_summary["unresolved_routes"] += 1
                    continue
                # Already a decorator gateway, or already added via another route.
                if dst_id in existing_gateway_ids or dst_id in seen_route_ids:
                    continue
                handler_sym = symbol_index_fe.get(dst_id)
                if not handler_sym:
                    continue
                if kind and kind != "http":
                    continue
                seen_route_ids.add(dst_id)
                gateways.append((handler_sym, "http", e.get("label", "")))
                flow_summary["route_gateways"] += 1
            elif etype == "render->view":
                src_id = e.get("src_id")
                if src_id:
                    renders_by_symbol.setdefault(src_id, []).append(e.get("dst_name", ""))
                    flow_summary["render_views"] += 1

    # Resolve a lookup's symbol BEFORE the no-gateway return (#869): a repo
    # with no gateways answered a nonexistent id with an empty chain list,
    # which reads as "the symbol is on no chain" rather than "no such symbol".
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    if symbol:
        # Resolve symbol: try exact ID match first, then name match
        symbol_index_map: dict[str, dict] = getattr(index, "_symbol_index", {})

        if symbol in symbol_index_map:
            target_id = symbol
            target_name = symbol_index_map[symbol].get("name", symbol)
        else:
            # Name match: find first symbol whose name matches
            for s in index.symbols:
                if s.get("name", "") == symbol:
                    target_id = s.get("id", "")
                    target_name = symbol
                    break

        if not target_id:
            return {"repo": f"{owner}/{name}", **symbol_not_found(symbol, index.symbols)}

    if not gateways:
        elapsed = (time.perf_counter() - t0) * 1000
        warning = (
            "No gateways detected. This tool looks for HTTP route decorators "
            "(Flask/FastAPI/Spring/NestJS/ASP.NET), CLI commands (@click.command, "
            "@app.command), task decorators (@celery.task), event handlers, and "
            "standard entry points (main.py, app.py, __main__.py). "
            "If your framework uses a different pattern, the call graph can "
            "still be explored with get_call_hierarchy."
        )
        no_gw_meta: dict = {"timing_ms": round(elapsed, 1)}
        if include_flow_edges:
            no_gw_meta["flow_edges"] = flow_summary
        return {
            "repo": f"{owner}/{name}",
            "gateway_count": 0,
            "chain_count": 0,
            "chains": [],
            "gateway_warning": warning,
            "_meta": no_gw_meta,
        }

    # ------------------------------------------------------------------
    # Phase 2: trace BFS chains from each gateway
    # ------------------------------------------------------------------
    chains: list[dict] = []
    # Track which symbol IDs appear on any chain (for orphan detection)
    symbol_ids_on_chains: set[str] = set()

    for gw_sym, gw_kind, gw_label in gateways:
        chain_syms, depth_reached = _bfs_chain(
            index, store, owner, name, gw_sym, symbols_by_file, max_depth,
            callee_index,
        )

        gw_id = gw_sym.get("id", "")
        symbol_ids_on_chains.add(gw_id)
        for cs in chain_syms:
            symbol_ids_on_chains.add(cs["id"])

        # Collect unique files touched
        files_touched: list[str] = []
        seen_files: set[str] = set()
        gw_file = gw_sym.get("file", "")
        if gw_file:
            seen_files.add(gw_file)
            files_touched.append(gw_file)
        for cs in chain_syms:
            f = cs.get("file", "")
            if f and f not in seen_files:
                seen_files.add(f)
                files_touched.append(f)

        # Collect symbol names for compact display
        sym_names = [gw_sym.get("name", "")]
        for cs in chain_syms:
            sym_names.append(cs.get("name", ""))

        chain_entry: dict = {
            "gateway": gw_id,
            "gateway_name": gw_sym.get("name", ""),
            "kind": gw_kind,
            "label": gw_label,
            "depth": depth_reached,
            "reach": len(chain_syms) + 1,  # +1 for the gateway itself
            "symbols": sym_names,
            "files_touched": files_touched,
            "file_count": len(files_touched),
        }

        # Templates rendered anywhere on this chain (render->view flow edges).
        # Additive: the key is omitted entirely when there are none, so output
        # is byte-identical to pre-flow-edge behavior absent any renders.
        if renders_by_symbol:
            views: list[str] = []
            seen_views: set[str] = set()
            for sid in [gw_id] + [cs["id"] for cs in chain_syms]:
                for tmpl in renders_by_symbol.get(sid, []):
                    if tmpl and tmpl not in seen_views:
                        seen_views.add(tmpl)
                        views.append(tmpl)
            if views:
                chain_entry["views"] = views

        chains.append(chain_entry)

    # Sort chains: http first, then by reach descending
    _kind_order = {"http": 0, "cli": 1, "task": 2, "event": 3, "main": 4, "test": 5}
    chains.sort(key=lambda c: (_kind_order.get(c["kind"], 9), -c["reach"]))

    # ------------------------------------------------------------------
    # Phase 3: lookup mode — filter to chains containing target symbol
    # ------------------------------------------------------------------
    if symbol:
        # Filter chains to those containing the target symbol
        matching_chains: list[dict] = []
        for chain in chains:
            # Check if target is the gateway itself
            if chain["gateway"] == target_id:
                matching_chains.append({
                    "gateway": chain["gateway"],
                    "gateway_name": chain["gateway_name"],
                    "kind": chain["kind"],
                    "label": chain["label"],
                    "depth_from_gateway": 0,
                    "chain_reach": chain["reach"],
                })
                continue

            # Check if target is in the chain symbols
            if target_name in chain["symbols"]:
                # Find depth of target in the BFS
                matching_chains.append({
                    "gateway": chain["gateway"],
                    "gateway_name": chain["gateway_name"],
                    "kind": chain["kind"],
                    "label": chain["label"],
                    "chain_reach": chain["reach"],
                })

        elapsed = (time.perf_counter() - t0) * 1000
        lookup_result = {
            "repo": f"{owner}/{name}",
            "symbol": target_name,
            "symbol_id": target_id,
            "chain_count": len(matching_chains),
            "chains": matching_chains,
            "on_no_chain": len(matching_chains) == 0,
            "_meta": {
                "timing_ms": round(elapsed, 1),
                "total_gateways": len(gateways),
            },
        }
        if include_flow_edges:
            lookup_result["_meta"]["flow_edges"] = flow_summary
        return lookup_result

    # ------------------------------------------------------------------
    # Phase 4: discovery mode — compute orphan stats
    # ------------------------------------------------------------------
    total_fn_method = sum(
        1 for s in index.symbols
        if s.get("kind") in ("function", "method")
    )
    orphan_count = total_fn_method - len(symbol_ids_on_chains)
    orphan_pct = round(100 * orphan_count / total_fn_method, 1) if total_fn_method else 0.0

    # Kind summary
    kind_counts: dict[str, int] = defaultdict(int)
    for c in chains:
        kind_counts[c["kind"]] += 1

    elapsed = (time.perf_counter() - t0) * 1000
    result = {
        "repo": f"{owner}/{name}",
        "gateway_count": len(gateways),
        "chain_count": len(chains),
        "chains": chains,
        "kind_summary": dict(kind_counts),
        "orphan_symbols": orphan_count,
        "orphan_symbol_pct": orphan_pct,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "max_depth": max_depth,
            "include_tests": include_tests,
            "symbols_on_chains": len(symbol_ids_on_chains),
            "total_functions_methods": total_fn_method,
        },
    }
    if include_flow_edges:
        result["_meta"]["flow_edges"] = flow_summary
    return result
