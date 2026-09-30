"""The release-cadence gate: an unreleased change may not wait past the Floor.

jjg, 2026-09-30, after 1.108.320 shipped 14 days and 124 PRs behind
1.108.319 because no step in the fix loop asked whether it was time to ship
(Standing lesson 09-30). `release.unreleased_max_hours` (criterion N8) fails
the fast tier when `[Unreleased]` has held an entry longer than the Floor.

Every case builds its own git repository under `tmp_path` and passes `now`,
`env` and `ref` explicitly: the gate reads the clock, the environment and the
checkout, and a test that inherited any of the three would be measuring this
box (Standing lesson 09-04, a default bound at import pins the wrong repo).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harness import release_age as RA
from harness import thresholds as T

T0 = 1_780_000_000  # an arbitrary fixed epoch; only differences matter
HOUR = 3600

_EMPTY = "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01 - first\n\n- old\n"


def _with(*entries: str) -> str:
    body = "".join(f"- {e}\n" for e in entries)
    return f"# Changelog\n\n## [Unreleased]\n\n{body}\n## [1.0.0] - 2026-01-01 - first\n\n- old\n"


def _git(root: Path, *args: str, when: int | None = None) -> str:
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    if when is not None:
        stamp = f"@{when} +0000"
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    out = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _commit(
    root: Path, text: str | None, when: int, msg: str, other: str | None = None
) -> str:
    if text is not None:
        (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
    if other is not None:
        (root / "other.txt").write_text(other, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", msg, when=when)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, _EMPTY, T0, "init")
    return root


def test_an_empty_block_is_zero(repo: Path) -> None:
    age = RA.measure(repo, ref="HEAD", now=T0 + 50 * HOUR)
    assert age.hours == 0.0
    assert age.applicable


def test_the_age_runs_from_the_commit_that_added_the_first_entry(repo: Path) -> None:
    first = _commit(repo, _with("a"), T0 + HOUR, "fix a")
    _commit(repo, _with("a", "b"), T0 + 2 * HOUR, "fix b")
    age = RA.measure(repo, ref="HEAD", now=T0 + 4 * HOUR)
    assert age.commit == first
    assert age.hours == pytest.approx(3.0)


def test_a_release_restarts_the_clock(repo: Path) -> None:
    _commit(repo, _with("a"), T0 + HOUR, "fix a")
    _commit(repo, _EMPTY, T0 + 2 * HOUR, "release")
    fresh = _commit(repo, _with("c"), T0 + 10 * HOUR, "fix c")
    age = RA.measure(repo, ref="HEAD", now=T0 + 11 * HOUR)
    assert age.commit == fresh
    assert age.hours == pytest.approx(1.0)


def test_a_commit_that_does_not_touch_the_changelog_moves_nothing(repo: Path) -> None:
    first = _commit(repo, _with("a"), T0 + HOUR, "fix a")
    _commit(repo, None, T0 + 5 * HOUR, "unrelated", other="x")
    age = RA.measure(repo, ref="HEAD", now=T0 + 6 * HOUR)
    assert age.commit == first
    assert age.hours == pytest.approx(5.0)


_CUT = (
    "# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-01-02 - second\n\n- a\n\n"
    "## [1.0.0] - 2026-01-01 - first\n\n- old\n"
)


def test_a_release_cut_in_the_working_tree_reads_zero(repo: Path) -> None:
    """The release commit empties the block under a new version heading; never refused."""
    _commit(repo, _with("a"), T0 + HOUR, "fix a")
    (repo / "CHANGELOG.md").write_text(_CUT, encoding="utf-8")
    age = RA.measure(repo, ref="HEAD", now=T0 + 40 * HOUR)
    assert age.hours == 0.0
    assert "release cut" in age.basis


def test_an_empty_tree_that_is_not_a_release_does_not_hide_the_ref(repo: Path) -> None:
    """Review probe: a branch cut before main's entry has an empty block and is no release."""
    _commit(repo, _with("a"), T0 + HOUR, "fix a on main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "topic", "HEAD~1")
    age = RA.measure(repo, ref="origin/main", now=T0 + 50 * HOUR)
    assert age.hours == pytest.approx(49.0)
    assert RA.verdict(age) is False


def test_an_unreadable_ref_is_unknown_never_empty(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text(_with("a"), encoding="utf-8")
    age = RA.measure(repo, ref="no-such-ref", now=T0 + 50 * HOUR)
    assert age.hours is None
    assert RA.verdict(age) is False


def test_a_failed_parent_read_is_unknown_not_a_shorter_age(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review probe: a failed read mid-walk stopped at a newer commit and under-reported."""
    _commit(repo, _with("a"), T0 + HOUR, "fix a")
    _commit(repo, _with("a", "b"), T0 + 2 * HOUR, "fix b")
    real = RA._read
    monkeypatch.setattr(
        RA,
        "_read",
        lambda root, rev: ("error", None) if rev.endswith("^1") else real(root, rev),
    )
    age = RA.measure(repo, ref="HEAD", now=T0 + 3 * HOUR)
    assert age.hours is None
    assert RA.verdict(age) is False


def test_the_walk_stops_once_the_verdict_is_settled(repo: Path) -> None:
    """Review probe: a 14-day block walked every commit (216 git calls, 5.88 s)."""
    _commit(repo, _with("a"), T0 + HOUR, "fix a")
    _commit(repo, _with("a", "b"), T0 + 2 * HOUR, "fix b")
    newest = _commit(repo, _with("a", "b", "c"), T0 + 3 * HOUR, "fix c")
    age = RA.measure(repo, ref="HEAD", now=T0 + 30 * HOUR)
    assert age.commit == newest
    assert age.hours == pytest.approx(27.0)
    assert "at least" in age.basis
    assert RA.verdict(age) is False


def test_an_entry_that_is_not_yet_on_the_ref_reads_zero(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text(_with("new"), encoding="utf-8")
    age = RA.measure(repo, ref="HEAD", now=T0 + 40 * HOUR)
    assert age.hours == 0.0


def test_the_ref_is_measured_not_the_branch(repo: Path) -> None:
    """Locally the ref is origin/main: main's entries count, a branch's own do not."""
    old = _commit(repo, _with("a"), T0 + HOUR, "fix a on main")
    _git(repo, "branch", "mainline")
    _git(repo, "checkout", "-q", "-b", "topic")
    _commit(repo, _with("a", "mine"), T0 + 9 * HOUR, "fix on the branch")
    age = RA.measure(repo, ref="mainline", now=T0 + 10 * HOUR)
    assert age.commit == old
    assert age.hours == pytest.approx(9.0)


def test_a_shallow_history_is_unknown_and_never_a_pass(
    repo: Path, tmp_path: Path
) -> None:
    _commit(repo, _with("a"), T0 + HOUR, "fix a")
    _commit(repo, _with("a", "b"), T0 + 2 * HOUR, "fix b")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)],
        check=True,
        capture_output=True,
    )
    age = RA.measure(shallow, ref="HEAD", now=T0 + 30 * HOUR)
    assert age.hours is None
    assert age.applicable
    assert "shallow" in age.basis
    assert RA.verdict(age) is False


@pytest.mark.parametrize(
    "env, applies",
    [
        ({}, True),
        ({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request"}, True),
        ({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "schedule"}, False),
        ({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push"}, False),
        ({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch"}, False),
        ({"GITHUB_ACTIONS": "true"}, False),
    ],
)
def test_github_evaluates_pull_requests_only(env: dict, applies: bool) -> None:
    ok, reason = RA.applies(env)
    assert ok is applies
    if not applies:
        assert "not evaluated" in reason


def test_the_ref_is_the_test_merges_base_on_a_pull_request(repo: Path) -> None:
    """A PR's own entry has reached nobody; a re-run hours later must not date it."""
    pr = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request"}
    assert RA.pick_ref(repo, pr) == "HEAD"  # not a merge
    _git(repo, "checkout", "-q", "-b", "topic")
    _commit(repo, _with("mine"), T0 + HOUR, "fix on the branch")
    _git(repo, "checkout", "-q", "main")
    _git(
        repo, "merge", "-q", "--no-ff", "-m", "test merge", "topic", when=T0 + 2 * HOUR
    )
    assert RA.pick_ref(repo, pr) == "HEAD^1"
    age = RA.measure(repo, now=T0 + 20 * HOUR, env=pr)
    assert age.hours == 0.0


def test_locally_the_upstream_is_found_by_url_not_by_the_name_origin(
    repo: Path,
) -> None:
    assert RA.pick_ref(repo, {}) == "HEAD"  # no remote in the scratch repo
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert RA.pick_ref(repo, {}) == "origin/main"
    _git(
        repo, "remote", "add", "origin", "https://github.com/someone/jcodemunch-mcp.git"
    )
    _git(
        repo,
        "remote",
        "add",
        "upstream",
        "https://github.com/jgravelle/jcodemunch-mcp.git",
    )
    _git(repo, "update-ref", "refs/remotes/upstream/main", "HEAD")
    assert RA.pick_ref(repo, {}) == "upstream/main"


def test_an_owner_that_merely_ends_in_the_name_is_not_upstream(repo: Path) -> None:
    """Review round 2: `xjgravelle/jcodemunch-mcp` ends with the slug and is someone else."""
    _git(
        repo,
        "remote",
        "add",
        "lookalike",
        "https://github.com/xjgravelle/jcodemunch-mcp",
    )
    _git(repo, "update-ref", "refs/remotes/lookalike/main", "HEAD")
    assert RA.pick_ref(repo, {}) == "HEAD"
    _git(repo, "remote", "add", "ssh", "git@github.com:jgravelle/jcodemunch-mcp.git")
    _git(repo, "update-ref", "refs/remotes/ssh/main", "HEAD")
    assert RA.pick_ref(repo, {}) == "ssh/main"


def test_a_failed_parent_listing_is_unknown_not_a_root_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2: a git error on `^1` read as a root commit and shortened the age."""
    _commit(repo, _with("a"), T0 + HOUR, "fix a")
    _commit(repo, _with("a", "b"), T0 + 2 * HOUR, "fix b")
    real = RA._git

    def flaky(root, *args):
        if args[:2] == ("rev-list", "--parents"):
            return 128, ""
        return real(root, *args)

    monkeypatch.setattr(RA, "_git", flaky)
    age = RA.measure(repo, ref="HEAD", now=T0 + 3 * HOUR)
    assert age.hours is None
    assert RA.verdict(age) is False


def test_a_date_far_in_the_future_is_unknown_and_seconds_of_skew_are_not(
    repo: Path,
) -> None:
    _commit(repo, _with("a"), T0 + 10 * HOUR, "fix a")
    far = RA.measure(repo, ref="HEAD", now=T0 + 9 * HOUR)
    assert far.hours is None
    assert RA.verdict(far) is False
    near = RA.measure(repo, ref="HEAD", now=T0 + 10 * HOUR - 30)
    assert near.hours == 0.0
    assert RA.verdict(near) is True


def test_a_not_applicable_run_is_not_a_verdict(repo: Path) -> None:
    age = RA.measure(
        repo,
        now=T0,
        env={"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "schedule"},
    )
    assert age.applicable is False
    assert RA.verdict(age) is None


def test_the_floor_is_criterion_n8_and_the_standard_names_it() -> None:
    e = T.get("release.unreleased_max_hours")
    assert e["criterion"] == "N8"
    assert e["comparator"] == "<="
    std = (T.REPO_ROOT / "docs" / "standard" / "STANDARD.md").read_text(
        encoding="utf-8"
    )
    assert "### N8." in std
    assert "[`release.unreleased_max_hours`]" in std


def test_the_verdict_reads_the_floor(repo: Path) -> None:
    _commit(repo, _with("a"), T0, "fix a")
    fl = T.floor("release.unreleased_max_hours")
    under = RA.measure(repo, ref="HEAD", now=T0 + int(fl * HOUR) - 60)
    over = RA.measure(repo, ref="HEAD", now=T0 + int(fl * HOUR) + 60)
    assert RA.verdict(under) is True
    assert RA.verdict(over) is False
    assert "/release" in RA.describe(over)


def test_the_fast_tier_runs_the_gate(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Registered in the fast tier's offline checks, and a FAIL there fails the tier."""
    import harness.__main__ as hm

    late = RA.Age(
        hours=99.0, commit="abc1234", ref="origin/main", basis="test", applicable=True
    )
    monkeypatch.setattr(RA, "measure", lambda *a, **k: late)
    ok, obs = hm.check("release.unreleased_max_hours")
    assert ok is False
    assert "release.unreleased_max_hours" in capsys.readouterr().out

    skipped = RA.Age(
        hours=None,
        commit=None,
        ref=None,
        basis="not evaluated: schedule run",
        applicable=False,
    )
    monkeypatch.setattr(RA, "measure", lambda *a, **k: skipped)
    ok, obs = hm.check("release.unreleased_max_hours")
    assert ok is None
    assert "not evaluated" in capsys.readouterr().out
