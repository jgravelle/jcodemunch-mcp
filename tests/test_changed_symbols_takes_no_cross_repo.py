"""`get_changed_symbols` takes no `cross_repo` argument (LEDGER L-57).

The parameter was in the signature and nothing read it: the MCP schema did not
offer it, the dispatcher did not pass it, and the embedded blast walk never saw
it. A Python caller who set it got a local-only blast with no sign the flag did
nothing. Removed, so the same call now fails loudly instead of being ignored.
"""

from __future__ import annotations

import inspect

import pytest

from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols


def test_the_signature_has_no_cross_repo():
    assert "cross_repo" not in inspect.signature(get_changed_symbols).parameters


def test_passing_cross_repo_is_an_error_not_a_silent_no_op():
    with pytest.raises(TypeError):
        get_changed_symbols("o/r", cross_repo=True)  # type: ignore[call-arg]


def test_every_remaining_parameter_is_read_by_the_body():
    """A parameter in the signature that the body never names does nothing."""
    src = inspect.getsource(get_changed_symbols)
    body = src[src.index('"""', src.index("def get_changed_symbols")) :]
    body = body[body.index('"""', 3) + 3 :]
    unread = [
        p for p in inspect.signature(get_changed_symbols).parameters
        if p not in body
    ]
    assert unread == []
