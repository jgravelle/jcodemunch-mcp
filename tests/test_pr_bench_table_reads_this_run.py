"""A latency row in the PR bench table is a measurement of THIS tree, or it is
not printed (workflow FINDINGS W-52).

`scripts/pr_bench_comment.py` took its PR column from `--results` without
asking what that file was a run of. Two callers handed it the artifact
committed on the base ref: `/feature` step 7, whose bench run did not pass
`--write-results`, and `pr-gate.yml`'s token job, which runs no harness bench
at all. Both tables read base = PR, delta `+0`, `PASS` in all six latency rows
(#1018's CI comment and its three local renders). The result carries the
commit it was measured on, so the renderer asks: rows from a file that is not
a run of HEAD are left out and said to be left out, the token rows beside them
still render, and a table with nothing measured in it is refused. A delta
between two different kinds of box reads `n/c` (W-28; Standing lesson 10-09).
`python -m harness --results-out PATH` is how a branch run hands over its own
result without rewriting the tracked file.
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
hm = importlib.import_module("harness.__main__")
pbc = importlib.import_module("pr_bench_comment")

TRACKED = "harness/results/latest.json"
LATENCY_ID = "latency.search_symbols_warm_p95_ms"
CI_BOX = {"os": "Linux-6.17.0-azure", "python": "3.12.14", "cpus": 4, "runner": "github"}
LOCAL_BOX = {"os": "Windows-11-10.0", "python": "3.12.4", "cpus": 24, "runner": "local"}


def _head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, encoding="utf-8"
    ).strip()


def _result(value: float, commit: str | None, box: dict = CI_BOX) -> dict:
    env = dict(box)
    if commit is not None:
        env["commit"] = commit
    return {
        "schema": "jcm-harness-result/v1",
        "date": "2026-10-11T00:00:00",
        "env": env,
        "tiers": {"bench": {"seconds": 1.0, "ok": True}},
        "artifacts": {"self_latency": {LATENCY_ID: value}},
    }


def _bench_file(tmp_path: Path) -> Path:
    p = tmp_path / "benchmark.json"
    p.write_text(
        json.dumps(
            [
                {
                    "repo": "expressjs/express",
                    "tasks": [{"jmunch_tokens": 100, "grep_baseline_tokens": 4000}],
                }
            ]
        ),
        encoding="utf-8",
    )
    return p


def _latency_row(text: str) -> list[str] | None:
    for ln in text.splitlines():
        if LATENCY_ID in ln and ln.startswith("|"):
            return [c.strip() for c in ln.strip("|").split("|")]
    return None


@pytest.fixture
def base_artifact(monkeypatch):
    """The base ref's committed artifacts, supplied without running git."""
    base = _result(7.9, "0ldc0mm1")

    def fake_show(ref: str, path: str):
        return json.loads(json.dumps(base)) if path == TRACKED else None

    monkeypatch.setattr(pbc, "_git_show", fake_show)
    return base


# -- the property: a latency row is this tree's run --------------------------


def test_a_run_of_head_renders_its_latency_rows(base_artifact, tmp_path):
    results = tmp_path / "latest_cur.json"
    results.write_text(json.dumps(_result(9.4, _head()[:8])), encoding="utf-8")
    summary = tmp_path / "bench_table.md"

    assert pbc.main(["--results", str(results), "--summary", str(summary)]) == 0

    assert _latency_row(summary.read_text(encoding="utf-8"))[3:6] == ["7.9", "9.4", "+1.5"]


def test_equal_values_measured_on_head_are_a_result_and_render(base_artifact, tmp_path):
    results = tmp_path / "latest_cur.json"
    results.write_text(json.dumps(_result(7.9, _head()[:8])), encoding="utf-8")
    summary = tmp_path / "bench_table.md"

    assert pbc.main(["--results", str(results), "--summary", str(summary)]) == 0

    assert _latency_row(summary.read_text(encoding="utf-8"))[3:6] == ["7.9", "7.9", "+0"]


@pytest.mark.parametrize(
    "commit",
    ["0ldc0mm1", "deadbeef", "unknown", "", None, "abc"],
    ids=["the-base-artifact", "an-older-run", "unknown", "empty", "absent", "too-short"],
)
def test_results_that_are_not_a_run_of_head_are_refused_alone(
    base_artifact, tmp_path, capsys, commit
):
    """`/feature` step 7's shape: no token benchmark, so nothing is measured."""
    results = tmp_path / "latest.json"
    results.write_text(json.dumps(_result(7.9, commit)), encoding="utf-8")
    summary = tmp_path / "bench_table.md"

    rc = pbc.main(["--results", str(results), "--summary", str(summary)])

    out = capsys.readouterr()
    assert rc == 2
    assert not summary.exists(), "a refused table must not be written as evidence"
    assert "| threshold |" not in out.out
    assert "--results-out" in out.err


