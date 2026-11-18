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

import pytest

pytest.importorskip("uvicorn")
pytest.importorskip("starlette")

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: E402

_TOKEN = "test-token-1491"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(port: int, proc: subprocess.Popen, deadline_s: float = 60.0) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with {proc.returncode}")
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
    env.pop("JCODEMUNCH_HTTP_TOKEN", None)
    if request.param:
        env["JCODEMUNCH_HTTP_TOKEN"] = _TOKEN
    env["CODE_INDEX_PATH"] = str(tmp_path / "index")
    src = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "jcodemunch_mcp.server", "serve",
            "--transport", "streamable-http",
            "--host", "127.0.0.1", "--port", str(port),
            "--watcher=false",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_listening(port, proc)
        headers = {"Authorization": f"Bearer {_TOKEN}"} if request.param else {}
        yield f"http://127.0.0.1:{port}/mcp", headers
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


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


def test_a_tier_set_in_one_session_is_not_the_tier_of_another(http_server):
    url, headers = http_server
    log = asyncio.run(_tier_calls(url, headers))
    (_, a), (_, b) = log
    assert a.get("changed") is True, a
    # B never set a tier, so it is still on the default and asking for it
    # changes nothing. Shared state reports `changed: true, from: core`.
    assert b.get("changed") is False, b
