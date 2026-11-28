"""A dead-code answer over withheld files reads them instead of refusing (L-352).

``find_dead_code`` capped every finding at the unproven ceiling whenever the
index withheld a file for size, and its only advice was to re-index with a
higher ``max_file_size``, a setting a session cannot reach.

A withheld file is withheld from the INDEX, not from the tree. When withheld
files are the only reason for the cap, their imports are read from disk with
the indexer's own extractor and resolved with the graph's own resolver, and
each withheld file is a live root. A withheld importer then counts exactly as
an indexed one would. Whatever that read cannot stand in for keeps the cap.

Review of the first version (a name match) found live files published dead
through a cascade, a Go package, a path alias and a UTF-16 file; those
shapes are pinned below.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING

PAD = "".join(f"def big_{i}():\n    return {i}\n" for i in range(4000))
GO_PAD = "".join(f"func big{i}() int {{ return {i} }}\n" for i in range(4000))
JS_PAD = "".join(f"export function big{i}() {{ return {i}; }}\n" for i in range(4000))


def _index(src, store):
    from jcodemunch_mcp.tools.index_folder import index_folder

    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), max_size=20000)
    assert r.get("success"), r
    return r["repo"]


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("import named_by_main\n\nif __name__ == '__main__':\n    pass\n", encoding="utf-8")
    (src / "named_by_main.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (src / "orphan_mod.py").write_text("def lonely():\n    return 2\n", encoding="utf-8")
    (src / "only_big_uses.py").write_text("import reached_through\n\ndef used():\n    return 3\n", encoding="utf-8")
    (src / "reached_through.py").write_text("def deep():\n    return 4\n", encoding="utf-8")
    (src / "big.py").write_text("import only_big_uses\n" + PAD, encoding="utf-8")
    store = tmp_path / "store"
    return _index(src, store), str(store), src


def _dead(repo_id, store, **kw):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    return find_dead_code(repo_id, granularity="file", storage_path=store, **kw)


def _files(r):
    return {d["file"]: d for d in r["dead_files"]}


def test_the_fixture_withheld_the_large_file(repo):
    repo_id, store, _ = repo
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    owner, name = repo_id.split("/", 1)
    cov = SQLiteIndexStore(base_path=store).load_index(owner, name).coverage
    assert (cov.get("withheld") or {}).get("too_large") == 1
    assert "big.py" in (cov.get("withheld_files") or {})


def test_a_file_nothing_imports_is_reported_at_its_graph_confidence(repo):
    repo_id, store, _ = repo
    r = _dead(repo_id, store)
    assert _files(r)["orphan_mod.py"]["confidence"] == 1.0
    assert "signal_warning" not in r
    ca = r["corpus_adequacy"]
    assert ca["withheld_files_read"] == 1 and ca["blockers_resolved"] == ["withheld_files"]
    assert r["withheld_files"] == ["big.py"]


def test_a_file_a_withheld_file_imports_is_live(repo):
    repo_id, store, _ = repo
    assert "only_big_uses.py" not in _files(_dead(repo_id, store, min_confidence=0))


def test_a_cascade_through_a_withheld_importer_stays_live(repo):
    """Review finding 4: big.py imports only_big_uses, which imports
    reached_through. The second hop must not read as all_importers_dead."""
    repo_id, store, _ = repo
    assert "reached_through.py" not in _files(_dead(repo_id, store, min_confidence=0))


def test_a_relative_js_import_from_a_withheld_file_is_resolved(tmp_path):
    src = tmp_path / "js"
    (src / "src" / "common").mkdir(parents=True)
    (src / "src" / "common" / "helpers.js").write_text("export function h() { return 1; }\n", encoding="utf-8")
    (src / "src" / "lonely.js").write_text("export function l() { return 2; }\n", encoding="utf-8")
    (src / "src" / "app.js").write_text("import { h } from './common/helpers';\n" + JS_PAD, encoding="utf-8")
    store = tmp_path / "store"
    repo_id = _index(src, store)
    files = _files(_dead(repo_id, str(store), min_confidence=0))
    assert "src/common/helpers.js" not in files
    assert files["src/lonely.js"]["confidence"] == 1.0


def test_a_withheld_go_file_keeps_the_cap(tmp_path):
    """Review findings 1-2: Go edges come from package membership and go.mod
    at load, which the resolver does not apply to a text read from disk."""
    src = tmp_path / "go"
    src.mkdir()
    (src / "go.mod").write_text("module example.com/app\n\ngo 1.21\n", encoding="utf-8")
    (src / "server.go").write_text("package main\n\nfunc runServer() {}\n", encoding="utf-8")
    (src / "main.go").write_text("package main\n\nfunc main() { runServer() }\n" + GO_PAD, encoding="utf-8")
    store = tmp_path / "store"
    repo_id = _index(src, store)
    r = _dead(repo_id, str(store), min_confidence=0)
    for entry in r["dead_files"]:
        assert entry["confidence"] <= UNPROVEN_CEILING, entry
    assert "signal_warning" in r


def test_an_undecodable_withheld_file_keeps_the_cap(repo):
    """Review finding 5: text decoded with replacements names nothing."""
    repo_id, store, src = repo
    (src / "big.py").write_bytes(("import only_big_uses\n" + PAD).encode("utf-16"))
    r = _dead(repo_id, store, min_confidence=0)
    assert _files(r)["orphan_mod.py"]["confidence"] == UNPROVEN_CEILING
    assert "signal_warning" in r


def test_an_unreadable_withheld_file_keeps_the_cap(repo):
    repo_id, store, src = repo
    (src / "big.py").unlink()
    r = _dead(repo_id, store, min_confidence=0)
    assert _files(r)["orphan_mod.py"]["confidence"] == UNPROVEN_CEILING
    assert "withheld_files_read" not in r["corpus_adequacy"]
    assert "signal_warning" in r


def test_a_truncated_withheld_listing_keeps_the_cap(repo, monkeypatch):
    from jcodemunch_mcp.tools import _corpus_adequacy as ca
    from jcodemunch_mcp.tools import find_dead_code as fdc

    repo_id, store, _ = repo
    real = ca.read_withheld_files

    def truncated(index):
        cov = dict(index.coverage)
        cov["withheld_files_truncated"] = True
        cov["withheld_files_total"] = 2

        class _I:
            coverage = cov
            source_root = index.source_root

        return real(_I())

    # Patched where it is used: find_dead_code imports the name at module level.
    monkeypatch.setattr(fdc, "read_withheld_files", truncated)
    r = _dead(repo_id, store, min_confidence=0)
    assert _files(r)["orphan_mod.py"]["confidence"] == UNPROVEN_CEILING


def test_another_blocker_beside_withheld_files_keeps_the_cap(repo, monkeypatch):
    from jcodemunch_mcp.tools import find_dead_code as fdc
    from jcodemunch_mcp.tools._corpus_adequacy import assess_corpus

    repo_id, store, _ = repo
    monkeypatch.setattr(
        fdc, "assess_corpus",
        lambda index, **kw: assess_corpus(index, extra_blockers=["runtime_discovery_unresolved"]),
    )
    r = _dead(repo_id, store, min_confidence=0)
    assert _files(r)["orphan_mod.py"]["confidence"] == UNPROVEN_CEILING


def _tree(tmp_path, files):
    src = tmp_path / "t"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    return _index(src, store), str(store)


def test_a_dynamic_import_in_a_withheld_file_keeps_its_targets_capped(tmp_path):
    """Review round 2, finding 1: the boundary reads the withheld file's
    dynamic-import site, so a module it can load is not proven dead."""
    repo_id, store = _tree(tmp_path, {
        "main.py": "import loader\n\nif __name__ == '__main__':\n    pass\n",
        "loader.py": "import importlib\n\ndef load(n):\n    return importlib.import_module('plugins.' + n)\n" + PAD,
        "plugins/__init__.py": "",
        "plugins/alpha.py": "def a():\n    return 1\n",
    })
    files = _files(_dead(repo_id, store, min_confidence=0))
    assert "plugins/alpha.py" not in files or files["plugins/alpha.py"]["confidence"] <= UNPROVEN_CEILING


def test_a_withheld_package_init_that_enumerates_its_modules_keeps_them_live(tmp_path):
    """Review round 2, finding 2: #569's shape through the withheld path."""
    repo_id, store = _tree(tmp_path, {
        "main.py": "import schemas\n\nif __name__ == '__main__':\n    pass\n",
        "schemas/__init__.py": (
            "import importlib\nimport pkgutil\n\n"
            "for _m in pkgutil.iter_modules(__path__):\n"
            "    importlib.import_module(__name__ + '.' + _m.name)\n" + PAD
        ),
        "schemas/enc_a.py": "def enc():\n    return 1\n",
    })
    files = _files(_dead(repo_id, store, min_confidence=0))
    assert "schemas/enc_a.py" not in files or files["schemas/enc_a.py"]["confidence"] <= UNPROVEN_CEILING


