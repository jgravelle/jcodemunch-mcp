"""The competitive tier's interface, nulls, scoring and result file
(docs/competitive/DESIGN.md s1, s4.3, s5; the brief's Phase 3 item 1).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the two null adapters and jcodemunch satisfy the one interface, so a row
  can never have a hole a competitor adapter could hide in;
- null_grep is deterministic and reads files whole (ARCHAEOLOGY R24-R26):
  ranking by match count then path, top 3, payload = list + 3 files;
- F1 with the field's line tolerance counts an expected hit once and a
  stray citation against precision; a read-all answer is the floor, not 1.0;
- the band rule (harness DESIGN s5): no band under three runs, unstable
  rows are never meaningful, a gap inside the band is not meaningful, and
  the same gap outside it is (the non-vacuity pair);
- the task check refuses a task whose expected file is absent or whose
  query names a jcodemunch tool (DESIGN s4.3);
- end to end on a tiny corpus: the result file validates, every adapter has
  every axis row, the null rows exist, nothing in it is a `claims` field.
- the self corpus is `src/` without its bytecode: `__pycache__` and `*.pyc`
  never reach the git repository the tools index (CF-39: 816 compiled
  files rode along as 'source' for every recorded row before 2026-09-05).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():  # excluded from the sdist (pyproject); the tests are meaningless without it
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
sys.path.insert(0, str(COMPETE))

from adapter import Corpus, Pin, Task, corpus_digest, validate  # noqa: E402
from adapters import null_grep, null_readall  # noqa: E402
from score import band, compare, f1  # noqa: E402


def _corpus(tmp_path: Path) -> Corpus:
    root = tmp_path / "c"
    root.mkdir()
    (root / "a.py").write_text("def alpha():\n    return beta()\n\ndef beta():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("from a import alpha\n\nalpha()\nalpha()\n", encoding="utf-8")
    (root / "c.py").write_text("x = 1\n", encoding="utf-8")
    files = ("a.py", "b.py", "c.py")
    return Corpus(id="t@0", path=root, sha256=corpus_digest(root, files), files=files)


def test_every_shipped_adapter_satisfies_the_interface():
    for mod in (null_readall, null_grep):
        a = validate(mod.make())
        assert a.categories and a.interface == "null"
        assert isinstance(a.pin, Pin)


def test_task_refuses_an_unknown_category_and_an_expected_set_on_a_token_task():
    with pytest.raises(ValueError):
        Task(id="x", corpus="c", category="P9", query="q")
    with pytest.raises(ValueError):
        Task(id="x", corpus="c", category="T", query="q", expected=(("a.py", 1),))


def test_null_grep_ranks_by_match_count_then_path_and_reads_whole_files(tmp_path):
    c = _corpus(tmp_path)
    a = null_grep.make()
    ans = a.answer(c, Task(id="t", corpus=c.id, category="P2", query="alpha"), tmp_path)
    # b.py has 3 matching lines, a.py has 1, c.py none: list then the two files whole
    assert ans.payload.startswith("b.py\na.py\nfrom a import alpha\n")  # list, then b.py bare
    assert "def alpha():" in ans.payload and "x = 1" not in ans.payload  # a.py read, c.py not
    assert ans.calls == 3  # the grep plus two files (only two matched)
    assert ("b.py", 3) in ans.cited and ("a.py", 1) in ans.cited
    again = a.answer(c, Task(id="t", corpus=c.id, category="P2", query="alpha"), tmp_path)
    assert again.payload == ans.payload and again.tokens == ans.tokens


def test_null_readall_pays_the_whole_corpus_and_cites_everything(tmp_path):
    c = _corpus(tmp_path)
    ans = null_readall.make().answer(c, Task(id="t", corpus=c.id, category="T", query="x"), tmp_path)
    assert ans.cites_all and ans.calls == 3
    for rel in c.files:
        assert (c.path / rel).read_text(encoding="utf-8") in ans.payload


def test_f1_counts_an_expected_hit_once_and_a_stray_citation_against_precision():
    exp = [("a.py", 10)]
    assert f1([("a.py", 12)], exp, tolerance=2) == 1.0
    assert f1([("a.py", 13)], exp, tolerance=2) == 0.0
    assert f1([("a.py", 10), ("z.py", 1)], exp, tolerance=0) == pytest.approx(0.6667, abs=1e-3)
    assert f1([], exp, tolerance=0) == 0.0
    assert f1([("a.py", 10)], [], tolerance=0) is None
    assert f1([], exp, tolerance=0, cites_all=True) == 0.0  # no corpus size: floor
    assert f1([], exp, tolerance=0, cites_all=True, corpus_lines=100) == pytest.approx(2 * 0.01 / 1.01, abs=1e-4)
    # ONE-TO-ONE: two citations beside one expected line are one hit and one stray;
    # one citation cannot credit two expected lines (the many-to-many draft gave 1.0 to both)
    assert f1([("a.py", 10), ("a.py", 11)], exp, tolerance=2) == pytest.approx(0.6667, abs=1e-3)
    assert f1([("a.py", 10)], [("a.py", 9), ("a.py", 11)], tolerance=2) == pytest.approx(0.6667, abs=1e-3)


def test_band_and_meaningful_follow_the_harness_rule():
    # no band under three runs
    r = compare("tokens_per_task", [100.0, 110.0], [200.0, 210.0])
    assert r["band"] is None and r["meaningful"] is False and "fewer than three" in r["note"]
    # inside the band: not meaningful; outside: meaningful (the non-vacuity pair)
    inside = compare("tokens_per_task", [196.0, 197.0, 198.0], [200.0, 201.0, 202.0])
    outside = compare("tokens_per_task", [100.0, 101.0, 102.0], [200.0, 201.0, 202.0])
    assert inside["meaningful"] is False and outside["meaningful"] is True
    assert outside["delta"] == pytest.approx(101 / 201, abs=1e-4)
    assert band(200.0, 2.0, 2.0) == pytest.approx(10.0)  # 5% of 200 beats 3x2
    assert band(200.0, 5.0, 2.0) == pytest.approx(15.0)  # 3x the larger spread wins
    # an unstable competitor row is never meaningful, in either direction, and
    # its own spread must not widen the band it is judged against (the first
    # draft built the band from 3x the larger spread FIRST, so 50/100/300
    # came out "stable" inside a band of 750)
    unstable = compare("tokens_per_task", [50.0, 100.0, 300.0], [200.0, 201.0, 202.0])
    assert unstable["stable"] is False and unstable["meaningful"] is False
    unstable_jcm = compare("tokens_per_task", [100.0, 101.0, 102.0], [150.0, 200.0, 260.0])
    assert unstable_jcm["stable"] is False and unstable_jcm["meaningful"] is False
    # a diff axis reports a difference, a ratio axis a ratio
    d = compare("f1_P1", [0.8, 0.8, 0.8], [0.5, 0.5, 0.5])
    assert d["delta"] == pytest.approx(0.3) and d["meaningful"] is True
    assert compare("f1_P1", [None, None, None], [0.5, 0.5, 0.5])["note"] == "NOT COMPARABLE"


def test_task_check_refuses_absent_expected_files_and_tool_naming_queries(tmp_path):
    import run as runner

    c = _corpus(tmp_path)
    adapters = [null_grep.make(), null_readall.make()]
    ok = Task(id="ok", corpus=c.id, category="P1", query="alpha", expected=(("a.py", 1),))
    absent = Task(id="absent", corpus=c.id, category="P1", query="alpha", expected=(("zz.py", 1),))
    names = Task(id="names", corpus=c.id, category="T", query="use search_symbols for alpha")
    assert runner.check_tasks([ok], {c.id: c}, adapters) == []
    problems = runner.check_tasks([absent, names], {c.id: c}, adapters)
    assert any("zz.py" in p for p in problems) and any("search_symbols" in p for p in problems)


def test_end_to_end_writes_a_valid_result_file_with_null_rows(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "svc.py").write_text("def router_handler(req):\n    return req\n\nclass Middleware:\n    pass\n", encoding="utf-8")
    (root / "util.py").write_text("from svc import router_handler\n\ndef bind_context(ctx):\n    return router_handler(ctx)\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    tasks = {"schema": "jcm-competitive-tasks/v1", "tasks": [
        {"id": "T1", "corpus": "tiny@0", "category": "T", "query": "router handler"},
        {"id": "P1a", "corpus": "tiny@0", "category": "P1", "query": "bind_context", "expected": [["util.py", 3]], "tolerance_lines": 3},
    ]}
    tf = tmp_path / "tasks.json"
    tf.write_text(json.dumps(tasks), encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(COMPETE / "run.py"), "--corpus", f"tiny@0={root}", "--tasks", str(tf), "--runs", "1", "--out-dir", str(out), "--sandbox", "none"],
        text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=580, cwd=REPO,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    files = list(out.glob("*.json"))
    assert len(files) == 1 and (out / "latest.md").exists()
    result = json.loads(files[0].read_text(encoding="utf-8"))
    assert result["header"]["schema"] == "jcm-competitive-result/v1"
    assert "claims" not in json.dumps(result)
    # benchmarks/ ships in the sdist (DESIGN D7): no local path may reach the file
    dumped = json.dumps(result)
    assert str(tmp_path) not in dumped and str(Path.home()) not in dumped
    assert set(result["header"]["runner"]) == {"os", "python", "cpus", "ci"}
    tools = {p["name"] for p in result["header"]["pins"]}
    assert tools == {"null_readall", "null_grep", "jcodemunch"}
    rows = {(r["axis"], r["tool"]) for r in result["rows"]}
    for t in tools:
        assert ("tokens_per_task", t) in rows and ("f1_P1", t) in rows
    run0 = result["runs"][0]
    assert run0["jcodemunch"]["tiny@0"]["axes"]["index_ok"] is True, run0["jcodemunch"]["tiny@0"]["index_error"]
    jcm_p1 = [t for t in run0["jcodemunch"]["tiny@0"]["tasks"] if t["task"] == "P1a"][0]
    assert jcm_p1["error"] is None and jcm_p1["tokens"] > 0
    grep_t = [t for t in run0["null_grep"]["tiny@0"]["tasks"] if t["task"] == "T1"][0]
    readall_t = [t for t in run0["null_readall"]["tiny@0"]["tasks"] if t["task"] == "T1"][0]
    # On a two-file corpus grep can cost MORE than read-all (it pays the file list
    # on top of both files, R25): the baseline is not tuned to lose.
    assert readall_t["tokens"] > 0 and grep_t["tokens"] > 0 and readall_t["calls"] == 2
    md = (out / "latest.md").read_text(encoding="utf-8")
    assert "A competitor's README figure is not on this page" in md
    assert "| null_grep |" in md and "| null_readall |" in md


def test_the_self_corpus_carries_no_bytecode(tmp_path):
    """CF-39: a plain copytree of src/ carried every __pycache__ into the corpus
    git repo; the property is 'no compiled file is tracked', by name and suffix."""
    import run

    src = tmp_path / "src"
    (src / "pkg" / "__pycache__").mkdir(parents=True)
    (src / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (src / "pkg" / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\x00")
    (src / "pkg" / "stray.pyc").write_bytes(b"\x00")
    dst = tmp_path / "corpus" / "src"
    run.copy_source_tree(src, dst)
    run._git_init(dst.parent)
    tracked = subprocess.run(["git", "ls-files"], cwd=dst.parent, check=True, capture_output=True, text=True).stdout.split()
    assert tracked == ["src/pkg/mod.py"], tracked

