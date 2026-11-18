"""Two streamable-http sessions that send the same bearer token keep separate state (#1491).

`_session_key()` preferred `session.session_id`, then the hashed Authorization
principal. The MCP library's `ServerSession` carries no `session_id` (1.30.0),
so the first branch never fired, and every client of a server started with
`JCODEMUNCH_HTTP_TOKEN` fell through to the one principal all of them share:
a tier set in one session was the tier of every other.

The older guard (`test_v1_108_150.py`) installed a fake session WITH a
`session_id`, the attribute the real one lacks, so it could not fail on this
(Standing lesson: a mock can supply a contract the producer lacks). This test
runs the real server and the real MCP client, the reporter's reproduction.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import importlib.util

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

# The server subprocess needs the HTTP extras; the unit tests below do not.
_HAS_HTTP = all(
    importlib.util.find_spec(name) is not None for name in ("uvicorn", "starlette")
)

_TOKEN = "test-token-1491"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(
    port: int, proc: subprocess.Popen, log: Path, deadline_s: float = 60.0
) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(
                f"server exited early with {proc.returncode}: "
                + log.read_text(encoding="utf-8", errors="replace")[-2000:]
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start listening")


@pytest.fixture(params=[True, False], ids=["token", "no-token"])
def http_server(request, tmp_path: Path):
    """The real server; with a token every client sends the same principal."""
    port = _free_port()
    env = dict(os.environ)
    # Nothing from the developer's or runner's environment may set the tier
    # or the token: B's default must be the configured `full`.
    for name in [n for n in env if n.startswith("JCODEMUNCH_")]:
        env.pop(name)
    if request.param:
        env["JCODEMUNCH_HTTP_TOKEN"] = _TOKEN
    env["CODE_INDEX_PATH"] = str(tmp_path / "index")
    src = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    log = tmp_path / "server.log"
    log_fh = open(log, "w", encoding="utf-8")
    # `-m jcodemunch_mcp`, the package entry point, not `-m jcodemunch_mcp.server`:
    # the latter runs server.py as `__main__`, a second copy of the module.
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "jcodemunch_mcp", "serve",
            "--transport", "streamable-http",
            "--host", "127.0.0.1", "--port", str(port),
            "--watcher=false",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
    )
    try:
        _wait_listening(port, proc, log)
        headers = {"Authorization": f"Bearer {_TOKEN}"} if request.param else {}
        yield f"http://127.0.0.1:{port}/mcp", headers
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fh.close()


async def _tier_calls(url: str, headers: dict[str, str]) -> list[tuple[str, dict]]:
    """The issue's steps 3-4: two concurrent sessions, the same headers on both."""
    log: list[tuple[str, dict]] = []

    async def call(session: ClientSession, name: str, tier: str) -> None:
        result = await session.call_tool("set_tool_tier", {"tier": tier})
        assert not result.isError, result
        data = result.structuredContent
        if data is None:
            data = json.loads(result.content[0].text)
        log.append((name, data))

    async with create_mcp_http_client(headers=headers) as ca, streamable_http_client(
        url, http_client=ca
    ) as (ra, wa, sid_a):
        async with ClientSession(ra, wa) as a:
            await a.initialize()
            async with create_mcp_http_client(headers=headers) as cb, streamable_http_client(
                url, http_client=cb
            ) as (rb, wb, sid_b):
                async with ClientSession(rb, wb) as b:
                    await b.initialize()
                    assert sid_a() and sid_b() and sid_a() != sid_b()
                    await call(a, "A", "core")
                    await call(b, "B", "full")
    return log


@pytest.mark.skipif(not _HAS_HTTP, reason="uvicorn and starlette are not installed")
def test_a_tier_set_in_one_session_is_not_the_tier_of_another(http_server):
    url, headers = http_server
    # A stalled server would otherwise hold each call for the client's read timeout.
    log = asyncio.run(asyncio.wait_for(_tier_calls(url, headers), timeout=120))
    (_, a), (_, b) = log
    assert a.get("changed") is True, a
    # B never set a tier, so it is still on the default and asking for it
    # changes nothing. Shared state reports `changed: true, from: core`.
    assert b.get("changed") is False, b


# The key and its cleanup, as units. The end-to-end test above cannot see what
# the server process keeps after a session ends.

from jcodemunch_mcp import server as server_mod  # noqa: E402


class _SessionWithoutId:
    """The real `ServerSession` shape: no `session_id` attribute."""


class _Ctx:
    def __init__(self, session):
        self.session = session


class _Srv:
    def __init__(self, session):
        self.request_context = _Ctx(session)


def test_the_transport_id_wins_over_the_principal(monkeypatch):
    monkeypatch.setattr(server_mod, "server", _Srv(_SessionWithoutId()))
    p = server_mod._HTTP_PRINCIPAL.set("principal-shared")
    s = server_mod._HTTP_TRANSPORT_SESSION.set("transport-a")
    try:
        assert server_mod._session_key() == "transport-a"
    finally:
        server_mod._HTTP_TRANSPORT_SESSION.reset(s)
        server_mod._HTTP_PRINCIPAL.reset(p)


def test_a_transport_that_issues_no_id_keys_by_the_principal(monkeypatch):
    monkeypatch.setattr(server_mod, "server", _Srv(_SessionWithoutId()))
    p = server_mod._HTTP_PRINCIPAL.set("principal-shared")
    s = server_mod._HTTP_TRANSPORT_SESSION.set(None)
    try:
        assert server_mod._session_key() == "principal-shared"
    finally:
        server_mod._HTTP_TRANSPORT_SESSION.reset(s)
        server_mod._HTTP_PRINCIPAL.reset(p)


def test_an_ended_session_leaves_no_tier_override(monkeypatch):
    monkeypatch.setattr(server_mod, "server", _Srv(_SessionWithoutId()))
    s = server_mod._HTTP_TRANSPORT_SESSION.set("transport-ended")
    try:
        server_mod._set_session_tier("core")
        assert server_mod._session_tier_overrides.get("transport-ended") == "core"
        server_mod._forget_http_session("transport-ended")
        assert "transport-ended" not in server_mod._session_tier_overrides
    finally:
        server_mod._HTTP_TRANSPORT_SESSION.reset(s)
        server_mod._reset_session_tiers()


def test_both_ways_a_session_ends_forget_it():
    """The idle sweep (`_drop_session`) and the runner's exit both call the cleanup."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(server_mod.run_streamable_http_server)))
    callers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_forget_http_session"
                ):
                    callers.add(fn.name)
    assert {"_drop_session", "_session_runner"} <= callers, callers
