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


def _docstring() -> str:
    tree = ast.parse((ROOT / "src" / "jcodemunch_mcp" / "__init__.py").read_text(encoding="utf-8"))
    return ast.get_docstring(tree) or ""


def test_the_registry_and_the_marketplace_carry_the_same_short_line():
    assert _marketplace_description() == _server_json_description()


def test_the_docstring_carries_the_pypi_summary():
    assert _pyproject_description() in _docstring()


def test_no_description_carries_the_old_project_name():
    for where, text in [
        ("pyproject.toml", _pyproject_description()),
        ("server.json", _server_json_description()),
        ("marketplace.json", _marketplace_description()),
        ("__init__.py", _docstring()),
    ]:
        assert "github-codemunch" not in text.lower(), where
