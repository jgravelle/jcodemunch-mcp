"""Git diff → affected symbols: identify which symbols changed between two commits."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..storage import IndexStore
from ..parser import parse_file, get_language_for_path
from ..parser.symbols import compute_content_hash
from ._utils import resolve_repo
from .get_blast_radius import _build_reverse_adjacency, _bfs_importers

logger = logging.getLogger(__name__)


def _run_git(args: list[str], cwd: str, timeout: int = 10) -> tuple[int, str, str]:
    """Run a git command; return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd, capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return -2, "", "git command timed out"
    except Exception as exc:  # pragma: no cover
        logger.debug("git subprocess error: %s", exc, exc_info=True)
        return -3, "", str(exc)


def _resolve_sha(sha: str, cwd: str) -> Optional[str]:
    """Expand a SHA/ref to full 40-char hex. Returns None on failure."""
    rc, out, _ = _run_git(["rev-parse", "--verify", sha], cwd=cwd)
    return out if rc == 0 and len(out) >= 7 else None


def _get_file_content_at(sha: str, file_path: str, cwd: str) -> Optional[str]:
    """Return file content at a given git SHA, or None (binary / not present)."""
    rc, out, _ = _run_git(["show", f"{sha}:{file_path}"], cwd=cwd, timeout=15)
    if rc != 0:
        return None
    return out


def _parse_symbols_from_content(content: str, rel_path: str, repo: str | None = None) -> dict[str, dict]:
    """Parse content → dict keyed by symbol qualified_name#kind → symbol dict."""
    language = get_language_for_path(rel_path)
    if not language:
        return {}
    try:
        symbols = parse_file(content, rel_path, language, repo=repo)
    except Exception:
        logger.debug("parse_file failed for %s", rel_path, exc_info=True)
        return {}
    # Build a lines array for extracting symbol bodies when byte offsets are absent
    lines = content.splitlines(keepends=True)
    result: dict[str, dict] = {}
    for sym in symbols:
        key = f"{sym.qualified_name}#{sym.kind}"
        # Prefer the content_hash set by parse_file (computed from actual source bytes).
        # Fall back to hashing the extracted line range when content_hash is empty.
        ch = sym.content_hash
        if not ch:
            if sym.line and sym.end_line:
                body = "".join(lines[sym.line - 1:sym.end_line])
            else:
                body = sym.signature
            ch = compute_content_hash(body.encode("utf-8"))
        result[key] = {
            "symbol_id": sym.id,
            "name": sym.name,
            "qualified_name": sym.qualified_name,
            "kind": sym.kind,
            "file": rel_path,
            "line": sym.line,
            "content_hash": ch,
        }
    return result


