"""A dead-code answer over withheld files reads them instead of refusing (L-352).

``find_dead_code`` capped every finding at the unproven ceiling whenever the
index withheld a file for size, and its only advice was to re-index with a
higher ``max_file_size``, a setting a session cannot reach. On BM-2's L16 task
(vscode, 29 files withheld) 16 of 18 calls came back as that refusal, and the
sessions either gave up or fell back to grep-and-read loops.

A withheld file is withheld from the INDEX, not from the tree. When the
withheld files are the only reason for the cap, the record lists all of them
and each one can be read, a candidate whose module name appears in none of
them cannot be imported by them. It is reported at its graph confidence. A
candidate a withheld file names keeps the cap and the reply names that file.
Anything the read cannot establish keeps the cap.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING


@pytest.fixture
def repo(tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("import named_by_big\n\nif __name__ == '__main__':\n    pass\n", encoding="utf-8")
    (src / "named_by_big.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (src / "orphan_mod.py").write_text("def lonely():\n    return 2\n", encoding="utf-8")
    (src / "only_big_uses.py").write_text("def used():\n    return 3\n", encoding="utf-8")
    big = "import only_big_uses\n" + "".join(f"def big_{i}():\n    return {i}\n" for i in range(4000))
    (src / "big.py").write_text(big, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), max_size=20000)
    assert r.get("success"), r
    return r["repo"], str(store), src


def _dead(repo_id, store, **kw):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    return find_dead_code(repo_id, granularity="file", storage_path=store, **kw)


def test_the_fixture_withheld_the_large_file(repo):
    repo_id, store, _ = repo
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    owner, name = repo_id.split("/", 1)
    cov = SQLiteIndexStore(base_path=store).load_index(owner, name).coverage
    assert (cov.get("withheld") or {}).get("too_large") == 1
    assert "big.py" in (cov.get("withheld_files") or {})


def test_a_candidate_no_withheld_file_names_is_reported_at_its_graph_confidence(repo):
    repo_id, store, _ = repo
    r = _dead(repo_id, store)
    files = {d["file"]: d for d in r["dead_files"]}
    assert "orphan_mod.py" in files, r
    assert files["orphan_mod.py"]["confidence"] == 1.0
    assert r["corpus_adequacy"]["withheld_files_read"] == 1


def test_a_candidate_a_withheld_file_names_keeps_the_cap_and_names_the_file(repo):
    repo_id, store, _ = repo
    r = _dead(repo_id, store, min_confidence=0)
    files = {d["file"]: d for d in r["dead_files"]}
    entry = files["only_big_uses.py"]
    assert entry["confidence"] == UNPROVEN_CEILING
    assert entry["named_by_withheld"] == ["big.py"]
    assert "withheld_files" in entry["confidence_capped_by"]
    # At the default threshold it is not published as dead.
    assert "only_big_uses.py" not in {d["file"] for d in _dead(repo_id, store)["dead_files"]}


def test_the_reply_lists_the_withheld_files_so_the_model_can_read_one(repo):
    repo_id, store, _ = repo
    r = _dead(repo_id, store)
    assert r["withheld_files"] == ["big.py"]
    assert "signal_warning" not in r or "re-index" not in r.get("next_step", "")
    assert "big.py" in r["next_step"]


def test_an_unreadable_withheld_file_keeps_the_cap(repo):
    repo_id, store, src = repo
    (src / "big.py").unlink()
    r = _dead(repo_id, store, min_confidence=0)
    files = {d["file"]: d for d in r["dead_files"]}
    assert files["orphan_mod.py"]["confidence"] == UNPROVEN_CEILING
    assert "withheld_files_read" not in r["corpus_adequacy"]
    assert "signal_warning" in r


def test_a_truncated_withheld_listing_keeps_the_cap(repo, monkeypatch):
    from jcodemunch_mcp.tools import _corpus_adequacy as ca
    from jcodemunch_mcp.tools import find_dead_code as fdc

    repo_id, store, _ = repo
    real = ca.read_withheld_texts

    def truncated(index):
        cov = dict(index.coverage)
        cov["withheld_files_truncated"] = True
        cov["withheld_files_total"] = 2

        class _I:
            coverage = cov
            source_root = index.source_root

        return real(_I())

    # Patched where it is used: find_dead_code imports the name at module level.
    monkeypatch.setattr(fdc, "read_withheld_texts", truncated)
    r = _dead(repo_id, store, min_confidence=0)
    files = {d["file"]: d for d in r["dead_files"]}
    assert files["orphan_mod.py"]["confidence"] == UNPROVEN_CEILING


def test_another_blocker_beside_withheld_files_keeps_the_cap(repo, monkeypatch):
    from jcodemunch_mcp.tools import find_dead_code as fdc
    from jcodemunch_mcp.tools._corpus_adequacy import assess_corpus

    repo_id, store, _ = repo
    monkeypatch.setattr(
        fdc, "assess_corpus",
        lambda index, **kw: assess_corpus(index, extra_blockers=["runtime_discovery_unresolved"]),
    )
    r = _dead(repo_id, store, min_confidence=0)
    files = {d["file"]: d for d in r["dead_files"]}
    assert files["orphan_mod.py"]["confidence"] == UNPROVEN_CEILING
