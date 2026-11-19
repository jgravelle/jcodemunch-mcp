"""The per-request MCP context, reachable from deep inside a tool call.

mcp 1.x exposed the active request through `Server.request_context`, a
contextvar-backed property the SDK set around each dispatch. 2.x removed it:
the handler receives a `ServerRequestContext` and nothing else publishes it.
`server.py`'s adapters put that object here for the duration of the call, so
helpers that are not handed the context (progress notifications, session
lookups) read the same value under both SDK majors.

Kept in its own module so `progress.py` can import it without importing
`server.py`, which imports `progress.py`.
"""

from __future__ import annotations

import contextvars
from typing import Any, Optional

REQUEST_CTX: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "jcodemunch_mcp_request_ctx", default=None
)


def current() -> Optional[Any]:
    """The context of the request being served, or None outside one."""
    return REQUEST_CTX.get()


def resolve(server_obj: Any) -> Optional[Any]:
    """The current request context, preferring the one this process published.

    Falls back to the 1.x `server.request_context` property, which tests and
    embedders set directly.
    """
    ctx = REQUEST_CTX.get()
    if ctx is not None:
        return ctx
    try:
        return server_obj.request_context
    except (LookupError, AttributeError):
        return None
