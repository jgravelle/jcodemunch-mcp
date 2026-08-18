"""Index local folder tool - walk, parse, summarize, save."""

from collections.abc import Generator
from dataclasses import dataclass, field
import hashlib
import logging
import os
import threading
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional
import re

import pathspec

logger = logging.getLogger(__name__)

from .. import config as _config
from ..parser import cached_parse_file as parse_file, LANGUAGE_EXTENSIONS, get_language_for_path
from ..parser.context import discover_providers, enrich_symbols, collect_metadata, collect_extra_imports
from ..parser.context._route_utils import iter_source_files
from ..parser.context.framework_profiles import detect_framework, profile_to_meta
from ..parser.imports import extract_imports, _alias_map_cache as _imap_cache, _LANGUAGE_EXTRACTORS as _IMPORT_EXTRACTORS
from ..security import (
    validate_path,
    is_symlink_escape,
    is_secret_file,
    is_binary_file,
    DEFAULT_MAX_FILE_SIZE,
    get_max_file_size,
    get_max_folder_files,
    get_extra_ignore_patterns,
    get_respect_cachedir_tag,
    get_skip_directories,
    is_cache_directory,
    SKIP_FILES
)
from ..storage import IndexStore
from ..storage.git_root import (
    IdentityModeAmbiguous,
    IdentityModeConflict,
    is_linked_worktree,
    resolve_index_identity,
)
from ..storage.index_store import (
    PARSER_GENERATION,
    _file_hash,
    _file_hash_bytes,
    _get_git_head,
    _get_git_branch,
)
from ..summarizer import summarize_symbols
from ..reindex_state import WatcherChange
from ..path_map import parse_path_map, remap

SKIP_FILES_REGEX = re.compile("(" + "|".join(re.escape(p) for p in SKIP_FILES) + ")$")


def _file_cap_report(skip_counts: dict, max_files: int) -> dict:
    """Summarise whether the max_folder_files walk cap dropped files (#366).

    Returns the full truncation block when the cap was hit, else
    ``{"truncated": False}``. Always a dict so it can be persisted on every
    save path and self-heal (a re-index under a raised cap writes False).
    """
    dropped = int((skip_counts or {}).get("file_limit", 0) or 0)
    if dropped <= 0:
        return {"truncated": False}
    return {
        "truncated": True,
        "files_discovered": max_files + dropped,
        "files_indexed": max_files,
        "files_skipped_cap": dropped,
        "max_folder_files": max_files,
    }


def _attach_cap_report(result: dict, cap: Optional[dict]) -> None:
    """Surface a file-cap truncation in an index result dict (#366).

    No-op when the walk wasn't truncated, so a healthy index is unchanged.
    """
    if not isinstance(result, dict) or not cap or not cap.get("truncated"):
        return
    result["truncated"] = True
    result["files_discovered"] = cap["files_discovered"]
    result["files_indexed"] = cap["files_indexed"]
    result["files_skipped_cap"] = cap["files_skipped_cap"]
    result.setdefault("warnings", []).append(
        f"File cap reached: {cap['files_discovered']} files discovered, "
        f"{cap['files_indexed']} indexed, {cap['files_skipped_cap']} dropped "
        f"(max_folder_files={cap['max_folder_files']}). Entire files are missing "
        f"from the index. Raise max_folder_files in config.jsonc (or set "
        f"JCODEMUNCH_MAX_FOLDER_FILES) and re-index, or narrow the path."
    )


#: Skip reasons where the file is REAL, CURRENT and WANTED, and we refused it
#: anyway (v1.108.193, reported by @dkiaulakis). Every other reason describes a
#: file that was never a candidate for this corpus: a `.png` is `binary`, a
#: vendored tree is `gitignore`, a `.lock` is `wrong_extension`. Excluding
#: those is the corpus being defined, and a search over it can still prove
#: absence.
#:
#: These are different in kind. The file is source, it is current, an agent
#: asked about it by name, and it is missing because of OUR limit rather than
#: the caller's intent. A zero-result over a corpus that withheld one cannot
#: prove absence: "I never learned that file" and "that file does not exist"
#: are exactly the two things this whole contract exists to keep apart.
WITHHELD_SKIP_REASONS = frozenset({
    "too_large",     # over max_file_size, which until v1.108.193 could not be raised
    "file_limit",    # over max_folder_files / max_index_files
    "unreadable",    # a permission or IO failure, NOT a statement about the file
})


def _coverage_report(
    skip_counts: dict, files_indexed: int, no_symbols_count: int,
    files_accepted: Optional[int] = None,
    post_discovery_drops: Optional[dict] = None,
) -> dict:
    """Coverage contract for absence claims, recorded per full discovery walk.

    Persisted to the index meta table so query-time verdicts can disclose what
    the corpus EXCLUDED (unsupported extensions, oversize, binary, secret,
    cap-dropped files) and how many files parsed to zero symbols — a scan
    count alone can't back an ``absent`` verdict when whole files never
    entered the corpus. ``skip_dir`` counts directories, not files, so no
    files_discovered total is derived from ``skip_counts`` (each reason stands
    on its own).

    v1.108.176 (#375 sub-problem C) adds the accounting that makes
    INCOMPLETENESS detectable rather than merely describable. ``files_accepted``
    is what the walk handed downstream; ``files_indexed`` is what survived to
    the index. Anything between the two is a file the walk said belonged in the
    corpus and that is not in it. Named drops are listed; ``unaccounted`` is the
    remainder we cannot explain, and its presence is what flips ``complete`` to
    False.

    ``complete`` is deliberately conservative: it is only ever True when we hold
    both counts AND they reconcile exactly. An older index without
    ``files_accepted`` reports ``complete: None`` — unknown, never True. Absence
    of evidence about coverage must not read as evidence of coverage.
    """
    from datetime import datetime, timezone

    skips = {
        k: int(v) for k, v in (skip_counts or {}).items() if int(v or 0) > 0
    }
    drops = {
        k: int(v) for k, v in (post_discovery_drops or {}).items() if int(v or 0) > 0
    }
    report: dict = {
        "walk": "full",
        "files_indexed": int(files_indexed),
        "skip_counts": skips,
        "no_symbols_count": int(no_symbols_count),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if files_accepted is None:
        report["complete"] = None
        return report

    report["files_accepted"] = int(files_accepted)
    if drops:
        report["dropped_after_discovery"] = drops
    if int(files_indexed) > int(files_accepted):
        # The index legitimately holds MORE than this walk enumerated: v1.96
        # subdir-merge and branch-delta modes walk a prefix while the index
        # carries the rest. The reconciliation below does not describe that
        # shape, so report unknown rather than a false incomplete.
        report["complete"] = None
        report["reconciliation"] = "partial_walk_over_wider_index"
        return report
    unaccounted = int(files_accepted) - int(files_indexed) - sum(drops.values())
    if unaccounted > 0:
        report["unaccounted"] = unaccounted
    # v1.108.193: withheld files never reach `files_accepted` (they are refused
    # during discovery), so the reconciliation below balances perfectly while a
    # real, wanted source file sits outside the corpus. Counting them keeps
    # `complete` honest about the tree rather than only about the walk.
    withheld = {k: v for k, v in skips.items() if k in WITHHELD_SKIP_REASONS}
    if withheld:
        report["withheld"] = withheld
    report["complete"] = (
        int(files_indexed) == int(files_accepted) and not drops and not withheld
    )
    return report


def _record_coverage(
    store, owner: str, repo_name: str,
    skip_counts: dict, files_indexed: int, no_symbols_count: int,
    files_accepted: Optional[int] = None,
    post_discovery_drops: Optional[dict] = None,
) -> None:
    """Persist the coverage contract after a save (best-effort, never raises)."""
    try:
        store._sqlite.set_coverage(
            owner, repo_name,
            _coverage_report(
                skip_counts, files_indexed, no_symbols_count,
                files_accepted=files_accepted,
                post_discovery_drops=post_discovery_drops,
            ),
        )
    except Exception:
        logger.debug(
            "Failed to record coverage for %s/%s", owner, repo_name, exc_info=True
        )


def _build_skip_dirs_regex(repo: Optional[str] = None) -> re.Pattern:
    """Build regex from config-filtered skip directories (called per-index).

    ``repo`` is the walk root, threaded so a project's
    ``exclude_skip_directories`` applies (#491).
    """
    dirs = get_skip_directories(repo=repo)
    return re.compile("^(" + "|".join(dirs) + ")$")


def _maybe_apply_adaptive(folder_path: str, result: dict) -> None:
    """Apply adaptive language config if enabled. Never raises."""
    if not isinstance(result, dict) or not result.get("success"):
        return
    detected = set(result.get("languages", {}).keys())
    if not detected:
        return
    try:
        from ..config import apply_adaptive_languages
        apply_adaptive_languages(str(folder_path), detected)
    except Exception:
        logger.debug("adaptive language update skipped", exc_info=True)


def get_filtered_files(path: str) -> Generator[str, None, None]:
    """Generator function to filter directories and files"""
    skip_dirs_regex = _build_skip_dirs_regex(repo=path)
    # Use os.walk with followlinks=False to avoid infinite loops caused by
    # NTFS junctions or symlinks pointing back to ancestor directories.
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dpath = Path(dirpath)
        # Don't walk directories that should be skipped. Nested linked
        # worktrees (`.git` FILE pointing at `.git/worktrees/<name>`) are
        # separate working trees, not part of this index (#372).
        dirnames[:] = [
            dir for dir in dirnames
            if not skip_dirs_regex.match(dir)
            and not is_linked_worktree(dpath / dir)
        ]
        for file in filenames:
            if not SKIP_FILES_REGEX.search(file):
                yield dpath / file


def _load_gitignore(folder_path: Path) -> Optional[pathspec.PathSpec]:
    """Load .gitignore from the folder root if it exists."""
    gitignore_path = folder_path / ".gitignore"
    if gitignore_path.is_file():
        try:
            content = gitignore_path.read_text(encoding="utf-8", errors="replace")
            return pathspec.PathSpec.from_lines("gitignore", content.splitlines())
        except Exception:
            pass
    return None


def _load_all_gitignores(root: Path) -> dict[Path, pathspec.PathSpec]:
    """Load all .gitignore files in the tree, keyed by their directory.

    Supports monorepos and poncho-style projects where subdirectories each
    have their own .gitignore (e.g. cap/.gitignore, core/.gitignore).

    Uses os.walk(followlinks=False) to avoid infinite loops caused by
    NTFS junctions or symlinks pointing back to ancestor directories.
    """
    specs: dict[Path, pathspec.PathSpec] = {}
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        # Nested linked worktrees are separate working trees (#372); their
        # .gitignore files belong to their own index, not this one.
        _dpath = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_linked_worktree(_dpath / d)]
        if ".gitignore" in filenames:
            gitignore_path = Path(dirpath) / ".gitignore"
            try:
                content = gitignore_path.read_text(encoding="utf-8", errors="replace")
                spec = pathspec.PathSpec.from_lines("gitignore", content.splitlines())
                specs[gitignore_path.parent.resolve()] = spec
            except Exception:
                pass
    return specs


def _is_container() -> bool:
    """Detect whether we're running inside a container (Docker, Podman, devcontainer, Codespaces)."""
    # VS Code devcontainers / GitHub Codespaces set these env vars
    if os.environ.get("REMOTE_CONTAINERS") or os.environ.get("CODESPACES"):
        return True
    # Generic container marker (set by some orchestrators)
    if os.environ.get("container"):
        return True
    # Docker creates this sentinel file (use os.path to avoid pathlib patch interference)
    if os.path.exists("/.dockerenv"):
        return True
    # Podman / cri-o
    if os.path.exists("/run/.containerenv"):
        return True
    return False


def _path_safety_part_count(path: Path) -> int:
    r"""Count path components for the broad-root guard.

    On Windows, pathlib stores a UNC share root such as ``\\server\share\`` as
    one anchor component. Treat that anchor as server + share so
    ``\\server\share\repo`` has the same logical depth as ``C:\Users\repo``,
    while the share root itself remains too broad.
    """
    count = len(path.parts)
    if os.name == "nt" and str(path.drive).startswith("\\\\"):
        count += 1
    return count


def _is_shallow_windows_git_root(path: Path) -> bool:
    r"""Return whether ``path`` is a Git root directly below a local drive.

    ``C:\repo`` has a safety depth of two and normally trips the broad-root
    guard. A ``.git`` directory or file at that exact path proves the caller
    selected a working-tree root rather than the drive itself or a broad parent.
    The corresponding POSIX case was considered and intentionally left unchanged
    so this exception remains scoped to the reported Windows drive-root behavior.

    ⚠ The depth check and the UNC check answer different questions and neither
    is redundant with the other. ``_path_safety_part_count`` is a DEPTH rule;
    ``not drive.startswith("\\\\")`` is a SCOPE rule. A UNC share root has one
    real part and the depth helper adds one for the ``\\server\share`` anchor,
    so it computes to exactly two -- the same value as ``C:\repo``. Dropping the
    UNC test would therefore admit ``\\server\share`` itself, which #321/#322
    classify as too broad whatever it happens to contain.
    """
    drive = str(path.drive)
    return (
        os.name == "nt"
        and _path_safety_part_count(path) == 2
        and bool(drive)
        and not drive.startswith("\\\\")
        and os.path.exists(path / ".git")
    )


@lru_cache(maxsize=512)
def _is_trusted(
    folder_path: Path, trusted_folders: tuple, whitelist_mode: bool = True
) -> bool:
    """Return True when folder_path is trusted.

    whitelist_mode=True (default): trusted_folders contains trusted paths
    whitelist_mode=False: trusted_folders contains untrusted paths (blacklist)

    Empty list returns False (nothing explicitly trusted) for backward compatibility.
    The trust check is skipped for empty list, but the broad check uses this value.
    """
    if not trusted_folders:
        # Empty list: nothing explicitly trusted (backward compatible)
        return False

    is_in_list = any(
        folder_path == Path(trusted_folder)
        or Path(trusted_folder) in folder_path.parents
        for trusted_folder in trusted_folders
    )

    return is_in_list if whitelist_mode else not is_in_list

def _is_gitignored(file_path: Path, gitignore_specs: dict[Path, pathspec.PathSpec]) -> bool:
    """Check if a file is excluded by any .gitignore in its ancestor chain.

    Each spec is applied relative to its own directory, matching standard git behaviour.
    """
    for gitignore_dir, spec in gitignore_specs.items():
        try:
            rel = file_path.relative_to(gitignore_dir)
            if spec.match_file(rel.as_posix()):
                return True
        except ValueError:
            continue
    return False


def _is_gitignored_fast(resolved_str: str, specs: list[tuple[str, "pathspec.PathSpec"]]) -> bool:
    """String-based gitignore check — avoids Path.relative_to() overhead.

    Same semantics as _is_gitignored but uses string prefix matching instead
    of Path operations (~10x faster in the inner loop). Uses os.path.normcase
    for the prefix comparison so the check is case-insensitive on Windows.
    """
    resolved_norm = os.path.normcase(resolved_str)
    for dir_prefix, spec in specs:
        if not resolved_norm.startswith(os.path.normcase(dir_prefix)):
            continue
        rel = resolved_str[len(dir_prefix):].replace("\\", "/")
        if spec.match_file(rel):
            return True
    return False


def _local_repo_name(folder_path: Path) -> str:
    """Stable local repo id derived from basename + resolved path hash."""
    digest = hashlib.sha1(str(folder_path).encode("utf-8")).hexdigest()[:8]
    return f"{folder_path.name}-{digest}"


@dataclass
class _IndexFilters:
    """Pre-computed configuration for ``_should_index_file``.

    Bundled so per-file call sites stay readable and so the helper does
    not have to recompute stable values (root path strings, compiled
    pathspecs, etc.) on every invocation. ``gitignore_specs`` is the
    one piece that may grow during a walk and is passed alongside the
    bundle rather than baked into it.
    """
    root: Path
    root_prefix: str         # str(root) + os.sep
    root_str_norm: str       # os.path.normcase(str(root))
    root_prefix_norm: str    # os.path.normcase(root_prefix)
    follow_symlinks: bool = False
    max_size: int = DEFAULT_MAX_FILE_SIZE
    extra_spec: Optional["pathspec.PathSpec"] = None
    forced_paths: set = field(default_factory=set)
    skip_dirs_regex: Optional[re.Pattern] = None
    check_binary: bool = True
    check_filename: bool = True
    respect_cachedir_tag: bool = True


def _build_index_filters(
    root: Path,
    *,
    follow_symlinks: bool = False,
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    extra_spec: Optional["pathspec.PathSpec"] = None,
    forced_paths: Optional[set] = None,
    skip_dirs_regex: Optional[re.Pattern] = None,
    check_binary: bool = True,
    check_filename: bool = True,
    respect_cachedir_tag: bool = True,
) -> _IndexFilters:
    """Bundle pre-computed filter config for ``_should_index_file``.

    ``root`` must already be resolved by the caller.
    """
    root_str = str(root)
    root_prefix = root_str + os.sep
    return _IndexFilters(
        root=root,
        root_prefix=root_prefix,
        root_str_norm=os.path.normcase(root_str),
        root_prefix_norm=os.path.normcase(root_prefix),
        follow_symlinks=follow_symlinks,
        max_size=max_size,
        extra_spec=extra_spec,
        forced_paths=forced_paths if forced_paths is not None else set(),
        skip_dirs_regex=skip_dirs_regex,
        check_binary=check_binary,
        check_filename=check_filename,
        respect_cachedir_tag=respect_cachedir_tag,
    )


def _should_index_file(
    file_path: Path,
    cfg: _IndexFilters,
    gitignore_specs: Optional[list] = None,
) -> tuple[bool, str, str, Optional[str]]:
    """Single source of truth for per-file index-eligibility checks.

    Used by both ``discover_local_files`` (full walk) and the watcher
    fast path in ``index_folder``. Any new filter added to indexing
    MUST land here so both paths apply it. This invariant is the fix
    for #306 (filter-on-one-path-but-not-the-other, third occurrence
    after v1.95.1 collision guards and v1.96 incremental save merge).

    Returns ``(ok, reason, rel_path, warning)``:
      - ``ok=True``: caller may index. ``rel_path`` is the posix
        relative path; ``warning`` is None.
      - ``ok=False``: caller must skip. ``reason`` is one of the
        ``skip_counts`` keys (``skip_file``, ``symlink``,
        ``symlink_escape``, ``path_traversal``, ``skip_dir``,
        ``nested_worktree``, ``gitignore``, ``extra_ignore``, ``secret``,
        ``wrong_extension``, ``too_large``, ``unreadable``, ``binary``).
        ``rel_path`` may
        be empty if rejection happened before path resolution.
        ``warning`` is a user-facing one-liner the caller should
        append to its warnings list for the user-visible rejections
        (``symlink_escape``, ``path_traversal``, ``secret``, ``binary``);
        None otherwise.

    Args:
        file_path: Absolute path to the file (not necessarily resolved).
        cfg: Pre-computed filter configuration.
        gitignore_specs: List of ``(dir_prefix, spec)`` tuples for the
            ``.gitignore`` files in scope. The full walk grows this list
            as it descends; the fast path pre-loads root-level entries.
            Pass None / empty to skip gitignore matching.
    """
    # 1. Filename filter (SKIP_FILES regex — lockfiles, *.pyc, etc.)
    if cfg.check_filename and SKIP_FILES_REGEX.search(file_path.name):
        return False, "skip_file", "", None

    # 2. Symlink protection
    is_symlink = file_path.is_symlink()
    if is_symlink and not cfg.follow_symlinks:
        return False, "symlink", "", None

    # 3. Symlink escape (only relevant when follow_symlinks=True)
    if is_symlink and is_symlink_escape(cfg.root, file_path):
        return False, "symlink_escape", "", f"Skipped symlink escape: {file_path}"

    # 4. Resolve once
    try:
        resolved = file_path.resolve()
    except OSError:
        return False, "unreadable", "", None
    resolved_str = str(resolved)
    resolved_norm = os.path.normcase(resolved_str)

    # 5. Path traversal — resolved path must be under root
    if not (
        resolved_norm == cfg.root_str_norm
        or resolved_norm.startswith(cfg.root_prefix_norm)
    ):
        return False, "path_traversal", "", f"Skipped path traversal: {file_path}"

    # 6. Relative path (posix-style)
    rel_path = (
        resolved_str[len(cfg.root_prefix):].replace("\\", "/")
        if resolved_norm != cfg.root_str_norm
        else ""
    )
    if not rel_path:
        # The file resolved to the root itself — degenerate case.
        return False, "unreadable", "", None

    # 7. Skipped-directory check. The full-walk caller prunes these
    # via ``os.walk``'s ``dirnames`` mutation so files there never
    # reach the helper; passing ``skip_dirs_regex=None`` keeps that
    # path's behaviour identical. The fast-path caller relies on
    # this check because watchfiles can emit events for files under
    # build / cache directories.
    if cfg.skip_dirs_regex is not None:
        ancestor = cfg.root
        for part in rel_path.split("/")[:-1]:  # exclude the filename itself
            if cfg.skip_dirs_regex.match(part):
                return False, "skip_dir", rel_path, None
            # Mirror the full walk's nested-worktree pruning (#372) so a
            # watchfiles event for a file inside `.claude/worktrees/<x>`
            # never lands in the parent index via the fast path.
            ancestor = ancestor / part
            if is_linked_worktree(ancestor):
                return False, "nested_worktree", rel_path, None
            # CACHEDIR.TAG on the fast path. The full walk prunes these in
            # `os.walk`'s dirnames and never reaches here, exactly like the two
            # checks above; this branch exists so a watchfiles event for a file
            # that appeared inside a tagged cache does not enter the index by
            # the back door. Third entry point, same rule as #429.
            if cfg.respect_cachedir_tag and is_cache_directory(ancestor):
                return False, "cache_dir", rel_path, None

    # 8. Gitignore (string-prefix specs, walk-order)
    if gitignore_specs and _is_gitignored_fast(resolved_str, gitignore_specs):
        return False, "gitignore", rel_path, None

    # 9. Extra ignore patterns
    if cfg.extra_spec is not None and cfg.extra_spec.match_file(rel_path):
        return False, "extra_ignore", rel_path, None

    # 10. Secret-file detection
    if is_secret_file(rel_path, repo=str(cfg.root)):
        return False, "secret", rel_path, f"Skipped secret file: {rel_path}"

    # 11. Extension filter
    ext = file_path.suffix
    if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(file_path)) is None:
        return False, "wrong_extension", rel_path, None

    # 12. Size cap (with package.json forced-path exemption)
    try:
        size = file_path.stat().st_size
    except OSError:
        return False, "unreadable", rel_path, None
    # ⚠ normcase, not the raw string: on Windows the SAME file resolves to
    # strings that differ by drive-letter case or 8.3 short form, and a raw
    # `in` test silently voids the #25 size-cap exemption. The gitignore check
    # a few lines up already normalises for exactly this reason; this one did
    # not, and CI on windows-latest caught it where a local Windows run did not.
    if size > cfg.max_size and resolved_norm not in cfg.forced_paths:
        return False, "too_large", rel_path, None

    # 13. Binary detection (opt-out for callers that read the file separately)
    if cfg.check_binary and is_binary_file(file_path):
        return False, "binary", rel_path, f"Skipped binary file: {rel_path}"

    return True, "", rel_path, None


class _CarriedSymbol:
    """Attribute-access wrapper around a serialized symbol dict.

    The save path expects ``Symbol``-like attribute access (`.id`, `.file`,
    `.line`, etc.); carried-over symbols arrive as dicts pulled from a
    loaded ``CodeIndex``.  This wrapper preserves the dict's values
    without re-parsing source.
    """

    __slots__ = ("_d",)

    def __init__(self, d: dict) -> None:
        self._d = d

    def __getattr__(self, name: str):
        try:
            return self._d[name]
        except KeyError:
            # Defaults that match Symbol dataclass field types.
            if name in ("decorators", "keywords", "call_references"):
                return []
            if name in ("line", "end_line", "byte_offset", "byte_length",
                         "cyclomatic", "max_nesting", "param_count"):
                return 0
            return ""


def _file_outside_walk_prefix(file_path: str, walk_prefix: str) -> bool:
    """Return True when ``file_path`` is *not* under ``walk_prefix``.

    ``walk_prefix`` is git-root-relative (e.g. ``"packages"``).  An empty
    prefix means the walk covered the entire git root, in which case
    nothing is outside it.
    """
    if not walk_prefix:
        return False
    if file_path == walk_prefix:
        return False
    return not file_path.startswith(walk_prefix + "/")


def _merge_subdir_into_existing(
    existing,  # CodeIndex
    walk_prefix: str,
    new_source_files: list[str],
    new_symbols,  # list[Symbol]
    new_file_hashes: dict,
    new_file_summaries: dict,
    new_file_languages: dict,
    new_file_mtimes: dict,
    new_file_imports: dict,
    new_context_metadata: dict,
    new_pkg_names: list[str],
) -> dict:
    """Merge a fresh subdir walk into an existing v1.96 index.

    Files in ``existing`` outside ``walk_prefix`` carry over unchanged;
    everything else is replaced by the fresh walk.  Returns a dict with
    the merged state, suitable for splat into ``save_index`` keyword args.
    """
    carry_files = [
        f for f in existing.source_files
        if _file_outside_walk_prefix(f, walk_prefix)
    ]
    carry_set = set(carry_files)

    merged_source_files = sorted(set(carry_files) | set(new_source_files))

    new_file_set = set(s.file for s in new_symbols)
    carried_symbols = [
        _CarriedSymbol(s) for s in existing.symbols
        if s.get("file") in carry_set and s.get("file") not in new_file_set
    ]

    def _carry_dict(d: dict) -> dict:
        return {k: v for k, v in (d or {}).items() if k in carry_set}

    merged_file_hashes = {**_carry_dict(existing.file_hashes), **new_file_hashes}
    merged_file_summaries = {**_carry_dict(existing.file_summaries), **new_file_summaries}
    merged_file_languages = {**_carry_dict(existing.file_languages), **new_file_languages}
    merged_file_mtimes = {**_carry_dict(existing.file_mtimes), **new_file_mtimes}
    merged_imports = {**_carry_dict(existing.imports or {}), **(new_file_imports or {})}

    # Recompute language counts from the merged file_languages map.
    merged_languages: dict[str, int] = {}
    for lang in merged_file_languages.values():
        merged_languages[lang] = merged_languages.get(lang, 0) + 1

    # Context metadata: shallow overlay (new keys win).  Provider-specific
    # data that's per-file inside walk_prefix may be lost on overlap; an
    # acceptable trade for v1.96 MVP.  Revisit if specific providers
    # surface bugs.
    merged_context_metadata = {**(existing.context_metadata or {}), **(new_context_metadata or {})}

    # Package names: union (manifest files in either subdir contribute).
    merged_pkg_names = sorted(set((existing.package_names or []) + (new_pkg_names or [])))

    # source_roots: a full-root walk (walk_prefix == "") supersedes every
    # earlier subdir slice — the new walk covers everything, so subdir
    # markers are no longer meaningful.  Any other prefix is appended to
    # the existing list, deduped, sorted.
    if walk_prefix == "":
        merged_source_roots: list[str] = [""]
    else:
        existing_roots = list(existing.source_roots or [])
        if walk_prefix not in existing_roots:
            existing_roots.append(walk_prefix)
        merged_source_roots = sorted(set(existing_roots))

    return {
        "source_files": merged_source_files,
        "symbols": carried_symbols,  # caller appends new symbols to this
        "file_hashes": merged_file_hashes,
        "file_summaries": merged_file_summaries,
        "file_languages": merged_file_languages,
        "file_mtimes": merged_file_mtimes,
        "imports": merged_imports,
        "languages": merged_languages,
        "context_metadata": merged_context_metadata,
        "package_names": merged_pkg_names,
        "source_roots": merged_source_roots,
    }


def _resolve_repo_identity(
    folder_path: Path,
    mode: str = "config",
    store: Optional[IndexStore] = None,
) -> tuple[str, str, str]:
    """Resolve the storage identity for an indexing run.

    Returns ``(owner, repo_name, git_root)``.  ``git_root`` is the
    absolute path of the enclosing git working tree when one was detected
    and used for the identity, else the empty string.

    v1.95.0 (#288): when the path resolves into a git working tree and
    the ``git_root_identity`` config knob is on (default), the identity
    comes from ``git remote get-url origin`` so a clone of
    ``elastic/kibana`` indexes as ``elastic/kibana`` regardless of the
    local folder name — matching what ``index_repo elastic/kibana``
    would produce.  Falls back to ``("local", git-root-basename)`` for
    git roots with no configured remote, and to today's
    basename-plus-hash form when no ``.git`` is found anywhere up the
    tree or when the knob is off.
    """
    decision = resolve_index_identity(str(folder_path), mode=mode, store=store)
    return decision.owner, decision.name, decision.git_root


