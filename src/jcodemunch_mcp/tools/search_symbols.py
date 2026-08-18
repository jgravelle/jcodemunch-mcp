"""Search symbols across repository."""

import heapq
import json
import logging
import math
import re
import time
from fnmatch import fnmatch
from typing import Optional

logger = logging.getLogger(__name__)

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ..parser.imports import resolve_specifier
from ._utils import (
    resolve_repo,
    resolve_fqn,
    index_status_to_tool_error,
    ledger_base_path as _ledger_base_path,
)

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
# Unicode word runs (was [a-zA-Z0-9]{2,}, which silently dropped ALL
# non-ASCII — CJK names/docstrings produced zero BM25 tokens; jdoc #91 class).
_TOKEN_RE = re.compile(r"[^\W_]+")
# CJK scripts carry no whitespace word boundaries; runs in these ranges are
# expanded to overlapping character bigrams (same expansion at index and
# query time, so bigram overlap is the match signal).
_CJK_RE = re.compile(
    "[ᄀ-ᇿ"  # Hangul Jamo
    "぀-ヿ"  # Hiragana + Katakana
    "㄰-㆏"  # Hangul Compatibility Jamo
    "ㇰ-ㇿ"  # Katakana Phonetic Extensions
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "一-鿿"  # CJK Unified Ideographs
    "가-힯"  # Hangul Syllables
    "豈-﫿]+"  # CJK Compatibility Ideographs
)


def _cjk_bigrams(run: str) -> list[str]:
    """Overlapping character bigrams for a CJK run; a lone char passes through."""
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]

# Search result cache (Feature 5 — session-aware routing)
import threading
from collections import OrderedDict

_RESULT_CACHE_MAX = 128
_result_cache: OrderedDict = OrderedDict()
_result_cache_lock = threading.Lock()


def _result_cache_state(key: tuple) -> Optional[dict]:
    """Subject state recorded when this entry was cached (#377 item 3)."""
    with _result_cache_lock:
        state = (_result_cache.get(key) or {}).get("_subject_state")
        return dict(state) if isinstance(state, dict) else None


def _result_cache_get(key: tuple) -> Optional[dict]:
    """Return cached result for key, or None on miss. Returns a copy.

    The result is copied down to the verdict, not just to ``_meta``: the
    dispatcher writes ``evidence_ref`` into ``_meta.verdict`` after the tool
    returns, and with a shared nested dict that write landed in the cached
    entry and was replayed to every later hit (#377 item 3).
    """
    with _result_cache_lock:
        if key in _result_cache:
            _result_cache.move_to_end(key)  # LRU refresh
            cached = _result_cache[key]
            # Track hit count for session state persistence priority
            cached["_hit_count"] = cached.get("_hit_count", 0) + 1
            # Shallow copy top-level + _meta to prevent caller mutations
            result = dict(cached)
            result.pop("_hit_count", None)  # don't leak internal field
            result.pop("_subject_state", None)
            if "_meta" in result:
                result["_meta"] = dict(result["_meta"])
                _v = result["_meta"].get("verdict")
                if isinstance(_v, dict):
                    result["_meta"]["verdict"] = dict(_v)
            # The ROWS need the same treatment, and for the same reason
            # (v1.108.225, @rknighton #404). Annotators downstream of this
            # return — `_freshness`, `_runtime_confidence` — write per-row IN
            # PLACE. With a shared list of shared dicts those writes land in the
            # stored entry and replay to every later hit: the `evidence_ref`
            # leak of #377 item 3, one level down. Re-annotating a cache hit
            # without this copy would reintroduce it.
            _rows = result.get("results")
            if isinstance(_rows, list):
                result["results"] = [
                    dict(_r) if isinstance(_r, dict) else _r for _r in _rows
                ]
            return result
    return None


def _attach_index_truncation(meta: Optional[dict], index) -> None:
    """Add ``_meta.index_truncated`` when the loaded index was cap-truncated (#366).

    No-op on a complete index or a pre-v1.108.126 index (unknown status), so a
    healthy result is unchanged.
    """
    if not isinstance(meta, dict):
        return
    from ..retrieval.verdict import index_truncation_meta as _itm
    block = _itm(getattr(index, "file_cap_status", None))
    if block:
        meta["index_truncated"] = block


def _get_cache_max() -> int:
    try:
        from .. import config as _cfg
        return _cfg.get("search_result_cache_max", _RESULT_CACHE_MAX)
    except Exception:
        return _RESULT_CACHE_MAX


def _result_cache_put(key: tuple, value: dict, state: Optional[dict] = None) -> None:
    """Store result in LRU cache, evicting oldest if full.

    ``state`` is the subject state the scan measured (#377 item 3). It rides
    with the entry so a later hit can prove the answer still describes the same
    subject rather than assuming a reindex is the only thing that can change it.
    """
    if state is not None:
        # Copy down to the verdict so the dispatcher's post-return writes (the
        # absence evidence_ref) cannot land in the stored entry.
        value = dict(value)
        value["_subject_state"] = state
        if isinstance(value.get("_meta"), dict):
            value["_meta"] = dict(value["_meta"])
            if isinstance(value["_meta"].get("verdict"), dict):
                value["_meta"]["verdict"] = dict(value["_meta"]["verdict"])
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
    # Pad CJK runs with spaces so mixed-script tokens split cleanly.
    text = _CJK_RE.sub(lambda m: " " + m.group(0) + " ", text)
    raw_tokens = [t.lower() for t in _TOKEN_RE.findall(text)]

    result = []
    seen: set[str] = set()
    for tok in raw_tokens:
        if _CJK_RE.fullmatch(tok):
            # Bigram expansion; stemming/abbreviations are English-only.
            for bg in _cjk_bigrams(tok):
                result.append(bg)
                seen.add(bg)
            continue
        if len(tok) < 2:
            continue
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


