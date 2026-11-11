"""How many tokens a response saved, counted rather than divided by 4.

``tokens_saved`` used to divide saved bytes by 4. Measured over the pinned
64-repo corpus in ``benchmarks/savings_tokenizer/`` (``corpus.json`` names
every repo and SHA), Claude's tokenizer reads 2.445 bytes per token overall,
so bytes/4 undercounted on Claude, and a single bytes/token figure per
language still missed individual repos by 7% to 24% (a docstring-heavy Python
repo reads far fewer tokens per byte than the language's average).

So the count is a real one. Each indexed file's ``cl100k_base`` token count
is taken once, when the index is saved (``storage/sqlite_store``'s
``token_counts`` table, keyed by content hash), and a response's own text is
counted when it is served. ``savings_tokenizer`` (config, default
``"claude"``) picks the unit:

- ``"openai"``: the cl100k_base count itself.
- ``"claude"``: the cl100k_base count times the measured Claude/cl100k ratio
  for the file's language (``RATIOS``), or the overall ratio for a language
  not measured. Over the corpus this lands within 0.95-1.07 of Claude's own
  count for 80% of (repo, language) samples, against 0.90-1.10 for the
  bytes/token table (``benchmarks/savings_tokenizer/ratios.py``).

Where a count is unavailable (an index saved before counts existed, or the
encoding cannot load), the bytes/token table for the same unit is the
fallback, and ``tokens_saved_basis`` on the response says which path was
used, or that it was mixed.

⚠⚠ **ONE NETWORK CALL, IN THE BACKGROUND, AND IT CAN BE TURNED OFF.**
The encoding is tiktoken's own: ``tiktoken.get_encoding("cl100k_base")``
fetches ``cl100k_base.tiktoken`` from ``openaipublic.blob.core.windows.net``
the first time it is needed and keeps it in tiktoken's cache
(``TIKTOKEN_CACHE_DIR``, else ``<temp>/data-gym-cache``); tiktoken checks the
file against its pinned SHA-256. A copy already in that cache is loaded in the
calling thread, which touches no network. A missing copy is fetched on a
daemon thread, never in a call: until it arrives, and whenever
``tokenizer_download`` is false or the fetch fails, ``encoding()`` returns
None and the bytes/token table is used, with ``tokens_saved_basis`` naming why
(``state()``). With the download off, a cached copy is used only after this
module has checked its hash, because tiktoken RE-FETCHES a cached file whose
hash does not match.

⚠⚠ **The table rows and ratios below are COPIED from the measurement files
and a test keeps them equal in both directions.** To change a figure, re-run
``measure.py`` / ``ratios.py`` and paste their published rows; never edit a
number here by hand. A language measured in fewer than two repos is not
published and reads the overall figure.

A leaf: stdlib plus ``tiktoken`` (imported lazily); ``config`` is read at call time.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT = "claude"

#: cl100k_base as tiktoken fetches it (tiktoken_ext/openai_public.py). The URL
#: is also tiktoken's cache key (sha1 of it), which is how ``_cached_copy``
#: finds a copy without asking tiktoken, whose lookup fetches on a miss.
ENCODING_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
ENCODING_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"

#: Claude tokens per cl100k_base token over the same chunks
#: (``benchmarks/savings_tokenizer/claude_over_cl100k.json``).
RATIOS: dict = {
    "overall": 1.665,
    "languages": {
        "bash": 1.719,
        "c": 1.671,
        "cpp": 1.713,
        "csharp": 1.856,
        "css": 1.544,
        "dart": 1.718,
        "elixir": 1.521,
        "erb": 1.466,
        "erlang": 1.669,
        "go": 1.718,
        "html": 1.536,
        "java": 1.777,
        "javascript": 1.64,
        "json": 1.461,
        "kotlin": 1.8,
        "nix": 1.575,
        "php": 1.616,
        "powershell": 1.698,
        "python": 1.544,
        "ruby": 1.565,
        "rust": 1.659,
        "scala": 1.726,
        "swift": 1.76,
        "toml": 1.555,
        "tsx": 1.663,
        "typescript": 1.649,
        "xml": 1.724,
        "yaml": 1.584,
    },
}

#: Bytes per token, the fallback when no count is available.
TABLES: dict[str, dict] = {
    "claude": {
        "overall": 2.445,
        "languages": {
            "bash": 2.143,
            "c": 2.135,
            "cpp": 2.219,
            "csharp": 2.746,
            "css": 2.117,
            "dart": 2.486,
            "elixir": 2.411,
            "erb": 2.099,
            "erlang": 1.998,
            "go": 2.132,
            "html": 2.319,
            "java": 2.57,
            "javascript": 2.402,
            "json": 2.546,
            "kotlin": 2.501,
            "nix": 2.191,
            "php": 2.75,
            "powershell": 2.385,
            "python": 2.604,
            "ruby": 2.668,
            "rust": 2.469,
            "scala": 2.275,
            "swift": 2.697,
            "toml": 2.244,
            "tsx": 2.58,
            "typescript": 2.428,
            "xml": 2.365,
            "yaml": 2.396,
        },
    },
    "openai": {
        "overall": 4.07,
        "languages": {
            "bash": 3.683,
            "c": 3.567,
            "cpp": 3.802,
            "csharp": 5.095,
            "css": 3.268,
            "dart": 4.27,
            "elixir": 3.667,
            "erb": 3.077,
            "erlang": 3.334,
            "go": 3.663,
            "html": 3.562,
            "java": 4.567,
            "javascript": 3.941,
            "json": 3.72,
            "kotlin": 4.5,
            "nix": 3.451,
            "php": 4.443,
            "powershell": 4.05,
            "python": 4.021,
            "ruby": 4.176,
            "rust": 4.097,
            "scala": 3.927,
            "swift": 4.748,
            "toml": 3.49,
            "tsx": 4.29,
            "typescript": 4.004,
            "xml": 4.077,
            "yaml": 3.795,
        },
    },
}

BASIS = {
    (
        "claude",
        "count",
    ): "claude tokens: cl100k_base count x measured claude/cl100k ratio per language",
    ("openai", "count"): "openai cl100k_base token count",
    (
        "claude",
        "table",
    ): "claude tokens: measured bytes/token per language (no token count available)",
    (
        "openai",
        "table",
    ): "openai cl100k_base: measured bytes/token per language (no token count available)",
}


def active() -> str:
    """The configured unit's name; anything unrecognised reads ``DEFAULT``."""
    try:
        from . import config

        name = config.get("savings_tokenizer", DEFAULT)
    except Exception:
        logger.debug("savings_tokenizer unreadable; using %s", DEFAULT, exc_info=True)
        return DEFAULT
    if isinstance(name, str) and name in TABLES:
        return name
    if name is not None:
        logger.debug("savings_tokenizer %r is not a table; using %s", name, DEFAULT)
    return DEFAULT


