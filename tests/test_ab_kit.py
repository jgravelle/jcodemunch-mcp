"""Unit tests for the self-run A/B kit: grading and statistics, and the runner's spending rules
(no network, no model calls; the `claude` call is replaced by a fake that writes a stream)."""
import argparse, importlib.util, json, pathlib, sys
import pytest
KIT = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "ab-kit"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, KIT / file)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


R = _load("ab_report", "report.py")
L = _load("ab_run_lookups", "run_lookups.py")
ENTRY = {"symbols": [{"name": "handle", "owner": "Router", "file": "lib/router/index.js"}]}


def test_grade_needs_symbol_and_file():
    assert R.grade("lib/router/index.js:136 Router.handle", ENTRY)
    assert R.grade(r"lib\router\index.js:136 handle", ENTRY)
    assert not R.grade("lib/application.js:10 handle", ENTRY)
    assert not R.grade("lib/router/index.js:10 route", ENTRY)


def test_grade_alt_files():
    e = {"symbols": [{"name": "Binding", "file": "binding/binding.go", "alt_files": ["binding/binding_nomsgpack.go"]}]}
    assert R.grade("binding/binding_nomsgpack.go:29 Binding", e)


def test_wilcoxon_exact_small():
    assert R.wilcoxon_exact([1, 2, 3, 4, 5]) == 2 / 32   # all positive, n=5: two-sided 1/16
    assert R.wilcoxon_exact([0, 0]) == 1.0
    assert R.wilcoxon_exact([1, -2, 3, -4, 5, -6]) > 0.5


def test_hodges_lehmann_and_mcnemar():
    assert R.hodges_lehmann([1.0, 2.0, 3.0]) == 2.0
    assert R.mcnemar_exact(0, 0) == 1.0
    assert abs(R.mcnemar_exact(0, 6) - 2 / 64) < 1e-12


# ---- the runner's spending rules -------------------------------------------------------------

def _args(**kw):
    base = dict(cap=2.0, session_cap=0.5, parent_model="opus", explore_model="haiku", yes=False)
    base.update(kw); return argparse.Namespace(**base)


class _Proc:
    returncode = 0


def _fake_claude(monkeypatch, lines):
    """Replace the `claude` call: write `lines` to the session's stream and return."""
    monkeypatch.setattr(L.shutil, "which", lambda name: None)  # plain `claude`, no cmd.exe

    def run(cmd, **kw):
        for ev in lines: kw["stdout"].write(json.dumps(ev) + "\n")
        return _Proc()
    monkeypatch.setattr(L.subprocess, "run", run)


def _session(tmp_path, name="q1-G-s1", **kw):
    ws = tmp_path / "ws"; ws.mkdir(exist_ok=True)
    return L.session(tmp_path, name, ws, "G", {"id": "q1", "question": "where?"}, 1, _args(**kw), "jcodemunch-mcp", None)


