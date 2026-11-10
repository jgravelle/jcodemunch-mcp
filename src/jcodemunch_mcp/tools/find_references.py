"""Find all files that reference (import) a given identifier."""

import posixpath
import time
from typing import Optional

from ..parser.imports import wildcard_edge_filter
from ..storage import IndexStore, result_cache_get, result_cache_put
from ..storage.generation import connect_readonly
from ._utils import index_status_to_tool_error, resolve_repo


def _build_import_name_index(index) -> dict[str, list[tuple[str, dict]]]:
    """Build inverted index: lowered_name -> [(src_file, imp), ...].

    Indexes both named imports and specifier stems so lookups are O(1).
    Built once per CodeIndex, cached on index._import_name_index.
    """
    inv: dict[str, list[tuple[str, dict]]] = {}
    dead_wildcard = wildcard_edge_filter(index)
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            if dead_wildcard(imp, src_file):
                continue
            for n in imp.get("names", []):
                inv.setdefault(n.lower(), []).append((src_file, imp))
            spec_stem = posixpath.splitext(posixpath.basename(imp["specifier"]))[0].lower()
            if spec_stem:
                inv.setdefault(spec_stem, []).append((src_file, imp))
    return inv


def _get_import_index(index) -> dict[str, list[tuple[str, dict]]]:
    """Return the cached import name index, building it on first call."""
    if index._import_name_index is None:
        index._import_name_index = _build_import_name_index(index)
    return index._import_name_index


def _find_import_line(content: str, specifier: str) -> Optional[int]:
    """Return the 1-based line number where ``specifier`` first appears in
    quotes (single, double, or backtick) in ``content``.

    Heuristic — matches ``from 'X' import``, ``import 'X'``, ``require('X')``,
    ``import('X')``, ``from "X"``, etc. Robust to whitespace and quote style.
    Returns None if the specifier isn't found in any quoting context.
    """
    if not content or not specifier:
        return None
    needles = (f'"{specifier}"', f"'{specifier}'", f"`{specifier}`")
    for idx, line in enumerate(content.splitlines(), start=1):
        for needle in needles:
            if needle in line:
                return idx
    return None


def _calling_symbols_in_file(
    index,
    store,
    owner: str,
    repo_name: str,
    src_file: str,
    identifier: str,
) -> list[dict]:
    """Return symbols in *src_file* whose bodies mention *identifier*.

    Used to populate the optional ``calling_symbols`` field when
    ``include_call_chain=True``.  Each result is ``{id, name, kind, line}``.
    """
    from ._call_graph import _word_match, _symbol_body

    # Lazy: build symbols_by_file only once per call_chain enrichment pass.
    # We do it here inline to keep the function self-contained; callers can
    # pass a pre-built map if they need efficiency across multiple files.
    file_content = store.get_file_content(owner, repo_name, src_file)
    if not file_content:
        return []
    if not _word_match(file_content, identifier):
        return []

    file_lines = file_content.splitlines()
    syms_in_file = [s for s in index.symbols if s.get("file") == src_file]
    results: list[dict] = []
    seen: set[str] = set()

    for sym in syms_in_file:
        sid = sym.get("id", "")
        if not sid or sid in seen or not sym.get("line"):
            continue
        body = _symbol_body(file_lines, sym)
        if body and _word_match(body, identifier):
            seen.add(sid)
            results.append({
                "id": sid,
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "line": sym.get("line", 0),
            })

    return results


