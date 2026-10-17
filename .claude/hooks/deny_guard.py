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

import json
import os
import re
import subprocess

import _common
from _common import block, ok, read_hook_input, tool_command

#: jjg (2026-10-16 DECISIONS line 78; 2026-10-17 in session, "relax the merge
#: hook for test- and doc-only PRs the Chief of Staff has cleared"): the session
#: may merge its OWN PRs that change only tests or docs once the Chief of Staff
#: has cleared them. Product code, contributor PRs and releases stay with jjg.
#: ⚠⚠ Only this exact form passes, and only when every check below holds; an API
#: or GraphQL merge, `--admin`, `--auto`, a chained line, a contributor PR, a
#: path outside tests/docs, a head that moved since clearance, or a PR that is
#: not CLEAN is refused exactly as before.
_CLEARED_MERGE_RE = re.compile(r"^\s*(?:GITHUB_TOKEN=(?:\"\"|'')?\s+)?gh\s+pr\s+merge\s+(\d+)\s+--squash\s*$")
_OWN_AUTHOR = "jgravelle"


def _doc_or_test(path: str) -> bool:
    return path.startswith(("tests/", "docs/")) or ("/" not in path and path.endswith(".md"))


def _gh_pr(number: str) -> dict:
    env = dict(os.environ, GITHUB_TOKEN="")
    out = subprocess.run(
        ["gh", "pr", "view", number, "--json", "author,files,headRefOid,mergeStateStatus"],
        capture_output=True, text=True, encoding="utf-8", timeout=30, env=env, cwd=str(_common.REPO),
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "gh pr view failed")
    return json.loads(out.stdout)


def cleared_merge_refusal(cmd: str, gh=_gh_pr) -> str | None:
    """None when this line is a cleared test/doc-only merge of our own PR; else why not.

    The clearance record is `.claude/state/cleared/<n>.json` (gitignored state),
    written by the session when the Chief of Staff clears PR <n>:
    `{"head": "<the cleared head SHA>", "cleared_by": "Chief of Staff", "at": "<UTC>"}`.
    A push after clearance moves the head and voids the record.
    """
    m = _CLEARED_MERGE_RE.match(cmd)
    if not m:
        return "only `gh pr merge <n> --squash`, alone on the line, can be a cleared merge"
    number = m.group(1)
    marker = _common.STATE / "cleared" / f"{number}.json"
    try:
        cleared = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"PR #{number} has no clearance record ({marker.name}); the Chief of Staff clears it first"
    try:
        pr = gh(number)
    except Exception as exc:  # noqa: BLE001 - any failure to read the PR refuses
        return f"could not read PR #{number} ({exc})"
    if (pr.get("author") or {}).get("login") != _OWN_AUTHOR:
        return f"PR #{number} is not ours; a contributor PR is jjg's to merge"
    if pr.get("headRefOid") != cleared.get("head"):
        return f"PR #{number}'s head moved since it was cleared; clear the new head first"
    if pr.get("mergeStateStatus") != "CLEAN":
        return f"PR #{number} is {pr.get('mergeStateStatus')}, not CLEAN"
    other = [f["path"] for f in pr.get("files") or [] if not _doc_or_test(f.get("path", ""))]
    if other or not pr.get("files"):
        return f"PR #{number} changes more than tests and docs ({', '.join(other[:5]) or 'no files listed'})"
    return None

#: The one verb with a conditional pass, `cleared_merge_refusal` (jjg 2026-10-17).
_PR_MERGE_PATTERN = r"\bgh\s+pr\s+merge\b"

