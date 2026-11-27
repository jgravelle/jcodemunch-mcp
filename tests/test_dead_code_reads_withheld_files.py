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
    real = ca.read_withheld_imports

    def truncated(index):
        cov = dict(index.coverage)
        cov["withheld_files_truncated"] = True
        cov["withheld_files_total"] = 2

        class _I:
            coverage = cov
            source_root = index.source_root

        return real(_I())

    # Patched where it is used: find_dead_code imports the name at module level.
    monkeypatch.setattr(fdc, "read_withheld_imports", truncated)
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
