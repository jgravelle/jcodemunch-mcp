"""Search symbols across repository."""

import heapq
import math
import re
import time
from fnmatch import fnmatch
from typing import Optional

from ..storage import IndexStore, CodeIndex, record_savings, estimate_savings, cost_avoided
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo, resolve_fqn

BYTES_PER_TOKEN = 4

# Fuzzy search: BM25 score below this auto-triggers the fuzzy pass
_FUZZY_NEAR_MISS_THRESHOLD = 0.1

# Feature 1: Negative evidence threshold (default; overridden by config)
_NEGATIVE_EVIDENCE_THRESHOLD = 0.5

# BM25 hyperparameters (standard Robertson et al. values)
_BM25_K1 = 1.5
_BM25_B = 0.75

# Per-field repetition weights: name appears 3× in the virtual doc, etc.
_FIELD_REPS = {"name": 3, "keywords": 2, "signature": 2, "summary": 1, "docstring": 1}

# Centrality: log-scaled bonus for symbols in frequently-imported files (tiebreaker only)
_CENTRALITY_WEIGHT = 0.3

# PageRank weight for sort_by="combined" (scales PR scores to be meaningful vs BM25 range)
_PR_COMBINED_WEIGHT = 100.0

# Pre-compiled regexes for _tokenize (called ~9000× on cold BM25 build)
_CAMEL_RE = re.compile(r"([a-z])([A-Z])")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]{2,}")

# Search result cache (Feature 5 — session-aware routing)
import threading
from collections import OrderedDict

_RESULT_CACHE_MAX = 128
_result_cache: OrderedDict = OrderedDict()
_result_cache_lock = threading.Lock()


def _result_cache_get(key: tuple) -> Optional[dict]:
    """Return cached result for key, or None on miss. Returns a shallow copy."""
    with _result_cache_lock:
        if key in _result_cache:
            _result_cache.move_to_end(key)  # LRU refresh
            cached = _result_cache[key]
            # Track hit count for session state persistence priority
            cached["_hit_count"] = cached.get("_hit_count", 0) + 1
            # Shallow copy top-level + _meta to prevent caller mutations
            result = dict(cached)
            result.pop("_hit_count", None)  # don't leak internal field
            if "_meta" in result:
                result["_meta"] = dict(result["_meta"])
            return result
    return None


def _get_cache_max() -> int:
    try:
        from .. import config as _cfg
        return _cfg.get("search_result_cache_max", _RESULT_CACHE_MAX)
    except Exception:
        return _RESULT_CACHE_MAX


def _result_cache_put(key: tuple, value: dict) -> None:
    """Store result in LRU cache, evicting oldest if full."""
    with _result_cache_lock:
        if key in _result_cache:
            _result_cache.move_to_end(key)
        _result_cache[key] = value
        _max = _get_cache_max()
        while len(_result_cache) > _max:
            _result_cache.popitem(last=False)  # evict oldest


def result_cache_invalidate_repo(repo_key: str) -> int:
    """Evict all cache entries for a specific repo."""
    evicted = 0
    with _result_cache_lock:
        keys_to_evict = [k for k in _result_cache if k[0] == repo_key]
        for k in keys_to_evict:
            del _result_cache[k]
            evicted += 1
    return evicted


# ---------------------------------------------------------------------------
# Abbreviation map: bidirectional code abbreviation <-> full form.
# Built once at import time.
# ---------------------------------------------------------------------------
_ABBREV_MAP: dict[str, list[str]] = {
    "db": ["database"], "auth": ["authentication", "authorization"],
    "config": ["configuration"], "ctx": ["context"], "env": ["environment"],
    "err": ["error"], "exec": ["execute", "execution"],
    "fn": ["function"], "func": ["function"],
    "impl": ["implementation", "implement"], "init": ["initialize", "initialization"],
    "iter": ["iterator", "iterate"], "len": ["length"], "lib": ["library"],
    "max": ["maximum"], "mem": ["memory"], "min": ["minimum"],
    "msg": ["message"], "num": ["number"], "obj": ["object"],
    "param": ["parameter"], "params": ["parameters"], "pkg": ["package"],
    "prev": ["previous"], "proc": ["process", "procedure"],
    "prop": ["property"], "props": ["properties"],
    "ref": ["reference"], "refs": ["references"], "repo": ["repository"],
    "req": ["request"], "res": ["response", "result"], "ret": ["return"],
    "src": ["source"], "str": ["string"],
    "sync": ["synchronize", "synchronous"], "sys": ["system"],
    "temp": ["temporary"], "tmp": ["temporary"],
    "val": ["value"], "var": ["variable"], "vars": ["variables"],
    # Reverse mappings
    "database": ["db"], "authentication": ["auth"], "authorization": ["auth"],
    "configuration": ["config"], "context": ["ctx"], "environment": ["env"],
    "error": ["err"], "execute": ["exec"], "function": ["func", "fn"],
    "initialize": ["init"], "initialization": ["init"],
    "iterator": ["iter"], "message": ["msg"],
    "parameter": ["param"], "parameters": ["params"],
    "repository": ["repo"], "request": ["req"], "response": ["res"],
    "temporary": ["temp", "tmp"], "variable": ["var"], "variables": ["vars"],
}

