"""Untested symbol detection — find symbols with no evidence of test-file reachability."""

from __future__ import annotations

import logging
import time
from typing import Optional

from .. import path_glob
from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import index_status_to_tool_error, resolve_repo
from ._call_graph import _word_match
from .find_dead_code import _is_test_file

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_reverse_adjacency(
    imports: dict, source_files: frozenset, alias_map: Optional[dict] = None,
    psr4_map: Optional[dict] = None,
) -> dict[str, list[str]]:
    """Return {file: [files_that_import_it]} from raw import data."""
    rev: dict[str, list[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if target and target != src_file:
                rev.setdefault(target, []).append(src_file)
    return {k: list(dict.fromkeys(v)) for k, v in rev.items()}


def _test_files_that_import(file_path: str, rev: dict[str, list[str]]) -> list[str]:
    """Return test files that directly import *file_path*."""
    return [f for f in rev.get(file_path, []) if _is_test_file(f)]


def _symbol_reached_by_tests(
    sym: dict,
    test_importers: list[str],
    index,
    store: IndexStore,
    owner: str,
    repo_name: str,
) -> tuple[bool, float, str]:
    """Check whether any test file references *sym* by name.

    Returns (reached: bool, confidence: float, reason: str).
    """
    sym_name: str = sym.get("name", "")
    sym_file: str = sym.get("file", "")
    if not sym_name or not sym_file:
        return False, 1.0, "unreached"

    if not test_importers:
        return False, 1.0, "unreached"

    # --- AST path: check call_references on test symbols ---
    get_callers = getattr(index, "get_callers_by_name", None)
    callers_by_name = get_callers() if get_callers else None

    if callers_by_name:
        test_importer_set = frozenset(test_importers)
        for tf in test_importer_set:
            if callers_by_name.get((tf, sym_name)):
                return True, 0.0, "reached"

    # --- Text heuristic fallback: word-boundary match in test file content ---
    for tf in test_importers:
        content = store.get_file_content(owner, repo_name, tf)
        if content and _word_match(content, sym_name):
            return True, 0.0, "reached"

    # Test file imports the module but no test references this specific symbol
    return False, 0.7, "imported_not_called"


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def get_untested_symbols(
    repo: str,
    file_pattern: Optional[str] = None,
    min_confidence: float = 0.5,
    max_results: int = 100,
    storage_path: Optional[str] = None,
) -> dict:
    """Find symbols with no evidence of being exercised by any test file.

    Uses import-graph reachability + name matching (AST call_references when
    available, word-boundary text heuristic as fallback).  This is heuristic
    reachability, NOT runtime coverage — it answers "does any test reference
    this symbol?" rather than "what % of lines are covered."

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        file_pattern: Optional glob to narrow which source files are analysed.
        min_confidence: Minimum confidence to include (0.0–1.0, default 0.5).
        max_results: Cap on returned symbols (default 100). Bounds the
            `symbols` page only -- `untested_count` and `reached_pct` are
            measured over the whole repository regardless (#559).
        storage_path: Custom storage path.

    Returns:
        Dict with untested_count (repo-wide, NOT capped by max_results),
        returned_count (the length of the returned page), total_non_test_symbols,
        reached_pct, and a symbols list sorted by file.
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if index.imports is None:
        return {
            "error": (
                "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 "
                "to enable untested symbol detection."
            )
        }

    source_files = frozenset(index.source_files)
    rev = _build_reverse_adjacency(
        index.imports, source_files, index.alias_map,
        getattr(index, "psr4_map", None),
    )

    # Partition: which files are tests?
    test_file_set = frozenset(f for f in index.source_files if _is_test_file(f))

    # Pre-compute: for each non-test file, which test files import it?
    test_importers_cache: dict[str, list[str]] = {}

    symbols: list[dict] = []
    total_non_test = 0

    for sym in index.symbols:
        kind = sym.get("kind", "")
        if kind not in ("function", "method"):
            continue

        sym_file = sym.get("file", "")
        if not sym_file or sym_file in test_file_set:
            continue

        # Apply optional file_pattern filter
        if file_pattern:
            if not path_glob.matches(sym_file, file_pattern):
                continue

        total_non_test += 1

        # Get cached test importers for this file
        if sym_file not in test_importers_cache:
            test_importers_cache[sym_file] = _test_files_that_import(sym_file, rev)
        test_importers = test_importers_cache[sym_file]

        reached, confidence, reason = _symbol_reached_by_tests(
            sym, test_importers, index, store, owner, name,
        )

        if reached:
            continue

        if confidence < min_confidence:
            continue

        symbols.append({
            "symbol_id": sym.get("id", ""),
            "name": sym.get("name", ""),
            "kind": kind,
            "file": sym_file,
            "line": sym.get("line", 0),
            "confidence": confidence,
            "reason": reason,
        })

    # Sort by file, then line
    symbols.sort(key=lambda s: (s["file"], s["line"]))

    # ⚠⚠ The count is taken BEFORE the page is cut (#559, @lilubot).
    # `untested_count` used to be `len(symbols)` computed after the slice, so
    # it described the PAGE and not the repository -- and `reached_pct`
    # divided by it. `get_repo_health` asks for `max_results=1` because it
    # "only needs the count", which made every repo with untested code score a
    # perfect test axis: measured 4,893 untested of 6,352 (23.0% reached)
    # published as 100.
    #
    # ⚠ The page size is still reported, under `returned_count`, which is the
    # name that says what it is. A caller wanting the page length has it; a
    # caller wanting the repository's number can no longer get the page's by
    # accident.
    untested_count = len(symbols)
    reached_pct = round(
        ((total_non_test - untested_count) / total_non_test * 100)
        if total_non_test > 0 else 100.0,
        1,
    )

    # Apply max_results cap
    truncated = untested_count > max_results
    symbols = symbols[:max_results]

    elapsed = (time.perf_counter() - start) * 1000
    result: dict = {
        "repo": f"{owner}/{name}",
        "untested_count": untested_count,
        "returned_count": len(symbols),
        "total_non_test_symbols": total_non_test,
        "reached_pct": reached_pct,
        "symbols": symbols,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }
    if truncated:
        result["_meta"]["truncated"] = True
        result["_meta"]["note"] = f"Results capped at max_results={max_results}"
    return result
