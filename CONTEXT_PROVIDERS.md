# Context Providers

## Overview

Context providers enrich jCodeMunch indexes with **business metadata** from ecosystem tools. When a provider detects its tool in a project (e.g., a `dbt_project.yml` file), it automatically loads descriptions, tags, and properties from that tool's configuration files and attaches them to the code index.

This metadata flows into:
- **AI summaries** — providers inject business context into summarization prompts, producing summaries that reflect what the code *means*, not just what it *does*
- **File summaries** — model descriptions, tags, and property counts appear in file-level overviews
- **Search keywords** — tags and property names become searchable terms in `search_symbols`
- **Column search** — providers that emit column metadata enable the `search_columns` tool for structured column discovery

Context enrichment is **automatic** — no configuration required. Providers self-detect during `index_folder` and activate when their ecosystem is present.

---

## Built-In Providers

| Provider   | Detects              | Metadata Source                     | Enriches With                                  |
| ---------- | -------------------- | ----------------------------------- | ---------------------------------------------- |
| dbt        | `dbt_project.yml`    | `schema.yml`, `{% docs %}` blocks   | Model descriptions, tags, column names/descriptions |

---

## dbt Provider

### Detection

Scans up to 2 levels deep for `dbt_project.yml`:

```
project/dbt_project.yml          ✓ (root)
project/DBT/dbt_project.yml      ✓ (one level deep)
project/a/b/dbt_project.yml      ✗ (too deep)
```

### What It Loads

**Doc blocks** — parsed from `{% docs name %}...{% enddocs %}` in `.md` files within docs directories:

```markdown
{% docs my_model %}
This model tracks daily revenue by product line.
{% enddocs %}
```

**Model metadata** — parsed from `schema.yml` files in model directories:

```yaml
models:
  - name: fct_daily_revenue
    description: "{{ doc('my_model') }}"
    config:
      tags: ['nightly', 'finance']
    columns:
      - name: revenue_date
        description: "The date revenue was recognized"
      - name: amount
        description: "Revenue amount in USD"
```

Doc references (`{{ doc('name') }}`) are resolved automatically.

### How It Matches Files

The provider matches indexed files to dbt models by **file stem** (filename without extension), but only for files within the project's configured `model-paths` directories. This prevents false matches — for example, a `scripts/schema.sql` file will not be matched to a dbt model named `schema`, but `models/schema.sql` will.

```
models/fct_daily_revenue.sql       ✓ matches model "fct_daily_revenue"
models/staging/fct_daily_revenue.sql  ✓ matches (subdirectories OK)
scripts/fct_daily_revenue.sql      ✗ outside model-paths
schema.sql                         ✗ outside model-paths
```

### How It Enriches

**Symbol `ecosystem_context`** (injected into AI prompts):

```
dbt: This model tracks daily revenue by product line.
Tags: nightly, finance. Properties: revenue_date (The date revenue was recognized),
amount (Revenue amount in USD)
```

**File summary** (visible in `get_file_outline`):

```
This model tracks daily revenue by product line. Tags: nightly, finance. 2 properties
```

**Search keywords** (indexed for `search_symbols`):

```
["nightly", "finance", "revenue_date", "amount"]
```

### Index Response

When the dbt provider is active, `index_folder` returns enrichment stats:

```json
{
  "context_enrichment": {
    "dbt": {
      "doc_blocks": 5591,
      "models_with_metadata": 3772
    }
  }
}
```

### Dependencies

The dbt provider requires `pyyaml` for schema.yml parsing:

```bash
pip install jcodemunch-mcp[dbt]
```

Without PyYAML, doc blocks are still parsed but model/column metadata from YAML files is skipped.

---

## Architecture

### Data Flow

