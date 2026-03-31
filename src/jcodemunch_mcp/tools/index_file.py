"""Index a single file within an existing index."""

import logging
import os
import time
from pathlib import Path
from typing import Optional

from .. import config as _config
from ..parser import LANGUAGE_EXTENSIONS, get_language_for_path
from ..parser.context import discover_providers, collect_metadata
from ..security import validate_path
from ..storage import IndexStore
from ..storage.index_store import _file_hash, _get_git_head
from ._indexing_pipeline import parse_and_prepare_incremental

logger = logging.getLogger(__name__)


def index_file(
    path: str,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    context_providers: bool = True,
) -> dict:
    """Index a single file within an existing index.

    Finds the matching index by checking which indexed folder's source_root
    is a parent of the given file path, then surgically updates that index.
    Can also add new files not yet in the index (as long as they're under
    an indexed folder's source_root).

    Args:
        path: Absolute path to the file to index.
        use_ai_summaries: Whether to use AI for symbol summaries.
        storage_path: Custom storage path (default: ~/.code-index/).
        context_providers: Whether to run context providers.

    Returns:
        Dict with indexing results.
    """
    t0 = time.monotonic()

    # Resolve and validate file path
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        return {"success": False, "error": f"File not found: {path}"}
    if not file_path.is_file():
        return {"success": False, "error": f"Path is not a file: {path}"}

    store = IndexStore(base_path=storage_path)

    # Find matching index by scanning all indexed repos for one whose
    # source_root is a parent of this file. Pick the most specific match.
    repos = store.list_repos()
    best_match: Optional[dict] = None
    best_root_len = -1

    for repo_entry in repos:
        source_root = repo_entry.get("source_root", "")
        if not source_root:
            continue
        try:
            root_path = Path(source_root).resolve()
            if file_path.is_relative_to(root_path) and len(str(root_path)) > best_root_len:
                best_match = repo_entry
                best_root_len = len(str(root_path))
        except (ValueError, OSError):
            continue

    if best_match is None:
        return {
            "success": False,
            "error": (
                f"No indexed folder found that contains {path}. "
                "Run index_folder on the parent directory first."
            ),
        }

    owner, name = best_match["repo"].split("/", 1)
    source_root = Path(best_match["source_root"]).resolve()

    # Security validation
    if not validate_path(source_root, file_path):
        return {"success": False, "error": f"File path failed security validation: {path}"}

    # Compute rel_path, hash, and mtime
    rel_path = file_path.relative_to(source_root).as_posix()

    # Check language support
    ext = file_path.suffix
    if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(file_path)) is None:
        return {
            "success": False,
            "error": f"Unsupported file type: {ext}. File not recognized as a supported language.",
        }

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            content = f.read()
    except Exception as e:
        return {"success": False, "error": f"Failed to read file: {e}"}

    file_hash = _file_hash(content)
    file_mtime = os.stat(file_path).st_mtime_ns

    # Load existing index to check if file has changed
    index = store.load_index(owner, name)
    if index is None:
        return {"success": False, "error": f"Failed to load index for {owner}/{name}"}

    stored_hash = index.file_hashes.get(rel_path)
    is_new = rel_path not in index.file_hashes

    if not is_new and stored_hash == file_hash:
        # File unchanged — update mtime only if needed, then early exit
        if index.file_mtimes.get(rel_path) != file_mtime:
            store.incremental_save(
                owner=owner, name=name,
                changed_files=[], new_files=[], deleted_files=[],
                new_symbols=[], raw_files={},
                file_mtimes={rel_path: file_mtime},
            )
        return {
            "success": True,
            "message": "File unchanged",
            "repo": f"{owner}/{name}",
            "file": rel_path,
            "duration_seconds": round(time.monotonic() - t0, 2),
        }

    # Discover context providers (same env var check as index_folder)
    _providers_enabled = context_providers and _config.get("context_providers", True)
    active_providers = discover_providers(source_root) if _providers_enabled else []
    # Gate SQL-dependent providers: when SQL is removed from languages config,
    # filter out the dbt provider to avoid unnecessary detection overhead.
    if active_providers and not _config.is_language_enabled("sql"):
        active_providers = [p for p in active_providers if p.name != "dbt"]

    # Shared pipeline: parse, enrich, summarize, extract metadata
    warnings: list[str] = []
    new_symbols, file_summaries, file_languages, file_imports, _no_symbols = (
        parse_and_prepare_incremental(
            files_to_parse={rel_path},
            file_contents={rel_path: content},
            active_providers=active_providers,
            use_ai_summaries=use_ai_summaries,
            warnings=warnings,
            repo=str(source_root),
        )
    )

    git_head = _get_git_head(source_root) or ""
    ctx_metadata = collect_metadata(active_providers) if active_providers else None

    # Determine changed vs new
    changed_files = [rel_path] if not is_new else []
    new_files = [rel_path] if is_new else []

    updated = store.incremental_save(
        owner=owner, name=name,
        changed_files=changed_files,
        new_files=new_files,
        deleted_files=[],
        new_symbols=new_symbols,
        raw_files={rel_path: content},
        git_head=git_head,
        file_summaries=file_summaries,
        file_languages=file_languages,
        imports=file_imports,
        context_metadata=ctx_metadata,
        file_hashes={rel_path: file_hash},
        file_mtimes={rel_path: file_mtime},
    )

    result: dict = {
        "success": True,
        "repo": f"{owner}/{name}",
        "file": rel_path,
        "is_new": is_new,
        "symbol_count": len(new_symbols),
        "indexed_at": updated.indexed_at if updated else "",
        "duration_seconds": round(time.monotonic() - t0, 2),
    }
    if warnings:
        result["warnings"] = warnings
    return result
