"""The Counter: an adaptive tool surface for jcodemunch-mcp.

Problem this solves
-------------------
jcm exposes ~90 MCP tools. The host serializes every resident tool's schema
into the model's context on every turn (a fixed per-turn token tax), and the
model must select one tool out of the whole catalog (dispatch dilution). Both costs scale
with tool count and work against jcm's own token-efficiency thesis.

The Counter is a small, stable front door that fronts the full catalog without
removing any capability:

  * ``order(action, args)`` -- single dispatch verb. Re-enters the normal
    tool pipeline for the chosen action. Read-only by default at the boundary:
    state-changing actions require an explicit opt-in, and exec/file-write
    verbs are refused unconditionally (a forward-looking charter tripwire --
    jcm ships none today, and the Counter must never become the surface that
    introduces one).
  * ``menu(query, tier)`` -- discovery. Search/browse the action catalog and
    return compact entries, so the full schema set need not stay resident.
  * ``route(task, execute)`` -- intent to action. Map a natural-language task
    to the best catalog action(s); optionally dispatch the top one. Composes
    with ``assemble_task_context`` / ``plan_turn`` (it recommends them for
    context-gathering intents); it does not replace them.

This module is pure logic with no server import (keeps the dependency one-way:
server.py imports counter, never the reverse). server.py owns the Tool
registration, the live catalog, and call_tool re-dispatch; it hands plain data
to the helpers here.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# The only two tool surfaces that exist. Anything else has always BEHAVED as
# "full" (only "counter" is ever special-cased).
VALID_TOOL_SURFACES = ("counter", "full")


def resolve_tool_surface(
    env_value: Optional[str], config_value: Optional[str]
) -> "tuple[str, str, bool]":
    """``(effective, requested, recognized)`` for a tool-surface setting.

    Env wins; a whitespace-only env value is treated as unset; anything
    unrecognized serves "full". One authority for the server and the
    out-of-process hooks — the two resolvers used to agree on precedence
    but only the server validated, so a typo'd value behaved identically
    while reporting differently depending on who read it.
    """
    env_value = (env_value or "").strip()
    raw = env_value if env_value else (config_value or "full")
    requested = str(raw).strip().lower()
    if requested in VALID_TOOL_SURFACES:
        return requested, requested, True
    return "full", requested, False


# The front-door tool names. These are never themselves dispatchable via
# ``order`` (no front-door recursion).
FRONT_DOOR: frozenset[str] = frozenset({"order", "menu", "route"})

# Actions that change persistent index / embedding / session / config state.
# These are charter-safe (none write the user's source files or execute code),
# but ``order`` requires an explicit ``allow_state_change=true`` before
# dispatching one, so the front door reads as read-only by default.
STATE_CHANGING_ACTIONS: frozenset[str] = frozenset({
    "index_repo", "index_folder", "index_file", "index_dependency",
    "invalidate_cache", "register_edit", "tune_weights",
    "set_tool_tier", "announce_model", "embed_repo",
    "import_runtime_signal", "summarize_repo",
    "finalize_handoff",  # persists a session handoff record (#374)
})

# Forward-looking tripwire. ``order`` refuses to dispatch any action whose name
# matches one of these verbs, even if such a tool were somehow added to the
# catalog later. jcm is read-only by charter and ships none of these; the gate
# exists so the consolidation layer can never silently become an exec/mutation
# backdoor (the line write-enabled competitors cross). This is the "safety
# surface" property: the dispatcher is a charter checkpoint, not just ergonomics.
_FORBIDDEN_VERB_RE = re.compile(
    r"(^|[._-])(exec|shell|run_command|spawn|eval|"
    r"write_file|edit_file|patch|apply_patch|delete_file|rm|mv|chmod)($|[._-])",
    re.IGNORECASE,
)


def is_state_changing(action: str) -> bool:
    return action in STATE_CHANGING_ACTIONS


def forbidden_reason(action: str) -> Optional[str]:
    """Return a rejection reason if *action* matches the exec/write tripwire."""
    if _FORBIDDEN_VERB_RE.search(action or ""):
        return (
            f"'{action}' names a write/exec verb. The Counter is a read-only "
            f"dispatch surface by charter and refuses to route execution or "
            f"file-mutation actions."
        )
    return None


def order_gate(
    action: str,
    catalog_names: Iterable[str],
    allow_state_change: bool,
) -> Optional[str]:
    """Validate an ``order`` request. Return an error string, or None if OK.

    Order of checks matters: structural (front door / unknown) before charter
    (tripwire) before policy (state-change opt-in), so the message an agent
    sees is the most actionable one.
    """
    if not action or not isinstance(action, str):
        return "order requires a non-empty 'action' name. Call 'menu' to list actions."
    if action in FRONT_DOOR:
        return f"'{action}' is a front-door tool and cannot be dispatched through order."
    names = set(catalog_names)
    if action not in names:
        return (
            f"Unknown action '{action}'. Call 'menu' (optionally with a query) "
            f"to discover valid actions."
        )
    tripwire = forbidden_reason(action)
    if tripwire is not None:
        return tripwire
    if is_state_changing(action) and not allow_state_change:
        return (
            f"'{action}' changes index/session state. Re-issue with "
            f"allow_state_change=true to proceed. (Read-only actions need no opt-in.)"
        )
    return None


# --- menu: catalog search -------------------------------------------------- #

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


# Function words carry no retrieval signal, and in THIS corpus they are actively
# harmful. idf is computed over description prose, where an English pronoun never
# appears, so idf hands it the HIGHEST weight in the query -- and a short token
# then substring-matches a large slice of snake_case names. Measured: "draw me a
# diagram of that" ranked check_rena-me-_safe, get_runti-me-_coverage and
# find_i-mple-mentations above render_diagram, all three on "me" alone, at 19.3
# points against render_diagram's 16.5 for the actual word "diagram".
#
# Deliberately NOT listed: find / get / show / search. Those are real verbs in
# this domain, they discriminate between tool families, and existing intent tests
# depend on them.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "my",
    "of", "on", "or", "our", "out", "over", "should", "so", "some", "that",
    "the", "their", "them", "then", "these", "this", "those", "to", "up",
    "us", "was", "we", "were", "what", "when", "where", "which", "will",
    "with", "would", "you", "your",
})

# A token shorter than this may only match a name as a WHOLE WORD, never as a
# fragment inside one. Without the floor, two-character noise matches a third of
# the catalog by accident.
_MIN_SUBSTRING_LEN = 4


def _query_tokens(text: str) -> list[str]:
    """Tokens worth scoring a query on. ``_tokens`` stays pure for callers that
    need the raw split (leakage measurement, description indexing)."""
    return [t for t in _tokens(text) if t not in _STOPWORDS]


def _first_sentence(desc: str, limit: int = 160) -> str:
    desc = (desc or "").strip().replace("\n", " ")
    # Cut at the first sentence boundary, else hard-truncate.
    m = re.search(r"(?<=[.!?])\s", desc)
    out = desc[: m.start() + 1] if m else desc
    if len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out


def _required_args(schema: dict) -> list[str]:
    if not isinstance(schema, dict):
        return []
    req = schema.get("required")
    return list(req) if isinstance(req, list) else []


# Curated example invocations for the highest-traffic catalog actions. These are
# the arg objects you'd hand to ``order(action, args)``, surfaced by ``menu``/
# ``route`` (and ONLY there) so an agent sees a concrete, ready-to-adapt call
# without paying for it in resident tool schemas. Correctness is not trusted to
# review: ``tests/test_counter.py`` validates every key here against the LIVE
# inputSchema of its action, so a wrong/renamed arg fails CI.
EXAMPLES: dict[str, dict] = {
    # discovery / bootstrap
    "resolve_repo": {"path": "."},
    "index_folder": {"path": "."},
    "index_file": {"path": "/abs/path/to/file.py"},
    "suggest_queries": {"repo": "owner/name"},
    # symbol + text search
    "search_symbols": {"repo": "owner/name", "query": "parse config", "kind": "function"},
    "search_text": {"repo": "owner/name", "query": "TODO|FIXME", "is_regex": True, "context_lines": 2},
    "search_columns": {"repo": "owner/name", "query": "user_id"},
    "search_ast": {"repo": "owner/name", "pattern": "nesting:>4"},
    # reading
    "get_file_outline": {"repo": "owner/name", "file_path": "src/app.py"},
    "get_symbol_source": {"repo": "owner/name", "symbol_id": "src/app.py::parse_config#function"},
    "get_context_bundle": {"repo": "owner/name", "symbol_id": "src/app.py::Loader#class"},
    "get_file_content": {"repo": "owner/name", "file_path": "src/app.py", "start_line": 1, "end_line": 40},
    "get_ranked_context": {"repo": "owner/name", "query": "how is config loaded", "token_budget": 4000},
    # structure / orientation
    "get_repo_outline": {"repo": "owner/name"},
    "get_repo_map": {"repo": "owner/name", "token_budget": 4000},
    "get_file_tree": {"repo": "owner/name", "path_prefix": "src/"},
    "digest": {"repo": "owner/name"},
    "finalize_handoff": {
        "repo": "owner/name",
        "task": "Audit the authentication surface",
        "sections": [{"heading": "Findings", "content": "…markdown authored by the assistant…"}],
        "evidence_refs": ["src/auth.py::login#function"],
    },
    # relationships / impact
    "find_importers": {"repo": "owner/name", "file_path": "src/app.py"},
    "find_references": {"repo": "owner/name", "identifier": "parse_config"},
    "check_references": {"repo": "owner/name", "identifier": "parse_config"},
    "get_blast_radius": {"repo": "owner/name", "symbol": "parse_config"},
    "get_call_hierarchy": {"repo": "owner/name", "symbol_id": "src/app.py::main#function"},
    "get_class_hierarchy": {"repo": "owner/name", "class_name": "Base"},
    "find_implementations": {"repo": "owner/name", "symbol": "Store"},
    "get_dependency_graph": {"repo": "owner/name", "file": "src/app.py"},
    "find_dead_code": {"repo": "owner/name"},
    "get_changed_symbols": {"repo": "owner/name"},
    # safety preflights
    "check_delete_safe": {"repo": "owner/name", "symbol": "parse_config"},
    "check_edit_safe": {"repo": "owner/name", "symbol": "parse_config"},
    # planning / context orchestration
    "assemble_task_context": {"repo": "owner/name", "task": "add caching to the config loader"},
    "plan_turn": {"repo": "owner/name", "query": "refactor config loading"},
}


def example_for(name: str) -> Optional[dict]:
    """Curated example args for *name*, or None. Used by menu/route surfaces."""
    ex = EXAMPLES.get(name)
    return dict(ex) if ex is not None else None


def catalog_entry(name: str, description: str, schema: dict) -> dict:
    """Compact, dense menu row for one action."""
    row = {
        "action": name,
        "summary": _first_sentence(description),
        "required": _required_args(schema),
        "state_changing": is_state_changing(name),
    }
    ex = EXAMPLES.get(name)
    if ex:
        row["example"] = ex
    return row


def score_action(
    query_tokens: list[str],
    name: str,
    description: str,
    weights: Optional[dict[str, float]] = None,
) -> float:
    """Heuristic relevance of an action to a query. Higher is better.

    Name hits dominate (an agent usually has a verb in mind); description word
    overlap breaks ties. Each query token is scaled by its idf ``weights`` so a
    rare, discriminating term ("calls") outranks a ubiquitous one ("symbol").
    Deterministic, no embeddings -- in the jMRI idiom.
    """
    if not query_tokens:
        return 0.0
    name_l = name.lower()
    name_toks = set(_tokens(name))
    desc_toks = set(_tokens(description))
    score = 0.0
    for qt in query_tokens:
        w = weights.get(qt, 1.0) if weights else 1.0
        if qt == name_l:
            score += 10.0 * w
        elif qt in name_toks:
            # Whole-word hit on a name segment. This MUST outrank the fragment
            # branch below: matching "diagram" in render_diagram is evidence,
            # matching "me" inside rename is coincidence. The two were inverted
            # (4.0 fragment vs 3.0 whole-word), which also made this branch
            # unreachable -- every token is trivially a substring of its own name.
            score += 4.0 * w
        elif len(qt) >= _MIN_SUBSTRING_LEN and qt in name_l:
            score += 1.5 * w
        if qt in desc_toks:
            score += 1.0 * w
    return score


def _idf_weights(query_tokens: list[str], rows: list[dict]) -> dict[str, float]:
    """Inverse document frequency of each query token across the catalog
    (name + description). Rare tokens weigh more; tokens in every row weigh ~0.
    """
    import math
    n = max(1, len(rows))
    docs = [set(_tokens(r["action"])) | set(_tokens(r.get("_description", r.get("summary", "")))) for r in rows]
    weights: dict[str, float] = {}
    for qt in set(query_tokens):
        df = sum(1 for d in docs if qt in d)
        # +1 smoothing; floor at a small positive so a common term still counts.
        weights[qt] = max(0.15, math.log((n + 1) / (df + 1)) + 0.3)
    return weights


def search_catalog(
    catalog: list[dict],
    query: Optional[str],
    limit: int,
) -> list[dict]:
    """Rank/filter catalog rows for *query*. ``catalog`` rows are
    ``{"action", "summary", "required", "state_changing", "_description"}``.
    With no query, return the catalog in stable order (capped at limit).
    """
    rows = [r for r in catalog if r["action"] not in FRONT_DOOR]
    if not query:
        return rows[:limit]
    qt = _query_tokens(query)
    if not qt:  # query was all function words; ranking on it is noise
        return rows[:limit]
    weights = _idf_weights(qt, rows)
    scored = []
    for r in rows:
        s = score_action(qt, r["action"], r.get("_description", r["summary"]), weights)
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: (-x[0], x[1]["action"]))
    return [r for _, r in scored[:limit]]


# --- route: intent to action ----------------------------------------------- #

# Ordered intent rules. First match whose pattern hits the task wins as the
# primary recommendation; remaining matches become alternates. Each rule is
# (compiled_pattern, action, why). Kept deterministic and legible -- this is a
# curated map, not a learned model, consistent with the read-only charter.
_INTENT_RULES: list[tuple[re.Pattern, str, str]] = [
    # --- specificity block: these MUST outrank the broad rules below --------- #
    #
    # v1.108.220. Every rule here exists because the v1.108.217 counterfactual
    # measured a `rule_preempted` miss: a broader rule further down claimed the
    # query, and because `route` runs its lexical fallback ONLY when no rule
    # matches, the correct action was never scored at all. Ranking work cannot
    # touch that class -- the scorer never ran -- so precedence is the only fix.
    #
    # ⚠ Added ABOVE the broad rules rather than by narrowing them. Narrowing
    # `search_symbols`' `\b(find|locate|where is|...)\b` would silently drop
    # phrasings no corpus covers; an earlier, more specific rule changes only
    # the cases it names. Same reasoning as the .212 transform block, one
    # direction reversed.

    # An HTTP endpoint is not a symbol, and `get_blast_radius` cannot accept
    # one -- so its `\b(break|impact|affect)\b` rule claiming this query handed
    # the user a tool that could not answer it.
    (re.compile(r"\b(get|post|put|patch|delete|head|options)\s+/?\S*\b.*\b"
                r"(endpoint|route|api)\b|"
                r"\b(endpoint|route)\b[^.]{0,40}\b(break|breaks|impact|affect|"
                r"depend|change|changing)\b|"
                r"\b(break|breaks|impact|affect|change|changing)\b[^.]{0,40}\b"
                r"(endpoint|api route)\b", re.I),
     "get_endpoint_impact", "Endpoint-centric impact: what breaks if this HTTP route changes."),

    # "break callers if I edit this signature" is a SAFETY question. The callers
    # rule below answers a narrower one (who calls it) and stops there.
    (re.compile(r"\b(safe|safely|going to break|will (it|this) break|hurt anyone|"
                r"ok to)\b[^.]{0,60}\b(edit|editing|change|changing|signature|"
                r"parameters?|arguments?|returns?)\b|"
                r"\b(edit|change|changing)\b[^.]{0,30}\b(signature|parameters?|"
                r"arguments?)\b[^.]{0,40}\b(break|safe|hurt|callers?)\b", re.I),
     "check_edit_safe", "Preflight whether editing this symbol breaks its callers."),

    # Structural/anti-pattern searches. `search_symbols` claimed these on the
    # bare verb "find", but a name search cannot express "caught and ignored".
    (re.compile(r"\b(swallow\w*|silently (ignor|catch)\w*|empty (catch|except)|"
                r"catch\w*\s+(block\s+)?that\s+(does nothing|is empty)|"
                r"caught\b[^.]{0,30}\b(ignor\w+|nothing)|"
                r"bare (except|catch)|anti[- ]?pattern)\b", re.I),
     "search_ast", "AST pattern match for structural anti-patterns a name search cannot express."),

    # A census of annotations, not a lookup of one.
    (re.compile(r"\b(every|all|inventory|census|which|what)\b[^.]{0,40}\b"
                r"(decorators?|annotations?|attributes?)\b|"
                r"\b(decorators?|annotations?)\b[^.]{0,30}\b(across|used|show up|"
                r"in the (project|codebase|repo))\b", re.I),
     "get_decorator_census", "Repo-wide census of decorators / annotations / attributes."),

    # ⚠ "semantic search for this project" contains the literal substring
    # "search for", which is why `search_symbols` claimed a request to BUILD the
    # embedding index rather than to query it.
    (re.compile(r"\b(turn on|enable|set up|precompute|build|make)\b[^.]{0,40}\b"
                r"(semantic|similarity|embedding|vector)\w*\b|"
                r"\b(semantic|similarity|embedding|vector)\w*\b[^.]{0,25}\b"
                r"(work|working|available|on for)\b", re.I),
     "embed_repo", "Precompute embeddings so semantic and similarity search work here."),

    # `get_session_context`'s `\bso far\b` claimed a savings question.
    (re.compile(r"\b(tokens?|context|spend|savings?|saved)\b[^.]{0,40}\b"
                r"(saved|save|avoid\w*|not (have|had) to send)\b|"
                r"\b(how many|running total|what has)\b[^.]{0,30}\btokens?\b", re.I),
     "get_session_stats", "Token savings for this session."),

    # --- vocabulary block: intents no shared token could reach ---------------- #
    #
    # The counterfactual's `no_lexical_overlap` class: the user's phrasing and
    # the action's name+description share ZERO tokens, so the score is zero at
    # any weight and no reweighting can rescue it. A curated rule states the
    # mapping explicitly instead of hoping vocabulary drifts together.
    #
    # ⚠ Deliberately NOT fixed by stuffing the benchmark's words into tool
    # descriptions: that fits the measurement rather than the product, and it
    # would raise the corpus's own leakage score -- the metric that exists to
    # catch exactly this.
    (re.compile(r"\b(show|see|read|print|give)\b[^.]{0,25}\b(body|implementation|"
                r"source|code)\b[^.]{0,25}\b(of|for)?\s*(this|that|the)\b|"
                r"\b(body|implementation) of (this|that|the)\b", re.I),
     "get_symbol_source", "Return one symbol's full source."),
    (re.compile(r"\b(natural|real|actual|logical)\b[^.]{0,20}\b(modules?|"
                r"subsystems?|components?|parts)\b|"
                r"\bgroup\b[^.]{0,40}\b(belong together|actually belong)\b|"
                r"\bas opposed to the (folder|directory) (layout|structure)\b", re.I),
     "get_tectonic_map", "Logical module topology, fused from three coupling signals."),
    (re.compile(r"\b(concentrated|concentration|piled into|spread out|evenly)\b|"
                r"\bhow (deep|many layers)\b[^.]{0,30}\b(nest\w*|dependenc\w+|go)\b", re.I),
     "get_architecture_metrics", "Concentration, dependency depth and modularity in one call."),
    (re.compile(r"\b(riskiest|most risky|most dangerous|careful editing|be careful)\b|"
                r"\bwhich (files?|parts?)\b[^.]{0,30}\b(risk|risky|careful)\b", re.I),
     "get_file_risk", "Per-symbol composite risk, highest first."),
    (re.compile(r"\b(risky|risk|nervous|dangerous|safe)\b[^.]{0,40}\b"
                r"(pull request|\bpr\b|merge|merging|branch)\b|"
                r"\b(pull request|\bpr\b|merging)\b[^.]{0,25}\b(risk|risky|safe)\b", re.I),
     "get_pr_risk_profile", "Unified risk assessment for a branch or PR."),
    (re.compile(r"\b(how )?(complicated|complex|hairy|convoluted|gnarly)\b"
                r"[^.]{0,30}\b(function|method|this one|it)\b|"
                r"\bhow many branches\b|\bcyclomatic\b", re.I),
     "get_symbol_complexity", "Cyclomatic complexity, nesting and parameter count for one symbol."),
    (re.compile(r"\b(actually (runs?|runs in|executed)|really (runs?|used))\b"
                r"[^.]{0,30}\b(production|prod|live|runtime)\b|"
                r"\b(never|not) (get |gets |be )?exercised\b|"
                r"\bhow much of (this|the) code\b[^.]{0,30}\b(runs?|used)\b", re.I),
     "get_runtime_coverage", "Runtime coverage: which indexed symbols have trace evidence."),

    # --- stateful: session / recent-change intents --------------------------- #
    # Read the session journal / working-tree delta, not the whole index.
    # Placed FIRST: stateful phrasings ("affected by my recent changes") carry
    # trigger words for the impact/reference rules below, so they must win first.
    (re.compile(r"\b(uncommitted|working tree|staged changes|since (the )?last commit|"
                r"changed (since|today|in the last)|what changed|different from main|"
                r"diff of what|what i modified|recently modified|"
                r"my (last|recent) (edit|change|changes)|last thing i edited|"
                r"renamed just now|(edits?|changes?) (are )?pending|pending (edits?|changes?)|"
                r"did i (just )?(change|edit)|just changed?)\b", re.I),
     "get_changed_symbols", "List symbols changed since the last commit or in the working tree."),
    (re.compile(r"\b(this session|we (touched|made|worked|work on)|left off|pick up where|"
                r"recap|so far|a minute ago|i was editing|(in the )?last hour)\b", re.I),
     "get_session_context", "Recap what this session has touched so far."),

    # --- mutate: edit/execute intents ---------------------------------------- #
    # jcm is read-only by charter and performs NO edit. When the task is a
    # COMMAND to change code, route recognizes it and recommends the read-only
    # PREP tool for that edit kind; the agent then applies the change with its
    # own editor. Anchored to a LEADING imperative verb so a question about an
    # edit ("what breaks if I rename X") falls through to the impact rules and is
    # NOT captured here. There is no auto-execute for these (no _QUERY_ARG entry):
    # jcm recommends the preflight, it never presumes to run a mutation flow.
    (re.compile(r"^\s*rename\b", re.I),
     "check_rename_safe", "jcm is read-only; verify a rename is safe, then apply it with your editor."),
    (re.compile(r"^\s*(delete|remove)\b", re.I),
     "check_delete_safe", "jcm is read-only; check what breaks before you delete, then remove it with your editor."),
    (re.compile(r"^\s*(refactor|extract|move|inline)\b", re.I),
     "plan_refactoring", "jcm is read-only; get an edit-ready refactor plan, then apply it with your editor."),
    (re.compile(r"^\s*(add|write|create|implement|fix|update|convert|reformat|change|wrap|generate|apply)\b", re.I),
     "check_edit_safe", "jcm is read-only; preflight the edit's risk, then modify with your editor."),

    (re.compile(r"\b(who )?calls?\b|\bcallers?\b|\bcall(ed)? by\b|\bcall (graph|hierarchy)\b", re.I),
     "get_call_hierarchy", "Trace callers/callees of a symbol."),
    (re.compile(r"\bused? (by|where)\b|\breferences?\b|\bwhere is .* used\b|\bis used\b", re.I),
     "check_references", "Where an identifier is used: import sites plus every content match."),
    (re.compile(r"\b(who|which files?) imports?\b|\bimported (by|where|or)\b|\bimporters? of\b|\bre-?exported\b", re.I),
     "find_references", "Who imports an identifier, via the import graph."),
    (re.compile(r"\b(blast|impact|break|breaks?|affect|ripple|what changes)\b", re.I),
     "get_blast_radius", "Show what a change to a symbol would affect."),
    (re.compile(r"\bdead code\b|\bunused\b|\bunreachable\b", re.I),
     "find_dead_code", "Find unreachable/unused code."),
    (re.compile(r"\boutline\b|\bstructure of\b|\bwhat'?s in .*\bfile\b|\bsymbols in\b", re.I),
     "get_file_outline", "List the symbols/structure of a file."),
    (re.compile(r"\b(string|text|literal|config value|comment|grep|regex)\b", re.I),
     "search_text", "Full-text search across file contents."),
    (re.compile(r"\bclass (hierarchy|tree)\b|\bsubclass|\bsuperclass|\binherit", re.I),
     "get_class_hierarchy", "Show a class inheritance hierarchy."),
    (re.compile(r"\bdependenc|\bimport graph\b|\bwhat imports\b", re.I),
     "get_dependency_graph", "Map file-level import dependencies."),
    (re.compile(r"\bhealth\b|\bhotspot|\bcomplexit|\bchurn\b|\brisk\b", re.I),
     "get_repo_health", "Repo-level health, hotspots, and risk."),
    # --- transform: act on another tool's OUTPUT ------------------------------ #
    # These consume a prior result rather than querying the index, so none of the
    # words an agent would actually type ("diagram", "a plan for renaming") appear
    # in the catalog text the lexical fallback ranks over -- measured route recall
    # for this whole group was 0% before these rules existed.
    #
    # Placed LATE on purpose, two constraints at once. Late enough that a specific
    # data-fetch intent still wins primary: "visualize the call graph" must lead
    # with get_call_hierarchy, because render_diagram consumes that output and has
    # nothing to draw without it. Early enough to precede the generic "\bplan\b"
    # rule below, which would otherwise claim every refactor-plan request.
    #
    # High-precision nouns only. A bare "graph"/"render"/"map" would capture the
    # call-graph, import-graph and topology intents -- the failure these rules
    # exist to fix, inverted.
    (re.compile(r"\b(diagram|mermaid|flowchart|graphviz|visuali[sz]e|visuali[sz]ation)\b", re.I),
     "render_diagram", "Render a graph-producing tool's output as an annotated Mermaid diagram."),
    # A REQUEST for a plan is not a mutation command, so this is deliberately not
    # leading-anchored the way the imperative rules above are.
    (re.compile(r"\b(plan|steps|walk me through)\b[^.]{0,40}"
                r"\b(rename|renaming|refactor|refactoring|extract|extracting|"
                r"inline|moving|migrat\w*)\b", re.I),
     "plan_refactoring", "Edit-ready plan for a rename, move, extract, or signature change."),

    (re.compile(r"\bplan\b|\bwhere (do|should) i (start|begin)\b|\bcontext for\b|\bonboard\b|\bunderstand the\b|"
                r"\bset me up\b|\beverything i need\b|\bup to speed\b|\bget me (started|going)\b|"
                r"\bhelp me (debug|fix|track down|get)\b", re.I),
     "assemble_task_context", "Single-call task-scoped context assembly."),
    # v1.108.253. CONTENT search, not NAME search. `search_symbols`' broad
    # `\b(find|locate|...)\b` below answered every one of these, because "find"
    # is the verb for both intents -- but a symbol-name index cannot match a log
    # message, a quoted literal, or "every place we do X".
    #
    # Measured, not assumed: on 40 agent-EMITTED task strings (the wording route
    # actually receives -- see benchmarks/route_recall/run_emitted_task.py) that
    # broad rule fired on 26 and returned search_symbols alone, while the gold
    # split 18 search_text / 17 search_symbols. It was a coin flip by
    # construction on the majority case.
    #
    # ⚠ Placed here, below the whole specificity block, so every narrower rule
    # still wins: "find every place we swallow an exception" must stay
    # search_ast, and this rule's `every place` would otherwise steal it.
    (re.compile(r"\b(string|literal|message|text|phrase|log line|error message|"
                r"comment|todo|fixme|regex|substring|hard[- ]?coded)\b|"
                r"\b(occurrences?|every place|all the places|any place|anywhere|"
                r"spots? where|places? where|everywhere)\b|"
                r"\b(find|locate|search for|grep)\b[^.]{0,40}"
                r"[\"'`][^\"'`]{3,}[\"'`]", re.I),
     "search_text", "Content search: the target is a string or phrase, not a symbol name."),

    (re.compile(r"\b(find|locate|where is|look up|search for|definition of)\b", re.I),
     "search_symbols", "Find a symbol by name."),

    # The ambiguous residue, and the reason this rule is LAST in the table.
    #
    # "find X" is genuinely undecidable between a name search and a content
    # search without more signal, and the emitted-string measurement put the
    # gold split at 18/17 -- so there is no honest rank-1 answer. What was
    # indefensible was returning ONE action and calling it confident: a curated
    # rule that matches emits a single recommendation, so 28 of those 40 cases
    # had no rank 2 at all and @3 was identical to @1 (25.0%). The caller was
    # given a coin flip with no way to see the other side.
    #
    # Appending here cannot displace anything -- every earlier rule has already
    # claimed its rank -- so this only ever ADDS the alternate that was missing.
    # It deliberately does not reorder the ambiguous case: 18 vs 17 is not a
    # signal, and flipping the default to chase one case would be fitting the
    # sample rather than the intent.
    (re.compile(r"\b(find|locate|where is|look up|search for)\b", re.I),
     "search_text", "Same phrasing also fits a content search; offered as the alternate."),
]

# Repo-scoped actions whose primary query arg is named differently. Used by
# route(execute=true) to shape args from (repo, task).
_QUERY_ARG: dict[str, str] = {
    "search_symbols": "query",
    "search_text": "query",
    "assemble_task_context": "task",
    "plan_turn": "query",
    "get_file_outline": "file_path",
}


def classify_intent(task: str, catalog_names: Iterable[str]) -> list[dict]:
    """Return recommended actions for a task from the curated intent rules ONLY.

    Walks ``_INTENT_RULES`` in declaration order (which is load-bearing -- see
    its block comments) and appends one ``{"action", "why"}`` row per matching
    rule, de-duplicated by action. Only actions present in the live catalog
    survive. Returns an empty list when no rule matches.

    ⚠ **The catalog-search fallback is NOT here.** ``_handle_route`` in
    ``server.py`` calls ``search_catalog`` itself, and only when this function
    returns nothing -- which is why an action a rule preempted was never
    scored at all (the ``rule_preempted`` miss class named above).
    """
    names = set(catalog_names)
    out: list[dict] = []
    seen: set[str] = set()
    for pat, action, why in _INTENT_RULES:
        if action in names and action not in seen and pat.search(task or ""):
            out.append({"action": action, "why": why})
            seen.add(action)
    return out


def shape_execute_args(action: str, repo: Optional[str], task: str) -> Optional[dict]:
    """Build a best-effort argument dict to dispatch *action* from (repo, task).

    Returns None when the action's inputs can't be satisfied from route's
    inputs (caller should then recommend rather than execute).
    """
    qarg = _QUERY_ARG.get(action)
    if qarg is None:
        return None
    if action == "get_file_outline":
        # Needs a concrete file path, which a free-form task rarely provides.
        return None
    if not repo:
        return None
    return {"repo": repo, qarg: task}