# Stemming rules: (suffix, replacement, min_base_length)
# Ordered longest-first; doubled-consonant rules before single.
_STEM_RULES: list[tuple[str, str, int]] = [
    ("ation", "", 3), ("izing", "ize", 3), ("ating", "ate", 3),
    ("nning", "n", 2), ("tting", "t", 2), ("pping", "p", 2),
    ("gging", "g", 2), ("bbing", "b", 2), ("dding", "d", 2),
    ("mming", "m", 2), ("lling", "l", 2),
    ("sses", "ss", 2), ("ness", "", 3), ("ment", "", 3), ("tion", "", 3),
    ("ized", "ize", 3), ("ling", "le", 3), ("ring", "r", 3),
    ("ning", "n", 3), ("ting", "t", 3), ("ping", "p", 3),
    ("bing", "b", 2), ("ding", "d", 3), ("ging", "g", 3),
    ("king", "k", 3), ("ming", "m", 3),
    ("lled", "ll", 3), ("nned", "n", 3), ("tted", "t", 3),
    ("pped", "p", 3), ("gged", "g", 3), ("bbed", "b", 3), ("dded", "d", 3),
    ("ing", "", 3), ("ies", "y", 3),
    ("ed", "", 3), ("er", "", 3), ("ly", "", 3), ("es", "", 4),
]


def _stem(word: str) -> str:
    """Lightweight Porter-style suffix stripping for code identifiers."""
    w = word.lower()
    if len(w) < 5:
        return w
    for suffix, replacement, min_base in _STEM_RULES:
        if w.endswith(suffix):
            base = w[:-len(suffix)]
            if len(base) >= min_base:
                return base + replacement
    # Strip trailing 's' if result is 4+ chars and doesn't end in 's'
    if w.endswith("s") and len(w) >= 5 and w[-2] != "s":
        return w[:-1]
    return w


def _tokenize(text: str) -> list[str]:
    """Split camelCase / snake_case text into tokens with stemming and
    abbreviation expansion for richer BM25 matching."""
    if not text:
        return []
    text = _CAMEL_RE.sub(r"\1_\2", text)
    raw_tokens = [t.lower() for t in _TOKEN_RE.findall(text)]

    result = []
    seen: set[str] = set()
    for tok in raw_tokens:
        result.append(tok)
        seen.add(tok)
        # Stemmed form
        stemmed = _stem(tok)
        if stemmed != tok and stemmed not in seen:
            result.append(stemmed)
            seen.add(stemmed)
        # Abbreviation expansion (canonical forms, not stemmed)
        for key in (tok, stemmed) if stemmed != tok else (tok,):
            for exp in _ABBREV_MAP.get(key, ()):
                if exp not in seen:
                    result.append(exp)
                    seen.add(exp)
    return result


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
    # T10: use unique token count for _dl so it matches df (document-frequency)
    # which also counts unique tokens per symbol. Using len(tokens) inflates
    # avgdl by the field-repetition weights, distorting BM25 normalisation.
    sym["_dl"] = len(set(tokens))
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
        # T11: always rewrite _dl with the canonical unique-token count.
        # This makes BM25 rebuilds correct even for retained symbols whose _dl
        # was cached before T10 (i.e., with the old len(tokens) formula).
        unique_toks = set(toks)
        dl = len(unique_toks)
        sym["_dl"] = dl
        total_dl += dl
        for t in unique_toks:
            df[t] = df.get(t, 0) + 1
            inverted.setdefault(t, []).append(i)
    avgdl = total_dl / N
    idf = {t: math.log((N - d + 0.5) / (d + 0.5) + 1.0) for t, d in df.items()}
    return idf, avgdl, inverted


def _compute_centrality(
    symbols: list[dict], imports: Optional[dict], alias_map: Optional[dict] = None,
    psr4_map: Optional[dict] = None,
) -> dict[str, float]:
    """Return {file: log-scaled centrality bonus} based on importer count."""
    if not imports:
        return {}
    source_files = frozenset(s["file"] for s in symbols)
    counts: dict[str, int] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if target:
                counts[target] = counts.get(target, 0) + 1
    return {f: math.log(1 + c) * _CENTRALITY_WEIGHT for f, c in counts.items()}


def _identity_score(sym: dict, query_joined: str, raw_query: str = "") -> float:
    """Identity channel: exact or prefix match on symbol name/ID.

    Returns a high score for exact matches and a decreasing score for
    prefix matches by specificity.  Replaces the old ``50.0`` exact-name hack.

    Scoring:
      - Exact name match          → 50.0
      - Exact ID match            → 50.0
      - Name starts with query    → 30.0
      - ID contains query segment → 20.0
      - No match                  →  0.0
    """
    raw_lower = raw_query.lower() if raw_query else ""
    if not raw_lower and not query_joined:
        return 0.0
    name_lower = sym.get("name", "").lower()
    sym_id_lower = sym.get("id", "").lower()

    # Raw query preserves snake_case/camelCase for exact matches.
    if raw_lower and (raw_lower == name_lower or raw_lower == sym_id_lower):
        return 50.0

    # Tokenized fallback preserves previous semantics for callers that only have terms.
    if query_joined == name_lower or query_joined == sym_id_lower:
        return 50.0

    # Prefix match on name (e.g. query "get_sym" matches "get_symbol_source")
    if query_joined and name_lower.startswith(query_joined):
        return 30.0
    if raw_lower and name_lower.startswith(raw_lower):
        return 30.0

    # Qualified ID segment match (e.g. query "storage.indexstore" matches
    # "src/storage/index_store.py::IndexStore")
    if query_joined and query_joined in sym_id_lower:
        return 20.0
    if raw_lower and raw_lower in sym_id_lower:
        return 20.0

    return 0.0


