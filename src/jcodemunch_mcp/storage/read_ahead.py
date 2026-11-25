"""Read files ahead of a serial scan, in order, on a small pool of daemon threads.

A text search reads its candidate files one at a time. On Windows the first
open of a file written moments ago can cost about 30 ms (measured on a fresh
Home Assistant index: 29.5 ms per file on the first open, 0.10 ms on the
second; consistent with real-time scanning of new files), so the first search
after indexing paid that cost once per candidate, serially. Opening those files
on a few threads lets the waits overlap: 300 new files read in 8.2 s serially,
1.0 s on 8 threads, 0.38 s on 32.

The scan still consumes the results in the order it asked for them and does
its own matching. Each result is exactly what one ``read`` call returned, or
the ``OSError`` it raised, so a caller's handling of an unreadable file is
unchanged. At most ``window`` reads are in flight or waiting to be consumed,
which bounds the memory held.

Nothing outlives the call. Closing the generator (or leaving it on an
exception) drops the reads not yet started and waits for the running ones.
⚠ The workers are DAEMON threads, deliberately not a ``ThreadPoolExecutor``:
the Grep-answer hook abandons its search thread when its budget runs out and
exits, and an executor's workers are joined at interpreter exit, after they
drain every queued read. That held the hook process past its budget.
"""

from __future__ import annotations

import os
import queue
import threading
from collections import deque
from concurrent.futures import Future
from typing import Callable, Generator, Iterable, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Threads spend their time waiting on the file system, not on the CPU, so more
# threads than cores still help (32 beat 16 on a 24-core box). The cap keeps a
# small machine from starting dozens of threads for a handful of files.
MAX_WORKERS = 32
DEFAULT_WINDOW = 64

_STOP = object()


def default_workers() -> int:
    return max(4, min(MAX_WORKERS, (os.cpu_count() or 4) * 2))


class _Pool:
    """Daemon workers taking ``(future, item)`` jobs from one queue."""

    def __init__(self, read: Callable, size: int):
        self._read = read
        self._size = size
        self._jobs: "queue.SimpleQueue" = queue.SimpleQueue()
        self._threads: list[threading.Thread] = []
        self._closed = threading.Event()

    def submit(self, item) -> Future:
        fut: Future = Future()
        self._jobs.put((fut, item))
        if len(self._threads) < self._size:
            t = threading.Thread(target=self._work, name="jcm-read", daemon=True)
            t.start()
            self._threads.append(t)
        return fut

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is _STOP:
                return
            fut, item = job
            if self._closed.is_set() or not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(self._read(item))
            except BaseException as exc:  # handed to the consumer at its turn
                fut.set_exception(exc)

    def close(self) -> None:
        """Drop queued jobs, then wait for the running ones to finish."""
        self._closed.set()
        for _ in self._threads:
            self._jobs.put(_STOP)
        for t in self._threads:
            t.join()


def read_ahead(
    items: Iterable[T],
    read: Callable[[T], R],
    *,
    workers: Optional[int] = None,
    window: int = DEFAULT_WINDOW,
    skip: Optional[Callable[[T], bool]] = None,
) -> Generator[tuple[T, Optional[R], Optional[OSError]], None, None]:
    """Yield ``(item, result, None)`` or ``(item, None, error)`` in input order.

    Only ``OSError`` is captured; any other exception from ``read`` is raised
    to the caller at that item's turn, as a serial call would raise it.

    An item for which ``skip`` is true is not read: it comes back in its place
    as ``(item, None, None)`` and takes no slot in the window, so a scan whose
    candidates are sparse still keeps ``window`` real reads going.
    """
    window = max(1, window)
    pending: deque[tuple[T, Optional[Future]]] = deque()
    in_flight = 0
    it = iter(items)
    pool = _Pool(read, workers or default_workers())
    try:
        def fill() -> None:
            nonlocal in_flight
            while in_flight < window:
                try:
                    item = next(it)
                except StopIteration:
                    return
                if skip is not None and skip(item):
                    pending.append((item, None))
                    continue
                pending.append((item, pool.submit(item)))
                in_flight += 1

        fill()
        while pending:
            item, fut = pending.popleft()
            if fut is None:
                yield item, None, None
                continue
            in_flight -= 1
            try:
                result = fut.result()
            except OSError as exc:
                fill()
                yield item, None, exc
                continue
            fill()
            yield item, result, None
    finally:
        pool.close()
