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

from ..word_rule import word_right

logger = logging.getLogger(__name__)


_CLAUDE_MD_POLICY = """\
## Code Exploration Policy

jCodemunch-MCP indexes this repository's symbols and how they relate. Use it where it is the cheaper route, and the built-in tools (Grep, Glob, Read) where they are.
- **Relationships are what the index is for:** who imports a symbol, where a name is used, what a change breaks, who calls a function, what is unreachable. The tools are listed under "Relationships & impact" below.
- **A name you already know, or a file you are about to work in:** the built-in search and `Read` are often as cheap. Take whichever is fewer steps.
- **On a small repository** the built-in tools are usually the cheaper route for finding and reading code.
- **A large repository, or a question with no known starting point:** the search and symbol tools below read less than opening whole files.
- The agent harness requires a `Read` before `Edit`/`Write` will succeed.

**Before the first jCodemunch call in a session** (skip it when the task needs none):
- `resolve_repo { "path": "." }` — confirm the project is indexed. If not: `index_folder { "path": "." }`
- `suggest_queries` — optional, when the repo is unfamiliar

**Finding code:**
- symbol by name → `search_symbols` (add `kind=`, `language=`, `file_pattern=`, `decorator=` to narrow)
- decorator-aware queries → `search_symbols(decorator="X")` to find symbols with a specific decorator (e.g. `@property`, `@route`); combine with set-difference to find symbols *lacking* a decorator (e.g. "which endpoints lack CSRF protection?")
- string, comment, config value → `search_text` (supports regex, `context_lines`)
- database columns (dbt/SQLMesh) → `search_columns`

**Reading code:**
- a file's shape without reading it → `get_file_outline`
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
- who calls a function, and what it calls → `get_call_hierarchy`
- what symbols actually changed since last commit → `get_changed_symbols`
- find unreachable/dead code → `find_dead_code`
- class hierarchy → `get_class_hierarchy`

## Session-Aware Routing

**For a broad task, when you do not know where to start:**
1. `plan_turn { "repo": "...", "query": "your task description" }` — get confidence + recommended files. It is one call; skip it when you already know where to look.
2. Obey the confidence level:
   - `high` → go directly to recommended symbols, max 2 supplementary reads
   - `medium` → explore recommended files, max 5 supplementary reads
   - `low` → the feature likely doesn't exist. Report the gap to the user. Do NOT search further hoping to find it.
3. **One-call shortcut for a concrete task** — `assemble_task_context { "repo": "...", "task": "..." }` returns a single token-budgeted, source-attributed context capsule. It auto-classifies the task (explore / debug / refactor / extend / audit / review), auto-extracts anchor symbols, and runs the intent-appropriate sequence of the tools below end-to-end — so you get the whole context in one request instead of chaining the primitives by hand. Prefer it over a manual chain when the task is well-defined; fall back to step 1's routing when you need to decide *whether* the feature exists first.

**Interpreting search results:**
- `search_symbols` proves absence only when the scan is citable: `_meta.verdict.evidence_ref` holds an `absent:` token, or, where `_meta.verdict` is not shown, `_meta.absence_evidence.citable` is `true`. A `negative_evidence` of `no_implementation_found`, a `state` of `absent` or an empty result is not proof on its own. When absence is proven:
  - Do NOT re-search with different terms hoping to find it
  - Do NOT assume a related file (e.g. auth middleware) implements the missing feature (e.g. CSRF)
  - DO report: "No existing implementation found for X. This would need to be created."
  - DO check `related_existing` files — they show what's nearby, not what exists
  - Anything short of citable proves nothing (`absence_citable: false`, `citable: false`, or neither field present). Read the note or `absence_blocked_by`; if it names the index, re-index, then search again.
- If `verdict: "low_confidence_matches"`: examine the matches critically before assuming they implement the feature

**After editing files:**
- If PostToolUse hooks are installed (Claude Code only), edited files are auto-reindexed
- Otherwise, call `register_edit` with edited file paths to invalidate caches and keep the index fresh
- For bulk edits (5+ files), always use `register_edit` with all paths to batch-invalidate

**Token efficiency:**
- If `_meta` contains `budget_warning`: stop exploring and work with what you have. Results are never silently shortened — the warning is advisory, and what you got is complete
- Use `get_session_context` to check what you've already read — avoid re-reading the same files

## Model-Driven Tool Tiering

Your jcodemunch-mcp server can narrow the exposed tool list to suit the model you are running as. It is off unless `adaptive_tiering` is enabled in the server's config, and while it is off a model id changes nothing. Where it is on, pass your model id, exactly as your runner prints it, as `model="<your-model-id>"` on a `plan_turn` call you are already making, or call `announce_model(model="...")` once.

The server reads the model's family from the id (Opus, Sonnet, Haiku, GPT, o1, Llama), so any current id works and none needs listing here.
"""


