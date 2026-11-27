"""Check if an identifier is referenced anywhere: imports + file content.

Combines find_references and search_text into one call.
Answers "is this identifier used anywhere?" for quick dead-code detection.
"""

import posixpath
import re
import time
import unicodedata
from ._enclosing import attach_enclosing_symbols
from typing import Optional

from ..parser.imports import wildcard_edge_filter
from ..storage import IndexStore
from ..word_rule import word_pattern
from ._utils import index_status_to_tool_error, resolve_repo


def _fold(text: str) -> str:
    """The spelling two identifiers are compared in (LEDGER L-84).

    ⚠⚠ Python normalises identifiers to NFKC, so `def file()` called as
    `\ufb01le()` (the fi ligature) is one function called once, and a byte
    comparison never saw the call: `check_delete_safe` read that as no
    reference and graded the used function `safe_to_delete` at 1.0.

    NFKC on both sides of a LINE or IMPORT match, for every language: a
    found reference only ever blocks a delete, and the search already
    over-matches on purpose (substring, case-insensitive). Folding can drop
    a raw hit only where a combining mark follows the identifier and NFKC
    composes it onto the last letter (`file` + U+0301), which spells a
    different identifier, so no real reference is lost.

    ⚠⚠ NEVER fold the definition-span EXCLUSION (review, L-84): that step
    REMOVES matches, and in a language that does not normalise, Java's
    `\ufb01le()` and `file()` are two methods. Folding it skipped the body of
    `\ufb01le` as `file`'s own definition and lost the call inside it --
    `safe_to_delete` at 1.0 where main blocked. The identifier is the
    declared spelling, so the exclusion needs no folding to find it.

    (LEDGER L-86) Unicode escapes are decoded first. Java translates them
    before it lexes (JLS 3.3), C# accepts them inside an identifier, and
    ECMAScript accepts its braced form there too, so a call spelled with an
    escape names the function it spells, and the raw line never matched it: a
    used Java method graded `safe_to_delete` at 1.0, and a JS function the
    same way through the braced form (review). Decoding runs on every line of
    every language, strings and comments included.
    ⚠ It cannot drop a CALL: wherever an escape can stand before a call, it
    IS one. It can drop a textual MENTION whose letters sit inside an
    escape's hex digits (a Windows path segment after a backslash that starts
    with `u` and four hex digits), which this search counted before; that is
    a mention, not a use. ⚠ Java's even-backslash rule is not applied: an
    escaped backslash followed by `u` decodes too, which only adds a match.

    ASCII is returned as `lower()` without normalising: NFKC is the
    identity there, and this runs on every line of every file.
    """
    if "\\" in text:
        text = _decode_escapes(text)
    if text.isascii():
        return text.lower()
    return unicodedata.normalize("NFKC", text).lower()


# A backslash, then: `u` and 1-6 hex digits in braces (ECMAScript); one or
# more `u` and four hex digits (Java, C#, ECMAScript); or `U` and eight (C#).
# Java allows the repeated `u`; the hex digits take either case.
_ESCAPE = re.compile(r"\\(?:u\{([0-9A-Fa-f]{1,6})\}|u+([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8}))")


def _unescape(m: "re.Match[str]") -> str:
    cp = int(m.group(1) or m.group(2) or m.group(3), 16)
    return chr(cp) if cp <= 0x10FFFF else m.group(0)


