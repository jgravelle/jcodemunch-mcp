"""deny_guard's one conditional merge: a CLEAN main.yml harness-results PR on a pinned head (jjg, 2026-10-17).

jjg ruled that weekly harness-results PRs from main.yml may auto-merge when every
check is green. `results_auto_merge_refusal` is that rule. ⚠⚠ The tests that
matter are the refusals: each guard is broken on its own and the line must be
refused, and every failure to establish a fact (a partial answer, a timeout, a
crash) must refuse too, because a guard that errors lets the command run. The
first draft was BLOCKed in review for failing open on a crash and a timeout,
trusting a 100-entry file list that hides renames, checking one repository while
the merge could act on another, and leaving a push window after enabling; each
of those has a test below. Round 2 found that `--auto` itself leaves a window
(GitHub keeps auto-merge on through a writer's later push), so the allowed form
merges now, on a head pinned by `--match-head-commit`, once GitHub reads it clean.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))

import deny_guard  # noqa: E402

_VERB = "gh pr " + "merge"  # split so no shell line ever carries the verb literally
_HEAD = "a" * 40
_REPO = "jgravelle/jcodemunch-mcp"
_CMD = f"{_VERB} 1030 --repo {_REPO} --squash --match-head-commit {_HEAD}"


def _pr(**over) -> dict:
    pr = {
        "state": "open",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "head": {"ref": "harness-bot/results-2026-10-19", "sha": _HEAD, "repo": {"full_name": _REPO}},
        "base": {"ref": "main"},
        "changed_files": 2,
        "mergeable_state": "clean",
    }
    pr.update(over)
    return pr


def _files(n: int = 2, **over) -> list[dict]:
    names = ["harness/results/latest.json", "harness/results/self_latency.json"]
    rows = [{"filename": names[i % 2] if n <= 2 else f"harness/results/r{i}.json", "status": "modified"} for i in range(n)]
    for k, v in over.items():
        rows[0][k] = v
    return rows


def _api(pr=None, files=None):
    pr = _pr() if pr is None else pr
    files = _files() if files is None else files

    def api(path):
        if path.endswith(f"pulls/1030"):
            return pr
        if "/files?" in path:
            page = int(path.rsplit("page=", 1)[1])
            return files[(page - 1) * 100 : page * 100]
        raise AssertionError(path)

    return api


def _refusal(cmd=_CMD, **kw):
    return deny_guard.results_auto_merge_refusal(cmd, api=_api(**kw))


def test_a_clean_results_pr_on_a_pinned_head_may_merge():
    assert _refusal() is None


def test_the_token_prefix_form_may_merge():
    assert _refusal(f'GITHUB_TOKEN="" {_CMD}') is None


@pytest.mark.parametrize(
    "cmd",
    [
        f"{_VERB} 1030 --squash",  # no repo, no pinned head
        f"{_VERB} 1030 --repo {_REPO} --squash",  # no pinned head
        f"{_VERB} 1030 --squash --match-head-commit {_HEAD}",  # no repo
        f"{_VERB} 1030 --repo jgravelle/jdocmunch-mcp --squash --match-head-commit {_HEAD}",
        f"{_VERB} 1030 --repo {_REPO} --squash --auto --match-head-commit {_HEAD}",  # --auto leaves a window
        f"{_CMD} --auto",
        f"{_CMD} --admin",
        f"{_VERB} 1030 --repo {_REPO} --merge --match-head-commit {_HEAD}",
        f"{_VERB} 1030 --repo {_REPO} --rebase --match-head-commit {_HEAD}",
        f"{_VERB} 1030 --repo {_REPO} --squash --match-head-commit {'A' * 40}",
        f"{_VERB} 1030 --repo {_REPO} --squash --match-head-commit {_HEAD[:39]}",
        f"{_CMD} && echo x",
        f"{_CMD}; git push",
        f"{_CMD}\necho x",
        f"cd x && {_CMD}",
        f"FOO=1 {_CMD}",
        f"{_VERB} 1030 --repo {_REPO} --squash --squash --match-head-commit {_HEAD}",
        f"{_VERB} 1030 --repo {_REPO} --squash --match-head-commit {_HEAD} -d",
    ],
)
def test_any_other_form_refuses_without_asking_the_api(cmd):
    def api(path):
        raise AssertionError("no network for a refused form")

    assert deny_guard.results_auto_merge_refusal(cmd, api=api) is not None


@pytest.mark.parametrize(
    "user",
    [{"login": "jgravelle", "type": "User"}, {"login": "github-actions[bot]", "type": "User"},
     {"login": "dependabot[bot]", "type": "Bot"}, {"login": "github-actions", "type": "Bot"}, {}, None, "github-actions[bot]"],
)
def test_any_other_author_refuses(user):
    assert _refusal(pr=_pr(user=user)) is not None


@pytest.mark.parametrize(
    "ref", ["harness-bot/results-latest", "feat/results-2026-10-19", "harness-bot/results-2026-10-19-x",
            "harness-bot/results-2026-10-19\n", "harness-bot/results-२०२६-10-19", "", None],
)
def test_any_other_branch_refuses(ref):
    pr = _pr()
    pr["head"] = dict(pr["head"], ref=ref)
    assert _refusal(pr=pr) is not None


def test_a_branch_from_another_repo_refuses():
    pr = _pr()
    pr["head"] = dict(pr["head"], repo={"full_name": "someone/fork"})
    assert "not a" in _refusal(pr=pr)


def test_a_base_other_than_main_refuses():
    assert _refusal(pr=_pr(base={"ref": "release/1.108.377"})) is not None


def test_a_head_that_differs_from_the_pinned_one_refuses():
    pr = _pr()
    pr["head"] = dict(pr["head"], sha="b" * 40)
    assert "not the pinned" in _refusal(pr=pr)


@pytest.mark.parametrize("mstate", ["blocked", "behind", "dirty", "unstable", "unknown", "has_hooks", None])
def test_a_pr_github_does_not_read_clean_refuses(mstate):
    assert "not clean" in _refusal(pr=_pr(mergeable_state=mstate))


@pytest.mark.parametrize("state", ["closed", None])
def test_a_pr_that_is_not_open_refuses(state):
    assert _refusal(pr=_pr(state=state)) is not None


@pytest.mark.parametrize(
    "name", ["harness/thresholds.json", "harness/results/sub/x.json", "harness/results/latest.txt",
             "harness/results/../thresholds.json", "harness/results/..\\..\\x.json", "src/jcodemunch_mcp/server.py",
             ".github/workflows/main.yml", ".claude/hooks/deny_guard.py", "CHANGELOG.md", None],
)
def test_a_file_outside_harness_results_refuses(name):
    files = _files()
    files[1] = {"filename": name, "status": "modified"}
    assert _refusal(files=files) is not None


@pytest.mark.parametrize("status", ["renamed", "removed", "copied", "changed", None])
def test_any_status_but_added_or_modified_refuses(status):
    assert _refusal(files=_files(status=status)) is not None


def test_a_rename_into_harness_results_refuses():
    """A rename lists only its NEW path in `gh pr view`; the REST row also carries the old one."""
    assert _refusal(files=_files(status="modified", previous_filename="harness/thresholds.json")) is not None


def test_a_file_count_that_disagrees_with_changed_files_refuses():
    assert "partial list" in _refusal(pr=_pr(changed_files=3))


def test_files_past_the_first_page_are_read():
    """`gh pr view --json files` stops at 100; the guard pages, so file 150 is seen."""
    files = _files(150)
    files[149] = {"filename": "src/jcodemunch_mcp/server.py", "status": "modified"}
    assert _refusal(pr=_pr(changed_files=150), files=files) is not None
    clean = _files(150)
    assert _refusal(pr=_pr(changed_files=150), files=clean) is None


@pytest.mark.parametrize("count", [0, 301, None, "2"])
def test_an_uncheckable_changed_files_count_refuses(count):
    assert _refusal(pr=_pr(changed_files=count)) is not None


@pytest.mark.parametrize("answer", [None, [], "x", 3])
def test_a_non_object_pr_answer_refuses(answer):
    def api(path):
        return answer

    assert deny_guard.results_auto_merge_refusal(_CMD, api=api) is not None


@pytest.mark.parametrize("files", [None, {}, "x"])
def test_a_non_list_file_answer_refuses(files):
    def api(path):
        return _pr() if path.endswith("pulls/1030") else files

    assert deny_guard.results_auto_merge_refusal(_CMD, api=api) is not None


def test_file_rows_that_are_not_objects_refuse():
    assert _refusal(files=["harness/results/latest.json", "harness/results/self_latency.json"]) is not None


@pytest.mark.parametrize("exc", [RuntimeError("gh failed"), subprocess.TimeoutExpired("gh", 8), ValueError("bad json"), KeyError("x")])
def test_an_api_failure_refuses_and_never_raises(exc):
    def api(path):
        raise exc

    assert "could not verify" in deny_guard.results_auto_merge_refusal(_CMD, api=api)


def test_the_gh_timeout_sits_inside_the_hook_budget():
    """A hook the runner kills is neither ok nor block, so the runner reads it as consent (W-42)."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    budget = next(
        h["timeout"] for entries in settings["hooks"].values() for e in entries for h in e["hooks"] if "deny_guard.py" in h["command"]
    )
    import re

    common = (HOOKS / "_common.py").read_text(encoding="utf-8")
    rebind = common[common.index("def _rebind_repo"):]
    rebind_s = int(re.search(r"timeout=(\d+)", rebind).group(1))  # read_hook_input runs it first
    worst = rebind_s + deny_guard._GH_TIMEOUT_S * (1 + deny_guard._MAX_FILE_PAGES)
    assert worst + 5 < budget, (worst, budget)  # 5 s for the interpreter and the imports


