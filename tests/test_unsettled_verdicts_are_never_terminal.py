"""An unproven absence verdict is never terminal, with every channel open (LEDGER L-80).

`check_delete_safe` returns `name_not_searchable` when a symbol's name is one no
call site writes (a C# operator is invoked as `a + b`), so "no reference found"
proves nothing (#714). The tool built the gap that says so -- "read the call
sites by hand" -- and never passed it to `build_stop_rule`. With cross-repo on,
runtime on and traces ingested, no channel was left open and the verdict came
back `terminal: True`: stop checking, before a delete, on a claim the tool's own
docstring calls "not evidence of disuse".

#714's test asserted `terminal is False` and passed only because this box, like
CI, has no runtime traces ingested, which left the runtime channel open. The
fixture could not express the shape that failed.

The property is one layer down: a verdict whose meaning is "the absence could
not be established" is never terminal, whichever gaps a caller remembered to
pass. `_stop_rule` enforces it for every such verdict, and the tool names the
gap it knows.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.tools import _stop_rule

_OPEN = dict(cross_repo=True, include_runtime=True, runtime_data_present=True)

UNSETTLED = [
    ("check_delete_safe", "name_not_searchable"),
    ("check_delete_safe", "corpus_inadequate"),
    ("check_delete_safe", "dynamic_import_boundary"),
    ("check_edit_safe", "dynamic_import_boundary"),
]


@pytest.mark.parametrize("tool,verdict", UNSETTLED)
def test_an_unsettled_verdict_is_not_terminal_with_no_gap_passed(tool, verdict):
    out = _stop_rule.build_stop_rule(tool, verdict, **_OPEN)
    assert out["terminal"] is False, out
    assert out["would_change_verdict"], "a non-terminal verdict must name what would move it"


def test_every_unsettled_verdict_is_a_classified_bounded_one():
    for tool, verdict in UNSETTLED:
        assert verdict in _stop_rule._BOUNDED[tool]
    declared = {(t, v) for t, vs in _stop_rule._UNSETTLED.items() for v in vs}
    assert declared == set(UNSETTLED), sorted(declared ^ set(UNSETTLED))


def test_a_settled_bounded_verdict_still_reaches_terminal():
    """Control: `safe_to_delete` with every channel open IS final. The rule
    must not make every bounded verdict non-terminal."""
    assert _stop_rule.build_stop_rule("check_delete_safe", "safe_to_delete", **_OPEN)["terminal"] is True


_SOURCE = """public class Vec {
    public static Vec operator +(Vec a, Vec b) { return a; }
    public void Ordinary(int x) { }
}
"""
_USE = """public class Use {
    public void Go() {
        var a = new Vec();
        var c = a + a;
        a.Ordinary(1);
    }
}
"""


def test_name_not_searchable_is_not_terminal_through_the_tool(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools import check_delete_safe as cds
    from jcodemunch_mcp.tools.index_folder import index_folder

    monkeypatch.setattr(cds, "_runtime_data_present", lambda *a, **k: True)
    (tmp_path / "Vec.cs").write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "Use.cs").write_text(_USE, encoding="utf-8")
    storage = str(tmp_path / "idx")
    repo = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"]

    got = cds.check_delete_safe(
        repo, "Vec.cs::Vec.operator +#method", cross_repo=True, include_runtime=True, storage_path=storage
    )
    assert got["verdict"] == "name_not_searchable", got["verdict"]
    assert got["stop_rule"]["terminal"] is False, got["stop_rule"]
    actions = [g["action"] for g in got["stop_rule"]["would_change_verdict"]]
    assert any("call sites" in a for a in actions), actions
