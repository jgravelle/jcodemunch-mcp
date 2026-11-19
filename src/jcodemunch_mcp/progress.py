"""MCP progress notification helper for long-running tools.

Emits ``notifications/progress`` so MCP hosts (e.g. VS Code) can show
a live inline indicator.  Zero token cost — notifications go to the host,
never the model.  No-op when the client omits ``progressToken``.

Flood control (v1.108.92, #359):
    A full-repo index used to emit one notification per file — ~2,000
    notifications in ~4 s on a 2,000-file walk. Some MCP clients lose the
    tool RESULT under that flood (Claude Code surfaces "Tool result missing
    due to internal error" while the index completes fine server-side).
    ``update()`` now throttles to at most one send per
    ``PROGRESS_MIN_STEP`` of forward progress AND one per
    ``PROGRESS_MIN_INTERVAL_S`` of wall clock; the final 100% send always
    goes out.

Straggler control (v1.108.92, #359):
    Sends are scheduled fire-and-forget onto the event loop from the
    worker thread, so a queued notification could be written AFTER the
    request's response — and a strict client treats progress for a
    completed request as a protocol error (observed: Claude Code drops the
    whole stdio connection on "progress notification for an unknown
    token"). The dispatcher awaits ``drain_reporter()`` before the
    response is returned: no notification can trail its response.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Callable, Optional

from . import request_context

logger = logging.getLogger(__name__)

# Type alias: (progress, total, message) → future-or-None
ProgressNotify = Callable[[float, Optional[float], Optional[str]], object]

# Type alias for tool-level callbacks: (done, total, detail) → None
ProgressHook = Optional[Callable[[int, int, str], None]]

# Minimum forward progress between sends (fraction of 1.0). 0.01 = one
# notification per 1% — caps a whole operation at ~100 sends no matter
# how many files it walks.
PROGRESS_MIN_STEP = 0.01

# Minimum wall-clock seconds between sends. 0.1 = at most 10/s — a rate
# every observed MCP client keeps up with (the failure mode was ~500/s).
PROGRESS_MIN_INTERVAL_S = 0.1

# Upper bound on how long the dispatcher waits for in-flight sends to
# flush before the response goes out. Generous — pending sends normally
# flush in single-digit milliseconds.
PROGRESS_DRAIN_TIMEOUT_S = 2.0

# Seconds of elapsed wall clock between heartbeat log lines on a client
# that sent no progressToken (#383). 0 / negative disables.
HEARTBEAT_INTERVAL_S = 30.0


def _heartbeat_interval() -> float:
    """Read the heartbeat interval from the environment.

    Garbage parses to the default rather than disabling the heartbeat —
    the failure this exists to fix is silence, so a typo must not
    reintroduce it.
    """
    raw = os.environ.get("JCODEMUNCH_HEARTBEAT_SECONDS")
    if raw is None or not raw.strip():
        return HEARTBEAT_INTERVAL_S
    try:
        return float(raw)
    except ValueError:
        logger.debug("Bad JCODEMUNCH_HEARTBEAT_SECONDS=%r, using default", raw)
        return HEARTBEAT_INTERVAL_S


class ProgressReporter:
    """Emit monotonic, rate-limited MCP progress notifications.

    Thread-safe: ``update()`` and ``finish()`` may be called from worker
    threads (e.g. inside ``asyncio.to_thread``).

    No fake drift, no pulse threads — progress reflects real completed work.
    If a slow sub-step stalls, the bar stalls.  That's honest.
    """

    __slots__ = (
        "_notify", "_label", "_bar_width", "_lock",
        "_last_sent", "_done", "_total", "_finished",
        "_min_step", "_min_interval", "_clock", "_last_send_ts",
        "_pending",
    )

    def __init__(
        self,
        notify: Optional[ProgressNotify],
        label: str,
        *,
        bar_width: int = 12,
        min_step: float = PROGRESS_MIN_STEP,
        min_interval: float = PROGRESS_MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._notify = notify
        self._label = label
        self._bar_width = bar_width
        self._lock = threading.Lock()
        self._last_sent: float = 0.0
        self._done: int = 0
        self._total: int = 0
        self._finished: bool = False
        self._min_step = max(min_step, 0.0)
        self._min_interval = max(min_interval, 0.0)
        self._clock = clock
        self._last_send_ts: float = float("-inf")
        self._pending: list = []  # in-flight notification futures

    def start(self, total: int = 0, detail: str = "Starting") -> None:
        """Emit initial 0% notification."""
        if self._notify is None:
            return
        with self._lock:
            if self._finished:
                return
            self._total = max(total, 0)
        self._send(0.0, detail)

    def update(self, done: int, total: int, detail: str = "") -> None:
        """Emit progress for real completed work (throttled)."""
        if self._notify is None:
            return
        total = max(int(total), 1)
        done = max(0, min(int(done), total))
        with self._lock:
            if self._finished:
                return
            self._done = done
            self._total = total
            progress = done / total
            final = done >= total
            # Monotonic: never go backwards
            if progress <= self._last_sent and not (final and self._last_sent < 1.0):
                return
            if not final:
                # Flood control: require both a real step forward and a
                # minimum gap since the last send (#359).
                if progress - self._last_sent < self._min_step:
                    return
                if self._clock() - self._last_send_ts < self._min_interval:
                    return
        self._send(progress, detail)

    def finish(self, detail: str = "Complete") -> None:
        """Emit 100% notification."""
        if self._notify is None:
            return
        with self._lock:
            if self._finished:
                return
            self._finished = True
            if self._total > 0:
                self._done = self._total
        self._send(1.0, detail)

    def close(self) -> list:
        """Stop accepting sends; return in-flight notification futures.

        Called by the dispatcher (via ``drain_reporter``) before the tool
        response is returned, so no notification can trail the response.
        """
        with self._lock:
            self._finished = True
            pending = [f for f in self._pending if not _future_done(f)]
            self._pending = []
        return pending

    def _send(self, progress: float, detail: str) -> None:
        with self._lock:
            progress = max(progress, self._last_sent)
            progress = min(progress, 1.0)
            self._last_sent = progress
            self._last_send_ts = self._clock()
            message = self._format(progress, detail)
            try:
                fut = self._notify(progress, 1.0, message)
            except Exception:
                logger.debug("progress notification failed", exc_info=True)
                return
            if fut is not None and hasattr(fut, "done"):
                self._pending = [f for f in self._pending if not _future_done(f)]
                self._pending.append(fut)

    def _format(self, progress: float, detail: str) -> str:
        filled = int(progress * self._bar_width)
        bar = "[" + "#" * filled + "-" * (self._bar_width - filled) + "]"
        pct = f"{progress * 100:5.1f}%"
        parts = [self._label, bar, pct]
        if self._total > 0:
            parts.append(f"{min(self._done, self._total)}/{self._total}")
        if detail:
            parts.append(detail)
        return " ".join(parts)


class HeartbeatReporter:
    """Elapsed-time liveness lines for a client that sent no progressToken.

    Why this exists (#383, split out of #375): ``make_progress_notify``
    returns None unless the client opted in with a ``progressToken``, and
    the MCP progress spec makes that the client's call — emitting
    unrequested ``notifications/progress`` is a protocol violation, so
    "notify anyway" was never on the table. The consequence was that a
    long run on a token-less client emitted NOTHING, and a healthy index
    was indistinguishable from a hung one right up until the client's
    idle timeout killed it and the kill read as a crash.

    So the signal moves to a channel the client already sees: the log.

    Two deliberate choices:

    * **WARNING, not INFO.** The default ``log_level`` is ``WARNING``
      (``config.py``), which is why #375 sub-problem B closed as
      not-a-defect — a healthy run is silent at the default level. A
      heartbeat nobody sees fixes nothing.
    * **Nothing before the first interval elapses.** A run that finishes
      inside ``JCODEMUNCH_HEARTBEAT_SECONDS`` says nothing at all, so the
      common fast path is byte-for-byte as quiet as it was. Only a run
      that has already outlived the window anyone would call normal gets
      a line, and at that point the silence IS the reported bug.

    Duck-types ``ProgressReporter`` (``start``/``update``/``finish``/
    ``close``) so the dispatcher wires either one identically and
    ``drain_reporter`` works on both. ``close()`` returns no futures:
    logging is synchronous, so nothing can trail the response.
    """

    __slots__ = (
        "_label", "_lock", "_interval", "_clock", "_log",
        "_t0", "_last_emit", "_emitted", "_finished", "_done", "_total",
    )

    def __init__(
        self,
        label: str,
        *,
        interval: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[..., None]] = None,
    ) -> None:
        self._label = label
        self._lock = threading.Lock()
        self._interval = _heartbeat_interval() if interval is None else interval
        self._clock = clock
        self._log = log if log is not None else logger.warning
        self._t0 = self._clock()
        self._last_emit = self._t0
        self._emitted = 0
        self._finished = False
        self._done = 0
        self._total = 0

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def start(self, total: int = 0, detail: str = "Starting") -> None:
        """Anchor the clock. Emits nothing — a fast run must stay silent."""
        with self._lock:
            if self._finished:
                return
            self._total = max(total, 0)
            self._t0 = self._clock()
            self._last_emit = self._t0

    def update(self, done: int, total: int, detail: str = "") -> None:
        """Emit a liveness line if a full interval has passed since the last."""
        if self._interval <= 0:
            return
        with self._lock:
            if self._finished:
                return
            self._total = max(int(total), 0)
            self._done = max(0, int(done))
            now = self._clock()
            if now - self._last_emit < self._interval:
                return
            self._last_emit = now
            self._emitted += 1
            message = self._format(now - self._t0, detail)
        self._log("%s", message)

    def finish(self, detail: str = "Complete") -> None:
        """Close out. Emits a total only if a heartbeat was already emitted.

        A run that never got a heartbeat never worried anyone, so it gets
        no completion line either — otherwise every sub-second index would
        add a WARNING to the log of a user who had no problem.
        """
        with self._lock:
            if self._finished:
                return
            self._finished = True
            if not self._emitted:
                return
            elapsed = self._clock() - self._t0
            message = f"{self._label}: finished after {elapsed:.0f}s ({detail})"
        self._log("%s", message)

    def close(self) -> list:
        """Match ProgressReporter.close(). No async sends to drain."""
        with self._lock:
            self._finished = True
        return []

    def _format(self, elapsed: float, detail: str) -> str:
        parts = [f"{self._label}: still working after {elapsed:.0f}s"]
        if self._total > 0:
            parts.append(f"{min(self._done, self._total)}/{self._total} files")
        elif self._done > 0:
            parts.append(f"{self._done} files")
        if detail:
            parts.append(detail)
        line = ", ".join(parts)
        if self._emitted == 1:
            # Say WHY the usual indicator is missing, once, on the first
            # line — otherwise this reads as an error rather than a
            # substitute for a progress bar the client declined to request.
            line += (
                " [no progressToken from this client, so heartbeats replace"
                " progress notifications; set JCODEMUNCH_HEARTBEAT_SECONDS=0"
                " to silence]"
            )
        return line


def _future_done(fut) -> bool:
    try:
        return bool(fut.done())
    except Exception:
        return True


async def drain_reporter(reporter: ProgressReporter, timeout: float = PROGRESS_DRAIN_TIMEOUT_S) -> None:
    """Flush in-flight progress notifications before the response is written.

    Awaiting yields the event loop, so notification coroutines scheduled by
    worker threads get to run; the response only goes out once none remain
    (or ``timeout`` elapses — never blocks a result on a stuck send).
    """
    pending = reporter.close()
    if not pending:
        return
    wrapped = []
    for f in pending:
        try:
            wrapped.append(asyncio.wrap_future(f))
        except Exception:
            logger.debug("progress drain: unwrappable future", exc_info=True)
    if wrapped:
        await asyncio.wait(wrapped, timeout=timeout)


def make_progress_notify(server_obj) -> Optional[ProgressNotify]:
    """Create a thread-safe MCP progress notifier for the current request.

    Returns None if the client didn't send a progressToken.
    The returned callable gives back the scheduled send's future (or None)
    so ProgressReporter can track in-flight sends for drain_reporter().
    """
    try:
        ctx = request_context.resolve(server_obj)
    except (LookupError, AttributeError):
        return None

    if ctx is None:
        return None

    meta = getattr(ctx, "meta", None)
    if meta is None:
        return None

    # 2.x spells it `progress_token`; 1.x spelled it `progressToken`.
    token = meta.progress_token if hasattr(meta, "progress_token") else getattr(meta, "progressToken", None)
    if token is None:
        return None

    loop = asyncio.get_running_loop()
    session = ctx.session
    progress_token = token

    def _notify(progress: float, total: float | None, message: str | None):
        async def _send() -> None:
            try:
                await session.send_progress_notification(
                    progress_token=progress_token,
                    progress=progress,
                    total=total,
                    message=message,
                )
            except Exception:
                logger.debug("progress notification send failed", exc_info=True)

        try:
            return asyncio.run_coroutine_threadsafe(_send(), loop)
        except RuntimeError:
            return None  # event loop closed or unavailable

    return _notify
