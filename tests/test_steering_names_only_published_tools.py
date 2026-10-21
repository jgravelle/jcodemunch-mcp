"""The steering hook names only tools the active surface publishes.

Found by Benchmarking on 1.108.386 (M4 design, 2026-10-20). `init`'s default
surface is `counter`: the client's tool list holds the front door (`order`,
`menu`, `route`) and the always-present controls, and every other action is
reachable only as `order { "action": ... }`. The PreToolUse steering told the
agent to use search_text, search_symbols, check_references, find_references,
find_importers, get_file_tree, get_repo_outline, get_file_outline and
get_symbol_source by bare name, i.e. tools the agent cannot call. The
`CLAUDE.md` policy `init` writes already speaks in `order` calls (cli/policy.py);
the hook was a second copy of the same guidance that never asked.

⚠ The assertion is the PROPERTY over every path the hook emits: the Grep,
Glob and Bash search nudge and its strict deny, and the Read nudge and its
strict deny. The first draft covered only the search table and passed with the
Read path still naming two bare tools (review, round 1), which is why each
path is driven through `run_pretooluse` here, the way a client reaches it.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from jcodemunch_mcp.cli import policy
from jcodemunch_mcp.cli.hooks import run_pretooluse, steering
from jcodemunch_mcp.cli.hooks._common import _norm_path
from jcodemunch_mcp.server import _catalog_names

_ACTION_RE = re.compile(r'"action":\s*"([a-z_]+)"')
_WORD_RE = re.compile(r"\b[a-z][a-z0-9_]+\b")


def _hook_text(tool_name: str, tool_input: dict, cwd: str) -> str:
    stdin = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool_name,
                        "tool_input": tool_input, "cwd": cwd})
    out = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(stdin)), mock.patch.object(sys, "stdout", out):
        run_pretooluse()
    payload = json.loads(out.getvalue())
    hso = payload.get("hookSpecificOutput", {})
    return hso.get("additionalContext") or hso.get("permissionDecisionReason") or ""


def _direct_calls(text: str) -> set[str]:
    """Catalog tool names the text names outside an `"action": "..."` slot."""
    return {w for w in _WORD_RE.findall(_ACTION_RE.sub("", text)) if w in set(_catalog_names())}


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    monkeypatch.setattr(steering, "_indexed_source_roots", lambda: [_norm_path(str(tmp_path))])
    big = tmp_path / "mod.py"
    big.write_text("x = 1\n" * 4000, encoding="utf-8")
    return tmp_path, big


def _paths(root: Path, big: Path):
    return [
        ("Grep", {"pattern": "foo", "path": str(root)}),
        ("Glob", {"pattern": "*.py", "path": str(root)}),
        ("Bash", {"command": f"grep -rn foo {root}"}),
        ("Read", {"file_path": str(big)}),
    ]


@pytest.mark.parametrize("enforce", ["advisory", "strict"])
def test_on_the_counter_surface_every_named_tool_is_callable(indexed, monkeypatch, enforce):
    root, big = indexed
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
    monkeypatch.setenv("JCODEMUNCH_ENFORCE", enforce)
    published = policy._front_door_tool_names()
    for tool_name, tool_input in _paths(root, big):
        text = _hook_text(tool_name, tool_input, str(root))
        assert text, (tool_name, "emitted nothing; the fixture no longer reaches this path")
        assert _direct_calls(text) <= published, (tool_name, _direct_calls(text) - published, text)
        actions = set(_ACTION_RE.findall(text))
        assert actions and actions <= set(_catalog_names()), (tool_name, actions, text)
        assert "order" in text and '"args"' in text, (tool_name, text)


def test_on_the_full_surface_the_routes_are_named_directly(indexed, monkeypatch):
    root, big = indexed
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
    for tool_name, tool_input in _paths(root, big):
        text = _hook_text(tool_name, tool_input, str(root))
        assert text and _direct_calls(text) and not _ACTION_RE.search(text), (tool_name, text)


def test_a_whitespace_env_value_falls_back_to_the_configured_surface(monkeypatch):
    """The hook reads the surface through `counter.resolve_tool_surface`, the
    authority the server uses: blank env is unset, so config decides."""
    from jcodemunch_mcp import config

    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "   ")
    real_get = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: "counter" if k == "tool_surface" else real_get(k, *a, **kw))
    assert '"action": "search_text"' in steering._routes_for("Grep")


def test_the_hook_path_imports_no_server_module():
    """A hook runs once per tool call; the server module's import cost must not
    ride on every Grep. Checked in a fresh interpreter, because this test
    module itself imports the server."""
    code = (
        "import os, sys; os.environ['JCODEMUNCH_TOOL_SURFACE'] = 'counter'\n"
        "from jcodemunch_mcp.cli.hooks import steering\n"
        "steering._routes_for('Grep'); steering._read_routes()\n"
        "print('jcodemunch_mcp.server' in sys.modules)\n"
    )
    src = str(Path(steering.__file__).resolve().parents[3])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": src}, check=True)
    assert out.stdout.strip() == "False", out.stdout + out.stderr
