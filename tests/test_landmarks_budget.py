"""L-219: the landmarks section's cost must be bounded, as the briefing's is (#1139).

`_build_landmark_section` runs inside the PreCompact hook, which Claude Code
waits for before it compacts. It hydrated and PageRanked every index holding
a file the session touched, with no size cap and no time budget, so one touched
file in a very large index put that whole hydration on the compaction path.

What is pinned:
  - an index past the size cap, or of unknown size, is never hydrated, and the
    section names it as skipped rather than dropping it in silence;
  - a small index is still hydrated and ranked;
  - once the budget is spent, no further index is started;
  - the shared rule lives in `_common`, and the briefing reads the same one.
"""

import json
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import _common, briefing, landmarks


def _ctx(*files):
    return {"files_edited": [{"file": f} for f in files], "files_accessed": []}


@pytest.fixture
def store(monkeypatch):
    loaded: list = []
    repos: list = []

    def make_idx():
        idx = mock.MagicMock()
        idx.imports = {"a.py": []}
        idx.source_files = ["a.py"]
        idx.symbols = [{"id": "a.py::f", "name": "f", "kind": "function", "file": "a.py", "line": 1}]
        return idx

    def _load(self, owner, name):
        loaded.append(name)
        return make_idx()

    fake = type("MockStore", (), {
        "__init__": lambda self, **kw: None,
        "list_repos": lambda self: repos,
        "load_index": _load,
    })
    monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", fake)
    # The membership probe reads a real .db; with none it answers True
    # ("hydrate on any doubt"), which is what these tests want.
    monkeypatch.setattr(_common, "_repo_contains_any", lambda *a, **k: True)
    monkeypatch.setattr(landmarks, "_top_symbols_by_pagerank",
                        lambda idx, **k: [(idx.symbols[0], 1.0)])
    return {"loaded": loaded, "repos": repos}


def test_a_small_index_is_still_ranked(store):
    store["repos"].append({"repo": "local/small", "symbol_count": 10})
    out = landmarks._build_landmark_section(context=_ctx("a.py"))
    assert store["loaded"] == ["small"]
    assert "Structural Landmarks (local/small)" in out


def test_an_index_past_the_cap_is_skipped_and_named(store):
    store["repos"].append({"repo": "local/huge", "symbol_count": _common.HYDRATE_MAX_SYMBOLS + 1})
    out = landmarks._build_landmark_section(context=_ctx("a.py"))
    assert store["loaded"] == []
    assert "local/huge" in out
    assert "too large" in out


def test_an_index_of_unknown_size_is_skipped_and_named(store):
    store["repos"].append({"repo": "local/nocount"})
    out = landmarks._build_landmark_section(context=_ctx("a.py"))
    assert store["loaded"] == []
    assert "local/nocount" in out
    assert "size unknown" in out
    assert "too large" not in out


@pytest.mark.parametrize("bad", [True, -1, None, "12"])
def test_a_corrupt_count_reads_as_unknown_never_too_large(bad):
    assert _common._too_large_note({"symbol_count": bad}) == "index size unknown"
    assert not _common._small_enough(bad)


def test_no_index_is_started_once_the_budget_is_spent(store, monkeypatch):
    store["repos"].extend([
        {"repo": "local/first", "symbol_count": 10},
        {"repo": "local/second", "symbol_count": 10},
    ])
    clock = iter([0.0, 0.0, landmarks.BUDGET_SECONDS + 1.0])
    monkeypatch.setattr(landmarks.time, "monotonic", lambda: next(clock, 10_000.0))
    out = landmarks._build_landmark_section(context=_ctx("a.py"))
    assert store["loaded"] == ["first"]
    assert "local/second" in out and "time budget" in out


def test_the_briefing_and_landmarks_share_one_size_rule():
    """One rule, one place: a second copy is how L-219 happened."""
    assert briefing.HYDRATE_MAX_SYMBOLS is _common.HYDRATE_MAX_SYMBOLS
    assert briefing._small_enough is _common._small_enough
    assert landmarks.BUDGET_SECONDS == briefing.BUDGET_SECONDS


def test_a_real_index_is_ranked_through_the_real_list_repos(tmp_path, monkeypatch):
    """No mocked store: the cap reads `symbol_count` from the real list_repos()."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    src, st = tmp_path / "proj", tmp_path / "store"
    src.mkdir()
    st.mkdir()
    (src / "a.py").write_text("import b\n\ndef first_probe():\n    return b.second_probe()\n", encoding="utf-8")
    (src / "b.py").write_text("def second_probe():\n    return 1\n", encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(st))
    assert r.get("success") is not False, r
    monkeypatch.setenv("CODE_INDEX_PATH", str(st))

    out = landmarks._build_landmark_section(context=_ctx("a.py"))
    assert "Recently Changed" in out and "first_probe" in out
    assert "skipped" not in out
    assert json.dumps(out)  # a plain string