def test_a_template_a_withheld_file_renders_is_live(tmp_path):
    """Review round 2, finding 3: the render edge is read from the withheld file."""
    repo_id, store = _tree(tmp_path, {
        "app.py": "from flask import render_template\n\ndef home():\n    return render_template('home.html')\n" + PAD,
        "templates/home.html": "<p>home</p>\n",
    })
    files = _files(_dead(repo_id, store, min_confidence=0))
    assert "templates/home.html" not in files or files["templates/home.html"]["confidence"] <= UNPROVEN_CEILING


def test_a_withheld_manifest_keeps_the_cap(tmp_path):
    """Review round 3, finding 1: entry points come from package.json before
    any withheld file is read, so a withheld manifest cannot be stood in for."""
    pad = ",".join(f'"k{i}": "{i}"' for i in range(3000))
    repo_id, store = _tree(tmp_path, {
        "package.json": '{"name": "p", "main": "lib/core.js", "config": {' + pad + "}}\n",
        "lib/core.js": "export function c() { return 1; }\n",
    })
    r = _dead(repo_id, store, min_confidence=0)
    for entry in r["dead_files"]:
        assert entry["confidence"] <= UNPROVEN_CEILING, entry
    assert "signal_warning" in r


def test_a_released_run_reads_adequate(repo):
    """Review round 3, note 3: a release does not leave adequate False beside a 1.0 ceiling."""
    repo_id, store, _ = repo
    ca = _dead(repo_id, store)["corpus_adequacy"]
    assert ca["adequate"] is True and "blockers" not in ca


