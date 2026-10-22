"""Lexical and vector scoring primitives shared by the retrieval path.

⚠⚠ Extracted from `tools/search_symbols.py` to break a real import cycle.
`retrieval/signal_fusion.py` reached into the TOOL for `_identity_score`,
`_cosine_similarity`, `_sym_tokens` and the BM25 constants, while
`search_symbols` imported the fusion functions back. Neither direction was
wrong: these are retrieval primitives that happened to be written inside the
first tool that needed them, so the shared half had no home of its own.

⚠ Nothing here imports `search_symbols` or `signal_fusion`, and it must stay
that way -- this is the LEAF they share. `search_symbols` re-exports every name
below, because ~30 call sites across `src/` and `tests/` still import them from
there and a move that also renames the import path is a different change.

⚠⚠ A re-export is a MONKEYPATCH TRAP: patching `search_symbols._identity_score`
does not affect callers that resolve it through this module's globals, and
nothing warns. Patch `retrieval.scoring` instead.
"""

from __future__ import annotations

import functools
import math
import re

_BM25_K1 = 1.5


_BM25_B = 0.75


_FIELD_REPS = {"name": 3, "keywords": 2, "signature": 2, "summary": 1, "docstring": 1}


_CAMEL_RE = re.compile(r"([a-z])([A-Z])")


_TOKEN_RE = re.compile(r"[^\W_]+")


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


# --- Query words and test demotion (shared by every lexical scorer) -------- #
# A natural-language question carries words that name no code: "How does
# dependency injection work?" scored "how" and "does" as content, so every
# test named ``test_*_does_not_*`` outranked the functions that implement the
# concept. In a multi-word query these words count at FUNCTION_WORD_WEIGHT
# (see `function_word_terms` and `query_idf`); nothing is removed from the
# query or the index. A one-word query, an identifier's parts, and a query
# made only of such words keep full weight, so ``is`` or ``do`` still resolve.
QUERY_STOPWORDS = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "from", "with",
    "and", "or", "as", "into", "about", "over",
    "how", "what", "where", "why", "when", "which", "who", "whom", "whose",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "doing", "done",
    "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "it", "its", "this", "that", "these", "those", "there", "here",
    "i", "we", "you", "me", "my", "our", "your",
})


# A plain word: letters only, all lower case or capitalised, with trailing
# punctuation allowed. Anything else (`ndcg_at_k`, `getUser`, `os.path`,
# `v2`) is an identifier, and every part of an identifier is content.
_PLAIN_WORD_RE = re.compile(r"[A-Za-z][a-z]*[?.,!;:]*")

# How much a function word counts in a multi-word query's lexical score,
# relative to its corpus IDF. Not 0: "is valid" and "value of" are names
# typed as words, and the exact names `is_valid` and `value_of` stay ahead of
# their neighbours only through the "is" and "of" they contain. Small: in
# "How does dependency injection work?" a test named `..._does_not_...` must
# not outrank the code on "does".
FUNCTION_WORD_WEIGHT = 0.25


def query_terms(query: str) -> list[str]:
    """Tokens a lexical scorer matches for ``query``: every token, in order.

    Falls back to the lowered query when there are no tokens at all (the old
    ``_tokenize(q) or [q.lower()]`` contract). Empty strings are never
    returned: "" matches every filename. How much each token COUNTS is
    `query_idf`'s job; nothing is removed here.
    """
    toks = [t for t in _tokenize(query) if t]
    if toks:
        return toks
    low = (query or "").lower()
    return [low] if low else []


def function_word_terms(query: str) -> frozenset:
    """The tokens of ``query`` that count as function words ("how", "does", ...).

    Only in a multi-word query, only where they stand as plain words, and never
    a token that is also part of an identifier in the same query. A one-word
    query is a name: the "at" in ``ndcg_at_k`` and the "is" in ``is_valid`` are
    content. A query made only of function words keeps them all at full weight.
    """
    words = (query or "").split()
    if len(words) < 2:
        return frozenset()
    fw: set[str] = set()
    content: set[str] = set()
    for w in words:
        plain = _PLAIN_WORD_RE.fullmatch(w) is not None
        for tok in _tokenize(w):
            if plain and tok in QUERY_STOPWORDS:
                fw.add(tok)
            else:
                content.add(tok)
    fw -= content
    if not content:
        return frozenset()
    return frozenset(fw)


def query_idf(idf: dict, query: str) -> dict:
    """The IDF map one query is scored with: the corpus IDF of each query token,
    with function words (`function_word_terms`) at `FUNCTION_WORD_WEIGHT`.

    Every lexical scorer reads IDF only for the query's own tokens, so this
    small map stands in for the corpus map and the shared cache is never edited.
    """
    fw = function_word_terms(query)
    out: dict = {}
    for t in query_terms(query):
        v = idf.get(t, 0.0)
        out[t] = v * FUNCTION_WORD_WEIGHT if t in fw else v
    return out


# Words that make a query ABOUT tests; with one present, test code is what
# the caller wants and is not demoted.
_TEST_INTENT = frozenset({
    "test", "tests", "testing", "spec", "specs", "fixture", "fixtures",
    "mock", "mocks", "pytest", "unittest", "conftest",
})

# Multiplier on a test-file symbol's lexical score for a query that is not
# about tests. Test suites outnumber the code they exercise and repeat its
# vocabulary in their names, so without this a conceptual question returns
# tests first. 0.25 is a chosen value, checked (not tuned) on the pinned
# fastapi questions, where it cleared tests from the top 10; no other value was
# compared. The miniature of that case is tests/test_conceptual_query_ranking.py.
# A test still ranks when nothing in the source matches, and only RANKING reads
# the reduced score (see `_bm25_score`).
TEST_SYMBOL_WEIGHT = 0.25


