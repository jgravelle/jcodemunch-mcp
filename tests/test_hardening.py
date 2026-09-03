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

    # ------------------------------------------------------------------
    # Symbol-kind parametrized tests
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("language,filename,symbol_name,expected_kind", [
        # Python
        ("python", "sample.py", "UserService", "class"),
        ("python", "sample.py", "authenticate", "function"),
        ("python", "sample.py", "MAX_RETRIES", "constant"),
        # JavaScript
        ("javascript", "sample.js", "UserService", "class"),
        ("javascript", "sample.js", "authenticate", "function"),
        # TypeScript
        ("typescript", "sample.ts", "UserService", "class"),
        ("typescript", "sample.ts", "authenticate", "function"),
        ("typescript", "sample.ts", "User", "type"),
        ("typescript", "sample.ts", "UserID", "type"),
        # Go
        ("go", "sample.go", "User", "type"),
        ("go", "sample.go", "GetUser", "function"),
        # Rust
        ("rust", "sample.rs", "User", "type"),
        ("rust", "sample.rs", "authenticate", "function"),
        # Java
        ("java", "Sample.java", "Sample", "class"),
        # C#
        ("csharp", "sample.cs", "UserService", "class"),
        ("csharp", "sample.cs", "IRepository", "type"),
        ("csharp", "sample.cs", "Status", "type"),
        ("csharp", "sample.cs", "Person", "class"),
        # C++
        ("cpp", "sample.cpp", "Box", "class"),
        ("cpp", "sample.cpp", "UserId", "type"),
        ("cpp", "sample.cpp", "Status", "type"),
        ("cpp", "sample.cpp", "MAX_USERS", "constant"),
        # Arduino
        ("arduino", "sample.ino", "MotorController", "class"),
        # VHDL
        ("vhdl", "sample.vhd", "alu", "class"),
        # Verilog
        ("verilog", "sample.sv", "alu", "class"),
        # C
        ("c", "sample.c", "User", "type"),
        ("c", "sample.c", "Status", "type"),
        ("c", "sample.c", "MAX_USERS", "constant"),
        # Elixir
        ("elixir", "sample.ex", "MyApp.Calculator", "class"),
        ("elixir", "sample.ex", "MyApp.Printable", "type"),
        ("elixir", "sample.ex", "result", "type"),
        # Ruby
        ("ruby", "sample.rb", "User", "class"),
        ("ruby", "sample.rb", "Serializable", "type"),
        ("ruby", "sample.rb", "format_name", "function"),
        # XML/XUL
        ("xml", "sample.xml", "config", "type"),
        ("xml", "sample.xml", "db-primary", "constant"),
        ("xml", "sample.xml", "validator.js", "function"),
        ("xml", "sample.xul", "window", "type"),
        ("xml", "sample.xul", "search-button", "constant"),
    ])
    def test_symbol_kind(self, language, filename, symbol_name, expected_kind):
        """Parametrized symbol-kind assertion across all languages."""
        content, fname = _fixture(language, filename)
        symbols = parse_file(content, fname, language)
        sym = _by_name(symbols, symbol_name)
        assert sym.kind == expected_kind, (
            f"{language}/{symbol_name}: expected kind={expected_kind}, got {sym.kind}"
        )

    # ------------------------------------------------------------------
    # Qualified-name parametrized tests
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("language,filename,symbol_name,expected_qname", [
        ("python", "sample.py", "get_user", "UserService.get_user"),
        ("python", "sample.py", "delete_user", "UserService.delete_user"),
        ("typescript", "sample.ts", "getUser", "UserService.getUser"),
        ("java", "Sample.java", "getUser", "Sample.getUser"),
        ("cpp", "sample.cpp", "get", "sample.Box.get"),
        ("elixir", "sample.ex", "add", "MyApp.Calculator.add"),
        ("ruby", "sample.rb", "initialize", "User.initialize"),
    ])
    def test_symbol_qualified_name(self, language, filename, symbol_name, expected_qname):
        """Parametrized qualified-name assertion across languages."""
        content, fname = _fixture(language, filename)
        symbols = parse_file(content, fname, language)
        sym = _by_name(symbols, symbol_name)
        assert sym.qualified_name == expected_qname, (
            f"{language}/{symbol_name}: expected qname={expected_qname}, got {sym.qualified_name}"
        )

    # ------------------------------------------------------------------
    # Multi-symbol set checks (keep separate — assert on sets)
    # ------------------------------------------------------------------

    def test_python_symbol_count(self):
        content, fname = _fixture("python", "sample.py")
        symbols = parse_file(content, fname, "python")
        assert len(symbols) >= 5

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

    def test_javascript_method(self):
        content, fname = _fixture("javascript", "sample.js")
        symbols = parse_file(content, fname, "javascript")
        grouped = _kinds(symbols)
        methods = grouped.get("method", [])
        method_names = {m.name for m in methods}
        assert "getUser" in method_names

    def test_typescript_method(self):
        content, fname = _fixture("typescript", "sample.ts")
        symbols = parse_file(content, fname, "typescript")
        get_user = _by_name(symbols, "getUser")
        assert get_user.kind == "method"
        assert "UserService" in get_user.qualified_name

    def test_go_functions(self):
        content, fname = _fixture("go", "sample.go")
        symbols = parse_file(content, fname, "go")
        grouped = _kinds(symbols)
        func_names = {f.name for f in grouped.get("function", [])}
        assert "GetUser" in func_names
        assert "Authenticate" in func_names

    def test_java_methods(self):
        content, fname = _fixture("java", "Sample.java")
        symbols = parse_file(content, fname, "java")
        grouped = _kinds(symbols)
        methods = grouped.get("method", [])
        method_names = {m.name for m in methods}
        assert "getUser" in method_names
        assert "authenticate" in method_names

    def test_csharp_method_qualified_name(self):
        content, fname = _fixture("csharp", "sample.cs")
        symbols = parse_file(content, fname, "csharp")
        method = _by_name(symbols, "GetUser")
        assert method.kind == "method"
        assert "UserService" in method.qualified_name

    def test_arduino_functions(self):
        content, fname = _fixture("arduino", "sample.ino")
        symbols = parse_file(content, fname, "arduino")
        func_names = {s.name for s in symbols if s.kind == "function"}
        assert "setup" in func_names
        assert "loop" in func_names
        assert "readTemperature" in func_names

    def test_vhdl_functions(self):
        content, fname = _fixture("vhdl", "sample.vhd")
        symbols = parse_file(content, fname, "vhdl")
        func_names = {s.name for s in symbols if s.kind == "function"}
        assert "max_val" in func_names
        assert "reset_reg" in func_names
        assert "compute" in func_names

    def test_verilog_functions(self):
        content, fname = _fixture("verilog", "sample.sv")
        symbols = parse_file(content, fname, "verilog")
        func_names = {s.name for s in symbols if s.kind == "function"}
        assert "compute" in func_names
        assert "display_result" in func_names

    def test_c_functions(self):
        content, fname = _fixture("c", "sample.c")
        symbols = parse_file(content, fname, "c")
        grouped = _kinds(symbols)
        func_names = {f.name for f in grouped.get("function", [])}
        assert "get_user" in func_names
        assert "authenticate" in func_names

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

    def test_ruby_singleton_method(self):
        content, fname = _fixture("ruby", "sample.rb")
        symbols = parse_file(content, fname, "ruby")
        find = _by_name(symbols, "find")
        assert find.kind == "method"
        assert find.qualified_name == "User.find"

    # ------------------------------------------------------------------
    # Special / negative / inline-source tests (keep separate)
    # ------------------------------------------------------------------

    def test_rust_impl_block_is_not_a_symbol(self):
        """An `impl` block binds no name a caller can reach.

        ⚠ The assertion is unchanged; the REASON is not. This used to read
        "impl_item is in symbol_node_types but has no name_fields entry, so
        the extractor skips it" -- a description of the mechanism, and of a
        defect: nothing was naming the block, so nothing could qualify its
        members either. `impl_item` is a container now and emits no symbol
        deliberately, which is also what `syn` says an impl block is.
        """
        content, fname = _fixture("rust", "sample.rs")
        symbols = parse_file(content, fname, "rust")
        grouped = _kinds(symbols)
        impl_syms = grouped.get("class", [])
        assert len(impl_syms) == 0

    def test_rust_fn_in_impl_belongs_to_its_type(self):
        """A method in `impl User` is `User.new`, not a free-floating `new`.

        ⚠⚠ This test asserted the OPPOSITE until 2026-08-27 -- `kind ==
        "function"` and `parent is None`, under the docstring "Without the
        impl parent being extracted, 'new' appears as a top-level function
        rather than a method." That is the defect written down as intended
        behaviour, so the test could only pass while the defect existed
        (Practice 9). Measured on ripgrep, it cost 37.9% of symbols a
        distinguishable name.
        """
        content, fname = _fixture("rust", "sample.rs")
        symbols = parse_file(content, fname, "rust")
        new_sym = _by_name(symbols, "new")
        assert new_sym.kind == "method"
        assert new_sym.qualified_name == "User.new"
        assert new_sym.parent is not None

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
            assert len(s.content_hash) == 64, "content_hash hex should be 64-char SHA-256"

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

    @pytest.mark.parametrize("current_files,expect_changed,expect_new,expect_deleted", [
        pytest.param(
            {"hello.py": "def hello():\n    return 42\n", "greet.js": "function greet() { return 1; }\n"},
            ["hello.py"], [], [],
            id="changed_file",
        ),
        pytest.param(
            {"hello.py": "def hello():\n    pass\n", "greet.js": "function greet() { return 1; }\n", "extra.py": "x = 1\n"},
            [], ["extra.py"], [],
            id="new_file",
        ),
        pytest.param(
            {"hello.py": "def hello():\n    pass\n"},
            [], [], ["greet.js"],
            id="deleted_file",
        ),
    ], ids=["changed", "new", "deleted"])
    def test_detect_changes(self, tmp_path, current_files, expect_changed, expect_new, expect_deleted):
        store = self._make_index(tmp_path)
        changed, new, deleted = store.detect_changes("test", "repo", current_files)
        assert set(changed) == set(expect_changed), f"changed: {changed} != {expect_changed}"
        assert set(new) == set(expect_new), f"new: {new} != {expect_new}"
        assert set(deleted) == set(expect_deleted), f"deleted: {deleted} != {expect_deleted}"

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
        assert index.index_version == 17

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

        # Manually bump the version in the SQLite database to a future version
        db_path = store._sqlite._db_path("ver", "test")
        conn = store._sqlite._connect(db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("index_version", str(INDEX_VERSION + 100)),
            )
        finally:
            conn.close()

        # Evict cache — direct DB edit bypasses save_index's cache update
        from jcodemunch_mcp.storage.sqlite_store import _cache_evict
        _cache_evict("ver", "test")

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

    @pytest.mark.parametrize("query,expect_count", [
        ("abc123", 1),          # found in app.py
        ("SECRET_KEY", 1),       # case-insensitive match
        ("nonexistent_xyz", 0), # not found
    ])
    def test_search_text_basic(self, tmp_path, query, expect_count):
        from jcodemunch_mcp.tools.search_text import search_text

        storage = self._seed_index(tmp_path)
        result = search_text(
            repo="tools/demo",
            query=query,
            storage_path=storage,
        )
        assert "error" not in result
        assert result["result_count"] == expect_count
        if expect_count > 0:
            files_found = {m["file"] for m in result["results"]}
            assert "app.py" in files_found

    @pytest.mark.parametrize("query,should_error,error_substr", [
        ("(a+)+b",  True,  "nested quantifier"),   # ReDoS: nested quantifier
        ("a" * 201, True,  "too long"),            # ReDoS: regex too long
        ("greet|add", False, ""),                   # safe regex: allowed
    ])
    def test_search_text_regex_safety(self, tmp_path, query, should_error, error_substr):
        from jcodemunch_mcp.tools.search_text import search_text

        storage = self._seed_index(tmp_path)
        result = search_text(
            repo="tools/demo",
            query=query,
            is_regex=True,
            storage_path=storage,
        )
        if should_error:
            assert "error" in result
            assert error_substr in result["error"].lower()
        else:
            assert "error" not in result
            assert result["result_count"] >= 1

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

    @pytest.mark.parametrize("repo,expect_success,error_contains", [
        ("tools/demo",  True,  None),                       # deletes index
        ("ghost/repo",  False, None),                       # missing repo
        ("shared",      False, "Ambiguous repository name"), # ambiguous bare name
    ], ids=["deletes_index", "missing_repo", "ambiguous_bare_name"])
    def test_invalidate_cache(self, tmp_path, repo, expect_success, error_contains):
        from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

        if repo == "tools/demo":
            storage = self._seed_index(tmp_path)
        elif repo == "ghost/repo":
            storage = str(tmp_path / "empty")
        else:  # ambiguous bare name
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

        result = invalidate_cache(repo=repo, storage_path=storage)
        actual_success = result.get("success")
        assert actual_success is expect_success or (not expect_success and "error" in result), (
            f"got: {result}"
        )
        if error_contains:
            assert result.get("error", "").startswith(error_contains)

        if expect_success and repo == "tools/demo":
            store = IndexStore(base_path=storage)
            loaded = store.load_index("tools", "demo")
            assert loaded is None