from ._indexing_pipeline import (
    file_languages_for_paths as _file_languages_for_paths,
    language_counts as _language_counts,
    complete_file_summaries as _complete_file_summaries,
    parse_and_prepare_incremental,
    parse_immediate,
)
from ._utils import (
    PARSER_UPGRADE_WARNING,
    describe_unloadable_index,
    needs_parser_upgrade as _needs_parser_upgrade,
    size_cap_warning as _size_cap_warning,
    stamp_incremental_outcome as _stamp_incremental_outcome,
)
from .package_registry import extract_package_names as _extract_package_names


# Watcher fast-path enrichment (audit W1). Discovering context providers runs
# each provider's detect()/load() — a `git log` or a tree walk, ~hundreds of ms.
# Per-symbol enrich_symbols() is cheap dict lookups. Providers don't change
# between file edits, so the discovered set from the initial full index is cached
# per folder and reused on subsequent watched edits, letting changed symbols keep
# their ecosystem_context / provider keywords without re-running discovery.
_PROVIDER_CACHE: dict[str, list] = {}


# Providers skipped on the most recent discovery, per folder. Discovery happens
# deep inside the index run, far from the response builder, and a provider that
# blew its budget is a KNOWN gap in the context this index carries — reporting
# it is the difference between "indexed" and "indexed, minus express".
_PROVIDER_SKIPS: dict[str, list] = {}


def _resolve_active_providers(folder_path: Path, context_providers: bool) -> list:
    """Discover the active, config-gated context providers for a folder."""
    enabled = context_providers and _config.get(
        "context_providers", True, repo=str(folder_path)
    )
    skipped: list = []
    active = discover_providers(folder_path, skipped=skipped) if enabled else []
    _PROVIDER_SKIPS[str(folder_path)] = skipped
    # Gate the SQL-dependent dbt provider when SQL is disabled for this repo.
    if active and not _config.is_language_enabled("sql", repo=str(folder_path)):
        active = [p for p in active if p.name != "dbt"]
    return active


def _attach_provider_skips(result: dict, folder_path: Path) -> None:
    """Surface budget-skipped / failed context providers on an index result."""
    skips = _PROVIDER_SKIPS.get(str(folder_path)) or []
    if not isinstance(result, dict) or not skips:
        return
    result["providers_skipped"] = skips
    for skip in skips:
        if skip.get("reason") == "budget_exceeded":
            result.setdefault("warnings", []).append(
                f"Context provider '{skip['provider']}' exceeded its "
                f"{skip.get('budget_seconds')}s budget after "
                f"{skip.get('seconds')}s and was skipped. Symbols are indexed but "
                f"carry no {skip['provider']} context, and its import edges "
                f"(route mounts, template renders) are missing from the graph. "
                f"Raise JCODEMUNCH_PROVIDER_BUDGET_SECONDS to let it finish, or "
                f"set context_providers=false to stop paying for it."
            )
        else:
            result.setdefault("warnings", []).append(
                f"Context provider '{skip['provider']}' failed: "
                f"{skip.get('error')}. Symbols are indexed but carry no "
                f"{skip['provider']} context."
            )


def _cache_active_providers(folder_path: Path, providers: list) -> None:
    """Remember a folder's providers for the watcher fast path (audit W1)."""
    _PROVIDER_CACHE[str(folder_path)] = providers


def _fast_path_providers(folder_path: Path, context_providers: bool) -> list:
    """Providers for a watched edit: reuse the initial full-index detection,
    discovering once on a cache miss (e.g. first fast cycle after a restart)."""
    cached = _PROVIDER_CACHE.get(str(folder_path))
    if cached is not None:
        return cached
    providers = _resolve_active_providers(folder_path, context_providers)
    _cache_active_providers(folder_path, providers)
    return providers


def _rel_to_root(abs_path: Path, root: Path) -> Optional[str]:
    """Root-relative posix path for a watcher change, or None if genuinely outside.

    ⚠ The naive `abs_path.relative_to(root)` raises ValueError whenever the two
    spell the same location differently, and the call sites answered that with a
    bare `continue` — so the change was **silently dropped, with no warning and
    no skip counter**. On Windows that is not hypothetical: a path can arrive in
    8.3 short form (`C:\\Users\\RUNNER~1\\...`) while the root is the long form
    (`C:\\Users\\runneradmin\\...`), or differ only by drive-letter case. A
    watcher emitting either would stop reindexing and say nothing.

    Tries the literal comparison first (cheap, and the overwhelmingly common
    case), then falls back to resolved + normcased forms before concluding the
    path is actually outside the root.
    """
    try:
        return abs_path.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        resolved = abs_path.resolve()
        root_resolved = root.resolve()
        try:
            return resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            pass
        # Last resort: normcase both (Windows drive-letter/short-name casing).
        r_norm = os.path.normcase(str(root_resolved))
        p_norm = os.path.normcase(str(resolved))
        prefix = r_norm if r_norm.endswith(os.sep) else r_norm + os.sep
        if p_norm.startswith(prefix):
            return str(resolved)[len(prefix):].replace("\\", "/")
    except OSError:
        pass
    return None