def test_the_token_job_keeps_its_token_rows_and_drops_the_latency_rows(
    base_artifact, tmp_path
):
    """`pr-gate.yml`'s shape: the tracked file is the base's, the token
    benchmark is this run's. The comment is still posted."""
    results = tmp_path / "latest.json"
    results.write_text(json.dumps(base_artifact), encoding="utf-8")
    summary = tmp_path / "summary.md"

    rc = pbc.main(
        [
            "--results",
            str(results),
            "--benchmark",
            str(_bench_file(tmp_path)),
            "--summary",
            str(summary),
        ]
    )

    text = summary.read_text(encoding="utf-8")
    assert rc == 0
    assert _latency_row(text) is None
    assert "`token.grand_ratio_vs_grep`" in text
    assert "Latency rows omitted" in text and "0ldc0mm1" in text


def test_a_delta_between_two_kinds_of_box_reads_not_comparable(base_artifact, tmp_path):
    results = tmp_path / "latest_cur.json"
    results.write_text(
        json.dumps(_result(170.6, _head()[:8], LOCAL_BOX)), encoding="utf-8"
    )
    summary = tmp_path / "bench_table.md"

    assert pbc.main(["--results", str(results), "--summary", str(summary)]) == 0

    text = summary.read_text(encoding="utf-8")
    row = _latency_row(text)
    assert row[3:6] == ["7.9", "170.6", "n/c"]
    assert row[6] == "**FAIL**", "the verdict is still this run against its Floor"
    assert "github Linux, 4 cpus" in text and "local Windows, 24 cpus" in text


def test_no_base_artifact_renders_the_run_without_a_delta(monkeypatch, tmp_path):
    monkeypatch.setattr(pbc, "_git_show", lambda ref, path: None)
    results = tmp_path / "latest_cur.json"
    results.write_text(json.dumps(_result(9.4, _head()[:8])), encoding="utf-8")
    summary = tmp_path / "bench_table.md"

    assert pbc.main(["--results", str(results), "--summary", str(summary)]) == 0

    assert _latency_row(summary.read_text(encoding="utf-8"))[3:6] == ["—", "9.4", "n/c"]


def test_a_failed_latency_step_still_shows_its_rows(base_artifact, tmp_path):
    """A failed step keeps its numbers under `measurement` (LEDGER L-72); the
    run where a Floor failed is the one the table must not come back empty for."""
    result = _result(0.0, _head()[:8])
    result["artifacts"]["self_latency"] = {
        "rc": 1,
        "seconds": 3.0,
        "tail": "FAIL",
        "measurement": {LATENCY_ID: 99.0},
    }
    results = tmp_path / "latest_cur.json"
    results.write_text(json.dumps(result), encoding="utf-8")
    summary = tmp_path / "bench_table.md"

    assert pbc.main(["--results", str(results), "--summary", str(summary)]) == 0

    row = _latency_row(summary.read_text(encoding="utf-8"))
    assert row[3:5] == ["7.9", "99"] and row[6] == "**FAIL**"


def test_the_refusal_names_the_cause_it_has(base_artifact, tmp_path, capsys):
    """Three ways to have nothing to table, three different sentences."""
    this_run_no_latency = _result(0.0, _head()[:8])
    this_run_no_latency["artifacts"] = {}
    a = tmp_path / "nolat.json"
    a.write_text(json.dumps(this_run_no_latency), encoding="utf-8")
    assert pbc.main(["--results", str(a)]) == 2
    err = capsys.readouterr().err
    assert "is a run of this tree" in err and "is not a run of this tree" not in err
    assert "no --benchmark was given" in err

    b = tmp_path / "base.json"
    b.write_text(json.dumps(base_artifact), encoding="utf-8")
    missing = tmp_path / "nope.json"
    assert pbc.main(["--results", str(b), "--benchmark", str(missing)]) == 2
    err = capsys.readouterr().err
    assert "is not a run of this tree" in err
    assert "does not exist" in err and "no --benchmark was given" not in err

    assert pbc.main(["--results", str(b)]) == 2
    err = capsys.readouterr().err
    assert "is not a run of this tree" in err and "no --benchmark was given" in err


