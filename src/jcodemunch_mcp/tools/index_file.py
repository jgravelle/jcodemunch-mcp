"""Index a single file within an existing index."""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .. import config as _config
from ..parser import LANGUAGE_EXTENSIONS, get_language_for_path
from ..parser.context import discover_providers, collect_metadata
from ..security import validate_path, is_secret_file
from ..storage import IndexStore
from ..storage.index_store import _file_hash, _get_git_head, _get_git_branch
from ._indexing_pipeline import parse_and_prepare_incremental
from .resolve_repo import _independent_repo_between

logger = logging.getLogger(__name__)


def _sibling_checkout_error(file_path: Path, store: IndexStore) -> Optional[str]:
    """Different-working-tree detection (v1.108.160, bench-observed dead-end):
    the file's checkout shares git identity with an ALREADY-INDEXED sibling
    checkout, so "run index_folder on the parent" is the wrong remedy — agents
    followed it and re-indexed thousands of files in-run. Returns an actionable
    error naming both checkouts, or None when this isn't that situation."""
    try:
        from ..storage.git_root import _find_git_root  # noqa: PLC0415
        from .resolve_repo import _compute_repo_id  # noqa: PLC0415

        root = _find_git_root(file_path.parent)
        if root is None:
            return None
        repo_id = _compute_repo_id(root, store)
        if not repo_id or "/" not in repo_id:
            return None
        owner, name = repo_id.split("/", 1)
        status = store.inspect_index(owner, name)
        if not status.index_present:
            return None
        indexed_root = status.source_root or ""
        if not indexed_root:
            return None
        if Path(indexed_root).expanduser().resolve() == Path(root).resolve():
            return None  # same checkout — the ordinary not-contained case
        return (
            f"Different working tree detected: {file_path} is in a separate "
            f"checkout ({root}) of already-indexed {repo_id} (indexed from "
            f"{indexed_root}). For read-only lookups, query repo '{repo_id}' — "
            f"it reflects the indexed checkout. To index THIS checkout as its "
            f"own repo, run index_folder(path='{root}', identity_mode='local'); "
            "do NOT re-index the whole tree under the existing identity."
        )
    except Exception:
        logger.debug("Sibling-checkout probe failed", exc_info=True)
        return None


