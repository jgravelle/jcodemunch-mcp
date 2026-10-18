"""`jcodemunch-mcp diagnose`: one local report a user can read and hand over.

The status commands print full paths and repository names, so a user asked
what the product did for them had nothing safe to send. This builds a report
from a FIXED LIST of fields and writes it to one file in the storage
directory. It sends nothing.

⚠⚠ **An allowlist of keys AND of what each value may be, checked on every
build** (`check_allowed`). A report is assembled from several stores, and a
field added to one of them later must not ride along because a dict was copied
whole: every block is built key by key, a key nobody listed raises, and so
does a value that is not the type, pattern or word its key allows. ⚠ The first
draft checked keys only, and `surface.profile`, which is the user's own
`tool_profile` string, carried a path into the report (review, 2026-10-13).
(Standing lesson 08-28: a denylist catches the instance, an allowlist the class.)

⚠⚠ **No name and no path.** An index is `index-N`, ordered by its file name
and never carrying it. A tool name is kept only when the caller's catalog
holds it; any other is added into `other`, because the savings and counter
files are keyed by strings this module did not choose. A surface or profile
that is not one of the shipped words reads `custom`.

⚠ `anon_id` is read only when the caller asks (`include_anon_id`). It is the
one value here that links a report to the savings telemetry.

⚠ UNKNOWN is `None` or `"unknown"`, never `0`, `False` or `"no"`: an index
that could not be opened is listed with its size on disk and unknown counts,
and a savings file that could not be read gives no saved figure at all.

⚠ **Two start dates.** Tokens saved have been recorded far longer than calls
and tokens returned, so each carries its own date (`saved_since`,
`counting_since`). One date over both would set a lifetime of savings beside
a day of calls.

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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, NoReturn, Optional

logger = logging.getLogger(__name__)

SCHEMA = "jcodemunch.diagnose/v1"
REPORT_DIR = "diagnose"
OTHER = "other"
CUSTOM = "custom"
ESTIMATOR = "bytes/4"

_SERVERS = ("jcodemunch-mcp", "jdocmunch-mcp", "jdatamunch-mcp")
POLICY_TARGETS = ("claude_md_global", "claude_md_project", "cursor_rules", "windsurf_rules", "agents_md")
_SURFACES = ("full", "counter")
_PROFILES = ("core", "standard", "full")


def _is_count(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _count_or_none(v: object) -> bool:
    return v is None or _is_count(v)


def _matches(pattern: str, *, none: bool = False) -> Callable[[object], bool]:
    rx = re.compile(pattern)
    return lambda v: (none and v is None) or (isinstance(v, str) and rx.fullmatch(v) is not None)


def _one_of(*words: object) -> Callable[[object], bool]:
    return lambda v: any(v is w or (isinstance(v, str) and v == w) for w in words)


_DATE = r"\d{4}-\d{2}-\d{2}"
_VERSION = _matches(r"[0-9A-Za-z][0-9A-Za-z.+!-]{0,39}", none=True)

# Every field a report may carry, and what its value may be.
_VERSIONS = {**{name: _VERSION for name in _SERVERS},
             "python": _matches(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]{0,20}"),
             "os": _matches(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,59}")}
_SURFACE = {
    "surface": _one_of(*_SURFACES, CUSTOM, None),
    "profile": _one_of(*_PROFILES, CUSTOM, None),
    "visible_tools": _count_or_none,
    "catalog_tools": _count_or_none,
    "schema_tokens_visible": _count_or_none,
    "estimator": _one_of(ESTIMATOR, None),
}
_INDEX = {
    "label": _matches(r"index-[1-9][0-9]*"),
    "file_count": _count_or_none,
    "symbol_count": _count_or_none,
    "source_bytes": _count_or_none,
    "index_bytes": _is_count,
    "embeddings": _one_of("yes", "no", "unknown"),
}
_POLICY = {
    "present": _one_of(True, False),
    "state": _one_of("current", "differs", None),
    "digest": _matches(r"[0-9a-f]{12}", none=True),
}
_TOOL = {"calls": _count_or_none, "tokens_returned": _count_or_none, "tokens_saved": _count_or_none}
_TOOLS = {
    "estimator": _one_of(ESTIMATOR),
    "counting_since": _matches(_DATE, none=True),
    "saved_since": _matches(_DATE, none=True),
    "tokens_saved_total": _count_or_none,
    "by_tool": None,  # a block, checked row by row
}
_LEAVES = {
    "schema": _one_of(SCHEMA),
    "generated_at": _matches(_DATE + r"T\d{2}:\d{2}:\d{2}Z"),
    "sent_anywhere": _one_of(False),
    "anon_id": _matches(r"[0-9a-fA-F-]{8,64}", none=True),
}
_BLOCKS = ("versions", "surface", "indexes", "policies", "tools")


def _fail(what: str) -> NoReturn:
    raise ValueError(f"diagnose: {what} is not on the list of what a report may carry")


def _check_block(where: str, block: object, spec: Mapping) -> None:
    if not isinstance(block, Mapping):
        _fail(f"value of {where}")
    for key, value in block.items():
        if key not in spec:
            _fail(f"key {where}.{key!r}")
        if spec[key] is not None and not spec[key](value):
            _fail(f"value of {where}.{key}")


def check_allowed(report: Mapping, known_tools: Optional[Iterable[str]] = None) -> None:
    """Raise `ValueError` naming the first key, label, name or value the list does not hold."""
    for key, value in report.items():
        if key in _LEAVES:
            if not _LEAVES[key](value):
                _fail(f"value of {key}")
        elif key not in _BLOCKS:
            _fail(f"key {key!r}")
    if "versions" in report:
        _check_block("versions", report["versions"], _VERSIONS)
    if "surface" in report:
        _check_block("surface", report["surface"], _SURFACE)
    indexes = report.get("indexes", [])
    if not isinstance(indexes, list):
        _fail("value of indexes")
    for row in indexes:
        if isinstance(row, Mapping) and "label" in row and not _INDEX["label"](row["label"]):
            _fail(f"index label {row['label']!r}")
        _check_block("indexes[]", row, _INDEX)
    policies = report.get("policies", {})
    _check_block("policies", policies, {"this_version_digest": _matches(r"[0-9a-f]{12}"), "targets": None})
    targets = policies.get("targets", {})
    if not isinstance(targets, Mapping):
        _fail("value of policies.targets")
    for target, row in targets.items():
        if target not in POLICY_TARGETS:
            _fail(f"policy target {target!r}")
        _check_block(f"policies.targets.{target}", row, _POLICY)
    tools = report.get("tools", {})
    _check_block("tools", tools, _TOOLS)
    by_tool = tools.get("by_tool", {})
    if not isinstance(by_tool, Mapping):
        _fail("value of tools.by_tool")
    allowed_names = None if known_tools is None else set(known_tools) | {OTHER}
    for name, row in by_tool.items():
        if not isinstance(name, str) or (allowed_names is not None and name not in allowed_names):
            _fail(f"tool name {name!r}")
        _check_block(f"tools.by_tool.{name}", row, _TOOL)


def _storage(storage_path: Optional[str]) -> Path:
    return Path(storage_path or os.environ.get("CODE_INDEX_PATH") or str(Path.home() / ".code-index"))


def _versions() -> dict:
    from importlib import metadata

    out: dict = {}
    for dist in _SERVERS:
        try:
            out[dist] = _public_version(metadata.version(dist))
        except metadata.PackageNotFoundError:
            out[dist] = None  # not installed in this environment
    python = platform.python_version()
    out["python"] = python if _VERSIONS["python"](python) else ".".join(str(n) for n in sys.version_info[:3])
    out["os"] = _os_label(platform.system(), platform.release())
    return out


def _public_version(version: object) -> Optional[str]:
    """A version without its local label: `1.2.3+local.name` ends in the builder's own string."""
    public = str(version).split("+", 1)[0]
    return public if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.!-]{0,39}", public) else None