def test_cap_is_required(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_lookups.py", "--repo", ".", "--lookups", "x.json"])
    with pytest.raises(SystemExit) as e: L.main()
    assert e.value.code == 2  # argparse refuses before anything runs


def test_a_reported_cost_replaces_the_budget_charge(tmp_path, monkeypatch):
    _fake_claude(monkeypatch, [{"type": "result", "result": "a.py:1 f", "total_cost_usd": 0.12}])
    _session(tmp_path)
    assert L.spent(tmp_path) == pytest.approx(0.12)


def test_a_session_with_no_reported_cost_stays_charged_at_its_budget(tmp_path, monkeypatch):
    _fake_claude(monkeypatch, [])  # crashed or killed: no result event
    _session(tmp_path, session_cap=0.4)
    assert L.spent(tmp_path) == pytest.approx(0.4)


def test_the_budget_is_charged_before_the_session_starts(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(L.shutil, "which", lambda name: None)

    def run(cmd, **kw):
        seen.append(L.spent(tmp_path)); return _Proc()
    monkeypatch.setattr(L.subprocess, "run", run)
    _session(tmp_path, session_cap=0.3)
    assert seen == [pytest.approx(0.3)]


def test_the_key_check_deleting_a_stream_does_not_delete_its_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    _fake_claude(monkeypatch, [{"type": "result", "result": "sk-test-not-a-real-key", "total_cost_usd": 0.2}])
    rec = _session(tmp_path)
    assert rec["key_found_in_outputs"]
    assert not (tmp_path / "runs" / "q1-G-s1" / "stream.jsonl").exists()
    assert L.spent(tmp_path) == pytest.approx(0.2)


def test_an_interrupted_session_keeps_its_charge_and_its_files(tmp_path, monkeypatch):
    L.charge(tmp_path, "earlier", 0.5, state="reserved")  # the runner stopped mid-session
    (tmp_path / "runs" / "q1-G-s1").mkdir(parents=True)
    _fake_claude(monkeypatch, [{"type": "result", "result": "a.py:1 f", "total_cost_usd": 0.1}])
    _session(tmp_path)
    assert (tmp_path / "runs" / "q1-G-s1-interrupted-1").is_dir()
    assert L.spent(tmp_path) == pytest.approx(0.6)


def test_billing_starts_only_on_yes():
    assert L.confirm(_args(yes=True), ask=lambda p: pytest.fail("asked despite --yes"), tty=False)
    assert L.confirm(_args(), ask=lambda p: "yes", tty=True)
    assert not L.confirm(_args(), ask=lambda p: "", tty=True)
    assert not L.confirm(_args(), ask=lambda p: "y", tty=True)
    with pytest.raises(SystemExit):
        L.confirm(_args(), ask=lambda p: "yes", tty=False)


def test_a_cmd_metacharacter_in_the_command_refuses():
    L.shell_safe(["claude.cmd", "-p", r"C:\results\run1\mcp-config.json"])
    for bad in ("a&b", "a|b", "a^b", "a%b%", 'a"b', "a>b"):
        with pytest.raises(SystemExit): L.shell_safe(["claude.cmd", "--mcp-config", bad])


def test_unrecorded_spend_is_never_shown_as_zero():
    assert "$" not in R.spend_text({})
    assert "not recorded" in R.spend_text({"spent_usd": None})
    assert R.spend_text({"spent_usd": 1.5, "finished_utc": "x"}) == "spend $1.50"
    assert "did not finish" in R.spend_text({"spent_usd": 1.5})


def _results(tmp_path, runs):
    """A results folder and lookups file as run_lookups.py writes them, with these run.json rows."""
    import hashlib
    lookups = tmp_path / "lookups.json"
    raw = json.dumps({"lookups": [{"id": "q1", "question": "where?", "symbols": [ENTRY["symbols"][0]]}]}).encode()
    lookups.write_bytes(raw)
    res = tmp_path / "res"
    for i, run in enumerate(runs):
        d = res / "runs" / f"r{i}"; d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"lookup": "q1", "seed": 1, "answer": "", "jcm_calls": 0, **run}), encoding="utf-8")
    (res / "manifest.json").write_text(json.dumps({"lookups_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
    return res, lookups


def _report(res, lookups):
    """Run report.py the way a user or a script does: as its own process. The exit status is the contract."""
    import subprocess
    return subprocess.run([sys.executable, "-I", str(KIT / "report.py"), "--results", str(res), "--lookups", str(lookups)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_a_run_with_no_pairs_reports_nothing_as_measured(tmp_path):
    res, lookups = _results(tmp_path, [{"arm": "G", "wiring_problems": ["no init event"], "explore_tokens": {"total": 0}}])
    p = _report(res, lookups)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "No paired sessions" in p.stdout and "G 1, J 0" in p.stdout
    for claim in ("Hodges-Lehmann", "Wilcoxon", "McNemar", "never called jCodeMunch"):
        assert claim not in p.stdout


def test_pairs_with_no_tokens_print_no_ratio_or_p_value(tmp_path):
    res, lookups = _results(tmp_path, [{"arm": "G", "explore_tokens": {"total": 0}},
                                       {"arm": "J", "explore_tokens": {"total": 0}}])
    p = _report(res, lookups)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "not measured" in p.stdout
    assert "Hodges-Lehmann" not in p.stdout and "Wilcoxon" not in p.stdout and "nan" not in p.stdout