def _unit(tokenizer: Optional[str]) -> str:
    return tokenizer if tokenizer in TABLES else active()


def bytes_per_token(
    language: Optional[str] = None, tokenizer: Optional[str] = None
) -> float:
    table = TABLES[_unit(tokenizer)]
    if language:
        row = table["languages"].get(language)
        if row:
            return row
    return table["overall"]


def ratio(language: Optional[str] = None) -> float:
    """Claude tokens per cl100k_base token for ``language``."""
    if language:
        row = RATIOS["languages"].get(language)
        if row:
            return row
    return RATIOS["overall"]


def basis(tokenizer: Optional[str] = None, path: str = "table") -> str:
    """What a ``tokens_saved`` figure was counted in, in words a reader can check."""
    return BASIS[(_unit(tokenizer), path)]


# --- the encoding -----------------------------------------------------------

_ENCODING = None
_STATE = "not_loaded"
_LOCK = threading.Lock()
_FETCH_THREAD: Optional[threading.Thread] = None

#: Why ``encoding()`` returned None, for ``tokens_saved_basis``.
STATE_REASONS = {
    "download_off": "tokenizer_download is off and tiktoken has no cached cl100k_base",
    "downloading": "cl100k_base is being fetched in the background",
    "download_failed": "the cl100k_base fetch failed (offline?)",
    "unavailable": "tiktoken could not load cl100k_base",
}


def download_allowed() -> bool:
    """``tokenizer_download`` (config, env ``JCODEMUNCH_TOKENIZER_DOWNLOAD``)."""
    try:
        from . import config

        return config.get("tokenizer_download", True) is not False
    except Exception:
        logger.debug("tokenizer_download unreadable; fetch allowed", exc_info=True)
        return True


def _cache_dir() -> Optional[str]:
    if "TIKTOKEN_CACHE_DIR" in os.environ:
        return os.environ["TIKTOKEN_CACHE_DIR"] or None
    if "DATA_GYM_CACHE_DIR" in os.environ:
        return os.environ["DATA_GYM_CACHE_DIR"] or None
    return os.path.join(tempfile.gettempdir(), "data-gym-cache")


def _cached_copy() -> bool:
    """True when tiktoken's cache holds a cl100k_base file with the pinned hash."""
    d = _cache_dir()
    if not d:
        return False
    path = os.path.join(d, hashlib.sha1(ENCODING_URL.encode()).hexdigest())
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() == ENCODING_SHA256
    except OSError:
        return False


def _load() -> None:
    """Build the encoding through tiktoken; sets ``_ENCODING`` and ``_STATE``."""
    global _ENCODING, _STATE
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        logger.info(
            "cl100k_base could not be loaded; tokens_saved uses the bytes/token table",
            exc_info=True,
        )
        _STATE = "download_failed"
        return
    _ENCODING = enc
    _STATE = "ready"


