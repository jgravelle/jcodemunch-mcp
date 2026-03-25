"""Search symbols across repository."""

import heapq
import math
import re
import time
from fnmatch import fnmatch
from typing import Optional

from ..storage import IndexStore, CodeIndex, record_savings, estimate_savings, cost_avoided
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo

BYTES_PER_TOKEN = 4

# BM25 hyperparameters (standard Robertson et al. values)
_BM25_K1 = 1.5
_BM25_B = 0.75

# Per-field repetition weights: name appears 3× in the virtual doc, etc.
_FIELD_REPS = {"name": 3, "keywords": 2, "signature": 2, "summary": 1, "docstring": 1}

# Centrality: log-scaled bonus for symbols in frequently-imported files (tiebreaker only)
_CENTRALITY_WEIGHT = 0.3

# Pre-compiled regexes for _tokenize (called ~9000× on cold BM25 build)
_CAMEL_RE = re.compile(r"([a-z])([A-Z])")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]{2,}")


def _tokenize(text: str) -> list[str]:
    """Split camelCase / snake_case text into lowercase tokens."""
    if not text:
        return []
    # Insert separator before each uppercase letter that follows a lowercase letter
    text = _CAMEL_RE.sub(r"\1_\2", text)
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _sym_tokens(sym: dict) -> list[str]:
    """Weighted token bag for a symbol (repetition = field weight).
    Cached on the symbol dict to avoid re-tokenizing across calls.
    Also caches _tf (term frequency dict) and _dl (document length)."""
    cached = sym.get("_tokens")
    # Fast path: tokens AND tf/dl all present — nothing to do
    if cached is not None and "_tf" in sym:
        return cached
    # Build tokens if not yet cached (or reuse if carried forward without _tf/_dl)
    if cached is not None:
        tokens = cached
    else:
        tokens = []
        tokens += _tokenize(sym.get("name", "")) * _FIELD_REPS["name"]
        tokens += [kw.lower() for kw in sym.get("keywords", [])] * _FIELD_REPS["keywords"]
        tokens += _tokenize(sym.get("signature", "")) * _FIELD_REPS["signature"]
        tokens += _tokenize(sym.get("summary", "")) * _FIELD_REPS["summary"]
        tokens += _tokenize(sym.get("docstring", "")) * _FIELD_REPS["docstring"]
        sym["_tokens"] = tokens
    # Always (re)compute tf/dl — cheap dict ops, ensures consistency
    # NB: _tokens/_tf/_dl are internal; all API-facing code must use explicit
    # key picks, not raw dict passthrough
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    sym["_tf"] = tf
    sym["_dl"] = len(tokens)
    return tokens


def _compute_bm25(symbols: list[dict]) -> tuple[dict[str, float], float, dict[str, list[int]]]:
    """Return (idf_map, avgdl, inverted_index) computed over all symbols.

    The inverted_index maps each term to the list of symbol indices that
    contain it, enabling candidate-set narrowing at query time.
    """
    N = len(symbols)
    if N == 0:
        return {}, 0.0, {}
    df: dict[str, int] = {}
    total_dl = 0
    inverted: dict[str, list[int]] = {}
    for i, sym in enumerate(symbols):
        toks = _sym_tokens(sym)
        total_dl += len(toks)
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
            inverted.setdefault(t, []).append(i)
    avgdl = total_dl / N
    idf = {t: math.log((N - d + 0.5) / (d + 0.5) + 1.0) for t, d in df.items()}
    return idf, avgdl, inverted


def _compute_centrality(symbols: list[dict], imports: Optional[dict]) -> dict[str, float]:
    """Return {file: log-scaled centrality bonus} based on importer count."""
    if not imports:
        return {}
    source_files = frozenset(s["file"] for s in symbols)
    counts: dict[str, int] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files)
            if target:
                counts[target] = counts.get(target, 0) + 1
    return {f: math.log(1 + c) * _CENTRALITY_WEIGHT for f, c in counts.items()}


