"""Agent-policy text and the surface filtering that selects it.

⚠⚠ Extracted from `init.py` to break a real import cycle: `skills.py` needed
`_CLAUDE_MD_POLICY`, `_filter_policy_for_tools` and `_get_active_tools`, while
`init.py` needed `install_claude_skill` from `skills.py`. Neither module was
wrong to want the other -- the shared half simply had no home of its own, so it
lived in whichever file wrote it first.

⚠ Nothing here imports `init` or `skills`, and it must stay that way: this is
the LEAF the two of them share. `init.py` re-exports every name below so
existing callers (server.py and the tests) are untouched.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


_CLAUDE_MD_POLICY = """\
## Code Exploration Policy

Always use jCodemunch-MCP tools for code navigation. Never fall back to Read, Grep, Glob, or Bash for code exploration.
**Exception:** Use `Read` when you need to edit a file — the agent harness requires a `Read` before `Edit`/`Write` will succeed. Use jCodemunch tools to *find and understand* code, then `Read` only the specific file you're about to modify.

**Start any session:**
1. `resolve_repo { "path": "." }` — confirm the project is indexed. If not: `index_folder { "path": "." }`
2. `suggest_queries` — when the repo is unfamiliar

**Finding code:**
- symbol by name → `search_symbols` (add `kind=`, `language=`, `file_pattern=`, `decorator=` to narrow)
- decorator-aware queries → `search_symbols(decorator="X")` to find symbols with a specific decorator (e.g. `@property`, `@route`); combine with set-difference to find symbols *lacking* a decorator (e.g. "which endpoints lack CSRF protection?")
- string, comment, config value → `search_text` (supports regex, `context_lines`)
- database columns (dbt/SQLMesh) → `search_columns`

**Reading code:**
- before opening any file → `get_file_outline` first
- one or more symbols → `get_symbol_source` (single ID → flat object; array → batch)
- symbol + its imports → `get_context_bundle`
- specific line range only → `get_file_content` (last resort)

**Repo structure:**
- `get_repo_outline` → dirs, languages, symbol counts
- `get_file_tree` → file layout, filter with `path_prefix`

**Relationships & impact:**
- what imports this file → `find_importers`
- who imports this identifier → `find_references`
- where is this name used (imports + every content match) → `check_references`
- file dependency graph → `get_dependency_graph`
- what breaks if I change X → `get_blast_radius`
- what symbols actually changed since last commit → `get_changed_symbols`
- find unreachable/dead code → `find_dead_code`
- class hierarchy → `get_class_hierarchy`

## Session-Aware Routing

**Opening move for any task:**
1. `plan_turn { "repo": "...", "query": "your task description", "model": "<your-model-id>" }` — get confidence + recommended files; the `model` parameter narrows the exposed tool list to match your capabilities at zero extra requests.
2. Obey the confidence level:
   - `high` → go directly to recommended symbols, max 2 supplementary reads
   - `medium` → explore recommended files, max 5 supplementary reads
   - `low` → the feature likely doesn't exist. Report the gap to the user. Do NOT search further hoping to find it.
3. **One-call shortcut for a concrete task** — `assemble_task_context { "repo": "...", "task": "..." }` returns a single token-budgeted, source-attributed context capsule. It auto-classifies the task (explore / debug / refactor / extend / audit / review), auto-extracts anchor symbols, and runs the intent-appropriate sequence of the tools below end-to-end — so you get the whole context in one request instead of chaining the primitives by hand. Prefer it over a manual chain when the task is well-defined; fall back to step 1's routing when you need to decide *whether* the feature exists first.

**Interpreting search results:**
- If `search_symbols` returns `negative_evidence` with `verdict: "no_implementation_found"`:
  - Do NOT re-search with different terms hoping to find it
  - Do NOT assume a related file (e.g. auth middleware) implements the missing feature (e.g. CSRF)
  - DO report: "No existing implementation found for X. This would need to be created."
  - DO check `related_existing` files — they show what's nearby, not what exists
- If `verdict: "low_confidence_matches"`: examine the matches critically before assuming they implement the feature

**After editing files:**
- If PostToolUse hooks are installed (Claude Code only), edited files are auto-reindexed
- Otherwise, call `register_edit` with edited file paths to invalidate caches and keep the index fresh
- For bulk edits (5+ files), always use `register_edit` with all paths to batch-invalidate

**Token efficiency:**
- If `_meta` contains `budget_warning`: stop exploring and work with what you have. Results are never silently shortened — the warning is advisory, and what you got is complete
- Use `get_session_context` to check what you've already read — avoid re-reading the same files

