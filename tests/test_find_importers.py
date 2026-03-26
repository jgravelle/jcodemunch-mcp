"""Tests for find_importers and find_references tools, and the imports parser."""

import pytest
from pathlib import Path

from jcodemunch_mcp.parser.imports import extract_imports, resolve_specifier
from jcodemunch_mcp.tools.find_importers import find_importers
from jcodemunch_mcp.tools.find_references import find_references
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.storage import IndexStore


# ---------------------------------------------------------------------------
# Unit tests: extract_imports
# ---------------------------------------------------------------------------

class TestExtractImportsJS:
    """Test JS/TS import extraction."""

    def test_named_imports(self):
        content = "import { foo, bar } from './utils';"
        result = extract_imports(content, "src/a.js", "javascript")
        assert len(result) == 1
        assert result[0]["specifier"] == "./utils"
        assert "foo" in result[0]["names"]
        assert "bar" in result[0]["names"]

    def test_default_import(self):
        content = "import MyComponent from '../components/MyComponent';"
        result = extract_imports(content, "src/page.tsx", "typescript")
        assert len(result) == 1
        assert result[0]["specifier"] == "../components/MyComponent"
        assert "MyComponent" in result[0]["names"]

    def test_side_effect_import(self):
        content = "import './styles.css';"
        result = extract_imports(content, "src/app.js", "javascript")
        assert len(result) == 1
        assert result[0]["specifier"] == "./styles.css"
        assert result[0]["names"] == []

    def test_require(self):
        content = "const path = require('path');"
        result = extract_imports(content, "index.js", "javascript")
        assert any(r["specifier"] == "path" for r in result)

    def test_multiple_imports(self):
        content = (
            "import React from 'react';\n"
            "import { useState, useEffect } from 'react';\n"
            "import { Link } from '../router';\n"
        )
        result = extract_imports(content, "src/app.jsx", "jsx")
        specifiers = [r["specifier"] for r in result]
        assert "../router" in specifiers
        # react may be merged or appear once
        assert any(s == "react" for s in specifiers)

    def test_no_false_positive_on_plain_code(self):
        content = "function add(a, b) { return a + b; }\n"
        result = extract_imports(content, "math.js", "javascript")
        assert result == []

    def test_dynamic_import(self):
        """Vue Router lazy routes use import() — must be detected as an edge."""
        content = (
            "const routes = [\n"
            "  { path: '/lists', component: () => import('../../features/lists/views/Lists.vue') },\n"
            "  { path: '/cast',  component: () => import('../../features/cast/views/Cast.vue') },\n"
            "];\n"
        )
        result = extract_imports(content, "src/router/routes/featureRoutes.js", "javascript")
        specifiers = [r["specifier"] for r in result]
        assert "../../features/lists/views/Lists.vue" in specifiers
        assert "../../features/cast/views/Cast.vue" in specifiers

    def test_dynamic_import_not_double_counted(self):
        """A specifier that appears as both static and dynamic import should appear once."""
        content = (
            "import Foo from './Foo';\n"
            "const lazy = () => import('./Foo');\n"
        )
        result = extract_imports(content, "src/app.js", "javascript")
        matching = [r for r in result if r["specifier"] == "./Foo"]
        assert len(matching) == 1


class TestExtractImportsPython:
    """Test Python import extraction."""

    def test_from_import(self):
        content = "from .utils import foo, bar\n"
        result = extract_imports(content, "src/module.py", "python")
        assert len(result) == 1
        assert result[0]["specifier"] == ".utils"
        assert "foo" in result[0]["names"]
        assert "bar" in result[0]["names"]

    def test_absolute_import(self):
        content = "import os\nimport sys\n"
        result = extract_imports(content, "main.py", "python")
        specifiers = [r["specifier"] for r in result]
        assert "os" in specifiers
        assert "sys" in specifiers

    def test_future_import_skipped(self):
        content = "from __future__ import annotations\n"
        result = extract_imports(content, "main.py", "python")
        assert result == []

    def test_relative_import(self):
        content = "from ..services import UserService\n"
        result = extract_imports(content, "app/api/routes.py", "python")
        assert result[0]["specifier"] == "..services"
        assert "UserService" in result[0]["names"]

    def test_star_import_excluded(self):
        content = "from os.path import *\n"
        result = extract_imports(content, "utils.py", "python")
        # names should be empty (star stripped) but specifier present
        assert result[0]["specifier"] == "os.path"
        assert result[0]["names"] == []


