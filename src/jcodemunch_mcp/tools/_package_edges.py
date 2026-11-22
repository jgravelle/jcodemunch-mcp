"""Can the file-level importer graph REACH a file? (#415, #718, LEDGER L-59)

A leaf: ``get_blast_radius.blast_verdict`` and ``find_dead_code`` both read
this one probe, and ``get_blast_radius`` imports ``find_dead_code``, so the
probe lives below both rather than in either.
"""

from __future__ import annotations

import posixpath
from typing import Optional

from ..parser.imports import resolve_specifier


class PackageEdgeProbe:
    """Evidence that the importer graph cannot REACH a file, not that nothing imports it.

    A file-level import graph resolves one specifier to one file. Languages that
    address imports at package/directory granularity (Go's ``import "mod/pkg"``
    names a directory of files, and files inside that package need no import at
    all) therefore produce edges this graph captures but cannot land on a member
    file. The importer set comes back empty for every symbol in the package, and
    that emptiness is a property of the resolver, not of the repository.

    Deliberately MEASURED rather than declared from a language table. An edge
    naming a file's own directory that resolved to nothing is direct evidence for
    that file; a hardcoded list of package-granular languages would be a claim
    about every repo, kept in sync by hand, and wrong the first time a resolver
    improves.

    Built once per index: every import is resolved once here, so a caller that
    asks about many files (``find_dead_code``, LEDGER L-59) pays one pass, and
    ``blast_verdict`` and that caller read one answer, not two copies of it.
    """

    def __init__(
        self,
        imports: dict,
        source_files: frozenset,
        alias_map: Optional[dict] = None,
        psr4_map: Optional[dict] = None,
    ) -> None:
        self._unresolved: list[tuple[str, str, str]] = []
        for src_file, file_imports in (imports or {}).items():
            for imp in file_imports or []:
                raw = imp.get("specifier") or ""
                spec = raw.replace("\\", "/").strip("/")
                if not spec:
                    continue
                if resolve_specifier(raw, src_file, source_files, alias_map, psr4_map):
                    continue  # the graph handled this one; it is not evidence of a gap
                self._unresolved.append((src_file, spec, raw))
        self._source_files = source_files
        self._dir_counts: dict[str, int] = {}
        for f in source_files:
            d = posixpath.dirname(f.replace("\\", "/")).strip("/")
            self._dir_counts[d] = self._dir_counts.get(d, 0) + 1

    def reaching(self, sym_file: str, sample_cap: int = 5) -> Optional[dict]:
        """The disclosure for ``sym_file``, or None when there is nothing to disclose:
        no unresolved edge names its directory, or it sits at the repo root, where a
        directory name cannot discriminate."""
        target_dir = posixpath.dirname((sym_file or "").replace("\\", "/")).strip("/")
        if not target_dir:
            # A root-level file has no directory to match a specifier against, so any
            # answer here would rest on a coincidence rather than on evidence.
            return None
        tail = target_dir.rsplit("/", 1)[-1]
        exact: list[dict] = []
        loose: list[dict] = []
        for src_file, spec, raw in self._unresolved:
            if spec == target_dir or spec.endswith("/" + target_dir):
                exact.append({"importer": src_file, "specifier": raw})
            elif spec.rsplit("/", 1)[-1] == tail:
                loose.append({"importer": src_file, "specifier": raw})
        matched = exact or loose
        if not matched:
            return None
        # Sibling files in the same directory. For a package-granular language these
        # can call the symbol with no import statement at all, so they are invisible
        # to an import graph by construction — a second, independent reason the zero
        # cannot be read as absence.
        siblings = self._dir_counts.get(target_dir, 0) - (sym_file in self._source_files)
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
