"""get_endpoint_impact — "what breaks if I change this HTTP endpoint?"

Maps an endpoint (HTTP method + URL) to its handler symbol, then composes the
existing impact primitives into one read-only answer:

  * **blast radius** — importers + (optionally) callers of the handler, via
    :func:`get_blast_radius`.
  * **rendered views** — templates the handler renders, via the ``render``
    flow edges from :mod:`flow_edges`.

Endpoint resolution is built on the route coverage the index already exposes,
unifying two sources into one endpoint table:

  * **string-dispatched routes** via :func:`flow_edges.resolve_flow_edges`
    (Django ``path()``, Express ``router.get(p, h)``, Flask ``add_url_rule``,
    Rails ``to:``) — these are invisible to the call graph.
  * **decorator-bound routes** via the same gateway classification
    :mod:`get_signal_chains` uses (Flask / FastAPI ``@app.get``, Spring
    ``@GetMapping``), reusing ``_classify_gateway`` / ``_extract_label``.

Read-only; nothing is persisted. Deeper framework path resolution — FastAPI
``APIRouter(prefix=...)`` / ``include_router`` composition and Spring class-level
``@RequestMapping`` inheritance — is a follow-on that will enrich this same
endpoint table; until then those routes resolve by their local (un-prefixed)
path or via ``handler_symbol_id``.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..retrieval.verdict import symbol_not_found
from ..storage import IndexStore
from ._utils import resolve_repo
from .flow_edges import resolve_flow_edges
from .get_signal_chains import _classify_gateway, _extract_label
from .get_blast_radius import get_blast_radius

logger = logging.getLogger(__name__)

# Verbs that match any requested method (Django path() carries none; add_url_rule
# defaults to ANY; an unspecified query verb matches everything).
_WILDCARD_VERBS = frozenset({"ANY", "PATH", ""})

# Decorator gateway labels are "VERB /path" (see get_signal_chains._extract_label);
# the http:<name> fallback (no extractable path) is intentionally not matched.
_LABEL_RE = re.compile(r"^([A-Z]+)\s+(\S+)")


def _norm_path(p: str) -> str:
    """Normalize a URL path for comparison: leading slash, no trailing slash, lower."""
    if not p:
        return ""
    p = p.strip()
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    return p.lower()


def _parse_endpoint_query(endpoint: str) -> tuple[Optional[str], str]:
    """'GET /users' -> ('GET', '/users'); '/users' -> (None, '/users')."""
    parts = endpoint.strip().split(None, 1)
    if len(parts) == 2 and parts[0].isalpha():
        return parts[0].upper(), _norm_path(parts[1])
    return None, _norm_path(endpoint.strip())


def _collect_endpoints(index, store, owner: str, name: str) -> list[dict]:
    """Unify string-dispatch route edges + decorator gateways into endpoint records."""
    endpoints: list[dict] = []
    seen: set = set()

    # 1) string-dispatched routes (flow edges)
    try:
        edges = resolve_flow_edges(index, store, owner, name, kinds=("route",))
    except Exception:  # pragma: no cover - resolver is best-effort
        logger.debug("resolve_flow_edges(route) failed", exc_info=True)
        edges = []
    for e in edges:
        if e.get("type") != "route->handler":
            continue
        verb = (e.get("verb") or "ANY").upper()
        key = (verb, _norm_path(e.get("path", "")), e.get("dst_id"), e.get("dst_name"))
        if key in seen:
            continue
        seen.add(key)
        endpoints.append({
            "verb": verb,
            "path": e.get("path", ""),
            "handler_id": e.get("dst_id"),
            "handler_name": e.get("dst_name"),
            "handler_file": e.get("dst_file"),
            "source": "flow_edge:" + (e.get("framework_shape") or "route"),
            "resolution": e.get("resolution", "unresolved"),
        })

    # 2) decorator-bound routes (gateway classification)
    for sym in index.symbols:
        if _classify_gateway(sym, None) != "http":
            continue
        m = _LABEL_RE.match(_extract_label(sym, "http") or "")
        if not m:
            continue
        verb, path = m.group(1).upper(), m.group(2)
        key = (verb, _norm_path(path), sym.get("id"), sym.get("name"))
        if key in seen:
            continue
        seen.add(key)
        endpoints.append({
            "verb": verb,
            "path": path,
            "handler_id": sym.get("id"),
            "handler_name": sym.get("name"),
            "handler_file": sym.get("file"),
            "source": "decorator",
            "resolution": "resolved",
        })

    return endpoints


def _match_endpoints(endpoints: list[dict], verb: Optional[str], path: str) -> list[dict]:
    """Match query (verb, normalized path) against the endpoint table.

    Exact path match first; if none, fall back to suffix/containment so a query
    for ``/users`` finds a route registered as ``/api/users`` (and vice versa).
    """
    def _verb_ok(ev: str) -> bool:
        return not verb or ev in _WILDCARD_VERBS or ev == verb

    exact = [e for e in endpoints if _verb_ok(e["verb"]) and _norm_path(e["path"]) == path]
    if exact:
        return exact
    if not path:
        return []
    loose = []
    for e in endpoints:
        if not _verb_ok(e["verb"]):
            continue
        en = _norm_path(e["path"])
        if en and (en.endswith(path) or path.endswith(en) or path in en or en in path):
            loose.append(e)
    return loose


def _impact_for_handler(
    repo: str, handler: dict, render_edges: list[dict], *,
    depth: int, call_depth: int, storage_path: Optional[str],
) -> dict:
    """Compose blast radius + rendered views for one handler symbol."""
    hid = handler.get("handler_id")
    br = get_blast_radius(
        repo, symbol=hid, depth=depth, call_depth=call_depth, storage_path=storage_path,
    )
    if not isinstance(br, dict) or "error" in br:
        br = {}
    views = [
        {"template": r.get("dst_name"), "file": r.get("dst_file")}
        for r in render_edges if r.get("src_id") == hid
    ]
    label = f'{handler.get("verb", "ANY")} {handler.get("path", "")}'.strip()
    return {
        "endpoint": label,
        "handler": {
            "id": hid,
            "name": handler.get("handler_name"),
            "file": handler.get("handler_file"),
        },
        "source": handler.get("source"),
        "affected_files": br.get("confirmed", []),
        "affected_file_count": br.get("confirmed_count", len(br.get("confirmed", []))),
        "callers": br.get("callers", []),
        "caller_count": br.get("caller_count", 0),
        "rendered_views": views,
    }


def _norm_rel(p: str) -> str:
    """Normalize an index-relative file path for set membership."""
    p = (p or "").replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    return p


def _classify_cross_ref_source(source: str) -> tuple:
    """Derive (category, label) from a project-intel cross-ref ``source`` string.

    Shapes emitted by ``_build_cross_references``: ``env:<VAR>``,
    ``compose:<svc>``, ``ci:<file>:<job>``, ``script:<target>``, and Dockerfile
    ``<rel>:ENTRYPOINT`` / ``<rel>:COPY``.
    """
    for prefix in ("env", "compose", "ci", "script"):
        if source.startswith(prefix + ":"):
            return prefix, source[len(prefix) + 1:]
    if source.endswith(":ENTRYPOINT") or source.endswith(":COPY"):
        return "docker", source
    return "other", source


def _downstream_item(cr: dict) -> dict:
    cat, label = _classify_cross_ref_source(cr.get("source", ""))
    return {
        "category": cat,
        "label": label,
        "source": cr.get("source"),
        "target_file": cr.get("target_file"),
        "type": cr.get("type"),
    }


def _ctx_contains(ctx: str, blast: set) -> bool:
    """True when a compose build_context directory contains a blast-radius file."""
    ctx = _norm_rel(ctx).strip("/")
    if ctx in ("", "."):
        return True  # repo-root context builds everything
    return any(b == ctx or b.startswith(ctx + "/") for b in blast)


def _image_repo(img: str) -> str:
    """Image reference without tag/digest, so 'myapp:latest' links to 'myapp:v2'."""
    img = (img or "").split("@", 1)[0]
    slash = img.rfind("/")
    colon = img.rfind(":")
    if colon > slash:
        img = img[:colon]
    return img


def _impact_endpoint_path(impact: dict) -> str:
    """URL path from the impact's 'VERB /path' label ('' when unresolvable)."""
    parts = (impact.get("endpoint") or "").split(" ", 1)
    if len(parts) == 2 and parts[1].startswith("/"):
        return parts[1]
    return ""


