"""Two safeguards before the inbound App may start runs (jjg, DECISIONS 2026-10-18).

jjg ruled to give the `jcodemunch-inbound` App permission to start runs, so
the weekly competitive feed can dispatch `competitive-run.yml`, and to build
two safeguards first. Actions write on the App would otherwise let ANY
workflow holding its token start ANY workflow, `release.yml` with
`dry_run=false` included; the `pypi` environment has a wait timer and no
required reviewer.

1. `release.yml` runs only when jgravelle triggers it: the first step of the
   first job refuses any other `github.triggering_actor`, and every job needs
   that one.
2. Every write ("app") token names its permissions, and only the competitive
   feed's carries `actions: write`.

⚠ The kill-switch ("switchtok") tokens cannot be scoped: `create-github-app-token`
has no input for the variables permission they need (checked on v3.2.0 and
the latest v3.3.0), so naming any permission would drop it. They inherit the
App's full set, Actions write included once granted. The guard on them is the
last test: their token reaches only the kill-switch script.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
APP_WRITES = {"permission-contents": "write", "permission-issues": "write", "permission-pull-requests": "write"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _token_steps():
    for wf in WORKFLOWS:
        for job_name, job in (_load(wf).get("jobs") or {}).items():
            steps = job.get("steps") or []
            for step in steps:
                if "create-github-app-token" in str(step.get("uses", "")):
                    yield wf.name, job_name, step, steps


def test_there_are_app_tokens_to_check():
    """An empty walk would pass every test below vacuously."""
    ids = [s.get("id") for _, _, s, _ in _token_steps()]
    assert ids.count("app") >= 10 and ids.count("switchtok") >= 10, ids


def test_every_app_token_names_exactly_its_write_permissions():
    for wf, job, step, _ in _token_steps():
        if step.get("id") != "app":
            continue
        perms = {k: v for k, v in (step.get("with") or {}).items() if k.startswith("permission-")}
        expected = dict(APP_WRITES)
        if wf == "competitive-feed.yml":
            expected["permission-actions"] = "write"
        assert perms == expected, (wf, job, perms)


def test_only_the_competitive_feed_token_may_start_a_run():
    carriers = [
        (wf, job) for wf, job, step, _ in _token_steps()
        if "permission-actions" in (step.get("with") or {})
    ]
    assert carriers == [("competitive-feed.yml", "feed")], carriers


def test_every_token_step_is_a_known_kind():
    """A new token step must be one of the two kinds, or this file must learn a third on purpose."""
    for wf, job, step, _ in _token_steps():
        assert step.get("id") in {"app", "switchtok"}, (wf, job, step.get("id"))


def test_a_kill_switch_token_reaches_only_the_kill_switch_script():
    """It cannot be scoped, so the guard is where its token goes."""
    for wf, job, step, steps in _token_steps():
        if step.get("id") != "switchtok":
            continue
        ref = "steps.switchtok.outputs.token"
        users = [s for s in steps if ref in yaml.safe_dump(s)]
        assert users, (wf, job, "switchtok is never used")
        for user in users:
            run = str(user.get("run", ""))
            assert "killswitch.py" in run, (wf, job, user.get("name"))
            assert "gh workflow" not in run and "gh api" not in run, (wf, job, user.get("name"))


@pytest.fixture(scope="module")
def release():
    return _load(ROOT / ".github" / "workflows" / "release.yml")


def test_the_release_refuses_any_actor_but_jgravelle_first(release):
    first = release["jobs"]["preflight"]["steps"][0]
    assert first.get("env", {}).get("ACTOR") == "${{ github.triggering_actor }}"
    run = first["run"]
    assert '"$ACTOR" != "jgravelle"' in run and "exit 1" in run
    assert "${{" not in run, "the actor must reach the script through env, never interpolated"
    assert "if" not in first, "the guard must run on every trigger, dispatch and tag push alike"


def test_every_release_job_waits_for_the_guard(release):
    for name, job in release["jobs"].items():
        if name == "preflight":
            continue
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        assert "preflight" in needs, (name, needs)
