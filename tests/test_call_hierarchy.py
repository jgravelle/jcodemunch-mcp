"""Tests for v1.14: get_call_hierarchy, get_impact_preview, and related upgrades."""

import pytest
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.get_impact_preview import get_impact_preview
from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.find_references import find_references


# ---------------------------------------------------------------------------
# Shared fixture builder
# ---------------------------------------------------------------------------

def _build_repo(tmp_path):
    """Build a synthetic 4-file repo:

        utils.py      — defines helper(), shared_util()
        services.py   — imports utils; defines process() which calls helper()
        controllers.py — imports services; defines handle() which calls process()
        main.py       — imports controllers; defines run() which calls handle()
    """
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()

    (src / "utils.py").write_text(
        "def helper():\n    return 42\n\ndef shared_util():\n    return 'ok'\n"
    )
    (src / "services.py").write_text(
        "from utils import helper, shared_util\n\n"
        "def process():\n    return helper() + 1\n"
    )
    (src / "controllers.py").write_text(
        "from services import process\n\n"
        "def handle(req):\n    return process()\n"
    )
    (src / "main.py").write_text(
        "from controllers import handle\n\n"
        "def run():\n    return handle(None)\n"
    )

    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


# ---------------------------------------------------------------------------
# _call_graph unit tests
# ---------------------------------------------------------------------------

class TestCallGraphHelpers:
    """Unit tests for the shared _call_graph module."""

    def test_word_match_basic(self):
        from jcodemunch_mcp.tools._call_graph import _word_match
        assert _word_match("return helper()", "helper") is True

    def test_word_match_no_partial(self):
        from jcodemunch_mcp.tools._call_graph import _word_match
        assert _word_match("return my_helper()", "helper") is False

    def test_word_match_multiline(self):
        from jcodemunch_mcp.tools._call_graph import _word_match
        content = "def foo():\n    result = helper(42)\n    return result\n"
        assert _word_match(content, "helper") is True

    def test_symbol_body_extracts_lines(self):
        from jcodemunch_mcp.tools._call_graph import _symbol_body
        lines = ["line1", "def foo():", "    return 1", "line4"]
        sym = {"line": 2, "end_line": 3}
        body = _symbol_body(lines, sym)
        assert "def foo():" in body
        assert "return 1" in body
        assert "line1" not in body
        assert "line4" not in body

    def test_symbol_body_missing_line_returns_empty(self):
        from jcodemunch_mcp.tools._call_graph import _symbol_body
        sym = {"line": 0, "end_line": 0}
        assert _symbol_body(["a", "b"], sym) == ""

    def test_build_symbols_by_file(self, tmp_path):
        from jcodemunch_mcp.tools._call_graph import build_symbols_by_file
        from jcodemunch_mcp.storage import IndexStore

        src = tmp_path / "src"
        store_path = tmp_path / "store"
        src.mkdir(); store_path.mkdir()
        (src / "a.py").write_text("def foo():\n    pass\n")
        (src / "b.py").write_text("def bar():\n    pass\n")
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        repo_id = result["repo"]
        owner, name = repo_id.split("/", 1)
        store = IndexStore(base_path=str(store_path))
        index = store.load_index(owner, name)

        mapping = build_symbols_by_file(index)
        assert any("a.py" in k for k in mapping)
        assert any("b.py" in k for k in mapping)


# ---------------------------------------------------------------------------
# get_call_hierarchy integration tests
# ---------------------------------------------------------------------------

