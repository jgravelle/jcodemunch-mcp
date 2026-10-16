"""Shared plumbing for the Claude Code hook handlers.

Helpers used by more than one hook family live here; the package
``__init__`` owns the family map.
"""

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)


# Extensions that benefit from jCodemunch structural navigation.
# Kept intentionally broad — mirrors languages.py LANGUAGE_REGISTRY;
# deliberate POLICY, not a drifting copy (differs from the registry
# on both sides). Shared by the steering and reindex families.
_CODE_EXTENSIONS: set[str] = {
    ".py", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".mts", ".cts",
    ".go",
    ".rs",
    ".java",
    ".php",
    ".rb",
    ".cs", ".cshtml", ".razor",
    ".cpp", ".c", ".h", ".hpp", ".cc", ".cxx", ".ino", ".pde",
    ".vhd", ".vhdl", ".vho", ".vhs",
    ".v", ".vh", ".sv", ".svh",
    ".swift",
    ".kt", ".kts",
    ".scala",
    ".dart",
    ".lua", ".luau",
    ".ex", ".exs",
    ".erl", ".hrl",
    ".vue", ".astro", ".svelte",
    ".sql",
    ".gd",       # GDScript
    ".al",       # AL (Business Central)
    ".gleam",
    ".nix",
    ".hcl", ".tf",
    ".proto",
    ".graphql", ".gql",
    ".verse",
    ".jl",       # Julia
    ".r", ".R",
    ".hs",       # Haskell
    ".f90", ".f95", ".f03", ".f08",  # Fortran
    ".groovy",
    ".pl", ".pm",  # Perl
    ".bash", ".sh", ".zsh",
}


def _note_transcript_root(data) -> None:
    """Record the profile this session's transcripts live under (jcm#421).

    Every hook payload carries ``transcript_path``, and the projects root is its
    grandparent, so the hooks are how ``receipt`` learns about profiles started
    with a custom ``CLAUDE_CONFIG_DIR``. Silent by construction: it writes one
    small file under the index store and touches neither stdout (which Claude
    Code parses as the hook's reply) nor the hook's exit code.
    """
    try:
        if not isinstance(data, dict):
            return
        from ...storage.transcript_roots import register_from_transcript_path
        register_from_transcript_path(data.get("transcript_path"))
    except Exception:
        logger.debug("transcript-root registration failed", exc_info=True)


def _read_hook_payload() -> "dict | None":
    """Parse the hook's stdin JSON; None for unparseable or non-dict payloads.

    A hook must never crash on hostile input; callers treat None as allow.
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _emit_additional_context(event_name: str, text: str) -> int:
    """Emit model-facing additionalContext for an exit-0 hook.

    The one rule the hooks turn on: a hook that exits 0 reaches the model
    ONLY via this channel. Both stderr and top-level ``systemMessage``
    surface to the user instead (on events that honor them — PreCompact
    discards ``systemMessage`` outright), so steering text written to either
    is silently inert. Exit 2 does feed stderr to the model, but it also
    blocks the call, which is not what an advisory nudge wants.

    Not available on every event — PreCompact and TaskCompleted have no such
    channel.

    Past 10,000 characters the text is NOT truncated: Claude Code writes it to a
    file and hands the model a path plus a short preview. Nothing is lost, but the
    model pays a re-read to see it, so keep emissions well under that. Measured on
    this repo's index: the SubagentStart briefing is ~866 characters, and snapshot
    plus landmarks ~91.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }))
    return 0


def _norm_path(path: str) -> str:
    """Normalise a path for comparison against indexed source roots.

    ``realpath`` is load-bearing: ``index_folder`` records ``source_root`` via
    ``Path.resolve()`` (symlinks resolved), so an ``abspath``-only comparison
    never matches a session addressed through a symlink component (macOS
    ``/tmp`` -> ``/private/tmp``, symlinked homes/worktrees) and the whole
    steering layer goes silently inert.
    """
    return os.path.normcase(os.path.realpath(path))


