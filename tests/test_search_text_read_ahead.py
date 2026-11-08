"""search_text opens its candidate files on a few threads, and answers the same.

On Windows the first open of a file written moments ago is slow (about 30 ms
measured right after indexing Home Assistant, against 0.1 ms on a second
open), so a serial scan of a just-built index paid that cost once per
candidate. The scan now reads ahead on a small pool. These tests pin that the
reads overlap, and that nothing the response reports changes: results, counts,
the cut page's ``more_files`` and the unreadable-file gap.
"""

from __future__ import annotations

import builtins
import os
import threading
import time

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import read_ahead as ra
from jcodemunch_mcp.tools import search_text as st
from jcodemunch_mcp.tools.index_folder import index_folder


# --- read_ahead itself -----------------------------------------------------


def test_results_come_back_in_input_order():
    def read(i):
        time.sleep(0.002 * (10 - i % 10))  # later items finish first
        return i * 2

    out = list(ra.read_ahead(range(40), read, workers=8, window=16))
    assert [item for item, _, _ in out] == list(range(40))
    assert [res for _, res, _ in out] == [i * 2 for i in range(40)]
    assert all(err is None for _, _, err in out)


def test_an_oserror_is_returned_for_its_item_and_the_rest_still_read():
    def read(i):
        if i == 3:
            raise FileNotFoundError("gone")
        return i

    out = list(ra.read_ahead(range(6), read, workers=3))
    assert [item for item, _, _ in out] == list(range(6))
    assert isinstance(out[3][2], FileNotFoundError) and out[3][1] is None
    assert [res for i, res, _ in out if i != 3] == [0, 1, 2, 4, 5]


def test_any_other_error_is_raised_at_its_turn():
    def read(i):
        if i == 2:
            raise ValueError("bad")
        return i

    gen = ra.read_ahead(range(5), read, workers=2)
    assert next(gen)[1] == 0
    assert next(gen)[1] == 1
    with pytest.raises(ValueError):
        next(gen)


def test_no_more_than_the_window_is_taken_ahead_of_the_consumer():
    taken = []

    def items():
        for i in range(100):
            taken.append(i)
            yield i

    gen = ra.read_ahead(items(), lambda i: i, workers=4, window=8)
    next(gen)
    assert len(taken) <= 9  # the window, plus the one refill after the first result
    gen.close()


def test_a_skipped_item_is_not_read_and_takes_no_window_slot():
    read_items = []

    def read(i):
        read_items.append(i)
        return i

    gen = ra.read_ahead(range(100), read, workers=1, window=4, skip=lambda i: i % 10 != 0)
    first = next(gen)
    assert first == (0, 0, None)
    # Four real reads went ahead even though nine of every ten items are skipped.
    time.sleep(0.05)
    assert set(read_items) >= {0, 10, 20, 30}
    rest = list(gen)
    assert [item for item, _, _ in [first] + rest] == list(range(100))
    assert all(res is None for item, res, _ in rest if item % 10)
    assert sorted(read_items) == list(range(0, 100, 10))


def test_closing_early_stops_the_remaining_reads():
    started = []
    lock = threading.Lock()

    def read(i):
        with lock:
            started.append(i)
        time.sleep(0.01)
        return i

    gen = ra.read_ahead(range(1000), read, workers=4, window=8)
    next(gen)
    gen.close()
    n = len(started)
    time.sleep(0.05)
    assert len(started) == n
    assert n < 50


# --- search_text over the read-ahead ---------------------------------------


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(40):
        body = "".join(f"x = 'needle line {j}'\n" for j in range(i % 4))
        (src / f"m{i:02d}.py").write_text(f"def f{i}():\n    return {i}\n{body}", encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"]
    owner, name = res["repo"].split("/", 1)
    return res["repo"], str(store), IndexStore(base_path=str(store))._content_dir(owner, name)


def _serial(items, read, skip=None, **_kw):
    for item in items:
        if skip is not None and skip(item):
            yield item, None, None
            continue
        try:
            yield item, read(item), None
        except OSError as exc:
            yield item, None, exc


def _comparable(res):
    # Everything but the clock and the process-wide savings counter, so
    # files_searched and the verdict (the counts a read-ahead could inflate)
    # are compared too.
    res = dict(res)
    res.pop("timing_ms", None)
    meta = dict(res.pop("_meta", None) or {})
    for key in ("timing_ms", "total_tokens_saved"):
        meta.pop(key, None)
    res["_meta"] = meta
    return res


def test_the_candidate_files_are_opened_concurrently(repo, monkeypatch):
    rid, store, content_dir = repo
    real = builtins.open
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}
    root = os.path.normcase(str(content_dir))

    def slow_open(path, *a, **kw):
        cached = os.path.normcase(str(path)).startswith(root) and str(path).endswith(".py")
        if cached:
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.02)
        try:
            return real(path, *a, **kw)
        finally:
            if cached:
                with lock:
                    state["now"] -= 1

    monkeypatch.setattr(builtins, "open", slow_open)
    res = st.search_text(rid, "needle", storage_path=store, scan_limit=1000)
    assert res["results"]
    assert state["peak"] > 1


@pytest.mark.parametrize("query,kwargs", [
    ("needle", {"scan_limit": 1000}),
    ("needle", {"max_results": 5}),  # a cut page and its more_files count
    ("needle line [23]", {"is_regex": True, "max_results": 7}),
    ("no such text anywhere", {}),
])
def test_the_response_is_the_same_as_a_serial_scan(repo, monkeypatch, query, kwargs):
    rid, store, _ = repo
    parallel = st.search_text(rid, query, storage_path=store, **kwargs)
    monkeypatch.setattr(st, "read_ahead", _serial)
    serial = st.search_text(rid, query, storage_path=store, **kwargs)
    assert _comparable(parallel) == _comparable(serial)


def test_an_unreadable_cached_copy_is_still_reported_as_a_gap(repo, monkeypatch):
    rid, store, content_dir = repo
    real = builtins.open

    def failing_open(path, *a, **kw):
        if str(path).endswith("m03.py") and os.path.normcase(str(content_dir)) in os.path.normcase(str(path)):
            raise PermissionError("locked")
        return real(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", failing_open)
    parallel = st.search_text(rid, "needle", storage_path=store, scan_limit=1000)
    monkeypatch.setattr(st, "read_ahead", _serial)
    serial = st.search_text(rid, "needle", storage_path=store, scan_limit=1000)
    assert _comparable(parallel) == _comparable(serial)
    assert "m03.py" not in {r["file"] for r in parallel["results"]}
    assert "files_unreadable" in str(parallel)