_CLAUDE_MD_POLICY_COUNTER = """\
## Code Exploration Policy

jCodeMunch-MCP indexes this repository's symbols and how they relate. Use it where it is the cheaper route, and the built-in tools (Grep, Glob, Read) where they are.
- **Relationships are what the index is for:** who imports a symbol, where a name is used, what a change breaks, who calls a function, what is unreachable.
- **A name you already know, or a file you are about to work in:** the built-in search and `Read` are often as cheap. Take whichever is fewer steps.
- **On a small repository** the built-in tools are usually the cheaper route for finding and reading code.
- The harness requires a `Read` before `Edit`/`Write`.

This server runs the **front door** surface: three tools reach every jCodeMunch capability, so the tool list stays small and the catalogue is fetched only when you need it.

**Before the first jCodeMunch call in a session** (skip it when the task needs none):
- `order { "action": "resolve_repo", "args": { "path": "." } }` — confirm the project is indexed. If it is not: `order { "action": "index_folder", "args": { "path": "." } }`

**Then, for a task the index suits:**
- Know what you want → `order { "action": "<name>", "args": { ... } }`
- Know the goal, not the tool → `route { "query": "your task in a sentence" }` picks the action and shapes the arguments
- Want to see what exists → `menu { "query": "what you are trying to do" }` returns matching actions with example arguments
- Want the whole catalogue and the usage rules → `jcodemunch_guide`

`menu` and `jcodemunch_guide` list every action this server can run, including ones absent from your tool list. That is expected: the front door is the way to call them.

**Interpreting results:**
- A search proves absence only when the scan is citable: `_meta.verdict.evidence_ref` holds an `absent:` token, or, where `_meta.verdict` is not shown, `_meta.absence_evidence.citable` is `true`. Then report the gap; do not re-search with different wording. A `no_implementation_found` verdict, a `state` of `absent` or an empty result is not proof on its own: read the note or `absence_blocked_by`, re-index if it names the index, then search again.
- `source: ""` alongside `source_status` means the body could not be read, not that the symbol is empty.

**After editing files:**
- With PostToolUse hooks installed (Claude Code), edited files are reindexed automatically.
- Otherwise `order { "action": "register_edit", "args": { "paths": [...] } }` after an edit, batched for bulk changes.

**Optional:** announce your model once so the server can size its answers: `announce_model { "model": "<your-model-id>" }`.
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
    return policy_for(**policy_inputs())


def policy_inputs() -> dict:
    """What THIS process's policy is chosen from: ``{"surface", "tools"}``.

    ``tools`` is the sorted tool names the policy is filtered to, or ``None``
    for no filtering (and always ``None`` under ``counter``, whose policy is
    not filtered). ``claude-md --refresh --match-server`` rebuilds the server's
    policy from the inputs the server recorded, with this version's text, so a
    record can choose WHICH of jcodemunch's policies is written and never
    supply text of its own (review of L-185).
    """
    surface = _effective_tool_surface()
    if surface == "counter":
        return {"surface": "counter", "tools": None}
    active = _get_active_tools()
    # Any value but `counter` serves the full surface (CLI-AND-ENV: JCODEMUNCH_TOOL_SURFACE).
    return {"surface": "full", "tools": None if active is None else sorted(active)}


def policy_for(surface: str, tools) -> str:
    """The policy for *surface*, filtered to *tools* (``None``: unfiltered). Pure; text is only ever ours."""
    if surface == "counter":
        return _CLAUDE_MD_POLICY_COUNTER
    return _filter_policy_for_tools(_CLAUDE_MD_POLICY, None if tools is None else set(tools))



# THE marker: `cli.init` imports it, so "already present" and the drift check
# find the same block (review of #871: there were two copies, matched two ways).
POLICY_MARKER = "## Code Exploration Policy"


def _policy_headings() -> set[str]:
    """Every `## ` heading any policy variant contains: the block's own sections."""
    heads: set[str] = set()
    for text in (_CLAUDE_MD_POLICY, _CLAUDE_MD_POLICY_COUNTER):
        heads |= {ln.strip() for ln in text.splitlines() if ln.startswith("## ")}
    return heads


