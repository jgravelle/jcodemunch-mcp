"""Process-local cache of a repository's normalised embedding matrix (#399).

Reported by @vondecron: every semantic ``search_symbols`` call re-read and
re-decoded **every** stored vector out of SQLite, then scored them with a
pure-Python cosine that recomputed the query vector's norm once per symbol. On
a 30,479-symbol index that is ~2.5 s of per-query cost that survives no matter
how warm the process is.

Two things happen here, and they are separable on purpose:

1. **The decode is paid once.** ``load()`` reads the raw float32 BLOBs, builds
   one L2-normalised matrix, and caches it against a stamp of the database's
   size + mtime (``.db`` and a ``-wal`` that holds frames). Every write path
   on ``EmbeddingStore`` goes through ``_connect``, which bumps that stamp, and
   also calls ``invalidate()`` directly.

2. **The scoring pass is vectorised when it can be.** Vectors are stored
   pre-normalised, so cosine collapses to a dot product and the query's norm is
   hoisted out of the loop. With numpy importable that dot product is one
   ``matrix @ q``; without it, the fallback is the same pure-Python loop minus
   the two redundant norms.

⚠ **numpy is not a new dependency and must never become one.** The fallback is
load-bearing: ``_scores_python`` is exercised by its own test with numpy forced
absent. What makes the fast path nearly free is that the bundled ``local_onnx``
provider already pulls numpy in transitively via onnxruntime, so the
zero-config configuration gets it without installing anything.

⚠ **Rows are stored as ``array.array('f')``, not ``list[float]``, in the
fallback.** A list of 384 Python floats is ~8x the memory of the same 384
float32s, and this cache holds the whole table for the life of the process —
the representation that was fine to allocate per-call and throw away is not
fine to keep.

Memory is bounded two ways: at most ``_MAX_CACHED_REPOS`` repositories, and
``JCODEMUNCH_EMBED_MATRIX_CACHE=0`` disables retention entirely (matrices are
still built per call, so scoring stays fast and only the decode is re-paid).
"""

import array
import logging
import math
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Repositories whose matrix may be held at once. A 30k-symbol / 384-dim index is
# ~46 MB as float32, so two is a deliberate ceiling rather than a round number:
# an agent hopping between a handful of repos should re-decode, not accumulate.
_MAX_CACHED_REPOS = 2

_cache: "OrderedDict[str, _Entry]" = OrderedDict()
_lock = threading.Lock()


def _numpy():
    """Return the numpy module, or None.

    ⚠ Deliberately NOT cached in a module global as "tried and failed": an
    import that fails once fails cheaply, and caching the failure means a
    process that gains numpy mid-life (an editable install, a test that
    manipulates ``sys.modules``) can never see it.
    """
    try:
        import numpy  # type: ignore[import]
        return numpy
    except Exception:
        return None


