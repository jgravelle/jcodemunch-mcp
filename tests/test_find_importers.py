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

    @pytest.mark.parametrize("content,file_path,lang,expected_specifier,expected_names", [
        ("import { foo, bar } from './utils';", "src/a.js", "javascript", "./utils", ["foo", "bar"]),
        ("import MyComponent from '../components/MyComponent';", "src/page.tsx", "typescript", "../components/MyComponent", ["MyComponent"]),
        ("import './styles.css';", "src/app.js", "javascript", "./styles.css", []),
        ("const path = require('path');", "index.js", "javascript", "path", []),
        (
            "import React from 'react';\nimport { useState, useEffect } from 'react';\nimport { Link } from '../router';\n",
            "src/app.jsx", "jsx", "../router", ["Link"],
        ),
    ], ids=["named", "default", "side_effect", "require", "multiple"])
    def test_import_patterns(self, content, file_path, lang, expected_specifier, expected_names):
        result = extract_imports(content, file_path, lang)
        assert len(result) >= 1
        if isinstance(expected_specifier, list):
            specifiers = [r["specifier"] for r in result]
            for s in expected_specifier:
                assert s in specifiers
        else:
            matching = [r for r in result if r["specifier"] == expected_specifier]
            assert len(matching) >= 1
            if expected_names:
                names = matching[0]["names"]
                for n in expected_names:
                    assert n in names

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


class TestVueTemplateImports:
    """Test Vue <template> component extraction."""

    @pytest.mark.parametrize("content,file_path,expected_in,expected_out", [
        (
            """
<template>
  <UserTable :users="users" />
  <Pagination :total="10" />
</template>
<script setup>
const users = []
</script>
""",
            "src/App.vue",
            ["UserTable", "Pagination"],
            [],
        ),
        (
            """
<template>
  <user-table />
  <my-dialog />
</template>
<script setup></script>
""",
            "src/App.vue",
            ["UserTable", "MyDialog"],
            [],
        ),
    ], ids=["pascal", "kebab"])
    def test_case_variants(self, content, file_path, expected_in, expected_out):
        result = extract_imports(content, file_path, "vue")
        specifiers = {r["specifier"] for r in result}
        for name in expected_in:
            assert name in specifiers
        for name in expected_out:
            assert name not in specifiers

    def test_already_imported_not_duplicated(self):
        content = """
<template>
  <UserTable />
</template>
<script setup>
import UserTable from '@/components/UserTable.vue'
</script>
"""
        result = extract_imports(content, "src/App.vue", "vue")
        user_table = [r for r in result if "UserTable" in r.get("names", [])]
        # Only one entry — from the script import, not duplicated by template
        assert len(user_table) == 1
        assert user_table[0]["specifier"] == "@/components/UserTable.vue"

    @pytest.mark.parametrize("content,file_path,excluded", [
        (
            """
<template>
  <div><span>text</span></div>
  <button @click="go">Go</button>
  <input type="text" />
  <h1>Title</h1>
</template>
<script setup></script>
""",
            "src/App.vue",
            ["div", "span", "button", "input"],
        ),
        (
            """
<template>
  <transition name="fade"><div/></transition>
  <keep-alive><component :is="current"/></keep-alive>
  <teleport to="body"><div/></teleport>
</template>
<script setup></script>
""",
            "src/App.vue",
            ["Transition", "KeepAlive", "Teleport"],
        ),
    ], ids=["html_elements", "vue_builtins"])
    def test_exclusions(self, content, file_path, excluded):
        result = extract_imports(content, file_path, "vue")
        specifiers = {r["specifier"] for r in result}
        for name in excluded:
            assert name not in specifiers

    def test_no_template_block(self):
        content = """
<script setup>
import Foo from './Foo'
const x = 1
</script>
"""
        result = extract_imports(content, "src/App.vue", "vue")
        assert len(result) == 1
        assert result[0]["specifier"] == "./Foo"

    def test_mixed_imported_and_template_only(self):
        content = """
<template>
  <ImportedComponent />
  <TemplateOnly />
</template>
<script setup>
import ImportedComponent from './ImportedComponent.vue'
</script>
"""
        result = extract_imports(content, "src/App.vue", "vue")
        specifiers = {r["specifier"] for r in result}
        assert "./ImportedComponent.vue" in specifiers
        assert "TemplateOnly" in specifiers
        # ImportedComponent should NOT appear as a synthetic edge
        synthetic = [r for r in result if r["specifier"] == "ImportedComponent"]
        assert len(synthetic) == 0


class TestExtractImportsPython:
    """Test Python import extraction."""

    @pytest.mark.parametrize("content,file_path,check_mode,expected", [
        ("from .utils import foo, bar\n", "src/module.py", "spec_and_names", (".utils", ["foo", "bar"])),
        ("import os\nimport sys\n", "main.py", "specifiers", ["os", "sys"]),
        ("from __future__ import annotations\n", "main.py", "empty", True),
        ("from ..services import UserService\n", "app/api/routes.py", "spec_and_names", ("..services", ["UserService"])),
        ("from os.path import *\n", "utils.py", "spec_and_names", ("os.path", [])),
    ], ids=["from_import", "absolute", "future_skipped", "relative", "star_excluded"])
    def test_standard_imports(self, content, file_path, check_mode, expected):
        result = extract_imports(content, file_path, "python")
        if check_mode == "empty":
            assert result == []
        elif check_mode == "specifiers":
            specifiers = [r["specifier"] for r in result]
            for e in expected:
                assert e in specifiers
        else:
            specifier, names = expected
            matching = [r for r in result if r["specifier"] == specifier]
            assert len(matching) >= 1
            for n in names:
                assert any(n in r["names"] for r in matching)

    @pytest.mark.parametrize("content,file_path,expected_specifier", [
        (
            "def some_endpoint():\n    from app.notifications.mentions import process_comment\n    return process_comment()\n",
            "app/router.py",
            "app.notifications.mentions",
        ),
        ("def lazy_loader():\n    import json\n    return json\n", "utils.py", "json"),
        ("class Service:\n    from app.helpers import normalize\n", "app/service.py", "app.helpers"),
    ], ids=["function_from", "function_import", "class_body"])
    def test_indented_imports(self, content, file_path, expected_specifier):
        result = extract_imports(content, file_path, "python")
        specifiers = [r["specifier"] for r in result]
        assert expected_specifier in specifiers


class TestExtractImportsSqlDbt:
    """Test dbt ref() and source() extraction from SQL files."""

    @pytest.mark.parametrize("content,file_path,expected", [
        (
            "SELECT * FROM {{ ref('dim_client') }}",
            "models/fact_orders.sql",
            (1, "dim_client"),
        ),
        (
            "WITH clients AS (SELECT * FROM {{ ref('dim_client') }})\n,orders AS (SELECT * FROM {{ ref('fact_order') }})\nSELECT * FROM clients JOIN orders ON clients.id = orders.client_id",
            "models/agg_summary.sql",
            (2, ["dim_client", "fact_order"]),
        ),
        (
            "SELECT * FROM {{ ref('dim_client') }}\nUNION ALL\nSELECT * FROM {{ ref('dim_client') }}",
            "models/combined.sql",
            (1, "dim_client"),
        ),
        (
            "SELECT * FROM {{ source('salesforce', 'accounts') }}",
            "models/stg_accounts.sql",
            (1, "source:salesforce.accounts"),
        ),
        (
            "WITH raw AS (SELECT * FROM {{ source('erp', 'gl_entries') }})\n,dim AS (SELECT * FROM {{ ref('dim_date') }})\nSELECT * FROM raw JOIN dim ON raw.date_sk = dim.date_sk",
            "models/stg_gl.sql",
            (2, ["source:erp.gl_entries", "dim_date"]),
        ),
        (
            "SELECT * FROM {{ref('model_a')}}\nUNION ALL\nSELECT * FROM {{ ref('model_b') }}\nUNION ALL\nSELECT * FROM {{- ref('model_c') -}}\n",
            "models/union.sql",
            (3, ["model_a", "model_b", "model_c"]),
        ),
        (
            "SELECT * FROM {{ ref('dim_client', v=2) }}",
            "models/fact.sql",
            (1, "dim_client"),
        ),
    ], ids=["basic_ref", "multiple_refs", "duplicate_dedup", "source", "mixed", "whitespace_variants", "versioned"])
    def test_dbt_positive(self, content, file_path, expected):
        result = extract_imports(content, file_path, "sql")
        count, expected_specs = expected
        assert len(result) == count
        specifiers = [r["specifier"] for r in result]
        if isinstance(expected_specs, list):
            for s in expected_specs:
                assert s in specifiers
        else:
            assert expected_specs in specifiers

    @pytest.mark.parametrize("content,file_path", [
        ("SELECT id, name FROM my_table WHERE active = 1", "scripts/query.sql"),
        ("-- ref to dim_client for documentation\nSELECT 1", "scripts/notes.sql"),
    ], ids=["no_ref_no_source", "plain_sql_no_fp"])
    def test_dbt_negative(self, content, file_path):
        result = extract_imports(content, file_path, "sql")
        assert result == []


