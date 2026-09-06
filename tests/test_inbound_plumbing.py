"""Kill switch fails closed, budgets decline before a run, the ledger keeps
every field and never a security excerpt (POLICY sections 6, 7, 8).

Red arms: `enabled("True")` returning True; `evaluate` letting a fourth fix
run or a fourth open agent PR through; `roll` appending the same run twice;
a security record accepted with evidence text in it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INBOUND = ROOT / ".github" / "inbound"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, INBOUND / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ks = _load("killswitch")
budget = _load("budget")
ledger = _load("ledger")


# --- kill switch ------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [None, "", "True", "TRUE", "1", "yes", "on", " true", "true\n"]
)
def test_only_exact_true_enables(value):
    assert ks.enabled(value) is False


def test_exact_true_enables():
    assert ks.enabled("true") is True


def test_missing_variable_reads_as_off_and_writes_a_skipped_record(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ks, "read_variable", lambda *a, **k: None)
    rec = tmp_path / "rec.json"
    rc = ks.main(["--record", str(rec), "--job", "inbound-triage", "--item", "1"])
    assert rc == ks.EXIT_SKIP
    data = json.loads(rec.read_text(encoding="utf-8"))
    assert data["outcome"] == "skipped" and data["kill_switch_state"] is None


def test_a_padded_value_reads_as_off_through_main(monkeypatch):
    """`gh` appends one newline; anything else the human typed stays and reads OFF."""

    class P:
        returncode = 0
        stdout = " true\n"

    monkeypatch.setattr(ks.subprocess, "run", lambda *a, **k: P())
    assert ks.main([]) == ks.EXIT_SKIP


def test_the_trailing_newline_alone_is_tolerated(monkeypatch):
    class P:
        returncode = 0
        stdout = "true\n"

    monkeypatch.setattr(ks.subprocess, "run", lambda *a, **k: P())
    assert ks.main([]) == 0


def test_gh_failure_reads_as_off(monkeypatch):
    class P:
        returncode = 1
        stdout = "true"

    monkeypatch.setattr(ks.subprocess, "run", lambda *a, **k: P())
    assert ks.read_variable() is None


# --- budgets ----------------------------------------------------------------


def test_budget_table_matches_policy_section_7():
    text = (ROOT / "docs" / "inbound" / "POLICY.md").read_text(encoding="utf-8")
    assert (
        "| runs per day | 20 triage, 3 fix attempts, 4 dependency evaluations, 4 full-corpus benches, 1 sweep, 1 digest, 1 competitive run, 1 competitive feed, 1 competitive post |"
        in text
    )
    assert "| turns, digest | 8 |" in text
    assert budget.BUDGETS["inbound-digest"]["turns"] == 8
    assert "2 USD digest" in text
    assert budget.BUDGETS["inbound-digest"]["cost_per_run_usd"] == 2.0
    assert budget.BUDGETS["inbound-bench-full"]["runs_per_day"] == 4
    assert budget.BUDGETS["inbound-triage"]["runs_per_day"] == 20
    assert budget.BUDGETS["inbound-fix"]["runs_per_day"] == 3
    assert budget.BUDGETS["inbound-depeval"]["runs_per_day"] == 4
    assert (
        "| cost per day, all jobs | 60 USD |" in text and budget.DAILY_COST_USD == 60.0
    )
    assert (
        "| agent-authored PRs open at once | 3 (drafts count) |" in text
        and budget.MAX_OPEN_AGENT_PRS == 3
    )


@pytest.mark.parametrize(
    "job,runs,prs,cost,ok",
    [
        ("inbound-fix", 2, 2, 10.0, True),
        ("inbound-fix", 3, 0, 0.0, False),
        ("inbound-fix", 0, 3, 0.0, False),
        ("inbound-fix", 0, 0, 60.0, False),
        ("inbound-triage", 19, 9, 0.0, True),
        ("inbound-triage", 20, 0, 0.0, False),
        ("inbound-depeval", 4, 0, 0.0, False),
    ],
)
def test_evaluate(job, runs, prs, cost, ok):
    got, reasons = budget.evaluate(job, runs, prs, cost)
    assert got is ok, reasons
    assert ok or reasons


def test_unknown_job_is_declined():
    ok, reasons = budget.evaluate("inbound-nope", 0, 0, 0.0)
    assert ok is False and "no budget row" in reasons[0]


def test_manual_dispatch_lifts_nothing():
    assert budget.evaluate("inbound-fix", 3, 0, 0.0, manual_dispatch=True)[0] is False


def test_cost_today_counts_an_over_ceiling_run_double(tmp_path):
    day = "2026-09-04"
    f = tmp_path / "2026-09.jsonl"
    rows = [
        {
            "recorded_at": f"{day}T01:00:00+00:00",
            "job": "inbound-triage",
            "cost_usd": 1.0,
        },
        {
            "recorded_at": f"{day}T02:00:00+00:00",
            "job": "inbound-triage",
            "cost_usd": 6.0,
        },  # over 5 -> 12
        {
            "recorded_at": "2026-09-03T02:00:00+00:00",
            "job": "inbound-fix",
            "cost_usd": 20.0,
        },  # yesterday
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert budget.cost_today(tmp_path, day) == 13.0


# --- ledger -----------------------------------------------------------------


def test_record_has_every_policy_field_and_rejects_unknown_outcome():
    rec = ledger.make_record(job="inbound-triage", item="12", outcome="acted")
    assert set(rec) == set(ledger.REQUIRED)
    with pytest.raises(ValueError):
        ledger.make_record(job="x", item="1", outcome="done")
    with pytest.raises(ValueError):
        ledger.make_record(job="x", item="1", outcome="acted", extra="no")


def test_security_record_carries_no_excerpt():
    ledger.make_record(
        job="inbound-intake",
        item="9",
        outcome="escalated",
        classification={"category": "security", "confidence": "high"},
    )
    with pytest.raises(ValueError):
        ledger.make_record(
            job="inbound-intake",
            item="9",
            outcome="escalated",
            classification={"category": "security", "evidence": ["the exploit is ..."]},
        )
    with pytest.raises(ValueError):
        ledger.make_record(
            job="inbound-intake",
            item="#9 path escape in install-pack",
            outcome="escalated",
            classification={"category": "security"},
        )


def test_roll_appends_by_month_and_dedups_on_run_id(tmp_path):
    art = tmp_path / "artifacts"
    for i, rid in enumerate(["r1", "r1", "r2"]):
        ledger.write_record(
            art / f"a{i}" / "rec.json",
            ledger.make_record(
                job="inbound-triage",
                item=str(i),
                outcome="acted",
                run_id=rid,
                recorded_at="2026-09-04T00:00:00+00:00",
            ),
        )
    ledger.write_record(
        art / "b" / "rec.json",
        ledger.make_record(
            job="inbound-triage",
            item="7",
            outcome="skipped",
            run_id="r3",
            recorded_at="2026-10-01T00:00:00+00:00",
        ),
    )
    led = tmp_path / "ledger"
    assert ledger.roll(art, led) == 3
    assert ledger.roll(art, led) == 0
    sep = (led / "2026-09.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(sep) == 2 and {json.loads(x)["run_id"] for x in sep} == {"r1", "r2"}
    assert len((led / "2026-10.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_roll_skips_a_record_missing_a_field(tmp_path):
    art = tmp_path / "a"
    art.mkdir()
    (art / "bad.json").write_text('{"job": "x"}', encoding="utf-8")
    assert ledger.roll(art, tmp_path / "led") == 0


# --- packaging ----------------------------------------------------------------


def test_sdist_excludes_the_github_directory():
    """IN-10: the prompts and helpers live under .github/ and must not ship."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    sdist = pyproject.split("[tool.hatch.build.targets.sdist]", 1)[1]
    sdist = sdist.split(chr(10) + "[", 1)[0]
    assert '".github/"' in sdist


