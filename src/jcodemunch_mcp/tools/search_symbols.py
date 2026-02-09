"""Search symbols across repository."""

from typing import Optional

from ..storage import IndexStore, CodeIndex


def search_symbols(
    repo: str,
    query: str,
    kind: Optional[str] = None,
    file_pattern: Optional[str] = None,
    max_results: int = 10,
    storage_path: Optional[str] = None
) -> dict:
    """Search for symbols matching a query.
    
    Args:
        repo: Repository identifier (owner/repo or just repo name)
        query: Search query
        kind: Optional filter by symbol kind
        file_pattern: Optional glob pattern to filter files
        max_results: Maximum results to return
        storage_path: Custom storage path
    
    Returns:
        Dict with search results
    """
    # Parse repo identifier
    if "/" in repo:
        owner, name = repo.split("/", 1)
    else:
        store = IndexStore(base_path=storage_path)
        repos = store.list_repos()
        matching = [r for r in repos if r["repo"].endswith(f"/{repo}")]
        if not matching:
            return {"error": f"Repository not found: {repo}"}
        owner, name = matching[0]["repo"].split("/", 1)
    
    # Load index
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}
    
    # Search
    results = index.search(query, kind=kind, file_pattern=file_pattern)
    
    # Score and sort (search already does this, but we need to add score to output)
    query_lower = query.lower()
    query_words = set(query_lower.split())
    
    scored_results = []
    for sym in results[:max_results]:
        score = _calculate_score(sym, query_lower, query_words)
        scored_results.append({
            "id": sym["id"],
            "kind": sym["kind"],
            "name": sym["name"],
            "file": sym["file"],
            "line": sym["line"],
            "signature": sym["signature"],
            "summary": sym.get("summary", ""),
            "score": score
        })
    
    return {
        "repo": f"{owner}/{name}",
        "query": query,
        "result_count": len(scored_results),
        "results": scored_results
    }


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