# ---------------------------------------------------------------------------
# S4 — Timing-safe bearer token comparison (T1)
# ---------------------------------------------------------------------------

class TestBearerAuthTimingSafe:
    """T1: BearerAuthMiddleware must use hmac.compare_digest, never plain ==."""

    def test_hmac_compare_digest_called_on_valid_token(self):
        """hmac.compare_digest is invoked for every auth check (not string ==)."""
        import hmac
        from unittest.mock import patch, AsyncMock, MagicMock
        import os

        with patch.dict(os.environ, {"JCODEMUNCH_HTTP_TOKEN": "secret"}):
            from jcodemunch_mcp.server import _make_auth_middleware
            middleware_wrapper = _make_auth_middleware()

        assert middleware_wrapper is not None

        # Instantiate the middleware class and verify compare_digest is used
        middleware_cls = middleware_wrapper.cls
        instance = middleware_cls(app=MagicMock())

        request = MagicMock()
        request.headers.get.return_value = "Bearer secret"
        call_next = AsyncMock(return_value=MagicMock())

        with patch("hmac.compare_digest", wraps=hmac.compare_digest) as mock_cd:
            import asyncio
            asyncio.run(instance.dispatch(request, call_next))
            mock_cd.assert_called_once()

    def test_wrong_token_returns_401(self):
        """A mismatched token yields a 401 response, not a 200."""
        import os
        from unittest.mock import MagicMock, AsyncMock, patch
        from starlette.responses import JSONResponse

        with patch.dict(os.environ, {"JCODEMUNCH_HTTP_TOKEN": "correct"}):
            from jcodemunch_mcp.server import _make_auth_middleware
            middleware_wrapper = _make_auth_middleware()

        middleware_cls = middleware_wrapper.cls
        instance = middleware_cls(app=MagicMock())

        request = MagicMock()
        request.headers.get.return_value = "Bearer wrong"
        call_next = AsyncMock()

        import asyncio
        response = asyncio.run(instance.dispatch(request, call_next))
        assert isinstance(response, JSONResponse)
        assert response.status_code == 401
        call_next.assert_not_called()