def test_a_box_with_no_recorded_fields_is_not_comparable_to_anything():
    bare_a, bare_b = {"env": {"commit": "aaaaaaaa"}}, {"env": {"commit": "bbbbbbbb"}}
    assert pbc.comparable(bare_a, bare_b) is False
    assert pbc.comparable(None, _result(1.0, "aaaaaaaa")) is False
    assert pbc.comparable(_result(1.0, "a" * 8), _result(2.0, "b" * 8)) is True
    assert pbc.comparable(_result(1.0, "a" * 8), _result(2.0, "b" * 8, LOCAL_BOX)) is False


# -- the real callers, with real git and no mock -----------------------------


def _committed_artifact(tmp_path: Path) -> Path:
    """The artifact committed on HEAD, copied to a file the test owns. It can
    never be a run of the commit that contains it."""
    blob = subprocess.check_output(["git", "show", f"HEAD:{TRACKED}"], cwd=REPO)
    p = tmp_path / "committed_latest.json"
    p.write_bytes(blob)
    return p


def test_the_committed_artifact_alone_is_refused_through_real_git(tmp_path):
    p = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "pr_bench_comment.py"),
            "--base-ref",
            "HEAD",
            "--results",
            str(_committed_artifact(tmp_path)),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert p.returncode == 2, p.stdout + p.stderr
    assert "| threshold |" not in p.stdout
    assert "REFUSED" in p.stderr


def test_the_committed_artifact_beside_a_token_run_posts_token_rows_only(tmp_path):
    summary = tmp_path / "summary.md"
    p = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "pr_bench_comment.py"),
            "--base-ref",
            "HEAD",
            "--results",
            str(_committed_artifact(tmp_path)),
            "--benchmark",
            str(_bench_file(tmp_path)),
            "--summary",
            str(summary),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    text = summary.read_text(encoding="utf-8")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "`token.grand_ratio_vs_grep`" in text
    assert "latency." not in text.split("_Latency rows omitted")[0]
    assert "Latency rows omitted" in text


# -- the harness half: this run's result, to a path the caller owns ----------


@pytest.fixture
def stub_bench(monkeypatch, tmp_path):
    """A bench tier that measures nothing, with the tracked directory moved to
    one the test owns."""
    results_dir = tmp_path / "tracked_results"
    results_dir.mkdir()
    (results_dir / "latest.json").write_text('{"committed": true}\n', encoding="utf-8")
    monkeypatch.setattr(hm, "RESULTS_DIR", results_dir)

    def fake_bench(result, *, offline, write_results=False):
        result["tiers"]["bench"] = {"seconds": 0.0, "ok": True}
        result["artifacts"] = {"self_latency": {LATENCY_ID: 9.4}}
        return True

    monkeypatch.setattr(hm, "tier_bench", fake_bench)
    return results_dir


def test_results_out_writes_this_run_and_leaves_the_tracked_file(stub_bench, tmp_path):
    out = tmp_path / "evidence" / "latest_cur.json"

    rc = hm.main(["bench", "--offline", "--results-out", str(out)])

    assert rc == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["artifacts"]["self_latency"][LATENCY_ID] == 9.4
    assert pbc.is_run_of(written, _head())
    assert (stub_bench / "latest.json").read_text(encoding="utf-8") == (
        '{"committed": true}\n'
    )


def test_write_results_still_writes_the_tracked_file(stub_bench):
    assert hm.main(["bench", "--offline", "--write-results"]) == 0
    tracked = json.loads((stub_bench / "latest.json").read_text(encoding="utf-8"))
    assert tracked["artifacts"]["self_latency"][LATENCY_ID] == 9.4


def test_a_run_that_dies_leaves_no_earlier_result_under_the_name(
    stub_bench, monkeypatch, tmp_path
):
    out = tmp_path / "latest_cur.json"
    out.write_text('{"from": "an earlier run"}', encoding="utf-8")

    def boom(result, *, offline, write_results=False):
        raise RuntimeError("bench died")

    monkeypatch.setattr(hm, "tier_bench", boom)
    with pytest.raises(RuntimeError):
        hm.main(["bench", "--offline", "--results-out", str(out)])

    assert not out.exists()


@pytest.mark.parametrize("command", ["thresholds", "check", "threshold", "corpora", "warm"])
def test_results_out_is_refused_by_a_command_that_runs_no_tier(command, tmp_path, capsys):
    out = tmp_path / "latest_cur.json"
    with pytest.raises(SystemExit) as exc:
        hm.main([command, "--results-out", str(out)])
    assert exc.value.code == 2
    assert "--results-out" in capsys.readouterr().err
    assert not out.exists()


