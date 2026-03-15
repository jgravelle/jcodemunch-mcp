"""Index repository tool - fetch, parse, summarize, save."""

import asyncio
import hashlib
import logging
import os
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

from ..parser import parse_file, LANGUAGE_EXTENSIONS, get_language_for_path
from ..parser.imports import extract_imports
from ..security import is_secret_file, is_binary_extension, get_max_index_files, get_extra_ignore_patterns, SKIP_PATTERNS
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


async def fetch_repo_tree(owner: str, repo: str, token: Optional[str] = None) -> tuple[list[dict], str]:
    """Fetch full repository tree via git/trees API.

    Uses recursive=1 to get all paths in a single API call.

    Returns:
        Tuple of (tree_entries, tree_sha). The tree_sha can be stored and
        compared on subsequent calls to detect whether anything has changed
        without downloading file contents.
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

    return data.get("tree", []), data.get("sha", "")


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
    max_size: int = 500 * 1024,  # 500KB
    extra_ignore_patterns: Optional[list] = None,
) -> tuple[list[str], dict[str, str], bool]:
    """Discover source files from tree entries.

    Applies filtering pipeline:
    1. Type filter (blobs only)
    2. Extension filter (supported languages)
    3. Skip list patterns
    4. Size limit
    5. .gitignore matching
    6. File count limit

    Returns:
        Tuple of (file_paths, blob_shas, truncated). blob_shas maps each
        accepted path to its GitHub blob SHA for incremental diff.
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

    # Merge env-var global patterns with per-call patterns
    effective_extra = get_extra_ignore_patterns(extra_ignore_patterns)
    extra_spec = None
    if effective_extra:
        try:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", effective_extra)
        except Exception:
            pass

    files = []
    blob_shas: dict[str, str] = {}

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

        # Extra ignore patterns (env-var + per-call)
        if extra_spec and extra_spec.match_file(path):
            continue

        files.append(path)
        blob_shas[path] = entry.get("sha", "")

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
        blob_shas = {p: blob_shas[p] for p in files}

    return files, blob_shas, truncated


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
    extra_ignore_patterns: Optional[list] = None,
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

    logger.info("index_repo start — repo: %s/%s, incremental: %s", owner, repo, incremental)

    # Get GitHub token from env if not provided
    if not github_token:
        github_token = os.environ.get("GITHUB_TOKEN")

    warnings = []
    max_files = get_max_index_files()

    try:
        t0 = time.monotonic()
        # Fetch tree (also returns the tree SHA for lightweight staleness checks)
        try:
            tree_entries, current_tree_sha = await fetch_repo_tree(owner, repo, github_token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"success": False, "error": f"Repository not found: {owner}/{repo}"}
            elif e.response.status_code == 403:
                return {"success": False, "error": "GitHub API rate limit exceeded. Set GITHUB_TOKEN."}
            raise

        # Load existing index once — reused for both the fast-path SHA check
        # and the full incremental change-detection path below.
        store = IndexStore(base_path=storage_path)
        existing_index = store.load_index(owner, repo)

        # Fast-path incremental check: if the stored tree SHA matches the current
        # one, no files have changed — skip all file downloads entirely.
        if incremental and current_tree_sha and existing_index is not None:
            if existing_index.git_head == current_tree_sha:
                logger.info(
                    "index_repo tree_sha_match — %s/%s: tree SHA unchanged (%s), skipping download",
                    owner, repo, current_tree_sha[:12],
                )
                return {
                    "success": True,
                    "message": "No changes detected (tree SHA unchanged)",
                    "repo": f"{owner}/{repo}",
                    "git_head": current_tree_sha,
                    "changed": 0, "new": 0, "deleted": 0,
                    "duration_seconds": round(time.monotonic() - t0, 2),
                }

        # Fetch .gitignore
        gitignore_content = await fetch_gitignore(owner, repo, github_token)

        # Discover source files (also collects blob SHAs for incremental diff)
        source_files, blob_shas, truncated = discover_source_files(
            tree_entries,
            gitignore_content,
            max_files=max_files,
            extra_ignore_patterns=extra_ignore_patterns,
        )

        logger.info("index_repo discovery — %d source files (truncated=%s)", len(source_files), truncated)

        if not source_files:
            return {"success": False, "error": "No source files found"}

        # Blob-SHA incremental fast path: diff blob SHAs from tree against stored ones
        # to determine exactly which files changed — without downloading anything first.
        files_to_fetch: set[str] = set(source_files)
        _blob_diff: Optional[tuple[list, list, list]] = None
        if incremental and existing_index is not None and existing_index.file_blob_shas:
            old_blob = existing_index.file_blob_shas
            old_set, new_set = set(old_blob), set(blob_shas)
            _deleted = sorted(old_set - new_set)
            _new = sorted(new_set - old_set)
            _changed = sorted(p for p in (old_set & new_set) if old_blob[p] != blob_shas[p])
            if not _changed and not _new and not _deleted:
                logger.info("index_repo blob_sha_diff — no changes, skipping")
                return {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo}",
                    "git_head": current_tree_sha,
                    "changed": 0, "new": 0, "deleted": 0,
                    "duration_seconds": round(time.monotonic() - t0, 2),
                }
            files_to_fetch = set(_changed) | set(_new)
            _blob_diff = (_changed, _new, _deleted)
            logger.info(
                "index_repo blob_sha_diff — changed: %d, new: %d, deleted: %d (fetching %d files)",
                len(_changed), len(_new), len(_deleted), len(files_to_fetch),
            )

        # Fetch file contents concurrently (only files that need updating)
        semaphore = asyncio.Semaphore(10)  # Limit concurrent requests

        async def fetch_with_limit(path: str) -> tuple[str, str]:
            async with semaphore:
                try:
                    content = await fetch_file_content(owner, repo, path, github_token)
                    return path, content
                except Exception:
                    return path, ""

        tasks = [fetch_with_limit(path) for path in files_to_fetch]
        file_contents = await asyncio.gather(*tasks)

        # Build current_files map from fetched content
        current_files: dict[str, str] = {}
        for path, content in file_contents:
            if content:
                current_files[path] = content

        if existing_index is None and store.has_index(owner, repo):
            logger.warning(
                "index_repo version_mismatch — %s/%s: on-disk index is a newer version; full re-index required",
                owner, repo,
            )
            warnings.append(
                "Existing index was created by a newer version of jcodemunch-mcp "
                "and cannot be read — performing a full re-index. "
                "If you downgraded the package, delete ~/.code-index/ (or your "
                "CODE_INDEX_PATH directory) to remove the stale index."
            )

        if incremental and existing_index is not None:
            if _blob_diff is not None:
                # Use pre-computed blob SHA diff (no need to hash all content)
                changed, new, deleted = _blob_diff
                logger.info(
                    "index_repo incremental (blob SHA) — changed: %d, new: %d, deleted: %d",
                    len(changed), len(new), len(deleted),
                )
            else:
                changed, new, deleted = store.detect_changes(owner, repo, current_files)
                logger.info(
                    "index_repo incremental (content hash) — changed: %d, new: %d, deleted: %d",
                    len(changed), len(new), len(deleted),
                )

            if not changed and not new and not deleted:
                logger.info("index_repo incremental — no changes detected, skipping save")
                return {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo}",
                    "changed": 0, "new": 0, "deleted": 0,
                    "duration_seconds": round(time.monotonic() - t0, 2),
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

            # Build import graph for changed/new files
            incr_file_imports: dict[str, list[dict]] = {}
            for path in files_to_parse:
                content = current_files[path]
                language = get_language_for_path(path)
                if language:
                    imps = extract_imports(content, path, language)
                    if imps:
                        incr_file_imports[path] = imps

            # Only record blob SHAs for files we successfully fetched
            # (failed fetches keep their old SHA so they're retried next run)
            incr_blob_shas = {p: blob_shas[p] for p in current_files if p in blob_shas}
            updated = store.incremental_save(
                owner=owner, name=repo,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_symbols=new_symbols,
                raw_files=raw_files_subset,
                file_summaries=incr_file_summaries,
                file_languages=incr_file_languages,
                git_head=current_tree_sha,
                imports=incr_file_imports,
                file_blob_shas=incr_blob_shas,
            )

            result = {
                "success": True,
                "repo": f"{owner}/{repo}",
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "symbol_count": len(updated.symbols) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "duration_seconds": round(time.monotonic() - t0, 2),
                "no_symbols_count": len(incremental_no_symbols),
                "no_symbols_files": incremental_no_symbols[:50],
            }
            if warnings:
                result["warnings"] = warnings
            return result

        # Full index path
        logger.info("index_repo full — parsing %d files", len(current_files))
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

        logger.info(
            "index_repo parsing complete — with symbols: %d, no symbols: %d",
            len(symbols_by_file), len(no_symbols_files),
        )

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

        # Build import graph
        file_imports: dict[str, list[dict]] = {}
        for path, content in current_files.items():
            language = get_language_for_path(path)
            if language:
                imps = extract_imports(content, path, language)
                if imps:
                    file_imports[path] = imps

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
            git_head=current_tree_sha,
            imports=file_imports,
            file_blob_shas=blob_shas,
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
            "duration_seconds": round(time.monotonic() - t0, 2),
            "no_symbols_count": len(no_symbols_files),
            "no_symbols_files": no_symbols_files[:50],
        }

        logger.info(
            "index_repo complete — repo: %s/%s, files: %d, symbols: %d",
            owner, repo, len(source_file_list), len(all_symbols),
        )

        if warnings:
            result["warnings"] = warnings

        if truncated:
            result["warnings"] = warnings + [f"Repository has many files; indexed first {max_files}"]

        return result

    except Exception as e:
        logger.error("index_repo failed — %s/%s: %s", owner, repo, e, exc_info=True)
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
