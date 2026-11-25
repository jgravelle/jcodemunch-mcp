"""Callers in the same Go package are found (LEDGER L-262).

Go files in one directory form one package and call each other with no import
statement. `get_call_hierarchy`'s callers pass only looked at files the import
graph linked to the symbol's file, so a same-package caller was never
considered: gin's who-calls found 1 of 4 callers on a real index.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder


def _index(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], str(store)


def _callers(repo, store, symbol_id):
    h = get_call_hierarchy(
        repo=repo, symbol_id=symbol_id, direction="callers", storage_path=store
    )
    assert "error" not in h, h
    return h


SAME_PACKAGE = {
    "gin/a.go": "package gin\n\nfunc helper() int { return 1 }\n",
    "gin/b.go": "package gin\n\nfunc Use() int { return helper() }\n",
    "gin/c.go": "package gin\n\nfunc Other() int { return helper() + 1 }\n",
}


def test_a_same_package_caller_is_found(tmp_path):
    repo, store = _index(tmp_path, SAME_PACKAGE)
    h = _callers(repo, store, "gin/a.go::helper#function")
    assert sorted(c["name"] for c in h["callers"]) == ["Other", "Use"], h


def test_found_callers_are_no_longer_disclosed_as_unreached(tmp_path):
    repo, store = _index(tmp_path, SAME_PACKAGE)
    h = _callers(repo, store, "gin/a.go::helper#function")
    gap = (h.get("_meta") or {}).get("caller_graph_incomplete") or {}
    assert "gin/b.go" not in gap.get("excluded_files", []), gap
    assert "gin/b.go" in {c["file"] for c in h["callers"]}, h


def test_a_root_package_caller_is_found(tmp_path):
    repo, store = _index(
        tmp_path,
        {
            "a.go": "package main\n\nfunc helper() int { return 1 }\n",
            "main.go": "package main\n\nfunc main() { helper() }\n",
        },
    )
    h = _callers(repo, store, "a.go::helper#function")
    assert [c["name"] for c in h["callers"]] == ["main"], h


def test_another_package_with_its_own_helper_is_not_a_caller(tmp_path):
    """A file in another directory calls its own `helper`, not this one."""
    files = dict(SAME_PACKAGE)
    files["other/x.go"] = (
        "package other\n\nfunc helper() int { return 2 }\n\n"
        "func Run() int { return helper() }\n"
    )
    repo, store = _index(tmp_path, files)
    h = _callers(repo, store, "gin/a.go::helper#function")
    assert sorted(c["name"] for c in h["callers"]) == ["Other", "Use"], h


def test_a_python_file_in_the_same_directory_is_still_gated(tmp_path):
    """Only a package-directory language shares scope by directory: a Python
    file beside the symbol's file still needs an import to call it."""
    repo, store = _index(
        tmp_path,
        {
            "pkg/a.py": "def helper():\n    return 1\n",
            "pkg/b.py": "def helper():\n    return 2\n\n\ndef use():\n    return helper()\n",
        },
    )
    h = _callers(repo, store, "pkg/a.py::helper#function")
    assert h["callers"] == [], h


def test_a_same_named_method_on_another_type_is_not_a_caller(tmp_path):
    """Review round 1: `RouterGroup.Use`'s own declaration names `Use`, so it
    was reported as a caller of `Engine.Use`. Nothing calls `Engine.Use`."""
    repo, store = _index(
        tmp_path,
        {
            "gin/engine.go": "package gin\n\ntype Engine struct{}\n\nfunc (e *Engine) Use() {}\n",
            "gin/group.go": "package gin\n\ntype RouterGroup struct{}\n\nfunc (g *RouterGroup) Use() {}\n",
        },
    )
    h = _callers(repo, store, "gin/engine.go::Engine.Use#method")
    assert h["callers"] == [], h


def test_impact_preview_sees_a_same_package_caller(tmp_path):
    """The reported what-breaks symptom: get_impact_preview reached nothing."""
    from jcodemunch_mcp.tools.get_impact_preview import get_impact_preview

    repo, store = _index(tmp_path, SAME_PACKAGE)
    r = get_impact_preview(
        repo=repo, symbol_id="gin/a.go::helper#function", storage_path=store
    )
    assert "error" not in r, r
    names = sorted(s["name"] for s in r["affected_symbols"])
    assert names == ["Other", "Use"], r