# ---------------------------------------------------------------------------
# S1 — GitHub URL hostname validation (T2)
# ---------------------------------------------------------------------------

class TestParseGithubUrlSecurity:
    """T2: parse_github_url must reject non-github.com hostnames (SSRF guard)."""

    @pytest.mark.parametrize("bad_url", [
        "https://evil.com/owner/repo",
        "https://192.168.1.1/owner/repo",
        "https://github.com.evil.com/owner/repo",
        "file:///etc/passwd",
        "http://localhost/owner/repo",
        "https://github.internal.corp.com/owner/repo",
    ])
    def test_rejects_non_github_hosts(self, bad_url):
        from jcodemunch_mcp.tools.index_repo import parse_github_url
        with pytest.raises(ValueError, match="Unsupported host"):
            parse_github_url(bad_url)

    @pytest.mark.parametrize("good_url,expected", [
        ("jgravelle/jcodemunch-mcp", ("jgravelle", "jcodemunch-mcp")),
        ("https://github.com/jgravelle/jcodemunch-mcp", ("jgravelle", "jcodemunch-mcp")),
        ("https://github.com/jgravelle/jcodemunch-mcp.git", ("jgravelle", "jcodemunch-mcp")),
        ("owner/repo-name.with_dots", ("owner", "repo-name.with_dots")),
    ])
    def test_accepts_valid_github_urls(self, good_url, expected):
        from jcodemunch_mcp.tools.index_repo import parse_github_url
        assert parse_github_url(good_url) == expected

    @pytest.mark.parametrize("bad_slug", [
        "owner/repo;rm -rf /",
        "../../../etc/owner/repo",
        "owner/repo\x00evil",
    ])
    def test_rejects_invalid_slugs(self, bad_slug):
        from jcodemunch_mcp.tools.index_repo import parse_github_url
        with pytest.raises(ValueError):
            parse_github_url(bad_slug)