def _exposes_for_impact(blast: set, endpoint_path: str, infra: dict) -> list:
    """Upstream exposure links for one impact, anchored to real evidence only.

    Anchors, strongest first:
    - compose service whose build_context contains a blast-radius file (its
      image also anchors K8s workloads by image repository);
    - Ingress path rule literally naming the endpoint path (the only
      endpoint-level evidence any manifest encodes -> precision ingress_path);
    - Service selected into the chain (selector matches an anchored workload's
      pod labels, or it backs a path-matched Ingress rule).
    Everything else carries precision host_port: the manifest exposes the app,
    not this specific route. Ambiguous resources are skipped, not guessed.
    """
    exposes: list = []
    seen: set = set()

    def _emit(item: dict) -> None:
        key = (item["kind"], item["label"])
        if key not in seen:
            seen.add(key)
            exposes.append(item)

    anchored_images: set = set()
    for svc in infra.get("compose_services") or []:
        ctx = svc.get("build_context")
        if not ctx or not _ctx_contains(ctx, blast):
            continue
        if svc.get("image"):
            anchored_images.add(_image_repo(svc["image"]))
        if svc.get("ports"):
            _emit({"kind": "compose_port", "label": svc.get("name", ""),
                   "ports": svc["ports"], "precision": "host_port"})

    k8s = infra.get("k8s_resources") or []
    ep = _norm_path(endpoint_path)

    anchored_labels: list = []
    for res in k8s:
        if res.get("kind") in ("Service", "Ingress") or not res.get("labels"):
            continue
        if any(_image_repo(i) in anchored_images for i in res.get("images") or []):
            anchored_labels.append(res["labels"])

    path_backends: set = set()
    ingress_rules: list = []  # (resource, rule, path_matched)
    for res in k8s:
        if res.get("kind") != "Ingress":
            continue
        for rule in res.get("ingress_rules") or []:
            rp = _norm_path(rule.get("path") or "")
            matched = bool(ep) and rp not in ("", "/") and (
                ep == rp or ep.startswith(rp + "/"))
            if matched and rule.get("service"):
                path_backends.add(rule["service"])
            ingress_rules.append((res, rule, matched))

    anchored_services: set = set(path_backends)
    for res in k8s:
        if res.get("kind") != "Service":
            continue
        sel = res.get("selector") or {}
        sel_hit = bool(sel) and any(sel.items() <= lb.items() for lb in anchored_labels)
        if sel_hit or res.get("name") in path_backends:
            anchored_services.add(res.get("name"))
            _emit({"kind": "k8s_service", "label": res.get("name", ""),
                   "file": res.get("file"), "ports": res.get("ports", []),
                   "precision": "host_port"})

    # Path-matched rules first so dedupe never downgrades ingress_path.
    for res, rule, matched in sorted(ingress_rules, key=lambda t: not t[2]):
        if matched:
            _emit({"kind": "k8s_ingress", "label": res.get("name", ""),
                   "file": res.get("file"), "host": rule.get("host"),
                   "path": rule.get("path"), "precision": "ingress_path"})
        elif rule.get("service") and rule["service"] in anchored_services:
            _emit({"kind": "k8s_ingress", "label": res.get("name", ""),
                   "file": res.get("file"), "host": rule.get("host"),
                   "path": rule.get("path"), "precision": "host_port"})

    return exposes[:20]


