"""The per-file parse budget, enforced inside the parser (LEDGER L-114).

`JCODEMUNCH_PARSE_BUDGET_SECONDS` was a thread wait around `parse_file`
(`tools/_indexing_pipeline.parse_file_budgeted`). ⚠⚠ tree-sitter's parse holds
the GIL for its whole duration, so that wait cannot return early: it returned
when the parse did, and the over-budget file came back with its symbols and no
warning (the measurements are the L-114 row). The full-index loop of
`index_folder` never asked it at all.

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

⚠ TWO clocks, and the split matters. BETWEEN a file's parses the deadline is
counted in the parsing thread's own time (`time.thread_time`): a parse in
another thread holds the GIL, and on a wall clock that wait was charged to this
file, which was then skipped as over budget having used a fraction of it.
INSIDE a parse the timer is tree-sitter's, and it is wall-clock: what is left
of the deadline is handed to it as a duration. So another THREAD of this
process cannot spend a file's budget (it cannot run while the parse holds the
GIL), and another PROCESS can: on a box whose cores are all taken, a parse that
would fit its budget is stopped.

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

_state = threading.local()
_warned_no_timeout = False
_warned_bad_env = False
_filter_installed = False

# What py-tree-sitter raises when `ts_parser_parse` returns no tree. On a parser
# with a language set, that happens only when the parse was stopped.
_STOPPED = "Parsing failed"

_clock = getattr(time, "thread_time", time.monotonic)


class ParseBudgetExceeded(Exception):
    """Raised when a single file's parse overruns its budget.

    From the parser the budget is the parsing thread's own time between a
    file's parses and tree-sitter's wall timer inside one (module docstring);
    from `parse_file_budgeted`'s thread wait it is wall-clock.
    """


def budget_seconds() -> float:
    """Per-file parse budget; ``0`` or negative disables the ceiling."""
    raw = os.environ.get("JCODEMUNCH_PARSE_BUDGET_SECONDS")
    if raw is None:
        return DEFAULT_PARSE_BUDGET_SECONDS
    try:
        return float(raw)
    except (TypeError, ValueError):
        global _warned_bad_env
        if not _warned_bad_env:  # once: this is read for every file
            _warned_bad_env = True
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
        self.started = _clock()
        self.deadline = self.started + budget
        self.language = language
        self.size = size
        self.cancelled = False

    def remaining(self) -> float:
        return self.deadline - _clock()

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
    _install_filter()
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
        remaining = scope.remaining()
        if remaining <= 0:
            scope.cancelled = True
            raise scope.error()
        try:
            self._parser.timeout_micros = max(1, int(remaining * 1_000_000))
        except Warning:
            # The host turned this DeprecationWarning into an error after our
            # filter went in. Parse without the limit; raising here would land
            # in a dedicated parser's `except Exception` as a file with no symbols.
            logger.debug("could not set the parse timeout; parsing unbounded", exc_info=True)
            return self._parser.parse(*args, **kwargs)
        # ⚠ No clock decides whether the parse was stopped. The first draft
        # compared elapsed time with the deadline, and Python's monotonic clock
        # ticks at 15.6 ms on Windows before 3.13, so a cancel near the end of
        # a deadline read as a real error and the file came back empty, unnamed.
        try:
            tree = self._parser.parse(*args, **kwargs)
        except ValueError as exc:
            if str(exc) == _STOPPED:
                self._release()
                scope.cancelled = True
                raise scope.error() from None
            raise
        if tree is None:
            self._release()
            scope.cancelled = True
            raise scope.error()
        return tree

    def _release(self) -> None:
        # ⚠ tree-sitter keeps a stopped parse to RESUME it: the next `parse` on
        # the same object returned the old source's tree, with the timeout still
        # set. The pack builds a parser per call today, and nothing promises that.
        try:
            self._parser.reset()
            self._parser.timeout_micros = 0
        except Exception:
            logger.debug("could not reset a stopped parser", exc_info=True)


def _install_filter() -> None:
    """Silence the timeout setter's DeprecationWarning, by message, ONCE per process.

    ⚠ Once: `warnings.filterwarnings` bumps the filter version on every call,
    which clears every module's warn-once registry, so calling it per file made
    a host's own once-only warnings print again after each parsed file. Not
    `catch_warnings`, which is not safe across threads.
    """
    global _filter_installed
    if _filter_installed:
        return
    _filter_installed = True
    warnings.filterwarnings(
        "ignore", message=r"Use the progress_callback in parse\(\)", category=DeprecationWarning,
    )


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
