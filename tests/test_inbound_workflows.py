"""Every `inbound-*.yml` keeps the properties DESIGN section 9 promises:
no `pull_request_target`; write permissions only on triggers with no
external actor; the kill switch is the first step after checkout; the
model action is pinned to the recorded commit and given the prompt's own
model and turn ceiling; every checkout is `ref: main` or a same-repo ref;
`timeout-minutes` matches the POLICY section 7 row.

Red arms: a workflow that adds `pull_request_target`; a `contents: write`
on an `issues:` trigger; a kill-switch step moved below the first write; a
`--max-turns` above the budget; an unpinned or moved action SHA.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
INBOUND = ROOT / ".github" / "inbound"
FILES = sorted(WF.glob("inbound-*.yml"))

# DESIGN D4 / AUDIT 3.4: the one commit the action is pinned to. Bumping it is
# a deliberate edit here AND in every workflow.
ACTION_SHA = "ef8bb1e43bf303cff727a1dd0b8837029fe982a2"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, INBOUND / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


budget = _load("budget")
rp = _load("render_prompts")


def _wf(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    on = doc.get("on") or doc.get(True)  # PyYAML reads a bare `on:` as True
    return on if isinstance(on, dict) else {t: {} for t in (on or [])}


def _jobs(doc: dict) -> dict:
    return doc.get("jobs", {})


def _steps(job: dict) -> list:
    return job.get("steps", [])


def _perm_values(doc: dict) -> set[str]:
    out = set()
    for scope in [doc.get("permissions", {})] + [
        j.get("permissions", {}) for j in _jobs(doc).values()
    ]:
        if isinstance(scope, dict):
            out |= {f"{k}: {v}" for k, v in scope.items()}
    return out


def test_the_layer_has_workflows():
    assert FILES, "no inbound-*.yml; the item-2 PR adds the first two"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_no_pull_request_target_and_no_fork_checkout(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    doc = _wf(path)
    for job in _jobs(doc).values():
        for s in _steps(job):
            uses = s.get("uses", "")
            if uses.startswith("actions/checkout@"):
                ref = (s.get("with") or {}).get("ref", "")
                assert (
                    ref == "main"
                    or ref.startswith("${{ github.event.pull_request.head") is False
                ), f"{path.name}: checkout of a PR head at the workspace root"
                with_ = s.get("with") or {}
                if with_.get("persist-credentials") is not False:
                    # The one exception (DESIGN D7): the sweep checks out the
                    # ledger branch into a subdirectory with the App token so
                    # it can push there, and nowhere else.
                    assert path.stem == "inbound-sweep", (
                        f"{path.name}: checkout persists credentials"
                    )
                    assert (
                        ref == "inbound-ledger"
                        and with_.get("path")
                        and "steps.app.outputs.token" in with_.get("token", "")
                    ), (
                        f"{path.name}: a persisted checkout must be the ledger branch, in a subdirectory, with the App token"
                    )


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_write_permissions_only_on_actorless_or_same_repo_triggers(path: Path):
    doc = _wf(path)
    triggers = set(_triggers(doc))
    writes = {
        p
        for p in _perm_values(doc)
        if p.endswith(": write") and not p.startswith("id-token")
    }
    if not writes:
        return
    allowed = {
        "issues",
        "schedule",
        "workflow_dispatch",
        "workflow_run",
        "pull_request",
        "issue_comment",
    }
    assert triggers <= allowed, (path.name, triggers)
    if "pull_request" in triggers:
        text = path.read_text(encoding="utf-8")
        assert (
            "github.event.pull_request.head.repo.full_name == github.repository" in text
        ), (
            f"{path.name}: pull_request with a write permission needs the same-repo guard"
        )
    if "contents: write" in writes:
        # The sweep pushes with the App token, not GITHUB_TOKEN (item-3
        # review round 2, note 1): only the fix job may hold this scope.
        assert path.stem == "inbound-fix", (
            f"{path.name}: contents: write is reserved for the fix job (DESIGN D7; the sweep writes with the App token)"
        )


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_kill_switch_precedes_every_write(path: Path):
    """The self-check is the one job that runs with the switch off (DESIGN 5)."""
    if path.stem == "inbound-selfcheck":
        return
    doc = _wf(path)
    jobs = _jobs(doc)
    for name, job in jobs.items():
        steps = _steps(job)
        runs = [
            (i, (s.get("run") or "") + " " + (s.get("uses") or ""))
            for i, s in enumerate(steps)
        ]
        kill = [i for i, r in runs if "killswitch.py" in r]
        if not kill:
            # VERIFICATION 7.1: only the App token can read the switch, and a
            # job that runs the model or PR code never holds it. Such a job
            # has no read of its own; it starts only from a no-model job's
            # `go`, read seconds earlier, and it writes nothing.
            needs = job.get("needs") or []
            needs = [needs] if isinstance(needs, str) else list(needs)
            gate = [n for n in needs if any("killswitch.py" in (s.get("run") or "") for s in _steps(jobs.get(n, {})))]
            cond = str(job.get("if", ""))
            assert gate and any(f"needs.{g}.outputs.go == 'true'" in cond for g in gate), (
                f"{path.name}:{name} has no kill-switch step and does not start from a gate job's go: {cond!r}"
            )
            assert not any("create-github-app-token" in (s.get("uses") or "") for s in steps), (
                f"{path.name}:{name}: a job without its own switch read must hold no App token"
            )
            # and it writes nothing: a write here would follow no read of
            # its own (live-fixes review, finding 2: the comment claimed it
            # and nothing asserted it; a `gh issue` write appended to each
            # model job stayed green)
            writes = [
                i for i, r in runs
                if re.search(r"gh (issue|pr) (edit|comment|create|ready|close)|git push|apply_triage|apply_depeval|gh api -X (POST|PATCH|PUT|DELETE)", r)
            ]
            assert not writes, f"{path.name}:{name}: a job with no switch read of its own carries a write at step(s) {writes}"
            continue
        first_write = [
            i
            for i, r in runs
            if re.search(
                r"gh (issue|pr) (edit|comment|create|ready)|git push|claude-code-action|apply_triage|apply_depeval",
                r,
            )
        ]
        if first_write:
            assert kill[0] < first_write[0], (
                f"{path.name}:{name}: a write precedes the kill switch"
            )


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_event_text_never_reaches_run_by_interpolation(path: Path):
    """Item-2 review, finding 4: `${{ github.event.* }}` inside a `run:` is
    template injection; event text reaches the shell only through `env:`."""
    doc = _wf(path)
    bad = []
    for name, job in _jobs(doc).items():
        for s in _steps(job):
            run = s.get("run") or ""
            for m in re.finditer(
                r"\$\{\{\s*github\.event\.(?!workflow_run\.(?:id|conclusion)\b)[\w.]+",
                run,
            ):
                bad.append((name, m.group(0)))
    assert not bad, bad


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_the_model_step_holds_no_write_scope(path: Path):
    """Item-2 review, finding 1: the job that runs the model has read-only
    permissions and the read-only GITHUB_TOKEN; the App token lives in a
    job with no model."""
    doc = _wf(path)
    for name, job in _jobs(doc).items():
        steps = _steps(job)
        has_model = any("claude-code-action" in (s.get("uses") or "") for s in steps)
        if not has_model:
            continue
        perms = job.get("permissions") or doc.get("permissions") or {}
        writes = {k for k, v in perms.items() if v == "write" and k != "id-token"}
        assert not writes, (
            f"{path.name}:{name} runs the model with write scope {writes}"
        )
        for s in steps:
            if "claude-code-action" in (s.get("uses") or ""):
                tok = (s.get("with") or {}).get("github_token", "")
                assert "secrets.GITHUB_TOKEN" in tok, (
                    f"{path.name}:{name}: the model step must use GITHUB_TOKEN, not the App"
                )
                assert (
                    "gh api"
                    not in (s.get("with") or {})
                    .get("claude_args", "")
                    .split("--disallowedTools")[0]
                ), "`gh api` admits POST forms; never in the model's allow-list"
            assert "create-github-app-token" not in (s.get("uses") or ""), (
                f"{path.name}:{name}: the App token in a model job"
            )


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_timeouts_and_turns_match_the_policy(path: Path):
    doc = _wf(path)
    row = budget.BUDGETS.get(path.stem)
    if row is None:
        assert path.stem in (
            "inbound-intake",
            "inbound-selfcheck",
            "inbound-fix-promote",
        ), path.stem
        return
    for name, job in _jobs(doc).items():
        assert job.get("timeout-minutes", 0) <= row["timeout_min"], (path.name, name)
        for s in _steps(job):
            if "claude-code-action" in (s.get("uses") or ""):
                args = (s.get("with") or {}).get("claude_args", "")
                m = re.search(r"--max-turns (\d+)", args)
                assert m and int(m.group(1)) <= row["turns"], (path.name, name, args)
                assert (
                    "--permission-mode dontAsk" in args
                    and "--permission-prompts none" in args
                )


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_model_action_is_pinned_and_given_the_prompts_model(path: Path):
    doc = _wf(path)
    for name, job in _jobs(doc).items():
        for s in _steps(job):
            uses = s.get("uses") or ""
            if "claude-code-action" not in uses:
                continue
            assert uses == f"anthropics/claude-code-action@{ACTION_SHA}", (
                path.name,
                uses,
            )
            with_ = s.get("with") or {}
            assert "prompt" in with_ and "prompt_file" not in with_, (
                "the action has a `prompt` input only"
            )
            assert "WebFetch" in with_.get(
                "claude_args", ""
            ) and "WebSearch" in with_.get("claude_args", ""), (
                "WebFetch/WebSearch must be disallowed by name (DESIGN 9)"
            )
    text = path.read_text(encoding="utf-8")
    for m in re.finditer(r"\.github/inbound/prompts/(\w+)\.md", text):
        fm = rp.front_matter(
            (INBOUND / "prompts" / f"{m.group(1)}.md").read_text(encoding="utf-8")
        )
        assert fm["model"] in text, (
            f"{path.name}: prompt {m.group(1)} pins {fm['model']} but the workflow does not pass it"
        )


# ---- item-4 review, finding 4: the guards the design promises, asserted ----

SAME_REPO_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_every_pull_request_job_carries_the_same_repo_guard(path: Path):
    """DESIGN section 9: a `pull_request` job runs only for a same-repo
    branch, whether or not it holds a write permission (the bench job has
    none and executes the merge ref's build hooks)."""
    doc = _wf(path)
    if "pull_request" not in _triggers(doc):
        return
    for name, job in _jobs(doc).items():
        cond = str(job.get("if", "")) if job.get("if") is not None else ""
        assert SAME_REPO_GUARD in cond or (
            name != next(iter(_jobs(doc)))
            and re.search(r"needs\.\w+\.outputs\.\w+ == 'true'", cond)
        ), f"{path.name}:{name}: a pull_request job without the same-repo guard (or a needs: gate on a guarded job)"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_label_triggered_jobs_name_the_label_and_the_branch_prefix(path: Path):
    """The bench starts from `agent:bench-pending` on a `dependabot/` head
    and nothing else; a `labeled` trigger without both guards would run
    for any label anyone with triage can apply."""
    doc = _wf(path)
    first = next(iter(_jobs(doc).values()))
    issues = _triggers(doc).get("issues")
    if isinstance(issues, dict) and "labeled" in (issues.get("types") or []):
        # item-6 review, note 9: the fix starts from ONE issue label
        assert re.search(r"github\.event\.label\.name == '[\w:-]+'", str(first.get("if", ""))), (path.name, first.get("if"))
    pr = _triggers(doc).get("pull_request")
    if not isinstance(pr, dict) or "labeled" not in (pr.get("types") or []):
        return
    cond = str(first.get("if", ""))
    by_label_event = re.search(r"github\.event\.label\.name == '[\w:-]+'", cond)
    by_label_set = re.search(r"contains\(github\.event\.pull_request\.labels\.\*\.name, '[\w:-]+'\)", cond)
    assert by_label_event or by_label_set, (path.name, cond)
    if by_label_event:
        # A job started by ONE label event (the bench) also pins the
        # branch prefix; a job that runs on every PR event and filters
        # by the label set (the self-check) checks the branch itself.
        assert "startsWith(github.event.pull_request.head.ref, '" in cond, (path.name, cond)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_workflow_run_jobs_restrict_the_upstream_actor_or_branch(path: Path):
    """DESIGN section 9: a `workflow_run` job names the upstream actor
    (Dependabot) or the branch prefix (`inbound/fix-`) and the upstream
    event, so a fork PR's gate run cannot reach it."""
    doc = _wf(path)
    if "workflow_run" not in _triggers(doc):
        return
    first_name, first = next(iter(_jobs(doc).items()))
    cond = str(first.get("if", ""))
    assert "github.event.workflow_run.event == 'pull_request'" in cond, (path.name, first_name, cond)
    assert (
        "github.event.workflow_run.actor.login == 'dependabot[bot]'" in cond
        or "startsWith(github.event.workflow_run.head_branch, 'inbound/fix-')" in cond
    ), (path.name, first_name, cond)
    if "dependabot" in cond:
        assert "startsWith(github.event.workflow_run.head_branch, 'dependabot/')" in cond, (path.name, cond)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_pr_code_is_never_checked_out_at_the_workspace_root(path: Path):
    """Item-4 review, finding 5: `refuses: to check out the PR head at the
    workspace root` was asserted only over `actions/checkout` steps. A
    `run:` line can do the same with `git checkout`; the only admitted
    form is a worktree under the runner temp."""
    doc = _wf(path)
    bad = []
    for name, job in _jobs(doc).items():
        for s in _steps(job):
            run = s.get("run") or ""
            for line in run.splitlines():
                if re.search(r"git\s+(checkout|switch)\b", line) and re.search(r"refs/(pull|inbound)|pr-(merge|head)", line):
                    bad.append((name, line.strip()))
                if re.search(r"git\s+worktree\s+add", line) and "$RUNNER_TEMP" not in line and "runner.temp" not in line:
                    bad.append((name, line.strip()))
                if re.search(r"git\s+fetch\b.*refs/pull/", line) and not re.search(r":refs/inbound/", line):
                    bad.append((name, line.strip()))
    assert not bad, bad


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_the_model_allow_list_carries_no_posting_verb(path: Path):
    """Item-4 review, note 7: the model job's read-only token is defence in
    depth; the allow-list itself must not name a write."""
    doc = _wf(path)
    for name, job in _jobs(doc).items():
        for s in _steps(job):
            if "claude-code-action" not in (s.get("uses") or ""):
                continue
            args = (s.get("with") or {}).get("claude_args", "")
            allow = args.split("--allowedTools", 1)[1].split("--disallowedTools", 1)[0] if "--allowedTools" in args else ""
            assert not re.search(
                r"gh (pr|issue|release|workflow|variable|secret) (edit|comment|create|ready|merge|review|close|run|set|delete)|git push",
                allow,
            ), (path.name, name, allow)
            assert "Bash(git *)" not in allow, (path.name, name, "a bare git wildcard admits push")


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_no_pipe_hides_a_gate_exit_status(path: Path):
    """Item-6 review, finding 1: `python gate.py | tee f; rc=$?` records
    tee's status, so every decline the gate computed was ignored. A
    `.github/inbound/*.py` invocation is never the left side of a pipe."""
    bad = []
    for name, job in _jobs(_wf(path)).items():
        for s in _steps(job):
            # join `\`-continued lines first: the first draft of this test
            # matched per physical line and stayed green with the pipe back
            for line in (s.get("run") or "").replace("\\\n", " ").splitlines():
                # the class, not the instance (round 2, note 2): any pipe
                # after a gate hides its status, `tee` was only the one seen
                if re.search(r"\.github/inbound/\w+\.py[^|]*\|(?!\|)", line):
                    bad.append((name, line.strip()[:80]))
    assert not bad, bad


def test_the_fix_model_job_cannot_push_to_origin():
    """VERIFICATION 6.4: the `no_push` URL rewrite is a step of the model
    job and precedes the action (item-6 review, finding 5: the row claimed
    this test before it existed)."""
    doc = _wf(WF / "inbound-fix.yml")
    fix = _jobs(doc)["fix"]
    steps = _steps(fix)
    no_push = [i for i, s in enumerate(steps) if "git remote set-url --push origin no_push" in (s.get("run") or "")]
    model = [i for i, s in enumerate(steps) if "claude-code-action" in (s.get("uses") or "")]
    assert no_push and model and no_push[0] < model[0], (no_push, model)
    for s in steps:
        assert (s.get("with") or {}).get("persist-credentials", False) is False, s.get("uses")


def test_promote_matches_the_app_login_exactly_and_binds_the_verdict_to_the_head_sha():
    """Item-6 review round 2, note 1: the two round-1 fixes in the promote
    job live in inline Python; this pins their text so a one-line
    regression (a substring `test(...)`, a verdict read without the SHA
    compare) goes red."""
    doc = _wf(WF / "inbound-fix-promote.yml")
    runs = "\n".join(s.get("run") or "" for s in _steps(_jobs(doc)["promote"]))
    assert 'test("jcodemunch' not in runs, "substring login match"
    assert "os.environ['APP_LOGIN']" in runs and ".replace('app/', '')" in runs
    assert "head-sha.txt" in runs and 'os.environ["HEAD_SHA"]' in runs and "stale" in runs


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_a_job_that_executes_pr_code_holds_no_app_token(path: Path):
    """Item-4 review, finding 5: the bench executes the merge ref's build
    hooks and harness; the App token is minted in a job that runs nothing
    from the PR."""
    doc = _wf(path)
    for name, job in _jobs(doc).items():
        runs = " ".join((s.get("run") or "") for s in _steps(job))
        executes_pr = bool(re.search(r"refs/(pull|inbound)/|pr-(merge|head|tree)", runs)) and bool(
            re.search(r"uv (sync|run)|pytest|harness", runs)
        )
        mints = any("create-github-app-token" in (s.get("uses") or "") for s in _steps(job))
        assert not (executes_pr and mints), f"{path.name}:{name}: executes PR code and mints the App token in one job"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_every_kill_switch_read_uses_the_app_token(path: Path):
    """VERIFICATION 7.1 (first live run, 2026-09-05): `gh variable get` on
    GITHUB_TOKEN is `403 Resource not accessible by integration` (no
    `permissions:` scope covers variables) and `${{ vars.* }}` is frozen at
    queue time (measured: `true` two minutes after the flip to `false`).
    Every kill-switch invocation runs under a token minted by
    `create-github-app-token` in the SAME job, before it; budget.py keeps
    GITHUB_TOKEN (the App has no Actions scope)."""
    doc = _wf(path)
    for name, job in _jobs(doc).items():
        steps = _steps(job)
        tok = [i for i, s in enumerate(steps) if "create-github-app-token" in (s.get("uses") or "") and s.get("id") == "switchtok"]
        for i, s in enumerate(steps):
            run = s.get("run") or ""
            if "killswitch.py" not in run:
                continue
            assert tok and tok[0] < i, f"{path.name}:{name}: kill switch at step {i} with no `switchtok` App token before it"
            env = s.get("env") or {}
            assert env.get("SWITCH_TOKEN") == "${{ steps.switchtok.outputs.token }}", (path.name, name, env)
            for line in run.replace("\\n", " ").splitlines():
                if "killswitch.py" in line:
                    assert 'GH_TOKEN="$SWITCH_TOKEN" python .github/inbound/killswitch.py' in line, (path.name, name, line.strip()[:90])
            if "budget.py" in run:
                gh = env.get("GH_TOKEN") or (job.get("env") or {}).get("GH_TOKEN", "")
                assert "secrets.GITHUB_TOKEN" in gh, (path.name, name, "budget.py reads runs with GITHUB_TOKEN")


ks = _load("killswitch")


def _model_gate_offenders(doc: dict) -> list[str]:
    """Every job that runs the model starts only from a gate output that the
    model switch feeds (owner ruling 2026-09-14: the model jobs billed the
    API account unseen). A model job cannot read a variable itself (it holds
    no App token, VERIFICATION 7.1), so the read lives in the gate job it
    `needs`, and its exit code must reach the output the model job tests."""
    jobs = _jobs(doc)
    bad = []
    for name, job in jobs.items():
        if not any("claude-code-action" in (s.get("uses") or "") for s in _steps(job)):
            continue
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else list(needs)
        cond = str(job.get("if", ""))
        ok = False
        if "||" in cond or "always()" in cond:
            # review round 1, M2: `always() || needs.queue.outputs.go == 'true'`
            # names the output and starts the model regardless of it
            bad.append(f"{name}: if={cond!r} can start without the gate")
            continue
        for g in needs:
            gate = jobs.get(g, {})
            for out, expr in (gate.get("outputs") or {}).items():
                if f"needs.{g}.outputs.{out} == 'true'" not in cond:
                    continue
                m = re.search(r"steps\.(\w+)\.outputs\.(\w+)", str(expr))
                if not m:
                    continue
                step = next((s for s in _steps(gate) if s.get("id") == m.group(1)), None)
                # join `\`-continued lines the way the shell does
                run = ((step or {}).get("run") or "").replace("\\\n", " ")
                read = re.search(
                    rf"killswitch\.py[^\n;]*--variable\s+\"?{re.escape(ks.MODEL_VARIABLE)}\"?[^\n;]*;\s*(\w+)=\$\?", run
                )
                # the read's exit code must sit in the SAME `if` that writes
                # exactly this output as false (review round 1, M1/M3: a bare
                # `go=false` substring matched inside `model_go=false`)
                if read and re.search(
                    rf"if [^\n]*\"\${read.group(1)}\"\s*!=\s*\"0\"[^\n]*;\s*then\s*(?:\n\s*)?echo\s+\"{re.escape(m.group(2))}=false\"",
                    run,
                ):
                    ok = True
        if not ok:
            bad.append(f"{name}: if={cond!r}")
    return bad


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_every_model_job_starts_only_from_the_model_switch(path: Path):
    """INBOUND_ENABLED is the whole layer; INBOUND_MODEL_ENABLED is the part
    that bills. Absent is OFF, exactly like the layer switch."""
    assert _model_gate_offenders(_wf(path)) == []


def test_the_model_gate_ratchet_fails_a_model_job_gated_on_the_layer_switch_alone():
    """Non-vacuity, in the suite: today's shape (one read, one `go`) is an
    offender; the same gate with the model read feeding `go` is not."""
    layer_only = yaml.safe_load(
        """
jobs:
  queue:
    outputs: {go: "${{ steps.pick.outputs.go }}"}
    steps:
      - id: pick
        run: |
          GH_TOKEN="$SWITCH_TOKEN" python .github/inbound/killswitch.py --repo "$REPO"; k=$?
          if [ "$k" != "0" ]; then echo "go=false" >> "$GITHUB_OUTPUT"; exit 0; fi
          echo "go=true" >> "$GITHUB_OUTPUT"
  classify:
    needs: queue
    if: "needs.queue.outputs.go == 'true'"
    steps:
      - uses: anthropics/claude-code-action@x
"""
    )
    assert _model_gate_offenders(layer_only)
    both = copy.deepcopy(layer_only)
    both["jobs"]["queue"]["steps"][0]["run"] = (
        'GH_TOKEN="$SWITCH_TOKEN" python .github/inbound/killswitch.py --repo "$REPO"; k=$?\n'
        f'GH_TOKEN="$SWITCH_TOKEN" python .github/inbound/killswitch.py --repo "$REPO" --variable {ks.MODEL_VARIABLE}; m=$?\n'
        'if [ "$k" != "0" ] || [ "$m" != "0" ]; then echo "go=false" >> "$GITHUB_OUTPUT"; exit 0; fi\n'
        'echo "go=true" >> "$GITHUB_OUTPUT"\n'
    )
    assert _model_gate_offenders(both) == []
    # a model read whose exit code reaches no output does not count
    both["jobs"]["queue"]["steps"][0]["run"] = both["jobs"]["queue"]["steps"][0]["run"].replace(
        ' || [ "$m" != "0" ]', ""
    )
    assert '"$m"' not in both["jobs"]["queue"]["steps"][0]["run"].split("m=$?")[1]
    assert _model_gate_offenders(both)


def test_the_model_gate_ratchet_fails_the_three_mutations_review_round_1_found():
    """Each passed the first draft of the ratchet against the REAL workflows."""
    digest = _wf(WF / "inbound-digest.yml")
    assert _model_gate_offenders(digest) == []
    # M1: the digest paragraph back on the layer switch alone, the new read left in place
    m1 = copy.deepcopy(digest)
    m1["jobs"]["prose"]["if"] = "needs.numbers.outputs.go == 'true'"
    assert _model_gate_offenders(m1)
    # M2: a condition that names the output and starts regardless of it
    triage = _wf(WF / "inbound-triage.yml")
    assert _model_gate_offenders(triage) == []
    m2 = copy.deepcopy(triage)
    m2["jobs"]["classify"]["if"] = "always() || needs.queue.outputs.go == 'true'"
    assert _model_gate_offenders(m2)
    # M3: the model read's code moved off the `go=false` line onto a separate output
    fix = _wf(WF / "inbound-fix.yml")
    assert _model_gate_offenders(fix) == []
    m3 = copy.deepcopy(fix)
    step = next(s for s in _steps(m3["jobs"]["preflight"]) if s.get("id") == "pre")
    assert ' || [ "$m" != "0" ]' in step["run"]
    step["run"] = step["run"].replace(' || [ "$m" != "0" ]', "") + (
        'if [ "$m" != "0" ]; then echo "model_go=false" >> "$GITHUB_OUTPUT"; fi\n'
    )
    assert _model_gate_offenders(m3)


def test_the_model_switch_is_a_second_variable_that_fails_closed_the_same_way():
    assert ks.MODEL_VARIABLE == "INBOUND_MODEL_ENABLED" and ks.MODEL_VARIABLE != ks.VARIABLE
    for value in (None, "", "True", "1", "yes"):
        assert ks.enabled(value) is False


# --- the gate steps EXECUTED (review round 2) --------------------------------
# A text pattern closes the spellings it names and leaves the next one open:
# `||` turned into `&&`, `|| true` before `m=$?`, `m=0` after the read, a
# removed `exit 0` all passed it. So each gate step in front of a model job is
# run under bash with stub switch scripts, for every combination of the two
# switches, and the outputs the model job starts from are read back.

_STUB_SWITCH = """import os, sys
a = sys.argv
var = a[a.index("--variable") + 1] if "--variable" in a else "INBOUND_ENABLED"
sys.exit(0 if os.environ.get("STUB_" + var) == "true" else 78)
"""


def _bash() -> str:
    import shutil

    for cand in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if cand and Path(cand).is_file() and "system32" not in cand.lower():
            return cand
    raise AssertionError("bash is required to execute the gate steps (Git Bash on Windows)")


def _gate_outputs(run: str, tmp: Path, layer: bool, model: bool) -> dict[str, str]:
    import os
    import subprocess

    inbound = tmp / ".github" / "inbound"
    inbound.mkdir(parents=True, exist_ok=True)
    (inbound / "killswitch.py").write_text(_STUB_SWITCH, encoding="utf-8")
    (inbound / "budget.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (inbound / "fix_preflight.py").write_text("print('{}')\n", encoding="utf-8")
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text("#!/usr/bin/env bash\necho '[1]'\n", encoding="utf-8", newline="\n")
    (bindir / "python").write_text(
        f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8", newline="\n"
    )
    out = tmp / "github_output.txt"
    out.write_text("", encoding="utf-8")
    (tmp / "runner").mkdir(exist_ok=True)
    script = tmp / "step.sh"
    script.write_text(
        'export PATH="$STUB_BIN:$PATH"\nchmod +x "$STUB_BIN/gh" "$STUB_BIN/python"\n' + run.replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    env = dict(os.environ)
    env.update(
        STUB_INBOUND_ENABLED="true" if layer else "false",
        STUB_INBOUND_MODEL_ENABLED="true" if model else "false",
        STUB_BIN=bindir.as_posix(),
        GITHUB_OUTPUT=out.as_posix(),
        RUNNER_TEMP=(tmp / "runner").as_posix(),
        REPO="o/r", ISSUE="7", REQUESTED="", LABELER="u", LABELER_TYPE="User", APP_LOGIN="a",
    )
    subprocess.run([_bash(), "-e", script.as_posix()], cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
    values: dict[str, str] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        values[k.strip()] = v.strip()  # the LAST write wins, as in Actions
    return values


def _model_start_combinations(doc: dict, tmp: Path) -> list[str]:
    """Every (layer, model) combination under which a model job would start,
    other than both switches on."""
    jobs = _jobs(doc)
    wrong = []
    for name, job in jobs.items():
        if not any("claude-code-action" in (s.get("uses") or "") for s in _steps(job)):
            continue
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else list(needs)
        cond = str(job.get("if", ""))
        for layer in (True, False):
            for model in (True, False):
                starts = True
                tested = 0
                for g in needs:
                    gate = jobs.get(g, {})
                    for outname, expr in (gate.get("outputs") or {}).items():
                        if f"needs.{g}.outputs.{outname} == 'true'" not in cond:
                            continue
                        m = re.search(r"steps\.(\w+)\.outputs\.(\w+)", str(expr))
                        step = next((s for s in _steps(gate) if m and s.get("id") == m.group(1)), None)
                        if step is None:
                            continue
                        d = tmp / f"{name}-{g}-{outname}-{layer}-{model}"
                        d.mkdir(parents=True)
                        tested += 1
                        if _gate_outputs(step.get("run") or "", d, layer, model).get(m.group(2)) != "true":
                            starts = False
                if tested == 0 or "||" in cond or "always()" in cond:
                    starts = True  # nothing we can execute holds it back
                if starts and not (layer and model):
                    wrong.append(f"{name}: starts with INBOUND_ENABLED={layer} INBOUND_MODEL_ENABLED={model}")
                if not starts and layer and model:
                    wrong.append(f"{name}: does not start with both switches on")
    return wrong


@pytest.mark.parametrize("stem", ["inbound-triage", "inbound-digest", "inbound-fix", "inbound-depeval"])
def test_executed_gates_start_a_model_job_only_with_both_switches_on(stem: str, tmp_path: Path):
    assert _model_start_combinations(_wf(WF / f"{stem}.yml"), tmp_path) == []


def test_executed_gates_see_the_mutations_the_patterns_could_not(tmp_path: Path):
    """Review round 2: M4 `&&` for `||`, M5 `m=0` after the read, M6 the
    early `exit 0` removed, M7 `|| true` before `m=$?`. Each starts a model
    job with the model switch off; the executed check must say so."""
    def mutate(stem, job, step_id, old, new):
        doc = _wf(WF / f"{stem}.yml")
        step = next(s for s in _steps(doc["jobs"][job]) if s.get("id") == step_id)
        assert old in step["run"], (stem, old)
        step["run"] = step["run"].replace(old, new, 1)
        return doc

    cases = {
        "M4": mutate("inbound-depeval", "gate", "gate", '[ "$k" != "0" ] || [ "$m" != "0" ]', '[ "$k" != "0" ] && [ "$m" != "0" ]'),
        "M5": mutate("inbound-fix", "preflight", "pre", "m=$?; fi", "m=$?; fi; m=0"),
        "M6": mutate("inbound-triage", "queue", "pick", 'echo "issues=[]" >> "$GITHUB_OUTPUT"; exit 0; fi', 'echo "issues=[]" >> "$GITHUB_OUTPUT"; fi'),
        "M7": mutate("inbound-triage", "queue", "pick", "--variable INBOUND_MODEL_ENABLED; m=$?", "--variable INBOUND_MODEL_ENABLED || true; m=$?"),
        "M1": mutate("inbound-digest", "numbers", "gate", 'echo "model_go=false"', 'echo "model_go=true"'),
    }
    for label, doc in cases.items():
        found = _model_start_combinations(doc, tmp_path / label)
        assert any("INBOUND_MODEL_ENABLED=False" in w for w in found), (label, found)
