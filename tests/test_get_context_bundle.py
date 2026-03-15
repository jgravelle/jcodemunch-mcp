"""Tests for get_context_bundle tool."""

import pytest
from pathlib import Path

from jcodemunch_mcp.tools.get_context_bundle import get_context_bundle, _extract_imports
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit tests: _extract_imports
# ---------------------------------------------------------------------------

class TestExtractImportsPython:
    def test_import_statement(self):
        content = "import os\nimport sys\n\ndef foo(): pass\n"
        result = _extract_imports(content, "python")
        assert "import os" in result
        assert "import sys" in result
        assert "def foo(): pass" not in result

    def test_from_import(self):
        content = "from typing import Optional, List\n\ndef f(): pass\n"
        result = _extract_imports(content, "python")
        assert any("from typing" in line for line in result)

    def test_relative_import(self):
        content = "from ..utils import helper\nfrom . import sibling\n"
        result = _extract_imports(content, "python")
        assert len(result) == 2
        assert all("from" in line for line in result)

    def test_no_imports(self):
        content = "x = 1\ndef foo(): return x\n"
        result = _extract_imports(content, "python")
        assert result == []

    def test_mixed_code(self):
        content = (
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "class Foo:\n"
            "    def bar(self):\n"
            "        return Path('.')\n"
        )
        result = _extract_imports(content, "python")
        assert len(result) == 2
        assert "import os" in result
        assert "from pathlib import Path" in result


class TestExtractImportsJavaScript:
    def test_es_module_import(self):
        content = "import React from 'react';\nimport { useState } from 'react';\n"
        result = _extract_imports(content, "javascript")
        assert len(result) == 2

    def test_require(self):
        content = "const path = require('path');\nconst fs = require('fs');\n"
        result = _extract_imports(content, "javascript")
        assert len(result) == 2

    def test_typescript(self):
        content = "import { Component } from '@angular/core';\nconst x = 1;\n"
        result = _extract_imports(content, "typescript")
        assert len(result) == 1
        assert "Component" in result[0]

    def test_tsx(self):
        content = "import React, { FC } from 'react';\nimport styles from './App.module.css';\n"
        result = _extract_imports(content, "tsx")
        assert len(result) == 2

    def test_no_imports(self):
        content = "function add(a, b) { return a + b; }\n"
        result = _extract_imports(content, "javascript")
        assert result == []


class TestExtractImportsGo:
    def test_single_import(self):
        content = 'import "fmt"\n\nfunc main() {}\n'
        result = _extract_imports(content, "go")
        assert any("fmt" in line for line in result)

    def test_block_import(self):
        content = (
            'import (\n'
            '\t"fmt"\n'
            '\t"os"\n'
            '\t"github.com/gin-gonic/gin"\n'
            ')\n'
            '\nfunc main() {}\n'
        )
        result = _extract_imports(content, "go")
        assert result[0].strip() == "import ("
        assert any("fmt" in line for line in result)
        assert any("gin" in line for line in result)
        assert result[-1].strip() == ")"

    def test_block_import_closed_correctly(self):
        content = 'import (\n\t"fmt"\n)\n\nfunc foo() {}\n'
        result = _extract_imports(content, "go")
        # Should include import (, body, and closing )
        assert result[0].strip() == "import ("
        assert result[-1].strip() == ")"

    def test_no_imports(self):
        content = "package main\n\nfunc foo() {}\n"
        result = _extract_imports(content, "go")
        assert result == []


class TestExtractImportsRust:
    def test_use_statement(self):
        content = "use std::collections::HashMap;\nuse std::io;\n\nfn main() {}\n"
        result = _extract_imports(content, "rust")
        assert len(result) == 2
        assert all(line.startswith("use ") for line in result)

    def test_no_imports(self):
        content = "fn add(a: i32, b: i32) -> i32 { a + b }\n"
        result = _extract_imports(content, "rust")
        assert result == []


class TestExtractImportsCSharp:
    def test_using_directive(self):
        content = "using System;\nusing System.Collections.Generic;\n\nclass Foo {}\n"
        result = _extract_imports(content, "csharp")
        assert len(result) == 2
        assert all("using" in line for line in result)


class TestExtractImportsJava:
    def test_import_statement(self):
        content = "import java.util.List;\nimport java.io.IOException;\n\nclass Foo {}\n"
        result = _extract_imports(content, "java")
        assert len(result) == 2

    def test_no_imports(self):
        content = "class Foo { void bar() {} }\n"
        result = _extract_imports(content, "java")
        assert result == []


class TestExtractImportsC:
    def test_include_directive(self):
        content = "#include <stdio.h>\n#include \"myheader.h\"\n\nint main() {}\n"
        result = _extract_imports(content, "c")
        assert len(result) == 2
        assert all("#include" in line for line in result)

    def test_cpp(self):
        content = "#include <iostream>\n#include <vector>\n\nint main() {}\n"
        result = _extract_imports(content, "cpp")
        assert len(result) == 2


class TestExtractImportsRuby:
    def test_require(self):
        content = "require 'json'\nrequire_relative 'utils'\n\ndef foo; end\n"
        result = _extract_imports(content, "ruby")
        assert len(result) == 2

    def test_no_imports(self):
        content = "def add(a, b)\n  a + b\nend\n"
        result = _extract_imports(content, "ruby")
        assert result == []


