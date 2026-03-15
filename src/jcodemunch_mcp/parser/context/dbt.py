"""dbt context provider — detects dbt projects and enriches with model metadata.

When a dbt project is detected (via dbt_project.yml), this provider:
1. Parses all {% docs %} blocks from markdown files
2. Parses schema.yml files for model + column descriptions
3. Resolves {{ doc('name') }} references to actual text
4. Provides per-file context lookups for symbol enrichment
"""

import logging
import re
from pathlib import Path
from typing import Optional

from .base import ContextProvider, FileContext, register_provider

logger = logging.getLogger(__name__)

# Pattern to match {% docs name %}...{% enddocs %} blocks
_DOC_BLOCK_RE = re.compile(
    r"\{%\s*docs\s+([\w]+)\s*%\}(.*?)\{%\s*enddocs\s*%\}",
    re.DOTALL,
)

# Pattern to match {{ doc('name') }} references
_DOC_REF_RE = re.compile(r"\{\{\s*doc\(['\"](\w+)['\"]\)\s*\}\}")


class DbtModelMetadata:
    """Metadata for a single dbt model from schema.yml + doc blocks."""

    __slots__ = ("name", "description", "tags", "columns")

    def __init__(
        self,
        name: str,
        description: str = "",
        tags: Optional[list[str]] = None,
        columns: Optional[dict[str, str]] = None,
    ):
        self.name = name
        self.description = description
        self.tags = tags or []
        self.columns = columns or {}  # column_name -> description

    def to_file_context(self) -> FileContext:
        """Convert to the generic FileContext structure."""
        return FileContext(
            description=self.description,
            tags=list(self.tags),
            properties=dict(self.columns),
        )


def _resolve_description(raw_desc: str, doc_blocks: dict[str, str]) -> str:
    """Resolve {{ doc('name') }} references to actual text."""
    if not raw_desc:
        return ""
    doc_ref = _DOC_REF_RE.search(raw_desc)
    if doc_ref:
        key = doc_ref.group(1)
        return doc_blocks.get(key, "")
    return raw_desc


def _parse_doc_blocks(docs_dirs: list[Path]) -> dict[str, str]:
    """Parse all {% docs name %}...{% enddocs %} blocks from .md files."""
    doc_blocks: dict[str, str] = {}
    for docs_dir in docs_dirs:
        if not docs_dir.is_dir():
            continue
        for md_file in docs_dir.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for match in _DOC_BLOCK_RE.finditer(text):
                name = match.group(1).strip()
                body = match.group(2).strip()
                doc_blocks[name] = body
    return doc_blocks


def _parse_yml_files(
    models_dirs: list[Path],
    doc_blocks: dict[str, str],
) -> dict[str, DbtModelMetadata]:
    """Parse schema.yml files and extract model + column descriptions."""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — dbt yml parsing skipped")
        return {}

    models: dict[str, DbtModelMetadata] = {}

    for models_dir in models_dirs:
        if not models_dir.is_dir():
            continue
        for yml_file in models_dir.rglob("*.yml"):
            try:
                text = yml_file.read_text(encoding="utf-8", errors="replace")
                data = yaml.safe_load(text)
            except Exception:
                continue

            if not isinstance(data, dict):
                continue

            for model in data.get("models", []):
                if not isinstance(model, dict):
                    continue
                name = model.get("name", "")
                if not name:
                    continue

                model_desc = _resolve_description(
                    model.get("description", ""), doc_blocks
                )

                # Extract tags from config
                config = model.get("config", {})
                tags = []
                if isinstance(config, dict):
                    tags = config.get("tags", [])
                    if not isinstance(tags, list):
                        tags = []

                # Parse columns
                columns: dict[str, str] = {}
                for col in model.get("columns", []):
                    if not isinstance(col, dict):
                        continue
                    col_name = col.get("name", "")
                    col_desc = _resolve_description(
                        col.get("description", ""), doc_blocks
                    )
                    if col_name:
                        columns[col_name] = col_desc

                models[name] = DbtModelMetadata(
                    name=name,
                    description=model_desc,
                    tags=tags,
                    columns=columns,
                )

    return models


