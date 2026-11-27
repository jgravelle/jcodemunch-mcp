"""Can this corpus support an absence claim at all? (#566)

``find_dead_code`` publishes ``confidence: 1.0`` and documents it as *provably
unreachable*. That is a claim about the TREE, and it was being computed from the
INDEX with nothing in between.

⚠⚠ **``search_text`` handled the identical situation correctly on the identical
index**, in the same session: ``absence_refused: true``, ``complete: false``,
and it named ``coverage.generation.git_head``. One tool refused an absence claim
it could not support and the other published the strongest available form of it.
This module is the missing half, and it deliberately reuses
``retrieval.verdict.index_coverage_meta`` and ``retrieval.freshness`` rather
than re-deriving either — a second answer to a settled question is the mechanism
this project keeps paying for.

Two causes, both of which the index already discloses and neither of which was
read:

**Stale index.** ``install_layout.py`` was reported dead at 1.0 with two live
importers, added in v1.108.313 against an index pinned at v1.108.303. The
verdict was true of the corpus it queried.

**Withheld files.** ``server.py`` is 550,036 bytes against a 512,000-byte cap,
so it is absent from the index — and it is the dispatcher that imports most of
the tree. Every module reached only from it is importer-less as a consequence of
OUR limit. ``index_folder`` already marks ``too_large`` as *withheld* rather
than an ordinary exclusion, which is exactly what makes ``complete: false``.

⚠ **UNKNOWN is not INADEQUATE, and neither is NOT APPLICABLE.** An index built
from a remote snapshot has no local tree to compare against and is not thereby
suspect; a plain unversioned folder has no revision and never will. Both are
disclosed and neither caps. What caps is a revision we should have been able to
read and could not (``unknown`` with a local source root present), a revision
that demonstrably moved (``stale``), and a file this project refused to index.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from ..retrieval.verdict import index_coverage_meta
from ..security import resolve_within

logger = logging.getLogger(__name__)

#: The ceiling a findings' confidence is clamped to when the corpus cannot
#: support a proof. Deliberately below ``find_dead_code``'s 0.8 default, so the
#: default call reports NOTHING rather than reporting an unprovable 1.0 — with
#: ``signal_warning`` naming the cause, because a bare empty list is the
#: ``dead_code_pct: 0.0`` shape (#559) read from the other end.
UNPROVEN_CEILING = 0.6

#: A NUL byte marks a file that is not UTF-8 text (UTF-16 and binaries).
NUL = bytes([0])

#: Languages of data and config files. A manifest is read by load-time
#: producers (package.json entry points, tsconfig path aliases, composer PSR-4
#: maps) that no view of the withheld files can replay, so one withheld keeps
#: the cap (L-352 review round 3).
DATA_LANGUAGES = frozenset({"json", "yaml", "toml", "xml"})

#: Languages whose import edges ``CodeIndex.__post_init__`` adds or resolves
#: at load, beyond ``resolve_specifier``: Go module paths (L-264), Kotlin and
#: Java top-level declarations (#1376), Racket collection paths, and the PHP
#: PSR-4 map. A withheld file in one of them would miss those edges, so it
#: keeps the cap (L-352 review round 4). A ratchet reads ``index_store.py``
#: and fails when a load-time gate names a language missing here.
LOAD_AUGMENTED_LANGUAGES = frozenset({"go", "kotlin", "java", "racket", "php"})


class CorpusAdequacy:
    """Whether an absence claim over this index is publishable, and why not."""

    __slots__ = ("index_freshness", "withheld", "coverage_complete", "blockers")

    def __init__(
        self,
        index_freshness: str,
        withheld: dict,
        coverage_complete: Optional[bool],
        blockers: list[str],
    ):
        self.index_freshness = index_freshness
        self.withheld = withheld
        self.coverage_complete = coverage_complete
        self.blockers = blockers

    @property
    def adequate(self) -> bool:
        return not self.blockers

    @property
    def ceiling(self) -> float:
        return 1.0 if self.adequate else UNPROVEN_CEILING

    def as_dict(self) -> dict:
        out: dict = {
            "adequate": self.adequate,
            "index_freshness": self.index_freshness,
            "confidence_ceiling": self.ceiling,
        }
        if self.coverage_complete is not None:
            out["coverage_complete"] = self.coverage_complete
        if self.withheld:
            out["withheld"] = dict(self.withheld)
        if self.blockers:
            out["blockers"] = list(self.blockers)
        return out

    def warning(self) -> Optional[str]:
        """One sentence naming what cannot be proven, and how to fix it."""
        if self.adequate:
            return None
        parts = []
        if "stale_index" in self.blockers:
            parts.append(
                "the index was built at a revision the working tree has since "
                "moved off, so an importer added after it is invisible"
            )
        if "index_freshness_unknown" in self.blockers:
            parts.append(
                "the index records no revision to compare against a source tree "
                "that has one, so it has not been shown current"
            )
        if "withheld_files" in self.blockers:
            detail = ", ".join(
                f"{n} {reason}" for reason, n in sorted(self.withheld.items())
            )
            parts.append(
                f"the index withheld real source files that belong to this corpus "
                f"({detail}), and every import they made is missing with them"
            )
        if "corpus_incomplete" in self.blockers:
            parts.append(
                "the index reports itself incomplete, so files are missing from "
                "the corpus along with every import they made"
            )
        if "runtime_discovery_unresolved" in self.blockers:
            parts.append(
                "a module in this repo enumerates a package at import time and "
                "the package it enumerates could not be resolved, so some import "
                "edges exist only at runtime"
            )
        return (
            "Unreachability cannot be proven against this corpus: "
            + "; ".join(parts)
            + f". Confidence is capped at {UNPROVEN_CEILING}; re-index (and raise "
            "max_file_size if files were withheld) to restore proof."
        )


def _repo_freshness(index) -> str:
    """``fresh`` / ``stale`` / ``unknown`` / ``not_tracked`` / ``no_source_root``.

    ⚠ ``no_source_root`` is OURS, not ``FreshnessProbe``'s. The probe answers
    ``unknown`` for a missing root, which is right for its callers and wrong as
    a cap here: an index built by ``index_repo`` from a pinned remote snapshot
    has no local tree by construction, and capping it would refuse a corpus that
    is complete and self-consistent. Distinguishing them is the difference
    between a capability failing and a capability the subject does not have.
    """
    root = getattr(index, "source_root", None)
    if not root or not os.path.isdir(root):
        return "no_source_root"
    try:
        from ..retrieval.freshness import FreshnessProbe

        return FreshnessProbe(
            root,
            getattr(index, "indexed_at", "") or "",
            getattr(index, "git_head", None),
        ).repo_freshness
    except Exception:
        logger.debug("freshness probe failed for %s", root, exc_info=True)
        return "unknown"


def read_withheld_files(index) -> Optional[dict]:
    """``{path: {"text", "imports"}}`` for every file the index withheld, or None (L-352).

    A file withheld for size is missing from the INDEX, not from the tree, so
    the imports it makes can still be read from disk, by the same
    ``extract_imports`` the indexer runs. The caller resolves them with the
    same ``resolve_specifier`` the graph uses, so a withheld importer counts
    exactly as an indexed one would. The text rides along so the other edge
    families (dynamic-import sites, runtime package enumeration, render
    edges) can read it through :func:`withheld_view`.

    None whenever that read cannot stand in for indexing the whole withheld
    set, and None keeps the cap:
    - a withheld reason other than size, or a listing that was truncated or
      does not account for the total;
    - no local source root, or a file that cannot be read;
    - a file that is not UTF-8 text (a NUL byte or a failed decode), since
      text decoded with replacements would name nothing;
    - a file with no known language, whose imports nothing can extract;
    - a file in :data:`LOAD_AUGMENTED_LANGUAGES` (Go, Kotlin, Java, Racket,
      PHP): the index adds those edges at load, which ``resolve_specifier``
      does not apply to a text read from disk;
    - a data or config file (:data:`DATA_LANGUAGES`): manifests feed entry
      points and alias maps that are built before this read and never see it.
    """
    from ..parser.imports import extract_imports
    from ..parser.languages import get_language_for_path
    from ..retrieval.verdict import WITHHELD_FILES_KEY

    cov = getattr(index, "coverage", None) or {}
    withheld = cov.get("withheld") or {}
    if not withheld or set(withheld) != {"too_large"}:
        return None
    listed = cov.get(WITHHELD_FILES_KEY)
    if not isinstance(listed, dict) or not listed or cov.get("withheld_files_truncated"):
        return None
    if cov.get("withheld_files_total") != len(listed) or withheld["too_large"] != len(listed):
        return None
    root = getattr(index, "source_root", None)
    if not root or not os.path.isdir(root):
        return None
    base = Path(root)
    base_resolved = str(base.resolve())
    out: dict = {}
    for rel in sorted(listed):
        language = get_language_for_path(rel)
        if not language or language in LOAD_AUGMENTED_LANGUAGES or language in DATA_LANGUAGES:
            return None
        path = resolve_within(base, rel, base_resolved=base_resolved)
        if path is None:
            return None
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            logger.debug("withheld file %s could not be read", rel, exc_info=True)
            return None
        if NUL in raw:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        try:
            out[rel] = {"text": text, "imports": extract_imports(text, rel, language, repo=root) or []}
        except Exception:
            logger.debug("imports of withheld file %s not extracted", rel, exc_info=True)
            return None
    return out


class _IndexWithWithheld:
    """The index, with the withheld files added to ``source_files`` and their
    imports to ``imports``. Every other attribute is the index's own."""

    def __init__(self, index, withheld: dict):
        self._index = index
        self.source_files = sorted(set(index.source_files) | set(withheld))
        self.imports = {**(index.imports or {}), **{k: v["imports"] for k, v in withheld.items()}}
        # One whole-file function per withheld file, so a producer that reads
        # symbol bodies (render edges) reads the withheld text too.
        self.symbols = list(index.symbols) + [
            {
                "id": f"{rel}::<withheld>#function", "name": "<withheld>", "kind": "function",
                "file": rel, "line": 1, "end_line": rec["text"].count("\n") + 1,
            }
            for rel, rec in sorted(withheld.items())
        ]

    def __getattr__(self, attr):
        return getattr(self._index, attr)