def test_results_out_refuses_the_tracked_file_and_leaves_it(stub_bench, capsys):
    """The flag unlinks its target before the run; the tracked file is
    `--write-results`' to write, and must not be the thing unlinked."""
    tracked = stub_bench / "latest.json"
    with pytest.raises(SystemExit) as exc:
        hm.main(["bench", "--offline", "--results-out", str(tracked)])
    assert exc.value.code == 2
    assert "--write-results" in capsys.readouterr().err
    assert tracked.read_text(encoding="utf-8") == '{"committed": true}\n'


# -- the command documents: a table reads the file its own step wrote --------

_TABLE_RE = re.compile(r"pr_bench_comment\.py(?P<args>[^`\n]*)")
_RESULTS_RE = re.compile(r"--results(?:=|\s+)(\S+)")
_OUT_RE = re.compile(r"--results-out(?:=|\s+)(\S+)")


def _joined(text: str) -> str:
    """Command text the way a reader runs it: a wrapped line is one line."""
    return re.sub(r"\s*\n\s+", " ", text)


def _tables_not_fed_by_results_out(text: str) -> list[str]:
    """Table commands whose `--results` is not a path a `--results-out` run
    wrote earlier in the same document. An omitted `--results` is the script's
    default, the tracked file, and is reported as `<default>`."""
    text = _joined(text)
    bad = []
    for m in _TABLE_RE.finditer(text):
        args = m.group("args")
        if "--base-ref" not in args and "--results" not in args and "--summary" not in args:
            continue  # a mention of the script, not a command
        r = _RESULTS_RE.search(args)
        if r is None:
            if "--summary" in args or "--post" in args:
                bad.append("<default>")
            continue
        path = r.group(1).rstrip("`.,)")
        written = {p.rstrip("`.,)") for p in _OUT_RE.findall(text[: m.start()])}
        if path not in written:
            bad.append(path)
    return bad


COMMAND_DOCS = sorted(
    str(p.relative_to(REPO)).replace("\\", "/")
    for p in list((REPO / ".claude" / "commands").glob("*.md"))
    + [REPO / "docs" / "workflows" / "DESIGN.md"]
)


def test_every_command_document_tables_a_file_its_own_run_wrote():
    found = {
        path: bad
        for path in COMMAND_DOCS
        if (bad := _tables_not_fed_by_results_out((REPO / path).read_text(encoding="utf-8")))
    }
    assert found == {}


def test_feature_step_seven_is_seen_by_the_scan():
    """The scan is not passing because it matches nothing."""
    text = _joined((REPO / ".claude" / "commands" / "feature.md").read_text(encoding="utf-8"))
    tabled = [
        r.group(1)
        for m in _TABLE_RE.finditer(text)
        if (r := _RESULTS_RE.search(m.group("args")))
    ]
    assert tabled and all(p in _OUT_RE.findall(text) for p in tabled)


_OLD = (
    "   `uv run python -m harness bench --offline --summary .claude/state/evidence/bench.md`\n"
    "   and then the per-criterion table against main:\n"
    "   `uv run python scripts/pr_bench_comment.py --base-ref origin/main\n"
    "   --results {results} --summary x.md`\n"
)


@pytest.mark.parametrize(
    "text, expected",
    [
        (_OLD.format(results=TRACKED), [TRACKED]),
        (_OLD.replace("--results {results}", f"--results={TRACKED}"), [TRACKED]),
        (_OLD.format(results="./" + TRACKED), ["./" + TRACKED]),
        (_OLD.replace(" --results {results}", ""), ["<default>"]),
        (
            _OLD.format(results=TRACKED).replace("--offline", "--offline --write-results"),
            [TRACKED],
        ),
        (
            "`python -m harness bench --offline --results-out ev/cur.json`\n"
            "The harness bench tier wrote it.\n"
            "`python scripts/pr_bench_comment.py --base-ref origin/main --results ev/cur.json`\n",
            [],
        ),
        (
            "`python scripts/pr_bench_comment.py --base-ref origin/main --results ev/cur.json`\n"
            "`python -m harness bench --offline --results-out ev/cur.json`\n",
            ["ev/cur.json"],
        ),
        ("`scripts/pr_bench_comment.py` renders the table (W-2).\n", []),
    ],
    ids=[
        "tracked",
        "equals-form",
        "dot-slash",
        "omitted-default",
        "write-results-then-tracked",
        "fed-by-results-out",
        "table-before-its-run",
        "a-mention-is-not-a-command",
    ],
)
def test_the_command_scan_against_each_spelling(text, expected):
    assert _tables_not_fed_by_results_out(text) == expected
