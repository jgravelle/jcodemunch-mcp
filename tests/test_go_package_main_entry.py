"""A Go `package main` program is an entry point (LEDGER L-321, item 1).

Go runs a program from the `func main` of a `package main`. Nothing imports
it, so before this change `find_dead_code` reported every file of a `cmd/`
program as `zero_importers` at confidence 1.0, and `get_dead_code_v2` read its
symbols as unreachable. On prometheus (49c815a0) 11 of the 24 Go files still
reported after the module-path fix were in `cmd/promtool`.

The package is the unit (L-264): the file holding `func main` is the root, and
its same-package peers are live with it. A `main.go` in a library package is
not a program, and a `func main` inside a `_test.go` file is not one either.
"""

from __future__ import annotations

from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
from jcodemunch_mcp.tools.index_folder import index_folder

APP = {
    "go.mod": "module example.com/app\n\ngo 1.22\n",
    # A program: `func main` in one file, a helper in a peer.
    "cmd/tool/main.go": (
        "package main\n\nimport \"example.com/app/internal/store\"\n\n"
        "func main() { run(store.Open()) }\n"
    ),
    "cmd/tool/flags.go": "package main\n\nfunc run(s int) int { return s + parse() }\n\nfunc parse() int { return 1 }\n",
    "internal/store/store.go": "package store\n\nfunc Open() int { return 1 }\n",
    # A library package whose file is named main.go: not a program.
    "lib/main.go": "package lib\n\nfunc Helper() int { return 2 }\n",
    # `package main` with no `func main` outside tests: not a program.
    "tools/gen/gen.go": "package main\n\nfunc gen() int { return 3 }\n",
    "tools/gen/gen_test.go": "package main\n\nimport \"testing\"\n\nfunc main() {}\n\nfunc TestGen(t *testing.T) { gen() }\n",
}


def _index(tmp_path, files):
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], str(store)


def _dead_files(repo, store):
    r = find_dead_code(repo, granularity="file", min_confidence=0.0, storage_path=store)
    assert "error" not in r, r
    return {d["file"]: d for d in r["dead_files"]}


def test_a_package_main_program_is_not_reported_dead(tmp_path):
    repo, store = _index(tmp_path, APP)
    dead = _dead_files(repo, store)
    for f in ("cmd/tool/main.go", "cmd/tool/flags.go", "internal/store/store.go"):
        assert f not in dead, (f, dead.get(f))


def test_a_library_main_go_and_a_test_only_main_are_still_dead(tmp_path):
    repo, store = _index(tmp_path, APP)
    dead = _dead_files(repo, store)
    assert "lib/main.go" in dead, sorted(dead)
    assert "tools/gen/gen.go" in dead, sorted(dead)


def test_v2_reaches_a_programs_peer(tmp_path):
    repo, store = _index(tmp_path, APP)
    r = get_dead_code_v2(repo, storage_path=store, min_confidence=0.0)
    assert "error" not in r, r
    signals = {s["id"]: s["signals"] for s in r["dead_symbols"]}
    for sid in ("cmd/tool/flags.go::parse#function", "cmd/tool/main.go::main#function"):
        assert "unreachable_file" not in signals.get(sid, []), (sid, signals.get(sid))
    assert "unreachable_file" in signals.get("lib/main.go::Helper#function", []), signals


def test_an_ignored_generator_does_not_revive_the_package_beside_it(tmp_path):
    # `go generate` runs a `//go:build ignore` file; `go build` leaves it out of
    # the package. Rooting it would make its directory live through the
    # package rule, hiding a dead library package beside it.
    files = {
        "go.mod": "module example.com/app\n\ngo 1.22\n",
        "lib/lib.go": "package lib\n\nfunc Helper() int { return 2 }\n",
        "lib/gen.go": "//go:build ignore\n\npackage main\n\nfunc main() {}\n",
        "tools/legacy/gen.go": "// +build ignore\n\npackage main\n\nfunc main() {}\n",
    }
    repo, store = _index(tmp_path, files)
    dead = _dead_files(repo, store)
    for f in ("lib/lib.go", "lib/gen.go", "tools/legacy/gen.go"):
        assert f in dead, (f, sorted(dead))


def test_only_the_package_clause_names_the_package(tmp_path):
    # A block comment above the clause that starts a line with `package main`
    # does not make a library file a program.
    files = {
        "go.mod": "module example.com/app\n\ngo 1.22\n",
        "lib/doc.go": "/*\npackage main is not this package\n*/\npackage lib\n\nfunc main() {}\n",
    }
    repo, store = _index(tmp_path, files)
    assert "lib/doc.go" in _dead_files(repo, store)


def test_v2_counts_a_program_as_an_entry_point_not_a_package_json_entry(tmp_path):
    repo, store = _index(tmp_path, APP)
    meta = get_dead_code_v2(repo, storage_path=store, min_confidence=0.0)["_meta"]
    assert meta["signal_diagnostics"]["entry_points_detected"] == 1, meta["signal_diagnostics"]
    assert "cmd/tool/main.go" not in meta.get("package_json_entries", []), meta
