"""A text sweep cut short is not a sweep that found nothing (LEDGER L-309).

The deletion investigator's `no_textual_use` obligation asks `search_text`
for the name and reads `results` only. `search_text` returns 20 lines by
default, so a symbol whose own file mentions its name 20 times or more fills
the page with its own file. A use in another file is never returned, and the
obligation read `SATISFIED`: "appears only in its own file". The same holds
for a scan stopped by the regex time budget and for an index that the
`max_folder_files` cap left without some files; `search_text` reports all
three, and the obligation looked at none of them.

The sweep now asks for the whole match set, and when the answer still says it
was cut short, a sweep that found no other file is `UNESTABLISHED`, never
`SATISFIED`. A use it did find still refutes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.investigator import REFUTED, SATISFIED, UNESTABLISHED
from jcodemunch_mcp.investigator.deletion_safety import investigate_deletion_safety
from jcodemunch_mcp.tools.index_folder import index_folder

# `aaa.py` sorts ahead of `main.py`, so a page of 20 lines is all its own.
_OWN_MENTIONS = 30


def _index(root: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    storage = str(root / ".index")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return res.get("repo", str(root)), storage


def _files(own_mentions: int) -> dict[str, str]:
    body = "def zap():\n    return 1\n" + "".join(
        f"# zap note {i}\n" for i in range(own_mentions)
    )
    return {
        "aaa.py": body,
        # A use the import graph cannot see: the name reached by string.
        "main.py": 'import aaa\n\nif __name__ == "__main__":\n    getattr(aaa, "zap")()\n',
    }


def _text_ob(result: dict) -> dict:
    for o in result["obligations"]:
        if o["obligation"] == "no_textual_use":
            return o
    raise AssertionError(result["obligations"])


def test_a_use_past_the_first_page_still_refutes(tmp_path):
    repo, storage = _index(tmp_path, _files(_OWN_MENTIONS))
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    ob = _text_ob(res)
    assert ob["status"] == REFUTED, ob
    assert res["verdict"] != "safe"


def test_the_use_refutes_when_the_own_file_is_short(tmp_path):
    """Control: the fixture's use is visible to the sweep at all."""
    repo, storage = _index(tmp_path, _files(2))
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    assert _text_ob(res)["status"] == REFUTED


@pytest.mark.parametrize(
    "signal",
    [
        {"truncated": True},
        {"timed_out": True},
        {"_meta": {"index_truncated": {"truncated": True}}},
    ],
    ids=["max_results", "timed_out", "index_cap"],
)
def test_a_cut_short_sweep_with_no_other_file_is_unestablished(
    tmp_path, monkeypatch, signal
):
    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    import jcodemunch_mcp.tools.search_text as st

    def cut_short(*a, **k):
        out = {
            "result_count": 1,
            "truncated": False,
            "timed_out": False,
            "results": [{"file": "aaa.py", "line": 1, "text": "def zap():"}],
            "_meta": {},
        }
        out.update(signal)
        return out

    monkeypatch.setattr(st, "search_text", cut_short)
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    ob = _text_ob(res)
    assert ob["status"] == UNESTABLISHED, ob
    assert res["verdict"] != "safe"


def test_a_complete_sweep_of_the_own_file_only_is_satisfied(tmp_path):
    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    assert _text_ob(res)["status"] == SATISFIED


def test_the_sweep_asks_for_the_whole_match_set(tmp_path, monkeypatch):
    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    import jcodemunch_mcp.tools.search_text as st

    seen: list = []
    real = st.search_text

    def spy(*a, **k):
        seen.append(k.get("scan_limit"))
        return real(*a, **k)

    monkeypatch.setattr(st, "search_text", spy)
    investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    assert seen and seen[0] == st.SCAN_LIMIT_MAX


