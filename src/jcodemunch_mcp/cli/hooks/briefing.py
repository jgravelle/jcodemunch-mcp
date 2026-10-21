"""SubagentStart: condensed, surface-aware repo orientation."""

import logging
import time

from ._common import (
    tool_surface,
    BUDGET_SECONDS,
    HYDRATE_MAX_SYMBOLS,  # noqa: F401  (re-exported; #1139's tests read it here)
    _emit_additional_context,
    _iter_loaded_repos,
    _top_symbols_by_pagerank,
    _norm_path,
    _note_transcript_root,
    _read_hook_payload,
    _small_enough,
    _too_large_note,
)

logger = logging.getLogger(__name__)

# #1139: the size rule and the budget are `_common`'s, shared with the
# landmarks section (L-219); only the listing cap is the briefing's own.
MAX_LISTED_REPOS = 10


def run_subagentstart() -> int:
    """SubagentStart hook: inject condensed repo orientation for spawned agents.

    Reads hook JSON from stdin. Returns a compact briefing containing:
      - Repo stats (files, symbols, languages)
      - Top 15 structurally central symbols (PageRank)
      - Available jCodemunch tool catalog

    Returns exit code (always 0).
    """
    data = _read_hook_payload()
    if data is None:
        return 0
    _note_transcript_root(data)

    try:
        from ...storage import IndexStore
        store = IndexStore()
        repos = store.list_repos()
    except Exception:
        return 0

    if not repos:
        return 0

    # #1139: this runs synchronously on the agent-startup path, so its cost
    # must be bounded. At most ONE index is hydrated (the most specific root
    # containing the cwd), only when list_repos() says it is small, and
    # PageRank is skipped once the budget is spent. Everything else is
    # briefed from the counts list_repos() already read.
    started = time.monotonic()
    hydrate, listed = _select_repos(repos, data.get("cwd"))

    parts = ["## jCodemunch Repo Briefing"]

    for entry in listed[:MAX_LISTED_REPOS]:
        parts.extend(_stats_lines(entry))
    if len(listed) > MAX_LISTED_REPOS:
        parts.append(
            f"\n... and {len(listed) - MAX_LISTED_REPOS} more indexed repos "
            "under this directory."
        )

    small = [e for e in hydrate if _small_enough(e.get("symbol_count"))]
    for entry in hydrate:
        if entry not in small:
            # UNKNOWN is not "too large": `_too_large_note` says which.
            parts.extend(_stats_lines(entry, note=f"stats only: {_too_large_note(entry)}"))

    for repo_id, idx in _iter_loaded_repos(store, small):
        # Stats
        n_files = len(idx.source_files)
        n_symbols = len(idx.symbols)
        langs = set()
        for sym in idx.symbols:
            lang = sym.get("language")
            if lang:
                langs.add(lang)
        lang_str = ", ".join(sorted(langs)[:8]) if langs else "unknown"

        parts.append(f"\n### {repo_id}")
        parts.append(f"- **Files:** {n_files} | **Symbols:** {n_symbols} | **Languages:** {lang_str}")

        # Top central symbols via PageRank, only inside the budget. A
        # hydration cannot be interrupted once started; PageRank can be
        # skipped, and on a large import graph it costs more than the load.
        if time.monotonic() - started > BUDGET_SECONDS:
            parts.append("- **Key symbols:** skipped (briefing time budget spent)")
            continue
        try:
            sym_pr = _top_symbols_by_pagerank(idx, n_files=30, n_syms=15)
            if sym_pr:
                parts.append("- **Key symbols:**")
                for sym, _ in sym_pr:
                    parts.append(f"  - `{sym.get('name', '?')}` ({sym.get('kind', '')}, {sym.get('file', '')}:{sym.get('line', 0)})")
        except Exception:
            logger.debug("PageRank failed for %s", repo_id, exc_info=True)

    # Tool catalog (compact). Must match the surface the subagent's MCP client
    # actually advertises: under the counter front door the raw catalog names
    # are NOT callable, and briefing them trains the model to distrust jcm.
    if _tool_surface() == "counter":
        parts.append("\n### Available jCodemunch Tools (Counter front door)")
        parts.append(
            "This server exposes three entry points: `menu` (search the tool "
            "catalog for the right action), `order` (execute a catalog action "
            "by name), and `route` (classify a task to a recommended action). "
            "Start with `menu` or `route`, then `order` the action it names."
        )
    else:
        parts.append("\n### Available jCodemunch Tools")
        parts.append(
            "search_symbols, get_symbol_source, get_context_bundle, get_file_content, "
            "search_text, get_ranked_context, find_importers, find_references, "
            "check_references, get_dependency_graph, get_class_hierarchy, "
            "get_call_hierarchy, get_blast_radius, get_impact_preview, "
            "get_changed_symbols, find_dead_code, get_untested_symbols, "
            "get_symbol_complexity, get_churn_rate, get_hotspots, get_repo_health, "
            "get_coupling_metrics, get_extraction_candidates, check_rename_safe, "
            "plan_refactoring, "
            "get_file_outline, get_file_tree, get_repo_outline, index_folder, "
            "index_repo, embed_repo, plan_turn, suggest_queries, "
            "get_session_context, get_session_snapshot, get_session_stats, "
            "get_cross_repo_map, get_layer_violations, audit_agent_config, "
            "get_dead_code_v2, search_columns"
        )
        parts.append("\nUse `plan_turn` to get recommended approach for your task.")

    return _emit_additional_context("SubagentStart", "\n".join(parts))


def _select_repos(repos: list, cwd) -> "tuple[list, list]":
    """``(hydrate, listed)`` for a SubagentStart cwd (#1139).

    - cwd at or inside an indexed root: hydrate the DEEPEST such root only.
      An ancestor index also contains the cwd and is the broad scope the
      report measured, so it is not briefed.
    - cwd an ancestor of indexed roots (and inside none): list those roots
      from their counts, hydrate nothing.
    - cwd missing, not a string, or matching nothing: nothing. A briefing of
      every repo on the box is the fallback that cost minutes.
    """
    if not isinstance(cwd, str) or not cwd.strip():
        return [], []
    try:
        from ...path_prefix import dir_prefix  # noqa: PLC0415  (leaf, stdlib only)

        here = _norm_path(cwd)
        containing, below = [], []
        for e in repos:
            sr = (e.get("source_root") or "").strip()
            if not sr:
                continue
            root = _norm_path(sr)
            if here == root or here.startswith(dir_prefix(root)):
                containing.append((len(root), e))
            elif root.startswith(dir_prefix(here)):
                below.append(e)
        if containing:
            return [max(containing, key=lambda t: t[0])[1]], []
        return [], sorted(below, key=lambda e: e.get("repo") or "")
    except Exception:
        logger.debug("cwd scoping failed", exc_info=True)
        return [], []


def _stats_lines(entry: dict, note: str = "") -> list:
    """A repo's briefing from list_repos() counts alone: no hydration."""
    lines = [f"\n### {entry.get('repo') or '?'}"]
    files, syms = entry.get("file_count"), entry.get("symbol_count")
    stats = f"- **Files:** {files if files is not None else '?'} | **Symbols:** {syms if syms is not None else '?'}"
    lines.append(f"{stats} ({note})" if note else stats)
    return lines


# The one hook-side reader of the served surface (moved to _common so the
# steering hook reads it the same way; kept under this name because tests
# patch it here).
_tool_surface = tool_surface
