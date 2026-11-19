"""What the client was told and what the telemetry row records must agree.

#551 (@rknighton): `_call_tool_impl` tracked its outcome in a local flag
initialised to ``True``, and three of its four error exits never cleared it.
Schema-validation rejections, the ``search_text`` argument guard and a
front-door relay of a child's refusal all returned ``isError=True`` to the
client and wrote ``ok=1`` to ``tool_calls`` -- a 0% error rate over calls the
client watched fail.

#552 (@rknighton): the Counter's ``order`` gate returned a body whose only key
was ``error`` and did not set ``isError`` at all, so a client branching on the
flag v1.108.74 added for exactly that purpose read a refusal as a success.

⚠ Both are guarded here as PROPERTIES, not as the reported sites. The reported
counts were three exits and two gates; the real counts were more in both cases,
and a fifth exit with the identical shape could not be made to fire in the
reporter's harness at all. A test naming the sites would pass while the next one
is added.
"""

import ast
import re
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "server.py"


@pytest.fixture(scope="module")
def module() -> ast.Module:
    return ast.parse(_SERVER.read_text(encoding="utf-8"))


def _function(module: ast.Module, name: str) -> ast.AST:
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone from server.py")


def _returned_calls(fn: ast.AST, *, skip: frozenset = frozenset()) -> list[str]:
    """Names of functions whose call is the subject of a `return` inside `fn`.

    ⚠ `ast.walk` yields a skipped function's CHILDREN too, so the excluded
    subtrees are collected first rather than filtered node by node -- the naive
    version reported `_fail`'s own body as an offending exit.
    """
    excluded = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in skip:
            excluded.update(id(n) for n in ast.walk(node))
    out = []
    for node in ast.walk(fn):
        if id(node) in excluded:
            continue
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name):
                out.append(node.value.func.id)
    return out


def test_every_in_frame_error_exit_clears_the_outcome_flag(module):
    """⚠ The guard is `_error_call_result` being UNREACHABLE as a direct return
    from this frame -- not a count of exits. `_fail` is the only door, so a new
    exit added tomorrow either goes through it or fails here."""
    fn = _function(module, "_call_tool_impl")
    # `_fail` itself is the one legitimate caller inside this frame.
    returned = _returned_calls(fn, skip=frozenset({"_fail"}))
    assert "_error_call_result" not in returned, (
        "_call_tool_impl returns _error_call_result directly. Use _fail, which "
        "clears the heartbeat's outcome flag; a bare _error_call_result leaves "
        "the call reported as 'ok' to the progress channel (#551)."
    )
    assert "_fail" in returned, "_call_tool_impl no longer routes any error exit through _fail"


def test_the_outcome_flag_is_cleared_inside_fail(module):
    fn = _function(module, "_call_tool_impl")
    fail = next(n for n in ast.walk(fn)
                if isinstance(n, ast.FunctionDef) and n.name == "_fail")
    assigns = [t.id for n in ast.walk(fail) if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)]
    nonlocals = [name for n in ast.walk(fail) if isinstance(n, ast.Nonlocal) for name in n.names]
    assert "_call_ok" in nonlocals, "_fail must declare _call_ok nonlocal or it writes a new local"
    assert "_call_ok" in assigns, "_fail no longer clears _call_ok"


def test_the_telemetry_row_is_derived_from_the_returned_result(module):
    """⚠⚠ The row must NOT come from a flag any frame asserts about itself.

    `_enforce_response_cap` refuses AFTER `_call_tool_impl`'s `finally` has run,
    so no flag inside that frame can ever describe a capped call. Reading
    `isError` off the value `call_tool` returns is the only place that sees
    every refusal, including that one.
    """
    fn = _function(module, "call_tool")
    src = ast.unparse(fn)
    assert "record_tool_latency" in src, (
        "call_tool no longer records the latency row. It was moved here from "
        "_call_tool_impl because only this frame sees the capped result (#551)."
    )
    assert re.search(r"is_error", src), (
        "call_tool no longer derives the outcome from the returned result's isError"
    )
    impl = ast.unparse(_function(module, "_call_tool_impl"))
    assert "record_tool_latency" not in impl, (
        "_call_tool_impl writes a latency row again. Two writers means two "
        "answers for one call, and the inner one cannot see the response cap."
    )


@pytest.mark.parametrize("handler", ["_handle_counter_tool", "_handle_order", "_handle_route"])
def test_front_door_error_bodies_set_is_error(module, handler):
    """#552: a body whose only key is `error` must reach the client with
    `isError=True`. The gate refusals returned a plain TextContent list."""
    fn = _function(module, handler)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        rendered = ast.unparse(node.value)
        if '"error"' not in rendered and "'error'" not in rendered:
            continue
        assert "_error_call_result" in rendered, (
            f"{handler} returns an error body without isError:\n    {rendered[:120]}\n"
            "Route it through _error_call_result (#552)."
        )


def test_the_ast_predicate_fires_on_the_reintroduced_defect():
    """⚠ A green ratchet and an absent ratchet look identical. This rebuilds the
    pre-fix shape and asserts the checks above would have caught it."""
    broken = ast.parse(
        "async def _handle_order(arguments):\n"
        "    if bad:\n"
        "        return [TextContent(type='text', text=json.dumps({'error': err}))]\n"
        "    return await call_tool(action, args)\n"
    )
    fn = _function(broken, "_handle_order")
    offenders = [
        ast.unparse(n.value) for n in ast.walk(fn)
        if isinstance(n, ast.Return) and n.value is not None
        and ("'error'" in ast.unparse(n.value) or '"error"' in ast.unparse(n.value))
        and "_error_call_result" not in ast.unparse(n.value)
    ]
    assert offenders, "the #552 predicate does not fire on the pre-fix shape"
