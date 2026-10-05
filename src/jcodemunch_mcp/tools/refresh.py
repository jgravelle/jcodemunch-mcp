"""Bounded, resumable, repo-wide refresh (#395, @dkiaulakis).

We ask users to re-index constantly: in issue replies, in staleness warnings, in
the `stale_index` verdict channel, and on every `INDEX_VERSION` or
`PARSER_GENERATION` bump. All of that assumes re-indexing is cheap enough to run
on request. On one laptop it is. On a fleet sharing a store it is a scheduled
maintenance event, so our standard advice is unactionable for exactly the users
with the most code.

This turns "re-index the repo" from one unbounded event into a sequence of
bounded slices that an operator can schedule:

    jcodemunch-mcp refresh . --max-seconds 300     # in a cron slot, hourly

Each run does as much as its budget allows, persists exactly where it stopped,
and returns. Running it again continues. The work converges whether it gets one
hour or twenty short slices.

⚠⚠ **A campaign only stamps `parser_generation` when it has re-parsed the whole
corpus**, verified by re-running discovery at the end and refusing to stamp if
anything was missed. `storage/index_store.py` documents why: an incremental save
deliberately leaves the stamp alone, because a partially re-parsed index must
keep claiming the older generation. A campaign that stamps early is that bug
with a scheduler in front of it.

⚠ The slice budget bounds the CALLER, not the machine. `--pause-ms` is what
actually lowers the duty cycle: a slice that runs flat out for its whole budget
still saturates a core while it runs. The budget is checked between batches,
so a batch in progress finishes first (one file's parse is bounded separately,
by `JCODEMUNCH_PARSE_BUDGET_SECONDS`): a budget makes the run END on time, it
does not make it cheap while it runs.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_DIRNAME = "refresh_state"

# A slice small enough that an operator who passes no bounds at all still gets
# a bounded run rather than the maintenance event this exists to replace.
DEFAULT_MAX_FILES = 250
DEFAULT_MAX_SECONDS = 300.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _storage_root(storage_path: Optional[str]) -> Path:
    if storage_path:
        return Path(storage_path)
    return Path(os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index")))


def _state_path(storage_path: Optional[str], owner: str, name: str) -> Path:
    safe = f"{owner}__{name}".replace("/", "_").replace("\\", "_")
    return _storage_root(storage_path) / STATE_DIRNAME / f"{safe}.json"


def _read_state(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # A corrupt campaign file must not strand the operator: report no
        # campaign so the next run starts a fresh one, rather than raising on
        # every invocation forever.
        logger.warning("Unreadable refresh state at %s; starting fresh", path, exc_info=True)
        return None


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic: a killed run never leaves a half-written cursor


def _resolve_target(path: str, storage_path: Optional[str]):
    """Map a filesystem path to (store, owner, name, source_root)."""
    from ..storage.sqlite_store import SQLiteIndexStore
    from .index_folder import _local_repo_name

    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        return None, f"Not a directory: {path}"

    store = SQLiteIndexStore(base_path=str(_storage_root(storage_path)))
    for entry in store.list_repos():
        root = entry.get("source_root") or ""
        if root and Path(root).resolve() == folder:
            repo_id = entry.get("repo") or ""
            if "/" in repo_id:
                owner, name = repo_id.split("/", 1)
                return (store, owner, name, str(folder)), None

    # Not indexed under this path. Fall back to the identity index_folder would
    # mint, so `refresh` on a never-indexed folder reports "no index" rather
    # than "unknown repo", which is a different and more confusing answer.
    return (store, "local", _local_repo_name(folder), str(folder)), None


def _discover(source_root: str) -> list[str]:
    """The file list a full index_folder walk would produce, root-relative."""
    from .index_folder import discover_local_files
    from .. import config as _config

    max_files = _config.get("max_folder_files", 2000, repo=source_root)
    root = Path(source_root).resolve()
    files, _warnings, _skips = discover_local_files(root, max_files=max_files)
    rels = []
    for f in files:
        try:
            rels.append(Path(f).resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(set(rels))


def _target_generation() -> int:
    from ..storage.index_store import PARSER_GENERATION
    return PARSER_GENERATION


def _index_generation(store, owner: str, name: str) -> Optional[int]:
    index = store.load_index(owner, name)
    if index is None:
        return None
    return int(getattr(index, "parser_generation", 0) or 0)


def _index_files(store, owner: str, name: str) -> Optional[set]:
    """The file paths the index still holds, or None if it cannot be read.

    ⚠ None is could-not-establish, never "the index is empty". The caller must
    not read an unreadable index as an absent one -- that is the same
    UNKNOWN-is-not-False rule the rest of this tree runs on.
    """
    try:
        index = store.load_index(owner, name)
    except Exception:
        logger.warning("refresh: could not read index file list", exc_info=True)
        return None
    if index is None:
        return None
    return {str(f) for f in (getattr(index, "source_files", None) or [])}


def start_campaign(
    source_root: str, store, owner: str, name: str, storage_path: Optional[str]
) -> dict:
    """Enumerate the corpus once and persist it as the plan of work."""
    files = _discover(source_root)
    gen = _index_generation(store, owner, name)
    target = _target_generation()
    if gen is None:
        reason = "no_index"
    elif gen < target:
        reason = "parser_generation_upgrade"
    else:
        reason = "manual"
    state = {
        "repo": f"{owner}/{name}",
        "source_root": source_root,
        "reason": reason,
        "started_at": _now(),
        "last_run_at": None,
        "files": files,
        "cursor": 0,
        "slices": 0,
        "files_reparsed": 0,
        "generation_at_start": gen,
        "generation_target": target,
        "stamped": False,
        "errors": [],
    }
    _write_state(_state_path(storage_path, owner, name), state)
    return state


def status(path: str, storage_path: Optional[str] = None) -> dict:
    """Report campaign progress without doing any work."""
    target, err = _resolve_target(path, storage_path)
    if err:
        return {"success": False, "error": err}
    store, owner, name, source_root = target
    state = _read_state(_state_path(storage_path, owner, name))
    gen = _index_generation(store, owner, name)
    out = {
        "success": True,
        "repo": f"{owner}/{name}",
        "source_root": source_root,
        "index_present": gen is not None,
        "parser_generation": gen,
        "parser_generation_target": _target_generation(),
    }
    if gen is not None and gen < _target_generation():
        out["needs_refresh"] = True
        out["needs_refresh_reason"] = "parser_generation_upgrade"
    if state is None:
        out["campaign"] = None
        return out
    total = len(state.get("files") or [])
    cursor = int(state.get("cursor", 0))
    out["campaign"] = {
        "reason": state.get("reason"),
        "started_at": state.get("started_at"),
        "last_run_at": state.get("last_run_at"),
        "total_files": total,
        "completed_files": min(cursor, total),
        "remaining_files": max(total - cursor, 0),
        "percent": round(100.0 * cursor / total, 1) if total else 100.0,
        "slices_run": state.get("slices", 0),
        "files_reparsed": state.get("files_reparsed", 0),
        "complete": cursor >= total,
        "stamped": state.get("stamped", False),
    }
    if state.get("errors"):
        out["campaign"]["errors"] = state["errors"][-5:]
    return out


def run(
    path: str,
    max_files: Optional[int] = None,
    max_seconds: Optional[float] = None,
    pause_ms: int = 0,
    reset: bool = False,
    batch_size: int = 25,
    storage_path: Optional[str] = None,
    use_ai_summaries: bool = False,
) -> dict:
    """Run one bounded slice of a repo-wide refresh. Resumable across runs.

    ⚠ `use_ai_summaries` defaults to **False** here, unlike `index_folder`. A
    scheduled background refresh that silently bills a paid summarizer API per
    slice is the last thing a fleet operator wants, and they would not find out
    until the invoice. Opt in explicitly.
    """
    t0 = time.monotonic()
    max_files = DEFAULT_MAX_FILES if max_files is None else int(max_files)
    max_seconds = DEFAULT_MAX_SECONDS if max_seconds is None else float(max_seconds)
    if max_files <= 0 or max_seconds <= 0:
        return {"success": False, "error": "max_files and max_seconds must be positive"}

    target, err = _resolve_target(path, storage_path)
    if err:
        return {"success": False, "error": err}
    store, owner, name, source_root = target

    if _index_generation(store, owner, name) is None:
        return {
            "success": False,
            "error": (
                f"No index for {owner}/{name}. `refresh` re-parses an existing "
                f"index in bounded slices; it does not build the first one. "
                f"Run `jcodemunch-mcp index {source_root}` first."
            ),
        }

    state_path = _state_path(storage_path, owner, name)
    state = None if reset else _read_state(state_path)
    if state is None:
        state = start_campaign(source_root, store, owner, name, storage_path)
        started_new = True
    else:
        started_new = False

    files = state.get("files") or []
    cursor = int(state.get("cursor", 0))

    from .index_folder import index_folder

    processed = 0
    slice_errors: list[str] = []
    stopped_because = "complete"

    while cursor < len(files):
        if time.monotonic() - t0 >= max_seconds:
            stopped_because = "time_budget"
            break
        if processed >= max_files:
            stopped_because = "file_budget"
            break

        remaining_budget = min(batch_size, max_files - processed)
        batch = files[cursor:cursor + remaining_budget]
        if not batch:
            break

        try:
            res = index_folder(
                path=source_root,
                paths=list(batch),
                incremental=True,
                force_reparse=True,
                use_ai_summaries=use_ai_summaries,
                storage_path=storage_path,
                context_providers=False,
            )
            if not res.get("success", False):
                slice_errors.append(f"batch at {cursor}: {res.get('error')}")
        except Exception as e:  # a bad batch must not strand the campaign
            slice_errors.append(f"batch at {cursor}: {e}")
            logger.warning("refresh batch failed at cursor %d", cursor, exc_info=True)

        cursor += len(batch)
        processed += len(batch)

        if pause_ms > 0 and cursor < len(files):
            time.sleep(pause_ms / 1000.0)

    state["cursor"] = cursor
    state["slices"] = int(state.get("slices", 0)) + 1
    state["files_reparsed"] = int(state.get("files_reparsed", 0)) + processed
    state["last_run_at"] = _now()
    if slice_errors:
        state.setdefault("errors", []).extend(slice_errors)

    result = {
        "success": True,
        "repo": f"{owner}/{name}",
        "source_root": source_root,
        "reason": state.get("reason"),
        "campaign_started": started_new,
        "files_this_run": processed,
        "completed_files": min(cursor, len(files)),
        "total_files": len(files),
        "remaining_files": max(len(files) - cursor, 0),
        "stopped_because": stopped_because,
        "duration_seconds": round(time.monotonic() - t0, 2),
    }
    if slice_errors:
        result["errors"] = slice_errors

    if cursor >= len(files):
        _finish(state, result, store, owner, name, source_root)

    _write_state(state_path, state)
    result["complete"] = bool(state.get("cursor", 0) >= len(state.get("files") or []))
    return result


def _finish(state: dict, result: dict, store, owner: str, name: str, source_root: str) -> None:
    """Close out a campaign whose manifest is exhausted.

    ⚠⚠ Exhausting the manifest is NOT the same as covering the corpus. The
    manifest was enumerated when the campaign started, and a repo that is still
    being worked on grows underneath it. Files added since then have never been
    parsed by this campaign, so stamping here would claim coverage the campaign
    does not have. They are appended and the campaign continues instead.
    """
    try:
        current = set(_discover(source_root))
    except Exception:
        result["stamped"] = False
        result["stamp_skipped_reason"] = "rediscovery_failed"
        logger.warning("refresh: re-discovery failed, not stamping", exc_info=True)
        return

    known = set(state.get("files") or [])

    # ⚠⚠ A CAMPAIGN THAT SAW NOTHING MUST NOT CERTIFY EVERYTHING.
    #
    # The drift check below asks only whether the corpus GREW. It cannot see the
    # opposite failure: a source root that has gone away -- moved, renamed,
    # unmounted, a removed worktree, a cleaned scratch dir -- makes discovery
    # return an empty list, so `current` and `known` are both empty, nothing has
    # "drifted", no batch errored, and the campaign stamps the target generation
    # having re-parsed zero files.
    #
    # ⚠⚠ That is not a cosmetic wrong answer, it is UNREPAIRABLE. A stamp EQUAL
    # to the constant is indistinguishable from a genuine one, so the index is
    # exempt from every future upgrade -- the exact bucket `PARSER_GENERATION`
    # exists to drain. Measured 2026-08-25 on the three pinned benchmark
    # corpora: bare .git directories with no working tree, 8,220 pre-.246
    # symbols between them, all three stamped `2` after re-parsing 0 files.
    #
    # ⚠ The test is EMPTY-vs-NON-EMPTY, deliberately not a shrink threshold. A
    # repo may legitimately lose most of its files, and inventing a percentage
    # here would be a magic number nobody measured. Files that are still indexed
    # but were never re-parsed are DISCLOSED below instead of guessed at.
    # ⚠ UNKNOWN blocks too. `_index_files` returns None when the index could not
    # be read, and `refresh` refuses to build a first index, so by this point an
    # index exists -- None therefore means could-not-establish, not empty. An
    # empty discovery we cannot check against is exactly the state this guard
    # exists for, so it refuses rather than assuming the benign reading.
    indexed = _index_files(store, owner, name)
    if not current and (indexed is None or indexed):
        result["stamped"] = False
        result["stamp_skipped_reason"] = (
            "index_unreadable" if indexed is None else "corpus_unreadable"
        )
        if indexed is not None:
            result["indexed_files_not_reparsed"] = len(indexed)
        logger.warning(
            "refresh: discovery found no files under %s while the index holds %s; "
            "not stamping generation (source root moved, unmounted or removed?)",
            source_root,
            "an unreadable number of" if indexed is None else len(indexed),
        )
        return

    # Disclosed, not blocked: rows for files this campaign never re-parsed are
    # still at the OLD generation. Ordinary deletions land here, which is why it
    # is a number the caller can see rather than a refusal.
    if indexed:
        stale = indexed - known
        if stale:
            result["indexed_files_not_reparsed"] = len(stale)

    added = sorted(current - known)
    if added:
        state["files"] = list(state.get("files") or []) + added
        result["corpus_drifted"] = len(added)
        result["stamped"] = False
        result["stamp_skipped_reason"] = "corpus_drifted"
        result["remaining_files"] = len(added)
        result["total_files"] = len(state["files"])
        return

    if state.get("errors"):
        result["stamped"] = False
        result["stamp_skipped_reason"] = "batch_errors"
        return

    target = int(state.get("generation_target") or _target_generation())
    stamped = store.stamp_parser_generation(owner, name, target)
    state["stamped"] = bool(stamped)
    result["stamped"] = bool(stamped)
    if stamped:
        result["parser_generation"] = target
    else:
        # Not an error: a campaign run for reasons other than a generation
        # upgrade finds the stamp already current, and moving it backwards is
        # refused by design.
        result["stamp_skipped_reason"] = "already_current"
