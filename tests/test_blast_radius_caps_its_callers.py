"""`get_blast_radius` caps its `call_depth` callers list and says so (2026-11-27).

With `call_depth` above 0, `get_blast_radius` publishes the same breadth-first
callers list `get_call_hierarchy` does, and it had no bound (LEDGER L-353).

The property:
- the MCP tool lists at most `max_results` callers (default 100; 0 lists all);
- `caller_count`, `direct_caller_count` and `callers_by_depth` stay the whole
  list's, and `callers_truncated` says whether rows were left out; it is
  present whenever `callers` is;
- the cut is the one `get_call_hierarchy` uses (`_call_graph.cap_rows`), so
  the two tools keep the same rows for the same symbol and cap;
- the result cache holds the whole list, so a cut answer never serves a later
  call that asked for more;
- the Python function lists all by default, no caller in `src/` other than
  the dispatcher passes a cap, and the compact encoding carries the key.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder

REPO = Path(__file__).resolve().parents[1]
N = 30


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("blast_rows")
    src = tmp / "proj"
    src.mkdir()
    (src / "core.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    for i in range(N):
        (src / f"user{i:02d}.py").write_text(
            f"from core import target\n\n\ndef caller_{i:02d}():\n    return target()\n",
            encoding="utf-8",
        )
    store = tmp / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    return r["repo"], str(store)


def _blast(repo, **kw):
    repo_id, store = repo
    out = get_blast_radius(repo_id, "target", storage_path=store, **kw)
    assert "error" not in out, out
    return out


def test_the_cap_cuts_callers_and_keeps_the_counts(repo):
    out = _blast(repo, call_depth=1, max_results=10)
    assert len(out["callers"]) == 10
    assert out["caller_count"] == N
    assert out["direct_caller_count"] == N
    assert out["callers_by_depth"] == {"1": N}
    assert out["callers_truncated"] is True


def test_a_list_under_the_cap_reads_complete(repo):
    out = _blast(repo, call_depth=1, max_results=100)
    assert len(out["callers"]) == N and out["callers_truncated"] is False


@pytest.mark.parametrize("cap", [0, -4])
def test_zero_or_less_lists_every_caller(repo, cap):
    out = _blast(repo, call_depth=1, max_results=cap)
    assert len(out["callers"]) == N and out["callers_truncated"] is False


def test_the_python_default_lists_every_caller(repo):
    out = _blast(repo, call_depth=1)
    assert len(out["callers"]) == N and out["callers_truncated"] is False


def test_no_callers_list_no_flag(repo):
    out = _blast(repo)
    assert "callers" not in out and "callers_truncated" not in out


@pytest.mark.parametrize("order", [(5, 0), (0, 5), (5, 5)])
def test_the_cache_holds_the_whole_list(repo, order):
    from jcodemunch_mcp.storage import result_cache_invalidate

    result_cache_invalidate()
    for cap in order:
        out = _blast(repo, call_depth=1, max_results=cap)
        want = cap if 0 < cap < N else N
        assert len(out["callers"]) == want, (order, cap)
        assert out["caller_count"] == N


def test_both_tools_keep_the_same_rows(repo):
    repo_id, store = repo
    hier = get_call_hierarchy(repo_id, "target", direction="callers", max_results=7, storage_path=store)
    blast = _blast(repo, call_depth=1, max_results=7)
    assert [r["id"] for r in blast["callers"]] == [r["id"] for r in hier["callers"]]


def _server_source() -> str:
    return (REPO / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8")


def test_the_mcp_tool_defaults_to_one_hundred_and_publishes_it():
    from jcodemunch_mcp import server

    tool = next(t for t in server._build_tools_list(surface_override="full") if t.name == "get_blast_radius")
    prop = tool.inputSchema["properties"]["max_results"]
    assert prop["default"] == 100 and "callers_truncated" in prop["description"]
    tree = ast.parse(_server_source())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and any(isinstance(a, ast.Name) and a.id == "get_blast_radius" for a in n.args)
    ]
    assert calls, "the dispatcher never calls get_blast_radius"
    for c in calls:
        kw = next((k for k in c.keywords if k.arg == "max_results"), None)
        assert kw is not None, "the dispatcher does not pass max_results"
        assert isinstance(kw.value, ast.Call) and kw.value.args[1].value == 100, ast.dump(kw.value)


def _forwards_its_own_cap(path: Path, call: ast.Call) -> bool:
    """`get_endpoint_impact` passes its own `max_results` through (L-354)."""
    kw = next((k for k in call.keywords if k.arg == "max_results"), None)
    return (
        path.name == "get_endpoint_impact.py"
        and isinstance(getattr(kw, "value", None), ast.Name)
        and kw.value.id == "max_results"
    )


def test_no_caller_in_src_but_the_dispatcher_passes_a_cap():
    stray = []
    for path in (REPO / "src").rglob("*.py"):
        if path.name == "server.py" and path.parent.name == "jcodemunch_mcp":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name == "get_blast_radius" and (
                len(n.args) > 12 or any(k.arg == "max_results" for k in n.keywords)
            ) and not _forwards_its_own_cap(path, n):
                stray.append((str(path.relative_to(REPO)), n.lineno))
    assert stray == [], stray


def test_the_compact_encoding_carries_the_cut(repo):
    from jcodemunch_mcp.encoding import decoder
    from jcodemunch_mcp.encoding import schema_driven as sd
    from jcodemunch_mcp.encoding.schemas import get_blast_radius as enc

    out = _blast(repo, call_depth=1, max_results=5)
    payload, enc_id = enc.encode("get_blast_radius", out)
    assert enc_id == enc.ENCODING_ID
    assert decoder.decode(payload)["callers_truncated"] is True
    assert "br5" in enc.LEGACY_ENCODING_IDS and enc.ENCODING_ID != "br5"
    # a br5 payload, written without the key, still decodes without it
    old = dict(out)
    del old["callers_truncated"]
    scalars = tuple(k for k in enc._SCALARS if k != "callers_truncated")
    payload, _ = sd.encode(
        "get_blast_radius", old, "br5", enc._TABLES, scalars,
        nested_dicts=enc._NESTED, meta_keys=enc._META, meta_json_blobs=enc._META_JSON,
        json_blobs=enc._JSON,
    )
    back = decoder.decode(payload)
    assert "callers_truncated" not in back and len(back["callers"]) == 5
