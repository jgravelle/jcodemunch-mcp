"""Summarize all symbols in an existing index using AI."""

import logging
import time
from typing import Optional

from ..storage import IndexStore
from ..summarizer import summarize_symbols
from ._utils import index_status_to_tool_error, resolve_repo

logger = logging.getLogger(__name__)


def _dict_to_symbol(d: dict):
    """Convert a serialized symbol dict back to a Symbol dataclass."""
    from ..parser.symbols import Symbol
    return Symbol(
        id=d["id"],
        file=d["file"],
        name=d["name"],
        qualified_name=d.get("qualified_name", d["name"]),
        kind=d["kind"],
        language=d.get("language", ""),
        signature=d.get("signature", ""),
        docstring=d.get("docstring", ""),
        summary=d.get("summary", ""),
        decorators=d.get("decorators", []),
        keywords=d.get("keywords", []),
        parent=d.get("parent"),
        line=d.get("line", 0),
        end_line=d.get("end_line", 0),
        byte_offset=d.get("byte_offset", 0),
        byte_length=d.get("byte_length", 0),
        content_hash=d.get("content_hash", ""),
        ecosystem_context=d.get("ecosystem_context", ""),
    )


def summarize_repo(
    repo: str,
    force: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Re-run AI summarization on all symbols in an existing index.

    Useful when index_folder completed without AI summaries — e.g., the deferred
    background thread was interrupted, AI was disabled at index time, or the
    summarizer provider wasn't configured yet.

    Args:
        repo: Repository identifier (owner/repo or local/hash).
        force: If True, clear all existing summaries and re-run the full 3-tier
               pipeline (docstring → AI → signature fallback) for every symbol.
               If False (default), only symbols with no summary at all are processed.
               Use force=True when index_folder already applied signature fallbacks
               and you want to replace them with AI summaries.
        storage_path: Custom storage path (defaults to CODE_INDEX_PATH).

    Returns:
        dict with success, repo, symbol_count, updated, skipped, duration_seconds.
    """
    t0 = time.monotonic()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if not index.symbols:
        return {
            "success": True,
            "repo": f"{owner}/{name}",
            "symbol_count": 0,
            "updated": 0,
            "skipped": 0,
            "message": "No symbols in index.",
            "duration_seconds": round(time.monotonic() - t0, 2),
        }

    # Convert serialized dicts → Symbol objects
    symbols = [_dict_to_symbol(d) for d in index.symbols]

    # In force mode, clear all summaries so the 3-tier pipeline re-runs fully.
    # Tier 1 will re-extract from docstrings (free); Tier 2 will AI-summarize
    # anything that remains; Tier 3 applies signature fallback as last resort.
    if force:
        for sym in symbols:
            sym.summary = ""

    before = {sym.id: sym.summary for sym in symbols}

    logger.info(
        "summarize_repo starting: repo=%s/%s symbols=%d force=%s",
        owner, name, len(symbols), force,
    )

    # Pass source_root as `repo` so project-aware config reads honor
    # `.jcodemunch.jsonc` overrides for summarizer_model / provider (#304).
    _index_source_root = getattr(index, "source_root", "") or None
    symbols = summarize_symbols(symbols, use_ai=True, repo=_index_source_root)

    updated = sum(1 for sym in symbols if sym.summary != before.get(sym.id, ""))
    skipped = len(symbols) - updated

    # Persist only the summaries that differ from what is stored. A whole-row
    # rewrite from these Symbol objects stored NULL in every field
    # `_dict_to_symbol` does not copy: the complexity columns and the
    # call_references in `data` (#1161).
    stored = {d["id"]: d.get("summary", "") for d in index.symbols}
    new_summaries = {
        sym.id: (sym.summary, sym.content_hash)
        for sym in symbols
        if sym.summary != stored.get(sym.id, "")
    }
    try:
        written = store.update_symbol_summaries(owner, name, new_summaries)
    except Exception as e:
        logger.warning("summarize_repo: failed to save updated summaries for %s/%s: %s", owner, name, e)
        return {"error": f"Summarization completed but save failed: {e}"}

    duration = round(time.monotonic() - t0, 2)
    logger.info(
        "summarize_repo complete: repo=%s/%s updated=%d skipped=%d duration=%.1fs",
        owner, name, updated, skipped, duration,
    )

    result = {
        "success": True,
        "repo": f"{owner}/{name}",
        "symbol_count": len(symbols),
        "updated": updated,
        "skipped": skipped,
        "duration_seconds": duration,
    }
    # A symbol a concurrent re-index removed or rewrote is not given a summary
    # made from its old body; say how many, rather than count them as updated.
    not_written = len(new_summaries) - written
    if not_written > 0:
        result["not_written"] = not_written
        result["not_written_reason"] = "the symbol was re-indexed or removed during the refresh"
    return result
