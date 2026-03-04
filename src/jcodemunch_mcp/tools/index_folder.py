"""Index local folder tool - walk, parse, summarize, save."""

import os
from pathlib import Path
from typing import Optional

import pathspec

from ..parser import parse_file, LANGUAGE_EXTENSIONS
from ..security import (
    validate_path,
    is_symlink_escape,
    is_secret_file,
    is_binary_file,
    should_exclude_file,
    DEFAULT_MAX_FILE_SIZE,
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
    # Normalize path separators for matching
    normalized = path.replace("\\", "/")
    for pattern in SKIP_PATTERNS:
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
    max_files: int = 500,
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
) -> tuple[list[Path], list[str]]:
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
    files = []
    warnings = []
    root = folder_path.resolve()

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
            continue
        if file_path.is_symlink() and is_symlink_escape(root, file_path):
            warnings.append(f"Skipped symlink escape: {file_path}")
            continue

        # Path traversal check
        if not validate_path(root, file_path):
            warnings.append(f"Skipped path traversal: {file_path}")
            continue

        # Get relative path for pattern matching
        try:
            rel_path = file_path.relative_to(root).as_posix()
        except ValueError:
            continue

        # Skip patterns
        if should_skip_file(rel_path):
            continue

        # .gitignore matching
        if gitignore_spec and gitignore_spec.match_file(rel_path):
            continue

        # Extra ignore patterns
        if extra_spec and extra_spec.match_file(rel_path):
            continue

        # Secret detection
        if is_secret_file(rel_path):
            warnings.append(f"Skipped secret file: {rel_path}")
            continue

        # Extension filter
        ext = file_path.suffix
        if ext not in LANGUAGE_EXTENSIONS:
            continue

        # Size limit
        try:
            if file_path.stat().st_size > max_size:
                continue
        except OSError:
            continue

        # Binary detection (content sniff for files with source extensions)
        if is_binary_file(file_path):
            warnings.append(f"Skipped binary file: {rel_path}")
            continue

        files.append(file_path)

    # File count limit with prioritization
    if len(files) > max_files:
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

    return files, warnings


def index_folder(
    path: str,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
    incremental: bool = False,
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

    try:
        # Discover source files (with security filtering)
        source_files, discover_warnings = discover_local_files(
            folder_path,
            extra_ignore_patterns=extra_ignore_patterns,
            follow_symlinks=follow_symlinks,
        )
        warnings.extend(discover_warnings)

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
            if ext not in LANGUAGE_EXTENSIONS:
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
            languages: dict[str, int] = {}
            raw_files_subset: dict[str, str] = {}

            for rel_path in files_to_parse:
                content = current_files[rel_path]
                ext = os.path.splitext(rel_path)[1]
                language = LANGUAGE_EXTENSIONS.get(ext)
                if not language:
                    continue
                try:
                    symbols = parse_file(content, rel_path, language)
                    if symbols:
                        new_symbols.extend(symbols)
                        raw_files_subset[rel_path] = content
                except Exception as e:
                    warnings.append(f"Failed to parse {rel_path}: {e}")

            new_symbols = summarize_symbols(new_symbols, use_ai=use_ai_summaries)

            # Compute updated language counts from all current files
            for rel_path in current_files:
                ext = os.path.splitext(rel_path)[1]
                lang = LANGUAGE_EXTENSIONS.get(ext)
                if lang:
                    languages[lang] = languages.get(lang, 0) + 1

            from ..storage.index_store import _get_git_head
            git_head = _get_git_head(folder_path) or ""

            updated = store.incremental_save(
                owner=owner, name=repo_name,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_symbols=new_symbols, raw_files=raw_files_subset,
                languages=languages, git_head=git_head,
            )

            result = {
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "folder_path": str(folder_path),
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "symbol_count": len(updated.symbols) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
            }
            if warnings:
                result["warnings"] = warnings
            return result

        # Full index path
        all_symbols = []
        languages = {}
        raw_files = {}
        parsed_files = []

        for rel_path, content in current_files.items():
            ext = os.path.splitext(rel_path)[1]
            language = LANGUAGE_EXTENSIONS.get(ext)
            if not language:
                continue
            try:
                symbols = parse_file(content, rel_path, language)
                if symbols:
                    all_symbols.extend(symbols)
                    languages[language] = languages.get(language, 0) + 1
                    raw_files[rel_path] = content
                    parsed_files.append(rel_path)
            except Exception as e:
                warnings.append(f"Failed to parse {rel_path}: {e}")
                continue

        if not all_symbols:
            return {"success": False, "error": "No symbols extracted from files"}

        # Generate summaries
        all_symbols = summarize_symbols(all_symbols, use_ai=use_ai_summaries)

        # Save index
        store.save_index(
            owner=owner,
            name=repo_name,
            source_files=parsed_files,
            symbols=all_symbols,
            raw_files=raw_files,
            languages=languages
        )

        result = {
            "success": True,
            "repo": f"{owner}/{repo_name}",
            "folder_path": str(folder_path),
            "indexed_at": store.load_index(owner, repo_name).indexed_at,
            "file_count": len(parsed_files),
            "symbol_count": len(all_symbols),
            "languages": languages,
            "files": parsed_files[:20],  # Limit files in response
        }

        if warnings:
            result["warnings"] = warnings

        if len(source_files) >= 500:
            result["note"] = "Folder has many files; indexed first 500"

        return result

    except Exception as e:
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