def _decode_escapes(text: str) -> str:
    out = _ESCAPE.sub(_unescape, text)
    # Java spells a supplementary character as two escaped surrogates; join
    # them, and leave any lone surrogate as a replacement character.
    return out.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _name_through(
    lines: list[str], lo: int, hi: int, ident_lower: str, ident_exact: str, signature: str = ""
) -> tuple[int, int]:
    """A sibling definition's declaration: its first line through the first line
    that names the identifier, so an annotation above the name does not leave
    the name line counting. Only the first line when no line in the span names
    it (LEDGER L-91).

    ⚠⚠ The first line that names it is found two ways (LEDGER L-331):

    - Within the lines the stored SIGNATURE spans, when the signature holds
      the exact declared name as a token: the first line holding that exact
      spelling as a whole token (the word rule), after decoding Unicode
      escapes. A case-folded substring took `@FileAnno` or `@Profile` above
      `public int file()` as the name line, and the declaration below it
      counted as a use of the target. The escapes are decoded so an escaped
      declaration (`int \\u0066ile()`) is found on its own line.
    - Otherwise main's rule, unchanged: the first line whose folded text holds
      the folded name.

    ⚠⚠ The signature is the BOUND, and it is load-bearing: the exact search
    finds a line at or after the folded one, so the excluded span can only
    grow, and a span that grows past the declaration hides a call. A name
    derived from the file name (a Razor component, `view Counter`, spanning
    the whole file) has no declaration line spelling it, and an unbounded
    search stopped at `App.Counter.Next()` in the body and licensed a delete
    main blocks (L-331 review). The signature never includes the body, so a
    line inside it is declaration.
    """
    for line_no in range(lo, min(max(lo, hi), len(lines)) + 1):
        if ident_lower in _fold(lines[line_no - 1]):
            folded = (lo, line_no)
            break
    else:
        folded = (lo, lo)
    word = re.compile(word_pattern(ident_exact))
    if not signature or not word.search(_decode_escapes(signature)):
        return folded
    last = min(lo + signature.count("\n"), max(lo, hi), len(lines))
    for line_no in range(lo, last + 1):
        line = lines[line_no - 1]
        if "\\" in line:
            line = _decode_escapes(line)
        if word.search(line):
            return (lo, line_no)
    return folded


def _calls_beyond_declarations(line: str, ident_exact: str, declarations: int) -> bool:
    """True when *line* calls the exact declared name beyond the *declarations*
    same-named siblings whose name line it is (LEDGER L-329).

    A call is the name as a whole token (the word rule) followed by optional
    space and `(`. The declarations are taken to be the first *declarations*
    tokens on the line, and each of those that is call-shaped (`int file()`,
    `def file(self)`) is subtracted from the calls; one that is not
    (`int file = ...`, Kotlin `val file get() = ...`) subtracts nothing, so a
    call after it still counts. Two declarations sharing a line (an interface
    overload pair, minified JS) are two call-shaped tokens and no call.
    """
    word = word_pattern(ident_exact)
    tokens = list(re.finditer(word, line))
    if len(tokens) <= declarations:
        return False
    call_after = re.compile(r"\s*\(")
    calls = [bool(call_after.match(line, m.end())) for m in tokens]
    return sum(calls) > sum(calls[:declarations])


def _sibling_declarations(
    lines: list[str], siblings: list[tuple[int, int, str]], ident_lower: str, ident_exact: str
) -> list[tuple[int, int]]:
    """The lines of each same-named sibling that are its declaration and nothing else.

    `_name_through`'s span, except that a name line is given back when it calls
    the name beyond the declarations whose name line it is, as a one-line
    method does: `@Override public int file() { return new C().file(); }`,
    Kotlin's `fun file() = C().file()`, a Python one-liner, or a field
    initialised by a call (`int file = new C().file();`) (LEDGER L-329). Hiding
    that line hid the call, and `check_delete_safe` graded the unused-looking
    `C.file` `safe_to_delete` with no blockers.

    ⚠ Exact case and call shape only. A parameter (`def file(self, file)`), a
    type (`File file()`), a constructor in another case (`new File(...)`) or a
    comment naming the name without `(` is not a call, so the declaration
    stays out. A comment that writes `file()` IS counted, and so is a one-line
    sibling that calls itself, the way a sibling's body lines already are
    (name-based, not resolved). A call spelled with an escape or in another
    case on that line stays hidden, as it was before this rule.
    """
    decls = [_name_through(lines, lo, hi, ident_lower, ident_exact, sig) for lo, hi, sig in siblings]
    per_line: dict[int, int] = {}
    for _lo, name_line in decls:
        per_line[name_line] = per_line.get(name_line, 0) + 1
    out = []
    for decl_lo, name_line in decls:
        if name_line <= len(lines) and _calls_beyond_declarations(
            lines[name_line - 1], ident_exact, per_line[name_line]
        ):
            out.append((decl_lo, name_line - 1))
        else:
            out.append((decl_lo, name_line))
    return out


