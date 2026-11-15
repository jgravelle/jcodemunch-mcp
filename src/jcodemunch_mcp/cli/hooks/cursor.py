"""Cursor hooks: count the agent's reads of indexed code beside our lookups.

``init --cursor-hooks`` registers ``jcodemunch-mcp hook-cursor`` in
``~/.cursor/hooks.json`` for two events (https://cursor.com/docs/hooks):

* ``beforeReadFile`` counts a read of a code file under an indexed source
  root. ⚠⚠ It is a PERMISSION hook: Cursor blocks the read when the reply is
  not valid JSON of the right shape, even with ``failClosed`` off. So every
  path through this handler prints ``{"permission": "allow"}``, including an
  unparseable payload and a counting failure. It never denies.
* ``afterMCPExecution`` counts a finished call to the server named
  ``jcodemunch`` and remembers the hashes of the files its answer named, so a
  later read of one counts as ``reads_after_lookup``.

Nothing reaches the conversation and nothing is sent. A Cursor without these
hooks never runs this command, and a payload without the documented fields
counts nothing.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from ._common import _CODE_EXTENSIONS, _norm_path, _read_hook_payload
from .steering import _indexed_source_roots

logger = logging.getLogger(__name__)

SERVER_NAME = "jcodemunch"
_ALLOW = {"permission": "allow"}


def _relative_to_indexed_root(file_path: str) -> "str | None":
    """The path relative to the indexed root that holds it, or None."""
    if not os.path.isabs(file_path):
        return None
    if os.path.splitext(file_path)[1].lower() not in _CODE_EXTENSIONS:
        return None
    target = _norm_path(file_path)
    best = None
    for root in _indexed_source_roots():
        base = root.rstrip("\\/")
        if target == base or not target.startswith(base + os.sep):
            continue
        if best is None or len(base) > len(best):
            best = base
    if best is None:
        return None
    return os.path.relpath(target, best).replace(os.sep, "/")


def _count(data: dict) -> None:
    from ...storage import direct_reads

    event = data.get("hook_event_name")
    conversation = data.get("conversation_id")
    version = data.get("cursor_version")
    if event == "beforeReadFile":
        file_path = data.get("file_path")
        if not isinstance(file_path, str):
            return
        rel = _relative_to_indexed_root(file_path)
        if rel is None:
            return
        direct_reads.record_read(
            conversation_id=conversation,
            key=direct_reads.path_key(rel),
            cursor_version=version,
        )
    elif event == "afterMCPExecution":
        if data.get("mcp_server_name") != SERVER_NAME:
            return
        direct_reads.record_lookup(
            conversation_id=conversation,
            keys=direct_reads.keys_named_in(data.get("result_json")),
            cursor_version=version,
        )


def run_cursor_hook() -> int:
    try:
        data = _read_hook_payload()
    except Exception:
        logger.debug("cursor hook payload unreadable", exc_info=True)
        data = None
    event = data.get("hook_event_name") if data else None
    try:
        if data:
            _count(data)
    except Exception:
        logger.debug("cursor hook count failed", exc_info=True)
    reply = {} if event == "afterMCPExecution" else _ALLOW
    sys.stdout.write(json.dumps(reply))
    sys.stdout.flush()
    return 0
