"""Surface the de-facto API contracts shared across a group of repos.

For each symbol exported by one group member and imported by ≥N other members,
classify the contract into one of four verdict tiers, attach stability metrics,
breaking-change history, and runtime evidence (when traces exist).

Verdict tiers:
  - de_facto_api          — symbol is used by ≥min_importers external repos
  - leaky_internal        — symbol declared internal (underscore prefix or
                            ``_internal/`` directory) but imported externally
  - dead_contract         — symbol declared public but imported by zero externals
  - version_skew          — same symbol name imported via multiple specifier roots
                            (e.g. direct path vs. re-export)

Pairs with ``get_cross_repo_map`` — that gives the repo-level dep graph;
``get_group_contracts`` zooms in to the symbol-level shared surface.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ..runtime.confidence import symbol_hit_count
from .package_registry import build_package_registry, extract_root_package_from_specifier
from ._utils import symbol_span_bytes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
_DEFAULT_MIN_IMPORTERS = 2
_DEFAULT_MAX_CONTRACTS = 50
_DEFAULT_TOKEN_BUDGET = 4000
_DEFAULT_CHURN_DAYS = 90

# Heuristics for "declared internal"
_INTERNAL_PATH_FRAGMENTS = ("_internal", "/internal/", "\\internal\\", "/private/", "\\private\\")


def _looks_internal(symbol_name: str, file_path: str) -> bool:
    """Heuristic: is this symbol declared internal by convention?"""
    if symbol_name.startswith("_") and not symbol_name.startswith("__"):
        return True
    fl = (file_path or "").lower()
    return any(frag in fl for frag in _INTERNAL_PATH_FRAGMENTS)


def _normalize_repo_id(target: str, all_repos_raw: list[dict]) -> Optional[str]:
    """Match a user-supplied repo string against indexed repo IDs."""
    for r in all_repos_raw:
        rid = r.get("repo", "")
        if rid == target:
            return rid
        if rid.endswith("/" + target):
            return rid
        # Try display name match
        if r.get("display_name") == target:
            return rid
    return None


def _churn_for_symbol(
    store: IndexStore, owner: str, name: str, file_path: str, since_days: int,
    storage_path: Optional[str],
) -> int:
    """Best-effort churn = commit count for the file over the window."""
    try:
        from .get_churn_rate import get_churn_rate  # noqa: PLC0415
        out = get_churn_rate(
            f"{owner}/{name}", target=file_path, days=since_days,
            storage_path=storage_path,
        )
        return int(out.get("commits", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_group_contracts: churn lookup failed for %s/%s:%s: %s",
                     owner, name, file_path, exc, exc_info=True)
        return 0


def _last_breaking_change(symbol_id: str, owner: str, name: str, storage_path: Optional[str]) -> Optional[str]:
    """Best-effort last breaking change date from get_symbol_provenance."""
    try:
        from .get_symbol_provenance import get_symbol_provenance  # noqa: PLC0415
        out = get_symbol_provenance(
            f"{owner}/{name}", symbol=symbol_id, storage_path=storage_path,
        )
        commits = out.get("commits", []) if isinstance(out, dict) else []
        # Walk newest-first looking for refactor/rename — those are the
        # signal-bearing breaking-change shapes the provenance classifier emits.
        for c in commits:
            kind = (c.get("classification") or "").lower()
            if kind in {"refactor", "rename", "revert", "feature"}:
                return c.get("date") or c.get("authored") or c.get("committed")
        # Fall back to the first non-creation commit, if any
        for c in commits[1:]:
            return c.get("date") or c.get("authored")
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_group_contracts: provenance lookup failed for %s: %s",
                     symbol_id, exc, exc_info=True)
    return None


def _runtime_hits_for(
    store: IndexStore, owner: str, name: str, symbol_id: str,
) -> Optional[int]:
    """Best-effort runtime hit count over the indexed trace window.

    Delegates to the one reader (#717); a local copy of this query is how
    `hit_count` outlived the schema that never had it.
    """
    try:
        db_path = store._sqlite._db_path(owner, name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_runtime_hits_for: db path unavailable: %s", exc, exc_info=True)
        return None
    return symbol_hit_count(db_path, symbol_id)


def get_group_contracts(
    repos: list,
    min_importers: int = _DEFAULT_MIN_IMPORTERS,
    include_internal: bool = True,
    include_dead_contracts: bool = False,
    classify: bool = True,
    churn_days: int = _DEFAULT_CHURN_DAYS,
    max_contracts: int = _DEFAULT_MAX_CONTRACTS,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    storage_path: Optional[str] = None,
) -> dict:
    """Surface the de-facto API contracts shared across a group of repos.

    For every symbol exported by a group member and imported by ≥min_importers
    other group members, returns a classified contract entry with stability +
    breaking-change history + runtime evidence.

    Args:
        repos: List of indexed repo IDs (owner/name or bare names). Treated as the
            group. Must contain at least 2 repos.
        min_importers: Minimum distinct external repo importers to surface a contract
            (default 2).
        include_internal: When True (default), surface leaky_internal contracts —
            symbols declared internal but imported externally (architecture violations).
        include_dead_contracts: When True, also surface public symbols with zero
            external importers (default False — uncommon use case).
        classify: When True (default), attach verdict tier per contract.
        churn_days: Window for stability scoring (default 90).
        max_contracts: Cap on returned contracts (default 50).
        token_budget: Hard cap on the response's payload (default 4000).
        storage_path: Custom storage path.

    Returns:
        Dict with ``contracts`` list, per-verdict counts, and ``_meta``.
    """
    start = time.perf_counter()

    if not isinstance(repos, list) or len(repos) < 2:
        return {"error": "repos must be a list of at least 2 repo identifiers"}
    if min_importers < 1:
        return {"error": "min_importers must be >= 1"}
    if max_contracts < 1:
        return {"error": "max_contracts must be >= 1"}
    if token_budget < 1:
        return {"error": "token_budget must be >= 1"}

    store = IndexStore(base_path=storage_path)
    all_repos_raw = store.list_repos()
    if not all_repos_raw:
        return {"error": "No repositories are indexed."}

    # Resolve the requested group members
    resolved: list[str] = []
    unresolved: list[str] = []
    for r in repos:
        rid = _normalize_repo_id(r, all_repos_raw)
        if rid:
            resolved.append(rid)
        else:
            unresolved.append(r)
    if len(resolved) < 2:
        return {
            "error": f"Could not resolve at least 2 group members. Unresolved: {unresolved}"
        }

    group_set = set(resolved)
    registry = build_package_registry(all_repos_raw)

    # Invert registry → {repo_id: [package_name, ...]}
    repo_packages: dict[str, list[str]] = {}
    for pkg_name, repo_ids in registry.items():
        for rid in repo_ids:
            repo_packages.setdefault(rid, []).append(pkg_name)

    # Load each group member's index once.
    indexes: dict[str, object] = {}
    for rid in resolved:
        owner, name = rid.split("/", 1)
        idx = store.load_index(owner, name)
        if idx is not None:
            indexes[rid] = idx

    if len(indexes) < 2:
        return {
            "error": f"Could not load at least 2 group indexes. Loaded: {list(indexes.keys())}"
        }

    # ── Aggregate cross-repo named imports ─────────────────────────────
    # contract_key = (exporting_repo_id, name)
    # Tracks: which external repos import this name and via which specifier roots.
    importers: dict[tuple[str, str], set[str]] = {}
    specifier_roots: dict[tuple[str, str], set[str]] = {}

    for importing_repo_id, idx in indexes.items():
        imports_map = getattr(idx, "imports", {}) or {}
        for src_file, file_imports in imports_map.items():
            lang = idx.file_languages.get(src_file, "") if hasattr(idx, "file_languages") else ""
            for imp in file_imports:
                names = imp.get("names") or []
                if not names:
                    continue
                specifier = imp.get("specifier", "")
                root_pkg = extract_root_package_from_specifier(specifier, lang)
                if not root_pkg:
                    continue
                # Find which group repo provides this package
                for exporting_repo_id in registry.get(root_pkg, []):
                    if exporting_repo_id == importing_repo_id:
                        continue
                    if exporting_repo_id not in group_set:
                        continue
                    for nm in names:
                        nm_clean = (nm or "").strip()
                        if not nm_clean or nm_clean == "*":
                            continue
                        key = (exporting_repo_id, nm_clean)
                        importers.setdefault(key, set()).add(importing_repo_id)
                        specifier_roots.setdefault(key, set()).add(root_pkg)

    # ── Optionally compute the set of public-looking symbols per exporting repo ─
    # (used only for include_dead_contracts mode)
    public_symbols_per_repo: dict[str, dict[str, dict]] = {}
    if include_dead_contracts:
        for rid, idx in indexes.items():
            sym_map: dict[str, dict] = {}
            for sym in idx.symbols:
                nm = sym.get("name", "")
                if not nm or nm.startswith("_"):
                    continue
                if sym.get("kind") not in {"function", "class", "method", "type", "constant"}:
                    continue
                # First-wins: keep the largest by byte_length when duplicate names
                prev = sym_map.get(nm)
                if prev is None or int(sym.get("byte_length", 0) or 0) > int(prev.get("byte_length", 0) or 0):
                    sym_map[nm] = sym
            public_symbols_per_repo[rid] = sym_map

    # ── Build per-symbol resolution map (name → list of {symbol_id, file, kind}) ─
    sym_by_name_per_repo: dict[str, dict[str, list[dict]]] = {}
    for rid, idx in indexes.items():
        by_name: dict[str, list[dict]] = {}
        for sym in idx.symbols:
            nm = sym.get("name", "")
            if nm:
                by_name.setdefault(nm, []).append(sym)
        sym_by_name_per_repo[rid] = by_name

    # ── Detect version_skew: same symbol name imported via multiple specifier roots ─
    # (separate pass — version_skew is per-name, not per-(repo, name))
    name_to_specifier_roots: dict[str, set[str]] = {}
    for (exporting_repo_id, nm), roots in specifier_roots.items():
        name_to_specifier_roots.setdefault(nm, set()).update(roots)

    # ── Build contract records ─────────────────────────────────────────
    contracts_out: list[dict] = []
    verdict_counts: dict[str, int] = {
        "de_facto_api": 0, "leaky_internal": 0, "dead_contract": 0, "version_skew": 0,
    }

    seen_symbol_ids: set[str] = set()

    # Surface contracts that were actually imported externally
    for (exporting_repo_id, sym_name), external_repos in importers.items():
        if len(external_repos) < min_importers:
            continue
        # Resolve the symbol in the exporting repo by name
        candidates = sym_by_name_per_repo.get(exporting_repo_id, {}).get(sym_name, [])
        # Prefer non-import kinds and larger bodies
        candidates_sorted = sorted(
            candidates,
            key=lambda s: (s.get("kind") == "import",
                           -int(s.get("byte_length", 0) or 0)),
        )
        if not candidates_sorted:
            # No matching symbol found — probably a re-exported or shadowed name. Skip.
            continue
        sym = candidates_sorted[0]
        if sym.get("kind") == "import":
            # Even after sort, the only match was an import alias — skip
            continue

        symbol_id = sym["id"]
        if symbol_id in seen_symbol_ids:
            continue
        seen_symbol_ids.add(symbol_id)

        file_path = sym.get("file", "")
        leaky = _looks_internal(sym_name, file_path)
        if leaky and not include_internal:
            continue
        version_skew_flag = len(name_to_specifier_roots.get(sym_name, set())) > 1

        if classify:
            if leaky:
                verdict = "leaky_internal"
            elif version_skew_flag:
                verdict = "version_skew"
            else:
                verdict = "de_facto_api"
        else:
            verdict = "shared"

        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        owner, name = exporting_repo_id.split("/", 1)
        churn = _churn_for_symbol(store, owner, name, file_path, churn_days, storage_path)
        # Stability: 1.0 = unchanged, drops as churn rises (log-scaled).
        # 0 commits → 1.0; 1 → 0.85; 5 → 0.65; 20 → 0.40; 50+ → 0.20-ish.
        if churn <= 0:
            stability = 1.0
        else:
            import math  # noqa: PLC0415
            stability = max(0.10, 1.0 - 0.30 * math.log1p(churn))

        last_break = _last_breaking_change(symbol_id, owner, name, storage_path)
        runtime_hits = _runtime_hits_for(store, owner, name, symbol_id)

        record = {
            "symbol_id": symbol_id,
            "name": sym_name,
            "kind": sym.get("kind", ""),
            "exporting_repo": exporting_repo_id,
            "file": file_path,
            "line": sym.get("line", 0),
            "verdict": verdict,
            "importer_count": len(external_repos),
            "importing_repos": sorted(external_repos),
            "specifier_roots": sorted(specifier_roots.get((exporting_repo_id, sym_name), [])),
            "stability_score": round(stability, 3),
            "churn_commits_window": churn,
            "churn_window_days": churn_days,
        }
        if last_break:
            record["last_breaking_change"] = last_break
        if runtime_hits is not None:
            record["runtime_hits"] = runtime_hits
        if version_skew_flag and verdict != "version_skew":
            record["version_skew_flag"] = True

        contracts_out.append(record)

    # Optional: dead_contracts surface
    if include_dead_contracts:
        for rid, sym_map in public_symbols_per_repo.items():
            for nm, sym in sym_map.items():
                key = (rid, nm)
                if key in importers:
                    continue  # already classified above
                symbol_id = sym["id"]
                if symbol_id in seen_symbol_ids:
                    continue
                seen_symbol_ids.add(symbol_id)
                owner, name = rid.split("/", 1)
                file_path = sym.get("file", "")
                churn = _churn_for_symbol(store, owner, name, file_path, churn_days, storage_path)
                if churn <= 0:
                    stability = 1.0
                else:
                    import math  # noqa: PLC0415
                    stability = max(0.10, 1.0 - 0.30 * math.log1p(churn))
                verdict_counts["dead_contract"] += 1
                contracts_out.append({
                    "symbol_id": symbol_id,
                    "name": nm,
                    "kind": sym.get("kind", ""),
                    "exporting_repo": rid,
                    "file": file_path,
                    "line": sym.get("line", 0),
                    "verdict": "dead_contract",
                    "importer_count": 0,
                    "importing_repos": [],
                    "specifier_roots": [],
                    "stability_score": round(stability, 3),
                    "churn_commits_window": churn,
                    "churn_window_days": churn_days,
                })

    # Rank by impact: importer_count × (1 + log1p(runtime_hits or 0)) when runtime
    # data exists; otherwise by importer_count alone. Leaky/skew tied with API.
    def _impact(rec: dict) -> tuple:
        rh = int(rec.get("runtime_hits") or 0)
        importer_imp = int(rec.get("importer_count", 0))
        rh_bonus = 0.0
        if rh > 0:
            import math  # noqa: PLC0415
            rh_bonus = math.log1p(rh) * 0.5
        # Higher-risk verdicts (leaky/skew) get a small boost so they surface.
        verdict_boost = 1.0 if rec["verdict"] in {"leaky_internal", "version_skew"} else 0.0
        return (importer_imp + rh_bonus + verdict_boost,
                -int(rec.get("churn_commits_window", 0)))

    contracts_out.sort(key=_impact, reverse=True)
    contracts_out = contracts_out[:max_contracts]

    # Token-pack: each record ≈ 70 tokens worst case; budget-cap conservatively.
    out: list[dict] = []
    total_tokens = 0
    for rec in contracts_out:
        cost = 70  # coarse estimate; payload is dense JSON, not source
        if total_tokens + cost > token_budget and out:
            break
        out.append(rec)
        total_tokens += cost

    # Token-savings ledger
    total_symbols = sum(len(getattr(idx, "symbols", [])) for idx in indexes.values())
    raw_bytes = sum(
        symbol_span_bytes(getattr(idx, "symbols", []))
        for idx in indexes.values()
    )
    response_bytes = total_tokens * 4
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_group_contracts")

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "contracts": out,
        "contracts_returned": len(out),
        "group": resolved,
        "verdict_counts": verdict_counts,
        "total_symbols_scanned": total_symbols,
        "total_tokens": total_tokens,
        "budget_tokens": token_budget,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    if unresolved:
        result["unresolved_repos"] = unresolved
    return result
