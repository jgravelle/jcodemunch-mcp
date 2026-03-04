"""Get file outline - symbols in a specific file."""

import os
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ..parser import build_symbol_tree
from ._utils import resolve_repo


def get_file_outline(
    repo: str,
    file_path: str,
    storage_path: Optional[str] = None
) -> dict:
    """Get symbols in a file with hierarchical structure.

    Args:
        repo: Repository identifier (owner/repo or just repo name)
        file_path: Path to file within repository
        storage_path: Custom storage path

    Returns:
        Dict with symbols outline
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}
    
    # Load index
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}
    
    # Filter symbols to this file
    file_symbols = [s for s in index.symbols if s.get("file") == file_path]
    
    if not file_symbols:
        return {
            "repo": f"{owner}/{name}",
            "file": file_path,
            "language": "",
            "symbols": []
        }
    
    # Build symbol tree
    from ..parser import Symbol
    symbol_objects = [_dict_to_symbol(s) for s in file_symbols]
    tree = build_symbol_tree(symbol_objects)
    
    # Convert to output format
    symbols_output = [_node_to_dict(n) for n in tree]
    
    # Get language
    language = file_symbols[0].get("language", "")
    
    elapsed = (time.perf_counter() - start) * 1000

    # Token savings: raw file size vs outline response size
    raw_bytes = 0
    try:
        raw_file = store._content_dir(owner, name) / file_path
        raw_bytes = os.path.getsize(raw_file)
    except OSError:
        pass
    response_bytes = sum(s.get("byte_length", 0) for s in file_symbols)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved)

    return {
        "repo": f"{owner}/{name}",
        "file": file_path,
        "language": language,
        "symbols": symbols_output,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "symbol_count": len(symbols_output),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }


def _dict_to_symbol(d: dict) -> "Symbol":
    """Convert dict back to Symbol dataclass."""
    from ..parser import Symbol
    return Symbol(
        id=d["id"],
        file=d["file"],
        name=d["name"],
        qualified_name=d["qualified_name"],
        kind=d["kind"],
        language=d["language"],
        signature=d["signature"],
        docstring=d.get("docstring", ""),
        summary=d.get("summary", ""),
        decorators=d.get("decorators", []),
        keywords=d.get("keywords", []),
        parent=d.get("parent"),
        line=d["line"],
        end_line=d["end_line"],
        byte_offset=d["byte_offset"],
        byte_length=d["byte_length"],
        content_hash=d.get("content_hash", ""),
    )


def _node_to_dict(node) -> dict:
    """Convert SymbolNode to output dict."""
    result = {
        "id": node.symbol.id,
        "kind": node.symbol.kind,
        "name": node.symbol.name,
        "signature": node.symbol.signature,
        "summary": node.symbol.summary,
        "line": node.symbol.line,
    }
    
    if node.children:
        result["children"] = [_node_to_dict(c) for c in node.children]
    
    return result
