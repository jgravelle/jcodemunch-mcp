"""get_tectonic_map's partition does not collapse through a hub file (#668).

Label propagation over the fused file graph put 772 of 1,139 files of this
repository in one plate at cohesion 0.0024, and #667's temporal signal made it
worse, not better (813 of 1,142 at 0.0021). The mechanism is a hub: a file
every module imports (`server.py` here) links every module to every other, and
label propagation adopts the heaviest neighbouring label, so one label floods
the graph. The figures above are the issue's (plates out of `file_count`); the
seven-corpus measurement (largest plate share out of files in the fused graph,
modularity, main vs branch) and the hash-seed runs are in the PR evidence and
CHANGELOG.

The end-to-end arm is a non-regression check, not the reproduction: on small
fixture repos the fused weights normalise the hub down and label propagation
separated them too. The unit arm is the reproduction (label propagation: one
plate of 49).

What each test pins (for docs/harness/ARCHAEOLOGY.md):
- modules joined only through a hub stay separate plates and none holds a
  majority of the graph (the collapse, on the fused-graph shape);
- the partition is deterministic (the same input gives the same plates);
- end to end, an indexed repo whose modules all import one hub file reports
  more than one plate and no majority plate.
"""

from __future__ import annotations

import random
from collections import Counter

from jcodemunch_mcp.tools import get_tectonic_map as gtm
from jcodemunch_mcp.tools.index_folder import index_folder

MODULES = 6
SIZE = 8


def _hub_graph():
    """MODULES modules of SIZE files, each file linked to its next two in the
    module (sparse, as import graphs are), plus one hub linked to every file.
    Label propagation floods this whole graph with one label."""
    nodes = ["hub"]
    edges: dict[tuple[str, str], float] = {}
    for m in range(MODULES):
        members = [f"m{m}_{i}" for i in range(SIZE)]
        nodes += members
        for i, a in enumerate(members):
            for step in (1, 2):
                edges[tuple(sorted((a, members[(i + step) % SIZE])))] = 0.3
            edges[tuple(sorted(("hub", a)))] = 0.4
    return sorted(nodes), edges


def test_modules_joined_only_through_a_hub_stay_separate():
    nodes, edges = _hub_graph()
    labels = gtm._partition(nodes, edges)
    sizes = Counter(labels.values())
    assert max(sizes.values()) <= len(nodes) // 2, sizes
    for m in range(MODULES):
        members = {labels[f"m{m}_{i}"] for i in range(SIZE)}
        assert len(members) == 1, (m, members)
    assert len({labels[f"m{m}_0"] for m in range(MODULES)}) == MODULES, labels


def test_the_partition_is_deterministic():
    nodes, edges = _hub_graph()
    assert gtm._partition(nodes, edges) == gtm._partition(list(reversed(nodes)), dict(reversed(list(edges.items()))))
    # A uniform ring is all ties, so only a fixed visiting order and a strict
    # tie-break give one answer: input order follows string hashing upstream.
    ring = [f"r{i:02d}" for i in range(9)]
    ring_edges = {tuple(sorted((ring[i], ring[(i + 1) % 9]))): 1.0 for i in range(9)}
    rng = random.Random(0)
    seen = set()
    for _ in range(8):
        order = ring[:]
        rng.shuffle(order)
        items = list(ring_edges.items())
        rng.shuffle(items)
        seen.add(tuple(sorted(gtm._partition(order, dict(items)).items())))
    assert len(seen) == 1, seen


def test_a_repo_whose_modules_all_import_one_hub_is_not_one_plate(tmp_path):
    src = tmp_path / "proj"
    (src / "core").mkdir(parents=True)
    (src / "core" / "hub.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "core" / "__init__.py").write_text("", encoding="utf-8")
    for m in range(MODULES):
        pkg = src / f"mod{m}"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        for i in range(SIZE):
            near = [(i + 1) % SIZE, (i + 2) % SIZE]
            siblings = "".join(f"from mod{m}.f{j} import fn{m}_{j}\n" for j in near)
            body = (
                "from core.hub import shared\n"
                + siblings
                + f"\ndef fn{m}_{i}():\n    return shared()"
                + "".join(f" + fn{m}_{j}()" for j in near)
                + "\n"
            )
            (pkg / f"f{i}.py").write_text(body, encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    out = gtm.get_tectonic_map(res["repo"], storage_path=str(store))
    assert "error" not in out, out
    plated = sum(p["file_count"] for p in out["plates"])
    assert out["plate_count"] > 1, out["plates"]
    assert max(p["file_count"] for p in out["plates"]) <= plated // 2, [
        (p["anchor"], p["file_count"]) for p in out["plates"]
    ]
