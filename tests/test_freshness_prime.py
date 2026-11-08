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


def test_the_grep_answer_primes_the_scope_before_it_classifies(tmp_path, monkeypatch):
    """The 4 s claim rests on this call; without it classify() stats serially."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    proj = tmp_path / "proj"
    (proj / "inside").mkdir(parents=True)
    (proj / "outside").mkdir()
    (proj / "inside" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (proj / "outside" / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    from jcodemunch_mcp.cli.hooks import grep_answer
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    assert index_folder(str(proj), use_ai_summaries=False)["success"]
    name = resolve_repo(str(proj))["repo"]
    primed = []
    real = fr.FreshnessProbe.prime_mtimes

    def spy(self, files):
        files = list(files)
        primed.append(files)
        return real(self, files)

    monkeypatch.setattr(fr.FreshnessProbe, "prime_mtimes", spy)
    assert grep_answer._index_matches_disk(name, "inside/*") is True
    assert primed == [["inside/a.py"]]


_ABANDON_SCRIPT = r"""
import os, sys, threading, time
sys.path.insert(0, sys.argv[1])
from jcodemunch_mcp.retrieval import freshness as fr
real = os.stat
def slow(path, *a, **kw):
    time.sleep(0.05)
    return real(path, *a, **kw)
os.stat = slow
probe = fr.FreshnessProbe(sys.argv[2], "", None)
rels = ["f%d.py" % i for i in range(2000)]
threading.Thread(target=probe.prime_mtimes, args=(rels,), daemon=True).start()
time.sleep(0.3)
"""


def test_an_abandoned_priming_does_not_hold_the_process_open(tmp_path):
    """The hook abandons its thread at the budget and exits; queued stats must not keep it alive.

    2,000 stats at 50 ms on 8 threads is about 12.5 s of queued work.
    """
    import subprocess
    import sys
    from pathlib import Path

    src = str(Path(fr.__file__).resolve().parents[2])
    script = tmp_path / "abandon.py"
    script.write_text(_ABANDON_SCRIPT, encoding="utf-8")
    t0 = time.monotonic()
    subprocess.run([sys.executable, str(script), src, str(tmp_path)], check=True, timeout=60)
    assert time.monotonic() - t0 < 5.0
