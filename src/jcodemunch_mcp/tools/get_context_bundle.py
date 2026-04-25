"""Get a context bundle: symbol definitions + file imports, with optional caller list."""

import math
import os
import re
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided as _cost_avoided
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo

_BYTES_PER_TOKEN = 4


def _count_tokens(text: str) -> int:
    """Estimate token count. Uses tiktoken (cl100k_base) when available, else len/4."""
    if not text:
        return 0
    try:
        import tiktoken  # noqa: PLC0415
        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except ImportError:
        return max(1, len(text) // _BYTES_PER_TOKEN)


def _entry_tokens(entry: dict) -> int:
    """Estimate total token cost for a symbol entry (source + signature + imports)."""
    parts = [
        entry.get("source") or "",
        entry.get("signature") or "",
        "\n".join(entry.get("imports") or []),
    ]
    return _count_tokens("".join(parts))


def _file_centrality(index) -> dict[str, float]:
    """Return {file: log-scaled centrality} from the import graph (in-degree count)."""
    if not index.imports:
        return {}
    source_files = frozenset(index.source_files)
    counts: dict[str, int] = {}
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, index.alias_map, getattr(index, "psr4_map", None))
            if target:
                counts[target] = counts.get(target, 0) + 1
    return {f: math.log(1 + c) for f, c in counts.items()}


def _apply_token_budget(
    entries: list[dict],
    token_budget: int,
    strategy: str,
    index,
) -> tuple[list[dict], dict]:
    """Trim/reshape entries to fit within token_budget.

    Returns (trimmed_entries, budget_report_dict).
    """
    if strategy == "compact":
        # Strip source bodies from all entries — keep signatures only.
        # No symbol exclusion: compact symbols are tiny.
        for e in entries:
            e["source"] = ""
        used = sum(_entry_tokens(e) for e in entries)
        return entries, {
            "budget_tokens": token_budget,
            "used_tokens": used,
            "included_symbols": len(entries),
            "excluded_symbols": 0,
            "excluded_by": [],
            "strategy": strategy,
        }

    centrality = _file_centrality(index)

    if strategy == "core_first":
        # Primary symbol (index 0) is highest priority; rest ranked by centrality.
        if len(entries) > 1:
            rest = sorted(entries[1:], key=lambda e: -centrality.get(e["file"], 0.0))
            ordered = [entries[0]] + rest
        else:
            ordered = list(entries)
    else:  # most_relevant
        ordered = sorted(entries, key=lambda e: -centrality.get(e["file"], 0.0))

    included: list[dict] = []
    used_tokens = 0
    for e in ordered:
        cost = _entry_tokens(e)
        if used_tokens + cost <= token_budget:
            included.append(e)
            used_tokens += cost

    excluded = len(entries) - len(included)
    return included, {
        "budget_tokens": token_budget,
        "used_tokens": used_tokens,
        "included_symbols": len(included),
        "excluded_symbols": excluded,
        "excluded_by": ["token_budget"] if excluded else [],
        "strategy": strategy,
    }


def _make_meta(timing_ms: float, **kwargs) -> dict:
    meta = {"timing_ms": round(timing_ms, 1)}
    meta.update(kwargs)
    return meta


# Import patterns per language: list of compiled regexes that match a single import line.
# For block-style imports (Go), we handle them separately.
_IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "python":     [re.compile(r"^\s*(import |from \S+ import )")],
    "javascript": [re.compile(r"^\s*(import |.*\brequire\s*\()")],
    "typescript": [re.compile(r"^\s*(import |.*\brequire\s*\()")],
    "tsx":        [re.compile(r"^\s*(import |.*\brequire\s*\()")],
    "go":         [re.compile(r"^\s*import\b")],
    "rust":       [re.compile(r"^\s*use \S")],
    "java":       [re.compile(r"^\s*import \S")],
    "kotlin":     [re.compile(r"^\s*import \S")],
    "csharp":     [re.compile(r"^\s*using \S")],
    "c":          [re.compile(r"^\s*#\s*include\b")],
    "cpp":        [re.compile(r"^\s*#\s*include\b")],
    "arduino":    [re.compile(r"^\s*#\s*include\b")],
    "vhdl":       [re.compile(r"^\s*(library|use)\s+\w+", re.IGNORECASE)],
    "verilog":    [re.compile(r"^\s*`include\s+")],
    "swift":      [re.compile(r"^\s*import \S")],
    "ruby":       [re.compile(r"^\s*(require |require_relative )")],
    "php":        [re.compile(r"^\s*(use |require|include)\b")],
    "elixir":     [re.compile(r"^\s*(import |alias |use |require )\S")],
    "scala":      [re.compile(r"^\s*import \S")],
    "haskell":    [re.compile(r"^\s*import \S")],
    "lua":        [re.compile(r"^\s*(require\s*[\(\"])")],
    "dart":       [re.compile(r"^\s*import \S")],
}


