"""Cursor hooks: count the agent's reads of indexed code beside our lookups.

``init --cursor-hooks`` registers ``jcodemunch-mcp hook-cursor`` in
``~/.cursor/hooks.json`` for two events (https://cursor.com/docs/hooks):

* ``beforeReadFile`` counts a read of a code file under an indexed source
  root, by the same path rule the Read steering uses (``_path_overlaps``, so a
  separate checkout nested in an indexed one is not counted). ⚠⚠ It is a
  PERMISSION hook: Cursor blocks the read when the reply is not valid JSON of
  the right shape, even with ``failClosed`` off. So every path through this
  handler prints ``{"permission": "allow"}``, including an unparseable payload
  and a count that failed. It never denies.
* ``afterMCPExecution`` counts a finished call to the server named
  ``jcodemunch``, the name ``init`` writes into Cursor's ``mcp.json``.

Nothing reaches the conversation and nothing is sent. A Cursor without these
hooks never runs this command; a payload without the documented fields counts
nothing.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from ._common import _CODE_EXTENSIONS, _norm_path, _path_overlaps, _read_hook_payload
from .steering import _indexed_source_roots

logger = logging.getLogger(__name__)

SERVER_NAME = "jcodemunch"


def _is_indexed_code(file_path) -> bool:
    if not isinstance(file_path, str) or not os.path.isabs(file_path):
        return False
    if os.path.splitext(file_path)[1].lower() not in _CODE_EXTENSIONS:
        return False
    roots = _indexed_source_roots()
    return bool(roots) and _path_overlaps(_norm_path(file_path), roots)


def _count(data: dict) -> None:
    from ...storage import direct_reads

    event = data.get("hook_event_name")
    version = data.get("cursor_version")
    if event == "beforeReadFile" and _is_indexed_code(data.get("file_path")):
        direct_reads.record_read(cursor_version=version)
    elif event == "afterMCPExecution" and data.get("mcp_server_name") == SERVER_NAME:
        direct_reads.record_lookup(cursor_version=version)


def run_cursor_hook() -> int:
    event = None
    try:
        data = _read_hook_payload()
        if data:
            event = data.get("hook_event_name")
            _count(data)
    except Exception:
        logger.debug("cursor hook count failed", exc_info=True)
    reply = {} if event == "afterMCPExecution" else {"permission": "allow"}
    sys.stdout.write(json.dumps(reply))
    sys.stdout.flush()
    return 0