def _os_label(system: str, release: str) -> str:
    """System name and the numeric start of its release.

    A kernel release can end in a suffix its builder chose
    (`6.6.30-alice-laptop`), so only the leading numbers are kept.
    """
    name = system if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,19}", system or "") else "unknown"
    number = re.match(r"[0-9]+(?:[.][0-9]+){0,3}", release or "")
    return f"{name} {number.group(0)}" if number else name


def _surface(stats: Mapping) -> dict:
    """The six surface fields. A surface or profile word this version does not ship reads `custom`."""

    def word(value: object, shipped: tuple) -> object:
        return value if value is None or value in shipped else CUSTOM

    def count(value: object) -> object:
        return value if _is_count(value) else None

    return {
        "surface": word(stats.get("surface"), _SURFACES),
        "profile": word(stats.get("profile"), _PROFILES),
        "visible_tools": count(stats.get("visible_tools")),
        "catalog_tools": count(stats.get("catalog_tools")),
        "schema_tokens_visible": count(stats.get("schema_tokens_visible")),
        "estimator": ESTIMATOR if stats.get("estimator") == ESTIMATOR else None,
    }


def _indexes(root: Path) -> list[dict]:
    from .storage.embedding_store import EmbeddingStore
    from .storage.generation import connect_readonly
    from .storage.sqlite_store import INDEX_DB_NOT_INDEX, classify_index_db

    rows: list[dict] = []
    try:
        candidates = sorted(root.glob("*.db"))
    except OSError:
        logger.debug("diagnose: storage directory not listed", exc_info=True)
        return rows
    dbs = []
    for db in candidates:
        try:
            if classify_index_db(db) == INDEX_DB_NOT_INDEX:
                continue  # another store, by content (L-144)
        except Exception:
            logger.debug("diagnose: %s not classified", db.name, exc_info=True)
        dbs.append(db)
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
            except FileNotFoundError:
                pass  # no sidecar: the usual case
            except OSError:
                logger.debug("diagnose: a part of index %d was not measured", n, exc_info=True)
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
        except FileNotFoundError:
            text = None
        except (OSError, UnicodeDecodeError):
            logger.debug("diagnose: policy target %s not read", target, exc_info=True)
            text = None
        block = _policy.installed_policy_block(text) if text is not None else None
        if block is not None:
            row = {"present": True, "state": "current" if block == current else "differs", "digest": _digest(block)}
        targets[target] = row
    return {"this_version_digest": _digest(current), "targets": targets}


