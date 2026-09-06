"""The competitive tier's findings-to-issues DRAFTS (docs/competitive/DESIGN.md
s7, s8; the brief's Phase 3 item 5).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- a `competitive-gap` draft is written for a meaningful row where jcm is
  behind, and for nothing else (not ahead, not unstable, not our own row,
  not a CLI's zero schema cost), carrying every field s7.1 lists and a
  hypothesis from the fixed list only;
- the hypothesis rule: our empty `cited` set on every task of the category
  is `tool_not_called` before anything else; an F1 row otherwise `ranking`
  (P1/P2) or `coverage` (P4); a token row `payload_shape`; the rest `unknown`;
- `competitive-watch` needs jcm ahead and `narrowed` on two consecutive
  runs, judged by trend.py's movement, never by one run;
- a `standard-proposal` needs two consecutive runs under the Target read
  VERBATIM from STANDARD.md (never a Floor), on the self corpus, and says in
  its first line that the standard is edited only by a human;
- de-duplication by fingerprint: an OPEN issue carrying it updates the
  draft in place (no new issue, a dated block appended), a CLOSED one is
  named as "the gap came back"; the tracker unreadable and no list handed
  in refuses the whole run (fail closed on duplicates);
- the draft file shape: title line, labels line with `needs-human`, the
  fingerprint line, `approved: false`; nothing is posted (no `gh` verb but
  `issue list` appears in the module).
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

import findings  # noqa: E402

PINS = [{"name": "jcodemunch", "registry": "tree", "package": "jcodemunch-mcp", "version": "c", "ran_as": "c", "interface": "mcp-stdio", "image_digest": None},
        {"name": "other", "registry": "pypi", "package": "other-tool", "version": "1.2", "ran_as": "1.2", "interface": "mcp-stdio", "image_digest": "sha256:abc"},
        {"name": "clitool", "registry": "pypi", "package": "cli-tool", "version": "3.0", "ran_as": "3.0", "interface": "cli", "image_digest": "sha256:def"}]


def _row(axis, tool, corpus, measured, jcm, meaningful=True, stable=True, band=0.01):
    delta = (measured / jcm) if axis in findings.RATIO_AXES else (measured - jcm)
    return {"axis": axis, "tool": tool, "corpus": corpus, "runs": [measured] * 3, "measured": measured, "spread": 0.0, "jcm": jcm, "jcm_spread": 0.0,
            "delta": round(delta, 4), "band": band, "meaningful": meaningful, "stable": stable, "note": ""}


def _result(rows, tools_not_called=(), runs=None):
    return {"header": {"schema": "jcm-competitive-result/v1", "date": "2026-09-06T00:00:00Z", "jcm_commit": "c", "jcm_version": "v", "runs": 3, "file": "r.json",
                       "pins": PINS, "corpora": [{"id": "self@c", "files": 10, "sha256": "0" * 64}, {"id": "x/y@1", "files": 5, "sha256": "1" * 64}]},
            "rows": rows, "runs": runs or [{}], "tools_not_called": list(tools_not_called), "capability_only": [], "not_runnable": []}


def test_gap_draft_only_for_meaningful_rows_where_jcm_is_behind():
    rows = [
        _row("f1_P1", "other", "self@c", 0.6, 0.4),                       # behind, meaningful -> draft
        _row("f1_P1", "other", "x/y@1", 0.3, 0.4),                        # ahead -> none
        _row("tokens_per_task", "other", "self@c", 500.0, 1000.0),        # behind (ratio 0.5) -> draft
        _row("tokens_per_task", "other", "x/y@1", 500.0, 1000.0, meaningful=False),  # inside the band -> none
        _row("tools_list_tokens", "clitool", "self@c", 0.0, 20000.0),     # a CLI's zero schema cost: NOT COMPARABLE -> none
        _row("tools_list_tokens", "other", "self@c", 400.0, 20000.0),     # a server's smaller schema: a real gap -> draft
        _row("f1_P1", "jcodemunch", "self@c", 0.4, 0.4),                  # our own row -> none
    ]
    drafts = findings.gap_drafts(_result(rows))
    got = {(d["axis"], d["tool"], d["corpus"]) for d in drafts}
    assert got == {("f1_P1", "other", "self@c"), ("tokens_per_task", "other", "self@c"), ("tools_list_tokens", "other", "self@c")}
    d = next(x for x in drafts if x["axis"] == "f1_P1")
    assert d["label"] == "competitive-gap" and d["fingerprint"] == "competitive-gap/f1_P1/other/self"
    for needle in ("task category P1", "median 0.4", "median 0.6", "band 0.01", "pypi:other-tool@1.2", "sha256:abc", "`r.json`", "hypothesis"):
        assert needle in d["body"], needle
    assert "never a fix" in d["body"] and "recommend" not in d["body"].lower()  # a hypothesis, never a prescription


def test_hypothesis_rule_from_the_fixed_list():
    r = _result([], tools_not_called=[{"tool": "jcodemunch", "corpus": "self@c", "category": "P2", "tasks": 3, "hypothesis": "tool_not_called"}])
    assert findings.hypothesis("f1_P2", _row("f1_P2", "other", "self@c", 0.5, 0.0), r) == "tool_not_called"
    assert findings.hypothesis("f1_P1", _row("f1_P1", "other", "self@c", 0.5, 0.3), r) == "ranking"
    assert findings.hypothesis("f1_P4", _row("f1_P4", "other", "self@c", 0.5, 0.3), r) == "coverage"
    assert findings.hypothesis("tokens_per_task", _row("tokens_per_task", "other", "self@c", 1.0, 2.0), r) == "payload_shape"
    assert findings.hypothesis("latency_call_ms", _row("latency_call_ms", "other", "self@c", 1.0, 2.0), r) == "unknown"
    # an index that reported fewer files than the corpus has, before ranking
    r2 = _result([], runs=[{"jcodemunch": {"self@c": {"index_error": "ok file_count=4"}}}])
    assert findings.hypothesis("f1_P1", _row("f1_P1", "other", "self@c", 0.5, 0.3), r2) == "index_missing_files"
    assert set(findings.HYPOTHESES) == {"tool_not_called", "ranking", "coverage", "payload_shape", "index_missing_files", "unknown"}


def _line(medians, pins=None, bands=None):
    return {"date": "d", "jcm_commit": "p", "jcm_version": "v", "pins": pins or {"other": "1.0"}, "medians": medians, "bands": bands or {}}


def test_watch_needs_ahead_and_narrowed_twice():
    key, jkey = "tokens_per_task/other/self", "tokens_per_task/jcodemunch/self"
    first = _line({key: 4000.0, jkey: 1000.0}, bands={key: 50.0})
    prev = _line({key: 3000.0, jkey: 1000.0}, bands={key: 50.0}, pins={"other": "1.1"})
    jrow = _row("tokens_per_task", "jcodemunch", "self@c", 1000.0, 1000.0)
    res = _result([_row("tokens_per_task", "other", "self@c", 2000.0, 1000.0, band=50.0), jrow])
    (d,) = findings.watch_drafts(res, [first, prev])
    assert d["label"] == "competitive-watch" and d["fingerprint"] == "competitive-watch/tokens_per_task/other/self"
    assert "now 2.0, previous 3.0, before that 4.0" in d["body"] and "1.2 / 1.1 / 1.0" in d["body"]
    # narrowed once only: none
    assert findings.watch_drafts(res, [_line({key: 3000.0, jkey: 1000.0}), prev]) == []
    # jcm not ahead (ratio under 1): none
    res2 = _result([_row("tokens_per_task", "other", "self@c", 500.0, 1000.0, band=50.0), jrow])
    assert findings.watch_drafts(res2, [_line({key: 900.0, jkey: 1000.0}, bands={key: 50.0}), _line({key: 700.0, jkey: 1000.0}, bands={key: 50.0})]) == []


def test_standard_proposal_reads_the_target_verbatim_and_never_a_floor():
    text = "Floor: [`index.cold_self_seconds`] 2x the median.\nTarget: (b) under 1 s p95; (c) under 20 s cold on the self corpus in CI.\n"
    assert findings.read_target("index_cold_seconds", text) == ("Target: (b) under 1 s p95; (c) under 20 s cold on the self corpus in CI.", 20.0)
    assert findings.read_target("latency_call_ms", text) is None  # a p95 Target is not read against a per-call median
    key = "index_cold_seconds/other/self"
    prev = _line({key: 12.0, "index_cold_seconds/jcodemunch/self": 25.0})
    res = _result([_row("index_cold_seconds", "other", "self@c", 11.0, 25.0)])
    (d,) = findings.standard_drafts(res, [prev], text)
    assert d["label"] == "standard-proposal"
    assert d["body"].splitlines()[0].startswith("The standard is edited only by a human")
    assert "> Target: (b) under 1 s p95; (c) under 20 s cold on the self corpus in CI." in d["body"]
    assert "proposed Target, same units: 11.0" in d["body"] and "Floor" not in d["body"].split("never a Floor")[1]
    # only one run under the Target: none; a non-self corpus: none
    assert findings.standard_drafts(res, [_line({key: 30.0, "index_cold_seconds/jcodemunch/self": 25.0})], text) == []
    assert findings.standard_drafts(_result([_row("index_cold_seconds", "other", "x/y@1", 11.0, 25.0)]), [prev], text) == []


def test_dedupe_and_draft_files(tmp_path):
    rows = [_row("f1_P1", "other", "self@c", 0.6, 0.4), _row("f1_P1", "other", "x/y@1", 0.6, 0.4), _row("f1_P4", "other", "self@c", 0.6, 0.4)]
    drafts = findings.gap_drafts(_result(rows))
    issues = [{"number": 7, "state": "OPEN", "body": "x\ncompetitive-id: competitive-gap/f1_P1/other/self\n"},
              {"number": 3, "state": "CLOSED", "body": "competitive-id: competitive-gap/f1_P1/other/x/y@1"}]
    findings.dedupe(drafts, issues)
    by = {d["corpus"]: d for d in drafts if d["axis"] == "f1_P1"}
    assert by["self@c"]["existing_open"] == 7 and by["x/y@1"]["existing_closed"] == 3
    out = tmp_path / "drafts"
    written = findings.write_drafts(drafts, out, "2026-09-06")
    assert len(written) == 3 and (out / "index.json").exists()
    self_draft = (out / findings._fname("competitive-gap/f1_P1/other/self")).read_text(encoding="utf-8")
    assert self_draft.startswith("title: competitive gap:") and "labels: competitive-gap, needs-human" in self_draft
    assert "existing-issue: #7 (open; this draft updates it in place, no new issue)" in self_draft
    assert "approved: false" in self_draft and "competitive-id: competitive-gap/f1_P1/other/self" in self_draft
    xy = (out / findings._fname("competitive-gap/f1_P1/other/x/y@1")).read_text(encoding="utf-8")
    assert "existing-issue: #3 (closed; the gap came back)" in xy
    # a second run against an OPEN duplicate appends a dated block, never a second file
    findings.write_drafts(drafts, out, "2026-10-06")
    again = (out / findings._fname("competitive-gap/f1_P1/other/self")).read_text(encoding="utf-8")
    assert again.count("## 2026-") == 2 and again.startswith("title:")
    assert len(list(out.glob("*.md"))) == 3


def test_cli_refuses_when_the_tracker_is_unreadable_and_no_list_is_given(tmp_path, monkeypatch, capsys):
    res = _result([_row("f1_P1", "other", "self@c", 0.6, 0.4)])
    rf = tmp_path / "r.json"
    rf.write_text(json.dumps(res), encoding="utf-8")
    hist = tmp_path / "history.jsonl"
    hist.write_text("", encoding="utf-8")

    def boom(repo):
        raise RuntimeError("gh: not logged in")

    monkeypatch.setattr(findings, "read_open_issues", boom)
    rc = findings.main([str(rf), "--history", str(hist), "--out", str(tmp_path / "d")])
    assert rc == 3 and "refused" in capsys.readouterr().err and not (tmp_path / "d").exists()
    issues = tmp_path / "issues.json"
    issues.write_text("[]", encoding="utf-8")
    rc = findings.main([str(rf), "--history", str(hist), "--out", str(tmp_path / "d"), "--open-issues", str(issues)])
    assert rc == 0 and len(list((tmp_path / "d").glob("*.md"))) == 1
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"header": {"schema": "other"}}), encoding="utf-8")
    assert findings.main([str(bad), "--out", str(tmp_path / "e"), "--open-issues", str(issues)]) == 2


def test_module_never_posts():
    src = (COMPETE / "findings.py").read_text(encoding="utf-8")
    verbs = re.findall(r'"gh",\s*"(\w+)",\s*"(\w+)"', src)
    assert verbs == [("issue", "list")]
    for word in ("issue create", "issue comment", "issue edit", "api -X POST", "--method POST", "pr create"):
        assert word not in src