def get_changed_symbols(
    repo: str,
    since_sha: Optional[str] = None,
    until_sha: str = "HEAD",
    include_blast_radius: bool = False,
    max_blast_depth: int = 3,
    suppress_meta: bool = False,
    storage_path: Optional[str] = None,
    cross_repo: Optional[bool] = None,
) -> dict:
    """Return symbols that changed between two git commits for a locally indexed repo.

    Uses ``git diff --name-only`` to find changed files, re-parses both versions
    of each file, and diffs the symbol sets. Optionally includes downstream blast
    radius for each changed symbol.

    Args:
        repo: Repository identifier (must be locally indexed with index_folder).
        since_sha: Compare from this SHA. Defaults to the SHA stored at index time.
        until_sha: Compare to this SHA (default "HEAD").
        include_blast_radius: Also return downstream importers for each changed symbol.
        max_blast_depth: Hop limit for blast radius traversal (capped at 5).
        suppress_meta: Strip the _meta envelope from the response.
        storage_path: Custom storage path.

    Returns:
        Dict with from_sha, to_sha, changed_files, changed_symbols, added_symbols,
        removed_symbols, and optionally blast_radius per symbol.
    """
    start = time.perf_counter()
    max_blast_depth = max(1, min(max_blast_depth, 5))

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    if not index.source_root:
        return {
            "error": "get_changed_symbols requires a locally indexed repo (index_folder). "
                     "GitHub-indexed repos (index_repo) do not have a local git working tree.",
            "is_local": False,
        }

    cwd = index.source_root

    # Verify git is available and this is a git repo
    rc, _, err = _run_git(["rev-parse", "--git-dir"], cwd=cwd)
    if rc != 0:
        if rc == -1:
            return {"error": "git not found on PATH. Install git and ensure it is in PATH."}
        return {"error": f"Not a git repository or git unavailable: {err}"}

    # Resolve since_sha — default to the SHA stored at index time
    if since_sha is None:
        if not index.git_head:
            return {
                "error": "No SHA stored at index time. Re-run index_folder, or provide since_sha explicitly.",
                "is_local": True,
                "source_root": cwd,
            }
        since_sha = index.git_head

    resolved_since = _resolve_sha(since_sha, cwd)
    if not resolved_since:
        return {"error": f"since_sha not found in git history: {since_sha!r}"}

    resolved_until = _resolve_sha(until_sha, cwd)
    if not resolved_until:
        return {"error": f"until_sha not found: {until_sha!r}"}

    # Count commits spanned
    rc2, count_out, _ = _run_git(
        ["rev-list", "--count", f"{resolved_since}..{resolved_until}"], cwd=cwd
    )
    commits_spanned = int(count_out) if rc2 == 0 and count_out.isdigit() else None

    # Get changed files (name-only diff, exclude binary files)
    rc3, diff_out, diff_err = _run_git(
        ["diff", "--name-only", "--diff-filter=ACDMRT", resolved_since, resolved_until],
        cwd=cwd,
    )
    if rc3 != 0:
        return {"error": f"git diff failed: {diff_err}"}

    all_diff_files = [f for f in diff_out.splitlines() if f.strip()] if diff_out else []

    # Exclude any files that live inside the index storage directory when it
    # happens to be under the repo root (e.g. .index/ as a test-time storage dir).
    storage_exclude_prefix: str = ""
    if storage_path:
        try:
            rel = Path(storage_path).relative_to(cwd)
            storage_exclude_prefix = rel.as_posix() + "/"
        except ValueError:
            pass  # storage_path is outside the repo — no exclusion needed

    changed_files = [
        f for f in all_diff_files
        if not (storage_exclude_prefix and f.startswith(storage_exclude_prefix))
    ]

    # For blast radius: build reverse adjacency from current index
    rev_adj = None
    if include_blast_radius and index.imports is not None:
        source_files = frozenset(index.source_files)
        rev_adj = _build_reverse_adjacency(index.imports, source_files, index.alias_map)

    # For each changed file, parse both versions and diff symbol sets
    added_symbols: list[dict] = []
    removed_symbols: list[dict] = []
    changed_symbols: list[dict] = []

    for file_path in changed_files:
        language = get_language_for_path(file_path)
        if not language:
            continue  # binary, config, etc. — skip silently

        before_content = _get_file_content_at(resolved_since, file_path, cwd)
        after_content = _get_file_content_at(resolved_until, file_path, cwd)

        before_syms: dict[str, dict] = {}
        after_syms: dict[str, dict] = {}

        if before_content is not None:
            before_syms = _parse_symbols_from_content(before_content, file_path, repo=cwd)
        if after_content is not None:
            after_syms = _parse_symbols_from_content(after_content, file_path, repo=cwd)

        before_keys = set(before_syms)
        after_keys = set(after_syms)

        for key in after_keys - before_keys:
            entry = dict(after_syms[key])
            entry["change_type"] = "added"
            if include_blast_radius and rev_adj is not None:
                flat, _ = _bfs_importers(file_path, rev_adj, max_blast_depth)
                entry["blast_radius"] = flat
            added_symbols.append(entry)

        for key in before_keys - after_keys:
            entry = dict(before_syms[key])
            entry["change_type"] = "removed"
            if include_blast_radius and rev_adj is not None:
                flat, _ = _bfs_importers(file_path, rev_adj, max_blast_depth)
                entry["blast_radius"] = flat
            removed_symbols.append(entry)

        for key in before_keys & after_keys:
            b = before_syms[key]
            a = after_syms[key]
            # Detect rename: same body hash but different name
            if b["name"] != a["name"] and b["content_hash"] == a["content_hash"] and b["content_hash"]:
                entry = dict(a)
                entry["change_type"] = "renamed"
                entry["previous_name"] = b["name"]
                if include_blast_radius and rev_adj is not None:
                    flat, _ = _bfs_importers(file_path, rev_adj, max_blast_depth)
                    entry["blast_radius"] = flat
                changed_symbols.append(entry)
            elif b["content_hash"] != a["content_hash"] and (b["content_hash"] or a["content_hash"]):
                entry = dict(a)
                entry["change_type"] = "modified"
                if include_blast_radius and rev_adj is not None:
                    flat, _ = _bfs_importers(file_path, rev_adj, max_blast_depth)
                    entry["blast_radius"] = flat
                changed_symbols.append(entry)

    def _sort_key(e: dict) -> tuple:
        return (e.get("file", ""), e.get("name", ""))

    added_symbols.sort(key=_sort_key)
    removed_symbols.sort(key=_sort_key)
    changed_symbols.sort(key=_sort_key)

    elapsed = (time.perf_counter() - start) * 1000
    result: dict = {
        "from_sha": resolved_since[:12],
        "to_sha": resolved_until[:12],
        "commits_spanned": commits_spanned,
        "is_local": True,
        "changed_files": changed_files,
        "changed_files_count": len(changed_files),
        "added_symbols": added_symbols,
        "removed_symbols": removed_symbols,
        "changed_symbols": changed_symbols,
        "added_count": len(added_symbols),
        "removed_count": len(removed_symbols),
        "changed_count": len(changed_symbols),
    }

    if not suppress_meta:
        result["_meta"] = {
            "timing_ms": round(elapsed, 1),
            "tip": (
                "changed_symbols = body modified; added_symbols = new; "
                "removed_symbols = deleted; renamed = same body, different name. "
                "Set include_blast_radius=true to see downstream impact."
            ),
        }

    return result