def _extract_imports(content: str, language: str) -> list[str]:
    """Extract import lines from file content for the given language."""
    patterns = _IMPORT_PATTERNS.get(language, [])
    if not patterns:
        return []

    lines = content.splitlines()
    imports: list[str] = []

    if language == "go":
        # Go has block imports: import ( ... ) as well as single-line imports
        in_block = False
        for line in lines:
            stripped = line.strip()
            if stripped == "import (":
                in_block = True
                imports.append(line)
                continue
            if in_block:
                imports.append(line)
                if stripped == ")":
                    in_block = False
                continue
            if any(p.match(line) for p in patterns):
                imports.append(line)
        return imports

    for line in lines:
        if any(p.match(line) for p in patterns):
            imports.append(line)

    return imports


def _direct_callers(index, store, owner: str, name: str, sym_file: str) -> list[str]:
    """Return files that directly import sym_file (depth=1)."""
    if index.imports is None:
        return []
    source_files = frozenset(index.source_files)
    callers: list[str] = []
    for src_file, file_imports in index.imports.items():
        if src_file == sym_file:
            continue
        for imp in file_imports:
            resolved = resolve_specifier(imp["specifier"], src_file, source_files, index.alias_map)
            if resolved == sym_file:
                callers.append(src_file)
                break
    return sorted(callers)


def _to_markdown(repo: str, symbol_entries: list[dict], file_imports_cache: dict) -> str:
    """Render symbol entries as structured markdown."""
    lines: list[str] = [f"# Context Bundle: {repo}\n"]
    for e in symbol_entries:
        lang = e.get("language", "")
        fence = lang if lang else ""
        lines.append(f"## `{e['name']}` ({e['kind']}) — `{e['file']}:{e['line']}`\n")

        imports = e.get("imports") or file_imports_cache.get(e["file"], [])
        if imports:
            lines.append(f"### Imports\n```{fence}\n" + "\n".join(imports) + "\n```\n")

        if e.get("docstring"):
            lines.append(f"> {e['docstring'].strip()}\n")

        if e.get("source"):
            lines.append(f"### Definition\n```{fence}\n{e['source'].rstrip()}\n```\n")

        if e.get("callers"):
            lines.append("### Callers\n" + "\n".join(f"- `{c}`" for c in e["callers"]) + "\n")

        lines.append("---\n")

    return "\n".join(lines)


