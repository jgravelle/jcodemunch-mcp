"""Find concrete implementations of an interface, abstract class, or method.

Multi-source resolution across four channels, each scored by confidence:
  - LSP dispatch (1.0)        — interface/trait dispatch_edges from the LSP bridge
  - AST class hierarchy (0.85) — subclasses that override the method (or class subtypes)
  - Duck-typed (0.65)         — classes with a matching method name and no declared inheritance
  - Decorator handler (0.45)  — @decorator-registered handlers (route/cli/signal/event)

Goes beyond a flat list of implementations: classifies each impl, ranks by PageRank,
attaches a ``differs_by`` breakdown, and supports cross-repo discovery via the
package registry.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from ..parser.symbols import STATE_KINDS
from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import symbol_span_bytes
from ._utils import index_status_to_tool_error, resolve_repo
from .get_class_hierarchy import _build_class_maps
from ._scip_consume import open_scip_reader, scip_meta_and_stale, scip_meta_block

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence tiers
# ---------------------------------------------------------------------------
_CONF_LSP = 1.0
_CONF_SCIP = 1.0   # compile-time SCIP evidence — compiler-grade, on par with LSP
_CONF_AST = 0.85
_CONF_DUCK = 0.65
_CONF_DECORATOR = 0.45


def _scip_implementation_ids(store, owner: str, name: str, target_id: str):
    """Compiler-verified implementations of ``target_id`` from ingested SCIP data.

    SCIP stores each ``A implements B`` relationship as an edge
    ``(impl_id, iface_id, "implementation")``, so the implementations of an
    interface are the ``from_symbol_id``s of the implementation edges pointing at
    it. Returns ``(impl_ids, scip_meta, stale)``; honest-empty ``([], {}, False)``
    when no SCIP data has been ingested (including pre-v17 databases).
    """
    if not target_id:
        return [], {}, False
    conn = open_scip_reader(store, owner, name)
    if conn is None:
        return [], {}, False
    try:
        scip_meta, stale = scip_meta_and_stale(conn)
        rows = conn.execute(
            """
            SELECT from_symbol_id AS impl_id
            FROM scip_edges
            WHERE kind = 'implementation' AND to_symbol_id = ?
            GROUP BY from_symbol_id
            """,
            (target_id,),
        ).fetchall()
        impl_ids = [
            r["impl_id"] for r in rows
            if r["impl_id"] and r["impl_id"] != target_id
        ]
        return impl_ids, scip_meta, stale
    except Exception:
        return [], {}, False
    finally:
        conn.close()

# Decorators that suggest "this is implementing a registered handler protocol"
_HANDLER_DECORATOR_PATTERNS = re.compile(
    r"\b(route|get|post|put|patch|delete|command|task|signal|event|listener|"
    r"handler|subscribe|on|receiver|websocket)\b",
    re.IGNORECASE,
)

# Valid relationship kinds we emit
_RELATIONSHIP_KINDS = frozenset({
    "subclass_override", "interface_impl", "duck_typed", "decorator_handler", "subclass",
})


def _has_handler_decorator(sym: dict) -> Optional[str]:
    """Return the matching decorator name when sym has a handler-pattern decorator."""
    decorators = sym.get("decorators") or []
    if isinstance(decorators, str):
        decorators = [decorators]
    for dec in decorators:
        dec_str = str(dec) if not isinstance(dec, dict) else (dec.get("name") or "")
        if dec_str and _HANDLER_DECORATOR_PATTERNS.search(dec_str):
            return dec_str
    return None


def _differs_by(target: dict, impl: dict) -> list[str]:
    """One-line divergence breakdown vs. the target symbol."""
    out: list[str] = []
    bt = int(target.get("byte_length", 0) or 0)
    bi = int(impl.get("byte_length", 0) or 0)
    if bt and bi:
        delta = abs(bt - bi)
        out.append("body: identical size" if delta <= 5 else f"body: ±{delta} bytes")
    pt = int(target.get("param_count", 0) or 0)
    pi = int(impl.get("param_count", 0) or 0)
    if pt == pi:
        out.append(f"params: {pt} (match)")
    else:
        out.append(f"params: {pt} vs {pi}")
    # Compare callee sets
    refs_t = set(target.get("call_references") or [])
    refs_i = set(impl.get("call_references") or [])
    if refs_t or refs_i:
        shared = len(refs_t & refs_i)
        unique = len(refs_t ^ refs_i)
        out.append(f"callees: {shared} shared, {unique} unique")
    return out


def _normalize_decorators_list(sym: dict) -> list[str]:
    decs = sym.get("decorators") or []
    if isinstance(decs, str):
        return [decs]
    out: list[str] = []
    for d in decs:
        if isinstance(d, dict):
            n = d.get("name") or d.get("decorator") or ""
        else:
            n = str(d)
        if n:
            out.append(n)
    return out


#: Which same-named symbol the user means: an implementable kind first. Every
#: state kind ranks where `constant` does, derived from `KIND_ORDER` (#806); a
#: `field` or `property` fell to the default behind `template` before.
_RESOLVE_KIND_RANK: dict[str, int] = {
    **{kind: 4 for kind in STATE_KINDS},
    "class": 0, "type": 1, "method": 2, "function": 3,
    "template": 5, "import": 9,
}


def _resolve_target_symbol(index, symbol: str) -> Optional[dict]:
    """Resolve a user-supplied symbol string to a single index symbol dict."""
    # Try exact id match first
    for sym in index.symbols:
        if sym.get("id") == symbol:
            return sym
    # Try exact name match — prefer class/method kinds over imports
    candidates = [s for s in index.symbols if s.get("name") == symbol]
    if not candidates:
        return None
    # Prioritise non-import kinds; among those prefer class/method
    def _rank(s: dict) -> tuple:
        kind = s.get("kind", "")
        kind_pri = _RESOLVE_KIND_RANK.get(kind, 8)
        return (kind_pri, -int(s.get("byte_length", 0) or 0))
    candidates.sort(key=_rank)
    return candidates[0]


def find_implementations(
    repo: str,
    symbol: str,
    relationship_kinds: Optional[list] = None,
    include_subclasses: bool = True,
    cross_repo: bool = False,
    rank_by_importance: bool = True,
    max_results: int = 50,
    token_budget: int = 4000,
    storage_path: Optional[str] = None,
) -> dict:
    """Find concrete implementations of an interface, abstract class, or method.

    Args:
        repo: Repository identifier.
        symbol: Symbol id or name of the interface/abstract/method to analyse.
        relationship_kinds: Optional whitelist of kinds to emit. Defaults to all.
            Recognised: subclass_override, interface_impl, duck_typed,
            decorator_handler, subclass.
        include_subclasses: When True, walk the class hierarchy for class-kind targets.
        cross_repo: When True, also search other indexed repos via the package registry.
        rank_by_importance: When True, sort results by PageRank × byte_length.
        max_results: Cap on returned implementations (default 50).
        token_budget: Hard cap on response payload (default 4000).
        storage_path: Custom storage path.

    Returns:
        Dict with ``implementations`` list and summary fields.
    """
    start = time.perf_counter()

    if max_results < 1:
        return {"error": "max_results must be >= 1"}
    if token_budget < 1:
        return {"error": "token_budget must be >= 1"}

    kinds_whitelist = (
        set(relationship_kinds) & _RELATIONSHIP_KINDS
        if relationship_kinds else _RELATIONSHIP_KINDS
    )

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    target = _resolve_target_symbol(index, symbol)
    if target is None:
        return {"error": f"Symbol not found: {symbol}"}

    target_name = target.get("name", "")
    target_kind = target.get("kind", "")
    target_id = target.get("id", "")

    # ── Channel 1: LSP dispatch edges ──────────────────────────────────
    dispatch_edges = (
        (getattr(index, "context_metadata", None) or {}).get("dispatch_edges", [])
        if hasattr(index, "context_metadata") else []
    )

    impls_by_id: dict[str, dict] = {}

    def _add_impl(sym: dict, *, kind: str, confidence: float, source: str,
                  via: Optional[str] = None, verification: Optional[str] = None) -> None:
        if kind not in kinds_whitelist:
            return
        sid = sym.get("id", "")
        if not sid:
            return
        # Prefer the higher-confidence channel when seen multiple ways
        prev = impls_by_id.get(sid)
        if prev is not None and prev["confidence"] >= confidence:
            return
        rec = {
            "symbol_id": sid,
            "name": sym.get("name", ""),
            "kind": sym.get("kind", ""),
            "file": sym.get("file", ""),
            "line": sym.get("line", 0),
            "relationship": kind,
            "confidence": confidence,
            "source": source,
            "byte_length": int(sym.get("byte_length", 0) or 0),
            "differs_by": _differs_by(target, sym),
        }
        if via:
            rec["via"] = via
        if verification:
            rec["verification"] = verification
        decs = _normalize_decorators_list(sym)
        if decs:
            rec["decorators"] = decs[:4]
        impls_by_id[sid] = rec

    # Build a name → symbol lookup once for resolution speed
    sym_by_name: dict[str, list[dict]] = {}
    sym_by_id: dict[str, dict] = {}
    for sym in index.symbols:
        n = sym.get("name", "")
        if n:
            sym_by_name.setdefault(n, []).append(sym)
        sid = sym.get("id", "")
        if sid:
            sym_by_id.setdefault(sid, sym)
    symbols_by_file: dict[str, list[dict]] = {}
    for sym in index.symbols:
        f = sym.get("file", "")
        if f:
            symbols_by_file.setdefault(f, []).append(sym)

    if dispatch_edges:
        for edge in dispatch_edges:
            iface_name = edge.get("interface_name", "")
            method_name = edge.get("method_name", "")
            impl_name = edge.get("impl_name", "")
            impl_file = edge.get("impl_file", "")
            impl_line = edge.get("impl_line", 0)
            if not impl_file:
                continue

            # Match: target is the interface itself, or the interface method
            iface_method_id = f"{iface_name}.{method_name}" if iface_name and method_name else ""
            target_is_iface = (
                target_name == iface_name
                or target_name == method_name
                or target_id == iface_method_id
            )
            if not target_is_iface:
                continue

            # Find the impl symbol in the file
            for cand in symbols_by_file.get(impl_file, []):
                cn = cand.get("name", "")
                cl = cand.get("line", 0)
                if (impl_line and cl == impl_line) or (impl_name and cn == impl_name) or cn == method_name:
                    _add_impl(
                        cand,
                        kind="interface_impl",
                        confidence=_CONF_LSP,
                        source="lsp_dispatch",
                        via=iface_name,
                    )
                    break

    # ── Channel 2: AST class hierarchy ─────────────────────────────────
    class_by_name, children_of = _build_class_maps(index.symbols)

    if target_kind in ("class", "type") and include_subclasses:
        # BFS down the class tree
        from collections import deque  # noqa: PLC0415
        visited: set[str] = {target_name}
        queue: deque = deque(children_of.get(target_name, []))
        while queue:
            child_name = queue.popleft()
            if child_name in visited:
                continue
            visited.add(child_name)
            child_sym = class_by_name.get(child_name)
            if child_sym is not None:
                _add_impl(
                    child_sym, kind="subclass",
                    confidence=_CONF_AST, source="class_hierarchy",
                )
            for grand in children_of.get(child_name, []):
                if grand not in visited:
                    queue.append(grand)

    # ── Channel 3: subclass_override + duck_typed for methods ──────────
    if target_kind == "method" or target_kind == "function":
        # Find classes that have a method of the same name
        # First: subclass overrides where parent is the containing class of target
        parent_id = target.get("parent")
        parent_class_name = None
        if parent_id:
            for sym in index.symbols:
                if sym.get("id") == parent_id and sym.get("kind") in ("class", "type"):
                    parent_class_name = sym.get("name", "")
                    break

        # All same-named methods/functions across the index
        same_named = sym_by_name.get(target_name, [])
        for cand in same_named:
            if cand.get("id") == target_id:
                continue
            cand_kind = cand.get("kind", "")
            if cand_kind not in ("method", "function"):
                continue
            # Try to find this candidate's enclosing class
            cand_parent_id = cand.get("parent")
            cand_class_name = None
            if cand_parent_id:
                for s in index.symbols:
                    if s.get("id") == cand_parent_id and s.get("kind") in ("class", "type"):
                        cand_class_name = s.get("name", "")
                        break

            if parent_class_name and cand_class_name:
                # Is cand_class a subclass of parent_class? Walk children_of.
                def _is_subclass(child: str, ancestor: str, _depth: int = 0) -> bool:
                    if _depth > 12:
                        return False
                    if child == ancestor:
                        return True
                    for c in children_of.get(ancestor, []):
                        if c == child or _is_subclass(child, c, _depth + 1):
                            return True
                    return False

                if _is_subclass(cand_class_name, parent_class_name):
                    _add_impl(
                        cand, kind="subclass_override",
                        confidence=_CONF_AST, source="class_hierarchy",
                        via=cand_class_name,
                    )
                    continue

            # No declared inheritance → duck-typed
            _add_impl(
                cand, kind="duck_typed",
                confidence=_CONF_DUCK, source="name_match",
                via=cand_class_name or None,
            )

    # ── Channel 4: decorator handlers ──────────────────────────────────
    # Symbols with handler-style decorators implementing a protocol named or
    # closely matching the target.
    target_lc = target_name.lower()
    for sym in index.symbols:
        sid = sym.get("id", "")
        if sid == target_id:
            continue
        if sym.get("kind") not in ("function", "method"):
            continue
        decs = _normalize_decorators_list(sym)
        if not decs:
            continue
        match_dec = None
        for d in decs:
            if _HANDLER_DECORATOR_PATTERNS.search(d) and target_lc in d.lower():
                match_dec = d
                break
        # Also catch the case where the decorator is exactly the target name
        if not match_dec:
            for d in decs:
                # @target_name(...) or @target_name
                if re.match(rf"@?{re.escape(target_name)}\b", d):
                    match_dec = d
                    break
        if match_dec:
            _add_impl(
                sym, kind="decorator_handler",
                confidence=_CONF_DECORATOR, source="decorator_match",
                via=match_dec,
            )

    # ── Channel 5: SCIP compiler-verified implementations ──────────────
    # Static compile-time evidence (import-scip): impl→iface edges give the
    # concrete implementations the compiler proved for this interface. 1.0
    # confidence, on par with live LSP dispatch, and it surfaces implementations
    # the AST/duck channels miss (e.g. an interface with no declared subclassing).
    # Honest no-op without ingested SCIP. Wins the dedup over lower-confidence
    # channels, tagging the record compiler_verified.
    scip_block: Optional[dict] = None
    _scip_impl_ids, _scip_meta, _scip_stale = _scip_implementation_ids(
        store, owner, name, target_id
    )
    _scip_proven = 0
    for _impl_id in _scip_impl_ids:
        _impl_sym = sym_by_id.get(_impl_id)
        if _impl_sym is None or _impl_sym.get("id") == target_id:
            continue
        _scip_proven += 1
        _add_impl(
            _impl_sym, kind="interface_impl", confidence=_CONF_SCIP,
            source="scip", via=target_name or None,
            verification="compiler_verified",
        )
    if _scip_proven:
        scip_block = scip_meta_block(_scip_meta, _scip_stale, implementations=_scip_proven)

    # ── Optional: cross-repo discovery ─────────────────────────────────
    cross_repo_count = 0
    if cross_repo:
        try:
            from .package_registry import build_package_registry  # noqa: PLC0415
            registry = build_package_registry(store.list_repos())
            # Find other repos that import this repo's package(s)
            our_packages = {pkg for pkg, repos in registry.items() if f"{owner}/{name}" in repos}
            other_repos = [
                rid for rid in {r for repos in registry.values() for r in repos}
                if rid != f"{owner}/{name}"
            ]
            for other_id in other_repos:
                try:
                    o_owner, o_name = other_id.split("/", 1)
                    other_idx = store.load_index(o_owner, o_name)
                    if not other_idx:
                        continue
                    # Does the other repo actually depend on us?
                    other_imports = getattr(other_idx, "imports", {}) or {}
                    depends = False
                    for _src, edges in other_imports.items():
                        for edge in edges:
                            spec = (edge.get("specifier") or "").lower()
                            if any(pkg.lower() in spec for pkg in our_packages):
                                depends = True
                                break
                        if depends:
                            break
                    if not depends:
                        continue
                    # Look for same-named subclasses/methods in the dependent repo
                    other_class_by_name, other_children = _build_class_maps(other_idx.symbols)
                    other_sym_by_name: dict[str, list[dict]] = {}
                    for s in other_idx.symbols:
                        n = s.get("name", "")
                        if n:
                            other_sym_by_name.setdefault(n, []).append(s)
                    # Class case: subclasses of target
                    if target_kind in ("class", "type"):
                        for cls_name, child_list in other_children.items():
                            if cls_name == target_name:
                                for child in child_list:
                                    csym = other_class_by_name.get(child)
                                    if csym:
                                        rec = {
                                            "symbol_id": csym["id"],
                                            "name": csym.get("name", ""),
                                            "kind": csym.get("kind", ""),
                                            "file": csym.get("file", ""),
                                            "line": csym.get("line", 0),
                                            "relationship": "subclass",
                                            "confidence": _CONF_AST,
                                            "source": "class_hierarchy",
                                            "cross_repo": True,
                                            "source_repo": other_id,
                                            "byte_length": int(csym.get("byte_length", 0) or 0),
                                        }
                                        impls_by_id[csym["id"]] = rec
                                        cross_repo_count += 1
                    # Method case: same-named methods in subclasses (heuristic)
                    if target_kind in ("method", "function"):
                        for sym in other_sym_by_name.get(target_name, []):
                            if sym.get("kind") in ("method", "function"):
                                rec = {
                                    "symbol_id": sym["id"],
                                    "name": sym.get("name", ""),
                                    "kind": sym.get("kind", ""),
                                    "file": sym.get("file", ""),
                                    "line": sym.get("line", 0),
                                    "relationship": "duck_typed",
                                    "confidence": _CONF_DUCK,
                                    "source": "name_match",
                                    "cross_repo": True,
                                    "source_repo": other_id,
                                    "byte_length": int(sym.get("byte_length", 0) or 0),
                                }
                                impls_by_id[sym["id"]] = rec
                                cross_repo_count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("find_implementations: cross-repo lookup failed for %s: %s",
                                 other_id, exc, exc_info=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("find_implementations: cross-repo discovery skipped: %s", exc, exc_info=True)

    # ── Rank + truncate ────────────────────────────────────────────────
    impls = list(impls_by_id.values())

    pagerank: dict[str, float] = {}
    if rank_by_importance:
        cache = getattr(index, "_bm25_cache", None) or {}
        if "pagerank" in cache:
            pagerank = cache["pagerank"]
        else:
            try:
                import threading  # noqa: PLC0415
                with getattr(index, "_bm25_lock", None) or threading.Lock():
                    if "pagerank" in cache:
                        pagerank = cache["pagerank"]
                    else:
                        from .pagerank import compute_pagerank  # noqa: PLC0415
                        pr, _ = compute_pagerank(
                            index.imports or {}, index.source_files, index.alias_map,
                            psr4_map=getattr(index, "psr4_map", None),
                        )
                        cache["pagerank"] = pr
                        pagerank = pr
            except Exception as exc:  # noqa: BLE001
                logger.debug("find_implementations: pagerank skipped: %s", exc, exc_info=True)

    def _impl_rank(rec: dict) -> tuple:
        # Higher confidence first, then higher PageRank of containing file,
        # then larger body.
        conf = float(rec.get("confidence", 0))
        pr = pagerank.get(rec.get("file", ""), 0.0)
        bl = int(rec.get("byte_length", 0))
        return (-conf, -pr, -bl, rec.get("symbol_id", ""))

    impls.sort(key=_impl_rank)
    impls = impls[:max_results]

    # Token-pack — each record is ~50 tokens
    out: list[dict] = []
    total_tokens = 0
    per_record_cost = 50
    for rec in impls:
        if total_tokens + per_record_cost > token_budget and out:
            break
        out.append(rec)
        total_tokens += per_record_cost

    # Verdict counts by relationship
    relationship_counts: dict[str, int] = {}
    for r in out:
        relationship_counts[r["relationship"]] = relationship_counts.get(r["relationship"], 0) + 1

    # Token-savings ledger
    raw_bytes = symbol_span_bytes(index.symbols)
    response_bytes = total_tokens * 4
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="find_implementations")

    elapsed = (time.perf_counter() - start) * 1000
    result = {
        "implementations": out,
        "implementations_returned": len(out),
        "target": {
            "symbol_id": target_id,
            "name": target_name,
            "kind": target_kind,
            "file": target.get("file", ""),
            "line": target.get("line", 0),
        },
        "relationship_counts": relationship_counts,
        "total_tokens": total_tokens,
        "budget_tokens": token_budget,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    from ..retrieval.provenance import channel_provenance
    result["_meta"]["confidence_provenance"] = channel_provenance("find_implementations")
    if scip_block is not None:
        result["_meta"]["scip"] = scip_block
    if cross_repo:
        result["cross_repo_count"] = cross_repo_count
    if not dispatch_edges and target_kind in ("method", "function"):
        result["note"] = (
            "No LSP dispatch_edges available — implementations resolved via AST + duck-typed "
            "fallbacks. Enable the LSP bridge for 1.0-confidence interface dispatch."
        )
    return result