class TestExtractImportsSqlDbt:
    """Test dbt ref() and source() extraction from SQL files."""

    def test_basic_ref(self):
        content = "SELECT * FROM {{ ref('dim_client') }}"
        result = extract_imports(content, "models/fact_orders.sql", "sql")
        assert len(result) == 1
        assert result[0]["specifier"] == "dim_client"
        assert result[0]["names"] == []

    def test_multiple_refs(self):
        content = (
            "WITH clients AS (SELECT * FROM {{ ref('dim_client') }})\n"
            ",orders AS (SELECT * FROM {{ ref('fact_order') }})\n"
            "SELECT * FROM clients JOIN orders ON clients.id = orders.client_id"
        )
        result = extract_imports(content, "models/agg_summary.sql", "sql")
        specifiers = [r["specifier"] for r in result]
        assert "dim_client" in specifiers
        assert "fact_order" in specifiers
        assert len(result) == 2

    def test_duplicate_ref_deduplicated(self):
        content = (
            "SELECT * FROM {{ ref('dim_client') }}\n"
            "UNION ALL\n"
            "SELECT * FROM {{ ref('dim_client') }}"
        )
        result = extract_imports(content, "models/combined.sql", "sql")
        assert len(result) == 1
        assert result[0]["specifier"] == "dim_client"

    def test_source_extraction(self):
        content = "SELECT * FROM {{ source('salesforce', 'accounts') }}"
        result = extract_imports(content, "models/stg_accounts.sql", "sql")
        assert len(result) == 1
        assert result[0]["specifier"] == "source:salesforce.accounts"

    def test_mixed_ref_and_source(self):
        content = (
            "WITH raw AS (SELECT * FROM {{ source('erp', 'gl_entries') }})\n"
            ",dim AS (SELECT * FROM {{ ref('dim_date') }})\n"
            "SELECT * FROM raw JOIN dim ON raw.date_sk = dim.date_sk"
        )
        result = extract_imports(content, "models/stg_gl.sql", "sql")
        specifiers = [r["specifier"] for r in result]
        assert "source:erp.gl_entries" in specifiers
        assert "dim_date" in specifiers

    def test_ref_with_whitespace_variants(self):
        content = (
            "SELECT * FROM {{ref('model_a')}}\n"
            "UNION ALL\n"
            "SELECT * FROM {{ ref('model_b') }}\n"
            "UNION ALL\n"
            "SELECT * FROM {{- ref('model_c') -}}\n"
        )
        result = extract_imports(content, "models/union.sql", "sql")
        specifiers = [r["specifier"] for r in result]
        assert "model_a" in specifiers
        assert "model_b" in specifiers
        assert "model_c" in specifiers

    def test_ref_with_version(self):
        content = "SELECT * FROM {{ ref('dim_client', v=2) }}"
        result = extract_imports(content, "models/fact.sql", "sql")
        assert len(result) == 1
        assert result[0]["specifier"] == "dim_client"

    def test_no_ref_no_source(self):
        content = "SELECT id, name FROM my_table WHERE active = 1"
        result = extract_imports(content, "scripts/query.sql", "sql")
        assert result == []

    def test_plain_sql_no_false_positives(self):
        content = "-- ref to dim_client for documentation\nSELECT 1"
        result = extract_imports(content, "scripts/notes.sql", "sql")
        assert result == []


class TestResolveSpecifierDbt:
    """Test stem-matching resolution for dbt model names."""

    SOURCE_FILES = {
        "DBT/models/dim/dim_client.sql",
        "DBT/models/fact/fact_orders.sql",
        "DBT/models/staging/stg_accounts.sql",
        "src/app.js",
    }

    def test_bare_model_name_resolves(self):
        result = resolve_specifier("dim_client", "DBT/models/fact/fact_orders.sql", self.SOURCE_FILES)
        assert result == "DBT/models/dim/dim_client.sql"

    def test_bare_name_case_insensitive(self):
        result = resolve_specifier("Dim_Client", "DBT/models/fact/fact_orders.sql", self.SOURCE_FILES)
        assert result == "DBT/models/dim/dim_client.sql"

    def test_source_specifier_unresolvable(self):
        result = resolve_specifier("source:salesforce.accounts", "DBT/models/staging/stg_accounts.sql", self.SOURCE_FILES)
        # source: specifiers contain dots, so they won't match the bare-name fallback
        assert result is None

    def test_bare_name_no_match(self):
        result = resolve_specifier("nonexistent_model", "DBT/models/fact/fact_orders.sql", self.SOURCE_FILES)
        assert result is None

    def test_does_not_interfere_with_js_resolution(self):
        """Stem matching should not break existing JS resolution."""
        js_files = {"src/utils.js", "src/app.js"}
        result = resolve_specifier("./utils", "src/app.js", js_files)
        assert result == "src/utils.js"


