"""`name_not_searchable` keeps every gap that could move it (LEDGER L-81).

`check_delete_safe` runs three gates over an absence verdict in order: the name
gate (#714), the dynamic-import gate (L-70) and the corpus gate (#566). The
later two fired only on the plain absence verdicts, so a symbol the name gate
had already moved to `name_not_searchable` never got their gaps. On a stale
index, `would_change_verdict` named reading the call sites and left out
"re-index this repo", although the corpus gate's own comment says dropping that
gap hides the re-index that could change the answer. The confidence also
skipped the corpus ceiling.

The verdict keeps its name: the name gate is the narrower cause, the rule the
corpus gate already applies to `dynamic_import_boundary`. What it gains is the
other gates' blockers and gaps.

A real repo can reach the corpus half today (a C# operator on a stale index).
The dynamic half cannot, because only Python has #876's dynamic imports and no
Python name fails the name predicate, so it is driven through the reach rule's
seam: the gate's condition is the property, not the language that reaches it.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.tools import check_delete_safe as cds
from jcodemunch_mcp.tools._corpus_adequacy import CorpusAdequacy
from jcodemunch_mcp.tools.index_folder import index_folder

_SOURCE = """public class Vec {
    public static Vec operator +(Vec a, Vec b) { return a; }
}
"""
_USE = """public class Use {
    public void Go() {
        var a = new Vec();
        var c = a + a;
    }
}
"""
OP = "Vec.cs::Vec.operator +#method"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "Vec.cs").write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "Use.cs").write_text(_USE, encoding="utf-8")
    storage = str(tmp_path / "idx")
    return index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"], storage


def _actions(result):
    return [g["action"] for g in result["stop_rule"]["would_change_verdict"]]


def _kinds(result):
    return [b["kind"] for b in result["blockers"]]


def test_a_stale_index_is_named_beside_the_unsearchable_name(repo, monkeypatch):
    r, sp = repo
    monkeypatch.setattr(cds, "assess_corpus", lambda index, **kw: CorpusAdequacy("stale", {}, True, ["stale_index"]))
    got = cds.check_delete_safe(r, OP, storage_path=sp)
    assert got["verdict"] == "name_not_searchable", got["verdict"]
    assert "re-index this repo" in _actions(got), _actions(got)
    assert any("call sites" in a for a in _actions(got)), _actions(got)
    assert "corpus_inadequate" in _kinds(got), _kinds(got)


def test_a_reaching_loader_is_named_beside_the_unsearchable_name(repo, monkeypatch):
    r, sp = repo
    monkeypatch.setattr(cds.DynamicBoundary, "reaching", lambda self, f: ["loader.py"])
    got = cds.check_delete_safe(r, OP, storage_path=sp)
    assert got["verdict"] == "name_not_searchable", got["verdict"]
    assert any("loaders" in a for a in _actions(got)), _actions(got)
    blocker = next(b for b in got["blockers"] if b["kind"] == "dynamic_import_boundary")
    assert blocker["files"] == ["loader.py"]


def test_the_corpus_ceiling_still_applies(repo, monkeypatch):
    r, sp = repo
    ceiling = CorpusAdequacy("stale", {}, True, ["stale_index"]).ceiling
    monkeypatch.setattr(cds, "assess_corpus", lambda index, **kw: CorpusAdequacy("stale", {}, True, ["stale_index"]))
    assert cds.check_delete_safe(r, OP, storage_path=sp)["confidence"] <= ceiling


def test_control_an_adequate_corpus_adds_nothing(repo):
    r, sp = repo
    got = cds.check_delete_safe(r, OP, storage_path=sp)
    assert got["verdict"] == "name_not_searchable"
    assert "re-index this repo" not in _actions(got)
    assert "corpus_inadequate" not in _kinds(got)
