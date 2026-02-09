"""Get symbol source code."""

from typing import Optional

from ..storage import IndexStore


def get_symbol(
    repo: str,
    symbol_id: str,
    storage_path: Optional[str] = None
) -> dict:
    """Get full source of a specific symbol.
    
    Args:
        repo: Repository identifier (owner/repo or just repo name)
        symbol_id: Symbol ID from get_file_outline or search_symbols
        storage_path: Custom storage path
    
    Returns:
        Dict with symbol details and source code
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
    
    # Find symbol
    symbol = index.get_symbol(symbol_id)
    
    if not symbol:
        return {"error": f"Symbol not found: {symbol_id}"}
    
    # Get source via byte-offset read
    source = store.get_symbol_content(owner, name, symbol_id)
    
    return {
        "id": symbol["id"],
        "kind": symbol["kind"],
        "name": symbol["name"],
        "file": symbol["file"],
        "line": symbol["line"],
        "end_line": symbol["end_line"],
        "signature": symbol["signature"],
        "decorators": symbol.get("decorators", []),
        "docstring": symbol.get("docstring", ""),
        "source": source or ""
    }


def get_symbols(
    repo: str,
    symbol_ids: list[str],
    storage_path: Optional[str] = None
) -> dict:
    """Get full source of multiple symbols.
    
    Args:
        repo: Repository identifier (owner/repo or just repo name)
        symbol_ids: List of symbol IDs
        storage_path: Custom storage path
    
    Returns:
        Dict with symbols list and any errors
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
            "source": source or ""
        })
    
    return {
        "symbols": symbols,
        "errors": errors
    }
