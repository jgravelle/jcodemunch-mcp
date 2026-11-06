"""Persist prepared lexical BM25 corpus statistics across processes.

Only the corpus-wide idf, avgdl, inverted lookup and centrality are stored.
Per-symbol token state remains lazy and is never restored from this file.
The generation key uses native database/committed-WAL load provenance,
symbol count, format version and the scoring-source fingerprint.

The sidecar is dot-prefixed so native repository discovery ignores it.
Reads are bounded and schema-validated; invalid or unverifiable data is a
cache miss. Writes publish a complete JSON payload atomically. Missing,
read-only or unverifiable source/storage falls back to native preparation.

Preparation remains lazy on first search. Index-time generation is a
possible follow-up; this module does not change indexing or scoring.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bump on any change to tokenization/BM25/centrality semantics. Belt-and-
# suspenders alongside _SEMANTICS_FINGERPRINT below (both are checked).
_BM25_DISK_CACHE_VERSION = 1

# Identifies this cache format/engine. Guards against a future on-disk
# format change (e.g. a different key set) being silently accepted just
# because the version integer happened to match.
_ENGINE_ID = "jcodemunch-bm25-disk-cache-safe-json-v1"

_SIDECAR_SUFFIX = ".bm25cache.json"

# Hard cap on sidecar size we are willing to read into memory. Generous for
# the real nodejs/node corpus (1,040,596 symbols) while bounding how much an
# oversized/garbage file can make a reader allocate. Chosen well above
# observed real sizes (tens of MB) and well below a reckless multi-GB file.
_MAX_SIDECAR_BYTES = 512 * 1024 * 1024  # 512 MiB


def _sidecar_path(db_path: str) -> Path:
    # Native list_repos treats root *.json as legacy indexes, but skips dotfiles.
    db = Path(db_path)
    return db.with_name('.' + db.name + _SIDECAR_SUFFIX)


def _semantics_fingerprint() -> Optional[str]:
    """Invalidate on scoring constants, helpers, resolver or engine changes.

    If source files cannot be read, disable persistence rather than trusting
    an unverifiable cache. Hashing files also leaves test instrumentation out
    of the semantic generation identifier.
    """
    try:
        root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256()
        for relative in ('__init__.py', 'retrieval/scoring.py',
                         'tools/search_symbols.py', 'parser/imports.py'):
            digest.update(relative.encode('utf-8'))
            digest.update((root / relative).read_bytes())
        return digest.hexdigest()
    except OSError:
        return None


def _is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _reject_json_constant(token: str):
    # json's stdlib loader, by default, silently accepts the non-standard
    # NaN/Infinity/-Infinity tokens and hands back float('nan')/float('inf').
    # Those are not valid BM25/centrality values (every number in this
    # payload must be finite) and must never round-trip through this cache
    # as if they were legitimate data.
    raise ValueError(f"non-finite JSON constant {token!r} is not permitted in BM25 disk cache")


def _validate_payload(payload: dict, mtime_ns: int, symbol_count: int) -> Optional[dict]:
    """Return the four validated stat dicts, or None if anything is off-schema."""
    if not isinstance(payload, dict):
        return None

    if payload.get("engine") != _ENGINE_ID:
        return None
    if payload.get("version") != _BM25_DISK_CACHE_VERSION:
        return None
    fingerprint = _semantics_fingerprint()
    if fingerprint is None or payload.get("semantics_fingerprint") != fingerprint:
        return None

    if not isinstance(payload.get("mtime_ns"), int) or isinstance(payload.get("mtime_ns"), bool):
        return None
    if payload["mtime_ns"] != int(mtime_ns):
        return None

    if not isinstance(payload.get("symbol_count"), int) or isinstance(payload.get("symbol_count"), bool):
        return None
    if payload["symbol_count"] != int(symbol_count):
        return None
    n = payload["symbol_count"]

    idf = payload.get("idf")
    avgdl = payload.get("avgdl")
    inverted = payload.get("inverted")
    centrality = payload.get("centrality")

    if not isinstance(idf, dict) or not isinstance(inverted, dict) or not isinstance(centrality, dict):
        return None
    if not _is_finite_number(avgdl) or avgdl < 0:
        return None

    # idf: term(str) -> finite float
    for term, val in idf.items():
        if not isinstance(term, str) or not _is_finite_number(val):
            return None

    # inverted: term(str) -> list[int] posting list, each index in-range,
    # strictly increasing (matches how _compute_bm25 appends: once per
    # symbol, in ascending symbol-index order, deduplicated per symbol).
    for term, postings in inverted.items():
        if not isinstance(term, str) or not isinstance(postings, list):
            return None
        prev = -1
        for p in postings:
            if not isinstance(p, int) or isinstance(p, bool):
                return None
            if p <= prev or p < 0 or p >= n:
                return None
            prev = p

    # Matching term keys: every term present in one must be present in the
    # other, and vice versa — idf/inverted are built from the SAME df loop
    # in _compute_bm25 so they must describe exactly the same vocabulary.
    if set(idf.keys()) != set(inverted.keys()):
        return None

    # centrality: file(str) -> finite float
    for fname, val in centrality.items():
        if not isinstance(fname, str) or not _is_finite_number(val):
            return None

    return {
        "idf": idf,
        "avgdl": float(avgdl),
        "inverted": inverted,
        "centrality": centrality,
    }


def load_disk_bm25_cache(
    db_path: Optional[str], mtime_ns: Optional[int], symbol_count: int,
) -> Optional[dict]:
    """Return the persisted corpus stats dict, or None on any miss/mismatch.

    Never raises: a corrupt, missing, oversized, schema-invalid, stale, or
    version/semantics-mismatched sidecar is a cache MISS (falls back to full
    recomputation), never an error and never a source of wrong data. No
    pickle or other arbitrary-code-executing deserializer is used anywhere
    in this function.
    """
    if not db_path or mtime_ns is None:
        return None
    path = _sidecar_path(db_path)
    try:
        if not path.exists() or not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > _MAX_SIDECAR_BYTES:
            logger.debug(
                "BM25 disk cache at %s has disallowed size %d bytes; treating as miss",
                path, size,
            )
            return None
        with path.open("rb") as stream:
            raw = stream.read(_MAX_SIDECAR_BYTES + 1)
        if len(raw) > _MAX_SIDECAR_BYTES:
            return None
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except Exception:
        logger.debug("BM25 disk cache unreadable at %s; treating as miss", path, exc_info=True)
        return None

    try:
        return _validate_payload(payload, mtime_ns, symbol_count)
    except Exception:
        logger.debug("BM25 disk cache failed schema validation at %s; treating as miss", path, exc_info=True)
        return None


def save_disk_bm25_cache(
    db_path: Optional[str],
    mtime_ns: Optional[int],
    symbol_count: int,
    idf: dict,
    avgdl: float,
    inverted: dict,
    centrality: dict,
) -> None:
    """Best-effort persist. Never raises — a failed write just means the next
    cold process recomputes, exactly today's behaviour (including on a
    read-only store/filesystem, where ``tempfile.mkstemp``/``os.replace``
    simply fail and are swallowed below).

    Writes JSON to a temp file in the SAME directory as the sidecar, then
    ``os.replace()``s it into place. ``os.replace`` is atomic on both POSIX
    and Windows, so a reader (this process or another) only ever observes
    either the previous complete sidecar or the new complete one — never a
    half-written file, including under concurrent writers.
    """
    if not db_path or mtime_ns is None:
        return
    path = _sidecar_path(db_path)
    fingerprint = _semantics_fingerprint()
    if fingerprint is None:
        return
    payload = {
        "engine": _ENGINE_ID,
        "version": _BM25_DISK_CACHE_VERSION,
        "semantics_fingerprint": fingerprint,
        "mtime_ns": int(mtime_ns),
        "symbol_count": int(symbol_count),
        "idf": idf,
        "avgdl": float(avgdl),
        "inverted": inverted,
        "centrality": centrality,
    }
    try:
        encoded = json.dumps(payload, allow_nan=False)
    except Exception:
        # A non-finite value or non-JSON-serializable type made it into the
        # stats dicts — this would have produced an invalid cache we could
        # never safely read back (NaN/Infinity are rejected on load). Skip
        # the write entirely rather than persist something load will reject
        # anyway; the in-memory cache this process just built is unaffected.
        logger.debug("BM25 disk cache payload not JSON-safe; skipping write", exc_info=True)
        return
    if len(encoded.encode("utf-8")) > _MAX_SIDECAR_BYTES:
        logger.debug("BM25 disk cache payload exceeds %d bytes; skipping write", _MAX_SIDECAR_BYTES)
        return
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(encoded)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug("BM25 disk cache write failed at %s; continuing without it", path, exc_info=True)
