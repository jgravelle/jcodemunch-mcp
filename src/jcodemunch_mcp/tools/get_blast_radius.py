"""Blast-radius analysis: find files affected by changing a symbol."""

import re
import time
from collections import deque
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo


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


def _bfs_importers(start: str, rev: dict[str, list[str]], depth: int) -> list[str]:
    """BFS over reverse graph; return all reachable files (excluding start)."""
    visited: set[str] = {start}
    queue: deque = deque([(start, 0)])
    result: list[str] = []
    while queue:
        node, level = queue.popleft()
        if level >= depth:
            continue
        for importer in rev.get(node, []):
            if importer not in visited:
                visited.add(importer)
                result.append(importer)
                queue.append((importer, level + 1))
    return result


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
    storage_path: Optional[str] = None,
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
        storage_path: Custom storage path.

    Returns:
        Dict with symbol info, confirmed/potential affected files, counts, and _meta.
    """
    depth = max(1, min(depth, 3))
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
    importer_files = _bfs_importers(sym_file, rev, depth)

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

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "symbol": {
            "name": sym_name,
            "kind": sym.get("kind", ""),
            "file": sym_file,
            "line": sym.get("line", 0),
            "id": sym.get("id", ""),
        },
        "depth": depth,
        "importer_count": len(importer_files),
        "confirmed_count": len(confirmed),
        "potential_count": len(potential),
        "confirmed": confirmed,
        "potential": potential,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tip": (
                "confirmed = imports the file + mentions the symbol name; "
                "potential = imports the file only (wildcard/namespace import)"
            ),
        },
    }
