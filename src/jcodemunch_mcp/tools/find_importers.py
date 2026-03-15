"""Find all files that import from a given file path."""

import time
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo


def find_importers(
    repo: str,
    file_path: str,
    max_results: int = 50,
    storage_path: Optional[str] = None,
) -> dict:
    """Find all indexed files that import from file_path.

    Args:
        repo: Repository identifier (owner/repo or display name).
        file_path: Target file path within the repo (e.g. 'src/features/intake/IntakeService.js').
        max_results: Maximum number of results.
        storage_path: Custom storage path.

    Returns:
        Dict with importers list and _meta envelope.
    """
    start = time.perf_counter()
    max_results = max(1, min(max_results, 200))

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
            "repo": f"{owner}/{name}",
            "file_path": file_path,
            "importers": [],
            "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_importers.",
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    source_files = frozenset(index.source_files)
    results = []

    for src_file, file_imports in index.imports.items():
        if src_file == file_path:
            continue
        for imp in file_imports:
            resolved = resolve_specifier(imp["specifier"], src_file, source_files)
            if resolved == file_path:
                results.append({
                    "file": src_file,
                    "specifier": imp["specifier"],
                    "names": imp.get("names", []),
                })
                break  # one match per file is enough

    results.sort(key=lambda r: r["file"])

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "file_path": file_path,
        "importer_count": len(results),
        "importers": results[:max_results],
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "truncated": len(results) > max_results,
        },
    }