def test_a_use_in_a_file_withheld_for_size_is_not_read_as_absent(tmp_path, monkeypatch):
    """Review round 1: the only other use sits in a file over max_file_size."""
    from jcodemunch_mcp import config as config_module

    # Set in the loaded config, never through load_config() (Practice 8).
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_file_size", 2000)
    repo, storage = _index(
        tmp_path,
        {
            "aaa.py": "def zap():\n    return 1\n",
            "big.py": 'import aaa\ngetattr(aaa, "zap")()\n'
            + "# pad xxxxxxxxxxxxxxxxxxxx\n" * 200,
        },
    )
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    ob = _text_ob(res)
    assert ob["status"] == UNESTABLISHED, ob
    assert any("max_file_size" in e for e in ob["evidence"]), ob
    assert res["verdict"] not in ("safe", "static_clear")


def _mock_sweep(monkeypatch, other_file: str, **signal):
    import jcodemunch_mcp.tools.search_text as st

    def fake(*a, **k):
        out = {
            "result_count": 2,
            "truncated": False,
            "timed_out": False,
            "results": [
                {"file": "aaa.py", "line": 1, "text": "def zap():"},
                {"file": other_file, "line": 1, "text": "zap"},
            ],
            "_meta": {},
        }
        out.update(signal)
        return out

    monkeypatch.setattr(st, "search_text", fake)


def test_only_unreachable_other_files_in_a_cut_short_sweep_is_unestablished(
    tmp_path, monkeypatch
):
    """A dead mention found does not prove the unseen rest is dead too."""
    from jcodemunch_mcp.investigator import deletion_safety as ds

    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    _mock_sweep(monkeypatch, "dead.py", truncated=True)
    monkeypatch.setattr(
        ds, "_split_importers_by_liveness", lambda r, f, s: (list(f), [])
    )
    ob = _text_ob(
        investigate_deletion_safety(
            repo, "zap", include_runtime=False, storage_path=storage
        )
    )
    assert ob["status"] == UNESTABLISHED, ob


def test_only_unreachable_other_files_in_a_complete_sweep_is_satisfied(
    tmp_path, monkeypatch
):
    from jcodemunch_mcp.investigator import deletion_safety as ds

    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    _mock_sweep(monkeypatch, "dead.py")
    monkeypatch.setattr(
        ds, "_split_importers_by_liveness", lambda r, f, s: (list(f), [])
    )
    ob = _text_ob(
        investigate_deletion_safety(
            repo, "zap", include_runtime=False, storage_path=storage
        )
    )
    assert ob["status"] == SATISFIED, ob


def test_a_live_use_in_a_cut_short_sweep_still_refutes(tmp_path, monkeypatch):
    from jcodemunch_mcp.investigator import deletion_safety as ds

    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    _mock_sweep(monkeypatch, "live.py", truncated=True)
    monkeypatch.setattr(
        ds, "_split_importers_by_liveness", lambda r, f, s: ([], list(f))
    )
    ob = _text_ob(
        investigate_deletion_safety(
            repo, "zap", include_runtime=False, storage_path=storage
        )
    )
    assert ob["status"] == REFUTED, ob


@pytest.mark.parametrize(
    "res",
    [
        {"truncated": True},
        {"timed_out": True},
        {"_meta": {"verdict": {"incomplete": {"reason": "unreadable_inputs"}}}},
        {"_meta": {"verdict": {"coverage": {"complete": False}}}},
        {"_meta": {"index_truncated": {"truncated": True}}},
        {"_meta": {"index_withheld": {"files": 1}}},
    ],
    ids=["truncated", "timed_out", "unreadable", "coverage", "index_cap", "withheld"],
)
def test_the_grep_answer_hook_refuses_every_cut(res):
    """The hook answers in Grep's place, so a cut answer must not be served.

    Its own list lacked `index_withheld` (L-309 review); it reads the shared
    predicate now. Asserted through `_complete` alone, so the arms run on a
    tree without the predicate too.
    """
    from jcodemunch_mcp.cli.hooks.grep_answer import _complete

    base = {"result_count": 3, "truncated": False, "timed_out": False, "results": []}
    assert _complete(dict(base))
    assert not _complete({**base, **res})