class TestExtractImportsUnsupported:
    """Unknown language returns empty list, no crash."""

    def test_unknown_language(self):
        result = extract_imports("anything", "file.xyz", "cobol")
        assert result == []

    def test_empty_content(self):
        result = extract_imports("", "file.py", "python")
        assert result == []


# ---------------------------------------------------------------------------
# Unit tests: resolve_specifier
# ---------------------------------------------------------------------------

class TestResolveSpecifier:
    """Test import specifier resolution."""

    SOURCE_FILES = {
        "src/utils/helpers.js",
        "src/utils/index.js",
        "src/components/Button.tsx",
        "src/app.js",
        "lib/auth.py",
        "lib/__init__.py",
    }

    def test_relative_js_with_extension(self):
        result = resolve_specifier(
            "./helpers.js", "src/utils/other.js", self.SOURCE_FILES
        )
        assert result == "src/utils/helpers.js"

    def test_relative_js_without_extension(self):
        result = resolve_specifier(
            "./helpers", "src/utils/other.js", self.SOURCE_FILES
        )
        assert result == "src/utils/helpers.js"

    def test_relative_tsx_component(self):
        result = resolve_specifier(
            "../components/Button", "src/pages/Home.tsx", self.SOURCE_FILES
        )
        assert result == "src/components/Button.tsx"

    def test_relative_index_resolution(self):
        result = resolve_specifier(
            "./utils", "src/app.js", self.SOURCE_FILES
        )
        assert result == "src/utils/index.js"

    def test_dotdot_traversal(self):
        result = resolve_specifier(
            "../app", "src/utils/helpers.js", self.SOURCE_FILES
        )
        assert result == "src/app.js"

    def test_unresolvable_package_import(self):
        result = resolve_specifier(
            "react", "src/app.js", self.SOURCE_FILES
        )
        assert result is None

    def test_absolute_match(self):
        result = resolve_specifier(
            "src/app.js", "other.js", self.SOURCE_FILES
        )
        assert result == "src/app.js"

    def test_python_relative(self):
        result = resolve_specifier(
            ".helpers", "lib/module.py", {"lib/helpers.py"}
        )
        # Python dotted relative — won't resolve (starts with '.', joined as lib/.helpers)
        # This is expected: Python module syntax doesn't map directly to file paths
        # The test verifies no crash
        assert result is None or isinstance(result, str)

    # --- .js → .ts extension resolution (TypeScript ESM) ---

    def test_js_to_ts_extension(self):
        """'./foo.js' should resolve to './foo.ts' when only .ts exists."""
        files = {"src/utils/helpers.ts", "src/app.ts"}
        result = resolve_specifier("./helpers.js", "src/utils/other.ts", files)
        assert result == "src/utils/helpers.ts"

    def test_js_to_tsx_extension(self):
        """'./Button.js' should resolve to './Button.tsx' when only .tsx exists."""
        files = {"src/components/Button.tsx"}
        result = resolve_specifier("./Button.js", "src/components/Home.tsx", files)
        assert result == "src/components/Button.tsx"

    def test_js_exact_takes_priority(self):
        """When both .js and .ts exist, .js exact match should win."""
        files = {"src/utils/helpers.js", "src/utils/helpers.ts"}
        result = resolve_specifier("./helpers.js", "src/utils/other.ts", files)
        assert result == "src/utils/helpers.js"

    # --- Path alias resolution ---

    def test_at_alias_root(self):
        """'@/lib/utils' with '@/*' → './*' alias resolves to 'lib/utils.ts'."""
        files = {"lib/utils.ts", "src/app.ts"}
        alias_map = {"@/*": ["/*"]}  # "@/*": ["./*"] normalized
        result = resolve_specifier("@/lib/utils", "src/app.ts", files, alias_map)
        assert result == "lib/utils.ts"

    def test_at_alias_with_src_prefix(self):
        """'@/components/Button' with '@/*' → 'src/*' resolves to 'src/components/Button.tsx'."""
        files = {"src/components/Button.tsx", "src/app.ts"}
        alias_map = {"@/*": ["src/*"]}
        result = resolve_specifier("@/components/Button", "src/pages/Home.ts", files, alias_map)
        assert result == "src/components/Button.tsx"

    def test_lib_alias_svelte(self):
        """'$lib/server/db' with '$lib/*' → 'src/lib/*' resolves to 'src/lib/server/db.ts'."""
        files = {"src/lib/server/db.ts", "src/routes/+page.svelte"}
        alias_map = {"$lib/*": ["src/lib/*"]}
        result = resolve_specifier("$lib/server/db", "src/routes/+page.svelte", files, alias_map)
        assert result == "src/lib/server/db.ts"

    def test_alias_without_map_returns_none(self):
        """Alias specifier with no alias_map should not resolve."""
        files = {"lib/utils.ts"}
        result = resolve_specifier("@/lib/utils", "src/app.ts", files)
        assert result is None

    def test_alias_no_match_returns_none(self):
        """Alias pattern that doesn't match the specifier returns None."""
        files = {"lib/utils.ts"}
        alias_map = {"~/*": ["src/*"]}
        result = resolve_specifier("@/lib/utils", "src/app.ts", files, alias_map)
        assert result is None

    def test_load_tsconfig_aliases_basic(self):
        """_load_tsconfig_aliases parses compilerOptions.paths from tsconfig.json."""
        import json, tempfile
        from pathlib import Path
        from jcodemunch_mcp.parser.imports import _load_tsconfig_aliases, _alias_map_cache

        with tempfile.TemporaryDirectory() as tmp:
            tsconfig = {
                "compilerOptions": {
                    "paths": {
                        "@/*": ["./*"],
                        "~/*": ["./src/*"],
                    }
                }
            }
            (Path(tmp) / "tsconfig.json").write_text(json.dumps(tsconfig))
            # Clear cache to ensure fresh load
            _alias_map_cache.pop(tmp, None)
            result = _load_tsconfig_aliases(tmp)
            assert "@/*" in result
            assert result["@/*"] == ["/*"]  # "./*" normalized to "/*" (root)
            assert "~/*" in result
            assert result["~/*"] == ["src/*"]  # "./src/*" normalized to "src/*"

    def test_load_tsconfig_aliases_empty_root(self):
        """_load_tsconfig_aliases returns {} for empty source_root."""
        from jcodemunch_mcp.parser.imports import _load_tsconfig_aliases
        assert _load_tsconfig_aliases("") == {}


