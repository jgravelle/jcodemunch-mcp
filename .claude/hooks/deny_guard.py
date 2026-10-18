"""H5: belt to the settings deny list (DESIGN section 4, D8).

purpose:  nothing in a session does the IRREVERSIBLE: publish, tag, dispatch a
          release, merge, force-push; the refusal names the RUNBOOK section
          the human runs instead. Posting (a PR, a comment, a review, an
          edit, an alert dismissal) is the session's: every one of those can
          be undone from the page (W-40, jjg 2026-09-07)
invokes:  nothing
produces: nothing
refuses:  the verbs below, on Bash and PowerShell; a guard that fails to load
          or crashes refuses too
budget:   1 s
"""

from __future__ import annotations

import re
import sys

# A guard that cannot load must refuse: Claude Code reads exit 1 as a non-blocking
# error and runs the command (review of the results-merge rule, 2026-10-17).
try:
    from _common import block, ok, read_hook_input, tool_command
except Exception as _exc:  # noqa: BLE001
    sys.stderr.write(f"deny_guard: refused; the guard could not load ({type(_exc).__name__}: {_exc})\n")
    sys.exit(2)

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
        # A write is `--method`/`-X` (with a space, `=` or attached; the value maybe
        # quoted) OR any field flag, because `gh api` sends POST whenever a field is
        # given (`gh api --help`): `-f ref=main` on .../dispatches is a dispatch with
        # no method named (review of W-57, 2026-10-18).
        # Bounded to ONE command (`[^|;&]*`, as the push rules are), so a `-f` in a
        # piped `grep -F` or a chained `rm -f` is not read as a field; an explicit
        # GET is a read. Short flags may be clustered (`-iXPUT`, `-if`).
        # A NAMED write method reads the whole line, as on main (a quoted `&` or `;`
        # in a merge title must not end the window; review round 3, 2026-10-18).
        r"\bgh\s+api\b(?=.*(?:--method[=\s]+['\"]?(?:POST|PATCH|PUT|DELETE)|\s-[A-Za-z]*X[=\s]*['\"]?(?:POST|PATCH|PUT|DELETE)))"
        r".*(?:/pulls/\d+/merge\b|/releases\b|/dispatches\b|/git/tags\b|refs/tags/|--method[=\s]+['\"]?DELETE|\s-[A-Za-z]*X[=\s]*['\"]?DELETE)",
        "an irreversible write through the API (merge, release, dispatch, tag or delete); the human runs it",
    ),
    (
        # An IMPLICIT write: `gh api` sends POST whenever a field flag is given, so
        # `-f ref=main` on .../dispatches needs no method. Read within ONE command
        # (a piped `grep -F` or a chained `rm -f` is not a field), waived by an
        # explicit GET, and not for graphql, whose mutations the rule below owns.
        r"\bgh\s+api\b(?![^|;&]*\bgraphql\b)(?![^|;&]*(?:--method[=\s]+['\"]?GET\b|\s-[A-Za-z]*X[=\s]*['\"]?GET\b))"
        r"(?=[^|;&]*\s(?:-[A-Za-z]*[fF]|--field|--raw-field|--input)(?=[\s=]|\S))[^|;&]*(?:/pulls/\d+/merge\b|/releases\b|/dispatches\b|/git/tags\b|refs/tags/)",
        "an implicit write through the API (a field flag on a merge, release, dispatch or tag path); the human runs it",
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
        # A GraphQL query read from a file carries no mutation text for the rule
        # above to read; the human runs those (2026-10-17).
        r"\bgh\s+api\b(?=[^|;&]*\bgraphql\b)[^|;&]*(?:query=@|--input\b)",
        "a GraphQL query read from a file, which no rule here can read; the human runs it",
    ),
    (
        # `gh alias set m 'pr merge'` then `gh m 5` reaches a refused verb that no
        # rule here reads (2026-10-17).
        r"\bgh\s+alias\s+(?:set|import)\b",
        "a gh alias, which can rename a refused verb past this guard; the human sets aliases",
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
            block(
                f"deny_guard: refused {why}. Hand the line to the human in cmd.exe form (docs/workflows/DESIGN.md D8)."
            )
    ok()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - a crashed guard must refuse: exit 1 lets the command run
        block(f"deny_guard: refused; the guard itself failed ({type(exc).__name__}: {exc}), and a failed guard never allows")
