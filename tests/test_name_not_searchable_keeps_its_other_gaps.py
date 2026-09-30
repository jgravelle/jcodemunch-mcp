"""`name_not_searchable` keeps every gap that could move it (LEDGER L-81).

`check_delete_safe` runs three gates over an absence verdict in order: the name
gate (#714), the dynamic-import gate (L-70) and the corpus gate (#566). The
later two fired only on the plain absence verdicts, so a symbol the name gate
had already moved to `name_not_searchable` never got their gaps. On a stale
index, `would_change_verdict` named reading the call sites and left out
"re-index this repo", although the corpus gate's own comment says dropping that
gap hides the re-index that could change the answer. A loader that could import
the file was dropped the same way.

The verdict keeps its name: the name gate is the narrower cause, the rule the
corpus gate already applies to `dynamic_import_boundary`. What it gains is the
other gates' blockers and gaps.

Both halves are reachable from a real repo. A C# operator reaches the name gate,
and so does a Python function with a non-ASCII name (`def café()`), because the
name predicate is ASCII-only (LEDGER L-83), so a package that loads its modules
by computed name reaches the dynamic gate too. Nothing below patches the reach
rule; only the corpus adequacy is faked, as #566's tests do, because a stale
index needs a git history the fixture does not have.
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

_LOADER = "import importlib\n\ndef load(n):\n    return importlib.import_module(__name__ + '.' + n)\n"
_UNICODE = "def café():\n    return 1\n"


def _index(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(root / "idx")
    return index_folder(path=str(root), use_ai_summaries=False, storage_path=storage)["repo"], storage


@pytest.fixture
def csharp(tmp_path):
    return _index(tmp_path, {"Vec.cs": _SOURCE, "Use.cs": _USE})


def _actions(result):
    return [g["action"] for g in result["stop_rule"]["would_change_verdict"]]


def _kinds(result):
    return [b["kind"] for b in result["blockers"]]


def test_a_stale_index_is_named_beside_the_unsearchable_name(csharp, monkeypatch):
    r, sp = csharp
    monkeypatch.setattr(cds, "assess_corpus", lambda index, **kw: CorpusAdequacy("stale", {}, True, ["stale_index"]))
    got = cds.check_delete_safe(r, OP, storage_path=sp)
    assert got["verdict"] == "name_not_searchable", got["verdict"]
    assert "re-index this repo" in _actions(got), _actions(got)
    assert any("call sites" in a for a in _actions(got)), _actions(got)
    assert "corpus_inadequate" in _kinds(got), _kinds(got)


def test_a_reaching_loader_is_named_beside_the_unsearchable_name(tmp_path):
    r, sp = _index(tmp_path, {"pkg/__init__.py": _LOADER, "pkg/m.py": _UNICODE})
    got = cds.check_delete_safe(r, "café", storage_path=sp)
    assert got["verdict"] == "name_not_searchable", got["verdict"]
    assert any("loaders" in a for a in _actions(got)), _actions(got)
    assert any("call sites" in a for a in _actions(got)), _actions(got)
    blocker = next((b for b in got["blockers"] if b["kind"] == "dynamic_import_boundary"), None)
    assert blocker is not None, _kinds(got)
    assert blocker["files"] == ["pkg/__init__.py"]


def test_control_an_adequate_corpus_and_no_loader_add_nothing(csharp):
    r, sp = csharp
    got = cds.check_delete_safe(r, OP, storage_path=sp)
    assert got["verdict"] == "name_not_searchable"
    assert _kinds(got) == ["name_not_searchable"], _kinds(got)
    assert "re-index this repo" not in _actions(got)
