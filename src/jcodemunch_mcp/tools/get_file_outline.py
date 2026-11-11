"""Get file outline - symbols in a specific file."""

import json
import os
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ..parser import Symbol, build_symbol_tree
from ..retrieval.verdict import file_verdict_for_index, withheld_file_info
from ._utils import load_repo_index_or_error


def _get_file_outline_single(
    file_path: str,
    index,
    owner: str,
    name: str,
    store: IndexStore,
    start: float,
) -> dict:
    """Core logic for a single file_path query. Returns the original flat shape."""
    if not index.has_source_file(file_path):
        withheld = withheld_file_info(index, file_path)
        out = {
            "repo": f"{owner}/{name}",
            "file": file_path,
            "language": "",
            "file_summary": "",
            "symbols": [],
            "_meta": {
                "verdict": file_verdict_for_index(
                    index, present=False, requested_path=file_path, withheld=withheld
                )
            },
        }
        if withheld:
            # #1396: in the body, because the default `meta_fields: []`
            # strips `_meta` and the verdict with it.
            out["withheld"] = withheld
        return out

    # Filter symbols to this file
    file_symbols = [s for s in index.symbols if s.get("file") == file_path]
    language = index.file_languages.get(file_path, "")
    file_summary = index.file_summaries.get(file_path, "")

    # Token savings: raw file size vs outline response size
    raw_bytes = 0
    try:
        raw_file = store._content_dir(owner, name) / file_path
        raw_bytes = os.path.getsize(raw_file)
    except OSError:
        pass

    if not file_symbols:
        elapsed = (time.perf_counter() - start) * 1000
        tokens_saved = estimate_savings(raw_bytes, 0)
        total_saved = record_savings(tokens_saved, tool_name="get_file_outline")
        return {
            "repo": f"{owner}/{name}",
            "file": file_path,
            "language": language,
            "file_summary": file_summary,
            "symbols": [],
            "_meta": {
                "timing_ms": round(elapsed, 1),
                "symbol_count": 0,
                "tokens_saved": tokens_saved,
                "total_tokens_saved": total_saved,
                **cost_avoided(tokens_saved, total_saved),
                "verdict": file_verdict_for_index(
                    index, present=True, empty_symbols=True
                ),
                "tip": "Tip: use file_paths=[...] to query multiple files in one call.",
            },
        }

    # DFS-flatten the symbol tree into a list with parent ids; class members
    # follow their class in order.
    symbol_objects = [_dict_to_symbol(s) for s in file_symbols]
    tree = build_symbol_tree(symbol_objects)
    symbols_output = _flatten_tree_with_parents(tree)

    elapsed = (time.perf_counter() - start) * 1000
    response_bytes = len(json.dumps(symbols_output).encode("utf-8"))
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_file_outline")

    return {
        "repo": f"{owner}/{name}",
        "file": file_path,
        "language": language,
        "file_summary": file_summary,
        "symbols": symbols_output,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "symbol_count": len(symbols_output),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
            "verdict": file_verdict_for_index(index, present=True),
            "tip": "Tip: use file_paths=[...] to query multiple files in one call.",
        },
    }


def _get_file_outline_batch(
    file_paths: list[str],
    index,
    owner: str,
    name: str,
    store: IndexStore,
    start: float,
) -> dict:
    """Batch logic: loop over file_paths, return grouped results array."""
    results = []

    for file_path in file_paths:
        result = _get_file_outline_single(file_path, index, owner, name, store, start)
        # Strip tip from batch results to keep them clean
        if "_meta" in result and "tip" in result["_meta"]:
            del result["_meta"]["tip"]
        results.append(result)

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "results": results,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }


def get_file_outline(
    repo: str,
    file_path: Optional[str] = None,
    storage_path: Optional[str] = None,
    file_paths: Optional[list[str]] = None,
) -> dict:
    """Get all symbols in a file as a flat list with ``parent`` ids.

    Hierarchy is carried by ``parent``: nested symbols point at their
    enclosing symbol's id; top-level symbols have ``parent=None``. DFS
    ordering groups class members immediately after the class.

    Modes:
    - Singular: pass ``file_path`` for one file's outline.
    - Batch: pass ``file_paths`` (list) to return a grouped ``results`` array.

    Args:
        repo: Repository identifier (owner/repo or just repo name)
        file_path: Path to file within repository (singular mode)
        storage_path: Custom storage path
        file_paths: List of file paths (batch mode)

    Returns:
        Singular mode: dict with file, language, file_summary, symbols, _meta.
        Batch mode: dict with ``results`` array (one entry per input file_path).

    Raises:
        ValueError: if neither or both of file_path and file_paths are provided.
    """
    # Normalize: some MCP clients send file_paths=[] alongside file_path when they mean singular mode
    if file_path is not None and file_paths is not None and len(file_paths) == 0:
        file_paths = None
    if (file_path is None and file_paths is None) or (file_path is not None and file_paths is not None):
        raise ValueError("Provide exactly one of 'file_path' or 'file_paths', not both and not neither.")

    start = time.perf_counter()

    # Load index ONCE for both modes
    index, error, _status = load_repo_index_or_error(repo, storage_path)
    if error:
        return error
    owner, name = index.owner, index.name
    store = IndexStore(base_path=storage_path)

    if file_paths is not None:
        return _get_file_outline_batch(file_paths, index, owner, name, store, start)
    else:
        return _get_file_outline_single(file_path, index, owner, name, store, start)


def _dict_to_symbol(d: dict) -> Symbol:
    """Convert dict back to Symbol dataclass."""
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


def _flatten_tree_with_parents(nodes, parent_id=None) -> list[dict]:
    """DFS-flatten a SymbolNode tree into dicts; each carries its parent's id (None for roots)."""
    out: list[dict] = []
    for node in nodes:
        sym = node.symbol
        d = {
            "id": sym.id,
            "kind": sym.kind,
            "name": sym.name,
            "signature": sym.signature,
            "summary": sym.summary,
            "line": sym.line,
            "end_line": sym.end_line,
            "parent": parent_id,
        }
        if sym.decorators:
            d["decorators"] = sym.decorators
        out.append(d)
        if node.children:
            out.extend(_flatten_tree_with_parents(node.children, parent_id=sym.id))
    return out