def _find_references_single(
    identifier: str,
    index,
    max_results: int,
    owner: str,
    name: str,
    start: float,
    include_call_chain: bool = False,
    store=None,
) -> dict:
    """Core logic for a single identifier query. Returns the original flat shape."""
    if index.imports is None:
        return {
            "repo": f"{owner}/{name}",
            "identifier": identifier,
            "references": [],
            "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_references.",
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    ident_lower = identifier.lower()
    inv = _get_import_index(index)
    entries = inv.get(ident_lower, [])

    # Group by file, dedup, and classify match type
    file_matches: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()  # (src_file, specifier) dedup
    for src_file, imp in entries:
        spec = imp["specifier"]
        key = (src_file, spec)
        if key in seen:
            continue
        seen.add(key)

        named_match = any(n.lower() == ident_lower for n in imp.get("names", []))
        spec_stem = posixpath.splitext(posixpath.basename(spec))[0].lower()
        stem_match = spec_stem == ident_lower

        if named_match or stem_match:
            file_matches.setdefault(src_file, []).append({
                "specifier": spec,
                "names": imp.get("names", []),
                "match_type": "named" if named_match else "specifier_stem",
            })

    results = [{"file": f, "matches": m} for f, m in file_matches.items()]
    results.sort(key=lambda r: r["file"])

    # Slice BEFORE enrichment, not in the return statement (#394, @rknighton).
    # The sort above fully determines which rows ship, so every file read below
    # for a row past `max_results` was work whose product got thrown away —
    # 1,525 of 1,575 reads on a `django/django` query, 3.5s -> 0.95s. The
    # response is byte-identical because `reference_count` and `_meta.truncated`
    # are still computed from the FULL `results`, which is why the two lists must
    # stay separate: `visible` is what we serve, `results` is what we found.
    visible = results[:max_results]

    # Enrich each match with the line number of its import statement so
    # downstream consumers (regex harvesters, IDE deeplinks, code review
    # bots) can jump straight to the import site instead of opening the
    # file and grepping. Heuristic: first line where the specifier
    # appears quoted. Skipped silently when file content is unavailable
    # (remote-only indexes); existing callers see additive `line` field.
    if store is not None:
        for ref in visible:
            try:
                content = store.get_file_content(owner, name, ref["file"])
            except Exception:
                content = None
            if not content:
                continue
            for match in ref["matches"]:
                line = _find_import_line(content, match.get("specifier", ""))
                if line is not None:
                    match["line"] = line

    # Optional: enrich each reference with which symbols in that file call the identifier
    if include_call_chain and store is not None:
        for ref in visible:
            ref["calling_symbols"] = _calling_symbols_in_file(
                index, store, owner, name, ref["file"], identifier
            )

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "identifier": identifier,
        "reference_count": len(results),
        "references": visible,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "truncated": len(results) > max_results,
            "tip": "Tip: use identifiers=[...] to query multiple identifiers in one call. "
                   "For usage-site matching beyond imports, also try search_text or check_references.",
        },
    }


