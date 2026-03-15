"""Tests for the dbt context provider."""

import pytest

from pathlib import Path
from jcodemunch_mcp.parser.context.dbt import (
    DbtContextProvider,
    DbtModelMetadata,
    _detect_dbt_project,
    _parse_doc_blocks,
    _resolve_description,
)
from jcodemunch_mcp.parser.context.base import FileContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_dbt_project(tmp_path, model_paths=None, docs_paths=None):
    """Create a minimal dbt project structure and return the project root."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # dbt_project.yml
    yml_content = "name: test_project\nversion: '1.0.0'\n"
    if model_paths:
        yml_content += "model-paths:\n"
        for mp in model_paths:
            yml_content += f"  - {mp}\n"
    if docs_paths:
        yml_content += "docs-paths:\n"
        for dp in docs_paths:
            yml_content += f"  - {dp}\n"

    (project_root / "dbt_project.yml").write_text(yml_content, encoding="utf-8")
    return project_root


def _write_schema_yml(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_doc_block(path, name, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{{% docs {name} %}}{body}{{% enddocs %}}\n"
    # Append if file exists
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        content = existing + content
    path.write_text(content, encoding="utf-8")


# ===========================================================================
# Detection tests
# ===========================================================================


class TestDetectDbtProject:

    def test_detect_root_level(self, tmp_path):
        (tmp_path / "dbt_project.yml").write_text("name: test\n")
        result = _detect_dbt_project(tmp_path)
        assert result is not None
        assert result.name == "dbt_project.yml"

    def test_detect_one_level_deep(self, tmp_path):
        subdir = tmp_path / "DBT"
        subdir.mkdir()
        (subdir / "dbt_project.yml").write_text("name: test\n")
        result = _detect_dbt_project(tmp_path)
        assert result is not None
        assert "DBT" in str(result)

    def test_detect_too_deep(self, tmp_path):
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "dbt_project.yml").write_text("name: test\n")
        result = _detect_dbt_project(tmp_path)
        assert result is None

    def test_detect_no_project(self, tmp_path):
        (tmp_path / "some_file.txt").write_text("hello\n")
        result = _detect_dbt_project(tmp_path)
        assert result is None

    def test_detect_ignores_hidden_dirs(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "dbt_project.yml").write_text("name: test\n")
        result = _detect_dbt_project(tmp_path)
        assert result is None


# ===========================================================================
# Doc block parsing tests
# ===========================================================================


class TestParseDocBlocks:

    def test_parse_single_doc_block(self, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        _write_doc_block(docs_dir / "models.md", "my_model", "Tracks daily revenue.")

        result = _parse_doc_blocks([docs_dir])
        assert "my_model" in result
        assert result["my_model"] == "Tracks daily revenue."

    def test_parse_multiple_doc_blocks(self, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        _write_doc_block(docs_dir / "models.md", "model_a", "First model.")
        _write_doc_block(docs_dir / "models.md", "model_b", "Second model.")

        result = _parse_doc_blocks([docs_dir])
        assert len(result) == 2
        assert result["model_a"] == "First model."
        assert result["model_b"] == "Second model."

    def test_parse_multiline_doc_block(self, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        body = "Line one.\nLine two.\nLine three."
        _write_doc_block(docs_dir / "models.md", "multi", body)

        result = _parse_doc_blocks([docs_dir])
        assert "Line one." in result["multi"]
        assert "Line three." in result["multi"]

    def test_parse_no_docs_dir(self, tmp_path):
        result = _parse_doc_blocks([tmp_path / "nonexistent"])
        assert result == {}

    def test_parse_multiple_docs_dirs(self, tmp_path):
        dir_a = tmp_path / "docs_a"
        dir_b = tmp_path / "docs_b"
        dir_a.mkdir()
        dir_b.mkdir()
        _write_doc_block(dir_a / "a.md", "from_a", "Doc A.")
        _write_doc_block(dir_b / "b.md", "from_b", "Doc B.")

        result = _parse_doc_blocks([dir_a, dir_b])
        assert "from_a" in result
        assert "from_b" in result


# ===========================================================================
# Description resolution tests
# ===========================================================================


class TestResolveDescription:

    def test_resolve_doc_reference(self):
        blocks = {"my_doc": "This is the resolved text."}
        result = _resolve_description("{{ doc('my_doc') }}", blocks)
        assert result == "This is the resolved text."

    def test_resolve_doc_reference_double_quotes(self):
        blocks = {"my_doc": "Resolved."}
        result = _resolve_description('{{ doc("my_doc") }}', blocks)
        assert result == "Resolved."

    def test_resolve_plain_description(self):
        result = _resolve_description("A plain description.", {})
        assert result == "A plain description."

    def test_resolve_missing_reference(self):
        result = _resolve_description("{{ doc('missing') }}", {})
        assert result == ""

    def test_resolve_empty(self):
        result = _resolve_description("", {})
        assert result == ""


# ===========================================================================
# YAML parsing tests (requires pyyaml)
# ===========================================================================


yaml = pytest.importorskip("yaml", reason="pyyaml required for yml tests")


class TestParseSchemaYml:

    def test_parse_model_metadata(self, tmp_path):
        from jcodemunch_mcp.parser.context.dbt import _parse_yml_files

        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: fct_orders
    description: "Order fact table"
    config:
      tags: ['nightly', 'core']
    columns:
      - name: order_id
        description: "Primary key"
      - name: amount
        description: "Order total in USD"
""")

        result = _parse_yml_files([models_dir], {})
        assert "fct_orders" in result
        model = result["fct_orders"]
        assert model.description == "Order fact table"
        assert model.tags == ["nightly", "core"]
        assert model.columns["order_id"] == "Primary key"
        assert model.columns["amount"] == "Order total in USD"

    def test_parse_yml_with_doc_refs(self, tmp_path):
        from jcodemunch_mcp.parser.context.dbt import _parse_yml_files

        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: dim_customer
    description: "{{ doc('dim_customer_doc') }}"
    columns:
      - name: customer_id
        description: "{{ doc('customer_id_doc') }}"
