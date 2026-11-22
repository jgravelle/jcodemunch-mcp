"""`find_dead_code` does not prove a Go file dead when no `go.mod` maps its imports (LEDGER L-59).

#415: a Go import names a module path (`import "example.com/app/repository"`),
not a file. Only `go.mod` maps a module path to a directory; L-264 adds those
edges when the index loads. Where no readable `go.mod` covers a Go file (none
in the tree, or an `index_repo` snapshot with no local tree), its import graph
could not be built, and `find_dead_code` published every such file with no
importer as `zero_importers` at confidence 1.0, documented as provably
unreachable. Now the file is capped like any other unproven absence and names
`go_module_unknown`.

The rule reads by MODULE, never by name. Review of the first three drafts
measured a name match (an unresolved specifier against a directory) withholding
real dead files beside stdlib and dependency imports in every language:
`import logging` and `logging/`, `"events"` and `events/`, Go's `"net/http"`
and `http/`, `github.com/pkg/errors` and `errors/`. Those keep 1.0 here.
"""

from __future__ import annotations

from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.index_folder import index_folder

ORDER_REPO_GO = "package repository\n\ntype OrderRepo struct{ db string }\n"
PICK_SERVICE_GO = (
    'package pkg\n\nimport "example.com/app/repository"\n\n'
    "type PickService struct{ orderRepo *repository.OrderRepo }\n"
)
ORPHAN_GO = "package orphan\n\nfunc Unused() int { return 1 }\n"


def _build(tmp_path, files: dict[str, str]):
    root = tmp_path / "fx"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    store = str(tmp_path / "store")
    res = index_folder(path=str(root), identity_mode="local", use_ai_summaries=False, storage_path=store)
    assert res["success"], res
    return res["repo"], store


def _dead(repo, store, **kw):
    out = find_dead_code(repo=repo, storage_path=store, **kw)
    assert "error" not in out, out
    return out, {e["file"]: e for e in out["dead_files"]}


GO_FILES = {
    "repository/order_repo.go": ORDER_REPO_GO,
    "pkg/pick_service.go": PICK_SERVICE_GO,
    "orphan/orphan.go": ORPHAN_GO,
}


def test_a_go_file_under_no_go_mod_is_not_proven_dead(tmp_path):
    out, dead = _dead(*_build(tmp_path, GO_FILES), min_confidence=0)
    for f in ("repository/order_repo.go", "orphan/orphan.go"):
        assert dead[f]["confidence"] <= UNPROVEN_CEILING, dead[f]
        assert dead[f]["uncapped_confidence"] == 1.0
        assert "go_module_unknown" in dead[f]["confidence_capped_by"]


def test_the_default_call_withholds_them_and_says_how_many(tmp_path):
    out, dead = _dead(*_build(tmp_path, GO_FILES))
    assert not {"repository/order_repo.go", "orphan/orphan.go"} & set(dead)
    # All three Go files have no importer here, the importer included.
    assert out["go_module_unknown_withheld"] == 3


def test_under_a_go_mod_the_graph_answers_and_nothing_is_capped(tmp_path):
    """L-264 maps the import through `go.mod`: the imported package is live,
    the orphan is proven dead at 1.0, and nothing is withheld."""
    files = dict(GO_FILES, **{"go.mod": "module example.com/app\n\ngo 1.22\n"})
    out, dead = _dead(*_build(tmp_path, files), min_confidence=0)
    assert "repository/order_repo.go" not in dead
    assert dead["orphan/orphan.go"]["confidence"] == 1.0
    assert "confidence_capped_by" not in dead["orphan/orphan.go"]
    assert "go_module_unknown_withheld" not in _dead(*_build(tmp_path / "again", files))[0]


def test_a_stdlib_or_dependency_name_does_not_hide_a_dead_file(tmp_path):
    """The shapes review measured against a name match: each dead file sits in
    a directory named like an import that resolves to no file, one level deep
    and at the root, in Python, JS and Go. All keep 1.0."""
    files = {
        "go.mod": "module example.com/app\n\ngo 1.22\n",
        "cmd/main.go": (
            'package main\n\nimport (\n\t"errors"\n\t"net/http"\n'
            '\tpkgerr "github.com/pkg/errors"\n)\n\n'
            "func main() { _ = errors.New(\"x\"); _ = http.StatusOK; _ = pkgerr.New(\"y\") }\n"
        ),
        "errors/dead.go": "package errors\n\nfunc Unused() int { return 1 }\n",
        "http/dead.go": "package http\n\nfunc Unused() int { return 1 }\n",
        "main.py": "import logging\nimport http\n",
        "app/main.py": "import logging\n",
        "logging/handlers.py": "def unused():\n    return 1\n",
        "app/logging/handlers.py": "def unused():\n    return 1\n",
        "index.js": 'import { EventEmitter } from "events";\n\nexport const f = EventEmitter;\n',
        "events/dead.js": "export function unused() { return 1; }\n",
        "web/events/dead.js": "export function unused() { return 1; }\n",
    }
    out, dead = _dead(*_build(tmp_path, files), min_confidence=0)
    for f in (
        "errors/dead.go",
        "http/dead.go",
        "logging/handlers.py",
        "app/logging/handlers.py",
        "events/dead.js",
        "web/events/dead.js",
    ):
        assert dead[f]["confidence"] == 1.0, dead[f]
        assert "confidence_capped_by" not in dead[f]