```
index_folder()
  │
  ├─ discover_providers(folder_path)
  │    ├─ DbtContextProvider.detect()  → found dbt_project.yml?
  │    ├─ DbtContextProvider.load()    → parse docs + schema.yml
  │    └─ ... (future providers)
  │
  ├─ Parse files → extract symbols (tree-sitter)
  │
  ├─ enrich_symbols(symbols, providers)
  │    └─ For each symbol, query each provider:
  │         provider.get_file_context(file_path) → FileContext
  │         → set symbol.ecosystem_context (for AI prompt)
  │         → extend symbol.keywords (for search)
  │
  ├─ collect_metadata(providers)
  │    └─ For each provider:
  │         provider.get_metadata() → {"dbt_columns": {...}, ...}
  │         → persisted in index.context_metadata
  │         → powers search_columns tool
  │
  ├─ Summarize symbols (AI sees ecosystem_context)
  │
  └─ Generate file summaries (providers consulted per-file)
```

### Core Types

**`FileContext`** — the common metadata structure all providers produce:

```python
@dataclass
class FileContext:
    description: str           # Business description of the file
    tags: list[str]            # Categorization tags
    properties: dict[str, str] # Named attributes (columns, variables, etc.)
```

Methods:
- `summary_context()` — compact string for AI prompts
- `file_summary()` — human-readable file-level summary
- `search_keywords()` — terms for search indexing

**`ContextProvider`** — the abstract base class:

```python
class ContextProvider(ABC):
    name: str                                          # e.g., "dbt"
    def detect(self, folder_path: Path) -> bool        # Is this tool present?
    def load(self, folder_path: Path) -> None          # Parse its metadata
    def get_file_context(self, path: str) -> FileContext | None  # Per-file lookup
    def stats(self) -> dict                            # Enrichment statistics
    def get_metadata(self) -> dict                     # Structured metadata for index (optional override)
```

The `get_metadata()` method returns a dict that gets persisted in `index.context_metadata`. Keys should be namespaced by provider (e.g., `"dbt_columns"`). Keys ending in `_columns` are auto-discovered by the `search_columns` tool.

---

## Adding a New Provider

### 1. Create the provider module

```python
# src/jcodemunch_mcp/parser/context/terraform.py

from pathlib import Path
from typing import Optional
from .base import ContextProvider, FileContext, register_provider

@register_provider
class TerraformContextProvider(ContextProvider):

    @property
    def name(self) -> str:
        return "terraform"

    def detect(self, folder_path: Path) -> bool:
        # Look for .tf files or terraform config
        for child in folder_path.rglob("*.tf"):
            return True
        return False

    def load(self, folder_path: Path) -> None:
        # Parse variable descriptions, module docs, etc.
        self._modules = {}
        # ... your parsing logic here ...

    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        # Validate the file is within your tool's project directories
        # before matching by stem, to avoid false positives
        module = self._modules.get(Path(file_path).stem)
        if module:
            return FileContext(
                description=module["description"],
                tags=module.get("tags", []),
                properties=module.get("variables", {}),
            )
        return None

    def stats(self) -> dict:
        return {"modules": len(self._modules)}
```

### 2. Register the module

Add the import to `parser/context/__init__.py`:

```python
from . import dbt        # noqa: F401
from . import terraform  # noqa: F401  ← add this line
```

The `@register_provider` decorator handles the rest — the provider will be auto-detected during `index_folder`.

### 3. Add optional dependencies

If your provider needs extra packages, add them to `pyproject.toml`:

```toml
[project.optional-dependencies]
terraform = ["python-hcl2>=4.0"]
```

### 4. Expose column metadata (optional)

If your ecosystem has column-level information (database schemas, model fields, table catalogs), you can make it searchable via the `search_columns` tool by overriding `get_metadata()`.

**The convention:** emit a key ending in `_columns` whose value is `{model_name: {col_name: col_desc}}`.

```python
def get_metadata(self) -> dict:
    """Return column metadata for search_columns."""
    columns: dict[str, dict[str, str]] = {}
    for model_name, model in self._models.items():
        if model.columns:
            columns[model_name] = dict(model.columns)
    if not columns:
        return {}
    return {"terraform_columns": columns}  # key = {provider}_columns
```

