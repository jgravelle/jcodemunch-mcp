"""The competitive tier's fairness checks and its pinned corpus set
(docs/competitive/DESIGN.md s3.3, s3.4, s4.2, s4.3, s10; the brief's Phase 3
item 3).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- corpus_check judges the SET, criterion by criterion, and each of (a)-(e)
  fails ALONE over a synthetic description set with every threshold read
  from corpus_policy.json (no literal restated here: a moved threshold
  moves these tests with it);
- the policy file carries every key corpus_check reads, and a missing one
  refuses rather than defaulting;
- corpora.json pins every corpus by a full 40-hex SHA with a domain, and
  every task file's corpus id is a set member or self@HEAD (a task for a
  corpus no run can fetch is unanswerable by construction);
- task_check.split keeps a task only two non-null adapters can answer out
  of the head-to-head set, symmetrically; tools_not_called names a tool
  that cited nothing on every P task of a corpus and nothing else;
- the task generators refuse a hand-given definition line that no longer
  carries the definition (a moved line would grade every tool against a
  wrong answer), and every generated P task's expected files are inside
  the task's own corpus at the SHA (asserted over the committed task files
  without the checkouts: file shapes, ids, categories, tolerances).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "tasks"))

import corpus_check  # noqa: E402
import task_check  # noqa: E402
from adapter import Corpus, Task  # noqa: E402
from adapters import null_grep, null_readall  # noqa: E402

POLICY = corpus_check.load_policy()


def _desc(cid, langs, longest=1, files=1, domain="d"):
    return {"id": cid, "files": files, "languages": langs, "longest_file": "f", "longest_lines": longest, "domain": domain}


def _passing_set():
    """A set that clears every criterion with margin, built from the policy."""
    n = POLICY["min_languages"]
    langs = {f"lang{i}": 10 for i in range(n)}  # equal shares: each 1/n, under the cap while n >= 2
    return [
        _desc("a", langs, longest=POLICY["big_file_min_lines"] + 1, files=POLICY["big_corpus_min_files"] + 1, domain="one"),
        _desc("b", dict(langs), domain="two"),
    ]


def test_passing_set_passes():
    problems, verdict = corpus_check.check(_passing_set(), POLICY)
    assert problems == [] and verdict["ok"] is True
    assert set(verdict["policy"]) == set(corpus_check.KEYS)
    assert len(verdict["languages_counted"]) == POLICY["min_languages"]


@pytest.mark.parametrize("criterion", ["(a)", "(b)", "(c)", "(d)", "(e)"])
def test_each_criterion_fails_alone(criterion):
    descs = _passing_set()
    if criterion == "(a)":
        keep = list(descs[0]["languages"])[: POLICY["min_languages"] - 1]
        for d in descs:
            d["languages"] = {k: d["languages"][k] for k in keep}
        # (a) drops a language, which may push the top share over the cap; keep (e) clear by many equal languages
        if len(keep) < 2:
            pytest.skip("min_languages 2 or less: (a) and (e) cannot be separated")
        if 1 / len(keep) > POLICY["max_language_share"]:
            pytest.skip("the cap is under 1/(min_languages-1): (a) cannot fail without (e)")
    elif criterion == "(b)":
        descs[0]["longest_lines"] = POLICY["big_file_min_lines"] - 1
    elif criterion == "(c)":
        descs[0]["files"] = POLICY["big_corpus_min_files"]  # "more than", so equal fails
    elif criterion == "(d)":
        for d in descs:
            d["domain"] = "same"
    elif criterion == "(e)":
        # just over the cap, with every other language still at or above language_min_share (so (a) holds)
        import math
        first = next(iter(descs[0]["languages"]))
        others = sum(c for d in descs for lang, c in d["languages"].items() if lang != first)  # the SET's other files
        first_elsewhere = sum(d["languages"][first] for d in descs[1:])
        cap = POLICY["max_language_share"]
        descs[0]["languages"][first] = math.ceil(cap * others / (1 - cap)) + 1 - first_elsewhere
    problems, verdict = corpus_check.check(descs, POLICY)
    assert [p[:3] for p in problems] == [criterion], problems
    assert verdict["ok"] is False and verdict["problems"] == problems


def test_language_min_share_excludes_a_trace_language():
    descs = _passing_set()
    descs[0]["languages"]["trace"] = 1  # far under language_min_share of the set
    _, verdict = corpus_check.check(descs, POLICY)
    assert "trace" not in verdict["languages_counted"]
    assert "trace" in verdict["language_shares"]


def test_policy_file_has_every_key_and_a_missing_one_refuses(tmp_path):
    doc = json.loads((COMPETE / "corpus_policy.json").read_text(encoding="utf-8"))
    assert all(k in doc for k in corpus_check.KEYS)
    short = {k: v for k, v in doc.items() if k != "max_language_share"}
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(short), encoding="utf-8")
    with pytest.raises(SystemExit, match="max_language_share"):
        corpus_check.load_policy(p)


def test_describe_counts_code_files_only(tmp_path):
    (tmp_path / "a.py").write_text("x\n" * 5, encoding="utf-8")
    (tmp_path / "b.md").write_text("y\n" * 50, encoding="utf-8")
    (tmp_path / "c.go").write_text("z\n" * 7, encoding="utf-8")
    d = corpus_check.describe("t", tmp_path, ["a.py", "b.md", "c.go"], "dom")
    assert d["languages"] == {"python": 1, "go": 1}
    assert d["longest_file"] == "c.go" and d["longest_lines"] == 7  # the markdown is longer and not counted
    assert d["files"] == 3


# --- the pinned set and the task files ------------------------------------

SET = json.loads((COMPETE / "corpora.json").read_text(encoding="utf-8"))["corpora"]
TASK_FILES = sorted((COMPETE / "tasks").glob("*.json"))


def test_corpora_json_pins_full_shas_with_domains():
    assert len(SET) >= 5
    ids = set()
    for e in SET:
        assert re.fullmatch(r"[0-9a-f]{40}", e["sha"]), e["id"]
        assert e["id"] == f"{e['repo']}@{e['sha'][:7]}"
        assert e["domain"] and e["description"]
        ids.add(e["id"])
    assert len(ids) == len(SET)
    assert len({e["domain"] for e in SET}) > 1


def test_every_task_file_loads_and_names_a_set_member():
    import run as runner

    members = {e["id"] for e in SET} | {"self@HEAD"}
    seen = set()
    assert len(TASK_FILES) >= 9
    for tp in TASK_FILES:
        tasks = runner.load_tasks(tp)
        assert tasks, tp.name
        for t in tasks:
            assert t.corpus in members, (tp.name, t.id, t.corpus)
            assert t.id not in seen, t.id
            seen.add(t.id)
            if t.category == "P4":
                assert t.tolerance_lines >= 1000 and all(n == 0 for _, n in t.expected), t.id
            if t.category in ("P1", "P2"):
                assert t.expected, t.id
                # line-level, or file-level (line 0) only under a wide tolerance (self.json's shape)
                assert all(n >= 1 for _, n in t.expected) or t.tolerance_lines >= 1000, t.id
            if t.category == "P1":
                assert len(t.expected) == 1, t.id
                assert t.tolerance_lines == 0 or t.expected[0][1] == 0, t.id
            assert t.source, t.id


def test_generated_task_files_say_which_generator_and_corpus():
    for tp in TASK_FILES:
        doc = json.loads(tp.read_text(encoding="utf-8"))
        if "generator" in doc:
            assert (COMPETE / "tasks" / doc["generator"]).is_file()
            assert all(t["corpus"] == doc["corpus"] for t in doc["tasks"])
            assert doc["generator"] != "from_sverklo.py" or "CC-BY-4.0" in doc["tasks"][0]["source"]


def test_from_sverklo_refuses_a_moved_definition_line(tmp_path):
    import from_sverklo

    (tmp_path / "x.js").write_text("var a = 1;\nfunction map(x) {}\nvar b = 2;\n", encoding="utf-8")
    def_re = re.compile(r"function\s+map\b")
    from_sverklo._verify_p1(tmp_path, "x.js", 2, "map", def_re)
    with pytest.raises(SystemExit, match="does not define 'map'"):
        from_sverklo._verify_p1(tmp_path, "x.js", 3, "map", def_re)
    with pytest.raises(SystemExit, match="does not define"):
        from_sverklo._verify_p1(tmp_path, "x.js", 99, "map", def_re)


def test_from_sverklo_grep_rules(tmp_path):
    import from_sverklo

    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.js").write_text("function foo() {}\nfoo();\nvar foobar = foo;\n", encoding="utf-8")
    (tmp_path / "other.js").write_text("foo();\n", encoding="utf-8")
    (tmp_path / "lib" / "b.md").write_text("foo\n", encoding="utf-8")
    files = ["lib/a.js", "other.js", "lib/b.md"]
    hits = from_sverklo._grep(tmp_path, files, re.compile(r"\bfoo\b"), ("lib",), ".js")
    assert [(f, n) for f, n, _ in hits] == [("lib/a.js", 1), ("lib/a.js", 2), ("lib/a.js", 3)]  # word boundary, path prefix, suffix
    hits = from_sverklo._grep(tmp_path, files, re.compile(r"\bfoo\b"), ("",), ".js")
    assert ("other.js", 1) in [(f, n) for f, n, _ in hits]
    imp = from_sverklo._files_matching(tmp_path, files, re.compile(r"foo\("), ".js", ("test",))
    assert imp == ["lib/a.js", "other.js"]


# --- task_check --------------------------------------------------------

class _Tool:
    def __init__(self, name, cats, interface="mcp-stdio"):
        self.name, self.categories, self.interface = name, cats, interface


def _corpus(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    return Corpus(id="t@0", path=tmp_path, sha256="0" * 64, files=("a.py",))


def test_split_is_symmetric_and_needs_two_answerers():
    p1 = Task(id="x", corpus="t@0", category="P1", query="f", expected=(("a.py", 1),))
    p2 = Task(id="y", corpus="t@0", category="P2", query="f", expected=(("a.py", 1),))
    t = Task(id="z", corpus="t@0", category="T", query="f")
    nulls = [null_grep.ADAPTER, null_readall.ADAPTER] if hasattr(null_grep, "ADAPTER") else []
    ours = _Tool("jcodemunch", {"P1", "P2", "T"})
    theirs = _Tool("other", {"P1", "T"})
    scored, cap = task_check.split([p1, p2, t], nulls + [ours, theirs])
    assert [x.id for x in scored] == ["x", "z"]
    assert cap == [{"task": "y", "category": "P2", "answerable_by": ["jcodemunch"]}]
    # the mirror image: a task only THEY answer is excluded the same way
    scored, cap = task_check.split([p1, p2, t], nulls + [_Tool("jcodemunch", {"P1", "T"}), _Tool("other", {"P1", "P2", "T"})])
    assert cap[0]["answerable_by"] == ["other"]
    # with one non-null adapter nothing is capability-only by count
    scored, cap = task_check.split([p1, p2, t], nulls + [ours])
    assert len(scored) == 3 and cap == []
    flagged = Task(id="w", corpus="t@0", category="P1", query="f", expected=(("a.py", 1),), capability_only=True)
    assert task_check.split([flagged], [ours, theirs])[1][0]["task"] == "w"


def test_check_refuses_absent_file_and_tool_words(tmp_path):
    c = _corpus(tmp_path)
    nulls = [_Tool("null_grep", {"P1", "P2", "P4", "P5", "T"}, "null")]
    ok = Task(id="ok", corpus="t@0", category="P1", query="f", expected=(("a.py", 1),))
    absent = Task(id="ab", corpus="t@0", category="P1", query="f", expected=(("b.py", 1),))
    names = Task(id="nm", corpus="t@0", category="T", query="use search_symbols for f")
    elsewhere = Task(id="el", corpus="u@0", category="T", query="f")
    assert task_check.check([ok], {c.id: c}, nulls) == []
    problems = task_check.check([absent, names, elsewhere], {c.id: c}, nulls)
    assert len(problems) == 3
    assert any("b.py" in p for p in problems) and any("search_symbols" in p for p in problems) and any("u@0" in p for p in problems)


def test_tools_not_called_names_only_the_silent_tool():
    runs = [{
        "loud": {"c": {"tasks": [{"category": "P1", "cited": 1}, {"category": "P2", "cited": 0}]}},
        "quiet": {"c": {"tasks": [{"category": "P1", "cited": 0}, {"category": "P2", "cited": 0}, {"category": "T", "cited": 0}]}},
        "errored": {"c": {"tasks": [{"category": "P1", "cited": 0, "error": "timeout"}]}},
    }]
    tools = [_Tool("loud", {"P1", "P2"}), _Tool("quiet", {"P1", "P2", "T"}), _Tool("errored", {"P1"}), _Tool("null_grep", {"P1"}, "null")]
    out = task_check.tools_not_called(runs, tools)
    assert out == [
        {"tool": "loud", "corpus": "c", "category": "P2", "tasks": 1, "hypothesis": "tool_not_called"},
        {"tool": "quiet", "corpus": "c", "category": "P1", "tasks": 1, "hypothesis": "tool_not_called"},
        {"tool": "quiet", "corpus": "c", "category": "P2", "tasks": 1, "hypothesis": "tool_not_called"},
    ]  # T is not a P category; an errored task is not a silent one; a null is never listed