def test_a_non_go_file_is_never_capped_for_a_go_module(tmp_path):
    """No `go.mod` anywhere: a dead Python file beside unmapped Go keeps 1.0."""
    files = dict(GO_FILES, **{"tools/dead.py": "def unused():\n    return 1\n"})
    out, dead = _dead(*_build(tmp_path, files), min_confidence=0)
    assert dead["tools/dead.py"]["confidence"] == 1.0
    assert "confidence_capped_by" not in dead["tools/dead.py"]


def test_a_nested_go_mod_covers_only_its_tree(tmp_path):
    """A `go.mod` in `svc/` maps `svc/`, not `svcx/` beside it, nor `other/`."""
    files = {
        "svc/go.mod": "module example.com/svc\n\ngo 1.22\n",
        "svc/orphan/o.go": ORPHAN_GO,
        "svcx/b/b.go": ORPHAN_GO,
        "other/o.go": ORPHAN_GO,
    }
    out, dead = _dead(*_build(tmp_path, files), min_confidence=0)
    assert dead["svc/orphan/o.go"]["confidence"] == 1.0
    for f in ("svcx/b/b.go", "other/o.go"):
        assert "go_module_unknown" in dead[f]["confidence_capped_by"], f


def test_an_unreadable_go_mod_maps_nothing(tmp_path):
    """A `go.mod` that cannot be read (here a directory) is no map."""
    out, dead = _dead(*_build(tmp_path, dict(GO_FILES, **{"go.mod/x.txt": "x\n"})), min_confidence=0)
    assert "go_module_unknown" in dead["orphan/orphan.go"]["confidence_capped_by"]


def test_a_go_mod_written_after_the_index_loaded_does_not_lift_the_cap(tmp_path):
    """Review of L-59: the cap reads the map the index built its edges from.
    A fresh read of a `go.mod` written later lifted the cap while the edges
    were still stale, publishing an imported file as proven dead."""
    repo, store = _build(tmp_path, GO_FILES)
    _out, first = _dead(repo, store, min_confidence=0)
    (tmp_path / "fx" / "go.mod").write_text("module example.com/app\n\ngo 1.22\n")
    _out, second = _dead(repo, store, min_confidence=0)
    assert second["repository/order_repo.go"]["confidence"] == first["repository/order_repo.go"]["confidence"]
    assert second["repository/order_repo.go"]["confidence"] <= UNPROVEN_CEILING


def test_an_index_with_no_local_tree_maps_no_go_file():
    """An `index_repo` snapshot has no source root, so no map was read."""
    from jcodemunch_mcp.storage.index_store import CodeIndex
    from jcodemunch_mcp.tools.find_dead_code import go_file_outside_module_map

    idx = CodeIndex(
        repo="o/r", owner="o", name="r", indexed_at="", symbols=[], source_files=["a/a.go"],
        languages={"go": 1}, imports={"a/a.go": [{"specifier": "example.com/r/b", "names": []}]},
    )
    assert idx.go_module_dirs == ()
    assert go_file_outside_module_map(idx, "a/a.go") is True
    assert go_file_outside_module_map(idx, "a/a.py") is False


def test_check_delete_safe_does_not_certify_an_unmapped_go_symbol(tmp_path):
    """Capping the report does not cap the tool that acts on it (09-01)."""
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo, store = _build(tmp_path, GO_FILES)
    out = check_delete_safe(repo=repo, symbol="orphan/orphan.go::Unused#function", storage_path=store)
    assert out["verdict"] == "dynamic_import_boundary", out
    assert any(b["kind"] == "go_module_unknown" for b in out["blockers"]), out
    assert out["confidence"] <= UNPROVEN_CEILING

    files = dict(GO_FILES, **{"go.mod": "module example.com/app\n\ngo 1.22\n"})
    repo, store = _build(tmp_path / "mapped", files)
    out = check_delete_safe(repo=repo, symbol="orphan/orphan.go::Unused#function", storage_path=store)
    assert out["verdict"] == "safe_to_delete", out
