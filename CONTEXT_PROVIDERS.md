# Context Providers

## Overview

Context providers enrich jCodeMunch indexes with **business metadata** from ecosystem tools. When a provider detects its tool in a project (e.g., a `dbt_project.yml` file), it automatically loads descriptions, tags, and properties from that tool's configuration files and attaches them to the code index.

This metadata flows into:
- **AI summaries** — providers inject business context into summarization prompts, producing summaries that reflect what the code *means*, not just what it *does*
- **File summaries** — model descriptions, tags, and property counts appear in file-level overviews
- **Search keywords** — tags and property names become searchable terms in `search_symbols`

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
```

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
        # Return context if this file has metadata
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

### 4. Test it

```python
def test_terraform_provider():
    from jcodemunch_mcp.parser.context import discover_providers
    providers = discover_providers(Path("/path/to/terraform/project"))
    assert any(p.name == "terraform" for p in providers)
```

---

## Provider Ideas

Potential future providers for community contribution:

| Provider       | Detects                   | Could Enrich With                                    |
| -------------- | ------------------------- | ---------------------------------------------------- |
| Terraform      | `*.tf` files              | Resource descriptions, variable docs, module metadata |
| OpenAPI        | `openapi.yaml`/`swagger.json` | Endpoint descriptions, parameter schemas         |
| Django         | `manage.py` + `models.py` | Model field descriptions, admin labels              |
| Helm           | `Chart.yaml`              | Chart descriptions, value documentation              |
| Protobuf       | `*.proto`                 | Service/message comments, field descriptions         |
| SQLAlchemy     | `models.py` with `Column` | Column docs, table comments                         |
| AsyncAPI       | `asyncapi.yaml`           | Channel descriptions, message schemas                |
| GraphQL        | `schema.graphql`          | Type/field descriptions                              |

---

## Configuration

Context providers require no configuration — they activate automatically when their ecosystem is detected. Provider-specific optional dependencies (like `pyyaml` for dbt) should be installed separately.

To verify which providers activated during indexing, check the `context_enrichment` key in the `index_folder` response or enable debug logging:

```
JCODEMUNCH_LOG_LEVEL=DEBUG
```