def _cache_enabled() -> bool:
    raw = os.environ.get("JCODEMUNCH_EMBED_MATRIX_CACHE", "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _stamp(db_path) -> Optional[tuple]:
    """Identity of the database file *and its WAL sidecars*, for invalidation.

    ⚠ The sidecars are not optional here. A write commits into ``-wal`` and may
    not touch ``.db`` until a checkpoint, so a stamp over the main file alone
    would hold a stale matrix across exactly the write this cache must notice —
    a freshly-embedded symbol scoring 0.0 forever. Same reasoning as
    ``storage.generation.wal_sidecar_present``.

    Returns None when the main file is absent (nothing to cache).
    """
    p = Path(db_path)
    try:
        st = p.stat()
    except OSError:
        return None
    # A commit is seen through a non-empty -wal; an empty -wal and -shm are what
    # a reader in any process leaves behind, and counting them threw the matrix
    # away on every read (generation.committed_wal_stat holds the rule).
    from .generation import committed_wal_stat

    wal = committed_wal_stat(p)
    return ((st.st_size, st.st_mtime_ns), (wal.st_size, wal.st_mtime_ns) if wal is not None else None)


class EmbeddingMatrix:
    """A repository's embeddings, decoded once and L2-normalised.

    ``ids`` preserves SQLite row order so that equal scores tie-break exactly as
    the previous dict-iteration order did.
    """

    __slots__ = ("ids", "_id_set", "dim", "_np", "_matrix", "_rows", "skipped_dim_mismatch")

    def __init__(self, ids, dim, np_module, matrix, rows, skipped_dim_mismatch=0):
        self.ids: list[str] = ids
        self._id_set = set(ids)
        self.dim = dim
        self._np = np_module
        self._matrix = matrix          # numpy (n, dim) float32, normalised — or None
        self._rows = rows              # list[array.array('f')], normalised — or None
        self.skipped_dim_mismatch = skipped_dim_mismatch

    def __len__(self) -> int:
        return len(self.ids)

    def __contains__(self, symbol_id: str) -> bool:
        return symbol_id in self._id_set

    @property
    def id_set(self) -> set:
        return self._id_set

    @property
    def vectorised(self) -> bool:
        return self._matrix is not None

    def score_all(self, query_vec) -> dict:
        """Cosine similarity of ``query_vec`` against every stored vector.

        Returns ``{symbol_id: cosine}``. A caller reads it with
        ``.get(sym_id, 0.0)`` — a missing id means "never embedded", which is
        the same 0.0 the per-symbol path produced.

        Returns ``{}`` for a zero-length, zero-norm, or wrong-dimension query,
        which is what ``_cosine_similarity`` answered for each of those cases
        one symbol at a time.
        """
        if not query_vec or len(query_vec) != self.dim:
            return {}
        norm = math.sqrt(sum(float(x) * float(x) for x in query_vec))
        if norm == 0.0:
            return {}
        q = [float(x) / norm for x in query_vec]
        if self._matrix is not None:
            return self._scores_numpy(q)
        return self._scores_python(q)

    def _scores_numpy(self, q) -> dict:
        np = self._np
        qv = np.asarray(q, dtype=np.float32)
        return dict(zip(self.ids, self._matrix.dot(qv).tolist()))

    def _scores_python(self, q) -> dict:
        """Pure-Python scoring — no numpy, no per-symbol norms.

        Still O(n·dim), but it is the shipped loop with both redundant
        ``math.sqrt(sum(...))`` passes hoisted out (the stored side at load
        time, the query side once above).
        """
        out: dict = {}
        rows = self._rows or []
        for sid, row in zip(self.ids, rows):
            out[sid] = sum(x * y for x, y in zip(row, q))
        return out


class _Entry:
    __slots__ = ("stamp", "matrix")

    def __init__(self, stamp, matrix):
        self.stamp = stamp
        self.matrix = matrix


def _build(raw_rows) -> Optional[EmbeddingMatrix]:
    """Decode + normalise raw ``(symbol_id, float32 blob)`` rows."""
    if not raw_rows:
        return None

    itemsize = array.array("f").itemsize
    dim = len(raw_rows[0][1]) // itemsize
    if dim <= 0:
        return None

    np = _numpy()
    expected_bytes = dim * itemsize
    ids: list[str] = []
    skipped = 0

    if np is not None:
        kept: list[bytes] = []
        for sid, blob in raw_rows:
            # A row of a different width is a store written by a different
            # model. `_cosine_similarity` returned 0.0 for those; excluding them
            # here reaches the same answer through `.get(sid, 0.0)`.
            if len(blob) != expected_bytes:
                skipped += 1
                continue
            ids.append(sid)
            kept.append(blob)
        if not ids:
            return None
        # ⚠ `np.float32`, not `'<f4'`: the blobs come from `array.array('f')`,
        # which writes NATIVE byte order. Pinning little-endian would decode
        # garbage on a big-endian host rather than failing visibly.
        mat = np.empty((len(ids), dim), dtype=np.float32)
        for i, blob in enumerate(kept):
            mat[i] = np.frombuffer(blob, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1)
        # A zero vector stays zero: cosine against it is 0.0, which is what the
        # per-symbol path answered. Dividing by 1.0 there keeps it that way
        # without emitting a nan that would poison the ranking.
        norms[norms == 0.0] = 1.0
        mat /= norms[:, None]
        if skipped:
            logger.debug("embedding_matrix: skipped %d rows of mismatched width", skipped)
        return EmbeddingMatrix(ids, dim, np, mat, None, skipped)

    rows: list = []
    for sid, blob in raw_rows:
        if len(blob) != expected_bytes:
            skipped += 1
            continue
        vec: array.array = array.array("f")
        vec.frombytes(blob)
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            inv = 1.0 / norm
            for i in range(dim):
                vec[i] = vec[i] * inv
        ids.append(sid)
        rows.append(vec)
    if not ids:
        return None
    if skipped:
        logger.debug("embedding_matrix: skipped %d rows of mismatched width", skipped)
    return EmbeddingMatrix(ids, dim, None, None, rows, skipped)


def get_matrix(db_path) -> Optional[EmbeddingMatrix]:
    """Normalised embedding matrix for ``db_path``, cached across calls.

    Returns None when the repository has no embeddings (or the store could not
    be read) — the caller's move is the same for both, and ``has_any()`` is the
    tri-state answer when the difference matters.

    Never raises: a failure here must degrade the ranking, not fail the search.
    """
    from .embedding_store import EmbeddingStore  # noqa: PLC0415 (cycle)

    key = str(db_path)
    stamp = _stamp(db_path)
    if stamp is None:
        return None

    caching = _cache_enabled()
    if caching:
        with _lock:
            entry = _cache.get(key)
            if entry is not None and entry.stamp == stamp:
                _cache.move_to_end(key)
                return entry.matrix

    try:
        raw = EmbeddingStore(Path(db_path)).iter_raw()
        matrix = _build(raw)
    except Exception:
        logger.debug("embedding_matrix: build failed for %s", key, exc_info=True)
        return None
    if matrix is None:
        return None

    if caching:
        with _lock:
            # Re-stamp: the read above is read-only, but a concurrent writer is
            # not. Caching against the pre-read stamp would pin a matrix that
            # was already superseded while it was being decoded.
            fresh = _stamp(db_path)
            if fresh == stamp:
                _cache[key] = _Entry(stamp, matrix)
                _cache.move_to_end(key)
                while len(_cache) > _MAX_CACHED_REPOS:
                    _cache.popitem(last=False)
    return matrix


def invalidate(db_path=None) -> None:
    """Drop the cached matrix for one database, or all of them."""
    with _lock:
        if db_path is None:
            _cache.clear()
            return
        _cache.pop(str(db_path), None)


def cache_stats() -> dict:
    """Introspection for tests and diagnostics. Reads nothing off disk."""
    with _lock:
        return {
            "enabled": _cache_enabled(),
            "repos": len(_cache),
            "max_repos": _MAX_CACHED_REPOS,
            "vectors": sum(len(e.matrix) for e in _cache.values()),
            "numpy": _numpy() is not None,
        }


# ⚠⚠ Subscribe rather than being called. `embedding_store` used to import this
# module to invalidate the cache after a write, which made the pair a cycle. The
# arrow now points one way -- a cache depends on its store, never the reverse.
#
# ⚠ Registration happens on IMPORT of this module. A process that writes
# embeddings without ever importing the matrix gets no notification, and that is
# harmless by construction: with no matrix imported there is no cached matrix to
# drop. The size+mtime stamp in `get_matrix` remains the primary guard; this is
# the belt to that suspenders, exactly as before.
try:  # pragma: no cover - registration side effect
    from .embedding_store import register_write_listener as _register

    _register(invalidate)
except Exception:  # pragma: no cover - defensive
    logger.debug("could not subscribe the matrix cache to store writes", exc_info=True)
