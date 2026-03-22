"""Index local folder tool - walk, parse, summarize, save."""

from collections.abc import Generator
import hashlib
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional
import re

import pathspec

logger = logging.getLogger(__name__)

from ..parser import parse_file, LANGUAGE_EXTENSIONS, get_language_for_path
from ..parser.context import discover_providers, enrich_symbols, collect_metadata
from ..parser.imports import extract_imports
from ..security import (
    validate_path,
    is_symlink_escape,
    is_secret_file,
    is_binary_file,
    should_exclude_file,
    DEFAULT_MAX_FILE_SIZE,
    get_max_folder_files,
    get_extra_ignore_patterns,
    SKIP_DIRECTORIES,
    SKIP_FILES
)
from ..storage import IndexStore
from ..storage.index_store import _file_hash, _get_git_head
from ..summarizer import summarize_symbols

SKIP_DIRS_REGEX = re.compile("^(" + "|".join(SKIP_DIRECTORIES) + ")$")
SKIP_FILES_REGEX = re.compile("(" + "|".join(re.escape(p) for p in SKIP_FILES) + ")$")

def get_filtered_files(path: str) -> Generator[str, None, None]:
    """Generator function to filter directories and files"""
    # Use os.walk with followlinks=False to avoid infinite loops caused by
    # NTFS junctions or symlinks pointing back to ancestor directories.
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        # Don't walk directories that should be skipped
        dirnames[:] = [dir for dir in dirnames if not SKIP_DIRS_REGEX.match(dir)]
        dpath = Path(dirpath)
        for file in filenames:
            if not SKIP_FILES_REGEX.search(file):
                yield dpath / file

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


def _is_gitignored_fast(resolved_str: str, specs: list[tuple[str, "pathspec.PathSpec"]]) -> bool:
    """String-based gitignore check — avoids Path.relative_to() overhead.

    Same semantics as _is_gitignored but uses string prefix matching instead
    of Path operations (~10x faster in the inner loop).
    """
    for dir_prefix, spec in specs:
        if not resolved_str.startswith(dir_prefix):
            continue
        rel = resolved_str[len(dir_prefix):].replace("\\", "/")
        if spec.match_file(rel):
            return True
    return False


def _local_repo_name(folder_path: Path) -> str:
    """Stable local repo id derived from basename + resolved path hash."""
    digest = hashlib.sha1(str(folder_path).encode("utf-8")).hexdigest()[:8]
    return f"{folder_path.name}-{digest}"


