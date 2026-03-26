"""Guided discovery: suggest useful search queries based on index content."""

import time
from collections import Counter
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo


def suggest_queries(
    repo: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Scan the index and suggest useful search queries and key entry points.

    Surfaces:
    * Most common keywords across all symbols — good starting search terms.
    * Most-imported files — likely core modules worth understanding first.
    * Kind distribution — how many functions, classes, methods, etc.
    * Language distribution — what languages are in the repo.
    * Example ready-to-run queries tailored to what's in the index.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        storage_path: Custom storage path.

    Returns:
        Dict with suggested queries, key files, and index statistics.
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

    symbols = index.symbols
    if not symbols:
        return {"error": "Index is empty — no symbols found."}

    # Kind distribution
    kind_counts = Counter(s.get("kind", "unknown") for s in symbols)

    # Language distribution
    lang_counts = Counter(s.get("language", "unknown") for s in symbols)

    # Top keywords across all symbols
    keyword_counts: Counter = Counter()
    for sym in symbols:
        for kw in sym.get("keywords", []):
            keyword_counts[kw.lower()] += 1
    top_keywords = [kw for kw, _ in keyword_counts.most_common(15)]

    # Most-imported files (by reverse import count)
    most_imported: list[dict] = []
    if index.imports:
        source_files = frozenset(index.source_files)
        importer_counts: Counter = Counter()
        for src_file, file_imports in index.imports.items():
            for imp in file_imports:
                target = resolve_specifier(imp["specifier"], src_file, source_files, index.alias_map)
                if target:
                    importer_counts[target] += 1
        for fpath, count in importer_counts.most_common(8):
            most_imported.append({"file": fpath, "imported_by": count})

    # Most common class and function names (potential entry points)
    class_names = [s["name"] for s in symbols if s.get("kind") == "class"][:5]
    func_names = [s["name"] for s in symbols
                  if s.get("kind") in ("function", "method") and not s["name"].startswith("_")][:5]

    # Build example queries
    example_queries: list[dict] = []

    if top_keywords:
        example_queries.append({
            "query": top_keywords[0],
            "tool": "search_symbols",
            "description": f"Find symbols related to '{top_keywords[0]}' (most common keyword)",
        })
    if class_names:
        example_queries.append({
            "query": class_names[0],
            "tool": "search_symbols",
            "description": f"Look up the '{class_names[0]}' class definition",
        })
    if func_names:
        example_queries.append({
            "query": func_names[0],
            "tool": "search_symbols",
            "description": f"Find the '{func_names[0]}' function",
        })
    if most_imported:
        example_queries.append({
            "query": most_imported[0]["file"],
            "tool": "get_file_outline",
            "description": f"Outline the most-imported file (imported by {most_imported[0]['imported_by']} files)",
        })
    if len(top_keywords) > 1:
        example_queries.append({
            "query": " ".join(top_keywords[1:3]),
            "tool": "search_symbols",
            "description": "Multi-keyword search combining two common topics",
        })

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "symbol_count": len(symbols),
        "file_count": len(index.source_files),
        "kind_distribution": dict(kind_counts.most_common()),
        "language_distribution": dict(lang_counts.most_common()),
        "top_keywords": top_keywords,
        "most_imported_files": most_imported,
        "example_queries": example_queries,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }
