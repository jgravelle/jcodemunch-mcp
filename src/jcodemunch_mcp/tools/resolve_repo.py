"""Resolve a filesystem path to its indexed repo identifier."""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..storage import IndexStore
from ..storage.git_root import resolve_index_identity

logger = logging.getLogger(__name__)


def _dotgit_kind(directory: Path) -> Optional[str]:
    """Classify a ``.git`` entry: "repo", "submodule", "worktree", or None.

    A ``.git`` DIRECTORY is an independent repository. A ``.git`` FILE carries a
    ``gitdir:`` pointer, and where it points is the whole distinction #372 drew:
    ``.git/worktrees/<name>`` is a linked worktree, ``.git/modules/<name>`` is a
    submodule. Anything else pointing outside both is a ``--separate-git-dir``
    clone, which is independent.
    """
    dotgit = directory / ".git"
    try:
        if dotgit.is_dir():
            return "repo"
        if not dotgit.is_file():
            return None
        text = dotgit.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    target = Path(text[len("gitdir:"):].strip())
    if not target.is_absolute():
        target = directory / target
    parent_name = target.parent.name
    if parent_name == "worktrees":
        return "worktree"
    if parent_name == "modules":
        return "submodule"
    return "repo"


def _independent_repo_between(p_resolved: Path, source_root: Path) -> Optional[Path]:
    """Root of an independent git repository between a path and a source root.

    Returns the deepest such root, or None when the path really does belong to
    ``source_root``'s repository.

    ⚠⚠ Containment is a statement about the FILESYSTEM; the caller wants one
    about the REPOSITORY. A nested independent clone inside an indexed parent
    satisfies the first and fails the second, and before #493's sibling #492 the
    resolver returned the parent as ``indexed: true`` for it, binding the caller
    to a corpus from a different checkout with a different history.

    ⚠ Submodules deliberately do NOT count. Their content IS indexed into the
    parent, which is the boundary #372 drew when it excluded linked worktrees
    without changing submodule behaviour. Linked worktrees are left alone here
    too: fast path 2 already answers for them via ``canonical_candidates``, and
    diverting them into this branch would change #303's answer.

    ⚠ Stats only, no subprocess — fast path 1 exists to avoid the
    ``resolve_index_identity`` walk that can hang (#303), so a correctness guard
    on it must not reintroduce a process spawn.
    """
    try:
        candidates = [p_resolved, *p_resolved.parents]
    except (OSError, ValueError):
        return None
    for directory in candidates:
        if directory == source_root:
            return None  # reached the indexed root without crossing a boundary
        try:
            if not directory.is_relative_to(source_root):
                return None
        except (OSError, ValueError, AttributeError):
            return None
        if _dotgit_kind(directory) == "repo":
            return directory
    return None


def _compute_repo_id(folder_path: Path, store: Optional[IndexStore] = None) -> str:
    """Compute the repo ID that index_folder would use for a directory path."""
    decision = resolve_index_identity(str(folder_path), mode="config", store=store)
    return f"{decision.owner}/{decision.name}"


def _local_provisional_repo_id(folder_path: Path) -> str:
    """Compute a cheap local/path-hash repo ID without any git probing (jcm#303).

    The not-indexed and canonical-candidate-found paths only need a stable
    provisional identifier to return as `repo`; they don't need git-identity
    resolution. `_compute_repo_id` would otherwise call `resolve_index_identity`,
    which when `git_root_identity=true` (or similar config) falls through to
    `detect_git_root` → `_read_origin_url`, spawning a `git config --get
    remote.origin.url` subprocess. In large-worktree environments that
    subprocess can hang, defeating the canonical-candidate fast return.

    This helper bypasses git entirely. Real repo IDs for indexed entries are
    surfaced via `canonical_candidates`; the provisional `repo` value is
    descriptive, not authoritative.
    """
    from ..storage.git_root import _local_repo_name
    resolved = Path(folder_path).expanduser().resolve()
    return f"local/{_local_repo_name(resolved)}"


def _git_common_dir_cheap(path: Path) -> Optional[Path]:
    """Resolve the canonical Git common-dir via filesystem reads (no subprocess).

    Standard layout:
      - Main checkout: ``<repo>/.git`` is a directory; that IS the common-dir.
      - Linked worktree (``git worktree add``): ``<worktree>/.git`` is a file
        containing ``gitdir: <abs path to linked worktree gitdir>``. The
        linked worktree gitdir contains a ``commondir`` file pointing back
        (relative path) to the canonical ``.git`` of the main checkout.
      - Submodule / unusual layout: ``.git`` is a file with ``gitdir:`` but
        no ``commondir`` file. The pointed-to gitdir itself is treated as
        the common-dir.

    Faster than `git rev-parse --git-common-dir` by 100-1000x on Windows;
    safe to call O(indexes) times inside a hot loop (jcm#303).

    Returns None when the path has no ``.git`` (not a git repo) or the
    pointer file is malformed. Caller falls back to no canonical match.
    """
    git = path / ".git"
    if not git.exists():
        return None

    if git.is_dir():
        return git.resolve()

    if git.is_file():
        try:
            content = git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not content.startswith("gitdir:"):
            return None
        gitdir_str = content[len("gitdir:"):].strip()
        if not gitdir_str:
            return None
        gitdir = Path(gitdir_str)
        if not gitdir.is_absolute():
            gitdir = (path / gitdir).resolve()
        else:
            gitdir = gitdir.resolve()
        if not gitdir.exists():
            return None
        commondir_file = gitdir / "commondir"
        if commondir_file.exists():
            try:
                rel = commondir_file.read_text(encoding="utf-8").strip()
            except OSError:
                rel = ""
            if rel:
                common = Path(rel)
                if not common.is_absolute():
                    common = (gitdir / common).resolve()
                else:
                    common = common.resolve()
                return common
        # Submodule or unusual layout — the gitdir itself is the common-dir.
        return gitdir

    return None


def _git_toplevel(path: Path) -> Optional[Path]:
    """Get the git repository root for a path, or None.

    The caller's path is not yet trusted — the whole point of resolve_repo is
    to discover whether it's already indexed. Neutralise system/global git
    config and disable hook execution so a hostile workspace cannot influence
    this probe (defense-in-depth on top of git's safe.directory check).
    """
    import os as _os
    env = _os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = _os.devnull
    # GIT_TERMINAL_PROMPT=0 prevents accidental credential prompts on
    # workspaces whose .git/config points at remotes requiring auth.
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(path),
            timeout=5,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _build_indexed_response(
    store: IndexStore,
    repo_id: str,
    p_resolved: Path,
    start: float,
    match_path: str,
) -> Optional[dict]:
    """Construct the indexed-repo response shape, or None when lookup fails."""
    if not repo_id or "/" not in repo_id:
        return None
    owner, name = repo_id.split("/", 1)
    status = store.inspect_index(owner, name)
    if not status.index_present:
        return None
    entry = _read_repo_metadata(store, owner, name)
    elapsed = (time.perf_counter() - start) * 1000
    result = {
        "found": True,
        "indexed": status.loadable,
        "repo": repo_id,
        **status.as_fields(),
        "_meta": {"timing_ms": round(elapsed, 1), "match_path": match_path},
    }
    if status.loadable:
        # Turn-economy steering (v1.108.158): resolve_repo is the universal
        # session opener — name the one-call exploration path up front.
        # v1.108.159: response-size guidance — turn cuts alone don't move heavy
        # repos when each capsule is fat; compress=True packs more symbols into
        # the same budget.
        result["_meta"]["opening_move"] = (
            "Exploration question ('how does X work')? get_ranked_context(repo, "
            "query, token_budget) answers in one call — prefer it over chained "
            "search_symbols/get_file_outline/get_symbol_source hops. Keep "
            "token_budget modest (4000 default) and pass compress=True to fit "
            "more symbols in the same budget."
        )
    metadata = {
        "source_root": entry.get("source_root") or status.source_root,
        "display_name": entry.get("display_name") or status.display_name,
        "symbol_count": entry.get("symbol_count", status.symbol_count),
        "file_count": entry.get("file_count", status.file_count),
        "languages": entry.get("languages", status.languages),
        "indexed_at": entry.get("indexed_at") or status.indexed_at,
    }
    for key, value in metadata.items():
        if value is not None and value != "":
            result[key] = value

    # Surface a max_folder_files truncation up front (#366): a silently capped
    # index looks healthy while quietly missing files. Cheap metadata-only read.
    if hasattr(store, "_sqlite"):
        try:
            cap = store._sqlite.get_file_cap_status(owner, name)
        except Exception:
            cap = {}
        if cap.get("truncated"):
            result["truncated"] = True
            result["files_discovered"] = cap.get("files_discovered")
            result["files_indexed"] = cap.get("files_indexed")
            result["files_skipped_cap"] = cap.get("files_skipped_cap")
            result["truncation_warning"] = (
                f"Index truncated by the file cap: {cap.get('files_discovered')} files "
                f"discovered, {cap.get('files_indexed')} indexed, "
                f"{cap.get('files_skipped_cap')} dropped (max_folder_files="
                f"{cap.get('max_folder_files')}). Entire files are missing from search "
                f"results. Raise max_folder_files in config.jsonc (or set "
                f"JCODEMUNCH_MAX_FOLDER_FILES) and re-index."
            )
    return result


def _resolve_repo_impl(path: str, storage_path: Optional[str] = None) -> dict:
    """Resolve a filesystem path to its indexed repo identifier.

    Accepts a repo root, worktree, subdirectory, or file path.
    Returns whether the path is indexed and its computed repo ID.

    Performance (jcm#303): in environments with many indexes and/or many
    Git worktrees of the same logical repo, this used to scale O(N) git
    subprocesses through `_find_canonical_candidates` and O(N) store probes
    through `resolve_index_identity(store=store)`. The fast paths below
    pre-fetch the repo list once, match by exact source_root (and source_root
    containment) before any subprocess work, and replace canonical-candidate
    git probes with filesystem reads of `.git` / `commondir`.
    """
    start = time.perf_counter()
    p = Path(path)

    if not p.exists():
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "found": False,
            "indexed": False,
            "error": f"Path does not exist: {path}",
            "_meta": {"timing_ms": round(elapsed, 1)},
        }

    # If it's a file, use parent directory
    if p.is_file():
        p = p.parent

    p_resolved = p.resolve()
    store = IndexStore(base_path=storage_path)

    # Single store enumeration — reused by all subsequent fast paths.
    try:
        all_repos = store.list_repos()
    except Exception:
        logger.debug("list_repos failed at resolve_repo entry", exc_info=True)
        all_repos = []

    # Fast path 1 (jcm#303): exact source_root match, then source_root
    # containment with the deepest match winning. Avoids the
    # resolve_index_identity(..., store=store) walk that probes every
    # indexed repo's git_root for path containment.
    containment_hits: list[tuple[int, dict]] = []
    for entry in all_repos:
        sr = entry.get("source_root", "")
        if not sr:
            continue
        try:
            sr_path = Path(sr).resolve()
        except (OSError, ValueError):
            continue
        if p_resolved == sr_path:
            built = _build_indexed_response(
                store, entry.get("repo", ""), p_resolved, start,
                match_path="exact_source_root",
            )
            if built is not None:
                return built
        else:
            try:
                if not p_resolved.is_relative_to(sr_path):
                    continue
            except (OSError, ValueError, AttributeError):
                continue
            # #492: containment is a filesystem fact, not a repository one. An
            # independent clone nested inside an indexed parent is contained by
            # it and belongs to neither its corpus nor its history.
            boundary = _independent_repo_between(p_resolved, sr_path)
            if boundary is not None:
                logger.debug(
                    "resolve_repo: %s is inside independent repo %s, not %s",
                    p_resolved, boundary, sr_path,
                )
                continue
            containment_hits.append((len(str(sr_path)), entry))

    if containment_hits:
        # Deepest source_root wins (most specific match).
        containment_hits.sort(key=lambda x: x[0], reverse=True)
        for _, entry in containment_hits:
            built = _build_indexed_response(
                store, entry.get("repo", ""), p_resolved, start,
                match_path="source_root_containment",
            )
            if built is not None:
                return built

    # Fast path 2 (jcm#303 follow-up, reported by @rknighton): canonical
    # worktree discovery via cheap .git / commondir reads BEFORE any
    # git-identity probing. If the input path is a worktree of an
    # already-indexed canonical, return immediately with canonical_candidates
    # and a cheap local provisional repo_id. This avoids `detect_git_root` →
    # `_read_origin_url` subprocess calls that can hang in large-worktree
    # environments under git_root_identity=true.
    canonical_candidates = _find_canonical_candidates(p, store, all_repos)
    if canonical_candidates:
        repo_id = _local_provisional_repo_id(p)
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "found": True,
            "indexed": False,
            "repo": repo_id,
            "canonical_candidates": canonical_candidates,
            "hint": (
                "this is a Git worktree of an already-indexed repo — use one of "
                "canonical_candidates for read-only lookups, or index this "
                "worktree explicitly if you need branch-local/uncommitted state"
            ),
            "_meta": {
                "timing_ms": round(elapsed, 1),
                "match_path": "canonical_candidate_fast",
            },
        }

    # Slow path: legacy compute-then-inspect for the (input, git_root)
    # candidate pair. Reached only when the fast paths above missed.
    candidates = [p]
    git_root = _git_toplevel(p)
    if git_root and git_root.resolve() != p_resolved:
        candidates.append(git_root)

    for candidate in candidates:
        repo_id = _compute_repo_id(candidate, store=store)
        built = _build_indexed_response(
            store, repo_id, p_resolved, start,
            match_path="computed_repo_id",
        )
        if built is not None:
            # A git-identity match that the source_root fast paths missed can
            # be a SEPARATE checkout of the same project (same origin, different
            # working tree) — flag it so callers don't treat the sibling's
            # source_root as this path's index (v1.108.160, bench-observed).
            _attach_working_tree_mismatch(built, p_resolved)
            return built

    # Not indexed and no canonical match — use cheap local/path-hash identity
    # for the provisional repo_id. Avoids `detect_git_root` → `_read_origin_url`
    # subprocess hangs in large-worktree environments (jcm#303 follow-up).
    best = candidates[0]
    repo_id = _local_provisional_repo_id(best)

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "found": True,
        "indexed": False,
        "repo": repo_id,
        "hint": "call index_folder to index this path",
        "_meta": {"timing_ms": round(elapsed, 1), "match_path": "not_indexed"},
    }


