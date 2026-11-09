"""Every place the package describes itself carries the same line.

Directories copy the MCP registry entry (server.json), the PyPI summary
(pyproject.toml), the plugin marketplace entry and the package docstring.
These four drifted apart, and the docstring kept an old project name for months,
because each was edited on its own. The short line (registry, marketplace) and the
medium line (PyPI, docstring) are pinned to each other here, so the next wording
change has to update all of them.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_description() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^description\s*=\s*"([^"]+)"', text, re.M)
    assert m, "pyproject.toml has no description"
    return m.group(1)


def _server_json_description() -> str:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))["description"]


def _marketplace_description() -> str:
    data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    rows = [p for p in data["plugins"] if p.get("name") == "jcodemunch"]
    assert len(rows) == 1, [p.get("name") for p in data["plugins"]]
    return rows[0]["description"]


def _plugin_manifest_description() -> str:
    path = ROOT / "plugin" / "jcodemunch" / ".claude-plugin" / "plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))["description"]


def _docstring() -> str:
    tree = ast.parse((ROOT / "src" / "jcodemunch_mcp" / "__init__.py").read_text(encoding="utf-8"))
    return ast.get_docstring(tree) or ""


def test_the_registry_and_the_marketplace_carry_the_same_short_line():
    assert _marketplace_description() == _server_json_description()


def test_the_docstring_carries_the_pypi_summary():
    assert _pyproject_description() in _docstring()


def test_every_description_says_commercial_use_needs_a_license():
    """Directories copy these lines, and one listing called the package
    "completely free" while none of them mentioned the license. LICENSE
    charges for commercial use only, so the line names commercial use;
    "work use" would also cover research and non-commercial work, which
    LICENSE leaves free. The registry length limit lives in
    test_server_json_sync.py."""
    missing = []
    for where, text in [
        ("pyproject.toml", _pyproject_description()),
        ("server.json", _server_json_description()),
        ("marketplace.json", _marketplace_description()),
        ("plugin.json", _plugin_manifest_description()),
        ("__init__.py", _docstring()),
    ]:
        low = text.lower()
        if not (re.search(r"\bcommercial use\b", low) and re.search(r"\blicense\b", low)):
            missing.append(where)
        if re.search(r"\bwork use\b", low):
            missing.append(f"{where}: says 'work use'")
    assert not missing, missing


def test_no_description_carries_the_old_project_name():
    for where, text in [
        ("pyproject.toml", _pyproject_description()),
        ("server.json", _server_json_description()),
        ("marketplace.json", _marketplace_description()),
        ("__init__.py", _docstring()),
    ]:
        assert "github-codemunch" not in text.lower(), where
