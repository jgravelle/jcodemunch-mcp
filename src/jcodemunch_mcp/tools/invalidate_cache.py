"""Invalidate cache / delete index tool."""

from typing import Optional

from ..storage import IndexStore
from .. import config as _cfg
from ..parser.imports import _alias_map_cache, _ALIAS_MAP_LOCK, _sql_stem_cache, _SQL_STEM_LOCK
from ._utils import resolve_repo, _bare_name_cache, _BARE_NAME_LOCK

# Repos invalidated since the last index_folder call.  index_folder checks
# this set and forces incremental=False for repos that were explicitly
# invalidated, regardless of what the on-disk index says.  This handles
# Windows WAL file-locking races where the .db delete fails silently and the
# next incremental index_folder would incorrectly return "No changes detected".
_force_full_reindex: set[str] = set()


def invalidate_cache(
    repo: str,
    storage_path: Optional[str] = None
) -> dict:
    """Delete an index and all cached data for a repository.

    This is an alias for delete_index that also ensures any in-memory
    state is cleared. Use when you want to force a full re-index.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        storage_path: Custom storage path.

    Returns:
        Dict with success status.
    """
    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)

    # Capture source_root before deletion so we can evict per-path caches (X1 / C4-B)
    source_root = None
    for entry in store.list_repos():
        if entry.get("repo") == f"{owner}/{name}":
            source_root = entry.get("source_root") or None
            break

    deleted = store.delete_index(owner, name, force=True)

    # Signal that the next index_folder call for this repo must do a full
    # reindex, even if the on-disk DB still exists (Windows WAL locking can
    # prevent immediate deletion).
    _force_full_reindex.add(f"{owner}/{name}")

    # Clear all in-process caches (X1 / C4-B / T4.5)
    _cfg.forget_repo_resolutions()
    with _cfg._CONFIG_LOCK:
        if source_root:
            _cfg._PROJECT_CONFIGS.pop(source_root, None)
            _cfg._PROJECT_CONFIG_HASHES.pop(source_root, None)
    with _BARE_NAME_LOCK:
        _bare_name_cache.pop(str(store.base_path), None)
    with _SQL_STEM_LOCK:
        _sql_stem_cache.clear()
    if source_root:
        with _ALIAS_MAP_LOCK:
            _alias_map_cache.pop(source_root, None)

    if deleted:
        return {
            "success": True,
            "repo": f"{owner}/{name}",
            "message": f"Index and cached files deleted for {owner}/{name}",
        }
    else:
        return {
            "success": False,
            "error": f"No index found for {owner}/{name}",
        }
