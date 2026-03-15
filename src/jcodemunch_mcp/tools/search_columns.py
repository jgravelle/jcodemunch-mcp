"""Search column metadata across indexed models."""

import os
import time
from typing import Optional

from ._utils import resolve_repo
from ..storage.index_store import IndexStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


def _collect_all_columns(context_metadata: dict) -> dict[str, dict[str, dict[str, str]]]:
    """Collect column metadata from all providers.

    Scans context_metadata for any key ending in ``_columns`` and returns
    a dict keyed by source name (e.g., ``"dbt"``) whose values are the
    ``{model_name: {col_name: col_desc}}`` dicts from each provider.
    """
    sources: dict[str, dict[str, dict[str, str]]] = {}
    for key, value in context_metadata.items():
        if key.endswith("_columns") and isinstance(value, dict):
            # Derive source name: "dbt_columns" -> "dbt", "sqlmesh_columns" -> "sqlmesh"
            source = key.removesuffix("_columns")
            sources[source] = value
    return sources


def search_columns(
    repo: str,
    query: str,
    model_pattern: Optional[str] = None,
    max_results: int = 20,
    storage_path: Optional[str] = None,
) -> dict:
    """Search column metadata across all indexed models.

    Searches any provider that emits a ``*_columns`` key in context_metadata
    (e.g., dbt, SQLMesh, database catalogs). Results include a ``source``
    field identifying which provider contributed the metadata.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Search query (matches column names and descriptions).
        model_pattern: Optional glob to filter by model name (e.g., 'fact_*').
        max_results: Maximum results to return.
        storage_path: Custom storage path.

    Returns:
        Dict with matching columns and _meta envelope.
    """
    start = time.perf_counter()
    hard_cap = int(os.environ.get("JCODEMUNCH_MAX_RESULTS", "500"))
    max_results = max(1, min(max_results, hard_cap))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # Collect column metadata from all providers
    column_sources = _collect_all_columns(index.context_metadata)
    if not column_sources:
        return {
            "error": "No column metadata found. Ensure the project has a supported "
            "ecosystem (dbt, etc.) and re-index to populate column data.",
        }

    # Build model name -> file path lookup from symbols
    model_files: dict[str, str] = {}
    for sym in index.symbols:
        file_path = sym.get("file", "")
        if not file_path.endswith(".sql"):
            continue
        stem = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem not in model_files:
            model_files[stem] = file_path

    # Apply model pattern filter
    if model_pattern:
        import fnmatch

    # Search across all sources
    query_lower = query.lower()
    query_words = set(query_lower.split())
    results = []
    total_models = 0
    total_columns = 0

    for source_name, all_models in column_sources.items():
        if model_pattern:
            filtered_models = {
                k: v for k, v in all_models.items()
                if fnmatch.fnmatch(k, model_pattern)
            }
        else:
            filtered_models = all_models

        total_models += len(all_models)
        total_columns += sum(len(cols) for cols in all_models.values())

        for model_name, columns in filtered_models.items():
            for col_name, col_desc in columns.items():
                col_lower = col_name.lower()
                desc_lower = col_desc.lower()

                score = 0
                # Exact column name match
                if query_lower == col_lower:
                    score = 30
                # Column name contains query
                elif query_lower in col_lower:
                    score = 15
                # Word overlap with column name
                else:
                    for word in query_words:
                        if word in col_lower:
                            score += 5

                # Description match (additive)
                if query_lower in desc_lower:
                    score += 8
                else:
                    for word in query_words:
                        if word in desc_lower:
                            score += 2

                if score > 0:
                    result = {
                        "model": model_name,
                        "file": model_files.get(model_name, ""),
                        "column": col_name,
                        "description": col_desc,
                        "score": score,
                    }
                    # Only include source when multiple providers contribute
                    if len(column_sources) > 1:
                        result["source"] = source_name
                    results.append(result)

    results.sort(key=lambda r: (-r["score"], r["model"], r["column"]))
    truncated = len(results) > max_results
    results = results[:max_results]

    # Token savings estimate
    raw_bytes = total_columns * 50
    response_bytes = sum(len(r["column"]) + len(r["description"]) + len(r["model"]) + 20 for r in results)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved)

    elapsed = (time.perf_counter() - start) * 1000

    return {
        "repo": f"{owner}/{name}",
        "query": query,
        "result_count": len(results),
        "total_models": total_models,
        "total_columns": total_columns,
        "sources": list(column_sources.keys()),
        "results": results,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "truncated": truncated,
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
