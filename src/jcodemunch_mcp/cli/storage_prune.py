"""`jcodemunch-mcp storage prune`: drop the indexes whose folder is gone.

A server start already sweeps them (`cleanup_orphan_indexes`). A store that
only the `index` CLI writes to is opened by no server, so nothing is ever
swept: a setup that indexes one worktree per lane keeps every lane's index
after the worktree is removed (V2-BACKLOG row 25).

⚠ It lists by default and deletes only with `--yes`. A delete cannot be
undone, and the only way back is a re-index of a folder that no longer exists.
⚠ A folder on a drive or share that is not there right now is `unreachable`,
never `gone`: an unplugged disk is not a removed checkout. Those are kept
unless `--include-unreachable` is passed.
⚠ The folder is looked at again just before its index is deleted, so a
checkout that came back between the listing and the delete is kept.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

logger = logging.getLogger(__name__)

PRESENT = "present"
GONE = "gone"
UNREACHABLE = "unreachable"

# Folders a POSIX system mounts removable or network volumes under. A source
# root whose nearest existing ancestor is one of these (or one level below the
# per-user ones) sat on a volume that is not mounted now.
_MOUNT_PARENTS = ("/Volumes", "/mnt", "/media", "/run/media")


def _exists(path: str) -> bool:
    return os.path.exists(path)


def folder_state(source_root: str) -> str:
    """`present`, `gone`, or `unreachable` for one index's source folder."""
    try:
        if os.path.isdir(source_root):
            return PRESENT
    except OSError:
        return UNREACHABLE
    windows = PureWindowsPath(source_root)
    if windows.drive:
        # `D:\\` or `\\\\server\\share\\`: the volume itself is absent.
        try:
            return GONE if _exists(windows.anchor) else UNREACHABLE
        except OSError:
            return UNREACHABLE
    posix = PurePosixPath(source_root)
    if posix.is_absolute():
        for ancestor in posix.parents:
            try:
                there = _exists(str(ancestor))
            except OSError:
                return UNREACHABLE
            if there:
                near = str(ancestor)
                if near in _MOUNT_PARENTS or str(ancestor.parent) in ("/media", "/run/media"):
                    return UNREACHABLE
                break
    return GONE


def _slug_bytes(storage_path: Optional[str]) -> dict[str, int]:
    from .storage_report import storage_report

    rep = storage_report(storage_path, top=-1)
    return {r["name"]: r["total_bytes"] for r in rep.get("indexes", [])}


def prune(
    storage_path: Optional[str] = None,
    delete: bool = False,
    include_unreachable: bool = False,
) -> dict:
    """List the indexes whose folder is gone; delete them when *delete* is set.

    A remote index (no source folder) and a starter pack are never candidates,
    by the same two rules the server-start sweep uses.
    """
    from ..storage import IndexStore
    from ..storage.sqlite_store import _default_base_path, is_pack_clone_path

    base = Path(storage_path).expanduser().resolve() if storage_path else _default_base_path()
    out: dict = {
        "path": str(base),
        "exists": base.is_dir(),
        "dry_run": not delete,
        "orphans": [],
        "deleted": 0,
        "freed_bytes": 0,
        "kept_unreachable": 0,
        "failed": 0,
    }
    if not out["exists"]:
        # An IndexStore creates its folder; a prune must not invent the store.
        return out
    sizes = _slug_bytes(str(base))
    store = IndexStore(base_path=str(base))
    try:
        for entry in store.list_repos():
            source_root = entry.get("source_root", "") or ""
            repo_id = entry.get("repo", "")
            parts = repo_id.split("/", 1)
            if not source_root or len(parts) != 2 or is_pack_clone_path(source_root):
                continue
            state = folder_state(source_root)
            if state == PRESENT:
                continue
            owner, name = parts
            row = {
                "repo": repo_id,
                "source_root": source_root,
                "state": state,
                "bytes": sizes.get(f"{owner}-{name}"),
                "deleted": False,
            }
            out["orphans"].append(row)
            wanted = state == GONE or include_unreachable
            if state == UNREACHABLE and not include_unreachable:
                out["kept_unreachable"] += 1
            if not (delete and wanted):
                continue
            if folder_state(source_root) == PRESENT:
                row["state"] = PRESENT  # it came back since the listing
                continue
            try:
                row["deleted"] = bool(store.delete_index(owner, name, force=True))
            except Exception as exc:
                logger.debug("storage prune: %s not deleted", repo_id, exc_info=True)
                row["error"] = f"{type(exc).__name__}: {exc}"
            if row["deleted"]:
                out["deleted"] += 1
                out["freed_bytes"] += row["bytes"] or 0
            else:
                out["failed"] += 1
    finally:
        store.close()
    out["orphans"].sort(key=lambda r: (-(r["bytes"] or 0), r["repo"]))
    return out


def _print_text(rep: dict) -> None:
    from .storage_report import _human

    print(f"Index store: {rep['path']}")
    if not rep["exists"]:
        print("It does not exist; nothing to prune.")
        return
    rows = rep["orphans"]
    if not rows:
        print("Every index's folder is still there. Nothing to prune.")
        return
    for r in rows:
        size = _human(r["bytes"]) if r["bytes"] is not None else "size unknown"
        if r["deleted"]:
            mark = "deleted"
        elif r.get("error"):
            mark = f"NOT deleted ({r['error']})"
        elif r["state"] == PRESENT:
            mark = "kept (the folder is back)"
        elif r["state"] == UNREACHABLE:
            mark = "unreachable (its drive or share is not there now)"
        else:
            mark = "folder gone"
        print(f"  {size:>10}  {r['repo']}  {mark}")
        print(f"              {r['source_root']}")
    gone = [r for r in rows if r["state"] == GONE]
    print()
    if rep["dry_run"]:
        total = sum(r["bytes"] or 0 for r in gone)
        print(f"{len(gone)} index(es) with a removed folder, {_human(total)}. Nothing was deleted.")
        if gone:
            print("Delete them with: jcodemunch-mcp storage prune --yes")
    else:
        print(f"Deleted {rep['deleted']} index(es), {_human(rep['freed_bytes'])} freed.")
        if rep["failed"]:
            print(f"{rep['failed']} could not be deleted.")
    if rep["kept_unreachable"]:
        print(
            f"{rep['kept_unreachable']} kept because the drive or share is not there now. "
            "Add --include-unreachable to drop those too."
        )


def run(args, storage_path: Optional[str] = None) -> int:
    rep = prune(
        storage_path,
        delete=bool(getattr(args, "yes", False)),
        include_unreachable=bool(getattr(args, "include_unreachable", False)),
    )
    if getattr(args, "as_json", False):
        print(json.dumps(rep, indent=2))
    else:
        _print_text(rep)
    return 1 if rep["failed"] else 0
