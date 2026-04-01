"""Shared AST-derived call-graph computation.

Strategy
--------
No call-site data is stored in the index. Callers and callees are derived
at query time using two heuristics that are already in use elsewhere:

Callers (who calls symbol X?):
  1. Find files that import X's defining file (import graph, same as blast radius).
  2. Within each importer, check which indexed symbols' source bodies mention
     X's name as a word token.

Callees (what does symbol X call?):
  1. Extract X's source body (by line range from file content).
  2. Find files that X's file imports (import graph).
  3. Within each imported file, check which indexed symbols' names appear in
     X's body.

Both heuristics are approximate (no type resolution, no dynamic dispatch
awareness), consistent with the rest of jCodemunch's AST-level analysis.
Results include a ``source: "ast"`` field so consumers can act accordingly.
"""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage import IndexStore
    from ..storage.index_store import CodeIndex


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _word_match(content: str, name: str) -> bool:
    """Return True if *name* appears as a word token in *content*."""
    return bool(re.search(r"\b" + re.escape(name) + r"\b", content))


def _symbol_body(file_lines: list[str], sym: dict) -> str:
    """Slice *file_lines* to the lines covered by *sym* (1-indexed)."""
    line = sym.get("line", 0)
    end_line = sym.get("end_line", line)
    if not line:
        return ""
    start_idx = max(0, line - 1)
    end_idx = min(len(file_lines), end_line)
    return "\n".join(file_lines[start_idx:end_idx])


def build_symbols_by_file(index: "CodeIndex") -> dict[str, list[dict]]:
    """Build ``{file_path: [symbol_dicts]}`` from *index.symbols*."""
    result: dict[str, list[dict]] = {}
    for sym in index.symbols:
        f = sym.get("file")
        if f:
            result.setdefault(f, []).append(sym)
    return result


# ---------------------------------------------------------------------------
# Direct caller / callee finders
# ---------------------------------------------------------------------------

def find_direct_callers(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    reverse_adj: dict[str, list[str]],
    symbols_by_file: dict[str, list[dict]],
) -> list[dict]:
    """Return symbols in importing files whose bodies mention *sym*'s name.

    Each result is ``{id, name, kind, file, line}``.
    """
    sym_name: str = sym.get("name", "")
    sym_file: str = sym.get("file", "")
    if not sym_name or not sym_file:
        return []

    callers: list[dict] = []
    seen_ids: set[str] = set()

    for imp_file in reverse_adj.get(sym_file, []):
        file_content = store.get_file_content(owner, repo_name, imp_file)
        if not file_content:
            continue
        # Fast gate: skip file if sym_name not present anywhere
        if not _word_match(file_content, sym_name):
            continue

        file_lines = file_content.splitlines()
        for candidate in symbols_by_file.get(imp_file, []):
            cid = candidate.get("id", "")
            if not cid or cid in seen_ids or not candidate.get("line"):
                continue
            body = _symbol_body(file_lines, candidate)
            if body and _word_match(body, sym_name):
                seen_ids.add(cid)
                callers.append({
                    "id": cid,
                    "name": candidate.get("name", ""),
                    "kind": candidate.get("kind", ""),
                    "file": imp_file,
                    "line": candidate.get("line", 0),
                })

    return callers


def find_direct_callees(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    symbols_by_file: dict[str, list[dict]],
) -> list[dict]:
    """Return symbols from imported files whose names appear in *sym*'s body.

    Each result is ``{id, name, kind, file, line}``.
    """
    from ..parser.imports import resolve_specifier

    sym_file: str = sym.get("file", "")
    if not sym_file:
        return []

    file_content = store.get_file_content(owner, repo_name, sym_file)
    if not file_content:
        return []

    file_lines = file_content.splitlines()
    sym_body = _symbol_body(file_lines, sym)
    if not sym_body:
        return []

    # Resolve files that sym's file imports
    file_imports = (index.imports or {}).get(sym_file, [])
    source_files_fs = frozenset(index.source_files)
    alias_map = getattr(index, "alias_map", {}) or {}

    imported_files: set[str] = set()
    for imp in file_imports:
        target = resolve_specifier(imp["specifier"], sym_file, source_files_fs, alias_map)
        if target and target != sym_file:
            imported_files.add(target)

    callees: list[dict] = []
    seen_ids: set[str] = set()

    for imported_file in imported_files:
        for candidate in symbols_by_file.get(imported_file, []):
            cid = candidate.get("id", "")
            cname = candidate.get("name", "")
            if not cid or not cname or cid in seen_ids:
                continue
            if _word_match(sym_body, cname):
                seen_ids.add(cid)
                callees.append({
                    "id": cid,
                    "name": cname,
                    "kind": candidate.get("kind", ""),
                    "file": imported_file,
                    "line": candidate.get("line", 0),
                })

    return callees


# ---------------------------------------------------------------------------
# BFS traversals
# ---------------------------------------------------------------------------

def bfs_callers(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    reverse_adj: dict[str, list[str]],
    symbols_by_file: dict[str, list[dict]],
    max_depth: int,
) -> tuple[list[dict], int]:
    """BFS over callers up to *max_depth* hops.

    Returns ``(results, depth_reached)`` where each result has a ``depth`` field.
    """
    sym_id = sym.get("id", "")
    visited: set[str] = {sym_id}
    queue: deque[tuple[dict, int]] = deque()
    results: list[dict] = []
    depth_reached = 0
    symbol_index: dict[str, dict] = getattr(index, "_symbol_index", {})

    # Depth-1 callers
    for c in find_direct_callers(index, store, owner, repo_name, sym, reverse_adj, symbols_by_file):
        if c["id"] not in visited:
            visited.add(c["id"])
            results.append({**c, "depth": 1})
            depth_reached = 1
            if max_depth > 1:
                queue.append((c, 1))

    while queue:
        curr_dict, curr_depth = queue.popleft()
        if curr_depth >= max_depth:
            continue
        curr_full = symbol_index.get(curr_dict["id"])
        if not curr_full:
            continue
        for c in find_direct_callers(index, store, owner, repo_name, curr_full, reverse_adj, symbols_by_file):
            if c["id"] not in visited:
                visited.add(c["id"])
                new_depth = curr_depth + 1
                results.append({**c, "depth": new_depth})
                depth_reached = max(depth_reached, new_depth)
                if new_depth < max_depth:
                    queue.append((c, new_depth))

    return results, depth_reached


def bfs_callees(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    symbols_by_file: dict[str, list[dict]],
    max_depth: int,
) -> tuple[list[dict], int]:
    """BFS over callees up to *max_depth* hops.

    Returns ``(results, depth_reached)`` where each result has a ``depth`` field.
    """
    sym_id = sym.get("id", "")
    visited: set[str] = {sym_id}
    queue: deque[tuple[dict, int]] = deque()
    results: list[dict] = []
    depth_reached = 0
    symbol_index: dict[str, dict] = getattr(index, "_symbol_index", {})

    for c in find_direct_callees(index, store, owner, repo_name, sym, symbols_by_file):
        if c["id"] not in visited:
            visited.add(c["id"])
            results.append({**c, "depth": 1})
            depth_reached = 1
            if max_depth > 1:
                queue.append((c, 1))

    while queue:
        curr_dict, curr_depth = queue.popleft()
        if curr_depth >= max_depth:
            continue
        curr_full = symbol_index.get(curr_dict["id"])
        if not curr_full:
            continue
        for c in find_direct_callees(index, store, owner, repo_name, curr_full, symbols_by_file):
            if c["id"] not in visited:
                visited.add(c["id"])
                new_depth = curr_depth + 1
                results.append({**c, "depth": new_depth})
                depth_reached = max(depth_reached, new_depth)
                if new_depth < max_depth:
                    queue.append((c, new_depth))

    return results, depth_reached