def _scan_package_json_forced_paths(folder_path: Path) -> set[str]:
    """Pre-scan ``package.json`` files under ``folder_path`` to collect the
    absolute paths of files referenced by ``main``/``module``/``exports``/
    ``bin``. These paths are exempted from the per-file size cap during
    indexing so a JS library's own entry point can never be silently
    skipped for being too large (issue #25 / lodash 4.x: ``lodash.js`` is
    548 KB and was excluded by the 500 KB default cap, leaving the package
    invisible to dead-code analysis).
    """
    import json as _json
    forced: set[str] = set()
    try:
        # Pruned walk, not rglob: rglob descends into node_modules and can only
        # discard it afterwards, so the "skip nested node_modules" filter this
        # replaces still paid to enumerate the whole dependency tree.
        # Skip set is exactly node_modules, matching the filter this replaces —
        # a manifest under dist/ or build/ still counts, as it always has.
        for pkg, _rel in iter_source_files(
            folder_path, {".json"}, skip_dirs=frozenset({"node_modules"})
        ):
            if pkg.name != "package.json":
                continue
            try:
                content = pkg.read_text(encoding="utf-8", errors="replace")
                data = _json.loads(content)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            candidates: list[str] = []
            for key in ("main", "module", "browser"):
                v = data.get(key)
                if isinstance(v, str):
                    candidates.append(v)
            exports = data.get("exports")
            if isinstance(exports, str):
                candidates.append(exports)
            elif isinstance(exports, dict):
                def _walk_exports(node):
                    if isinstance(node, str):
                        candidates.append(node)
                    elif isinstance(node, dict):
                        for v in node.values():
                            _walk_exports(v)
                _walk_exports(exports)
            bins = data.get("bin")
            if isinstance(bins, str):
                candidates.append(bins)
            elif isinstance(bins, dict):
                candidates.extend(v for v in bins.values()
                                  if isinstance(v, str))
            pkg_dir = pkg.parent
            for cand in candidates:
                cand = cand.lstrip("./")
                target = (pkg_dir / cand).resolve()
                # If extension-less, try common JS/TS extensions and index
                # variants so we resolve to a concrete file on disk.
                if target.is_file():
                    forced.add(os.path.normcase(str(target)))
                    continue
                for ext in (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"):
                    trial = pkg_dir / f"{cand}{ext}"
                    if trial.is_file():
                        forced.add(os.path.normcase(str(trial.resolve())))
                        break
                else:
                    for sub in ("/index.js", "/index.ts", "/index.mjs",
                                "/index.cjs"):
                        trial = pkg_dir / f"{cand}{sub}"
                        if trial.is_file():
                            forced.add(os.path.normcase(str(trial.resolve())))
                            break
    except OSError:
        pass
    return forced


def _refresh_git_head_if_advanced(store, owner, name, folder_path, stored_head):
    """On a no-change incremental run, advance the index's stored ``git_head``
    if live HEAD has moved (#330).

    ``FreshnessProbe`` flags every result ``stale_index`` when the stored index
    SHA differs from live HEAD. A commit that changes only non-indexed files (or
    otherwise leaves indexed content unchanged) advances HEAD while the
    no-change return paths in ``index_folder`` never updated the stored SHA, so
    retrieval kept reporting otherwise-current symbols as stale even right after
    a successful "No changes detected" run.

    Writes a metadata-only delta (empty changed/new/deleted) that just refreshes
    ``git_head`` + ``indexed_at``. Best-effort: any failure is swallowed so a
    no-change run never hard-fails on the freshness-metadata refresh. Returns the
    new head when written, else ``""``.
    """
    try:
        current_head = _get_git_head(folder_path) or ""
    except Exception:
        logger.debug("git_head probe failed for %s/%s", owner, name, exc_info=True)
        return ""
    if not current_head or current_head == (stored_head or ""):
        return ""
    try:
        store.incremental_save(
            owner=owner, name=name,
            changed_files=[], new_files=[], deleted_files=[],
            new_symbols=[], raw_files={},
            git_head=current_head,
        )
        return current_head
    except Exception:
        logger.debug("git_head metadata refresh failed for %s/%s", owner, name, exc_info=True)
        return ""


def resolve_explicit_paths(
    walk_root: Path,
    paths: list,
    max_files: Optional[int],
    max_size: Optional[int] = None,
    follow_symlinks: bool = False,
) -> tuple[list[Path], list[str], dict[str, int], list[str]]:
    """Materialise a caller-supplied list of paths into the (files, warnings,
    skip_counts, requested_rels) shape that the standard indexing pipeline
    expects.

    ``requested_rels`` is the list of root-relative entries the caller asked
    for — including directories and entries that no longer exist on disk. The
    incremental path uses it to scope deletions to exactly the listed subset so
    a subset refresh never prunes unlisted indexed files, while still removing a
    listed file that was deleted on disk (#333).

    Each entry can be absolute or relative to ``walk_root``. Files are added
    when they live under ``walk_root`` and have a known language. Directories
    are recursed via ``discover_local_files`` against that subtree (so the
    same .gitignore / framework filter applies). Entries outside the root,
    non-existent paths, symlink escapes, oversize files, secret/credential
    files, and binary files are rejected with per-entry warnings. The
    security-relevant filters (symlink, secret, binary, size) mirror the full
    walk; unlike the walk, explicit paths deliberately opt past gitignore and
    skip-directory rules so a caller can name a generated/ignored source file
    on purpose (e.g. ``index_dependency`` indexing a ``dist/`` snapshot).

    Used by ``index_folder(paths=...)`` so agents can re-index exactly the
    files they already know about (git-diff list, edited-files list,
    rg-matched list) without paying the cost of a full directory walk.
    """
    # Same resolve-on-entry rule as discover_local_files: the explicit-paths
    # route is a second walk entry point, and a cap that applies to one and not
    # the other is the defect v1.108.194 exists to close. `repo=` is what makes
    # the walked root, not the ambient process, decide the cap (#390).
    max_size = get_max_file_size(max_size, repo=str(Path(walk_root).resolve()))
    files: list[Path] = []
    warnings: list[str] = []
    skip_counts: dict[str, int] = {}
    requested_rels: list[str] = []
    seen: set = set()

    cap = max_files if max_files is not None else 10_000_000

    for raw in paths:
        if len(files) >= cap:
            break
        if not isinstance(raw, str) or not raw.strip():
            warnings.append(f"Skipped empty/non-string path: {raw!r}")
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (walk_root / p)
        try:
            p = p.resolve()
        except OSError as e:
            warnings.append(f"Skipped unresolvable path {raw!r}: {e}")
            continue

        try:
            _rel = p.relative_to(walk_root).as_posix()
        except ValueError:
            warnings.append(f"Skipped path outside walk root: {raw!r}")
            continue
        # Record the requested root-relative entry (even when it no longer
        # exists on disk) so the incremental path can scope deletions to exactly
        # the listed subset and still prune a listed file that was deleted (#333).
        requested_rels.append(_rel)

        if not p.exists():
            warnings.append(f"Skipped non-existent path: {raw!r}")
            continue

        if p.is_dir():
            remaining = cap - len(files)
            sub_files, sub_warnings, sub_skip = discover_local_files(
                p,
                max_files=remaining,
                max_size=max_size,
                follow_symlinks=follow_symlinks,
            )
            warnings.extend(sub_warnings)
            for k, v in sub_skip.items():
                skip_counts[k] = skip_counts.get(k, 0) + v
            for f in sub_files:
                fr = f.resolve()
                if fr not in seen:
                    seen.add(fr)
                    files.append(f)
                    if len(files) >= cap:
                        break
            continue

        if not p.is_file():
            warnings.append(f"Skipped non-file/non-dir entry: {raw!r}")
            continue

        # File-level security mirrors discover_local_files
        if not follow_symlinks and p.is_symlink():
            warnings.append(f"Skipped symlink (follow_symlinks=False): {raw!r}")
            skip_counts["symlink"] = skip_counts.get("symlink", 0) + 1
            continue

        # Secret-file detection — mirrors _should_index_file step 10. Without
        # this, an explicitly-listed credential file (.env, *.pem,
        # secrets/*.yaml, credentials.json) was indexed and later served
        # unredacted by the source-dump tools, while the full walk refused it.
        # The explicit-paths branch must apply the same secret filter the walk
        # does; it intentionally still opts past gitignore/skip-dir so callers
        # can name generated/ignored source files on purpose.
        if is_secret_file(_rel, repo=str(walk_root)):
            warnings.append(f"Skipped secret file: {_rel}")
            skip_counts["secret"] = skip_counts.get("secret", 0) + 1
            continue

        if get_language_for_path(str(p)) is None and p.suffix not in LANGUAGE_EXTENSIONS:
            warnings.append(f"Skipped unsupported extension: {raw!r}")
            skip_counts["unknown_extension"] = skip_counts.get("unknown_extension", 0) + 1
            continue

        try:
            if p.stat().st_size > max_size:
                warnings.append(f"Skipped oversize file (>{max_size} bytes): {raw!r}")
                skip_counts["too_large"] = skip_counts.get("too_large", 0) + 1
                continue
        except OSError as e:
            warnings.append(f"Skipped stat-error path {raw!r}: {e}")
            continue

        # Binary detection — mirrors _should_index_file step 13.
        if is_binary_file(p):
            warnings.append(f"Skipped binary file: {_rel}")
            skip_counts["binary"] = skip_counts.get("binary", 0) + 1
            continue

        pr = p.resolve()
        if pr not in seen:
            seen.add(pr)
            files.append(p)

    return files[:cap], warnings, skip_counts, requested_rels


def discover_local_files(
    folder_path: Path,
    max_files: Optional[int] = None,
    max_size: Optional[int] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
) -> tuple[list[Path], list[str], dict[str, int]]:
    """Discover source files in a local folder with security filtering.

    Args:
        folder_path: Root folder to scan (must be resolved).
        max_files: Maximum number of files to index.
        max_size: Maximum file size in bytes.
        extra_ignore_patterns: Additional gitignore-style patterns to exclude.
        follow_symlinks: Whether to include symlinked files in indexing.
            Symlinked directories are never followed to prevent infinite
            loops from circular symlinks. Default False for safety.

    Returns:
        Tuple of (list of Path objects for source files, list of warning strings).
    """
    # ⚠ v1.108.194: resolve the size cap HERE, not at each call site. v1.108.193
    # gave the cap a config key and an env var, but `index_folder` called this
    # function without `max_size=`, so the walk kept the hardcoded default and
    # the new route reached only the watcher fast path. Resolving on entry makes
    # every caller correct by default, including ones not yet written — the
    # same shape as `max_files` on the line above (@dkiaulakis, #375).
    #
    # ⚠ v1.108.197: pass `repo=` too. Resolving on entry fixed WHERE the cap is
    # read; it did not fix WHICH config is read. Without a repo the resolver sees
    # global config only, so a cap set in the project's own `.jcodemunch.jsonc`
    # was parsed, cached, and then never consulted (#390 / #391 @amarakramali). The key
    # is the walked root because that is what `load_project_config` was called
    # with, before the git-root retarget moves `folder_path` (line ~1319).
    root = folder_path.resolve()
    _repo_key = str(root)
    max_size = get_max_file_size(max_size, repo=_repo_key)
    max_files = get_max_folder_files(max_files, repo=_repo_key)
    respect_cachedir_tag = get_respect_cachedir_tag(repo=_repo_key)
    files = []
    warnings = []
    oversize: list[str] = []

    skip_counts: dict[str, int] = {
        "skip_dir": 0,
        "nested_worktree": 0,
        "skip_file": 0,
        "symlink": 0,
        "symlink_escape": 0,
        "path_traversal": 0,
        "gitignore": 0,
        "extra_ignore": 0,
        "secret": 0,
        "wrong_extension": 0,
        "too_large": 0,
        "unreadable": 0,
        "binary": 0,
        "file_limit": 0,
        "cache_dir": 0,
    }

    # Pre-compute string-based gitignore specs — built incrementally during
    # the walk below (P8: single os.walk pass instead of two).
    gitignore_str_specs: list[tuple[str, pathspec.PathSpec]] = []

    # Pre-compute root path strings (root is already resolved above).
    # Normalized variants use os.path.normcase for case-insensitive comparison
    # on Windows (no-op on POSIX).
    root_str = str(root)
    root_prefix = root_str + os.sep
    root_str_norm = os.path.normcase(root_str)
    root_prefix_norm = os.path.normcase(root_prefix)

    # Merge env-var global, project-level, and per-call patterns, then build
    # spec. Passing repo=str(folder_path) so .jcodemunch.jsonc overrides land
    # (issue #300, reported by @domis86).
    effective_extra = get_extra_ignore_patterns(
        extra_ignore_patterns, repo=str(folder_path)
    )
    extra_spec = None
    if effective_extra:
        try:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", effective_extra)
        except Exception:
            pass

    # Pre-scan package.json files; their `main`/`module`/`exports`/`bin`
    # targets get the size-cap exemption. Built once before the walk.
    forced_paths = _scan_package_json_forced_paths(root)

    # Build per-file filter config once. Shared with the watcher fast path
    # via ``_should_index_file`` (see #306). ``skip_dirs_regex`` is None
    # here because ``os.walk`` below prunes those directories before any
    # of their files reach the helper — keeping behaviour identical.
    filter_cfg = _build_index_filters(
        root=root,
        follow_symlinks=follow_symlinks,
        max_size=max_size,
        extra_spec=extra_spec,
        forced_paths=forced_paths,
        skip_dirs_regex=None,
        check_binary=True,
        check_filename=True,
        respect_cachedir_tag=respect_cachedir_tag,
    )

    skip_dirs_regex = _build_skip_dirs_regex(repo=str(root))
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        dpath = Path(dirpath)
        # Prune directories that should always be skipped before descending.
        # Nested linked worktrees (`.git` FILE → `.git/worktrees/<name>`,
        # e.g. Claude Code's `<repo>/.claude/worktrees/`) are separate
        # working trees whose near-duplicate checkouts would pollute this
        # index and burn the max_folder_files cap (#372).
        pruned = []
        worktrees = []
        caches = []
        kept = []
        for d in dirnames:
            if skip_dirs_regex.match(d):
                pruned.append(d)
            elif is_linked_worktree(dpath / d):
                worktrees.append(d)
            # A directory that declares ITSELF a cache, per the Cache Directory
            # Tagging Specification. Checked last because it costs an open() and
            # the two rules above are string/stat work; ordering is behaviour-
            # neutral since a directory matching an earlier rule is pruned
            # either way.
            elif respect_cachedir_tag and is_cache_directory(dpath / d):
                caches.append(d)
            else:
                kept.append(d)
        if pruned or worktrees or caches:
            rel_dir = os.path.relpath(dirpath, root_str)
            for d in pruned:
                skip_counts["skip_dir"] += 1
                logger.debug("SKIP skip_dir: %s", os.path.join(rel_dir, d))
            for d in worktrees:
                skip_counts["nested_worktree"] += 1
                logger.debug(
                    "SKIP nested_worktree: %s", os.path.join(rel_dir, d)
                )
            for d in caches:
                skip_counts["cache_dir"] += 1
                logger.debug("SKIP cache_dir: %s", os.path.join(rel_dir, d))
        dirnames[:] = kept

        # Load .gitignore for this directory BEFORE filtering its files so
        # that patterns defined here apply to siblings in the same directory.
        if ".gitignore" in filenames:
            gitignore_path = dpath / ".gitignore"
            try:
                content = gitignore_path.read_text(encoding="utf-8", errors="replace")
                spec = pathspec.PathSpec.from_lines("gitignore", content.splitlines())
                gitignore_str_specs.append((str(dpath.resolve()) + os.sep, spec))
            except Exception:
                pass

        for filename in filenames:
            file_path = dpath / filename
            ok, reason, rel_path, warning = _should_index_file(
                file_path, filter_cfg, gitignore_str_specs
            )
            if not ok:
                skip_counts[reason] = skip_counts.get(reason, 0) + 1
                if reason == "too_large" and rel_path:
                    # Collected, not warned per file: the aggregate lands once
                    # after the walk (#429). `_should_index_file` deliberately
                    # returns no warning here because the watcher fast path
                    # shares it and fires per event.
                    oversize.append(rel_path)
                if warning is not None:
                    warnings.append(warning)
                logger.debug(
                    "SKIP %s: %s", reason,
                    rel_path or os.path.join(os.path.relpath(dirpath, root_str), filename),
                )
                continue

            logger.debug("ACCEPT: %s", rel_path)
            files.append(file_path)

    logger.info(
        "Discovery complete — accepted: %d, skipped by reason: %s",
        len(files),
        skip_counts,
    )

    _size_warning = _size_cap_warning(oversize, max_size)
    if _size_warning is not None:
        warnings.append(_size_warning)

    # File count limit with prioritization
    if len(files) > max_files:
        skip_counts["file_limit"] = len(files) - max_files
        # Prioritize: src/, lib/, pkg/, cmd/, internal/ first
        priority_dirs = ["src/", "lib/", "pkg/", "cmd/", "internal/"]

        def priority_key(file_path: Path) -> tuple:
            try:
                rel_path = file_path.relative_to(root).as_posix()
            except ValueError:
                return (999, 999, str(file_path))

            # Check if in priority dir
            for i, prefix in enumerate(priority_dirs):
                if rel_path.startswith(prefix):
                    return (i, rel_path.count("/"), rel_path)
            # Not in priority dir - sort after
            return (len(priority_dirs), rel_path.count("/"), rel_path)

        files.sort(key=priority_key)
        files = files[:max_files]

    return files, warnings, skip_counts


def index_folder(
    path: str,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
    incremental: bool = True,
    context_providers: bool = True,
    changed_paths: Optional[list[WatcherChange]] = None,
    paths: Optional[list[str]] = None,
    progress_cb: "Optional[Callable[[int, int, str], None]]" = None,
    identity_mode: str = "config",
    force_reparse: bool = False,
    max_size: Optional[int] = None,
) -> dict:
    """Index a local folder containing source code.

    Args:
        path: Path to local folder (absolute or relative).
        use_ai_summaries: Whether to use AI for symbol summaries.
        storage_path: Custom storage path (default: ~/.code-index/).
        extra_ignore_patterns: Additional gitignore-style patterns to exclude.
        follow_symlinks: Whether to include symlinked files. Symlinked directories
            are never followed (prevents infinite loops). Default False.
        context_providers: Whether to run context providers (default True).
            Set to False or set JCODEMUNCH_CONTEXT_PROVIDERS=0 to disable.
        incremental: When True and an existing index exists, only re-index changed files.
        changed_paths: Optional pre-known change set from the watcher, as a list of
            (change_type, absolute_path) tuples where change_type is one of
            "added", "modified", "deleted".  When provided with incremental=True
            and an existing index, skips full directory discovery (~3s → ~50ms).
        identity_mode: "config" (default), "local", or "git". Local mode keeps
            v1.90 path-hash identity; git mode opts in to git-root identity.
        force_reparse: Re-parse the files listed in `paths` even when their
            content is unchanged (v1.108.259, #395). Requires `paths` and
            `incremental`; ignored otherwise.

            ⚠ Without this, a subset refresh over unchanged files is a no-op by
            design: `detect_changes_with_mtimes` compares hashes and correctly
            reports "No changes detected". That is right for an edit and wrong
            for a PARSER_GENERATION upgrade, where the file content is identical
            and the stored SYMBOLS are the thing that is wrong. It is the whole
            reason `index_folder(paths=[...])` could not be used to slice up a
            generation upgrade before this existed.
        max_size: Per-file byte cap for this run, overriding config and the
            default (#429). ``get_max_file_size`` has accepted this override
            since v1.108.193, but no caller passed one and it reached no tool
            schema, so over MCP — the transport every actual user is on — the
            only route to the cap was editing a config file. Left None the
            resolution order is unchanged: project ``.jcodemunch.jsonc``, then
            global config / ``JCODEMUNCH_MAX_FILE_SIZE``, then the default.

            ⚠ Per-call, so it does NOT persist. A repo with a permanently
            oversize file wants the config key; this is for one run.

    Returns:
        Dict with indexing results.
    """
    # Resolve folder path
    folder_path = Path(path).expanduser().resolve()

    if not folder_path.exists():
        return {"success": False, "error": f"Folder not found: {path}"}

    if not folder_path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}

    # Evict stale tsconfig alias map so re-indexing picks up edited tsconfig.json (C6-A)
    _imap_cache.pop(str(folder_path), None)

    # Load and cache project-level config (.jcodemunch.jsonc) so subsequent
    # config.get() calls within this indexing run use project overrides.
    # This handles both first-time indexing and re-indexing of existing projects.
    _config.load_project_config(str(folder_path))

    warnings = []
    # What the caller ASKED for, pinned before anything downstream can flip
    # `incremental` (#413). Every non-error return stamps requested vs
    # performed, so a caller never has to grep `warnings[]` for a sentence to
    # learn that the operation it requested was substituted for another one.
    _requested_incremental = incremental
    rebuild_reason: Optional[str] = None
    trusted_folders = _config.get("trusted_folders", [], repo=str(folder_path))
    whitelist_mode = _config.get(
        "trusted_folders_whitelist_mode", True, repo=str(folder_path)
    )

    # Handle empty blacklist as error
    if not whitelist_mode and not trusted_folders:
        error_msg = (
            "trusted_folders_whitelist_mode is False (blacklist mode) but "
            "trusted_folders is empty. No folders would be trusted. "
            "Add entries to trusted_folders to specify which folders should be untrusted."
        )
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    is_trusted = _is_trusted(folder_path, tuple(trusted_folders), whitelist_mode)
    if trusted_folders and not is_trusted:
        return {
            "success": False,
            "error": f"Resolved path '{folder_path}' is not under trusted_folders.",
        }

    # Guard against dangerously broad roots.  A relative path like "." resolves
    # against the MCP server's CWD (not the caller's project directory), which
    # can be "/" or "~" when the server is launched by a system launcher.
    # Reject paths with fewer than 3 parts (e.g. "/", "/home", "C:\Users") and
    # warn whenever the caller supplied a relative path so the resolved value is
    # always visible in the tool response.
    #
    # In container environments (Docker, devcontainers, Codespaces, Podman),
    # projects are commonly mounted at shallow paths like /workspace or /app.
    # These have only 2 path parts and would be blocked by the default minimum
    # of 3.  When a container is detected, the minimum is lowered to 2 so that
    # /workspace works out of the box while bare "/" is still rejected.
    container = _is_container()
    _MIN_PATH_PARTS = 2 if container else 3
    path_part_count = _path_safety_part_count(folder_path)
    if path_part_count < _MIN_PATH_PARTS:
        shallow_windows_git_root = _is_shallow_windows_git_root(folder_path)
        if not is_trusted and not shallow_windows_git_root:
            error_msg = (
                f"Resolved path '{folder_path}' is too broad to index safely "
                f"(fewer than {_MIN_PATH_PARTS} path components). "
                "Pass an absolute path to the specific project directory instead of a "
                "relative path like '.' — relative paths resolve against the MCP "
                "server's working directory, which may not be your project root."
            )
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        if is_trusted:
            warning_msg = (
                f"Resolved path '{folder_path}' would normally be rejected as too broad, "
                "but it matched trusted_folders and was allowed."
            )
        else:
            warning_msg = (
                f"Resolved path '{folder_path}' is a Git working-tree root directly "
                "below a Windows drive root and was allowed."
            )
        logger.warning(warning_msg)
        warnings.append(warning_msg)

    if container and path_part_count < 3:
        warning_msg = (
            f"Container environment detected — allowing shallow path '{folder_path}'. "
            "The minimum path depth has been relaxed from 3 to 2 components."
        )
        logger.info(warning_msg)
        warnings.append(warning_msg)

    # Warn when a relative path was given so callers can see what it resolved to.
    if not Path(path).expanduser().is_absolute():
        warning_msg = (
            f"Relative path '{path}' resolved to '{folder_path}' (MCP server CWD). "
            "Prefer passing an absolute path to avoid unexpected behaviour."
        )
        logger.warning(warning_msg)
        warnings.append(warning_msg)

    # Redact absolute path from responses when redact_source_root is enabled.
    # Project-overridable (#301): per-repo privacy preferences are valid.
    _redact = _config.get("redact_source_root", False, repo=str(folder_path))
    _folder_display = folder_path.name if _redact else str(folder_path)
    store = IndexStore(base_path=storage_path)
    _pairs_for_identity = parse_path_map()
    _identity_path = Path(remap(str(folder_path), _pairs_for_identity, reverse=True))
    try:
        _identity_decision = resolve_index_identity(
            str(_identity_path),
            mode=identity_mode,
            store=store,
        )
    except (IdentityModeAmbiguous, IdentityModeConflict) as exc:
        return {"success": False, "error": str(exc)}
    owner = _identity_decision.owner
    repo_name = _identity_decision.name
    _git_root = _identity_decision.git_root

    # ── v1.96 git-root retarget ──
    # If git_root_identity is on (default) and `folder_path` resolves into a
    # git working tree, anchor path resolution at the git root and walk
    # only the user-requested subdir.  All file paths in the resulting
    # index are git-root-relative, so multiple `index <subdir>` calls
    # against the same clone coalesce into a single repo index.
    walk_root = folder_path
    _git_root_for_walk = ""
    _gr = None
    if _identity_decision.mode == "git" and _identity_decision.git_root:
        try:
            from ..storage.git_root import GitRootIdentity
            _gr = GitRootIdentity(
                git_root=_identity_decision.git_root,
                owner=_identity_decision.owner,
                name=_identity_decision.name,
            )
        except Exception:
            logger.debug("git-root detection failed during retarget", exc_info=True)
            _gr = None
    if _gr is not None:
        _gr_path = Path(_gr.git_root).resolve()
        try:
            _is_subdir = folder_path != _gr_path and folder_path.is_relative_to(_gr_path)
        except AttributeError:
            # Python < 3.9 fallback (shouldn't trigger; project requires 3.10+)
            try:
                folder_path.relative_to(_gr_path)
                _is_subdir = folder_path != _gr_path
            except ValueError:
                _is_subdir = False
        if folder_path == _gr_path or _is_subdir:
            walk_root = folder_path
            _git_root_for_walk = str(_gr_path)
            folder_path = _gr_path

    # walk_prefix is what `walk_root` looks like relative to `folder_path`
    # (= the git root when we retargeted, else folder_path itself so the
    # prefix is "").  Used by the merge logic to decide which existing
    # files to carry over.
    if walk_root == folder_path:
        walk_prefix = ""
    else:
        walk_prefix = walk_root.relative_to(folder_path).as_posix()

    # ⚠ v1.108.247: pass `repo=`. Resolving here WITHOUT a repo key was not a
    # missing route — it was a route that OVERRODE the correct one. `max_files`
    # is handed to `discover_local_files`/`resolve_explicit_paths` below, and
    # `_discover_source_files` treats a non-None `max_files` as an explicit
    # caller override, short-circuiting its own repo-aware resolution (line
    # ~1134). So the global value resolved here silently won on EVERY path, full
    # walk included — `.jcodemunch.jsonc` was parsed, reported by
    # `config --check`, and then never consulted (#416 @domis86). The key is
    # `walk_root`, matching both `load_project_config` above and the key
    # `_discover_source_files` builds, and it is read BEFORE the git-root
    # retarget moves `folder_path`.
    max_files = get_max_folder_files(repo=str(walk_root.resolve()))

    try:
        t0 = time.monotonic()

        # ── Deferred summarization helper (defined before fast path so it is in scope) ──

        def _run_deferred_summarize(
            gen: int,
            repo_full: str,
            symbols: list,
            file_contents: dict,
            store: "IndexStore",
            owner: str,
            repo_name: str,
        ) -> None:
            """Fill in AI summaries and update the store. Checks generation counter to abandon stale work."""
            from ..reindex_state import _get_state, get_deferred_save_lock
            from ._indexing_pipeline import deferred_summarize

            # Check 1: has a newer reindex started while we were parsing?
            if _get_state(repo_full).deferred_generation != gen:
                logger.debug(
                    "Deferred summarize gen=%d abandoned for %s (generation advanced before summarize)",
                    gen, repo_full,
                )
                return

            summarized = deferred_summarize(symbols, file_contents, use_ai_summaries=True, repo=repo_full)
            if not summarized:
                return

            # Check 2 + save are held under the deferred-save lock (T7).
            # mark_reindex_start also acquires this lock before bumping the generation,
            # so the check and the write are atomic with respect to new reindexes:
            # either we write before the new gen is bumped, or we see the new gen and abort.
            save_lock = get_deferred_save_lock(repo_full)
            with save_lock:
                if _get_state(repo_full).deferred_generation != gen:
                    logger.debug(
                        "Deferred summarize gen=%d abandoned for %s (generation advanced before save)",
                        gen, repo_full,
                    )
                    return

                # Update only the symbol summaries (empty change lists → INSERT OR REPLACE updates existing rows)
                try:
                    store.incremental_save(
                        owner=owner, name=repo_name,
                        changed_files=[], new_files=[], deleted_files=[],
                        new_symbols=summarized,
                        raw_files={},
                    )
                    logger.info(
                        "Deferred AI summarization gen=%d saved %d symbols for %s",
                        gen, len(summarized), repo_full,
                    )
                except Exception as e:
                    logger.warning("Deferred summarization failed for %s: %s", repo_full, e)

        # ── Fast path: watcher-driven incremental reindex ──
        # When the watcher provides the exact change set, skip full directory
        # discovery (~3s on Windows) and only process the affected files.
        if changed_paths and incremental:
            # Build the same filter bundle the full walk uses (#306). The
            # fast path previously applied only the extension check (and as
            # of v1.108.19 extra_ignore_patterns) but skipped every other
            # filter from ``discover_local_files`` — gitignore, size cap,
            # symlink protection, skip-dirs, secrets, binary. A modify
            # event on an oversize/ignored/symlinked file would silently
            # re-index it. ``_should_index_file`` is the shared helper.
            #
            # Tradeoffs accepted on this path for ~50ms per-event latency:
            #   - gitignore loads root-level only (not per-subdir). Nested
            #     .gitignores miss; documented in #306 as a known limit.
            #   - package.json forced-path exemption from the size cap is
            #     skipped (would require an rglob). Initial full walk
            #     handles the exemption; subsequent fast-path edits to a
            #     forced file may hit the size cap.
            #   - is_binary_file disabled — the fast path reads file bytes
            #     immediately after; a second open for binary sniffing is
            #     wasteful. Extension check already rejects most binaries.
            _fast_effective_extra = get_extra_ignore_patterns(
                extra_ignore_patterns, repo=str(folder_path)
            )
            _fast_extra_spec = None
            if _fast_effective_extra:
                try:
                    _fast_extra_spec = pathspec.PathSpec.from_lines(
                        "gitignore", _fast_effective_extra
                    )
                except Exception:
                    _fast_extra_spec = None

            # Load root-level .gitignore once (string-prefix spec form).
            _fast_gitignore_specs: list[tuple[str, "pathspec.PathSpec"]] = []
            try:
                _root_gitignore = folder_path / ".gitignore"
                if _root_gitignore.is_file():
                    _gi_content = _root_gitignore.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    _gi_spec = pathspec.PathSpec.from_lines(
                        "gitignore", _gi_content.splitlines()
                    )
                    _fast_gitignore_specs.append(
                        (str(folder_path.resolve()) + os.sep, _gi_spec)
                    )
            except Exception:
                pass

            # ── forced_paths on the fast path (@dkiaulakis, 2026-07-27) ──
            # The full walk exempts package.json entry points from the size cap
            # (#25). This path passed an EMPTY set, so the same oversize file was
            # indexed by a full walk and dropped as `too_large` by an incremental
            # one — it appeared, then silently vanished on the next edit.
            #
            # Scanning is NOT unconditional: _scan_package_json_forced_paths
            # walks the tree, and this path exists to avoid exactly that per
            # event. The exemption can only change an outcome for a file that is
            # actually over the cap, so the scan is paid only when the change set
            # contains one — the common case still walks nothing.
            # `repo=` for the same reason the two walks below take it: this is a
            # third discovery entry point, and a cap the project sets must reach
            # all three or the file appears on one route and vanishes on another.
            # `max_size` first for the same reason `repo=` is passed: this is
            # the third discovery entry point, and a cap that reaches only two
            # of them makes a file appear on one route and vanish on another
            # (#429 follows the same rule the comment above states for repo).
            _fast_max_size = get_max_file_size(
                max_size, repo=str(Path(walk_root).resolve())
            )

            def _fast_forced_paths() -> set:
                for _c in changed_paths:
                    _p = _c[1] if isinstance(_c, (tuple, WatcherChange)) else _c
                    try:
                        if Path(_p).stat().st_size > _fast_max_size:
                            return _scan_package_json_forced_paths(
                                folder_path.resolve()
                            )
                    except OSError:
                        continue
                return set()

            _fast_filter_cfg = _build_index_filters(
                root=folder_path.resolve(),
                follow_symlinks=follow_symlinks,
                max_size=_fast_max_size,
                extra_spec=_fast_extra_spec,
                forced_paths=_fast_forced_paths(),
                skip_dirs_regex=_build_skip_dirs_regex(
                    repo=str(Path(walk_root).resolve())
                ),
                check_binary=False,
                check_filename=True,
                respect_cachedir_tag=get_respect_cachedir_tag(
                    repo=str(Path(walk_root).resolve())
                ),
            )

            # Branch detection for watcher fast-path
            _fast_branch = _get_git_branch(folder_path)
            _fast_is_branch_delta = False
            _fast_base_index = store.load_index(owner, repo_name)  # always load base for branch check
            if _fast_base_index is not None and _fast_branch:
                _fast_base_branch = getattr(_fast_base_index, "branch", "") or ""
                if not _fast_base_branch:
                    _fast_base_branch = _fast_branch
                if _fast_branch != _fast_base_branch:
                    _fast_is_branch_delta = True

            # Determine if watcher provided old_hash via WatcherChange objects.
            # If so, we can skip loading the index and use the memory-cached hashes.
            watcher_changes_with_hashes = [
                c for c in changed_paths
                if isinstance(c, WatcherChange) and c.old_hash
            ]
            use_memory_hash_cache = bool(watcher_changes_with_hashes)

            if _fast_is_branch_delta:
                # For branch delta mode, load the composed branch index for comparison
                existing_index = store.load_index(owner, repo_name, branch=_fast_branch)
            elif not use_memory_hash_cache:
                existing_index = _fast_base_index
            else:
                existing_index = None

            # Build memory hash map from WatcherChange objects (from watcher memory cache)
            _old_hash_map: dict[str, str] = {}
            if use_memory_hash_cache:
                for wc in watcher_changes_with_hashes:
                    # Use index access for both WatcherChange and legacy tuple compat
                    change_type = wc[0]
                    abs_path_str = wc[1]
                    old_hash = wc[2]
                    abs_path = Path(abs_path_str)
                    rel_path = _rel_to_root(abs_path, folder_path)
                    if rel_path is None:
                        continue
                    _old_hash_map[rel_path] = old_hash

            # An index whose symbols predate the current extraction semantics
            # cannot be repaired by a delta: the files carrying the bad symbols
            # are UNCHANGED, so a change-set-driven pass never re-parses them
            # (#414). Disarm the fast path and let the full walk below take the
            # upgrade branch, which is the only thing that rewrites every row.
            if _needs_parser_upgrade(_fast_base_index):
                existing_index = None
                use_memory_hash_cache = False

            if existing_index is not None or use_memory_hash_cache:
                # Reuse the providers discovered by the initial full index so a
                # watched edit re-enriches its changed symbols (ecosystem_context
                # / keywords) rather than dropping enrichment until the next full
                # reindex (audit W1). Discovery is the ~hundreds-of-ms cost and
                # providers don't change between edits, so the cached set is
                # reused; enrichment itself is cheap dict lookups. Discovered once
                # on a cache miss (first fast cycle after a process restart).
                active_providers = _fast_path_providers(folder_path, context_providers)

                # Classify watcher events into changed/new/deleted rel_paths
                changed_files: list[str] = []
                new_files: list[str] = []
                deleted_files: list[str] = []
                rel_path_map_fast: dict[str, Path] = {}

                for wc_item in changed_paths:
                    # Support both WatcherChange (with .change_type/.path/.old_hash)
                    # and legacy (change_type, path) or (change_type, path, old_hash) tuples
                    if isinstance(wc_item, WatcherChange):
                        change_type = wc_item.change_type
                        abs_path_str = wc_item.path
                        old_hash = wc_item.old_hash
                    else:
                        change_type = wc_item[0]
                        abs_path_str = wc_item[1]
                        old_hash = wc_item[2] if len(wc_item) > 2 else ""

                    abs_path = Path(abs_path_str)
                    rel_path = _rel_to_root(abs_path, folder_path)
                    if rel_path is None:
                        continue

                    # Apply the shared filter bundle (#306). Deletions bypass
                    # filters — a file that was indexed before its ignore
                    # rule existed should still be removed from the index
                    # when deleted, and the file may already be gone (so
                    # filter checks that stat the path would fail anyway).
                    if change_type != "deleted":
                        _ok, _reason, _hl_rel_path, _warning = _should_index_file(
                            abs_path, _fast_filter_cfg, _fast_gitignore_specs
                        )
                        if not _ok:
                            logger.debug(
                                "SKIP %s (watcher fast path): %s",
                                _reason, _hl_rel_path or rel_path,
                            )
                            continue

                    if change_type == "deleted":
                        if use_memory_hash_cache:
                            # Memory cache path: the watcher confirmed this file was
                            # in the index (it was in the hash cache), so trust it.
                            deleted_files.append(rel_path)
                        elif existing_index is not None and existing_index.has_source_file(rel_path):
                            deleted_files.append(rel_path)
                    elif change_type == "added":
                        if existing_index is None or not existing_index.has_source_file(rel_path):
                            new_files.append(rel_path)
                            rel_path_map_fast[rel_path] = abs_path
                        else:
                            # File exists in index but watcher says "added" (e.g. recreated)
                            changed_files.append(rel_path)
                            rel_path_map_fast[rel_path] = abs_path
                    else:  # modified
                        changed_files.append(rel_path)
                        rel_path_map_fast[rel_path] = abs_path

                if not changed_files and not new_files and not deleted_files:
                    _refresh_git_head_if_advanced(
                        store, owner, repo_name, folder_path,
                        existing_index.git_head if existing_index else None,
                    )
                    _fast_no_change = {
                        "success": True,
                        "message": "No changes detected",
                        "repo": f"{owner}/{repo_name}",
                        "folder_path": _folder_display,
                        "changed": 0, "new": 0, "deleted": 0,
                        "duration_seconds": round(time.monotonic() - t0, 2),
                    }
                    _stamp_incremental_outcome(
                        _fast_no_change, _requested_incremental, True
                    )
                    return _fast_no_change

                # Read and hash only the changed/new files.
                # For "modified" files, compare hash against stored hash —
                # if content is identical (e.g. touch, save-without-change),
                # skip re-parsing and just update the mtime.
                # Use memory cache (_old_hash_map) if available, otherwise fall back to
                # the index's stored hashes.
                old_hashes: dict[str, str]
                if use_memory_hash_cache:
                    old_hashes = _old_hash_map
                else:
                    _idx = existing_index  # type: ignore[assignment]
                    old_hashes = _idx.file_hashes or {}
                actually_changed: list[str] = []
                raw_files_subset: dict[str, str] = {}
                subset_hashes: dict[str, str] = {}
                fast_mtimes: dict[str, int] = {}
                fast_warnings: list[str] = []
                mtime_only_updates: dict[str, int] = {}

                for rel_path in set(changed_files) | set(new_files):
                    abs_path = rel_path_map_fast[rel_path]
                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                            content = f.read()
                    except Exception as e:
                        fast_warnings.append(f"Failed to read {abs_path}: {e}")
                        continue
                    new_hash = _file_hash(content)
                    try:
                        cur_mtime = os.stat(abs_path).st_mtime_ns
                    except OSError:
                        cur_mtime = None

                    # Content unchanged — skip parse, just record new mtime
                    if rel_path in changed_files and new_hash == old_hashes.get(rel_path, ""):
                        if cur_mtime is not None:
                            mtime_only_updates[rel_path] = cur_mtime
                        continue

                    raw_files_subset[rel_path] = content
                    subset_hashes[rel_path] = new_hash
                    if cur_mtime is not None:
                        fast_mtimes[rel_path] = cur_mtime
                    if rel_path in changed_files:
                        actually_changed.append(rel_path)

                # Replace changed_files with only the truly changed ones
                changed_files = actually_changed

                # If only mtimes changed (no content changes, no new, no deleted),
                # update mtimes in DB and return early — no parsing needed.
                if not changed_files and not new_files and not deleted_files:
                    _new_head = _get_git_head(folder_path) or ""
                    _head_advanced = bool(_new_head) and _new_head != (
                        existing_index.git_head if existing_index else ""
                    )
                    if mtime_only_updates or _head_advanced:
                        # Update mtimes directly via incremental_save with empty
                        # deltas; also refresh git_head when it advanced so
                        # FreshnessProbe does not keep flagging unchanged symbols
                        # stale_index after a no-op source-index run (#330).
                        store.incremental_save(
                            owner=owner, name=repo_name,
                            changed_files=[], new_files=[], deleted_files=[],
                            new_symbols=[], raw_files={},
                            file_mtimes=mtime_only_updates,
                            git_head=_new_head if _head_advanced else "",
                        )
                    _fast_mtime_only = {
                        "success": True,
                        "message": "No changes detected",
                        "repo": f"{owner}/{repo_name}",
                        "folder_path": _folder_display,
                        "fast_path": True,
                        "changed": 0, "new": 0, "deleted": 0,
                        "duration_seconds": round(time.monotonic() - t0, 2),
                    }
                    _stamp_incremental_outcome(
                        _fast_mtime_only, _requested_incremental, True
                    )
                    return _fast_mtime_only

                files_to_parse = set(changed_files) | set(new_files)
                # Split pipeline: parse immediately (no AI), fire summarization thread.
                new_symbols, incr_file_summaries, incr_file_languages, incr_file_imports, incremental_no_symbols = (
                    parse_immediate(
                        files_to_parse=files_to_parse,
                        file_contents=raw_files_subset,
                        active_providers=active_providers,
                        warnings=fast_warnings,
                        repo=str(folder_path),
                    )
                )

                git_head = _get_git_head(folder_path) or ""
                incr_context_metadata = collect_metadata(active_providers) if active_providers else None

                # Merge mtime-only updates so they're persisted alongside real changes
                all_mtimes = {**mtime_only_updates, **fast_mtimes}

                # Capture deferred generation BEFORE incremental_save to avoid a race:
                # if mark_reindex_start fires between save and read, the deferred thread
                # would incorrectly think it belongs to the newer generation.
                _repo_full = f"{owner}/{repo_name}"
                from ..reindex_state import _get_state
                _deferred_gen = _get_state(_repo_full).deferred_generation

                if _fast_is_branch_delta:
                    store.save_branch_delta(
                        owner=owner, name=repo_name, branch=_fast_branch,
                        changed_files=changed_files, new_files=new_files,
                        deleted_files=deleted_files,
                        new_symbols=new_symbols,
                        raw_files=raw_files_subset,
                        git_head=git_head,
                        base_head=_fast_base_index.git_head if _fast_base_index else "",
                        file_hashes=subset_hashes,
                        file_mtimes=all_mtimes,
                        file_languages=incr_file_languages,
                        file_summaries=incr_file_summaries,
                        file_imports=incr_file_imports,
                    )
                    updated = store.load_index(owner, repo_name, branch=_fast_branch)
                else:
                    updated = store.incremental_save(
                        owner=owner, name=repo_name,
                        changed_files=changed_files, new_files=new_files, deleted_files=deleted_files,
                        new_symbols=new_symbols,
                        raw_files=raw_files_subset,
                        git_head=git_head,
                        file_summaries=incr_file_summaries,
                        file_languages=incr_file_languages,
                        imports=incr_file_imports,
                        context_metadata=incr_context_metadata,
                        file_hashes=subset_hashes,
                        file_mtimes=all_mtimes,
                        # Refresh the package registry so a manifest add/rename on
                        # the incremental path isn't stale until a full reindex (W7).
                        package_names=_extract_package_names(str(folder_path)),
                    )

                # Fire daemon thread for deferred summarization — index is already saved
                # with empty summaries; this fills them in without blocking the response.
                _summarization_deferred = False
                if new_symbols and use_ai_summaries:
                    _summaries_copy = list(new_symbols)
                    _contents_copy = dict(raw_files_subset)
                    _daemon = threading.Thread(
                        target=lambda _g=_deferred_gen, _s=_summaries_copy, _c=_contents_copy: _run_deferred_summarize(
                            _g, _repo_full, _s, _c, store, owner, repo_name,
                        ),
                        daemon=True,
                        name="deferred-summarizer",
                    )
                    _daemon.start()
                    _summarization_deferred = True
                    logger.info(
                        "Deferred AI summarization started for %s/%s (%d symbols)",
                        owner, repo_name, len(new_symbols),
                    )

                result = {
                    "success": True,
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": _folder_display,
                    "incremental": True,
                    "fast_path": True,
                    "changed": len(changed_files), "new": len(new_files), "deleted": len(deleted_files),
                    "symbol_count": len(updated.symbols) if updated else 0,
                    "indexed_at": updated.indexed_at if updated else "",
                    "duration_seconds": round(time.monotonic() - t0, 2),
                }
                if _fast_is_branch_delta:
                    result["branch"] = _fast_branch
                    result["branch_delta"] = True
                if _summarization_deferred:
                    result["summarization_deferred"] = True
                    result["summarization_note"] = (
                        "AI summarization is running in the background. "
                        "Call summarize_repo to run it synchronously if summaries are missing."
                    )
                if fast_warnings:
                    result["warnings"] = fast_warnings
                _stamp_incremental_outcome(result, _requested_incremental, True)
                _maybe_apply_adaptive(folder_path, result)
                return result

        # ── Standard path: full directory discovery ──
        # Detect framework profile and merge its ignore patterns before discovery
        _framework_profile = detect_framework(folder_path)
        _profile_ignore: list[str] = []
        if _framework_profile:
            _profile_ignore = _framework_profile.ignore_patterns
            logger.info(
                "Framework profile '%s' active — adding %d ignore patterns",
                _framework_profile.name,
                len(_profile_ignore),
            )

        _merged_ignore = list(extra_ignore_patterns or []) + _profile_ignore

        # Discover source files (with security filtering).  When v1.96 has
        # retargeted folder_path to the git root and walk_root is a strict
        # subdir, we walk only the subdir but resolve paths relative to
        # folder_path (= git root) downstream so file_paths are
        # git-root-relative.
        #
        # v1.108: when the caller supplied `paths=[...]`, skip the directory
        # walk entirely and materialise the file list from those explicit
        # entries. Validation matches the walk path (outside-root, traversal,
        # symlink-escape, oversize, unsupported-extension all warn-and-skip).
        requested_rels: Optional[list[str]] = None
        if paths is not None:
            source_files, discover_warnings, skip_counts, requested_rels = resolve_explicit_paths(
                walk_root,
                list(paths),
                max_files=max_files,
                max_size=max_size,
                follow_symlinks=follow_symlinks,
            )
        else:
            source_files, discover_warnings, skip_counts = discover_local_files(
                walk_root,
                max_files=max_files,
                max_size=max_size,
                extra_ignore_patterns=_merged_ignore or None,
                follow_symlinks=follow_symlinks,
            )
        warnings.extend(discover_warnings)
        logger.info("Discovery skip counts: %s", skip_counts)

        # Truncation status for this walk (#366). Persisted on every save path so a
        # silently-capped index is loud in the index result, in resolve_repo, and
        # in query _meta. Always a dict (self-heals when a raised cap clears it).
        _cap_status = _file_cap_report(skip_counts, max_files)

        # Warn when no root .gitignore is present and the file count is large —
        # a common cause of bloated indexes that then overflow get_file_tree.
        # Project-overridable (#301): big monorepos vs small repos want different thresholds.
        gitignore_warn_threshold = _config.get(
            "gitignore_warn_threshold", 500, repo=str(folder_path)
        )
        if (
            gitignore_warn_threshold > 0
            and not (folder_path / ".gitignore").exists()
            and len(source_files) >= gitignore_warn_threshold
        ):
            gitignore_warning = (
                f"No .gitignore found in {folder_path}. "
                f"{len(source_files)} files were indexed — this may include unintended files "
                f"(build artifacts, vendored dependencies, etc.). "
                f"Add a .gitignore and re-run index_folder to exclude them."
            )
            logger.warning(gitignore_warning)
            warnings.append(gitignore_warning)

        if not source_files:
            # A subset refresh (paths=[...]) whose listed files were all deleted
            # on disk legitimately yields zero source files. Let it fall through
            # to the incremental path so those files get pruned, instead of
            # erroring out and leaving stale symbols behind (#333). Every other
            # empty-discovery case is still an error.
            _deletion_only_subset = (
                paths is not None
                and bool(requested_rels)
                and incremental
                and store.has_index(owner, repo_name)
            )
            if not _deletion_only_subset:
                result = {"success": False, "error": "No source files found"}
                if warnings:
                    result["warnings"] = warnings
                return result

        # Discover context providers (dbt, terraform, etc.).
        # Project-overridable (#301): per-repo feature toggle for context providers.
        active_providers = _resolve_active_providers(folder_path, context_providers)
        # Cache for the watcher fast path (audit W1): subsequent watched edits
        # reuse this detection instead of re-running the discovery walk each edit.
        _cache_active_providers(folder_path, active_providers)
        if active_providers:
            names = ", ".join(p.name for p in active_providers)
            logger.info("Active context providers: %s", names)

        # v1.95.0/1.96: collision guard + subdir-merge resolution.
        #
        # The guard only operates when the new identity came from git-root
        # detection (`_git_root` is non-empty) and an existing index at the
        # same identity also recorded a `git_root`.
        #
        # Three cases:
        #
        # 1. Different working trees of the same repo (`_git_root` mismatch):
        #    refuse rather than silently overwriting.  Two clones of
        #    `elastic/kibana` at different paths would otherwise collapse.
        #
        # 2. Same git_root, existing source_root == git_root (v1.96+ format,
        #    file paths git-root-relative): set `_merge_with_existing` so
        #    the save path carries over files outside `walk_prefix` from
        #    the existing index and unions them with the fresh walk.
        #
        # 3. Same git_root, existing source_root != git_root (v1.95-style
        #    where source_root was the user's subdir and file paths were
        #    subdir-relative): not safely mergeable into the new
        #    git-root-relative scheme.  Discard the v1.95 index and rebuild
        #    fresh from the current walk.  Logged as a warning so users
        #    upgrading see what happened.
        _merge_with_existing: Optional["CodeIndex"] = None  # noqa: F821
        _v195_legacy_rebuild = False
        _existing_for_collision = store.load_index(owner, repo_name)
        if (
            _git_root
            and _existing_for_collision is not None
            and getattr(_existing_for_collision, "git_root", "")
        ):
            _existing_git_root = _existing_for_collision.git_root
            _existing_source_root = getattr(_existing_for_collision, "source_root", "") or ""
            if _existing_git_root != _git_root:
                return {
                    "success": False,
                    "error": (
                        f"Index '{owner}/{repo_name}' already exists at "
                        f"'{_existing_git_root}'. Indexing a second working "
                        f"tree at '{_git_root}' would overwrite it. Set "
                        "`git_root_identity: false` in config (or "
                        "JCODEMUNCH_GIT_ROOT_IDENTITY=0) to keep per-path "
                        "indexes, or delete the existing index first."
                    ),
                }
            # Same git_root.  Decide between merge (v1.96 format) and
            # rebuild (v1.95 legacy format).
            if _existing_source_root == _git_root:
                _merge_with_existing = _existing_for_collision
            elif _existing_source_root:
                _v195_legacy_rebuild = True
                # Drop the legacy index so the full-save path below
                # creates a clean v1.96-format replacement.  Without this,
                # `incremental_save` would try to layer the new walk on
                # top of the legacy file set, leaving subdir-relative
                # paths from v1.95 mixed with git-root-relative paths
                # from v1.96.
                try:
                    store.delete_index(owner, repo_name)
                except Exception:
                    logger.debug("legacy v1.95 index delete failed", exc_info=True)
                logger.warning(
                    "Existing index for %s/%s was created by v1.95 with "
                    "subdir-relative paths (source_root=%s); rebuilding "
                    "fresh under v1.96 git-root-relative format.",
                    owner, repo_name, _existing_source_root,
                )
                warnings.append(
                    "Existing v1.95 index detected with subdir-relative file "
                    "paths; rebuilding under the v1.96 git-root-relative "
                    "scheme.  Re-run any prior subdir indexes against the "
                    "same clone so they re-merge into this index."
                )

        # ── Branch-aware indexing ──
        # Detect current git branch. If a base index exists and we're on a
        # different branch, save as a branch delta instead of overwriting the base.
        _current_branch = _get_git_branch(folder_path)
        _is_branch_delta = False
        _base_branch: str = ""

        # Always load the base index (branch="") to check if it exists
        existing_index = store.load_index(owner, repo_name)

        if existing_index is not None and _current_branch:
            # Read stored base_branch from meta — defaults to "" (first indexed branch)
            _base_branch = getattr(existing_index, "branch", "") or ""
            if not _base_branch:
                # First time: the existing index becomes the base; record its branch
                _base_branch = _current_branch  # base IS this branch

            if _current_branch != _base_branch:
                # We're on a non-base branch — use branch delta mode.
                # Load the branch-composed index for incremental comparison.
                _is_branch_delta = True
                existing_index = store.load_index(owner, repo_name, branch=_current_branch)
                logger.info(
                    "Branch-aware indexing: current='%s', base='%s' → delta mode",
                    _current_branch, _base_branch,
                )

        if existing_index is None and store.has_index(owner, repo_name):
            rebuild_reason, _rebuild_message = describe_unloadable_index(
                store, owner, repo_name
            )
            logger.warning(
                "index_folder unloadable_index — %s/%s: %s; full re-index required",
                owner, repo_name, rebuild_reason,
            )
            warnings.append(_rebuild_message)
        elif _needs_parser_upgrade(existing_index) and not (force_reparse and paths is not None):
            # One-off full re-parse after an extraction-semantics bump (#414).
            # Reported through the same fields a caller already reads for an
            # unreadable index, so a substituted rebuild always has a reason.
            #
            # ⚠⚠ Exempted for a forced subset refresh (v1.108.259, #395). A
            # caller passing `paths=` AND `force_reparse=True` is running the
            # upgrade in bounded SLICES on purpose. Escalating to a full
            # reindex here would run the entire unbounded maintenance event
            # inside every slice — measured on an 8-file fixture as four full
            # re-parses where four bounded ones were requested, which on the
            # fleet this was written for is worse than doing nothing.
            #
            # The campaign owns coverage and the generation stamp instead; see
            # tools/refresh.py. Nothing else may skip this escalation, because
            # any other caller has no mechanism to finish the job.
            incremental = False
            rebuild_reason = "parser_generation_upgrade"
            logger.warning(
                "index_folder parser_generation_upgrade — %s/%s: stored=%s current=%s; "
                "re-parsing every file once",
                owner, repo_name,
                getattr(existing_index, "parser_generation", 0), PARSER_GENERATION,
            )
            warnings.append(PARSER_UPGRADE_WARNING)

        # Discovery pass — resolve rel_paths and collect mtimes without
        # reading file contents (P2-5: avoids 200MB-1GB allocation
        # for large projects). Content is read on-demand later.
        file_mtimes: dict[str, int] = {}
        rel_path_map: dict[str, Path] = {}  # rel_path -> absolute Path
        # Files discovery ACCEPTED that this pass still drops. Every `continue`
        # below used to be silent, so the corpus could end up smaller than the
        # walk reported with nothing anywhere recording the difference — the
        # index then answered `fresh` while whole files were missing (#375
        # sub-problem C: "learned 7,659 of 9,634 files, still reports itself up
        # to date"). A drop we cannot name is the one that must be counted.
        post_discovery_drops: dict[str, int] = {}

        def _drop(reason: str) -> None:
            post_discovery_drops[reason] = post_discovery_drops.get(reason, 0) + 1

        for file_path in source_files:
            if not validate_path(folder_path, file_path):
                _drop("outside_root")
                continue
            try:
                rel_path = file_path.relative_to(folder_path).as_posix()
            except ValueError:
                _drop("outside_root")
                continue
            ext = file_path.suffix
            if ext not in LANGUAGE_EXTENSIONS and get_language_for_path(str(file_path)) is None:
                # Discovery's `_should_index_file` applies CONFIG-driven language
                # gating; this applies the LANGUAGE_EXTENSIONS registry. When the
                # two disagree a file passes the walk and dies here, so the
                # divergence has to be visible rather than inferred.
                _drop("no_language")
                continue
            try:
                file_mtimes[rel_path] = os.stat(file_path).st_mtime_ns
            except OSError as e:
                warnings.append(f"Failed to stat {file_path}: {e}")
                _drop("stat_failed")
                continue
            rel_path_map[rel_path] = file_path

        if post_discovery_drops:
            logger.info(
                "Post-discovery drops (accepted by the walk, not indexed): %s",
                post_discovery_drops,
            )

        def _read_file(rel_path: str) -> str | None:
            """Re-read a file by its rel_path. Returns content or None on error."""
            abs_path = rel_path_map[rel_path]
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    return f.read()
            except Exception as e:
                warnings.append(f"Failed to read {abs_path}: {e}")
                return None

        _hash_file_cache: dict[str, str] = {}  # rel_path -> content

        def _hash_file(rel_path: str) -> str:
            """Read and hash a single file on demand; cache content for parse step."""
            abs_path = rel_path_map[rel_path]
            with open(abs_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                content = f.read()
            _hash_file_cache[rel_path] = content
            return _file_hash(content)

        # Force full reindex if invalidate_cache was called for this repo.
        # Handles cases where the DB deletion failed (e.g. Windows WAL
        # file-locking) and load_index still returns the old index.
        _repo_full = f"{owner}/{repo_name}"
        try:
            from .invalidate_cache import _force_full_reindex
            if _repo_full in _force_full_reindex:
                _force_full_reindex.discard(_repo_full)
                incremental = False
                logger.info(
                    "index_folder: forcing full reindex for %s (post-invalidation)",
                    _repo_full,
                )
        except ImportError:
            pass

        # Incremental path: detect changes using mtime fast-path.  Disabled
        # when v1.96 subdir-merge mode is active (`_merge_with_existing`)
        # because the new walk only covers `walk_prefix` while the existing
        # index covers other subdirs — incremental's "changed/new/deleted"
        # accounting against the full existing file set would mis-attribute
        # carryover files as deleted.
        if incremental and existing_index is not None and _merge_with_existing is None:
            changed, new, deleted, computed_hashes, updated_mtimes = (
                store.detect_changes_with_mtimes(
                    owner, repo_name, file_mtimes, _hash_file
                )
            )

            # Subset refresh (paths=[...]): detect_changes_with_mtimes diffs the
            # supplied subset against the ENTIRE stored index, so every unlisted
            # indexed file lands in `deleted`. Rescope `deleted` to only the files
            # the caller actually listed — a subset refresh must never prune
            # unlisted files, while still removing a listed file that was deleted
            # on disk. Listing the root ('.' / '') preserves full-corpus diff
            # semantics. Applies to both the normal and branch-delta saves below
            # (they share `deleted`). (#333)
            if requested_rels is not None:
                old_files = (
                    set((existing_index.file_hashes or {}).keys())
                    if existing_index is not None else set()
                )
                if any(req in ("", ".") for req in requested_rels):
                    covered = old_files
                else:
                    covered = {
                        fp for fp in old_files
                        if any(fp == req or fp.startswith(req + "/") for req in requested_rels)
                    }
                deleted = sorted(covered - set(file_mtimes))

                # v1.108.259 (#395): re-parse the listed files even when their
                # content is unchanged. `detect_changes_with_mtimes` compares
                # hashes, so a subset refresh over untouched files is otherwise
                # a correct no-op — right for an edit, wrong for a parser
                # generation upgrade, where the bytes are identical and the
                # stored symbols are what is wrong.
                #
                # ⚠ Scoped to `requested_rels` deliberately. Forcing without an
                # explicit list would mean re-parsing the whole corpus in one
                # call, i.e. exactly the unbounded maintenance event #395 exists
                # to avoid.
                if force_reparse:
                    _already = set(changed) | set(new) | set(deleted)
                    _known = set((existing_index.file_hashes or {}).keys())
                    _forced = [
                        fp for fp in sorted(file_mtimes)
                        if fp not in _already and fp in _known
                    ]
                    if _forced:
                        changed = sorted(set(changed) | set(_forced))
                        logger.info(
                            "index_folder: force_reparse promoted %d unchanged file(s)",
                            len(_forced),
                        )

            if not changed and not new and not deleted:
                _refresh_git_head_if_advanced(
                    store, owner, repo_name, folder_path,
                    existing_index.git_head if existing_index else None,
                )
                _no_change_result = {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": _folder_display,
                    "changed": 0, "new": 0, "deleted": 0,
                    "duration_seconds": round(time.monotonic() - t0, 2),
                }
                _stamp_incremental_outcome(
                    _no_change_result, _requested_incremental, True
                )
                # This ran a full discovery walk, so a still-truncated index
                # stays loud even when nothing changed (#366).
                _attach_cap_report(_no_change_result, _cap_status)
                _attach_provider_skips(_no_change_result, folder_path)
                return _no_change_result

            # Read changed + new files into memory
            files_to_parse = set(changed) | set(new)
            raw_files_subset: dict[str, str] = {}
            subset_hashes: dict[str, str] = {}
            _incr_total = len(files_to_parse)
            for _incr_idx, rel_path in enumerate(sorted(files_to_parse)):
                if progress_cb:
                    progress_cb(_incr_idx, _incr_total, rel_path)
                # Use content cached by _hash_file if available (avoids second read)
                content = _hash_file_cache.pop(rel_path, None) or _read_file(rel_path)
                if content is None:
                    continue
                raw_files_subset[rel_path] = content
                subset_hashes[rel_path] = computed_hashes.get(rel_path, _file_hash(content))
            if progress_cb and _incr_total > 0:
                progress_cb(_incr_total, _incr_total, "Parsing complete")

            # Shared pipeline: parse, enrich, summarize, extract metadata
            new_symbols, incr_file_summaries, incr_file_languages, incr_file_imports, incremental_no_symbols = (
                parse_and_prepare_incremental(
                    files_to_parse=files_to_parse,
                    file_contents=raw_files_subset,
                    active_providers=active_providers,
                    use_ai_summaries=use_ai_summaries,
                    warnings=warnings,
                    repo=str(folder_path),
                )
            )

            git_head = _get_git_head(folder_path) or ""
            incr_context_metadata = collect_metadata(active_providers) if active_providers else None

            # ── Optional LSP enrichment (incremental path) ──
            try:
                from ..enrichment.lsp_bridge import is_lsp_enabled, enrich_call_graph_with_lsp, enrich_dispatch_edges
                if is_lsp_enabled(repo=str(folder_path)):
                    lsp_edges = enrich_call_graph_with_lsp(
                        root_path=str(folder_path),
                        symbols=new_symbols,
                        file_contents=raw_files_subset,
                        file_languages=incr_file_languages,
                        repo=str(folder_path),
                    )
                    if lsp_edges:
                        if incr_context_metadata is None:
                            incr_context_metadata = {}
                        incr_context_metadata["lsp_edges"] = lsp_edges
                        logger.info("LSP enrichment added %d edges (incremental)", len(lsp_edges))

                    dispatch_edges = enrich_dispatch_edges(
                        root_path=str(folder_path),
                        symbols=new_symbols,
                        file_contents=raw_files_subset,
                        file_languages=incr_file_languages,
                        repo=str(folder_path),
                    )
                    if dispatch_edges:
                        if incr_context_metadata is None:
                            incr_context_metadata = {}
                        incr_context_metadata["dispatch_edges"] = dispatch_edges
                        logger.info("LSP dispatch enrichment added %d edges (incremental)", len(dispatch_edges))
            except Exception:
                logger.debug("LSP enrichment skipped (incremental)", exc_info=True)

            if _is_branch_delta:
                # Save as branch delta instead of overwriting the base index
                base_index = store.load_index(owner, repo_name)  # base (no branch)
                store.save_branch_delta(
                    owner=owner, name=repo_name, branch=_current_branch,
                    changed_files=changed, new_files=new, deleted_files=deleted,
                    new_symbols=new_symbols,
                    raw_files=raw_files_subset,
                    git_head=git_head,
                    base_head=base_index.git_head if base_index else "",
                    file_hashes=subset_hashes,
                    file_mtimes=updated_mtimes,
                    file_languages=incr_file_languages,
                    file_summaries=incr_file_summaries,
                    file_imports=incr_file_imports,
                )
                # Load composed index for reporting
                updated = store.load_index(owner, repo_name, branch=_current_branch)
            else:
                updated = store.incremental_save(
                    owner=owner, name=repo_name,
                    changed_files=changed, new_files=new, deleted_files=deleted,
                    new_symbols=new_symbols,
                    raw_files=raw_files_subset,
                    git_head=git_head,
                    file_summaries=incr_file_summaries,
                    file_languages=incr_file_languages,
                    imports=incr_file_imports,
                    context_metadata=incr_context_metadata,
                    file_hashes=subset_hashes,
                    file_mtimes=updated_mtimes,
                    # Refresh the package registry so a manifest add/rename on
                    # the incremental path isn't stale until a full reindex (W7).
                    package_names=_extract_package_names(str(folder_path)),
                    # This path did a full discovery walk, so refresh the cap
                    # status (self-heals when a raised cap now clears it) (#366).
                    file_cap_status=_cap_status,
                )

            # This path did a full discovery walk (paths=None), so the skip
            # counts describe the whole corpus — refresh the coverage contract.
            if paths is None:
                _record_coverage(
                    store, owner, repo_name,
                    skip_counts,
                    # The COMPOSED file set (carried + changed + new - deleted),
                    # not the walk's accepted list: on this path they are only
                    # equal if nothing was dropped, which is the thing being
                    # measured.
                    len(updated_mtimes),
                    len(incremental_no_symbols),
                    files_accepted=len(source_files),
                    post_discovery_drops=post_discovery_drops,
                )

            result = {
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "folder_path": _folder_display,
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "symbol_count": len(updated.symbols) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "duration_seconds": round(time.monotonic() - t0, 2),
                "discovery_skip_counts": skip_counts,
                "no_symbols_count": len(incremental_no_symbols),
                "no_symbols_files": incremental_no_symbols[:50],
            }
            if _is_branch_delta:
                result["branch"] = _current_branch
                result["branch_delta"] = True
            if warnings:
                result["warnings"] = warnings
            _stamp_incremental_outcome(result, _requested_incremental, True)
            _attach_cap_report(result, _cap_status)
            _attach_provider_skips(result, folder_path)
            _maybe_apply_adaptive(folder_path, result)
            return result

        # Full index path — stream through files one at a time to avoid
        # loading all contents into memory simultaneously.
        # Compute hashes and collect mtimes during the per-file loop.
        file_hashes: dict[str, str] = {}
        all_symbols = []
        symbols_by_file: dict[str, list] = defaultdict(list)
        source_file_list = sorted(file_mtimes)
        file_imports: dict[str, list[dict]] = {}
        content_dir = store._content_dir(owner, repo_name)
        content_dir.mkdir(parents=True, exist_ok=True)

        no_symbols_files: list[str] = []
        _languages_with_symbols: set[str] = set()
        _total_files = len(source_file_list)
        for _file_idx, rel_path in enumerate(source_file_list):
            if progress_cb:
                progress_cb(_file_idx, _total_files, rel_path)
            content = _read_file(rel_path)
            if content is None:
                continue

            # Encode once — reused for both hashing and tree-sitter parsing
            content_bytes = content.encode("utf-8")
            file_hashes[rel_path] = _file_hash_bytes(content_bytes)

            # Write raw content to cache immediately, then process
            file_dest = store._safe_content_path(content_dir, rel_path)
            if file_dest:
                file_dest.parent.mkdir(parents=True, exist_ok=True)
                store._write_cached_text(file_dest, content)

            language = get_language_for_path(rel_path)
            if not language:
                no_symbols_files.append(rel_path)
                # content eligible for GC after this iteration
                continue
            try:
                symbols = parse_file(content, rel_path, language, source_bytes=content_bytes, repo=str(folder_path))
                if symbols:
                    all_symbols.extend(symbols)
                    symbols_by_file[rel_path].extend(symbols)
                    _languages_with_symbols.add(language)
                else:
                    no_symbols_files.append(rel_path)
                    logger.debug("NO SYMBOLS: %s", rel_path)
            except Exception as e:
                warnings.append(f"Failed to parse {rel_path}: {e}")
                logger.debug("PARSE ERROR: %s — %s", rel_path, e)

            # Extract imports while content is in scope
            imps = extract_imports(content, rel_path, language)
            if imps:
                file_imports[rel_path] = imps
            # content is discarded at end of iteration

        if progress_cb:
            progress_cb(_total_files, _total_files, "Parsing complete")

        logger.info(
            "Parsing complete — with symbols: %d, no symbols: %d",
            len(symbols_by_file),
            len(no_symbols_files),
        )

        # Enrich with context providers before summarization
        if active_providers and all_symbols:
            enrich_symbols(all_symbols, active_providers)

        # Merge extra imports from context providers (Blade refs, facades, etc.)
        if active_providers:
            collect_extra_imports(active_providers, file_imports)

        # Generate summaries — preserve existing summaries for unchanged files
        if all_symbols:
            _folder_existing_summaries: dict[tuple[str, str, str], str] | None = None
            _folder_unchanged_files: set[str] | None = None
            if (
                existing_index is not None
                and existing_index.file_hashes
                and existing_index.symbols
            ):
                _folder_unchanged_files = {
                    f for f, h in file_hashes.items()
                    if existing_index.file_hashes.get(f) == h
                }
                if _folder_unchanged_files:
                    _folder_existing_summaries = {
                        (s["file"], s["name"], s["kind"]): s["summary"]
                        for s in existing_index.symbols
                        if s.get("summary") and s.get("file") in _folder_unchanged_files
                    }
                    logger.info(
                        "index_folder full — %d/%d files unchanged, %d summaries preserved",
                        len(_folder_unchanged_files), len(file_hashes),
                        len(_folder_existing_summaries) if _folder_existing_summaries else 0,
                    )

            if _folder_existing_summaries and _folder_unchanged_files:
                from ._indexing_pipeline import _split_for_summarization
                _needs_summary, _already_summarized = _split_for_summarization(
                    all_symbols, _folder_existing_summaries, _folder_unchanged_files
                )
                _summarized = summarize_symbols(_needs_summary, use_ai=use_ai_summaries, repo=str(folder_path)) if _needs_summary else []
                all_symbols = _summarized + _already_summarized
            else:
                all_symbols = summarize_symbols(all_symbols, use_ai=use_ai_summaries, repo=str(folder_path))

        # Generate file-level summaries (single-pass grouping) using shared helpers
        file_symbols_map = defaultdict(list)
        for s in all_symbols:
            file_symbols_map[s.file].append(s)
        file_languages = _file_languages_for_paths(source_file_list, file_symbols_map)
        languages = _language_counts(file_languages)
        file_summaries = _complete_file_summaries(source_file_list, file_symbols_map, context_providers=active_providers)

        # Collect structured metadata from providers
        full_context_metadata = collect_metadata(active_providers) if active_providers else None

        # Merge framework profile metadata into context_metadata
        if _framework_profile:
            profile_meta = profile_to_meta(_framework_profile)
            if full_context_metadata:
                full_context_metadata.update(profile_meta)
            else:
                full_context_metadata = profile_meta

        # Extract package names from manifest files
        _pkg_names: list[str] = []
        try:
            _pkg_names = _extract_package_names(str(folder_path))
        except Exception:
            logger.debug("extract_package_names failed for %s", folder_path, exc_info=True)

        # ── Optional LSP enrichment ──
        # When enabled, resolve unqualified call sites via language servers.
        # Results are stored in context_metadata["lsp_edges"] for the call graph.
        try:
            from ..enrichment.lsp_bridge import is_lsp_enabled, enrich_call_graph_with_lsp, enrich_dispatch_edges
            if is_lsp_enabled(repo=str(folder_path)):
                lsp_edges = enrich_call_graph_with_lsp(
                    root_path=str(folder_path),
                    symbols=all_symbols,
                    file_contents={},  # full path: LSP bridge reads from disk
                    file_languages=file_languages,
                    repo=str(folder_path),
                )
                if lsp_edges:
                    if full_context_metadata is None:
                        full_context_metadata = {}
                    full_context_metadata["lsp_edges"] = lsp_edges
                    logger.info("LSP enrichment added %d edges", len(lsp_edges))

                dispatch_edges = enrich_dispatch_edges(
                    root_path=str(folder_path),
                    symbols=all_symbols,
                    file_contents={},
                    file_languages=file_languages,
                    repo=str(folder_path),
                )
                if dispatch_edges:
                    if full_context_metadata is None:
                        full_context_metadata = {}
                    full_context_metadata["dispatch_edges"] = dispatch_edges
                    logger.info("LSP dispatch enrichment added %d edges", len(dispatch_edges))
        except Exception:
            logger.debug("LSP enrichment skipped", exc_info=True)

        # Save index — raw files already written to content dir above,
        # pass empty dict to skip duplicate writes.
        git_head = _get_git_head(folder_path) or ""

        if _is_branch_delta:
            # Full index on a non-base branch — diff against base and save as delta.
            base_index = store.load_index(owner, repo_name)  # base (no branch)
            if base_index is not None:
                base_files = set(base_index.source_files)
                current_files_set = set(source_file_list)

                delta_new = sorted(current_files_set - base_files)
                delta_deleted = sorted(base_files - current_files_set)
                delta_changed = sorted(
                    f for f in (current_files_set & base_files)
                    if file_hashes.get(f, "") != base_index.file_hashes.get(f, "")
                )

                # Gather symbols for changed/new files
                delta_files = set(delta_changed) | set(delta_new)
                delta_symbols = [s for s in all_symbols if s.file in delta_files]

                store.save_branch_delta(
                    owner=owner, name=repo_name, branch=_current_branch,
                    changed_files=delta_changed, new_files=delta_new,
                    deleted_files=delta_deleted,
                    new_symbols=delta_symbols,
                    raw_files={},  # already written to content dir
                    git_head=git_head,
                    base_head=base_index.git_head,
                    file_hashes={f: file_hashes[f] for f in delta_files if f in file_hashes},
                    file_mtimes={f: file_mtimes[f] for f in delta_files if f in file_mtimes},
                    file_languages={f: file_languages[f] for f in delta_files if f in file_languages},
                    file_summaries={f: file_summaries[f] for f in delta_files if f in file_summaries},
                    file_imports={f: file_imports[f] for f in delta_files if f in file_imports},
                )
                index = store.load_index(owner, repo_name, branch=_current_branch)
                if index is None:
                    index = base_index  # fallback
            else:
                # No base index — save as full (becomes the base)
                index = store.save_index(
                    owner=owner, name=repo_name,
                    source_files=source_file_list, symbols=all_symbols,
                    raw_files={}, languages=languages, file_hashes=file_hashes,
                    file_summaries=file_summaries, git_head=git_head,
                    source_root=str(folder_path), file_languages=file_languages,
                    display_name=folder_path.name, imports=file_imports,
                    context_metadata=full_context_metadata, file_mtimes=file_mtimes,
                    package_names=_pkg_names, git_root=_git_root,
                    file_cap_status=_cap_status,
                )
        else:
            # v1.96: when an existing v1.96-format index covers the same
            # git_root, carry over files outside `walk_prefix` and union
            # them with the freshly walked subdir.  The collision-guard
            # block above sets `_merge_with_existing` only in this case.
            _save_source_files = source_file_list
            _save_symbols = all_symbols
            _save_file_hashes = file_hashes
            _save_file_summaries = file_summaries
            _save_file_languages = file_languages
            _save_file_mtimes = file_mtimes
            _save_imports = file_imports
            _save_languages = languages
            _save_context_metadata = full_context_metadata
            _save_pkg_names = _pkg_names
            _save_source_roots = [walk_prefix] if walk_prefix else [""]

            if _merge_with_existing is not None:
                merged = _merge_subdir_into_existing(
                    existing=_merge_with_existing,
                    walk_prefix=walk_prefix,
                    new_source_files=source_file_list,
                    new_symbols=all_symbols,
                    new_file_hashes=file_hashes,
                    new_file_summaries=file_summaries,
                    new_file_languages=file_languages,
                    new_file_mtimes=file_mtimes,
                    new_file_imports=file_imports,
                    new_context_metadata=full_context_metadata or {},
                    new_pkg_names=_pkg_names or [],
                )
                _save_source_files = merged["source_files"]
                # Carried symbols (dicts) + freshly parsed Symbols.
                # save_index serializes Symbols itself; we pre-serialize
                # the carryover dicts by leaving them as dicts (save_index
                # path tolerates pre-serialized via _symbol_to_dict no-op).
                _save_symbols = merged["symbols"] + list(all_symbols)
                _save_file_hashes = merged["file_hashes"]
                _save_file_summaries = merged["file_summaries"]
                _save_file_languages = merged["file_languages"]
                _save_file_mtimes = merged["file_mtimes"]
                _save_imports = merged["imports"]
                _save_languages = merged["languages"]
                _save_context_metadata = merged["context_metadata"]
                _save_pkg_names = merged["package_names"]
                _save_source_roots = merged["source_roots"]
                logger.info(
                    "v1.96 subdir merge: %d carried + %d new = %d files "
                    "(%d source_roots: %s)",
                    len(_save_source_files) - len(source_file_list),
                    len(source_file_list),
                    len(_save_source_files),
                    len(_save_source_roots),
                    _save_source_roots,
                )

            index = store.save_index(
                owner=owner,
                name=repo_name,
                source_files=_save_source_files,
                symbols=_save_symbols,
                raw_files={},
                languages=_save_languages,
                file_hashes=_save_file_hashes,
                file_summaries=_save_file_summaries,
                git_head=git_head,
                source_root=str(folder_path),
                file_languages=_save_file_languages,
                display_name=folder_path.name,
                imports=_save_imports,
                context_metadata=_save_context_metadata,
                file_mtimes=_save_file_mtimes,
                package_names=_save_pkg_names,
                git_root=_git_root,
                source_roots=_save_source_roots,
                file_cap_status=_cap_status,
            )

        # Full-save paths above all followed a full discovery walk (paths=None);
        # record the coverage contract the verdicts disclose at query time.
        if paths is None:
            _record_coverage(
                store, owner, repo_name,
                skip_counts, len(source_file_list), len(no_symbols_files),
                files_accepted=len(source_files),
                post_discovery_drops=post_discovery_drops,
            )

        # Identify languages that were indexed (symbols found) but have no import extractor
        _missing_import_extractors = sorted(
            lang for lang in _languages_with_symbols
            if lang not in _IMPORT_EXTRACTORS
        )

        result = {
            "success": True,
            "repo": index.repo,
            "folder_path": _folder_display,
            "indexed_at": index.indexed_at,
            "file_count": len(source_file_list),
            "symbol_count": len(all_symbols),
            "file_summary_count": sum(1 for v in file_summaries.values() if v),
            "languages": languages,
            "files": source_file_list[:20],  # Limit files in response
            "duration_seconds": round(time.monotonic() - t0, 2),
            "discovery_skip_counts": skip_counts,
            "no_symbols_count": len(no_symbols_files),
            "no_symbols_files": no_symbols_files[:50],  # Show up to 50 for inspection
        }
        if _is_branch_delta:
            result["branch"] = _current_branch
            result["branch_delta"] = True
        if _missing_import_extractors:
            result["missing_extractors"] = _missing_import_extractors
            result.setdefault("parse_warnings", []).append(
                f"Import graph incomplete for: {', '.join(_missing_import_extractors)}. "
                "Dead code and dependency analysis may be less accurate for these languages."
            )

        # Report context enrichment stats from all active providers
        if active_providers:
            enrichment = {}
            for provider in active_providers:
                enrichment[provider.name] = provider.stats()
            result["context_enrichment"] = enrichment

        if _framework_profile:
            result["framework_profile"] = _framework_profile.name

        if warnings:
            result["warnings"] = warnings

        # This path rebuilt the whole corpus. When the caller asked for an
        # incremental, that substitution is now a field, not a sentence (#413).
        _stamp_incremental_outcome(
            result, _requested_incremental, False, rebuild_reason
        )
        _attach_cap_report(result, _cap_status)
        _attach_provider_skips(result, folder_path)

        _maybe_apply_adaptive(folder_path, result)
        return result

    except Exception as e:
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