# The four keys the lexical corpus cache publishes together. ``idf`` is the
# readiness sentinel every historical call site checked, so it stays the last
# one written; the fast path below checks all four anyway, so a future edit that
# reorders the writes degrades to an extra lock acquisition instead of a
# KeyError (#490).
_BM25_CORPUS_KEYS = ("avgdl", "inverted", "centrality", "idf")

# Used only if an index arrives without its own lock. A process-wide lock is
# slower than a per-index one and correct; the `threading.Lock()` this replaces
# was a NEW lock per caller, i.e. no mutual exclusion at all, which is the
# failure this whole helper exists to remove.
_BM25_FALLBACK_LOCK = threading.Lock()


def ensure_bm25_cache(index) -> dict:
    """Populate and return ``index._bm25_cache``'s lexical corpus stats.

    Builds ``idf`` / ``avgdl`` / ``inverted`` / ``centrality`` exactly once per
    loaded index, under the index's own lock, and returns the cache dict.

    LIMITATION: this covers only the lexical corpus keys. ``pagerank`` and
    ``name_map`` are built by their own single-key blocks elsewhere; each writes
    the one key it also checks, so those are atomic by construction and are not
    routed through here.

    ⚠⚠ The build must never publish a key a reader treats as "cache ready"
    before the keys that reader will go on to read. Three call sites wrote
    ``cache["idf"], cache["avgdl"], cache["inverted"] = _compute_bm25(...)`` and
    then ``cache["centrality"] = ...`` as a separate statement -- four
    ``__setitem__`` calls behind a check-then-build guarded on ``idf`` alone. A
    second caller arriving in that window passed the readiness check and raised
    ``KeyError: 'centrality'`` (#490). The window is not narrow: it is the whole
    runtime of ``_compute_centrality`` over the corpus.
    """
    cache = index._bm25_cache
    if all(k in cache for k in _BM25_CORPUS_KEYS):
        return cache
    with getattr(index, "_bm25_lock", None) or _BM25_FALLBACK_LOCK:
        if all(k in cache for k in _BM25_CORPUS_KEYS):
            return cache
        idf, avgdl, inverted = _compute_bm25(index.symbols)
        centrality = _compute_centrality(
            index.symbols, index.imports, index.alias_map,
            getattr(index, "psr4_map", None),
        )
        cache["avgdl"] = avgdl
        cache["inverted"] = inverted
        cache["centrality"] = centrality
        # Sentinel last: see the module note above.
        cache["idf"] = idf
    return cache


