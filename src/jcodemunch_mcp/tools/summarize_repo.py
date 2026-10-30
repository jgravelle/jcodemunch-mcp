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

    logger.info(
        "summarize_repo starting: repo=%s/%s symbols=%d force=%s",
        owner, name, len(symbols), force,
    )

    # Pass source_root as `repo` so project-aware config reads honor
    # `.jcodemunch.jsonc` overrides for summarizer_model / provider (#304).
    _index_source_root = getattr(index, "source_root", "") or None
    report: dict = {}
    symbols = summarize_symbols(
        symbols, use_ai=True, repo=_index_source_root,
        # summarizer_body_chars sends the index's own text, not the file on disk.
        source_for=lambda rel: store.read_cached_source(owner, name, rel),
        report=report,
    )

    stored = {d["id"]: d.get("summary", "") for d in index.symbols}

    # (#1282) A symbol the provider was asked for and did not summarize (a 429,
    # an error body, the circuit breaker, a blank answer) keeps what was stored
    # rather than have its signature fallback saved over it, and the result
    # says so: otherwise a failed forced run reads like a healthy one and a
    # later plain run finds nothing to redo. The summarizer names those symbols;
    # a fallback it did not name (no provider, the docstring tier switched off)
    # is the pipeline's answer and is written as before.
    failed = report.get("provider_failed") or set()
    provider_fallbacks = 0
    kept = 0
    for sym in symbols:
        if sym.id not in failed:
            continue
        provider_fallbacks += 1
        previous = stored.get(sym.id, "")
        if previous and previous != sym.summary:
            sym.summary = previous
            kept += 1

    # `updated` counts summaries that differ from what was stored, so a kept
    # summary is not counted as updated (#1282). Before, a forced run counted
    # every summary against the blank it had just set.
    updated = sum(1 for sym in symbols if sym.summary != stored.get(sym.id, ""))
    skipped = len(symbols) - updated

    # Persist only the summaries that differ from what is stored. A whole-row
    # rewrite from these Symbol objects stored NULL in every field
    # `_dict_to_symbol` does not copy: the complexity columns and the
    # call_references in `data` (#1161).
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
    if provider_fallbacks > 0:
        result["provider_fallbacks"] = provider_fallbacks
        logger.warning(
            "summarize_repo: %d symbol(s) got only a signature fallback from the configured provider "
            "(%d kept their stored summary) for %s/%s",
            provider_fallbacks, kept, owner, name,
        )
    if kept > 0:
        result["previous_summaries_kept"] = kept
        result["previous_summaries_kept_reason"] = (
            "the AI provider was asked for these symbols and returned no summary (a failed or rate-limited request, or a blank answer), "
            "so each kept the summary it already had; run summarize_repo(force=true) again to retry them"
        )
    return result