## Model-Driven Tool Tiering

Your jcodemunch-mcp server narrows the exposed tool list based on the model you are running as. To avoid wasting requests on primitives when a composite would do, always include `model="<your-model-id>"` in your opening `plan_turn` call.

Replace `<your-model-id>` with your active model:
- Claude Opus variants → `claude-opus-4-7` (or any `claude-opus-*`)
- Claude Sonnet variants → `claude-sonnet-4-6`
- Claude Haiku variants → `claude-haiku-4-5`
- GPT-4o / GPT-5 / o1 / Llama → use the model id as printed by your runner

The `model=` parameter rides on the existing `plan_turn` call — it does **not** add a separate tool invocation. If `plan_turn` is not appropriate for a non-code task, call `announce_model(model="...")` once instead.
"""


_CLAUDE_MD_POLICY_COUNTER = """\
## Code Exploration Policy

Always use jCodeMunch-MCP for code navigation. Never fall back to Read, Grep, Glob, or Bash for code exploration.
**Exception:** use `Read` when you are about to edit a file — the harness requires a `Read` before `Edit`/`Write`. Use jCodeMunch to *find and understand* code, then `Read` only the file you are changing.

This server runs the **front door** surface: three tools reach every jCodeMunch capability, so the tool list stays small and the catalogue is fetched only when you need it.

**Start any session:**
1. `order { "action": "resolve_repo", "args": { "path": "." } }` — confirm the project is indexed. If it is not: `order { "action": "index_folder", "args": { "path": "." } }`

**Then, for any task:**
- Know what you want → `order { "action": "<name>", "args": { ... } }`
- Know the goal, not the tool → `route { "query": "your task in a sentence" }` picks the action and shapes the arguments
- Want to see what exists → `menu { "query": "what you are trying to do" }` returns matching actions with example arguments
- Want the whole catalogue and the usage rules → `jcodemunch_guide`

`menu` and `jcodemunch_guide` list every action this server can run, including ones absent from your tool list. That is expected: the front door is the way to call them.

**Interpreting results:**
- A `verdict` of `no_implementation_found` is evidence of absence. Report the gap; do not re-search with different wording.
- A `verdict` of `degraded` means a channel was unavailable, so absence is NOT proven. Read the note before relying on the result.
- `source: ""` alongside `source_status` means the body could not be read, not that the symbol is empty.

**After editing files:**
- With PostToolUse hooks installed (Claude Code), edited files are reindexed automatically.
- Otherwise `order { "action": "register_edit", "args": { "paths": [...] } }` after an edit, batched for bulk changes.