def get_context_bundle(
    repo: str,
    symbol_id: Optional[str] = None,
    symbol_ids: Optional[list] = None,
    include_callers: bool = False,
    output_format: str = "json",
    token_budget: Optional[int] = None,
    budget_strategy: str = "most_relevant",
    include_budget_report: bool = False,
    storage_path: Optional[str] = None,
    fqn: Optional[str] = None,
) -> dict:
    """Get a context bundle: symbol definitions + imports from their files.

    Supports single or multi-symbol bundles. When multiple symbols share a
    file, imports for that file are deduplicated — you get one import block
    per file, not one per symbol.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol_id: Single symbol ID (backward-compatible). Mutually exclusive
            with symbol_ids.
        symbol_ids: List of symbol IDs for a multi-symbol bundle.
        include_callers: When True, each symbol entry gains a ``callers`` list
            of files that directly import its defining file.
        output_format: 'json' (default) or 'markdown'.
        token_budget: Max tokens to return. When set, content is ranked and
            trimmed to fit. Uses ``budget_strategy`` to decide what to keep.
        budget_strategy: How to rank/trim when token_budget is set.
            'most_relevant' (default) ranks by file centrality (import in-degree).
            'core_first' keeps the primary symbol first, ranks rest by centrality.
            'compact' strips all source bodies — returns signatures only, no trimming.
        include_budget_report: When True, include a 'budget_report' field
            showing what was included/excluded.
        storage_path: Custom storage path.

    Returns:
        Single-symbol: legacy flat response (backward-compatible).
        Multi-symbol: ``symbols`` list + ``files`` import map.
    """
    start = time.perf_counter()

    if output_format not in ("json", "markdown"):
        return {"error": f"Invalid output_format '{output_format}'. Must be 'json' or 'markdown'."}

    if budget_strategy not in ("most_relevant", "core_first", "compact"):
        return {"error": f"Invalid budget_strategy '{budget_strategy}'. Must be 'most_relevant', 'core_first', or 'compact'."}

    # FQN resolution: translate PHP FQN → symbol_id
    if fqn and symbol_id is None and symbol_ids is None:
        from ._utils import resolve_fqn
        _resolved, _err = resolve_fqn(repo, fqn, storage_path)
        if _resolved is None:
            return {"error": _err or f"Could not resolve FQN '{fqn}'."}
        symbol_id = _resolved

    # Normalise inputs
    if symbol_ids is not None:
        ids = list(dict.fromkeys(symbol_ids))  # deduplicate, preserve order
        multi = True
    elif symbol_id is not None:
        ids = [symbol_id]
        multi = False
    else:
        return {"error": "Provide either 'symbol_id' or 'symbol_ids'."}

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # Resolve all symbols
    resolved: list[dict] = []
    missing: list[str] = []
    for sid in ids:
        sym = index.get_symbol(sid)
        if sym:
            resolved.append(sym)
        else:
            missing.append(sid)
    if missing:
        return {"error": f"Symbol(s) not found: {', '.join(missing)}"}

    # Per-file import cache (deduplicate across symbols sharing a file)
    file_imports_cache: dict[str, list[str]] = {}
    file_content_cache: dict[str, Optional[str]] = {}

    def _get_file_imports(sym_file: str, language: str) -> list[str]:
        if sym_file not in file_imports_cache:
            content = store.get_file_content(owner, name, sym_file, _index=index)
            file_content_cache[sym_file] = content
            file_imports_cache[sym_file] = _extract_imports(content, language) if content else []
        return file_imports_cache[sym_file]

    # Token savings accumulator
    seen_files_for_savings: set[str] = set()
    raw_bytes_total = 0
    response_bytes_total = 0

    # Build per-symbol entries
    symbol_entries: list[dict] = []
    for sym in resolved:
        source = store.get_symbol_content(owner, name, sym["id"], _index=index)
        language = sym.get("language", "")
        imports = _get_file_imports(sym["file"], language)

        entry: dict = {
            "symbol_id": sym["id"],
            "name": sym["name"],
            "kind": sym["kind"],
            "file": sym["file"],
            "line": sym["line"],
            "end_line": sym["end_line"],
            "signature": sym["signature"],
            "docstring": sym.get("docstring", ""),
            "source": source or "",
            "imports": imports,
        }

        if include_callers:
            entry["callers"] = _direct_callers(index, store, owner, name, sym["file"])

        symbol_entries.append(entry)

        # Token savings: count each source file only once
        if sym["file"] not in seen_files_for_savings:
            seen_files_for_savings.add(sym["file"])
            try:
                raw_bytes_total += os.path.getsize(store._content_dir(owner, name) / sym["file"])
            except OSError:
                pass
        response_bytes_total += sym.get("byte_length", 0)

    tokens_saved = estimate_savings(raw_bytes_total, response_bytes_total)
    total_saved = record_savings(tokens_saved, tool_name="get_context_bundle")

    # ── Token budget trimming ─────────────────────────────────────────────────
    budget_report: Optional[dict] = None
    if token_budget is not None:
        symbol_entries, budget_report = _apply_token_budget(
            symbol_entries, token_budget, budget_strategy, index
        )

    elapsed = (time.perf_counter() - start) * 1000
    meta_kwargs = {
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        **_cost_avoided(tokens_saved, total_saved),
    }

    repo_id = f"{owner}/{name}"

    # ── Markdown output (single or multi) ─────────────────────────────────────
    if output_format == "markdown":
        md = _to_markdown(repo_id, symbol_entries, file_imports_cache)
        result = {"markdown": md, "_meta": _make_meta(elapsed, **meta_kwargs)}
        if include_budget_report and budget_report is not None:
            result["budget_report"] = budget_report
        return result

    # ── Single-symbol: preserve original flat response shape ──────────────────
    if not multi:
        if not symbol_entries:
            # Budget trimmed the only symbol
            result = {"error": "token_budget too small to include any symbol content."}
            if include_budget_report and budget_report is not None:
                result["budget_report"] = budget_report
            return result
        e = symbol_entries[0]
        from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
        _single_probe = _FreshnessProbe(
            source_root=getattr(index, "source_root", "") or None,
            indexed_at=getattr(index, "indexed_at", ""),
            index_sha=getattr(index, "git_head", None),
            file_mtimes=getattr(index, "file_mtimes", None),
        )
        result = {
            "symbol_id": e["symbol_id"],
            "name": e["name"],
            "kind": e["kind"],
            "file": e["file"],
            "line": e["line"],
            "end_line": e["end_line"],
            "signature": e["signature"],
            "docstring": e["docstring"],
            "source": e["source"],
            "imports": e["imports"],
            "_freshness": _single_probe.classify(e.get("file", "")),
        }
        if include_callers:
            result["callers"] = e["callers"]
        if include_budget_report and budget_report is not None:
            result["budget_report"] = budget_report
        result["_meta"] = _make_meta(elapsed, **meta_kwargs)
        return result

    # ── Multi-symbol: new format with deduped file import map ─────────────────
    files_map: dict[str, dict] = {}
    for sym_file, imports in file_imports_cache.items():
        files_map[sym_file] = {"imports": imports}

    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(symbol_entries)

    result = {
        "repo": repo_id,
        "symbol_count": len(symbol_entries),
        "symbols": symbol_entries,
        "files": files_map,
    }
    if include_budget_report and budget_report is not None:
        result["budget_report"] = budget_report
    result["_meta"] = _make_meta(elapsed, **meta_kwargs)
    result["_meta"]["freshness"] = _probe.summary(symbol_entries)
    return result
