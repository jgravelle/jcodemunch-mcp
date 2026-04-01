"""Blast-radius analysis: find files affected by changing a symbol."""

import re
import time
from collections import deque
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo
from .package_registry import extract_root_package_from_specifier
from ._call_graph import build_symbols_by_file, find_direct_callers, bfs_callers


def _build_reverse_adjacency(
    imports: dict, source_files: frozenset, alias_map: Optional[dict] = None
) -> dict[str, list[str]]:
    """Return {file: [files_that_import_it]} from raw import data."""
    rev: dict[str, list[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map)
            if target and target != src_file:
                rev.setdefault(target, []).append(src_file)
    # Deduplicate
    return {k: list(dict.fromkeys(v)) for k, v in rev.items()}


def _bfs_importers(
    start: str, rev: dict[str, list[str]], depth: int
) -> tuple[list[str], dict[int, list[str]]]:
    """BFS over reverse graph; return (flat list, depth-bucketed dict) excluding start."""
    visited: set[str] = {start}
    queue: deque = deque([(start, 0)])
    result: list[str] = []
    by_depth: dict[int, list[str]] = {}
    while queue:
        node, level = queue.popleft()
        if level >= depth:
            continue
        for importer in rev.get(node, []):
            if importer not in visited:
                visited.add(importer)
                d = level + 1
                result.append(importer)
                by_depth.setdefault(d, []).append(importer)
                queue.append((importer, d))
    return result, by_depth


def _find_symbol(index, symbol: str) -> list[dict]:
    """Find symbols by ID or name. Returns all matches."""
    # Try exact ID first
    by_id = index.get_symbol(symbol)
    if by_id:
        return [by_id]
    # Exact name match
    exact = [s for s in index.symbols if s.get("name") == symbol]
    if exact:
        return exact
    # Case-insensitive fallback
    lower = symbol.lower()
    return [s for s in index.symbols if s.get("name", "").lower() == lower]


def _name_in_content(content: str, name: str) -> bool:
    """Return True if name appears as a word token in content."""
    return bool(re.search(r"\b" + re.escape(name) + r"\b", content))


def get_blast_radius(
    repo: str,
    symbol: str,
    depth: int = 1,
    include_depth_scores: bool = False,
    storage_path: Optional[str] = None,
    cross_repo: Optional[bool] = None,
    call_depth: int = 0,
) -> dict:
    """Find all files that would be affected if a symbol's signature or behaviour changed.

    Uses two-stage analysis:
      1. Dependency graph — collect every file that (transitively) imports the
         file that defines ``symbol`` up to ``depth`` hops.
      2. Text scan — check whether each importing file actually mentions the
         symbol by name.  Files that do are ``confirmed`` references; files that
         import the module but don't name the symbol are ``potential`` references
         (e.g. wildcard / namespace imports).

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol: Symbol name or ID to analyse.
        depth: Import hops to traverse (1 = direct importers only; max 3).
        call_depth: Call-graph hops for caller detection (0 = disabled; max 3).
                    When > 0, adds a ``callers`` list of calling symbols with depth scores.
        storage_path: Custom storage path.

    Returns:
        Dict with symbol info, confirmed/potential affected files, counts, and _meta.
        When call_depth > 0: also includes ``callers`` and ``caller_count``.
    """
    depth = max(1, min(depth, 3))
    call_depth = max(0, min(call_depth, 3))
    start = time.perf_counter()

    # Resolve cross_repo default from config if not explicitly provided
    if cross_repo is None:
        from .. import config as _cfg
        cross_repo = bool(_cfg.get("cross_repo_default", False))

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
                "to enable blast radius analysis."
            )
        }

    # Resolve symbol
    matches = _find_symbol(index, symbol)
    if not matches:
        return {"error": f"Symbol not found: '{symbol}'. Try search_symbols first."}
    if len(matches) > 1:
        # Multiple definitions (e.g. overloads in different files) — report all
        ambiguous = [{"name": s["name"], "file": s["file"], "id": s["id"]} for s in matches]
        return {
            "error": (
                f"Ambiguous symbol '{symbol}': found {len(matches)} definitions. "
                "Use the symbol 'id' field to disambiguate."
            ),
            "candidates": ambiguous,
        }

    sym = matches[0]
    sym_name: str = sym["name"]
    sym_file: str = sym["file"]

    # Build reverse adjacency (importer graph)
    source_files = frozenset(index.source_files)
    rev = _build_reverse_adjacency(index.imports, source_files, index.alias_map)

    # BFS to collect all importing files
    importer_files, files_by_depth = _bfs_importers(sym_file, rev, depth)

    # Text-scan each importer for the symbol name
    confirmed: list[dict] = []
    potential: list[dict] = []

    for imp_file in importer_files:
        content = store.get_file_content(owner, name, imp_file)
        if content is None:
            potential.append({"file": imp_file, "reason": "content unavailable"})
            continue
        if _name_in_content(content, sym_name):
            # Count occurrences for extra signal
            count = len(re.findall(r"\b" + re.escape(sym_name) + r"\b", content))
            confirmed.append({"file": imp_file, "references": count})
        else:
            potential.append({"file": imp_file, "reason": "symbol name not found (may use namespace/wildcard import)"})

    confirmed.sort(key=lambda x: x["file"])
    potential.sort(key=lambda x: x["file"])

    # Cross-repo: find other repos that import this repo's package
    cross_repo_confirmed: list[dict] = []
    if cross_repo:
        try:
            from .list_repos import list_repos
            from .package_registry import build_package_registry
            all_repos_data = list_repos(storage_path=storage_path).get("repos", [])
            pkg_names = getattr(index, "package_names", []) or []
            if pkg_names:
                for repo_entry in all_repos_data:
                    other_repo_id = repo_entry.get("repo", "")
                    if not other_repo_id or other_repo_id == f"{owner}/{name}" or "/" not in other_repo_id:
                        continue
                    other_owner, other_name = other_repo_id.split("/", 1)
                    other_index = store.load_index(other_owner, other_name)
                    if not other_index or not other_index.imports:
                        continue
                    for src_file, file_imports in other_index.imports.items():
                        for imp in file_imports:
                            specifier = imp.get("specifier", "")
                            lang = other_index.file_languages.get(src_file, "")
                            root_pkg = extract_root_package_from_specifier(specifier, lang)
                            if root_pkg and root_pkg in pkg_names:
                                cross_repo_confirmed.append({
                                    "file": src_file,
                                    "cross_repo": True,
                                    "source_repo": other_repo_id,
                                    "references": 1,
                                })
                                break
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).debug("cross_repo blast radius failed", exc_info=True)

    # Risk scoring (always computed, cheap)
    total = len(importer_files)
    direct_count = len(files_by_depth.get(1, []))
    if total > 0:
        overall_risk = sum(
            (1.0 / (d ** 0.7)) * len(files)
            for d, files in files_by_depth.items()
        ) / total
    else:
        overall_risk = 0.0

    # Call-level analysis (optional, gated on call_depth > 0)
    callers: list[dict] = []
    if call_depth > 0:
        symbols_by_file = build_symbols_by_file(index)
        callers, _ = bfs_callers(
            index, store, owner, name, sym, rev, symbols_by_file, call_depth
        )

    elapsed = (time.perf_counter() - start) * 1000
    result = {
        "repo": f"{owner}/{name}",
        "symbol": {
            "name": sym_name,
            "kind": sym.get("kind", ""),
            "file": sym_file,
            "line": sym.get("line", 0),
            "id": sym.get("id", ""),
        },
        "depth": depth,
        "importer_count": total,
        "direct_dependents_count": direct_count,
        "overall_risk_score": round(overall_risk, 4),
        "confirmed_count": len(confirmed),
        "potential_count": len(potential),
        "confirmed": confirmed,
        "potential": potential,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tip": (
                "confirmed = imports the file + mentions the symbol name; "
                "potential = imports the file only (wildcard/namespace import). "
                "Use call_depth > 0 to also get symbol-level callers."
            ),
        },
    }
    if call_depth > 0:
        result["caller_count"] = len(callers)
        result["callers"] = callers
    if cross_repo and cross_repo_confirmed:
        result["cross_repo_confirmed"] = cross_repo_confirmed
        result["cross_repo_confirmed_count"] = len(cross_repo_confirmed)
    if include_depth_scores:
        result["impact_by_depth"] = [
            {
                "depth": d,
                "files": sorted(files_by_depth[d]),
                "risk_score": round(1.0 / (d ** 0.7), 4),
            }
            for d in sorted(files_by_depth)
        ]
    return result