def _identity_score(sym: dict, query_joined: str, raw_query: str = "") -> float:
    """Identity channel: exact, normalised, or prefix match on symbol name/ID.

    Returns a high score for exact matches and a decreasing score for weaker
    identity matches by specificity.  Replaces the old ``50.0`` exact-name hack.

    Scoring:
      - Exact name match          → 50.0
      - Exact ID match            → 50.0
      - Normalised name/ID match  → 40.0
      - Name starts with query    → 30.0
      - ID contains query segment → 20.0
      - No match                  →  0.0

    ⚠ **The 40.0 tier is the whole point of #458 and it is easy to delete by
    "simplification".** ``_tokenize`` folds case *and* strips leading
    underscores and punctuation, so a pytest fixture named ``state`` and the
    class literally named ``_State`` both reach the tokenized comparison for
    the query ``_State``. Grading them alike put them at 50.0 apiece, and the
    tie fell through to BM25, where the shorter name with a docstring won —
    a test fixture outranking the source symbol it tests, by 0.355 points out
    of ~58. A literal match must outrank a normalised one, and ``identity_type``
    must not report ``exact`` for a grade it did not measure (#440's shape).

    ⚠ **Case folding alone still counts as exact, deliberately.** ``raw_lower``
    is already case-folded and has graded exact since the channel arrived, so
    making case load-bearing would change the answer for every caller who types
    ``getuser`` for ``getUser`` — a behaviour change with no defect behind it.
    What drops to 40.0 is a match that needed *more* than case: an underscore,
    a separator, anything ``_tokenize`` removed.
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
        # With no raw spelling there is nothing to be literal about, so the
        # tokenized match is the best evidence available and stays exact.
        return 50.0 if not raw_lower else 40.0

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


def _ledger_identity_rows(
    scores: list[float],
    entries: list[dict],
    query_terms: list[str],
    raw_query: str,
) -> list[dict]:
    """Ledger input pairing each ranking score with the identity channel's verdict.

    v1.108.272 (#440). ``extract_ledger_features`` reads ``identity`` off the rows it
    is handed, and the non-fusion exits handed it score-only rows — so ``identity_hit``
    was recorded 0 on every ``search_symbols`` row whatever the identity channel found.

    Identity is recomputed here rather than threaded out of scoring. It is a pure
    function of the symbol's name/id and the query, the lexical path folds it into the
    BM25 total inside ``_bm25_score`` where it is no longer separable, and only the top
    three rows are ever read — so this is three string comparisons, not a second pass.

    ⚠ Recomputing also makes ``semantic_only`` honest. That mode skips the identity
    channel entirely (``idn = 0.0``), so reusing its value would keep recording a
    default dressed as a measurement — the very thing being fixed.

    ⚠ For the LEDGER only. ``compute_confidence`` sniffs the same key when no
    ``has_identity_match`` is passed and scores it 1.0 known-true / 0.7 unknown / 0.6
    known-false, so feeding these rows to ``attach_confidence`` would move the
    confidence of every non-fusion search. That is its own change with its own
    justification, not a side effect of recording a column.
    """
    query_joined = " ".join(query_terms)
    rows: list[dict] = []
    for i, score in enumerate(scores):
        row: dict = {"score": score}
        # Only the top three are read; below that the lookup is pure cost.
        if i < 3 and i < len(entries):
            row["identity"] = _identity_score(entries[i], query_joined, raw_query)
        rows.append(row)
    return rows


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
    elif identity >= 40.0:
        # #458: the query matched only after tokenization folded case,
        # underscores and punctuation away. Still a match, not an exact one.
        out["identity_type"] = "normalized"
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


def _packing_cost_bytes(entry: dict, detail_level: str) -> int:
    """Bytes an entry charges against token_budget (jcm#328).

    full: the materialized byte_length set by _materialize_full_entry before
    packing (§1.2) — source + docstring dominate the payload there.
    compact/standard: the encoded row itself. In these shapes the symbol's
    source-body byte_length is metadata, not payload; charging it admitted
    "as many rows as fit budget_bytes of source code" (84 rows observed for
    max_results=18) and made tokens_used describe code nobody received.
    """
    if detail_level == "full":
        return entry.get("byte_length", 0)
    return len(json.dumps(entry, default=str))


def _row_summary(sym: dict) -> str:
    """Summary for a result row; empty when it merely echoes the signature.

    Indexes built without an AI summarizer persist signature_fallback output
    (the signature truncated to 120 chars) as the summary, so emitting both
    columns duplicated the full signature in every such row (jcm#328).
    Class/constant/type fallbacks ("Class Foo") carry real signal and pass.
    """
    summary = sym.get("summary", "") or ""
    sig = sym.get("signature", "") or ""
    if summary and sig and (summary == sig or summary == sig[:120]):
        return ""
    return summary


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
            score until the budget is exhausted. Overrides max_results. The
            budget charges each result's actual payload contribution (jcm#328):
            encoded row size in compact/standard, materialized source bytes in
            full. Compact rows are cheap (~15 tokens), so a budget can admit
            many rows; pass max_results without token_budget when row count is
            the constraint that matters.
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
            provider; ``embeddings.advice.PROVIDER_HINT`` is the single source for
            which ones and which wins (#489 — this docstring used to enumerate
            them and had gone stale).
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

    # v1.79.0 — apply learned per-repo semantic_weight override when the
    # caller used the default. Treats 0.5 (the function default) as
    # "unspecified"; explicit non-default values always win.
    if (semantic or fusion) and semantic_weight == 0.5:
        from ..retrieval.tuning import get_semantic_weight as _get_tuned_sw
        semantic_weight = _get_tuned_sw(
            f"{owner}/{name}", explicit=None, base_path=storage_path
        )

    # Load index
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

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
            _cached_state = _result_cache_state(_cache_key)
            # #377 item 3: a cached NEGATIVE must prove it still describes the
            # current subject before it stays citable. A cached positive may
            # keep serving with disclosure, so only an absence-shaped verdict
            # pays for the working-tree probe.
            try:
                from ..retrieval import subject_state as _subject
                _cv = (_cached.get("_meta") or {}).get("verdict")
                _is_negative = isinstance(_cv, dict) and _cv.get("state") == "absent"
                _now_state = _subject.capture(index, include_tree=_is_negative)
                _why = _subject.changed(_cached_state, _now_state)
                if _why:
                    _subject.revalidate_verdict(_cv, _why)
                    # The verdict now discloses that the subject moved — but the
                    # rows still carry the `_freshness` computed when this entry
                    # was FILLED, and `_meta.freshness` still summarises that
                    # pass. Left alone, one payload asserts both
                    # `revalidated.stale_cache: true` and `_freshness: "fresh"`,
                    # and the field an agent reads to decide whether to trust the
                    # content is the one that is wrong (@rknighton #404).
                    #
                    # ⚠ This is NOT a reversal of the v1.108.178 cached-positive
                    # policy: the results really were in the index at that
                    # generation and they keep serving. The defect is that the
                    # disclosure reached `verdict` and stopped. Re-annotating
                    # costs the same few stats that policy already accepts and
                    # does not re-run the search.
                    #
                    # Safe to annotate in place: `_result_cache_get` copies the
                    # rows, so this cannot write through to the stored entry.
                    from ..retrieval.freshness import FreshnessProbe as _RevalFP
                    _reval_rows = _cached.get("results") or []
                    _reval_probe = _RevalFP(
                        source_root=getattr(index, "source_root", "") or None,
                        indexed_at=getattr(index, "indexed_at", ""),
                        index_sha=getattr(index, "git_head", None),
                        file_mtimes=getattr(index, "file_mtimes", None),
                    )
                    _reval_probe.annotate(_reval_rows)
                    _cached.setdefault("_meta", {})["freshness"] = (
                        _reval_probe.summary(_reval_rows)
                    )
            except Exception:
                logger.debug("Cached-result revalidation failed", exc_info=True)
            # Cache hit — return immediately with fresh timing.
            # Synthesize _meta if the cached result lacks it (#331): a cached
            # entry without _meta must not raise KeyError here, because the
            # dispatcher renders any KeyError as a bogus "missing argument".
            _hit_meta = _cached.setdefault("_meta", {})
            # A repeat query avoids the identical file read the first one did,
            # so its savings are just as real. Record the cached per-call
            # figure into the lifetime meter here — without this, every cache
            # hit silently undercounts the persistent meter + community counter
            # (the cached _meta is a fresh copy per get(), so the stored figure
            # stays pristine and each hit re-records the same original value).
            _hit_tokens = _hit_meta.get("tokens_saved", 0)
            if _hit_tokens:
                _hit_meta["total_tokens_saved"] = record_savings(
                    _hit_tokens, tool_name="search_symbols"
                )
            _hit_meta["timing_ms"] = round((time.perf_counter() - start) * 1000, 1)
            _hit_meta["cache_hit"] = True
            return _cached

    # #377 item 6: identity BEFORE the scan, compared after it. A search can
    # start against one state and finish after a concurrent edit, a watcher
    # reindex or a published generation, and neither reading then describes
    # what was searched. Cheap here (one stat plus a cached HEAD); the fresh
    # HEAD read is paid only by a zero-result scan, in `moved_during_scan`.
    from ..retrieval import subject_state as _subject_pre
    _state_before = _subject_pre.capture(index)

    # Semantic: validate provider before doing any expensive work
    _semantic_provider: Optional[tuple[str, str]] = None
    if semantic or semantic_only:
        semantic = True  # semantic_only implies semantic
        from .embed_repo import _detect_provider
        _semantic_provider = _detect_provider()
        if _semantic_provider is None:
            from ..embeddings.advice import (  # noqa: PLC0415
                NO_PROVIDER_MESSAGE as _NO_PROVIDER_MESSAGE,
            )
            return {
                "error": "no_embedding_provider",
                "message": _NO_PROVIDER_MESSAGE,
            }

    # BM25 corpus stats — cached on CodeIndex, computed once per index load
    query_terms = _tokenize(query) or [query.lower()]
    # Guard: empty string in query_terms causes "" to match every filename
    query_terms = [t for t in query_terms if t]
    # Single-flight: concurrent cold searches must not each build the
    # full-corpus BM25 state (#370), nor observe a half-published one (#490).
    cache = ensure_bm25_cache(index)
    idf = cache["idf"]
    avgdl = cache["avgdl"]
    centrality = cache["centrality"]
    inverted = cache["inverted"]

    # PageRank scores — computed and cached when sort_by requires it
    pagerank: dict = {}
    if sort_by in ("centrality", "combined"):
        if "pagerank" not in cache:
            with getattr(index, "_bm25_lock", None) or threading.Lock():
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
            # #377 item 6 reaches this exit too (v1.108.184): the capture is taken
            # before retrieval on the shared path above and was simply never
            # handed to the branch that diverges here.
            state_before=_state_before,
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
            # #377 item 6 reaches this exit too (v1.108.185).
            state_before=_state_before,
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
                "summary": _row_summary(sym),
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
    _sorted_heap = sorted(heap, key=lambda x: x[0], reverse=True)
    scored_results = [entry for _, _, entry in _sorted_heap]
    # Real ranking scores (top-first) for confidence/ledger — kept separate from
    # the response entries so _meta.confidence grades on real gap/strength instead
    # of flat-lining at the no-score neutral default (V6).
    _conf_scores = [hs for hs, _, _ in _sorted_heap]
    heap_count = len(scored_results)  # save before budget packing

    # §1.2: Materialize full-detail payload BEFORE packing so byte_length reflects
    # what will actually be returned. Prior to this fix, the packer saw the pre-full
    # skeleton size and the token_budget could overshoot by 5-20x.
    if detail_level == "full":
        for entry in scored_results:
            _materialize_full_entry(entry, index, store, owner, name)

    budget_truncated = False
    if token_budget is not None:
        # jcm#328: charge what the row actually adds to the response.
        packed, used_bytes = [], 0
        for entry in scored_results:
            b = _packing_cost_bytes(entry, detail_level)
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

    # Feature 1: Negative evidence / retrieval verdict — computed after the
    # freshness probe below so the verdict can report index staleness.
    negative_evidence: Optional[dict] = None
    _ne_threshold = _NEGATIVE_EVIDENCE_THRESHOLD
    try:
        from .. import config as _cfg
        _ne_threshold = _cfg.get("negative_evidence_threshold", _NEGATIVE_EVIDENCE_THRESHOLD)
    except Exception:
        pass

    meta = {
        "timing_ms": round(elapsed, 1),
        "total_symbols": len(index.symbols),
        "truncated": candidates_scored > heap_count or budget_truncated,
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        **cost_avoided(tokens_saved, total_saved),
    }
    if token_budget is not None:
        # jcm#328: report payload cost, not source-body bytes.
        used = sum(_packing_cost_bytes(e, detail_level) for e in scored_results)
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
    from ..retrieval.confidence import BM25_CEILING as _BM25_CEILING
    from ..retrieval.confidence import extract_ledger_features as _ledger_feats
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    from ..storage.token_tracker import record_ranking_event as _record_ranking_event
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(scored_results)
    meta["freshness"] = _probe.summary(scored_results)
    # Phase 2: runtime confidence — zero-cost no-op when no traces ingested.
    from ..runtime.confidence import attach_runtime_confidence as _attach_runtime
    _runtime_summary = _attach_runtime(
        scored_results,
        str(store._sqlite._db_path(owner, name)),
        id_field="id",
    )
    if _runtime_summary:
        meta["runtime_freshness"] = _runtime_summary
    _conf_input = [{"score": s} for s in _conf_scores]
    # `sort_by` decides the scale, and it is not always BM25. `centrality`
    # ranks by PageRank, which sums to 1 across FILES — a top file lands
    # near 0.05, so the BM25 curve graded the most central symbol in the
    # repo at strength ~0.01. Its honest ceiling is the most central file
    # there is. `combined` adds PageRank x100 to BM25 and stays BM25-scaled.
    if sort_by == "centrality":
        _conf_ceiling = max(pagerank.values(), default=0.0) or _BM25_CEILING
    else:
        _conf_ceiling = _BM25_CEILING
    _attach_confidence(
        result, _conf_input, is_stale=_probe.repo_is_stale,
        score_ceiling=_conf_ceiling,
    )
    # v1.108.272 (#440). Pre-packing entries, matching `_conf_scores` and the fusion
    # exit: the packer can drop rows the ranking did produce, and `identity_hit`
    # describes what the ranking found.
    _feat = _ledger_feats(
        _ledger_identity_rows(
            _conf_scores, [e for _, _, e in _sorted_heap], query_terms, query
        )
    )
    _record_ranking_event(
        # v1.108.188: the store this call was told to use, not whichever one the
        # first savings write of the process happened to pin.
        base_path=_ledger_base_path(store),
        tool="search_symbols",
        repo=f"{owner}/{name}",
        query=query,
        returned_ids=[r.get("id", "") for r in scored_results],
        confidence=result["_meta"].get("confidence"),
        semantic_used=False,
        repo_is_stale=_probe.repo_is_stale,
        **_feat,
    )

    from ..retrieval.verdict import build_verdict as _build_verdict
    from ..retrieval.verdict import index_changed_since_load as _index_changed_since_load
    from ..retrieval.verdict import index_coverage_meta as _index_coverage_meta
    _vres = _build_verdict(
        result_count=len(scored_results),
        matches_before_packing=heap_count,
        scanned_symbols=candidates_scored if candidates_scored > 0 else len(index.symbols),
        scanned_files=len(seen_files) if seen_files else len(index.source_files),
        best_score=max_bm25_score,
        threshold=_ne_threshold,
        query_terms=query_terms,
        source_files=index.source_files,
        semantic_requested=bool(semantic or semantic_only),
        index_stale=_probe.repo_is_stale,
        freshness=_probe.repo_freshness,
        index_changed=_index_changed_since_load(index),
        coverage=_index_coverage_meta(index),
        moved_during_scan=_subject_pre.moved_during_scan(
            _state_before, index, result_count=len(scored_results)
        ),
        working_tree=(
            _subject_pre.working_tree_state(
                index, scope=file_pattern, freshness=_probe.repo_freshness
            )
            if not scored_results else None
        ),
    )
    negative_evidence = _vres["negative_evidence"]
    meta["verdict"] = _vres["verdict"]

    # Exact-match honesty (v1.108.173). An identifier-shaped query that returns
    # only token-overlap neighbours previously came back as state "ok" with the
    # note "Confident matches returned", so a fuzzy near-miss was
    # indistinguishable from a hit. Attach the finding, and downgrade the
    # verdict so the note stops vouching for results that are ranked guesses.
    #
    # Deliberately NOT `absent`: results WERE returned, and under the absence
    # contract only `absent` proves absence. `low_confidence` is the honest
    # state and cannot be cited as evidence the symbol does not exist.
    from ..retrieval.query_shape import exact_match_report as _exact_match_report
    _exact = _exact_match_report(query, scored_results)
    if _exact is not None:
        meta["exact_match"] = _exact
        if not _exact["found"] and scored_results:
            _v = meta["verdict"]
            if _v.get("state") == "ok":
                _v["state"] = "low_confidence"
                _v["note"] = _exact["note"]

    _attach_index_truncation(meta, index)

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
        # #377 item 3: record what this answer was measured against. The
        # working tree is captured only for an absence, the one answer a later
        # edit can falsify while the index and its generation sit still.
        from ..retrieval import subject_state as _subject
        _result_cache_put(
            _cache_key,
            result,
            _subject.capture(
                index,
                include_tree=(meta.get("verdict") or {}).get("state") == "absent",
            ),
        )

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
    state_before: Optional[dict] = None,
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
    from ..retrieval import subject_state as _subject_state
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
    # v1.108.223 (#399, @vondecron): the matrix is decoded and L2-normalised
    # ONCE per (repo, store-stamp) and cached in process, instead of re-reading
    # and re-parsing every stored vector on every semantic query. It also
    # replaces `get_all()` with a read-only load, so this path no longer bumps
    # the .db mtime as a side effect of reading it (same defect class as .185).
    db_path = store._sqlite._db_path(owner, name)
    emb_store = EmbeddingStore(db_path)
    from ..storage import embedding_matrix as _embed_matrix
    matrix = _embed_matrix.get_matrix(db_path)
    embedded_ids = matrix.id_set if matrix is not None else set()

    missing = [s for s in index.symbols if s["id"] not in embedded_ids]
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

    # Cosine for every embedded symbol, in one vectorised pass. `matrix` was
    # loaded before the top-up above, so vectors embedded just now are scored
    # individually rather than forcing a full re-decode for a handful of rows.
    cos_by_id: dict[str, float] = matrix.score_all(query_vec) if matrix is not None else {}
    if missing and new_emb:
        for _sid, _vec in new_emb.items():
            cos_by_id[_sid] = _cosine_similarity(query_vec, _vec)

    # ── Two-pass scoring ───────────────────────────────────────────────────
    # Pass 1: collect lexical BM25 (identity EXCLUDED), the identity signal, and
    # cosine for every filtered symbol. Keeping identity out of the lexical
    # normalisation basis is the fix for W5: folding the exact/prefix boost
    # (50/30/20) into max_bm25 inflated the denominator whenever any exact match
    # existed, crushing every non-exact result's genuine lexical score toward 0.
    from ..retrieval.signal_fusion import _bm25_score_no_identity
    raw: list[tuple[dict, float, float, float]] = []  # (sym, lex, identity, cosine)
    max_lex = 0.0
    max_id = 0.0
    max_cos = 0.0
    # Identity-inclusive max (== the old max_bm25, since _bm25_score == lex + idn),
    # kept only for the negative-evidence threshold below so that check is unchanged.
    max_bm25 = 0.0
    query_joined = " ".join(query_terms)

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

        if semantic_only:
            lex = idn = 0.0
        else:
            lex = _bm25_score_no_identity(sym, query_terms, idf, avgdl, centrality)
            idn = _identity_score(sym, query_joined, raw_query=query)
        if lex > max_lex:
            max_lex = lex
        if idn > max_id:
            max_id = idn
        if lex + idn > max_bm25:
            max_bm25 = lex + idn

        cos = cos_by_id.get(sym["id"], 0.0)
        if cos > max_cos:
            max_cos = cos

        raw.append((sym, lex, idn, cos))

    # Pass 2: normalise lexical and identity on their OWN scales, then take the
    # max. An exact/prefix match still dominates — its identity term is max_id, so
    # id_norm == 1.0 and the channel matches the value the old identity-boosted
    # bm25_norm produced, leaving exact-match scores unchanged — while non-exact
    # results keep their full lexical dynamic range instead of being divided down
    # by the identity-inflated denominator (W5).
    scored: list[tuple[float, dict]] = []
    for sym, lex, idn, cos in raw:
        lex_norm = (lex / max_lex) if max_lex > 0.0 else 0.0
        id_norm = (idn / max_id) if max_id > 0.0 else 0.0
        lexical_channel = max(lex_norm, id_norm)
        score = cos if semantic_only else (1.0 - semantic_weight) * lexical_channel + semantic_weight * cos
        if score <= 0.0:
            continue
        scored.append((score, sym))

    # ⚠⚠ `(-score, symbol_id)`, not score alone (v1.108.228, #398 Arc 4 / #403).
    # A stable sort on score breaks ties on INSERTION ORDER — deterministic, but
    # arbitrary: it is the order SQLite handed the rows back. A ranking must not
    # depend on that.
    #
    # This became urgent rather than merely correct in v1.108.223. That release
    # gave the semantic scorer two lanes — a numpy float32 matmul and a
    # pure-Python float64 fallback — and their scores differ by ~1e-7. Measured
    # on a deliberately near-tied 4,000-vector corpus, they disagreed at RANK 0:
    # two installs of the SAME version ranked differently based only on whether
    # numpy was importable. A total order on (score, id) does not remove the
    # float difference, but it removes insertion order as a second, invisible
    # source of divergence — and it collapses the exact-tie bucket that #403's
    # certification breadth measures.
    #
    # ⚠ On real embeddings @rknighton measured ZERO float32/float64 boundary
    # disagreements across Django, FastAPI and jcm. The 4,000-vector corpus above
    # is synthetic and maximally homogeneous — it shows the hazard is real in
    # principle, NOT that it fires in practice. Both facts belong together.
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    top = scored[:effective_limit]
    # Real ranking scores (top-first) for confidence/ledger (V6).
    _conf_scores = [s for s, _ in top]

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
                "summary": _row_summary(sym),
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
        # jcm#328: charge what the row actually adds to the response.
        packed: list[dict] = []
        used = 0
        for entry in scored_results:
            b = _packing_cost_bytes(entry, detail_level)
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
        # jcm#328: report payload cost, not source-body bytes.
        used_bytes = sum(_packing_cost_bytes(e, detail_level) for e in scored_results)
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
    from ..retrieval.confidence import extract_ledger_features as _ledger_feats
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    from ..storage.token_tracker import record_ranking_event as _record_ranking_event
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(scored_results)
    meta["freshness"] = _probe.summary(scored_results)
    # Phase 2: runtime confidence — zero-cost no-op when no traces ingested.
    from ..runtime.confidence import attach_runtime_confidence as _attach_runtime
    _runtime_summary = _attach_runtime(
        scored_results,
        str(store._sqlite._db_path(owner, name)),
        id_field="id",
    )
    if _runtime_summary:
        meta["runtime_freshness"] = _runtime_summary
    _conf_input = [{"score": s} for s in _conf_scores]
    # Bounded at 1.0: `semantic_only` scores are a raw cosine, and the hybrid
    # score is `(1-w)*lexical_norm + w*cos` where BOTH terms are already
    # normalized to [0,1]. On the BM25 curve a 0.82 cosine read as strength
    # 0.185 — a strong hit graded as a weak one.
    from ..retrieval.confidence import COSINE_CEILING as _COSINE_CEILING
    _attach_confidence(
        result, _conf_input, is_stale=_probe.repo_is_stale,
        score_ceiling=_COSINE_CEILING,
    )
    # v1.108.272 (#440). `top` is pre-packing and already ordered, so its symbols line
    # up with `_conf_scores` positionally.
    _feat = _ledger_feats(
        _ledger_identity_rows(_conf_scores, [s for _, s in top], query_terms, query)
    )
    _record_ranking_event(
        # v1.108.188: the store this call was told to use, not whichever one the
        # first savings write of the process happened to pin.
        base_path=_ledger_base_path(store),
        tool="search_symbols",
        repo=f"{owner}/{name}",
        query=query,
        returned_ids=[r.get("id", "") for r in scored_results],
        confidence=result["_meta"].get("confidence"),
        semantic_used=True,
        repo_is_stale=_probe.repo_is_stale,
        **_feat,
    )
    best_score = max_cos if semantic_only else max_bm25

    # v1.108.184. This exit used to hand-roll the legacy `negative_evidence` block
    # and emit NO `_meta.verdict` at all, so not one of the absence gates shipped
    # since v1.108.166 applied to it \u2014 and it asserted "Do not claim this feature
    # exists" anyway.
    #
    # \u26a0 The reproduction, because the shape matters more than the missing block:
    # `semantic=True, token_budget=1` over a repo CONTAINING the target returned
    # `result_count: 0`, `_meta.truncated: True`, "No implementation found ... Do
    # not claim this feature exists", and `best_match_score: 53.774` \u2014 a strong
    # match reported in the same breath as its own absence. That is the item-1
    # defect ("an empty RESPONSE is not an empty SEARCH") fixed on the lexical
    # path in v1.108.177 and left standing here, which is what an early return
    # that reimplements the answer costs.
    #
    # This exit now builds the SAME verdict as the lexical one, so every gate
    # applies: stale, rebuilding, partial, unknown freshness, working tree,
    # movement, packing. `matches_before_packing` is the post-cap match count, so
    # a budget that empties the response can no longer read as an empty search.
    from ..retrieval.verdict import build_verdict as _build_verdict
    from ..retrieval.verdict import index_changed_since_load as _index_changed_since_load
    from ..retrieval.verdict import index_coverage_meta as _index_coverage_meta
    _vres = _build_verdict(
        result_count=len(scored_results),
        matches_before_packing=len(top),
        scanned_symbols=len(raw),
        scanned_files=len(seen_files) if seen_files else len(index.source_files),
        best_score=best_score,
        threshold=_ne_threshold,
        query_terms=query_terms,
        source_files=index.source_files,
        # The provider was resolved before this function was reached, so the
        # channel really did run; `semantic_requested` would re-probe it.
        semantic_requested=False,
        index_stale=_probe.repo_is_stale,
        freshness=_probe.repo_freshness,
        index_changed=_index_changed_since_load(index),
        coverage=_index_coverage_meta(index),
        moved_during_scan=_subject_state.moved_during_scan(
            state_before, index, result_count=len(scored_results)
        ),
        working_tree=(
            _subject_state.working_tree_state(
                index, scope=file_pattern, freshness=_probe.repo_freshness
            )
            if not scored_results else None
        ),
        # \u26a0 The load-bearing line. A zero result here is a statement about
        # embedding geometry, not about the repository: a symbol can sit in the
        # corpus and still score at or below zero against the query vector. The
        # lexical path's absence rests on a corpus fact (no symbol contains any
        # query term); this one cannot, ever, at any freshness. Expressed as a
        # DOWNGRADE so `handoff.absence_refusal` does the refusing off the
        # existing "only `absent` proves absence" rule, with no second rule to
        # keep in sync.
        absence_unprovable=(
            "a semantic ranking scores similarity rather than looking a name up, "
            "so a symbol can be present in the index and still fall at or below "
            "zero against this query vector."
        ),
    )
    meta["verdict"] = _vres["verdict"]
    # The semantic channel really ran, and the verdict should say so rather than
    # inherit the `off` default from `semantic_requested=False` above.
    meta["verdict"]["channels"]["semantic"] = "ok"
    # #500: a store written across a model change holds two vector widths, and
    # the matrix silently excludes whichever width is not the first row's. The
    # producer is fixed, but stores already in that state stay that way until
    # the next model change or a forced re-embed — so say so, or a partial
    # corpus reads as a complete one and short results read as a finding.
    _skipped_dim = getattr(matrix, "skipped_dim_mismatch", 0) if matrix else 0
    if _skipped_dim:
        meta["semantic_partial"] = {
            "symbols_excluded": _skipped_dim,
            "reason": "embedding_dimension_mismatch",
            "remedy": "embed_repo(force=True) rebuilds the store at one width",
        }
        meta["verdict"]["channels"]["semantic"] = "partial"
    negative_evidence = _vres["negative_evidence"]
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

    _attach_index_truncation(result.get("_meta"), index)
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
    state_before: Optional[dict] = None,
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
        # v1.108.185. A FOURTH exit in this tool, and the one with the least
        # standing to stay silent: the filters selected nothing, so not a byte was
        # read. Previously this returned a bare empty result with no verdict at
        # all, which reads exactly like "we looked everywhere and it is not there"
        # (#377 hardening item 9).
        elapsed = (time.perf_counter() - start) * 1000
        _empty_meta: dict = {
            "timing_ms": round(elapsed, 1),
            "total_symbols": len(index.symbols),
            "fusion": True,
            "search_mode": "fusion",
        }
        from ..retrieval.verdict import retrieval_verdict_for_index as _rv
        _empty_meta["verdict"] = _rv(
            index,
            result_count=0,
            query_terms=query_terms,
            scope=file_pattern,
            state_before=state_before,
            incomplete={
                "reason": "empty_scope",
                "files_eligible": 0,
                "note": (
                    "No indexed symbol matched this scope, so nothing was scanned. "
                    "An empty eligible set proves nothing about the corpus."
                ),
            },
        )["verdict"]
        return {"result_count": 0, "results": [], "_meta": _empty_meta}

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
            with getattr(index, "_bm25_lock", None) or threading.Lock():
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

    # Similarity channel: only if embeddings exist for this repo.
    # (Mirrors the semantic path's EmbeddingStore(db_path) + get_all() usage and
    #  embed_repo.embed_texts; the earlier EmbeddingStore(base_path=)/get_all(owner,name)/
    #  _embed_texts forms all raised and were swallowed, so this channel never ran.)
    similarity_used = False
    try:
        # v1.108.185: read-only, because the plain read wrote. `_connect` runs a
        # WAL pragma and a CREATE-TABLE script on every connection, so probing for
        # embeddings here moved the .db mtime mid-scan and made this exit report
        # `rebuilding` + `moved_during_scan` on the first fusion search of any
        # process — which downgraded the verdict and put `absent` out of reach for
        # an entirely self-inflicted reason.
        # v1.108.223 (#399): still read-only, and now decoded once per store
        # stamp rather than once per query.
        from ..storage import embedding_matrix as _embed_matrix
        matrix = _embed_matrix.get_matrix(store._sqlite._db_path(owner, name))
        if matrix is not None:
            from .embed_repo import _detect_provider, embed_texts
            provider = _detect_provider()
            if provider:
                q_emb = embed_texts([query], provider[0], provider[1])
                if q_emb and q_emb[0]:
                    from ..retrieval.signal_fusion import (
                        build_similarity_channel_from_scores,
                    )
                    sim_ch = build_similarity_channel_from_scores(
                        matrix.score_all(q_emb[0])
                    )
                    channels.append(sim_ch)
                    similarity_used = True
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "fusion similarity channel unavailable", exc_info=True
        )

    # Fuse
    fused = fuse(channels, smoothing=smoothing, weights=weights)

    # Build result list
    sym_by_id = {sym["id"]: sym for sym in candidates}
    scored_results = []
    # Real fused ranking scores (top-first) for confidence/ledger (V6).
    _conf_scores = [fr.score for fr in fused[:effective_limit]]

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
                "summary": _row_summary(sym),
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
        # jcm#328: charge what the row actually adds to the response.
        packed, used_bytes = [], 0
        for entry in scored_results:
            b = _packing_cost_bytes(entry, detail_level)
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
        "search_mode": "fusion",
        "channels": [ch.name for ch in channels],
    }
    if token_budget is not None:
        # jcm#328: report payload cost, not source-body bytes.
        used = sum(_packing_cost_bytes(e, detail_level) for e in scored_results)
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
    from ..retrieval.confidence import extract_ledger_features as _ledger_feats
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    from ..storage.token_tracker import record_ranking_event as _record_ranking_event
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(scored_results)
    meta["freshness"] = _probe.summary(scored_results)
    # Phase 2: runtime confidence — zero-cost no-op when no traces ingested.
    from ..runtime.confidence import attach_runtime_confidence as _attach_runtime
    _runtime_summary = _attach_runtime(
        scored_results,
        str(store._sqlite._db_path(owner, name)),
        id_field="id",
    )
    if _runtime_summary:
        meta["runtime_freshness"] = _runtime_summary
    # v1.108.187. `_ledger_feats` reads `identity`/`identity_match` off these rows and
    # this input carried neither, so `identity_hit` was recorded False on every fusion
    # row regardless of what the identity channel found. The channel's own `raw_scores`
    # is the answer, and it admits only symbols scoring above zero.
    #
    # ⚠ v1.108.272 (#440). This comment used to say the non-fusion paths' rows "carry
    # `identity` from `_identity_score`", as though fusion were the lone offender. That
    # was wrong: they carried the key nowhere `extract_ledger_features` looks, and had
    # the same defect for the same reason. Fixed at both exits via
    # `_ledger_identity_rows`. Reported by @rknighton.
    #
    # ⚠ Fed to the LEDGER only, not to `attach_confidence`. `compute_confidence`
    # sniffs the same key when no `has_identity_match` is passed and scores it 1.0
    # known-true / 0.7 unknown / 0.6 known-false, so sharing one input would move the
    # confidence of every fusion search. That is its own change with its own
    # justification, not a side effect of recording a column.
    _conf_input = [{"score": s} for s in _conf_scores]
    # A WRR score tops out at sum(weights)/(k+1) — ~0.049 for three unit-weight
    # channels, against BM25's tens. Grading it on the BM25 curve reported
    # near-zero strength for a perfect fused hit, which then drove the weight
    # tuner the wrong way; see retrieval/confidence.py.
    from ..retrieval.signal_fusion import fused_score_ceiling as _fused_ceiling
    _attach_confidence(
        result, _conf_input, is_stale=_probe.repo_is_stale,
        score_ceiling=_fused_ceiling(channels, smoothing=smoothing, weights=weights),
    )
    _id_raw = getattr(id_ch, "raw_scores", None) or {}
    _feat = _ledger_feats([
        {"score": fr.score, "identity": _id_raw.get(fr.symbol_id, 0.0)}
        for fr in fused[:effective_limit]
    ])
    _record_ranking_event(
        base_path=_ledger_base_path(store),
        tool="search_symbols_fusion",
        repo=f"{owner}/{name}",
        query=query,
        returned_ids=[r.get("id", "") for r in scored_results],
        confidence=result["_meta"].get("confidence"),
        semantic_used=similarity_used,
        repo_is_stale=_probe.repo_is_stale,
        **_feat,
    )

    # v1.108.185. This exit emitted no verdict and no negative evidence at all, so
    # a zero-result fusion search came back as a bare empty response: nothing
    # false, and nothing honest either. The same token_budget packing that broke
    # the lexical path in v1.108.177 and the semantic path in v1.108.184 lives here
    # too, so an empty response could be pure packing.
    #
    # ⚠ Unlike the semantic exit, fusion CAN prove absence, and the reason is the
    # channel construction rather than a judgement call. `build_lexical_channel`
    # and `build_identity_channel` each score EVERY eligible candidate with no cap,
    # and `fuse` keeps every symbol appearing in ANY channel — so a zero fused set
    # means every candidate scored zero on both BM25 and identity, which is the
    # same corpus fact the lexical path's absence rests on. The similarity channel
    # can only ADD symbols to the fused set, never remove one, so its absence
    # weakens the RANKING and cannot manufacture a false absence. That asymmetry is
    # exactly what the semantic exit lacked, and it is why there is no
    # `absence_unprovable` here.
    from ..retrieval.verdict import retrieval_verdict_for_index as _rv
    _vres = _rv(
        index,
        result_count=len(scored_results),
        # Pre-cap AND pre-packing: `fused[:effective_limit]` drops matches before
        # the packer ever sees them, and both drops are the caller's to know about.
        matches_before_packing=len(fused),
        scanned_symbols=len(candidates),
        scanned_files=len(seen_files) if seen_files else len(index.source_files),
        best_score=_conf_scores[0] if _conf_scores else None,
        query_terms=query_terms,
        scope=file_pattern,
        state_before=state_before,
        semantic_channel="ok" if similarity_used else "off",
    )
    meta["verdict"] = _vres["verdict"]
    if _vres["negative_evidence"] is not None:
        # Parity with this tool's other exits: the same question must not get a
        # differently-honest answer depending on which ranking mode ran.
        result["negative_evidence"] = _vres["negative_evidence"]
        _q = query[:80]
        if _vres["negative_evidence"]["verdict"] == "no_implementation_found":
            result["⚠ warning"] = (
                f"No implementation found for '{_q}'. Do not claim this feature exists."
            )
        else:
            result["⚠ warning"] = (
                f"Low-confidence matches for '{_q}' "
                f"(best score: {_vres['negative_evidence']['best_match_score']}). "
                f"Verify before claiming this feature exists."
            )

    _attach_index_truncation(result.get("_meta"), index)
    if cacheable and cache_key is not None:
        from ..retrieval import subject_state as _subject
        _result_cache_put(
            cache_key,
            result,
            _subject.capture(
                index,
                include_tree=((result.get("_meta") or {}).get("verdict") or {}).get(
                    "state"
                ) == "absent",
            ),
        )

    return result
