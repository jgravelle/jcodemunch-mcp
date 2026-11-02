"""`jcodemunch-mcp storage`: how much disk the index store uses, and where.

`diagnose` lists each index's size without its name, and counts no total. This
report names every index by its store slug, splits database bytes (`<slug>.db`
and its sidecars) from the content cache (`<slug>/`), and lists everything else
in the store, largest first.

⚠ It only stats files. It never opens a database, so it can't move an index's
mtime (the key of the in-memory index cache) or create a `-wal`/`-shm` file.
⚠ A file it can't stat is counted in `unreadable`, never as 0 bytes, and a
symlink is never followed, so nothing outside the store is counted.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import stat
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TOP = 10
# stat.FILE_ATTRIBUTE_REPARSE_POINT, spelled out: the constant exists on Windows only.
_REPARSE_POINT = 0x400


def _size_of(path: str) -> int:
    """Size of one regular file, without following a link."""
    return os.lstat(path).st_size


def _is_link(path: str) -> bool:
    """A symlink, a Windows junction, or any other reparse point.

    `os.path.islink` and `os.walk(followlinks=False)` don't see a junction, so
    a junction out of the store would be walked and counted (review)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _walk_bytes(path: Path) -> tuple[int, int, int]:
    """(bytes, unreadable, links) for every regular file under `path`, or `path` itself.

    `unreadable` counts the files and directories that couldn't be measured.
    Their bytes are left out, never counted as 0. `links` counts the symlinks
    and junctions that were not followed, so a link never reads as an empty
    entry (review)."""
    unreadable = 0
    links = 0
    try:
        if _is_link(str(path)):
            return 0, 0, 1
        if not path.is_dir():
            return _size_of(str(path)), 0, 0
    except OSError:
        logger.debug("storage: %s not measured", path, exc_info=True)
        return 0, 1, 0
    total = 0

    def _onerror(err: OSError) -> None:
        nonlocal unreadable
        unreadable += 1
        logger.debug(
            "storage: %s not read", getattr(err, "filename", path), exc_info=True
        )

    for root, dirs, files in os.walk(path, followlinks=False, onerror=_onerror):
        kept = [d for d in dirs if not _is_link(os.path.join(root, d))]
        links += len(dirs) - len(kept)
        dirs[:] = kept
        for name in files:
            full = os.path.join(root, name)
            try:
                if _is_link(full):
                    links += 1
                    continue
                total += _size_of(full)
            except OSError:
                unreadable += 1
                logger.debug("storage: %s not measured", full, exc_info=True)
    return total, unreadable, links


def _index_slugs(entries: list[Path]) -> set[str]:
    """Slugs of the indexes in the store.

    An index file is `<owner>-<name>.db` (`_repo_slug`), so its stem always
    has a dash. The store's own databases (`telemetry.db`, `org_savings.db`,
    `parse_cache.db`) have none, so they're listed under `other`."""
    return {
        e.stem for e in entries if e.suffix == ".db" and "-" in e.stem and e.is_file()
    }


def _cut(rows: list[dict], top: Optional[int], key: str) -> tuple[list[dict], dict]:
    rows.sort(key=lambda r: (-r[key], r["name"]))
    if top is None or top < 0 or len(rows) <= top:
        return rows, {"count": 0, "bytes": 0}
    rest = rows[top:]
    return rows[:top], {"count": len(rest), "bytes": sum(r[key] for r in rest)}


def _empty(report: dict, measured: bool, unreadable: int) -> dict:
    report.update(
        measured=measured,
        total_bytes=0 if measured else None,
        indexes_bytes=0 if measured else None,
        other_bytes=0 if measured else None,
        unreadable=unreadable,
        indexes=[],
        indexes_more={"count": 0, "bytes": 0},
        other=[],
        other_more={"count": 0, "bytes": 0},
    )
    return report


