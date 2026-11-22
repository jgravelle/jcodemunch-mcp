"""`get_changed_symbols` takes no `cross_repo` argument (LEDGER L-57).

The parameter was in the signature and nothing read it: the MCP schema did not
offer it, the dispatcher did not pass it, and the embedded blast walk never saw
it. A Python caller who set it got a local-only blast with no sign the flag did
nothing. Removed, so the same call now fails loudly instead of being ignored.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols


def _unread_parameters(fn_source: str) -> list[str]:
    """Parameters the function body never READS as a name.

    Walks the AST, so a name that appears only in a comment, a string, a longer
    identifier or as an assignment target does not count as a read.
    """
    fn = ast.parse(textwrap.dedent(fn_source)).body[0]
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    reads = {
        node.id
        for stmt in fn.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return [p for p in params if p not in reads]


def test_the_signature_has_no_cross_repo():
    assert "cross_repo" not in inspect.signature(get_changed_symbols).parameters


def test_passing_cross_repo_is_an_error_not_a_silent_no_op(tmp_path):
    with pytest.raises(TypeError):
        get_changed_symbols("o/r", storage_path=str(tmp_path), cross_repo=True)  # type: ignore[call-arg]


def test_every_remaining_parameter_is_read_by_the_body():
    assert _unread_parameters(inspect.getsource(get_changed_symbols)) == []


@pytest.mark.parametrize(
    "body",
    [
        "    # cross_repo is ignored\n    return repo\n",
        "    note = 'cross_repo is ignored'\n    return repo, note\n",
        "    repo_cross_repo = 1\n    return repo, repo_cross_repo\n",
        "    cross_repo = False\n    return repo, 0\n",
    ],
    ids=["comment", "string", "longer_identifier", "assigned_not_read"],
)
def test_the_read_check_sees_an_unread_parameter_in_every_spelling(body):
    """Non-vacuity: each spelling that names `cross_repo` without reading it is caught."""
    src = "def f(repo, cross_repo=None):\n" + body
    assert _unread_parameters(src) == ["cross_repo"]