DENIED = [
    (
        r"\bgit\s+push\b[^|;&]*(?:--force|(?-i:\s-f\b)|--force-with-lease)",
        "a force-push; RUNBOOK section 6 is the emergency path",
    ),
    (
        # W-44: the creating forms, as `settings.json` lists them (a `v`-prefixed
        # name, or a create/sign/force/delete flag), not any two words after the
        # verb: prose such as "the git tag rule" is not a tag. The read forms
        # (`--list`, `--sort`, `--contains`, ...) fall outside by construction.
        # A combined short flag (`-am msg`) is the same act (review round 1).
        # Residual, recorded in W-44: a lightweight non-`v` name (`git tag foo`)
        # is refused by neither list, and neither is its push by bare name.
        r"\bgit\s+tag\s+(?:-[asfdm]\w*|--force\b|--delete\b|--annotate\b|--sign\b|--message\b|v\d)",
        "a tag; release.yml tags (RUNBOOK section 1)",
    ),
    (
        r"\bgit\s+push\b[^|;&]*\s(?:--tags\b|v\d|refs/tags/)",
        "pushing a tag; release.yml tags (RUNBOOK section 1)",
    ),
    (
        # W-44: the ACT, never the word. `view`, `list` and `download` are reads
        # (the hand-finish downloads the CI artifact); flags may sit between
        # `release` and its verb (`gh release -R x/y create`).
        r"\bgh\s+release\b(?:\s+-\S+(?:\s+\S+)?)*\s+(?:create|edit|delete|delete-asset|upload)\b",
        "a GitHub release; release.yml creates it (RUNBOOK section 1)",
    ),
    (
        r"\bgh\s+workflow\s+run\b",
        "a workflow dispatch; the human dispatches (RUNBOOK section 1, step 3)",
    ),
    (
        _PR_MERGE_PATTERN,
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
        # `graphql` may sit after flags (`gh api -H ... graphql`), as the REST
        # rule's path may; adjacency was a spelling (review round 1).
        r"\bgh\s+api\b(?=.*\bgraphql\b).*\bmutation\b.*(?:\b(?:mergePullRequest|enablePullRequestAutoMerge"
        r"|deleteRef|deleteIssue|deleteDiscussion|deleteProjectV2)\b|refs/tags/)",
        "an irreversible act through a GraphQL mutation (merge, ref deletion, a deletion, or a tag ref); the human runs it",
    ),
    (
        r"\bgh\s+repo\s+delete\b",
        "deleting a repository, which no page undoes; the human does it",
    ),
    # W-44: `twine check` is RUNBOOK 1a's own gate line and a grep may carry the
    # word; only the upload is irreversible.
    # Flags may sit between the binary and its verb (`twine --no-color upload`),
    # as `settings.json`'s `*twine upload*` glob already admits (review round 1).
    (
        r"\btwine\b(?:\s+-\S+(?:\s+[^-\s]\S*)?)*\s+(?:upload|register)\b",
        "a PyPI upload; RUNBOOK section 1a is the human's hand-finish",
    ),
    # W-44: `--version`/`--help` and prose naming the binary are not a publish.
    # `login` stays refused: it writes live credential files into the CWD and
    # is the human's device flow. Optional `.exe"` and flags between name and
    # verb; `--help`/`-h` anywhere after the verb, within the SAME command, is
    # a read: the lookahead stops at `|`, `;` and `&` like the push rules, or
    # `publish && echo --help` would pass (review round 2).
    (
        r"mcp-publisher(?:\.exe)?[\"']?(?:\s+-\S+(?:\s+[^-\s]\S*)?)*\s+(?:publish|login)\b(?![^|;&]*\s(?:--help|-h)\b)",
        "a registry publish; release.yml publishes",
    ),
]


def main() -> None:
    cmd = tool_command(read_hook_input())
    if not cmd:
        ok()
    for pattern, why in DENIED:
        if re.search(pattern, cmd, re.I):
            if pattern == _PR_MERGE_PATTERN:
                refusal = cleared_merge_refusal(cmd)
                if refusal is None:
                    continue
                why = f"{why} ({refusal})"
            block(
                f"deny_guard: refused {why}. Hand the line to the human in cmd.exe form (docs/workflows/DESIGN.md D8)."
            )
    ok()


if __name__ == "__main__":
    main()