def storage_report(
    storage_path: Optional[str] = None, top: Optional[int] = DEFAULT_TOP
) -> dict:
    """The store's size, by index and by other entry. Reads nothing but stats.

    `measured` is False when the store exists but can't be listed: the totals
    are then None, never 0, because nothing was counted."""
    # The store's own rule for its path, without constructing an IndexStore:
    # its __init__ creates the directory, and a report must not invent the
    # store it was asked about.
    from ..storage.sqlite_store import _default_base_path

    base = (
        Path(storage_path).expanduser().resolve()
        if storage_path
        else _default_base_path()
    )
    report: dict = {"path": str(base), "exists": base.is_dir()}
    if not report["exists"]:
        return _empty(report, measured=True, unreadable=0)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        logger.debug("storage: %s not listed", base, exc_info=True)
        return _empty(report, measured=False, unreadable=1)
    slugs = _index_slugs(entries)

    by_slug: dict[str, dict] = {
        s: {
            "name": s,
            "db_bytes": 0,
            "content_bytes": 0,
            "unreadable": 0,
            "links_not_followed": 0,
        }
        for s in slugs
    }
    other: list[dict] = []
    unreadable_total = 0
    for e in entries:
        owner, part = None, "db_bytes"
        if e.name in slugs:
            owner, part = e.name, "content_bytes"
        else:
            # `<slug>.db`, `<slug>.db-wal`, `<slug>.textfilter`, ...: the
            # longest matching slug wins, so `a-b` never takes `a-b-c`'s files.
            for s in sorted(slugs, key=len, reverse=True):
                if e.name.startswith(s + "."):
                    owner = s
                    break
        size, bad, links = _walk_bytes(e)
        unreadable_total += bad
        if owner is not None:
            by_slug[owner][part] += size
            by_slug[owner]["unreadable"] += bad
            by_slug[owner]["links_not_followed"] += links
        else:
            other.append(
                {"name": e.name, "bytes": size, "unreadable": bad, "links_not_followed": links}
            )

    indexes = list(by_slug.values())
    for r in indexes:
        r["total_bytes"] = r["db_bytes"] + r["content_bytes"]
    indexes_bytes = sum(r["total_bytes"] for r in indexes)
    other_bytes = sum(r["bytes"] for r in other)
    shown_idx, idx_more = _cut(indexes, top, "total_bytes")
    shown_other, other_more = _cut(other, top, "bytes")
    report.update(
        measured=True,
        total_bytes=indexes_bytes + other_bytes,
        indexes_bytes=indexes_bytes,
        other_bytes=other_bytes,
        unreadable=unreadable_total,
        indexes=shown_idx,
        indexes_more=idx_more,
        other=shown_other,
        other_more=other_more,
    )
    return report


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _links_note(row: dict) -> str:
    n = row.get("links_not_followed", 0)
    return f"  ({n} link(s) not followed, not counted)" if n else ""


def _print_text(rep: dict) -> None:
    if not rep["exists"]:
        print(f"Index store: {rep['path']} (does not exist; nothing indexed yet)")
        return
    print(f"Index store: {rep['path']}")
    if not rep["measured"]:
        print(
            "Not measured: the store's folder could not be listed, so no size is known."
        )
        return
    print(
        f"Total: {_human(rep['total_bytes'])}  "
        f"(indexes {_human(rep['indexes_bytes'])}, other {_human(rep['other_bytes'])})"
    )
    if rep["unreadable"]:
        print(
            f"Not measured: {rep['unreadable']} file(s) or folder(s) could not be read; "
            "the total leaves them out."
        )
    print()
    print("Indexes, largest first (database + content cache):")
    for r in rep["indexes"]:
        print(
            f"  {_human(r['total_bytes']):>10}  {r['name']}  "
            f"(db {_human(r['db_bytes'])}, content {_human(r['content_bytes'])})"
            + (f", {r['unreadable']} not measured" if r["unreadable"] else "")
            + _links_note(r)
        )
    if rep["indexes_more"]["count"]:
        m = rep["indexes_more"]
        print(f"  {_human(m['bytes']):>10}  ... {m['count']} more")
    if rep["other"]:
        print()
        print("Other entries in the store:")
        for r in rep["other"]:
            print(f"  {_human(r['bytes']):>10}  {r['name']}{_links_note(r)}")
        if rep["other_more"]["count"]:
            m = rep["other_more"]
            print(f"  {_human(m['bytes']):>10}  ... {m['count']} more")
    print()
    print("Remove an index with: jcodemunch-mcp delete-index <repo>")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON")
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        metavar="N",
        help=f"Rows per section (default {DEFAULT_TOP}; -1 for all)",
    )


def main(argv: Optional[list[str]] = None, storage_path: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="jcodemunch-mcp storage")
    add_arguments(parser)
    args = parser.parse_args(argv or [])
    return run(args, storage_path=storage_path)


def run(args: argparse.Namespace, storage_path: Optional[str] = None) -> int:
    rep = storage_report(storage_path, top=getattr(args, "top", DEFAULT_TOP))
    if getattr(args, "as_json", False):
        print(json.dumps(rep, indent=2))
    else:
        _print_text(rep)
    return 0
