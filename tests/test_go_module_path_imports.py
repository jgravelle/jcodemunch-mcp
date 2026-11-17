"""A Go import by module path reaches the package it names (LEDGER L-264).

Go names an in-module package by its module path, `github.com/gin-gonic/gin/binding`,
not by a relative path. The resolver had no `go.mod` reading, so no such import
reached a file: on gin (0f09c3a9) every file of `binding/`, `render/` and
`codec/json/` had zero importers and `find_dead_code` reported each as dead at
confidence 1.0, while `context.go` imports both packages.

Go files of one package also call each other with no import (L-262), so a file
of a package something imports was reported dead when only its peers used it.
The package is the unit: a package anything outside it imports is live, and a
package nothing reaches is still reported, every file of it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.find_importers import find_importers
from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
from jcodemunch_mcp.tools.index_folder import index_folder

# The shape of gin at 0f09c3a9: a library root package `gin` that imports its
# own `binding` and `render` packages by module path.
GIN = {
    "go.mod": "module github.com/gin-gonic/gin\n\ngo 1.24\n\nrequire github.com/gin-contrib/sse v1.1.0\n",
    "gin.go": 'package gin\n\nimport "github.com/gin-gonic/gin/render"\n\nfunc New() render.Render { return nil }\n',
    "context.go": (
        "package gin\n\nimport (\n"
        '\t"errors"\n'
        '\t"github.com/gin-contrib/sse"\n'
        '\t"github.com/gin-gonic/gin/binding"\n'
        '\t"github.com/gin-gonic/gin/render"\n'
        ")\n\nfunc Bind() error { _ = sse.Event{}; _ = render.Render(nil); return binding.Validate(nil) }\n"
        "\nvar _ = errors.New\n"
    ),
    "binding/binding.go": "package binding\n\nfunc Validate(x any) error { return check(x) }\n",
    "binding/default_validator.go": "package binding\n\nfunc check(x any) error { return nil }\n",
    "binding/form_mapping.go": "package binding\n\nfunc mapForm() {}\n",
    "render/render.go": "package render\n\ntype Render interface{ Render() error }\n",
    "render/json.go": "package render\n\ntype JSON struct{}\n",
    # A package nothing imports: still dead, every file of it.
    "unused/a.go": "package unused\n\nfunc A() int { return b() }\n",
    "unused/b.go": "package unused\n\nfunc b() int { return 1 }\n",
    # A top-level dir named like a standard-library package.
    "errors/errors.go": "package errors\n\nfunc Local() {}\n",
}


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


def _dead_files(repo, store) -> dict[str, dict]:
    r = find_dead_code(repo, granularity="file", min_confidence=0.0, storage_path=store)
    assert "error" not in r, r
    return {d["file"]: d for d in r["dead_files"]}


def _importers(repo, store, path) -> set[str]:
    r = find_importers(repo, file_path=path, storage_path=store)
    assert "error" not in r, r
    return {i["file"] for i in r["importers"]}


def test_a_module_path_import_reaches_the_package(tmp_path):
    repo, store = _index(tmp_path, GIN)
    assert "context.go" in _importers(repo, store, "binding/binding.go")


def test_no_file_of_an_imported_package_is_reported_dead(tmp_path):
    # gin's binding/ case: 17 files there were dead at 1.0 on 0f09c3a9.
    repo, store = _index(tmp_path, GIN)
    dead = _dead_files(repo, store)
    for f in ("binding/binding.go", "binding/default_validator.go",
              "binding/form_mapping.go", "render/render.go", "render/json.go"):
        assert f not in dead, (f, dead.get(f))


def test_a_package_nothing_imports_is_still_reported_whole(tmp_path):
    # Peers that only reach each other are not a reason to call a package live.
    repo, store = _index(tmp_path, GIN)
    dead = _dead_files(repo, store)
    for f in ("unused/a.go", "unused/b.go"):
        assert f in dead, sorted(dead)
        assert dead[f]["reason"] == "zero_importers", dead[f]


def test_a_standard_library_import_reaches_no_same_named_dir(tmp_path):
    repo, store = _index(tmp_path, GIN)
    assert _importers(repo, store, "errors/errors.go") == set()
    assert "errors/errors.go" in _dead_files(repo, store)


def test_a_dependency_reaches_nothing(tmp_path):
    # `github.com/gin-contrib/sse` is outside the module, and a path that only
    # shares a string prefix with the module (`.../ginx`) is outside it too.
    files = dict(GIN)
    files["sse/sse.go"] = "package sse\n\ntype Event struct{}\n"
    files["x.go"] = 'package gin\n\nimport "github.com/gin-gonic/ginx/sse"\n'
    repo, store = _index(tmp_path, files)
    assert _importers(repo, store, "sse/sse.go") == set()


def test_a_nested_module_resolves_against_its_own_go_mod(tmp_path):
    files = {
        "go.mod": "module example.com/app\n",
        "main.go": 'package main\n\nimport "example.com/tools/lint"\n\nfunc main() { lint.Run() }\n',
        "tools/go.mod": "module example.com/tools // the tools module\n",
        "tools/lint/lint.go": "package lint\n\nfunc Run() {}\n",
        "tools/lint/rules.go": "package lint\n\nfunc rules() {}\n",
    }
    repo, store = _index(tmp_path, files)
    assert "main.go" in _importers(repo, store, "tools/lint/lint.go")
    dead = _dead_files(repo, store)
    assert "tools/lint/rules.go" not in dead, dead.get("tools/lint/rules.go")


def test_a_directory_of_test_files_alone_gains_no_importer(tmp_path):
    # Go cannot import a package that holds only `_test.go` files.
    files = {
        "go.mod": "module example.com/app\n",
        "main.go": 'package main\n\nimport "example.com/app/t"\n',
        "t/x_test.go": "package t\n\nfunc TestX() {}\n",
    }
    repo, store = _index(tmp_path, files)
    assert _importers(repo, store, "t/x_test.go") == set()


def test_a_module_path_with_no_dot_resolves(tmp_path):
    files = {
        "go.mod": 'module "myapp"\n',
        "cmd/serve.go": 'package cmd\n\nimport "myapp/internal/store"\n\nfunc Serve() { store.Open() }\n',
        "internal/store/store.go": "package store\n\nfunc Open() {}\n",
    }
    repo, store = _index(tmp_path, files)
    assert "cmd/serve.go" in _importers(repo, store, "internal/store/store.go")


def _v2_signals(repo, store) -> dict[str, list[str]]:
    r = get_dead_code_v2(repo, storage_path=store, min_confidence=0.0)
    assert "error" not in r, r
    return {s["id"]: s["signals"] for s in r["dead_symbols"]}


def test_v2_reaches_a_peer_of_an_imported_package_file(tmp_path):
    # `check` sits in a peer of the file the import edge reaches, and is called
    # from that file with no import: neither unreachable nor uncalled.
    files = dict(GIN)
    files["cmd/app/main.go"] = (
        'package main\n\nimport "github.com/gin-gonic/gin"\n\nfunc main() { gin.New() }\n'
    )
    repo, store = _index(tmp_path, files)
    signals = _v2_signals(repo, store)
    check = signals.get("binding/default_validator.go::check#function", [])
    assert "unreachable_file" not in check, check
    assert "no_callers" not in check, check
    # A package nothing reaches stays unreachable, peers or not.
    assert "unreachable_file" in signals["unused/b.go::b#function"], signals


def test_blast_radius_reaches_a_cross_package_caller(tmp_path):
    # The #415 fixture with its go.mod: `pkg/pick_service.go` imports
    # `fixture/repository` and calls `Record`. Without module resolution the
    # blast radius withheld its score; it now names the caller.
    from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius

    files = {
        "go.mod": "module fixture\n\ngo 1.22\n",
        "repository/order_repo.go": (
            "package repository\n\ntype AuditRepo struct{ log string }\n\n"
            "func (a *AuditRepo) Record(msg string) error { return nil }\n"
        ),
        "pkg/pick_service.go": (
            'package pkg\n\nimport "fixture/repository"\n\n'
            "type PickService struct{ auditRepo *repository.AuditRepo }\n\n"
            'func (s *PickService) Start() error { return s.auditRepo.Record("x") }\n'
        ),
    }
    repo, store = _index(tmp_path, files)
    r = get_blast_radius(repo=repo, symbol="Record", storage_path=store)
    assert "error" not in r, r
    assert "pkg/pick_service.go" in {c["file"] for c in r["confirmed"]}, r
    assert r["overall_risk_score"] is not None, r


def test_a_multi_file_go_package_is_one_stable_unit_for_coupling(tmp_path):
    # Two programs import `lib`; the import edge reaches one file of it, and
    # another of its files imports `util`. Read file by file, that file has
    # Ca=0 and Ce=1 and is unstable. Read as the package it is, `lib` has two
    # importers and one import: stable, every file of it.
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.get_repo_health import _count_unstable_modules

    files = {
        "go.mod": "module example.com/app\n",
        "cmd/a/main.go": 'package main\n\nimport "example.com/app/lib"\n\nfunc main() { lib.A() }\n',
        "cmd/b/main.go": 'package main\n\nimport "example.com/app/lib"\n\nfunc main() { lib.A() }\n',
        "lib/lib.go": "package lib\n\nfunc A() int { return b() }\n",
        "lib/more.go": 'package lib\n\nimport "example.com/app/util"\n\nfunc b() int { return util.U() }\n',
        "util/util.go": "package util\n\nfunc U() int { return 1 }\n",
    }
    repo, store = _index(tmp_path, files)
    owner, _, name = repo.partition("/")
    ix = IndexStore(base_path=store).load_index(owner, name)
    unstable, total, _, _ = _count_unstable_modules(ix)
    # Only the two programs are unstable (Ce=1, Ca=0); `lib/more.go` is not.
    assert (unstable, total) == (2, 5), (unstable, total)


def test_the_added_edge_is_not_stored(tmp_path):
    # Derived at every load, like the Kotlin declaration edges: a stored copy
    # would outlive a package that moved.
    repo, store = _index(tmp_path, GIN)
    db = next(Path(store).glob("*.db"))
    with sqlite3.connect(db) as c:
        (row,) = c.execute("SELECT imports FROM files WHERE path = 'context.go'").fetchone()
    assert "binding/binding.go" not in row, row
    assert "github.com/gin-gonic/gin/binding" in row, row