def encoding(wait: Optional[float] = None):
    """The cl100k_base encoding, or None while it is unavailable. Never blocks on the network.

    A cached copy loads here, in the caller's thread. Otherwise, when
    ``tokenizer_download`` allows, one daemon thread fetches it and this
    returns None (``state()`` says why). ``wait`` (seconds) lets a caller that
    can afford it, such as a benchmark, wait for that fetch.
    """
    global _STATE, _FETCH_THREAD
    if _ENCODING is not None:
        return _ENCODING
    with _LOCK:
        if _ENCODING is not None:
            return _ENCODING
        if _STATE in ("not_loaded", "download_off"):
            try:
                import tiktoken  # noqa: F401
            except Exception:
                logger.debug("tiktoken not importable", exc_info=True)
                _STATE = "unavailable"
                return None
            if _cached_copy():
                _load()
                if _ENCODING is None:
                    _STATE = "unavailable"
                return _ENCODING
            if not download_allowed():
                _STATE = "download_off"
                return None
            _STATE = "downloading"
            _FETCH_THREAD = threading.Thread(
                target=_load, name="jcodemunch-cl100k-fetch", daemon=True
            )
            _FETCH_THREAD.start()
        thread = _FETCH_THREAD
    if wait and thread is not None:
        thread.join(wait)
    return _ENCODING


def state() -> str:
    """``ready``, ``not_loaded``, or a key of ``STATE_REASONS``."""
    return _STATE


def _reset_for_tests() -> None:
    global _ENCODING, _STATE, _FETCH_THREAD
    with _LOCK:
        _ENCODING = None
        _STATE = "not_loaded"
        _FETCH_THREAD = None


def count(text: str) -> Optional[int]:
    """cl100k_base tokens in ``text``, or None when the encoding is unavailable."""
    enc = encoding()
    if enc is None:
        return None
    try:
        return len(enc.encode_ordinary(text))
    except Exception:
        logger.debug("token count failed", exc_info=True)
        return None


def count_many(texts: list[str]) -> Optional[list[int]]:
    """cl100k_base tokens for each text, or None when the encoding is unavailable."""
    enc = encoding()
    if enc is None:
        return None
    try:
        return [len(t) for t in enc.encode_ordinary_batch(texts, num_threads=4)]
    except Exception:
        logger.debug("batch token count failed", exc_info=True)
        return None


# --- crediting ---------------------------------------------------------------


def credit(
    raw_files: Iterable[tuple[Optional[str], int, Optional[int]]],
    served: Iterable[tuple[Optional[str], str]],
) -> tuple[int, str]:
    """Tokens saved and the basis they were counted in.

    ``raw_files``: ``(language, size_bytes, cl100k_count_or_None)`` for each
    file the caller would otherwise have read. ``served``: ``(language,
    text)`` for each piece of the response. A file with a stored count is
    counted; one without is divided by the table, and the basis says so.
    """
    name = active()
    counted = fallback = 0
    raw = 0.0
    for lang, size, n in raw_files:
        if n is not None:
            raw += n if name == "openai" else n * ratio(lang)
            counted += 1
        else:
            raw += (size or 0) / bytes_per_token(lang, name)
            fallback += 1
    sent = 0.0
    served_counted = True
    for lang, text in served:
        n = count(text)
        if n is None:
            served_counted = False
            sent += len(text.encode("utf-8")) / bytes_per_token(lang, name)
        else:
            sent += n if name == "openai" else n * ratio(lang)
    if fallback == 0 and served_counted:
        why = basis(name, "count")
    elif counted == 0:
        why = basis(name, "table")
    else:
        why = f"{basis(name, 'count')} for {counted} of {counted + fallback} files; measured bytes/token for the rest"
    if (fallback or not served_counted) and state() in STATE_REASONS:
        why = f"{why} ({STATE_REASONS[state()]})"
    return max(0, int(raw - sent)), why


def served_text(row) -> str:
    """A served row as the bytes it costs on the wire (compact JSON)."""
    import json

    try:
        return json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str)
    except Exception:
        logger.debug("served row not serializable", exc_info=True)
        return ""


def credit_files(
    index, files: Iterable[str], served: Iterable[tuple[Optional[str], str]]
) -> tuple[int, str]:
    """``credit`` for files of a loaded index: counts come from the index's store.

    ``files``: the distinct indexed paths the caller would otherwise have
    read. Their sizes, languages and content hashes come from the index; their
    counts from ``storage.token_counts`` (one read-only query). ``served``:
    ``(language, text)`` pieces of the response.
    """
    from .storage.token_counts import lookup

    files = list(dict.fromkeys(files))
    hashes = getattr(index, "file_hashes", None) or {}
    langs = getattr(index, "file_languages", None) or {}
    sizes = getattr(index, "file_sizes", None) or {}
    counts = lookup(getattr(index, "_db_path", None), (hashes.get(f) for f in files))
    raw = [
        (langs.get(f), sizes.get(f, 0) or 0, counts.get(hashes.get(f) or ""))
        for f in files
    ]
    return credit(raw, served)