def _find_references_batch(
    identifiers: list[str],
    index,
    max_results: int,
    owner: str,
    name: str,
    start: float,
) -> dict:
    """Batch logic: loop over identifiers, return grouped results array."""
    if index.imports is None:
        return {
            "repo": f"{owner}/{name}",
            "results": [
                {
                    "identifier": ident,
                    "references": [],
                    "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_references.",
                }
                for ident in identifiers
            ],
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    inv = _get_import_index(index)

    # Pre-compute lowercased query identifiers
    query_stems = {ident.lower() for ident in identifiers}

    # Build reverse map: identifier_lower -> dict of file -> entry (deduped per file)
    # First-match-wins on match_type: "named" takes priority since it represents
    # a more specific import name match.
    ident_map: dict[str, dict[str, dict]] = {}
    for ident_lower in query_stems:
        entries = inv.get(ident_lower, [])
        for src_file, imp in entries:
            if src_file not in ident_map.get(ident_lower, {}):
                named_match = any(n.lower() == ident_lower for n in imp.get("names", []))
                match_type = "named" if named_match else "specifier_stem"
                ident_map.setdefault(ident_lower, {})[src_file] = {
                    "file": src_file, "specifier": imp["specifier"], "match_type": match_type,
                }

    results = []
    for identifier in identifiers:
        ident_lower = identifier.lower()
        file_results = list(ident_map.get(ident_lower, {}).values())
        file_results.sort(key=lambda r: r["file"])
        results.append({
            "identifier": identifier,
            "reference_count": len(file_results),
            "references": file_results[:max_results],
        })

    return {
        "repo": f"{owner}/{name}",
        "results": results,
        "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
    }


def _attach_runtime_to_response(response: dict, store, owner: str, name: str) -> dict:
    """Phase 2: stamp file-level runtime confidence on every reference in
    a find_references response (single or batch mode). No-op when no traces.
    """
    from ..runtime.confidence import attach_runtime_confidence_by_file
    refs: list[dict] = []
    if "references" in response:
        # Singular mode
        refs.extend(response["references"])
    if "results" in response:
        # Batch mode
        for entry in response.get("results", []):
            refs.extend(entry.get("references", []))
    if not refs:
        return response
    summary = attach_runtime_confidence_by_file(
        refs,
        str(store._sqlite._db_path(owner, name)),
        file_field="file",
    )
    if summary:
        response.setdefault("_meta", {})["runtime_freshness"] = summary
    return response


def _attach_scip_to_response(response: dict, store, owner: str, name: str) -> dict:
    """Stamp compile-time (SCIP) evidence on a find_references response.

    When the repo has ingested SCIP data (`jcodemunch-mcp import-scip`):
    files whose symbols carry a compiler-verified reference to the queried
    identifier gain ``verification: "compiler_verified"``, and files the
    compiler proved but the import graph missed (dynamic dispatch, barrel
    re-exports) are appended as ``source: "scip"`` rows. ``_meta.scip``
    summarises counts + staleness. Byte-identical no-op when no SCIP data
    has been ingested (honest-empty), including on pre-v17 databases.

    Idempotent across the result cache: re-attaching to an already-annotated
    response neither duplicates rows nor changes counts.
    """
    import sqlite3

    db_path = store._sqlite._db_path(owner, name)
    try:
        conn = connect_readonly(db_path, isolation_level="")
    except sqlite3.Error:
        return response
    conn.row_factory = sqlite3.Row
    try:
        try:
            if conn.execute("SELECT 1 FROM scip_edges LIMIT 1").fetchone() is None:
                return response
        except sqlite3.OperationalError:
            # Pre-v17 database that has never been migrated or ingested.
            return response

        scip_meta = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM scip_meta").fetchall()
        }
        head_row = conn.execute(
            "SELECT value FROM meta WHERE key = 'git_head'"
        ).fetchone()
        live_head = head_row["value"] if head_row and head_row["value"] else ""
        ingest_head = scip_meta.get("git_head", "")
        stale = bool(ingest_head and live_head and ingest_head != live_head)

        def _verified_files(identifier: str) -> dict[str, int]:
            rows = conn.execute(
                """
                SELECT s_from.file AS ref_file, SUM(e.count) AS n
                FROM scip_edges e
                JOIN symbols s_to ON s_to.id = e.to_symbol_id
                JOIN symbols s_from ON s_from.id = e.from_symbol_id
                WHERE e.kind = 'reference' AND s_to.name = ? COLLATE NOCASE
                GROUP BY s_from.file
                """,
                (identifier,),
            ).fetchall()
            return {row["ref_file"]: row["n"] for row in rows}

        # (identifier, holder-dict) pairs for singular and batch shapes
        entries: list[tuple[Optional[str], dict]] = []
        if "references" in response and "identifier" in response:
            entries.append((response.get("identifier"), response))
        for entry in response.get("results", []) or []:
            entries.append((entry.get("identifier"), entry))

        verified_count = 0
        scip_only_count = 0
        touched = False
        for identifier, holder in entries:
            if not identifier:
                continue
            files = _verified_files(identifier)
            if not files:
                continue
            touched = True
            refs = holder.get("references", [])
            import_refs = [r for r in refs if r.get("source") != "scip"]
            import_files = {r.get("file") for r in import_refs}
            already_appended = {r.get("file") for r in refs if r.get("source") == "scip"}
            for ref in import_refs:
                if ref.get("file") in files:
                    ref["verification"] = "compiler_verified"
                    verified_count += 1
            scip_only = sorted(f for f in files if f not in import_files)
            scip_only_count += len(scip_only)
            for f in scip_only:
                if f in already_appended:
                    continue
                holder.setdefault("references", []).append({
                    "file": f,
                    "matches": [{"match_type": "scip_reference", "names": [identifier]}],
                    "verification": "compiler_verified",
                    "source": "scip",
                })
            if "reference_count" in holder:
                holder["reference_count"] = len(holder.get("references", []))

        if touched:
            scip_block: dict = {
                "verified_files": verified_count,
                "scip_only_files": scip_only_count,
                "tool": scip_meta.get("tool", ""),
                "ingested_at": scip_meta.get("ingested_at", ""),
                "stale": stale,
            }
            if stale:
                scip_block["note"] = (
                    "SCIP evidence was ingested at a different index HEAD. "
                    "Re-run your SCIP indexer and `jcodemunch-mcp import-scip` "
                    "after reindexing."
                )
            response.setdefault("_meta", {})["scip"] = scip_block
    except sqlite3.Error:
        return response
    finally:
        conn.close()
    return response