def _bm25_score(sym: dict, query_terms: list[str], idf: dict[str, float], avgdl: float,
                centrality: Optional[dict] = None, raw_query: str = "") -> float:
    """BM25 score for a single symbol.

    Uses pre-cached _tf and _dl from _sym_tokens() to avoid rebuilding
    the term frequency dict on every call.
    """
    _sym_tokens(sym)  # ensure _tf/_dl are populated
    tf_raw = sym["_tf"]
    dl = sym["_dl"]

    # Identity channel: exact/prefix match on symbol name or ID
    query_joined = " ".join(query_terms)
    score: float = _identity_score(sym, query_joined, raw_query)

    K = _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / max(avgdl, 1.0))
    for term in set(query_terms):
        idf_val = idf.get(term, 0.0)
        if idf_val == 0.0:
            continue
        tf = tf_raw.get(term, 0)
        if tf == 0:
            continue
        score += idf_val * (tf * (_BM25_K1 + 1)) / (tf + K)

    if centrality and score > 0:
        score += centrality.get(sym.get("file", ""), 0.0)

    return score


def _bm25_breakdown(sym: dict, query_terms: list[str], idf: dict[str, float], avgdl: float, raw_query: str = "") -> dict:
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
    query_joined = " ".join(query_terms)
    identity = _identity_score(sym, query_joined, raw_query)
    out["identity"] = identity
    if identity >= 50.0:
        out["identity_type"] = "exact"
    elif identity >= 30.0:
        out["identity_type"] = "prefix"
    elif identity >= 20.0:
        out["identity_type"] = "segment"
    else:
        out["identity_type"] = "none"
    return out


