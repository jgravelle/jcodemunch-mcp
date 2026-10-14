"""`scripts/release_preflight.py` refuses on the cases it exists for.

ENFORCEMENT-PLAN item 3: four releases shipped on a RED build because the
release step trusted a local run. The pre-flight reads CI. These tests cover
its pure verdicts with data, never `gh`, and each one has a red arm: a check
that only passes against a good input can be deleted (Standing lessons).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "release_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("_release_preflight", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


pf = _load()


def _runs(**conclusions):
    return [{"name": n, "conclusion": c} for n, c in conclusions.items()]


WITNESSES_OK = {
    "main: harness full (ubuntu, 3.12)": "success",
    "main: harness bench (online)": "success",
    "codeql (python)": "success",
}


def test_ci_passes_when_the_main_witnesses_succeeded():
    ok, pending, msg = pf.main_witness_verdict(_runs(**WITNESSES_OK))
    assert ok and pending is None, msg


def test_ci_ignores_the_release_workflows_own_running_jobs():
    runs = _runs(**WITNESSES_OK) + [
        {"name": "release: pre-flight", "conclusion": None, "status": "in_progress"}
    ]
    ok, pending, msg = pf.main_witness_verdict(runs)
    assert ok, msg


def test_ci_fails_on_a_failed_run_of_any_name():
    ok, pending, msg = pf.main_witness_verdict(
        _runs(**{**WITNESSES_OK, "codeql (python)": "failure"})
    )
    assert not ok and pending is None and "codeql (python): failure" in msg


def test_ci_fails_when_a_witness_is_absent():
    """A renamed main.yml job silently stops being a witness; this is the only place that notices."""
    ok, pending, msg = pf.main_witness_verdict(
        _runs(**{k: v for k, v in WITNESSES_OK.items() if "bench" not in k})
    )
    assert not ok and "witness absent" in msg and "main: harness bench (online)" in msg


def test_ci_reports_a_running_witness_as_pending_not_pass():
    runs = _runs(**{k: v for k, v in WITNESSES_OK.items() if "bench" not in k}) + [
        {
            "name": "main: harness bench (online)",
            "conclusion": None,
            "status": "in_progress",
        }
    ]
    ok, pending, msg = pf.main_witness_verdict(runs)
    assert (
        not ok and pending == "main: harness bench (online)" and "still running" in msg
    )


def test_ci_fails_with_no_runs_at_all():
    ok, pending, msg = pf.main_witness_verdict([])
    assert not ok and pending is None and "no check-runs" in msg
    ok, pending, msg = pf.main_witness_verdict(
        [{"name": "release: build", "conclusion": None, "status": "queued"}]
    )
    assert not ok and "no check-runs" in msg


def test_live_pin_sites_all_agree():
    pins = pf.read_pins(REPO)
    assert len(pins) == 7, sorted(pins)
    ok, msg = pf.pins_verdict(pins, None)
    assert ok, msg


def test_pins_fail_when_one_site_lags(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "jcodemunch-mcp"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    (tmp_path / "server.json").write_text(
        json.dumps({"version": "9.9.9", "packages": [{"version": "9.9.8"}]}),
        encoding="utf-8",
    )
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )
    (tmp_path / "whatsnew.json").write_text(
        json.dumps({"current": "9.9.9", "entries": [{"version": "9.9.9"}]}),
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "jcodemunch-mcp"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    ok, msg = pf.pins_verdict(pf.read_pins(tmp_path), None)
    assert not ok and "packages[0].version=9.9.8" in msg


def test_pins_fail_when_a_site_is_unreadable(tmp_path):
    ok, msg = pf.pins_verdict(pf.read_pins(tmp_path), None)
    assert not ok and "unreadable" in msg


def test_pins_must_equal_the_requested_version():
    pins = pf.read_pins(REPO)
    ok, msg = pf.pins_verdict(pins, "0.0.0")
    assert not ok and "--version says 0.0.0" in msg


def test_changelog_heading_detection():
    text = "## [Unreleased]\n\n## [1.2.3] - 2026-01-01 - title\n"
    assert pf.changelog_has("1.2.3", text)
    assert not pf.changelog_has("1.2.4", text)
    assert not pf.changelog_has("1.2", text), "a prefix is not a heading"


def test_only_a_mergeable_clean_contributor_pr_blocks():
    prs = [
        {
            "number": 1,
            "author": {"login": "someone"},
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
        },
        {
            "number": 2,
            "author": {"login": "someone"},
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
        },
        {
            "number": 3,
            "author": {"login": "someone"},
            "mergeable": "CONFLICTING",
            "mergeStateStatus": "DIRTY",
        },
        {
            "number": 4,
            "author": {"login": pf.OWNER},
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
        },
    ]
    assert pf.mergeable_contributor_prs(prs) == ["#1 someone"]


# ---- policy 3b: a PR the maintainer has asked to change does not hold a release
#
# The gate refused every release while a contributor PR read MERGEABLE CLEAN, and
# CLEAN says nothing about review: a PR with a real defect and a posted
# changes-requested review held our release until the contributor revised it,
# the coupling policy 2e forbids. The hold is lifted only while the maintainer's
# LATEST review requests changes ON THE CURRENT HEAD; a new push puts the PR back
# in line, because then it may be ready.

HEAD = "a" * 40


def _review(state, commit=HEAD, login=None, at="2026-10-14T20:00:00Z"):
    return {"user": {"login": login or pf.OWNER}, "state": state, "commit_id": commit, "submitted_at": at}


def _lines(reviews):
    """What `gh api --paginate --jq '.[]'` prints: one review per line, every page."""
    return NL.join(json.dumps(r) for r in reviews)


NL = chr(10)


def test_changes_requested_on_the_head_releases_the_hold():
    assert pf.changes_requested_on_head([_review("CHANGES_REQUESTED")], HEAD) is True


def test_a_push_after_the_review_puts_the_pr_back_in_line():
    assert pf.changes_requested_on_head([_review("CHANGES_REQUESTED", commit="b" * 40)], HEAD) is False


def test_a_later_approval_or_dismissal_ends_the_request():
    for later in ("APPROVED", "DISMISSED"):
        reviews = [_review("CHANGES_REQUESTED", at="2026-10-14T20:00:00Z"), _review(later, at="2026-10-14T21:00:00Z")]
        assert pf.changes_requested_on_head(reviews, HEAD) is False, later


def test_a_later_comment_does_not_end_the_request():
    reviews = [_review("CHANGES_REQUESTED", at="2026-10-14T20:00:00Z"), _review("COMMENTED", at="2026-10-14T21:00:00Z")]
    assert pf.changes_requested_on_head(reviews, HEAD) is True


def test_only_the_maintainer_can_lift_the_hold():
    assert pf.changes_requested_on_head([_review("CHANGES_REQUESTED", login="someone-else")], HEAD) is False


def test_no_reviews_holds_as_before():
    assert pf.changes_requested_on_head([], HEAD) is False


def test_check_prs_names_a_held_pr_and_passes(monkeypatch):
    prs = [{"number": 1080, "author": {"login": "someone"}, "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "headRefOid": HEAD}]
    calls = []

    def fake_run(cmd, gh=False):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(prs)
        return 0, _lines([_review("CHANGES_REQUESTED")])

    monkeypatch.setattr(pf, "_run", fake_run)
    ok, msg = pf.check_prs()
    assert ok is True, msg
    assert "#1080" in msg and "changes requested" in msg
    review_call = next(c for c in calls if any("pulls/1080/reviews" in part for part in c))
    assert f"repos/{pf.SLUG}/pulls/1080/reviews" in review_call[2]
    assert "--paginate" in review_call, "a PR with over 100 reviews would lose its newest decision"


def test_check_prs_still_refuses_a_ready_pr(monkeypatch):
    prs = [{"number": 1081, "author": {"login": "someone"}, "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "headRefOid": HEAD}]

    def fake_run(cmd, gh=False):
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(prs)
        return 0, _lines([])

    monkeypatch.setattr(pf, "_run", fake_run)
    ok, msg = pf.check_prs()
    assert ok is False and "#1081" in msg


def test_unreadable_reviews_hold_the_release(monkeypatch):
    """Could not ask is not 'changes requested': the PR keeps holding."""
    prs = [{"number": 1082, "author": {"login": "someone"}, "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "headRefOid": HEAD}]

    def fake_run(cmd, gh=False):
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(prs)
        return 1, "HTTP 502"

    monkeypatch.setattr(pf, "_run", fake_run)
    ok, msg = pf.check_prs()
    assert ok is False and "#1082" in msg


def test_a_dismissal_rewrites_the_review_in_place():
    """GitHub keeps a dismissed review's commit and time and changes its state."""
    assert pf.changes_requested_on_head([_review("DISMISSED")], HEAD) is False


def test_the_newest_decision_on_a_later_page_wins(monkeypatch):
    """Reviews arrive oldest first across pages; an approval on page two ends the request."""
    prs = [{"number": 1083, "author": {"login": "someone"}, "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "headRefOid": HEAD}]
    page_one = [_review("COMMENTED", at=f"2026-10-14T10:{i:02d}:00Z") for i in range(59)]
    page_one.append(_review("CHANGES_REQUESTED", at="2026-10-14T11:00:00Z"))
    page_two = [_review("APPROVED", at="2026-10-14T12:00:00Z")]

    def fake_run(cmd, gh=False):
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(prs)
        return 0, _lines(page_one) + NL + _lines(page_two)

    monkeypatch.setattr(pf, "_run", fake_run)
    ok, msg = pf.check_prs()
    assert ok is False and "#1083" in msg


def test_a_malformed_reviews_body_holds_the_release(monkeypatch):
    prs = [{"number": 1084, "author": {"login": "someone"}, "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "headRefOid": HEAD}]

    def fake_run(cmd, gh=False):
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(prs)
        return 0, "<html>rate limited</html>"

    monkeypatch.setattr(pf, "_run", fake_run)
    ok, msg = pf.check_prs()
    assert ok is False and "#1084" in msg