class TestExtractImportsElixir:
    def test_import_alias_use(self):
        content = (
            "import MyApp.Utils\n"
            "alias MyApp.Repo\n"
            "use Phoenix.Router\n"
            "\ndef foo, do: :ok\n"
        )
        result = _extract_imports(content, "elixir")
        assert len(result) == 3


class TestExtractImportsUnknownLanguage:
    def test_unsupported_language_returns_empty(self):
        result = _extract_imports("anything here", "cobol")
        assert result == []

    def test_empty_content(self):
        result = _extract_imports("", "python")
        assert result == []

    def test_empty_content_unknown_lang(self):
        result = _extract_imports("", "brainfuck")
        assert result == []


# ---------------------------------------------------------------------------
# Integration tests: get_context_bundle via index_folder
# ---------------------------------------------------------------------------

class TestGetContextBundleIntegration:

    def test_python_symbol_with_imports(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "utils.py", (
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "def read_file(path: str) -> str:\n"
            "    \"\"\"Read a file and return its contents.\"\"\"\n"
            "    return Path(path).read_text()\n"
        ))

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        # Find the symbol ID
        from jcodemunch_mcp.tools.search_symbols import search_symbols
        hits = search_symbols(result["repo"], "read_file", storage_path=str(store))
        assert hits["results"], "Expected symbol to be found"
        symbol_id = hits["results"][0]["id"]

        bundle = get_context_bundle(result["repo"], symbol_id, storage_path=str(store))

        assert "error" not in bundle
        assert bundle["name"] == "read_file"
        assert bundle["kind"] == "function"
        assert "read_file" in bundle["source"]
        assert "import os" in bundle["imports"]
        assert any("from pathlib" in line for line in bundle["imports"])

    def test_js_symbol_with_imports(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "service.js", (
            "import axios from 'axios';\n"
            "import { API_URL } from './config';\n"
            "\n"
            "async function fetchData(endpoint) {\n"
            "    return axios.get(API_URL + endpoint);\n"
            "}\n"
        ))

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        from jcodemunch_mcp.tools.search_symbols import search_symbols
        hits = search_symbols(result["repo"], "fetchData", storage_path=str(store))
        assert hits["results"]
        symbol_id = hits["results"][0]["id"]

        bundle = get_context_bundle(result["repo"], symbol_id, storage_path=str(store))

        assert "error" not in bundle
        assert bundle["name"] == "fetchData"
        assert any("axios" in line for line in bundle["imports"])
        assert any("config" in line for line in bundle["imports"])

    def test_symbol_with_no_imports(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "math.py", (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def subtract(a, b):\n"
            "    return a - b\n"
        ))

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        from jcodemunch_mcp.tools.search_symbols import search_symbols
        hits = search_symbols(result["repo"], "add", storage_path=str(store))
        assert hits["results"]
        symbol_id = hits["results"][0]["id"]

        bundle = get_context_bundle(result["repo"], symbol_id, storage_path=str(store))

        assert "error" not in bundle
        assert bundle["imports"] == []

    def test_bundle_includes_required_fields(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "app.py", "import sys\n\ndef main():\n    pass\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        from jcodemunch_mcp.tools.search_symbols import search_symbols
        hits = search_symbols(result["repo"], "main", storage_path=str(store))
        symbol_id = hits["results"][0]["id"]

        bundle = get_context_bundle(result["repo"], symbol_id, storage_path=str(store))

        for field in ("symbol_id", "name", "kind", "file", "line", "end_line",
                      "signature", "docstring", "source", "imports", "_meta"):
            assert field in bundle, f"Missing field: {field}"

        assert "_meta" in bundle
        assert "timing_ms" in bundle["_meta"]
        assert "tokens_saved" in bundle["_meta"]

    def test_error_on_unknown_repo(self, tmp_path):
        store = tmp_path / "store"
        result = get_context_bundle(
            "nonexistent/repo",
            "some/file.py::foo#function",
            storage_path=str(store),
        )
        assert "error" in result

    def test_error_on_unknown_symbol(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "app.py", "def foo(): pass\n")
        index_result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert index_result["success"] is True

        result = get_context_bundle(
            index_result["repo"],
            "app.py::nonexistent#function",
            storage_path=str(store),
        )
        assert "error" in result

    def test_meta_token_savings_populated(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"

        _write(src / "big.py", (
            "import os\n"
            "import sys\n"
            "from typing import List\n"
            "\n"
            "def process(items: List[str]) -> None:\n"
            "    for item in items:\n"
            "        print(item)\n"
            "\n"
            "def helper():\n"
            "    return os.getcwd()\n"
        ))

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        from jcodemunch_mcp.tools.search_symbols import search_symbols
        hits = search_symbols(result["repo"], "process", storage_path=str(store))
        symbol_id = hits["results"][0]["id"]

        bundle = get_context_bundle(result["repo"], symbol_id, storage_path=str(store))

        assert bundle["_meta"]["tokens_saved"] >= 0
        assert bundle["_meta"]["total_tokens_saved"] >= 0
