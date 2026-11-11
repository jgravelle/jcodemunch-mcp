"""Get raw cached file content."""

import time
from typing import Optional

from ..retrieval.verdict import file_verdict_for_index, withheld_file_info
from ..storage import IndexStore, cost_avoided, estimate_savings, record_savings
from ._utils import index_status_to_tool_error, resolve_repo


def get_file_content(
    repo: str,
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Return cached file content, optionally sliced to a line range."""
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))
    if not index.has_source_file(file_path):
        withheld = withheld_file_info(index, file_path)
        out = {
            "error": (
                f"File not indexed: {file_path}. {withheld['summary']} {withheld['remedy']}" if withheld
                else f"File not found: {file_path}"
            ),
            "file": file_path,
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

    content = store.get_file_content(owner, name, file_path, _index=index)
    if content is None:
        # Distinguish "no body cached because we're in metadata-only mode"
        # from "indexed file but cache is missing for some other reason."
        # P1.4: when cache_mode == metadata_only, get_file_content is a
        # documented no-op rather than a surprise cache miss.
        try:
            from .. import config as _cfg
            if _cfg.get("cache_mode", "full") == "metadata_only":
                return {
                    "error": "metadata_only_mode",
                    "detail": (
                        "cache_mode=metadata_only — source bodies are not "
                        "persisted to disk under this config. Re-index with "
                        "cache_mode=full to populate file content, or use "
                        "tools that don't require bodies (search_symbols, "
                        "get_file_outline, find_references)."
                    ),
                    "file": file_path,
                }
        except Exception:
            pass
        return {"error": f"File content not found: {file_path}"}

    lines = content.splitlines()
    line_count = len(lines)
    if line_count == 0:
        actual_start = 0
        actual_end = 0
        selected_content = ""
    elif start_line is None and end_line is None:
        actual_start = 1
        actual_end = line_count
        selected_content = content
    else:
        actual_start = max(1, min(start_line if start_line is not None else 1, line_count))
        actual_end = max(actual_start, min(end_line if end_line is not None else line_count, line_count))
        selected_content = "\n".join(lines[actual_start - 1:actual_end])

    raw_bytes = index.file_sizes.get(file_path, 0)
    response_bytes = len(selected_content.encode("utf-8"))
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_file_content")
    elapsed = (time.perf_counter() - start) * 1000

    return {
        "repo": f"{owner}/{name}",
        "file": file_path,
        "language": index.file_languages.get(file_path, ""),
        "file_summary": index.file_summaries.get(file_path, ""),
        "start_line": actual_start,
        "end_line": actual_end,
        "line_count": line_count,
        "content": selected_content,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
            "verdict": file_verdict_for_index(index, present=True),
        },
    }