KT_PAD = "".join(f"fun big{i}(): Int = {i}\n" for i in range(4000))


def test_a_withheld_kotlin_importer_keeps_the_cap(tmp_path):
    """Review round 4, finding 1: a Kotlin import of a top-level declaration
    gains its edge at load (#1376), which a disk read does not replay."""
    repo_id, store = _tree(tmp_path, {
        "app/Main.kt": "package app\n\nimport a.b.buildSnapshot\n\nfun main() { buildSnapshot() }\n" + KT_PAD,
        "a/b/SnapshotMapper.kt": "package a.b\n\nfun buildSnapshot(): Int = 1\n",
    })
    r = _dead(repo_id, store, min_confidence=0)
    for entry in r["dead_files"]:
        assert entry["confidence"] <= UNPROVEN_CEILING, entry
    assert "signal_warning" in r


def test_every_load_time_edge_language_keeps_the_cap():
    """A language gate in CodeIndex's load names a language the read refuses.
    Reads the source, so a new augmenter must join the set or this fails."""
    import re
    from pathlib import Path

    from jcodemunch_mcp.storage import index_store
    from jcodemunch_mcp.tools._corpus_adequacy import LOAD_AUGMENTED_LANGUAGES

    text = Path(index_store.__file__).read_text(encoding="utf-8")
    gated = set(re.findall(r'"([a-z]+)" in (?:self\.languages|langs|\(self\.languages)', text))
    assert gated, "the scan found no language gate; the pattern no longer matches index_store"
    assert gated <= LOAD_AUGMENTED_LANGUAGES, sorted(gated - LOAD_AUGMENTED_LANGUAGES)


def test_every_augmenter_selector_maps_into_the_set():
    """The augmenters pick importers by extension, not only by a language gate;
    each extension's language must be one the read refuses."""
    from jcodemunch_mcp.parser import imports
    from jcodemunch_mcp.parser.languages import get_language_for_path
    from jcodemunch_mcp.tools._corpus_adequacy import LOAD_AUGMENTED_LANGUAGES

    exts = set(imports._JVM_IMPORTER_EXTENSIONS) | set(imports._RACKET_EXTENSIONS)
    assert exts
    langs = {get_language_for_path("f" + e) for e in exts}
    assert langs <= LOAD_AUGMENTED_LANGUAGES, sorted(map(str, langs - LOAD_AUGMENTED_LANGUAGES))