def _path_overlaps(root: str, source_roots: list[str]) -> bool:
    """True when *root* is equal to, inside, or an ancestor of any indexed root.

    The ancestor case matters too: grepping a parent directory that *contains*
    an indexed repo is still a search jcm can serve.

    A filesystem root already ends in a separator (`C:\\`, `/`), so appending
    one more made the ancestor test fail for every root (L-195).
    """
    from ...path_prefix import dir_prefix  # noqa: PLC0415  (leaf, stdlib only)

    for sr in source_roots:
        if root == sr or root.startswith(dir_prefix(sr)) or sr.startswith(dir_prefix(root)):
            return True
    return False


def _repo_owner_name(entry: dict) -> "tuple[str, str]":
    """(owner, name) from an ``IndexStore.list_repos()`` entry, or ("", "").

    The real store keys entries ``{"repo": "owner/name", ...}`` — there is no
    top-level ``owner``/``name``. Three hook loops read those absent keys for
    months and silently skipped every repo (briefing, landmarks, task
    diagnostics all dead); the only producer of the owner/name shape was a
    wrong test mock, so no fallback for it — ``repo`` is the one authority.
    """
    repo = entry.get("repo") or ""
    if isinstance(repo, str) and "/" in repo:
        owner, name = repo.split("/", 1)
        if owner and name:
            return owner, name
    return "", ""


def _repo_contains_any(store, owner: str, name: str, files: list) -> bool:
    """Cheap read-only probe: does this repo's index know any of *files*?

    Full hydration of an unrelated index is minutes-scale on big repos; one
    ``SELECT 1 ... LIMIT 1`` answers the membership question first. Returns
    True on ANY doubt (missing db, probe error) so the caller hydrates —
    a wrong False silently kills the caller's whole feature, a wrong True
    only costs the load this probe usually saves.
    """
    try:
        db = store._sqlite._db_path(owner, name)
        if not db.exists():
            return True
        from ...storage.generation import connect_readonly
        wanted = [f for f in files if isinstance(f, str)][:500]
        if not wanted:
            return True
        conn = connect_readonly(db)
        try:
            placeholders = ",".join("?" * len(wanted))
            hit = conn.execute(
                f"SELECT 1 FROM files WHERE path IN ({placeholders}) LIMIT 1",
                wanted,
            ).fetchone()
        finally:
            conn.close()
        return hit is not None
    except Exception:
        logger.debug("membership probe failed", exc_info=True)
        return True


def _iter_loaded_repos(store, repos, wanted_files=None):
    """Yield ``(repo_id, idx)`` for each loadable entry of ``list_repos()``.

    Membership and scoping guards stay with each caller — the landmark,
    taskcomplete and subagent loops genuinely differ there. ``wanted_files``
    skips hydrating repos whose index provably contains none of them.
    """
    for entry in repos:
        owner, name = _repo_owner_name(entry)
        if not owner or not name:
            continue
        if wanted_files and not _repo_contains_any(store, owner, name, wanted_files):
            continue
        try:
            idx = store.load_index(owner, name)
        except Exception:
            logger.debug("load_index failed for %s/%s", owner, name, exc_info=True)
            continue
        if not idx:
            continue
        yield f"{owner}/{name}", idx


def _top_symbols_by_pagerank(idx, *, n_files: int, n_syms: int):
    """``[(symbol, score), ...]`` for the symbols in the top-PageRank files.

    One owner for the compute_pagerank incantation and the file->symbol
    ranking; formatting stays with each caller. Returns [] when the index
    has no import graph or PageRank yields nothing.
    """
    if not idx.imports or not idx.source_files:
        return []
    from ...tools.pagerank import compute_pagerank
    pr_scores, _ = compute_pagerank(
        idx.imports, idx.source_files,
        alias_map=getattr(idx, "alias_map", None),
        psr4_map=getattr(idx, "psr4_map", None),
    )
    if not pr_scores:
        return []
    top_files = sorted(pr_scores.items(), key=lambda x: x[1], reverse=True)[:n_files]
    top_file_set = {f for f, _ in top_files}
    symbol_pr = [
        (sym, pr_scores.get(sym.get("file", ""), 0.0))
        for sym in idx.symbols
        if sym.get("file", "") in top_file_set
    ]
    symbol_pr.sort(key=lambda x: x[1], reverse=True)
    return symbol_pr[:n_syms]
