"""Index local folder tool - walk, parse, summarize, save."""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

import pathspec

from collections import defaultdict

logger = logging.getLogger(__name__)

from ..parser import parse_file, LANGUAGE_EXTENSIONS, get_language_for_path
from ..summarizer import generate_file_summaries
from ..security import (
    validate_path,
    is_symlink_escape,
    is_secret_file,
    is_binary_file,
    should_exclude_file,
    DEFAULT_MAX_FILE_SIZE,
    get_max_index_files,
)
from ..storage import IndexStore
from ..summarizer import summarize_symbols


# File patterns to skip (sync with index_repo.py)
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


def _load_gitignore(folder_path: Path) -> Optional[pathspec.PathSpec]:
    """Load .gitignore from the folder root if it exists."""
    gitignore_path = folder_path / ".gitignore"
    if gitignore_path.is_file():
        try:
            content = gitignore_path.read_text(encoding="utf-8", errors="replace")
            return pathspec.PathSpec.from_lines("gitignore", content.splitlines())
        except Exception:
            pass
    return None


def discover_local_files(
    folder_path: Path,
    max_files: Optional[int] = None,
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
) -> tuple[list[Path], list[str], dict[str, int]]:
    """Discover source files in a local folder with security filtering.

    Args:
        folder_path: Root folder to scan (must be resolved).
        max_files: Maximum number of files to index.
        max_size: Maximum file size in bytes.
        extra_ignore_patterns: Additional gitignore-style patterns to exclude.
        follow_symlinks: Whether to follow symlinks (default False for safety).

    Returns:
        Tuple of (list of Path objects for source files, list of warning strings).
    """
    max_files = get_max_index_files(max_files)
    files = []
    warnings = []
    root = folder_path.resolve()

    skip_counts: dict[str, int] = {
        "symlink": 0,
        "symlink_escape": 0,
        "path_traversal": 0,
        "skip_pattern": 0,
        "gitignore": 0,
        "extra_ignore": 0,
        "secret": 0,
        "wrong_extension": 0,
        "too_large": 0,
        "unreadable": 0,
        "binary": 0,
        "file_limit": 0,
    }

    # Load .gitignore
    gitignore_spec = _load_gitignore(root)

    # Build extra ignore spec if provided
    extra_spec = None
    if extra_ignore_patterns:
        try:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", extra_ignore_patterns)
        except Exception:
            pass

    for file_path in folder_path.rglob("*"):
        # Skip directories
        if not file_path.is_file():
            continue

        # Symlink protection
        if not follow_symlinks and file_path.is_symlink():
            skip_counts["symlink"] += 1
            logger.debug("SKIP symlink: %s", file_path)
            continue
        if file_path.is_symlink() and is_symlink_escape(root, file_path):
            skip_counts["symlink_escape"] += 1
            warnings.append(f"Skipped symlink escape: {file_path}")
            continue

        # Path traversal check
        if not validate_path(root, file_path):
            skip_counts["path_traversal"] += 1
            warnings.append(f"Skipped path traversal: {file_path}")
            continue

        # Get relative path for pattern matching
        try:
            rel_path = file_path.relative_to(root).as_posix()
        except ValueError:
            skip_counts["path_traversal"] += 1
            logger.debug("SKIP relative_to_failed: %s", file_path)
            continue

        # Skip patterns
        if should_skip_file(rel_path):
            skip_counts["skip_pattern"] += 1
            logger.debug("SKIP skip_pattern: %s", rel_path)
            continue

        # .gitignore matching
        if gitignore_spec and gitignore_spec.match_file(rel_path):
            skip_counts["gitignore"] += 1
            logger.debug("SKIP gitignore: %s", rel_path)
            continue

        # Extra ignore patterns
        if extra_spec and extra_spec.match_file(rel_path):
            skip_counts["extra_ignore"] += 1
            logger.debug("SKIP extra_ignore: %s", rel_path)
            continue

        # Secret detection
        if is_secret_file(rel_path):
            skip_counts["secret"] += 1
            warnings.append(f"Skipped secret file: {rel_path}")
            continue

        # Extension filter
        ext = file_path.suffix
        if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(file_path)) is None:
            skip_counts["wrong_extension"] += 1
            logger.debug("SKIP wrong_extension: %s", rel_path)
            continue

        # Size limit
        try:
            if file_path.stat().st_size > max_size:
                skip_counts["too_large"] += 1
                logger.debug("SKIP too_large: %s", rel_path)
                continue
        except OSError:
            skip_counts["unreadable"] += 1
            logger.debug("SKIP unreadable (stat failed): %s", rel_path)
            continue

        # Binary detection (content sniff for files with source extensions)
        if is_binary_file(file_path):
            skip_counts["binary"] += 1
            warnings.append(f"Skipped binary file: {rel_path}")
            continue

        logger.debug("ACCEPT: %s", rel_path)
        files.append(file_path)

    logger.info(
        "Discovery complete — accepted: %d, skipped by reason: %s",
        len(files),
        skip_counts,
    )

    # File count limit with prioritization
    if len(files) > max_files:
        skip_counts["file_limit"] = len(files) - max_files
        # Prioritize: src/, lib/, pkg/, cmd/, internal/ first
        priority_dirs = ["src/", "lib/", "pkg/", "cmd/", "internal/"]

        def priority_key(file_path: Path) -> tuple:
            try:
                rel_path = file_path.relative_to(root).as_posix()
            except ValueError:
                return (999, 999, str(file_path))

            # Check if in priority dir
            for i, prefix in enumerate(priority_dirs):
                if rel_path.startswith(prefix):
                    return (i, rel_path.count("/"), rel_path)
            # Not in priority dir - sort after
            return (len(priority_dirs), rel_path.count("/"), rel_path)

        files.sort(key=priority_key)
        files = files[:max_files]

    return files, warnings, skip_counts


