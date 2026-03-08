"""Get file tree for a repository."""

import os
import time
from collections import Counter
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import resolve_repo


def get_file_tree(
    repo: str,
    path_prefix: str = "",
    include_summaries: bool = False,
    storage_path: Optional[str] = None
) -> dict:
    """Get repository file tree, optionally filtered by path prefix.

    Args:
        repo: Repository identifier (owner/repo or just repo name)
        path_prefix: Optional path prefix to filter
        storage_path: Custom storage path

    Returns:
        Dict with hierarchical tree structure
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}
    
    # Load index
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}
    
    # Filter files by prefix
    files = [f for f in index.source_files if f.startswith(path_prefix)]
    
    if not files:
        return {
            "repo": f"{owner}/{name}",
            "path_prefix": path_prefix,
            "tree": []
        }
    
    # Build tree structure
    tree = _build_tree(files, index, path_prefix, include_summaries)

    elapsed = (time.perf_counter() - start) * 1000

    # Token savings: sum of raw file sizes vs compact tree response
    store2 = IndexStore(base_path=storage_path)
    content_dir = store2._content_dir(owner, name)
    raw_bytes = 0
    for f in files:
        try:
            raw_bytes += os.path.getsize(content_dir / f)
        except OSError:
            pass
    response_bytes = len(str(tree).encode())
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved)

    return {
        "repo": f"{owner}/{name}",
        "path_prefix": path_prefix,
        "tree": tree,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "file_count": len(files),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }


def _build_tree(files: list[str], index, path_prefix: str, include_summaries: bool = False) -> list[dict]:
    """Build nested tree from flat file list."""
    # Group files by directory
    root = {}
    symbol_counts = Counter(sym.get("file") for sym in index.symbols if sym.get("file"))
    
    for file_path in files:
        # Remove prefix for relative path
        rel_path = file_path[len(path_prefix):].lstrip("/")
        parts = rel_path.split("/")
        
        # Navigate/create tree
        current = root
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            
            if is_last:
                # File node
                node = {
                    "path": file_path,
                    "type": "file",
                    "language": index.file_languages.get(file_path, ""),
                    "symbol_count": symbol_counts.get(file_path, 0),
                }
                if include_summaries:
                    node["summary"] = index.file_summaries.get(file_path, "")
                current[part] = node
            else:
                # Directory node
                if part not in current:
                    current[part] = {"type": "dir", "children": {}}
                current = current[part]["children"]
    
    # Convert to list format
    return _dict_to_list(root)


def _dict_to_list(node_dict: dict) -> list[dict]:
    """Convert tree dict to list format."""
    result = []
    
    for name, node in sorted(node_dict.items()):
        if node.get("type") == "file":
            result.append(node)
        else:
            result.append({
                "path": name + "/",
                "type": "dir",
                "children": _dict_to_list(node.get("children", {}))
            })
    
    return result
