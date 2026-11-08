"""FreshnessProbe.prime_mtimes stats ahead on threads and changes no verdict.

The Grep answer refuses unless every file in scope still has the mtime it was
indexed at. Its per-file stats now run on a few threads first; these tests pin
that each file's classification is identical with and without the priming, for
unchanged, edited and missing files, and that the stats really are made ahead.
"""

from __future__ import annotations

import os
import threading
import time

from jcodemunch_mcp.retrieval import freshness as fr


def _tree(tmp_path, n=400):
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    rels = []
    for i in range(n):
        rel = f"pkg/m{i}.py"
        (root / rel).write_text("x = 1\n", encoding="utf-8")
        rels.append(rel)
    mtimes = {rel: os.stat(root / rel).st_mtime_ns for rel in rels}
    return root, rels, mtimes


def _probe(root, mtimes):
    return fr.FreshnessProbe(str(root), "", None, file_mtimes=mtimes)


def test_priming_gives_the_same_verdict_for_every_file(tmp_path):
    root, rels, mtimes = _tree(tmp_path)
    edited, gone = rels[7], rels[11]
    later = time.time() + 30
    os.utime(root / edited, (later, later))
    os.remove(root / gone)
    rels_and_unindexed = rels + ["pkg/never_indexed.py"]

    plain = _probe(root, mtimes)
    expected = {rel: plain.classify(rel) for rel in rels_and_unindexed}
    primed = _probe(root, mtimes)
    primed.prime_mtimes(rels_and_unindexed)
    got = {rel: primed.classify(rel) for rel in rels_and_unindexed}

    assert got == expected
    assert expected[edited] != "fresh"
    assert expected[gone] != "fresh"
    assert expected[rels[0]] == "fresh"


def test_priming_makes_the_stats_before_classify_runs(tmp_path, monkeypatch):
    root, rels, mtimes = _tree(tmp_path)
    probe = _probe(root, mtimes)
    threads = set()
    real = os.stat

    def spy(path, *a, **kw):
        threads.add(threading.get_ident())
        return real(path, *a, **kw)

    monkeypatch.setattr(fr.os, "stat", spy)
    probe.prime_mtimes(rels)
    assert set(probe._mtime_cache) == set(rels)
    assert threading.get_ident() not in threads  # made on the pool, not inline

    calls = []
    monkeypatch.setattr(fr.Path, "stat", lambda self, *a, **kw: calls.append(self) or real(self))
    assert all(probe.classify(rel) == "fresh" for rel in rels)
    assert calls == []  # classify read every mtime from the primed cache


def test_a_small_scope_or_no_source_root_is_left_to_classify(tmp_path):
    root, rels, mtimes = _tree(tmp_path, n=10)
    probe = _probe(root, mtimes)
    probe.prime_mtimes(rels)
    assert probe._mtime_cache == {}
    rootless = fr.FreshnessProbe(None, "", None, file_mtimes=mtimes)
    rootless.prime_mtimes(rels * 100)
    assert rootless._mtime_cache == {}
