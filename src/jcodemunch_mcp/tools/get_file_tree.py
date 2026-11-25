"""Get file tree for a repository."""

import os
import time
from collections import Counter
from typing import Optional

from .. import config as _config
from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import load_repo_index_or_error

# Fallback used only when config is not yet loaded (e.g. tests that bypass main()).
_DEFAULT_MAX_FILES = 500


def get_file_tree(
    repo: str,
    path_prefix: str = "",
    include_summaries: bool = False,
    max_files: Optional[int] = None,
    storage_path: Optional[str] = None
) -> dict:
    """Get repository file tree, optionally filtered by path prefix.

    Args:
        repo: Repository identifier (owner/repo or just repo name)
        path_prefix: Optional path prefix to filter
        include_summaries: Include file-level summaries in tree nodes
        max_files: Maximum number of files to include (default 500).
            When the result would exceed this, the tree is truncated and
            a ``truncated`` flag + ``hint`` are included in the response.
            Use ``path_prefix`` to scope the query to a subdirectory.
        storage_path: Custom storage path

    Returns:
        Dict with hierarchical tree structure
    """
    start = time.perf_counter()

    # Resolve cap: per-call override → config → hardcoded fallback.
    # Project-overridable (#301): per-repo caps are sensible.
    if max_files is None:
        max_files = _config.get("file_tree_max_files", _DEFAULT_MAX_FILES, repo=repo)
    max_files = max(1, max_files)  # guard against 0 or negative

    index, error, _status = load_repo_index_or_error(repo, storage_path)
    if error:
        return error
    owner, name = index.owner, index.name

    # Filter files by prefix
    all_files = [f for f in index.source_files if f.startswith(path_prefix)]

    if not all_files:
        return {
            "repo": f"{owner}/{name}",
            "path_prefix": path_prefix,
            "tree": []
        }

    total_files = len(all_files)
    truncated = total_files > max_files
    files = all_files[:max_files] if truncated else all_files

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
    total_saved = record_savings(tokens_saved, tool_name="get_file_tree")

    result = {
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

    if truncated:
        result["truncated"] = True
        result["total_file_count"] = total_files
        result["hint"] = (
            f"Result capped at {max_files} of {total_files} files. "
            f"Use path_prefix to scope the query to a subdirectory, "
            f"or pass max_files={total_files} to retrieve the full tree."
        )

    return result


def _build_tree(files: list[str], index, path_prefix: str, include_summaries: bool = False) -> list[dict]:
    """Build nested tree from flat file list."""
    symbol_counts = Counter(sym.get("file") for sym in index.symbols if sym.get("file"))
    nodes = []
    for file_path in files:
        node = {
            "path": file_path,
            "type": "file",
            "language": index.file_languages.get(file_path, ""),
            "symbol_count": symbol_counts.get(file_path, 0),
        }
        if include_summaries:
            node["summary"] = index.file_summaries.get(file_path, "")
        nodes.append(node)
    return nest_file_nodes(nodes, path_prefix)


def nest_file_nodes(nodes: list[dict], path_prefix: str) -> list[dict]:
    """Nest file nodes under their directories, the tree this tool returns.

    The one rule for the tree's shape. The compact encoder sends the file
    nodes flat and decodes by calling this, so the decoded tree is the tree
    the tool built rather than a second copy of how it nests.
    """
    root: dict = {}
    for node in nodes:
        rel_path = node["path"][len(path_prefix):].lstrip("/")
        parts = rel_path.split("/")
        current = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                current[part] = node
            else:
                if part not in current:
                    current[part] = {"type": "dir", "children": {}}
                current = current[part]["children"]
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
