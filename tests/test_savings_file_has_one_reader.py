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


# The file is named by its string, by the authority's own constant and path
# helper, or by a module constant that holds the string. Any of them counts.
_FILE_NAMES = {"_SAVINGS_FILE"}
_FILE_HELPERS = {"_savings_path"}


def _module_spellings(tree: ast.Module) -> "tuple[set, set]":
    """Module-level names bound to the savings file, and local names for `json.load(s)`."""
    file_names = set(_FILE_NAMES)
    parse_names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _holds_the_file_name(node.value, set()):
            file_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.ImportFrom) and node.module == "json":
            parse_names |= {
                alias.asname or alias.name
                for alias in node.names
                if alias.name in {"load", "loads"}
            }
    return file_names, parse_names


def _holds_the_file_name(node: ast.AST, file_names: set) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and "_savings.json" in sub.value:
            return True
        if isinstance(sub, ast.Name) and sub.id in file_names:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in file_names:
            return True
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in _FILE_HELPERS:
                return True
    return False


def _names_the_savings_file(scope: ast.AST, file_names: set) -> bool:
    """True when the scope names the savings file in code, not in a docstring."""
    doc = ast.get_docstring(scope, clean=False) if not isinstance(scope, ast.Module) else None
    for stmt in _own_statements(scope):
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value == doc:
            continue
        if _holds_the_file_name(stmt, file_names):
            return True
    return False


def _own_statements(scope: ast.AST) -> list:
    """A function's body, or a module's statements outside its functions and classes."""
    body = list(getattr(scope, "body", []))
    if isinstance(scope, ast.Module):
        body = [s for s in body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return body


def _json_parses(scope: ast.AST, parse_names: set) -> list:
    found = []
    for stmt in _own_statements(scope):
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr in {"load", "loads"}
                and isinstance(f.value, ast.Name)
                and f.value.id in {"json", "_json"}
            ) or (isinstance(f, ast.Name) and f.id in parse_names):
                found.append(node)
    return found


def _offenders(source: str, label: str = "<src>") -> list:
    tree = ast.parse(source)
    file_names, parse_names = _module_spellings(tree)
    scopes = [tree] + [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    out = []
    for scope in scopes:
        if _names_the_savings_file(scope, file_names):
            for call in _json_parses(scope, parse_names):
                out.append(f"{label}:{call.lineno} {getattr(scope, 'name', '<module>')}")
    return out


def test_no_other_function_parses_the_savings_file():
    """Code that names the savings file reads it through `_read_ledger`, never `json.load`.

    Keyed on how the file is named in code, not on a variable's spelling:
    `receipt` read it as `json.loads(path.read_text(...))`.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "token_tracker.py":
            continue
        offenders += _offenders(path.read_text(encoding="utf-8"), str(path.relative_to(SRC)))
    assert not offenders, offenders


@pytest.mark.parametrize(
    "source",
    [
        'import json\ndef f(root):\n    return json.loads((root / "_savings.json").read_text())\n',
        "import json\nfrom .storage import token_tracker\ndef f():\n"
        "    return json.loads(token_tracker._savings_path().read_text())\n",
        "import json\nfrom .storage.token_tracker import _SAVINGS_FILE\ndef f(root):\n"
        "    return json.load(open(root / _SAVINGS_FILE))\n",
        'import json\nLEDGER = "_savings.json"\ndef f(root):\n    return json.loads((root / LEDGER).read_text())\n',
        'from json import loads as parse\ndef f(root):\n    return parse((root / "_savings.json").read_text())\n',
        'import json\nDATA = json.loads(open("_savings.json").read())\n',
    ],
    ids=["string", "path-helper", "constant", "module-constant", "from-import", "module-level"],
)
def test_the_ratchet_sees_every_spelling_of_the_file_and_the_parse(source):
    assert _offenders(source), source


def test_the_ratchet_ignores_a_docstring_and_the_meters_reader():
    doc = 'import json\ndef f(root):\n    """Reads _savings.json elsewhere."""\n    return json.loads(root.read_text())\n'
    via_reader = (
        "from .storage import token_tracker\ndef f(root):\n"
        '    return token_tracker._read_ledger(root / "_savings.json")\n'
    )
    assert _offenders(doc) == [] and _offenders(via_reader) == []


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
