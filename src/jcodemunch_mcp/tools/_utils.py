"""Shared helpers for tool modules."""

from typing import Optional

from ..storage import IndexStore


def resolve_repo(repo: str, storage_path: Optional[str] = None) -> tuple[str, str]:
    """Resolve an indexed repository id or unique bare display/name.

    Raises ValueError if the repo is not found or the bare name is ambiguous.
    """
    if "/" in repo:
        return repo.split("/", 1)

    store = IndexStore(base_path=storage_path)
    repos = store.list_repos()
    matching = []
    for repo_entry in repos:
        _, repo_name = repo_entry["repo"].split("/", 1)
        if repo_name == repo or repo_entry.get("display_name") == repo:
            matching.append(repo_entry["repo"])

    if not matching:
        raise ValueError(f"Repository not found: {repo}")

    candidates = sorted(set(matching))
    if len(candidates) > 1:
        raise ValueError(
            f"Ambiguous repository name: {repo}. Use one of: {', '.join(candidates)}"
        )

    return candidates[0].split("/", 1)