def _count(value: object) -> int:
    return value if _is_count(value) else 0  # type: ignore[return-value]


def _savings(root: Path) -> Optional[dict]:
    """The savings file: `{}` when there is none, `None` when it could not be read."""
    try:
        data = json.loads((root / "_savings.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.debug("diagnose: savings file not read", exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def _date(value: object) -> Optional[str]:
    return value if isinstance(value, str) and re.fullmatch(_DATE, value) else None


def _tools(root: Path, savings: Optional[dict], known: set) -> dict:
    from .storage import tool_counters

    counters = tool_counters.read_or_none(str(root))
    by_tool: dict = {}
    # A file that exists and could not be read gives no figure: not saved tokens
    # from an unreadable savings file, not calls from an unreadable counters
    # file. ⚠ The second was missed when the first was fixed (review, 2026-10-13).
    blank = 0 if savings is not None else None
    uncounted = 0 if counters is not None else None

    def row(name: str) -> dict:
        key = name if name in known else OTHER
        return by_tool.setdefault(key, {"calls": uncounted, "tokens_returned": uncounted, "tokens_saved": blank})

    for name, c in (counters["tools"].items() if counters is not None else ()):
        r = row(name)
        r["calls"] += _count(c.get("calls"))
        r["tokens_returned"] += _count(c.get("tokens_returned"))
    saved = (savings or {}).get("by_tool")
    for name, n in (saved.items() if isinstance(saved, dict) else ()):
        row(str(name))["tokens_saved"] += _count(n)
    # No savings file is nothing saved; a total that is not a count is unknown.
    total = savings.get("total_tokens_saved", 0) if savings is not None else None
    return {
        "estimator": ESTIMATOR,
        "counting_since": _date(counters["since"]) if counters is not None else None,
        "saved_since": _date((savings or {}).get("by_tool_since")),
        "tokens_saved_total": total if _is_count(total) else None,
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
    report: dict = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sent_anywhere": False,
        "versions": _versions(),
        "surface": _surface(surface_stats or {}),
        "indexes": _indexes(root),
        "policies": _policies(policy_paths if policy_paths is not None else _default_policy_paths()),
        "tools": _tools(root, savings, known),
    }
    if include_anon_id:
        anon = (savings or {}).get("anon_id")
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
