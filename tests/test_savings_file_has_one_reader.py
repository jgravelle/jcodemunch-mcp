"""`receipt` and `diagnose` read `_savings.json` through the meter's own reader (LEDGER L-297).

`token_tracker._read_ledger` refuses a savings file whose totals or tables the
meter cannot add to (#1309, L-293). `receipt.lifetime_meter` and
`diagnose._savings` each parsed the file by their own rules: `receipt` took
`True` as a total and raised `OverflowError` on `Infinity`, and `diagnose`
took a file the meter refuses as readable.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from jcodemunch_mcp import diagnose
from jcodemunch_mcp.cli import receipt

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"


def _write(root: Path, text: str) -> None:
    (root / "_savings.json").write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "text",
    [
        '{"total_tokens_saved": Infinity}',
        '{"total_tokens_saved": NaN}',
        '{"total_tokens_saved": true}',
        '{"total_tokens_saved": 500, "by_tool": {"search_symbols": "12"}}',
        '{"total_tokens_saved": 500, "total_encoding_tokens_saved": "x"}',
    ],
)
def test_receipt_refuses_a_file_the_meter_refuses(tmp_path, text):
    _write(tmp_path, text)
    assert receipt.lifetime_meter(tmp_path) is None


def test_receipt_still_reads_a_good_file(tmp_path):
    _write(tmp_path, json.dumps({"total_tokens_saved": 1234, "anon_id": "a"}))
    got = receipt.lifetime_meter(tmp_path)
    assert got is not None and got["total_tokens_saved"] == 1234


def test_receipt_reads_a_file_with_a_byte_order_mark(tmp_path):
    (tmp_path / "_savings.json").write_text(
        '﻿{"total_tokens_saved": 9}', encoding="utf-8"
    )
    got = receipt.lifetime_meter(tmp_path)
    assert got is not None and got["total_tokens_saved"] == 9


def test_receipt_reads_nothing_from_an_empty_meter(tmp_path):
    assert receipt.lifetime_meter(tmp_path) is None
    _write(tmp_path, '{"total_tokens_saved": 0}')
    assert receipt.lifetime_meter(tmp_path) is None


def test_diagnose_gives_no_figure_from_a_file_the_meter_refuses(tmp_path):
    _write(tmp_path, '{"total_tokens_saved": 500, "by_tool": {"search_symbols": "12"}}')
    assert diagnose._savings(tmp_path) is None


def test_diagnose_reads_absent_as_empty_and_good_as_itself(tmp_path):
    assert diagnose._savings(tmp_path) == {}
    _write(tmp_path, '{"total_tokens_saved": 7}')
    assert diagnose._savings(tmp_path) == {"total_tokens_saved": 7}


def _names_the_savings_file(func: ast.AST) -> bool:
    """True when the function names `_savings.json` in code, not in its docstring."""
    doc = ast.get_docstring(func, clean=False)
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "_savings.json" in node.value and node.value != doc:
                return True
    return False


def _json_parses(func: ast.AST) -> list:
    return [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"load", "loads"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"json", "_json"}
    ]


def test_no_other_function_parses_the_savings_file():
    """A function that names `_savings.json` reads it through `_read_ledger`, never `json.load`.

    Keyed on the file name in code, not on a variable's spelling: `receipt`
    read the file as `json.loads(path.read_text(...))`.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "token_tracker.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if isinstance(
                func, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and _names_the_savings_file(func):
                for call in _json_parses(func):
                    offenders.append(
                        f"{path.relative_to(SRC)}:{call.lineno} {func.name}"
                    )
    assert not offenders, offenders


# --- a refused file is not an absent one ---------------------------------------


def _agg():
    return receipt.aggregate([])


def test_receipt_says_why_a_refused_file_gives_no_figure(tmp_path):
    _write(tmp_path, '{"total_tokens_saved": Infinity}')
    why = receipt.lifetime_meter_refusal(tmp_path)
    assert why == "holds a total that is not a non-negative number"
    text = receipt.render_text(_agg(), days=30, model="sonnet", meter=None, meter_refused=why)
    assert "Lifetime meter not shown" in text and why in text
    assert "No lifetime meter data yet" not in text
    payload = json.loads(receipt.render_json(_agg(), model="sonnet", meter=None, meter_refused=why))
    assert payload["lifetime"] == {"refused": why}


def test_receipt_reports_no_file_as_no_data_yet(tmp_path):
    assert receipt.lifetime_meter_refusal(tmp_path) is None
    text = receipt.render_text(_agg(), days=30, model="sonnet", meter=None, meter_refused=None)
    assert "No lifetime meter data yet" in text
    payload = json.loads(receipt.render_json(_agg(), model="sonnet", meter=None))
    assert "lifetime" not in payload


def test_a_zero_byte_file_is_refused_not_absent(tmp_path):
    _write(tmp_path, "")
    assert receipt.lifetime_meter(tmp_path) is None
    assert receipt.lifetime_meter_refusal(tmp_path) == "is not valid JSON"
    assert diagnose._savings(tmp_path) is None


def test_a_good_file_has_no_refusal(tmp_path):
    _write(tmp_path, '{"total_tokens_saved": 5}')
    assert receipt.lifetime_meter_refusal(tmp_path) is None
