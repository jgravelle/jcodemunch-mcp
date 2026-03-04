"""Full-text search across indexed file contents."""

import time
from typing import Optional

from ..storage import IndexStore
from ._utils import resolve_repo


def search_text(
    repo: str,
    query: str,
    file_pattern: Optional[str] = None,
    max_results: int = 20,
    storage_path: Optional[str] = None,
) -> dict:
    """Search for text across all indexed files in a repository.

    Useful when symbol search misses — e.g., searching for string literals,
    comments, configuration values, or patterns not captured as symbols.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Text to search for (case-insensitive substring match).
        file_pattern: Optional glob pattern to filter files.
        max_results: Maximum number of matching lines to return.
        storage_path: Custom storage path.

    Returns:
        Dict with matching lines grouped by file, plus _meta envelope.
    """
    start = time.perf_counter()
    max_results = max(1, min(max_results, 100))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # Filter files
    import fnmatch
    files = index.source_files
    if file_pattern:
        files = [f for f in files if fnmatch.fnmatch(f, file_pattern) or fnmatch.fnmatch(f, f"*/{file_pattern}")]

    content_dir = store._content_dir(owner, name)
    query_lower = query.lower()
    matches = []
    files_searched = 0

    for file_path in files:
        full_path = content_dir / file_path
        if not full_path.exists():
            continue

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        files_searched += 1
        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            if query_lower in line.lower():
                matches.append({
                    "file": file_path,
                    "line": line_num,
                    "text": line.rstrip()[:200],  # Truncate long lines
                })
                if len(matches) >= max_results:
                    break

        if len(matches) >= max_results:
            break

    elapsed = (time.perf_counter() - start) * 1000

    return {
        "repo": f"{owner}/{name}",
        "query": query,
        "result_count": len(matches),
        "results": matches,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "files_searched": files_searched,
            "truncated": len(matches) >= max_results,
        },
    }
