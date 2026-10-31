"""The route-recall artifacts must reflect the code that is checked in.

⚠⚠ **They did not, and nobody could have noticed.** On 2026-08-21 the committed
`results.json` reported `route@1 69.5 / @3 88.1`; a fresh run of the same harness
against the same corpus returned `71.2 / 86.4`. Two queries had moved — one
gained rank 1, one fell out of the top 3 entirely — because the catalog's
descriptions drifted under a frozen artifact. `catalog_actions` was unchanged at
91, so nothing about the corpus or the catalog SIZE gave the drift away.

That matters beyond tidiness: **`CLAUDE.md` cites `route@1 69.5%` as the evidence
that catalog-moratorium condition 1 is met.** The verdict did not change (71.2 is
still above the 60% bar), but the cited number was a fact about a tree that no
longer existed. Maintenance Practice 4 says never hand-type a benchmark number;
this is the same failure one level up — the artifact itself went stale, and a
number read out of it is as wrong as one typed by hand.

Both harnesses run in about a second, so there is no reason for this to be
checked by remembering.

Failure here means re-run and commit the artifact, NOT edit the expectation:

    PYTHONPATH=src python benchmarks/route_recall/run_route_recall.py --write
    PYTHONPATH=src python benchmarks/route_recall/run_emitted_task.py --write
    PYTHONPATH=src python benchmarks/route_recall/explain_misses.py --write

⚠ A red here on a PR that only touched tool descriptions is the signal working.
Descriptions are the router's input; changing them changes routing, and the
moratorium conditions are stated over these exact numbers.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmarks" / "route_recall"


def _load(script: str):
    path = BENCH / script
    if not path.is_file():
        pytest.skip(f"{script} not present (sdist checkout)")
    spec = importlib.util.spec_from_file_location(f"_bench_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _committed(name: str) -> dict:
    path = BENCH / name
    if not path.is_file():
        pytest.skip(f"{name} not present (sdist checkout)")
    return json.loads(path.read_text(encoding="utf-8"))["summary"]


def _diff(fresh: dict, committed: dict) -> list[str]:
    """Field-by-field, so a failure names what moved rather than dumping two
    summaries and leaving the reader to find it."""
    out = []
    for key in sorted(set(fresh) | set(committed)):
        a, b = committed.get(key, "<absent>"), fresh.get(key, "<absent>")
        if a != b:
            out.append(f"  {key}:\n      committed {a}\n      fresh     {b}")
    return out


def _row_drift(fresh: dict, committed: dict) -> list[str]:
    """Every key beside the summary, row by row (LEDGER L-266).

    ⚠⚠ The summary alone let two artifacts go stale: `emitted_task_results.json`
    moved 30 of 40 rows and `explanations.json` moved its gate counts while every
    summary check here stayed green, because a row can change without moving an
    aggregate. A failure names the key and the first rows that differ.
    """
    out = []
    for key in sorted(set(fresh) | set(committed)):
        if key == "summary":
            continue
        a, b = committed.get(key, "<absent>"), fresh.get(key, "<absent>")
        if a == b:
            continue
        if isinstance(a, list) and isinstance(b, list):
            moved = [i for i in range(max(len(a), len(b)))
                     if i >= len(a) or i >= len(b) or a[i] != b[i]]
            out.append(
                f"  {key}: {len(moved)} of {max(len(a), len(b))} rows differ "
                f"(first: {moved[:5]}; committed {len(a)} rows, fresh {len(b)})"
            )
        else:
            out.append(f"  {key}:\n      committed {a}\n      fresh     {b}")
    return out


def _assert_fresh(out: Path, name: str, rerun: str) -> None:
    fresh = json.loads(out.read_text(encoding="utf-8"))
    path = BENCH / name
    if not path.is_file():
        pytest.skip(f"{name} not present (sdist checkout)")
    committed = json.loads(path.read_text(encoding="utf-8"))
    drift = []
    if "summary" in committed or "summary" in fresh:
        drift += _diff(fresh.get("summary", {}), committed.get("summary", {}))
    drift += _row_drift(fresh, committed)
    assert not drift, (
        f"benchmarks/route_recall/{name} is stale: the harness and the "
        f"committed artifact disagree. Re-run it, do not edit this test:\n"
        f"    {rerun}\n" + "\n".join(drift)
    )


def test_the_human_corpus_artifact_is_fresh(tmp_path, monkeypatch, capsys):
    module = _load("run_route_recall.py")
    out = tmp_path / "results.json"
    monkeypatch.setattr(sys, "argv", ["run_route_recall.py", "--write", "--out", str(out)])
    assert module.main() == 0
    capsys.readouterr()
    _assert_fresh(out, "results.json", "run_route_recall.py --write")


def test_the_holdout_artifact_is_fresh(tmp_path, monkeypatch, capsys):
    """⚠⚠ The gap this file had: two artifacts gated, three written.

    `holdout_results.json` is produced by the SAME harness under `--corpus
    holdout.json` and was covered by nothing, so it rotted in the one direction
    that matters — it published **route@3 75.0** while the harness measured
    **72.7**, and carried `null` for two fields (`blind_floor_kset`,
    `route_vs_floor_pts`) that the harness had since started emitting. Caught
    2026-09-02 while re-running the gated sibling after a description trim, and
    proven pre-existing: the pre-trim tree measures 72.7 as well.

    ⚠ **The held-out set is the honest one** — frozen before the routing fixes —
    so a stale, flattering number here is worth more than a stale one on the
    tuned corpus. Same family as Practice 4: several artifacts mirror one run,
    and re-syncing the ones with tests is how the untested one goes stale.
    """
    module = _load("run_route_recall.py")
    out = tmp_path / "holdout_results.json"
    monkeypatch.setattr(sys, "argv", [
        "run_route_recall.py", "--write",
        "--corpus", str(BENCH / "holdout.json"),
        "--out", str(out),
    ])
    assert module.main() == 0
    capsys.readouterr()
    _assert_fresh(
        out,
        "holdout_results.json",
        "run_route_recall.py --write --corpus benchmarks/route_recall/holdout.json "
        "--out benchmarks/route_recall/holdout_results.json",
    )


def test_the_emitted_task_artifact_is_fresh(tmp_path, monkeypatch, capsys):
    module = _load("run_emitted_task.py")
    out = tmp_path / "emitted_task_results.json"
    monkeypatch.setattr(module, "RESULTS", out)
    monkeypatch.setattr(sys, "argv", ["run_emitted_task.py", "--write"])
    assert module.main() == 0
    capsys.readouterr()
    _assert_fresh(out, "emitted_task_results.json", "run_emitted_task.py --write")


def test_the_explanations_artifact_is_fresh(tmp_path, monkeypatch, capsys):
    """`explanations.json` was gated by nothing (LEDGER L-266): its gate counts
    moved under description edits while every other artifact here was checked.
    It explains the committed `results.json`, which the test above keeps fresh."""
    module = _load("explain_misses.py")
    out = tmp_path / "explanations.json"
    monkeypatch.setattr(module, "OUT", out)
    monkeypatch.setattr(
        sys, "argv", ["explain_misses.py", "--write", "--results", str(BENCH / "results.json")]
    )
    assert module.main() == 0
    capsys.readouterr()
    _assert_fresh(out, "explanations.json", "explain_misses.py --write")


def test_every_reported_recall_has_a_k_matched_floor():
    """⚠⚠ The bug this whole change exists for.

    `run_emitted_task.py` compared route's THREE guesses against a baseline
    allowed ONE, and a comment argued that was the fair comparison. It is not,
    and it was wrong in route's favour: against the best constant 3-SET,
    `strict@3` is -30.0 rather than +17.5.

    A recall figure at k whose floor was computed at a different k is not a
    comparison. This asserts the pairing exists for every k either harness
    reports, so the next `@5` or `@10` cannot arrive without its own bar.
    """
    human = _committed("results.json")
    assert "blind_floor_kset" in human, "the human harness reports no floor at all"
    for k in human["route_recall"]:
        assert k in human["blind_floor_kset"], f"route_recall has {k} with no {k} floor"

    emitted = _committed("emitted_task_results.json")
    assert emitted["blind_floor_kset"]["k"] == 3, "the k-set floor must state its own k"
    for metric in ("strict", "exact", "family"):
        assert f"{metric}@3" in emitted["vs_kset_floor_pts"], (
            f"{metric}@3 is reported with no k-matched floor delta"
        )


def test_the_emitted_corpus_records_that_it_cannot_discriminate():
    """⚠⚠ A corpus a fixed list already saturates cannot measure a router.

    The best constant 3-set scores 100% exact on the emitted corpus. This is not
    a threshold to pass or fail — it is a property of the sample that must stay
    visible, because every `@3` figure measured against it is bounded above by a
    baseline that needs no routing at all. If a future corpus fixes this, the
    assertion should be re-derived, not deleted.
    """
    emitted = _committed("emitted_task_results.json")
    exact_floor = emitted["blind_floor_kset"]["exact"]["pct"]
    assert exact_floor == 100.0, (
        f"the emitted corpus's best constant 3-set now scores {exact_floor}% "
        f"exact rather than 100%. If the corpus was resampled for label "
        f"diversity that is the goal, and this test should be rewritten to "
        f"assert the new bound — after re-reading whether the @3 figures now "
        f"mean something they did not before."
    )
