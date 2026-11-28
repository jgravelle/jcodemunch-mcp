"""`get_call_hierarchy` caps its caller and callee rows and says so (2026-11-27).

`callers` and `callees` listed every row at every depth, with no bound: on
this repository's own index `resolve_repo` at depth 2 came to 3,053 callers.

The property:
- the MCP tool lists at most `max_results` rows in each of `callers` and
  `callees` (default 100; 0 lists all);
- `caller_count` and `callee_count` stay the whole counts, as do the
  per-depth counts, and `callers_truncated` / `callees_truncated` say
  whether rows were left out; both are always present;
- the cut keeps the nearest rows: the lists are ordered by depth, then file,
  line and id, so a kept row's `via` row is kept too, and the same rows are
  kept whatever order the store returns its files in;
- the Python function lists all by default, and no caller in `src/` other
  than the dispatcher passes a cap;
- the compact encoding carries both keys.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder

REPO = Path(__file__).resolve().parents[1]
N = 30


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("call_rows")
    src = tmp / "proj"
    src.mkdir()
    (src / "core.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    for i in range(N):
        (src / f"user{i:02d}.py").write_text(
            f"from core import target\n\n\ndef caller_{i:02d}():\n    return target()\n",
            encoding="utf-8",
        )
    # one second-hop caller, so depth 2 has a row whose `via` is a depth-1 row
    (src / "outer.py").write_text(
        "from user00 import caller_00\n\n\ndef outer():\n    return caller_00()\n",
        encoding="utf-8",
    )
    (src / "lib.py").write_text(
        "".join(f"def f{i:02d}():\n    return {i}\n\n\n" for i in range(N)), encoding="utf-8"
    )
    names = ", ".join(f"f{i:02d}" for i in range(N))
    calls = "\n".join(f"    f{i:02d}()" for i in range(N))
    (src / "hub.py").write_text(f"from lib import {names}\n\n\ndef hub():\n{calls}\n", encoding="utf-8")
    store = tmp / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    return r["repo"], str(store)


def _call(repo, symbol, **kw):
    repo_id, store = repo
    out = get_call_hierarchy(repo_id, symbol, storage_path=store, **kw)
    assert "error" not in out, out
    return out


def test_the_cap_cuts_callers_and_keeps_the_count(repo):
    out = _call(repo, "target", direction="callers", max_results=10)
    assert len(out["callers"]) == 10
    assert out["caller_count"] == N
    assert out["callers_truncated"] is True
    assert out["direct_caller_count"] == N
    assert out["callers_by_depth"] == {"1": N}


def test_the_cap_cuts_callees_and_keeps_the_count(repo):
    out = _call(repo, "hub", direction="callees", max_results=10)
    assert len(out["callees"]) == 10
    assert out["callee_count"] == N
    assert out["callees_truncated"] is True


def test_a_list_under_the_cap_reads_complete(repo):
    out = _call(repo, "target", max_results=100)
    assert len(out["callers"]) == out["caller_count"] == N
    assert out["callers_truncated"] is False
    assert out["callees_truncated"] is False


@pytest.mark.parametrize("cap", [0, -2])
def test_zero_or_less_lists_every_row(repo, cap):
    out = _call(repo, "target", direction="callers", max_results=cap)
    assert len(out["callers"]) == N and out["callers_truncated"] is False


def test_the_python_default_lists_every_row(repo):
    out = _call(repo, "target", direction="callers")
    assert len(out["callers"]) == N and out["callers_truncated"] is False


def test_the_cut_keeps_the_nearest_rows_and_their_via(repo):
    whole = _call(repo, "target", direction="callers", depth=2)
    assert any(r.get("depth") == 2 for r in whole["callers"]), whole["callers"]
    page = _call(repo, "target", direction="callers", depth=2, max_results=N)
    assert all(r.get("depth") == 1 for r in page["callers"])
    assert page["caller_count"] == whole["caller_count"] and page["callers_truncated"] is True
    full = _call(repo, "target", direction="callers", depth=2, max_results=N + 1)
    kept = {r["id"] for r in full["callers"]}
    for r in full["callers"]:
        if r.get("depth", 1) > 1:
            assert r.get("via") in kept, r


def test_the_cut_keeps_the_same_rows_whatever_the_store_order(repo, monkeypatch):
    """The store's row order is not path order; reverse it so a cut that kept
    the walk's order keeps different rows."""
    before = [r["id"] for r in _call(repo, "target", direction="callers", max_results=7)["callers"]]

    from jcodemunch_mcp.storage import IndexStore

    real = IndexStore.load_index

    def reversed_rows(self, *a, **kw):
        idx = real(self, *a, **kw)
        if idx is not None and idx.imports:
            idx.imports = dict(reversed(list(idx.imports.items())))
        return idx

    monkeypatch.setattr(IndexStore, "load_index", reversed_rows)
    after = [r["id"] for r in _call(repo, "target", direction="callers", max_results=7)["callers"]]
    assert after == before
    whole = [r["id"] for r in _call(repo, "target", direction="callers")["callers"]]
    assert before == whole[:7]


