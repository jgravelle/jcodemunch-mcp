"""Index repository tool - fetch, parse, summarize, save."""

import asyncio
import hashlib
import os
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

import httpx

from ..parser import parse_file, LANGUAGE_EXTENSIONS, get_language_for_path
from ..security import is_secret_file, is_binary_extension, get_max_index_files, SKIP_PATTERNS
from ..storage import IndexStore
from ..summarizer import summarize_symbols, generate_file_summaries


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract owner/repo from GitHub URL or owner/repo string.
    
    Supports:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - owner/repo
    """
    # Remove .git suffix
    url = url.removesuffix(".git")
    
    # If it contains a / but not ://, treat as owner/repo
    if "/" in url and "://" not in url:
        parts = url.split("/")
        return parts[0], parts[1]
    
    # Parse URL
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    
    # Extract owner/repo from path
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    
    raise ValueError(f"Could not parse GitHub URL: {url}")


async def fetch_repo_tree(owner: str, repo: str, token: Optional[str] = None) -> list[dict]:
    """Fetch full repository tree via git/trees API.
    
    Uses recursive=1 to get all paths in a single API call.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD"
    params = {"recursive": "1"}
    headers = {"Accept": "application/vnd.github.v3+json"}
    
    if token:
        headers["Authorization"] = f"token {token}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
    
    return data.get("tree", [])


def should_skip_file(path: str) -> bool:
    """Check if file should be skipped based on path patterns."""
    normalized = path.replace("\\", "/")
    for pattern in SKIP_PATTERNS:
        if pattern.endswith("/"):
            # Directory pattern: match only complete path segments to avoid
            # false positives on names like "rebuild/" or "proto-utils/"
            if normalized.startswith(pattern) or ("/" + pattern) in normalized:
                return True
        else:
            if pattern in normalized:
                return True
    return False


def discover_source_files(
    tree_entries: list[dict],
    gitignore_content: Optional[str] = None,
    max_files: Optional[int] = None,
    max_size: int = 500 * 1024  # 500KB
) -> tuple[list[str], bool]:
    """Discover source files from tree entries.
    
    Applies filtering pipeline:
    1. Type filter (blobs only)
    2. Extension filter (supported languages)
    3. Skip list patterns
    4. Size limit
    5. .gitignore matching
    6. File count limit
    """
    import pathspec

    max_files = get_max_index_files(max_files)
    
    # Parse gitignore if provided
    gitignore_spec = None
    if gitignore_content:
        try:
            gitignore_spec = pathspec.PathSpec.from_lines(
                "gitignore",
                gitignore_content.split("\n")
            )
        except Exception:
            pass
    
    files = []
    
    for entry in tree_entries:
        # Type filter - only blobs (files)
        if entry.get("type") != "blob":
            continue
        
        path = entry.get("path", "")
        size = entry.get("size", 0)
        
        # Extension filter
        _, ext = os.path.splitext(path)
        if get_language_for_path(path) is None:
            continue

        # Skip list
        if should_skip_file(path):
            continue

        # Secret detection
        if is_secret_file(path):
            continue

        # Binary extension check
        if is_binary_extension(path):
            continue
        
        # Size limit
        if size > max_size:
            continue
        
        # Gitignore matching
        if gitignore_spec and gitignore_spec.match_file(path):
            continue
        
        files.append(path)
    
    truncated = len(files) > max_files

    # File count limit with prioritization
    if truncated:
        # Prioritize: src/, lib/, pkg/, cmd/, internal/ first
        priority_dirs = ["src/", "lib/", "pkg/", "cmd/", "internal/"]
        
        def priority_key(path):
            # Check if in priority dir
            for i, prefix in enumerate(priority_dirs):
                if path.startswith(prefix):
                    return (i, path.count("/"), path)
            # Not in priority dir - sort after
            return (len(priority_dirs), path.count("/"), path)
        
        files.sort(key=priority_key)
        files = files[:max_files]
    
    return files, truncated


