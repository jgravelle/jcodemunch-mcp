"""The startup note says the repo IS indexed when the server can see that it is.

The MCP `instructions` string is often the only jcodemunch text an agent reads
(a client that defers tools sends names and this string, nothing else). It
opened with "This repo can be indexed", which reads as work still to do even
when an index is already there. On the stdio transport the server's working
directory is the user's project, so at startup it asks `resolve_repo` whether
that directory is indexed and, if so, says "This repo is indexed by jcodemunch
(N files)". The file count lets the agent judge "small repository" for itself.

Anything it cannot establish keeps the old wording: not indexed, a lookup
error, or a lookup that takes longer than its time limit (`resolve_repo` has a
documented hang, #303, and startup must not wait on it). SSE and
streamable-http keep the old wording too, because there the server's working
directory is not the caller's.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

from jcodemunch_mcp import server


@pytest.mark.parametrize("surface", ["counter", "full"])
def test_an_indexed_repo_is_named_as_indexed_with_its_size(surface):
    text = server._mcp_instructions(surface, indexed_files=1406)
    assert "This repo is indexed by jcodemunch (1,406 files)" in text
    assert "can be indexed" not in text
    assert len(text) < server._MCP_INSTRUCTIONS_MAX_CHARS


@pytest.mark.parametrize("surface", ["counter", "full"])
def test_without_a_count_the_wording_is_unchanged(surface):
    assert server._mcp_instructions(surface, indexed_files=None) == server._mcp_instructions(surface)
    assert "This repo can be indexed by jcodemunch" in server._mcp_instructions(surface)


@pytest.mark.parametrize("surface", ["counter", "full"])
def test_the_small_repository_guidance_stays(surface):
    """The 2026-10-14 wording on small repositories is not changed here."""
    for count in (None, 12, 50_000):
        assert "On a small repository" in server._mcp_instructions(surface, indexed_files=count)


def test_one_file_is_singular():
    assert "(1 file)" in server._mcp_instructions("counter", indexed_files=1)


def _fake_resolve(result=None, exc=None, delay=0.0):
    def fake(path, storage_path=None):
        if delay:
            time.sleep(delay)
        if exc:
            raise exc
        return result

    return fake


def test_indexed_here_returns_the_file_count(monkeypatch):
    seen = {}

    def fake(path, storage_path=None):
        seen["path"] = path
        return {"indexed": True, "file_count": 2950}

    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", fake)
    assert server._indexed_here() == 2950
    assert Path(seen["path"]).is_absolute()


@pytest.mark.parametrize(
    "result",
    [
        {"indexed": False},
        {"indexed": True},
        {"indexed": True, "file_count": "many"},
        {"indexed": True, "file_count": -1},
        None,
    ],
)
def test_indexed_here_is_none_when_it_cannot_tell(monkeypatch, result):
    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", _fake_resolve(result))
    assert server._indexed_here() is None


def test_indexed_here_is_none_on_an_error(monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", _fake_resolve(exc=OSError("boom")))
    assert server._indexed_here() is None


def test_indexed_here_does_not_wait_past_its_limit(monkeypatch):
    release = threading.Event()

    def stuck(path, storage_path=None):
        release.wait(5)
        return {"indexed": True, "file_count": 10}

    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", stuck)
    started = time.monotonic()
    try:
        assert server._indexed_here(timeout=0.2) is None
        assert time.monotonic() - started < 2.0
    finally:
        release.set()


def _init_option_calls() -> list[ast.Call]:
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_initialization_options"
    ]


def test_only_the_stdio_transport_passes_a_count():
    calls = _init_option_calls()
    assert len(calls) == 3
    with_count = [c for c in calls if c.keywords and any(k.arg == "indexed_files" for k in c.keywords)]
    assert len(with_count) == 1
    src = Path(server.__file__).read_text(encoding="utf-8").splitlines()
    window = "\n".join(src[max(0, with_count[0].lineno - 30): with_count[0].lineno])
    assert "stdio_server(" in window
