"""List indexed repositories."""

from typing import Optional

from ..storage import IndexStore


def list_repos(storage_path: Optional[str] = None) -> dict:
    """List all indexed repositories.
    
    Returns:
        Dict with count and list of repos
    """
    store = IndexStore(base_path=storage_path)
    repos = store.list_repos()
    
    return {
        "count": len(repos),
        "repos": repos
    }
