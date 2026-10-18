"""deny_guard merges only a cleared test/doc-only PR of ours (jjg, 2026-10-16 and 2026-10-17).

jjg ruled that the session may merge its OWN PRs that change only tests or docs
once the Chief of Staff has cleared them; product code, contributor PRs and
releases stay with him. `cleared_merge_refusal` is that rule. ⚠ The load-bearing
tests are the refusals: each condition is removed on its own and the merge must
be refused for it. The one allowed case is the control.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))

import _common  # noqa: E402
import deny_guard  # noqa: E402

_HEAD = "a" * 40
_VERB = "gh pr " + "merge"  # split so no shell line ever carries the verb literally


def _pr(**over) -> dict:
    pr = {
        "author": {"login": "jgravelle"},
        "headRefOid": _HEAD,
        "mergeStateStatus": "CLEAN",
        "files": [{"path": "tests/test_x.py"}, {"path": "docs/workflows/LEDGER.md"}, {"path": "CHANGELOG.md"}],
    }
    pr.update(over)
    return pr


@pytest.fixture
def cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(_common, "STATE", tmp_path)
    (tmp_path / "cleared").mkdir()
    (tmp_path / "cleared" / "1127.json").write_text(json.dumps({"head": _HEAD, "cleared_by": "Chief of Staff"}), encoding="utf-8")
    return tmp_path


def _refusal(cmd, pr):
    return deny_guard.cleared_merge_refusal(cmd, gh=lambda n: pr)


def test_a_cleared_test_and_doc_only_pr_of_ours_may_merge(cleared):
    assert _refusal(f"{_VERB} 1127 --squash", _pr()) is None


def test_the_token_prefix_form_may_merge(cleared):
    assert _refusal(f'GITHUB_TOKEN="" {_VERB} 1127 --squash', _pr()) is None


def test_no_clearance_record_refuses(cleared):
    assert "no clearance record" in _refusal(f"{_VERB} 1128 --squash", _pr())


def test_a_head_moved_since_clearance_refuses(cleared):
    assert "head moved" in _refusal(f"{_VERB} 1127 --squash", _pr(headRefOid="b" * 40))


@pytest.mark.parametrize("state", ["BLOCKED", "BEHIND", "DIRTY", "UNKNOWN", None])
def test_a_pr_that_is_not_clean_refuses(cleared, state):
    assert "not CLEAN" in _refusal(f"{_VERB} 1127 --squash", _pr(mergeStateStatus=state))


def test_a_contributor_pr_refuses(cleared):
    assert "not ours" in _refusal(f"{_VERB} 1127 --squash", _pr(author={"login": "lilubot"}))


@pytest.mark.parametrize(
    "path",
    ["src/jcodemunch_mcp/config.py", ".claude/hooks/deny_guard.py", ".claude/settings.json", ".github/workflows/pr-gate.yml",
     "harness/thresholds.json", "pyproject.toml", "uv.lock", "server.json", "scripts/x.py", "benchmarks/tasks.json"],
)
def test_any_file_outside_tests_and_docs_refuses(cleared, path):
    files = _pr()["files"] + [{"path": path}]
    assert "more than tests and docs" in _refusal(f"{_VERB} 1127 --squash", _pr(files=files))


def test_an_empty_file_list_refuses(cleared):
    assert "more than tests and docs" in _refusal(f"{_VERB} 1127 --squash", _pr(files=[]))


@pytest.mark.parametrize(
    "cmd",
    [
        f"{_VERB} 1127",
        f"{_VERB} 1127 --squash --admin",
        f"{_VERB} 1127 --squash --auto",
        f"{_VERB} 1127 --merge",
        f"{_VERB} 1127 --squash && echo done",
        f"{_VERB} 1127 --squash; git push",
        f"cd x && {_VERB} 1127 --squash",
    ],
)
def test_any_other_form_refuses(cleared, cmd):
    assert "only" in _refusal(cmd, _pr())


def test_an_unreadable_pr_refuses(cleared):
    def boom(n):
        raise RuntimeError("gh failed")

    assert "could not read" in deny_guard.cleared_merge_refusal(f"{_VERB} 1127 --squash", gh=boom)


def test_the_api_and_graphql_merges_are_still_refused_unconditionally():
    """Only the CLI form has a conditional pass; the REST and GraphQL merge rules are untouched."""
    patterns = [p for p, _ in deny_guard.DENIED]
    assert any("/pulls/" in p and "merge" in p for p in patterns)
    assert any("mergePullRequest" in p for p in patterns)


# --- the second allowed merge: auto-merge on a main.yml harness-results PR (jjg 2026-10-17) ---


def _results_pr(**over) -> dict:
    pr = {
        "author": {"login": "app/github-actions", "is_bot": True},
        "headRefName": "harness-bot/results-2026-10-19",
        "files": [{"path": "harness/results/latest.json"}, {"path": "harness/results/self_latency.json"}],
    }
    pr.update(over)
    return pr


def _results_refusal(cmd, pr):
    return deny_guard.results_auto_merge_refusal(cmd, gh=lambda n: pr)


def test_auto_merge_may_be_enabled_on_a_results_pr():
    assert _results_refusal(f"{_VERB} 1030 --squash --auto", _results_pr()) is None


def test_auto_merge_with_the_token_prefix_may_be_enabled():
    assert _results_refusal(f'GITHUB_TOKEN="" {_VERB} 1030 --squash --auto', _results_pr()) is None


@pytest.mark.parametrize("login", ["jgravelle", "lilubot", "app/dependabot", "github-actions", ""])
def test_a_results_auto_merge_from_any_other_author_refuses(login):
    assert "not a main.yml results PR" in _results_refusal(f"{_VERB} 1030 --squash --auto", _results_pr(author={"login": login}))


@pytest.mark.parametrize("branch", ["harness-bot/results-latest", "feat/results-2026-10-19", "harness-bot/results-2026-10-19-x", "", None])
def test_a_results_auto_merge_on_another_branch_refuses(branch):
    assert "is not harness-bot/results-<date>" in _results_refusal(f"{_VERB} 1030 --squash --auto", _results_pr(headRefName=branch))


@pytest.mark.parametrize(
    "path",
    ["harness/thresholds.json", "harness/results/sub/x.json", "harness/results/latest.txt", "src/jcodemunch_mcp/server.py",
     ".github/workflows/main.yml", ".claude/hooks/deny_guard.py", "tests/test_x.py", "CHANGELOG.md"],
)
def test_a_results_auto_merge_touching_anything_else_refuses(path):
    files = _results_pr()["files"] + [{"path": path}]
    assert "changes more than harness/results/*.json" in _results_refusal(f"{_VERB} 1030 --squash --auto", _results_pr(files=files))


def test_a_results_auto_merge_with_no_files_refuses():
    assert "changes more than" in _results_refusal(f"{_VERB} 1030 --squash --auto", _results_pr(files=[]))


@pytest.mark.parametrize(
    "cmd",
    [f"{_VERB} 1030 --auto", f"{_VERB} 1030 --squash --auto --admin", f"{_VERB} 1030 --merge --auto",
     f"{_VERB} 1030 --squash --auto && echo x", f"{_VERB} 1030 --auto --squash"],
)
def test_any_other_auto_form_refuses(cmd):
    assert deny_guard.merge_refusal(cmd) is not None


def test_a_plain_squash_of_a_results_pr_is_not_the_auto_case():
    """Without `--auto` the line is a cleared-merge attempt, which needs our authorship and a clearance record."""
    refusal = deny_guard.cleared_merge_refusal(f"{_VERB} 1030 --squash", gh=lambda n: _results_pr())
    assert refusal is not None


def test_the_dispatcher_routes_the_auto_form_to_the_results_rule(monkeypatch):
    seen = []
    monkeypatch.setattr(deny_guard, "results_auto_merge_refusal", lambda cmd, gh=None: seen.append(cmd) or None)
    assert deny_guard.merge_refusal(f"{_VERB} 1030 --squash --auto") is None and seen


def test_an_unreadable_results_pr_refuses():
    def boom(n):
        raise RuntimeError("gh failed")

    assert "could not read" in deny_guard.results_auto_merge_refusal(f"{_VERB} 1030 --squash --auto", gh=boom)