# ---------------------------------------------------------------------------
# T5 — Windows gitignore case-normalisation (_is_gitignored_fast)
# ---------------------------------------------------------------------------

class TestIsGitignoredfastCaseNormalization:
    """_is_gitignored_fast must match gitignore patterns case-insensitively on Windows."""

    def _make_specs(self, dir_prefix: str, patterns: list[str]):
        """Build a gitignore_str_specs list with a single spec."""
        import pathspec
        spec = pathspec.PathSpec.from_lines("gitignore", patterns)
        return [(dir_prefix, spec)]

    def test_exact_match(self, tmp_path):
        """Standard case — dir_prefix and resolved_str share the same casing."""
        import os
        from jcodemunch_mcp.tools.index_folder import _is_gitignored_fast
        prefix = str(tmp_path) + os.sep
        specs = self._make_specs(prefix, ["*.pyc"])
        assert _is_gitignored_fast(str(tmp_path / "foo.pyc"), specs) is True
        assert _is_gitignored_fast(str(tmp_path / "foo.py"), specs) is False

    @pytest.mark.skipif(
        __import__("os").path.normcase("A") == "A",
        reason="case normalization is only relevant on Windows",
    )
    def test_case_mismatch_still_matches(self, tmp_path):
        """If the dir_prefix has different casing than the resolved path, it must still match."""
        import os
        from jcodemunch_mcp.tools.index_folder import _is_gitignored_fast
        # Simulate Windows path where dir_prefix was stored with different case
        prefix_upper = str(tmp_path).upper() + os.sep
        prefix_lower = str(tmp_path).lower() + os.sep
        specs_upper = self._make_specs(prefix_upper, ["*.log"])
        specs_lower = self._make_specs(prefix_lower, ["*.log"])

        file_str = str(tmp_path / "app.log")
        # Both prefix casings should match the file regardless
        assert _is_gitignored_fast(file_str, specs_upper) is True
        assert _is_gitignored_fast(file_str, specs_lower) is True

    def test_non_child_path_not_matched(self, tmp_path):
        """Files outside the gitignore directory must not be matched."""
        import os
        from jcodemunch_mcp.tools.index_folder import _is_gitignored_fast
        other = tmp_path / "other_project"
        other.mkdir()
        prefix = str(tmp_path / "project") + os.sep
        specs = self._make_specs(prefix, ["*.log"])
        assert _is_gitignored_fast(str(other / "app.log"), specs) is False


