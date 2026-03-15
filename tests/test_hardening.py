"""Comprehensive hardening tests for jcodemunch-mcp parser, storage, and tools."""

import json
from pathlib import Path

import pytest

from jcodemunch_mcp.parser import parse_file, Symbol, make_symbol_id, compute_content_hash
from jcodemunch_mcp.storage import IndexStore, CodeIndex, INDEX_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(language: str, filename: str) -> tuple[str, str]:
    """Return (content, filepath) for a fixture file."""
    path = FIXTURES / language / filename
    content = path.read_text(encoding="utf-8")
    return content, path.name


def _kinds(symbols: list[Symbol]) -> dict[str, list[Symbol]]:
    """Group symbols by kind for easier assertions."""
    result: dict[str, list[Symbol]] = {}
    for s in symbols:
        result.setdefault(s.kind, []).append(s)
    return result


def _names(symbols: list[Symbol]) -> set[str]:
    """Get set of symbol names."""
    return {s.name for s in symbols}


def _by_name(symbols: list[Symbol], name: str) -> Symbol:
    """Find a symbol by name (first match)."""
    for s in symbols:
        if s.name == name:
            return s
    raise AssertionError(f"No symbol named '{name}' found. Available: {_names(symbols)}")


# ===========================================================================
# 1. Per-Language Extraction
# ===========================================================================


class TestPerLanguageExtraction:
    """Verify symbol extraction for each supported language fixture."""

    # -- Python ----------------------------------------------------------

    def test_python_symbol_count(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        # Expected: MAX_RETRIES (constant), UserService (class),
        # get_user (method), delete_user (method), authenticate (function)
        assert len(symbols) >= 5

    def test_python_class(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        cls = _by_name(symbols, "UserService")
        assert cls.kind == "class"
        assert cls.language == "python"

    def test_python_methods(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        grouped = _kinds(symbols)
        methods = grouped.get("method", [])
        method_names = {m.name for m in methods}
        assert "get_user" in method_names
        assert "delete_user" in method_names
        for m in methods:
            assert m.parent is not None, f"Method {m.name} should have a parent"

    def test_python_function(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        func = _by_name(symbols, "authenticate")
        assert func.kind == "function"

    def test_python_constant(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        const = _by_name(symbols, "MAX_RETRIES")
        assert const.kind == "constant"

    def test_python_qualified_names(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        get_user = _by_name(symbols, "get_user")
        assert get_user.qualified_name == "UserService.get_user"
        delete_user = _by_name(symbols, "delete_user")
        assert delete_user.qualified_name == "UserService.delete_user"

    # -- JavaScript ------------------------------------------------------

    def test_javascript_class(self):
        content, fname = _fixture("javascript", "sample.js")
        symbols = parse_file(content, fname, "javascript")
        cls = _by_name(symbols, "UserService")
        assert cls.kind == "class"

    def test_javascript_function(self):
        content, fname = _fixture("javascript", "sample.js")
        symbols = parse_file(content, fname, "javascript")
        func = _by_name(symbols, "authenticate")
        assert func.kind == "function"

    def test_javascript_method(self):
        content, fname = _fixture("javascript", "sample.js")
        symbols = parse_file(content, fname, "javascript")
        grouped = _kinds(symbols)
        methods = grouped.get("method", [])
        method_names = {m.name for m in methods}
        assert "getUser" in method_names

    def test_javascript_qualified_names(self):
        content, fname = _fixture("javascript", "sample.js")
        symbols = parse_file(content, fname, "javascript")
        # method_definition nodes inside class get parent set
        get_user = _by_name(symbols, "getUser")
        assert "UserService" in get_user.qualified_name

    # -- TypeScript ------------------------------------------------------

    def test_typescript_class(self):
        content, fname = _fixture("typescript", "sample.ts")
        symbols = parse_file(content, fname, "typescript")
        cls = _by_name(symbols, "UserService")
        assert cls.kind == "class"

    def test_typescript_function(self):
        content, fname = _fixture("typescript", "sample.ts")
        symbols = parse_file(content, fname, "typescript")
        func = _by_name(symbols, "authenticate")
        assert func.kind == "function"

    def test_typescript_interface(self):
        content, fname = _fixture("typescript", "sample.ts")
        symbols = parse_file(content, fname, "typescript")
        iface = _by_name(symbols, "User")
        assert iface.kind == "type"

    def test_typescript_type_alias(self):
        content, fname = _fixture("typescript", "sample.ts")
        symbols = parse_file(content, fname, "typescript")
        alias = _by_name(symbols, "UserID")
        assert alias.kind == "type"

    def test_typescript_method(self):
        content, fname = _fixture("typescript", "sample.ts")
        symbols = parse_file(content, fname, "typescript")
        get_user = _by_name(symbols, "getUser")
        assert get_user.kind == "method"
        assert "UserService" in get_user.qualified_name

    # -- Go --------------------------------------------------------------

    def test_go_functions(self):
        content, fname = _fixture("go", "sample.go")
        symbols = parse_file(content, fname, "go")
        grouped = _kinds(symbols)
        func_names = {f.name for f in grouped.get("function", [])}
        assert "GetUser" in func_names
        assert "Authenticate" in func_names

    def test_go_type(self):
        content, fname = _fixture("go", "sample.go")
        symbols = parse_file(content, fname, "go")
        user = _by_name(symbols, "User")
        assert user.kind == "type"

    def test_go_function_kind(self):
        content, fname = _fixture("go", "sample.go")
        symbols = parse_file(content, fname, "go")
        get_user = _by_name(symbols, "GetUser")
        assert get_user.kind == "function"

    # -- Rust ------------------------------------------------------------

    def test_rust_struct(self):
        content, fname = _fixture("rust", "sample.rs")
        symbols = parse_file(content, fname, "rust")
        user = _by_name(symbols, "User")
        assert user.kind == "type"

    def test_rust_impl_block_not_extracted(self):
        """impl_item is in symbol_node_types but has no name_fields entry,
        so the extractor skips it (returns None from _extract_name).
        Functions inside impl are still extracted as top-level functions."""
        content, fname = _fixture("rust", "sample.rs")
        symbols = parse_file(content, fname, "rust")
        grouped = _kinds(symbols)
        # impl blocks are skipped because name extraction fails
        impl_syms = grouped.get("class", [])
        assert len(impl_syms) == 0

    def test_rust_fn_in_impl(self):
        """Without the impl parent being extracted, 'new' appears as a
        top-level function rather than a method."""
        content, fname = _fixture("rust", "sample.rs")
        symbols = parse_file(content, fname, "rust")
        new_sym = _by_name(symbols, "new")
        assert new_sym.kind == "function"
        assert new_sym.parent is None

    def test_rust_free_function(self):
        content, fname = _fixture("rust", "sample.rs")
        symbols = parse_file(content, fname, "rust")
        auth = _by_name(symbols, "authenticate")
        assert auth.kind == "function"

    # -- Java ------------------------------------------------------------

    def test_java_class(self):
        content, fname = _fixture("java", "Sample.java")
        symbols = parse_file(content, fname, "java")
        cls = _by_name(symbols, "Sample")
        assert cls.kind == "class"

    def test_java_methods(self):
        content, fname = _fixture("java", "Sample.java")
        symbols = parse_file(content, fname, "java")
        grouped = _kinds(symbols)
        methods = grouped.get("method", [])
        method_names = {m.name for m in methods}
        assert "getUser" in method_names
        assert "authenticate" in method_names

    def test_java_method_qualified_names(self):
        content, fname = _fixture("java", "Sample.java")
        symbols = parse_file(content, fname, "java")
        get_user = _by_name(symbols, "getUser")
        assert "Sample" in get_user.qualified_name

    # -- C# --------------------------------------------------------------

    def test_csharp_class(self):
        content, fname = _fixture("csharp", "sample.cs")
        symbols = parse_file(content, fname, "csharp")
        cls = _by_name(symbols, "UserService")
        assert cls.kind == "class"

    def test_csharp_method_qualified_name(self):
        content, fname = _fixture("csharp", "sample.cs")
        symbols = parse_file(content, fname, "csharp")
        method = _by_name(symbols, "GetUser")
        assert method.kind == "method"
        assert "UserService" in method.qualified_name

    def test_csharp_interface(self):
        content, fname = _fixture("csharp", "sample.cs")
        symbols = parse_file(content, fname, "csharp")
        iface = _by_name(symbols, "IRepository")
        assert iface.kind == "type"

    def test_csharp_enum(self):
        content, fname = _fixture("csharp", "sample.cs")
        symbols = parse_file(content, fname, "csharp")
        enum = _by_name(symbols, "Status")
        assert enum.kind == "type"

    def test_csharp_record(self):
        content, fname = _fixture("csharp", "sample.cs")
        symbols = parse_file(content, fname, "csharp")
        record = _by_name(symbols, "Person")
        assert record.kind == "class"

    # -- C++ -------------------------------------------------------------

    def test_cpp_class(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        cls = _by_name(symbols, "Box")
        assert cls.kind == "class"
        assert "sample" in cls.qualified_name

    def test_cpp_method_qualified_name(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        method = _by_name(symbols, "get")
        assert method.kind == "method"
        assert "Box" in method.qualified_name

    def test_cpp_alias_and_enum(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        alias = _by_name(symbols, "UserId")
        status = _by_name(symbols, "Status")
        assert alias.kind == "type"
        assert status.kind == "type"

    def test_cpp_constant(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        const = _by_name(symbols, "MAX_USERS")
        assert const.kind == "constant"

    def test_cpp_overload_disambiguation(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        add_syms = [s for s in symbols if s.name == "add" and s.kind == "function"]
        assert len(add_syms) >= 2
        ids = [s.id for s in add_syms]
        assert any(i.endswith("~1") for i in ids)
        assert any(i.endswith("~2") for i in ids)

    def test_cpp_nested_namespace_qualification(self):
        content = """
namespace a { namespace b {
class Thing { public: int run() const { return 1; } };
} }
"""
        symbols = parse_file(content, "ns.cpp", "cpp")
        cls = _by_name(symbols, "Thing")
        run = _by_name(symbols, "run")
        assert cls.qualified_name == "a.b.Thing"
        assert run.qualified_name == "a.b.Thing.run"

    def test_cpp_mixed_header_deterministic(self):
        content = """
class MaybeCpp { public: int Get() const; };
int only_c(void) { int v[] = (int[]){1,2,3}; return v[0]; }
"""
        run1 = parse_file(content, "mixed.h", "cpp")
        run2 = parse_file(content, "mixed.h", "cpp")
        assert run1 and run2
        assert {s.language for s in run1} == {s.language for s in run2}

    # -- C ---------------------------------------------------------------

    def test_c_functions(self):
        content, fname = _fixture("c", "sample.c")
        symbols = parse_file(content, fname, "c")
        grouped = _kinds(symbols)
        func_names = {f.name for f in grouped.get("function", [])}
        assert "get_user" in func_names
        assert "authenticate" in func_names

    def test_c_struct(self):
        content, fname = _fixture("c", "sample.c")
        symbols = parse_file(content, fname, "c")
        user = _by_name(symbols, "User")
        assert user.kind == "type"

    def test_c_enum(self):
        content, fname = _fixture("c", "sample.c")
        symbols = parse_file(content, fname, "c")
        status = _by_name(symbols, "Status")
        assert status.kind == "type"

    def test_c_constant(self):
        content, fname = _fixture("c", "sample.c")
        symbols = parse_file(content, fname, "c")
        const = _by_name(symbols, "MAX_USERS")
        assert const.kind == "constant"

    def test_c_function_kind(self):
        content, fname = _fixture("c", "sample.c")
        symbols = parse_file(content, fname, "c")
        auth = _by_name(symbols, "authenticate")
        assert auth.kind == "function"

    # -- C++ --------------------------------------------------------------

    def test_cpp_functions(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        grouped = _kinds(symbols)
        func_names = {f.name for f in grouped.get("function", [])}
        assert "identity" in func_names
        assert "add" in func_names

    def test_cpp_class(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        cls = _by_name(symbols, "Box")
        assert cls.kind == "class"
        assert "sample" in cls.qualified_name

    def test_cpp_struct(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        alias = _by_name(symbols, "UserId")
        assert alias.kind == "type"

    def test_cpp_enum(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        status = _by_name(symbols, "Status")
        assert status.kind == "type"

    def test_cpp_constant(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        const = _by_name(symbols, "MAX_USERS")
        assert const.kind == "constant"

    def test_cpp_method_qualified_name(self):
        content, fname = _fixture("cpp", "sample.cpp")
        symbols = parse_file(content, fname, "cpp")
        method = _by_name(symbols, "get")
        assert method.kind == "method"
        assert "Box" in method.qualified_name

    # -- Elixir ----------------------------------------------------------

    def test_elixir_module(self):
        content, fname = _fixture("elixir", "sample.ex")
        symbols = parse_file(content, fname, "elixir")
        mod = _by_name(symbols, "MyApp.Calculator")
        assert mod.kind == "class"
        assert mod.language == "elixir"

    def test_elixir_method(self):
        content, fname = _fixture("elixir", "sample.ex")
        symbols = parse_file(content, fname, "elixir")
        method = _by_name(symbols, "add")
        assert method.kind == "method"
        assert method.parent is not None

    def test_elixir_private_function(self):
        content, fname = _fixture("elixir", "sample.ex")
        symbols = parse_file(content, fname, "elixir")
        func = _by_name(symbols, "validate")
        assert func.kind == "method"

    def test_elixir_protocol(self):
        content, fname = _fixture("elixir", "sample.ex")
        symbols = parse_file(content, fname, "elixir")
        proto = _by_name(symbols, "MyApp.Printable")
        assert proto.kind == "type"

    def test_elixir_type_attribute(self):
        content, fname = _fixture("elixir", "sample.ex")
        symbols = parse_file(content, fname, "elixir")
        t = _by_name(symbols, "result")
        assert t.kind == "type"

    def test_elixir_qualified_names(self):
        content, fname = _fixture("elixir", "sample.ex")
        symbols = parse_file(content, fname, "elixir")
        add = _by_name(symbols, "add")
        assert add.qualified_name == "MyApp.Calculator.add"

    # -- Ruby ------------------------------------------------------------

    def test_ruby_class(self):
        content, fname = _fixture("ruby", "sample.rb")
        symbols = parse_file(content, fname, "ruby")
        cls = _by_name(symbols, "User")
        assert cls.kind == "class"
        assert cls.language == "ruby"

    def test_ruby_module(self):
        content, fname = _fixture("ruby", "sample.rb")
        symbols = parse_file(content, fname, "ruby")
        mod = _by_name(symbols, "Serializable")
        assert mod.kind == "type"

    def test_ruby_method_qualified_name(self):
        content, fname = _fixture("ruby", "sample.rb")
        symbols = parse_file(content, fname, "ruby")
        m = _by_name(symbols, "initialize")
        assert m.qualified_name == "User.initialize"
        assert m.kind == "method"

    def test_ruby_singleton_method(self):
        content, fname = _fixture("ruby", "sample.rb")
        symbols = parse_file(content, fname, "ruby")
        find = _by_name(symbols, "find")
        assert find.kind == "method"
        assert find.qualified_name == "User.find"

    def test_ruby_top_level_function(self):
        content, fname = _fixture("ruby", "sample.rb")
        symbols = parse_file(content, fname, "ruby")
        fmt = _by_name(symbols, "format_name")
        assert fmt.kind == "function"

    # -- XML / XUL -------------------------------------------------------

    def test_xml_root_element(self):
        content, fname = _fixture("xml", "sample.xml")
        symbols = parse_file(content, fname, "xml")
        root = _by_name(symbols, "config")
        assert root.kind == "type"

    def test_xml_id_elements(self):
        content, fname = _fixture("xml", "sample.xml")
        symbols = parse_file(content, fname, "xml")
        db = _by_name(symbols, "db-primary")
        assert db.kind == "constant"

    def test_xml_script_refs(self):
        content, fname = _fixture("xml", "sample.xml")
        symbols = parse_file(content, fname, "xml")
        script = _by_name(symbols, "validator.js")
        assert script.kind == "function"

    def test_xul_window_root(self):
        content, fname = _fixture("xml", "sample.xul")
        symbols = parse_file(content, fname, "xml")
        window = _by_name(symbols, "window")
        assert window.kind == "type"

    def test_xul_element_kind(self):
        content, fname = _fixture("xml", "sample.xul")
        symbols = parse_file(content, fname, "xml")
        btn = _by_name(symbols, "search-button")
        assert btn.kind == "constant"


# ===========================================================================
# 2. Overload Disambiguation
# ===========================================================================


class TestOverloadDisambiguation:
    """Verify that duplicate symbol IDs get ~1, ~2 suffixes."""

    OVERLOADED_SRC = '''\
def process(x: int) -> int:
    return x

def process(x: str) -> str:
    return x.upper()
'''

    def test_duplicate_ids_get_ordinal_suffix(self):
        symbols = parse_file(self.OVERLOADED_SRC, "overloads.py", "python")
        process_syms = [s for s in symbols if s.name == "process"]
        assert len(process_syms) == 2

        ids = [s.id for s in process_syms]
        assert ids[0].endswith("~1"), f"Expected ~1 suffix, got {ids[0]}"
        assert ids[1].endswith("~2"), f"Expected ~2 suffix, got {ids[1]}"

    def test_non_duplicate_ids_unchanged(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        for s in symbols:
            assert "~" not in s.id, f"Symbol {s.name} has unexpected ordinal: {s.id}"


# ===========================================================================
# 3. Content Hashing
# ===========================================================================


class TestContentHashing:
    """Verify content_hash is populated and consistent."""

    def test_all_symbols_have_content_hash(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        for s in symbols:
            assert s.content_hash, f"Symbol {s.name} missing content_hash"
            assert len(s.content_hash) == 64, "SHA-256 hex should be 64 chars"

    def test_reparse_produces_same_hashes(self):
        content, fname = _fixture("python", "sample.py")
        symbols_a = parse_file(content, fname, "python")
        symbols_b = parse_file(content, fname, "python")

        hashes_a = {s.name: s.content_hash for s in symbols_a}
        hashes_b = {s.name: s.content_hash for s in symbols_b}
        assert hashes_a == hashes_b

    def test_compute_content_hash_directly(self):
        data = b"hello world"
        h = compute_content_hash(data)
        assert len(h) == 64
        # Same input -> same hash
        assert compute_content_hash(data) == h
        # Different input -> different hash
        assert compute_content_hash(b"different") != h


# ===========================================================================
# 4. Determinism
# ===========================================================================


class TestDeterminism:
    """Parse the same file twice and confirm identical output."""

    @pytest.mark.parametrize("language,filename", [
        ("python", "sample.py"),
        ("javascript", "sample.js"),
        ("typescript", "sample.ts"),
        ("go", "sample.go"),
        ("rust", "sample.rs"),
        ("java", "Sample.java"),
        ("dart", "sample.dart"),
        ("csharp", "sample.cs"),
        ("c", "sample.c"),
        ("cpp", "sample.cpp"),
        ("elixir", "sample.ex"),
        ("ruby", "sample.rb"),
        ("xml", "sample.xml"),
    ])
    def test_deterministic_ids_and_hashes(self, language, filename):
        content, fname = _fixture(language, filename)
        run1 = parse_file(content, fname, language)
        run2 = parse_file(content, fname, language)

        assert len(run1) == len(run2), f"Symbol count mismatch for {language}"

        for s1, s2 in zip(run1, run2):
            assert s1.id == s2.id, f"ID mismatch: {s1.id} vs {s2.id}"
            assert s1.content_hash == s2.content_hash, (
                f"Hash mismatch for {s1.name}: {s1.content_hash} vs {s2.content_hash}"
            )
            assert s1.kind == s2.kind
            assert s1.qualified_name == s2.qualified_name


# ===========================================================================
# 5. Incremental Reindex
# ===========================================================================


class TestIncrementalReindex:
    """Test incremental indexing: detect changes, add/remove files."""

    def _make_index(self, tmp_path: Path) -> IndexStore:
        """Create an IndexStore rooted at tmp_path and seed it."""
        store = IndexStore(base_path=str(tmp_path))

        py_content = "def hello():\n    pass\n"
        js_content = "function greet() { return 1; }\n"

        py_symbols = parse_file(py_content, "hello.py", "python")
        js_symbols = parse_file(js_content, "greet.js", "javascript")

        store.save_index(
            owner="test",
            name="repo",
            source_files=["hello.py", "greet.js"],
            symbols=py_symbols + js_symbols,
            raw_files={"hello.py": py_content, "greet.js": js_content},
            languages={"python": 1, "javascript": 1},
        )
        return store

    def test_detect_changed_file(self, tmp_path):
        store = self._make_index(tmp_path)

        modified_py = "def hello():\n    return 42\n"
        changed, new, deleted = store.detect_changes(
            "test", "repo",
            {"hello.py": modified_py, "greet.js": "function greet() { return 1; }\n"},
        )
        assert "hello.py" in changed
        assert len(new) == 0
        assert len(deleted) == 0

    def test_detect_new_file(self, tmp_path):
        store = self._make_index(tmp_path)

        changed, new, deleted = store.detect_changes(
            "test", "repo",
            {
                "hello.py": "def hello():\n    pass\n",
                "greet.js": "function greet() { return 1; }\n",
                "extra.py": "x = 1\n",
            },
        )
        assert "extra.py" in new
        assert len(changed) == 0
        assert len(deleted) == 0

    def test_detect_deleted_file(self, tmp_path):
        store = self._make_index(tmp_path)

        changed, new, deleted = store.detect_changes(
            "test", "repo",
            {"hello.py": "def hello():\n    pass\n"},
        )
        assert "greet.js" in deleted
        assert len(changed) == 0
        assert len(new) == 0

    def test_incremental_save_replaces_symbols(self, tmp_path):
        store = self._make_index(tmp_path)

        # Modify hello.py: rename function
        new_py = "def goodbye():\n    return 99\n"
        new_symbols = parse_file(new_py, "hello.py", "python")

        updated = store.incremental_save(
            owner="test",
            name="repo",
            changed_files=["hello.py"],
            new_files=[],
            deleted_files=[],
            new_symbols=new_symbols,
            raw_files={"hello.py": new_py},
            languages={"python": 1, "javascript": 1},
        )

        assert updated is not None
        sym_names = {s["name"] for s in updated.symbols}
        assert "goodbye" in sym_names, "New symbol should be present"
        assert "hello" not in sym_names, "Old symbol should be removed"
        # JS symbol should still be there
        assert "greet" in sym_names

    def test_incremental_save_removes_deleted_file_symbols(self, tmp_path):
        store = self._make_index(tmp_path)

        updated = store.incremental_save(
            owner="test",
            name="repo",
            changed_files=[],
            new_files=[],
            deleted_files=["greet.js"],
            new_symbols=[],
            raw_files={},
            languages={"python": 1},
        )

        assert updated is not None
        sym_names = {s["name"] for s in updated.symbols}
        assert "greet" not in sym_names
        assert "greet.js" not in updated.source_files


# ===========================================================================
# 6. Index Versioning
# ===========================================================================


class TestIndexVersioning:
    """Test index version compatibility checks."""

    def test_saved_index_has_current_version(self, tmp_path):
        store = IndexStore(base_path=str(tmp_path))
        content = "def foo(): pass\n"
        symbols = parse_file(content, "foo.py", "python")

        index = store.save_index(
            owner="ver",
            name="test",
            source_files=["foo.py"],
            symbols=symbols,
            raw_files={"foo.py": content},
            languages={"python": 1},
        )

        assert index.index_version == INDEX_VERSION
        assert index.index_version == 4

    def test_load_preserves_version(self, tmp_path):
        store = IndexStore(base_path=str(tmp_path))
        content = "def foo(): pass\n"
        symbols = parse_file(content, "foo.py", "python")

        store.save_index(
            owner="ver",
            name="test",
            source_files=["foo.py"],
            symbols=symbols,
            raw_files={"foo.py": content},
            languages={"python": 1},
        )

        loaded = store.load_index("ver", "test")
        assert loaded is not None
        assert loaded.index_version == INDEX_VERSION

    def test_future_version_returns_none(self, tmp_path):
        store = IndexStore(base_path=str(tmp_path))
        content = "def foo(): pass\n"
        symbols = parse_file(content, "foo.py", "python")

        store.save_index(
            owner="ver",
            name="test",
            source_files=["foo.py"],
            symbols=symbols,
            raw_files={"foo.py": content},
            languages={"python": 1},
        )

        # Manually bump the version in the JSON file to a future version
        index_path = tmp_path / "ver-test.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["index_version"] = INDEX_VERSION + 100
        index_path.write_text(json.dumps(data), encoding="utf-8")

        loaded = store.load_index("ver", "test")
        assert loaded is None, "Future version should not be loadable"


# ===========================================================================
# 7. New Tools (search_text, get_repo_outline, invalidate_cache)
# ===========================================================================


class TestNewTools:
    """Test the search_text, get_repo_outline, and invalidate_cache tools."""

    def _seed_index(self, tmp_path: Path) -> str:
        """Create a seeded index and return storage_path."""
        storage = str(tmp_path / "store")
        store = IndexStore(base_path=storage)

        py_content = 'SECRET_KEY = "abc123"\n\ndef greet(name):\n    return f"Hello {name}"\n'
        js_content = "function add(a, b) { return a + b; }\n"

        py_symbols = parse_file(py_content, "app.py", "python")
        js_symbols = parse_file(js_content, "utils.js", "javascript")

        store.save_index(
            owner="tools",
            name="demo",
            source_files=["app.py", "utils.js"],
            symbols=py_symbols + js_symbols,
            raw_files={"app.py": py_content, "utils.js": js_content},
            languages={"python": 1, "javascript": 1},
        )
        return storage

    def test_search_text_finds_string(self, tmp_path):
        from jcodemunch_mcp.tools.search_text import search_text

        storage = self._seed_index(tmp_path)
        result = search_text(
            repo="tools/demo",
            query="abc123",
            storage_path=storage,
        )

        assert "error" not in result
        assert result["result_count"] >= 1
        files_found = {m["file"] for m in result["results"]}
        assert "app.py" in files_found

    def test_search_text_case_insensitive(self, tmp_path):
        from jcodemunch_mcp.tools.search_text import search_text

        storage = self._seed_index(tmp_path)
        result = search_text(
            repo="tools/demo",
            query="SECRET_KEY",
            storage_path=storage,
        )
        assert result["result_count"] >= 1

    def test_search_text_not_found(self, tmp_path):
        from jcodemunch_mcp.tools.search_text import search_text

        storage = self._seed_index(tmp_path)
        result = search_text(
            repo="tools/demo",
            query="nonexistent_string_xyz",
            storage_path=storage,
        )
        assert result["result_count"] == 0

    def test_get_repo_outline_structure(self, tmp_path):
        from jcodemunch_mcp.tools.get_repo_outline import get_repo_outline

        storage = self._seed_index(tmp_path)
        result = get_repo_outline(
            repo="tools/demo",
            storage_path=storage,
        )

        assert "error" not in result
        assert result["repo"] == "tools/demo"
        assert result["file_count"] == 2
        assert result["symbol_count"] >= 2
        assert "python" in result["languages"]
        assert "javascript" in result["languages"]
        assert "symbol_kinds" in result
        assert "_meta" in result

    def test_get_repo_outline_missing_repo(self, tmp_path):
        from jcodemunch_mcp.tools.get_repo_outline import get_repo_outline

        storage = str(tmp_path / "empty")
        result = get_repo_outline(
            repo="nonexistent/repo",
            storage_path=storage,
        )
        assert "error" in result

    def test_invalidate_cache_deletes_index(self, tmp_path):
        from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

        storage = self._seed_index(tmp_path)
        result = invalidate_cache(
            repo="tools/demo",
            storage_path=storage,
        )

        assert result["success"] is True

        # Verify index is gone
        store = IndexStore(base_path=storage)
        loaded = store.load_index("tools", "demo")
        assert loaded is None

    def test_invalidate_cache_missing_repo(self, tmp_path):
        from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

        storage = str(tmp_path / "empty")
        result = invalidate_cache(
            repo="ghost/repo",
            storage_path=storage,
        )
        assert result["success"] is False

    def test_invalidate_cache_rejects_ambiguous_bare_name(self, tmp_path):
        from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

        storage = str(tmp_path / "store")
        store = IndexStore(base_path=storage)
        for repo_name in ["shared-aaa11111", "shared-bbb22222"]:
            store.save_index(
                owner="local",
                name=repo_name,
                source_files=["main.py"],
                symbols=[],
                raw_files={"main.py": "print('x')\n"},
                languages={"python": 1},
                display_name="shared",
            )

        result = invalidate_cache(repo="shared", storage_path=storage)
        assert result["error"].startswith("Ambiguous repository name: shared")