def first_match_line(ref: dict) -> int:
    """The first matching line of one `content_references` entry.

    The entry is `{file, matches: [{line, text}]}` and has no `line` of its
    own; the preflights read one anyway, so every blocker said line 0.
    """
    matches = ref.get("matches") or []
    return int(matches[0].get("line", 0) or 0) if matches else 0


def target_span_of(sym: dict) -> Optional[tuple[str, int, int]]:
    """``(file, first_line, last_line)`` of one symbol, or None when it has none.

    None sends ``_check_single`` back to excluding every same-named definition,
    the honest answer when the target's own lines are unknown.
    """
    file_path = sym.get("file") or ""
    try:
        lo = int(sym.get("line") or 0)
        hi = int(sym.get("end_line") or lo)
    except (TypeError, ValueError):
        return None
    if not file_path or lo <= 0:
        return None
    return (file_path, lo, max(lo, hi))


def _check_single(
    identifier: str,
    index,
    search_content: bool,
    max_content_results: int,
    owner: str,
    name: str,
    store: "IndexStore",
    start: float,
    target_span: Optional[tuple[str, int, int]] = None,
    max_import_results: int = 0,
) -> dict:
    """Core logic for checking a single identifier against import + content data.

    ``target_span`` is ``(file, first_line, last_line)`` of the ONE symbol a
    caller is asking about. Given it, only that span is excluded from the
    content search; without it, every definition spelled like the identifier
    is (LEDGER L-91).
    """
    ident_lower = _fold(identifier)
    # The exclusion's key: the EXACT declared spelling, case included. An
    # exclusion REMOVES matches, so it must never widen (see `_fold`'s ⚠⚠):
    # compared case-insensitively, Java `File()` beside `file()` skipped the
    # body of `File` as `file`'s own definition and lost the call inside it,
    # grading the used `file` `safe_to_delete` at 1.0 (LEDGER L-88). In a
    # case-insensitive language, exact case errs toward counting a
    # differently-cased declaration as a reference, which only blocks.
    ident_exact = identifier

    # ── Import-level check ──────────────────────────────────────────────────
    import_references = []
    if index.imports is not None:
        dead_wildcard = wildcard_edge_filter(index)
        for src_file, file_imports in index.imports.items():
            matches = []
            for imp in file_imports:
                if dead_wildcard(imp, src_file):
                    continue
                named_match = any(_fold(n) == ident_lower for n in imp.get("names", []))
                spec = imp["specifier"]
                spec_stem = _fold(posixpath.splitext(posixpath.basename(spec))[0])
                stem_match = spec_stem == ident_lower

                if named_match or stem_match:
                    matches.append({
                        "specifier": spec,
                        "names": imp.get("names", []),
                        "match_type": "named" if named_match else "specifier_stem",
                    })

            if matches:
                import_references.append({"file": src_file, "matches": matches})

    # The import list is cut to `max_import_results` files (0 lists all, the
    # Python default, which the deletion investigator reads); the MCP tool
    # defaults to 50. The count and the verdict come from the whole list,
    # never the page. Sorted by file first: `index.imports` follows the
    # store's row order, so an unsorted cut would keep a different 50 files
    # after a re-index, and a larger cap would not extend a smaller one.
    import_references.sort(key=lambda ref: ref["file"])
    import_count = len(import_references)
    import_references_truncated = 0 < max_import_results < import_count
    if import_references_truncated:
        import_references = import_references[:max_import_results]

    # ── Content-level check ─────────────────────────────────────────────────
    # Exclude the definition's own LINE SPAN, not its whole file (#406,
    # @rknighton).
    #
    # ⚠⚠ The intent — "finding the name in the defining file is not a
    # reference" — was right; the implementation discarded the file rather than
    # the definition, so every SAME-FILE call site was lost and this tool
    # answered `is_referenced: false` for symbols that are demonstrably called.
    # Single-file modules were hit hardest: a helper defined and used in one
    # module read as dead code, which is the exact question this tool exists to
    # answer.
    #
    # A list of spans per file, not one span: 102 duplicate `(file, name)` pairs
    # were measured on a real 1837-symbol index (overloads, nested defs), so one
    # span per file would leave the others counting their own signatures.
    #
    # A self-recursive call sits inside the definition span and stays excluded,
    # which is correct for the dead-code question — a function calling itself is
    # not evidence anyone else uses it.
    # ⚠ `unspanned_files` is the third bucket, and it is not optional. A symbol
    # carrying no usable `line` gives us nothing to exclude, so counting its
    # file would report the DEFINITION as a reference to itself — trading this
    # fix's false negative for a false positive on exactly the indexes with the
    # least metadata (older indexes, and any writer that omits `line`). Where
    # the evidence is missing we degrade to the pre-v1.108.226 behaviour and
    # skip the file, which is the honest answer rather than a guess.
    defining_spans: dict[str, list[tuple[int, int]]] = {}
    # The stored signature of each definition, by (file, first line): the
    # bound on a sibling's declaration (`_name_through`, LEDGER L-331).
    signatures: dict[tuple[str, int], str] = {}
    unspanned_files: set[str] = set()
    for sym in index.symbols:
        if sym.get("name", "") != ident_exact:
            continue
        file_path = sym.get("file", "")
        if not file_path:
            continue
        start_line = sym.get("line")
        if not start_line:
            unspanned_files.add(file_path)
            continue
        try:
            lo = int(start_line)
            hi = int(sym.get("end_line") or lo)
        except (TypeError, ValueError):
            unspanned_files.add(file_path)
            continue
        # An inverted or absent span must still hide the declaration line
        # itself; `lo > hi` would match nothing and report the signature as a
        # reference to itself.
        defining_spans.setdefault(file_path, []).append((lo, max(lo, hi)))
        signatures.setdefault((file_path, lo), str(sym.get("signature") or ""))

    # ⚠⚠ (LEDGER L-91) A caller that knows WHICH symbol it asks about excludes
    # that symbol's whole span, and of every other same-named definition only
    # its DECLARATION: from its first line through the first line that names
    # the identifier (`_name_through`: by its exact spelling as a token
    # within the stored signature's lines, LEDGER L-331), so an `@Override` or
    # `[Attr]` line above it does not leave the name line counting. Hiding each sibling's
    # whole body hid real callers: Java `B.file()` calling `new A().file()`
    # was skipped as "a definition of `file`", and `check_delete_safe` graded
    # the used `A.file` `safe_to_delete` with zero blockers. Hiding nothing of
    # the siblings counted their declarations as uses instead (a partial
    # class, an overload's signature, `def show` in another controller) and
    # moved pinned verdicts. The narrower rule only ADDS matches, so it can
    # hold a delete back and never licenses one. A sibling with no line no
    # longer hides its whole file: a call there counts, at the cost of its
    # declaration. A sibling's NAME line counts when it calls the name
    # beyond its declarations, as a one-line method does
    # (`_sibling_declarations`, LEDGER
    # L-329).
    sibling_spans: dict[str, list[tuple[int, int, str]]] = {}
    if target_span is not None:
        t_file, t_lo, t_hi = target_span
        t_hi = max(t_lo, t_hi)
        for file_path, spans in defining_spans.items():
            for lo, hi in spans:
                if file_path == t_file and lo == t_lo:
                    continue
                sibling_spans.setdefault(file_path, []).append(
                    (lo, hi, signatures.get((file_path, lo), ""))
                )
        defining_spans = {t_file: [(t_lo, t_hi)]}
        unspanned_files = set()

    content_references = []
    # True once a matching file beyond the cap is seen: the scan reads one
    # matching file past `max_content_results` to know the page was cut.
    content_truncated = False

    if search_content:
        content_dir = store._content_dir(owner, name)
        for file_path in index.source_files:
            if file_path in unspanned_files:
                continue
            spans = defining_spans.get(file_path, ())

            full_path = store._safe_content_path(content_dir, file_path)
            if not full_path or not full_path.exists():
                continue

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    content = f.read()
            except OSError:
                continue

            file_lines = content.split("\n")
            siblings = sibling_spans.get(file_path)
            if siblings:
                spans = list(spans) + _sibling_declarations(
                    file_lines, siblings, ident_lower, ident_exact
                )

            file_matches = []
            for line_index, line in enumerate(file_lines):
                if ident_lower not in _fold(line):
                    continue
                line_no = line_index + 1
                if any(lo <= line_no <= hi for lo, hi in spans):
                    continue
                file_matches.append({
                    "line": line_no,
                    "text": line.rstrip()[:200],
                })

            if file_matches:
                # Stop after N files, not N lines, and say so when a file was left out.
                if len(content_references) >= max_content_results:
                    content_truncated = True
                    break
                content_references.append({"file": file_path, "matches": file_matches})

    content_count = len(content_references)
    attach_enclosing_symbols(content_references, index.symbols)

    elapsed = (time.perf_counter() - start) * 1000
    is_referenced = import_count > 0 or content_count > 0

    result = {
        "repo": f"{owner}/{name}",
        "identifier": identifier,
        "is_referenced": is_referenced,
        "import_count": import_count,
        "import_references": import_references,
        "import_references_total": import_count,
        "import_references_truncated": import_references_truncated,
        "content_count": content_count,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }

    if search_content:
        result["content_references"] = content_references
        result["content_truncated"] = content_truncated

    return result


