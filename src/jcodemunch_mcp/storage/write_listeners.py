"""Who must hear that an index's stored rows changed without a re-index (L-226).

A result cache keyed on the index's identity (`indexed_at`, the .db mtime)
stays valid across a write that leaves that identity alone. A summary-only
write (`SQLiteIndexStore.update_symbol_summaries`) is one, so `search_symbols`
kept serving rows with the summaries from before a `summarize_repo` refresh.

The store calls `notify(owner, name)` after such a write, and a cache that
holds rows for the repository registers to evict them. This module is a LEAF
(stdlib only): `storage` must not import `tools`, so the tool registers
itself here when it is imported, and a cache that was never imported holds
nothing to evict. A listener that raises is logged and does not stop the
others or fail the write.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

_listeners: list[Callable[[str, str], object]] = []
_lock = threading.Lock()


def register(listener: Callable[[str, str], object]) -> None:
    """Call ``listener(owner, name)`` after a write that keeps the index's identity."""
    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)


def notify(owner: str, name: str) -> None:
    with _lock:
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener(owner, name)
        except Exception:
            logger.warning("index write listener %r failed for %s/%s", listener, owner, name, exc_info=True)
