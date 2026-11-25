"""Every key the VS Code risk gutter reads is in `file-risk`'s output.

`vscode-extension/src/riskGutter.ts` runs `jcodemunch-mcp file-risk <file>`
and reads its JSON through the TypeScript interfaces at the top of that file.
Nothing pinned those keys: a rename in `get_file_risk` would leave the gutter
painting nothing, with no test failing on either side.

The expected keys are read from the TS interfaces themselves, so a field the
extension starts reading is checked with no edit here. Each required field
must be present with a value of its TypeScript type; an optional field (`?`)
may be absent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jcodemunch_mcp.cli.file_risk import main as file_risk_main
from jcodemunch_mcp.tools.index_folder import index_folder

ROOT = Path(__file__).resolve().parents[1]
GUTTER = ROOT / "vscode-extension" / "src" / "riskGutter.ts"
TOP = "FileRiskResponse"

_INTERFACE = re.compile(r"^interface (\w+) \{\n(.*?)^\}", re.M | re.S)
_FIELD = re.compile(r"^\s*(\w+)(\??):\s*([^;]+);", re.M)


def _interfaces(source: str) -> dict:
    out = {}
    for name, body in _INTERFACE.findall(source.replace("\r\n", "\n")):
        out[name] = [(f, opt == "?", t.strip()) for f, opt, t in _FIELD.findall(body)]
    return out


def _mismatches(value, type_name: str, ifaces: dict, path: str) -> list[str]:
    if type_name.endswith("[]"):
        if not isinstance(value, list):
            return [f"{path}: expected an array, got {type(value).__name__}"]
        found = []
        for i, item in enumerate(value):
            found += _mismatches(item, type_name[:-2], ifaces, f"{path}[{i}]")
        return found
    if type_name in ifaces:
        if not isinstance(value, dict):
            return [f"{path}: expected an object, got {type(value).__name__}"]
        found = []
        for field, optional, ftype in ifaces[type_name]:
            if field not in value:
                if not optional:
                    found.append(f"{path}.{field}: missing")
                continue
            found += _mismatches(value[field], ftype, ifaces, f"{path}.{field}")
        return found
    if type_name == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif type_name == "string":
        ok = isinstance(value, str)
    elif type_name == "boolean":
        ok = isinstance(value, bool)
    elif "|" in type_name:  # a union of string literals
        allowed = {s.strip().strip('"') for s in type_name.split("|")}
        ok = value in allowed
    else:
        return [f"{path}: TypeScript type {type_name!r} is not one this test knows"]
    return [] if ok else [f"{path}: {value!r} is not a {type_name}"]


def test_the_gutter_declares_the_response_interface():
    ifaces = _interfaces(GUTTER.read_text(encoding="utf-8"))
    assert TOP in ifaces, sorted(ifaces)
    fields = {f for f, _, _ in ifaces[TOP]}
    assert {"symbols", "file_metrics"} <= fields, fields


@pytest.fixture
def file_risk_output(tmp_path, capsys):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "util.py").write_text(
        "def helper(x):\n"
        "    if x > 1:\n"
        "        return x\n"
        "    for i in range(x):\n"
        "        if i % 2:\n"
        "            x += i\n"
        "    return x\n",
        encoding="utf-8",
    )
    (src / "app.py").write_text("from util import helper\n\n\ndef run():\n    return helper(3)\n", encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    capsys.readouterr()
    code = file_risk_main([str(src / "util.py"), "--storage-path", str(store)])
    out = capsys.readouterr()
    assert code == 0, out.err
    return json.loads(out.out)


def test_every_key_the_gutter_reads_is_in_the_output(file_risk_output):
    ifaces = _interfaces(GUTTER.read_text(encoding="utf-8"))
    assert file_risk_output["symbols"], "no symbols: the per-symbol keys went unchecked"
    problems = _mismatches(file_risk_output, TOP, ifaces, "file-risk")
    assert not problems, "\n".join(problems)


def test_the_check_fails_on_a_renamed_key(file_risk_output):
    ifaces = _interfaces(GUTTER.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(file_risk_output))
    broken["file_metrics"]["churn_90d"] = broken["file_metrics"].pop("churn_30d")
    broken["symbols"][0]["risk"]["level"] = "purple"
    problems = _mismatches(broken, TOP, ifaces, "file-risk")
    assert "file-risk.file_metrics.churn_30d: missing" in problems
    assert any("purple" in p for p in problems), problems
