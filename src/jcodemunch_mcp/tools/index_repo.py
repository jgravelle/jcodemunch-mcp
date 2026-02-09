"""Index repository tool - fetch, parse, summarize, save."""

import asyncio
import os
from typing import Optional
from urllib.parse import urlparse

import httpx

from ..parser import parse_file, LANGUAGE_EXTENSIONS
from ..storage import IndexStore
from ..summarizer import summarize_symbols


# File patterns to skip
SKIP_PATTERNS = [
    "node_modules/", "vendor/", "venv/", ".venv/", "__pycache__/",
    "dist/", "build/", ".git/", ".tox/", ".mypy_cache/",
    "target/",
    ".gradle/",
    "test_data/", "testdata/", "fixtures/", "snapshots/",
    "migrations/",
    ".min.js", ".min.ts", ".bundle.js",
    "package-lock.json", "yarn.lock", "go.sum",
    "generated/", "proto/",
]


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
    for pattern in SKIP_PATTERNS:
        if pattern in path:
            return True
    return False


def discover_source_files(
    tree_entries: list[dict],
    gitignore_content: Optional[str] = None,
    max_files: int = 500,
    max_size: int = 500 * 1024  # 500KB
) -> list[str]:
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
    
    # Parse gitignore if provided
    gitignore_spec = None
    if gitignore_content:
        try:
            gitignore_spec = pathspec.PathSpec.from_lines(
                "gitwildmatch",
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
        if ext not in LANGUAGE_EXTENSIONS:
            continue
        
        # Skip list
        if should_skip_file(path):
            continue
        
        # Size limit
        if size > max_size:
            continue
        
        # Gitignore matching
        if gitignore_spec and gitignore_spec.match_file(path):
            continue
        
        files.append(path)
    
    # File count limit with prioritization
    if len(files) > max_files:
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
    
    return files


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
    storage_path: Optional[str] = None
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
        source_files = discover_source_files(tree_entries, gitignore_content)
        
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
        
        # Parse each file
        all_symbols = []
        languages = {}
        raw_files = {}
        parsed_files = []
        
        for path, content in file_contents:
            if not content:
                continue
            
            # Determine language from extension
            _, ext = os.path.splitext(path)
            language = LANGUAGE_EXTENSIONS.get(ext)
            
            if not language:
                continue
            
            # Parse file
            try:
                symbols = parse_file(content, path, language)
                if symbols:
                    all_symbols.extend(symbols)
                    languages[language] = languages.get(language, 0) + 1
                    raw_files[path] = content
                    parsed_files.append(path)
            except Exception:
                warnings.append(f"Failed to parse {path}")
                continue
        
        if not all_symbols:
            return {"success": False, "error": "No symbols extracted"}
        
        # Generate summaries
        all_symbols = summarize_symbols(all_symbols, use_ai=use_ai_summaries)
        
        # Save index
        store = IndexStore(base_path=storage_path)
        store.save_index(
            owner=owner,
            name=repo,
            source_files=parsed_files,
            symbols=all_symbols,
            raw_files=raw_files,
            languages=languages
        )
        
        result = {
            "success": True,
            "repo": f"{owner}/{repo}",
            "indexed_at": store.load_index(owner, repo).indexed_at,
            "file_count": len(parsed_files),
            "symbol_count": len(all_symbols),
            "languages": languages,
            "files": parsed_files[:20],  # Limit files in response
        }
        
        if warnings:
            result["warnings"] = warnings
        
        if len(source_files) >= 500:
            result["warnings"] = warnings + ["Repository has many files; indexed first 500"]
        
        return result
    
    except Exception as e:
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
