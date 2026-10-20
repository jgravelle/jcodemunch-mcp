"""`cli.policy._effective_tool_surface` reads the surface the way the server does.

`init` and the policy writer choose between the counter policy and the
direct-tool policy with this reader. It returned the raw env value, so a
whitespace-only `JCODEMUNCH_TOOL_SURFACE` read as "" (neither surface) while
the server and the hooks, which go through `counter.resolve_tool_surface`,
treated it as unset and served the configured `counter`. An unrecognized value
(from env or config) passed through as itself, where the server serves "full".
Mixed case and padding were already normalised and are pinned here as a
control, not as a former failure. Found in review of the counter-surface
steering fix (2026-10-20), as the same non-validating reader one layer over.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import config
from jcodemunch_mcp.cli import policy
from jcodemunch_mcp.counter import resolve_tool_surface


def _with_config(monkeypatch, value):
    real = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: value if k == "tool_surface" else real(k, *a, **kw))


@pytest.mark.parametrize(
    "env, configured",
    [
        ("   ", "counter"),     # blank env is unset: config decides
        ("", "counter"),
        (None, "counter"),
        ("Counter ", "full"),   # env wins; already normalised before the fix (control)
        ("nonsense", "counter"),  # unknown serves full
        (None, "nonsense"),
        (None, None),
    ],
)
def test_the_policy_reader_agrees_with_the_server_authority(monkeypatch, env, configured):
    if env is None:
        monkeypatch.delenv("JCODEMUNCH_TOOL_SURFACE", raising=False)
    else:
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", env)
    _with_config(monkeypatch, configured)
    expected = resolve_tool_surface(env, configured)[0]
    assert policy._effective_tool_surface() == expected
    assert expected in ("full", "counter")


def test_a_failing_config_read_serves_full(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_TOOL_SURFACE", raising=False)

    def boom(*a, **k):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(config, "get", boom)
    assert policy._effective_tool_surface() == "full"