def _attach_working_tree_mismatch(result: dict, p_resolved: Path) -> None:
    """Flag an indexed response whose source_root does not contain the queried
    path — a separate working tree (second clone/checkout) of an already-indexed
    project. Previously served silently as `indexed: true` with the OTHER
    checkout's source_root; agents then dead-ended in index_file and recovered
    by re-indexing thousands of files in-run (measured 2026-07-22 bench)."""
    sr = result.get("source_root") or ""
    if not sr:
        return
    try:
        sr_path = Path(sr).expanduser().resolve()
        if p_resolved == sr_path or p_resolved.is_relative_to(sr_path):
            return
    except (OSError, ValueError):
        return
    result["working_tree_mismatch"] = True
    result["warning"] = (
        f"Different working tree detected: this path is a separate checkout of "
        f"{result.get('repo')}; the index was built from {sr}. Read-only lookups "
        "against this repo id reflect THAT checkout. To index THIS checkout as "
        "its own repo, call index_folder(path=<this checkout root>, "
        "identity_mode='local')."
    )
    result.setdefault("_meta", {})["working_tree"] = {
        "queried_path": str(p_resolved),
        "indexed_root": sr,
    }


def resolve_repo(path: str, storage_path: Optional[str] = None) -> dict:
    """Resolve a filesystem path to its indexed repo identifier.

    Thin wrapper over `_resolve_repo_impl` that flags relative-path inputs.

    Relative-path safety: a relative `path` (e.g. ".") is resolved against the
    jcodemunch SERVER process's working directory. Over a detached SSE /
    streamable-http transport that is NOT the caller's directory, so "." can
    silently bind to the server's install or a system directory and return the
    wrong repo (or `indexed: false` for what the caller believes is an indexed
    tree). The resolution behavior is unchanged for backward compatibility, but
    when the input is relative the response gains a top-level
    `relative_path_warning` plus a structured `_meta.relative_path` (raw input,
    the CWD-relative absolute resolution, and a fix hint) so the silent
    misbinding is visible rather than a wrong-repo surprise. Absolute-path
    callers get byte-identical output.
    """
    result = _resolve_repo_impl(path, storage_path)
    try:
        if not Path(path).is_absolute():
            resolved_against_cwd = str(Path(path).resolve())
            hint = (
                "relative paths resolve against the jcodemunch server's working "
                "directory, which over a detached SSE/streamable-http transport "
                "is not the caller's directory; pass an absolute path to resolve "
                "the intended repo deterministically"
            )
            if isinstance(result, dict):
                result["relative_path_warning"] = hint
                meta = result.setdefault("_meta", {})
                if isinstance(meta, dict):
                    meta["relative_path"] = {
                        "input": path,
                        "resolved_against_cwd": resolved_against_cwd,
                        "hint": hint,
                    }
    except (OSError, ValueError):
        logger.debug("relative-path annotation failed for %r", path, exc_info=True)
    return result


