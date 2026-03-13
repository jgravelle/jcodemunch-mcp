"""Search symbols across repository."""

import os
import time
from typing import Optional

from ..storage import IndexStore, CodeIndex, record_savings, estimate_savings, cost_avoided
from ._utils import resolve_repo


def search_symbols(
    repo: str,
    query: str,
    kind: Optional[str] = None,
    file_pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 10,
    debug: bool = False,
    storage_path: Optional[str] = None
) -> dict:
    """Search for symbols matching a query.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Search query.
        kind: Optional filter by symbol kind.
        file_pattern: Optional glob pattern to filter files.
        language: Optional filter by language (e.g., "python", "javascript").
        max_results: Maximum results to return.
        debug: When True, include per-field score breakdown in each result.
        storage_path: Custom storage path.

    Returns:
        Dict with search results and _meta envelope.
    """
    start = time.perf_counter()
    max_results = max(1, min(max_results, 100))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    # Load index
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # Search
    results = index.search(query, kind=kind, file_pattern=file_pattern)

    # Apply language filter (post-search since CodeIndex.search doesn't support it)
    if language:
        results = [s for s in results if s.get("language") == language]

    # Score and sort (search already does this, but we need to add score to output)
    query_lower = query.lower()
    query_words = set(query_lower.split())

    candidates_scored = len(results)
    scored_results = []
    for sym in results[:max_results]:
        score = _calculate_score(sym, query_lower, query_words)
        entry = {
            "id": sym["id"],
            "kind": sym["kind"],
            "name": sym["name"],
            "file": sym["file"],
            "line": sym["line"],
            "signature": sym["signature"],
            "summary": sym.get("summary", ""),
            "score": score
        }
        if debug:
            entry["score_breakdown"] = _score_breakdown(sym, query_lower, query_words)
        scored_results.append(entry)

    # Token savings: files containing matches vs symbol byte_lengths of results
    raw_bytes = 0
    seen_files: set = set()
    response_bytes = 0
    content_dir = store._content_dir(owner, name)
    for sym in results[:max_results]:
        f = sym["file"]
        if f not in seen_files:
            seen_files.add(f)
            try:
                raw_bytes += os.path.getsize(content_dir / f)
            except OSError:
                pass
        response_bytes += sym.get("byte_length", 0)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved)

    elapsed = (time.perf_counter() - start) * 1000

    meta = {
        "timing_ms": round(elapsed, 1),
        "total_symbols": len(index.symbols),
        "truncated": len(results) > max_results,
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        **cost_avoided(tokens_saved, total_saved),
    }
    if debug:
        meta["candidates_scored"] = candidates_scored

    return {
        "repo": f"{owner}/{name}",
        "query": query,
        "result_count": len(scored_results),
        "results": scored_results,
        "_meta": meta,
    }


def _score_breakdown(sym: dict, query_lower: str, query_words: set) -> dict:
    """Return per-field score contributions for debug mode."""
    b: dict = {
        "name_exact": 0,
        "name_contains": 0,
        "name_word_overlap": 0,
        "signature_phrase": 0,
        "signature_word_overlap": 0,
        "summary_phrase": 0,
        "summary_word_overlap": 0,
        "keywords": 0,
        "docstring_word_overlap": 0,
    }

    name_lower = sym.get("name", "").lower()
    if query_lower == name_lower:
        b["name_exact"] = 20
    elif query_lower in name_lower:
        b["name_contains"] = 10
    for word in query_words:
        if word in name_lower:
            b["name_word_overlap"] += 5

    sig_lower = sym.get("signature", "").lower()
    if query_lower in sig_lower:
        b["signature_phrase"] = 8
    for word in query_words:
        if word in sig_lower:
            b["signature_word_overlap"] += 2

    summary_lower = sym.get("summary", "").lower()
    if query_lower in summary_lower:
        b["summary_phrase"] = 5
    for word in query_words:
        if word in summary_lower:
            b["summary_word_overlap"] += 1

    keywords = set(sym.get("keywords", []))
    b["keywords"] = len(query_words & keywords) * 3

    doc_lower = sym.get("docstring", "").lower()
    for word in query_words:
        if word in doc_lower:
            b["docstring_word_overlap"] += 1

    return b


def _calculate_score(sym: dict, query_lower: str, query_words: set) -> int:
    """Calculate search score for a symbol."""
    score = 0

    # 1. Exact name match (highest weight)
    name_lower = sym.get("name", "").lower()
    if query_lower == name_lower:
        score += 20
    elif query_lower in name_lower:
        score += 10

    # 2. Name word overlap
    for word in query_words:
        if word in name_lower:
            score += 5

    # 3. Signature match
    sig_lower = sym.get("signature", "").lower()
    if query_lower in sig_lower:
        score += 8
    for word in query_words:
        if word in sig_lower:
            score += 2

    # 4. Summary match
    summary_lower = sym.get("summary", "").lower()
    if query_lower in summary_lower:
        score += 5
    for word in query_words:
        if word in summary_lower:
            score += 1

    # 5. Keyword match
    keywords = set(sym.get("keywords", []))
    matching_keywords = query_words & keywords
    score += len(matching_keywords) * 3

    # 6. Docstring match
    doc_lower = sym.get("docstring", "").lower()
    for word in query_words:
        if word in doc_lower:
            score += 1

    return score
