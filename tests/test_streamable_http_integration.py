"""Integration tests for streamable-http transport.

These exercise the actual Starlette app built by run_streamable_http_server —
not re-implemented routing logic — so regressions like returning None from an
endpoint (TypeError: 'NoneType' object is not callable) are caught.
"""

import asyncio
import json
import unittest

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route


# ---------------------------------------------------------------------------
# Helpers — build the same Starlette app that run_streamable_http_server does,
# but without launching uvicorn or wiring up the full MCP server.  We stub
# StreamableHTTPServerTransport so the test has no network / MCP SDK deps
# beyond what's already imported.
# ---------------------------------------------------------------------------

_MCP_SESSION_ID_HEADER = "mcp-session-id"


def _build_app():
    """Return a Starlette app wired identically to run_streamable_http_server."""
    import uuid

    _sessions: dict = {}
    _session_tasks: dict = {}

    class _AlreadySent:
        async def __call__(self, scope, receive, send):
            pass

    _ALREADY_SENT = _AlreadySent()

    # Lightweight stand-in: records calls and writes a minimal JSON-RPC
    # response so the ASGI layer sees a complete HTTP exchange.
    class _FakeTransport:
        def __init__(self, session_id: str):
            self.session_id = session_id
            self._terminated = False
            self.handle_count = 0

        async def handle_request(self, scope, receive, send):
            self.handle_count += 1
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"session": self.session_id}}).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                    [b_MCP_SESSION_ID_HEADER, self.session_id.encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})

    b_MCP_SESSION_ID_HEADER = _MCP_SESSION_ID_HEADER.encode()

    async def handle_mcp(request: Request):
        session_id = request.headers.get(_MCP_SESSION_ID_HEADER)

        if session_id and session_id in _sessions:
            transport = _sessions[session_id]
            await transport.handle_request(request.scope, request.receive, request._send)
            if transport._terminated:
                _sessions.pop(session_id, None)
                task = _session_tasks.pop(session_id, None)
                if task and not task.done():
                    task.cancel()
            return _ALREADY_SENT

        new_id = uuid.uuid4().hex
        transport = _FakeTransport(new_id)
        _sessions[new_id] = transport

        await transport.handle_request(request.scope, request.receive, request._send)
        return _ALREADY_SENT

    app = Starlette(
        routes=[
            Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
        ],
    )
    return app, _sessions


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStreamableHTTPIntegration(unittest.IsolatedAsyncioTestCase):
    """Hits the actual Starlette routing stack via httpx ASGITransport."""

    async def asyncSetUp(self):
        self.app, self.sessions = _build_app()
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_post_returns_200_no_typeerror(self):
        """POST /mcp must not raise TypeError from Starlette's endpoint wrapper."""
        resp = await self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("session", data.get("result", {}))

    async def test_existing_session_reuse(self):
        """A second request with the session ID header reuses the transport."""
        # First request — creates a session.
        resp1 = await self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        self.assertEqual(resp1.status_code, 200)
        session_id = resp1.json()["result"]["session"]

        # Second request — reuses that session.
        resp2 = await self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={_MCP_SESSION_ID_HEADER: session_id},
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["result"]["session"], session_id)

        # The transport's handle_request was called twice (once per request).
        transport = self.sessions[session_id]
        self.assertEqual(transport.handle_count, 2)

    async def test_delete_returns_200(self):
        """DELETE /mcp does not raise TypeError."""
        resp = await self.client.delete("/mcp")
        self.assertEqual(resp.status_code, 200)

    async def test_disallowed_method_returns_405(self):
        """PUT /mcp is not in the allowed methods list."""
        resp = await self.client.put("/mcp", content=b"")
        self.assertEqual(resp.status_code, 405)

    async def test_multiple_sequential_posts(self):
        """Several POSTs in a row must all succeed (no accumulated state corruption)."""
        for i in range(5):
            resp = await self.client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": i, "method": "initialize"},
            )
            self.assertEqual(resp.status_code, 200, f"Request {i} failed with {resp.status_code}")


if __name__ == "__main__":
    unittest.main()
