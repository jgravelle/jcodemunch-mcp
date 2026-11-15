"""`jcodemunch-mcp direct-reads`: the Cursor direct-read counts, as text or JSON.

Reads ``_direct_reads.json`` under the index store (written by ``hook-cursor``)
and prints it without a model in the loop, so the numbers can be pasted into a
ticket. Counts only; no token or savings figure.
"""

from __future__ import annotations

import json


def _line(label: str, c: dict) -> str:
    return (
        f"  {label}: {c['direct_reads']} direct reads of indexed code against "
        f"{c['lookups']} lookups ({c['reads_after_lookup']} of the reads were of a "
        f"file a lookup had already named)"
    )


def run_direct_reads(*, as_json: bool = False, storage_path=None) -> int:
    from ..storage import direct_reads

    s = direct_reads.summary(storage_path)
    if as_json:
        print(json.dumps(s if s is not None else {}))
        return 0
    if s is None:
        print(
            "No direct-read count yet. Run `jcodemunch-mcp init --cursor-hooks`, "
            "restart Cursor, and use its agent."
        )
        return 0
    version = f", Cursor {s['cursor_version']}" if s.get("cursor_version") else ""
    print(f"Cursor agent (IDE{version}), counted by the jCodeMunch hook:")
    print(_line("Today", s["today"]))
    print(_line("Last 7 days", s["last_7_days"]))
    print(_line("All time", s["all_time"]))
    print(
        "This is a floor: a read the hook did not see (the Cursor CLI, a cloud "
        "agent, a hook that timed out) is not counted."
    )
    return 0