@pytest.mark.parametrize(
    "cmd",
    ["git push origin harness-bot/results-2026-10-19", "git push origin HEAD:harness-bot/results-2026-10-19",
     "git push -u origin harness-bot/results-2026-10-19", "gh alias set m 'pr merge'", "gh alias import x.yml"],
)
def test_a_push_to_a_results_branch_and_a_gh_alias_are_refused(cmd):
    import re

    assert any(re.search(p, cmd, re.I) for p, _ in deny_guard.DENIED), cmd


@pytest.mark.parametrize(
    "cmd",
    ["gh api --method=PUT repos/x/y/pulls/5/merge", "gh api --method PUT repos/x/y/pulls/5/merge",
     "gh api -XPUT repos/x/y/pulls/5/merge", "gh api graphql -F query=@m.graphql", "gh api graphql --input q.json"],
)
def test_api_merge_spellings_and_a_graphql_query_from_a_file_are_refused(cmd):
    import re

    assert any(re.search(p, cmd, re.I) for p, _ in deny_guard.DENIED), cmd


def test_the_api_and_graphql_merges_stay_unconditional():
    patterns = [p for p, _ in deny_guard.DENIED]
    assert any("/pulls/" in p and "merge" in p for p in patterns)
    assert any("mergePullRequest" in p for p in patterns)


