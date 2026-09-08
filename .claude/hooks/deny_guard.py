"""H5: belt to the settings deny list (DESIGN section 4, D8).

purpose:  nothing in a session does the IRREVERSIBLE: publish, tag, dispatch a
          release, merge, force-push; the refusal names the RUNBOOK section
          the human runs instead. Posting (a PR, a comment, a review, an
          edit, an alert dismissal) is the session's: every one of those can
          be undone from the page (W-40, jjg 2026-09-07)
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
        r"\bgh\s+issue\s+delete\b",
        "deleting an issue, which no page undoes; the human does it",
    ),
    (
        # An API write is the session's (W-40) unless its path is one of the
        # irreversible acts the verbs above already refuse: a merge, a release,
        # a workflow dispatch, a tag ref, a deletion.
        r"\bgh\s+api\b(?=.*(?:--method\s+(?:POST|PATCH|PUT|DELETE)|\s-X\s*(?:POST|PATCH|PUT|DELETE)))"
        r".*(?:/pulls/\d+/merge\b|/releases\b|/dispatches\b|/git/tags\b|refs/tags/|--method\s+DELETE|\s-X\s*DELETE)",
        "an irreversible write through the API (merge, release, dispatch, tag or delete); the human runs it",
    ),
    (
        # W-41: the rule above is gated on --method/-X, and a GraphQL mutation
        # carries neither. The same acts by the mutation names GraphQL has (a
        # release and a dispatch are REST-only): a merge or auto-merge, a ref
        # deletion, an issue, discussion or project deletion, and a ref
        # CREATED under refs/tags/ (a tag push by another spelling).
        r"\bgh\s+api\s+graphql\b.*\bmutation\b.*(?:\b(?:mergePullRequest|enablePullRequestAutoMerge"
        r"|deleteRef|deleteIssue|deleteDiscussion|deleteProjectV2)\b|refs/tags/)",
        "an irreversible act through a GraphQL mutation (merge, ref deletion, a deletion, or a tag ref); the human runs it",
    ),
    (
        r"\bgh\s+repo\s+delete\b",
        "deleting a repository, which no page undoes; the human does it",
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