def _trigrams(text: str) -> frozenset:
    """Return trigram frozenset for a lowercased string."""
    s = text.lower()
    if len(s) < 3:
        return frozenset({s}) if s else frozenset()
    return frozenset(s[i:i + 3] for i in range(len(s) - 2))


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance (Wagner-Fischer, O(min(m,n)) space)."""
    if len(a) > len(b):
        a, b = b, a
    la, lb = len(a), len(b)
    row = list(range(la + 1))
    for j in range(1, lb + 1):
        prev, row[0] = row[0], j
        for i in range(1, la + 1):
            temp = row[i]
            row[i] = min(row[i] + 1, row[i - 1] + 1, prev + (0 if a[i - 1] == b[j - 1] else 1))
            prev = temp
    return row[la]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in pure Python (no numpy).

    Returns 0.0 if either vector is zero-length or the lists differ in size.
    Uses ``math.sqrt`` and ``sum()`` — no external deps.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _materialize_full_entry(entry: dict, index, store, owner: str, name: str) -> None:
    """Inline source/docstring/end_line and update byte_length to reflect full content.

    Mutates entry in place. Called before token-budget packing so the packer sees
    real byte sizes rather than the pre-materialization signature-only skeleton.
    Without this, full + token_budget can overshoot the budget by 5-20x (§1.2).
    """
    sym = index._get_symbol_raw(entry["id"])
    if not sym:
        return
    source = store.get_symbol_content(owner, name, entry["id"], _index=index) or ""
    docstring = sym.get("docstring", "") or ""
    entry["end_line"] = sym.get("end_line", entry["line"])
    entry["docstring"] = docstring
    entry["source"] = source
    entry["byte_length"] = (sym.get("byte_length", 0) or 0) + len(docstring.encode("utf-8")) + len(source.encode("utf-8"))


def search_symbols(
    repo: str,
    query: str,
    kind: Optional[str] = None,
    file_pattern: Optional[str] = None,
    language: Optional[str] = None,
    decorator: Optional[str] = None,
    max_results: int = 10,
    token_budget: Optional[int] = None,
    detail_level: str = "auto",
    debug: bool = False,
    fuzzy: bool = False,
    fuzzy_threshold: float = 0.4,
    max_edit_distance: int = 2,
    sort_by: str = "relevance",
    semantic: bool = False,
    semantic_weight: float = 0.5,
    semantic_only: bool = False,
    fusion: bool = False,
    storage_path: Optional[str] = None,
    fqn: Optional[str] = None,
) -> dict:
    """Search for symbols matching a query.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Search query.
        kind: Optional filter by symbol kind.
        file_pattern: Optional glob pattern to filter files.
        language: Optional filter by language (e.g., "python", "javascript").
        decorator: Optional filter by decorator (substring match, e.g. 'route', 'property').
        max_results: Maximum results to return (ignored when token_budget is set).
        token_budget: Maximum tokens to consume. Results are greedily packed by
            score until the budget is exhausted. Overrides max_results.
        detail_level: Controls result verbosity.
            "auto" (default) picks "compact" for broad discovery (no token_budget,
            no debug, max_results >= 5) and "standard" otherwise. Explicitly-passed
            values are always honored.
            "compact" returns id/name/kind/file/line only (~15 tokens each, ideal
            for discovery).
            "standard" returns signatures and summaries.
            "full" inlines source code, docstring, and end_line.
        debug: When True, include per-field score breakdown in each result.
        fuzzy: Enable fuzzy matching. When True (or when BM25 confidence is low),
            uses trigram overlap + edit distance as fallback. Fuzzy results carry
            match_type="fuzzy", fuzzy_similarity, and edit_distance fields.
        fuzzy_threshold: Minimum Jaccard trigram similarity (0.0–1.0) for fuzzy
            candidates. Default 0.4.
        max_edit_distance: Maximum Levenshtein distance for direct name matching
            (catches typos even when trigrams don't match). Default 2.
        sort_by: Ranking strategy. "relevance" (default) = BM25 + centrality tiebreaker.
            "centrality" = filter by query match, rank by PageRank score.
            "combined" = BM25 + PageRank weighted combination.
        semantic: Enable semantic (embedding-based) search. Requires an embedding
            provider to be configured (JCODEMUNCH_EMBED_MODEL, GOOGLE_API_KEY +
            GOOGLE_EMBED_MODEL, or OPENAI_API_KEY + OPENAI_EMBED_MODEL).
            When False (default) there is zero performance impact and no new imports.
        semantic_weight: Weight for semantic score in hybrid ranking (0.0–1.0).
            BM25 receives ``1 - semantic_weight``. Default 0.5.
            Set to 0.0 for pure BM25 behaviour; set to 1.0 for pure semantic.
        semantic_only: Skip BM25 entirely; rank solely by embedding similarity.
            Implies semantic=True.
        fusion: Enable multi-signal fusion (Weighted Reciprocal Rank) across
            lexical, structural, similarity, and identity channels. Produces
            higher-quality ranking than linear score addition. When True,
            ``sort_by`` is ignored (fusion handles its own ranking).
        storage_path: Custom storage path.

    Returns:
        Dict with search results and _meta envelope.
    """
    if detail_level not in ("auto", "compact", "standard", "full"):
        return {"error": f"Invalid detail_level '{detail_level}'. Must be 'auto', 'compact', 'standard', or 'full'."}

    if sort_by not in ("relevance", "centrality", "combined"):
        return {"error": f"Invalid sort_by '{sort_by}'. Must be 'relevance', 'centrality', or 'combined'."}

    # FQN shortcut: resolve PHP FQN and use class name as query
    if fqn:
        _resolved, _ = resolve_fqn(repo, fqn, storage_path)
        if _resolved:
            query = fqn.rsplit("\\", 1)[-1].split("::")[0]

    _MAX_QUERY_LEN = 500
    if len(query) > _MAX_QUERY_LEN:
        return {"error": f"Query too long ({len(query)} chars, max {_MAX_QUERY_LEN})"}

    start = time.perf_counter()
    max_results = max(1, min(max_results, 100))

    # §1.1: Resolve "auto" to a concrete level BEFORE cache_key build so cache
    # keys reflect what we'll actually materialize. Explicit values pass through.
    if detail_level == "auto":
        if token_budget is None and not debug and max_results >= 5:
            detail_level = "compact"
        else:
            detail_level = "standard"

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    # Load index
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # Feature 5: Search result cache
    # Skip cache for debug/semantic modes (these need fresh data)
    _cacheable = not debug and not semantic and not semantic_only and _get_cache_max() > 0
    _indexed_at = getattr(index, "indexed_at", "")
    _cache_key: Optional[tuple] = None
    if _cacheable:
        # Include indexed_at in key so cache auto-invalidates on reindex
        _cache_key = (
            f"{owner}/{name}",
            _indexed_at,
            query,
            detail_level,
            kind,
            file_pattern,
            language,
            decorator,
            max_results,
            fuzzy,
            fuzzy_threshold,
            max_edit_distance,
            sort_by,
            semantic_weight,
            token_budget,
            fusion,
        )
        _cached = _result_cache_get(_cache_key)
        if _cached is not None:
            # Cache hit — return immediately with fresh timing
            _cached["_meta"]["timing_ms"] = round((time.perf_counter() - start) * 1000, 1)
            _cached["_meta"]["cache_hit"] = True
            return _cached

    # Semantic: validate provider before doing any expensive work
    _semantic_provider: Optional[tuple[str, str]] = None
    if semantic or semantic_only:
        semantic = True  # semantic_only implies semantic
        from .embed_repo import _detect_provider
        _semantic_provider = _detect_provider()
        if _semantic_provider is None:
            return {
                "error": "no_embedding_provider",
                "message": (
                    "No embedding provider is configured. Set one of: "
                    "JCODEMUNCH_EMBED_MODEL (sentence-transformers, free/local), "
                    "GOOGLE_API_KEY + GOOGLE_EMBED_MODEL (Gemini), or "
                    "OPENAI_API_KEY + OPENAI_EMBED_MODEL (OpenAI)."
                ),
            }

    # BM25 corpus stats — cached on CodeIndex, computed once per index load
    query_terms = _tokenize(query) or [query.lower()]
    # Guard: empty string in query_terms causes "" to match every filename
    query_terms = [t for t in query_terms if t]
    cache = index._bm25_cache
    if "idf" not in cache:
        cache["idf"], cache["avgdl"], cache["inverted"] = _compute_bm25(index.symbols)
        cache["centrality"] = _compute_centrality(index.symbols, index.imports, index.alias_map, getattr(index, "psr4_map", None))
    idf = cache["idf"]
    avgdl = cache["avgdl"]
    centrality = cache["centrality"]
    inverted = cache["inverted"]

    # PageRank scores — computed and cached when sort_by requires it
    pagerank: dict = {}
    if sort_by in ("centrality", "combined"):
        if "pagerank" not in cache:
            from .pagerank import compute_pagerank
            pr_scores, _ = compute_pagerank(
                index.imports or {}, index.source_files, index.alias_map, psr4_map=getattr(index, "psr4_map", None)
            )
            cache["pagerank"] = pr_scores
        pagerank = cache["pagerank"]

    has_filters = bool(kind or file_pattern or language or decorator)

    # Bound the heap size in both modes.
    # token_budget mode: estimate ceiling as budget_bytes / min_symbol_size so the
    # heap stays O(N log K) instead of O(N log N) on large indexes.
    # A 20-byte floor is conservative — real symbols are rarely smaller.
    _MIN_BYTES_PER_SYMBOL = 20
    if token_budget is not None:
        budget_bytes = token_budget * BYTES_PER_TOKEN
        effective_limit = max(max_results, budget_bytes // _MIN_BYTES_PER_SYMBOL)
    else:
        budget_bytes = 0
        effective_limit = max_results

    # ── Semantic / hybrid search path ──────────────────────────────────────
    # Diverges here when semantic=True; pure BM25 path continues below.
    if semantic and _semantic_provider is not None:
        return _search_symbols_semantic(
            index=index,
            store=store,
            owner=owner,
            name=name,
            query=query,
            query_terms=query_terms,
            idf=idf,
            avgdl=avgdl,
            centrality=centrality,
            has_filters=has_filters,
            kind=kind,
            file_pattern=file_pattern,
            language=language,
            decorator=decorator,
            max_results=max_results,
            effective_limit=effective_limit,
            token_budget=token_budget,
            budget_bytes=budget_bytes,
            detail_level=detail_level,
            debug=debug,
            semantic_weight=semantic_weight,
            semantic_only=semantic_only,
            provider=_semantic_provider[0],
            model=_semantic_provider[1],
            start=start,
        )

    # ── Fusion search path ──────────────────────────────────────────────
    # Multi-signal ranking via Weighted Reciprocal Rank (WRR).
    if fusion:
        return _search_symbols_fusion(
            index=index,
            store=store,
            owner=owner,
            name=name,
            query=query,
            query_terms=query_terms,
            idf=idf,
            avgdl=avgdl,
            centrality=centrality,
            pagerank=pagerank,
            has_filters=has_filters,
            kind=kind,
            file_pattern=file_pattern,
            language=language,
            decorator=decorator,
            max_results=max_results,
            effective_limit=effective_limit,
            token_budget=token_budget,
            budget_bytes=budget_bytes,
            detail_level=detail_level,
            debug=debug,
            start=start,
            cache_key=_cache_key,
            cacheable=_cacheable,
        )

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
    heap: list[tuple[float, int, dict]] = []  # (score, candidates_scored, entry)
    candidates_scored = 0
    max_bm25_score = 0.0

    for sym in candidates:
        if has_filters:
            if kind and sym.get("kind") != kind:
                continue
            if file_pattern and not fnmatch(sym.get("file", ""), file_pattern):
                continue
            if language and sym.get("language") != language:
                continue
            if decorator and not any(decorator.lower() in d.lower() for d in (sym.get("decorators") or [])):
                continue

        score = _bm25_score(sym, query_terms, idf, avgdl, centrality, raw_query=query)
        if score <= 0:
            continue

        if score > max_bm25_score:
            max_bm25_score = score
        candidates_scored += 1

        # Compute sort key based on sort_by strategy
        if sort_by == "centrality":
            heap_score = pagerank.get(sym.get("file", ""), 0.0)
        elif sort_by == "combined":
            heap_score = score + pagerank.get(sym.get("file", ""), 0.0) * _PR_COMBINED_WEIGHT
        else:
            heap_score = score

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
        decs = sym.get("decorators") or []
        if decs:
            entry["decorators"] = decs
        if debug:
            entry["score"] = round(score, 3)
            entry["score_breakdown"] = _bm25_breakdown(sym, query_terms, idf, avgdl, raw_query=query)

        # Bounded heap: O(N log K) instead of O(N log N)
        if len(heap) < effective_limit:
            heapq.heappush(heap, (heap_score, candidates_scored, entry))
        elif heap_score > heap[0][0]:
            heapq.heapreplace(heap, (heap_score, candidates_scored, entry))

    # Extract results sorted by score descending
    scored_results = [entry for _, _, entry in sorted(heap, key=lambda x: x[0], reverse=True)]
    heap_count = len(scored_results)  # save before budget packing

    # §1.2: Materialize full-detail payload BEFORE packing so byte_length reflects
    # what will actually be returned. Prior to this fix, the packer saw the pre-full
    # skeleton size and the token_budget could overshoot by 5-20x.
    if detail_level == "full":
        for entry in scored_results:
            _materialize_full_entry(entry, index, store, owner, name)

    budget_truncated = False
    if token_budget is not None:
        packed, used_bytes = [], 0
        for entry in scored_results:
            b = entry["byte_length"]
            if used_bytes + b <= budget_bytes:
                packed.append(entry)
                used_bytes += b
        budget_truncated = len(packed) < heap_count
        scored_results = packed

    # Fuzzy pass: runs when explicitly requested OR when BM25 found nothing useful
    run_fuzzy = fuzzy or (max_bm25_score < _FUZZY_NEAR_MISS_THRESHOLD)
    if run_fuzzy:
        for entry in scored_results:
            entry["match_type"] = "exact"

        query_lower = query.lower()
        query_tris = _trigrams(query_lower)
        existing_ids = {e["id"] for e in scored_results}
        fuzzy_hits: list[tuple[dict, float, int]] = []

        # Cap fuzzy candidates to avoid O(N) scan on very large repos.
        # Collect up to 5× max_results candidates, then stop scanning.
        fuzzy_cap = max_results * 5
        for sym in index.symbols:
            if sym["id"] in existing_ids:
                continue
            if has_filters:
                if kind and sym.get("kind") != kind:
                    continue
                if file_pattern and not fnmatch(sym.get("file", ""), file_pattern):
                    continue
                if language and sym.get("language") != language:
                    continue
                if decorator and not any(decorator.lower() in d.lower() for d in (sym.get("decorators") or [])):
                    continue
            name_lower = sym.get("name", "").lower()
            name_tris = _trigrams(name_lower)
            union_size = len(query_tris | name_tris)
            jac = len(query_tris & name_tris) / union_size if union_size else 0.0
            ed = _edit_distance(query_lower, name_lower)
            if jac < fuzzy_threshold and ed > max_edit_distance:
                continue
            fuzzy_hits.append((sym, jac, ed))
            if len(fuzzy_hits) >= fuzzy_cap:
                break

        # Rank: lowest edit distance first, then highest Jaccard as tiebreaker
        fuzzy_hits.sort(key=lambda x: (x[2], -x[1]))

        for sym, jac, ed in fuzzy_hits[:max_results]:
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
            decs = sym.get("decorators") or []
            if decs:
                entry["decorators"] = decs
            entry["match_type"] = "fuzzy"
            entry["fuzzy_similarity"] = round(jac, 3)
            entry["edit_distance"] = ed
            if debug:
                entry["score"] = 0.0
            if detail_level == "full":
                _materialize_full_entry(entry, index, store, owner, name)
            scored_results.append(entry)

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

    # Feature 1: Negative evidence — tell the AI when nothing was found
    negative_evidence: Optional[dict] = None
    _ne_threshold = _NEGATIVE_EVIDENCE_THRESHOLD
    try:
        from .. import config as _cfg
        _ne_threshold = _cfg.get("negative_evidence_threshold", _NEGATIVE_EVIDENCE_THRESHOLD)
    except Exception:
        pass
    if not scored_results or max_bm25_score < _ne_threshold:
        # Find files whose names partially match query terms
        query_lower = query.lower()
        related_existing: list[str] = []
        for f in index.source_files:
            fname = f.lower().split("/")[-1].split("\\")[-1]
            for term in query_terms:
                if term in fname:
                    related_existing.append(f)
                    break
        related_existing = related_existing[:5]  # cap at 5

        verdict = "no_implementation_found" if not scored_results else "low_confidence_matches"
        negative_evidence = {
            "verdict": verdict,
            "scanned_symbols": candidates_scored if candidates_scored > 0 else len(index.symbols),
            "scanned_files": len(seen_files) if seen_files else len(index.source_files),
            "best_match_score": round(max_bm25_score, 3) if max_bm25_score > 0 else 0.0,
        }
        if related_existing:
            negative_evidence["related_existing"] = related_existing

    meta = {
        "timing_ms": round(elapsed, 1),
        "total_symbols": len(index.symbols),
        "truncated": candidates_scored > heap_count or budget_truncated,
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
    if scored_results:
        meta["hint"] = "Use get_context_bundle(symbol_id) to retrieve source + imports in one call"

    result = {
        "result_count": len(scored_results),
        "results": scored_results,
        "_meta": meta,
    }
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(scored_results)
    meta["freshness"] = _probe.summary(scored_results)
    _attach_confidence(result, scored_results, is_stale=_probe.repo_is_stale)

    # Feature 1: Add negative_evidence if present
    if negative_evidence is not None:
        result["negative_evidence"] = negative_evidence
        query_display = query[:80]
        if negative_evidence["verdict"] == "no_implementation_found":
            result["\u26a0 warning"] = (
                f"No implementation found for '{query_display}'. "
                f"Do not claim this feature exists."
            )
        else:
            result["\u26a0 warning"] = (
                f"Low-confidence matches for '{query_display}' "
                f"(best score: {negative_evidence['best_match_score']}). "
                f"Verify before claiming this feature exists."
            )

    # Feature 5: Cache the result if cacheable
    if _cacheable and _cache_key is not None:
        _result_cache_put(_cache_key, result)

    return result


def _search_symbols_semantic(
    *,
    index,
    store,
    owner: str,
    name: str,
    query: str,
    query_terms: list[str],
    idf: dict,
    avgdl: float,
    centrality: dict,
    has_filters: bool,
    kind: Optional[str],
    file_pattern: Optional[str],
    language: Optional[str],
    decorator: Optional[str],
    max_results: int,
    effective_limit: int,
    token_budget: Optional[int],
    budget_bytes: int,
    detail_level: str,
    debug: bool,
    semantic_weight: float,
    semantic_only: bool,
    provider: str,
    model: str,
    start: float,
) -> dict:
    """Semantic / hybrid scoring path for search_symbols.

    Two-pass algorithm:
    1. Compute BM25 scores for all filtered symbols (for normalisation).
    2. Compute cosine similarity against the query embedding for all symbols.
    3. Combine: ``combined = (1-w)*bm25_norm + w*cosine``.

    When ``semantic_only=True`` the BM25 component is skipped entirely (w=1).
    When ``semantic_weight=0.0`` the result is identical to pure BM25.
    """
    from .embed_repo import embed_texts, _sym_text, EMBED_BATCH_SIZE, _gemini_task_aware
    from ..storage.embedding_store import EmbeddingStore
    import logging as _logging

    _logger = _logging.getLogger(__name__)

    # Config-driven negative evidence threshold
    _ne_threshold = _NEGATIVE_EVIDENCE_THRESHOLD
    try:
        from .. import config as _cfg
        _ne_threshold = _cfg.get("negative_evidence_threshold", _NEGATIVE_EVIDENCE_THRESHOLD)
    except Exception:
        pass

    # Determine task types (Gemini only; no-op for other providers).
    query_task_type: Optional[str] = None
    doc_task_type: Optional[str] = None
    if provider == "gemini" and _gemini_task_aware():
        query_task_type = "CODE_RETRIEVAL_QUERY"
        doc_task_type = "RETRIEVAL_DOCUMENT"

    # ── Get query embedding ────────────────────────────────────────────────
    try:
        query_vec = embed_texts([query], provider, model, task_type=query_task_type)[0]
    except Exception as exc:
        return {"error": f"Failed to embed query: {exc}"}

    # ── Load / lazily compute symbol embeddings ────────────────────────────
    db_path = store._sqlite._db_path(owner, name)
    emb_store = EmbeddingStore(db_path)
    all_emb: dict[str, list[float]] = emb_store.get_all()

    missing = [s for s in index.symbols if s["id"] not in all_emb]
    if missing:
        new_emb: dict[str, list[float]] = {}
        for bi in range(0, len(missing), EMBED_BATCH_SIZE):
            batch = missing[bi : bi + EMBED_BATCH_SIZE]
            try:
                vecs = embed_texts(
                    [_sym_text(s) for s in batch], provider, model,
                    task_type=doc_task_type,
                )
                for j, sym in enumerate(batch):
                    new_emb[sym["id"]] = vecs[j]
            except Exception as exc:
                _logger.warning("semantic: embedding batch %d failed: %s", bi // EMBED_BATCH_SIZE, exc)
        if new_emb:
            if emb_store.get_dimension() is None:
                dim = len(next(iter(new_emb.values())))
                emb_store.set_dimension(dim, model)
                emb_store.set_task_type(doc_task_type or "")
            emb_store.set_many(new_emb)
            all_emb.update(new_emb)

    # ── Two-pass scoring ───────────────────────────────────────────────────
    # Pass 1: collect BM25 + cosine for every filtered symbol
    raw: list[tuple[dict, float, float]] = []  # (sym, bm25, cosine)
    max_bm25 = 0.0
    max_cos = 0.0

    for sym in index.symbols:
        if has_filters:
            if kind and sym.get("kind") != kind:
                continue
            if file_pattern and not fnmatch(sym.get("file", ""), file_pattern):
                continue
            if language and sym.get("language") != language:
                continue
            if decorator and not any(decorator.lower() in d.lower() for d in (sym.get("decorators") or [])):
                continue

        bm25 = 0.0 if semantic_only else _bm25_score(sym, query_terms, idf, avgdl, centrality, raw_query=query)
        if bm25 > max_bm25:
            max_bm25 = bm25

        sym_vec = all_emb.get(sym["id"])
        cos = _cosine_similarity(query_vec, sym_vec) if sym_vec else 0.0
        if cos > max_cos:
            max_cos = cos

        raw.append((sym, bm25, cos))

    # Pass 2: normalise BM25 and compute combined score
    scored: list[tuple[float, dict]] = []
    for sym, bm25, cos in raw:
        bm25_norm = (bm25 / max_bm25) if max_bm25 > 0.0 else 0.0
        score = cos if semantic_only else (1.0 - semantic_weight) * bm25_norm + semantic_weight * cos
        if score <= 0.0:
            continue
        scored.append((score, sym))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:effective_limit]

    # ── Build result entries ───────────────────────────────────────────────
    scored_results: list[dict] = []
    for score, sym in top:
        if detail_level == "compact":
            entry: dict = {
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
        decs = sym.get("decorators") or []
        if decs:
            entry["decorators"] = decs
        if debug:
            entry["score"] = round(score, 4)
        scored_results.append(entry)

    # ── Full detail: materialize BEFORE packing (§1.2) ─────────────────────
    # Semantic path had the same packer/materialization ordering bug.
    if detail_level == "full":
        for entry in scored_results:
            _materialize_full_entry(entry, index, store, owner, name)

    # ── Token budget packing ───────────────────────────────────────────────
    if token_budget is not None:
        packed: list[dict] = []
        used = 0
        for entry in scored_results:
            b = entry["byte_length"]
            if used + b <= budget_bytes:
                packed.append(entry)
                used += b
        scored_results = packed

    # ── Meta ───────────────────────────────────────────────────────────────
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

    meta: dict = {
        "timing_ms": round(elapsed, 1),
        "total_symbols": len(index.symbols),
        "truncated": len(scored) > len(scored_results),
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        "search_mode": "semantic_only" if semantic_only else "hybrid",
        **cost_avoided(tokens_saved, total_saved),
    }
    if token_budget is not None:
        used_bytes = sum(e["byte_length"] for e in scored_results)
        meta["token_budget"] = token_budget
        meta["tokens_used"] = used_bytes // BYTES_PER_TOKEN
        meta["tokens_remaining"] = max(0, token_budget - used_bytes // BYTES_PER_TOKEN)
    if scored_results:
        meta["hint"] = "Use get_context_bundle(symbol_id) to retrieve source + imports in one call"

    # Feature 1: Negative evidence for semantic search
    result = {
        "result_count": len(scored_results),
        "results": scored_results,
        "_meta": meta,
    }
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(scored_results)
    meta["freshness"] = _probe.summary(scored_results)
    _attach_confidence(result, scored_results, is_stale=_probe.repo_is_stale)
    best_score = max_cos if semantic_only else max_bm25
    if not scored_results or best_score < _ne_threshold:
        # Find files whose names partially match query terms
        query_lower = query.lower()
        related_existing: list[str] = []
        for f in index.source_files:
            fname = f.lower().split("/")[-1].split("\\")[-1]
            for term in query_terms:
                if term in fname:
                    related_existing.append(f)
                    break
        related_existing = related_existing[:5]  # cap at 5

        verdict = "no_implementation_found" if not scored_results else "low_confidence_matches"
        result["negative_evidence"] = {
            "verdict": verdict,
            "scanned_symbols": len(raw),
            "scanned_files": len(seen_files) if seen_files else len(index.source_files),
            "best_match_score": round(best_score, 3) if best_score > 0 else 0.0,
            **({"related_existing": related_existing} if related_existing else {}),
        }
        # Add warning string alongside negative_evidence
        query_display = query[:80]
        if verdict == "no_implementation_found":
            result["\u26a0 warning"] = (
                f"No implementation found for '{query_display}'. "
                f"Do not claim this feature exists."
            )
        else:
            _best = result["negative_evidence"]["best_match_score"]
            result["\u26a0 warning"] = (
                f"Low-confidence matches for '{query_display}' "
                f"(best score: {_best}). "
                f"Verify before claiming this feature exists."
            )

    return result


def _search_symbols_fusion(
    *,
    index,
    store,
    owner: str,
    name: str,
    query: str,
    query_terms: list[str],
    idf: dict,
    avgdl: float,
    centrality: dict,
    pagerank: dict,
    has_filters: bool,
    kind,
    file_pattern,
    language,
    decorator,
    max_results: int,
    effective_limit: int,
    token_budget,
    budget_bytes: int,
    detail_level: str,
    debug: bool,
    start: float,
    cache_key,
    cacheable: bool,
) -> dict:
    """Fusion search path: multi-signal WRR ranking."""
    from ..retrieval.signal_fusion import (
        fuse,
        build_lexical_channel,
        build_structural_channel,
        build_identity_channel,
        load_fusion_weights,
    )

    # Apply filters to get candidate symbols
    if has_filters:
        from fnmatch import fnmatch as _fnmatch
        candidates = [
            sym for sym in index.symbols
            if (not kind or sym.get("kind") == kind)
            and (not file_pattern or _fnmatch(sym.get("file", ""), file_pattern))
            and (not language or sym.get("language") == language)
            and (not decorator or any(decorator.lower() in d.lower() for d in (sym.get("decorators") or [])))
        ]
    else:
        candidates = index.symbols

    if not candidates:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "result_count": 0,
            "results": [],
            "_meta": {"timing_ms": round(elapsed, 1), "total_symbols": len(index.symbols)},
        }

    # Load config weights
    weights, smoothing = load_fusion_weights()

    # Build channels
    channels = []

    # Lexical (BM25 without identity — identity is a separate channel)
    lex_ch = build_lexical_channel(candidates, query_terms, idf, avgdl, centrality)
    channels.append(lex_ch)

    # Identity
    id_ch = build_identity_channel(candidates, query)
    channels.append(id_ch)

    # Structural (PageRank) — only if we have PageRank data
    if not pagerank:
        cache = index._bm25_cache
        if "pagerank" not in cache:
            from .pagerank import compute_pagerank
            pr_scores, _ = compute_pagerank(
                index.imports or {}, index.source_files, index.alias_map,
                psr4_map=getattr(index, "psr4_map", None),
            )
            cache["pagerank"] = pr_scores
        pagerank = cache["pagerank"]

    if pagerank:
        candidate_ids = set(lex_ch.ranked_ids) | set(id_ch.ranked_ids)
        struct_ch = build_structural_channel(candidates, pagerank, candidate_ids)
        channels.append(struct_ch)

    # Similarity channel: only if embeddings exist for this repo
    try:
        from ..storage.embedding_store import EmbeddingStore
        emb_store = EmbeddingStore(base_path=store._base_path if hasattr(store, "_base_path") else None)
        all_embeddings = emb_store.get_all(owner, name)
        if all_embeddings:
            from .embed_repo import _detect_provider, _embed_texts
            provider = _detect_provider()
            if provider:
                q_emb = _embed_texts([query], provider[0], provider[1])
                if q_emb and q_emb[0]:
                    from ..retrieval.signal_fusion import build_similarity_channel
                    sim_ch = build_similarity_channel(q_emb[0], all_embeddings)
                    channels.append(sim_ch)
    except Exception:
        pass  # Similarity is optional

    # Fuse
    fused = fuse(channels, smoothing=smoothing, weights=weights)

    # Build result list
    sym_by_id = {sym["id"]: sym for sym in candidates}
    scored_results = []

    for fr in fused[:effective_limit]:
        sym = sym_by_id.get(fr.symbol_id)
        if not sym:
            continue

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
        decs = sym.get("decorators") or []
        if decs:
            entry["decorators"] = decs
        if debug:
            entry["fusion_score"] = round(fr.score, 6)
            entry["channel_contributions"] = {
                k: round(v, 6) for k, v in fr.channel_contributions.items()
            }
            entry["channel_ranks"] = fr.channel_ranks
        scored_results.append(entry)

    # Full detail: materialize BEFORE packing so byte_length reflects payload (§1.2).
    if detail_level == "full":
        for entry in scored_results:
            _materialize_full_entry(entry, index, store, owner, name)

    # Budget packing
    budget_truncated = False
    if token_budget is not None:
        packed, used_bytes = [], 0
        for entry in scored_results:
            b = entry["byte_length"]
            if used_bytes + b <= budget_bytes:
                packed.append(entry)
                used_bytes += b
        budget_truncated = len(packed) < len(scored_results)
        scored_results = packed

    # Token savings
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
        "truncated": len(fused) > len(scored_results) or budget_truncated,
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        **cost_avoided(tokens_saved, total_saved),
        "fusion": True,
        "channels": [ch.name for ch in channels],
    }
    if token_budget is not None:
        used = sum(e["byte_length"] for e in scored_results)
        meta["token_budget"] = token_budget
        meta["tokens_used"] = used // BYTES_PER_TOKEN
        meta["tokens_remaining"] = max(0, token_budget - used // BYTES_PER_TOKEN)
    if debug:
        meta["fusion_weights"] = weights
        meta["fusion_smoothing"] = smoothing
    if scored_results:
        meta["hint"] = "Use get_context_bundle(symbol_id) to retrieve source + imports in one call"

    result = {
        "result_count": len(scored_results),
        "results": scored_results,
        "_meta": meta,
    }
    from ..retrieval.confidence import attach_confidence as _attach_confidence
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(scored_results)
    meta["freshness"] = _probe.summary(scored_results)
    _attach_confidence(result, scored_results, is_stale=_probe.repo_is_stale)

    if cacheable and cache_key is not None:
        _result_cache_put(cache_key, result)

    return result
