"""Index local folder tool - walk, parse, summarize, save."""

import os
from pathlib import Path
from typing import Optional

from ..parser import parse_file, LANGUAGE_EXTENSIONS
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


def discover_local_files(
    folder_path: Path,
    max_files: int = 500,
    max_size: int = 500 * 1024,  # 500KB
) -> list[Path]:
    """Discover source files in a local folder.
    
    Args:
        folder_path: Root folder to scan
        max_files: Maximum number of files to index
        max_size: Maximum file size in bytes
    
    Returns:
        List of Path objects for source files
    """
    files = []
    
    for file_path in folder_path.rglob("*"):
        # Skip directories
        if not file_path.is_file():
            continue
        
        # Get relative path for skip pattern matching
        try:
            rel_path = file_path.relative_to(folder_path).as_posix()
        except ValueError:
            continue
        
        # Skip patterns
        if should_skip_file(rel_path):
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
        
        files.append(file_path)
    
    # File count limit with prioritization
    if len(files) > max_files:
        # Prioritize: src/, lib/, pkg/, cmd/, internal/ first
        priority_dirs = ["src/", "lib/", "pkg/", "cmd/", "internal/"]
        
        def priority_key(file_path: Path) -> tuple:
            try:
                rel_path = file_path.relative_to(folder_path).as_posix()
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
    
    return files


def index_folder(
    path: str,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None
) -> dict:
    """Index a local folder containing source code.
    
    Args:
        path: Path to local folder (absolute or relative)
        use_ai_summaries: Whether to use AI for symbol summaries
        storage_path: Custom storage path (default: ~/.code-index/)
    
    Returns:
        Dict with indexing results
    """
    # Resolve folder path
    folder_path = Path(path).expanduser().resolve()
    
    if not folder_path.exists():
        return {"success": False, "error": f"Folder not found: {path}"}
    
    if not folder_path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}
    
    warnings = []
    
    try:
        # Discover source files
        source_files = discover_local_files(folder_path)
        
        if not source_files:
            return {"success": False, "error": "No source files found"}
        
        # Read and parse files
        all_symbols = []
        languages = {}
        raw_files = {}
        parsed_files = []
        
        for file_path in source_files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                warnings.append(f"Failed to read {file_path}: {e}")
                continue
            
            # Get relative path for storage
            try:
                rel_path = file_path.relative_to(folder_path).as_posix()
            except ValueError:
                warnings.append(f"Could not get relative path for {file_path}")
                continue
            
            # Determine language from extension
            ext = file_path.suffix
            language = LANGUAGE_EXTENSIONS.get(ext)
            
            if not language:
                continue
            
            # Parse file
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
        
        # Create repo identifier from folder path
        # Use folder name as repo name, parent as "owner"
        repo_name = folder_path.name
        owner = "local"
        
        # Save index
        store = IndexStore(base_path=storage_path)
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