def _file_languages_for_paths(
    file_paths: list[str],
    symbols_by_file: dict[str, list],
) -> dict[str, str]:
    """Resolve file languages using parsed symbols first, then extension fallback."""
    file_languages: dict[str, str] = {}
    for file_path in file_paths:
        file_symbols = symbols_by_file.get(file_path, [])
        language = file_symbols[0].language if file_symbols else ""
        if not language:
            language = get_language_for_path(file_path) or ""
        if language:
            file_languages[file_path] = language
    return file_languages


def _language_counts(file_languages: dict[str, str]) -> dict[str, int]:
    """Count files by language."""
    counts: dict[str, int] = {}
    for language in file_languages.values():
        counts[language] = counts.get(language, 0) + 1
    return counts


def _complete_file_summaries(
    file_paths: list[str],
    symbols_by_file: dict[str, list],
) -> dict[str, str]:
    """Generate file summaries and include empty entries for no-symbol files."""
    generated = generate_file_summaries(dict(symbols_by_file))
    return {file_path: generated.get(file_path, "") for file_path in file_paths}


async def fetch_file_content(
    owner: str,
    repo: str,
    path: str,
    token: Optional[str] = None
) -> str:
    """Fetch raw file content from GitHub."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {"Accept": "application/vnd.github.v3.raw"}
    
    if token:
        headers["Authorization"] = f"token {token}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


async def fetch_gitignore(
    owner: str,
    repo: str,
    token: Optional[str] = None
) -> Optional[str]:
    """Fetch .gitignore file if it exists."""
    try:
        return await fetch_file_content(owner, repo, ".gitignore", token)
    except Exception:
        return None


async def index_repo(
    url: str,
    use_ai_summaries: bool = True,
    github_token: Optional[str] = None,
    storage_path: Optional[str] = None,
    incremental: bool = True,
) -> dict:
    """Index a GitHub repository.
    
    Args:
        url: GitHub repository URL or owner/repo string
        use_ai_summaries: Whether to use AI for symbol summaries
        github_token: GitHub API token (optional, for private repos/higher rate limits)
        storage_path: Custom storage path (default: ~/.code-index/)
    
    Returns:
        Dict with indexing results
    """
    # Parse URL
    try:
        owner, repo = parse_github_url(url)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    
    # Get GitHub token from env if not provided
    if not github_token:
        github_token = os.environ.get("GITHUB_TOKEN")
    
    warnings = []
    max_files = get_max_index_files()
    
    try:
        # Fetch tree
        try:
            tree_entries = await fetch_repo_tree(owner, repo, github_token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"success": False, "error": f"Repository not found: {owner}/{repo}"}
            elif e.response.status_code == 403:
                return {"success": False, "error": "GitHub API rate limit exceeded. Set GITHUB_TOKEN."}
            raise
        
        # Fetch .gitignore
        gitignore_content = await fetch_gitignore(owner, repo, github_token)
        
        # Discover source files
        source_files, truncated = discover_source_files(
            tree_entries,
            gitignore_content,
            max_files=max_files,
        )
        
        if not source_files:
            return {"success": False, "error": "No source files found"}
        
        # Fetch all file contents concurrently
        semaphore = asyncio.Semaphore(10)  # Limit concurrent requests

        async def fetch_with_limit(path: str) -> tuple[str, str]:
            async with semaphore:
                try:
                    content = await fetch_file_content(owner, repo, path, github_token)
                    return path, content
                except Exception:
                    return path, ""

        tasks = [fetch_with_limit(path) for path in source_files]
        file_contents = await asyncio.gather(*tasks)

        # Build current_files map from fetched content
        current_files: dict[str, str] = {}
        for path, content in file_contents:
            if content:
                current_files[path] = content

        store = IndexStore(base_path=storage_path)

        # Incremental path
        existing_index = store.load_index(owner, repo)
        if incremental and existing_index is not None:
            changed, new, deleted = store.detect_changes(owner, repo, current_files)

            if not changed and not new and not deleted:
                return {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo}",
                    "changed": 0, "new": 0, "deleted": 0,
                }

            files_to_parse = set(changed) | set(new)
            new_symbols = []
            raw_files_subset: dict[str, str] = {}
            incremental_no_symbols: list[str] = []

            for path in files_to_parse:
                content = current_files[path]
                # Track file hashes for changed/new files even when symbol extraction yields none.
                raw_files_subset[path] = content
                language = get_language_for_path(path)
                if not language:
                    incremental_no_symbols.append(path)
                    continue
                try:
                    symbols = parse_file(content, path, language)
                    if symbols:
                        new_symbols.extend(symbols)
                    else:
                        incremental_no_symbols.append(path)
                except Exception:
                    warnings.append(f"Failed to parse {path}")

            new_symbols = summarize_symbols(new_symbols, use_ai=use_ai_summaries)

            # Generate file summaries for changed/new files
            incr_symbols_map = defaultdict(list)
            for s in new_symbols:
                incr_symbols_map[s.file].append(s)
            incr_file_summaries = _complete_file_summaries(sorted(files_to_parse), incr_symbols_map)
            incr_file_languages = _file_languages_for_paths(sorted(files_to_parse), incr_symbols_map)

            updated = store.incremental_save(
                owner=owner, name=repo,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_symbols=new_symbols,
                raw_files=raw_files_subset,
                file_summaries=incr_file_summaries,
                file_languages=incr_file_languages,
            )

            result = {
                "success": True,
                "repo": f"{owner}/{repo}",
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "symbol_count": len(updated.symbols) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "no_symbols_count": len(incremental_no_symbols),
                "no_symbols_files": incremental_no_symbols[:50],
            }
            if warnings:
                result["warnings"] = warnings
            return result

        # Full index path
        all_symbols = []
        symbols_by_file: dict[str, list] = defaultdict(list)
        source_file_list = sorted(current_files)
        no_symbols_files: list[str] = []

        for path, content in current_files.items():
            language = get_language_for_path(path)
            if not language:
                no_symbols_files.append(path)
                continue
            try:
                symbols = parse_file(content, path, language)
                if symbols:
                    all_symbols.extend(symbols)
                    symbols_by_file[path].extend(symbols)
                else:
                    no_symbols_files.append(path)
            except Exception:
                warnings.append(f"Failed to parse {path}")
                continue

        # Generate summaries
        if all_symbols:
            all_symbols = summarize_symbols(all_symbols, use_ai=use_ai_summaries)

        # Generate file-level summaries (single-pass grouping)
        file_symbols_map = defaultdict(list)
        for s in all_symbols:
            file_symbols_map[s.file].append(s)
        file_languages = _file_languages_for_paths(source_file_list, file_symbols_map)
        languages = _language_counts(file_languages)
        file_summaries = _complete_file_summaries(source_file_list, file_symbols_map)

        # Save index
        # Track hashes for all discovered source files so incremental change detection
        # does not repeatedly report no-symbol files as "new".
        file_hashes = {
            fp: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for fp, content in current_files.items()
        }
        index = store.save_index(
            owner=owner,
            name=repo,
            source_files=source_file_list,
            symbols=all_symbols,
            raw_files=current_files,
            languages=languages,
            file_hashes=file_hashes,
            file_summaries=file_summaries,
            source_root="",
            file_languages=file_languages,
            display_name=repo,
        )

        result = {
            "success": True,
            "repo": index.repo,
            "indexed_at": index.indexed_at,
            "file_count": len(source_file_list),
            "symbol_count": len(all_symbols),
            "file_summary_count": sum(1 for v in file_summaries.values() if v),
            "languages": languages,
            "files": source_file_list[:20],  # Limit files in response
            "no_symbols_count": len(no_symbols_files),
            "no_symbols_files": no_symbols_files[:50],
        }

        if warnings:
            result["warnings"] = warnings

        if truncated:
            result["warnings"] = warnings + [f"Repository has many files; indexed first {max_files}"]

        return result
    
    except Exception as e:
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
