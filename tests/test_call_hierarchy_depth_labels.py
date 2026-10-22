"""A caller list says which callers are direct and how an indirect one gets there.

``get_call_hierarchy`` defaults to depth 3 and returned every depth in one
``callers`` list under one ``caller_count``. On one function in this repository
that was 735 rows, 12 of them direct, and an indirect row carried only a depth
number, so a reader taking "callers" at its word listed indirect callers as
direct ones. Now:

- every row deeper than 1 names the row it is reached through (``via``), and
  that row is one level shallower;
- ``direct_caller_count`` / ``direct_callee_count`` count depth 1 only, and
  ``callers_by_depth`` / ``callees_by_depth`` count each depth;
- rows are ordered by depth, including the direct callers compile-time
  evidence adds after the walk;
- the compact encoding carries all of it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools import get_call_hierarchy as gch_mod
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder


@pytest.fixture
def chain(tmp_path):
    """helper <- process <- handle <- run, plus a second direct caller of helper."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    (src / "services.py").write_text(
        "from utils import helper\n\ndef process():\n    return helper() + 1\n\n"
        "def audit():\n    return helper()\n",
        encoding="utf-8",
    )
    (src / "controllers.py").write_text(
        "from services import process\n\ndef handle(req):\n    return process()\n",
        encoding="utf-8",
    )
    (src / "main.py").write_text(
        "from controllers import handle\n\ndef run():\n    return handle(None)\n",
        encoding="utf-8",
    )
    store = tmp_path / "store"
    out = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert out["success"] is True
    return out["repo"], str(store)


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_an_indirect_caller_names_the_caller_it_goes_through(chain):
    rid, store = chain
    res = get_call_hierarchy(
        rid, "helper", direction="callers", depth=3, storage_path=store
    )
    rows = _by_name(res["callers"])
    assert rows["process"]["depth"] == 1 and rows["audit"]["depth"] == 1
    assert (
        rows["handle"]["depth"] == 2 and rows["handle"]["via"] == rows["process"]["id"]
    )
    assert rows["run"]["depth"] == 3 and rows["run"]["via"] == rows["handle"]["id"]
    assert "via" not in rows["process"] and "via" not in rows["audit"]


def test_every_via_points_one_level_shallower(chain):
    rid, store = chain
    res = get_call_hierarchy(
        rid, "helper", direction="callers", depth=3, storage_path=store
    )
    by_id = {r["id"]: r for r in res["callers"]}
    deep = [r for r in res["callers"] if r["depth"] > 1]
    assert deep  # non-vacuity
    for r in deep:
        assert by_id[r["via"]]["depth"] == r["depth"] - 1, r


def test_direct_and_per_depth_counts(chain):
    rid, store = chain
    res = get_call_hierarchy(
        rid, "helper", direction="callers", depth=3, storage_path=store
    )
    assert res["caller_count"] == 4  # every depth, as before
    assert res["direct_caller_count"] == 2
    assert res["callers_by_depth"] == {"1": 2, "2": 1, "3": 1}
    assert sum(res["callers_by_depth"].values()) == res["caller_count"]


def test_callees_carry_the_same_labels(chain):
    rid, store = chain
    res = get_call_hierarchy(
        rid, "run", direction="callees", depth=3, storage_path=store
    )
    rows = _by_name(res["callees"])
    assert rows["handle"]["depth"] == 1
    assert (
        rows["process"]["depth"] == 2 and rows["process"]["via"] == rows["handle"]["id"]
    )
    assert res["direct_callee_count"] == 1
    assert sum(res["callees_by_depth"].values()) == res["callee_count"]


def test_rows_stay_in_depth_order_after_compile_time_evidence_adds_direct_callers(
    chain, monkeypatch
):
    """Compile-time evidence appends its direct callers after the walk."""
    real = gch_mod._attach_scip_to_hierarchy

    def append_a_direct_caller(response, store, owner, name):
        response = real(response, store, owner, name)
        response["callers"].append(
            {
                "id": "extra.py::late#function",
                "name": "late",
                "kind": "function",
                "file": "extra.py",
                "line": 1,
                "depth": 1,
                "resolution": "scip_reference",
            }
        )
        response["caller_count"] = len(response["callers"])
        return response

    monkeypatch.setattr(gch_mod, "_attach_scip_to_hierarchy", append_a_direct_caller)
    rid, store = chain
    res = get_call_hierarchy(
        rid, "helper", direction="callers", depth=3, storage_path=store
    )
    depths = [r["depth"] for r in res["callers"]]
    assert depths == sorted(depths), depths
    assert res["direct_caller_count"] == 3
    assert res["callers_by_depth"]["1"] == 3


