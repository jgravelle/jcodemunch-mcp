"""Transactional selective code snapshots. #398 Arc 2 (@rknighton).

``load_index`` runs ``SELECT * FROM symbols`` and builds the whole ``CodeIndex``
before anyone can ask it anything. Issue #370 measured what that costs at scale:
thirteen concurrent cold searches over 665,982 symbols, 7.5-11.4 minutes each,
~16.4 GiB, every caller hydrating independently. A tool that wants one symbol
body pays for the entire corpus.

This module is the narrow path. It reads repository metadata plus **all** file
rows -- the ``files`` table is bounded by file count, not symbol count, so it is
cheap and it keeps every file-level answer exact -- plus only the symbol rows the
caller named, inside one read transaction.

## The promotion boundary is the whole design

A view that answers a corpus-wide question from a partial row set is a false
absence generator, which is precisely the defect class Arc 1 shipped a release to
close. So this class does not guess and it does not fail:

  * Every field that is **exact** on a selective read is copied onto the instance
    in ``__init__``, so attribute lookup finds it normally.
  * Everything else -- ``symbols``, ``search``, ``get_callers_by_name``, the BM25
    caches, and anything added to ``CodeIndex`` in the future -- falls through to
    ``__getattr__``, which loads the full index once and delegates.

⚠ The fail-safe direction is load-bearing and it is why ``__getattr__`` is the
mechanism rather than an allow-list check at each call site. A field added to
``CodeIndex`` next year is unknown to this file, and an unknown field promotes.
The failure mode of forgetting to update this module is *slower*, never *wrong*.

``get_symbol`` is the one exception that does not promote, because it does not
have to: a miss against the preloaded set is answered by a targeted single-row
read, which is exact for any id in the database. Falling back to a query is
strictly better than either guessing absent or hydrating 665k rows.

⚠⚠ **No read on this path uses ``immutable=1``.** The trade-off that flag
accepts is channel-specific (@rknighton, 2026-08-01): on the similarity channel a
vector missed in an un-checkpointed WAL only weakens a ranking, because that
channel adds candidates and never removes any. Here the reads are exact rows, and
an invisible row is a symbol that exists and was not returned. Every connection
goes through ``generation.connect_readonly``, which reads the WAL when its
sidecar is already there, and a test fails on any hand-rolled read-only URI.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Fields of ``CodeIndex`` that a selective read answers EXACTLY.
#:
#: Everything here is either repository-level metadata or derived from the
#: ``files`` table, both of which this path reads in full. A field belongs on
#: this list only if a partial symbol set cannot change its value.
EXACT_FIELDS: tuple[str, ...] = (
    "repo", "owner", "name", "indexed_at", "summaries_generation", "index_version",
    "languages", "git_head", "source_root", "git_root", "source_roots",
    "display_name", "branch",
    "source_files", "file_hashes", "file_summaries", "file_languages",
    "file_blob_shas", "file_mtimes", "file_sizes", "imports",
    "context_metadata", "package_names", "alias_map", "psr4_map",
    "file_cap_status", "coverage",
    # Re-parse stamps. Both are META rows, so a partial read answers them
    # exactly -- and both are read by predicates that run BEFORE any symbol is
    # touched (`needs_parser_upgrade`, `racket_reparse_reason`). Leaving them
    # out sent the watcher's per-event upgrade check through `__getattr__` and
    # promoted the whole index to read one integer.
    # ⚠ `racket_config_digest` is None-MEANINGFUL: absent means "built
    # before the gate", not "unknown". Copying it exactly preserves that;
    # promoting to answer it never changed the value, only the cost.
    "parser_generation", "racket_config_digest",
)

#: Names that MUST promote, listed for documentation and asserted by tests.
#: ``__getattr__`` promotes anything not copied in ``__init__``, so this tuple
#: does not gate behaviour -- it records the corpus-wide surface we know about
#: so a test can prove each one promotes rather than answering from a subset.
CORPUS_WIDE: tuple[str, ...] = (
    "symbols", "search", "get_callers_by_name",
    "_symbol_index", "_source_file_set", "_bm25_cache", "_bm25_lock",
    "_import_name_index", "_callers_by_name", "_get_symbol_raw",
    "_score_symbol", "_match_pattern",
)


class SelectiveIndexView:
    """A ``CodeIndex``-shaped read view over metadata plus named symbol rows.

    Not a subclass. Subclassing would inherit ``CodeIndex``'s corpus-wide methods
    silently operating over a partial ``symbols`` list, which is the one outcome
    this class exists to make impossible.
    """

    #: Load provenance stamped by ``_stamp_load_provenance``. These describe the
    #: DATABASE FILE, not the symbol set, so a selective read answers them
    #: exactly — and they must be real slots. Leaving them out would send
    #: ``subject_state.capture`` through ``__getattr__`` and promote the whole
    #: corpus to answer a question about a file's mtime, on every verdict path.
    _PROVENANCE = ("_db_path", "_loaded_mtime_ns")

    __slots__ = (
        "_partial", "_promote", "_full", "_fetch_symbol", "_extra",
        "_promotions", "_promoted_by", "_generation", "_generation_moved",
        *_PROVENANCE,
        *EXACT_FIELDS,
    )

    def __init__(
        self,
        partial,
        *,
        promote: Callable[[], Optional[object]],
        fetch_symbol: Callable[[str], Optional[dict]],
        generation: Optional[str] = None,
    ) -> None:
        object.__setattr__(self, "_partial", partial)
        object.__setattr__(self, "_promote", promote)
        object.__setattr__(self, "_fetch_symbol", fetch_symbol)
        object.__setattr__(self, "_full", None)
        object.__setattr__(self, "_promotions", 0)
        object.__setattr__(self, "_promoted_by", None)
        object.__setattr__(self, "_generation", generation)
        object.__setattr__(self, "_generation_moved", False)
        # Rows fetched one at a time after the snapshot, memoised so a repeated
        # lookup costs one query rather than one per call.
        object.__setattr__(self, "_extra", {})
        for field in self._PROVENANCE:
            object.__setattr__(self, field, getattr(partial, field, None))
        for field in EXACT_FIELDS:
            object.__setattr__(self, field, getattr(partial, field, None))

    # -- the exact half ---------------------------------------------------- #

    def get_symbol(self, symbol_id: str) -> Optional[dict]:
        """A symbol by id. Exact for any id in the database, never promotes.

        The preloaded set is checked first; a miss is a targeted single-row read,
        not an absence. ``None`` here means the row is not in the database, which
        is the same answer the full index gives.
        """
        hit = self._partial.get_symbol(symbol_id)
        if hit is not None:
            return hit
        if symbol_id in self._extra:
            return self._extra[symbol_id]
        row = None
        try:
            row = self._fetch_symbol(symbol_id)
        except Exception:
            logger.debug("Selective symbol fetch failed for %s", symbol_id, exc_info=True)
            # Could not establish. Promote rather than report an absence we did
            # not measure -- the full index answers it for certain.
            return self._promoted("get_symbol").get_symbol(symbol_id)
        self._extra[symbol_id] = row
        return row

    def has_source_file(self, file_path: str) -> bool:
        """Exact: this path reads every ``files`` row."""
        return self._partial.has_source_file(file_path)

    # -- the promoting half ------------------------------------------------ #

    def __getattr__(self, attr: str):
        """Anything not copied in ``__init__`` needs the whole corpus.

        Reached only for names absent from ``__slots__`` and from the class, so
        every corpus-wide field and method lands here, including ones that do not
        exist yet.
        """
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        return getattr(self._promoted(attr), attr)

    def _promoted(self, because: str = "unknown"):
        """Load the full index once and remember why."""
        full = self._full
        if full is None:
            logger.debug(
                "Selective view promoting to a full index (attribute: %s)", because
            )
            full = self._promote()
            if full is None:
                raise RuntimeError(
                    "selective view could not promote to a full index; the "
                    f"attribute {because!r} cannot be answered from a partial "
                    "symbol set and answering it anyway would be a false absence"
                )
            object.__setattr__(self, "_full", full)
            object.__setattr__(self, "_promotions", self._promotions + 1)
            object.__setattr__(self, "_promoted_by", because)
            live = getattr(full, "indexed_at", None) or None
            if self._generation and live and live != self._generation:
                # The index was rebuilt between the snapshot and the promotion.
                # The promoted index is newer AND internally consistent, so it
                # wins whole -- mixing the two would be the one thing worse than
                # either. Recorded so a caller can disclose it.
                object.__setattr__(self, "_generation_moved", True)
                logger.debug(
                    "Index generation moved during a selective request: %s -> %s",
                    self._generation, live,
                )
        return full

    # -- introspection for tests and telemetry ----------------------------- #

    @property
    def promoted(self) -> bool:
        return self._full is not None

    @property
    def promoted_by(self) -> Optional[str]:
        return self._promoted_by

    @property
    def promotions(self) -> int:
        """How many times the full index was BUILT. Must never exceed 1."""
        return self._promotions

    @property
    def generation_moved(self) -> bool:
        """Whether a promotion crossed a reindex. Unknown reads as False."""
        return self._generation_moved

    @property
    def selective_symbol_count(self) -> int:
        """Rows this view was opened with, before any targeted fetches."""
        return len(self._partial.symbols)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = f"promoted by {self._promoted_by!r}" if self.promoted else "selective"
        return (
            f"<SelectiveIndexView {self.repo!r} "
            f"{self.selective_symbol_count} rows, {state}>"
        )
