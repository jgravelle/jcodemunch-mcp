"""Two safeguards before the inbound App may start runs (jjg, DECISIONS 2026-10-18).

jjg ruled to give the `jcodemunch-inbound` App permission to start runs, so
the weekly competitive feed can dispatch `competitive-run.yml`, and to build
two safeguards first. Actions write on the App would otherwise let ANY
workflow holding its token start ANY workflow, `release.yml` with
`dry_run=false` included; the `pypi` environment has a wait timer and no
required reviewer.

1. `release.yml` runs only when jgravelle triggers it: the first step of EVERY
   job refuses any other `github.triggering_actor`. The pre-flight alone is not
   enough: "re-run failed jobs" skips the pre-flight's steps, and the re-run's
   triggering actor is whoever pressed it.
2. Every App token names only the permissions its job uses. Only one token
   carries `actions: write`: the competitive feed's dispatch token, minted only
   when there is a run to start and held by one step.

The scope table below is the contract. A job that needs a new write changes
the table here and its workflow in the same diff, so the widening is reviewed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

APP_SCOPES = {
    ("competitive-feed.yml", "feed"): {"contents": "write"},
    ("competitive-post.yml", "post"): {"contents": "write", "issues": "write"},
    ("competitive-run.yml", "ledger"): {"contents": "write"},
    ("inbound-bench-full.yml", "apply"): {"issues": "write", "pull-requests": "write"},
    ("inbound-depeval.yml", "apply"): {"issues": "write", "pull-requests": "write"},
    ("inbound-digest.yml", "apply"): {"issues": "write"},
    ("inbound-fix-promote.yml", "promote"): {"issues": "write", "pull-requests": "write"},
    ("inbound-fix.yml", "preflight"): {"issues": "write"},
    ("inbound-fix.yml", "publish"): {"contents": "write", "issues": "write", "pull-requests": "write"},
    ("inbound-sweep.yml", "sweep"): {"contents": "write", "issues": "write", "pull-requests": "write"},
    ("inbound-triage.yml", "apply"): {"issues": "write"},
}
SWITCH_SCOPE = {"actions-variables": "read"}
DISPATCH_SCOPE = {"actions": "write"}
GUARD_NAME = "A release runs only when jgravelle triggers it"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _token_steps():
    for wf in WORKFLOWS:
        for job_name, job in (_load(wf).get("jobs") or {}).items():
            steps = job.get("steps") or []
            for step in steps:
                if "create-github-app-token" in str(step.get("uses", "")):
                    yield wf.name, job_name, step, steps


def _perms(step) -> dict:
    return {
        k[len("permission-"):]: v
        for k, v in (step.get("with") or {}).items()
        if k.startswith("permission-")
    }


def _users(step_id: str, steps: list) -> list:
    ref = f"steps.{step_id}.outputs.token"
    return [s for s in steps if ref in yaml.safe_dump(s)]


def test_there_are_tokens_to_check():
    """An empty walk would pass every test below vacuously."""
    ids = [s.get("id") for _, _, s, _ in _token_steps()]
    assert ids.count("app") == len(APP_SCOPES), ids
    assert ids.count("switchtok") >= 10 and ids.count("dispatch") == 1, ids


def test_every_token_step_is_a_known_kind():
    """A new token step must be one of the three kinds, or this file must learn a fourth on purpose."""
    for wf, job, step, _ in _token_steps():
        assert step.get("id") in {"app", "switchtok", "dispatch"}, (wf, job, step.get("id"))


def test_every_app_token_names_exactly_the_writes_its_job_makes():
    found = {}
    for wf, job, step, _ in _token_steps():
        if step.get("id") == "app":
            found[(wf, job)] = _perms(step)
    assert found == APP_SCOPES


def test_every_switch_token_reads_variables_and_nothing_else():
    for wf, job, step, _ in _token_steps():
        if step.get("id") == "switchtok":
            assert _perms(step) == SWITCH_SCOPE, (wf, job)


def test_a_token_that_names_no_permission_does_not_exist():
    """A step with no permission-* input inherits everything the App holds, Actions write included."""
    for wf, job, step, _ in _token_steps():
        assert _perms(step), (wf, job, step.get("id"))


def test_only_the_feed_dispatch_token_may_start_a_run():
    carriers = [
        (wf, job, step.get("id")) for wf, job, step, _ in _token_steps()
        if "actions" in _perms(step)
    ]
    assert carriers == [("competitive-feed.yml", "feed", "dispatch")], carriers


def test_the_dispatch_token_is_minted_only_for_a_run_and_held_by_one_step():
    (step, steps), = [(s, st) for wf, _, s, st in _token_steps() if s.get("id") == "dispatch"]
    assert _perms(step) == DISPATCH_SCOPE
    assert "steps.reason.outputs.reason != ''" in str(step.get("if", ""))
    users = _users("dispatch", steps)
    assert len(users) == 1, [u.get("name") for u in users]
    run = str(users[0].get("run", ""))
    assert "gh workflow run competitive-run.yml" in run
    assert run.count("gh ") == 1, "the dispatch token runs one gh command"
    assert "checkout" not in str(users[0].get("uses", "")), "it must never be persisted into git config"


def test_a_kill_switch_token_reaches_only_the_kill_switch_script():
    for wf, job, step, steps in _token_steps():
        if step.get("id") != "switchtok":
            continue
        users = _users("switchtok", steps)
        assert users, (wf, job, "switchtok is never used")
        for user in users:
            run = str(user.get("run", ""))
            assert "killswitch.py" in run, (wf, job, user.get("name"))
            assert "gh workflow" not in run and "gh api" not in run, (wf, job, user.get("name"))


@pytest.fixture(scope="module")
def release():
    return _load(ROOT / ".github" / "workflows" / "release.yml")


def test_every_release_job_refuses_any_actor_but_jgravelle_first(release):
    jobs = release["jobs"]
    assert len(jobs) >= 9, list(jobs)
    for name, job in jobs.items():
        first = job["steps"][0]
        assert first.get("name") == GUARD_NAME, name
        assert first.get("shell") == "bash", (name, "a windows-latest leg runs an unset shell as pwsh")
        assert first.get("env") == {"ACTOR": "${{ github.triggering_actor }}"}, name
        run = first["run"]
        assert '"$ACTOR" != "jgravelle"' in run and "exit 1" in run, name
        assert "${{" not in run, (name, "the actor must reach the script through env, never interpolated")
        assert "if" not in first, (name, "the guard must run on every trigger, re-runs included")
        assert "continue-on-error" not in first, name
        assert "|| true" not in run and "set +e" not in run, name


def test_every_release_job_waits_for_the_preflight(release):
    for name, job in release["jobs"].items():
        if name == "preflight":
            continue
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        assert "preflight" in needs, (name, needs)


def test_the_feed_refuses_a_reason_that_could_forge_an_output_line():
    """The reason reaches GITHUB_OUTPUT; a newline in it would add a key. Match the whole string, never per line."""
    doc = _load(ROOT / ".github" / "workflows" / "competitive-feed.yml")
    (step,) = [s for s in doc["jobs"]["feed"]["steps"] if s.get("id") == "reason"]
    run = step["run"]
    assert "PATTERN='^release:[A-Za-z0-9._-]+@[A-Za-z0-9.+_-]+$'" in run
    assert '[[ "$REASON" =~ $PATTERN ]]' in run and "exit 1" in run
    assert "grep" not in run, "grep matches line by line, so a two-line value passes"
    assert run.index("=~") < run.index("GITHUB_OUTPUT")
