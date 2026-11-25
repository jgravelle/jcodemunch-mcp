"""An argument the dispatcher honors is never reported as ignored, and a call
whose arguments cannot form one is the caller's error, not an internal one.

Found in use: ``get_file_outline(file=...)`` ran on the named file and its
reply listed ``file`` under ``ignored_arguments``, which also refuses any
absence the call could prove. ``get_file_outline(path=...)`` came back
``"Internal error processing get_file_outline"``, which reads as our bug and
sends the caller nowhere, while the fix was to name ``file_path``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.tools import _arg_contract

SERVER = Path(server.__file__)


#: Keys read in a tool's branch that are rightly reported as ignored when the
#: schema omits them. ``open_in_viewer`` is declared only while
#: ``render_diagram_viewer_enabled`` is on, and the tool does nothing with it
#: while that is off.
NOT_HONORED_WHEN_UNDECLARED = {("render_diagram", "open_in_viewer")}


def _read_key(node):
    """The constant key of ``arguments.get("k", ...)`` or ``arguments["k"]``."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "arguments"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "arguments"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value
    return None


def _dispatched_keys(source: str) -> set[tuple[str, str]]:
    """(tool, key) for every key read inside an ``if/elif name == "<tool>"``
    branch, in any spelling: ``get``, a subscript, a second ``or`` spelling, a
    default that is itself a ``get``. Branches over a SET of names (``name in
    {...}``) are bookkeeping shared by several tools, not one tool's call, so
    they are not this tool's parameters."""
    tree = ast.parse(source)
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if not (
            isinstance(t, ast.Compare)
            and isinstance(t.left, ast.Name)
            and t.left.id == "name"
            and len(t.ops) == 1
            and isinstance(t.ops[0], ast.Eq)
            and isinstance(t.comparators[0], ast.Constant)
        ):
            continue
        tool = t.comparators[0].value
        for sub in node.body:
            for n in ast.walk(sub):
                k = _read_key(n)
                if k:
                    found.add((tool, k))
    return found


def _undeclared(found):
    out = []
    for tool, key in sorted(found):
        declared = server._declared_arg_keys(tool)
        if not declared or (tool, key) in NOT_HONORED_WHEN_UNDECLARED:
            continue
        known = (
            set(declared)
            | _arg_contract.PROTOCOL_KEYS
            | _arg_contract.DISPATCH_ALIASES.get(tool, frozenset())
        )
        if key not in known:
            out.append(f"{tool}:{key}")
    return out


def test_the_scan_sees_the_known_alias():
    """Non-vacuity: the scanner finds the alias this file was written for."""
    assert ("get_file_outline", "file") in _dispatched_keys(
        SERVER.read_text(encoding="utf-8")
    )


def test_every_key_a_tool_branch_reads_is_declared_or_listed():
    missing = _undeclared(_dispatched_keys(SERVER.read_text(encoding="utf-8")))
    assert not missing, (
        "the dispatcher honors these keys but the argument contract would report "
        f"them as ignored; declare them or add them to _arg_contract.DISPATCH_ALIASES: {missing}"
    )


@pytest.mark.parametrize(
    "spelling",
    [
        'file_path = arguments.get("file_path") or arguments.get("filez")',
        'file_path = arguments.get("filez") or arguments.get("file_path")',
        'file_path = arguments.get("file_path", arguments.get("filez"))',
        'file_path = arguments["filez"]',
    ],
)
def test_the_ratchet_fails_on_an_unlisted_alias_in_any_spelling(spelling):
    """Run against the reintroduced defect, not only the fixed tree."""
    src = f'if name == "get_file_outline":\n    {spelling}\n'
    assert _undeclared(_dispatched_keys(src)) == ["get_file_outline:filez"]


def test_an_alias_is_honored_only_for_its_own_tool():
    declared = ("repo", "file_path", "file_paths")
    assert (
        _arg_contract.unrecognized_keys(
            {"repo": "r", "file": "a.py"}, declared, "get_file_outline"
        )
        == []
    )
    assert _arg_contract.unrecognized_keys(
        {"repo": "r", "file": "a.py"}, declared, "find_importers"
    ) == ["file"]


async def _call(name, arguments):
    out = await server.call_tool(name, arguments)
    content = out.content if hasattr(out, "content") else out
    return json.loads(content[0].text), getattr(out, "isError", False)


@pytest.fixture
def repo(tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    return r["repo"], str(store)


@pytest.mark.asyncio
async def test_file_is_honored_and_not_reported_ignored(repo, monkeypatch):
    repo_id, store = repo
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    body, is_error = await _call("get_file_outline", {"repo": repo_id, "file": "a.py"})
    assert not is_error
    assert [s["name"] for s in body["symbols"]] == ["alpha"]
    assert "ignored_arguments" not in (body.get("_meta") or {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args,names",
    [
        ("get_file_outline", {"path": "a.py"}, "'file_path' or 'file_paths'"),
        ("find_importers", {"path": "a.py"}, "'file_path' or 'file_paths'"),
        ("find_references", {"name": "alpha"}, "'identifier' or 'identifiers'"),
        ("check_references", {"name": "alpha"}, "'identifier' or 'identifiers'"),
    ],
)
async def test_a_call_with_neither_argument_is_the_callers_error(
    repo, monkeypatch, tool, args, names
):
    repo_id, store = repo
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    body, is_error = await _call(tool, {"repo": repo_id, **args})
    assert is_error
    assert "Internal error" not in body["error"]
    assert names in body["error"]
    assert body["ignored_arguments"] == sorted(args)
