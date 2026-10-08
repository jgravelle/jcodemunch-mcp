"""The generation-safe read contract: one answer to "which index is this?".

#398 Arc 1 (@rknighton). Three surfaces were answering one question, each
reading the index object directly and each normalising differently:

  * ``retrieval.subject_state.capture`` published ``indexed_at`` as
    ``generation`` and ``git_head`` as ``index_sha``, un-normalised;
  * ``evidence.producers._snapshot`` re-exposed the same two as
    ``index_generation`` / ``indexed_revision``, applying its own ``or None``;
  * ``retrieval.verdict.index_changed_since_load`` read ``_db_mtime_ns``
    separately against its own stamped baseline.

That is the [[feedback_two_paths_one_decision]] shape. The reporter's own
estimate for this arc is ~1.0x and that is honest: it buys correctness, not
speed. It is a prerequisite for Arc 2 because Arc 2 reads exact rows, and a
row that exists but is invisible to the reader is a false absence rather than
a weaker ranking.

Two things live here, and they are the same contract seen from either end:
what generation a loaded index IS, and how to open its database to read that
generation without changing it.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _text(value) -> Optional[str]:
    """A stored revision or stamp, or None when there is not one.

    The empty string is not a revision. It means the index stored none, which
    is ``unknown``, and ``None`` is how every consumer spells that. Doing it
    once here is the point of the module: ``producers`` applied ``or None`` and
    ``capture`` did not, so the same index described itself two ways depending
    on which surface was asked.
    """
    if isinstance(value, str):
        return value or None
    return None


def wal_sidecar_present(db_path: PathLike) -> bool:
    """Whether a ``-wal`` sidecar already exists beside this database."""
    try:
        return Path(str(db_path) + "-wal").exists()
    except OSError:
        return False


def readonly_uri(db_path: PathLike) -> str:
    """A read-only URI that sees committed WAL data without creating sidecars.

    ⚠ Both halves of this are measured, and neither is the obvious choice.

    ``mode=ro`` alone is NOT safe when the sidecars are absent. Read-only in
    SQLite means "cannot modify the DATABASE", not "cannot touch the
    filesystem": opening a WAL-mode database read-only CREATES the ``-wal`` and
    ``-shm`` files, and ``_db_mtime_ns`` takes the max over ``.db`` and
    ``-wal``. v1.108.185 measured the consequence — the first fusion search in
    a process moved the mtime mid-scan, ``index_changed_since_load`` reported
    ``channels.index: "rebuilding"``, and a genuine absence could not reach
    ``absent``. Reproduced 2026-08-02: sidecars ``(False, False)`` ->
    ``(True, True)``, mtime moved.

    ``immutable=1`` alone is NOT safe when the sidecars are present, and the
    cost is worse than the docstring it replaces admitted. That flag tells
    SQLite the file cannot change, so the ``-wal`` is not consulted at all —
    and the invisible content is not merely "some vectors". Measured on the
    same run: with a table created and rows inserted but not yet checkpointed,
    an ``immutable=1`` reader raised **"no such table: symbol_embeddings"**.
    A caller that maps that to "this repository has no embeddings" states a
    false absence about a repository that has just been embedded.

    So: read the WAL when it is already there (nothing is created that was not
    already), and read immutably when it is not (there is no WAL, so nothing is
    missed). The trade-off v1.108.185 accepted disappears rather than being
    balanced.
    """
    return _uri(db_path, immutable=not wal_sidecar_present(db_path))


def _uri(db_path: PathLike, *, immutable: bool) -> str:
    """The read-only URI for ``db_path``, with the path escaped.

    ⚠⚠ SQLite reads this string as a URI, so a raw path is not a path: ``#``
    starts a fragment, ``?`` starts the query, and ``%HH`` is an escape.
    Measured (LEDGER L-137) under a directory named ``c#proj``: the query
    string fell into the fragment, so SQLite opened the path up to the ``#``
    read-WRITE, created an empty file there, and the read failed on a missing
    table; ``EmbeddingStore.has_any()`` answered False over a store holding
    vectors. ``a%20b`` named a directory called ``a b``.

    ⚠ Only those three characters are escaped, ``%`` first. Every other
    character reaches SQLite as it always has (a space, a backslash, a drive
    colon, non-ASCII), so no path that opened before opens differently.
    """
    path = str(db_path).replace("%", "%25").replace("#", "%23").replace("?", "%3f")
    return f"file:{path}?mode=ro" + ("&immutable=1" if immutable else "")


def connect_readonly(db_path: PathLike, **kwargs) -> sqlite3.Connection:
    """Open ``db_path`` under :func:`readonly_uri`, never creating sidecars.

    Falls back to ``immutable=1`` when the WAL-reading open fails. A read-only
    connection to a WAL database needs to map the ``-shm``, which a
    read-only-filesystem or a permission-restricted mount can refuse. Losing
    the un-checkpointed tail is worse than today; failing the read outright is
    worse than that.
    """
    kwargs.setdefault("isolation_level", None)
    uri = readonly_uri(db_path)
    try:
        return sqlite3.connect(uri, uri=True, **kwargs)
    except sqlite3.Error:
        if uri.endswith("immutable=1"):
            raise
        logger.debug(
            "WAL-visible read-only open failed for %s; falling back to immutable",
            db_path, exc_info=True,
        )
        return sqlite3.connect(_uri(db_path, immutable=True), uri=True, **kwargs)


def db_mtime_ns(db_path: PathLike) -> Optional[int]:
    """Most recent mtime_ns across ``.db`` and ``.db-wal``, or None.

    Thin, non-raising wrapper over ``sqlite_store._db_mtime_ns`` so the three
    surfaces stop importing a private name from a 2000-line module. ``None``
    means "could not measure", which is never the same as "unchanged" — see
    :meth:`IndexGeneration.rewritten_since_load`.
    """
    try:
        from .sqlite_store import _db_mtime_ns

        return _db_mtime_ns(Path(db_path))
    except Exception:
        logger.debug("Could not read db mtime for %s", db_path, exc_info=True)
        return None


@dataclass(frozen=True)
class IndexGeneration:
    """Which generation of an index a caller is holding. Never raises to build.

    ``generation`` is the rebuild stamp (``indexed_at``): it changes when the
    index is rebuilt and on nothing else. ``indexed_revision`` is the commit
    the index was built from, which is a property of the SUBJECT rather than of
    the index, and is ``None`` on a folder that is not a checkout.
    ``db_mtime_ns`` is the cross-process rewrite signal — deliberately a
    filesystem reading, because a ``watch-all`` service reindexing in another
    process is invisible to anything in-memory.
    """

    generation: Optional[str] = None
    indexed_revision: Optional[str] = None
    db_path: Optional[str] = None
    db_mtime_ns: Optional[int] = None
    loaded_mtime_ns: Optional[int] = None

    @property
    def rewritten_since_load(self) -> bool:
        """Whether the .db was rewritten since this index was loaded.

        Unknown is NOT changed. An index with no stamped provenance (a test
        double, a hand-built ``CodeIndex``) answers ``False`` rather than
        degrading every verdict it touches.
        """
        if self.loaded_mtime_ns is None or self.db_mtime_ns is None:
            return False
        return self.db_mtime_ns != self.loaded_mtime_ns


def describe(index) -> IndexGeneration:
    """The generation contract for a loaded index. Never raises.

    The single place ``indexed_at`` / ``git_head`` / ``_db_path`` /
    ``_loaded_mtime_ns`` are read off an index object.
    """
    try:
        db_path = getattr(index, "_db_path", None)
        db_path = str(db_path) if db_path else None
        loaded = getattr(index, "_loaded_mtime_ns", None)
        try:
            loaded = int(loaded) if loaded is not None else None
        except (TypeError, ValueError):
            loaded = None
        return IndexGeneration(
            generation=_text(getattr(index, "indexed_at", None)),
            indexed_revision=_text(getattr(index, "git_head", None)),
            db_path=db_path,
            db_mtime_ns=db_mtime_ns(db_path) if db_path else None,
            loaded_mtime_ns=loaded,
        )
    except Exception:
        logger.debug("Generation describe failed", exc_info=True)
        return IndexGeneration()