def _server_source() -> str:
    return (REPO / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8")


def test_the_mcp_tool_defaults_to_one_hundred_and_publishes_it():
    from jcodemunch_mcp import server

    tool = next(t for t in server._build_tools_list(surface_override="full") if t.name == "get_call_hierarchy")
    assert tool.inputSchema["properties"]["max_results"]["default"] == 100
    assert "callers_truncated" in tool.description
    tree = ast.parse(_server_source())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and any(isinstance(a, ast.Name) and a.id == "get_call_hierarchy" for a in n.args)
    ]
    assert calls, "the dispatcher never calls get_call_hierarchy"
    for c in calls:
        kw = next((k for k in c.keywords if k.arg == "max_results"), None)
        assert kw is not None, "the dispatcher does not pass max_results"
        assert isinstance(kw.value, ast.Call) and kw.value.args[1].value == 100, ast.dump(kw.value)


def test_no_caller_in_src_but_the_dispatcher_passes_a_cap():
    stray = []
    for path in (REPO / "src").rglob("*.py"):
        if path.name == "server.py" and path.parent.name == "jcodemunch_mcp":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "get_call_hierarchy" and (
                len(n.args) > 5 or any(k.arg == "max_results" for k in n.keywords)
            ):
                stray.append((str(path.relative_to(REPO)), n.lineno))
    assert stray == [], stray


def test_the_compact_encoding_carries_the_cut(repo):
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as enc

    out = _call(repo, "target", max_results=5)
    payload, enc_id = enc.encode("get_call_hierarchy", out)
    assert enc_id == enc.ENCODING_ID
    back = enc.decode(payload)
    assert back["callers_truncated"] is True and back["callees_truncated"] is False
    assert "ch4" in enc.LEGACY_ENCODING_IDS and enc.ENCODING_ID != "ch4"


def test_the_cut_ranks_on_the_resolved_depth_not_the_stored_one():
    """A row's depth is one more than its `via` row's; a stale stored depth
    must not put a row ahead of the row it is reached through."""
    from jcodemunch_mcp.tools.get_call_hierarchy import _cap_rows

    rows = [
        {"id": "b", "file": "b.py", "line": 1, "depth": 2, "via": "p"},
        {"id": "p", "file": "p.py", "line": 1, "depth": 5},
        {"id": "c", "file": "c.py", "line": 1, "depth": 1},
    ]
    response = {"callers": rows, "callees": []}
    _cap_rows(response, 2)
    assert [r["id"] for r in response["callers"]] == ["c", "p"]
    assert response["callers_truncated"] is True and response["callees_truncated"] is False


def test_a_ch4_payload_decodes_without_the_cut_keys(repo):
    from jcodemunch_mcp.encoding import decoder
    from jcodemunch_mcp.encoding import schema_driven as sd
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as enc

    out = _call(repo, "target")
    del out["callers_truncated"], out["callees_truncated"]
    scalars = tuple(k for k in enc._SCALARS if k not in ("callers_truncated", "callees_truncated"))
    payload, _ = sd.encode(
        "get_call_hierarchy", enc._flatten(out), "ch4", enc._TABLES, scalars,
        nested_dicts=enc._NESTED, meta_keys=enc._META, meta_json_blobs=enc._META_JSON,
        json_blobs=enc._JSON,
    )
    assert " enc=ch4" in payload.partition(chr(10))[0]
    back = decoder.decode(payload)
    assert "callers_truncated" not in back and "callees_truncated" not in back
    assert [r["id"] for r in back["callers"]] == [r["id"] for r in out["callers"]]