def test_the_run_that_asks_is_not_counted_against_its_own_daily_budget():
    """The first live sweep (2026-09-05, run 33936406280) counted itself in
    `runs_today` and declined with "runs_per_day: 1 of 1 used": a job
    allowed one run a day could never run. The asking run is excluded;
    every other run today, declined or not, still counts."""
    runs = [{"databaseId": 1}, {"databaseId": 2}, {"databaseId": 3}]
    assert budget.count_other_runs(runs, "3") == 2
    assert budget.count_other_runs(runs, 3) == 2
    assert budget.count_other_runs(runs, None) == 3, "no run id known: count everything (fail closed)"
    assert budget.count_other_runs([{"databaseId": 7}], "7") == 0
    ok, reasons = budget.evaluate("inbound-sweep", budget.count_other_runs([{"databaseId": 7}], "7"), 0, 0.0, False)
    assert ok is True and reasons == [], reasons


def test_a_failed_switch_read_names_its_error(monkeypatch, capsys):
    """First live run (2026-09-05, run 33936406280): `gh variable get` on
    GITHUB_TOKEN returned 403 and the verdict printed `value: null` with no
    reason, so a permission failure read exactly like the switch being
    off. The stderr reaches the verdict now."""
    import subprocess as sp

    def fake_run(cmd, **kw):
        return sp.CompletedProcess(cmd, 1, stdout="", stderr="gh: Resource not accessible by integration (HTTP 403)\n")

    monkeypatch.setattr(ks.subprocess, "run", fake_run)
    rc = ks.main(["--repo", "o/r"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert rc == ks.EXIT_SKIP and out["enabled"] is False and out["value"] is None
    assert "403" in out["error"], out


def test_a_run_that_declined_at_its_gate_spends_no_budget():
    """FINDINGS IN-17, closed 2026-09-05 after the second live sweep: the
    first sweep of the day had declined (switch unreadable), and the second
    was declined by the budget for it, `runs_per_day: 1 of 1 used`. A run
    in which nothing but the plumbing steps succeeded did no work."""
    declined = [{"name": "Set up job", "conclusion": "success"}, {"name": "Run actions/checkout@abc", "conclusion": "success"},
                {"name": "switch-reading token (App)", "conclusion": "success"}, {"name": "kill switch and budget", "conclusion": "success"},
                {"name": "App token (DESIGN D2)", "conclusion": "skipped"}, {"name": "commit the ledger", "conclusion": "skipped"},
                {"name": "audit record", "conclusion": "success"}, {"name": "Post Run actions/checkout@abc", "conclusion": "success"},
                {"name": "Complete job", "conclusion": "success"}]
    assert budget.run_did_work(declined) is False
    worked = declined[:4] + [{"name": "App token (DESIGN D2)", "conclusion": "success"}, {"name": "commit the ledger", "conclusion": "success"}]
    assert budget.run_did_work(worked) is True
    failed = declined[:4] + [{"name": "classify (model)", "conclusion": "failure"}]
    assert budget.run_did_work(failed) is True, "a failed model step spent its budget (review, finding 3)"
    assert budget.run_did_work([]) is True, "no steps readable: fail closed, count it"
    # `gate (read by the gate job ...)` is the echo step of a model job, plumbing too
    assert budget.run_did_work([{"name": "gate (read by the gate job)", "conclusion": "success"}]) is False


def test_the_budget_stops_reading_runs_at_the_ceiling():
    """Review of IN-17's fix, finding 2: one `gh run view` per prior run,
    on a job that fires every 15 minutes; the count stops at the ceiling."""
    reads = []
    def steps_of(r):
        reads.append(r["databaseId"])
        return [{"name": "work", "conclusion": "success"}]
    runs = [{"databaseId": i} for i in range(10)]
    assert budget.count_working_runs(runs, steps_of, 3) == 3 and reads == [0, 1, 2]
    reads.clear()
    assert budget.count_working_runs(runs, steps_of, 0) == 10, "no ceiling: read everything"
    assert budget.other_runs(runs, "4") == [r for r in runs if r["databaseId"] != 4]
    assert len(budget.other_runs([{"databaseId": 4}, {"databaseId": 5}], "4")) == 1


def test_plumbing_prefixes_match_every_gate_step_and_no_work_step():
    """Review of IN-17's fix, note 4: the prefix list is hand-written. Bind
    it to the workflows: in every job with a kill-switch step, every step
    up to and including that step matches a prefix, and the first step
    after it that is neither an upload nor the audit record does not."""
    import yaml
    for path in sorted((ROOT / ".github" / "workflows").glob("inbound-*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jname, job in doc.get("jobs", {}).items():
            steps = job.get("steps", [])
            names = [s.get("name") or ("Run " + s["uses"] if s.get("uses") else "run") for s in steps]
            kills = [i for i, s in enumerate(steps) if "killswitch.py" in (s.get("run") or "")]
            if not kills:
                continue
            if names[kills[0]].startswith("kill switch"):
                # a gate job: nothing before its gate is work
                for n in names[: kills[0] + 1]:
                    assert n.startswith(budget._PLUMBING_STEP_PREFIXES), (path.name, jname, n)
            else:
                # a write job whose only read is the re-read: a decline there
                # counts as work (steps before it ran), the fail-closed side
                assert names[kills[0]].startswith("re-read the kill switch"), (path.name, jname, names[kills[0]])
            after = [n for n in names[kills[0] + 1:] if not n.startswith(("Run actions/upload", "audit record", "Post ", "Complete"))]
            if after:
                assert not after[0].startswith(budget._PLUMBING_STEP_PREFIXES), (path.name, jname, after[0])
