"""The per-file parse budget, enforced inside the parser (LEDGER L-114).

`JCODEMUNCH_PARSE_BUDGET_SECONDS` was a thread wait around `parse_file`
(`tools/_indexing_pipeline.parse_file_budgeted`). ⚠⚠ tree-sitter's parse holds
the GIL for its whole duration, so that wait cannot return early: measured, a
`join(2.0)` returned after 4.57 s with the parse already finished, and the
over-budget file came back with its symbols and no warning. The full-index loop
of `index_folder` never asked it at all.

So the limit lives here, one layer down. `extractor.parse_file` opens a deadline
with `armed()`, and `grammar_pack.get_parser`, the one loader every grammar call
uses, passes each parser through `bind()`. A bound parser gives tree-sitter what
is left of the deadline as its own timeout, and the C parser stops itself.

⚠⚠ The mechanism is `Parser.timeout_micros`, which tree-sitter 0.25 DEPRECATES
in favour of `parse(progress_callback=)`. The callback is not usable: it is
ignored for a bytestring, and with a read callback it killed the interpreter
with an access violation on all eight variants probed (tree-sitter 0.25.2,
Python 3.12.4, Windows). A binding without the attribute leaves only the thread
wait; `bind()` says so once in the log and
`tests/test_parse_budget_cancels.py` fails on it.

⚠ Many dedicated parsers catch `Exception` around their parse and return `[]`
(a grammar that failed to load is "indexed for text search only"). A cancel
raised into one of those would be swallowed, so the scope RECORDS it and
`parse_file` raises after the dispatch returns.

⚠ A parser loaded outside `parse_file` (`search_ast`) has no scope and is the
pack's own object, unchanged.

A leaf: stdlib only.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import warnings
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_PARSE_BUDGET_SECONDS = 20.0

# tree-sitter checks its clock between parse operations, so a cancel arrives a
# little after the deadline, never before it. A parse that fails this close to
# the deadline or later is the timeout; one that fails earlier is a real error.
_CANCEL_SLACK = 0.9

_state = threading.local()
_warned_no_timeout = False


class ParseBudgetExceeded(Exception):
    """Raised when a single file's parse overruns its wall-clock budget."""


def budget_seconds() -> float:
    """Per-file parse budget; ``0`` or negative disables the ceiling."""
    raw = os.environ.get("JCODEMUNCH_PARSE_BUDGET_SECONDS")
    if raw is None:
        return DEFAULT_PARSE_BUDGET_SECONDS
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring non-numeric JCODEMUNCH_PARSE_BUDGET_SECONDS=%r; using %.1fs",
            raw, DEFAULT_PARSE_BUDGET_SECONDS,
        )
        return DEFAULT_PARSE_BUDGET_SECONDS


class _Scope:
    """One file's deadline, shared by every parser its `parse_file` call loads."""

    __slots__ = ("budget", "started", "deadline", "language", "size", "cancelled")

    def __init__(self, budget: float, language: str, size: int) -> None:
        self.budget = budget
        self.started = time.monotonic()
        self.deadline = self.started + budget
        self.language = language
        self.size = size
        self.cancelled = False

    def error(self) -> ParseBudgetExceeded:
        return ParseBudgetExceeded(
            f"parse exceeded the {self.budget:g}s budget ({self.size:,} bytes, "
            f"{self.language}); file skipped and its symbols are absent from this "
            f"index. Raise JCODEMUNCH_PARSE_BUDGET_SECONDS to allow more time."
        )


def active() -> Optional[_Scope]:
    """The deadline this thread's current `parse_file` call opened, or None."""
    return getattr(_state, "scope", None)


@contextmanager
def armed(language: str, size: int) -> Iterator[Optional[_Scope]]:
    """Open this file's deadline; yields None when one is already open or the budget is off.

    ⚠ `parse_file` is re-entrant (a Vue file's `<script>`, a Razor block), and
    the nested call must share the outer file's deadline, not start its own.
    Only the call that opened the scope gets it back, so only that call raises.
    """
    if active() is not None:
        yield None
        return
    budget = budget_seconds()
    if budget <= 0:
        yield None
        return
    # The timeout's setter warns on every call (deprecated in 0.25). Suppressed
    # by message, once per file, without `catch_warnings`, which is not safe
    # across threads.
    warnings.filterwarnings(
        "ignore", message=r"Use the progress_callback in parse\(\)", category=DeprecationWarning,
    )
    scope = _Scope(budget, language, size)
    _state.scope = scope
    try:
        yield scope
    finally:
        _state.scope = None


class _BudgetedParser:
    """A tree-sitter parser whose `parse` stops at its file's deadline.

    ⚠ It exposes `parse` and `language` and nothing else, by name: a computed
    `getattr` pass-through here would blind the spec-field scan of
    `tests/test_grammar_spelled_forms.py`. A grammar call that needs another
    parser attribute adds it here; `tests/test_parse_budget_cancels.py` fails
    on a parser attribute this class does not carry.
    """

    __slots__ = ("_parser", "_scope")

    def __init__(self, parser, scope: _Scope) -> None:
        self._parser = parser
        self._scope = scope

    @property
    def language(self):
        return self._parser.language

    def parse(self, *args, **kwargs):
        scope = self._scope
        remaining = scope.deadline - time.monotonic()
        if remaining <= 0:
            scope.cancelled = True
            raise scope.error()
        self._parser.timeout_micros = max(1, int(remaining * 1_000_000))
        asked = time.monotonic()
        try:
            tree = self._parser.parse(*args, **kwargs)
        except ValueError:
            if time.monotonic() - asked >= remaining * _CANCEL_SLACK:
                scope.cancelled = True
                raise scope.error() from None
            raise
        if tree is None and time.monotonic() - asked >= remaining * _CANCEL_SLACK:
            scope.cancelled = True
            raise scope.error()
        return tree


def _can_cancel(parser) -> bool:
    # ⚠ Asked of the TYPE: reading the attribute off an instance warns too.
    return hasattr(type(parser), "timeout_micros")


def bind(parser):
    """`parser` carrying the open deadline, or `parser` itself when there is none."""
    global _warned_no_timeout
    scope = active()
    if scope is None:
        return parser
    if not _can_cancel(parser):
        if not _warned_no_timeout:
            _warned_no_timeout = True
            logger.warning(
                "the installed tree-sitter has no Parser.timeout_micros; "
                "JCODEMUNCH_PARSE_BUDGET_SECONDS cannot stop a slow parse on it"
            )
        return parser
    return _BudgetedParser(parser, scope)