def test_the_compact_encoding_carries_via_and_the_counts(chain):
    from jcodemunch_mcp.encoding import decoder
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as enc

    rid, store = chain
    res = get_call_hierarchy(
        rid, "helper", direction="both", depth=3, storage_path=store
    )
    payload, enc_id = enc.encode("get_call_hierarchy", res)
    assert enc_id == enc.ENCODING_ID
    back = decoder.decode(payload)
    assert back["direct_caller_count"] == 2
    assert back["callers_by_depth"] == {"1": 2, "2": 1, "3": 1}
    rows = _by_name(back["callers"])
    assert rows["run"]["via"] == rows["handle"]["id"]


def test_the_old_compact_encoding_id_stays_readable():
    """Encode with the ch1 tables (no `via`, no new counts) and decode it."""
    from jcodemunch_mcp.encoding import decoder
    from jcodemunch_mcp.encoding import schema_driven as sd
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as enc

    assert enc.ENCODING_ID != "ch1"
    assert "ch1" in enc.LEGACY_ENCODING_IDS
    cols = ["id", "name", "kind", "file", "line", "depth", "resolution"]
    tables = [
        sd.TableSpec(
            key="callers",
            tag="c",
            cols=cols,
            intern=["file", "id"],
            types={"line": "int", "depth": "int"},
        ),
        sd.TableSpec(
            key="callees",
            tag="e",
            cols=cols,
            intern=["file", "id"],
            types={"line": "int", "depth": "int"},
        ),
    ]
    old = {
        "repo": "o/r",
        "direction": "callers",
        "depth": 2,
        "depth_reached": 2,
        "caller_count": 1,
        "callee_count": 0,
        "callers": [
            {
                "id": "a.py::f#function",
                "name": "f",
                "kind": "function",
                "file": "a.py",
                "line": 3,
                "depth": 1,
                "resolution": "ast_resolved",
            }
        ],
        "callees": [],
    }
    payload, enc_id = sd.encode(
        "get_call_hierarchy",
        old,
        "ch1",
        tables,
        ("repo", "direction", "depth", "depth_reached", "caller_count", "callee_count"),
    )
    assert enc_id == "ch1"
    back = decoder.decode(payload)
    assert (
        back["callers"][0]["id"] == "a.py::f#function"
        and back["callers"][0]["depth"] == 1
    )


class _FakeScip:
    """An in-memory SCIP store whose only reference edge says `handle` calls `helper`."""

    def __init__(self, caller_id, target_id):
        import sqlite3

        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE symbols (id TEXT, name TEXT, kind TEXT, file TEXT, line INT)"
        )
        self.conn.execute(
            "CREATE TABLE scip_edges (kind TEXT, from_symbol_id TEXT, to_symbol_id TEXT, count INT)"
        )
        name = caller_id.split("::")[1].split("#")[0]
        self.conn.execute(
            "INSERT INTO symbols VALUES (?, ?, 'function', 'controllers.py', 3)",
            (caller_id, name),
        )
        self.conn.execute(
            "INSERT INTO scip_edges VALUES ('reference', ?, ?, 1)",
            (caller_id, target_id),
        )


def test_a_compiler_verified_caller_the_walk_reached_deeper_is_direct(
    chain, monkeypatch
):
    """The compiler says `handle` references `helper` itself; the walk reached it at depth 2."""
    rid, store = chain
    base = get_call_hierarchy(
        rid, "helper", direction="callers", depth=3, storage_path=store
    )
    rows = _by_name(base["callers"])
    assert rows["handle"]["depth"] == 2  # non-vacuity: the walk files it as indirect

    fake = _FakeScip(rows["handle"]["id"], base["symbol"]["id"])
    monkeypatch.setattr(gch_mod, "open_scip_reader", lambda *a, **k: fake.conn)
    monkeypatch.setattr(gch_mod, "scip_meta_and_stale", lambda conn: ({}, False))
    monkeypatch.setattr(gch_mod, "scip_meta_block", lambda *a, **k: {})
    res = get_call_hierarchy(
        rid, "helper", direction="callers", depth=3, storage_path=store
    )
    rows = _by_name(res["callers"])
    assert rows["handle"]["depth"] == 1 and "via" not in rows["handle"]
    assert rows["handle"].get("verification") == "compiler_verified"
    assert rows["run"]["depth"] == 2 and rows["run"]["via"] == rows["handle"]["id"]
    assert res["direct_caller_count"] == 3
    assert res["callers_by_depth"] == {"1": 3, "2": 1}
    depths = [r["depth"] for r in res["callers"]]
    assert depths == sorted(depths), depths


def test_a_row_with_no_depth_is_never_counted_as_direct():
    response = {
        "callers": [
            {"id": "a", "depth": 1},
            {"id": "b"},  # no depth: unknown
            {"id": "c", "depth": 2, "via": "a"},
        ],
        "callees": [],
    }
    gch_mod._label_depths(response)
    assert response["direct_caller_count"] == 1
    assert response["callers_by_depth"] == {"1": 1, "2": 1, "unknown": 1}
    assert [r["id"] for r in response["callers"]] == ["a", "c", "b"]
