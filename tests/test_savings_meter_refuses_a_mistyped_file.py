"""A `_savings.json` that parses but holds the wrong type is refused, not silently frozen.

Found 2026-11-03 checking a customer's frozen meter against 1.108.410's fix
(#1309). #1309 refuses a file that cannot be read, is not JSON, is not an
object or whose total is not a non-negative number, with a WARNING and the
batch kept. A file that passes those checks but holds a string where the merge
adds a number (a `by_tool` value, `total_encoding_tokens_saved`, a `daily` day)
raised TypeError inside the merge instead. The error escaped as a DEBUG line,
the batch was already taken and was lost, the file never changed, and the
meter still read `current`: the frozen-and-shown-as-current symptom #1309 set
out to end.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import savings_meter as sm
from jcodemunch_mcp.storage import token_tracker as tt


@pytest.fixture(autouse=True)
def _no_share(monkeypatch):
    monkeypatch.setattr(tt, "_share_savings", lambda *a, **k: None)


def _file(root: Path) -> Path:
    return root / "_savings.json"


TODAY = datetime.now().date().isoformat()


@pytest.mark.parametrize(
    "ledger",
    [
        {"total_tokens_saved": 1000, "by_tool": {"search_symbols": "7"}},
        {"total_tokens_saved": 1000, "total_encoding_tokens_saved": "lots"},
        {"total_tokens_saved": 1000, "daily": {TODAY: "5"}},
    ],
    ids=["by_tool value", "encoding total", "daily value"],
)
def test_a_mistyped_file_is_refused_with_a_warning_and_the_batch_kept(
    tmp_path, caplog, ledger
):
    text = json.dumps(ledger)
    _file(tmp_path).write_text(text, encoding="utf-8")
    st = tt._State()
    st._encoding_unflushed = (
        3  # the merge adds to the encoding total only when a batch carries some
    )
    with caplog.at_level(logging.WARNING, logger=tt.logger.name):
        for _ in range(3):
            st.add(100, str(tmp_path), "search_symbols")
    assert _file(tmp_path).read_text(encoding="utf-8") == text, (
        "a refused file is never replaced"
    )
    assert st._unflushed == 300, "the batch stays in memory for the next write"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a refused write is a WARNING, visible at the default INFO level"
    assert str(_file(tmp_path)) in warnings[-1].getMessage()
    assert st.lifetime_label()[sm.STATUS] != "current", (
        "a meter that cannot write never reads current"
    )


def test_a_repaired_file_takes_the_kept_batch(tmp_path):
    _file(tmp_path).write_text(
        json.dumps({"total_tokens_saved": 1000, "by_tool": {"x": "7"}}),
        encoding="utf-8",
    )
    st = tt._State()
    for _ in range(3):
        st.add(100, str(tmp_path), "search_symbols")
    _file(tmp_path).write_text(
        json.dumps({"total_tokens_saved": 1000}), encoding="utf-8"
    )
    st.flush()
    assert (
        json.loads(_file(tmp_path).read_text(encoding="utf-8"))["total_tokens_saved"]
        == 1300
    )
    assert st._unflushed == 0
    assert st.lifetime_label()[sm.STATUS] == "current"


@pytest.mark.parametrize(
    "ledger",
    [
        {"total_tokens_saved": 1000, "by_tool": {"search_symbols": "7"}},
        {"total_tokens_saved": 1000, "basis_generation": "2"},
        {"total_tokens_saved": 1000, "daily": {"2026-10-29": None}},
    ],
    ids=["by_tool str", "generation str", "daily null"],
)
def test_a_tool_call_never_raises_on_a_mistyped_file(tmp_path, ledger):
    """`add` is a tool call's savings step: loading a mistyped file must refuse
    it (unknown, nothing written), never raise into the tool."""
    _file(tmp_path).write_text(json.dumps(ledger), encoding="utf-8")
    st = tt._State()
    for _ in range(4):
        st.add(10, str(tmp_path), "search_symbols")
    assert st.lifetime_label()[sm.STATUS] == "unknown"
    assert json.loads(_file(tmp_path).read_text(encoding="utf-8")) == ledger


def test_a_well_typed_file_still_loads_and_writes(tmp_path):
    ledger = {
        "total_tokens_saved": 1000,
        "total_encoding_tokens_saved": 5,
        "by_tool": {"a": 7.5},
        "daily": {"2026-10-29": 3},
        "basis_generation": 2,
        "basis_generation_first": 1,
    }
    _file(tmp_path).write_text(json.dumps(ledger), encoding="utf-8")
    st = tt._State()
    for _ in range(3):
        st.add(10, str(tmp_path), "search_symbols")
    assert (
        json.loads(_file(tmp_path).read_text(encoding="utf-8"))["total_tokens_saved"]
        == 1030
    )


# --- review round 1: every spelling the merge would overwrite ----------------


@pytest.mark.parametrize(
    "ledger",
    [
        {"total_tokens_saved": 1000, "daily": [5, 6]},
        {"total_tokens_saved": 1000, "by_tool": [["search_symbols", 7]]},
        {"total_tokens_saved": 1000, "measured_days": [{"calls": 4}]},
        {"total_tokens_saved": 1000, "by_tool": {"search_symbols": -7}},
        {"total_tokens_saved": None},
        {"total_tokens_saved": float("inf")},
        {"total_tokens_saved": 1000, "measured_days": {TODAY: {"calls": "50"}}},
        {"total_tokens_saved": 1000, "measured_days": {TODAY: {"calls": 50.5}}},
        {"total_tokens_saved": 1000, "measured_days": {TODAY: {"saved": -1}}},
        {"total_tokens_saved": 1000, "measured_days": {TODAY: [50]}},
        {"total_tokens_saved": 1000, "basis_generation": 2.5},
    ],
    ids=[
        "daily list",
        "by_tool list",
        "measured_days list",
        "negative count",
        "null total",
        "infinite total",
        "measured string count",
        "measured fractional count",
        "measured negative count",
        "measured day list",
        "fractional generation",
    ],
)
def test_every_mistyped_spelling_is_refused_and_kept(tmp_path, caplog, ledger):
    """Each of these used to be rebuilt or coerced to 0 and written back, with
    the meter reading `current`: the counts it held were lost with no symptom."""
    text = json.dumps(ledger)
    _file(tmp_path).write_text(text, encoding="utf-8")
    data, problem = tt._read_ledger(_file(tmp_path))
    assert data is None and problem, ledger
    st = tt._State()
    for _ in range(4):
        st.add(10, str(tmp_path), "search_symbols")
    assert _file(tmp_path).read_text(encoding="utf-8") == text
    assert st.lifetime_label()[sm.STATUS] != "current"


@pytest.mark.parametrize(
    "ledger",
    [
        {"total_tokens_saved": 1000, "daily": "garbage"},
        {"total_tokens_saved": 1000, "measured_days": None},
        {"total_tokens_saved": 1000, "by_tool": []},
    ],
    ids=["daily string", "measured null", "by_tool empty list"],
)
def test_a_wrong_shape_that_holds_no_count_is_still_rebuilt(tmp_path, ledger):
    """Nothing is lost by rebuilding a value that holds no count, so the merge
    replaces it as before and the meter keeps measuring."""
    _file(tmp_path).write_text(json.dumps(ledger), encoding="utf-8")
    data, problem = tt._read_ledger(_file(tmp_path))
    assert problem is None and data == ledger


def test_a_whole_float_generation_and_count_are_read_as_numbers(tmp_path):
    ledger = {
        "total_tokens_saved": 1000,
        "basis_generation": 2.0,
        "measured_days": {TODAY: {"calls": 4.0, "saved": 0}},
    }
    _file(tmp_path).write_text(json.dumps(ledger), encoding="utf-8")
    assert tt._read_ledger(_file(tmp_path))[1] is None
    out = tt._merge_into(dict(ledger, measured_days=dict(ledger["measured_days"])),
                         {"anon_id": "a", "saved": 5, "calls": 2, "by_tool": {},
                          "by_tool_since": None, "encoding": 0})
    assert out[0] == "a"


def test_a_file_mistyped_mid_session_stops_the_meter_reading_current(tmp_path, caplog):
    """The file loads clean, then another writer leaves it mistyped. The next
    write is refused, the batch is kept, and the label stops reading current."""
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 1000}), encoding="utf-8")
    st = tt._State()
    st.add(10, str(tmp_path), "search_symbols")
    bad = json.dumps({"total_tokens_saved": 1000, "daily": [5, 6]})
    _file(tmp_path).write_text(bad, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=tt.logger.name):
        for _ in range(4):
            st.add(10, str(tmp_path), "search_symbols")
        st.flush()
    assert _file(tmp_path).read_text(encoding="utf-8") == bad
    assert st._unflushed > 0
    assert st.lifetime_label()[sm.STATUS] != "current"
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_a_merge_defect_is_logged_with_its_traceback_and_keeps_the_batch(tmp_path, monkeypatch):
    """The catch-all around the merge is reached only by a shape the reader does
    not know or a bug in the merge. It keeps the batch, leaves the file whole,
    and carries the traceback, so our defect is not read as the user's file."""
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 1000}), encoding="utf-8")

    def broken(data, batch):
        raise KeyError("by_tool_since")

    monkeypatch.setattr(tt, "_merge_into", broken)
    batch = {"anon_id": None, "saved": 30, "calls": 3, "by_tool": {}, "by_tool_since": None,
             "encoding": 0}
    out = tt._merge_batch(tmp_path, batch, 1.0)
    assert out["ok"] is False and out["exc_info"] is True and out["level"] == "warning"
    assert "wrong type" not in out["msg"]
    assert json.loads(_file(tmp_path).read_text(encoding="utf-8")) == {"total_tokens_saved": 1000}