def _bm25_score(sym: dict, query_terms: list[str], idf: dict[str, float], avgdl: float,
                centrality: Optional[dict] = None) -> float:
    """BM25 score for a single symbol.

    Uses pre-cached _tf and _dl from _sym_tokens() to avoid rebuilding
    the term frequency dict on every call.
    """
    _sym_tokens(sym)  # ensure _tf/_dl are populated
    tf_raw = sym["_tf"]
    dl = sym["_dl"]

    # Exact name match bonus so direct lookups still float to the top
    name_lower = sym.get("name", "").lower()
    query_joined = " ".join(query_terms)
    score: float = 50.0 if query_joined == name_lower else 0.0

    K = _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / max(avgdl, 1.0))
    for term in set(query_terms):
        idf_val = idf.get(term, 0.0)
        if idf_val == 0.0:
            continue
        tf = tf_raw.get(term, 0)
        if tf == 0:
            continue
        score += idf_val * (tf * (_BM25_K1 + 1)) / (tf + K)

    if centrality:
        score += centrality.get(sym.get("file", ""), 0.0)

    return score


def _bm25_breakdown(sym: dict, query_terms: list[str], idf: dict[str, float], avgdl: float) -> dict:
    """Per-field BM25 contribution breakdown (for debug mode).

    Uses cached _dl from _sym_tokens() for K computation but re-tokenizes
    per field to attribute score contributions individually.
    """
    _sym_tokens(sym)  # ensure _dl is populated
    dl = sym["_dl"]
    K = _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / max(avgdl, 1.0))

    query_set = set(query_terms)
    # Per-field tokenization is unavoidable here — we need per-field attribution
    fields = {
        "name": _tokenize(sym.get("name", "")) * _FIELD_REPS["name"],
        "keywords": [kw.lower() for kw in sym.get("keywords", [])] * _FIELD_REPS["keywords"],
        "signature": _tokenize(sym.get("signature", "")) * _FIELD_REPS["signature"],
        "summary": _tokenize(sym.get("summary", "")) * _FIELD_REPS["summary"],
        "docstring": _tokenize(sym.get("docstring", "")) * _FIELD_REPS["docstring"],
    }
    out: dict[str, float] = {}
    for fname, ftoks in fields.items():
        tf_raw: dict[str, int] = {}
        for t in ftoks:
            tf_raw[t] = tf_raw.get(t, 0) + 1
        field_score = 0.0
        for term in query_set:
            tf = tf_raw.get(term, 0)
            if tf > 0 and idf.get(term, 0.0) > 0:
                field_score += idf[term] * (tf * (_BM25_K1 + 1)) / (tf + K)
        out[fname] = round(field_score, 3)
    out["name_exact_bonus"] = 50.0 if " ".join(query_terms) == sym.get("name", "").lower() else 0.0
    return out


