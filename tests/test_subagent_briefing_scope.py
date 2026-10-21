"""#1139 (@treyecollins): the SubagentStart briefing's cost must be bounded.

The hook runs synchronously on Claude Code's agent-startup path. It used to
keep every stored repo whose root overlapped the cwd in EITHER direction, and
every repo when the cwd was missing or matched nothing, then hydrate and
PageRank each one with no time budget: 486 recorded runs, 90.4 s median,
533 s maximum. The four selection cases are the reporter's probe.

What is pinned:
  - a project cwd hydrates ONE index, its most specific containing root, never
    the ancestor index that also contains it;
  - a broad parent cwd names its child indexes from list_repos() stats and
    hydrates none of them;
  - an unindexed or missing cwd hydrates nothing (brief nothing, never
    everything);
  - an index past the size cap is briefed from stats and never hydrated;
  - past the time budget, PageRank is skipped.
"""

import io
import json
import sys
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import briefing, run_subagentstart


def _run(stdin_text: str) -> str:
    fake_in, fake_out = io.StringIO(stdin_text), io.StringIO()
    with mock.patch.object(sys, "stdin", fake_in), \
         mock.patch.object(sys, "stdout", fake_out):
        assert run_subagentstart() == 0
    return fake_out.getvalue()


def _ctx(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """The reporter's layout: an ancestor index, two nested projects, and an
    unrelated index. Real directories, so _norm_path's realpath applies."""
    work = tmp_path / "work"
    a, b, elsewhere = work / "a", work / "b", tmp_path / "elsewhere"
    for d in (a, b, elsewhere):
        d.mkdir(parents=True)
    repos = [
        {"repo": "local/ancestor", "source_root": str(work), "symbol_count": 10, "file_count": 3},
        {"repo": "local/project-a", "source_root": str(a), "symbol_count": 10, "file_count": 2},
        {"repo": "local/project-b", "source_root": str(b), "symbol_count": 10, "file_count": 2},
        {"repo": "local/unrelated", "source_root": str(elsewhere), "symbol_count": 10, "file_count": 1},
    ]
    loaded: list = []
    idx = mock.MagicMock()
    idx.symbols = [{"name": "f", "kind": "function", "file": "m.py", "line": 1, "language": "python"}]
    idx.source_files = ["m.py"]
    idx.imports = {}

    def _load(self, owner, name):
        loaded.append(name)
        return idx

    store = type("MockStore", (), {
        "__init__": lambda self, **kw: None,
        "list_repos": lambda self: repos,
        "load_index": _load,
    })
    monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", store)
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
    return {"work": work, "a": a, "repos": repos, "loaded": loaded, "tmp": tmp_path}


def test_project_cwd_hydrates_only_its_most_specific_root(tree):
    ctx = _ctx(_run(json.dumps({"cwd": str(tree["a"])})))
    assert tree["loaded"] == ["project-a"]
    assert "local/project-a" in ctx
    assert "local/ancestor" not in ctx


def test_cwd_below_a_root_picks_the_deepest_containing_root(tree):
    deep = tree["a"] / "src" / "pkg"
    deep.mkdir(parents=True)
    _run(json.dumps({"cwd": str(deep)}))
    assert tree["loaded"] == ["project-a"]


def test_broad_parent_cwd_hydrates_nothing_and_names_children(tree):
    parent = tree["tmp"]  # contains every root, and no root contains it
    ctx = _ctx(_run(json.dumps({"cwd": str(parent)})))
    assert tree["loaded"] == []
    for name in ("local/ancestor", "local/project-a", "local/project-b", "local/unrelated"):
        assert name in ctx
    assert "**Symbols:** 10" in ctx  # from list_repos(), no hydration


def test_unindexed_cwd_hydrates_nothing(tree):
    nowhere = tree["tmp"] / "not-indexed"
    nowhere.mkdir()
    ctx = _ctx(_run(json.dumps({"cwd": str(nowhere)})))
    assert tree["loaded"] == []
    assert "local/" not in ctx
    assert "search_symbols" in ctx  # the catalog still briefs; it costs nothing


@pytest.mark.parametrize("payload", [{}, {"cwd": ""}, {"cwd": 7}])
def test_missing_cwd_hydrates_nothing(tree, payload):
    ctx = _ctx(_run(json.dumps(payload)))
    assert tree["loaded"] == []
    assert "local/" not in ctx


def test_index_past_the_size_cap_is_briefed_from_stats(tree):
    for e in tree["repos"]:
        if e["repo"] == "local/project-a":
            e["symbol_count"] = briefing.HYDRATE_MAX_SYMBOLS + 1
    ctx = _ctx(_run(json.dumps({"cwd": str(tree["a"])})))
    assert tree["loaded"] == []
    assert "local/project-a" in ctx
    assert f"**Symbols:** {briefing.HYDRATE_MAX_SYMBOLS + 1}" in ctx


def test_unknown_size_is_not_hydrated(tree):
    """No symbol_count is UNKNOWN, never small: an unknown size is not a
    license to put a hydration of unknown cost on the startup path."""
    for e in tree["repos"]:
        if e["repo"] == "local/project-a":
            e.pop("symbol_count")
    ctx = _ctx(_run(json.dumps({"cwd": str(tree["a"])})))
    assert tree["loaded"] == []
    assert "index size unknown" in ctx
    assert "too large" not in ctx


def test_pagerank_is_skipped_past_the_budget(tree, monkeypatch):
    clock = iter([0.0, briefing.BUDGET_SECONDS + 1.0])
    monkeypatch.setattr(briefing.time, "monotonic", lambda: next(clock, 10_000.0))
    called = []
    monkeypatch.setattr(briefing, "_top_symbols_by_pagerank",
                        lambda *a, **k: called.append(1) or [])
    ctx = _ctx(_run(json.dumps({"cwd": str(tree["a"])})))
    assert tree["loaded"] == ["project-a"]
    assert called == []
    assert "local/project-a" in ctx


def test_pagerank_runs_inside_the_budget(tree, monkeypatch):
    monkeypatch.setattr(briefing.time, "monotonic", lambda: 0.0)
    called = []
    monkeypatch.setattr(briefing, "_top_symbols_by_pagerank",
                        lambda *a, **k: called.append(1) or [])
    _run(json.dumps({"cwd": str(tree["a"])}))
    assert called == [1]


def test_child_listing_is_capped(tree):
    work = tree["work"]
    extra = []
    for i in range(briefing.MAX_LISTED_REPOS + 5):
        d = work / f"c{i:02d}"
        d.mkdir()
        extra.append({"repo": f"local/c{i:02d}", "source_root": str(d),
                      "symbol_count": 1, "file_count": 1})
    tree["repos"][:] = extra
    ctx = _ctx(_run(json.dumps({"cwd": str(work)})))
    listed = [line for line in ctx.splitlines() if line.startswith("### local/")]
    assert len(listed) == briefing.MAX_LISTED_REPOS
    assert "and 5 more" in ctx
    assert tree["loaded"] == []


def test_a_real_index_is_hydrated_through_the_real_list_repos(tmp_path, monkeypatch):
    """No mocked store. The hydrate gate reads ``symbol_count`` from
    ``list_repos()``, so if the real producer stopped setting it every
    briefing would quietly turn stats-only while the mocked tests stayed
    green (Standing lesson 08-13). Index a real folder; the hook must load
    it and list its symbols."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    src, store = tmp_path / "proj", tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "app.py").write_text(
        "def distinctive_briefing_probe():\n    return 1\n", encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success") is not False, r
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")

    ctx = _ctx(_run(json.dumps({"cwd": str(src)})))
    assert "**Languages:** python" in ctx  # only a hydrated index knows this
    assert "stats only" not in ctx


def test_selection_rule_is_the_reporters_four_cases(tree):
    """The probe from the issue, run against the selection function itself."""
    roots = {e["repo"]: e for e in tree["repos"]}

    def sel(cwd):
        hydrate, listed = briefing._select_repos(list(roots.values()), cwd)
        return ([e["repo"] for e in hydrate], sorted(e["repo"] for e in listed))

    assert sel(str(tree["a"])) == (["local/project-a"], [])
    assert sel(str(tree["work"])) == (["local/ancestor"], [])
    assert sel(str(tree["tmp"] / "nope")) == ([], [])
    assert sel(None) == ([], [])
