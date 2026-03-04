"""Get symbol source code."""

import hashlib
import os
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided as _cost_avoided
from ._utils import resolve_repo


def _make_meta(timing_ms: float, **kwargs) -> dict:
    """Build a _meta envelope dict."""
    meta = {"timing_ms": round(timing_ms, 1)}
    meta.update(kwargs)
    return meta


def get_symbol(
    repo: str,
    symbol_id: str,
    verify: bool = False,
    context_lines: int = 0,
    storage_path: Optional[str] = None
) -> dict:
    """Get full source of a specific symbol.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol_id: Symbol ID from get_file_outline or search_symbols.
        verify: If True, re-read source and verify content hash matches.
        context_lines: Number of lines before/after the symbol to include.
        storage_path: Custom storage path.

    Returns:
        Dict with symbol details, source code, and _meta envelope.
    """
    start = time.perf_counter()
    context_lines = max(0, min(context_lines, 50))

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

    # Get source via byte-offset read
    source = store.get_symbol_content(owner, name, symbol_id)

    # Add context lines if requested
    context_before = ""
    context_after = ""
    if context_lines > 0 and source:
        file_path = store._content_dir(owner, name) / symbol["file"]
        if file_path.exists():
            try:
                all_lines = file_path.read_text(encoding="utf-8", errors="replace").split("\n")
                start_line = symbol["line"] - 1  # 0-indexed
                end_line = symbol["end_line"]     # exclusive
                before_start = max(0, start_line - context_lines)
                after_end = min(len(all_lines), end_line + context_lines)
                if before_start < start_line:
                    context_before = "\n".join(all_lines[before_start:start_line])
                if end_line < after_end:
                    context_after = "\n".join(all_lines[end_line:after_end])
            except Exception:
                pass

    meta = {}
    if verify and source:
        actual_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        stored_hash = symbol.get("content_hash", "")
        meta["content_verified"] = actual_hash == stored_hash if stored_hash else None

    # Token savings: raw file size vs symbol byte length
    raw_bytes = 0
    try:
        raw_file = store._content_dir(owner, name) / symbol["file"]
        raw_bytes = os.path.getsize(raw_file)
    except OSError:
        pass
    tokens_saved = estimate_savings(raw_bytes, symbol.get("byte_length", 0))
    total_saved = record_savings(tokens_saved)
    meta["tokens_saved"] = tokens_saved
    meta["total_tokens_saved"] = total_saved
    meta.update(_cost_avoided(tokens_saved, total_saved))

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "id": symbol["id"],
        "kind": symbol["kind"],
        "name": symbol["name"],
        "file": symbol["file"],
        "line": symbol["line"],
        "end_line": symbol["end_line"],
        "signature": symbol["signature"],
        "decorators": symbol.get("decorators", []),
        "docstring": symbol.get("docstring", ""),
        "content_hash": symbol.get("content_hash", ""),
        "source": source or "",
        "_meta": _make_meta(elapsed, **meta),
    }

    if context_before:
        result["context_before"] = context_before
    if context_after:
        result["context_after"] = context_after

    return result


def get_symbols(
    repo: str,
    symbol_ids: list[str],
    storage_path: Optional[str] = None
) -> dict:
    """Get full source of multiple symbols.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol_ids: List of symbol IDs.
        storage_path: Custom storage path.

    Returns:
        Dict with symbols list, errors, and _meta envelope.
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

    symbols = []
    errors = []

    for symbol_id in symbol_ids:
        symbol = index.get_symbol(symbol_id)

        if not symbol:
            errors.append({"id": symbol_id, "error": f"Symbol not found: {symbol_id}"})
            continue

        source = store.get_symbol_content(owner, name, symbol_id)

        symbols.append({
            "id": symbol["id"],
            "kind": symbol["kind"],
            "name": symbol["name"],
            "file": symbol["file"],
            "line": symbol["line"],
            "end_line": symbol["end_line"],
            "signature": symbol["signature"],
            "decorators": symbol.get("decorators", []),
            "docstring": symbol.get("docstring", ""),
            "content_hash": symbol.get("content_hash", ""),
            "source": source or ""
        })

    # Token savings: unique file sizes vs sum of symbol byte_lengths
    raw_bytes = 0
    seen_files: set = set()
    response_bytes = 0
    for symbol_id in symbol_ids:
        symbol = index.get_symbol(symbol_id)
        if not symbol:
            continue
        f = symbol["file"]
        if f not in seen_files:
            seen_files.add(f)
            try:
                raw_bytes += os.path.getsize(store._content_dir(owner, name) / f)
            except OSError:
                pass
        response_bytes += symbol.get("byte_length", 0)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved)

    elapsed = (time.perf_counter() - start) * 1000

    return {
        "symbols": symbols,
        "errors": errors,
        "_meta": _make_meta(elapsed, symbol_count=len(symbols),
                            tokens_saved=tokens_saved, total_tokens_saved=total_saved,
                            **_cost_avoided(tokens_saved, total_saved)),
    }