def _check_batch(
    identifiers: list[str],
    index,
    search_content: bool,
    max_content_results: int,
    owner: str,
    name: str,
    store: "IndexStore",
    start: float,
    max_import_results: int = 0,
) -> dict:
    """Batch logic: loop over identifiers, return grouped results array."""
    results = []
    for identifier in identifiers:
        result = _check_single(
            identifier=identifier,
            index=index,
            search_content=search_content,
            max_content_results=max_content_results,
            owner=owner,
            name=name,
            store=store,
            start=start,
            max_import_results=max_import_results,
        )
        # Strip envelope fields for consistency with other batch tools
        result.pop("repo", None)
        result.pop("_meta", None)
        results.append(result)

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "results": results,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "identifiers_checked": len(identifiers),
        },
    }


def check_references(
    repo: str,
    identifier: Optional[str] = None,
    identifiers: Optional[list[str]] = None,
    search_content: bool = True,
    max_content_results: int = 20,
    storage_path: Optional[str] = None,
    max_import_results: int = 0,
) -> dict:
    """Check if an identifier is referenced anywhere: imports + file content.

    Combines find_references and search_text into one call. Answers
    "is this identifier used anywhere?" for quick dead-code detection.

    Supports two modes:
    - Singular: pass ``identifier`` to get the original flat response shape.
    - Batch: pass ``identifiers`` (list) to query multiple identifiers at once,
      returning a grouped ``results`` array.

    Args:
        repo: Repository identifier (owner/repo or display name).
        identifier: The symbol/module name to check (singular mode).
        identifiers: List of symbol/module names to check (batch mode).
        search_content: Also search file contents (not just imports).
            Set False for fast import-only check.
        max_content_results: Max files to return per identifier for content search.
        storage_path: Custom storage path.
        max_import_results: Max importing files to list per identifier; 0 or
            less lists all (the default here). ``import_count`` and
            ``import_references_total`` are always the whole count, and
            ``import_references_truncated`` says whether any were left out.

    Returns:
        Singular mode: dict with is_referenced, import/content counts, and
            reference lists.
        Batch mode: dict with ``results`` array (one entry per identifier).
    """
    # Normalize: some MCP clients send identifiers=[] alongside identifier when they mean singular mode
    if identifier is not None and identifiers is not None and len(identifiers) == 0:
        identifiers = None
    if (identifier is None and identifiers is None) or (identifier is not None and identifiers is not None):
        from ._arg_contract import ArgumentError

        raise ArgumentError("Provide exactly one of 'identifier' or 'identifiers', not both and not neither.")

    start = time.perf_counter()
    max_content_results = max(1, min(max_content_results, 100))
    max_import_results = max(0, int(max_import_results or 0))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if identifiers is not None:
        return _check_batch(
            identifiers,
            index,
            search_content,
            max_content_results,
            owner,
            name,
            store,
            start,
            max_import_results=max_import_results,
        )
    else:
        return _check_single(
            identifier=identifier,  # type: ignore[arg-type]  # validated above
            index=index,
            search_content=search_content,
            max_content_results=max_content_results,
            owner=owner,
            name=name,
            store=store,
            start=start,
            max_import_results=max_import_results,
        )
