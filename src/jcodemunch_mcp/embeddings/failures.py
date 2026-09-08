"""What went wrong when a batch of texts could not be embedded, for the caller.

Two loops call ``embed_texts`` over batches and continue past a failure:
``tools/embed_repo.py`` and the lazy top-up in ``tools/search_symbols.py``.
Both used to log the exception and go on, so a rejected key, a network
outage and a model the endpoint does not serve reached the caller as a count
at best and as nothing at worst (docs/competitive/FINDINGS.md CF-66, the half
of zvec-grep #81 that applied here). One ledger serves both sites so a third
loop inherits the disclosure instead of re-deriving it.
"""
from __future__ import annotations

import re

from ..redact import redact_text

# A provider's message can carry the whole request; keep enough to name the
# cause and no more. Redaction runs BEFORE the cut so a secret straddling the
# boundary cannot leave an unmatchable prefix behind.
MESSAGE_CHARS = 300
# Distinct causes kept; the rest are counted in ``omitted``, never dropped
# silently (a count taken after the page is cut describes the page).
LIST_MAX = 10
_MARKER = re.compile(r"\[REDACTED:[^\]]*\]")


def _cut(text: str) -> str:
    """Shorten to MESSAGE_CHARS without splitting a redaction marker.

    A marker cut in half (``[REDAC``) is neither the secret nor the notice
    that one was removed; the cut moves back to before it.
    """
    if len(text) <= MESSAGE_CHARS:
        return text
    end = MESSAGE_CHARS
    for m in _MARKER.finditer(text):
        if m.start() < end < m.end():
            end = m.start()
            break
    return text[:end] + "..."


class FailureLedger:
    """Distinct (type, message) causes with the number of batches each explains."""

    __slots__ = ("_causes", "omitted", "batches", "items")

    def __init__(self) -> None:
        self._causes: dict[tuple[str, str], int] = {}
        self.omitted = 0
        self.batches = 0
        self.items = 0

    def record(self, exc: BaseException, items: int = 0) -> None:
        self.batches += 1
        self.items += items
        key = (type(exc).__name__, _cut(redact_text(str(exc))))
        if key in self._causes or len(self._causes) < LIST_MAX:
            self._causes[key] = self._causes.get(key, 0) + 1
        else:
            self.omitted += 1

    def __bool__(self) -> bool:
        return self.batches > 0

    def rows(self) -> list[dict]:
        return [
            {"type": t, "message": m, "batches": n} for (t, m), n in self._causes.items()
        ]

    def disclose(self, into: dict) -> None:
        """Write ``error_causes`` and, when any were cut, ``causes_omitted``."""
        if not self.batches:
            return
        into["error_causes"] = self.rows()
        if self.omitted:
            into["causes_omitted"] = self.omitted
