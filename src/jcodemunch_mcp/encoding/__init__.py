"""Compact response encoding — the MUNCH format.

Dispatcher entry point. Given a tool name and a response dict, returns
either the original dict (JSON passthrough) or a MUNCH payload string
together with the encoding id and a savings measurement.

Usage from server.py:

    from .encoding import encode_response

    payload, meta = encode_response(tool_name, result, requested_format)
    if meta["encoding"] == "json":
        text = json.dumps(payload, separators=(",", ":"))
    else:
        text = payload

`requested_format` supports both canonical and user-facing aliases:
- canonical: "auto", "compact", "json"
- aliases: "adaptive", "encoded", "raw"
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import decoder, gate, generic
from . import schema_driven as _sd
from .schemas import registry

logger = logging.getLogger(__name__)

_FORMATS = ("auto", "compact", "json")
_FORMAT_ALIASES = {
    "auto": "auto",
    "compact": "compact",
    "json": "json",
    "adaptive": "auto",
    "encoded": "compact",
    "raw": "json",
}


def _normalize_format(raw: str | None, fallback: str) -> str:
    if not isinstance(raw, str):
        return fallback
    return _FORMAT_ALIASES.get(raw.strip().lower(), fallback)


def default_format(repo: str | None = None) -> str:
    try:
        from .. import config as app_config

        configured = app_config.get("server_output", "adaptive", repo=repo)
    except Exception:
        configured = "adaptive"
    return _normalize_format(configured, "auto")


# A value with nothing in it. An encoder may write it as a blank cell or leave
# an optional column out (`signature_truncated: False`), so its key is not
# required back; every other key is.
_EMPTY = (None, "", False)


def _keys_kept(orig: Any, back: Any, dropped: frozenset[str] = frozenset()) -> bool:
    """True when every key ``orig`` holds a value under comes back, at every depth.

    (LEDGER L-340) The top-level check of L-341 could not see a row column a
    schema did not name, nor a key a schema's own reshaping step (a
    hand-written flatten) left out. Keys only, never values: a table decodes
    typed, so a value is not byte-equal to its source. A list must come back
    the same length, item for item. A `_`-prefixed key is metadata or a row
    verdict whose default an encoder omits by design (L-325), so it is not
    required. Neither is a key the schema drops by name (`TableSpec.drop`),
    the one deliberate drop.
    """
    if isinstance(orig, dict):
        if not isinstance(back, dict):
            return False
        for k, v in orig.items():
            if not isinstance(k, str) or k.startswith("_") or k in dropped:
                continue
            if v in _EMPTY or v == [] or v == {}:
                continue
            if k not in back or not _keys_kept(v, back[k], dropped):
                return False
        return True
    if isinstance(orig, list):
        if not isinstance(back, list) or len(back) != len(orig):
            return False
        return all(_keys_kept(o, b, dropped) for o, b in zip(orig, back))
    return True


def _declared_drops(encoder: Any) -> frozenset[str]:
    """The row keys a schema leaves out on purpose (`TableSpec.drop`)."""
    return frozenset(
        name
        for spec in getattr(encoder, "_TABLES", None) or ()
        for name in getattr(spec, "drop", None) or ()
    )


def encode_response(
    tool_name: str,
    response: Any,
    requested_format: str | None = None,
    repo: str | None = None,
) -> tuple[Any, dict]:
    """Return (payload, meta).

    payload is either the original dict (for json path) or a MUNCH string.
    meta is a dict with keys: encoding, json_bytes, encoded_bytes,
    encoding_tokens_saved.
    """
    fmt = _normalize_format(requested_format, default_format(repo=repo))

    if fmt == "json" or not isinstance(response, dict):
        return response, {"encoding": "json"}

    # Error/degenerate dicts match no tool schema: their keys aren't in any
    # table/scalar/meta list, so schema-encoding silently drops the "error"
    # message into an empty-table envelope and the agent sees a blank "0
    # results" it can't distinguish from genuine emptiness. Always pass these
    # through as JSON so the error survives the wire.
    if "error" in response:
        return response, {"encoding": "json", "json_bytes": gate.json_size(response)}

    json_bytes = gate.json_size(response)

    try:
        encoder = registry.for_tool(tool_name)
        if encoder is not None:
            payload, enc_id = encoder.encode(tool_name, response)
        else:
            payload, enc_id = generic.encode(tool_name, response)
    except Exception:
        logger.debug("Encoder failed for %s; falling back to JSON", tool_name, exc_info=True)
        return response, {"encoding": "json", "json_bytes": json_bytes}

    # A warning or `negative_evidence` must survive the wire whatever a schema
    # does to the response first, and so must every other body key (LEDGER
    # L-341). The schema encoder carries them, but a schema that reshapes the
    # response before encoding (a batch flatten) can lose one, and the next
    # such schema would too. A warning must come back EQUAL; any other key
    # must come back PRESENT, since a table decodes typed and is not
    # byte-equal to its source. Either miss ships JSON.
    contract = {
        k: v for k, v in response.items()
        if not k.startswith("_") and _sd.is_universal_body_key(k)
    }
    body_keys = [k for k in response if not k.startswith("_")]
    if body_keys:
        try:
            back = decoder.decode(payload)
            kept = all(back.get(k) == v for k, v in contract.items()) and all(
                k in back for k in body_keys
            ) and _keys_kept(response, back, _declared_drops(encoder))
        except Exception:
            logger.debug("Decode check failed for %s", tool_name, exc_info=True)
            kept = False
        if not kept:
            logger.debug("Encoder for %s dropped a body key; falling back to JSON", tool_name)
            return response, {"encoding": "json", "json_bytes": json_bytes}

    encoded_bytes = len(payload)
    if fmt == "auto" and not gate.passes(json_bytes, encoded_bytes, repo=repo):
        return response, {
            "encoding": "json",
            "json_bytes": json_bytes,
            "encoded_bytes": encoded_bytes,
        }

    return payload, {
        "encoding": enc_id,
        "json_bytes": json_bytes,
        "encoded_bytes": encoded_bytes,
        "encoding_tokens_saved": max(0, (json_bytes - encoded_bytes) // 4),
    }


__all__ = ["encode_response", "default_format"]
