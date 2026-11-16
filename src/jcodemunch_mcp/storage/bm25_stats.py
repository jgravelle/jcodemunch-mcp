"""BM25 corpus statistics stored inside the index .db (#1343, @tmhtech4).

A fresh process has no in-memory ``_bm25_cache``, so its first search used to
re-tokenize every symbol to rebuild idf, avgdl, the inverted lists and file
centrality. This module stores those four values in the index's own .db, in
the ``bm25_stats`` table, so a process that loads the same symbols reuses them.

Two writers:

* a full save computes them and writes them in the SAME transaction as the
  symbols (``write_in_txn``), so the stored row always belongs to the symbols
  committed with it;
* an incremental save or a summary write deletes them in ITS transaction
  (``clear_in_txn``), and the next search that has to compute them queues a
  write-back (``queue_write_back``). The call dispatcher runs it
  (``flush_pending``) after the response is sent and only when no other tool
  call is running in that server, and holds new calls until it is done, so
  the write never lands inside any call's before/after check on the .db.

A stored row is reused only when it describes what was loaded: ``digest`` is a
hash of every tokenized field of every symbol IN LOAD ORDER (the posting lists
are positions in ``index.symbols``), plus the files and the import graph
centrality is computed from (including the file-extension table, which the
user's ``extra_extensions`` setting changes), and ``fingerprint`` hashes the
code that tokenizes and resolves. So the same symbols in another order, a rewrite that
keeps the symbol count and the mtime, a writer that does not know this table,
and a release that changes the tokenizer or the resolver are all misses.

Reads are bounded and schema-checked; a damaged, oversized or unreadable row is
a miss, never an error and never a source of wrong statistics. No deserializer
that can run code is used.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import logging
import math
import threading
import zlib
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

TABLE = "bm25_stats"
_ROW_KEY = "corpus"

# Bump on any change to what the payload means.
_VERSION = 2
_ENGINE_ID = "jcodemunch-bm25-corpus-v2"

# Ceiling on the stored (compressed) row and on what it may inflate to. A
# 50,320-symbol index measured 3.7 MB of JSON, 1.3 MB compressed.
_MAX_STATS_BYTES = 512 * 1024 * 1024

# The code the stored statistics depend on, relative to the package root.
_FINGERPRINT_ROOTS = (
    "__init__.py",
    "storage/bm25_stats.py",
    "tools/search_symbols.py",
)
# Tokenizing goes through `retrieval/scoring.py` and centrality through
# `parser/imports.resolve_specifier`; every module either reaches through a
# package-relative import, at any depth, is fingerprinted too
# (`fingerprint_files`), so a release that changes only a helper two imports
# away still invalidates the stored statistics.
_FINGERPRINT_FOLLOW = ("retrieval/scoring.py", "parser/imports.py")

# What `retrieval.scoring._sym_tokens` builds a symbol's token bag from, plus
# `file`, which centrality reads.
_DIGEST_FIELDS = ("name", "signature", "summary", "docstring", "file")


def _relative_imports(root: Path, relative: str) -> set[str]:
    """Package files ``relative`` imports through a relative import."""
    tree = ast.parse((root / relative).read_bytes())
    parts = relative.split("/")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level < 1:
            continue
        base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
        if node.level - 1 > len(parts):
            continue
        stems = [node.module.split(".")] if node.module else [[a.name] for a in node.names]
        if node.module:
            stems += [node.module.split(".") + [a.name] for a in node.names]
        for stem in stems:
            for candidate in ("/".join(base + stem) + ".py", "/".join(base + stem + ["__init__.py"])):
                if (root / candidate).is_file():
                    found.add(candidate)
                    break
    return found


@functools.lru_cache(maxsize=1)
def fingerprint_files() -> tuple[str, ...]:
    """Every file the fingerprint hashes: the roots plus the import closure."""
    root = Path(__file__).resolve().parents[1]
    seen: set[str] = set(_FINGERPRINT_ROOTS)
    todo: list[str] = list(_FINGERPRINT_FOLLOW)
    while todo:
        relative = todo.pop()
        if relative in seen and relative not in _FINGERPRINT_FOLLOW:
            continue
        seen.add(relative)
        for nxt in _relative_imports(root, relative):
            if nxt not in seen:
                todo.append(nxt)
    return tuple(sorted(seen))


@functools.lru_cache(maxsize=1)
def semantics_fingerprint() -> Optional[str]:
    """A hash of the code the statistics depend on, or None if unreadable.

    None disables both reuse and storage: a row that cannot be tied to the
    code that would read it is not trusted.
    """
    try:
        root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256()
        for relative in fingerprint_files():
            digest.update(relative.encode("utf-8"))
            digest.update((root / relative).read_bytes())
        return digest.hexdigest()
    except SyntaxError:
        logger.debug("BM25 stats fingerprint unparseable; stored statistics disabled", exc_info=True)
        return None
    except OSError:
        logger.debug("BM25 stats fingerprint unreadable; stored statistics disabled", exc_info=True)
        return None


def corpus_digest(index) -> str:
    """Hash of everything the four statistics are computed from, in load order."""
    h = hashlib.sha256()
    for sym in index.symbols:
        row = [sym.get(f) or "" for f in _DIGEST_FIELDS]
        row.append(list(sym.get("keywords") or []))
        h.update(json.dumps(row, ensure_ascii=False).encode("utf-8", "surrogatepass"))
        h.update(b"\n")
    # A key with an empty value adds nothing to centrality, and a load writes
    # one for every file a save may have left out. Import order per file does
    # not change the counts, so imports are hashed sorted; alias and PSR-4
    # maps are tried in order, so their order is hashed as it stands.
    imports = {k: v for k, v in (getattr(index, "imports", None) or {}).items() if v}
    parts = [
        json.dumps(imports, sort_keys=True, ensure_ascii=False),
        json.dumps(getattr(index, "alias_map", None) or {}, ensure_ascii=False),
        json.dumps(getattr(index, "psr4_map", None) or {}, ensure_ascii=False),
        json.dumps(_extension_table(), sort_keys=True, ensure_ascii=False),
    ]
    for part in parts:
        h.update(part.encode("utf-8", "surrogatepass"))
        h.update(b"\n")
    return h.hexdigest()


def _extension_table() -> dict:
    """The effective extension-to-language table, which the user's
    ``extra_extensions`` setting extends and the resolver reads."""
    from ..parser.languages import get_language_extensions

    return dict(get_language_extensions())


def _is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _reject_json_constant(token: str):
    # The stdlib loader accepts NaN/Infinity by default; no value in this
    # payload may be non-finite.
    raise ValueError(f"non-finite JSON constant {token!r} is not permitted in BM25 stats")


def encode(digest: str, symbol_count: int, idf: dict, avgdl: float,
           inverted: dict, centrality: dict) -> Optional[bytes]:
    """The stored row, or None when it cannot be stored safely."""
    fingerprint = semantics_fingerprint()
    if fingerprint is None:
        return None
    payload = {
        "engine": _ENGINE_ID,
        "version": _VERSION,
        "fingerprint": fingerprint,
        "digest": digest,
        "symbol_count": int(symbol_count),
        "idf": idf,
        "avgdl": float(avgdl),
        "inverted": inverted,
        "centrality": centrality,
    }
    try:
        raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        logger.debug("BM25 stats not JSON-safe; not stored", exc_info=True)
        return None
    if len(raw) > _MAX_STATS_BYTES:
        logger.debug("BM25 stats exceed %d bytes; not stored", _MAX_STATS_BYTES)
        return None
    return zlib.compress(raw, 1)


def _validate(payload, digest: str, symbol_count: int) -> Optional[dict]:
    """The four statistics, or None if anything is off-schema or not ours."""
    if not isinstance(payload, dict):
        return None
    if payload.get("engine") != _ENGINE_ID or payload.get("version") != _VERSION:
        return None
    fingerprint = semantics_fingerprint()
    if fingerprint is None or payload.get("fingerprint") != fingerprint:
        return None
    if payload.get("digest") != digest:
        return None
    n = payload.get("symbol_count")
    if not isinstance(n, int) or isinstance(n, bool) or n != symbol_count:
        return None

    idf = payload.get("idf")
    avgdl = payload.get("avgdl")
    inverted = payload.get("inverted")
    centrality = payload.get("centrality")
    if not isinstance(idf, dict) or not isinstance(inverted, dict) or not isinstance(centrality, dict):
        return None
    if not isinstance(avgdl, (int, float)) or not _is_finite_number(avgdl) or avgdl < 0:
        return None
    for term, val in idf.items():
        if not isinstance(term, str) or not _is_finite_number(val):
            return None
    # Each posting list is ascending positions in range: `_compute_bm25`
    # appends each symbol once, in index order.
    for term, postings in inverted.items():
        if not isinstance(term, str) or not isinstance(postings, list):
            return None
        prev = -1
        for p in postings:
            if not isinstance(p, int) or isinstance(p, bool) or p <= prev or p >= n:
                return None
            prev = p
    # idf and inverted come from the same document-frequency loop.
    if idf.keys() != inverted.keys():
        return None
    for fname, val in centrality.items():
        if not isinstance(fname, str) or not _is_finite_number(val):
            return None
    return {"idf": idf, "avgdl": float(avgdl), "inverted": inverted, "centrality": centrality}


def decode(blob, digest: str, symbol_count: int) -> Optional[dict]:
    """Validate a stored row against what was loaded. Never raises."""
    try:
        if not isinstance(blob, (bytes, bytearray)) or not blob or len(blob) > _MAX_STATS_BYTES:
            return None
        inflater = zlib.decompressobj()
        raw = inflater.decompress(bytes(blob), _MAX_STATS_BYTES + 1)
        if len(raw) > _MAX_STATS_BYTES or not inflater.eof:
            return None
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        return _validate(payload, digest, symbol_count)
    except Exception:
        logger.debug("BM25 stats row unreadable; treating as a miss", exc_info=True)
        return None


def read_blob(db_path) -> Optional[bytes]:
    """The stored row, read without writing to the .db or its sidecars."""
    from .generation import connect_readonly

    try:
        conn = connect_readonly(db_path)
    except Exception:
        logger.debug("BM25 stats: could not open %s read-only", db_path, exc_info=True)
        return None
    try:
        row = conn.execute(f"SELECT value FROM {TABLE} WHERE key = ?", (_ROW_KEY,)).fetchone()
        return row[0] if row else None
    except Exception:
        # An index written before this table existed has none.
        logger.debug("BM25 stats: no row in %s", db_path, exc_info=True)
        return None
    finally:
        conn.close()


def load(db_path, index, digest: Optional[str] = None) -> Optional[dict]:
    """The stored statistics for ``index`` as loaded, or None on any miss."""
    if not db_path:
        return None
    blob = read_blob(db_path)
    if blob is None:
        return None
    return decode(blob, digest or corpus_digest(index), len(index.symbols))


def _ensure_table(conn) -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (key TEXT PRIMARY KEY, value BLOB NOT NULL)")


def write_in_txn(conn, blob: Optional[bytes]) -> None:
    """Replace the stored row inside the caller's open transaction."""
    _ensure_table(conn)
    conn.execute(f"DELETE FROM {TABLE}")
    if blob:
        conn.execute(f"INSERT INTO {TABLE} (key, value) VALUES (?, ?)", (_ROW_KEY, blob))