def _paths_changed_between(source_root: Path, old_head: str, new_head: str) -> Optional[set[str]]:
    """Repo-relative posix paths that differ between two commits, or None.

    ``None`` means the question could not be answered — a git failure, a missing
    commit, a tree that is not a repository. It is NEVER an empty set: callers
    must treat unknown as "cannot prove", not as "nothing changed".

    ``--relative`` scopes the answer to ``source_root``, so a monorepo subtree
    index is not held back by a commit that touched a sibling it never indexed.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--relative", old_head, new_head],
            cwd=str(source_root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, stdin=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("git diff failed for %s", source_root, exc_info=True)
        return None
    if result.returncode != 0:
        logger.debug(
            "git diff %s..%s returned %s for %s",
            old_head[:12], new_head[:12], result.returncode, source_root,
        )
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _head_may_advance(
    index, source_root: Path, stored_head: str, live_head: str, refreshed_rel: str,
) -> bool:
    """May this single-file refresh record ``live_head`` as the corpus's head?

    Only when refreshing this one file is what brought the corpus into line with
    ``live_head``: every other path that moved between the two commits must be
    one the index does not carry and would not index.

    ⚠⚠ **The write is not the defect; what has been proven before it is.**
    ``index_folder``'s ``_refresh_git_head_if_advanced`` performs the identical
    write on a no-change run (#330), and it is correct there because that run
    walked the corpus and established that nothing indexed had changed.
    ``index_file`` establishes something far weaker — that ONE requested file now
    matches — and then advanced the repository-level head anyway, clearing
    ``repo_is_stale`` for every file that moved in the same commit and was never
    refreshed (#493). Nothing errored: the served content was simply the old
    commit's, reported ``fresh``.

    ⚠ Unknown resolves to False. A head left behind reads ``stale`` for a
    repository that may in fact match, which costs a re-index; a head advanced
    without proof reads ``fresh`` for one that does not, which costs a wrong
    answer with no signal attached. Same asymmetry as v1.108.209's rule that
    ``classify()`` must never answer ``fresh`` for a comparison it could not
    make.
    """
    if not live_head:
        return False
    if not stored_head:
        # No baseline to diff against, so nothing can be proven. The index keeps
        # its empty head and `repo_is_stale` keeps reporting False for want of a
        # SHA, exactly as before this call; a full `index_folder` sets it.
        return False
    if stored_head == live_head:
        return True  # nothing moved; the write is a no-op

    changed = _paths_changed_between(source_root, stored_head, live_head)
    if changed is None:
        return False

    indexed_paths = set(getattr(index, "source_files", None) or ())
    for path in changed:
        if path == refreshed_rel:
            continue
        if path in indexed_paths:
            return False  # a file we carry moved and we did not re-read it
        if get_language_for_path(path) is not None:
            # Not in the corpus but the indexer would take it: an added source
            # file. Advancing here would report a complete index over a corpus
            # that is missing a file, which is an absence claim we cannot back.
            return False
    return True


def index_file(
    path: str,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    context_providers: bool = True,
    progress_cb: "Optional[Callable[[int, int, str], None]]" = None,
) -> dict:
    """Index a single file within an existing index.

    Finds the matching index by checking which indexed folder's source_root
    is a parent of the given file path, then surgically updates that index.
    Can also add new files not yet in the index (as long as they're under
    an indexed folder's source_root).

    Args:
        path: Absolute path to the file to index.
        use_ai_summaries: Whether to use AI for symbol summaries.
        storage_path: Custom storage path (default: ~/.code-index/).
        context_providers: Whether to run context providers.

    Returns:
        Dict with indexing results.
    """
    t0 = time.monotonic()

    # Resolve and validate file path
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        return {"success": False, "error": f"File not found: {path}"}
    if not file_path.is_file():
        return {"success": False, "error": f"Path is not a file: {path}"}

    store = IndexStore(base_path=storage_path)

    # Find matching index by scanning all indexed repos for one whose
    # source_root is a parent of this file. Pick the most specific match.
    repos = store.list_repos()
    best_match: Optional[dict] = None
    best_root_len = -1
    # Candidates rejected for belonging to a different repository, kept so the
    # refusal can say WHICH repository rather than "no index contains this".
    _blocked_by_boundary: list[tuple[str, Path]] = []

    for repo_entry in repos:
        source_root = repo_entry.get("source_root", "")
        if not source_root:
            continue
        try:
            root_path = Path(source_root).resolve()
            if not file_path.is_relative_to(root_path):
                continue
        except (ValueError, OSError):
            continue
        # #509: containment is a FILESYSTEM fact; attribution needs a REPOSITORY
        # one. `resolve_repo` stopped matching on containment alone in #492 and
        # this path still did — where the consequence is a WRITE into an index
        # built from a different repository, not merely a wrong read.
        #
        # ⚠ Imported, not reimplemented. Copying the check is exactly how these
        # two call sites diverged in the first place.
        _boundary = _independent_repo_between(file_path, root_path)
        if _boundary is not None:
            _blocked_by_boundary.append((repo_entry.get("repo", ""), _boundary))
            continue
        if len(str(root_path)) > best_root_len:
            best_match = repo_entry
            best_root_len = len(str(root_path))

    if best_match is None:
        sibling_error = _sibling_checkout_error(file_path, store)
        if sibling_error:
            return {
                "success": False,
                "error": sibling_error,
                "skipped": "different_working_tree",
            }
        if _blocked_by_boundary:
            _enclosing, _repo_root = _blocked_by_boundary[0]
            return {
                "success": False,
                "error": (
                    f"{path} belongs to the git repository at {_repo_root}, which "
                    f"has no index of its own. It is inside the indexed folder "
                    f"for '{_enclosing}', but that is a different repository with "
                    f"a different history — writing this file into it would "
                    f"attribute one repository's source to another. Run "
                    f"index_folder on {_repo_root} first."
                ),
                "skipped": "different_repository",
            }
        return {
            "success": False,
            "error": (
                f"No indexed folder found that contains {path}. "
                "Run index_folder on the parent directory first."
            ),
        }

    owner, name = best_match["repo"].split("/", 1)
    source_root = Path(best_match["source_root"]).resolve()

    # #508: `config.get(key, repo=...)` reads an overlay that only
    # `load_project_config` populates, and nothing on this path called it — so
    # every `repo=` below (is_secret_file, context_providers, language gating)
    # silently resolved to GLOBAL config and the project's settings were inert.
    # `index_folder` loads it for the same reason at its own walk root.
    #
    # ⚠ A parameter that is present and does nothing is indistinguishable from
    # the defect it was added to fix (#491 threaded the keyword; this is the
    # path where it never reached anything).
    _config.load_project_config(str(source_root))

    # Security validation
    if not validate_path(source_root, file_path):
        return {"success": False, "error": f"File path failed security validation: {path}"}

    # Compute rel_path, hash, and mtime
    rel_path = file_path.relative_to(source_root).as_posix()

    # Eligibility: match index_folder's discovery filter so a manual index_file
    # call cannot add a credential file that the next full folder index would
    # skip and then prune — the flip-flop in #351. is_secret_file already exempts
    # source modules (e.g. secret_redaction.py) after the same-issue fix, so this
    # only refuses actual credential files (.env, *.pem, secrets/db.yaml, …).
    if is_secret_file(rel_path, repo=str(source_root)):
        return {
            "success": False,
            "error": (
                f"Skipped secret/credential file: {rel_path}. "
                "Folder indexing skips this file too, so indexing it here would "
                "not survive the next full index."
            ),
            "skipped": "secret",
        }

    # Check language support
    ext = file_path.suffix
    if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(file_path)) is None:
        return {
            "success": False,
            "error": f"Unsupported file type: {ext}. File not recognized as a supported language.",
        }

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            content = f.read()
    except Exception as e:
        return {"success": False, "error": f"Failed to read file: {e}"}

    file_hash = _file_hash(content)
    file_mtime = os.stat(file_path).st_mtime_ns

    # Load existing index to check if file has changed
    # Detect branch for branch-aware indexing
    _current_branch = _get_git_branch(source_root)
    _is_branch_delta = False

    base_index = store.load_index(owner, name)  # always load base
    if base_index is None:
        return {"success": False, "error": f"Failed to load index for {owner}/{name}"}

    if _current_branch:
        _base_branch = getattr(base_index, "branch", "") or ""
        if not _base_branch:
            _base_branch = _current_branch
        if _current_branch != _base_branch:
            _is_branch_delta = True
            # Load branch-composed index for comparison
            index = store.load_index(owner, name, branch=_current_branch) or base_index
        else:
            index = base_index
    else:
        index = base_index

    stored_hash = index.file_hashes.get(rel_path)
    is_new = rel_path not in index.file_hashes

    if not is_new and stored_hash == file_hash:
        # File unchanged — update mtime only if needed, then early exit
        if index.file_mtimes.get(rel_path) != file_mtime:
            store.incremental_save(
                owner=owner, name=name,
                changed_files=[], new_files=[], deleted_files=[],
                new_symbols=[], raw_files={},
                file_mtimes={rel_path: file_mtime},
            )
        return {
            "success": True,
            "message": "File unchanged",
            "repo": f"{owner}/{name}",
            "file": rel_path,
            "duration_seconds": round(time.monotonic() - t0, 2),
        }

    # Discover context providers (same env var check as index_folder).
    # Project-overridable (#301): per-repo feature toggle.
    _providers_enabled = context_providers and _config.get(
        "context_providers", True, repo=str(source_root)
    )
    active_providers = discover_providers(source_root) if _providers_enabled else []
    # Gate SQL-dependent providers: when SQL is removed from languages config,
    # filter out the dbt provider to avoid unnecessary detection overhead.
    # Project-overridable (#301): per-project language gating.
    if active_providers and not _config.is_language_enabled("sql", repo=str(source_root)):
        active_providers = [p for p in active_providers if p.name != "dbt"]

    # Shared pipeline: parse, enrich, summarize, extract metadata
    if progress_cb:
        progress_cb(0, 1, rel_path)
    warnings: list[str] = []
    new_symbols, file_summaries, file_languages, file_imports, _no_symbols = (
        parse_and_prepare_incremental(
            files_to_parse={rel_path},
            file_contents={rel_path: content},
            active_providers=active_providers,
            use_ai_summaries=use_ai_summaries,
            warnings=warnings,
            repo=str(source_root),
        )
    )

    live_head = _get_git_head(source_root) or ""
    stored_head = (getattr(base_index, "git_head", "") or "") if base_index else ""
    # #493: advance the repository-level head only when refreshing this one file
    # is what brought the corpus into line with it. See _head_may_advance.
    head_advanced = _head_may_advance(
        index, source_root, stored_head, live_head, rel_path,
    )
    git_head = live_head if head_advanced else stored_head
    ctx_metadata = collect_metadata(active_providers) if active_providers else None

    # Determine changed vs new
    changed_files = [rel_path] if not is_new else []
    new_files = [rel_path] if is_new else []

    if _is_branch_delta:
        store.save_branch_delta(
            owner=owner, name=name, branch=_current_branch,
            changed_files=changed_files, new_files=new_files, deleted_files=[],
            new_symbols=new_symbols,
            raw_files={rel_path: content},
            # ⚠ The branch-delta path writes `branch_meta`, not the
            # repository-level `meta` row #493 is about, and carries its own
            # `base_head`. Left on the live head deliberately; see CHANGELOG.
            git_head=live_head,
            base_head=base_index.git_head if base_index else "",
            file_hashes={rel_path: file_hash},
            file_mtimes={rel_path: file_mtime},
            file_languages=file_languages,
            file_summaries=file_summaries,
            file_imports=file_imports,
        )
        updated = store.load_index(owner, name, branch=_current_branch)
    else:
        updated = store.incremental_save(
            owner=owner, name=name,
            changed_files=changed_files,
            new_files=new_files,
            deleted_files=[],
            new_symbols=new_symbols,
            raw_files={rel_path: content},
            git_head=git_head,
            file_summaries=file_summaries,
            file_languages=file_languages,
            imports=file_imports,
            context_metadata=ctx_metadata,
            file_hashes={rel_path: file_hash},
            file_mtimes={rel_path: file_mtime},
        )
    if progress_cb:
        progress_cb(1, 1, rel_path)

    result: dict = {
        "success": True,
        "repo": f"{owner}/{name}",
        "file": rel_path,
        "is_new": is_new,
        "symbol_count": len(new_symbols),
        "indexed_at": updated.indexed_at if updated else "",
        "duration_seconds": round(time.monotonic() - t0, 2),
    }
    if _is_branch_delta:
        result["branch"] = _current_branch
        result["branch_delta"] = True
    if warnings:
        result["warnings"] = warnings
    return result