""")

        doc_blocks = {
            "dim_customer_doc": "Customer dimension table.",
            "customer_id_doc": "Unique customer identifier.",
        }
        result = _parse_yml_files([models_dir], doc_blocks)
        model = result["dim_customer"]
        assert model.description == "Customer dimension table."
        assert model.columns["customer_id"] == "Unique customer identifier."

    def test_parse_yml_malformed(self, tmp_path):
        from jcodemunch_mcp.parser.context.dbt import _parse_yml_files

        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "bad.yml", "not: [valid: yaml: {{")

        # Should not raise
        result = _parse_yml_files([models_dir], {})
        assert isinstance(result, dict)

    def test_parse_yml_no_models_key(self, tmp_path):
        from jcodemunch_mcp.parser.context.dbt import _parse_yml_files

        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "schema.yml", """
sources:
  - name: raw
    tables:
      - name: orders
""")

        result = _parse_yml_files([models_dir], {})
        assert result == {}

    def test_parse_yml_missing_name(self, tmp_path):
        from jcodemunch_mcp.parser.context.dbt import _parse_yml_files

        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - description: "No name field"
""")

        result = _parse_yml_files([models_dir], {})
        assert result == {}

    def test_parse_multiple_models(self, tmp_path):
        from jcodemunch_mcp.parser.context.dbt import _parse_yml_files

        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: model_a
    description: "First"
  - name: model_b
    description: "Second"
""")

        result = _parse_yml_files([models_dir], {})
        assert len(result) == 2
        assert result["model_a"].description == "First"
        assert result["model_b"].description == "Second"


# ===========================================================================
# DbtModelMetadata tests
# ===========================================================================


class TestDbtModelMetadata:

    def test_to_file_context(self):
        model = DbtModelMetadata(
            name="fct_orders",
            description="Order fact table",
            tags=["nightly"],
            columns={"order_id": "PK", "amount": "USD"},
        )
        ctx = model.to_file_context()
        assert isinstance(ctx, FileContext)
        assert ctx.description == "Order fact table"
        assert ctx.tags == ["nightly"]
        assert ctx.properties == {"order_id": "PK", "amount": "USD"}

    def test_to_file_context_empty(self):
        model = DbtModelMetadata(name="empty")
        ctx = model.to_file_context()
        assert ctx.description == ""
        assert ctx.tags == []
        assert ctx.properties == {}


# ===========================================================================
# DbtContextProvider integration tests
# ===========================================================================


class TestDbtContextProvider:

    def test_full_lifecycle(self, tmp_path):
        root = _create_dbt_project(tmp_path)
        models_dir = root / "models"
        models_dir.mkdir()
        docs_dir = root / "docs"
        docs_dir.mkdir()

        _write_doc_block(docs_dir / "docs.md", "orders_doc", "All customer orders.")
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: fct_orders
    description: "{{ doc('orders_doc') }}"
    config:
      tags: ['nightly']
    columns:
      - name: order_id
        description: "Primary key"
""")

        provider = DbtContextProvider()
        assert provider.detect(root) is True

        provider.load(root)

        ctx = provider.get_file_context("models/fct_orders.sql")
        assert ctx is not None
        assert ctx.description == "All customer orders."
        assert "nightly" in ctx.tags
        assert "order_id" in ctx.properties

    def test_get_file_context_by_stem(self, tmp_path):
        """Matches by file stem within model directories."""
        root = _create_dbt_project(tmp_path)
        models_dir = root / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: my_model
    description: "Found by stem"
