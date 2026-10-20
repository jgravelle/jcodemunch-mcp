"""pre_pr reads the full-tier stamp of the tree `gh pr create` runs in (jjg, 2026-10-19).

W-30 made the hooks follow the payload's `cwd` into a worktree. The Bash tool
resets its cwd to the session folder on every call, so a PR opened from a
worktree is `cd <worktree> && gh pr create ...` with the payload's `cwd` still
the live checkout. pre_pr then read the LIVE checkout's stamp: the PR was
refused when that checkout had no stamp, and when the live checkout carried a
valid stamp of its own, the worktree's PR passed on a tree nobody had tested.
So branch work had to happen in the live checkout, which every session serves
as an editable install.

The rule, as ruled: the stamp is read from the git toplevel of the directory
the command `cd`s into before `gh pr create`; it must match that tree; the
checklist and fast verdict come from the same tree; there is no fallback to
the session's tree. A `cd` target that is not a checkout carrying
`.claude/hooks` refuses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


def _clone(dst: Path, branch: str) -> Path:
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(dst)], check=True
    )
    subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=dst, check=True)
    hooks = dst / ".claude" / "hooks"
    if hooks.exists():
        shutil.rmtree(hooks)
    shutil.copytree(HOOKS, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _tree(checkout: Path) -> str:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s'); import _common; print(_common.tree_id())"
            % (checkout / ".claude" / "hooks"),
        ],
        capture_output=True,
        text=True,
        cwd=checkout,
        check=True,
    ).stdout.strip()


def _stamp(checkout: Path, tree: str | None = None) -> None:
    """A passing full tier, fast tier and checklist on `checkout` (for `tree`, default its own)."""
    evidence = checkout / ".claude" / "state" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (checkout / ".claude" / "state" / "full-tier.json").write_text(
        json.dumps({"tree": tree or _tree(checkout), "ok": True, "date": "x"}),
        encoding="utf-8",
    )
    (evidence / "checklist.md").write_text("| 1 | x | met | y |\n", encoding="utf-8")
    (evidence / "fast.md").write_text(
        "## harness fast: PASS\n\nHARNESS PASS\n", encoding="utf-8"
    )


def _unstamp(checkout: Path) -> None:
    state = checkout / ".claude" / "state"
    if state.exists():
        shutil.rmtree(state)


def _pre_pr(command: str, session_cwd: Path) -> subprocess.CompletedProcess:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(session_cwd),
    }
    return subprocess.run(
        [sys.executable, str(HOOKS / "pre_pr.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=session_cwd,
    )


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    base = tmp_path_factory.mktemp("prepr")
    live = _clone(base / "live", "feat/live")
    wt = _clone(base / "wt dir", "feat/wt")  # a space: the path must survive quoting
    return live, wt


CREATE = 'GITHUB_TOKEN="" gh pr ' + "create --title x --body-file b.md"


def test_a_pr_from_a_worktree_reads_the_worktrees_stamp(trees):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    r = _pre_pr(f'cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr


def test_the_session_trees_stamp_never_stands_in_for_the_worktrees(trees):
    """No fallback: a valid live stamp does not pass a worktree whose own stamp is for another tree."""
    live, wt = trees
    _stamp(live)
    _stamp(wt, tree="0" * 40)
    r = _pre_pr(f'cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "DIFFERENT tree" in r.stderr, r.stderr


def test_a_worktree_with_no_stamp_refuses_even_when_the_session_tree_has_one(trees):
    live, wt = trees
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(f"cd '{wt.as_posix()}' && {CREATE}", live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


def test_a_cd_into_something_that_is_not_a_checkout_refuses(trees, tmp_path):
    live, _ = trees
    _stamp(live)
    r = _pre_pr(f'cd "{tmp_path.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "not a checkout" in r.stderr, r.stderr


def test_a_relative_cd_resolves_against_the_session_cwd(trees):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    rel = Path("..") / wt.name
    r = _pre_pr(f'cd "{rel.as_posix()}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr


def test_the_last_cd_before_the_create_is_the_one_that_counts(trees):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    r = _pre_pr(f'cd "{live.as_posix()}" && cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(f'cd "{live.as_posix()}" && cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


def test_a_chained_relative_cd_resolves_against_the_previous_cd(trees):
    """`cd <wt> && cd src` runs in <wt>/src, whose toplevel is <wt>, never <session>/src."""
    live, wt = trees
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(f'cd "{wt.as_posix()}" && cd src && {CREATE}', live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


def _refusals(live: Path, wt: Path) -> list[str]:
    w, ll = wt.as_posix(), live.as_posix()
    return [
        f'cd "{w}"\n{CREATE}',
        f'(cd "{w}" && {CREATE})',
        f'{{ cd "{w}" && {CREATE}; }}',
        f'pushd "{w}" && {CREATE}',
        f'Set-Location "{w}"; {CREATE}',
        f'sl "{w}"; {CREATE}',
        f'cd "{ll}" || cd "{w}" && {CREATE}',
        f'X=1 cd "{w}" && {CREATE}',
        f'builtin cd "{w}" && {CREATE}',
        f'cd "{w}" && echo "{CREATE}" && cd "{ll}" && {CREATE}',
        f'cd "$(echo {w})" && {CREATE}',
        f"cd - && {CREATE}",
        f"cd && {CREATE}",
        # Review round 3: a keyword before the cd hid it from a first-word check.
        f'if cd "{w}"; then {CREATE}; fi',
        f'time cd "{w}"; {CREATE}',
        f'! cd "{w}"; {CREATE}',
        f'eval cd "{w}" && {CREATE}',
        f'eval "cd {w}" && {CREATE}',
        f'source "{w}/x.sh" && {CREATE}',
        f'& {{ cd "{w}" }}; {CREATE}',
        f'if ($true) {{ cd "{w}" }}; {CREATE}',
        # Review round 4: gh run by another program, a glued redirect, cd..
        f"bash -c 'cd \"{w}\" && {CREATE}'",
        f"sh -c 'cd \"{w}\"; {CREATE}'",
        f"pwsh -c 'cd \"{w}\"; {CREATE}'",
        f'cmd /c "cd /d {w} && {CREATE}"',
        f'env -C "{w}" {CREATE}',
        f'env --chdir="{w}" {CREATE}',
        f'cd>/dev/null "{w}" && {CREATE}',
        f'cd</dev/null "{w}" && {CREATE}',
        f"cd..; {CREATE}",
        # Review round 5: a function defined on the line, a branch or repository named elsewhere.
        f'f(){{ $X "{w}"; }}; X=cd; f; {CREATE}',
        f'X=cd; f(){{ $X "{w}"; }}; f && {CREATE}',
        f'gh(){{ $X "{w}"; command gh "$@"; }}; X=cd; {CREATE}',
        f'function f {{ cd "{w}" }}; f; {CREATE}',
        f"{CREATE} --head feat/untested",
        f"{CREATE} -H feat/untested",
        f'GIT_DIR="{w}/.git" {CREATE}',
        f'export GIT_WORK_TREE="{w}"; {CREATE}',
    ]


@pytest.mark.parametrize("index", range(38))
def test_a_directory_change_it_cannot_follow_refuses(trees, index):
    """Fail closed: both trees carry a valid stamp, so a fallback to EITHER would pass."""
    live, wt = trees
    _stamp(live)
    _stamp(wt)
    command = _refusals(live, wt)[index]
    r = _pre_pr(command, live)
    assert r.returncode == 2 and "cannot tell which tree" in r.stderr, (
        command,
        r.stderr,
    )


def test_the_refusal_list_is_the_one_parametrized(trees):
    assert len(_refusals(*trees)) == 38


@pytest.mark.parametrize("spelling", ["-P", "-L", "--"])
def test_cd_options_that_still_name_one_directory_are_followed(trees, spelling):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    r = _pre_pr(f'cd {spelling} "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(
    os.name != "nt", reason="the /c/... drive spelling is Git Bash on Windows"
)
def test_the_git_bash_drive_spelling_is_followed(trees):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    posix = wt.as_posix()
    drive = "/" + posix[0].lower() + posix[2:]
    r = _pre_pr(f'cd "{drive}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "flag",
    [
        "-R jgravelle/jcodemunch-mcp",
        "--repo jgravelle/jcodemunch-mcp",
        "--repo=jgravelle/jcodemunch-mcp",
    ],
)
def test_a_repo_flag_before_the_subcommand_is_still_gated(trees, flag):
    """`gh -R o/r pr create` used to miss the pattern entirely and pass with no check (W-58)."""
    live, _ = trees
    _unstamp(live)
    r = _pre_pr("gh " + flag + " pr " + "create --title x", live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


@pytest.mark.parametrize("spelling", ["\\cd", "c''d", 'c""d', "c\\d"])
def test_a_quoted_or_escaped_cd_is_followed_never_skipped(trees, spelling):
    """The shell runs each of these as `cd`; so must the hook (review round 2)."""
    live, wt = trees
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(f'{spelling} "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


def test_an_expanded_command_word_refuses(trees):
    live, wt = trees
    _stamp(live)
    _stamp(wt)
    r = _pre_pr(f'C=c; ${{C}}d "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "cannot tell which tree" in r.stderr, r.stderr


def test_a_fake_heredoc_cannot_hide_a_cd(trees):
    """A quoted `<<X` makes `strip_heredocs` swallow the real `cd` behind it."""
    live, wt = trees
    _stamp(live)
    _stamp(wt)
    command = f"echo '<<X' && cd \"{wt.as_posix()}\"\n\nX\n{CREATE}"
    r = _pre_pr(command, live)
    assert r.returncode == 2 and "cannot tell which tree" in r.stderr, r.stderr


@pytest.mark.parametrize(
    "title", ["fix: cd into the worktree", "the sl alias", "pushd and popd"]
)
def test_a_title_that_mentions_cd_is_not_a_directory_change(trees, title):
    live, _ = trees
    _stamp(live)
    r = _pre_pr(f'{CREATE} --title "{title}"', live)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "spelling",
    [
        "gh -Rjgravelle/jcodemunch-mcp pr " + "create",
        "gh pr " + "new",
        "gh.exe pr " + "create",
    ],
)
def test_every_spelling_that_opens_a_pr_is_gated(trees, spelling):
    live, _ = trees
    _unstamp(live)
    r = _pre_pr(spelling + " --title x", live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


@pytest.mark.parametrize("binary", ["GH", "Gh"])
def test_the_binary_name_is_matched_in_any_case(trees, binary):
    """Windows resolves `GH` to gh.exe, so a case-sensitive pattern skipped the gate."""
    live, _ = trees
    _unstamp(live)
    r = _pre_pr(binary + " pr " + "create --title x", live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


def test_a_heredoc_body_that_says_ci_cd_is_not_a_directory_change(trees):
    """The default PR form: the body is one quoted word, so `CI/CD` in it changes nothing."""
    live, _ = trees
    _stamp(live)
    command = (
        CREATE
        + " --body \"$(cat <<'EOF'\nAdds CI/CD notes; cd is not run here\nEOF\n)\""
    )
    r = _pre_pr(command, live)
    assert r.returncode == 0, r.stderr


def test_the_powershell_token_prefix_is_an_assignment_not_an_expansion(trees):
    """`$env:GITHUB_TOKEN=""` is this box's prescribed PowerShell prefix (CLAUDE.md shell notes)."""
    live, wt = trees
    _stamp(live)
    r = _pre_pr('$env:GITHUB_TOKEN=""; gh pr ' + "create --title x", live)
    assert r.returncode == 0, r.stderr
    _unstamp(live)
    _stamp(wt)
    r = _pre_pr(
        f'cd "{wt.as_posix()}"; $env:GITHUB_TOKEN=""; gh pr ' + "create --title x", live
    )
    assert r.returncode == 0, r.stderr


def test_without_a_cd_the_session_tree_is_still_the_one_read(trees):
    live, wt = trees
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(CREATE, live)
    assert r.returncode == 0, r.stderr
    _unstamp(live)
    r = _pre_pr(CREATE, live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr
