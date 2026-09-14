"""Tests for get_tectonic_map — tectonic analysis (module topology discovery)."""

import pytest
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.get_tectonic_map import (
    get_tectonic_map,
    _structural_edges,
    _behavioral_edges,
    _fuse_signals,
    _partition,
    _majority_directory,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _build_two_cluster_repo(tmp_path):
    """Two clear clusters: auth (a1,a2,a3) and api (b1,b2,b3), with one cross-link.

    auth cluster: a1 → a2 → a3 (tight internal imports)
    api  cluster: b1 → b2 → b3 (tight internal imports)
    cross-link:   b1 → a1      (single bridge)
    """
    src = tmp_path / "auth"
    api = tmp_path / "api"
    store = tmp_path / "store"
    src.mkdir(); api.mkdir(); store.mkdir()

    (src / "a1.py").write_text("from auth.a2 import helper\n\ndef login():\n    return helper()\n")
    (src / "a2.py").write_text("from auth.a3 import validate\n\ndef helper():\n    return validate()\n")
    (src / "a3.py").write_text("def validate():\n    return True\n")

    (api / "b1.py").write_text("from api.b2 import fetch\nfrom auth.a1 import login\n\ndef handle():\n    login(); return fetch()\n")
    (api / "b2.py").write_text("from api.b3 import store_data\n\ndef fetch():\n    return store_data()\n")
    (api / "b3.py").write_text("def store_data():\n    return True\n")

    result = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


def _build_flat_repo(tmp_path):
    """Simple linear chain: a → b → c."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir(); store.mkdir()

    (src / "c.py").write_text("def leaf():\n    pass\n")
    (src / "b.py").write_text("from c import leaf\n\ndef mid():\n    return leaf()\n")
    (src / "a.py").write_text("from b import mid\n\ndef top():\n    return mid()\n")

    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


def _build_isolated_repo(tmp_path):
    """Three files with no imports (all isolated)."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir(); store.mkdir()

    (src / "x.py").write_text("def x():\n    pass\n")
    (src / "y.py").write_text("def y():\n    pass\n")
    (src / "z.py").write_text("def z():\n    pass\n")

    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


# ---------------------------------------------------------------------------
# Unit tests — community detection (Louvain since #668; the class name predates it)
# ---------------------------------------------------------------------------

class TestLabelPropagation:

    def test_two_disconnected_cliques(self):
        """Two cliques with no edges between them → two communities."""
        nodes = ["a", "b", "c", "d", "e", "f"]
        edges = {
            ("a", "b"): 1.0, ("a", "c"): 1.0, ("b", "c"): 1.0,  # clique 1
            ("d", "e"): 1.0, ("d", "f"): 1.0, ("e", "f"): 1.0,  # clique 2
        }
        labels = _partition(nodes, edges)
        # a,b,c should share a label; d,e,f should share a different label
        assert labels["a"] == labels["b"] == labels["c"]
        assert labels["d"] == labels["e"] == labels["f"]
        assert labels["a"] != labels["d"]

    def test_single_clique(self):
        """Fully connected → one community."""
        nodes = ["a", "b", "c"]
        edges = {("a", "b"): 1.0, ("a", "c"): 1.0, ("b", "c"): 1.0}
        labels = _partition(nodes, edges)
        assert labels["a"] == labels["b"] == labels["c"]

    def test_weak_bridge(self):
        """Two cliques with a weak bridge → still two communities."""
        nodes = ["a", "b", "c", "d", "e", "f"]
        edges = {
            ("a", "b"): 1.0, ("a", "c"): 1.0, ("b", "c"): 1.0,
            ("d", "e"): 1.0, ("d", "f"): 1.0, ("e", "f"): 1.0,
            ("c", "d"): 0.05,  # weak bridge
        }
        labels = _partition(nodes, edges)
        assert labels["a"] == labels["b"] == labels["c"]
        assert labels["d"] == labels["e"] == labels["f"]

    def test_empty_graph(self):
        """No edges → each node its own community."""
        nodes = ["a", "b"]
        labels = _partition(nodes, {})
        assert labels["a"] != labels["b"]


# ---------------------------------------------------------------------------
# Unit tests — helpers
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_majority_directory_nested(self):
        files = ["src/auth/a.py", "src/auth/b.py", "src/api/c.py"]
        assert _majority_directory(files) == "src/auth"

    def test_majority_directory_flat(self):
        files = ["lib/a.py", "lib/b.py", "test/c.py"]
        assert _majority_directory(files) == "lib"

    def test_structural_edges_normalised(self):
        fwd = {"a": ["b", "c"], "b": ["c"]}
        edges = _structural_edges(fwd)
        # All values should be in [0, 1]
        for v in edges.values():
            assert 0 <= v <= 1.0

    def test_fuse_signals_prunes_negligible(self):
        s = {("a", "b"): 0.01}
        b = {("a", "b"): 0.01}
        t = {("a", "b"): 0.01}
        fused = _fuse_signals(s, b, t)
        # 0.01*0.4 + 0.01*0.3 + 0.01*0.3 = 0.01 → pruned (≤ 0.01)
        assert ("a", "b") not in fused

    def test_fuse_signals_keeps_strong_edges(self):
        s = {("a", "b"): 1.0}
        b = {("a", "b"): 1.0}
        t = {}
        fused = _fuse_signals(s, b, t)
        assert ("a", "b") in fused
        assert fused[("a", "b")] == pytest.approx(0.7, abs=0.01)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestGetTectonicMap:

    def test_basic_output_shape(self, tmp_path):
        repo, store = _build_flat_repo(tmp_path)
        result = get_tectonic_map(repo, storage_path=store)
        assert "error" not in result
        assert "plates" in result
        assert "isolated_files" in result
        assert "signals_used" in result
        assert "drifter_summary" in result
        assert "_meta" in result
        assert result["_meta"]["methodology"] == "tectonic_louvain"

    def test_isolated_repo_returns_all_isolated(self, tmp_path):
        repo, store = _build_isolated_repo(tmp_path)
        result = get_tectonic_map(repo, storage_path=store)
        # No imports → error about insufficient data
        if "error" in result:
            assert "import" in result["error"].lower() or "insufficient" in result["error"].lower()
        else:
            # All files should be isolated
            assert result["plate_count"] == 0
            assert len(result["isolated_files"]) > 0

    def test_structural_signal_present(self, tmp_path):
        repo, store = _build_flat_repo(tmp_path)
        result = get_tectonic_map(repo, storage_path=store)
        assert "error" not in result
        assert "structural" in result["signals_used"]

    def test_plates_have_anchors(self, tmp_path):
        repo, store = _build_flat_repo(tmp_path)
        result = get_tectonic_map(repo, storage_path=store)
        assert "error" not in result
        for plate in result["plates"]:
            assert "anchor" in plate
            assert plate["anchor"] in plate["files"]

    def test_cohesion_in_range(self, tmp_path):
        repo, store = _build_flat_repo(tmp_path)
        result = get_tectonic_map(repo, storage_path=store)
        assert "error" not in result
        for plate in result["plates"]:
            assert 0 <= plate["cohesion"] <= 1.0

    def test_meta_has_timing(self, tmp_path):
        repo, store = _build_flat_repo(tmp_path)
        result = get_tectonic_map(repo, storage_path=store)
        assert "error" not in result
        assert result["_meta"]["timing_ms"] >= 0

    def test_not_indexed_error(self):
        result = get_tectonic_map("nonexistent/repo")
        assert "error" in result

    def test_min_plate_size_filters(self, tmp_path):
        repo, store = _build_flat_repo(tmp_path)
        # With min_plate_size=100, everything goes to isolated
        result = get_tectonic_map(repo, min_plate_size=100, storage_path=store)
        assert "error" not in result
        assert result["plate_count"] == 0
        assert len(result["isolated_files"]) > 0