def _find_canonical_candidates(
    path: Path,
    store: IndexStore,
    repos: Optional[list[dict]] = None,
) -> list[dict]:
    """Find indexed repos sharing this path's Git common-dir.

    Returns a list of `{repo, source_root, rationale}` dicts. Empty when the
    path isn't in a Git repo, has no common-dir, or no indexed repo matches.

    Performance (jcm#303): uses `_git_common_dir_cheap` (filesystem reads
    only, no subprocess) for both the input path and every candidate path.
    Accepts a pre-fetched `repos` list so the caller can avoid a redundant
    `store.list_repos()` round-trip.
    """
    common = _git_common_dir_cheap(path)
    if common is None:
        return []

    if repos is None:
        try:
            repos = store.list_repos()
        except Exception:
            logger.debug("list_repos failed during worktree resolution", exc_info=True)
            return []

    candidates: list[dict] = []
    for entry in repos:
        source_root = entry.get("source_root", "")
        if not source_root:
            continue
        try:
            other_path = Path(source_root)
            if not other_path.exists():
                continue
            other_common = _git_common_dir_cheap(other_path)
        except (OSError, ValueError):
            continue
        if other_common is None:
            continue
        if other_common == common:
            candidates.append({
                "repo": entry.get("repo", ""),
                "source_root": source_root,
                "rationale": "shared --git-common-dir",
            })
    return candidates


def _read_repo_metadata(store: IndexStore, owner: str, name: str) -> dict:
    """Read repo metadata from SQLite, sidecar, or full index JSON."""
    # Try SQLite first (primary backend since v1.9.0)
    if hasattr(store, '_sqlite'):
        db_path = store._sqlite._db_path(owner, name)
        if db_path.exists():
            entry = store._sqlite._list_repo_from_db(db_path)
            if entry:
                return entry

    slug = store._repo_slug(owner, name)

    # Try lightweight sidecar
    meta_path = store.base_path / f"{slug}.meta.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entry = store._repo_entry_from_data(data)
            if entry:
                return entry
        except (json.JSONDecodeError, ValueError):
            logger.debug("Corrupted sidecar JSON at %s, skipping", meta_path)

    # Fall back to full index JSON
    index_path = store._index_path(owner, name)
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entry = store._repo_entry_from_data(data)
            if entry:
                return entry
        except (json.JSONDecodeError, ValueError):
            logger.debug("Corrupted index JSON at %s, skipping", index_path)

    return {}
