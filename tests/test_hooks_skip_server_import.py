"""A hook command starts without importing the server module.

Every hook went through `server.cli`, so each hook process imported
`server.py`, the MCP SDK, httpx, jsonschema and every tool before it read its
stdin. Measured on this project: 4.5 s of imports on a loaded machine, paid on
every Read, Grep, Glob and Bash call once `init --guidance` installs the
model-facing hooks. The console entry now runs a bare `hook-<name>` directly,
after the same first steps as `server.main`.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp import cli_entry

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"


def _server_hook_dispatch() -> dict:
    """`if args.command == "hook-x": from .cli.hooks import run_y` pairs in server.main."""
    tree = ast.parse((SRC / "server.py").read_text(encoding="utf-8"))
    pairs = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Attribute)
            and test.left.attr == "command"
            and isinstance(test.comparators[0], ast.Constant)
        ):
            continue
        name = test.comparators[0].value
        if not str(name).startswith("hook-"):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.ImportFrom) and stmt.module == "cli.hooks":
                pairs[name] = stmt.names[0].name
    return pairs


def test_the_runner_map_matches_the_server_dispatch():
    server = _server_hook_dispatch()
    assert server, "found no hook dispatch in server.main"
    assert cli_entry.HOOK_RUNNERS == server


def test_every_runner_exists():
    from jcodemunch_mcp.cli import hooks

    for runner in cli_entry.HOOK_RUNNERS.values():
        assert callable(getattr(hooks, runner))


def test_the_console_scripts_point_at_the_thin_entry():
    text = (SRC.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'jcodemunch-mcp = "jcodemunch_mcp.cli_entry:cli"' in text
    main_py = (SRC / "__main__.py").read_text(encoding="utf-8")
    assert "from .cli_entry import cli" in main_py and "cli()" in main_py


def _run(code: str, stdin: str, tmp_path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC.parent) + os.pathsep + env.get("PYTHONPATH", "")
    env["CODE_INDEX_PATH"] = str(tmp_path / "store")
    return subprocess.run(
        [sys.executable, "-c", code],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_a_hook_runs_without_importing_the_server(tmp_path):
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": str(tmp_path),
        }
    )
    code = (
        "import sys\n"
        "from jcodemunch_mcp import cli_entry\n"
        "try:\n"
        "    cli_entry.cli(['hook-pretooluse'])\n"
        "except SystemExit as e:\n"
        "    rc = e.code\n"
        "print('SERVER' if 'jcodemunch_mcp.server' in sys.modules else 'NO_SERVER', rc, file=sys.stderr)\n"
    )
    res = _run(code, payload, tmp_path)
    assert "NO_SERVER 0" in res.stderr, res.stderr


def test_the_hook_path_still_checks_package_integrity(monkeypatch):
    calls = []
    import jcodemunch_mcp.security as security
    from jcodemunch_mcp.cli import hooks

    monkeypatch.setattr(
        security, "verify_package_integrity", lambda: calls.append("integrity")
    )
    monkeypatch.setattr(hooks, "run_precompact", lambda: calls.append("hook") or 0)
    with pytest.raises(SystemExit) as exc:
        cli_entry.cli(["hook-precompact"])
    assert exc.value.code == 0
    assert calls == ["integrity", "hook"]


def test_anything_else_goes_to_the_server(monkeypatch):
    seen = []
    import jcodemunch_mcp.server as server

    monkeypatch.setattr(server, "main", lambda argv=None: seen.append(argv))
    cli_entry.cli(["hook-pretooluse", "--extra"])
    cli_entry.cli(["--version"])
    assert seen == [["hook-pretooluse", "--extra"], ["--version"]]
