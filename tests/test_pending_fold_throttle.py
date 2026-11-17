"""A per-call store folds its pending files at most once a minute between its first and exit writes.

Measured 2026-11-17: folding on every write lists the store's directory twice,
and the savings meter and tool counters write every few calls inside a tool
call. That put about half a millisecond on the slowest calls of a warm read
(`_merge_batch`, 300 calls in a 40-file store: median 11.4 ms on main, 11.5 to
12.0 ms with a fold on every write). `pending_files.fold_due` folds on a
process's first write to a file, on its exit write, and once a minute between.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import pending_files
from jcodemunch_mcp.storage import token_tracker as tt
from jcodemunch_mcp.storage import tool_counters as tc


@pytest.fixture(autouse=True)
def _fresh_gate():
    # getattr: on a tree without the throttle the store tests below must fail
    # on their assertions, not error in setup.
    saved = (
        getattr(pending_files, "FOLD_INTERVAL_SECONDS", None),
        dict(getattr(pending_files, "_last_fold", {})),
    )
    pending_files.FOLD_INTERVAL_SECONDS = 60.0
    pending_files._last_fold = {}
    yield
    if saved[0] is None:
        del pending_files.FOLD_INTERVAL_SECONDS
    else:
        pending_files.FOLD_INTERVAL_SECONDS = saved[0]
    pending_files._last_fold = saved[1]


@pytest.fixture(autouse=True)
def _no_share(monkeypatch):
    monkeypatch.setattr(tt, "_share_savings", lambda *a, **k: None)


def test_the_first_write_folds_and_the_next_one_inside_a_minute_does_not(tmp_path):
    main = tmp_path / "store.json"
    assert pending_files.fold_due(main)
    assert not pending_files.fold_due(main)


def test_an_exit_write_always_folds(tmp_path):
    main = tmp_path / "store.json"
    assert pending_files.fold_due(main)
    assert pending_files.fold_due(main, force=True)


def test_a_write_a_minute_later_folds(tmp_path, monkeypatch):
    main = tmp_path / "store.json"
    clock = [1000.0]
    monkeypatch.setattr(pending_files.time, "monotonic", lambda: clock[0])
    assert pending_files.fold_due(main)
    clock[0] += 59.0
    assert not pending_files.fold_due(main)
    clock[0] += 1.0
    assert pending_files.fold_due(main)


def test_each_file_has_its_own_first_fold(tmp_path):
    assert pending_files.fold_due(tmp_path / "a.json")
    assert pending_files.fold_due(tmp_path / "b.json")


def _kept(root: Path, saved: int) -> None:
    pending_files.write(
        root / tt._SAVINGS_FILE,
        {"saved": saved, "calls": 1, "by_tool": {}, "encoding": 0, "telemetry": 0},
    )


def _batch(saved: int) -> dict:
    return {
        "saved": saved,
        "calls": 1,
        "by_tool": {},
        "encoding": 0,
        "telemetry": 0,
        "anon_id": "a",
        "by_tool_since": None,
    }


def _total(root: Path) -> int:
    return json.loads((root / tt._SAVINGS_FILE).read_text(encoding="utf-8"))[
        "total_tokens_saved"
    ]


def test_the_savings_meter_lists_pending_files_once_inside_a_minute(
    tmp_path, monkeypatch
):
    seen = []
    real = pending_files.paths
    monkeypatch.setattr(
        pending_files, "paths", lambda main: seen.append(main) or real(main)
    )
    for _ in range(3):
        assert tt._merge_batch(tmp_path, _batch(1), 0.0)["ok"]
    assert len(seen) == 1, "only the first write listed the directory"


def test_a_kept_batch_written_after_the_first_fold_waits_for_the_next(
    tmp_path, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(pending_files.time, "monotonic", lambda: clock[0])
    assert tt._merge_batch(tmp_path, _batch(1), 0.0)["ok"]
    _kept(tmp_path, 9)
    assert tt._merge_batch(tmp_path, _batch(1), 0.0)["ok"]
    assert _total(tmp_path) == 2, "inside the minute the kept batch stays pending"
    clock[0] += 60.0
    assert tt._merge_batch(tmp_path, _batch(1), 0.0)["ok"]
    assert _total(tmp_path) == 12
    assert pending_files.paths(tmp_path / tt._SAVINGS_FILE) == []


def test_the_savings_meters_exit_write_folds_inside_the_minute(tmp_path):
    assert tt._merge_batch(tmp_path, _batch(1), 0.0)["ok"]
    _kept(tmp_path, 9)
    assert tt._merge_batch(tmp_path, _batch(1), 0.0, last_chance=True)["ok"]
    assert _total(tmp_path) == 11


def test_a_new_process_counts_a_kept_batch_the_throttle_has_not_folded(tmp_path):
    assert tt._merge_batch(tmp_path, _batch(1), 0.0)["ok"]
    _kept(tmp_path, 9)
    fresh = tt._State()
    fresh._ensure_loaded(str(tmp_path))
    assert fresh._total == 10, "a reader counts pending files at load"


def test_the_tool_counters_list_pending_files_once_inside_a_minute(
    tmp_path, monkeypatch
):
    seen = []
    real = pending_files.paths
    monkeypatch.setattr(
        pending_files, "paths", lambda main: seen.append(main) or real(main)
    )
    for _ in range(3):
        assert tc._merge_into_file(str(tmp_path), {"list_repos": [1, 10, 0]}, 0.0)
    assert len(seen) == 1


def test_the_tool_counters_exit_write_folds_inside_the_minute(tmp_path, monkeypatch):
    seen = []
    real = pending_files.paths
    monkeypatch.setattr(
        pending_files, "paths", lambda main: seen.append(main) or real(main)
    )
    assert tc._merge_into_file(str(tmp_path), {"list_repos": [1, 10, 0]}, 0.0)
    assert tc._merge_into_file(
        str(tmp_path), {"list_repos": [1, 10, 0]}, 0.0, last_chance=True
    )
    assert len(seen) == 2
