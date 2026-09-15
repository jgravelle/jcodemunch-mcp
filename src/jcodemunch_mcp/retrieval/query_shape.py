"""Query-shape classification: detect source-shaped tokens in a retrieval query.

A query like "how does SqliteStore.incremental_save handle deltas?" carries a
token the author lifted straight out of the source. That token deserves an
exact-symbol lookup ahead of any ranked scoring — BM25 tokenization splits
``SqliteStore.incremental_save`` into fragments and dilutes the one identifier
the caller actually named. This module classifies query tokens into shapes:

- ``qualified``: ``Store::get`` / ``config.load_config`` — identifier segments
  joined by ``::`` or ``.`` (file-extension tails are excluded; a filename is
  not a symbol name)
- ``camel``: ``FreshnessProbe`` / ``getUser`` — an interior case change only an
  identifier has
- ``snake``: ``get_ranked_context`` — underscore-joined identifier

Plain prose words never match any shape, so a pure natural-language query
yields an empty list and downstream behavior is byte-identical.
"""

import re

# Identifier segment: letter/underscore head, word tail.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

_QUALIFIED_RE = re.compile(rf"^{_IDENT}(?:(?:::|\.){_IDENT})+$")
_CAMEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_INTERIOR_CASE_RE = re.compile(r"[a-z0-9][A-Z]|[A-Z]{2,}[a-z]")
_SNAKE_RE = re.compile(rf"^{_IDENT}$")

# Dotted tails that mean "this token is a filename, not a qualified symbol".
_FILE_EXT_TAILS = frozenset({
    "py", "js", "jsx", "ts", "tsx", "mjs", "cjs", "mts", "cts", "md", "txt",
    "json", "jsonc", "yaml", "yml", "toml", "xml", "html", "css", "scss",
    "rs", "go", "java", "kt", "rb", "php", "c", "h", "cpp", "hpp", "cc",
    "cs", "swift", "sql", "sh", "bash", "ps1", "lua", "dart", "vue", "svelte",
    "astro", "erl", "ex", "exs", "scala", "clj", "zig", "gz", "db", "sqlite",
})

# Strip surrounding punctuation a prose sentence wraps around a pasted
# identifier: quotes, backticks, parens, trailing sentence punctuation.
_TRIM_RE = re.compile(r"^[\s'\"`(\[{<]+|[\s'\"`)\]}>.,;:!?]+$")

_MAX_TOKENS = 3  # seed at most this many distinct source-shaped tokens


