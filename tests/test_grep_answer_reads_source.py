"""The Grep answer reads a proved-unchanged file from its source, not its cached copy.

On a just-indexed workspace the index's cached copies are files written
moments ago, and on Windows the first open of such a file is slow (15.2 ms per
file against 0.08 ms for the source files the indexer had just read, measured
on a fresh Home Assistant index). The Grep answer's freshness check has already
proved every file in scope unchanged since indexing, and a cached copy is the
source as read at index time, so the answer reads the source. Everything else
(the server's `search_text`) still reads the cached copies.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import sys
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import grep_answer, run_pretooluse
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import search_text as st
from jcodemunch_mcp.tools.index_folder import index_folder


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(12):
        body = "".join(f"x = 'needle line {j}'\n" for j in range(i % 4))
        (src / f"m{i:02d}.py").write_text(f"def f{i}():\n    return {i}\n{body}", encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"]
    owner, name = res["repo"].split("/", 1)
    content_dir = IndexStore(base_path=str(store))._content_dir(owner, name)
    return res["repo"], str(store), src, content_dir


def _opened(monkeypatch):
    seen = []
    real = builtins.open

    def spy(path, *a, **kw):
        seen.append(os.path.normcase(os.path.abspath(str(path))))
        return real(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    return seen


def _under(paths, root):
    root = os.path.normcase(os.path.abspath(str(root)))
    return [p for p in paths if p.startswith(root + os.sep) and p.endswith(".py")]


def _comparable(res):
    res = dict(res)
    res.pop("timing_ms", None)
    meta = dict(res.pop("_meta", None) or {})
    for key in ("timing_ms", "total_tokens_saved"):
        meta.pop(key, None)
    res["_meta"] = meta
    return res


def test_a_read_root_reads_the_source_and_not_the_cached_copy(repo, monkeypatch):
    rid, store, src, content_dir = repo
    seen = _opened(monkeypatch)
    res = st.search_text(rid, "needle", storage_path=store, scan_limit=1000, _read_root=str(src))
    assert res["results"]
    assert _under(seen, src), "no source file was read"
    assert not _under(seen, content_dir), "a cached copy was read"


@pytest.mark.parametrize("query,kwargs", [
    ("needle", {"scan_limit": 1000}),
    ("needle", {"max_results": 5}),  # a cut page: the rest is counted from the same base
    ("needle line [23]", {"is_regex": True, "max_results": 3}),
    ("no such text anywhere", {}),
])
def test_the_answer_is_the_same_from_either_base(repo, query, kwargs):
    rid, store, src, _ = repo
    cached = st.search_text(rid, query, storage_path=store, **kwargs)
    source = st.search_text(rid, query, storage_path=store, _read_root=str(src), **kwargs)
    assert _comparable(source) == _comparable(cached)


def test_without_a_read_root_the_cached_copies_are_read(repo, monkeypatch):
    rid, store, src, content_dir = repo
    seen = _opened(monkeypatch)
    st.search_text(rid, "needle", storage_path=store, scan_limit=1000)
    assert _under(seen, content_dir)
    assert not _under(seen, src)


def _link_dir(link, target):
    """A directory link without admin rights: a symlink, or a junction on Windows."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def test_a_file_that_leaves_the_root_is_not_read(tmp_path, monkeypatch):
    """A directory link inside the source root pointing outside it is refused, not followed.

    A junction needs no admin rights on Windows, so this runs there instead of
    skipping like a file symlink would.
    """
    src = tmp_path / "proj"
    (src / "sub").mkdir(parents=True)
    (src / "a.py").write_text("x = 'needle inside'\n", encoding="utf-8")
    (src / "sub" / "b.py").write_text("x = 'needle sub'\n", encoding="utf-8")
    store = tmp_path / "store"
    idx = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert idx["success"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.py").write_text("x = 'needle outside'\n", encoding="utf-8")
    (src / "sub" / "b.py").unlink()
    (src / "sub").rmdir()
    _link_dir(src / "sub", outside)
    seen = _opened(monkeypatch)
    res = st.search_text(idx["repo"], "needle", storage_path=str(store), scan_limit=1000, _read_root=str(src))
    assert not _under(seen, outside)
    assert "needle outside" not in json.dumps(res)
    assert "needle inside" in json.dumps(res)


def test_a_source_read_records_no_trigram_row(tmp_path):
    """The trigram sidecar answers for the cached copy's text, so a source read must not write it.

    A source edited with its mtime kept passes the freshness check. Had the
    search recorded the source's trigrams under the cached copy's stamp, a
    later server search for the cached copy's text would be ruled out and
    come back empty.
    """
    from jcodemunch_mcp.storage import text_filter as tf
    import sqlite3

    src = tmp_path / "proj"
    src.mkdir()
    f = src / "a.py"
    f.write_text("def a():\n    return 'oldneedle'\n", encoding="utf-8")
    (src / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"]
    rid = res["repo"]
    owner, name = rid.split("/", 1)
    content_dir = IndexStore(base_path=str(store))._content_dir(owner, name)
    con = sqlite3.connect(str(tf.sidecar_path(content_dir)))
    con.execute(f"DELETE FROM {tf._TABLE}")
    con.commit()
    con.close()
    before = f.stat()
    f.write_text("def a():\n    return 'newneedle'\n", encoding="utf-8")
    os.utime(f, ns=(before.st_atime_ns, before.st_mtime_ns))

    st.search_text(rid, "needle", storage_path=str(store), scan_limit=1000, _read_root=str(src))
    assert tf.load(content_dir) == {}
    plain = st.search_text(rid, "oldneedle", storage_path=str(store))
    assert plain.get("result_count") == 1, plain


# --- the hook passes the source root, and only after freshness -------------


def _run(payload: dict) -> str:
    out = io.StringIO()
    with (
        mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
        mock.patch.object(sys, "stdout", out),
        mock.patch.object(sys, "stderr", io.StringIO()),
    ):
        assert run_pretooluse() == 0
    return out.getvalue()


@pytest.fixture
def hooked_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    monkeypatch.delenv("JCODEMUNCH_ENFORCE", raising=False)
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER", "1")
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER_MIN_LINES", "1")
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER_MIN_FILES", "1")
    proj = tmp_path / "proj"
    proj.mkdir()
    for i in range(3):
        (proj / f"mod_{i}.py").write_text(f"def h{i}():\n    x = 'beacon_{i}'\n    return x\n", encoding="utf-8")
    assert index_folder(str(proj), use_ai_summaries=False)["success"]
    return proj


def _ask(cwd, session):
    return _run({
        "hook_event_name": "PreToolUse",
        "tool_name": "Grep",
        "tool_input": {"pattern": "beacon"},
        "cwd": str(cwd),
        "session_id": session,
    })


def _capture_search(monkeypatch):
    calls = []
    real = st.search_text

    def capture(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(st, "search_text", capture)
    return calls


def test_the_hook_reads_the_source_root_once_freshness_passes(hooked_repo, monkeypatch):
    calls = _capture_search(monkeypatch)
    out = _ask(hooked_repo, "fresh-1")
    assert grep_answer.ANSWER_MARKER in out
    assert len(calls) == 1
    root = calls[0].get("_read_root")
    assert root and os.path.normcase(os.path.realpath(root)) == os.path.normcase(os.path.realpath(hooked_repo))


def test_the_hook_does_not_search_when_freshness_fails(hooked_repo, monkeypatch):
    calls = _capture_search(monkeypatch)
    monkeypatch.setattr(grep_answer, "_index_matches_disk", lambda *a, **kw: False)
    out = _ask(hooked_repo, "stale-1")
    assert grep_answer.ANSWER_MARKER not in out
    assert calls == []