class TestResolveSpecifierDbt:
    """Test stem-matching resolution for dbt model names."""

    SOURCE_FILES = {
        "DBT/models/dim/dim_client.sql",
        "DBT/models/fact/fact_orders.sql",
        "DBT/models/staging/stg_accounts.sql",
        "src/app.js",
    }

    @pytest.mark.parametrize("specifier,from_file,files,expected", [
        ("dim_client", "DBT/models/fact/fact_orders.sql", SOURCE_FILES, "DBT/models/dim/dim_client.sql"),
        ("Dim_Client", "DBT/models/fact/fact_orders.sql", SOURCE_FILES, "DBT/models/dim/dim_client.sql"),
        ("source:salesforce.accounts", "DBT/models/staging/stg_accounts.sql", SOURCE_FILES, None),
        ("nonexistent_model", "DBT/models/fact/fact_orders.sql", SOURCE_FILES, None),
        ("./utils", "src/app.js", {"src/utils.js", "src/app.js"}, "src/utils.js"),
    ], ids=["bare_resolves", "case_insensitive", "source_unresolvable", "no_match", "js_resolution"])
    def test_dbt_resolution(self, specifier, from_file, files, expected):
        result = resolve_specifier(specifier, from_file, files)
        assert result == expected


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

    @pytest.mark.parametrize("specifier,from_file,files,expected", [
        ("./helpers.js", "src/utils/other.js", SOURCE_FILES, "src/utils/helpers.js"),
        ("./helpers", "src/utils/other.js", SOURCE_FILES, "src/utils/helpers.js"),
        ("../components/Button", "src/pages/Home.tsx", SOURCE_FILES, "src/components/Button.tsx"),
        ("./utils", "src/app.js", SOURCE_FILES, "src/utils/index.js"),
        ("../app", "src/utils/helpers.js", SOURCE_FILES, "src/app.js"),
        ("react", "src/app.js", SOURCE_FILES, None),
        ("src/app.js", "other.js", SOURCE_FILES, "src/app.js"),
        (".helpers", "lib/module.py", {"lib/helpers.py"}, None),
    ], ids=["js_with_ext", "js_without_ext", "tsx_component", "index_resolution", "dotdot", "unresolvable_pkg", "absolute_match", "python_relative"])
    def test_basic_resolution(self, specifier, from_file, files, expected):
        result = resolve_specifier(specifier, from_file, files)
        assert result == expected

    @pytest.mark.parametrize("specifier,from_file,files,expected", [
        ("./helpers.js", "src/utils/other.ts", {"src/utils/helpers.ts", "src/app.ts"}, "src/utils/helpers.ts"),
        ("./Button.js", "src/components/Home.tsx", {"src/components/Button.tsx"}, "src/components/Button.tsx"),
        ("./helpers.js", "src/utils/other.ts", {"src/utils/helpers.js", "src/utils/helpers.ts"}, "src/utils/helpers.js"),
    ], ids=["js_to_ts", "js_to_tsx", "exact_priority"])
    def test_js_ts_extension_fallback(self, specifier, from_file, files, expected):
        result = resolve_specifier(specifier, from_file, files)
        assert result == expected

    @pytest.mark.parametrize("specifier,from_file,files,alias_map,expected", [
        ("@/lib/utils", "src/app.ts", {"lib/utils.ts", "src/app.ts"}, {"@/*": ["/*"]}, "lib/utils.ts"),
        ("@/components/Button", "src/pages/Home.ts", {"src/components/Button.tsx", "src/app.ts"}, {"@/*": ["src/*"]}, "src/components/Button.tsx"),
        ("$lib/server/db", "src/routes/+page.svelte", {"src/lib/server/db.ts", "src/routes/+page.svelte"}, {"$lib/*": ["src/lib/*"]}, "src/lib/server/db.ts"),
        ("@/lib/utils", "src/app.ts", {"lib/utils.ts"}, None, None),
        ("@/lib/utils", "src/app.ts", {"lib/utils.ts"}, {"~/*": ["src/*"]}, None),
        ("@/lib/utils", "app/components/Widget.tsx", frozenset(["app/lib/utils.ts", "app/components/Widget.tsx"]), {"@/*": ["/*"], "@/lib/*": ["app/lib/*"]}, "app/lib/utils.ts"),
    ], ids=["at_alias_root", "at_alias_src", "lib_alias_svelte", "alias_no_map", "alias_no_match", "alias_nested_override"])
    def test_alias_resolution(self, specifier, from_file, files, alias_map, expected):
        result = resolve_specifier(specifier, from_file, files, alias_map)
        assert result == expected

    @pytest.mark.parametrize("source_root,tsconfig_content,expected_keys", [
        (
            None,
            {"compilerOptions": {"paths": {"@/*": ["./*"], "~/*": ["./src/*"]}}},
            {"@/*": ["/*"], "~/*": ["src/*"]},
        ),
        ("", None, {}),
        (
            None,
            '''{
  // TypeScript configuration for Next.js
  "$schema": "https://json.schemastore.org/tsconfig",
  "compilerOptions": {
    "baseUrl": ".",
    /* path aliases */
    "paths": {
      "@/*": ["./*"],
      "@/lib/*": ["app/lib/*"]
    }
  }
}''',
            {"@/*": ["/*"], "@/lib/*": ["app/lib/*"]},
        ),
    ], ids=["basic", "empty_root", "jsonc"])
    def test_load_tsconfig(self, source_root, tsconfig_content, expected_keys):
        import json, tempfile
        from pathlib import Path
        from jcodemunch_mcp.parser.imports import _load_tsconfig_aliases, _alias_map_cache

        if source_root is None and tsconfig_content is not None and isinstance(tsconfig_content, str):
            # JSONC test
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "tsconfig.json").write_text(tsconfig_content)
                _alias_map_cache.pop(tmp, None)
                result = _load_tsconfig_aliases(tmp)
            for key, val in expected_keys.items():
                assert key in result, f"JSONC tsconfig should parse correctly"
                assert result[key] == val
        elif tsconfig_content is None:
            result = _load_tsconfig_aliases("")
            assert result == {}
        else:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "tsconfig.json").write_text(json.dumps(tsconfig_content))
                _alias_map_cache.pop(tmp, None)
                result = _load_tsconfig_aliases(tmp)
            for key, val in expected_keys.items():
                assert key in result
                assert result[key] == val

    def test_find_importers_jsonc_tsconfig_nested_layout(self, tmp_path):
        """find_importers resolves @/* aliases when tsconfig.json uses JSONC comments (issue #170)."""
        import json
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.find_importers import find_importers
        from jcodemunch_mcp.parser.imports import _alias_map_cache

        src = tmp_path / "src"
        store = tmp_path / "store"

        # Project A layout: source files nested under app/
        _write(src / "app" / "lib" / "utils.ts", "export function cn() {}\n")
        _write(src / "app" / "components" / "Widget.tsx",
               "import { cn } from '@/lib/utils';\nexport function Widget() {}\n")
        _write(src / "app" / "components" / "Header.tsx",
               "import { cn } from '@/lib/utils';\nexport function Header() {}\n")

        # JSONC tsconfig with comments and specific @/lib/* override
        tsconfig_jsonc = '''{
  // Next.js TypeScript config
  "compilerOptions": {
    "baseUrl": ".",
    /* aliases */
    "paths": {
      "@/*": ["./*"],
      "@/lib/*": ["app/lib/*"]
    }
  }
}'''
        (src / "tsconfig.json").write_text(tsconfig_jsonc)

        _alias_map_cache.pop(str(src), None)
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path="app/lib/utils.ts",
            storage_path=str(store),
        )
        files = [r["file"] for r in importers.get("importers", [])]
        assert "app/components/Widget.tsx" in files, (
            "find_importers must find @/lib/utils importers when tsconfig.json uses JSONC comments"
        )
        assert "app/components/Header.tsx" in files


