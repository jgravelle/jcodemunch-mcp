"""Get file tree for a repository."""

import os
from typing import Optional

from ..storage import IndexStore


def get_file_tree(
    repo: str,
    path_prefix: str = "",
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
    # Parse repo identifier
    if "/" in repo:
        owner, name = repo.split("/", 1)
    else:
        # Try to find by name only
        store = IndexStore(base_path=storage_path)
        repos = store.list_repos()
        matching = [r for r in repos if r["repo"].endswith(f"/{repo}")]
        if not matching:
            return {"error": f"Repository not found: {repo}"}
        owner, name = matching[0]["repo"].split("/", 1)
    
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
    tree = _build_tree(files, index, path_prefix)
    
    return {
        "repo": f"{owner}/{name}",
        "path_prefix": path_prefix,
        "tree": tree
    }


def _build_tree(files: list[str], index, path_prefix: str) -> list[dict]:
    """Build nested tree from flat file list."""
    # Group files by directory
    root = {}
    
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
                # Count symbols for this file
                symbol_count = sum(1 for s in index.symbols if s.get("file") == file_path)
                
                # Get language
                lang = ""
                _, ext = os.path.splitext(file_path)
                from ..parser import LANGUAGE_EXTENSIONS
                lang = LANGUAGE_EXTENSIONS.get(ext, "")
                
                current[part] = {
                    "path": file_path,
                    "type": "file",
                    "language": lang,
                    "symbol_count": symbol_count
                }
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
