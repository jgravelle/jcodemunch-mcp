"""Find all files that import from a given file path."""

import time
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import (
    build_re_export_maps,
    expand_barrel_leaves,
    resolve_specifier,
)
from ._dynamic_boundary import FILES_CAP as DYNAMIC_FILES_CAP, DynamicBoundary
from ._utils import index_status_to_tool_error, resolve_repo
from .package_registry import (
    extract_root_package_from_specifier,
)


def _resolve_to_leaves(
    imp: dict,
    src_file: str,
    source_files: frozenset,
    alias_map,
    psr4_map,
    wildcard_map: dict[str, list[str]],
    named_map: dict[str, dict[str, tuple[str, str]]],
) -> set[str]:
    """Resolve `imp` to its direct target plus every leaf reachable through
    re-export chains, with per-name routing for selective re-exports.

    For `export { Foo } from './foo'` in a barrel, only consumers that
    actually import `Foo` from the barrel credit `./foo`.
    """
    direct = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
    if not direct:
        return set()
    return expand_barrel_leaves(direct, imp.get("names", []), wildcard_map, named_map)


def _dynamic_block(boundary: DynamicBoundary, file_path: str) -> Optional[dict]:
    """(LEDGER L-73) The scoped dynamic imports that can load ``file_path``.

    ⚠ They are NOT importers and never counted in ``importer_count``: the
    module is named at runtime, so which file it loads is unknown. They are
    named because an answer without them reads as "nothing imports this"
    while `get_blast_radius` refuses to say so over the same file. The reach
    rule is `_dynamic_boundary.DynamicBoundary`, the one every absence tool
    reads (#876, L-70).
    """
    reaching = boundary.reaching(file_path)
    if not reaching:
        return None
    return {
        "files": reaching[:DYNAMIC_FILES_CAP],
        "files_total": len(reaching),
        "note": (
            f"{len(reaching)} file(s) import a module by a computed name that can "
            f"reach this file (e.g. {reaching[0]}). They are not counted as importers, "
            "and a count without them is not evidence that nothing loads it."
        ),
    }


def _opaque_disclosure(boundary: DynamicBoundary, file_paths, counts) -> Optional[dict]:
    """(#876) Opaque sites, disclosed beside an EMPTY answer only.

    The rule `get_blast_radius` applies, exactly: a loader that names its
    module from data could reach anything, so it is disclosed and never
    refuses, and only where the static answer is empty AND no scoped site
    already says the empty answer is not evidence (jjg, 2026-09-29). A file
    that qualifies has no reaching site, so nothing is excluded, and a batch
    discloses what any one qualifying file would.
    """
    if any(
        n == 0 and fp.endswith((".py", ".pyi")) and not boundary.reaching(fp)
        for fp, n in zip(file_paths, counts)
    ):
        return boundary.disclosure()
    return None


def _find_importers_single(
    file_path: str,
    index,
    max_results: int,
    owner: str,
    name: str,
    start: float,
) -> dict:
    """Core logic for a single file_path query. Returns the original flat shape."""
    if index.imports is None:
        return {
            "repo": f"{owner}/{name}",
            "file_path": file_path,
            "importers": [],
            "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_importers.",
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    source_files = frozenset(index.source_files)
    alias_map = index.alias_map
    psr4_map = getattr(index, "psr4_map", None)

    # v1.94.0: symbol-aware barrel resolution.  Wildcard re-exports
    # (`export * from`) credit every leaf to anyone importing the barrel
    # (v1.93 behavior).  Selective re-exports (`export { Foo } from`)
    # credit only consumers that actually import `Foo` from the barrel.
    # Old indexes without `re_export_kind` default to wildcard semantics.
    wildcard_map, named_map = build_re_export_maps(
        index.imports, source_files, alias_map, psr4_map,
    )

    # Build a set of all files that are imported by at least one other file
    # (used for has_importers). Counts barrel-expanded leaves so a leaf
    # definition file isn't flagged as orphan just because its only
    # importers reach it via re-exports.
    files_that_are_imported: set[str] = set()
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            if imp.get("is_re_export"):
                continue  # re-exports forward, not consume
            files_that_are_imported.update(
                _resolve_to_leaves(imp, src_file, source_files, alias_map, psr4_map, wildcard_map, named_map)
            )

    results = []

    for src_file, file_imports in index.imports.items():
        if src_file == file_path:
            continue
        for imp in file_imports:
            if imp.get("is_re_export"):
                continue  # re-export-only files are forwarders, not consumers
            leaves = _resolve_to_leaves(imp, src_file, source_files, alias_map, psr4_map, wildcard_map, named_map)
            if file_path in leaves:
                results.append({
                    "file": src_file,
                    "specifier": imp["specifier"],
                    "names": imp.get("names", []),
                    "has_importers": src_file in files_that_are_imported,
                })
                break  # one match per file is enough

    results.sort(key=lambda r: r["file"])

    elapsed = (time.perf_counter() - start) * 1000
    truncated = len(results) > max_results
    out: dict = {
        "repo": f"{owner}/{name}",
        "file_path": file_path,
        "importer_count": len(results),
        "importers": results[:max_results],
    }
    boundary = DynamicBoundary(index.imports)
    if boundary:
        block = _dynamic_block(boundary, file_path)
        if block:
            out["dynamic_import_boundary"] = block
        unfollowed = _opaque_disclosure(boundary, [file_path], [len(results)])
        if unfollowed:
            out["dynamic_imports_unfollowed"] = unfollowed
    out.update({
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "truncated": truncated,
            "tip": "Tip: use file_paths=['{0}','...'] to query multiple files in one call.".format(file_path)
            if truncated
            else "Tip: use file_paths=['{0}','...'] to query multiple files in one call. "
                 "For usage-site matching beyond imports, also try check_references.".format(file_path),
        },
    })
    return out


