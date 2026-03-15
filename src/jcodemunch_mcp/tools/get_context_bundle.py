"""Get a context bundle: symbol definition + file imports."""

import re
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided as _cost_avoided
from ._utils import resolve_repo


def _make_meta(timing_ms: float, **kwargs) -> dict:
    meta = {"timing_ms": round(timing_ms, 1)}
    meta.update(kwargs)
    return meta


# Import patterns per language: list of compiled regexes that match a single import line.
# For block-style imports (Go), we handle them separately.
_IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "python":     [re.compile(r"^\s*(import |from \S+ import )")],
    "javascript": [re.compile(r"^\s*(import |.*\brequire\s*\()")],
    "typescript": [re.compile(r"^\s*(import |.*\brequire\s*\()")],
    "tsx":        [re.compile(r"^\s*(import |.*\brequire\s*\()")],
    "go":         [re.compile(r"^\s*import\b")],
    "rust":       [re.compile(r"^\s*use \S")],
    "java":       [re.compile(r"^\s*import \S")],
    "kotlin":     [re.compile(r"^\s*import \S")],
    "csharp":     [re.compile(r"^\s*using \S")],
    "c":          [re.compile(r"^\s*#\s*include\b")],
    "cpp":        [re.compile(r"^\s*#\s*include\b")],
    "swift":      [re.compile(r"^\s*import \S")],
    "ruby":       [re.compile(r"^\s*(require |require_relative )")],
    "php":        [re.compile(r"^\s*(use |require|include)\b")],
    "elixir":     [re.compile(r"^\s*(import |alias |use |require )\S")],
    "scala":      [re.compile(r"^\s*import \S")],
    "haskell":    [re.compile(r"^\s*import \S")],
    "lua":        [re.compile(r"^\s*(require\s*[\(\"])")],
    "dart":       [re.compile(r"^\s*import \S")],
}


def _extract_imports(content: str, language: str) -> list[str]:
    """Extract import lines from file content for the given language."""
    patterns = _IMPORT_PATTERNS.get(language, [])
    if not patterns:
        return []

    lines = content.splitlines()
    imports: list[str] = []

    if language == "go":
        # Go has block imports: import ( ... ) as well as single-line imports
        in_block = False
        for line in lines:
            stripped = line.strip()
            if stripped == "import (":
                in_block = True
                imports.append(line)
                continue
            if in_block:
                imports.append(line)
                if stripped == ")":
                    in_block = False
                continue
            if any(p.match(line) for p in patterns):
                imports.append(line)
        return imports

    for line in lines:
        if any(p.match(line) for p in patterns):
            imports.append(line)

    return imports


def get_context_bundle(
    repo: str,
    symbol_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Get a context bundle: symbol definition + imports from its file.

    Returns the symbol's full source and all import/require statements from
    the same file — giving an AI just enough context to understand and modify
    the symbol without loading the entire file.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol_id: Symbol ID from get_file_outline or search_symbols.
        storage_path: Custom storage path.

    Returns:
        Dict with symbol details, source, imports list, and _meta envelope.
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    symbol = index.get_symbol(symbol_id)
    if not symbol:
        return {"error": f"Symbol not found: {symbol_id}"}

    source = store.get_symbol_content(owner, name, symbol_id, _index=index)
    file_content = store.get_file_content(owner, name, symbol["file"], _index=index)

    imports: list[str] = []
    if file_content:
        language = symbol.get("language", "")
        imports = _extract_imports(file_content, language)

    # Token savings
    raw_bytes = 0
    try:
        raw_file = store._content_dir(owner, name) / symbol["file"]
        import os
        raw_bytes = os.path.getsize(raw_file)
    except OSError:
        pass
    tokens_saved = estimate_savings(raw_bytes, symbol.get("byte_length", 0))
    total_saved = record_savings(tokens_saved)

    elapsed = (time.perf_counter() - start) * 1000

    meta = {
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
    }
    meta.update(_cost_avoided(tokens_saved, total_saved))

    return {
        "symbol_id": symbol["id"],
        "name": symbol["name"],
        "kind": symbol["kind"],
        "file": symbol["file"],
        "line": symbol["line"],
        "end_line": symbol["end_line"],
        "signature": symbol["signature"],
        "docstring": symbol.get("docstring", ""),
        "source": source or "",
        "imports": imports,
        "_meta": _make_meta(elapsed, **meta),
    }