_EXPOSES_HONEST_NOTE = (
    "precision=host_port exposes the app serving this endpoint, not this "
    "specific route; only ingress_path means the manifest names this path."
)


def _infra_for_impact(
    impact: dict, cross_refs: list,
    infra_discoveries: Optional[dict] = None, endpoint_path: str = "",
) -> dict:
    """Intersect project-intel cross-refs against one impact's blast-radius files.

    Downstream links are file-granular (a cross-ref names a file the endpoint's
    blast radius contains, not a specific symbol); the ``source``/``type``
    fields carry that evidence granularity honestly. A COPY/build_context
    cross-ref targets a DIRECTORY, so prefix matches count too. When
    ``infra_discoveries`` (project-intel infra category) is provided, upstream
    exposure links are fused into ``exposes`` via :func:`_exposes_for_impact`.
    """
    blast: set = set()
    handler_file = (impact.get("handler") or {}).get("file")
    if handler_file:
        blast.add(_norm_rel(handler_file))
    for f in impact.get("affected_files", []):
        blast.add(_norm_rel(f))
    for v in impact.get("rendered_views", []):
        if v.get("file"):
            blast.add(_norm_rel(v["file"]))
    blast.discard("")

    downstream: list = []
    seen: set = set()
    for cr in cross_refs:
        target = _norm_rel(cr.get("target_file", ""))
        if not target:
            continue
        hit = target in blast or any(b.startswith(target + "/") for b in blast)
        if not hit:
            continue
        item = _downstream_item(cr)
        key = (item["category"], item["label"])
        if key in seen:
            continue
        seen.add(key)
        downstream.append(item)

    exposes = _exposes_for_impact(blast, endpoint_path, infra_discoveries or {})
    meta = {
        "cross_refs_scanned": len(cross_refs),
        "blast_radius_files": len(blast),
    }
    if exposes:
        meta["honest_note"] = _EXPOSES_HONEST_NOTE
    return {"downstream": downstream, "exposes": exposes, "_meta": meta}


