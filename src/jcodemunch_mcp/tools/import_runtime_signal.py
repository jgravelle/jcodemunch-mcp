"""MCP tool: import_runtime_signal.

Phase 1 ingest entrypoint exposed both as the CLI ``import-trace``
subcommand and as the MCP tool ``import_runtime_signal``. Resolves the
repo identifier, opens the per-repo SQLite database, and dispatches to
the runtime ingest orchestrator.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from .. import config as _config_mod
from ..runtime import (
    VALID_SOURCES,
    ingest_diagnostics_file,
    ingest_otel_file,
    ingest_sql_log_file,
    ingest_stack_log_file,
)
from ..storage import IndexStore
from .resolve_repo import resolve_repo

logger = logging.getLogger(__name__)


def import_runtime_signal(
    *,
    source: str,
    path: str,
    repo: Optional[str] = None,
    redact_enabled: Optional[bool] = None,
    storage_path: Optional[str] = None,
    format: Optional[str] = None,
) -> dict[str, Any]:
    """Import a runtime trace file into the runtime_* tables for a repo.

    Args:
        source: One of ``{'otel', 'sql_log', 'stack_log', 'diagnostics',
            'apm'}``. Phase 1 implemented ``'otel'``; Phase 4 added
            ``'sql_log'``; Phase 5 added ``'stack_log'``; ``'diagnostics'``
            (2026-09) reads a type checker's / linter's own output file
            (mypy / pyright / tsc / ruff / generic JSONL) into the
            ``diagnostics`` SNAPSHOT table, replaced per tool; ``'apm'`` is
            reserved.
        path: Path to the trace file.
        repo: Repo identifier as ``owner/name`` or just ``name``. If
            omitted, defaults to resolving the current working directory
            via ``resolve_repo``.
        redact_enabled: Override the ``runtime_redact_enabled`` config key.
            Defaults to the config value (True). Disable only for offline
            debugging on synthetic data.
        storage_path: Custom storage path (matches other tools).
        format: ``source='diagnostics'`` only. One of ``mypy`` / ``pyright`` /
            ``tsc`` / ``ruff`` / ``generic``; default auto-detects from the
            file's CONTENT. Pass it for an EMPTY file (a clean run is a valid
            snapshot only when the tool is named).

    Returns:
        ``{
            'success': bool,
            'repo': '<owner>/<name>',
            'source': '<source>',
            'records': N,
            'mapped': M,
            'unmapped': K,
            'redactions_fired': {...},
            'unmapped_reasons': {...},
            'evicted': X,
        }``
    """
    if source not in VALID_SOURCES:
        return {
            "success": False,
            "error": f"unknown source {source!r}. Valid: {sorted(VALID_SOURCES)}",
        }
    if source not in ("otel", "sql_log", "stack_log", "diagnostics"):
        return {
            "success": False,
            "error": (
                f"source {source!r} is not yet implemented. "
                "Supported: source='otel', 'sql_log', 'stack_log', 'diagnostics'."
            ),
        }

    # Resolve repo → (owner, name)
    if repo:
        if "/" in repo:
            owner, name = repo.split("/", 1)
        else:
            owner, name = "local", repo
    else:
        resolved = resolve_repo(path=str(Path.cwd()), storage_path=storage_path)
        if not resolved.get("indexed"):
            return {
                "success": False,
                "error": (
                    "could not resolve current directory to an indexed repo. "
                    "Pass --repo <owner/name> or run `jcodemunch-mcp index .` first."
                ),
            }
        repo_id = resolved["repo"]
        owner, name = repo_id.split("/", 1)

    store = IndexStore(base_path=storage_path)
    db_path = store._sqlite._db_path(owner, name)  # type: ignore[attr-defined]
    if not db_path.exists():
        return {
            "success": False,
            "error": f"index database not found for {owner}/{name}; run `jcodemunch-mcp index` first.",
        }

    cfg = _config_mod.get
    if redact_enabled is None:
        redact_enabled = bool(cfg("runtime_redact_enabled", True))
    max_rows = int(cfg("runtime_max_rows", 100_000))

    try:
        if source == "otel":
            result = ingest_otel_file(
                db_path=str(db_path),
                file_path=path,
                redact_enabled=redact_enabled,
                max_rows=max_rows,
            )
        elif source == "sql_log":
            result = ingest_sql_log_file(
                db_path=str(db_path),
                file_path=path,
                redact_enabled=redact_enabled,
                max_rows=max_rows,
            )
        elif source == "stack_log":
            result = ingest_stack_log_file(
                db_path=str(db_path),
                file_path=path,
                redact_enabled=redact_enabled,
                max_rows=max_rows,
            )
        else:  # diagnostics
            result = ingest_diagnostics_file(
                db_path=str(db_path),
                file_path=path,
                redact_enabled=redact_enabled,
                max_rows=max_rows,
                fmt=format or "auto",
            )
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}
    except ValueError as e:
        # An unrecognised diagnostics shape: refuse rather than guess lines.
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "repo": f"{owner}/{name}",
        "source": source,
        **result,
    }
