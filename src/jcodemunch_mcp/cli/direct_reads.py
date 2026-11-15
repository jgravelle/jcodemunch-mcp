"""`jcodemunch-mcp direct-reads`: the Cursor direct-read counts, as text or JSON.

Reads ``_direct_reads.json`` under the index store (written by ``hook-cursor``)
and prints it without a model in the loop. Counts only; no token or savings
figure.
"""

from __future__ import annotations

import json


def run_direct_reads(*, as_json: bool = False, storage_path=None) -> int:
    from ..storage import direct_reads

    s = direct_reads.summary(storage_path)
    if as_json:
        print(json.dumps(s if s is not None else {}))
        return 1 if s and "unreadable" in s else 0
    if s is None:
        print(
            "No direct-read count yet. Run `jcodemunch-mcp init --cursor-hooks`, restart Cursor, and use its agent."
        )
        return 0
    if "unreadable" in s:
        print(f"Cursor direct-read counts: {s['unreadable']}.")
        return 1
    version = f", Cursor {s['cursor_version']}" if s.get("cursor_version") else ""
    print(f"Cursor agent (IDE{version}), counted by the jCodeMunch hook:")
    for label, key in (
        ("Today", "today"),
        ("Last 7 days", "last_7_days"),
        ("All time", "all_time"),
    ):
        c = s[key]
        print(
            f"  {label}: {c['direct_reads']} direct reads of indexed code against {c['lookups']} jCodeMunch lookups"
        )
    print(
        "This is a floor: a read the hook did not see (the Cursor CLI, a cloud agent, "
        "a hook that timed out) is not counted."
    )
    return 0
