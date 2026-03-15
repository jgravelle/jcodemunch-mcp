"""Find all files that reference (import) a given identifier."""

import posixpath
import time
from typing import Optional

from ..storage import IndexStore
from ._utils import resolve_repo


def find_references(
    repo: str,
    identifier: str,
    max_results: int = 50,
    storage_path: Optional[str] = None,
) -> dict:
    """Find all indexed files that import or reference an identifier.

    Searches import names and specifier stems for the identifier.  For
    deeper usage-site matching use search_text.

    Args:
        repo: Repository identifier (owner/repo or display name).
        identifier: The symbol/module name to look for (e.g. 'bulkImport', 'IntakeService').
        max_results: Maximum number of results.
        storage_path: Custom storage path.

    Returns:
        Dict with references list and _meta envelope.
    """
    start = time.perf_counter()
    max_results = max(1, min(max_results, 200))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    if index.imports is None:
        return {
            "repo": f"{owner}/{name}",
            "identifier": identifier,
            "references": [],
            "note": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable find_references.",
            "_meta": {"timing_ms": round((time.perf_counter() - start) * 1000, 1)},
        }

    ident_lower = identifier.lower()
    results = []

    for src_file, file_imports in index.imports.items():
        matches = []
        for imp in file_imports:
            # Match against named imports
            named_match = any(n.lower() == ident_lower for n in imp.get("names", []))
            # Match against specifier stem (e.g. 'IntakeService' in './IntakeService.js')
            spec = imp["specifier"]
            spec_stem = posixpath.splitext(posixpath.basename(spec))[0].lower()
            stem_match = spec_stem == ident_lower

            if named_match or stem_match:
                matches.append({
                    "specifier": spec,
                    "names": imp.get("names", []),
                    "match_type": "named" if named_match else "specifier_stem",
                })

        if matches:
            results.append({"file": src_file, "matches": matches})

    results.sort(key=lambda r: r["file"])

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "identifier": identifier,
        "reference_count": len(results),
        "references": results[:max_results],
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "truncated": len(results) > max_results,
            "tip": "For usage-site matching beyond imports, also try search_text.",
        },
    }
