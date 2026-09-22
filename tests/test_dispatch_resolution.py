"""Tests for Phase 5: Interface & trait dispatch resolution."""

import os
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

# Use a real absolute path for Windows compat
_ABS_PREFIX = os.path.abspath(os.sep + "tmp")


# ---------------------------------------------------------------------------
# 1. _detect_interface_keywords tests
# ---------------------------------------------------------------------------

class TestDetectInterfaceKeywords:
    """Verify tree-sitter node type → keyword tagging."""

    def _make_node(self, ntype, children=None, text=None):
        node = MagicMock()
        node.type = ntype
        node.children = children or []
        if text is not None:
            node.text = text.encode("utf-8") if isinstance(text, str) else text
        return node

    # ⚠ The node handed in is the `type_spec` since #817 -- it is the symbol
    # node now, and the `interface` keyword is a property of the spec rather
    # than of the declaration above it. Passing a `type_declaration` here
    # answers `[]`, which is why `test_go_struct_not_tagged` would have gone on
    # passing while measuring nothing: a mock of the wrong node type is
    # indistinguishable from a correct negative. The product-level assertion is
    # `test_an_interface_in_a_block_keeps_the_keywords_dispatch_reads`.
    def test_go_interface(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        interface_type = self._make_node("interface_type")
        type_spec = self._make_node("type_spec", children=[interface_type])
        assert _detect_interface_keywords(type_spec, "go") == ["interface"]

    def test_go_struct_not_tagged(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        struct_type = self._make_node("struct_type")
        type_spec = self._make_node("type_spec", children=[struct_type])
        assert _detect_interface_keywords(type_spec, "go") == []

    def test_rust_trait(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        trait = self._make_node("trait_item")
        assert _detect_interface_keywords(trait, "rust") == ["trait"]

    def test_rust_struct_not_tagged(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("struct_item")
        assert _detect_interface_keywords(node, "rust") == []

    def test_ts_interface(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("interface_declaration")
        assert _detect_interface_keywords(node, "typescript") == ["interface"]

    def test_tsx_interface(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("interface_declaration")
        assert _detect_interface_keywords(node, "tsx") == ["interface"]

    def test_java_interface(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("interface_declaration")
        assert _detect_interface_keywords(node, "java") == ["interface"]

    def test_java_abstract_class(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        abstract_mod = self._make_node("abstract")
        modifiers = self._make_node("modifiers", children=[abstract_mod])
        class_decl = self._make_node("class_declaration", children=[modifiers])
        assert _detect_interface_keywords(class_decl, "java") == ["abstract"]

    def test_java_regular_class_not_tagged(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        public_mod = self._make_node("public")
        modifiers = self._make_node("modifiers", children=[public_mod])
        class_decl = self._make_node("class_declaration", children=[modifiers])
        assert _detect_interface_keywords(class_decl, "java") == []

    def test_csharp_interface(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("interface_declaration")
        assert _detect_interface_keywords(node, "csharp") == ["interface"]

    def test_csharp_abstract_class(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        mod = self._make_node("modifier", text="abstract")
        class_decl = self._make_node("class_declaration", children=[mod])
        assert _detect_interface_keywords(class_decl, "csharp") == ["abstract"]

    def test_php_interface(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("interface_declaration")
        assert _detect_interface_keywords(node, "php") == ["interface"]

    def test_php_trait(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("trait_declaration")
        assert _detect_interface_keywords(node, "php") == ["trait"]

    def test_python_not_tagged(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("class_definition")
        assert _detect_interface_keywords(node, "python") == []

    def test_function_not_tagged(self):
        from jcodemunch_mcp.parser.extractor import _detect_interface_keywords
        node = self._make_node("function_declaration")
        assert _detect_interface_keywords(node, "go") == []


# ---------------------------------------------------------------------------
# 2. goto_implementation() unit tests (mock LSP responses)
# ---------------------------------------------------------------------------

class TestGotoImplementation:
    """Test LSPServer.goto_implementation() method."""

    def test_returns_list_on_success(self, tmp_path):
        from jcodemunch_mcp.enrichment.lsp_bridge import LSPServer
        iface = str(tmp_path / "iface.go")
        server = LSPServer("go", ["gopls", "serve"], str(tmp_path), timeout=5)

        locations = [
            {"uri": "file:///tmp/impl.go", "range": {"start": {"line": 10, "character": 0}, "end": {"line": 10, "character": 5}}},
        ]
        with patch.object(server, "_send_request", return_value=locations):
            result = server.goto_implementation(iface, 5, 4)
        assert result == locations

    def test_normalizes_single_location(self, tmp_path):
        from jcodemunch_mcp.enrichment.lsp_bridge import LSPServer
        iface = str(tmp_path / "iface.go")
        server = LSPServer("go", ["gopls", "serve"], str(tmp_path), timeout=5)

        location = {"uri": "file:///tmp/impl.go", "range": {"start": {"line": 10, "character": 0}, "end": {"line": 10, "character": 5}}}
        with patch.object(server, "_send_request", return_value=location):
            result = server.goto_implementation(iface, 5, 4)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_returns_none_on_failure(self, tmp_path):
        from jcodemunch_mcp.enrichment.lsp_bridge import LSPServer
        iface = str(tmp_path / "iface.go")
        server = LSPServer("go", ["gopls", "serve"], str(tmp_path), timeout=5)

        with patch.object(server, "_send_request", return_value=None):
            result = server.goto_implementation(iface, 5, 4)
        assert result is None

    def test_returns_none_on_invalid_type(self, tmp_path):
        from jcodemunch_mcp.enrichment.lsp_bridge import LSPServer
        iface = str(tmp_path / "iface.go")
        server = LSPServer("go", ["gopls", "serve"], str(tmp_path), timeout=5)

        with patch.object(server, "_send_request", return_value="invalid"):
            result = server.goto_implementation(iface, 5, 4)
        assert result is None


# ---------------------------------------------------------------------------
# 3. DispatchEdge dataclass
# ---------------------------------------------------------------------------

class TestDispatchEdge:
    def test_default_resolution(self):
        from jcodemunch_mcp.enrichment.lsp_bridge import DispatchEdge
        edge = DispatchEdge(
            interface_file="/tmp/iface.go",
            interface_name="Writer",
            method_name="Write",
            impl_file="/tmp/file_writer.go",
            impl_line=10,
            impl_name="FileWriter",
        )
        assert edge.resolution == "lsp_dispatch"
        assert edge.method_name == "Write"
        assert edge.interface_name == "Writer"


# ---------------------------------------------------------------------------
# 4. _dispatch_callers / _dispatch_callees unit tests
# ---------------------------------------------------------------------------

class TestDispatchCallGraph:
    """Test _dispatch_callers and _dispatch_callees with mock indexes."""

    def _make_index(self, dispatch_edges=None):
        index = MagicMock()
        index.context_metadata = {}
        if dispatch_edges:
            index.context_metadata["dispatch_edges"] = dispatch_edges
        return index

    def test_dispatch_callees_finds_implementations(self):
        from jcodemunch_mcp.tools._call_graph import _dispatch_callees

        dispatch_edges = [
            {
                "interface_file": "writer.go",
                "interface_name": "Writer",
                "method_name": "Write",
                "impl_file": "file_writer.go",
                "impl_line": 5,
                "impl_name": "FileWriter.Write",
                "resolution": "lsp_dispatch",
            },
            {
                "interface_file": "writer.go",
                "interface_name": "Writer",
                "method_name": "Write",
                "impl_file": "net_writer.go",
                "impl_line": 8,
                "impl_name": "NetWriter.Write",
                "resolution": "lsp_dispatch",
            },
        ]
        index = self._make_index(dispatch_edges)

        # Sym calls "Write"
        sym = {"name": "process", "file": "main.go", "call_references": ["Write"]}

        symbols_by_file = {
            "file_writer.go": [
                {"id": "fw_write", "name": "FileWriter.Write", "kind": "method", "file": "file_writer.go", "line": 5},
            ],
            "net_writer.go": [
                {"id": "nw_write", "name": "NetWriter.Write", "kind": "method", "file": "net_writer.go", "line": 8},
            ],
        }

        result = _dispatch_callees(index, sym, symbols_by_file)
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert "FileWriter.Write" in names
        assert "NetWriter.Write" in names
        for r in result:
            assert r["resolution"] == "lsp_dispatch"
            assert r["dispatch_interface"] == "Writer"

    def test_dispatch_callees_empty_when_no_edges(self):
        from jcodemunch_mcp.tools._call_graph import _dispatch_callees
        index = self._make_index()
        sym = {"name": "process", "file": "main.go", "call_references": ["Write"]}
        result = _dispatch_callees(index, sym, {})
        assert result == []

    def test_dispatch_callees_empty_when_no_matching_refs(self):
        from jcodemunch_mcp.tools._call_graph import _dispatch_callees
        dispatch_edges = [{
            "interface_file": "writer.go", "interface_name": "Writer",
            "method_name": "Write", "impl_file": "file_writer.go",
            "impl_line": 5, "impl_name": "FW", "resolution": "lsp_dispatch",
        }]
        index = self._make_index(dispatch_edges)
        sym = {"name": "process", "file": "main.go", "call_references": ["Read"]}
        result = _dispatch_callees(index, sym, {})
        assert result == []

    def test_dispatch_callers_finds_interface_callers(self):
        from jcodemunch_mcp.tools._call_graph import _dispatch_callers

        dispatch_edges = [{
            "interface_file": "writer.go",
            "interface_name": "Writer",
            "method_name": "Write",
            "impl_file": "file_writer.go",
            "impl_line": 5,
            "impl_name": "FileWriter.Write",
            "resolution": "lsp_dispatch",
        }]
        index = self._make_index(dispatch_edges)

        # The implementation symbol
        sym = {"id": "fw_write", "name": "FileWriter.Write", "file": "file_writer.go", "line": 5}

        symbols_by_file = {
            "main.go": [
                {"id": "process", "name": "process", "kind": "function", "file": "main.go", "line": 1,
                 "call_references": ["Write"]},
            ],
        }

        result = _dispatch_callers(index, sym, symbols_by_file)
        assert len(result) == 1
        assert result[0]["name"] == "process"
        assert result[0]["resolution"] == "lsp_dispatch"

    def test_dispatch_callers_empty_when_sym_not_impl(self):
        from jcodemunch_mcp.tools._call_graph import _dispatch_callers
        dispatch_edges = [{
            "interface_file": "writer.go", "interface_name": "Writer",
            "method_name": "Write", "impl_file": "file_writer.go",
            "impl_line": 5, "impl_name": "FW", "resolution": "lsp_dispatch",
        }]
        index = self._make_index(dispatch_edges)
        sym = {"id": "unrelated", "name": "unrelated", "file": "other.go", "line": 1}
        result = _dispatch_callers(index, sym, {})
        assert result == []


# ---------------------------------------------------------------------------
# 5. get_call_hierarchy dispatches section
# ---------------------------------------------------------------------------

class TestCallHierarchyDispatchesSection:
    """Test that get_call_hierarchy includes dispatches in its response."""

    def test_dispatches_section_present(self, tmp_path):
        """Build a repo, mock dispatch_edges in metadata, verify response."""
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy

        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "main.py").write_text("def run():\n    return 1\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True
        repo = result["repo"]

        # Inject dispatch_edges into the stored index
        from jcodemunch_mcp.storage import IndexStore
        idx_store = IndexStore(base_path=str(store))
        index = idx_store.load_index(*repo.split("/"))
        if index and index.context_metadata is None:
            index.context_metadata = {}
        if index:
            index.context_metadata["dispatch_edges"] = [
                {
                    "interface_file": "iface.py",
                    "interface_name": "Handler",
                    "method_name": "handle",
                    "impl_file": "impl.py",
                    "impl_line": 5,
                    "impl_name": "ConcreteHandler",
                    "resolution": "lsp_dispatch",
                },
            ]

            # Call get_call_hierarchy (symbol doesn't need dispatch edges to test section presence)
            result = get_call_hierarchy(repo, "run", storage_path=str(store))

            # The dispatches section should be present (may be empty since we didn't modify the loaded index on disk)
            assert "dispatches" in result

    def test_dispatches_empty_when_no_edges(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy

        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "main.py").write_text("def run():\n    return 1\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True
        repo = result["repo"]

        result = get_call_hierarchy(repo, "run", storage_path=str(store))
        assert "dispatches" in result
        assert result["dispatches"] == []


# ---------------------------------------------------------------------------
# 6. Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """Ensure dispatch resolution degrades gracefully."""

    def test_enrich_returns_empty_when_lsp_disabled(self):
        from jcodemunch_mcp.enrichment.lsp_bridge import enrich_dispatch_edges
        with patch("jcodemunch_mcp.enrichment.lsp_bridge.get_lsp_config", return_value={"lsp_enabled": False}):
            result = enrich_dispatch_edges("/tmp", [], {}, {})
        assert result == []

    def test_enrich_returns_empty_when_no_interfaces(self):
        from jcodemunch_mcp.enrichment.lsp_bridge import enrich_dispatch_edges
        # Symbols with no interface keywords
        syms = [MagicMock(keywords=[], id="s1", parent=None, name="foo", file="foo.py", line=1)]
        with patch("jcodemunch_mcp.enrichment.lsp_bridge.get_lsp_config", return_value={
            "lsp_enabled": True, "lsp_servers": {"python": "pyright"}, "lsp_timeout_seconds": 5,
        }):
            result = enrich_dispatch_edges("/tmp", syms, {}, {"foo.py": "python"})
        assert result == []

    def test_resolve_implementations_empty_input(self):
        from jcodemunch_mcp.enrichment.lsp_bridge import LSPBridge
        bridge = LSPBridge("/tmp")
        result = bridge.resolve_implementations([], {}, {})
        assert result == []


# ---------------------------------------------------------------------------
# 7. Interface keyword propagation through index_folder
# ---------------------------------------------------------------------------

class TestInterfaceKeywordPropagation:
    """Verify that interface keywords survive indexing for TS interfaces."""

    def test_ts_interface_gets_keyword(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder

        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "types.ts").write_text(
            "interface Writer {\n  write(data: string): void;\n}\n"
        )

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        # Load the index and check keywords
        from jcodemunch_mcp.storage import IndexStore
        idx_store = IndexStore(base_path=str(store))
        repo = result["repo"]
        index = idx_store.load_index(*repo.split("/"))
        assert index is not None

        # Find the Writer symbol
        writer_syms = [s for s in index.symbols if s.get("name") == "Writer"]
        assert len(writer_syms) >= 1
        writer = writer_syms[0]
        keywords = writer.get("keywords", [])
        assert "interface" in keywords, f"Expected 'interface' in keywords, got {keywords}"
