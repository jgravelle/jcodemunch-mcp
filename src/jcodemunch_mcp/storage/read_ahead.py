"""Read files ahead of a serial scan, in order, on a small thread pool.

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
which bounds the memory held. Nothing outlives the call: closing the generator
cancels the reads not yet started and waits for the running ones.
"""

from __future__ import annotations

import os
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Threads spend their time waiting on the file system, not on the CPU, so more
# threads than cores still help (32 beat 16 on a 24-core box). The cap keeps a
# small machine from starting dozens of threads for a handful of files.
MAX_WORKERS = 32
DEFAULT_WINDOW = 64


def default_workers() -> int:
    return max(4, min(MAX_WORKERS, (os.cpu_count() or 4) * 2))


def read_ahead(
    items: Iterable[T],
    read: Callable[[T], R],
    *,
    workers: Optional[int] = None,
    window: int = DEFAULT_WINDOW,
    skip: Optional[Callable[[T], bool]] = None,
) -> Iterator[tuple[T, Optional[R], Optional[OSError]]]:
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
    executor = ThreadPoolExecutor(max_workers=workers or default_workers(), thread_name_prefix="jcm-read")
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
                pending.append((item, executor.submit(read, item)))
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
        executor.shutdown(wait=True, cancel_futures=True)
