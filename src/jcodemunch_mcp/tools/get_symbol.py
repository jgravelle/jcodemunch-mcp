"""Get symbol source code."""

import hashlib
import os
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided as _cost_avoided
from ._utils import resolve_repo, resolve_fqn


def _make_meta(timing_ms: float, **kwargs) -> dict:
    """Build a _meta envelope dict."""
    meta = {"timing_ms": round(timing_ms, 1)}
    meta.update(kwargs)
    return meta


def get_symbol_source(
    repo: str,
    symbol_id: Optional[str] = None,
    symbol_ids: Optional[list[str]] = None,
    verify: bool = False,
    context_lines: int = 0,
    storage_path: Optional[str] = None,
    fqn: Optional[str] = None,
) -> dict:
    """Get full source of one or more symbols by ID.

    Pass symbol_id (string) for one symbol — returns flat symbol object.
    Pass symbol_ids (array) for batch — returns {symbols, errors}.
    Both modes support verify and context_lines.
    Pass fqn (PHP FQN like 'App\\Models\\User') to resolve via PSR-4.
    """
    # FQN resolution: translate PHP FQN → symbol_id
    if fqn and symbol_id is None and symbol_ids is None:
        resolved, fqn_error = resolve_fqn(repo, fqn, storage_path)
        if resolved is None:
            return {"error": fqn_error or f"Could not resolve FQN '{fqn}'."}
        symbol_id = resolved

    # Normalize: some MCP clients send symbol_ids=[] alongside symbol_id when they mean singular mode
    if symbol_id is not None and symbol_ids is not None and len(symbol_ids) == 0:
        symbol_ids = None
    if symbol_id is None and symbol_ids is None:
        return {"error": "Provide symbol_id (string), symbol_ids (array), or fqn (PHP FQN)."}
    if symbol_id is not None and symbol_ids is not None:
        return {"error": "Provide symbol_id or symbol_ids, not both."}

    batch_mode = symbol_ids is not None
    ids = symbol_ids if batch_mode else [symbol_id]

    start = time.perf_counter()
    context_lines = max(0, min(context_lines, 50))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    symbols_out = []
    errors_out = []
    seen_files: set = set()
    raw_bytes = 0
    response_bytes = 0

    for sid in ids:
        symbol = index.get_symbol(sid)

        if not symbol:
            errors_out.append({"id": sid, "error": f"Symbol not found: {sid}"})
            continue

        source = store.get_symbol_content(owner, name, sid, _index=index)
        content_dir = store._content_dir(owner, name)
        file_full_path = content_dir / symbol["file"]

        context_before = ""
        context_after = ""
        if context_lines > 0 and source and file_full_path.exists():
            try:
                all_lines = file_full_path.read_text(encoding="utf-8", errors="replace").split("\n")
                s_line = symbol["line"] - 1  # 0-indexed
                e_line = symbol["end_line"]   # exclusive
                before_start = max(0, s_line - context_lines)
                after_end = min(len(all_lines), e_line + context_lines)
                if before_start < s_line:
                    context_before = "\n".join(all_lines[before_start:s_line])
                if e_line < after_end:
                    context_after = "\n".join(all_lines[e_line:after_end])
            except Exception:
                pass

        entry = {
            "id": symbol["id"],
            "kind": symbol["kind"],
            "name": symbol["name"],
            "file": symbol["file"],
            "line": symbol["line"],
            "end_line": symbol["end_line"],
            "signature": symbol["signature"],
            "decorators": symbol.get("decorators", []),
            "docstring": symbol.get("docstring", ""),
            "content_hash": symbol.get("content_hash", ""),
            "source": source or "",
        }
        if context_before:
            entry["context_before"] = context_before
        if context_after:
            entry["context_after"] = context_after

        if verify and source:
            actual_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            stored_hash = symbol.get("content_hash", "")
            entry["content_verified"] = actual_hash == stored_hash if stored_hash else None

        symbols_out.append(entry)

        # Accumulate token savings
        f = symbol["file"]
        if f not in seen_files:
            seen_files.add(f)
            try:
                raw_bytes += os.path.getsize(file_full_path)
            except OSError:
                pass
        response_bytes += symbol.get("byte_length", 0)

    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_symbol_source")
    elapsed = (time.perf_counter() - start) * 1000
    meta = _make_meta(elapsed, tokens_saved=tokens_saved, total_tokens_saved=total_saved,
                      **_cost_avoided(tokens_saved, total_saved))

    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(symbols_out)

    if batch_mode:
        meta["symbol_count"] = len(symbols_out)
        meta["freshness"] = _probe.summary(symbols_out)
        return {"symbols": symbols_out, "errors": errors_out, "_meta": meta}

    # Single mode: flat object or error
    if errors_out:
        return {"error": errors_out[0]["error"]}
    result = symbols_out[0]
    meta["hint"] = "Use get_context_bundle(symbol_id) to retrieve source + imports in one call"
    meta["freshness"] = _probe.summary(symbols_out)
    result["_meta"] = meta
    return result