""")

        provider = DbtContextProvider()
        provider.detect(root)
        provider.load(root)

        # Within models directory — matches by stem
        assert provider.get_file_context("models/my_model.sql") is not None
        assert provider.get_file_context("models/staging/my_model.sql") is not None

    def test_get_file_context_outside_model_path(self, tmp_path):
        """Files outside model directories are not matched, even if stem matches."""
        root = _create_dbt_project(tmp_path)
        models_dir = root / "models"
        models_dir.mkdir()
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: my_model
    description: "Should not match outside models/"
  - name: schema
    description: "A model named schema"
""")

        provider = DbtContextProvider()
        provider.detect(root)
        provider.load(root)

        # Outside models/ — should not match
        assert provider.get_file_context("my_model.sql") is None
        assert provider.get_file_context("scripts/my_model.sql") is None
        assert provider.get_file_context("schema.sql") is None

        # Inside models/ — should match
        assert provider.get_file_context("models/my_model.sql") is not None
        assert provider.get_file_context("models/schema.sql") is not None

    def test_get_file_context_no_match(self, tmp_path):
        root = _create_dbt_project(tmp_path)
        (root / "models").mkdir()

        provider = DbtContextProvider()
        provider.detect(root)
        provider.load(root)

        assert provider.get_file_context("models/unknown.sql") is None

    def test_stats(self, tmp_path):
        root = _create_dbt_project(tmp_path)
        models_dir = root / "models"
        models_dir.mkdir()
        docs_dir = root / "docs"
        docs_dir.mkdir()

        _write_doc_block(docs_dir / "docs.md", "doc1", "First.")
        _write_doc_block(docs_dir / "docs.md", "doc2", "Second.")
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: model_a
    description: "A"
""")

        provider = DbtContextProvider()
        provider.detect(root)
        provider.load(root)

        stats = provider.stats()
        assert stats["doc_blocks"] == 2
        assert stats["models_with_metadata"] == 1

    def test_detect_returns_false_for_non_dbt(self, tmp_path):
        provider = DbtContextProvider()
        assert provider.detect(tmp_path) is False

    def test_custom_model_paths(self, tmp_path):
        root = _create_dbt_project(tmp_path, model_paths=["src/models"])
        custom_dir = root / "src" / "models"
        custom_dir.mkdir(parents=True)
        _write_schema_yml(custom_dir / "schema.yml", """
models:
  - name: custom_model
    description: "Found via custom path"
""")

        provider = DbtContextProvider()
        provider.detect(root)
        provider.load(root)

        ctx = provider.get_file_context("src/models/custom_model.sql")
        assert ctx is not None
        assert ctx.description == "Found via custom path"

    def test_docs_inside_models_dir(self, tmp_path):
        """Doc blocks in .md files alongside models should be found."""
        root = _create_dbt_project(tmp_path)
        models_dir = root / "models"
        models_dir.mkdir()

        _write_doc_block(models_dir / "docs.md", "inline_doc", "Inline doc block.")
        _write_schema_yml(models_dir / "schema.yml", """
models:
  - name: my_model
    description: "{{ doc('inline_doc') }}"
""")

        provider = DbtContextProvider()
        provider.detect(root)
        provider.load(root)

        ctx = provider.get_file_context("models/my_model.sql")
        assert ctx is not None
        assert ctx.description == "Inline doc block."