def get_endpoint_impact(
    repo: str,
    endpoint: Optional[str] = None,
    handler_symbol_id: Optional[str] = None,
    depth: int = 1,
    call_depth: int = 2,
    include_infra: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Endpoint-centric impact analysis. Read-only.

    Args:
        repo:              Repository identifier (owner/repo or just repo name).
        endpoint:          HTTP endpoint, e.g. ``"GET /users"`` or ``"/users"``
                           (verb optional). Matched against the resolved route
                           table.
        handler_symbol_id: Alternative to ``endpoint`` — analyse a handler symbol
                           directly (use when a route's full path isn't yet
                           resolvable, e.g. prefixed FastAPI/Spring routes).
        depth:             Import hops for blast radius (1 = direct importers).
        call_depth:        Call-graph hops for caller detection (0 disables).
        include_infra:     Attach an ``infra`` block per impact: project-intel
                           cross-references (env vars, compose services,
                           Dockerfiles, CI jobs, scripts) intersected against
                           the endpoint's blast-radius file set (downstream),
                           plus what exposes the app serving the endpoint
                           (compose ports, K8s Service/Ingress) with explicit
                           precision - host_port unless an Ingress path rule
                           literally names the route (ingress_path).
                           File-granular evidence, honestly labelled. Default
                           off = output identical to prior releases.
        storage_path:      Custom storage path.

    Returns:
        ``{repo, query, matched_endpoints, impacts, _meta}`` — one ``impacts``
        entry per distinct handler. Honest empty result + hint when nothing
        matches.
    """
    try:
        owner, name = resolve_repo(repo, storage_path)
    except Exception as e:
        return {"error": str(e)}
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}
    if not endpoint and not handler_symbol_id:
        return {"error": "Provide either 'endpoint' (e.g. 'GET /users') or 'handler_symbol_id'."}

    endpoints = _collect_endpoints(index, store, owner, name)

    if handler_symbol_id:
        matched = [e for e in endpoints if e.get("handler_id") == handler_symbol_id]
        if not matched:
            sym = next((s for s in index.symbols if s.get("id") == handler_symbol_id), None)
            if sym is None:
                return {**symbol_not_found(handler_symbol_id, index.symbols), "matched_endpoints": []}
            matched = [{
                "verb": "ANY", "path": "",
                "handler_id": sym.get("id"), "handler_name": sym.get("name"),
                "handler_file": sym.get("file"),
                "source": "handler_symbol_id", "resolution": "resolved",
            }]
        query = {"handler_symbol_id": handler_symbol_id}
    else:
        verb, path = _parse_endpoint_query(endpoint)
        matched = _match_endpoints(endpoints, verb, path)
        query = {"endpoint": endpoint}
        if not matched:
            return {
                "repo": f"{owner}/{name}",
                "query": query,
                "matched_endpoints": [],
                "hint": (
                    "No route matched. Resolution covers string-dispatch "
                    "(Django/Express/Flask/Rails) + decorator routes "
                    "(Flask/FastAPI/Spring local path). FastAPI APIRouter prefix "
                    "composition and Spring class-level mappings are not yet "
                    "resolved — try the handler directly via handler_symbol_id, "
                    "or query by a path suffix."
                ),
                "_meta": {"endpoints_known": len(endpoints)},
            }

    try:
        render_edges = resolve_flow_edges(index, store, owner, name, kinds=("render",))
    except Exception:  # pragma: no cover
        logger.debug("resolve_flow_edges(render) failed", exc_info=True)
        render_edges = []

    impacts: list[dict] = []
    seen_handlers: set = set()
    for e in matched:
        hid = e.get("handler_id")
        if not hid or hid in seen_handlers:
            continue
        seen_handlers.add(hid)
        impacts.append(_impact_for_handler(
            repo, e, render_edges, depth=depth, call_depth=call_depth,
            storage_path=storage_path,
        ))

    if include_infra:
        source_root = getattr(index, "source_root", "") or ""
        import os  # noqa: PLC0415
        if not os.path.isdir(source_root):
            for imp in impacts:
                imp["infra"] = {
                    "downstream": [], "exposes": [],
                    "_meta": {"reason": "no_local_source_root"},
                }
        else:
            from .get_project_intel import collect_project_intel  # noqa: PLC0415
            try:
                collected = collect_project_intel(
                    index, source_root, cats=["infra", "config", "ci", "deps"],
                )
                cross_refs = collected["cross_references"]
                infra_disc = (collected.get("discoveries") or {}).get("infra") or {}
            except Exception:  # pragma: no cover - fusion is best-effort
                logger.debug("collect_project_intel failed", exc_info=True)
                cross_refs = []
                infra_disc = {}
            for imp in impacts:
                imp["infra"] = _infra_for_impact(
                    imp, cross_refs, infra_disc, _impact_endpoint_path(imp),
                )

    return {
        "repo": f"{owner}/{name}",
        "query": query,
        "matched_endpoints": [
            {k: e.get(k) for k in
             ("verb", "path", "handler_id", "handler_name", "handler_file", "source", "resolution")}
            for e in matched
        ],
        "impacts": impacts,
        "_meta": {"endpoints_known": len(endpoints), "handler_count": len(impacts)},
    }