**Announce your model once per session** so the server can size its answers: `announce_model { "model": "<your-model-id>" }`.
"""


def _effective_tool_surface() -> str:
    """The tool surface this install will actually serve ("full" or "counter")."""
    try:
        from ..config import get as cfg_get
        env = os.environ.get("JCODEMUNCH_TOOL_SURFACE")
        return (env or cfg_get("tool_surface", "full") or "full").strip().lower()
    except Exception:
        return "full"


def _get_active_tools() -> set[str] | None:
    """Return the set of tool names active under current config.

    Applies tool_surface, tool_profile and disabled_tools filtering.
    Returns ``None`` when the profile is "full" and nothing is disabled
    (i.e. no filtering needed).

    ⚠ Surface is checked FIRST and is not a filter over the tier: under
    ``counter`` the server advertises only the front door, whatever the profile
    says, so a policy naming direct tools describes calls the client cannot
    offer the model. The tools remain callable by name, which is exactly why
    this went unnoticed -- nothing errors, the guidance is simply unreachable
    through the tool list.
    """
    try:
        from ..server import _build_tools_list
    except Exception:
        logger.debug("could not import the tool-list builder", exc_info=True)
        return None

    if _effective_tool_surface() == "counter":
        return set(_front_door_tool_names())

    # ⚠⚠ ASK the builder; do not reconstruct its answer (#507). This used to
    # rebuild the active set from `tool_profile` + the baked `_PROFILE_TIERS`,
    # which omits three inputs `tools/list` actually reads:
    #   1. the SESSION tier override — `set_tool_tier`, and also `announce_model`
    #      via `resolve_model_to_tier`, so an agent that announces a small model
    #      and then reads the guide diverges without configuring anything;
    #   2. `tool_tier_bundles`, which lets a user redefine what a tier contains;
    #   3. the `languages` gate, which drops `search_columns` when SQL is off.
    # Measured on one process: 70, 15 and 1 unmounted names respectively, and
    # the `init` half of the last two is written into the user's CLAUDE.md and
    # stays there.
    try:
        active = {t.name for t in _build_tools_list()}
    except Exception:
        logger.debug("tool-list build failed; not filtering", exc_info=True)
        return None

    # ⚠ An empty answer must not filter the policy down to nothing. `None` means
    # "no filtering", which is the safe direction: a policy naming a few
    # unavailable tools is a smaller harm than a policy with no workflow left in
    # it. Same shape as v1.108.209's rule that an unmeasurable comparison never
    # answers `fresh`.
    if not active:
        logger.debug("tool-list build returned nothing; not filtering")
        return None
    return active


def _front_door_tool_names() -> set[str]:
    """Tool names the server advertises under ``tool_surface="counter"``.

    The front door itself is only three tools, but the surface it produces is
    six: ``_ALWAYS_PRESENT_TOOLS`` survives every filter, and the policy
    legitimately uses two of them (``announce_model`` to size answers,
    ``jcodemunch_guide`` to discover the catalogue). Reading both from the
    server keeps this from drifting the moment either list changes.
    """
    names: set[str] = set()
    try:
        from ..server import _ALWAYS_PRESENT_TOOLS, _counter_front_door_tools
        names = {t.name for t in _counter_front_door_tools()}
        names |= set(_ALWAYS_PRESENT_TOOLS)
    except Exception:
        logger.debug("could not read the front-door tool list", exc_info=True)
    return names or {"order", "menu", "route", "jcodemunch_guide",
                     "announce_model", "set_tool_tier"}


_TOOL_REF_RE = re.compile(r"`([a-z][a-z0-9_]*)[`(\s{]")


def active_policy() -> str:
    """The agent policy matching the surface this install actually serves.

    Every writer goes through here so the choice cannot be made two ways. Under
    the front door the direct-tool policy is not merely over-long, it names
    calls the client will not offer the model, so the counter policy replaces it
    outright rather than being filtered down to the three surviving names --
    filtering a workflow away leaves an agent with no workflow at all.
    """
    if _effective_tool_surface() == "counter":
        return _CLAUDE_MD_POLICY_COUNTER
    return _filter_policy_for_tools(_CLAUDE_MD_POLICY, _get_active_tools())



def _filter_policy_for_tools(policy: str, active_tools: set[str] | None) -> str:
    """Filter the CLAUDE.md policy to only reference available tools.

    Lines containing backtick-quoted tool names that are NOT in
    *active_tools* are removed.  Sections left empty after filtering
    are also removed.  Returns the policy unchanged when *active_tools*
    is ``None`` (full profile, nothing disabled).
    """
    if active_tools is None:
        return policy

    # Build the set of all known tool names for reference-detection.
    try:
        from ..server import _CANONICAL_TOOL_NAMES
        all_tools = set(_CANONICAL_TOOL_NAMES)
    except Exception:
        return policy

    lines = policy.splitlines(keepends=True)
    kept: list[str] = []

    for line in lines:
        refs = _TOOL_REF_RE.findall(line)
        # Only consider refs that are actual tool names
        tool_refs = [r for r in refs if r in all_tools]
        if tool_refs and any(t not in active_tools for t in tool_refs):
            continue  # drop line — references unavailable tool(s)
        kept.append(line)

    # Remove bold-label headers (e.g. "**Finding code:**") that lost all
    # their child bullets.  A bold-label is "empty" if the next non-blank
    # line is another bold-label, a ## heading, or EOF.
    # We do NOT prune ## headings here — they may legitimately sit above
    # bold-label sub-sections that survived filtering.
    result: list[str] = []
    i = 0
    while i < len(kept):
        line = kept[i]
        stripped = line.strip()

        is_bold_label = (
            stripped.startswith("**")
            and stripped.endswith(":**")
            and not stripped.startswith("## ")
        )

        if is_bold_label:
            j = i + 1
            while j < len(kept) and not kept[j].strip():
                j += 1
            if j >= len(kept):
                break  # trailing empty label — drop
            next_s = kept[j].strip()
            next_is_boundary = (
                (next_s.startswith("**") and next_s.endswith(":**"))
                or next_s.startswith("## ")
            )
            if next_is_boundary:
                i = j  # skip empty bold-label section
                continue

        result.append(line)
        i += 1

    return "".join(result)