class _StoreWithWithheld:
    """The store, serving a withheld file's text from the disk read."""

    def __init__(self, store, withheld: dict):
        self._store = store
        self._texts = {k: v["text"] for k, v in withheld.items()}

    def get_file_content(self, owner, name, path, *args, **kwargs):
        if path in self._texts:
            return self._texts[path]
        return self._store.get_file_content(owner, name, path, *args, **kwargs)

    def __getattr__(self, attr):
        return getattr(self._store, attr)


def withheld_view(index, store, withheld: dict):
    """An index and a store that also hold the withheld files (L-352).

    The edge producers a dead-code answer reads (render edges, runtime package
    enumeration, dynamic-import sites) walk ``index.source_files`` and read
    text through the store. Handing them this view runs the same producers
    over the withheld files, so no family has a second copy of its rules.
    """
    return _IndexWithWithheld(index, withheld), _StoreWithWithheld(store, withheld)


def assess_corpus(index, *, extra_blockers=()) -> CorpusAdequacy:
    """Assess whether this index can back a proof of unreachability.

    ``extra_blockers`` lets a caller add a cause it alone can see — the
    unresolved runtime-discovery sites of #569 — without this module growing a
    dependency on the dead-code tools it serves.
    """
    freshness = _repo_freshness(index)
    coverage = index_coverage_meta(index) or {}
    withheld = coverage.get("withheld") or {}
    complete = coverage.get("complete")

    blockers: list[str] = []
    if freshness == "stale":
        blockers.append("stale_index")
    elif freshness == "unknown":
        blockers.append("index_freshness_unknown")
    if withheld:
        blockers.append("withheld_files")
    elif complete is False:
        # ⚠ ``complete: false`` with nothing in ``withheld`` is the OTHER way a
        # corpus loses files — the ``max_folder_files`` cap, a drop after
        # discovery — and their imports vanish by the same mechanism. Tri-state:
        # ``None`` is an index that predates the coverage contract and is not
        # thereby incomplete, which is why this tests ``is False``.
        blockers.append("corpus_incomplete")
    blockers.extend(extra_blockers)

    return CorpusAdequacy(freshness, withheld, complete, blockers)