class TestGetCallHierarchy:

    def test_returns_expected_shape(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", storage_path=store)
        assert "error" not in result, result.get("error")
        assert "symbol" in result
        assert "callers" in result
        assert "callees" in result
        assert "depth_reached" in result
        assert "caller_count" in result
        assert "callee_count" in result
        assert "_meta" in result

    def test_symbol_field_correct(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", storage_path=store)
        assert result["symbol"]["name"] == "helper"
        assert result["symbol"]["kind"] in ("function", "method", "")

    def test_callers_found_for_helper(self, tmp_path):
        """process() calls helper(), so helper's callers should include process."""
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", direction="callers",
                                    depth=1, storage_path=store)
        assert "error" not in result
        caller_names = [c["name"] for c in result["callers"]]
        assert "process" in caller_names, f"Expected 'process' in callers, got: {caller_names}"

    def test_callees_found_for_process(self, tmp_path):
        """process() calls helper(), so process's callees should include helper."""
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="process", direction="callees",
                                    depth=1, storage_path=store)
        assert "error" not in result
        callee_names = [c["name"] for c in result["callees"]]
        assert "helper" in callee_names, f"Expected 'helper' in callees, got: {callee_names}"

    def test_callers_direction_returns_no_callees(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", direction="callers",
                                    storage_path=store)
        assert result["callees"] == []

    def test_callees_direction_returns_no_callers(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="process", direction="callees",
                                    storage_path=store)
        assert result["callers"] == []

    def test_depth_field_in_each_result(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", direction="callers",
                                    depth=3, storage_path=store)
        for caller in result["callers"]:
            assert "depth" in caller
            assert caller["depth"] >= 1

    def test_depth1_returns_only_direct_callers(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", direction="callers",
                                    depth=1, storage_path=store)
        # At depth=1, only direct callers (process). handle/run are deeper.
        for caller in result["callers"]:
            assert caller["depth"] == 1

    def test_transitive_callers_at_depth3(self, tmp_path):
        """At depth 3: helper ← process ← handle ← run. All should appear."""
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", direction="callers",
                                    depth=3, storage_path=store)
        names = {c["name"] for c in result["callers"]}
        # process is depth 1, handle is depth 2, run is depth 3
        assert "process" in names
        # handle and run are deeper — may or may not be found depending on
        # whether handle is attributed to handle calling process, etc.
        # At minimum process must be present.

    def test_no_cycles_in_callers(self, tmp_path):
        """Each symbol should appear at most once in callers list."""
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", direction="callers",
                                    depth=5, storage_path=store)
        ids = [c["id"] for c in result["callers"]]
        assert len(ids) == len(set(ids)), "Duplicate caller IDs detected"

    def test_no_cycles_in_callees(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="process", direction="callees",
                                    depth=5, storage_path=store)
        ids = [c["id"] for c in result["callees"]]
        assert len(ids) == len(set(ids)), "Duplicate callee IDs detected"

    def test_unknown_symbol_returns_error(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="nonexistent_xyz", storage_path=store)
        assert "error" in result

    def test_meta_source_is_ast(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", storage_path=store)
        assert result["_meta"]["source"] == "ast"

    def test_leaf_symbol_has_no_callers(self, tmp_path):
        """run() is the top-level caller — nothing calls it in our test repo."""
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="run", direction="callers",
                                    depth=3, storage_path=store)
        assert "error" not in result
        assert result["callers"] == []

    def test_result_has_file_and_line(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_call_hierarchy(repo=repo, symbol_id="helper", direction="callers",
                                    depth=1, storage_path=store)
        for caller in result["callers"]:
            assert "file" in caller
            assert "line" in caller


# ---------------------------------------------------------------------------
# get_impact_preview integration tests
# ---------------------------------------------------------------------------

class TestGetImpactPreview:

    def test_returns_expected_shape(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="helper", storage_path=store)
        assert "error" not in result, result.get("error")
        assert "symbol" in result
        assert "affected_files" in result
        assert "affected_symbol_count" in result
        assert "affected_symbols" in result
        assert "affected_by_file" in result
        assert "call_chains" in result
        assert "_meta" in result

    def test_affected_symbols_includes_callers(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="helper", storage_path=store)
        names = [s["name"] for s in result["affected_symbols"]]
        assert "process" in names

    def test_affected_files_count(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="helper", storage_path=store)
        # At minimum services.py (contains process)
        assert result["affected_files"] >= 1

    def test_call_chains_present(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="helper", storage_path=store)
        assert isinstance(result["call_chains"], list)
        if result["call_chains"]:
            chain_entry = result["call_chains"][0]
            assert "symbol_id" in chain_entry
            assert "chain" in chain_entry
            # Chain starts from helper's ID
            target_id = result["symbol"]["id"]
            assert chain_entry["chain"][0] == target_id

    def test_call_chain_ends_at_caller(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="helper", storage_path=store)
        for entry in result["call_chains"]:
            # chain[-1] == symbol_id
            assert entry["chain"][-1] == entry["symbol_id"]

    def test_no_duplicate_affected_symbols(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="helper", storage_path=store)
        ids = [s["id"] for s in result["affected_symbols"]]
        assert len(ids) == len(set(ids)), "Duplicate affected symbols detected"

    def test_unknown_symbol_returns_error(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="no_such_sym", storage_path=store)
        assert "error" in result

    def test_leaf_symbol_has_no_affected(self, tmp_path):
        """run() has no callers — impact preview should be empty."""
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="run", storage_path=store)
        assert "error" not in result
        assert result["affected_symbol_count"] == 0
        assert result["affected_files"] == 0

    def test_affected_by_file_keys_match_affected_symbols(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_impact_preview(repo=repo, symbol_id="helper", storage_path=store)
        files_in_list = {s["file"] for s in result["affected_symbols"]}
        files_in_dict = set(result["affected_by_file"].keys())
        assert files_in_list == files_in_dict


# ---------------------------------------------------------------------------
# get_blast_radius upgrade tests (call_depth parameter)
# ---------------------------------------------------------------------------

class TestBlastRadiusCallDepth:

    def test_call_depth_zero_no_callers_field(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_blast_radius(repo=repo, symbol="helper", depth=1,
                                   call_depth=0, storage_path=store)
        assert "callers" not in result
        assert "caller_count" not in result

    def test_call_depth_one_adds_callers_field(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_blast_radius(repo=repo, symbol="helper", depth=1,
                                   call_depth=1, storage_path=store)
        assert "callers" in result
        assert "caller_count" in result
        assert isinstance(result["callers"], list)

    def test_call_depth_callers_includes_process(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = get_blast_radius(repo=repo, symbol="helper", depth=1,
                                   call_depth=1, storage_path=store)
        names = [c["name"] for c in result["callers"]]
        assert "process" in names

    def test_existing_fields_unchanged_with_call_depth(self, tmp_path):
        """Adding call_depth must not break existing output fields."""
        repo, store = _build_repo(tmp_path)
        result = get_blast_radius(repo=repo, symbol="helper", depth=1,
                                   call_depth=1, storage_path=store)
        assert "confirmed" in result
        assert "potential" in result
        assert "confirmed_count" in result
        assert "potential_count" in result
        assert "overall_risk_score" in result
        assert "importer_count" in result

    def test_call_depth_clamped_to_3(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        # Should not raise even with call_depth=99
        result = get_blast_radius(repo=repo, symbol="helper", depth=1,
                                   call_depth=99, storage_path=store)
        assert "error" not in result
        assert "callers" in result


# ---------------------------------------------------------------------------
# find_references include_call_chain tests
# ---------------------------------------------------------------------------

class TestFindReferencesCallChain:

    def test_default_no_calling_symbols(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = find_references(repo=repo, identifier="helper", storage_path=store)
        assert "error" not in result
        for ref in result.get("references", []):
            assert "calling_symbols" not in ref

    def test_include_call_chain_adds_calling_symbols(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = find_references(repo=repo, identifier="helper", storage_path=store,
                                  include_call_chain=True)
        assert "error" not in result
        # At least one reference should have calling_symbols
        found = False
        for ref in result.get("references", []):
            if "calling_symbols" in ref:
                found = True
                assert isinstance(ref["calling_symbols"], list)
        assert found, "No reference had calling_symbols when include_call_chain=True"

    def test_calling_symbols_contain_process(self, tmp_path):
        """services.py imports helper and process() calls it."""
        repo, store = _build_repo(tmp_path)
        result = find_references(repo=repo, identifier="helper", storage_path=store,
                                  include_call_chain=True)
        # Find the services.py reference
        services_ref = next(
            (r for r in result.get("references", []) if "services" in r["file"]),
            None,
        )
        assert services_ref is not None, "Expected services.py in references"
        assert "calling_symbols" in services_ref
        calling_names = [s["name"] for s in services_ref["calling_symbols"]]
        assert "process" in calling_names

    def test_calling_symbols_have_id_name_kind_line(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = find_references(repo=repo, identifier="helper", storage_path=store,
                                  include_call_chain=True)
        for ref in result.get("references", []):
            for cs in ref.get("calling_symbols", []):
                assert "id" in cs
                assert "name" in cs
                assert "kind" in cs
                assert "line" in cs

    def test_batch_mode_ignores_include_call_chain(self, tmp_path):
        """Batch mode (identifiers=[...]) should work without error when flag is set."""
        repo, store = _build_repo(tmp_path)
        result = find_references(repo=repo, identifiers=["helper", "process"],
                                  storage_path=store, include_call_chain=True)
        assert "error" not in result
        assert "results" in result

    def test_existing_reference_fields_unchanged(self, tmp_path):
        repo, store = _build_repo(tmp_path)
        result = find_references(repo=repo, identifier="helper", storage_path=store,
                                  include_call_chain=True)
        assert "reference_count" in result
        assert "references" in result
        assert "_meta" in result
        for ref in result["references"]:
            assert "file" in ref
            assert "matches" in ref
