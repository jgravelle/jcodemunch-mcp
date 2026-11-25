"""`get_changed_symbols` says that `cross_repo` does nothing (LEDGER L-57).

The parameter was in the signature and nothing read it: the MCP schema did not
offer it, the dispatcher did not pass it, and the embedded blast walk never saw
it. A Python caller who set it got a local-only blast with no sign the flag did
nothing. Removing it would break that caller on 1.x, so it stays and a call that
passes it gets a `FutureWarning` naming it as ignored.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import warnings

import pytest

from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols


def _unread_parameters(fn_source: str) -> list[str]:
    """Parameters the function body never READS as a name.

    Walks the AST, so a name that appears only in a comment, a string, a longer
    identifier or as an assignment target does not count as a read.
    """
    fn = ast.parse(textwrap.dedent(fn_source)).body[0]
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    params = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
    reads = {
        node.id
        for stmt in fn.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return [p for p in params if p not in reads]


def test_passing_cross_repo_warns_that_it_does_nothing(tmp_path):
    with pytest.warns(FutureWarning, match="cross_repo has no effect"):
        get_changed_symbols("o/r", storage_path=str(tmp_path), cross_repo=True)


def test_a_call_without_cross_repo_does_not_warn(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        get_changed_symbols("o/r", storage_path=str(tmp_path))


def test_cross_repo_still_accepted_so_a_1x_caller_does_not_break(tmp_path):
    assert "cross_repo" in inspect.signature(get_changed_symbols).parameters
    with pytest.warns(FutureWarning):
        out = get_changed_symbols("o/r", storage_path=str(tmp_path), cross_repo=False)
    assert isinstance(out, dict)


def test_every_parameter_is_read_by_the_body():
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
