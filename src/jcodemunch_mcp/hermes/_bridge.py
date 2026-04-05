"""Async-to-sync bridge for the jcodemunch Hermes plugin.

jcodemunch_mcp exposes its tools as async coroutines (``list_tools``,
``call_tool``). Hermes plugin handlers and hooks, by contrast, are
synchronous callables. We bridge the two worlds by running a dedicated,
long-lived asyncio event loop on a background daemon thread and
submitting coroutines via ``asyncio.run_coroutine_threadsafe``.

Why not ``asyncio.run()`` per call?

* It creates and tears down a fresh loop each time, which is slow and
  breaks any resources jcodemunch caches across calls.
* If the calling thread already has a running loop (some Hermes
  platforms run the tool loop inside asyncio), ``asyncio.run()`` raises
  ``RuntimeError: asyncio.run() cannot be called from a running event
  loop``.
* A persistent worker-thread loop sidesteps both problems and is the
  same pattern Hermes' own native MCP client uses internally.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Awaitable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Lazily create the worker thread + event loop on first use."""
    global _loop, _thread
    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop

        ready = threading.Event()

        def _runner() -> None:
            global _loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                # Best-effort cleanup; normally runs only on interpreter exit.
                try:
                    pending = asyncio.all_tasks(loop=loop)
                    for task in pending:
                        task.cancel()
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                except Exception:  # pragma: no cover — defensive
                    pass
                finally:
                    loop.close()

        _thread = threading.Thread(
            target=_runner,
            name="jcm-hermes-plugin-loop",
            daemon=True,
        )
        _thread.start()
        ready.wait(timeout=5.0)

        if _loop is None or not _loop.is_running():
            raise RuntimeError("jcodemunch plugin event loop failed to start")
        return _loop


def run_async(coro: Awaitable[T], timeout: Optional[float] = 300.0) -> T:
    """Execute an awaitable on the plugin's background event loop.

    Blocks until the coroutine returns or raises. Propagates whatever
    the coroutine raises, plus ``concurrent.futures.TimeoutError`` if
    the timeout elapses.
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
    return future.result(timeout=timeout)


def run_async_background(coro: Awaitable[Any]) -> None:
    """Fire-and-forget variant of :func:`run_async`.

    Schedules the coroutine on the worker loop and returns immediately
    without waiting for the result. Used by the index hook so indexing
    after an edit never blocks the agent's next action. Errors are
    logged by the coroutine's done-callback.
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]

    def _on_done(f: Any) -> None:
        try:
            f.result()
        except Exception as exc:  # pragma: no cover — logged only
            logger.warning("jcm hermes plugin background task failed: %s", exc)

    future.add_done_callback(_on_done)


def shutdown() -> None:
    """Stop the background loop.

    Optional — the daemon thread dies with the interpreter anyway.
    Exposed mainly for tests that want a clean teardown between runs.
    """
    global _loop, _thread
    with _lock:
        if _loop is not None and _loop.is_running():
            _loop.call_soon_threadsafe(_loop.stop)
        _loop = None
        _thread = None