def _run_hook(cmd: str, env_path: str | None = None) -> int:
    env = dict(os.environ)
    if env_path is not None:
        env["PATH"] = env_path
    r = subprocess.run(
        [sys.executable, str(HOOKS / "deny_guard.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(ROOT)}),
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env, timeout=60,
    )
    return r.returncode


def test_the_hook_process_refuses_a_plain_merge():
    assert _run_hook(f"{_VERB} 5 --squash") == 2


def test_the_hook_process_refuses_the_allowed_form_when_gh_cannot_run():
    """End to end through main(): with no `gh` on PATH the check cannot be made, and the hook exits 2."""
    py_dir = str(Path(sys.executable).parent)
    assert _run_hook(_CMD, env_path=py_dir) == 2


def test_a_crashed_guard_refuses():
    """`exit 1` lets the command run; the __main__ wrapper turns any crash into `block` (exit 2)."""
    code = (
        "import sys, runpy; sys.path.insert(0, r'%s'); import _common; "
        "_common.tool_command = lambda p: (_ for _ in ()).throw(RuntimeError('boom')); "
        "runpy.run_path(r'%s', run_name='__main__')" % (HOOKS, HOOKS / "deny_guard.py")
    )
    r = subprocess.run(
        [sys.executable, "-c", code], input=json.dumps({"tool_input": {"command": "echo hi"}}),
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60,
    )
    assert r.returncode == 2, (r.returncode, r.stderr)


def _main_exit(monkeypatch, cmd, refusal):
    monkeypatch.setattr(deny_guard, "read_hook_input", lambda: {"tool_input": {"command": cmd}})
    monkeypatch.setattr(deny_guard, "results_auto_merge_refusal", lambda c: refusal)
    with pytest.raises(SystemExit) as stop:
        deny_guard.main()
    return stop.value.code


def test_main_lets_the_checked_merge_through_when_the_rule_allows_it(monkeypatch):
    """The wiring itself: delete the `continue` in main() and this turns red."""
    assert _main_exit(monkeypatch, _CMD, None) == 0


def test_main_blocks_the_merge_when_the_rule_refuses(monkeypatch):
    assert _main_exit(monkeypatch, _CMD, "no") == 2


def test_main_still_blocks_another_refused_verb_on_an_allowed_merge_line(monkeypatch):
    """A merge the rule allows does not excuse a second refused act on the same line."""
    assert _main_exit(monkeypatch, f"{_CMD} ; gh workflow run x.yml", None) == 2


def test_a_guard_that_cannot_load_refuses(tmp_path):
    """An import failure used to exit 1, which lets the command run."""
    lone = tmp_path / "deny_guard.py"
    lone.write_text((HOOKS / "deny_guard.py").read_text(encoding="utf-8"), encoding="utf-8")
    r = subprocess.run([sys.executable, str(lone)], input="{}", capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 2, (r.returncode, r.stderr)
