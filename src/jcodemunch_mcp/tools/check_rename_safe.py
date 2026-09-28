"""check_rename_safe — detect name collisions before renaming a symbol."""

from __future__ import annotations

import time
from typing import Optional

from ..retrieval.verdict import symbol_not_found
from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo as _resolve_repo


def check_rename_safe(
    repo: str,
    symbol_id: str,
    new_name: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Check whether renaming *symbol_id* to *new_name* is collision-free.

    Scans every file that imports the symbol's defining module and checks
    whether *new_name* is already defined there.  Also checks the symbol's
    own file for an existing definition with that name.

    Args:
        repo:       Repo identifier (path, slug, or ``owner/repo``).
        symbol_id:  Symbol ID to rename (e.g. ``src/utils.py::helper#function``).
        new_name:   Proposed new symbol name.
        storage_path: Optional override for index storage directory.

    Returns:
        ``{safe, conflicts, checked_files, symbol, timing_ms}``
        - safe: True if no collisions were found; None when none was found
          but the importer graph could not reach the symbol's file, so the
          files that use it were never checked (``unresolvable`` says why,
          #879); False on a collision.
        - conflicts: List of ``{file, existing_symbol_id, existing_name, line}``.
        - checked_files: Number of files inspected.
    """
    t0 = time.monotonic()
    try:
        owner, name = _resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    sym = index.get_symbol(symbol_id)
    if sym is None:
        # Try bare-name resolution
        matches = [s for s in index.symbols if s.get("name") == symbol_id]
        if len(matches) == 1:
            sym = matches[0]
        elif len(matches) > 1:
            return {
                "error": f"Ambiguous symbol name {symbol_id!r} — {len(matches)} matches. "
                         "Use a full symbol ID.",
                "candidates": [s["id"] for s in matches[:10]],
            }
        else:
            return symbol_not_found(symbol_id, index.symbols)

    sym_file: str = sym["file"]
    sym_kind: str = sym.get("kind", "")
    new_name_lower = new_name.lower()

    # Build set of files to inspect:
    # 1. The symbol's own file.
    # 2. All files that import the symbol's file.
    files_to_check: set[str] = {sym_file}
    if index.imports:
        source_files_fs = frozenset(index.source_files)
        alias_map = getattr(index, "alias_map", {}) or {}
        psr4_map = getattr(index, "psr4_map", None)
        for src_file, file_imports in index.imports.items():
            for imp in file_imports:
                target = resolve_specifier(imp["specifier"], src_file, source_files_fs, alias_map, psr4_map)
                if target == sym_file:
                    files_to_check.add(src_file)
                    break

    # No importer found: ask the authority whether the graph could have found
    # one. A Go package import lands on no member file (#415), and `safe: True`
    # from that walk was a claim about files nobody looked at (#879).
    unresolvable = None
    if files_to_check == {sym_file}:
        from .get_blast_radius import blast_verdict  # noqa: PLC0415

        _, unresolvable = blast_verdict(index, frozenset(index.source_files), sym_file, 0)

    # Build a per-file symbol name map for fast lookup
    name_by_file: dict[str, list[dict]] = {}
    for s in index.symbols:
        f = s.get("file")
        if f and f in files_to_check:
            name_by_file.setdefault(f, []).append(s)

    conflicts: list[dict] = []
    for f in files_to_check:
        for s in name_by_file.get(f, []):
            if s.get("name", "").lower() == new_name_lower and s["id"] != sym["id"]:
                conflicts.append({
                    "file": f,
                    "existing_symbol_id": s["id"],
                    "existing_name": s["name"],
                    "kind": s.get("kind", ""),
                    "line": s.get("line", 0),
                })

    timing_ms = round((time.monotonic() - t0) * 1000, 1)
    safe = False if conflicts else (None if unresolvable else True)
    result = {
        "safe": safe,
        "symbol": {
            "id": sym["id"],
            "name": sym["name"],
            "kind": sym_kind,
            "file": sym_file,
        },
        "new_name": new_name,
        "conflicts": conflicts,
        "checked_files": len(files_to_check),
        "_meta": {"timing_ms": timing_ms},
    }
    if safe is None:
        result["unresolvable"] = unresolvable
    return result
