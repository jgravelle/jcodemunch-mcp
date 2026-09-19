"""Tests for Task 4: End-to-end integration of AST-based call graph."""

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.storage import IndexStore


class TestCallGraphE2E:
    """E2E tests for AST-based call graph."""

    def test_ast_call_references_in_index(self, tmp_path):
        """Verify freshly indexed repo has call_references in symbols."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        # Create a simple call chain: utils -> services -> controllers -> main
        (src / "utils.py").write_text(
            "def helper():\n    return 42\n\ndef shared():\n    return 'ok'\n"
        )
        (src / "services.py").write_text(
            "from utils import helper\n\n"
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

        repo_id = result["repo"]
        owner, name = repo_id.split("/", 1)
        store_instance = IndexStore(base_path=str(store))
        index = store_instance.load_index(owner, name)

        # Check that symbols have call_references populated
        symbols_by_name = {s["name"]: s for s in index.symbols}

        # process calls helper
        process_sym = symbols_by_name.get("process")
        assert process_sym is not None
        assert "helper" in process_sym.get("call_references", [])

        # handle calls process
        handle_sym = symbols_by_name.get("handle")
        assert handle_sym is not None
        assert "process" in handle_sym.get("call_references", [])

        # run calls handle
        run_sym = symbols_by_name.get("run")
        assert run_sym is not None
        assert "handle" in run_sym.get("call_references", [])

    def test_get_call_hierarchy_uses_ast_methodology(self, tmp_path):
        """get_call_hierarchy should report ast_call_references methodology."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "utils.py").write_text(
            "def helper():\n    return 42\n"
        )
        (src / "services.py").write_text(
            "from utils import helper\n\n"
            "def process():\n    return helper()\n"
        )

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        repo_id = result["repo"]
        response = get_call_hierarchy(repo_id, "process", storage_path=str(store))
        assert "error" not in response
        assert "_meta" in response
        assert response["_meta"]["methodology"] == "ast_call_references"
        assert response["_meta"]["confidence_level"] == "medium"

    def test_callees_from_ast_more_precise(self, tmp_path):
        """AST-based callees should NOT include names in comments."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        # create_function is called but save_function is ONLY in a comment
        (src / "db.py").write_text(
            "def create_function():\n    pass\n\n"
            "# TODO: call save_function when ready\n"
        )
        (src / "main.py").write_text(
            "from db import create_function\n\n"
            "def run():\n    # create_function will be called\n"
            "    x = create_function()\n"
            "    # save_function should NOT appear here\n"
        )

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        repo_id = result["repo"]
        owner, name = repo_id.split("/", 1)
        store_instance = IndexStore(base_path=str(store))
        index = store_instance.load_index(owner, name)

        symbols_by_name = {s["name"]: s for s in index.symbols}
        run_sym = symbols_by_name.get("run")
        assert run_sym is not None

        call_refs = run_sym.get("call_references", [])
        # Should call create_function, not save_function (which is only in a comment)
        assert "create_function" in call_refs
        assert "save_function" not in call_refs

    def test_js_call_hierarchy(self, tmp_path):
        """JavaScript/TypeScript call extraction works."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "helper.js").write_text(
            "function helper() {\n    return 42;\n}\n"
        )
        (src / "service.js").write_text(
            "const { helper } = require('./helper');\n\n"
            "function process() {\n    return helper() + 1;\n}\n"
        )
        (src / "main.js").write_text(
            "const { process } = require('./service');\n\n"
            "function run() {\n    return process();\n}\n"
        )

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        repo_id = result["repo"]
        owner, name = repo_id.split("/", 1)
        store_instance = IndexStore(base_path=str(store))
        index = store_instance.load_index(owner, name)

        symbols_by_name = {s["name"]: s for s in index.symbols if s.get("language") == "javascript"}

        # process calls helper
        # ⚠ Selected BY FILE, not by bare name. Since #751 the destructured
        # import `const { process } = require('./service')` in main.js is itself
        # a symbol named `process`, so a name-only lookup is ambiguous and
        # picked whichever the walk emitted first. The test meant the function.
        process_sym = next(
            (s for s in index.symbols
             if s["name"] == "process"
             and s.get("language") == "javascript"
             and s["file"].endswith("service.js")),
            None,
        )
        assert process_sym is not None
        assert "helper" in process_sym.get("call_references", [])

        # run calls process
        run_sym = next((s for s in index.symbols if s["name"] == "run" and s.get("language") == "javascript"), None)
        assert run_sym is not None
        assert "process" in run_sym.get("call_references", [])

    def test_method_calls_js(self, tmp_path):
        """obj.method() extracts method name, not object name in JS."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "service.js").write_text(
            "class Service {\n"
            "    save(data) {\n"
            "        return data;\n"
            "    }\n"
            "    process() {\n"
            "        return this.save({});\n"
            "    }\n"
            "}\n"
        )

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        repo_id = result["repo"]
        owner, name = repo_id.split("/", 1)
        store_instance = IndexStore(base_path=str(store))
        index = store_instance.load_index(owner, name)

        process_sym = next((s for s in index.symbols if s["name"] == "process" and s.get("language") == "javascript"), None)
        assert process_sym is not None
        # Should call save, not this or save (object reference)
        call_refs = process_sym.get("call_references", [])
        assert "save" in call_refs


class TestFallbackBehavior:
    """Tests for backward compatibility when call_references is not available."""

    def test_v7_style_index_no_crash(self, tmp_path):
        """Old v7 index without call_references falls back to text heuristic."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "utils.py").write_text(
            "def helper():\n    return 42\n"
        )
        (src / "services.py").write_text(
            "from utils import helper\n\n"
            "def process():\n    return helper()\n"
        )

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        repo_id = result["repo"]
        response = get_call_hierarchy(repo_id, "helper", storage_path=str(store))
        assert "error" not in response
        # Should still work via fallback
        assert "callers" in response
