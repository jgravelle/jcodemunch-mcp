"""Signal-chain traversal cost and correctness.

The motivating repository-wide ``get_signal_chains`` run was too slow on a
large index: Phase 2 runs one BFS per gateway, and every gateway that reaches a shared hub re-resolved that hub's
direct callees — one SQLite file read plus body scan per (gateway, node)
pair. The traversal now shares one ``_ContentCache`` and one per-symbol
callee memo across all gateways, so resolution work is bounded by the
distinct symbols visited, not by gateways x nodes.

These tests pin the three guarantees that matter:

1. Small-graph chain results are unchanged (gateway -> callees -> depth).
2. Cyclic graphs terminate with each cycle member appearing once.
3. A hub reached from two gateways is resolved exactly once, and both
   chains still carry it — the memo removes repeated work, not results.
"""

from collections import Counter

from jcodemunch_mcp.tools import get_signal_chains as gs
from jcodemunch_mcp.tools.get_signal_chains import get_signal_chains
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _index(src, store):
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


def _repo(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    for name, text in files.items():
        (src / name).write_text(text)
    return _index(src, store)


def _chains_by_gateway_name(result):
    return {c["gateway_name"]: c for c in result["chains"]}


# ---------------------------------------------------------------------------
# 1. Small-graph results preserved
# ---------------------------------------------------------------------------


def test_small_graph_chain_contents_preserved(tmp_path):
    repo, store_path = _repo(
        tmp_path,
        {
            "main.py": (
                "from service import handle\n"
                "\n"
                "def run():\n"
                "    handle()\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    run()\n"
            ),
            "service.py": ("from db import save\n\ndef handle():\n    save()\n"),
            "db.py": "def save():\n    return 1\n",
        },
    )

    result = get_signal_chains(repo, storage_path=store_path, include_flow_edges=False)

    assert result["gateway_count"] == 1
    chain = _chains_by_gateway_name(result)["run"]
    # Gateway + direct callee + transitive callee, in BFS order.
    assert chain["symbols"] == ["run", "handle", "save"]
    assert chain["reach"] == 3
    assert chain["depth"] == 2
    assert result["orphan_symbols"] == 0
    # The new sharing counter is reported truthfully: one resolution per
    # distinct symbol expanded (run, handle, save).
    assert result["_meta"]["callee_resolutions"] == 3
    from jcodemunch_mcp.encoding.schemas.get_signal_chains import encode, decode

    result["_meta"]["flow_edges"] = {
        "route_gateways": 2,
        "unresolved_routes": 1,
        "render_views": 3,
    }
    payload, _ = encode("get_signal_chains", result)
    decoded = decode(payload)
    assert decoded["_meta"]["callee_resolutions"] == 3
    assert decoded["_meta"]["flow_edges"] == result["_meta"]["flow_edges"]


def test_gateway_without_callees_reports_reach_one(tmp_path):
    # The tool refuses indexes with no import data at all, so the repo needs
    # one import; the gateway itself still calls nothing.
    repo, store_path = _repo(
        tmp_path,
        {
            "main.py": (
                "from util import unused_helper\n"
                "\n"
                "def run():\n"
                "    return 1\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    run()\n"
            ),
            "util.py": "def unused_helper():\n    return 2\n",
        },
    )

    result = get_signal_chains(repo, storage_path=store_path, include_flow_edges=False)

    chain = _chains_by_gateway_name(result)["run"]
    assert chain["symbols"] == ["run"]
    assert chain["reach"] == 1
    assert chain["depth"] == 0


# ---------------------------------------------------------------------------
# 2. Cyclic graphs terminate and dedupe
# ---------------------------------------------------------------------------


def test_cyclic_graph_terminates_with_each_member_once(tmp_path):
    repo, store_path = _repo(
        tmp_path,
        {
            "main.py": ("from cycle import step_a\n\ndef run():\n    step_a()\n"),
            "cycle.py": (
                "def step_a():\n"
                "    step_b()\n"
                "\n"
                "def step_b():\n"
                "    step_c()\n"
                "\n"
                "def step_c():\n"
                "    step_a()\n"
            ),
        },
    )

    # max_depth=8 (the tool caps at 8): an unbounded traversal would loop
    # forever on step_a -> step_b -> step_c -> step_a; the visited set must
    # stop it with each member appearing exactly once.
    result = get_signal_chains(
        repo,
        storage_path=store_path,
        max_depth=8,
        include_flow_edges=False,
    )

    chain = _chains_by_gateway_name(result)["run"]
    assert chain["symbols"] == ["run", "step_a", "step_b", "step_c"]
    assert chain["reach"] == 4
    assert chain["depth"] == 3


# ---------------------------------------------------------------------------
# 3. Shared hub resolved once across gateways, results intact
# ---------------------------------------------------------------------------

_HUB_REPO = {
    "main.py": ("from hub import shared\n\ndef entry_one():\n    shared()\n"),
    "app.py": ("from hub import shared\n\ndef entry_two():\n    shared()\n"),
    "hub.py": ("def shared():\n    leaf()\n\ndef leaf():\n    return 1\n"),
}


def test_shared_hub_resolved_once_and_present_in_both_chains(tmp_path, monkeypatch):
    repo, store_path = _repo(tmp_path, dict(_HUB_REPO))

    real = gs.find_direct_callees
    resolved_ids: list[str] = []

    def spy(
        index,
        store,
        owner,
        repo_name,
        sym,
        symbols_by_file,
        content_cache=None,
        callee_index=None,
    ):
        resolved_ids.append(sym.get("id", ""))
        return real(
            index,
            store,
            owner,
            repo_name,
            sym,
            symbols_by_file,
            content_cache,
            callee_index,
        )

    monkeypatch.setattr(gs, "find_direct_callees", spy)

    result = get_signal_chains(repo, storage_path=store_path, include_flow_edges=False)

    by_name = _chains_by_gateway_name(result)
    assert set(by_name) == {"entry_one", "entry_two"}
    for name in ("entry_one", "entry_two"):
        # The memo must remove repeated RESOLUTION, never chain results:
        # both gateways still carry the hub and its leaf.
        assert by_name[name]["symbols"] == [name, "shared", "leaf"]
        assert by_name[name]["reach"] == 3

    counts = Counter(resolved_ids)
    # Four distinct symbols expanded, each resolved exactly once — the old
    # behavior resolved `shared` and `leaf` once per gateway.
    assert len(counts) == 4
    assert set(counts.values()) == {1}
    assert result["_meta"]["callee_resolutions"] == 4


def test_lookup_mode_reports_callee_resolutions(tmp_path):
    repo, store_path = _repo(tmp_path, dict(_HUB_REPO))

    result = get_signal_chains(
        repo,
        symbol="leaf",
        storage_path=store_path,
        include_flow_edges=False,
    )

    assert result["chain_count"] == 2
    assert result["_meta"]["callee_resolutions"] == 4
