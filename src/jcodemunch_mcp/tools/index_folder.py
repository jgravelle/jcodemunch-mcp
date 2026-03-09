"""Index local folder tool - walk, parse, summarize, save."""

import hashlib
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pathspec

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
    get_max_folder_files,
    get_extra_ignore_patterns,
    SKIP_PATTERNS,
)
from ..storage import IndexStore
from ..storage.index_store import _file_hash, _get_git_head
from ..summarizer import summarize_symbols


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


def _load_all_gitignores(root: Path) -> dict[Path, pathspec.PathSpec]:
    """Load all .gitignore files in the tree, keyed by their directory.

    Supports monorepos and poncho-style projects where subdirectories each
    have their own .gitignore (e.g. cap/.gitignore, core/.gitignore).

    Uses os.walk(followlinks=False) to avoid infinite loops caused by
    NTFS junctions or symlinks pointing back to ancestor directories.
    """
    specs: dict[Path, pathspec.PathSpec] = {}
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        if ".gitignore" in filenames:
            gitignore_path = Path(dirpath) / ".gitignore"
            try:
                content = gitignore_path.read_text(encoding="utf-8", errors="replace")
                spec = pathspec.PathSpec.from_lines("gitignore", content.splitlines())
                specs[gitignore_path.parent.resolve()] = spec
            except Exception:
                pass
    return specs


def _is_gitignored(file_path: Path, gitignore_specs: dict[Path, pathspec.PathSpec]) -> bool:
    """Check if a file is excluded by any .gitignore in its ancestor chain.

    Each spec is applied relative to its own directory, matching standard git behaviour.
    """
    for gitignore_dir, spec in gitignore_specs.items():
        try:
            rel = file_path.relative_to(gitignore_dir)
            if spec.match_file(rel.as_posix()):
                return True
        except ValueError:
            continue
    return False


def _local_repo_name(folder_path: Path) -> str:
    """Stable local repo id derived from basename + resolved path hash."""
    digest = hashlib.sha1(str(folder_path).encode("utf-8")).hexdigest()[:8]
    return f"{folder_path.name}-{digest}"


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
    max_files = get_max_folder_files(max_files)
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

    # Load all .gitignore files in the tree (root + all subdirectories)
    gitignore_specs = _load_all_gitignores(root)

    # Merge env-var global patterns with per-call patterns, then build spec
    effective_extra = get_extra_ignore_patterns(extra_ignore_patterns)
    extra_spec = None
    if effective_extra:
        try:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", effective_extra)
        except Exception:
            pass

    # Use os.walk with followlinks=False to avoid infinite loops caused by
    # NTFS junctions or symlinks pointing back to ancestor directories.
    raw_walk = (
        Path(dirpath) / filename
        for dirpath, dirnames, filenames in os.walk(str(folder_path), followlinks=False)
        for filename in filenames
    )
    for file_path in raw_walk:

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

        # .gitignore matching (root + all nested .gitignore files)
        if gitignore_specs and _is_gitignored(file_path.resolve(), gitignore_specs):
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
    max_files = get_max_folder_files()

    try:
        t0 = time.monotonic()
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
        repo_name = _local_repo_name(folder_path)
        owner = "local"
        store = IndexStore(base_path=storage_path)
        existing_index = store.load_index(owner, repo_name)

        if existing_index is None and store.has_index(owner, repo_name):
            logger.warning(
                "index_folder version_mismatch — %s/%s: on-disk index is a newer version; full re-index required",
                owner, repo_name,
            )
            warnings.append(
                "Existing index was created by a newer version of jcodemunch-mcp "
                "and cannot be read — performing a full re-index. "
                "If you downgraded the package, delete ~/.code-index/ (or your "
                "CODE_INDEX_PATH directory) to remove the stale index."
            )

        # Read all files to build current_files map
        current_files: dict[str, str] = {}
        for file_path in source_files:
            if not validate_path(folder_path, file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    content = f.read()
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
        if incremental and existing_index is not None:
            changed, new, deleted = store.detect_changes(owner, repo_name, current_files)

            if not changed and not new and not deleted:
                return {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": str(folder_path),
                    "changed": 0, "new": 0, "deleted": 0,
                    "duration_seconds": round(time.monotonic() - t0, 2),
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
                language = get_language_for_path(rel_path)
                if not language:
                    incremental_no_symbols.append(rel_path)
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
            incr_file_summaries = _complete_file_summaries(sorted(files_to_parse), incr_symbols_map)
            incr_file_languages = _file_languages_for_paths(sorted(files_to_parse), incr_symbols_map)

            git_head = _get_git_head(folder_path) or ""

            updated = store.incremental_save(
                owner=owner, name=repo_name,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_symbols=new_symbols,
                raw_files=raw_files_subset,
                git_head=git_head,
                file_summaries=incr_file_summaries,
                file_languages=incr_file_languages,
            )

            result = {
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "folder_path": str(folder_path),
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "symbol_count": len(updated.symbols) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "duration_seconds": round(time.monotonic() - t0, 2),
                "discovery_skip_counts": skip_counts,
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
        for rel_path, content in current_files.items():
            language = get_language_for_path(rel_path)
            if not language:
                no_symbols_files.append(rel_path)
                continue
            try:
                symbols = parse_file(content, rel_path, language)
                if symbols:
                    all_symbols.extend(symbols)
                    symbols_by_file[rel_path].extend(symbols)
                else:
                    no_symbols_files.append(rel_path)
                    logger.debug("NO SYMBOLS: %s", rel_path)
            except Exception as e:
                warnings.append(f"Failed to parse {rel_path}: {e}")
                logger.debug("PARSE ERROR: %s — %s", rel_path, e)
                continue

        logger.info(
            "Parsing complete — with symbols: %d, no symbols: %d",
            len(symbols_by_file),
            len(no_symbols_files),
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

        # Save index
        # Track hashes for all discovered source files so incremental change detection
        # does not repeatedly report no-symbol files as "new".
        file_hashes = {
            fp: _file_hash(content)
            for fp, content in current_files.items()
        }
        index = store.save_index(
            owner=owner,
            name=repo_name,
            source_files=source_file_list,
            symbols=all_symbols,
            raw_files=current_files,
            languages=languages,
            file_hashes=file_hashes,
            file_summaries=file_summaries,
            git_head=_get_git_head(folder_path) or "",
            source_root=str(folder_path),
            file_languages=file_languages,
            display_name=folder_path.name,
        )

        result = {
            "success": True,
            "repo": index.repo,
            "folder_path": str(folder_path),
            "indexed_at": index.indexed_at,
            "file_count": len(source_file_list),
            "symbol_count": len(all_symbols),
            "file_summary_count": sum(1 for v in file_summaries.values() if v),
            "languages": languages,
            "files": source_file_list[:20],  # Limit files in response
            "duration_seconds": round(time.monotonic() - t0, 2),
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