def _find_importers_batch(
    file_paths: list[str],
    index,
    max_results: int,
    owner: str,
    name: str,
    start: float,
) -> dict:
    """Batch logic: loop over file_paths, return grouped results array."""
    if index.imports is None:
        return {
            "repo": f"{owner}/{name}",
            "results": [
                {
                    "file_path": fp,
                    "importers": [],
                    "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_importers.",
                }
                for fp in file_paths
            ],
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    source_files = frozenset(index.source_files)
    alias_map = index.alias_map
    psr4_map = getattr(index, "psr4_map", None)

    # v1.94.0: symbol-aware barrel resolution. See _find_importers_single.
    wildcard_map, named_map = build_re_export_maps(
        index.imports, source_files, alias_map, psr4_map,
    )

    # Pass 1: build files_that_are_imported (counts barrel-expanded leaves)
    files_that_are_imported: set[str] = set()
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            if imp.get("is_re_export"):
                continue
            files_that_are_imported.update(
                _resolve_to_leaves(imp, src_file, source_files, alias_map, psr4_map, wildcard_map, named_map)
            )

    # Pass 2: build import_map. Each importer is recorded under every leaf
    # its specifier reaches (including transitively through barrels).
    import_map: dict[str, list[dict]] = {}
    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            if imp.get("is_re_export"):
                continue
            leaves = _resolve_to_leaves(imp, src_file, source_files, alias_map, psr4_map, wildcard_map, named_map)
            if not leaves:
                continue
            entry = {
                "file": src_file,
                "specifier": imp["specifier"],
                "names": imp.get("names", []),
                "has_importers": src_file in files_that_are_imported,
            }
            for leaf in leaves:
                import_map.setdefault(leaf, []).append(entry)

    boundary = DynamicBoundary(index.imports)
    results = []
    for file_path in file_paths:
        file_results = import_map.get(file_path, [])  # O(1) lookup
        file_results.sort(key=lambda r: r["file"])
        entry = {
            "file_path": file_path,
            "importer_count": len(file_results),
            "importers": file_results[:max_results],
        }
        block = _dynamic_block(boundary, file_path) if boundary else None
        if block:
            entry["dynamic_import_boundary"] = block
        results.append(entry)

    out: dict = {
        "repo": f"{owner}/{name}",
        "results": results,
    }
    # Opaque sites are repo-wide, so a batch names them once, not per file.
    unfollowed = (
        _opaque_disclosure(boundary, file_paths, [e["importer_count"] for e in results])
        if boundary else None
    )
    if unfollowed:
        out["dynamic_imports_unfollowed"] = unfollowed
    out["_meta"] = {"timing_ms": round((time.perf_counter() - start) * 1000, 1)}
    return out


def _find_cross_repo_importers(
    file_path: str,
    repo_id: str,
    all_repos: list[dict],
    store: IndexStore,
    owner: str,
    name: str,
) -> list[dict]:
    """Search other indexed repos for files that import from this repo's package."""
    # Look up this repo's package names from its index
    current_index = store.load_index(owner, name)
    if not current_index:
        return []
    pkg_names = getattr(current_index, "package_names", []) or []
    if not pkg_names:
        return []

    cross_results: list[dict] = []

    for repo_entry in all_repos:
        other_repo_id = repo_entry.get("repo", "")
        if not other_repo_id or other_repo_id == repo_id or "/" not in other_repo_id:
            continue
        other_owner, other_name = other_repo_id.split("/", 1)
        other_index = store.load_index(other_owner, other_name)
        if not other_index or not other_index.imports:
            continue

        other_source_files = frozenset(other_index.source_files)

        for src_file, file_imports in other_index.imports.items():
            for imp in file_imports:
                specifier = imp.get("specifier", "")
                # Determine language from file extension
                lang = other_index.file_languages.get(src_file, "")
                root_pkg = extract_root_package_from_specifier(specifier, lang)
                if root_pkg and root_pkg in pkg_names:
                    cross_results.append({
                        "file": src_file,
                        "specifier": specifier,
                        "names": imp.get("names", []),
                        "has_importers": True,  # cross-repo — not analyzed further
                        "cross_repo": True,
                        "source_repo": other_repo_id,
                    })
                    break  # one match per file per other-repo is enough

    return cross_results


def find_importers(
    repo: str,
    file_path: Optional[str] = None,
    max_results: int = 50,
    storage_path: Optional[str] = None,
    file_paths: Optional[list[str]] = None,
    cross_repo: Optional[bool] = None,
) -> dict:
    """Find all indexed files that import from file_path.

    Supports two modes:
    - Singular: pass ``file_path`` to get the original flat response shape.
    - Batch: pass ``file_paths`` (list) to query multiple files at once,
      returning a grouped ``results`` array.

    Args:
        repo: Repository identifier (owner/repo or display name).
        file_path: Target file path within the repo (singular mode).
        file_paths: List of target file paths (batch mode).
        max_results: Maximum number of importers per file.
        storage_path: Custom storage path.
        cross_repo: When True, also search other indexed repos for cross-repo importers.
                    Defaults to the ``cross_repo_default`` config value (False).
                    Package-level (whole-repo) scope, so it is only supported with
                    singular ``file_path`` or a single-element ``file_paths`` batch;
                    combining it with a multi-file batch returns an error (#339).

    Returns:
        Singular mode: dict with flat ``importers`` list and _meta envelope.
        Batch mode: dict with ``results`` array (one entry per input file_path).

    Raises:
        ValueError: if neither or both of file_path and file_paths are provided.
    """
    # Normalize: some MCP clients send file_paths=[] alongside file_path when they mean singular mode
    if file_path is not None and file_paths is not None and len(file_paths) == 0:
        file_paths = None
    if (file_path is None and file_paths is None) or (file_path is not None and file_paths is not None):
        raise ValueError("Provide exactly one of 'file_path' or 'file_paths', not both and not neither.")

    # Resolve cross_repo default from config if not explicitly provided
    if cross_repo is None:
        from .. import config as _cfg
        cross_repo = bool(_cfg.get("cross_repo_default", False))

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

    repo_id = f"{owner}/{name}"

    if file_paths is not None:
        # Cross-repo importer lookup is package-level (whole-repo, not per-file —
        # _find_cross_repo_importers ignores its file_path arg). Attaching one
        # whole-repo result across a multi-file batch would either drop the
        # evidence (the old `""` path) or imply per-file precision that does not
        # exist. Fail closed and point the caller at singular calls (#339).
        if cross_repo and len(file_paths) > 1:
            return {
                "error": (
                    "cross_repo=true is not supported with a multi-file 'file_paths' "
                    "batch: cross-repo importer lookup is package-level, not per-file. "
                    "Use singular 'file_path' calls for cross-repo evidence."
                ),
                "_meta": {
                    "cross_repo_scope": "package",
                    "file_count": len(file_paths),
                },
            }
        result = _find_importers_batch(file_paths, index, max_results, owner, name, start)
        if cross_repo:
            # len(file_paths) == 1 here — equivalent to the singular cross-repo path.
            try:
                from .list_repos import list_repos
                all_repos = list_repos(storage_path=storage_path).get("repos", [])
                cross_results = _find_cross_repo_importers(
                    file_paths[0], repo_id, all_repos, store, owner, name,
                )
                if cross_results and "results" in result:
                    # Attach cross-repo results to the batch response
                    result["cross_repo_importers"] = cross_results[:max_results]
            except Exception:
                pass
        return result
    else:
        result = _find_importers_single(file_path, index, max_results, owner, name, start)
        if cross_repo and "importers" in result:
            try:
                from .list_repos import list_repos
                all_repos = list_repos(storage_path=storage_path).get("repos", [])
                cross_results = _find_cross_repo_importers(
                    file_path, repo_id, all_repos, store, owner, name,
                )
                if cross_results:
                    result["importers"] = result.get("importers", []) + cross_results[:max_results]
                    result["cross_repo_importer_count"] = len(cross_results)
            except Exception:
                pass
        return result