def index_folder(
    path: str,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
    incremental: bool = True,
) -> dict:
    """Index a local folder containing source code.

    Args:
        path: Path to local folder (absolute or relative).
        use_ai_summaries: Whether to use AI for symbol summaries.
        storage_path: Custom storage path (default: ~/.code-index/).
        extra_ignore_patterns: Additional gitignore-style patterns to exclude.
        follow_symlinks: Whether to follow symlinks (default False for safety).
        incremental: When True and an existing index exists, only re-index changed files.

    Returns:
        Dict with indexing results.
    """
    # Resolve folder path
    folder_path = Path(path).expanduser().resolve()

    if not folder_path.exists():
        return {"success": False, "error": f"Folder not found: {path}"}

    if not folder_path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}

    warnings = []
    max_files = get_max_index_files()

    try:
        # Discover source files (with security filtering)
        source_files, discover_warnings, skip_counts = discover_local_files(
            folder_path,
            max_files=max_files,
            extra_ignore_patterns=extra_ignore_patterns,
            follow_symlinks=follow_symlinks,
        )
        warnings.extend(discover_warnings)
        logger.info("Discovery skip counts: %s", skip_counts)

        if not source_files:
            return {"success": False, "error": "No source files found"}

        # Create repo identifier from folder path
        repo_name = folder_path.name
        owner = "local"
        store = IndexStore(base_path=storage_path)

        # Read all files to build current_files map
        current_files: dict[str, str] = {}
        for file_path in source_files:
            if not validate_path(folder_path, file_path):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                warnings.append(f"Failed to read {file_path}: {e}")
                continue
            try:
                rel_path = file_path.relative_to(folder_path).as_posix()
            except ValueError:
                continue
            ext = file_path.suffix
            if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(file_path)) is None:
                continue
            current_files[rel_path] = content

        # Incremental path: detect changes and only re-parse affected files
        if incremental and store.load_index(owner, repo_name) is not None:
            changed, new, deleted = store.detect_changes(owner, repo_name, current_files)

            if not changed and not new and not deleted:
                return {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": str(folder_path),
                    "changed": 0, "new": 0, "deleted": 0,
                }

            # Parse only changed + new files
            files_to_parse = set(changed) | set(new)
            new_symbols = []
            raw_files_subset: dict[str, str] = {}

            incremental_no_symbols: list[str] = []
            for rel_path in files_to_parse:
                content = current_files[rel_path]
                # Track file hashes for changed/new files even when symbol extraction yields none.
                raw_files_subset[rel_path] = content
                ext = os.path.splitext(rel_path)[1]
                language = get_language_for_path(rel_path)
                if not language:
                    continue
                try:
                    symbols = parse_file(content, rel_path, language)
                    if symbols:
                        new_symbols.extend(symbols)
                    else:
                        incremental_no_symbols.append(rel_path)
                        logger.debug("NO SYMBOLS (incremental): %s", rel_path)
                except Exception as e:
                    warnings.append(f"Failed to parse {rel_path}: {e}")
                    logger.debug("PARSE ERROR (incremental): %s — %s", rel_path, e)

            logger.info(
                "Incremental parsing — with symbols: %d, no symbols: %d",
                len(new_symbols),
                len(incremental_no_symbols),
            )

            new_symbols = summarize_symbols(new_symbols, use_ai=use_ai_summaries)

            # Generate file summaries for changed/new files
            incr_symbols_map = defaultdict(list)
            for s in new_symbols:
                incr_symbols_map[s.file].append(s)
            incr_file_summaries = generate_file_summaries(dict(incr_symbols_map))

            from ..storage.index_store import _get_git_head
            git_head = _get_git_head(folder_path) or ""

            updated = store.incremental_save(
                owner=owner, name=repo_name,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_symbols=new_symbols, raw_files=raw_files_subset,
                languages={}, git_head=git_head,
                file_summaries=incr_file_summaries,
            )

            result = {
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "folder_path": str(folder_path),
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "symbol_count": len(updated.symbols) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "discovery_skip_counts": skip_counts,
                "no_symbols_count": len(incremental_no_symbols),
                "no_symbols_files": incremental_no_symbols[:50],
            }
            if warnings:
                result["warnings"] = warnings
            return result

        # Full index path
        all_symbols = []
        languages = {}
        raw_files = {}
        parsed_files = []

        no_symbols_files: list[str] = []
        for rel_path, content in current_files.items():
            ext = os.path.splitext(rel_path)[1]
            language = get_language_for_path(rel_path)
            if not language:
                continue
            try:
                symbols = parse_file(content, rel_path, language)
                if symbols:
                    all_symbols.extend(symbols)
                    file_language = symbols[0].language or language
                    languages[file_language] = languages.get(file_language, 0) + 1
                    raw_files[rel_path] = content
                    parsed_files.append(rel_path)
                else:
                    no_symbols_files.append(rel_path)
                    logger.debug("NO SYMBOLS: %s", rel_path)
            except Exception as e:
                warnings.append(f"Failed to parse {rel_path}: {e}")
                logger.debug("PARSE ERROR: %s — %s", rel_path, e)
                continue

        logger.info(
            "Parsing complete — with symbols: %d, no symbols: %d",
            len(parsed_files),
            len(no_symbols_files),
        )

        if not all_symbols:
            return {"success": False, "error": "No symbols extracted from files"}

        # Generate summaries
        all_symbols = summarize_symbols(all_symbols, use_ai=use_ai_summaries)

        # Generate file-level summaries (single-pass grouping)
        file_symbols_map = defaultdict(list)
        for s in all_symbols:
            file_symbols_map[s.file].append(s)
        file_summaries = generate_file_summaries(dict(file_symbols_map))


        # Save index
        # Track hashes for all discovered source files so incremental change detection
        # does not repeatedly report no-symbol files as "new".
        file_hashes = {
            fp: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for fp, content in current_files.items()
        }
        store.save_index(
            owner=owner,
            name=repo_name,
            source_files=parsed_files,
            symbols=all_symbols,
            raw_files=raw_files,
            languages=languages,
            file_hashes=file_hashes,
            file_summaries=file_summaries,
        )

        result = {
            "success": True,
            "repo": f"{owner}/{repo_name}",
            "folder_path": str(folder_path),
            "indexed_at": store.load_index(owner, repo_name).indexed_at,
            "file_count": len(parsed_files),
            "symbol_count": len(all_symbols),
            "file_summary_count": sum(1 for v in file_summaries.values() if v),
            "languages": languages,
            "files": parsed_files[:20],  # Limit files in response
            "discovery_skip_counts": skip_counts,
            "no_symbols_count": len(no_symbols_files),
            "no_symbols_files": no_symbols_files[:50],  # Show up to 50 for inspection
        }

        if warnings:
            result["warnings"] = warnings

        if skip_counts.get("file_limit", 0) > 0:
            result["note"] = f"Folder has many files; indexed first {max_files}"

        return result

    except Exception as e:
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