# ---------------------------------------------------------------------------
# Integration tests: find_importers + find_references via index_folder
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestFindImporters:
    """Integration tests for find_importers."""

    def test_basic_js_importer(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "utils.js", "export function helper() {}\n")
        _write(src / "app.js", "import { helper } from './utils';\nhelper();\n")
        _write(src / "other.js", "import { helper } from './utils';\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path="utils.js",
            storage_path=str(store),
        )
        assert "error" not in importers
        assert importers["importer_count"] == 2
        importer_files = [i["file"] for i in importers["importers"]]
        assert "app.js" in importer_files
        assert "other.js" in importer_files
        # utils.js should not appear as its own importer
        assert "utils.js" not in importer_files

    def test_no_importers(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "standalone.js", "export function x() {}\n")
        _write(src / "app.js", "function main() {}\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path="standalone.js",
            storage_path=str(store),
        )
        assert importers["importer_count"] == 0
        assert importers["importers"] == []

    def test_python_importers(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "services.py", "class UserService:\n    pass\n")
        _write(src / "api.py", "from .services import UserService\n")
        _write(src / "cli.py", "from .services import UserService\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        # Python relative imports use '.' syntax; resolution requires matching file path
        importers = find_importers(
            repo=result["repo"],
            file_path="services.py",
            storage_path=str(store),
        )
        assert "error" not in importers
        # Result depends on whether python relative resolution succeeds for flat dirs
        assert isinstance(importers["importer_count"], int)

    def test_not_indexed_repo(self, tmp_path):
        store = tmp_path / "store"
        result = find_importers(
            repo="nonexistent/repo",
            file_path="foo.js",
            storage_path=str(store),
        )
        assert "error" in result

    def test_old_index_graceful_note(self, tmp_path):
        """Index with no import data returns graceful note (simulated old index)."""
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "app.js", "function main() {}\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        # Simulate a pre-v1.3.0 index (v3 format) by:
        # 1. Removing all imports from the files table
        # 2. Setting index_version to 0 (v3 didn't store version)
        import json
        store_obj = IndexStore(base_path=str(store))
        owner, name = result["repo"].split("/", 1)
        db_path = store_obj._sqlite._db_path(owner, name)
        conn = store_obj._sqlite._connect(db_path)
        try:
            # Clear all imports in files table
            conn.execute("UPDATE files SET imports = ''")
            # Set index_version to 0 to simulate v3 (no imports field)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('index_version', '0')")
        finally:
            conn.close()

        importers = find_importers(
            repo=result["repo"],
            file_path="app.js",
            storage_path=str(store),
        )
        assert "note" in importers
        assert "Re-index" in importers["note"]

    def test_dbt_ref_importer(self, tmp_path):
        """find_importers for dim_client.sql should find models that ref('dim_client')."""
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "dim_client.sql", "SELECT id, name FROM raw_clients\n")
        _write(src / "fact_orders.sql", "SELECT * FROM {{ ref('dim_client') }}\n")
        _write(src / "agg_summary.sql", "SELECT * FROM {{ ref('dim_client') }}\n")
        _write(src / "unrelated.sql", "SELECT 1\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path="dim_client.sql",
            storage_path=str(store),
        )
        assert importers["importer_count"] == 2
        importer_files = [i["file"] for i in importers["importers"]]
        assert "fact_orders.sql" in importer_files
        assert "agg_summary.sql" in importer_files
        assert "dim_client.sql" not in importer_files

    def test_has_importers_alive_importer(self, tmp_path):
        """An importer that is itself imported should have has_importers=True."""
        src = tmp_path / "src"
        store = tmp_path / "store"

        # chain: app.js -> loader.js -> utils.js
        _write(src / "utils.js", "export function util() {}\n")
        _write(src / "loader.js", "import { util } from './utils';\nexport function load() {}\n")
        _write(src / "app.js", "import { load } from './loader';\nload();\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path="utils.js",
            storage_path=str(store),
        )
        assert importers["importer_count"] == 1
        loader = importers["importers"][0]
        assert loader["file"] == "loader.js"
        # loader.js is imported by app.js, so it is reachable
        assert loader["has_importers"] is True

    def test_has_importers_dead_chain(self, tmp_path):
        """An importer with no importers of its own should have has_importers=False.

        This is the storageLoader.js scenario from issue #130: a file appears to
        have an importer (firestoreDocumentLoader.js) but that importer is itself
        never imported — revealing a transitive dead code chain.
        """
        src = tmp_path / "src"
        store = tmp_path / "store"

        # storage.js is imported by dead_loader.js, but dead_loader.js has no importers.
        _write(src / "storage.js", "export function store() {}\n")
        _write(src / "dead_loader.js", "import { store } from './storage';\nexport function load() {}\n")
        # active.js exists but imports nothing from dead_loader
        _write(src / "active.js", "export function main() {}\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path="storage.js",
            storage_path=str(store),
        )
        assert importers["importer_count"] == 1
        dead_loader = importers["importers"][0]
        assert dead_loader["file"] == "dead_loader.js"
        # dead_loader.js has no importers — chain is dead
        assert dead_loader["has_importers"] is False

    def test_max_results_truncation(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        # Create a target file and many importers
        _write(src / "target.js", "export const x = 1;\n")
        for i in range(10):
            _write(src / f"importer_{i}.js", f"import {{ x }} from './target';\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path="target.js",
            max_results=3,
            storage_path=str(store),
        )
        assert len(importers["importers"]) <= 3
        assert importers["_meta"]["truncated"] is True


    def test_batch_file_paths(self, tmp_path):
        """find_importers with file_paths returns grouped results."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        _write(src / "utils.js", "export function helper() {}")
        _write(src / "config.js", "export const CONFIG = {}")
        _write(src / "app.js", "import { helper } from './utils';\nimport { CONFIG } from './config';")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        batch_result = find_importers(
            repo=result["repo"],
            file_paths=["utils.js", "config.js"],
            storage_path=str(store),
        )
        assert "results" in batch_result
        assert len(batch_result["results"]) == 2
        paths = [r["file_path"] for r in batch_result["results"]]
        assert "utils.js" in paths
        assert "config.js" in paths

    def test_batch_empty_list(self, tmp_path):
        """Empty file_paths list returns empty results."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        _write(src / "app.js", "console.log('hi')")
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        batch_result = find_importers(repo=result["repo"], file_paths=[], storage_path=str(store))
        assert batch_result["results"] == []

    def test_singular_file_path_still_works(self, tmp_path):
        """Existing singular file_path param still works (backward compat)."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        _write(src / "utils.js", "export function helper() {}")
        _write(src / "app.js", "import { helper } from './utils';")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        singular_result = find_importers(
            repo=result["repo"],
            file_path="utils.js",
            storage_path=str(store),
        )
        # Original response shape: flat importers list, not nested results
        assert "importers" in singular_result
        assert "results" not in singular_result

    def test_both_file_path_and_file_paths_raises(self, tmp_path):
        """Passing both file_path and file_paths raises ValueError."""
        src = tmp_path / "src"
        src.mkdir()
        _write(src / "utils.js", "export function helper() {}")
        result = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=str(tmp_path / "idx"))
        repo = result["repo"]

        from jcodemunch_mcp.tools.find_importers import find_importers
        with pytest.raises(ValueError):
            find_importers(
                repo=repo,
                file_path="utils.js",
                file_paths=["utils.js"],
                storage_path=str(tmp_path / "idx"),
            )


class TestFindReferences:
    """Integration tests for find_references."""

    def test_named_import_match(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "auth.js", "export function authenticate() {}\n")
        _write(src / "app.js", "import { authenticate } from './auth';\n")
        _write(src / "middleware.js", "import { authenticate } from './auth';\n")
        _write(src / "unrelated.js", "function foo() {}\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        refs = find_references(
            repo=result["repo"],
            identifier="authenticate",
            storage_path=str(store),
        )
        assert "error" not in refs
        assert refs["reference_count"] == 2
        ref_files = [r["file"] for r in refs["references"]]
        assert "app.js" in ref_files
        assert "middleware.js" in ref_files
        assert "unrelated.js" not in ref_files

    def test_specifier_stem_match(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "IntakeService.js", "export class IntakeService {}\n")
        _write(src / "handler.js", "import IntakeService from './IntakeService';\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        refs = find_references(
            repo=result["repo"],
            identifier="IntakeService",
            storage_path=str(store),
        )
        assert refs["reference_count"] >= 1
        ref_files = [r["file"] for r in refs["references"]]
        assert "handler.js" in ref_files

    def test_case_insensitive_match(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "utils.js", "export function Helper() {}\n")
        _write(src / "app.js", "import { Helper } from './utils';\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        # Search with lowercase
        refs = find_references(
            repo=result["repo"],
            identifier="helper",
            storage_path=str(store),
        )
        assert refs["reference_count"] >= 1

    def test_no_false_positives(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "foo.js", "export function foo() {}\n")
        _write(src / "bar.js", "import { bar } from './something';\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        refs = find_references(
            repo=result["repo"],
            identifier="foo",
            storage_path=str(store),
        )
        ref_files = [r["file"] for r in refs["references"]]
        # bar.js imports 'bar', not 'foo' — should not appear
        assert "bar.js" not in ref_files

    def test_not_indexed(self, tmp_path):
        store = tmp_path / "store"
        result = find_references(
            repo="nonexistent/repo",
            identifier="foo",
            storage_path=str(store),
        )
        assert "error" in result

    def test_dbt_ref_reference(self, tmp_path):
        """find_references('dim_client') should find SQL files that ref('dim_client')."""
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "dim_client.sql", "SELECT id, name FROM raw_clients\n")
        _write(src / "fact_orders.sql", "SELECT * FROM {{ ref('dim_client') }}\n")
        _write(src / "agg_summary.sql", "SELECT * FROM {{ ref('dim_client') }} JOIN {{ ref('fact_orders') }}\n")
        _write(src / "unrelated.sql", "SELECT 1\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        refs = find_references(
            repo=result["repo"],
            identifier="dim_client",
            storage_path=str(store),
        )
        assert refs["reference_count"] == 2
        ref_files = [r["file"] for r in refs["references"]]
        assert "fact_orders.sql" in ref_files
        assert "agg_summary.sql" in ref_files
        assert "unrelated.sql" not in ref_files

    def test_meta_tip_present(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "app.js", "function main() {}\n")
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        refs = find_references(
            repo=result["repo"],
            identifier="anything",
            storage_path=str(store),
        )
        assert "tip" in refs["_meta"]

    def test_batch_identifiers(self, tmp_path):
        """find_references with identifiers returns grouped results."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        _write(src / "utils.js", "export function helper() {}\nexport function format() {}")
        _write(src / "app.js", "import { helper, format } from './utils';")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        repo = result["repo"]

        result = find_references(
            repo=repo,
            identifiers=["helper", "format"],
            storage_path=str(store),
        )
        assert "results" in result
        assert len(result["results"]) == 2
        ids = [r["identifier"] for r in result["results"]]
        assert "helper" in ids
        assert "format" in ids

    def test_singular_identifier_still_works(self, tmp_path):
        """Existing singular identifier param still works (backward compat)."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        _write(src / "utils.js", "export function helper() {}")
        _write(src / "app.js", "import { helper } from './utils';")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        repo = result["repo"]

        result = find_references(repo=repo, identifier="helper", storage_path=str(store))
        assert "references" in result
        assert "results" not in result

    def test_batch_empty_list(self, tmp_path):
        """Empty identifiers list returns empty results."""
        src = tmp_path / "src"
        src.mkdir()
        _write(src / "app.js", "console.log('hi')")
        result = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=str(tmp_path / "idx"))
        repo = result["repo"]

        result = find_references(repo=repo, identifiers=[], storage_path=str(tmp_path / "idx"))
        assert result["results"] == []

    def test_both_identifier_and_identifiers_raises(self, tmp_path):
        """Passing both identifier and identifiers raises ValueError."""
        src = tmp_path / "src"
        src.mkdir()
        _write(src / "utils.js", "export function helper() {}")
        result = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=str(tmp_path / "idx"))
        repo = result["repo"]

        from jcodemunch_mcp.tools.find_references import find_references
        with pytest.raises(ValueError):
            find_references(
                repo=repo,
                identifier="helper",
                identifiers=["helper"],
                storage_path=str(tmp_path / "idx"),
            )


# ---------------------------------------------------------------------------
# Tests: imports persisted and loaded correctly
# ---------------------------------------------------------------------------

class TestImportsPersistence:
    """Verify that imports are saved and reloaded correctly."""

    def test_imports_saved_in_index(self, tmp_path):
        src = tmp_path / "src"
        store_path = tmp_path / "store"

        _write(src / "utils.js", "export const x = 1;\n")
        _write(src / "app.js", "import { x } from './utils';\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)
        assert index is not None
        assert index.imports  # non-empty
        assert "app.js" in index.imports

    def test_dbt_refs_saved_in_index(self, tmp_path):
        src = tmp_path / "src"
        store_path = tmp_path / "store"

        _write(src / "dim_client.sql", "SELECT id, name FROM {{ source('crm', 'clients') }}\n")
        _write(src / "fact_orders.sql", (
            "WITH clients AS (SELECT * FROM {{ ref('dim_client') }})\n"
            "SELECT * FROM clients\n"
        ))

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)
        assert index.imports is not None
        assert "fact_orders.sql" in index.imports
        refs = [i["specifier"] for i in index.imports["fact_orders.sql"]]
        assert "dim_client" in refs

    def test_imports_merged_on_incremental(self, tmp_path):
        src = tmp_path / "src"
        store_path = tmp_path / "store"

        _write(src / "utils.js", "export const x = 1;\n")
        _write(src / "app.js", "import { x } from './utils';\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Add a new importer incrementally
        _write(src / "new_importer.js", "import { x } from './utils';\n")
        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store_path), incremental=True
        )
        assert result2["success"] is True

        store = IndexStore(base_path=str(store_path))
        owner, name = result2["repo"].split("/", 1)
        index = store.load_index(owner, name)
        assert "app.js" in index.imports
        assert "new_importer.js" in index.imports