def source_shaped_tokens(query: str) -> list[dict]:
    """Extract source-shaped tokens from *query*, first-seen order, deduped.

    Returns a list of ``{"token", "name", "parent", "shape"}`` dicts where
    ``name`` is the identifier to match symbol names against (the tail segment
    for qualified tokens) and ``parent`` is the qualifying segment before the
    tail (``None`` for bare identifiers). Capped at ``_MAX_TOKENS`` entries.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for raw in query.split():
        tok = _TRIM_RE.sub("", raw)
        # A trailing call spelling (``foo()``, or ``foo(`` after the trim ate
        # the closing paren) is the same identifier.
        tok = re.sub(r"\(\)?$", "", tok)
        if len(tok) < 3 or tok in seen:
            continue
        shape = _classify(tok)
        if shape is None:
            continue
        seen.add(tok)
        if shape == "qualified":
            parts = re.split(r"::|\.", tok)
            entry = {"token": tok, "name": parts[-1], "parent": parts[-2], "shape": shape}
        else:
            entry = {"token": tok, "name": tok, "parent": None, "shape": shape}
        out.append(entry)
        if len(out) >= _MAX_TOKENS:
            break
    return out


def _classify(tok: str) -> str | None:
    if _QUALIFIED_RE.match(tok):
        if tok.rsplit(".", 1)[-1].lower() in _FILE_EXT_TAILS and "::" not in tok:
            return None  # filename, not a qualified symbol
        return "qualified"
    if "_" in tok and _SNAKE_RE.match(tok) and not tok.startswith("__"):
        return "snake"
    if tok.startswith("__") and tok.endswith("__") and _SNAKE_RE.match(tok):
        return "snake"  # dunder is still an exact identifier
    if _CAMEL_RE.match(tok) and _INTERIOR_CASE_RE.search(tok):
        return "camel"
    return None


# --- Exact-match honesty (v1.108.173) --------------------------------------
#
# A user searched the exact token `runPromiseFulfillmentCheck`, a function
# committed that day and not yet indexed, and got ten ranked hits on the
# substring "fulfillment" (order fulfilment, shipping routing, PHP helpers)
# formatted exactly like real hits. Reproduced here against this repo:
# `extractMountsFulfillmentCheck` returns `_extract_mounts`, `_JS_MOUNT` and
# friends under `state: "ok"` with the note "Confident matches returned."
#
# BM25 tokenization is why. `runPromiseFulfillmentCheck` splits into
# run/promise/fulfillment/check, so anything sharing one fragment scores. That
# is correct for prose and actively misleading for an identifier: a fuzzy
# near-miss becomes indistinguishable from a hit, which turns a missing index
# into confident misinformation.
#
# The fix is to say so, not to suppress the results. They are still the best
# available guesses; they are just not what was asked for.

def is_identifier_query(query: str) -> bool:
    """True when the whole query is one source-shaped identifier.

    Deliberately strict: a single token that classifies as qualified, camel, or
    snake. Prose, multi-word queries and bare lowercase words all return False,
    so their behavior is byte-identical.

    Note a bare lowercase word like ``fulfillment`` is NOT an identifier query
    even though it could name a symbol. Without an interior case change or an
    underscore there is nothing to distinguish "I am naming a symbol" from "I am
    describing a topic", and guessing wrong would attach a false-negative label
    to an ordinary keyword search.
    """
    stripped = query.strip()
    if not stripped or len(stripped.split()) != 1:
        return False
    return bool(source_shaped_tokens(stripped))


def exact_needles(query: str) -> tuple[str, str] | None:
    """The two forms an exact match may take, or None for a multi-word query.

    ⚠ This is THE definition of "exact", and it has two readers: the report
    below, and the result cut in `search_symbols`, which must not evict an exact
    match in favour of a lexical near-miss. A second copy of this normalisation
    would let the two disagree about which rows are exact, which is worse than
    either answer alone.

    ⚠⚠ Deliberately NOT gated on `is_identifier_query`, and the difference is
    the whole defect. That gate refuses a single lower-case word with no
    underscore, because such a query cannot be told from a topic -- correct for
    deciding whether to ATTACH a report, and wrong for deciding what to KEEP:
    `partial`, `pick` and `run` are exactly the names whose same-named locals
    fill the result window (#699). Ranking a row whose name IS the query above
    one that merely shares tokens costs nothing when the query was prose, since
    then no row matches exactly.

    For a qualified query (`Store.load`), the leaf is what a symbol `name`
    holds; the full path is what an `id` holds.
    """
    stripped = query.strip()
    if not stripped or len(stripped.split()) != 1:
        return None
    needle = stripped.lower()
    return needle, needle.rsplit("::", 1)[-1].rsplit(".", 1)[-1]


def is_exact_row(row: dict, needle: str, leaf: str) -> bool:
    """Does one result row answer the query exactly? See `exact_needles`."""
    name = str(row.get("name", "")).lower()
    sym_id = str(row.get("id", "")).lower()
    return needle in (name, sym_id) or bool(leaf) and name == leaf


def exact_match_report(
    query: str, results: list[dict], total_exact: int | None = None
) -> dict | None:
    """Classify how well `results` answer an identifier-shaped `query`.

    Returns None for non-identifier queries so nothing is attached and no
    tokens are spent on the common prose case.

    Matching mirrors `_identity_score` in search_symbols: exact on name or id,
    then a name prefix. Case-insensitive, because a caller retyping a symbol
    from memory gets the case wrong more often than they get the spelling wrong.

    ⚠⚠ `total_exact` is how many exact matches the SCAN found, before the result
    cap cut the page. Counting the rows handed to this function answers "how
    many did you return" while reading as "how many exist" -- #559's shape, and
    it is what made the cap's effect invisible: a query with 38 exact matches
    reported `exact: 9` and looked complete. Pass it whenever the caller can
    count before slicing; when it is None the field falls back to the page and
    `exact_truncated` is absent rather than falsely False.
    """
    # The report stays gated on identifier shape: attaching "no symbol named
    # 'the' is in this index" to a prose search would be noise. The CUT is not
    # gated -- see `exact_needles`.
    if not is_identifier_query(query):
        return None
    needles = exact_needles(query)
    if needles is None:
        return None
    needle, leaf = needles

    exact, prefix = [], []
    for row in results:
        name = str(row.get("name", "")).lower()
        if is_exact_row(row, needle, leaf):
            exact.append(row)
        elif name.startswith(needle) or (leaf and name.startswith(leaf)):
            prefix.append(row)

    returned_exact = len(exact)
    scanned_exact = returned_exact if total_exact is None else total_exact
    found = bool(exact) or bool(prefix)
    report = {
        "queried": query.strip(),
        "found": found,
        "exact": scanned_exact,
        "prefix": len(prefix),
    }
    if total_exact is not None and scanned_exact > returned_exact:
        # The caller asked for a name, more symbols carry it than fit the page,
        # and the remedy is theirs: raise max_results. Silence here is how the
        # cut stayed invisible.
        report["exact_returned"] = returned_exact
        report["exact_truncated"] = True
    if not found and results:
        report["note"] = (
            f"No symbol named '{query.strip()}' is in this index. The results "
            f"below share tokens with the query and are ranked guesses, not "
            f"matches. If you expected this symbol to exist, the index may "
            f"predate it: re-index before concluding it does not."
        )
    return report
