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
