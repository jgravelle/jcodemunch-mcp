"""Full-text search across indexed file contents."""

import json
import os
import re
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import resolve_repo


def search_text(
    repo: str,
    query: str,
    file_pattern: Optional[str] = None,
    max_results: int = 20,
    context_lines: int = 0,
    is_regex: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Search for text across all indexed files in a repository.

    Useful when symbol search misses — e.g., searching for string literals,
    comments, configuration values, or patterns not captured as symbols.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Text to search for. Case-insensitive substring by default;
               set is_regex=True for full regex (e.g. 'estimateToken|tokenEstimat').
        file_pattern: Optional glob pattern to filter files.
        max_results: Maximum number of matching lines to return.
        context_lines: Lines of context before/after each match (like grep -C).
        is_regex: When True, treat query as a Python regex (re.search, IGNORECASE).
        storage_path: Custom storage path.

    Returns:
        Dict with matching lines grouped by file, plus _meta envelope.
    """
    start = time.perf_counter()
    max_results = max(1, min(max_results, 100))
    context_lines = max(0, min(context_lines, 10))

    if is_regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}

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
    query_lower = query.lower() if not is_regex else None
    results = []
    result_count = 0
    files_searched = 0
    truncated = False
    raw_bytes = 0

    for file_path in files:
        full_path = store._safe_content_path(content_dir, file_path)
        if not full_path:
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                content = f.read()
        except OSError:
            continue

        files_searched += 1
        try:
            raw_bytes += os.path.getsize(full_path)
        except OSError:
            pass
        lines = content.split("\n")
        file_matches = []
        for line_index, line in enumerate(lines):
            hit = pattern.search(line) if is_regex else (query_lower in line.lower())
            if hit:
                match = {
                    "line": line_index + 1,
                    "text": line.rstrip()[:200],  # Truncate long lines
                }
                if context_lines > 0:
                    before_start = max(0, line_index - context_lines)
                    after_end = min(len(lines), line_index + context_lines + 1)
                    match["before"] = [value.rstrip()[:200] for value in lines[before_start:line_index]]
                    match["after"] = [value.rstrip()[:200] for value in lines[line_index + 1:after_end]]
                file_matches.append(match)
                result_count += 1
                if result_count >= max_results:
                    truncated = True
                    break

        if file_matches:
            results.append({"file": file_path, "matches": file_matches})

        if truncated:
            break

    elapsed = (time.perf_counter() - start) * 1000

    # Token savings: raw bytes of searched files vs grouped match response
    response_bytes = len(json.dumps(results, ensure_ascii=False).encode("utf-8"))
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved)

    return {
        "repo": f"{owner}/{name}",
        "query": query,
        "is_regex": is_regex,
        "context_lines": context_lines,
        "result_count": result_count,
        "results": results,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "files_searched": files_searched,
            "truncated": truncated,
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
