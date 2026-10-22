"""Shared plumbing for the Claude Code hook handlers.

Helpers used by more than one hook family live here; the package
``__init__`` owns the family map.
"""

import json
import logging
import os
import sys
import time

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

    ⚠ (#1202) A path INSIDE an indexed root is served by it only if no separate
    checkout sits between them: a linked worktree nested in the checkout
    (``.claude/worktrees/<name>``) is another branch the index does not hold
    (#372), and so is a nested independent clone. ``resolve_repo`` answers both
    ``indexed: false``; steering toward the parent's index there would send the
    agent to another branch's code.
    """
    from ...path_prefix import dir_prefix  # noqa: PLC0415  (leaf, stdlib only)

    for sr in source_roots:
        if root == sr or sr.startswith(dir_prefix(root)):
            return True
        if root.startswith(dir_prefix(sr)) and not _crosses_a_checkout(root, sr):
            return True
    return False


def _crosses_a_checkout(path: str, source_root: str) -> bool:
    """True when a linked worktree or independent repo lies between the two.

    Stats only (``checkout_boundary`` is a stdlib leaf), so the per-call hook
    cost is a few ``stat`` calls on the path's own ancestors. Any doubt is
    False: a wrong True would switch steering off for an indexed file.
    """
    from pathlib import Path  # noqa: PLC0415

    from ...checkout_boundary import separate_checkout_between  # noqa: PLC0415

    try:
        return separate_checkout_between(Path(path), Path(source_root), worktrees=True) is not None
    except Exception:
        logger.debug("checkout boundary probe failed", exc_info=True)
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


# #1139 / L-219: the one size rule for a hook that hydrates on a synchronous
# path (SubagentStart, PreCompact). Seeded, not calibrated: on the
# maintainer's box (2026-10-19) a 50,320-symbol index hydrated in 0.86 s and
# a 46,765-symbol one took 2.50 s of PageRank. An index above the cap, or of
# unknown size, is not hydrated by a hook.
HYDRATE_MAX_SYMBOLS = 100_000
BUDGET_SECONDS = 3.0


def _small_enough(symbol_count) -> bool:
    """True only for a KNOWN count at or under the cap; unknown is not small."""
    return (
        isinstance(symbol_count, int)
        and not isinstance(symbol_count, bool)
        and 0 <= symbol_count <= HYDRATE_MAX_SYMBOLS
    )


def _too_large_note(entry: dict) -> str:
    """Why an index was not hydrated: too large, or of unknown size."""
    n = entry.get("symbol_count")
    known = isinstance(n, int) and not isinstance(n, bool) and n >= 0
    return "too large to load in a hook" if known else "index size unknown"


def _iter_loaded_repos(store, repos, wanted_files=None, *, size_capped=False, skipped=None,
                       stop_at=None, late=None):
    """Yield ``(repo_id, idx)`` for each loadable entry of ``list_repos()``.

    Membership and scoping guards stay with each caller — the landmark,
    taskcomplete and subagent loops genuinely differ there. ``wanted_files``
    skips hydrating repos whose index provably contains none of them.

    ``size_capped=True`` applies the hook size rule (``_small_enough``): an
    entry past the cap or of unknown size is not hydrated, and is appended to
    ``skipped`` as ``(repo_id, entry)`` when a list is passed, so the caller
    can say so instead of dropping it in silence. ``stop_at`` is a
    ``time.monotonic()`` deadline: past it, no further index is loaded, and
    each one not started is appended to ``late``.
    """
    for entry in repos:
        owner, name = _repo_owner_name(entry)
        if not owner or not name:
            continue
        if wanted_files and not _repo_contains_any(store, owner, name, wanted_files):
            continue
        if size_capped and not _small_enough(entry.get("symbol_count")):
            if skipped is not None:
                skipped.append((f"{owner}/{name}", entry))
            continue
        # Checked BEFORE the load: a hydration cannot be interrupted once it
        # starts, so a deadline checked after it has already paid for it.
        if stop_at is not None and time.monotonic() > stop_at:
            if late is not None:
                late.append(f"{owner}/{name}")
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


def tool_surface() -> str:
    """Effective ``tool_surface`` as the MCP server would resolve it (env wins,
    then config). Best-effort: any failure reads as ``full``.

    Duplicates the resolution order of ``server._effective_surface()`` on
    purpose: importing the server module here would put its full import cost
    in front of every subagent spawn, and this hook only needs the one key.

    Reads via ``config.get`` — NOT ``load_config()``, which returns None (its
    job is populating the module global) and, worse, defaults to
    ``create_missing=True``: a config READ from a hook process must never
    WRITE a config file into the user's storage dir (Maintenance Practice 8).
    ``config.get``'s lazy load passes ``create_missing=False`` for exactly
    that reason, and its env-var fallback layer already honors
    ``JCODEMUNCH_TOOL_SURFACE``.
    """
    # counter.resolve_tool_surface is the one authority for precedence and
    # validation (env wins; unrecognized serves "full"). The env pre-check
    # also skips the config import entirely when the env var decides —
    # config.get's own env layer is only a FALLBACK, so it cannot be used
    # for the precedence half.
    env = os.environ.get("JCODEMUNCH_TOOL_SURFACE")
    try:
        from ...counter import resolve_tool_surface
        if (env or "").strip():
            return resolve_tool_surface(env, None)[0]
        from ...config import get as _config_get
        return resolve_tool_surface(None, _config_get("tool_surface"))[0]
    except Exception:
        logger.debug("tool_surface resolution failed", exc_info=True)
        return "full"
