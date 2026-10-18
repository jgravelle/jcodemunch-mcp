"""deny_guard hardening found while reviewing a merge rule that did not ship (jjg, 2026-10-17).

jjg ruled "Option C": weekly results PRs stay his one-click merge, and the
unrelated hardening the review found ships on its own. Each case below was a
route past the guard on `main`:

- `gh api --method=PUT .../pulls/N/merge` (the rule needed `--method `);
- a GraphQL query read from a file, which carries no mutation text to read;
- `gh alias set m 'pr merge'`, after which `gh m 5` reaches a refused verb;
- a guard that cannot import `_common`, or crashes, exited 1, which Claude Code
  reads as a non-blocking error and RUNS the command.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))

import deny_guard  # noqa: E402


def _refused(cmd: str) -> bool:
    return any(re.search(p, cmd, re.I) for p, _ in deny_guard.DENIED)


@pytest.mark.parametrize(
    "cmd",
    [
        "gh api --method=PUT repos/x/y/pulls/5/merge",
        "gh api --method PUT repos/x/y/pulls/5/merge",
        "gh api -XPUT repos/x/y/pulls/5/merge",
        "gh api --method=DELETE repos/x/y/git/refs/heads/z",
        "gh api -X=PUT repos/x/y/pulls/5/merge",
        "gh api -iXPUT repos/x/y/pulls/5/merge",
        "gh api --method 'PUT' repos/x/y/pulls/5/merge",
        "gh api -X \"PUT\" repos/x/y/pulls/5/merge",
        # main refused these; a bounded window let a quoted & ; | end it (review round 3)
        "gh api -X PUT -f commit_title='Fix A & B (#12)' repos/o/r/pulls/5/merge",
        "gh api -X PUT -f commit_title='Fix A; B' repos/o/r/pulls/5/merge",
        "gh api --method PUT -f commit_message='a | b' repos/o/r/pulls/5/merge",
        "gh api --jq '.sha | tostring' -X PUT repos/o/r/pulls/5/merge",
        "gh api -H 'Accept: application/vnd.github+json; q=1' -X PUT repos/o/r/pulls/5/merge",
    ],
)
def test_every_spelling_of_an_api_merge_or_delete_is_refused(cmd):
    assert _refused(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    ["gh api graphql -F query=@m.graphql", "gh api graphql --input q.json", "gh api -H x graphql -f query=@q",
     "gh api graphql -Fquery=@m.graphql", "gh api graphql -fquery=@m.graphql"],
)
def test_a_graphql_query_read_from_a_file_is_refused(cmd):
    assert _refused(cmd), cmd


@pytest.mark.parametrize("cmd", ["gh alias set m 'pr merge'", "gh alias import aliases.yml", "gh  alias  set x y"])
def test_creating_a_gh_alias_is_refused(cmd):
    assert _refused(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    ["gh api repos/o/r/actions/workflows/release.yml/dispatches -f ref=main",
     "gh api repos/o/r/releases -f tag_name=v1",
     "gh api repos/o/r/git/refs -f ref=refs/tags/v1 -f sha=abc",
     "gh api repos/o/r/pulls/5/merge -f merge_method=squash",
     "gh api repos/o/r/dispatches --input payload.json",
     "gh api repos/o/r/releases -Ftag_name=v1",
     "gh api -if ref=main repos/o/r/actions/workflows/w.yml/dispatches",
     "gh api -iF tag_name=v1 repos/o/r/releases",
     "gh api -X GET --method POST repos/o/r/dispatches -f ref=main",
     "gh api --method GET -X PUT repos/o/r/pulls/5/merge"],
)
def test_an_implicit_post_through_a_field_flag_is_refused(cmd):
    """`gh api` sends POST whenever a field is given, so no `--method` is needed for the act."""
    assert _refused(cmd), cmd


@pytest.mark.parametrize(
    "cmd", ["gh alias list", "gh api graphql -f query='query { viewer { login } }'", "gh api repos/x/y/pulls/5",
            "gh api repos/x/y/issues/5/comments -f body=thanks", "gh api -X=GET repos/x/y",
            "gh api repos/o/r/releases | grep -F v1", "gh api repos/o/r/releases/latest > x.json; rm -f x.json",
            "gh api repos/o/r/releases -X GET -f per_page=100", "gh api --method GET repos/o/r/releases -f per_page=5",
            "gh api graphql -f query='query { viewer { login } }' | grep -F --input",
            "gh api graphql -f query='query { repository(owner:\"o\", name:\"r\") { refs(refPrefix:\"refs/tags/\", first:5) { nodes { name } } } }'"],
)
def test_reads_and_posting_still_pass(cmd):
    """Posting and reads are the session's (W-40); the new rules refuse acts, not words."""
    assert not _refused(cmd), cmd


def test_settings_deny_the_alias_forms_on_both_shells():
    deny = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
    for shell in ("Bash", "PowerShell"):
        for entry in (f"{shell}(gh alias set*)", f"{shell}(gh alias import*)"):
            assert entry in deny, entry


def _run(args, stdin: str, cwd=ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=60)


def test_a_guard_that_cannot_load_refuses(tmp_path):
    """Copied away from `_common`, the import fails; that must be exit 2, never exit 1."""
    lone = tmp_path / "deny_guard.py"
    lone.write_text((HOOKS / "deny_guard.py").read_text(encoding="utf-8"), encoding="utf-8")
    r = _run([sys.executable, str(lone)], "{}", cwd=tmp_path)
    assert r.returncode == 2, (r.returncode, r.stderr)


def test_a_crashed_guard_refuses():
    code = (
        "import sys, runpy; sys.path.insert(0, r'%s'); import _common; "
        "_common.tool_command = lambda p: (_ for _ in ()).throw(RuntimeError('boom')); "
        "runpy.run_path(r'%s', run_name='__main__')" % (HOOKS, HOOKS / "deny_guard.py")
    )
    r = _run([sys.executable, "-c", code], json.dumps({"tool_input": {"command": "echo hi"}}))
    assert r.returncode == 2, (r.returncode, r.stderr)


def test_a_harmless_line_still_passes_the_real_hook():
    r = _run([sys.executable, str(HOOKS / "deny_guard.py")], json.dumps({"tool_input": {"command": "echo hi"}, "cwd": str(ROOT)}))
    assert r.returncode == 0, (r.returncode, r.stderr)