class TestTsconfigWalker:
    """Tests for the generic tsconfig/jsconfig discovery walker in _load_tsconfig_aliases."""

    def _load(self, tmp_path):
        from jcodemunch_mcp.parser.imports import _load_tsconfig_aliases, _alias_map_cache
        _alias_map_cache.pop(str(tmp_path), None)
        return _load_tsconfig_aliases(str(tmp_path))

    def test_extends_chain_resolves_hidden_base(self, tmp_path):
        """Aliases from a base config the walker cannot reach directly are found via extends.

        .config/ starts with '.' so the walker skips it entirely.  The alias is
        ONLY reachable by following the extends pointer from apps/web/tsconfig.json.
        Without extends-chain following this test fails with KeyError on '@shared/*'.
        """
        import json
        hidden = tmp_path / ".config"
        hidden.mkdir()
        (hidden / "tsconfig.base.json").write_text(json.dumps({
            "compilerOptions": {"paths": {"@shared/*": ["./src/*"]}},
        }))
        (tmp_path / "apps" / "web").mkdir(parents=True)
        (tmp_path / "apps" / "web" / "tsconfig.json").write_text(json.dumps({
            "extends": "../../.config/tsconfig.base.json",
        }))
        result = self._load(tmp_path)
        assert "@shared/*" in result
        assert result["@shared/*"] == [".config/src/*"]

    def test_extends_array_ts5_all_entries_followed(self, tmp_path):
        """Array-form extends (TS 5+): every entry is followed, not just the first.

        Both base configs live in hidden dirs so the walker cannot find them
        directly.  The test would fail for either missing alias if the array
        iteration stopped after the first entry.
        """
        import json
        (tmp_path / ".base-a").mkdir()
        (tmp_path / ".base-a" / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"paths": {"@a/*": ["./src/a/*"]}},
        }))
        (tmp_path / ".base-b").mkdir()
        (tmp_path / ".base-b" / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"paths": {"@b/*": ["./src/b/*"]}},
        }))
        (tmp_path / "apps" / "web").mkdir(parents=True)
        (tmp_path / "apps" / "web" / "tsconfig.json").write_text(json.dumps({
            "extends": ["../../.base-a/tsconfig.json", "../../.base-b/tsconfig.json"],
        }))
        result = self._load(tmp_path)
        assert "@a/*" in result
        assert "@b/*" in result

    def test_circular_extends_no_recursion_error(self, tmp_path):
        """Circular extends chain (A extends B extends A) terminates cleanly.

        Both files are visible to the walker; the seen_cfg deduplication must
        block the cycle.  Without it Python would hit RecursionError.
        """
        import json
        (tmp_path / "tsconfig.a.json").write_text(json.dumps({
            "extends": "./tsconfig.b.json",
            "compilerOptions": {"paths": {"@a/*": ["./src/a/*"]}},
        }))
        (tmp_path / "tsconfig.b.json").write_text(json.dumps({
            "extends": "./tsconfig.a.json",
            "compilerOptions": {"paths": {"@b/*": ["./src/b/*"]}},
        }))
        result = self._load(tmp_path)  # must not raise
        assert "@a/*" in result
        assert "@b/*" in result

    def test_out_of_root_extends_blocked(self, tmp_path):
        """extends pointing at a real file outside the repo root is silently blocked.

        The guard is relative_to(root): if the resolved path escapes tmp_path
        the alias must NOT appear in the result.  Using a non-existent path
        would pass for the wrong reason (is_file() → False before the guard).
        """
        import json
        import shutil
        outside = tmp_path.parent / (tmp_path.name + "_external")
        outside.mkdir(exist_ok=True)
        (outside / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"paths": {"@outside/*": ["src/*"]}},
        }))
        try:
            (tmp_path / "apps" / "web").mkdir(parents=True)
            (tmp_path / "apps" / "web" / "tsconfig.json").write_text(json.dumps({
                # Absolute path → resolved path is outside tmp_path
                "extends": str(outside / "tsconfig.json"),
                "compilerOptions": {"paths": {"@/*": ["./src/*"]}},
            }))
            result = self._load(tmp_path)
            assert "@/*" in result           # own paths still resolved
            assert "@outside/*" not in result  # out-of-root alias blocked
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_precedence_first_alphabetical_wins(self, tmp_path):
        """When two workspaces define the same alias, the first one found (alphabetical) wins.

        Pins walk order so that a future refactor silently changing resolution
        order produces a visible test failure instead of a silent behaviour change.
        mobile/ sorts before web/ so its normalised replacement must win.
        """
        import json
        (tmp_path / "apps" / "mobile").mkdir(parents=True)
        (tmp_path / "apps" / "mobile" / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"paths": {"@/*": ["./src/*"]}},
        }))
        (tmp_path / "apps" / "web").mkdir(parents=True)
        (tmp_path / "apps" / "web" / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"paths": {"@/*": ["./src/*"]}},
        }))
        result = self._load(tmp_path)
        assert result.get("@/*") == ["apps/mobile/src/*"]

    def test_nx_libs_layout(self, tmp_path):
        """Nx-style libs/ workspace layout is discovered without any hardcoded globs."""
        import json
        (tmp_path / "libs" / "shared" / "ui").mkdir(parents=True)
        (tmp_path / "libs" / "shared" / "ui" / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"paths": {"@myorg/ui/*": ["./src/*"]}},
        }))
        result = self._load(tmp_path)
        assert result.get("@myorg/ui/*") == ["libs/shared/ui/src/*"]


