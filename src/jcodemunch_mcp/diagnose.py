"""`jcodemunch-mcp diagnose`: one local report a user can read and hand over.

The status commands print full paths and repository names, so a user asked
what the product did for them had nothing safe to send. This builds a report
from a FIXED LIST of fields and writes it to one file in the storage
directory. It sends nothing.

⚠⚠ **An allowlist, checked on every build** (`check_allowed`). A report is
assembled from several stores, and a field added to one of them later must not
ride along because a dict was copied whole: every block here is built key by
key, and a key nobody listed raises. (Standing lesson 08-28: a denylist catches
the instance, an allowlist the class.)

⚠⚠ **No name and no path.** An index is `index-N`, ordered by its file name
and never carrying it. A tool name is kept only when the caller's catalog
holds it; any other is added into `other`, because the savings and counter
files are keyed by strings this module did not choose.

⚠ `anon_id` is read only when the caller asks (`include_anon_id`). It is the
one value here that links a report to the savings telemetry.

⚠ UNKNOWN is `None` or `"unknown"`, never `0`, `False` or `"no"`: an index
that could not be opened is listed with its size on disk and unknown counts.

⚠ This module imports no network library and does not import `server`; the
caller passes the surface figures in. Both are asserted by
`tests/test_diagnose_writes_only_what_is_on_its_list.py`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

SCHEMA = "jcodemunch.diagnose/v1"
REPORT_DIR = "diagnose"
OTHER = "other"

_SERVERS = ("jcodemunch-mcp", "jdocmunch-mcp", "jdatamunch-mcp")
_SURFACE_FIELDS = ("surface", "profile", "visible_tools", "catalog_tools", "schema_tokens_visible", "estimator")
_INDEX_FIELDS = ("label", "file_count", "symbol_count", "source_bytes", "index_bytes", "embeddings")
_TOOL_FIELDS = ("calls", "tokens_returned", "tokens_saved")
_POLICY_FIELDS = ("present", "state", "digest")
POLICY_TARGETS = ("claude_md_global", "claude_md_project", "cursor_rules", "windsurf_rules", "agents_md")
_LABEL = re.compile(r"index-[1-9][0-9]*")

# key -> the keys allowed beneath it (None: a leaf).
_TOP = {
    "schema": None,
    "generated_at": None,
    "sent_anywhere": None,
    "versions": frozenset(_SERVERS) | {"python", "os"},
    "surface": frozenset(_SURFACE_FIELDS),
    "indexes": frozenset(_INDEX_FIELDS),
    "policies": frozenset({"this_version_digest", "targets"}),
    "tools": frozenset({"estimator", "counting_since", "tokens_saved_total", "by_tool"}),
    "anon_id": None,
}


def _fail(what: str) -> None:
    raise ValueError(f"diagnose: {what} is not on the list of fields a report may carry")


def check_allowed(report: Mapping, known_tools: Optional[Iterable[str]] = None) -> None:
    """Raise `ValueError` naming the first key or label the list does not hold."""
    for key in report:
        if key not in _TOP:
            _fail(f"key {key!r}")
    for block in ("versions", "surface"):
        for key in report.get(block) or {}:
            if key not in _TOP[block]:
                _fail(f"key {block}.{key!r}")
    for row in report.get("indexes") or []:
        for key in row:
            if key not in _TOP["indexes"]:
                _fail(f"key indexes[].{key!r}")
        if not _LABEL.fullmatch(str(row.get("label"))):
            _fail(f"index label {row.get('label')!r}")
    policies = report.get("policies") or {}
    for key in policies:
        if key not in _TOP["policies"]:
            _fail(f"key policies.{key!r}")
    for target, row in (policies.get("targets") or {}).items():
        if target not in POLICY_TARGETS:
            _fail(f"policy target {target!r}")
        for key in row:
            if key not in _POLICY_FIELDS:
                _fail(f"key policies.targets.{target}.{key!r}")
    tools = report.get("tools") or {}
    for key in tools:
        if key not in _TOP["tools"]:
            _fail(f"key tools.{key!r}")
    allowed_names = None if known_tools is None else set(known_tools) | {OTHER}
    for name, row in (tools.get("by_tool") or {}).items():
        if allowed_names is not None and name not in allowed_names:
            _fail(f"tool name {name!r}")
        for key in row:
            if key not in _TOOL_FIELDS:
                _fail(f"key tools.by_tool.{name}.{key!r}")


def _storage(storage_path: Optional[str]) -> Path:
    return Path(storage_path or os.environ.get("CODE_INDEX_PATH") or str(Path.home() / ".code-index"))


def _versions() -> dict:
    from importlib import metadata

    out: dict = {}
    for dist in _SERVERS:
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = None  # not installed in this environment
    out["python"] = platform.python_version()
    out["os"] = f"{platform.system()} {platform.release()}".strip()
    return out


def _indexes(root: Path) -> list[dict]:
    from .storage.embedding_store import EmbeddingStore
    from .storage.generation import connect_readonly
    from .storage.sqlite_store import _NON_REPO_DB_FILES

    rows = []
    try:
        dbs = sorted(p for p in root.glob("*.db") if p.name not in _NON_REPO_DB_FILES)
    except OSError:
        logger.debug("diagnose: storage directory not listed", exc_info=True)
        return rows
    for n, db in enumerate(dbs, start=1):
        files = symbols = source = None
        embeddings = "unknown"
        try:
            conn = connect_readonly(db)
            try:
                files, unsized, total = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(size_bytes IS NULL), 0), SUM(size_bytes) FROM files"
                ).fetchone()
                # Some indexing routes store no file size. A sum over the files that
                # have one would read as the whole corpus, so one missing size makes
                # the total unknown.
                source = (total or 0) if not unsized else None
                symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            finally:
                conn.close()
            has = EmbeddingStore(db).has_any()
            embeddings = "unknown" if has is None else ("yes" if has else "no")
        except Exception:
            # An unreadable index is still an index on disk: list it, counts unknown.
            logger.debug("diagnose: index %d not read", n, exc_info=True)
            files = symbols = source = None
            embeddings = "unknown"
        size = 0
        for part in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
            try:
                size += part.stat().st_size
            except OSError:
                pass
        rows.append({
            "label": f"index-{n}",
            "file_count": files,
            "symbol_count": symbols,
            "source_bytes": source,
            "index_bytes": size,
            "embeddings": embeddings,
        })
    return rows


def _default_policy_paths() -> dict:
    from .cli import init as _init

    return {
        "claude_md_global": _init._claude_md_path("global"),
        "claude_md_project": _init._claude_md_path("project"),
        "cursor_rules": _init._cursor_rules_path(),
        "windsurf_rules": _init._windsurf_rules_path(),
        "agents_md": Path.cwd() / "AGENTS.md",
    }


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def _policies(paths: Mapping[str, Path]) -> dict:
    from .cli import policy as _policy

    current = _policy._normalise(_policy.active_policy().splitlines())
    targets = {}
    for target, path in paths.items():
        if target not in POLICY_TARGETS:
            continue
        row = {"present": False, "state": None, "digest": None}
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = None
        block = _policy.installed_policy_block(text) if text is not None else None
        if block is not None:
            row = {"present": True, "state": "current" if block == current else "differs", "digest": _digest(block)}
        targets[target] = row
    return {"this_version_digest": _digest(current), "targets": targets}


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _savings(root: Path) -> dict:
    try:
        data = json.loads((root / "_savings.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _tools(root: Path, savings: dict, known: set) -> dict:
    from .storage import tool_counters

    counters = tool_counters.read(str(root))
    since = counters["since"]
    by_tool: dict = {}

    def row(name: str) -> dict:
        key = name if name in known else OTHER
        return by_tool.setdefault(key, {"calls": 0, "tokens_returned": 0, "tokens_saved": 0})

    for name, c in counters["tools"].items():
        r = row(name)
        r["calls"] += _count(c.get("calls"))
        r["tokens_returned"] += _count(c.get("tokens_returned"))
    saved = savings.get("by_tool")
    for name, n in (saved.items() if isinstance(saved, dict) else ()):
        row(str(name))["tokens_saved"] += _count(n)
    return {
        "estimator": counters["estimator"],
        "counting_since": since if isinstance(since, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since) else None,
        "tokens_saved_total": _count(savings.get("total_tokens_saved")),
        "by_tool": {k: by_tool[k] for k in sorted(by_tool)},
    }


def build_report(
    *,
    storage_path: Optional[str] = None,
    surface_stats: Optional[Mapping] = None,
    known_tools: Iterable[str] = (),
    policy_paths: Optional[Mapping[str, Path]] = None,
    include_anon_id: bool = False,
) -> dict:
    """Assemble the report. Every block is built key by key and checked before return."""
    root = _storage(storage_path)
    known = set(known_tools)
    savings = _savings(root)
    stats = surface_stats or {}
    report: dict = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sent_anywhere": False,
        "versions": _versions(),
        "surface": {k: stats.get(k) for k in _SURFACE_FIELDS},
        "indexes": _indexes(root),
        "policies": _policies(policy_paths if policy_paths is not None else _default_policy_paths()),
        "tools": _tools(root, savings, known),
    }
    if include_anon_id:
        anon = savings.get("anon_id")
        report["anon_id"] = anon if isinstance(anon, str) and re.fullmatch(r"[0-9a-fA-F-]{8,64}", anon) else None
    check_allowed(report, known)
    return report


def write_report(report: Mapping, storage_path: Optional[str] = None) -> Path:
    """Write *report* under `<storage>/diagnose/` and return the file's path."""
    out_dir = _storage(storage_path) / REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"jcodemunch-diagnose-{stamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path
