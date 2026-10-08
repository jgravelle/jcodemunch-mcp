"""SQLite-backed storage for symbol embeddings.

The symbol_embeddings table lives in the same .db file as the symbol index.
Embeddings are stored as float32 BLOBs serialised via the stdlib ``array`` module —
no numpy or other deps required.
"""

import array
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from . import generation as _generation

logger = logging.getLogger(__name__)

#: Callables notified after any write to a `symbol_embeddings` table, with the
#: database path. Registered by whoever holds derived state over the store.
#:
#: ⚠ The store deliberately does NOT import its subscribers. That inversion is
#: the whole point: `embedding_matrix` caches decoded rows and must drop them on
#: a write, but a cache depending on a store is ordinary while a store
#: depending on its caches is a cycle.
_WRITE_LISTENERS: list = []


def register_write_listener(fn) -> None:
    """Subscribe *fn(db_path)* to writes. Idempotent."""
    if fn not in _WRITE_LISTENERS:
        _WRITE_LISTENERS.append(fn)

_EMBEDDINGS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS symbol_embeddings (
    symbol_id TEXT PRIMARY KEY,
    embedding  BLOB NOT NULL
);
"""

# Bound on ids per `IN (...)` in get_many. SQLITE_MAX_VARIABLE_NUMBER is 32766
# on modern builds but 999 on older ones; 900 clears the floor with headroom.
_GET_MANY_CHUNK = 900

# Every key this store keeps in the shared `meta` table starts with this prefix.
# `save_index` clears the index's own keys on a full save and leaves these, because
# it leaves `symbol_embeddings` too: metadata erased beside surviving vectors reads
# as "model unknown", and `embed_repo` does not treat unknown as a change (#522).
# A new key must carry the prefix, and be named `_EMBED_*_KEY` so that
# `tests/test_full_save_keeps_embedding_meta.py` reads it off this module.
META_KEY_PREFIX = "embed_"

_EMBED_DIM_KEY = "embed_dimension"
_EMBED_MODEL_KEY = "embed_model"
_EMBED_TASK_TYPE_KEY = "embed_task_type"


#: Why stored vectors may not be extended or scored under the active model.
#: `embed_repo` reports the same strings as `rebuild_reason`.
STALE_MODEL_CHANGED = "embedding_model_changed"
STALE_TASK_TYPE_CHANGED = "embedding_task_type_changed"
STALE_METADATA_MISSING = "embedding_metadata_missing"


def stale_reason(stored: Optional[dict], model: str, task_type: str) -> Optional[str]:
    """Why vectors described by ``stored`` do not belong with ``model``, or None.

    ``stored`` is `EmbeddingStore.read_meta()`. THE one rule for both vector
    writers: `embed_repo` rebuilds on a reason, and the semantic top-up in
    `search_symbols` writes nothing (LEDGER L-121: the rule lived inline in
    `embed_repo` only, so the top-up wrote a second model's vectors beside the
    first's). A function that calls `set_many` and not this fails
    `tests/test_semantic_topup_checks_the_stored_model.py`.

    ⚠ Unknown is NOT a change (#500). An unreadable store (``None``), a store
    with no model name, and an empty ``model`` all answer None: forcing a
    rebuild on those bills a full re-embed for a model that may be identical.

    ⚠⚠ The task type has three states (#523). An absent row is never recorded
    and is not a change; ``""`` IS a recorded value, written by every provider
    but task-aware Gemini, so a truthiness test misses a real toggle from it.

    ⚠ Vectors with no metadata at all (no dimension either) are stale: nothing
    says what produced them, and the next write would stamp the store with the
    active model over them. A full re-index left stores so before #522.
    """
    if not stored or not stored.get("has_vectors"):
        return None
    stored_model = stored.get("model")
    if stored_model and model and stored_model != model:
        return STALE_MODEL_CHANGED
    stored_task_type = stored.get("task_type")
    if stored_task_type is None:
        return STALE_METADATA_MISSING if stored.get("dimension") is None else None
    if stored_task_type != task_type:
        return STALE_TASK_TYPE_CHANGED
    return None


def _encode_embedding(vec: list[float]) -> bytes:
    """Serialise a float list to bytes (float32, native byte order)."""
    return array.array("f", vec).tobytes()


def _decode_embedding(data: bytes) -> list[float]:
    """Deserialise bytes back to a float list."""
    a: array.array = array.array("f")
    a.frombytes(data)
    return list(a)


class EmbeddingStore:
    """Thin CRUD wrapper around the ``symbol_embeddings`` SQLite table."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # ── Connection ─────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA journal_size_limit = 67108864")  # bound WAL growth under starved checkpoints
        conn.executescript(_EMBEDDINGS_SCHEMA)
        return conn

    # ── Dimension / model meta ─────────────────────────────────────────────

    def get_dimension(self) -> Optional[int]:
        """Return stored embedding dimension, or None if not set."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (_EMBED_DIM_KEY,)
                ).fetchone()
                return int(row[0]) if row else None
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_dimension failed", exc_info=True)
            return None

    def set_dimension(self, dim: int, model: str = "") -> None:
        """Persist embedding dimension (and optionally model name) to meta."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_EMBED_DIM_KEY, str(dim)),
            )
            if model:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (_EMBED_MODEL_KEY, model),
                )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_model(self) -> Optional[str]:
        """Return the model name these embeddings were built with, or None.

        ⚠ None means UNKNOWN, never "a different model". Stores written before
        this key was populated have no row, and `set_dimension` only writes it
        when given a non-empty name. Callers comparing models must treat None
        as "cannot tell" and NOT force a rebuild on it (#500).

        ⚠ `evidence/capability.py` has called this since v1.108.221 behind a
        `type: ignore` and a bare except, so the capability certificate reported
        `model: "unknown"` for every repo. It resolves now.
        """
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (_EMBED_MODEL_KEY,)
                ).fetchone()
                return str(row[0]) if row and row[0] else None
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_model failed", exc_info=True)
            return None

    def read_meta(self, for_writer: bool = False) -> Optional[dict]:
        """What built the stored vectors, read WITHOUT touching the file.

        ``{"has_vectors", "dimension", "model", "task_type"}``; an absent row is
        ``None``, and ``task_type`` keeps ``""`` apart from absent (#523).
        Returns ``None`` when the store could not be read, which is unknown and
        never "nothing stored".

        ⚠ `get_dimension`/`get_model`/`get_task_type` open a read-WRITE
        connection, and `_connect` runs a PRAGMA and a CREATE TABLE on each, so
        they move the .db mtime. A search calls this on every semantic query,
        before its scan; a getter there makes the search's own movement check
        report a rebuild the search caused (see `get_all_readonly`).

        ⚠ ``for_writer=True`` is for a caller about to write anyway
        (`embed_repo`), and reads over the read-write connection that caller
        writes with. Unknown is not a change, so a writer left with ``None``
        skips the model-change rebuild and writes a second width beside the
        first; it must not be blind where it can write. The read-only open has
        ways to fail that the read-write one does not (LEDGER L-137: under a
        path holding ``#`` it opens another, empty file, and the read fails).
        """
        try:
            conn = self._connect() if for_writer else _generation.connect_readonly(self._db_path)
        except Exception:
            logger.debug("EmbeddingStore.read_meta could not open %s",
                         self._db_path, exc_info=True)
            return None
        try:
            try:
                has_vectors = conn.execute(
                    "SELECT 1 FROM symbol_embeddings LIMIT 1"
                ).fetchone() is not None
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
                has_vectors = False
            rows = dict(conn.execute(
                "SELECT key, value FROM meta WHERE key IN (?, ?, ?)",
                (_EMBED_DIM_KEY, _EMBED_MODEL_KEY, _EMBED_TASK_TYPE_KEY),
            ).fetchall())
            try:
                dim = int(rows[_EMBED_DIM_KEY])
            except (KeyError, TypeError, ValueError):
                # One unreadable row is one unknown; the model and task type
                # beside it still decide.
                dim = None
            return {
                "has_vectors": has_vectors,
                "dimension": dim,
                "model": str(rows[_EMBED_MODEL_KEY]) if rows.get(_EMBED_MODEL_KEY) else None,
                "task_type": rows.get(_EMBED_TASK_TYPE_KEY),
            }
        except Exception:
            logger.debug("EmbeddingStore.read_meta failed", exc_info=True)
            return None
        finally:
            conn.close()

    def get_task_type(self) -> Optional[str]:
        """Return stored embedding task type, or None if not set."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (_EMBED_TASK_TYPE_KEY,)
                ).fetchone()
                return row[0] if row else None
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_task_type failed", exc_info=True)
            return None

    def set_task_type(self, task_type: str) -> None:
        """Persist the embedding task type used when building the index."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_EMBED_TASK_TYPE_KEY, task_type),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ── Read ───────────────────────────────────────────────────────────────

    def get(self, symbol_id: str) -> Optional[list[float]]:
        """Return the embedding for one symbol, or None."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT embedding FROM symbol_embeddings WHERE symbol_id = ?",
                    (symbol_id,),
                ).fetchone()
                return _decode_embedding(row[0]) if row else None
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get failed for %s", symbol_id, exc_info=True)
            return None

    def get_all_ids(self) -> set[str]:
        """Return the set of symbol IDs that have stored embeddings."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT symbol_id FROM symbol_embeddings"
                ).fetchall()
                return {row[0] for row in rows}
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_all_ids failed", exc_info=True)
            return set()

    def get_all(self) -> dict[str, list[float]]:
        """Return every stored embedding as {symbol_id: vector}."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT symbol_id, embedding FROM symbol_embeddings"
                ).fetchall()
                return {row[0]: _decode_embedding(row[1]) for row in rows}
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_all failed", exc_info=True)
            return {}

    def get_all_readonly(self) -> dict[str, list[float]]:
        """Every stored embedding, WITHOUT touching the file (v1.108.185).

        ``_connect`` runs ``PRAGMA journal_mode = WAL`` and a CREATE-TABLE script
        on every connection, so even a pure read wrote to the database and bumped
        its mtime. That is the same defect ``runtime/confidence.py`` was fixed for,
        and on the fusion search path it had a sharper consequence than a wasted
        cache entry:

        ``_search_symbols_fusion`` probes for embeddings on every call, so the
        first fusion search in a process moved the ``.db`` mtime mid-scan. That
        made ``index_changed_since_load`` report ``channels.index: "rebuilding"``
        and ``moved_during_scan`` fire, which downgraded the verdict to
        ``degraded`` — so a genuine fusion absence could not reach ``absent`` at
        all, for a reason that was entirely self-inflicted.

        Returns an empty mapping when the file or the table is absent, which is
        the honest answer to "are there embeddings for this repo" and is exactly
        what the caller does with a failure anyway.

        ⚠ ``mode=ro`` alone is NOT enough, which cost an hour to establish:
        read-only in SQLite means "cannot modify the DATABASE", not "cannot
        touch the filesystem". A plain ``mode=ro`` connection to a WAL-mode
        database CREATES the ``-wal`` and ``-shm`` sidecars when they are
        absent, and ``_db_mtime_ns`` takes the max over the ``.db`` and the
        ``-wal`` — so the mtime moved anyway and the spurious `rebuilding`
        verdict came back. Measured: sidecars went None -> present across one
        fusion search.

        v1.108.185 answered that with an unconditional ``immutable=1`` and
        stated the trade-off it accepted: vectors in an un-checkpointed WAL go
        unread, which can only weaken a ranking. **#398 Arc 1 removes the
        trade-off rather than balancing it**, via
        ``storage.generation.connect_readonly``: read the WAL when its sidecar
        is already there (nothing is created that was not), read immutably when
        it is not (there is no WAL, so nothing is missed). See that function for
        the measurements on both halves — including the one the old wording
        understated, where an ``immutable=1`` reader raised "no such table"
        rather than merely returning fewer rows.
        """
        try:
            conn = _generation.connect_readonly(self._db_path)
        except Exception:
            logger.debug("EmbeddingStore.get_all_readonly could not open %s",
                         self._db_path, exc_info=True)
            return {}
        try:
            rows = conn.execute(
                "SELECT symbol_id, embedding FROM symbol_embeddings"
            ).fetchall()
            return {row[0]: _decode_embedding(row[1]) for row in rows}
        except Exception:
            logger.debug("EmbeddingStore.get_all_readonly failed", exc_info=True)
            return {}
        finally:
            conn.close()

    def iter_raw(self) -> list[tuple[str, bytes]]:
        """Every stored embedding as (symbol_id, raw float32 BLOB), undecoded.

        The one read path that does NOT pay ``_decode_embedding`` per row. It
        exists for ``storage.embedding_matrix``, which decodes the whole table
        once into a single normalised matrix and caches it — decoding to Python
        lists first would throw away the representation it wants and allocate
        ~8x the memory to do it (reported by @vondecron in #399).

        Same sidecar-aware read-only connection as ``get_all_readonly``, and for
        the same reason: see that method's docstring.

        Returns an empty list when the file or the table is absent, which is
        indistinguishable from "nothing embedded" on purpose — the caller's next
        move (fall back to no similarity channel) is the same either way. Use
        ``has_any()`` when the difference matters.
        """
        try:
            conn = _generation.connect_readonly(self._db_path)
        except Exception:
            logger.debug("EmbeddingStore.iter_raw could not open %s",
                         self._db_path, exc_info=True)
            return []
        try:
            return [
                (row[0], row[1])
                for row in conn.execute(
                    "SELECT symbol_id, embedding FROM symbol_embeddings"
                )
            ]
        except Exception:
            logger.debug("EmbeddingStore.iter_raw failed", exc_info=True)
            return []
        finally:
            conn.close()

    def get_many(self, symbol_ids) -> dict[str, list[float]]:
        """Embeddings for a NAMED set of symbols, without touching the file.

        For callers that already know which symbols can participate in their
        result. ``find_similar_symbols`` prefilters its pairs on non-semantic
        evidence and then used ``count()`` + ``get_all()``, reading every vector
        in the repository to keep the few percent its own prefilter had already
        selected. On a large repository that is the dominant cost of the call
        (reported by @rknighton in #398: 2.63% to 7.65% of fetched vectors were
        actually used).

        Uses the same sidecar-aware read-only connection as
        ``get_all_readonly`` and for the same reason: ``_connect`` runs a
        PRAGMA and a CREATE-TABLE on every connection, so a plain read bumps the
        database mtime and can make an unrelated scan report itself as
        rebuilding. See that method's docstring for why neither ``mode=ro`` nor
        ``immutable=1`` is right on its own.

        Chunked: an ``IN (...)`` clause is bounded by SQLITE_MAX_VARIABLE_NUMBER,
        which is 999 on older builds. A candidate set large enough to matter here
        is exactly the one that would overflow it.

        Returns only the ids that are present. A missing id is not an error, it
        is a symbol that was never embedded.
        """
        ids = [s for s in dict.fromkeys(symbol_ids) if s]
        if not ids:
            return {}
        try:
            conn = _generation.connect_readonly(self._db_path)
        except Exception:
            logger.debug("EmbeddingStore.get_many could not open %s",
                         self._db_path, exc_info=True)
            return {}
        out: dict[str, list[float]] = {}
        try:
            for start in range(0, len(ids), _GET_MANY_CHUNK):
                chunk = ids[start:start + _GET_MANY_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    "SELECT symbol_id, embedding FROM symbol_embeddings "
                    f"WHERE symbol_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    out[row[0]] = _decode_embedding(row[1])
            return out
        except Exception:
            logger.debug("EmbeddingStore.get_many failed", exc_info=True)
            return {}
        finally:
            conn.close()

    def has_any(self) -> Optional[bool]:
        """Does this repository have ANY embedding? Without touching the file.

        Three-state on purpose (v1.108.211, raised by @rknighton in #398):
        ``True`` / ``False`` / ``None`` for "could not establish".

        ``count()`` used to answer the repository-level half of this question as
        a side effect of guarding a fetch, and v1.108.210 removed it — correctly,
        because it opened a read-WRITE connection and paid a full ``COUNT(*)``
        scan. But removing it collapsed two states a caller can act on
        differently: *this repository has no embeddings at all* and *this
        repository is embedded, but not these candidates*. `find_similar_symbols`
        was telling both of them to run ``embed_repo``, which is wrong advice for
        the second.

        ``SELECT 1 ... LIMIT 1`` on the same sidecar-aware read-only connection
        as ``get_all_readonly``: no scan, no write, no sidecars created.

        ⚠⚠ **This method is why #398 Arc 1 stopped opening ``immutable=1``
        unconditionally.** Measured 2026-08-02: against a database whose table
        creation and rows were still in an un-checkpointed WAL, an
        ``immutable=1`` reader raised ``no such table: symbol_embeddings`` —
        and the branch below maps exactly that to ``False``. A repository that
        had just been embedded would have answered "this repository has no
        embeddings at all", with confidence, which is the false absence the
        evidence work exists to prevent. The un-checkpointed tail is not a
        weaker ranking here; it is the whole answer.

        ``False`` is reserved for the two cases that genuinely mean "nothing
        embedded": the database file is absent, or the table is. Anything else —
        a locked file, a corrupt page, a permission error — is ``None``, because
        answering ``False`` there is a guess about a repository we could not
        read.
        """
        try:
            if not Path(self._db_path).exists():
                return False
        except Exception:
            return None
        try:
            conn = _generation.connect_readonly(self._db_path)
        except Exception:
            logger.debug("EmbeddingStore.has_any could not open %s",
                         self._db_path, exc_info=True)
            return None
        try:
            row = conn.execute(
                "SELECT 1 FROM symbol_embeddings LIMIT 1"
            ).fetchone()
            return row is not None
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return False
            logger.debug("EmbeddingStore.has_any failed", exc_info=True)
            return None
        except Exception:
            logger.debug("EmbeddingStore.has_any failed", exc_info=True)
            return None
        finally:
            conn.close()

    def count(self) -> int:
        """Return the number of stored embeddings."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM symbol_embeddings"
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    # ── Write ──────────────────────────────────────────────────────────────

    def _invalidate_matrix(self) -> None:
        """Announce a write so caches over this database can drop it (#399).

        The matrix cache keys itself on the database's mtime/size stamp and
        would notice a write on its own — every writer here goes through
        ``_connect``, which bumps the file. This is the belt to that
        suspenders: a write and a read landing inside the same filesystem
        mtime granularity is a real window on Windows, and it is cheap to
        close from the side that knows a write happened.

        ⚠⚠ This used to `from . import embedding_matrix` and call it directly,
        which made the two modules a CYCLE: the matrix reads the store, and the
        store reached back into the matrix. The store has no business knowing a
        cache exists. It now announces the write and lets whoever cares
        subscribe, so the import arrow points one way.
        """
        for listener in tuple(_WRITE_LISTENERS):
            try:
                listener(self._db_path)
            except Exception:  # pragma: no cover - defensive
                logger.debug("embedding write listener failed", exc_info=True)

    def set_many(self, embeddings: dict[str, list[float]]) -> None:
        """Upsert multiple symbol embeddings in one transaction."""
        if not embeddings:
            return
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.executemany(
                "INSERT OR REPLACE INTO symbol_embeddings (symbol_id, embedding) VALUES (?, ?)",
                [(sid, _encode_embedding(vec)) for sid, vec in embeddings.items()],
            )
            conn.execute("COMMIT")
            self._invalidate_matrix()
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def delete_many(self, symbol_ids: list[str]) -> None:
        """Remove embeddings for specific symbols (e.g. after incremental reindex)."""
        if not symbol_ids:
            return
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            placeholders = ",".join("?" * len(symbol_ids))
            conn.execute(
                f"DELETE FROM symbol_embeddings WHERE symbol_id IN ({placeholders})",
                symbol_ids,
            )
            conn.execute("COMMIT")
            self._invalidate_matrix()
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def drop_orphan_stamp(self) -> None:
        """Remove the dimension, model and task type rows IF no vector is stored.

        For a writer that found a stamp beside no vectors (a store emptied
        before `clear()` removed the rows). ⚠ One statement, conditioned in
        SQL on the vectors table being empty, on the read-write connection:
        a writer deciding from an earlier reading and then calling `clear()`
        deleted vectors another client had written in between, and vectors a
        read-only reading of the wrong file had not seen (LEDGER L-121,
        review round 6; L-137). This cannot delete a vector.
        """
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM meta WHERE key IN (?, ?, ?) "
                "AND NOT EXISTS (SELECT 1 FROM symbol_embeddings)",
                (_EMBED_DIM_KEY, _EMBED_MODEL_KEY, _EMBED_TASK_TYPE_KEY),
            )
        except sqlite3.OperationalError as exc:
            # A database with no `meta` table has nothing recorded.
            if "no such table" not in str(exc).lower():
                raise
        finally:
            conn.close()

    def clear(self) -> None:
        """Delete all stored embeddings and what is recorded about them.

        Used by embed_repo with force=True. ⚠ The dimension, model and task
        type go with the vectors: a rebuild whose every batch then failed left
        an empty store stamped with the OLD model, the next writer wrote the
        new model's vectors under that stamp, and a reader of the stamp
        (`stale_reason`) then refused vectors the active model had built
        (LEDGER L-121, review round 3). An empty store has no stamp.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM symbol_embeddings")
            try:
                conn.execute(
                    "DELETE FROM meta WHERE key IN (?, ?, ?)",
                    (_EMBED_DIM_KEY, _EMBED_MODEL_KEY, _EMBED_TASK_TYPE_KEY),
                )
            except sqlite3.OperationalError as exc:
                # A database with no `meta` table has nothing recorded.
                if "no such table" not in str(exc).lower():
                    raise
            conn.execute("COMMIT")
            self._invalidate_matrix()
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