def search_symbols(
    repo: str,
    query: str,
    kind: Optional[str] = None,
    file_pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 10,
    token_budget: Optional[int] = None,
    detail_level: str = "standard",
    debug: bool = False,
    storage_path: Optional[str] = None
) -> dict:
    """Search for symbols matching a query.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Search query.
        kind: Optional filter by symbol kind.
        file_pattern: Optional glob pattern to filter files.
        language: Optional filter by language (e.g., "python", "javascript").
        max_results: Maximum results to return (ignored when token_budget is set).
        token_budget: Maximum tokens to consume. Results are greedily packed by
            score until the budget is exhausted. Overrides max_results.
        detail_level: Controls result verbosity. "compact" returns id/name/kind/file/line
            only (~15 tokens each, ideal for discovery). "standard" returns signatures
            and summaries (default). "full" inlines source code, docstring, and end_line.
        debug: When True, include per-field score breakdown in each result.
        storage_path: Custom storage path.

    Returns:
        Dict with search results and _meta envelope.
    """
    if detail_level not in ("compact", "standard", "full"):
        return {"error": f"Invalid detail_level '{detail_level}'. Must be 'compact', 'standard', or 'full'."}

    _MAX_QUERY_LEN = 500
    if len(query) > _MAX_QUERY_LEN:
        return {"error": f"Query too long ({len(query)} chars, max {_MAX_QUERY_LEN})"}

    start = time.perf_counter()
    max_results = max(1, min(max_results, 100))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    # Load index
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # BM25 corpus stats — cached on CodeIndex, computed once per index load
    query_terms = _tokenize(query) or [query.lower()]
    cache = index._bm25_cache
    if "idf" not in cache:
        cache["idf"], cache["avgdl"], cache["inverted"] = _compute_bm25(index.symbols)
        cache["centrality"] = _compute_centrality(index.symbols, index.imports)
    idf = cache["idf"]
    avgdl = cache["avgdl"]
    centrality = cache["centrality"]
    inverted = cache["inverted"]

    # Narrow candidates using inverted index: only score symbols that
    # contain at least one query term (union of posting lists).
    # Filters (kind/file_pattern/language) are applied AFTER narrowing.
    # Falls back to full scan when no posting lists match (e.g. query
    # terms not in any symbol) to preserve centrality-only results.
    candidate_indices: set[int] = set()
    for term in query_terms:
        posting = inverted.get(term)
        if posting:
            candidate_indices.update(posting)
    if candidate_indices:
        candidates = [index.symbols[i] for i in sorted(candidate_indices)]
    else:
        candidates = index.symbols

    has_filters = bool(kind or file_pattern or language)

    # Bound the heap size in both modes.
    # token_budget mode: estimate ceiling as budget_bytes / min_symbol_size so the
    # heap stays O(N log K) instead of O(N log N) on large indexes.
    # A 20-byte floor is conservative — real symbols are rarely smaller.
    _MIN_BYTES_PER_SYMBOL = 20
    if token_budget is not None:
        budget_bytes = token_budget * BYTES_PER_TOKEN
        effective_limit = max(max_results, budget_bytes // _MIN_BYTES_PER_SYMBOL)
    else:
        effective_limit = max_results
    heap: list[tuple[float, int, dict]] = []  # (score, candidates_scored, entry)
    candidates_scored = 0

    for sym in candidates:
        if has_filters:
            if kind and sym.get("kind") != kind:
                continue
            if file_pattern and not fnmatch(sym.get("file", ""), file_pattern):
                continue
            if language and sym.get("language") != language:
                continue

        score = _bm25_score(sym, query_terms, idf, avgdl, centrality)
        if score <= 0:
            continue

        candidates_scored += 1

        if detail_level == "compact":
            entry = {
                "id": sym["id"],
                "name": sym["name"],
                "kind": sym["kind"],
                "file": sym["file"],
                "line": sym["line"],
                "byte_length": sym.get("byte_length", 0),
            }
        else:
            entry = {
                "id": sym["id"],
                "kind": sym["kind"],
                "name": sym["name"],
                "file": sym["file"],
                "line": sym["line"],
                "signature": sym["signature"],
                "summary": sym.get("summary", ""),
                "byte_length": sym.get("byte_length", 0),
            }
        if debug:
            entry["score"] = round(score, 3)
            entry["score_breakdown"] = _bm25_breakdown(sym, query_terms, idf, avgdl)

        # Bounded heap: O(N log K) instead of O(N log N)
        if len(heap) < effective_limit:
            heapq.heappush(heap, (score, candidates_scored, entry))
        elif score > heap[0][0]:
            heapq.heapreplace(heap, (score, candidates_scored, entry))

    # Extract results sorted by score descending
    scored_results = [entry for _, _, entry in sorted(heap, key=lambda x: x[0], reverse=True)]

    if token_budget is not None:
        packed, used_bytes = [], 0
        for entry in scored_results:
            b = entry["byte_length"]
            if used_bytes + b <= budget_bytes:
                packed.append(entry)
                used_bytes += b
        scored_results = packed

    # Full detail: inline source, docstring, end_line for each result
    if detail_level == "full":
        for entry in scored_results:
            sym = index._get_symbol_raw(entry["id"])
            if sym:
                source = store.get_symbol_content(owner, name, entry["id"], _index=index)
                entry["end_line"] = sym.get("end_line", entry["line"])
                entry["docstring"] = sym.get("docstring", "")
                entry["source"] = source or ""

    # Token savings: files containing matches vs symbol byte_lengths of results
    raw_bytes = 0
    seen_files: set = set()
    response_bytes = 0
    for entry in scored_results:
        f = entry["file"]
        if f not in seen_files:
            seen_files.add(f)
            raw_bytes += index.file_sizes.get(f, 0)
        response_bytes += entry["byte_length"]
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="search_symbols")

    elapsed = (time.perf_counter() - start) * 1000

    meta = {
        "timing_ms": round(elapsed, 1),
        "total_symbols": len(index.symbols),
        "truncated": candidates_scored > len(scored_results),
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        **cost_avoided(tokens_saved, total_saved),
    }
    if token_budget is not None:
        used = sum(e["byte_length"] for e in scored_results)
        meta["token_budget"] = token_budget
        meta["tokens_used"] = used // BYTES_PER_TOKEN
        meta["tokens_remaining"] = max(0, token_budget - used // BYTES_PER_TOKEN)
    if debug:
        meta["candidates_scored"] = candidates_scored

    return {
        "result_count": len(scored_results),
        "results": scored_results,
        "_meta": meta,
    }