def _normalise(lines) -> list[str]:
    return [ln.rstrip() for ln in lines if ln.strip()]


def installed_policy_drift(text: str, policy: str | None = None) -> dict | None:
    """Does the policy block in a CLAUDE.md match what this version installs? (#871)

    ``None`` when the text holds no policy block (nothing to compare: the
    one-line ``jcodemunch_guide`` form, or no install). Otherwise
    ``{"state": "current"}`` or ``{"state": "differs", "lines_differing": n}``.

    The block runs from the marker to the first ``## `` heading that is not one
    of the policy's own sections, or to the end of the file, so a user's own
    sections after it are never compared. Trailing whitespace and blank lines are
    not a difference; indentation is. ⚠ "differs" cannot tell an outdated block from one its owner
    edited on purpose; the caller must say so, and must never rewrite it
    (the ``surface_offer`` rule: a message, never a migration).

    *policy* is the text to compare against, ``active_policy()`` when omitted;
    ``claude-md --refresh --match-server`` passes the policy the server
    recorded, which its environment may select differently (L-185).
    """
    installed = installed_policy_block(text)
    if installed is None:
        return None
    current = _normalise((active_policy() if policy is None else policy).splitlines())
    if installed == current:
        return {"state": "current"}
    import difflib

    # Lines, not diff operations: a substituted line is one line that differs
    # (an ndiff count reports it twice, as a removal and an addition).
    changed = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=installed, b=current, autojunk=False).get_opcodes()
        if tag != "equal"
    )
    return {"state": "differs", "lines_differing": changed}


def installed_policy_block(text: str) -> list[str] | None:
    """The policy block in *text*, normalised, or ``None`` when there is none.

    THE one reader of where the block starts and ends: ``installed_policy_drift``
    compares it and ``diagnose`` digests it, and a second copy of the boundary
    rule would let the two disagree about the same file.
    """
    # A byte-order mark is not part of the first line's text.
    lines = text.removeprefix(chr(0xFEFF)).splitlines()
    span = policy_block_span(lines)
    if span is None:
        return None
    return _normalise(lines[span[0] : span[1]])


def policy_block_span(lines: list[str]) -> tuple[int, int] | None:
    """``(start, end)`` line indexes of the policy block in *lines*, or ``None``.

    THE boundary rule. ``installed_policy_block`` reads through it, and so does
    ``claude-md --refresh`` (``policy_refresh``), which replaces exactly these
    lines: the check that says a block differs and the command that rewrites it
    cannot disagree about where the block ends.

    It starts at the first heading that OPENS with the marker's title, at any
    level, outside a code fence, so a sentence or a heading that mentions
    the policy by name is not mistaken for it, and neither is a fenced example
    (review of L-186: a fenced marker above the real block became the start,
    flipped the fence parity and ran the block to the end of the file). A
    byte-order mark before the first line is not part of its text; every
    caller gets that here, not by stripping it first. It runs to the first
    Markdown heading of any level that is not one of the policy's own ``## ``
    sections, or to the end of the file. The policy text at every release tag
    uses only ``## `` headings and holds no code fence (read from all 686 tags
    on 2026-10-14), so a user's own ``#`` or ``###`` section after the block
    ends it too (review, 2026-10-14: the refresh would otherwise have deleted
    it). A ``#`` line inside a fenced code block is not a heading.
    """
    start = None
    in_fence = False
    for i, ln in enumerate(lines):
        stripped = (ln.removeprefix(chr(0xFEFF)) if i == 0 else ln).strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and _MARKER_HEADING_RE.fullmatch(stripped):
            start = i
            break
    if start is None:
        return None
    own = _policy_headings()
    end = len(lines)
    in_fence = False
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and _HEADING_RE.match(stripped) and stripped not in own:
            end = i
            break
    return start, end


_HEADING_RE = re.compile(r"#{1,6}\s")
# The title opens the heading; an owner's suffix ("... (mine)") still counts.
_MARKER_TITLE = POLICY_MARKER.lstrip("#").strip()
_MARKER_HEADING_RE = re.compile(r"#{1,6}[ \t]+" + re.escape(_MARKER_TITLE) + word_right(_MARKER_TITLE) + ".*")


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