from ._indexing_pipeline import (
    file_languages_for_paths as _file_languages_for_paths,
    language_counts as _language_counts,
    complete_file_summaries as _complete_file_summaries,
    parse_and_prepare_incremental,
    parse_and_prepare_full,
)


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
        follow_symlinks: Whether to include symlinked files in indexing.
            Symlinked directories are never followed to prevent infinite
            loops from circular symlinks. Default False for safety.

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

    # Pre-compute string-based gitignore specs for the hot loop (avoids
    # Path.relative_to() + exception handling per file per spec).
    gitignore_str_specs: list[tuple[str, pathspec.PathSpec]] = [
        (str(d) + os.sep, spec) for d, spec in gitignore_specs.items()
    ] if gitignore_specs else []

    # Pre-compute root path strings (root is already resolved above)
    root_str = str(root)
    root_prefix = root_str + os.sep

    # Merge env-var global patterns with per-call patterns, then build spec
    effective_extra = get_extra_ignore_patterns(extra_ignore_patterns)
    extra_spec = None
    if effective_extra:
        try:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", effective_extra)
        except Exception:
            pass


    for file_path in get_filtered_files(str(folder_path)):
        # Symlink protection
        if not follow_symlinks and file_path.is_symlink():
            skip_counts["symlink"] += 1
            logger.debug("SKIP symlink: %s", file_path)
            continue
        if file_path.is_symlink() and is_symlink_escape(root, file_path):
            skip_counts["symlink_escape"] += 1
            warnings.append(f"Skipped symlink escape: {file_path}")
            continue

        # Resolve once per file — reused for traversal check, relative path,
        # and gitignore matching (was resolved 2-3x before this optimization).
        try:
            resolved = file_path.resolve()
        except OSError:
            skip_counts["unreadable"] += 1
            logger.debug("SKIP unreadable (resolve failed): %s", file_path)
            continue
        resolved_str = str(resolved)

        # Path traversal check (same logic as validate_path but avoids
        # re-resolving root on every iteration)
        if not (resolved_str == root_str or resolved_str.startswith(root_prefix)):
            skip_counts["path_traversal"] += 1
            warnings.append(f"Skipped path traversal: {file_path}")
            continue

        # Get relative path via string slicing (avoids Path.relative_to)
        rel_path = resolved_str[len(root_prefix):].replace("\\", "/") if resolved_str != root_str else ""
        if not rel_path:
            continue

        # .gitignore matching (string-based, avoids Path.relative_to per spec)
        if gitignore_str_specs and _is_gitignored_fast(resolved_str, gitignore_str_specs):
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
    context_providers: bool = True,
    changed_paths: Optional[list[tuple[str, str]]] = None,
) -> dict:
    """Index a local folder containing source code.

    Args:
        path: Path to local folder (absolute or relative).
        use_ai_summaries: Whether to use AI for symbol summaries.
        storage_path: Custom storage path (default: ~/.code-index/).
        extra_ignore_patterns: Additional gitignore-style patterns to exclude.
        follow_symlinks: Whether to include symlinked files. Symlinked directories
            are never followed (prevents infinite loops). Default False.
        context_providers: Whether to run context providers (default True).
            Set to False or set JCODEMUNCH_CONTEXT_PROVIDERS=0 to disable.
        incremental: When True and an existing index exists, only re-index changed files.
        changed_paths: Optional pre-known change set from the watcher, as a list of
            (change_type, absolute_path) tuples where change_type is one of
            "added", "modified", "deleted".  When provided with incremental=True
            and an existing index, skips full directory discovery (~3s → ~50ms).

    Returns:
        Dict with indexing results.
    """
    # Resolve folder path
    folder_path = Path(path).expanduser().resolve()

    if not folder_path.exists():
        return {"success": False, "error": f"Folder not found: {path}"}

    if not folder_path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}

    # Guard against dangerously broad roots.  A relative path like "." resolves
    # against the MCP server's CWD (not the caller's project directory), which
    # can be "/" or "~" when the server is launched by a system launcher.
    # Reject paths with fewer than 3 parts (e.g. "/", "/home", "C:\Users") and
    # warn whenever the caller supplied a relative path so the resolved value is
    # always visible in the tool response.
    _MIN_PATH_PARTS = 3
    if len(folder_path.parts) < _MIN_PATH_PARTS:
        return {
            "success": False,
            "error": (
                f"Resolved path '{folder_path}' is too broad to index safely "
                f"(fewer than {_MIN_PATH_PARTS} path components). "
                "Pass an absolute path to the specific project directory instead of a "
                "relative path like '.' — relative paths resolve against the MCP "
                "server's working directory, which may not be your project root."
            ),
        }

    warnings = []

    # Warn when a relative path was given so callers can see what it resolved to.
    if not Path(path).expanduser().is_absolute():
        warnings.append(
            f"Relative path '{path}' resolved to '{folder_path}' (MCP server CWD). "
            "Prefer passing an absolute path to avoid unexpected behaviour."
        )

    max_files = get_max_folder_files()

    try:
        t0 = time.monotonic()

        # ── Fast path: watcher-driven incremental reindex ──
        # When the watcher provides the exact change set, skip full directory
        # discovery (~3s on Windows) and only process the affected files.
        if changed_paths and incremental:
            repo_name = _local_repo_name(folder_path)
            owner = "local"
            store = IndexStore(base_path=storage_path)
            existing_index = store.load_index(owner, repo_name)

            if existing_index is not None:
                # Skip discover_providers on the watcher fast path — provider
                # detection walks the tree (~500ms) and providers don't change
                # between file edits.  The initial index_folder call (without
                # changed_paths) already ran provider detection.
                active_providers = []

                # Classify watcher events into changed/new/deleted rel_paths
                changed_files: list[str] = []
                new_files: list[str] = []
                deleted_files: list[str] = []
                rel_path_map_fast: dict[str, Path] = {}

                for change_type, abs_path_str in changed_paths:
                    abs_path = Path(abs_path_str)
                    try:
                        rel_path = abs_path.relative_to(folder_path).as_posix()
                    except ValueError:
                        continue
                    # Skip non-source files
                    ext = abs_path.suffix
                    if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(abs_path)) is None:
                        continue

                    if change_type == "deleted":
                        if existing_index.has_source_file(rel_path):
                            deleted_files.append(rel_path)
                    elif change_type == "added":
                        if not existing_index.has_source_file(rel_path):
                            new_files.append(rel_path)
                            rel_path_map_fast[rel_path] = abs_path
                        else:
                            # File exists in index but watcher says "added" (e.g. recreated)
                            changed_files.append(rel_path)
                            rel_path_map_fast[rel_path] = abs_path
                    else:  # modified
                        changed_files.append(rel_path)
                        rel_path_map_fast[rel_path] = abs_path

                if not changed_files and not new_files and not deleted_files:
                    return {
                        "success": True,
                        "message": "No changes detected",
                        "repo": f"{owner}/{repo_name}",
                        "folder_path": str(folder_path),
                        "changed": 0, "new": 0, "deleted": 0,
                        "duration_seconds": round(time.monotonic() - t0, 2),
                    }

                # Read and hash only the changed/new files.
                # For "modified" files, compare hash against stored hash —
                # if content is identical (e.g. touch, save-without-change),
                # skip re-parsing and just update the mtime.
                old_hashes = existing_index.file_hashes or {}
                actually_changed: list[str] = []
                raw_files_subset: dict[str, str] = {}
                subset_hashes: dict[str, str] = {}
                fast_mtimes: dict[str, int] = {}
                fast_warnings: list[str] = []
                mtime_only_updates: dict[str, int] = {}

                for rel_path in set(changed_files) | set(new_files):
                    abs_path = rel_path_map_fast[rel_path]
                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                            content = f.read()
                    except Exception as e:
                        fast_warnings.append(f"Failed to read {abs_path}: {e}")
                        continue
                    new_hash = _file_hash(content)
                    try:
                        cur_mtime = os.stat(abs_path).st_mtime_ns
                    except OSError:
                        cur_mtime = None

                    # Content unchanged — skip parse, just record new mtime
                    if rel_path in changed_files and new_hash == old_hashes.get(rel_path, ""):
                        if cur_mtime is not None:
                            mtime_only_updates[rel_path] = cur_mtime
                        continue

                    raw_files_subset[rel_path] = content
                    subset_hashes[rel_path] = new_hash
                    if cur_mtime is not None:
                        fast_mtimes[rel_path] = cur_mtime
                    if rel_path in changed_files:
                        actually_changed.append(rel_path)

                # Replace changed_files with only the truly changed ones
                changed_files = actually_changed

                # If only mtimes changed (no content changes, no new, no deleted),
                # update mtimes in DB and return early — no parsing needed.
                if not changed_files and not new_files and not deleted_files:
                    if mtime_only_updates:
                        # Update mtimes directly via incremental_save with empty deltas
                        store.incremental_save(
                            owner=owner, name=repo_name,
                            changed_files=[], new_files=[], deleted_files=[],
                            new_symbols=[], raw_files={},
                            file_mtimes=mtime_only_updates,
                        )
                    return {
                        "success": True,
                        "message": "No changes detected",
                        "repo": f"{owner}/{repo_name}",
                        "folder_path": str(folder_path),
                        "fast_path": True,
                        "changed": 0, "new": 0, "deleted": 0,
                        "duration_seconds": round(time.monotonic() - t0, 2),
                    }

                files_to_parse = set(changed_files) | set(new_files)
                new_symbols, incr_file_summaries, incr_file_languages, incr_file_imports, incremental_no_symbols = (
                    parse_and_prepare_incremental(
                        files_to_parse=files_to_parse,
                        file_contents=raw_files_subset,
                        active_providers=active_providers,
                        use_ai_summaries=use_ai_summaries,
                        warnings=fast_warnings,
                    )
                )

                git_head = _get_git_head(folder_path) or ""
                incr_context_metadata = collect_metadata(active_providers) if active_providers else None

                # Merge mtime-only updates so they're persisted alongside real changes
                all_mtimes = {**mtime_only_updates, **fast_mtimes}

                updated = store.incremental_save(
                    owner=owner, name=repo_name,
                    changed_files=changed_files, new_files=new_files, deleted_files=deleted_files,
                    new_symbols=new_symbols,
                    raw_files=raw_files_subset,
                    git_head=git_head,
                    file_summaries=incr_file_summaries,
                    file_languages=incr_file_languages,
                    imports=incr_file_imports,
                    context_metadata=incr_context_metadata,
                    file_hashes=subset_hashes,
                    file_mtimes=all_mtimes,
                )

                result = {
                    "success": True,
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": str(folder_path),
                    "incremental": True,
                    "fast_path": True,
                    "changed": len(changed_files), "new": len(new_files), "deleted": len(deleted_files),
                    "symbol_count": len(updated.symbols) if updated else 0,
                    "indexed_at": updated.indexed_at if updated else "",
                    "duration_seconds": round(time.monotonic() - t0, 2),
                }
                if fast_warnings:
                    result["warnings"] = fast_warnings
                return result

        # ── Standard path: full directory discovery ──
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

        # Discover context providers (dbt, terraform, etc.)
        _providers_enabled = context_providers and os.environ.get("JCODEMUNCH_CONTEXT_PROVIDERS", "1") != "0"
        active_providers = discover_providers(folder_path) if _providers_enabled else []
        if active_providers:
            names = ", ".join(p.name for p in active_providers)
            logger.info("Active context providers: %s", names)

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

        # Discovery pass — resolve rel_paths and collect mtimes without
        # reading file contents (P2-5: avoids 200MB-1GB allocation
        # for large projects). Content is read on-demand later.
        file_mtimes: dict[str, int] = {}
        rel_path_map: dict[str, Path] = {}  # rel_path -> absolute Path
        for file_path in source_files:
            if not validate_path(folder_path, file_path):
                continue
            try:
                rel_path = file_path.relative_to(folder_path).as_posix()
            except ValueError:
                continue
            ext = file_path.suffix
            if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(file_path)) is None:
                continue
            try:
                file_mtimes[rel_path] = os.stat(file_path).st_mtime_ns
            except OSError as e:
                warnings.append(f"Failed to stat {file_path}: {e}")
                continue
            rel_path_map[rel_path] = file_path

        def _read_file(rel_path: str) -> str | None:
            """Re-read a file by its rel_path. Returns content or None on error."""
            abs_path = rel_path_map[rel_path]
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    return f.read()
            except Exception as e:
                warnings.append(f"Failed to read {abs_path}: {e}")
                return None

        def _hash_file(rel_path: str) -> str:
            """Read and hash a single file on demand."""
            abs_path = rel_path_map[rel_path]
            with open(abs_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                return _file_hash(f.read())

        # Incremental path: detect changes using mtime fast-path
        if incremental and existing_index is not None:
            changed, new, deleted, computed_hashes, updated_mtimes = (
                store.detect_changes_with_mtimes(
                    owner, repo_name, file_mtimes, _hash_file
                )
            )

            if not changed and not new and not deleted:
                return {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": str(folder_path),
                    "changed": 0, "new": 0, "deleted": 0,
                    "duration_seconds": round(time.monotonic() - t0, 2),
                }

            # Read changed + new files into memory
            files_to_parse = set(changed) | set(new)
            raw_files_subset: dict[str, str] = {}
            subset_hashes: dict[str, str] = {}
            for rel_path in files_to_parse:
                content = _read_file(rel_path)
                if content is None:
                    continue
                raw_files_subset[rel_path] = content
                subset_hashes[rel_path] = computed_hashes.get(rel_path, _file_hash(content))

            # Shared pipeline: parse, enrich, summarize, extract metadata
            new_symbols, incr_file_summaries, incr_file_languages, incr_file_imports, incremental_no_symbols = (
                parse_and_prepare_incremental(
                    files_to_parse=files_to_parse,
                    file_contents=raw_files_subset,
                    active_providers=active_providers,
                    use_ai_summaries=use_ai_summaries,
                    warnings=warnings,
                )
            )

            git_head = _get_git_head(folder_path) or ""
            incr_context_metadata = collect_metadata(active_providers) if active_providers else None

            updated = store.incremental_save(
                owner=owner, name=repo_name,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_symbols=new_symbols,
                raw_files=raw_files_subset,
                git_head=git_head,
                file_summaries=incr_file_summaries,
                file_languages=incr_file_languages,
                imports=incr_file_imports,
                context_metadata=incr_context_metadata,
                file_hashes=subset_hashes,
                file_mtimes=updated_mtimes,
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

        # Full index path — stream through files one at a time to avoid
        # loading all contents into memory simultaneously.
        # Compute hashes and collect mtimes during the per-file loop.
        file_hashes: dict[str, str] = {}
        all_symbols = []
        symbols_by_file: dict[str, list] = defaultdict(list)
        source_file_list = sorted(file_mtimes)
        file_imports: dict[str, list[dict]] = {}
        content_dir = store._content_dir(owner, repo_name)
        content_dir.mkdir(parents=True, exist_ok=True)

        no_symbols_files: list[str] = []
        for rel_path in source_file_list:
            content = _read_file(rel_path)
            if content is None:
                continue

            # Compute hash while content is in memory
            file_hashes[rel_path] = _file_hash(content)

            # Write raw content to cache immediately, then process
            file_dest = store._safe_content_path(content_dir, rel_path)
            if file_dest:
                file_dest.parent.mkdir(parents=True, exist_ok=True)
                store._write_cached_text(file_dest, content)

            language = get_language_for_path(rel_path)
            if not language:
                no_symbols_files.append(rel_path)
                # content eligible for GC after this iteration
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

            # Extract imports while content is in scope
            imps = extract_imports(content, rel_path, language)
            if imps:
                file_imports[rel_path] = imps
            # content is discarded at end of iteration

        logger.info(
            "Parsing complete — with symbols: %d, no symbols: %d",
            len(symbols_by_file),
            len(no_symbols_files),
        )

        # Enrich with context providers before summarization
        if active_providers and all_symbols:
            enrich_symbols(all_symbols, active_providers)

        # Generate summaries
        if all_symbols:
            all_symbols = summarize_symbols(all_symbols, use_ai=use_ai_summaries)

        # Generate file-level summaries (single-pass grouping) using shared helpers
        file_symbols_map = defaultdict(list)
        for s in all_symbols:
            file_symbols_map[s.file].append(s)
        file_languages = _file_languages_for_paths(source_file_list, file_symbols_map)
        languages = _language_counts(file_languages)
        file_summaries = _complete_file_summaries(source_file_list, file_symbols_map, context_providers=active_providers)

        # Collect structured metadata from providers
        full_context_metadata = collect_metadata(active_providers) if active_providers else None

        # Save index — raw files already written to content dir above,
        # pass empty dict to skip duplicate writes.
        index = store.save_index(
            owner=owner,
            name=repo_name,
            source_files=source_file_list,
            symbols=all_symbols,
            raw_files={},
            languages=languages,
            file_hashes=file_hashes,
            file_summaries=file_summaries,
            git_head=_get_git_head(folder_path) or "",
            source_root=str(folder_path),
            file_languages=file_languages,
            display_name=folder_path.name,
            imports=file_imports,
            context_metadata=full_context_metadata,
            file_mtimes=file_mtimes,
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

        # Report context enrichment stats from all active providers
        if active_providers:
            enrichment = {}
            for provider in active_providers:
                enrichment[provider.name] = provider.stats()
            result["context_enrichment"] = enrichment

        if warnings:
            result["warnings"] = warnings

        if skip_counts.get("file_limit", 0) > 0:
            result["note"] = f"Folder has many files; indexed first {max_files}"

        return result

    except Exception as e:
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