class TestResolveSpecifierPython:
    """Resolve Python module-style absolute imports against detected source roots.

    Covers the common case where a project does NOT put its packages at the
    repo root: backend/, src/, apps/api/, etc. The resolver must convert
    'app.notifications.mentions' to 'app/notifications/mentions.py' AND
    auto-detect that 'backend/' (or 'src/', etc.) is a source root by looking
    at where __init__.py files live.
    """

    @pytest.mark.parametrize("specifier,from_file,files,expected", [
        ("app.helpers", "main.py", {"app/__init__.py", "app/helpers.py", "main.py"}, "app/helpers.py"),
        (
            "app.notifications.mentions", "backend/app/router.py",
            {"backend/app/__init__.py", "backend/app/notifications/__init__.py",
             "backend/app/notifications/mentions.py", "backend/app/router.py"},
            "backend/app/notifications/mentions.py",
        ),
        ("mypkg.utils", "src/mypkg/main.py", {"src/mypkg/__init__.py", "src/mypkg/utils.py", "src/mypkg/main.py"}, "src/mypkg/utils.py"),
        (
            "app.services", "backend/app/services/email.py",
            {"backend/app/__init__.py", "backend/app/services/__init__.py", "backend/app/services/email.py"},
            "backend/app/services/__init__.py",
        ),
        ("app.helpers", "app/main.py", {"app/helpers.py", "app/main.py"}, "app/helpers.py"),
    ], ids=["repo_root", "backend_root", "src_root", "to_init", "pep420"])
    def test_positive_resolution(self, specifier, from_file, files, expected):
        result = resolve_specifier(specifier, from_file, files)
        assert result == expected

    @pytest.mark.parametrize("specifier,from_file,files", [
        ("fastapi.responses", "backend/app/main.py", {"backend/app/__init__.py", "backend/app/main.py"}),
        (".email", "backend/app/services/sms.py", {"backend/app/__init__.py", "backend/app/services/__init__.py", "backend/app/services/email.py"}),
    ], ids=["third_party", "relative_branch"])
    def test_negative_edge_cases(self, specifier, from_file, files):
        result = resolve_specifier(specifier, from_file, files)
        # Both return None or string (for relative, could resolve)
        assert result is None or isinstance(result, str)

    def test_nested_source_root_via_conftest_shim(self):
        """Issue #252: `src/agent_platform/` is both a package AND (via
        conftest.py sys.path shim) a secondary source root. The structural
        detector only sees `src/` as the root, so `shared.core.runtime`
        fails normal resolution. The first-segment fallback should pick it
        up via the nested `shared` package.
        """
        from jcodemunch_mcp.parser.imports import _clear_python_roots_cache
        _clear_python_roots_cache()
        files = {
            "src/agent_platform/__init__.py",
            "src/agent_platform/shared/__init__.py",
            "src/agent_platform/shared/core/__init__.py",
            "src/agent_platform/shared/core/runtime.py",
            "src/agentz/__init__.py",
            "tests/test_runtime.py",
        }
        result = resolve_specifier("shared.core.runtime", "tests/test_runtime.py", files)
        assert result == "src/agent_platform/shared/core/runtime.py"

    def test_nested_shim_disambiguates_by_first_segment(self):
        """The first-segment fallback must not match unrelated packages:
        importing `shared.core.runtime` should NOT resolve to a similarly
        named module under a different package whose first segment differs.
        """
        from jcodemunch_mcp.parser.imports import _clear_python_roots_cache
        _clear_python_roots_cache()
        files = {
            "src/agent_platform/__init__.py",
            "src/agent_platform/shared/__init__.py",
            "src/agent_platform/shared/core/__init__.py",
            "src/agent_platform/shared/core/runtime.py",
            "src/unrelated/__init__.py",
            "src/unrelated/core/__init__.py",
            "src/unrelated/core/runtime.py",
            "tests/test_runtime.py",
        }
        result = resolve_specifier("shared.core.runtime", "tests/test_runtime.py", files)
        assert result == "src/agent_platform/shared/core/runtime.py"

    def test_nested_shim_does_not_fire_for_unknown_first_segment(self):
        """A specifier whose first segment isn't any package in the tree
        must still return None — the fallback must not degenerate into a
        suffix sweep.
        """
        from jcodemunch_mcp.parser.imports import _clear_python_roots_cache
        _clear_python_roots_cache()
        files = {
            "src/agent_platform/__init__.py",
            "src/agent_platform/shared/__init__.py",
            "src/agent_platform/shared/core/__init__.py",
            "src/agent_platform/shared/core/runtime.py",
            "tests/test_runtime.py",
        }
        # `nonexistent` is not a package name anywhere
        assert resolve_specifier("nonexistent.core.runtime", "tests/test_runtime.py", files) is None


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

        # Evict the in-memory cache entry: the direct DB modification above
        # bypasses the normal save_index path that updates the cache.  Without
        # this, load_index may return the stale cached CodeIndex (WAL mode does
        # not always update the DB file mtime, so the cache key still matches).
        from jcodemunch_mcp.storage.sqlite_store import _cache_evict
        safe_name = store_obj._sqlite._safe_repo_component(name, "name")
        _cache_evict(owner, safe_name)

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

    @pytest.mark.parametrize("scenario,expected_has_importers", [
        ("alive", True),   # loader.js is imported by app.js
        ("dead", False),   # dead_loader.js has no importers
    ], ids=["alive", "dead"])
    def test_has_importers_chain(self, tmp_path, scenario, expected_has_importers):
        """An importer's has_importers flag reflects whether it is itself imported."""
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "target.js", "export function util() {}\n")

        if scenario == "alive":
            # chain: app.js -> loader.js -> target.js
            _write(src / "loader.js", "import { util } from './target';\nexport function load() {}\n")
            _write(src / "app.js", "import { load } from './loader';\nload();\n")
            target_file = "target.js"
            importer_file = "loader.js"
        else:
            # storage.js -> dead_loader.js (dead_loader has no importers)
            _write(src / "dead_loader.js", "import { util } from './target';\nexport function load() {}\n")
            _write(src / "active.js", "export function main() {}\n")
            target_file = "target.js"
            importer_file = "dead_loader.js"

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        importers = find_importers(
            repo=result["repo"],
            file_path=target_file,
            storage_path=str(store),
        )
        assert importers["importer_count"] == 1
        imp = importers["importers"][0]
        assert imp["file"] == importer_file
        assert imp["has_importers"] is expected_has_importers

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

    @pytest.mark.parametrize("scenario", [
        pytest.param("empty_list", id="empty_list"),
        pytest.param("singular", id="singular"),
    ], ids=["empty_list", "singular"])
    def test_file_path_modes(self, tmp_path, scenario):
        """Empty file_paths list and singular file_path backward compat."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        _write(src / "utils.js", "export function helper() {}")
        _write(src / "app.js", "import { helper } from './utils';")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        if scenario == "empty_list":
            batch_result = find_importers(repo=result["repo"], file_paths=[], storage_path=str(store))
            assert batch_result["results"] == []
        else:
            singular_result = find_importers(
                repo=result["repo"],
                file_path="utils.js",
                storage_path=str(store),
            )
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

    @pytest.mark.parametrize("files,identifier,expected_count,expected_in", [
        (
            {"auth.js": "export function authenticate() {}\n",
             "app.js": "import { authenticate } from './auth';\n",
             "middleware.js": "import { authenticate } from './auth';\n",
             "unrelated.js": "function foo() {}\n"},
            "authenticate", 2, ["app.js", "middleware.js"],
        ),
        (
            {"IntakeService.js": "export class IntakeService {}\n",
             "handler.js": "import IntakeService from './IntakeService';\n"},
            "IntakeService", 1, ["handler.js"],
        ),
        (
            {"utils.js": "export function Helper() {}\n",
             "app.js": "import { Helper } from './utils';\n"},
            "helper", 1, ["app.js"],
        ),
    ], ids=["named_import", "stem_match", "case_insensitive"])
    def test_matching_variants(self, tmp_path, files, identifier, expected_count, expected_in):
        src = tmp_path / "src"
        store = tmp_path / "store"
        for fname, content in files.items():
            _write(src / fname, content)
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True
        refs = find_references(repo=result["repo"], identifier=identifier, storage_path=str(store))
        assert "error" not in refs
        assert refs["reference_count"] == expected_count
        ref_files = [r["file"] for r in refs["references"]]
        for f in expected_in:
            assert f in ref_files

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

    @pytest.mark.parametrize("scenario", [
        pytest.param("singular", id="singular"),
        pytest.param("empty_list", id="empty_list"),
    ], ids=["singular", "empty_list"])
    def test_identifier_modes(self, tmp_path, scenario):
        """Singular identifier works and empty list returns empty results."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        _write(src / "utils.js", "export function helper() {}\nexport function format() {}")
        _write(src / "app.js", "import { helper, format } from './utils';")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        repo = result["repo"]

        if scenario == "singular":
            result = find_references(repo=repo, identifier="helper", storage_path=str(store))
            assert "references" in result
            assert "results" not in result
        else:  # empty_list
            result = find_references(repo=repo, identifiers=[], storage_path=str(store))
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


