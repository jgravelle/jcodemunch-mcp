"""H5: belt to the settings deny list (DESIGN section 4, D8).

purpose:  nothing in a session does the IRREVERSIBLE: publish, tag, dispatch a
          release, merge, force-push; the refusal names the RUNBOOK section
          the human runs instead. Posting (a PR, a comment, a review, an
          edit, an alert dismissal) is the session's: every one of those can
          be undone from the page (W-40, jjg 2026-09-07). One merge passes
          its checks (jjg 2026-10-17): enabling auto-merge on a main.yml
          harness-results PR that touches only harness/results/*.json;
          a crashed or unverifiable check refuses
invokes:  gh api (read only), for that one merge
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

#: jjg (2026-10-17, DECISIONS line 80, and in session the same day): "Weekly
#: harness-results PRs from main.yml may auto-merge when every check is green."
#: The session may ENABLE GitHub's auto-merge on such a PR; GitHub then merges
#: it only when every required check, license/cla included, is green.
#: ⚠⚠ This is the ONLY conditional merge, and it fails CLOSED: any form other
#: than `_RESULTS_AUTO_RE` is refused by the regex alone, with no network, and
#: every exception or timeout on the checked form refuses. Review of 2026-10-17
#: (BLOCK) found each guard below by breaking it:
#:  - author by REST `user.type == "Bot"` and login `github-actions[bot]`;
#:  - branch `harness-bot/results-YYYY-MM-DD` of THIS repo, base `main`;
#:  - the head pinned by `--match-head-commit`, equal to the live head, so a push
#:    between this check and the merge makes GitHub refuse the merge;
#:  - files from the paginated REST list (`gh pr view` stops at 100 and hides a
#:    rename's old path), each `added`/`modified`, none renamed, all under
#:    `harness/results/*.json`, and their count equal to `changed_files`;
#:  - `--repo jgravelle/jcodemunch-mcp` in the command, so the merge cannot act
#:    on another checkout's PR of the same number.
#: A push to a results branch after enabling is refused separately (DENIED).
_REPO = "jgravelle/jcodemunch-mcp"
_RESULTS_AUTO_RE = re.compile(
    r"\A\s*(?:GITHUB_TOKEN=(?:\"\"|'')?[ \t]+)?gh[ \t]+pr[ \t]+merge[ \t]+([0-9]+)"
    r"[ \t]+--repo[ \t]+jgravelle/jcodemunch-mcp[ \t]+--squash[ \t]+--auto"
    r"[ \t]+--match-head-commit[ \t]+([0-9a-f]{40})[ \t]*\Z"
)
_RESULTS_BOT_LOGIN = "github-actions[bot]"
_RESULTS_BRANCH_RE = re.compile(r"\Aharness-bot/results-[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_RESULTS_FILE_RE = re.compile(r"\Aharness/results/[A-Za-z0-9_.-]+\.json\Z")
_GH_TIMEOUT_S = 6  # each call, at most 1 + _MAX_FILE_PAGES of them, inside settings.json's 30 s hook budget:
# a slow API refuses rather than letting the runner kill the hook (W-42: a killed hook reads as consent)
_MAX_FILE_PAGES = 3


def _gh_api(path: str) -> object:
    env = dict(os.environ, GITHUB_TOKEN="")
    out = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, encoding="utf-8",
        timeout=_GH_TIMEOUT_S, env=env, cwd=str(_common.REPO),
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"gh api {path} failed")
    return json.loads(out.stdout)


def results_auto_merge_refusal(cmd: str, api=_gh_api) -> str | None:
    """None when this line enables auto-merge on a main.yml results PR; else why not. Never raises."""
    m = _RESULTS_AUTO_RE.match(cmd)
    if not m:
        return (
            f"the only merge the session may run is `gh pr merge <n> --repo {_REPO} --squash --auto "
            "--match-head-commit <40-hex head>`, alone on the line, for a main.yml results PR"
        )
    number, head = m.group(1), m.group(2)
    try:
        pr = api(f"repos/{_REPO}/pulls/{number}")
        if not isinstance(pr, dict):
            return f"PR #{number}: unreadable answer"
        user = pr.get("user") if isinstance(pr.get("user"), dict) else {}
        head_ref = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        base_ref = pr.get("base") if isinstance(pr.get("base"), dict) else {}
        head_repo = head_ref.get("repo") if isinstance(head_ref.get("repo"), dict) else {}
        if pr.get("state") != "open":
            return f"PR #{number} is not open"
        if user.get("login") != _RESULTS_BOT_LOGIN or user.get("type") != "Bot":
            return f"PR #{number} is not a main.yml results PR (author {user.get('login')!r})"
        if not _RESULTS_BRANCH_RE.match(str(head_ref.get("ref") or "")):
            return f"PR #{number}'s branch {head_ref.get('ref')!r} is not harness-bot/results-<date>"
        if head_repo.get("full_name") != _REPO or base_ref.get("ref") != "main":
            return f"PR #{number} is not a {_REPO} branch into main"
        if head_ref.get("sha") != head:
            return f"PR #{number}'s head is {head_ref.get('sha')!r}, not the pinned {head}"
        expected = pr.get("changed_files")
        if not isinstance(expected, int) or expected < 1 or expected > 100 * _MAX_FILE_PAGES:
            return f"PR #{number}: changed_files {expected!r} is not a checkable count"
        files: list = []
        for page in range(1, _MAX_FILE_PAGES + 1):
            batch = api(f"repos/{_REPO}/pulls/{number}/files?per_page=100&page={page}")
            if not isinstance(batch, list):
                return f"PR #{number}: unreadable file list"
            files.extend(batch)
            if len(batch) < 100:
                break
        if len(files) != expected:
            return f"PR #{number}: {len(files)} files listed, {expected} changed; refusing a partial list"
        for f in files:
            name = f.get("filename") if isinstance(f, dict) else None
            if (
                not isinstance(name, str)
                or not _RESULTS_FILE_RE.match(name)
                or f.get("status") not in ("added", "modified")
                or f.get("previous_filename")
            ):
                return f"PR #{number} changes more than harness/results/*.json ({name!r}, {f.get('status') if isinstance(f, dict) else '?'})"
    except Exception as exc:  # noqa: BLE001 - any failure to establish the facts refuses
        return f"could not verify PR #{number} ({type(exc).__name__}: {exc})"
    return None


#: The one verb with a conditional pass, `results_auto_merge_refusal` (jjg 2026-10-17).
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
        # After auto-merge is enabled on a results PR, GitHub would still merge a later
        # push by a writer; the session must not be that writer (review 2026-10-17).
        r"\bgit\s+push\b[^|;&]*\bharness-bot/",
        "a push to a harness-bot results branch; only main.yml writes those (an auto-merge may be pending)",
    ),
    (
        # `gh alias set m 'pr merge'` then `gh m 5` reaches a merge no rule reads.
        r"\bgh\s+alias\s+(?:set|import)\b",
        "a gh alias, which can rename a refused verb past this guard; the human sets aliases",
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
                refusal = results_auto_merge_refusal(cmd)
                if refusal is None:
                    continue
                why = f"{why} ({refusal})"
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