def clear_in_txn(conn) -> None:
    """Drop the stored row inside the caller's open transaction.

    Every write of symbols that does not recompute the statistics calls this,
    so a row never outlives the symbols it was computed from.
    """
    _ensure_table(conn)
    conn.execute(f"DELETE FROM {TABLE}")


# --- write-back after a search computed what was not stored ----------------

_MAX_PENDING = 4
_pending: list[tuple[Any, str, tuple]] = []
_pending_lock = threading.Lock()


def queue_write_back(index, digest: str, stats: tuple) -> None:
    """Remember statistics a search computed, for ``flush_pending``."""
    with _pending_lock:
        _pending[:] = [p for p in _pending if p[0] is not index]
        _pending.append((index, digest, stats))
        del _pending[:-_MAX_PENDING]


def pending_count() -> int:
    with _pending_lock:
        return len(_pending)


def flush_pending() -> int:
    """Store every queued result. Never raises; returns how many were stored."""
    with _pending_lock:
        work = list(_pending)
        _pending.clear()
    stored = 0
    for index, digest, (idf, avgdl, inverted, centrality) in work:
        try:
            if corpus_digest(index) != digest:
                continue  # the index changed in memory since the search
            blob = encode(digest, len(index.symbols), idf, avgdl, inverted, centrality)
            if blob is None:
                continue
            from .sqlite_store import store_bm25_stats_for_loaded_index

            if store_bm25_stats_for_loaded_index(index, blob):
                stored += 1
        except Exception:
            logger.debug("BM25 stats write-back failed", exc_info=True)
    return stored
