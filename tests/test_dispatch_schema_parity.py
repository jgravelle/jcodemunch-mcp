"""Schema-vs-dispatch param parity (PRD WI-3.2 / F-P02 / F-C02).

The existing registration guard (test_config.py) checks that every built tool
NAME is in _CANONICAL_TOOL_NAMES — presence only, not param-level. So a param
wired into the dispatch but missing from the declared inputSchema (a caller
cannot know to pass it) slips through silently. Adding a param touches ~7
hand-synced spots; nothing tested that the schema and the dispatch agree at the
argument level.

This parses the dispatcher's source (`_call_tool_impl`), collects the literal argument keys each
`if/elif name == "<tool>"` branch reads via arguments["k"] / arguments.get("k"),
and asserts each is a declared inputSchema property of that tool.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from jcodemunch_mcp import server as server_mod
from jcodemunch_mcp.server import _build_tools_list


# Cross-cutting keys consumed by call_tool that are intentionally NOT per-tool
# inputSchema properties.
_NON_SCHEMA_KEYS = {
    "format",   # popped before dispatch (compact-output selector, server.py)
}

# (tool, key) pairs the dispatch reads as a documented forgiving alias of a
# declared property — accepted input, deliberately not surfaced in the schema.
_KNOWN_FORGIVING_ALIASES = {
    ("get_file_outline", "file"),  # alias for file_path (arguments.get("file_path") or .get("file"))
}


def _is_name_eq_str(test) -> bool:
    """True for a `name == "<literal>"` comparison."""
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "name"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and isinstance(test.comparators[0].value, str)
    )


def _arg_keys_in(nodes) -> set[str]:
    """Literal string keys read from `arguments` within the given AST nodes."""
    keys: set[str] = set()
    for root in nodes:
        for n in ast.walk(root):
            # arguments["k"]
            if (
                isinstance(n, ast.Subscript)
                and isinstance(n.value, ast.Name)
                and n.value.id == "arguments"
                and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)
            ):
                keys.add(n.slice.value)
            # arguments.get("k"[, default])
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "arguments"
                and n.args
                and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)
            ):
                keys.add(n.args[0].value)
    return keys


def _collect_dispatch_arg_keys() -> dict[str, set[str]]:
    """tool name -> the literal `arguments` keys its dispatch branch(es) read."""
    # v1.108.257 (#425): `call_tool` is now a thin wrapper that applies the
    # response cap; the dispatch chain lives in `_call_tool_impl`. Walking the
    # wrapper finds zero branches and makes this whole file pass VACUOUSLY --
    # which is exactly what `test_dispatch_chain_is_parseable_and_nonempty`
    # caught, while the parity assertions below sailed through green.
    src = textwrap.dedent(inspect.getsource(server_mod._call_tool_impl))
    func = ast.parse(src).body[0]
    assert isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))

    out: dict[str, set[str]] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.If) and _is_name_eq_str(node.test):
            tool = node.test.comparators[0].value
            # node.body is THIS branch only; the elif chain lives in node.orelse
            # and is visited separately by ast.walk.
            out.setdefault(tool, set()).update(_arg_keys_in(node.body))
    return out


def _full_schema_props() -> dict[str, set[str]]:
    from jcodemunch_mcp import config as config_module

    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {
        k: cfg.get(k)
        for k in ("tool_profile", "compact_schemas", "render_diagram_viewer_enabled")
    }
    try:
        cfg["tool_profile"] = "full"
        cfg["compact_schemas"] = False
        # Enable feature flags that gate config-conditional schema properties so
        # the property superset is declared (e.g. render_diagram.open_in_viewer is
        # only added when render_diagram_viewer_enabled is on, but the dispatch
        # always reads it with a default).
        cfg["render_diagram_viewer_enabled"] = True
        tools = _build_tools_list()
        return {
            t.name: set((t.input_schema or {}).get("properties", {}).keys())
            for t in tools
        }
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v


def test_dispatch_reads_only_declared_schema_params():
    """Every `arguments[...]` key a tool's dispatch reads must be a declared
    inputSchema property (else a caller has no way to know to pass it)."""
    schema_props = _full_schema_props()
    dispatch = _collect_dispatch_arg_keys()

    problems: list[str] = []
    for tool, keys in sorted(dispatch.items()):
        if tool not in schema_props:
            # Front-door re-dispatch targets or non-listed handlers; the
            # registration guard covers name presence separately.
            continue
        for key in sorted(keys):
            if key in _NON_SCHEMA_KEYS:
                continue
            if (tool, key) in _KNOWN_FORGIVING_ALIASES:
                continue
            if key not in schema_props[tool]:
                problems.append(
                    f"{tool}: dispatch reads arguments[{key!r}] but it is not a "
                    f"declared inputSchema property"
                )

    assert not problems, (
        "Dispatch reads arguments not declared in the tool schema (param drift):\n"
        + "\n".join(problems)
    )


def test_dispatch_chain_is_parseable_and_nonempty():
    """Guard the guard: if call_tool is refactored such that the AST walk stops
    finding `name == "..."` branches, this test fails loudly rather than passing
    vacuously."""
    dispatch = _collect_dispatch_arg_keys()
    assert len(dispatch) > 50, (
        f"Only {len(dispatch)} dispatch branches found by AST walk; the parity "
        f"check may have gone vacuous (call_tool dispatch shape changed?)."
    )
