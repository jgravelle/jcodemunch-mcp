"""Shared helpers for tool modules."""

from typing import Optional

from ..storage import IndexStore


def resolve_repo(repo: str, storage_path: Optional[str] = None) -> tuple[str, str]:
    """Parse 'owner/repo' or look up single name. Returns (owner, name).

    Raises ValueError if repo not found.
    """
    if "/" in repo:
        return repo.split("/", 1)
    store = IndexStore(base_path=storage_path)
    repos = store.list_repos()
    matching = [r for r in repos if r["repo"].endswith(f"/{repo}")]
    if not matching:
        raise ValueError(f"Repository not found: {repo}")
    return matching[0]["repo"].split("/", 1)
