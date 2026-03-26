"""Find symbols related to a given symbol via heuristic clustering."""

import re
import time
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo

# Weights for each relatedness signal
_W_SAME_FILE = 3.0
_W_SHARED_IMPORT = 1.5
_W_NAME_TOKEN = 0.5   # per overlapping token


def _tokenize_name(name: str) -> set[str]:
    """Split camelCase/snake_case name into lowercase tokens (≥2 chars)."""
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    return {t.lower() for t in re.findall(r"[a-zA-Z0-9]+", name) if len(t) > 1}


def _build_file_importers(
    imports: Optional[dict], source_files: frozenset, alias_map: Optional[dict] = None
) -> dict[str, set[str]]:
    """Return {file: {files_that_import_it}} — used for shared-importer signal."""
    if not imports:
        return {}
    rev: dict[str, set[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map)
            if target and target != src_file:
                rev.setdefault(target, set()).add(src_file)
    return rev


def get_related_symbols(
    repo: str,
    symbol_id: str,
    max_results: int = 10,
    storage_path: Optional[str] = None,
) -> dict:
    """Find symbols related to a given symbol using heuristic clustering.

    Three signals are combined:

    * **Same file** (weight 3.0) — symbols defined in the same file are likely
      to cooperate closely.
    * **Shared importers** (weight 1.5) — if another file's defining file is
      imported by the same files that import the target's file, they probably
      serve the same consumers.
    * **Name token overlap** (weight 0.5 per token) — ``getUserById`` and
      ``getUserProfile`` share ``get`` and ``user``, suggesting relatedness.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol_id: ID of the symbol to find relatives for.
        max_results: Maximum number of related symbols to return.
        storage_path: Custom storage path.

    Returns:
        Dict with ``related`` list (scored, descending), each entry containing
        the symbol info and a ``relatedness_score``.
    """
    start = time.perf_counter()
    max_results = max(1, min(max_results, 50))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    target = index.get_symbol(symbol_id)
    if not target:
        return {"error": f"Symbol not found: {symbol_id}"}

    target_file = target.get("file", "")
    target_tokens = _tokenize_name(target.get("name", ""))

    # Build shared-importer map if imports are available
    source_files = frozenset(index.source_files)
    file_importers = _build_file_importers(index.imports, source_files, index.alias_map)
    target_importers = file_importers.get(target_file, set())

    scores: dict[str, float] = {}

    for sym in index.symbols:
        sid = sym.get("id", "")
        if sid == symbol_id:
            continue

        sym_file = sym.get("file", "")
        score = 0.0

        # Same file
        if sym_file == target_file:
            score += _W_SAME_FILE

        # Shared importers
        elif target_importers and file_importers.get(sym_file, set()) & target_importers:
            score += _W_SHARED_IMPORT

        # Name token overlap
        sym_tokens = _tokenize_name(sym.get("name", ""))
        overlap = target_tokens & sym_tokens
        if overlap:
            score += len(overlap) * _W_NAME_TOKEN

        if score > 0:
            scores[sid] = score

    # Sort and take top results
    top_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:max_results]

    related = []
    for sid in top_ids:
        sym = index.get_symbol(sid)
        if sym:
            related.append({
                "id": sym["id"],
                "name": sym["name"],
                "kind": sym["kind"],
                "file": sym["file"],
                "line": sym["line"],
                "signature": sym.get("signature", ""),
                "relatedness_score": round(scores[sid], 2),
            })

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "symbol": {
            "id": target["id"],
            "name": target["name"],
            "kind": target["kind"],
            "file": target_file,
        },
        "related_count": len(related),
        "related": related,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }
