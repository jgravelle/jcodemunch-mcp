"""Check if an identifier is referenced anywhere: imports + file content.

Combines find_references and search_text into one call.
Answers "is this identifier used anywhere?" for quick dead-code detection.
"""

import posixpath
import time
import unicodedata
from typing import Optional

from ..storage import IndexStore
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

    ASCII is returned as `lower()` without normalising: NFKC is the
    identity there, and this runs on every line of every file.
    """
    if text.isascii():
        return text.lower()
    return unicodedata.normalize("NFKC", text).lower()


def _check_single(
    identifier: str,
    index,
    search_content: bool,
    max_content_results: int,
    owner: str,
    name: str,
    store: "IndexStore",
    start: float,
) -> dict:
    """Core logic for checking a single identifier against import + content data."""
    ident_lower = _fold(identifier)
    # The exclusion's key: exact spelling, case-insensitive as before (see
    # `_fold`'s ⚠⚠ -- an exclusion must never widen).
    ident_exact = identifier.lower()

    # ── Import-level check ──────────────────────────────────────────────────
    import_references = []
    if index.imports is not None:
        for src_file, file_imports in index.imports.items():
            matches = []
            for imp in file_imports:
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

    import_count = len(import_references)

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
    unspanned_files: set[str] = set()
    for sym in index.symbols:
        if sym.get("name", "").lower() != ident_exact:
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

    content_references = []

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

            file_matches = []
            for line_index, line in enumerate(content.split("\n")):
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
                content_references.append({"file": file_path, "matches": file_matches})
                # Stop after N files, not N lines
                if len(content_references) >= max_content_results:
                    break

    content_count = len(content_references)

    elapsed = (time.perf_counter() - start) * 1000
    is_referenced = import_count > 0 or content_count > 0

    result = {
        "repo": f"{owner}/{name}",
        "identifier": identifier,
        "is_referenced": is_referenced,
        "import_count": import_count,
        "import_references": import_references,
        "content_count": content_count,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }

    if search_content:
        result["content_references"] = content_references

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

    Returns:
        Singular mode: dict with is_referenced, import/content counts, and
            reference lists.
        Batch mode: dict with ``results`` array (one entry per identifier).
    """
    # Normalize: some MCP clients send identifiers=[] alongside identifier when they mean singular mode
    if identifier is not None and identifiers is not None and len(identifiers) == 0:
        identifiers = None
    if (identifier is None and identifiers is None) or (identifier is not None and identifiers is not None):
        raise ValueError("Provide exactly one of 'identifier' or 'identifiers', not both and not neither.")

    start = time.perf_counter()
    max_content_results = max(1, min(max_content_results, 100))

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
        )
