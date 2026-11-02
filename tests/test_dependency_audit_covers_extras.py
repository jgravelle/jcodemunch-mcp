"""The dependency Floor audits what a user can install, extras included.

`deps.vuln_max` exported the dependency set with `uv export --no-dev` and no
extras, so a package reached only through `jcodemunch-mcp[semantic]`, `[http]`
or `[all]` was never audited, though the README tells users to install them.
These tests capture the commands the measurer runs; nothing reaches the network.
"""

from __future__ import annotations

import importlib
import json

import pytest

harness = importlib.import_module("harness.__main__")


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_run(cmd, *, env=None, **_):
        calls.append(cmd)
        if cmd[:2] == ["uv", "export"]:
            out = cmd[cmd.index("-o") + 1]
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("click==8.5.0\n")
            return 0, "", 0.0
        return 0, json.dumps({"dependencies": [{"name": "click", "version": "8.5.0", "vulns": []}]}), 0.0

    monkeypatch.setattr(harness, "_run", fake_run)
    return calls


def test_the_export_includes_every_extra(captured):
    assert harness._measure_deps() == 0
    export = next(c for c in captured if c[:2] == ["uv", "export"])
    assert "--all-extras" in export


def test_the_export_still_leaves_out_dev_tools(captured):
    harness._measure_deps()
    export = next(c for c in captured if c[:2] == ["uv", "export"])
    assert "--no-dev" in export


def test_the_floor_names_what_it_measures():
    rows = json.loads((harness.HERE / "thresholds.json").read_text(encoding="utf-8"))["thresholds"]
    row = next(r for r in rows if r.get("id") == "deps.vuln_max")
    assert "extra" in row["metric"]
