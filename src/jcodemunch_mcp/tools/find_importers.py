"""Find all files that import from a given file path."""

import time
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo


def _find_importers_single(
    file_path: str,
    index,
    max_results: int,
    owner: str,
    name: str,
    start: float,
) -> dict:
    """Core logic for a single file_path query. Returns the original flat shape."""
    if index.imports is None:
        return {
            "repo": f"{owner}/{name}",
            "file_path": file_path,
            "importers": [],
            "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_importers.",
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    source_files = frozenset(index.source_files)

    # Build a set of all files that are imported by at least one other file.
    # Used to annotate each importer with has_importers so the caller can detect
    # dead chains (an importer with has_importers=False is itself unreachable).
    files_that_are_imported: set[str] = set()
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            resolved = resolve_specifier(imp["specifier"], src_file, source_files, index.alias_map)
            if resolved:
                files_that_are_imported.add(resolved)

    results = []

    for src_file, file_imports in index.imports.items():
        if src_file == file_path:
            continue
        for imp in file_imports:
            resolved = resolve_specifier(imp["specifier"], src_file, source_files, index.alias_map)
            if resolved == file_path:
                results.append({
                    "file": src_file,
                    "specifier": imp["specifier"],
                    "names": imp.get("names", []),
                    "has_importers": src_file in files_that_are_imported,
                })
                break  # one match per file is enough

    results.sort(key=lambda r: r["file"])

    elapsed = (time.perf_counter() - start) * 1000
    truncated = len(results) > max_results
    return {
        "repo": f"{owner}/{name}",
        "file_path": file_path,
        "importer_count": len(results),
        "importers": results[:max_results],
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "truncated": truncated,
            "tip": "Tip: use file_paths=['{0}','...'] to query multiple files in one call.".format(file_path)
            if truncated
            else "Tip: use file_paths=['{0}','...'] to query multiple files in one call. "
                 "For usage-site matching beyond imports, also try check_references.".format(file_path),
        },
    }


def _find_importers_batch(
    file_paths: list[str],
    index,
    max_results: int,
    owner: str,
    name: str,
    start: float,
) -> dict:
    """Batch logic: loop over file_paths, return grouped results array."""
    if index.imports is None:
        return {
            "repo": f"{owner}/{name}",
            "results": [
                {
                    "file_path": fp,
                    "importers": [],
                    "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_importers.",
                }
                for fp in file_paths
            ],
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    source_files = frozenset(index.source_files)

    # Pass 1: build files_that_are_imported (needed for has_importers annotation)
    files_that_are_imported: set[str] = set()
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            resolved = resolve_specifier(imp["specifier"], src_file, source_files, index.alias_map)
            if resolved:
                files_that_are_imported.add(resolved)

    # Pass 2: build import_map using the complete set
    import_map: dict[str, list[dict]] = {}
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            resolved = resolve_specifier(imp["specifier"], src_file, source_files, index.alias_map)
            if resolved:
                import_map.setdefault(resolved, []).append({
                    "file": src_file,
                    "specifier": imp["specifier"],
                    "names": imp.get("names", []),
                    "has_importers": src_file in files_that_are_imported,
                })

    results = []
    for file_path in file_paths:
        file_results = import_map.get(file_path, [])  # O(1) lookup
        file_results.sort(key=lambda r: r["file"])
        results.append({
            "file_path": file_path,
            "importer_count": len(file_results),
            "importers": file_results[:max_results],
        })

    return {
        "repo": f"{owner}/{name}",
        "results": results,
        "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
    }


def find_importers(
    repo: str,
    file_path: Optional[str] = None,
    max_results: int = 50,
    storage_path: Optional[str] = None,
    file_paths: Optional[list[str]] = None,
) -> dict:
    """Find all indexed files that import from file_path.

    Supports two modes:
    - Singular: pass ``file_path`` to get the original flat response shape.
    - Batch: pass ``file_paths`` (list) to query multiple files at once,
      returning a grouped ``results`` array.

    Args:
        repo: Repository identifier (owner/repo or display name).
        file_path: Target file path within the repo (singular mode).
        file_paths: List of target file paths (batch mode).
        max_results: Maximum number of importers per file.
        storage_path: Custom storage path.

    Returns:
        Singular mode: dict with flat ``importers`` list and _meta envelope.
        Batch mode: dict with ``results`` array (one entry per input file_path).

    Raises:
        ValueError: if neither or both of file_path and file_paths are provided.
    """
    if (file_path is None and file_paths is None) or (file_path is not None and file_paths is not None):
        raise ValueError("Provide exactly one of 'file_path' or 'file_paths', not both and not neither.")

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

    if file_paths is not None:
        return _find_importers_batch(file_paths, index, max_results, owner, name, start)
    else:
        return _find_importers_single(file_path, index, max_results, owner, name, start)
