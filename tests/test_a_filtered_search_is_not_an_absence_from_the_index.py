"""A filtered search describes the filter, never the whole index.

``search_symbols(query="index_folder", language="rust")`` answered "No symbol
named 'index_folder' is in this index" while the index held it in Python. With
no rows, the same call on a complete index minted a citable absence and the
warning "Do not claim this feature exists", and named the filter nowhere. A
filter narrows the scan; nothing found inside it says nothing about the rest.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.tools.search_symbols import search_symbols


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("def alpha_beta():\n    return 1\n", encoding="utf-8")
    (src / "b.js").write_text("function alphaGamma() { return 2; }\n", encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    return r["repo"], str(store)


async def _call(arguments):
    out = await server.call_tool("search_symbols", arguments)
    content = out.content if hasattr(out, "content") else out
    return json.loads(content[0].text)


def test_unfiltered_the_symbol_is_found(repo):
    """Non-vacuity: the name is in the index."""
    repo_id, store = repo
    r = search_symbols(repo=repo_id, query="alpha_beta", storage_path=store)
    assert r["results"][0]["name"] == "alpha_beta"


def test_an_empty_filtered_scan_is_not_a_citable_absence(repo):
    repo_id, store = repo
    r = search_symbols(
        repo=repo_id, query="alpha_beta", language="go", storage_path=store
    )
    assert r["results"] == []
    v = r["_meta"]["verdict"]
    assert v["state"] != "absent" and v.get("absence_refused") is True
    assert v["filters"] == {"language": "go"}
    assert "language=go" in v["note"] and "1 symbol(s) named 'alpha_beta'" in v["note"]
    warning = r["⚠ warning"]
    assert (
        "within language=go" in warning
        and "Do not claim this feature exists" not in warning
    )
    assert r["negative_evidence"]["exact_outside_filters"] == 1


async def test_through_the_dispatcher_no_evidence_ref_and_the_filter_is_the_reason(
    repo,
):
    repo_id, _ = repo
    body = await _call({"repo": repo_id, "query": "alpha_beta", "language": "go"})
    # The default install strips `_meta.verdict`; the carrier is re-attached.
    carrier = body["_meta"]["absence_evidence"]
    assert carrier["citable"] is False and "ref" not in carrier
    assert "narrowed by language=go" in carrier["blocked_by"]


def test_rows_from_inside_the_filter_never_claim_the_index_lacks_the_name(repo):
    repo_id, store = repo
    r = search_symbols(
        repo=repo_id, query="alpha_beta", language="javascript", storage_path=store
    )
    names = [x["name"] for x in r["results"]]
    assert "alpha_beta" not in names
    exact = r["_meta"]["exact_match"]
    assert "is in this index" not in exact["note"]
    assert "matches language=javascript" in exact["note"]
    assert exact["exact_outside_filters"] == 1
    assert "is in this index" not in r["_meta"]["verdict"].get("note", "")
    assert r["_meta"]["verdict"]["filters"] == {"language": "javascript"}


def test_a_name_absent_everywhere_is_still_reported_with_its_filter(repo):
    repo_id, store = repo
    r = search_symbols(
        repo=repo_id, query="zeta_omega", kind="class", storage_path=store
    )
    v = r["_meta"]["verdict"]
    assert v["filters"] == {"kind": "class"} and v["state"] != "absent"
    assert "outside this filter" not in v.get("note", "")


def test_a_name_inside_and_outside_the_filter_claims_no_outside_count(
    tmp_path, monkeypatch
):
    """The count is whole-index; published only when nothing inside matched."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "p2"
    src.mkdir()
    (src / "a.py").write_text("def alpha_beta():\n    return 1\n", encoding="utf-8")
    (src / "b.js").write_text("function alpha_beta() { return 2; }\n", encoding="utf-8")
    store = tmp_path / "s2"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    out = search_symbols(
        repo=r["repo"],
        query="alpha_beta",
        language="python",
        token_budget=1,
        storage_path=str(store),
    )
    assert "exact_outside_filters" not in (out["_meta"].get("exact_match") or {})
    assert "exact_outside_filters" not in (out.get("negative_evidence") or {})
    assert "outside this filter" not in out["_meta"]["verdict"].get("note", "")


@pytest.mark.parametrize("language", ["go", "javascript"])
def test_the_fusion_exits_name_their_filters(repo, language):
    repo_id, store = repo
    r = search_symbols(
        repo=repo_id,
        query="alpha_beta",
        language=language,
        fusion=True,
        storage_path=store,
    )
    v = r["_meta"]["verdict"]
    assert v["filters"] == {"language": language}
    assert v["state"] != "absent"


def test_a_path_scope_alone_stays_a_citable_scoped_absence(repo):
    """As in search_text: a file_pattern is a path scope the evidence carries."""
    repo_id, store = repo
    r = search_symbols(
        repo=repo_id, query="zeta_omega", file_pattern="*.py", storage_path=store
    )
    v = r["_meta"]["verdict"]
    assert v["filters"] == {"file_pattern": "*.py"}
    assert v["state"] == "absent" and not v.get("absence_refused")
    assert "within file_pattern=*.py" in r["⚠ warning"]


async def test_search_text_with_language_reports_it_ignored_and_refuses_absence(repo):
    repo_id, _ = repo
    out = await server.call_tool(
        "search_text", {"repo": repo_id, "query": "zeta_omega", "language": "go"}
    )
    content = out.content if hasattr(out, "content") else out
    body = json.loads(content[0].text)
    assert body["_meta"]["ignored_arguments"] == ["language"]
    assert body["_meta"]["absence_evidence"]["citable"] is False


def test_a_path_scoped_absence_names_its_scope_not_the_index(repo):
    """The name exists outside the path scope: the citable note must say so."""
    repo_id, store = repo
    r = search_symbols(
        repo=repo_id, query="alpha_beta", file_pattern="*.go", storage_path=store
    )
    v = r["_meta"]["verdict"]
    assert v["state"] == "absent" and not v.get("absence_refused")
    assert "after scanning the index" not in v["note"]
    assert "file_pattern=*.go" in v["note"]
    assert "1 symbol(s) named 'alpha_beta'" in v["note"]


def test_one_rule_decides_which_filters_refuse_absence():
    from jcodemunch_mcp.retrieval.verdict import filters_refuse_absence

    assert filters_refuse_absence({"language": "go"})
    assert filters_refuse_absence({"file_pattern": "*.py", "kind": "function"})
    assert not filters_refuse_absence({"file_pattern": "*.py"})
    assert not filters_refuse_absence({})
