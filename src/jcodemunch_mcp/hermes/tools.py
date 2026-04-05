"""Tool discovery and handler generation for the Hermes plugin.

Rather than hand-copying ~49 tool schemas (which would drift every
time jcodemunch ships a new release), we discover them at registration
time by calling :func:`jcodemunch_mcp.server.list_tools` and converting
each returned :class:`mcp.types.Tool` into a Hermes tool spec.

Every tool is exposed to the model with a ``jcm_`` prefix so it
doesn't collide with other code-search tools. When the model calls
``jcm_search_symbols`` the plugin dispatches to jcodemunch's
``search_symbols`` under the hood.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Tuple

from ._bridge import run_async

logger = logging.getLogger(__name__)

# Prefix applied to every jcodemunch tool name before exposing it to
# the LLM. Keeps the namespace clean when mixed with other Hermes tools
# and lets hooks cheaply detect "is this one of ours?" via string prefix.
TOOL_PREFIX = "jcm_"

# Per-call timeout. Indexing a large repo can legitimately take a while,
# so we give tools 10 minutes by default. Individual jcodemunch calls
# enforce their own freshness / strict-mode timeouts internally.
DEFAULT_TIMEOUT_SEC = 600.0


def prefixed_name(original_name: str) -> str:
    """Return the Hermes-facing name for a jcodemunch tool."""
    return TOOL_PREFIX + original_name


def strip_prefix(hermes_name: str) -> str:
    """Inverse of :func:`prefixed_name`. Returns the name unchanged if
    the prefix isn't present."""
    if hermes_name.startswith(TOOL_PREFIX):
        return hermes_name[len(TOOL_PREFIX):]
    return hermes_name


# --------------------------------------------------------------------------- #
# Tool discovery                                                              #
# --------------------------------------------------------------------------- #


def _tool_to_hermes_schema(tool: Any) -> Dict[str, Any]:
    """Convert an ``mcp.types.Tool`` into a Hermes tool schema dict.

    MCP Tool:       ``{ name, description, inputSchema }``
    Hermes schema:  ``{ name, description, parameters }``

    The JSON Schema under ``inputSchema`` is valid as-is for
    ``parameters``.
    """
    # Tool is a pydantic model in recent MCP SDKs; attribute access
    # works and is more robust than dict lookup across SDK versions.
    name = getattr(tool, "name", None) or tool["name"]  # type: ignore[index]
    description = (
        getattr(tool, "description", None)
        or (tool["description"] if isinstance(tool, dict) else "")  # type: ignore[index]
        or ""
    )
    input_schema = (
        getattr(tool, "inputSchema", None)
        or (tool["inputSchema"] if isinstance(tool, dict) else None)  # type: ignore[index]
        or {"type": "object", "properties": {}}
    )

    return {
        "name": prefixed_name(name),
        "description": description,
        "parameters": input_schema,
        # Stashed so the handler closure below knows what name to
        # dispatch to. Stripped before handing the schema to Hermes.
        "_jcm_original_name": name,
    }


def discover_tools() -> List[Dict[str, Any]]:
    """Return a list of Hermes tool schemas for every jcodemunch tool.

    Each entry is a dict with ``name``, ``description``, ``parameters``,
    and an internal ``_jcm_original_name`` that the handler closure
    captures. Returns an empty list (and logs) if jcodemunch's tool
    listing can't be fetched for any reason.
    """
    try:
        # Import lazily so importing this subpackage never triggers the
        # full server.py import chain unless a caller actually asks for
        # tools. server.py pulls in heavy deps (tree_sitter, jsonschema).
        from ..server import list_tools as _list_tools
    except Exception as exc:  # pragma: no cover — install integrity check
        logger.exception(
            "jcodemunch hermes plugin: failed to import server.list_tools: %s",
            exc,
        )
        return []

    try:
        tools = run_async(_list_tools(), timeout=30.0)
    except Exception as exc:
        logger.exception(
            "jcodemunch hermes plugin: list_tools() failed: %s", exc
        )
        return []

    schemas: List[Dict[str, Any]] = []
    for tool in tools:
        try:
            schemas.append(_tool_to_hermes_schema(tool))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "jcodemunch hermes plugin: skipping malformed tool %r: %s",
                tool,
                exc,
            )
    return schemas


# --------------------------------------------------------------------------- #
# Handler factory                                                             #
# --------------------------------------------------------------------------- #


def _extract_text(result: Any) -> str:
    """Collapse an MCP ``list[TextContent]`` into a single string.

    jcodemunch tools always return JSON-encoded text content, so
    joining ``.text`` fields gives us a parsable blob. If jcodemunch
    ever returns non-text content (images, blobs), we fall back to
    ``repr()`` so we never raise from inside a handler.
    """
    if result is None:
        return json.dumps({"error": "tool returned None"})

    if isinstance(result, list):
        parts: List[str] = []
        for item in result:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(repr(item))
        return "\n".join(parts) if parts else json.dumps({"result": None})

    text = getattr(result, "text", None)
    if text is not None:
        return text

    try:
        return json.dumps(result)
    except Exception:
        return repr(result)


def make_handler(original_name: str) -> Callable[..., str]:
    """Build a sync Hermes handler that dispatches to jcodemunch's
    async ``call_tool(original_name, args)``.

    Handler contract (per the Hermes plugin docs):

    * Signature: ``(args: dict, **kwargs) -> str``
    * Returns: JSON string on success AND on error
    * Never raises
    """

    def handler(args: Dict[str, Any], **kwargs: Any) -> str:
        try:
            # Lazy import for the same reason as discover_tools(): keep
            # the cold start cheap and avoid pulling tree_sitter into
            # memory unless a tool is actually invoked.
            from ..server import call_tool as _call_tool

            coro = _call_tool(original_name, args or {})
            result = run_async(coro, timeout=DEFAULT_TIMEOUT_SEC)
            return _extract_text(result)
        except Exception as exc:
            logger.exception(
                "jcodemunch hermes plugin: %s raised: %s", original_name, exc
            )
            return json.dumps(
                {
                    "error": f"{original_name} failed: {exc}",
                    "type": type(exc).__name__,
                }
            )

    handler.__name__ = f"jcm_{original_name}_handler"
    handler.__qualname__ = handler.__name__
    return handler


def build_tool_entries() -> List[Tuple[Dict[str, Any], Callable[..., str]]]:
    """Combine discovery + handler factory.

    Returns a list of ``(schema, handler)`` tuples ready to be fed into
    ``ctx.register_tool(...)``. The internal ``_jcm_original_name`` key
    is stripped from each schema before returning so it doesn't leak
    into the Hermes registry.
    """
    entries: List[Tuple[Dict[str, Any], Callable[..., str]]] = []
    for schema in discover_tools():
        original = schema.pop("_jcm_original_name")
        entries.append((schema, make_handler(original)))
    return entries


def call_jcm_tool(original_name: str, args: Dict[str, Any]) -> str:
    """Public helper for hooks that need to invoke a jcodemunch tool
    directly (e.g., the index hook re-indexing after an edit).

    Returns the tool's text response, or a JSON error string. Never
    raises — matches the handler contract so hooks stay observer-safe.
    """
    return make_handler(original_name)(args)