def find_references(
    repo: str,
    identifier: Optional[str] = None,
    max_results: int = 50,
    storage_path: Optional[str] = None,
    identifiers: Optional[list[str]] = None,
    include_call_chain: bool = False,
) -> dict:
    """Find all indexed files that import or reference an identifier.

    Supports two modes:
    - Singular: pass ``identifier`` to get the original flat response shape.
    - Batch: pass ``identifiers`` (list) to query multiple identifiers at once,
      returning a grouped ``results`` array.

    Args:
        repo: Repository identifier (owner/repo or display name).
        identifier: The symbol/module name to look for (singular mode, e.g. 'bulkImport').
        max_results: Maximum number of results.
        storage_path: Custom storage path.
        identifiers: List of symbol/module names to look for (batch mode).
        include_call_chain: When True (singular mode only), each reference entry gains a
            ``calling_symbols`` list of symbols in that file whose bodies mention the
            identifier. Gated off by default; batch mode ignores this flag.

    Returns:
        Singular mode: dict with flat ``references`` list and _meta envelope.
        Batch mode: dict with ``results`` array (one entry per input identifier).

    Raises:
        ValueError: if neither or both of identifier and identifiers are provided.
    """
    # Normalize: some MCP clients send identifiers=[] alongside identifier when they mean singular mode
    if identifier is not None and identifiers is not None and len(identifiers) == 0:
        identifiers = None
    if (identifier is None and identifiers is None) or (identifier is not None and identifiers is not None):
        raise ValueError("Provide exactly one of 'identifier' or 'identifiers', not both and not neither.")

    start = time.perf_counter()
    max_results = max(1, min(max_results, 200))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if identifiers is not None:
        result = _find_references_batch(identifiers, index, max_results, owner, name, start)
        return _attach_scip_to_response(
            _attach_runtime_to_response(result, store, owner, name),
            store, owner, name,
        )
    else:
        repo_key = f"{owner}/{name}"
        specific_key = (identifier, max_results, include_call_chain)
        cached = result_cache_get("find_references", repo_key, specific_key)
        if cached is not None:
            result = dict(cached)
            result["_meta"] = {**cached["_meta"],
                               "timing_ms": round((time.perf_counter() - start) * 1000, 1),
                               "cache_hit": True}
            return _attach_scip_to_response(
                _attach_runtime_to_response(result, store, owner, name),
                store, owner, name,
            )
        result = _find_references_single(
            identifier, index, max_results, owner, name, start,
            include_call_chain=include_call_chain,
            store=store,
        )
        result_cache_put("find_references", repo_key, specific_key, result)
        return _attach_scip_to_response(
            _attach_runtime_to_response(result, store, owner, name),
            store, owner, name,
        )