def _detect_dbt_project(folder_path: Path) -> Optional[Path]:
    """Find dbt_project.yml in the folder tree (max 2 levels deep)."""
    candidate = folder_path / "dbt_project.yml"
    if candidate.is_file():
        return candidate

    for child in folder_path.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            candidate = child / "dbt_project.yml"
            if candidate.is_file():
                return candidate

    return None


@register_provider
class DbtContextProvider(ContextProvider):
    """Context provider for dbt projects.

    Detects dbt_project.yml, parses doc blocks and schema.yml files,
    and provides model-level context for symbol enrichment.
    """

    def __init__(self):
        self._dbt_yml_path: Optional[Path] = None
        self._doc_blocks: dict[str, str] = {}
        self._models: dict[str, DbtModelMetadata] = {}
        self._model_path_prefixes: list[str] = []

    @property
    def name(self) -> str:
        return "dbt"

    def detect(self, folder_path: Path) -> bool:
        self._dbt_yml_path = _detect_dbt_project(folder_path)
        return self._dbt_yml_path is not None

    def load(self, folder_path: Path) -> None:
        if self._dbt_yml_path is None:
            logger.warning("load() called without detect() — skipping")
            return
        project_root = self._dbt_yml_path.parent
        logger.info("dbt project detected at %s", project_root)

        docs_dirs = []
        models_dirs = []

        try:
            import yaml
            text = self._dbt_yml_path.read_text(encoding="utf-8", errors="replace")
            project_config = yaml.safe_load(text)
            if isinstance(project_config, dict):
                model_paths = project_config.get("model-paths") or project_config.get("source-paths") or ["models"]
                for mp in model_paths:
                    models_dirs.append(project_root / mp)
                docs_paths = project_config.get("docs-paths") or ["docs"]
                for dp in docs_paths:
                    docs_dirs.append(project_root / dp)
        except Exception:
            models_dirs = [project_root / "models"]
            docs_dirs = [project_root / "docs"]

        # Also check for docs inside models
        for md in models_dirs:
            if md not in docs_dirs:
                docs_dirs.append(md)

        # Store model path prefixes (relative to indexed folder) for path validation.
        # Only files under these prefixes are considered dbt models.
        self._model_path_prefixes = []
        for md in models_dirs:
            try:
                rel = md.resolve().relative_to(folder_path.resolve())
                # Normalize to forward slashes for cross-platform matching
                self._model_path_prefixes.append(str(rel).replace("\\", "/") + "/")
            except ValueError:
                # Model dir is outside the indexed folder — use absolute as fallback
                self._model_path_prefixes.append(str(md).replace("\\", "/") + "/")

        self._doc_blocks = _parse_doc_blocks(docs_dirs)
        logger.info("Loaded %d dbt doc blocks", len(self._doc_blocks))

        self._models = _parse_yml_files(models_dirs, self._doc_blocks)
        logger.info("Loaded metadata for %d dbt models", len(self._models))

    def _is_in_model_path(self, file_path: str) -> bool:
        """Check if a file is within a dbt model directory."""
        normalized = file_path.replace("\\", "/")
        return any(normalized.startswith(prefix) for prefix in self._model_path_prefixes)

    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        if not self._is_in_model_path(file_path):
            return None
        stem = Path(file_path).stem
        model = self._models.get(stem)
        if model is not None:
            return model.to_file_context()
        return None

    def get_metadata(self) -> dict:
        """Return dbt column metadata for index persistence."""
        dbt_columns: dict[str, dict[str, str]] = {}
        for model_name, model in self._models.items():
            if model.columns:
                dbt_columns[model_name] = dict(model.columns)
        if not dbt_columns:
            return {}
        return {"dbt_columns": dbt_columns}

    def stats(self) -> dict:
        return {
            "doc_blocks": len(self._doc_blocks),
            "models_with_metadata": len(self._models),
        }