@pytest.mark.parametrize(
    "res",
    [
        {"truncated": True},
        {"timed_out": True},
        {"_meta": {"verdict": {"incomplete": {"reason": "unreadable_inputs"}}}},
        {"_meta": {"verdict": {"coverage": {"complete": False}}}},
        {"_meta": {"index_truncated": {"truncated": True}}},
        {"_meta": {"index_withheld": {"files": 1}}},
    ],
    ids=["truncated", "timed_out", "unreadable", "coverage", "index_cap", "withheld"],
)
def test_the_shared_predicate_names_every_cut(res):
    from jcodemunch_mcp.retrieval.verdict import search_text_cut_short

    base = {"result_count": 3, "truncated": False, "timed_out": False, "results": []}
    assert search_text_cut_short(dict(base)) == []
    assert search_text_cut_short({**base, **res})


def test_unknown_coverage_is_not_a_cut():
    from jcodemunch_mcp.retrieval.verdict import search_text_cut_short

    res = {"result_count": 3, "_meta": {"verdict": {"coverage": {"complete": None}}}}
    assert search_text_cut_short(res) == []


def _withheld_repo(tmp_path, monkeypatch):
    from jcodemunch_mcp import config as config_module

    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_file_size", 2000)
    return _index(
        tmp_path,
        {
            "src/aaa.py": "def zap():\n    return 1\n",
            "big/big.py": "zap = 1\n" + "# pad xxxxxxxxxxxxxxxxxxxx\n" * 200,
        },
    )


@pytest.mark.parametrize(
    "pattern,in_scope",
    [(None, True), ("src/*.py", False), ("big/*", True), ("*.py", True)],
    ids=["unscoped", "out_of_scope", "in_scope", "glob_covers_it"],
)
def test_a_withheld_file_is_a_cut_only_where_the_scope_reaches_it(
    tmp_path, monkeypatch, pattern, in_scope
):
    """Review round 2: the withheld count is repo-wide. A scoped search that
    no withheld file can match is not cut by it, so the Grep-answer hook still
    answers it."""
    import jcodemunch_mcp.tools.search_text as st
    from jcodemunch_mcp.cli.hooks.grep_answer import _complete
    from jcodemunch_mcp.retrieval.verdict import search_text_cut_short

    repo, storage = _withheld_repo(tmp_path, monkeypatch)
    res = st.search_text(
        repo,
        "zap",
        file_pattern=pattern,
        storage_path=storage,
        scan_limit=st.SCAN_LIMIT_MAX,
    )
    block = res["_meta"]["index_withheld"]
    assert block["in_scope"] is in_scope, block
    cut = any("max_file_size" in r for r in search_text_cut_short(res))
    assert cut is in_scope
    if pattern == "src/*.py":
        assert res["result_count"] and _complete(res)


def test_a_cut_list_of_withheld_names_leaves_the_scope_unknown(tmp_path, monkeypatch):
    import jcodemunch_mcp.tools.search_text as st
    from jcodemunch_mcp.retrieval.verdict import search_text_cut_short
    from jcodemunch_mcp.storage import IndexStore

    repo, storage = _withheld_repo(tmp_path, monkeypatch)
    real_load = IndexStore.load_index

    def load(self, *a, **k):
        idx = real_load(self, *a, **k)
        if idx is not None and isinstance(getattr(idx, "coverage", None), dict):
            idx.coverage = {**idx.coverage, "withheld_files_truncated": True}
        return idx

    monkeypatch.setattr(IndexStore, "load_index", load)
    res = st.search_text(repo, "zap", file_pattern="src/*.py", storage_path=storage)
    assert res["_meta"]["index_withheld"]["in_scope"] is None
    assert any("max_file_size" in r for r in search_text_cut_short(res))


def test_an_error_answer_is_a_cut():
    from jcodemunch_mcp.retrieval.verdict import search_text_cut_short

    assert search_text_cut_short({"error": "no index"})