That's it. `search_columns` auto-discovers any `*_columns` key in `context_metadata` — no changes to the tool itself are needed. When multiple providers contribute columns, results include a `source` field so users can distinguish origins.

**What the key name controls:**
- `"dbt_columns"` → source shown as `"dbt"`
- `"sqlmesh_columns"` → source shown as `"sqlmesh"`
- `"catalog_columns"` → source shown as `"catalog"`

The suffix `_columns` is stripped to derive the display name.

**Required shape:**

```python
{
    "{provider}_columns": {
        "model_or_table_name": {
            "column_name": "Human-readable description",
            "another_column": "Another description",
        },
        "another_model": { ... }
    }
}
```

Descriptions should be plain text (resolve any template references like Jinja `{{ doc() }}` at index time, not search time). Empty descriptions are allowed — the column will still be searchable by name.

### 5. Test it

```python
def test_terraform_provider():
    from jcodemunch_mcp.parser.context import discover_providers
    providers = discover_providers(Path("/path/to/terraform/project"))
    assert any(p.name == "terraform" for p in providers)
```

```python
def test_terraform_column_metadata():
    from jcodemunch_mcp.parser.context import discover_providers, collect_metadata
    providers = discover_providers(Path("/path/to/terraform/project"))
    metadata = collect_metadata(providers)
    # Verify columns are emitted under the right key
    assert "terraform_columns" in metadata
    assert isinstance(metadata["terraform_columns"], dict)
```

---

## Provider Ideas

Potential future providers for community contribution:

| Provider       | Detects                   | Could Enrich With                                    | Column metadata? |
| -------------- | ------------------------- | ---------------------------------------------------- | ---------------- |
| SQLMesh        | `config.yaml` + models    | Model descriptions, column lineage, audits           | Yes — `sqlmesh_columns` |
| Terraform      | `*.tf` files              | Resource descriptions, variable docs, module metadata | No |
| OpenAPI        | `openapi.yaml`/`swagger.json` | Endpoint descriptions, parameter schemas         | Yes — schema properties |
| Django         | `manage.py` + `models.py` | Model field descriptions, admin labels               | Yes — `django_columns` |
| SQLAlchemy     | `models.py` with `Column` | Column docs, table comments                          | Yes — `sqlalchemy_columns` |
| DB catalog     | Connection config         | `INFORMATION_SCHEMA` column comments                 | Yes — `catalog_columns` |
| Protobuf       | `*.proto`                 | Service/message comments, field descriptions         | Yes — message fields |
| GraphQL        | `schema.graphql`          | Type/field descriptions                              | Yes — type fields |
| Helm           | `Chart.yaml`              | Chart descriptions, value documentation              | No |
| AsyncAPI       | `asyncapi.yaml`           | Channel descriptions, message schemas                | No |

---

## Configuration

Context providers require no configuration — they activate automatically when their ecosystem is detected. Provider-specific optional dependencies (like `pyyaml` for dbt) should be installed separately.

### Disabling Context Providers

Context providers can be disabled globally via environment variable or per-call via parameter:

**Environment variable** — disables providers for all `index_folder` calls:

```bash
JCODEMUNCH_CONTEXT_PROVIDERS=0
```

In your MCP server config:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"],
      "env": {
        "JCODEMUNCH_CONTEXT_PROVIDERS": "0"
      }
    }
  }
}
```

**Per-call parameter** — pass `context_providers: false` to `index_folder`:

```python
index_folder(path="/my/project", context_providers=False)
```

Either method skips provider discovery entirely — no YAML parsing, no doc block scanning, no enrichment overhead.

### Debugging

To verify which providers activated during indexing, check the `context_enrichment` key in the `index_folder` response or enable debug logging:

```
JCODEMUNCH_LOG_LEVEL=DEBUG
```