# ---------------------------------------------------------------------------
# S9 — Rate-limiting middleware (T6)
# ---------------------------------------------------------------------------

class TestRateLimitMiddleware:
    """T6: _make_rate_limit_middleware enforces per-IP request caps."""

    def test_returns_none_when_disabled(self):
        """JCODEMUNCH_RATE_LIMIT unset or 0 must return None (no middleware)."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JCODEMUNCH_RATE_LIMIT", None)
            from jcodemunch_mcp.server import _make_rate_limit_middleware
            assert _make_rate_limit_middleware() is None

        with patch.dict(os.environ, {"JCODEMUNCH_RATE_LIMIT": "0"}):
            assert _make_rate_limit_middleware() is None

    def test_allows_requests_within_limit(self):
        """Requests up to the per-minute limit must pass through (200)."""
        import os
        import asyncio
        from unittest.mock import MagicMock, AsyncMock, patch

        with patch.dict(os.environ, {"JCODEMUNCH_RATE_LIMIT": "3"}):
            from jcodemunch_mcp.server import _make_rate_limit_middleware
            mw_wrapper = _make_rate_limit_middleware()

        assert mw_wrapper is not None
        instance = mw_wrapper.cls(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        def make_request(ip="127.0.0.1"):
            req = MagicMock()
            req.client.host = ip
            return req

        loop = asyncio.new_event_loop()
        try:
            for _ in range(3):
                resp = loop.run_until_complete(instance.dispatch(make_request(), call_next))
                assert resp.status_code == 200
        finally:
            loop.close()

    def test_blocks_request_over_limit(self):
        """The (limit+1)-th request from the same IP must return 429."""
        import os
        import asyncio
        from unittest.mock import MagicMock, AsyncMock, patch
        from starlette.responses import JSONResponse

        with patch.dict(os.environ, {"JCODEMUNCH_RATE_LIMIT": "2"}):
            from jcodemunch_mcp.server import _make_rate_limit_middleware
            mw_wrapper = _make_rate_limit_middleware()

        instance = mw_wrapper.cls(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        def make_request(ip="10.0.0.1"):
            req = MagicMock()
            req.client.host = ip
            return req

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(instance.dispatch(make_request(), call_next))
            loop.run_until_complete(instance.dispatch(make_request(), call_next))
            response = loop.run_until_complete(instance.dispatch(make_request(), call_next))
        finally:
            loop.close()

        assert isinstance(response, JSONResponse)
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_different_ips_are_independent(self):
        """Rate limit buckets are per-IP — a second IP must not be blocked."""
        import os
        import asyncio
        from unittest.mock import MagicMock, AsyncMock, patch
        from starlette.responses import JSONResponse

        with patch.dict(os.environ, {"JCODEMUNCH_RATE_LIMIT": "1"}):
            from jcodemunch_mcp.server import _make_rate_limit_middleware
            mw_wrapper = _make_rate_limit_middleware()

        instance = mw_wrapper.cls(app=MagicMock())
        ok_response = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=ok_response)

        def make_request(ip):
            req = MagicMock()
            req.client.host = ip
            return req

        loop = asyncio.new_event_loop()
        try:
            # Exhaust IP A's bucket
            loop.run_until_complete(instance.dispatch(make_request("192.168.1.1"), call_next))
            blocked = loop.run_until_complete(instance.dispatch(make_request("192.168.1.1"), call_next))
            # IP B is still fresh
            allowed = loop.run_until_complete(instance.dispatch(make_request("192.168.1.2"), call_next))
        finally:
            loop.close()

        assert isinstance(blocked, JSONResponse)
        assert blocked.status_code == 429
        assert allowed.status_code == 200


def test_ci_probe_deliberate_failure():
    assert 1 == 2, "deliberate failure to prove stage 2 blocks"