def test_blast_radius_call_depth_sees_a_same_package_caller(tmp_path):
    """Review round 2: with call_depth, get_blast_radius reported no callers and
    an `absent` verdict for a helper two same-package files call."""
    from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius

    repo, store = _index(tmp_path, SAME_PACKAGE)
    r = get_blast_radius(
        repo=repo, symbol="gin/a.go::helper#function", call_depth=2, storage_path=store
    )
    assert "error" not in r, r
    assert sorted(c["name"] for c in r["callers"]) == ["Other", "Use"], r
    verdict = ((r.get("_meta") or {}).get("verdict") or {}).get("state")
    assert verdict != "absent", r


def test_an_interface_declaring_the_method_is_not_a_caller(tmp_path):
    """Review round 2: Go interface methods are not symbols, so the text pass
    read `type R interface { Delete() }` as a call to `Store.Delete`."""
    repo, store = _index(
        tmp_path,
        {
            "repo/iface.go": "package repo\n\ntype R interface {\n\tDelete() error\n}\n",
            "repo/store.go": "package repo\n\ntype Store struct{}\n\nfunc (s *Store) Delete() error { return nil }\n",
        },
    )
    h = _callers(repo, store, "repo/store.go::Store.Delete#method")
    assert h["callers"] == [], h


def test_a_wrapper_that_calls_the_target_is_still_a_caller(tmp_path):
    """The declaration skip must not hide a real call from a same-named method."""
    repo, store = _index(
        tmp_path,
        {
            "gin/engine.go": "package gin\n\ntype Engine struct{}\n\nfunc (e *Engine) Use() {}\n",
            "gin/group.go": (
                "package gin\n\ntype RouterGroup struct{ engine *Engine }\n\n"
                "func (g *RouterGroup) Use() {\n\tg.engine.Use()\n}\n"
            ),
        },
    )
    h = _callers(repo, store, "gin/engine.go::Engine.Use#method")
    assert [c["file"] for c in h["callers"]] == ["gin/group.go"], h


_ROOT_PACKAGE = {
    "gin/a.go": "package gin\n\nfunc helper() int { return 1 }\n",
    "gin/b.go": "package gin\n\nfunc Use() int { return helper() }\n",
    "gin/c.go": "package gin\n\nfunc Other() int { return 2 }\n",
}


@pytest.mark.parametrize("call_depth", [0, 1])
def test_blast_radius_counts_a_same_package_caller_as_a_dependent(tmp_path, call_depth):
    """Review round 3: with call_depth=0 the importer walk said `absent` and
    risk 0.0, and with call_depth=1 it said risk 0.0 beside a caller list
    naming a dependent. A package nobody imports has no unresolved edge, so
    nothing refused either answer."""
    from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius

    repo, store = _index(tmp_path, _ROOT_PACKAGE)
    r = get_blast_radius(
        repo=repo, symbol="gin/a.go::helper#function", call_depth=call_depth,
        storage_path=store,
    )
    assert [c["file"] for c in r["confirmed"]] == ["gin/b.go"], r
    assert r["overall_risk_score"] > 0
    assert r["_meta"]["verdict"]["state"] != "absent"
    # A peer that never calls the symbol is not listed as a potential importer.
    assert r["potential"] == [], r["potential"]


def test_a_same_package_go_test_gives_test_reach(tmp_path):
    """Review round 4: a Go test sits in its package with no import, so
    `has_test_reach` read every same-package dependent as untested."""
    from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius

    files = dict(_ROOT_PACKAGE)
    files["gin/b_test.go"] = (
        "package gin\n\nimport \"testing\"\n\n"
        "func TestUse(t *testing.T) { if Use() != helper() { t.Fatal() } }\n"
    )
    repo, store = _index(tmp_path, files)
    r = get_blast_radius(repo=repo, symbol="gin/a.go::helper#function", storage_path=store)
    by_file = {c["file"]: c for c in r["confirmed"]}
    assert by_file["gin/b.go"]["has_test_reach"] is True, r["confirmed"]


def test_blast_radius_finds_a_same_package_caller_two_hops_up(tmp_path):
    """Review round 5: the peer prefilter keeps files that may name the
    TARGET; a peer calling an intermediate caller names only that caller,
    so the filter must not reach the depth-2 search."""
    from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius

    repo, store = _index(
        tmp_path,
        {
            "gin/a.go": "package gin\n\nfunc Target() int { return 1 }\n\nfunc Helper() int { return Target() }\n",
            "gin/b.go": "package gin\n\nfunc Outer() int { return Helper() }\n",
        },
    )
    r = get_blast_radius(
        repo=repo, symbol="gin/a.go::Target#function", call_depth=2, storage_path=store
    )
    assert sorted((c["name"], c["depth"]) for c in r["callers"]) == [
        ("Helper", 1), ("Outer", 2),
    ], r["callers"]
