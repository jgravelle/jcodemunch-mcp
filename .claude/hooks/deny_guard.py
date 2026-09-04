"""H5: belt to the settings deny list (DESIGN section 4, D8).

purpose:  nothing in a session publishes, tags, merges, force-pushes or posts;
          the refusal names the RUNBOOK section the human runs instead
invokes:  nothing
produces: nothing
refuses:  the verbs below, on Bash and PowerShell
budget:   1 s
"""

from __future__ import annotations

import re

from _common import block, ok, read_hook_input, tool_command

DENIED = [
    (
        r"\bgit\s+push\b[^|;&]*(?:--force|(?-i:\s-f\b)|--force-with-lease)",
        "a force-push; RUNBOOK section 6 is the emergency path",
    ),
    (
        r"\bgit\s+tag\b(?![^|;&]*(?:\s-l\b|--list|--sort|--contains|--points-at|--merged|--no-merged))",
        "a tag; release.yml tags (RUNBOOK section 1)",
    ),
    (
        r"\bgit\s+push\b[^|;&]*\s(?:--tags|v\d)",
        "pushing a tag; release.yml tags (RUNBOOK section 1)",
    ),
    (
        r"\bgh\s+release\b",
        "a GitHub release; release.yml creates it (RUNBOOK section 1)",
    ),
    (
        r"\bgh\s+workflow\s+run\b",
        "a workflow dispatch; the human dispatches (RUNBOOK section 1, step 3)",
    ),
    (
        r"\bgh\s+pr\s+merge\b",
        "a merge; the human merges when the gate is green (RUNBOOK section 1, step 2)",
    ),
    (
        r"\bgh\s+pr\s+(?:comment|review|close|edit|ready)\b",
        "posting to a PR; drafts only in this layer",
    ),
    (
        r"\bgh\s+issue\s+(?:comment|close|edit|reopen|transfer|delete)\b",
        "posting to an issue; drafts only in this layer",
    ),
    (
        r"\bgh\s+api\b.*(?:--method\s+(?:POST|PATCH|PUT|DELETE)|\s-X\s*(?:POST|PATCH|PUT|DELETE))",
        "a write through the API; the human runs it",
    ),
    (r"\btwine\b", "a PyPI upload; RUNBOOK section 1a is the human's hand-finish"),
    (r"mcp-publisher", "a registry publish; release.yml publishes"),
]


def main() -> None:
    cmd = tool_command(read_hook_input())
    if not cmd:
        ok()
    for pattern, why in DENIED:
        if re.search(pattern, cmd, re.I):
            block(
                f"deny_guard: refused {why}. Hand the line to the human in cmd.exe form (docs/workflows/DESIGN.md D8)."
            )
    ok()


if __name__ == "__main__":
    main()
