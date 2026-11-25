"""A file's byte size, from the index or from the index's own copy of the file.

`search_symbols`, `search_text`, `get_file_content`, `get_ranked_context`,
`get_repo_map` and `get_symbol_importance` credit savings against
`index.file_sizes` (since v1.10.23, which replaced a stat per result with the
stored size). Only a save that is handed the file CONTENTS stored a size, and
a full `index_folder` writes the contents to the content dir and passes the
save none, so every file a full index wrote had no size and each of those
tools credited 0 tokens. An install that only searched never had a total to
report.

Two halves, both here so the rule lives once:

- :func:`sizes_from_content` fills a save's missing sizes from the content dir
  the indexer has already written, so a new index stores a size for every file.
- :class:`ContentSizes` is the map the loaded index carries. A stored size is
  served as stored; a missing one is read from the content dir copy once and
  kept, so an index built before this fix credits without a re-index. A file
  with no copy is remembered as a miss and never stat'ed twice. Nothing is
  written back: a read path never writes the index.

The in-memory patch paths (`incremental_save`, the branch compose) rebuild
the map as a plain dict; :func:`carry_fallback` is what they call so the
patched index keeps the fallback instead of losing it at the first hook save.

A path is confined by ``security.resolve_within``, the rule that placed the
copy, never by a string test of our own.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


def _content_size(
    content_dir: Optional[Path], rel: str, base_resolved: Optional[str] = None
) -> Optional[int]:
    """Size of the indexed copy of ``rel``, or None when there is none to read."""
    if content_dir is None or not rel:
        return None
    # Imported here: `security` imports `config`, and this module stays a leaf.
    from ..security import resolve_within

    try:
        path = resolve_within(content_dir, rel, base_resolved=base_resolved)
    except (OSError, ValueError):
        return None
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _resolved(content_dir: Optional[Path]) -> Optional[str]:
    if content_dir is None:
        return None
    try:
        return str(content_dir.resolve())
    except (OSError, ValueError):
        logger.debug("content dir did not resolve: %s", content_dir, exc_info=True)
        return None


def sizes_from_content(
    sizes: dict, files: Iterable[str], content_dir: Optional[Path]
) -> dict:
    """``sizes`` plus the content-dir size of each of ``files`` it lacks."""
    out = dict(sizes)
    base = _resolved(content_dir)
    for rel in files:
        if rel in out:
            continue
        size = _content_size(content_dir, rel, base)
        if size is not None:
            out[rel] = size
    return out


class ContentSizes(dict):
    """Stored sizes, falling back to the content dir copy for a file stored without one.

    Only ``get`` and ``[]`` fall back; ``in``, ``len`` and iteration describe the
    stored sizes plus those read so far, so nothing that walks the map pays a
    stat per file.
    """

    __slots__ = ("_content_dir", "_base", "_misses")

    def __init__(self, stored: dict, content_dir: Optional[Path]) -> None:
        super().__init__(stored)
        self._content_dir = content_dir
        self._base: Optional[str] = None
        self._misses: set = set()

    def _fill(self, key) -> Optional[int]:
        if not isinstance(key, str) or key in self._misses or self._content_dir is None:
            return None
        if self._base is None:
            self._base = _resolved(self._content_dir)
        size = _content_size(self._content_dir, key, self._base)
        if size is None:
            self._misses.add(key)
        else:
            dict.__setitem__(self, key, size)
        return size

    def get(self, key, default=None):  # type: ignore[override]
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        size = self._fill(key)
        return default if size is None else size

    def __missing__(self, key):
        size = self._fill(key)
        if size is None:
            raise KeyError(key)
        return size

    def __reduce__(self):
        return (ContentSizes, (dict(self), self._content_dir))


def carry_fallback(base, patched: dict) -> dict:
    """``patched`` with ``base``'s content-dir fallback, when ``base`` had one."""
    if isinstance(base, ContentSizes):
        return ContentSizes(patched, base._content_dir)
    return patched
