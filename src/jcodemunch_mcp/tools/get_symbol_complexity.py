"""get_symbol_complexity — return cyclomatic complexity, nesting depth, and parameter count.

Complexity data is stored at index time for every indexed symbol (requires INDEX_VERSION >= 7,
i.e. jcodemunch-mcp >= 1.16).  For symbols indexed before v1.16, all metrics will be 0;
re-index the repo to populate them.
"""

from __future__ import annotations

import time
from typing import Optional

from ..retrieval.verdict import symbol_not_found
from ..storage import IndexStore
from ._utils import resolve_repo


def _complexity_assessment(cyclomatic: int) -> str:
    if cyclomatic <= 4:
        return "low"
    if cyclomatic <= 10:
        return "medium"
    return "high"


def get_symbol_complexity(
    repo: str,
    symbol_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return complexity metrics for a single indexed symbol.

    Metrics (all stored at index time — no re-parsing required):
        cyclomatic   — branch count; 1 = no branches, higher = more paths through the code.
        max_nesting  — deepest nesting level within the function body.
        param_count  — number of declared parameters.
        lines        — number of source lines (end_line - line + 1 when available).
        assessment   — "low" (1–4), "medium" (5–10), or "high" (11+) based on cyclomatic.

    Args:
        repo:        Repository identifier (owner/repo or bare name).
        symbol_id:   Full symbol ID as returned by search_symbols / get_file_outline.
        storage_path: Optional index storage path override.

    Returns:
        ``{repo, symbol_id, name, kind, file, line,
           cyclomatic, max_nesting, param_count, lines, assessment, _meta}``
    """
    t0 = time.perf_counter()
    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    sym = next((s for s in index.symbols if s.get("id") == symbol_id), None)
    if sym is None:
        return symbol_not_found(symbol_id, index.symbols)

    cyclomatic = sym.get("cyclomatic") or 0
    max_nesting = sym.get("max_nesting") or 0
    param_count = sym.get("param_count") or 0

    line = sym.get("line") or 0
    end_line = sym.get("end_line") or 0
    lines = (end_line - line + 1) if (end_line and line and end_line >= line) else 0

    no_data = cyclomatic == 0 and max_nesting == 0 and param_count == 0
    note = (
        "Complexity data not available — re-index with jcodemunch-mcp >= 1.16 "
        "to populate complexity metrics."
        if no_data else None
    )

    result: dict = {
        "repo": f"{owner}/{name}",
        "symbol_id": symbol_id,
        "name": sym.get("name", ""),
        "kind": sym.get("kind", ""),
        "file": sym.get("file", ""),
        "line": line,
        "cyclomatic": cyclomatic,
        "max_nesting": max_nesting,
        "param_count": param_count,
        "lines": lines,
        "assessment": _complexity_assessment(cyclomatic),
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "methodology": "stored_metrics",
            "confidence_level": "medium",
        },
    }
    if note:
        result["note"] = note
    return result
