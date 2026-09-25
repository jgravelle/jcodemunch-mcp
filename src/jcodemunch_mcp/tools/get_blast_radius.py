"""Blast-radius analysis: find files affected by changing a symbol."""

import posixpath
import re
import time
from collections import deque
from typing import Optional

from ..storage import IndexStore, result_cache_get, result_cache_put
from ..parser.imports import resolve_specifier
from ..retrieval.verdict import (
    build_verdict,
    index_changed_since_load as _index_changed_since_load,
    index_coverage_meta,
)
from ._utils import index_status_to_tool_error, resolve_repo, resolve_fqn
from .package_registry import extract_root_package_from_specifier
from ._call_graph import build_symbols_by_file, bfs_callers
from .find_dead_code import _is_test_file
from .decision_context import resolve_decision_context
from ._scip_consume import open_scip_reader, scip_meta_and_stale, scip_meta_block


def _attach_scip_to_blast(result: dict, store, owner: str, name: str) -> dict:
    """Union SCIP compiler-verified reference files into a blast-radius result.

    Files whose symbols carry a compiler-verified reference to the focal symbol
    gain ``verification: "compiler_verified"`` on their ``confirmed`` entry; files
    the compiler proved but the import graph missed (dynamic dispatch, barrel
    re-exports) are appended to ``confirmed`` as ``source: "scip"`` rows.
    ``confirmed_count`` is refreshed and ``_meta.scip`` summarises. Byte-identical
    no-op when no SCIP data has been ingested; idempotent across the result cache
    (guarded so it never double-appends). ``importer_count`` is left untouched —
    it counts import-graph importers, and SCIP-only files are by definition the
    ones the import graph missed.
    """
    if "error" in result:
        return result
    sym = result.get("symbol") or {}
    target_id = sym.get("id") or ""
    target_name = sym.get("name") or ""
    if not target_id and not target_name:
        return result
    conn = open_scip_reader(store, owner, name)
    if conn is None:
        return result
    try:
        scip_meta, stale = scip_meta_and_stale(conn)
        if target_id:
            rows = conn.execute(
                """
                SELECT s_from.file AS ref_file, SUM(e.count) AS n
                FROM scip_edges e
                JOIN symbols s_from ON s_from.id = e.from_symbol_id
                WHERE e.kind = 'reference' AND e.to_symbol_id = ?
                GROUP BY s_from.file
                """,
                (target_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT s_from.file AS ref_file, SUM(e.count) AS n
                FROM scip_edges e
                JOIN symbols s_to ON s_to.id = e.to_symbol_id
                JOIN symbols s_from ON s_from.id = e.from_symbol_id
                WHERE e.kind = 'reference' AND s_to.name = ? COLLATE NOCASE
                GROUP BY s_from.file
                """,
                (target_name,),
            ).fetchall()
        files = {r["ref_file"]: r["n"] for r in rows if r["ref_file"]}
        # A symbol referencing itself in its own file is not an affected importer.
        files.pop(sym.get("file"), None)
        if not files:
            return result
        confirmed = result.setdefault("confirmed", [])
        import_files = {c.get("file") for c in confirmed if c.get("source") != "scip"}
        already_scip = {c.get("file") for c in confirmed if c.get("source") == "scip"}
        for c in confirmed:
            if c.get("source") != "scip" and c.get("file") in files:
                c["verification"] = "compiler_verified"
        for f in sorted(f for f in files if f not in import_files):
            if f in already_scip:
                continue
            confirmed.append({
                "file": f,
                "reference_count": files[f],
                "verification": "compiler_verified",
                "source": "scip",
            })
        result["confirmed_count"] = len(confirmed)
        # Counts derive from final list state → idempotent across the cache.
        verified_files = sum(
            1 for c in confirmed
            if c.get("source") != "scip" and c.get("verification") == "compiler_verified"
        )
        scip_only_files = sum(1 for c in confirmed if c.get("source") == "scip")
        result.setdefault("_meta", {})["scip"] = scip_meta_block(
            scip_meta, stale,
            verified_files=verified_files, scip_only_files=scip_only_files,
        )
    except Exception:
        return result
    finally:
        conn.close()
    return result


def _build_reverse_adjacency(
    imports: dict, source_files: frozenset, alias_map: Optional[dict] = None,
    psr4_map: Optional[dict] = None,
) -> dict[str, list[str]]:
    """Return {file: [files_that_import_it]} from raw import data."""
    rev: dict[str, list[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if target and target != src_file:
                rev.setdefault(target, []).append(src_file)
    # Deduplicate
    return {k: list(dict.fromkeys(v)) for k, v in rev.items()}


def _unresolved_package_edges(
    imports: dict,
    source_files: frozenset,
    sym_file: str,
    alias_map: Optional[dict] = None,
    psr4_map: Optional[dict] = None,
    sample_cap: int = 5,
) -> Optional[dict]:
    """Evidence that the importer graph cannot REACH ``sym_file``, not that nothing imports it.

    A file-level import graph resolves one specifier to one file. Languages that
    address imports at package/directory granularity (Go's ``import "mod/pkg"``
    names a directory of files, and files inside that package need no import at
    all) therefore produce edges this graph captures but cannot land on a member
    file. The importer set comes back empty for every symbol in the package, and
    that emptiness is a property of the resolver, not of the repository.

    Deliberately MEASURED rather than declared from a language table. An edge
    naming this symbol's own directory that resolved to nothing is direct
    evidence for this specific query; a hardcoded list of package-granular
    languages would be a claim about every repo, kept in sync by hand, and wrong
    the first time a resolver improves.

    Returns None when there is nothing to disclose — no such edge exists, or the
    symbol sits at the repo root where a directory name cannot discriminate.
    """
    target_dir = posixpath.dirname((sym_file or "").replace("\\", "/")).strip("/")
    if not target_dir:
        # A root-level file has no directory to match a specifier against, so any
        # answer here would rest on a coincidence rather than on evidence.
        return None
    tail = target_dir.rsplit("/", 1)[-1]

    exact: list[dict] = []
    loose: list[dict] = []
    for src_file, file_imports in (imports or {}).items():
        for imp in file_imports or []:
            spec = (imp.get("specifier") or "").replace("\\", "/").strip("/")
            if not spec:
                continue
            if resolve_specifier(
                imp.get("specifier") or "", src_file, source_files, alias_map, psr4_map
            ):
                continue  # the graph handled this one; it is not evidence of a gap
            if spec == target_dir or spec.endswith("/" + target_dir):
                exact.append({"importer": src_file, "specifier": imp.get("specifier")})
            elif spec.rsplit("/", 1)[-1] == tail:
                loose.append({"importer": src_file, "specifier": imp.get("specifier")})

    matched = exact or loose
    if not matched:
        return None

    # Sibling files in the same directory. For a package-granular language these
    # can call the symbol with no import statement at all, so they are invisible
    # to an import graph by construction — a second, independent reason the zero
    # cannot be read as absence.
    siblings = sum(
        1
        for f in source_files
        if f != sym_file
        and posixpath.dirname(f.replace("\\", "/")).strip("/") == target_dir
    )
    return {
        "reason": "package_granular_imports",
        "package_dir": target_dir,
        "match": "exact" if exact else "basename",
        "unresolved_edges": len(matched),
        "unresolved_sample": matched[:sample_cap],
        "same_package_files": siblings,
        "note": (
            f"No importer of '{sym_file}' could be resolved to a file, but "
            f"{len(matched)} import edge(s) name its package directory "
            f"'{target_dir}' and resolve to no file. This language addresses "
            "imports at package granularity, so the file-level import graph "
            "cannot reach this symbol"
            + (
                f", and {siblings} file(s) in the same package can call it with no "
                "import statement at all"
                if siblings
                else ""
            )
            + ". An empty result here is NOT evidence that nothing depends on this "
            "symbol. Use check_references for a text-backed answer."
        ),
    }


def blast_verdict(
    index, source_files: frozenset, sym_file: str, result_count: int
) -> tuple[dict, Optional[dict]]:
    """THE verdict on an importer walk from ``sym_file``: ``(verdict, unresolvable)``.

    One authority for every caller that walks the importer graph, because the
    walk alone cannot tell "nothing depends on this" from "the graph cannot
    reach this" (#415), and a second call site that ran the walk without asking
    shipped a bare ``[]`` that read as no downstream impact (#718,
    ``get_changed_symbols``). ``unresolvable`` is returned so a caller can
    withhold a number it would otherwise compute from the zero.

    ``result_count`` is what the caller found by every channel it ran; only an
    empty answer is probed, since a found importer is positive evidence.

    ⚠ ``file_not_in_index`` never fires for the standalone tool, whose symbol
    comes from the index. It exists for a caller holding a path the index never
    saw -- a file added since the indexed commit -- whose empty walk is a
    question the graph was never asked.
    """
    unresolvable: Optional[dict] = None
    if result_count == 0:
        if sym_file not in source_files:
            unresolvable = {
                "reason": "file_not_in_index",
                "file": sym_file,
                "note": (
                    f"'{sym_file}' is not in the index, so its importers were never "
                    "in the graph this walk read. An empty result here is NOT "
                    "evidence that nothing depends on it; re-index and ask again."
                ),
            }
        else:
            unresolvable = _unresolved_package_edges(
                index.imports,
                source_files,
                sym_file,
                index.alias_map,
                getattr(index, "psr4_map", None),
            )
    verdict = build_verdict(
        result_count=result_count,
        scanned_files=len(source_files),
        coverage=index_coverage_meta(index),
        # An empty answer measured while the .db was being rewritten underneath
        # this call cannot prove absence either, and that gate outranks ours: it
        # names something the caller can retry.
        index_changed=_index_changed_since_load(index),
        incomplete=unresolvable,
    )["verdict"]
    return verdict, unresolvable


def _bfs_importers(
    start: str, rev: dict[str, list[str]], depth: int
) -> tuple[list[str], dict[int, list[str]]]:
    """BFS over reverse graph; return (flat list, depth-bucketed dict) excluding start."""
    visited: set[str] = {start}
    queue: deque = deque([(start, 0)])
    result: list[str] = []
    by_depth: dict[int, list[str]] = {}
    while queue:
        node, level = queue.popleft()
        if level >= depth:
            continue
        for importer in rev.get(node, []):
            if importer not in visited:
                visited.add(importer)
                d = level + 1
                result.append(importer)
                by_depth.setdefault(d, []).append(importer)
                queue.append((importer, d))
    return result, by_depth


def _find_symbol(index, symbol: str) -> list[dict]:
    """Find symbols by ID or name. Returns all matches."""
    # Try exact ID first
    by_id = index.get_symbol(symbol)
    if by_id:
        return [by_id]
    # Exact name match
    exact = [s for s in index.symbols if s.get("name") == symbol]
    if exact:
        return exact
    # Case-insensitive fallback
    lower = symbol.lower()
    return [s for s in index.symbols if s.get("name", "").lower() == lower]


def _name_in_content(content: str, name: str) -> bool:
    """Return True if name appears as a word token in content."""
    return bool(re.search(r"\b" + re.escape(name) + r"\b", content))


def _extract_reference_snippets(content: str, name: str) -> list[dict]:
    """Extract lines where ``name`` appears as a word token.

    Returns list of {"line": int, "text": str} dicts.
    """
    snippets: list[dict] = []
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")
    for i, line in enumerate(content.splitlines()):
        if pattern.search(line):
            snippets.append({"line": i + 1, "text": line.rstrip()})
    return snippets


def _get_symbols_near_references(
    syms_by_file: dict, file_path: str, snippet_lines: list[int]
) -> list[dict]:
    """Return symbols from *file_path* that contain or neighbour any snippet line.

    *syms_by_file* must be pre-built via ``build_symbols_by_file(index)`` so
    callers can reuse a single dict across many files.

    Returns compact dicts: {name, kind, line, signature}.
    """
    file_syms = syms_by_file.get(file_path, [])
    result: list[dict] = []
    seen: set[str] = set()
    for sym in file_syms:
        sym_start = sym.get("line", 0)
        sym_end = sym.get("end_line", sym_start)
        for ref_line in snippet_lines:
            if (sym_start <= ref_line <= sym_end) or abs(ref_line - sym_start) <= 5:
                sid = sym.get("id", sym.get("name"))
                if sid not in seen:
                    seen.add(sid)
                    result.append({
                        "name": sym.get("name", ""),
                        "kind": sym.get("kind", ""),
                        "line": sym_start,
                        "signature": sym.get("signature", ""),
                    })
                break
    return result


def get_blast_radius(
    repo: str,
    symbol: str,
    depth: int = 1,
    include_depth_scores: bool = False,
    storage_path: Optional[str] = None,
    cross_repo: Optional[bool] = None,
    call_depth: int = 0,
    fqn: Optional[str] = None,
    decorator_filter: Optional[str] = None,
    include_source: bool = False,
    source_budget: int = 8000,
    include_decisions: bool = False,
) -> dict:
    """Find all files that would be affected if a symbol's signature or behaviour changed.

    Uses two-stage analysis:
      1. Dependency graph — collect every file that (transitively) imports the
         file that defines ``symbol`` up to ``depth`` hops.
      2. Text scan — check whether each importing file actually mentions the
         symbol by name.  Files that do are ``confirmed`` references; files that
         import the module but don't name the symbol are ``potential`` references
         (e.g. wildcard / namespace imports).

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        symbol: Symbol name or ID to analyse.
        depth: Import hops to traverse (1 = direct importers only; max 3).
        call_depth: Call-graph hops for caller detection (0 = disabled; max 3).
                    When > 0, adds a ``callers`` list of calling symbols with depth scores.
        storage_path: Custom storage path.
        decorator_filter: Optional case-insensitive substring filter. When set,
            only confirmed files containing a symbol with a matching decorator
            are returned (e.g. ``"route"`` matches ``@route('/users')``).
        include_source: When True, each confirmed entry includes ``source_snippets``
            (lines referencing the symbol) and ``symbols_in_file`` (nearby symbol
            signatures).  Enables fix-ready context in one call.
        source_budget: Max tokens for source snippets across all files (default 8000).
            Files are prioritised by reference count.
        include_decisions: When True, attach a read-only ``decisions`` block —
            decision-bearing commits (revert/perf/refactor/rename/bugfix) mined
            from the git history of the focal symbol's file and the confirmed
            affected files, plus a volatility read. Surface-only; nothing is
            persisted. Default False (it spends a few git-log calls).

    Returns:
        Dict with symbol info, confirmed/potential affected files, counts, and _meta.
        When call_depth > 0: also includes ``callers`` and ``caller_count``.
    """
    # FQN resolution: translate PHP FQN → symbol name/id
    if fqn:
        _resolved, _ = resolve_fqn(repo, fqn, storage_path)
        if _resolved:
            symbol = _resolved

    depth = max(1, min(depth, 3))
    call_depth = max(0, min(call_depth, 3))
    start = time.perf_counter()

    # Resolve cross_repo default from config if not explicitly provided
    if cross_repo is None:
        from .. import config as _cfg
        cross_repo = bool(_cfg.get("cross_repo_default", False))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    # Store constructed before the cache check so the cache-hit path can also
    # attach SCIP evidence (construction is cheap and side-effect-free).
    store = IndexStore(base_path=storage_path)

    # Check session cache before the expensive BFS + content scans
    repo_key = f"{owner}/{name}"
    specific_key = (symbol, depth, call_depth, bool(cross_repo), include_depth_scores, decorator_filter, include_source, source_budget, include_decisions)
    cached = result_cache_get("get_blast_radius", repo_key, specific_key)
    if cached is not None:
        result = dict(cached)
        result["_meta"] = {**cached.get("_meta", {}),
                           "timing_ms": round((time.perf_counter() - start) * 1000, 1),
                           "cache_hit": True}
        return _attach_scip_to_blast(result, store, owner, name)

    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if index.imports is None:
        return {
            "error": (
                "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 "
                "to enable blast radius analysis."
            )
        }

    # Resolve symbol
    matches = _find_symbol(index, symbol)
    if not matches:
        return {"error": f"Symbol not found: '{symbol}'. Try search_symbols first."}
    if len(matches) > 1:
        # Multiple definitions (e.g. overloads in different files) — report all
        ambiguous = [{"name": s["name"], "file": s["file"], "id": s["id"]} for s in matches]
        return {
            "error": (
                f"Ambiguous symbol '{symbol}': found {len(matches)} definitions. "
                "Use the symbol 'id' field to disambiguate."
            ),
            "candidates": ambiguous,
        }

    sym = matches[0]
    sym_name: str = sym["name"]
    sym_file: str = sym["file"]

    # Build reverse adjacency (importer graph)
    source_files = frozenset(index.source_files)
    rev = _build_reverse_adjacency(index.imports, source_files, index.alias_map, getattr(index, "psr4_map", None))

    # BFS to collect all importing files
    importer_files, files_by_depth = _bfs_importers(sym_file, rev, depth)

    # Text-scan each importer for the symbol name
    confirmed: list[dict] = []
    potential: list[dict] = []
    content_cache: dict[str, str] = {}

    for imp_file in importer_files:
        content = store.get_file_content(owner, name, imp_file)
        if content is not None:
            content_cache[imp_file] = content
        if content is None:
            potential.append({"file": imp_file, "reason": "content unavailable"})
            continue
        if _name_in_content(content, sym_name):
            # Count occurrences for extra signal
            count = len(re.findall(r"\b" + re.escape(sym_name) + r"\b", content))
            confirmed.append({"file": imp_file, "references": count})
        else:
            potential.append({"file": imp_file, "reason": "symbol name not found (may use namespace/wildcard import)"})

    confirmed.sort(key=lambda x: x["file"])
    potential.sort(key=lambda x: x["file"])

    # Build symbols-by-file once if needed by decorator_filter or include_source
    _need_syms_by_file = bool(decorator_filter) or (include_source and confirmed and source_budget > 0)
    syms_by_file = build_symbols_by_file(index) if _need_syms_by_file else {}

    # Post-filter by decorator: keep only confirmed files that contain a symbol with the matching decorator
    if decorator_filter:
        filtered_confirmed = []
        for entry in confirmed:
            imp_file = entry["file"]
            file_symbols = syms_by_file.get(imp_file, [])
            if any(
                any(decorator_filter.lower() in d.lower() for d in (s.get("decorators") or []))
                for s in file_symbols
            ):
                filtered_confirmed.append(entry)
        confirmed = filtered_confirmed

    # Enrich confirmed entries with source snippets (optional)
    if include_source:
        # Ensure consistent shape: every confirmed entry gets these keys
        for entry in confirmed:
            entry.setdefault("source_snippets", [])
            entry.setdefault("symbols_in_file", [])
        if confirmed and source_budget > 0:
            budget_remaining = source_budget
            # Sort by reference count descending — most-referenced files first
            confirmed.sort(key=lambda x: x.get("references", 0), reverse=True)
            for entry in confirmed:
                if budget_remaining <= 0:
                    break
                content = content_cache.get(entry["file"])
                if not content:
                    continue
                snippets = _extract_reference_snippets(content, sym_name)
                # Rough token estimate: ~4 chars per token
                snippet_tokens = sum(len(s["text"]) // 4 + 1 for s in snippets)
                if snippet_tokens > budget_remaining:
                    kept: list[dict] = []
                    for s in snippets:
                        t = len(s["text"]) // 4 + 1
                        if t > budget_remaining:
                            break
                        kept.append(s)
                        budget_remaining -= t
                    snippets = kept
                else:
                    budget_remaining -= snippet_tokens
                entry["source_snippets"] = snippets
                snippet_lines = [s["line"] for s in snippets]
                entry["symbols_in_file"] = _get_symbols_near_references(
                    syms_by_file, entry["file"], snippet_lines
                )
            # Re-sort by file path for stable output
            confirmed.sort(key=lambda x: x["file"])

    # Enrich confirmed entries with test-reachability signal
    # For each confirmed file, check if any test file imports it AND
    # references sym_name — lightweight per-file check, no BFS.
    test_file_set = frozenset(f for f in index.source_files if _is_test_file(f))
    for entry in confirmed:
        imp_file = entry["file"]
        # Which test files import imp_file?
        test_importers = [f for f in rev.get(imp_file, []) if f in test_file_set]
        if not test_importers:
            entry["has_test_reach"] = False
            continue
        # Any test file reference the affected symbol by name?
        reached = False
        for tf in test_importers:
            tf_content = content_cache.get(tf) or store.get_file_content(owner, name, tf)
            if tf_content and _name_in_content(tf_content, sym_name):
                reached = True
                break
        entry["has_test_reach"] = reached

    # Cross-repo: find other repos that import this repo's package
    cross_repo_confirmed: list[dict] = []
    if cross_repo:
        try:
            from .list_repos import list_repos
            all_repos_data = list_repos(storage_path=storage_path).get("repos", [])
            pkg_names = getattr(index, "package_names", []) or []
            if pkg_names:
                for repo_entry in all_repos_data:
                    other_repo_id = repo_entry.get("repo", "")
                    if not other_repo_id or other_repo_id == f"{owner}/{name}" or "/" not in other_repo_id:
                        continue
                    other_owner, other_name = other_repo_id.split("/", 1)
                    other_index = store.load_index(other_owner, other_name)
                    if not other_index or not other_index.imports:
                        continue
                    for src_file, file_imports in other_index.imports.items():
                        for imp in file_imports:
                            specifier = imp.get("specifier", "")
                            lang = other_index.file_languages.get(src_file, "")
                            root_pkg = extract_root_package_from_specifier(specifier, lang)
                            if root_pkg and root_pkg in pkg_names:
                                cross_repo_confirmed.append({
                                    "file": src_file,
                                    "cross_repo": True,
                                    "source_repo": other_repo_id,
                                    "references": 1,
                                })
                                break
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).debug("cross_repo blast radius failed", exc_info=True)

    # Risk scoring (always computed, cheap)
    total = len(importer_files)
    direct_count = len(files_by_depth.get(1, []))
    if total > 0:
        overall_risk = sum(
            (1.0 / (d ** 0.7)) * len(files)
            for d, files in files_by_depth.items()
        ) / total
    else:
        overall_risk = 0.0

    # Call-level analysis (optional, gated on call_depth > 0)
    callers: list[dict] = []
    if call_depth > 0:
        symbols_by_file = build_symbols_by_file(index)
        callers, _ = bfs_callers(
            index, store, owner, name, sym, rev, symbols_by_file, call_depth
        )

    # An answer is "empty" only when every channel this call actually ran came
    # back with nothing. Checked before the response is built so the risk score
    # can be withheld rather than computed and then contradicted.
    answered_nothing = (
        total == 0
        and not confirmed
        and not potential
        and not callers
        and not cross_repo_confirmed
    )
    verdict, unresolvable = blast_verdict(
        index,
        source_files,
        sym_file,
        0 if answered_nothing else max(total, len(confirmed), len(callers)),
    )

    elapsed = (time.perf_counter() - start) * 1000
    result = {
        "repo": f"{owner}/{name}",
        "symbol": {
            "name": sym_name,
            "kind": sym.get("kind", ""),
            "file": sym_file,
            "line": sym.get("line", 0),
            "id": sym.get("id", ""),
        },
        "depth": depth,
        "importer_count": total,
        "direct_dependents_count": direct_count,
        # Withheld, not zeroed, when the graph could not reach this symbol at
        # all. `0.0` is a measurement meaning "nothing depends on this"; a caller
        # deciding whether a change is safe reads it as one, and that is the whole
        # defect. `None` is the third answer: not measurable here.
        "overall_risk_score": None if unresolvable else round(overall_risk, 4),
        "confirmed_count": len(confirmed),
        "potential_count": len(potential),
        "confirmed": confirmed,
        "potential": potential,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tip": (
                "confirmed = imports the file + mentions the symbol name; "
                "potential = imports the file only (wildcard/namespace import). "
                "Use call_depth > 0 to also get symbol-level callers."
            ),
        },
    }

    # The same honesty contract search_text already keeps: an empty result the
    # scan could not have found anything in is `degraded`, never citable absence.
    # `incomplete` is the registered hook for "inputs the scan was supposed to
    # read but could not", which is exactly an import edge that names this
    # symbol's package and lands nowhere. build_verdict then sets
    # `absence_refused`, and the dispatcher turns that into
    # `absence_citable: False` + `absence_blocked_by` with no second rule to keep
    # in sync.
    result["_meta"]["verdict"] = verdict
    if call_depth > 0:
        result["caller_count"] = len(callers)
        result["callers"] = callers
    if cross_repo and cross_repo_confirmed:
        result["cross_repo_confirmed"] = cross_repo_confirmed
        result["cross_repo_confirmed_count"] = len(cross_repo_confirmed)
    if include_depth_scores:
        result["impact_by_depth"] = [
            {
                "depth": d,
                "files": sorted(files_by_depth[d]),
                "risk_score": round(1.0 / (d ** 0.7), 4),
            }
            for d in sorted(files_by_depth)
        ]

    # Phase 2: runtime confidence — zero-cost no-op when no traces ingested.
    # We stamp three surfaces:
    #   1. `result["symbol"]._runtime_confidence` — the focal symbol's status
    #   2. `result["confirmed"][i]._runtime_confidence` — file-level for downstream importers
    #   3. `result["callers"][i]._runtime_confidence` — per-symbol when call_depth > 0
    from ..runtime.confidence import (
        attach_runtime_confidence as _attach_runtime,
        attach_runtime_confidence_by_file as _attach_runtime_files,
    )
    _db_path_str = str(store._sqlite._db_path(owner, name))
    # Focal symbol — wrap so the helper can stamp _runtime_confidence in-place
    _focal_list = [result["symbol"]]
    _focal_summary = _attach_runtime(_focal_list, _db_path_str, id_field="id")
    # File-level on confirmed importers
    _file_summary = _attach_runtime_files(result["confirmed"], _db_path_str, file_field="file")
    # Symbol-level on callers (when present)
    _caller_summary: dict = {}
    if "callers" in result and result["callers"]:
        _caller_summary = _attach_runtime(result["callers"], _db_path_str, id_field="id")
    if _focal_summary or _file_summary or _caller_summary:
        # Merge sources + take the freshest last_seen across the surfaces
        _all_sources: set[str] = set()
        _last_seen = ""
        for s in (_focal_summary, _file_summary, _caller_summary):
            for src in s.get("sources", []):
                _all_sources.add(src)
            if s.get("last_seen", "") > _last_seen:
                _last_seen = s["last_seen"]
        # Coverage = focal + caller per-symbol confirmations / total surfaces stamped
        _stamped = (
            len(_focal_list)
            + len(result.get("confirmed", []))
            + (len(result.get("callers", [])) if "callers" in result else 0)
        )
        _confirmed_count = sum(
            1 for items in (_focal_list, result.get("confirmed", []), result.get("callers", []) if "callers" in result else [])
            for e in items if isinstance(e, dict) and e.get("_runtime_confidence") == "confirmed"
        )
        result["_meta"]["runtime_freshness"] = {
            "sources": sorted(_all_sources),
            "last_seen": _last_seen,
            "coverage_pct": round(100 * _confirmed_count / max(1, _stamped)),
        }
    # Decision context (read-only git archaeology) on request: focal symbol's
    # file first, then the confirmed affected files. Additive — absent the flag
    # the response is byte-identical to prior behavior. Computed before the cache
    # write so a cached hit (keyed on include_decisions) carries it too.
    if include_decisions:
        decision_files = [sym_file] + [c["file"] for c in confirmed]
        result["decisions"] = resolve_decision_context(
            getattr(index, "source_root", None), decision_files,
        )

    result_cache_put("get_blast_radius", repo_key, specific_key, result)
    return _attach_scip_to_blast(result, store, owner, name)