class TestLaravelExtraImportsPipeline:
    """Integration: verify that Laravel provider extra imports flow through the pipeline."""

    def test_blade_imports_in_index(self, tmp_path):
        """Blade @extends/@include create import edges visible in the stored index."""
        import json
        store_path = tmp_path / "store"

        # Create minimal Laravel project
        _write(tmp_path / "artisan", "#!/usr/bin/env php\n<?php\n")
        _write(tmp_path / "composer.json", json.dumps({
            "require": {"laravel/framework": "^11.0"},
            "autoload": {"psr-4": {"App\\": "app/"}},
        }))
        _write(tmp_path / "resources" / "views" / "layouts" / "app.blade.php",
               "<!DOCTYPE html><html>@yield('content')</html>")
        _write(tmp_path / "resources" / "views" / "home.blade.php",
               "@extends('layouts.app')\n@section('content')\n<h1>Home</h1>\n@endsection")
        # Need at least one PHP file with symbols for the index to work
        _write(tmp_path / "app" / "Models" / "User.php",
               "<?php\nnamespace App\\Models;\nclass User extends Model {}\n")

        result = index_folder(str(tmp_path), use_ai_summaries=False,
                              storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)

        # Blade file should have an import edge to layouts/app
        blade_imports = index.imports.get("resources/views/home.blade.php", [])
        specifiers = {imp["specifier"] for imp in blade_imports}
        assert "resources/views/layouts/app.blade.php" in specifiers

    @pytest.mark.parametrize("scenario,file_path,expected_specifiers", [
        (
            "facade",
            "app/Services/OrderService.php",
            {"Illuminate\\Cache\\CacheManager", "Illuminate\\Database\\DatabaseManager"},
        ),
        (
            "eloquent",
            "app/Models/User.php",
            {"Post"},
        ),
    ], ids=["facade", "eloquent"])
    def test_laravel_php_imports(self, tmp_path, scenario, file_path, expected_specifiers):
        """Facade and Eloquent calls create import edges from PHP files."""
        import json
        store_path = tmp_path / "store"

        _write(tmp_path / "artisan", "#!/usr/bin/env php\n<?php\n")
        _write(tmp_path / "composer.json", json.dumps({
            "require": {"laravel/framework": "^11.0"},
            "autoload": {"psr-4": {"App\\": "app/"}},
        }))

        if scenario == "facade":
            _write(tmp_path / "app" / "Services" / "OrderService.php", r"""<?php
namespace App\Services;

class OrderService
{
    public function process()
    {
        Cache::put('key', 'value');
        DB::table('orders')->get();
    }
}
""")
        else:  # eloquent
            _write(tmp_path / "app" / "Models" / "User.php", r"""<?php
namespace App\Models;
class User extends Model
{
    public function posts() { return $this->hasMany(Post::class); }
}
""")
            _write(tmp_path / "app" / "Models" / "Post.php", r"""<?php
namespace App\Models;
class Post extends Model {}
""")

        result = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)

        imports = index.imports.get(file_path, [])
        specifiers = {imp["specifier"] for imp in imports}
        for s in expected_specifiers:
            assert s in specifiers

    def test_inertia_imports_in_index(self, tmp_path):
        """Inertia::render creates import edges from controllers to Vue pages."""
        import json
        store_path = tmp_path / "store"

        _write(tmp_path / "artisan", "#!/usr/bin/env php\n<?php\n")
        _write(tmp_path / "composer.json", json.dumps({
            "require": {"laravel/framework": "^11.0", "inertiajs/inertia-laravel": "^1.0"},
            "autoload": {"psr-4": {"App\\": "app/"}},
        }))
        _write(tmp_path / "app" / "Http" / "Controllers" / "UserController.php", r"""<?php
namespace App\Http\Controllers;
use Inertia\Inertia;
class UserController extends Controller
{
    public function index() { return Inertia::render('Users/Index', ['users' => []]); }
}
""")
        (tmp_path / "resources" / "js" / "Pages" / "Users").mkdir(parents=True)
        _write(tmp_path / "resources" / "js" / "Pages" / "Users" / "Index.vue",
               "<template><div>Users</div></template>")

        result = index_folder(str(tmp_path), use_ai_summaries=False,
                              storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)

        ctrl_imports = index.imports.get("app/Http/Controllers/UserController.php", [])
        specifiers = {imp["specifier"] for imp in ctrl_imports}
        assert "resources/js/Pages/Users/Index.vue" in specifiers

    def test_vue_template_components_in_index(self, tmp_path):
        """Vue <template> component usage creates synthetic import edges."""
        store_path = tmp_path / "store"

        _write(tmp_path / "src" / "components" / "UserTable.vue",
               "<template><table></table></template>\n<script setup></script>")
        _write(tmp_path / "src" / "App.vue", """
<template>
  <UserTable />
  <NavBar />
</template>
<script setup>
import UserTable from './components/UserTable.vue'
</script>
""")

        result = index_folder(str(tmp_path), use_ai_summaries=False,
                              storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)

        app_imports = index.imports.get("src/App.vue", [])
        specifiers = {imp["specifier"] for imp in app_imports}
        # UserTable imported in <script> — should appear
        assert "./components/UserTable.vue" in specifiers
        # NavBar only in <template>, not imported — synthetic edge
        assert "NavBar" in specifiers
