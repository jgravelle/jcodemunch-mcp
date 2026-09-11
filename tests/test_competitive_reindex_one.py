"""STANDARD criterion 3(b), one-file reindex cost, enters the competitive tier
as an axis (DESIGN s2; CF-61).

`reindex_one(corpus, path, scratch)` is on the adapter interface: the wall
seconds of re-indexing ONE file the tool already holds, through the tool's
documented incremental path, as a `ReindexReport` (seconds, the path, the
mode: `incremental`, or `full_reindex` where the tool's only path re-indexes
everything and reports that as its cost). None is NOT COMPARABLE: a tool with
no index step (the nulls), or a failed step. An adapter WITHOUT the method is
NOT COMPARABLE too, and the row says which of the two it is, so a competitor
row that has not implemented it (each adapter's own PR) is never mistaken for
a tool that cannot. The file is chosen by one rule both the runner and our
worker read (`adapter.reindex_target`): the first expected file of the
corpus's tasks, else its first file. Our worker re-parses that file through
`index_folder(paths=[...], force_reparse=True)`, the incremental path the
watcher and `refresh` use, without editing the file: a pinned corpus is a
checkout and the container mounts it read-only, and the cost is the
re-parse and the incremental save, not the edit.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPETE = ROOT / "benchmarks" / "competitive"

# Bound at COLLECTION time, before any fixture in any worker runs, so the
# identity assertion below holds `Pin` as `adapters.jcodemunch` first saw it
# under every xdist ordering, not only when the sandbox file ran first (review
# round 1 of the F-25 identity PR).
sys.path.insert(0, str(COMPETE))
try:
    _JCM_ADAPTER = importlib.import_module("adapters.jcodemunch")
finally:
    sys.path.remove(str(COMPETE))


@pytest.fixture(scope="module")
def mods():
    # Never pop these from sys.modules first: `adapters.*` imported by an earlier
    # test file hold the `adapter` module that was current THEN, and a re-import
    # here makes a second `Pin` class, so `validate` (isinstance) fails under one
    # xdist ordering and passes under the rest (five of eight gate jobs on #651's first attempt).
    sys.path.insert(0, str(COMPETE))
    try:
        return {m: importlib.import_module(m) for m in ("adapter", "score", "run")}
    finally:
        sys.path.remove(str(COMPETE))


def _corpus(adapter, tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import f\n", encoding="utf-8")
    return adapter.Corpus(id="tiny@0", path=tmp_path, files=("a.py", "b.py"), sha256="0" * 64)


def test_the_axis_is_scored_as_a_ratio(mods):
    score = mods["score"]
    assert "reindex_one_seconds" in score.RATIO_AXES
    assert "reindex_one_seconds" not in score.DIFF_AXES


def test_the_target_file_is_one_rule_shared_by_runner_and_worker(mods, tmp_path):
    adapter = mods["adapter"]
    c = _corpus(adapter, tmp_path)
    tasks = [adapter.Task(id="T1", corpus="tiny@0", category="T", query="f"),
             adapter.Task(id="P1", corpus="tiny@0", category="P1", query="f", expected=(("b.py", 1),), tolerance_lines=3)]
    assert adapter.reindex_target(c, tasks) == "b.py"
    assert adapter.reindex_target(c, tasks[:1]) == "a.py"


def test_a_null_has_no_index_and_reports_not_comparable(mods, tmp_path):
    adapter = mods["adapter"]
    c = _corpus(adapter, tmp_path)
    sys.path.insert(0, str(COMPETE))
    try:
        for name in ("null_readall", "null_grep"):
            a = importlib.import_module(f"adapters.{name}").make()
            assert a.reindex_one(c, "a.py", tmp_path) is None
    finally:
        sys.path.remove(str(COMPETE))


def test_the_runner_records_the_axis_and_says_why_a_row_is_not_comparable(mods, tmp_path):
    adapter, run = mods["adapter"], mods["run"]
    c = _corpus(adapter, tmp_path)
    tasks = [adapter.Task(id="P1", corpus="tiny@0", category="P1", query="f", expected=(("b.py", 1),), tolerance_lines=3)]

    class Base:
        pin = adapter.Pin(registry="none", package="x", version="0")
        categories = frozenset({"P1"})
        interface = "python"

        def index(self, corpus, scratch):
            return adapter.IndexReport(seconds=1.0, ok=True, files_indexed=2)

        def answer(self, corpus, task, scratch):
            return adapter.Answer(payload="x", tokens=1, calls=1, latency_ms=[1.0], cited=frozenset({("b.py", 1)}))

        def tools_list_tokens(self):
            return None

        def version(self):
            return "0"

    class Incremental(Base):
        name = "inc"
        seen = None

        def reindex_one(self, corpus, path, scratch):
            Incremental.seen = path
            return adapter.ReindexReport(seconds=0.25, path=path, mode="incremental")

    class Full(Base):
        name = "full"

        def reindex_one(self, corpus, path, scratch):
            return adapter.ReindexReport(seconds=4.0, path=path, mode="full_reindex")

    class Absent(Base):
        name = "absent"

    class NoIndex(Base):
        name = "noindex"

        def reindex_one(self, corpus, path, scratch):
            return None

    class Failed(Base):
        name = "failed"

        def reindex_one(self, corpus, path, scratch):
            return adapter.ReindexReport(seconds=None, path=path, mode=None, error="re-index raised: boom")

    out = run.run_once([Incremental(), Full(), Absent(), NoIndex(), Failed()], {"tiny@0": c}, tasks, tmp_path / "scratch")
    # four NOT COMPARABLE causes, four different rows (review round 1)
    assert out["noindex"]["tiny@0"]["axes"]["reindex_one_seconds"] is None
    assert out["noindex"]["tiny@0"]["reindex_one"] == {"path": "b.py", "mode": None, "note": "no index step"}
    assert out["failed"]["tiny@0"]["axes"]["reindex_one_seconds"] is None
    assert out["failed"]["tiny@0"]["reindex_one"] == {"path": "b.py", "mode": None, "note": "re-index raised: boom"}
    assert Incremental.seen == "b.py"
    assert out["inc"]["tiny@0"]["axes"]["reindex_one_seconds"] == 0.25
    assert out["inc"]["tiny@0"]["reindex_one"] == {"path": "b.py", "mode": "incremental"}
    assert out["full"]["tiny@0"]["axes"]["reindex_one_seconds"] == 4.0
    assert out["full"]["tiny@0"]["reindex_one"]["mode"] == "full_reindex"
    assert out["absent"]["tiny@0"]["axes"]["reindex_one_seconds"] is None
    assert out["absent"]["tiny@0"]["reindex_one"] == {"path": "b.py", "mode": None, "note": "adapter has no reindex_one (CF-61)"}


def test_a_failed_index_leaves_the_axis_none_with_the_other_axes(mods, tmp_path):
    adapter, run = mods["adapter"], mods["run"]
    c = _corpus(adapter, tmp_path)

    class Broken:
        name = "broken"
        pin = adapter.Pin(registry="none", package="x", version="0")
        categories = frozenset({"P1"})
        interface = "python"

        def index(self, corpus, scratch):
            return adapter.IndexReport(seconds=None, ok=False, stderr_tail="boom")

        def answer(self, corpus, task, scratch):
            raise AssertionError("not reached")

        def reindex_one(self, corpus, path, scratch):
            raise AssertionError("not reached after a failed index")

        def tools_list_tokens(self):
            return None

        def version(self):
            return "0"

    out = run.run_once([Broken()], {"tiny@0": c}, [], tmp_path / "scratch")
    assert out["broken"]["tiny@0"]["axes"]["reindex_one_seconds"] is None
    assert out["broken"]["tiny@0"]["not_runnable"].startswith("index failed")


def test_our_worker_reparses_the_target_through_the_incremental_path(mods, tmp_path):
    """The worker's fifth argument names the file; the answers file carries the
    cost under `reindex_one`, measured AFTER every task so no answer pays for it."""
    import subprocess

    adapter = mods["adapter"]
    (tmp_path / "corpus").mkdir()
    c = _corpus(adapter, tmp_path / "corpus")
    store, out = tmp_path / "store", tmp_path / "out"
    store.mkdir()
    out.mkdir()
    (out / "tasks.json").write_text(json.dumps([{"id": "P1", "category": "P1", "query": "f"}]), encoding="utf-8")
    import os

    env = dict(os.environ, CODE_INDEX_PATH=str(store), PYTHONPATH=str(ROOT / "src"), JCODEMUNCH_TRUSTED_FOLDERS=str(c.path), JCODEMUNCH_LIVE_JOURNAL="0")
    proc = subprocess.run([sys.executable, str(COMPETE / "sandbox" / "jcm_worker.py"), str(c.path), str(store), str(out / "tasks.json"), str(out / "answers.json"), "b.py"],
                          env=env, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    res = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    r = res["reindex_one"]
    assert r["path"] == "b.py" and r["success"] is True and r["secs"] > 0 and r["mode"] == "incremental"
    assert r["files_reparsed"] == 1
    assert (c.path / "b.py").read_text(encoding="utf-8") == "from a import f\n"  # never edited


def test_our_adapter_reports_the_workers_measurement(mods, tmp_path):
    adapter, run = mods["adapter"], mods["run"]
    a = run.load_adapter("jcodemunch", "none")
    c = _corpus(adapter, tmp_path)
    a._cache[(c.id, str(tmp_path))] = {"index": {"secs": 1.0, "success": True}, "answers": {},
                                       "reindex_one": {"secs": 0.3, "path": "b.py", "success": True, "mode": "incremental", "files_reparsed": 1}}
    r = a.reindex_one(c, "b.py", tmp_path)
    assert r == adapter.ReindexReport(seconds=0.3, path="b.py", mode="incremental")
    # a failed re-index and an unmeasured one each carry their reason to the row (review round 1)
    a._cache[(c.id, str(tmp_path))]["reindex_one"] = {"secs": 0.3, "path": "b.py", "success": False, "mode": None, "error": "boom"}
    assert a.reindex_one(c, "b.py", tmp_path) == adapter.ReindexReport(seconds=None, path="b.py", mode=None, error="boom")
    del a._cache[(c.id, str(tmp_path))]["reindex_one"]
    assert a.reindex_one(c, "b.py", tmp_path) == adapter.ReindexReport(seconds=None, path="b.py", mode=None, error="worker did not measure")


def test_the_summary_names_the_mode_beside_the_axis(mods):
    run = mods["run"]
    header = {"date": "d", "jcm_commit": "c", "jcm_version": "v", "runs": 1, "sandbox": "none", "tree_dirty": False,
              "scorer_sha256": "f" * 64, "corpora": [{"id": "tiny@0", "files": 2, "sha256": "0" * 64}],
              "pins": [{"name": "jcodemunch", "registry": "tree", "package": "j", "version": "1", "ran_as": "1"},
                       {"name": "other", "registry": "npm", "package": "o", "version": "1", "ran_as": "1"}]}
    score = mods["score"]

    def row(axis, tool, measured, delta, note):
        return {"axis": axis, "tool": tool, "corpus": "tiny@0", "measured": measured, "spread": None, "jcm": 0.3, "jcm_spread": None,
                "delta": delta, "band": None, "meaningful": False, "stable": None, "note": note, "runs": [measured]}

    # the summary indexes every (axis, tool, corpus) row, so the synthetic file carries them all
    rows = [row(ax, t, None, None, "NOT COMPARABLE") for ax in (*score.RATIO_AXES, *score.DIFF_AXES) if ax != "reindex_one_seconds" for t in ("jcodemunch", "other")]
    rows += [row("reindex_one_seconds", "jcodemunch", 0.3, 1.0, "fewer than three runs: no band (harness DESIGN s5)"),
             row("reindex_one_seconds", "other", None, None, "NOT COMPARABLE")]
    runs = [{"jcodemunch": {"tiny@0": {"axes": {}, "tasks": [], "reindex_one": {"path": "b.py", "mode": "incremental"}}},
             "other": {"tiny@0": {"axes": {}, "tasks": [], "reindex_one": {"path": "b.py", "mode": None, "note": "adapter has no reindex_one (CF-61)"}}}}]
    md = run.render_md({"header": header, "rows": rows, "runs": runs, "capability_only": [], "tools_not_called": [], "not_runnable": []})
    assert "## reindex_one_seconds" in md
    assert "`b.py`" in md and "incremental" in md and "adapter has no reindex_one (CF-61)" in md


def test_the_adapters_hold_the_same_pin_class_the_fixture_imported(mods):
    """F-25's property, not its spelling (review note on #652): whatever a fixture
    does to `sys.modules`, the `Pin` class `adapters.jcodemunch` bound at its
    import is the one `adapter.Pin` names now, or `validate`'s isinstance fails
    under one worker ordering. A scan over `sys.modules.pop` cannot see
    `importlib.reload` or `sys.modules.clear()`; this can. The adapter is the
    module-scope `_JCM_ADAPTER`, imported at collection, so the check does not
    need another file to have imported it first (review round 1)."""
    assert _JCM_ADAPTER.Pin is mods["adapter"].Pin


def test_no_competitive_test_pops_the_tier_modules():
    """F-25: a fixture that pops `adapter`, `run`, `sandbox`, `score`, `findings` or
    `trend` from `sys.modules` and re-imports makes a second `Pin` class behind
    `adapters.*`, and `validate` fails under one xdist ordering out of many. The
    rule lived in three comments; this is the scan that fails on a reintroduction."""
    import re

    offenders = []
    for path in sorted((ROOT / "tests").glob("test_competitive_*.py")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"sys\.modules\.pop\(|del sys\.modules\[", src):
            offenders.append(f"{path.name}:{src[: m.start()].count(chr(10)) + 1}")
    assert not offenders, offenders