def has_test_intent(terms: list[str]) -> bool:
    return any(t in _TEST_INTENT for t in terms)


def test_file_factor(sym: dict, terms: list[str]) -> float:
    """1.0, or `TEST_SYMBOL_WEIGHT` for a symbol in a test file when the query
    is not about tests. The file check is `_test_paths.is_test_file`, the one
    test-file rule, cached on the symbol dict."""
    if has_test_intent(terms):
        return 1.0
    flag = sym.get("_is_test")
    if flag is None:
        # The shared rule itself, imported here because importing ``tools``
        # at module level would cycle back into this module.
        from ..tools._test_paths import is_test_file as _shared_test_rule  # noqa: PLC0415

        flag = bool(_shared_test_rule(sym.get("file") or ""))
        sym["_is_test"] = flag
    return TEST_SYMBOL_WEIGHT if flag else 1.0


# Languages whose symbols are settings, data or styling rather than code: a
# workflow's env keys, a pyproject table, a CSS selector. Settings files can
# hold as many symbols as the code beside them, and a common word in a key
# (`UV_RESOLUTION` for "resolution") fills a code question's top results with
# them. Every name here is one `parser/languages.py` produces (a test holds
# that). Left out on purpose: `hcl` and `openapi`, whose symbols are
# infrastructure and API definitions a code question may be asking about.
_CONFIG_LANGUAGES = frozenset({"yaml", "toml", "json", "xml"})
_STYLE_LANGUAGES = frozenset({"css", "scss", "sass", "less", "styl"})
NON_CODE_LANGUAGES = _CONFIG_LANGUAGES | _STYLE_LANGUAGES

# Words that make a query ABOUT configuration, or about styling; with one
# present, that group of languages is not weighted (and only that group: a
# question about the CI workflow is not a question about the stylesheet).
_CONFIG_INTENT = frozenset({
    "config", "configuration", "configure", "yaml", "yml", "toml", "json", "xml",
    "workflow", "workflows", "ci", "setting", "settings", "pyproject", "manifest", "env",
})
_STYLE_INTENT = frozenset({
    "css", "scss", "sass", "less", "styl", "stylus", "style", "styles", "stylesheet",
    "selector", "selectors",
})

# Words that make a query ABOUT values; with one present no field or constant
# is weighted.
_DATA_INTENT = frozenset({
    "field", "fields", "constant", "constants", "attribute", "attributes",
    "property", "properties", "variable", "variables", "setting", "settings",
    "value", "values",
})

# Multipliers for a NON_CODE_LANGUAGES symbol and for a data symbol (see
# `is_data_symbol`). 0.5 is a chosen value, checked on the pinned fastapi
# questions: it cleared every settings-file symbol from their top 5 while
# "python version matrix" still ranked a workflow key first, which 0.25 did
# not. The data weight reuses the value; on "context bind" it moved a field
# and an UPPER_CASE constant out of the top 5. Only RANKING reads the reduced score; verdicts,
# confidence and absence read the raw one.
NON_CODE_WEIGHT = 0.5
DATA_KIND_WEIGHT = 0.5


def is_data_symbol(sym: dict) -> bool:
    """A field, or a constant whose name is written as one (UPPER_CASE).

    The kind alone is not enough: JavaScript and TypeScript index every
    module-level `const` as a constant, so `const app = express()` and
    `const router = express.Router()` are kind ``constant`` and are the code a
    routing question is asking about. The UPPER_CASE spelling is the
    convention for a value across languages."""
    kind = sym.get("kind")
    if kind == "field":
        return True
    if kind != "constant":
        return False
    name = (sym.get("name") or "").rsplit(".", 1)[-1].lstrip("_")
    # A cased letter is required: a name in a script with no case (CJK, for
    # one) equals its own upper-case form and says nothing about convention.
    return any(c.isupper() for c in name) and name == name.upper()


def rank_factor(sym: dict, terms: list[str]) -> tuple[float, str | None]:
    """The ranking multiplier for ``sym`` on this query, and why.

    One reason applies, the first that matches: ``test_file``
    (`test_file_factor`), ``non_code_file`` (a NON_CODE_LANGUAGES symbol on a
    query with no configuration, or styling, word) or ``data_kind``
    (`is_data_symbol`, on a query with no value word). ``(1.0, None)``
    otherwise."""
    weight = test_file_factor(sym, terms)
    if weight != 1.0:
        return weight, "test_file"
    lang = (sym.get("language") or "").lower()
    if lang in NON_CODE_LANGUAGES:
        intent = _CONFIG_INTENT if lang in _CONFIG_LANGUAGES else _STYLE_INTENT
        if any(t in intent for t in terms):
            return 1.0, None
        return NON_CODE_WEIGHT, "non_code_file"
    if is_data_symbol(sym) and not any(t in _DATA_INTENT for t in terms):
        return DATA_KIND_WEIGHT, "data_kind"
    return 1.0, None


@functools.lru_cache(maxsize=256)
def identity_query(query: str) -> str:
    """The query as the identity (exact-name) channel reads it: EVERY token.

    Function words are down-weighted only in the lexical score (`query_idf`);
    the identity channel matches names, so "is valid" and "value of" are
    compared with `is_valid` and `value_of` in full. Same value the scorers
    used before `query_terms` existed. Cached: it runs once per
    scored symbol.
    """
    return " ".join(_tokenize(query) or [(query or "").lower()])


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
