"""MCP server for jcodemunch-mcp."""

import argparse
import asyncio
import atexit
import errno
import functools
import hmac
import json
import jsonschema
import logging
import os
import sys
import time
from pathlib import Path
from typing import IO, Any, Optional

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Tool, ToolAnnotations, TextContent, Resource, Prompt, PromptMessage, GetPromptResult, CallToolResult

from . import __version__
from . import config as config_module
from .embeddings.advice import PROVIDER_HINT as _PROVIDER_HINT
from . import runtime_identity
from .tools import _arg_contract
# Tool modules are imported lazily inside each call_tool() dispatch branch.
# This defers loading heavy dependencies (tree-sitter, httpx, pathspec) until
# the first actual call to a tool that needs them, reducing cold-start latency
# for sessions that only use query tools and never trigger indexing.
from .parser.symbols import VALID_KINDS
from .summarizer import get_provider_name
from .reindex_state import await_freshness_if_strict
from .storage import result_cache_invalidate as _result_cache_invalidate
from .storage import write_pulse as _write_pulse

try:
    from .watcher import watch_folders, WatcherError, WatcherManager
except ImportError:
    watch_folders = None  # type: ignore[assignment, misc]
    WatcherManager = None  # type: ignore[assignment, misc]
    WatcherError = type("WatcherError", (Exception,), {})  # type: ignore[assignment, misc]

# Global watcher manager instance (set in _run_server_with_watcher)
_watcher_manager: Optional["WatcherManager"] = None


# Canonical list of all registered tool names (unfiltered).
# Keep in sync with _build_tools_list(). Used by `config --check` and
# `claude-md --generate` to detect CLAUDE.md / hook-script drift.
_CANONICAL_TOOL_NAMES: tuple[str, ...] = (
    # Indexing
    "index_repo", "index_folder", "summarize_repo", "index_file",
    "index_dependency",
    # Discovery
    "list_repos", "resolve_repo", "suggest_queries",
    "get_repo_outline", "get_file_tree", "get_file_outline",
    # Search & Retrieval
    "search_symbols", "get_symbol_source", "get_context_bundle",
    "get_file_content", "search_text", "search_columns", "get_ranked_context",
    "assemble_task_context",
    # Relationships
    "find_importers", "find_references", "check_references",
    "get_dependency_graph", "get_class_hierarchy", "get_related_symbols",
    "get_call_hierarchy",
    # Impact & Safety
    "get_blast_radius", "check_rename_safe", "check_delete_safe", "check_edit_safe",
    "get_impact_preview", "get_changed_symbols", "plan_refactoring",
    "get_symbol_provenance", "get_pr_risk_profile", "get_endpoint_impact",
    # Symbol navigation
    "find_implementations",
    # Architecture
    "get_dependency_cycles", "get_coupling_metrics", "get_layer_violations",
    "get_extraction_candidates", "get_cross_repo_map", "get_group_contracts",
    "get_tectonic_map", "get_signal_chains", "get_decorator_census", "get_architecture_metrics",
    "render_diagram", "get_project_intel", "list_workspaces",
    # Quality & Metrics
    "get_symbol_complexity", "get_churn_rate", "get_delivery_metrics", "get_hotspots",
    "get_parity_map",
    "get_repo_health", "get_symbol_importance", "get_repo_map", "find_dead_code",
    "get_dead_code_v2", "get_untested_symbols", "find_similar_symbols", "search_ast",
    # Diffs & Embeddings
    "get_symbol_diff", "embed_repo",
    # Utilities
    "get_session_stats", "get_session_context", "get_session_snapshot", "plan_turn", "register_edit", "invalidate_cache", "test_summarizer",
    "audit_agent_config", "get_watch_status", "analyze_perf", "tune_weights", "check_embedding_drift",
    "suggest_corrections",
    # Canonical handoff (#374)
    "finalize_handoff",
    # Agent stand-up briefing
    "digest",
    # Health-radar diff (PR-time diff-grade reports)
    "diff_health_radar",
    # Per-file risk (powers VS Code gutter)
    "get_file_risk",
    # Runtime tier switching
    "set_tool_tier", "announce_model",
    # Composite retrieval
    "winnow_symbols",
    # Runtime trace ingest + analytics (Phases 1-6)
    "import_runtime_signal",
    "get_runtime_coverage",
    "find_hot_paths",
    "find_unused_paths",
    "get_redaction_log",
    # Self-guide (force-included; lets one-line CLAUDE.md pull full policy on demand)
    "jcodemunch_guide",
)

# Category groupings for the generated CLAUDE.md snippet (`claude-md --generate`).
# Module-level so test_tool_registration_consistency can enumerate it: every
# tool the builder emits must appear here AND in _CANONICAL_TOOL_NAMES, or the
# meta-test fails listing the gap. Keeps a new tool from drifting across the
# registration surfaces (the recurring "added the tool in 4 of 5 places" trap).
_SNIPPET_TOOL_CATEGORIES: list[tuple[str, list[str]]] = [
    ("Indexing", ["index_repo", "index_folder", "summarize_repo", "index_file",
                  "index_dependency"]),
    ("Discovery", ["list_repos", "resolve_repo", "suggest_queries",
                   "get_repo_outline", "get_file_tree", "get_file_outline"]),
    ("Search & Retrieval", ["search_symbols", "get_symbol_source", "get_context_bundle",
                             "get_file_content", "search_text", "search_columns",
                             "get_ranked_context", "assemble_task_context"]),
    ("Relationships", ["find_importers", "find_references", "check_references",
                       "get_dependency_graph", "get_class_hierarchy",
                       "get_related_symbols", "get_call_hierarchy",
                       "find_implementations"]),
    ("Impact & Safety", ["get_blast_radius", "check_rename_safe", "check_delete_safe",
                          "check_edit_safe",
                          "get_impact_preview", "get_changed_symbols",
                          "plan_refactoring", "get_symbol_provenance",
                          "get_pr_risk_profile", "get_endpoint_impact"]),
    ("Architecture", ["get_dependency_cycles", "get_coupling_metrics",
                      "get_layer_violations", "get_extraction_candidates",
                      "get_cross_repo_map", "get_tectonic_map",
                      "get_signal_chains", "render_diagram",
                      "get_project_intel", "list_workspaces",
                      "get_group_contracts", "get_decorator_census",
                      "get_architecture_metrics"]),
    ("Quality & Metrics", ["get_symbol_complexity", "get_churn_rate",
                            "get_delivery_metrics", "get_parity_map", "get_hotspots",
                            "get_repo_health", "diff_health_radar",
                            "get_file_risk", "get_symbol_importance",
                            "get_repo_map", "find_similar_symbols",
                            "find_dead_code", "get_dead_code_v2",
                            "get_untested_symbols", "search_ast",
                            "winnow_symbols"]),
    ("Diffs & Embeddings", ["get_symbol_diff", "embed_repo"]),
    ("Session-Aware Routing", ["plan_turn", "get_session_context", "get_session_snapshot", "register_edit", "digest", "finalize_handoff"]),
    ("Utilities", ["get_session_stats", "analyze_perf", "tune_weights", "check_embedding_drift",
                    "invalidate_cache", "test_summarizer",
                    "audit_agent_config", "suggest_corrections", "get_watch_status"]),
    ("Runtime Trace Ingest & Analytics", [
        "import_runtime_signal", "get_runtime_coverage",
        "find_hot_paths", "find_unused_paths", "get_redaction_log",
    ]),
    ("Runtime Tier Switching", ["set_tool_tier", "announce_model"]),
    ("Self-Guide", ["jcodemunch_guide"]),
]

# --------------------------------------------------------------------------- #
# Tool profiles: tiered sets for controlling context budget.                   #
# core ⊂ standard ⊂ full.  Config key: tool_profile (default "full").         #
# --------------------------------------------------------------------------- #
_TOOL_TIER_CORE: frozenset[str] = frozenset({
    # Indexing
    "index_repo", "index_folder", "index_file",
    # Discovery
    "list_repos", "resolve_repo", "get_repo_outline",
    "get_file_tree", "get_file_outline",
    # Search & Retrieval
    "search_symbols", "get_symbol_source", "get_file_content",
    "search_text", "get_context_bundle", "get_ranked_context",
    "assemble_task_context",
    # Relationships
    "find_importers", "find_references",
})

_TOOL_TIER_STANDARD: frozenset[str] = _TOOL_TIER_CORE | frozenset({
    # Indexing extras
    "summarize_repo", "embed_repo", "index_dependency",
    "import_runtime_signal", "get_runtime_coverage", "find_hot_paths", "find_unused_paths",
    "get_redaction_log",
    # Discovery extras
    "suggest_queries", "search_columns",
    # Relationships
    "check_references", "get_dependency_graph",
    "get_class_hierarchy", "get_related_symbols", "get_call_hierarchy",
    # Impact & Safety
    "get_blast_radius", "check_rename_safe", "check_delete_safe", "check_edit_safe",
    "get_impact_preview", "get_changed_symbols", "get_symbol_diff",
    "get_symbol_provenance", "get_pr_risk_profile", "get_endpoint_impact",
    # Symbol navigation
    "find_implementations",
    # Quality & Metrics
    "get_symbol_complexity", "get_churn_rate", "get_delivery_metrics", "get_hotspots",
    "get_parity_map",
    "get_symbol_importance", "get_repo_map", "find_dead_code", "get_dead_code_v2",
    "get_untested_symbols", "find_similar_symbols",
    "get_repo_health", "search_ast", "winnow_symbols",
    # Architecture
    "get_dependency_cycles", "get_coupling_metrics", "get_layer_violations",
    "get_cross_repo_map", "get_group_contracts",
    "get_tectonic_map", "get_signal_chains", "get_decorator_census", "get_architecture_metrics",
    "render_diagram", "get_project_intel", "list_workspaces",
    # Utilities
    "invalidate_cache", "get_watch_status", "analyze_perf", "tune_weights", "check_embedding_drift",
    "suggest_corrections",
    # Canonical handoff (#374)
    "finalize_handoff",
    # Agent stand-up briefing
    "digest",
    # Health-radar diff
    "diff_health_radar",
    # Per-file risk (powers VS Code gutter)
    "get_file_risk",
})

# full = everything (no filter applied)

_PROFILE_TIERS: dict[str, frozenset[str] | None] = {
    "core": _TOOL_TIER_CORE,
    "standard": _TOOL_TIER_STANDARD,
    "full": None,  # None = no filtering
}

# Tools that survive tier filtering (always visible in core/standard tiers).
# jcodemunch_guide is included so a one-line CLAUDE.md keeps working at any tier.
_ALWAYS_PRESENT_TOOLS: frozenset[str] = frozenset({"set_tool_tier", "announce_model", "jcodemunch_guide"})

# Subset of _ALWAYS_PRESENT_TOOLS that ALSO survives disabled_tools. These are
# runtime tier controls — disabling them would lock the user out of switching
# tiers in-session. jcodemunch_guide is intentionally NOT in this set (issue
# #298): it's a documentation snippet, not a control surface, so users who
# explicitly list it in disabled_tools should be honored.
_UNDISABLEABLE_TOOLS: frozenset[str] = frozenset({"set_tool_tier", "announce_model"})

# --- The Counter: adaptive tool surface (front door) ----------------------- #
# order/menu/route collapse the whole tool surface to a 3-tool front door without
# removing any capability. See docs/prd-adaptive-tool-surface.md + counter.py.
from . import counter as _counter

_COUNTER_FRONT_DOOR: frozenset[str] = _counter.FRONT_DOOR

# Unfiltered tool catalog, captured by _build_tools_list before tier/surface
# filtering. Single source of truth for menu() and order()'s action allowlist,
# so the front door can surface/dispatch any action regardless of resident tier.
_RAW_CATALOG: "Optional[list]" = None


# The only two tool surfaces that exist. Anything else has always BEHAVED as
# "full" (only "counter" is ever special-cased); v1.108.260 makes the reported
# value agree with that instead of echoing whatever was typed.
VALID_TOOL_SURFACES = ("counter", "full")
_UNRECOGNIZED_SURFACES_LOGGED: set = set()


def _surface_resolution() -> tuple:
    """(effective, requested, recognized) for the active tool surface.

    v1.108.260 (#424 follow-up). `JCODEMUNCH_TOOL_SURFACE=countr` used to report
    itself back verbatim while silently serving the full 91-tool surface, because
    only "counter" is ever special-cased. Someone debugging "why didn't my token
    cost drop" then read a receipt that CONFIRMED their typo.

    ⚠ Fifth occurrence of the diagnostic-disagrees-with-the-runtime class (see
    .250 and .255). The reported value must be what is actually in force.

    ⚠ Resolving to "full" is not enough on its own: silently normalising hides
    the typo in the other direction. The requested value is carried alongside so
    the receipt can say the setting was REJECTED, not merely that something else
    is active.
    """
    env = os.environ.get("JCODEMUNCH_TOOL_SURFACE")
    if env:
        requested = env.strip().lower()
    else:
        requested = (config_module.get("tool_surface", "full") or "full").strip().lower()

    if requested in VALID_TOOL_SURFACES:
        return requested, requested, True

    if requested not in _UNRECOGNIZED_SURFACES_LOGGED:
        _UNRECOGNIZED_SURFACES_LOGGED.add(requested)
        logger.warning(
            "Unrecognized tool_surface %r; using 'full'. Valid values: %s. "
            "(Set via JCODEMUNCH_TOOL_SURFACE or the 'tool_surface' config key.)",
            requested, ", ".join(VALID_TOOL_SURFACES),
        )
    return "full", requested, False


def _effective_surface() -> str:
    """Active tool surface. 'counter' collapses list_tools to the front door;
    'full' (the default) preserves existing tiered behavior unchanged.
    Env JCODEMUNCH_TOOL_SURFACE wins over config 'tool_surface'.

    ⚠ Always one of VALID_TOOL_SURFACES. An unrecognized value resolves to
    "full", which is what it has always DONE; see `_surface_resolution` for why
    it used to be reported differently.
    """
    return _surface_resolution()[0]


def _counter_front_door_tools() -> list:
    """Tool definitions for order / menu / route."""
    return [
        Tool(
            name="order",
            description=(
                "Dispatch any jcodemunch action by name: order(action, args). The "
                "single-verb front door to the full tool catalog. Read-only by "
                "default — actions that change index/session state require "
                "allow_state_change=true, and execution/file-write verbs are refused. "
                "For exploration questions ('how does X work'), "
                "order('get_ranked_context', {repo, query, token_budget}) answers in "
                "ONE call — prefer it over chained search/outline/source hops; add "
                "compress=true to fit more symbols in the same budget. "
                "Call 'menu' to discover actions, or 'route' to pick one from a task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Name of the catalog action to run (e.g. 'search_symbols')."},
                    "args": {"type": "object", "description": "Arguments for that action, exactly as you'd pass them directly.", "default": {}},
                    "allow_state_change": {"type": "boolean", "description": "Opt in to dispatching an index/session state-changing action (e.g. index_repo).", "default": False},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="menu",
            description=(
                "Discover catalog actions without keeping the full tool catalog "
                "resident: menu(query?). Returns compact rows (action, summary, "
                "required args, state_changing). With no query, lists the catalog. "
                "Pair with 'order' to dispatch the chosen action."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional. Keywords describing what you want to do; ranks matching actions."},
                    "limit": {"type": "integer", "description": "Max actions to return.", "default": 25},
                },
            },
        ),
        Tool(
            name="route",
            description=(
                "Map a natural-language task to the best catalog action(s): "
                "route(task, repo?, execute?). Returns ranked recommendations with "
                "ready-to-run argument templates. With execute=true, dispatches the "
                "top recommendation and returns its result in the same call, "
                "collapsing discover-then-call into one round-trip. Recommends "
                "assemble_task_context / plan_turn for context-gathering intents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "What you're trying to do, in plain language."},
                    "repo": {"type": "string", "description": "Repository identifier (required to execute repo-scoped actions)."},
                    "execute": {"type": "boolean", "description": "If true, dispatch the top recommended action and return its result.", "default": False},
                    "model": {"type": "string", "description": "Optional active model id; piggybacks tier-switch like plan_turn(model=...)."},
                },
                "required": ["task"],
            },
        ),
    ]


def _raw_catalog_tools() -> list:
    """Return the unfiltered catalog, building it once on demand."""
    global _RAW_CATALOG
    if _RAW_CATALOG is None:
        _build_tools_list()  # populates _RAW_CATALOG as a side effect
    return _RAW_CATALOG or []


# Declared on the REGISTERED evidence producers only (#377 phase 2), so that
# passing it to a tool that cannot mint is surfaced as an ignored argument rather
# than silently accepted — the v1.108.175 contract doing its job. Kept as one
# string so the four producers cannot drift into describing it differently.
_RECEIPT_ARG_DESCRIPTION = (
    "Opt in to an immutable evidence receipt (jcodemunch.evidence/v1). Response "
    "carries only the id in _meta.receipts; read the body from munch://evidence/<id>. "
    "Binds the exact subject (symbol id, line range, content hash) and the snapshot "
    "it was measured against, so a handoff citing it attests what was retrieved "
    "rather than the whole file. Default false = unchanged response."
)

_DECLARED_ARG_KEYS: "Optional[dict]" = None


def _declared_arg_keys(name: str):
    """Declared inputSchema property names for a tool, or None if unknown.

    Built from the same catalog `list_tools` publishes, snapshotted BEFORE the
    compact-schemas strip, so the contract can never drift from what the tool
    actually accepts. None (not an empty set) when the tool or its schema is
    missing: an absent declaration is not evidence that a caller's key is wrong.
    """
    if _DECLARED_ARG_KEYS is None:
        _build_tools_list()  # populates the snapshot as a side effect
    return (_DECLARED_ARG_KEYS or {}).get(name)


def _catalog_rows() -> "list[dict]":
    """Menu-shaped rows for every real action (front door excluded)."""
    rows = []
    for t in _raw_catalog_tools():
        if t.name in _COUNTER_FRONT_DOOR:
            continue
        row = _counter.catalog_entry(t.name, t.description or "", t.inputSchema or {})
        row["_description"] = t.description or ""
        rows.append(row)
    return rows


def _catalog_names() -> set:
    return {t.name for t in _raw_catalog_tools() if t.name not in _COUNTER_FRONT_DOOR}


def _tool_surface_stats(top_n: int = 15) -> dict:
    """Schema token weight of the currently visible tool surface vs the raw catalog.

    Estimator matches the meter's scale (bytes/4) over the same serialization
    the schema-budget baseline uses ({name, description, inputSchema}, compact
    separators). Advisory receipt only — never blocks, nothing persisted.
    """
    import json as _json

    def _weight(tool) -> int:
        payload = _json.dumps(
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {},
            },
            separators=(",", ":"),
            default=str,
        )
        return max(1, len(payload.encode("utf-8")) // 4)

    visible = {t.name: _weight(t) for t in _build_tools_list()}
    catalog = {t.name: _weight(t) for t in _raw_catalog_tools()}
    visible_total = sum(visible.values())
    catalog_total = sum(catalog.values())
    heaviest = dict(sorted(visible.items(), key=lambda kv: -kv[1])[:top_n])
    _surface, _requested, _recognized = _surface_resolution()
    out = {
        "surface": _surface,
        "profile": _effective_profile(),
        "visible_tools": len(visible),
        "catalog_tools": len(catalog),
        "schema_tokens_visible": visible_total,
        "schema_tokens_catalog": catalog_total,
        "schema_tokens_avoided": max(0, catalog_total - visible_total),
        "heaviest_tools": heaviest,
        "estimator": "bytes/4",
    }
    # Omit-when-clean: a correct setting pays nothing. An unrecognized one is
    # named, because resolving to "full" without saying so hides the typo in the
    # other direction.
    if not _recognized:
        out["surface_requested"] = _requested
        out["surface_unrecognized"] = True
        out["surface_note"] = (
            f"tool_surface {_requested!r} is not recognized and was ignored; "
            f"'full' is in force. Valid values: {', '.join(VALID_TOOL_SURFACES)}."
        )
    return out


# --- Runtime session tier state -------------------------------------------- #
import contextvars
import hashlib
import threading
import uuid
import weakref
from typing import Hashable

# Tier overrides are keyed by MCP session identity so concurrent HTTP clients
# don't clobber each other. Stdio and tests have no active session; they land
# on the "__default__" sentinel, preserving pre-v1.61 single-session semantics.
#
# Earlier versions used an LRU-capped OrderedDict keyed by id(session). That
# had two bugs (audit findings F2 + F3):
#   F2: id() is reused after GC, so a freed session's tier could be inherited
#       by a freshly-allocated replacement at the same address.
#   F3: LRU eviction silently reset a live session's tier to config default.
# Both are fixed by keying on a per-session UUID tracked in a WeakKeyDict;
# entries disappear exactly when the session object is collected, and there
# is no cap to evict from.
_SESSION_TIER_DEFAULT_KEY: Hashable = "__default__"
_session_tier_overrides: dict[Hashable, str] = {}
_session_tier_lock = threading.Lock()

# Auth-principal fallback for stateless HTTP (MCP spec 2026-07-28 removes
# protocol sessions). Set ONLY by the streamable-http handler at session
# creation, before the session task is spawned — asyncio.create_task copies the
# request's context, so every handler in that session sees the value. Never set
# for SSE: concurrent SSE clients share the single JCODEMUNCH_HTTP_TOKEN, and
# keying them by principal would merge their per-session state.
_HTTP_PRINCIPAL: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "jcodemunch_http_principal", default=None
)


def _principal_from_authorization(auth_header: Optional[str]) -> Optional[str]:
    """Derive a stable, non-reversible state key from an Authorization header.

    Never stores or logs the raw credential. None when no header was sent.
    """
    if not auth_header:
        return None
    digest = hashlib.sha256(auth_header.encode("utf-8", "surrogatepass")).hexdigest()
    return f"principal-{digest[:16]}"


_no_principal_logged = False


def _note_no_principal_session() -> None:
    # Demand signal for a session-handle contract under stateless MCP; once per
    # process so a chatty client can't spam the log.
    global _no_principal_logged
    if _no_principal_logged:
        return
    _no_principal_logged = True
    logger.info(
        "HTTP session created with no Authorization header; once MCP transports "
        "go stateless, per-session state (tool tiers, budgets, session stats) "
        "will not persist for unauthenticated callers."
    )

# Maps a live session object → a stable uuid used as the dict key above.
# WeakKeyDictionary drops entries automatically when the session is freed,
# and the matching override entry is purged lazily via the finalizer below.
_session_uuid: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()


def _session_key() -> Hashable:
    """Return a stable hashable key for the active MCP session.

    Priority:
      1. `session.session_id` if the MCP library exposes one (HTTP transport).
      2. The hashed auth principal captured by the streamable-http handler —
         fires only when the transport issues no session id (stateless MCP,
         spec 2026-07-28), so authed callers keep durable state there.
      3. A per-process UUID tracked in a WeakKeyDictionary keyed by the
         session object. Survives for the lifetime of the session and
         disappears with it — no id() reuse after GC (F2), no LRU eviction
         required (F3).
      4. The default sentinel when there is no active session (stdio/tests).
    """
    session = _get_mcp_session()
    sid = getattr(session, "session_id", None) if session is not None else None
    if isinstance(sid, str) and sid:
        return sid
    principal = _HTTP_PRINCIPAL.get()
    if principal is not None:
        return principal
    if session is None:
        return _SESSION_TIER_DEFAULT_KEY
    try:
        existing = _session_uuid.get(session)
        if existing is not None:
            return existing
        new_uuid = uuid.uuid4().hex
        _session_uuid[session] = new_uuid
        # When the session is GC'd, remove its override entry too so the
        # dict doesn't grow unbounded with stale keys.
        weakref.finalize(session, _drop_override, new_uuid)
        return new_uuid
    except TypeError:
        # Session object isn't weakref-able — fall back to id(). Known to
        # be risky after GC but there's no safer hashable available here.
        return id(session)


def _drop_override(key: Hashable) -> None:
    with _session_tier_lock:
        _session_tier_overrides.pop(key, None)


def _set_session_tier(tier: str | None) -> None:
    """Atomically set (or clear, when tier is None) the active session's override."""
    key = _session_key()
    with _session_tier_lock:
        if tier is None:
            _session_tier_overrides.pop(key, None)
            return
        _session_tier_overrides[key] = tier


def _reset_session_tiers() -> None:
    """Clear every session's tier override. Test helper."""
    with _session_tier_lock:
        _session_tier_overrides.clear()
    _session_uuid.clear()


def _effective_profile() -> str:
    """Return the active tier, preferring the session override over config."""
    key = _session_key()
    with _session_tier_lock:
        override = _session_tier_overrides.get(key)
    if override is not None:
        return override
    return config_module.get("tool_profile", "full") or "full"


def _resolve_tier_bundle(profile: str) -> frozenset[str] | None:
    """Return the set of tool names allowed for the given profile.

    Reads from config['tool_tier_bundles'] first, falls back to baked-in
    _TOOL_TIER_CORE / _TOOL_TIER_STANDARD constants if the config key is
    missing or malformed. 'full' returns None (no filter).
    """
    if profile == "full":
        return None
    bundles = config_module.get("tool_tier_bundles") or {}
    if isinstance(bundles, dict) and isinstance(bundles.get(profile), list):
        return frozenset(bundles[profile])
    # Fallback to constants.
    return _PROFILE_TIERS.get(profile)


async def _emit_tools_list_changed() -> None:
    """Send notifications/tools/list_changed to the client, best-effort.

    No-op if the transport / SDK does not support it.
    """
    session = _get_mcp_session(server)
    if session is None:
        logger.debug("tools/list_changed skipped: no active MCP session")
        return

    send_fn = getattr(session, "send_tool_list_changed", None)
    if send_fn is None:
        logger.warning("tools/list_changed skipped: session has no send_tool_list_changed()")
        return

    try:
        maybe_awaitable = send_fn()
        if asyncio.iscoroutine(maybe_awaitable):
            await maybe_awaitable
    except (RuntimeError, TypeError, AttributeError) as exc:
        logger.warning("tools/list_changed notification failed: %s", exc, exc_info=True)


def _get_mcp_session(mcp_server: Server | None = None) -> Any | None:
    """Best-effort session lookup from an MCP server instance.

    Returns None when no request context/session is available.
    """
    srv = mcp_server if mcp_server is not None else globals().get("server")
    if srv is None:
        return None
    try:
        request_context = srv.request_context
    except (LookupError, AttributeError):
        return None
    if request_context is None:
        return None
    return getattr(request_context, "session", None)


def _note_adaptive_tiering_transport(transport: str) -> None:
    """Log an INFO line when adaptive_tiering is active under an HTTP transport.

    As of v1.61, tier overrides are session-keyed, so HTTP transports handle
    concurrent clients safely. The v1.60.1 refuse-to-start guard was removed.
    This hook stays as an observability breadcrumb.
    """
    if not config_module.get("adaptive_tiering", False):
        return
    logger.info(
        "adaptive_tiering active under transport=%s; tier overrides are session-keyed.",
        transport,
    )


def _log_startup_validation_warnings() -> None:
    """Emit WARNING logs for any bundle/disabled_tools overlap at startup."""
    from .tier_resolver import validate_bundle_disabled_overlap
    try:
        cfg = {
            "tool_tier_bundles": config_module.get("tool_tier_bundles") or {},
            "disabled_tools": config_module.get("disabled_tools") or [],
        }
        for msg in validate_bundle_disabled_overlap(cfg):
            logger.warning(msg)
    except Exception as exc:  # noqa: BLE001
        logger.debug("startup validation failed: %s", exc, exc_info=True)


async def _apply_model_announcement(model: str) -> dict:
    """Resolve model → tier, switch if changed, emit list_changed if changed.

    Gated by the adaptive_tiering config flag. When the flag is false
    (the default), this is a no-op: returns the current tier without
    switching. set_tool_tier is not affected by this flag because it is
    an explicit user invocation.
    """
    from .tier_resolver import resolve_model_to_tier
    adaptive = bool(config_module.get("adaptive_tiering", False))
    if not adaptive:
        return {
            "ok": True,
            "tier": _effective_profile(),
            "changed": False,
            "adaptive_tiering": False,
            "_meta": {
                "hint": (
                    "adaptive_tiering is disabled in config.jsonc — "
                    "model self-report accepted but tier was not "
                    "switched. Set adaptive_tiering: true to enable."
                )
            },
        }

    mp = config_module.get("model_tier_map") or {}
    tier, match_reason = resolve_model_to_tier(model, mp)
    prev = _effective_profile()
    changed = tier != prev
    res = {
        "ok": True,
        "tier": tier,
        "changed": changed,
        "match_reason": match_reason,
        "adaptive_tiering": True,
    }
    if match_reason == "unmatched_fallback":
        res.setdefault("_meta", {})["warning"] = (
            f"model {model!r} did not match any entry in model_tier_map; "
            f"falling back to 'full'. Add a pattern to model_tier_map to "
            f"route this model explicitly."
        )
    if changed:
        _set_session_tier(tier)
        await _emit_tools_list_changed()
    return res

# Parameters stripped from tool schemas when compact_schemas is enabled.
# These are advanced/rarely-used params that cost tokens every session but
# are used <5% of the time.  The underlying handler still accepts them.
#
# `receipt` (v1.108.183) is stripped from all four evidence producers for the
# same reason and with the same guarantee: the core_compact ceiling is 4000
# tokens and it sits at 3996, so a param declared on four core tools does not
# fit there at any description length. The dispatcher honors it regardless, and
# `_DECLARED_ARG_KEYS` is snapshotted before this strip runs, so a hidden-but-
# honored param is never reported as an ignored argument.
_COMPACT_STRIP_PARAMS: dict[str, set[str]] = {
    "search_symbols": {
        "debug", "fusion", "semantic", "semantic_only", "semantic_weight",
        "fuzzy", "fuzzy_threshold", "max_edit_distance", "sort_by", "fqn",
        "decorator", "token_budget", "receipt",
    },
    # Bounded-source mode is an advanced opt-in; the tool still accepts these
    # params under compact, they're just hidden from the schema to protect the
    # core_compact budget (the body is always callable with them).
    "get_symbol_source": {
        "source_start_line", "source_end_line", "max_source_lines",
        "max_source_bytes", "max_total_source_bytes", "receipt",
        # v1.108.227: `verify_against` joins them, same rule and same reason.
        # Externally-attested verification is an audit workflow, not a core
        # retrieval path — `verify` alone stays visible, and the tool still
        # honours `verify_against` when passed.
        #
        # ⚠ Measured while adding #402's `git_sha_rev`: core_compact had **4
        # tokens** of headroom, so ANY core-tier description gaining a clause
        # broke the ceiling. Shaving words to fit is the wrong instinct — it
        # makes the next person shave again. Removing an advanced param from
        # the minimal surface is the fix the budget was designed to take.
        "verify_against",
    },
    # v1.108.231: `degeneracy_cutoff` is an escape hatch for callers who want the
    # pre-.231 volume back, not a core retrieval control. get_dead_code_v2 is a
    # core-tier tool and core_compact sits at 3996 of 4000, so a new property on
    # its schema does not fit there at any description length — the same
    # measurement that moved `verify_against` here for #402. The handler honours
    # it regardless, and `_DECLARED_ARG_KEYS` is snapshotted before this strip
    # runs, so a hidden-but-honoured param is never reported as an ignored arg.
    "get_dead_code_v2": {"degeneracy_cutoff"},
    "get_context_bundle": {"budget_strategy"},
    "get_ranked_context": {"detail_level", "compress", "receipt"},
    "search_text": {"receipt"},
    "get_blast_radius": {"cross_repo", "max_depth"},
    "get_endpoint_impact": {"include_infra"},
    "index_dependency": {"ecosystem", "max_files"},
    "find_importers": {"cross_repo"},
    "get_dependency_graph": {"cross_repo"},
    # v1.108.269 (#429): `max_size` joins them on the same rule. It is an escape
    # hatch for a repo with one oversize file, not a routine indexing control —
    # and the response now NAMES the withheld files in `warnings`, so a caller
    # who needs it is told the param exists at the moment it becomes relevant.
    # Honoured under compact all the same; only the schema property is hidden.
    "index_repo": {"extra_ignore_patterns", "incremental", "max_size"},
    "index_folder": {"extra_ignore_patterns", "incremental", "max_size"},
}

# Params whose enum is demoted to a plain string filter under compact_schemas.
# The `language` enum is the full LANGUAGE_REGISTRY (~76 values) — ~200 tokens
# of mechanical names an agent already knows. Dropping the enum keeps the param
# fully usable as a free-string filter (the tool tolerates any language string)
# while reclaiming the tokens. Keyed by param name so every tool that exposes a
# `language` enum (search_symbols, search_ast, ...) benefits across all tiers.
_COMPACT_DEMOTE_ENUM_PARAMS: frozenset[str] = frozenset({"language"})

# Tools eligible for Agent Selector complexity scoring
_AGENT_SELECTOR_TOOLS = frozenset({
    "get_ranked_context", "get_context_bundle", "search_symbols",
    "search_text", "get_symbol_source", "plan_turn",
    "get_blast_radius", "get_impact_preview", "get_dependency_graph",
})

# Tools excluded from strict freshness mode (don't wait for reindex)
_EXCLUDED_FROM_STRICT = frozenset({
    "list_repos",
    "resolve_repo",
    "get_session_stats",
    "get_session_context",
    "get_session_snapshot",
    "test_summarizer",
    "index_repo",
    "index_folder",
    "index_file",
    "invalidate_cache",
    "analyze_perf",
    "tune_weights",
    "check_embedding_drift",
})


logger = logging.getLogger(__name__)


def _default_use_ai_summaries() -> bool:
    """Return whether AI summarization is enabled, as a bool.

    Collapses the tri-state config value ("auto", True, "true" → True;
    "false", False, "0", "no", "off" → False) into a simple gate.
    Note: _create_summarizer() reads the config directly to resolve
    the "auto" vs. explicit-provider distinction at summarization time.
    """
    raw = config_module.get("use_ai_summaries", "auto")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("false", "0", "no", "off")


def _load_index_paths_from_arg(paths_from: str) -> tuple[Optional[list], Optional[str]]:
    """Read explicit paths from a file or stdin for `jcodemunch-mcp index --paths-from`.

    Returns ``(paths, None)`` on success or ``(None, error_message)`` on failure.
    Filters out empty lines and ``# …`` comments. An empty list is treated as
    an error so the command doesn't silently fall through to a full-tree index.
    """
    from pathlib import Path as _Path
    try:
        if paths_from == "-":
            raw = sys.stdin.read()
        else:
            raw = _Path(paths_from).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"Cannot read --paths-from {paths_from!r}: {e}"
    out = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not out:
        return None, f"--paths-from {paths_from!r} contained no usable paths"
    return out, None


# ---------------------------------------------------------------------------
# Session state persistence (Feature 10: Session-Aware Routing)
# ---------------------------------------------------------------------------

_session_state_restored = False


def _restore_session_state() -> None:
    """Load and restore session state on server startup.
    
    Called from run_stdio_server / run_sse_server / run_streamable_http_server.
    Restores journal entries and search cache from previous session.
    """
    global _session_state_restored
    if _session_state_restored:
        return
    
    if not config_module.get("session_resume", False):
        return
    
    try:
        from .tools.session_state import get_session_state
        from .tools.session_journal import get_journal
        from .tools.search_symbols import _result_cache, _result_cache_lock
        from .storage import SQLiteIndexStore
        
        state = get_session_state()
        max_age = config_module.get("session_max_age_minutes", 30)
        
        loaded = state.load(max_age_minutes=max_age)
        if not loaded:
            logger.debug("No session state to restore")
            return
        
        # Restore journal
        journal = get_journal()
        count = state.restore_journal(journal, loaded)
        logger.info("Restored %d session journal entries", count)
        
        # Build current_indexes for cache restoration
        storage_path = os.environ.get("CODE_INDEX_PATH", "")
        store = SQLiteIndexStore(base_path=storage_path)
        current_indexes = {}
        try:
            repos = store.list_repos()
            for r in repos:
                # list_repos already returns indexed_at — no need to load full index
                repo_id = r.get("repo", f"{r.get('owner', '')}/{r.get('name', '')}")
                indexed_at = r.get("indexed_at", "")
                if indexed_at:
                    current_indexes[repo_id] = indexed_at
        except Exception:
            pass
        
        # Restore search cache
        with _result_cache_lock:
            count = state.restore_search_cache(_result_cache, loaded, current_indexes)
        logger.info("Restored %d search cache entries", count)
        
        _session_state_restored = True
        
    except Exception as e:
        logger.warning("Failed to restore session state: %s", e)


def _save_session_state() -> None:
    """Save session state on server shutdown.
    
    Registered with atexit for clean shutdown.
    """
    if not config_module.get("session_resume", False):
        return
    
    try:
        from .tools.session_state import get_session_state
        from .tools.session_journal import get_journal
        from .tools.search_symbols import _result_cache, _result_cache_lock
        
        state = get_session_state()
        journal = get_journal()
        max_queries = config_module.get("session_max_queries", 50)
        
        neg_log = journal.get_negative_evidence_log()
        with _result_cache_lock:
            state.save(journal, _result_cache, max_queries=max_queries,
                       negative_evidence_log=neg_log)
        
        logger.info("Saved session state")
        
    except Exception as e:
        logger.warning("Failed to save session state: %s", e)


# Register atexit handler for session state persistence
atexit.register(_save_session_state)


# ---------------------------------------------------------------------------
# Live journal persistence (#334) — feeds the out-of-process PreCompact hook.
#
# The hook (`jcodemunch-mcp hook-precompact`) runs in a separate process from
# this server, so it sees a fresh, empty SessionJournal. We persist a compact
# snapshot of the live journal to a small file the hook reads back. Writes are
# throttled (not every tool call) and best-effort. Disable with
# JCODEMUNCH_LIVE_JOURNAL=0.
# ---------------------------------------------------------------------------

_live_journal_lock = threading.Lock()
_live_journal_last_flush = 0.0
_LIVE_JOURNAL_MIN_INTERVAL_S = 2.0


def _live_journal_enabled() -> bool:
    val = os.environ.get("JCODEMUNCH_LIVE_JOURNAL", "").strip().lower()
    return val not in {"0", "false", "no", "off"}


def _maybe_flush_live_journal(journal) -> None:
    """Throttled, best-effort flush of the live journal to disk (#334)."""
    if not _live_journal_enabled():
        return
    global _live_journal_last_flush
    now = time.monotonic()
    with _live_journal_lock:
        if now - _live_journal_last_flush < _LIVE_JOURNAL_MIN_INTERVAL_S:
            return
        _live_journal_last_flush = now
    try:
        from .tools.session_state import save_live_journal
        save_live_journal(journal, base_path=os.environ.get("CODE_INDEX_PATH") or None)
    except Exception:
        logger.debug("live journal flush failed", exc_info=True)


def _cleanup_mermaid_temp_startup() -> None:
    """Clean stale mermaid viewer temp files from previous sessions."""
    if not config_module.get("render_diagram_viewer_enabled", False):
        return
    try:
        from .tools.mermaid_viewer import cleanup_temp_dir
        cleanup_temp_dir()
    except Exception as e:
        logger.debug("Mermaid temp startup cleanup failed: %s", e, exc_info=True)


def _cleanup_mermaid_temp_shutdown() -> None:
    """Clean mermaid viewer temp files only if viewer was used this session."""
    if not config_module.get("render_diagram_viewer_enabled", False):
        return
    try:
        from .tools.mermaid_viewer import cleanup_temp_dir, was_viewer_used
        if not was_viewer_used():
            return
        cleanup_temp_dir()
    except Exception as e:
        logger.debug("Mermaid temp shutdown cleanup failed: %s", e, exc_info=True)


# Startup: clean stale files from previous sessions.
_cleanup_mermaid_temp_startup()
# Shutdown: clean only if viewer was actually used this session.
atexit.register(_cleanup_mermaid_temp_shutdown)


def _parse_watcher_flag(value: Optional[str]) -> bool:
    """Parse the --watcher flag value.

    None = not provided (disabled).
    'true'/'1'/'yes' = enabled (const from nargs='?').
    'false'/'0'/'no' = explicitly disabled.
    """
    if value is None:
        return False
    return value.lower() not in ("0", "no", "false")


def _get_watcher_enabled(args) -> bool:
    """Determine if the watcher should be enabled for the serve subcommand.

    Precedence (highest to lowest):
      1. --watcher CLI flag
      2. config file "watch" key  (JCODEMUNCH_WATCH env var is a fallback for this key
         when it is absent from config.jsonc — handled by config._apply_env_var_fallback)
    """
    flag = getattr(args, "watcher", None)
    if flag is not None:
        return _parse_watcher_flag(flag)
    return config_module.get("watch", False)


_BOOL_TRUE = frozenset(("true", "1", "yes", "on"))
_BOOL_FALSE = frozenset(("false", "0", "no", "off"))


def _coerce_arguments(arguments: dict, schema: dict) -> dict:
    """Coerce stringified values to their expected types per JSON schema.

    Handles boolean ("true"/"false"), integer ("5"), and number ("3.14")
    without eval. Unknown or already-correct types are passed through unchanged.
    """
    props = schema.get("properties", {})
    if not props:
        return arguments
    result = {}
    for k, v in arguments.items():
        if k in props and isinstance(v, str):
            expected = props[k].get("type")
            if expected == "boolean":
                if v.lower() in _BOOL_TRUE:
                    v = True
                elif v.lower() in _BOOL_FALSE:
                    v = False
            elif expected == "integer":
                try:
                    v = int(v)
                except (ValueError, TypeError):
                    pass
            elif expected == "number":
                try:
                    v = float(v)
                except (ValueError, TypeError):
                    pass
        result[k] = v
    return result


_TOOL_SCHEMAS: dict[str, dict] | None = None


def _build_language_enum() -> list[str]:
    """Build language enum from config, falling back to all registry languages."""
    languages = config_module.get("languages")
    if languages is None:
        from .parser.languages import LANGUAGE_REGISTRY
        return sorted(LANGUAGE_REGISTRY.keys())
    return languages


async def _ensure_tool_schemas() -> dict[str, dict]:
    """Lazy-initialize the tool name → inputSchema lookup for type coercion.

    Uses our own list_tools() — no coupling to private MCP SDK internals.
    Populated once on the first tool call, then cached for the process lifetime.
    """
    global _TOOL_SCHEMAS
    if _TOOL_SCHEMAS is None:
        tools = await list_tools()
        _TOOL_SCHEMAS = {t.name: t.inputSchema for t in tools if t.inputSchema}
    return _TOOL_SCHEMAS


# Create server
server = Server("jcodemunch-mcp")


# Handshake watchdog: a stderr diagnostic that fires when the client never
# completes an MCP handshake / never calls a handler. Reproduces the
# Codex-CLI hang described in the v1.81.3 client report — under that bug,
# `uvx` chatter on stdout corrupted the first frame and the client sat
# silent for 5h+. This event is set on the first call into any MCP
# handler (list_tools / list_resources / list_prompts / get_prompt /
# call_tool); the watchdog in run_stdio_server prints a one-line hint to
# stderr if it stays unset past JCODEMUNCH_HANDSHAKE_TIMEOUT (default 5s).
_handshake_event: Optional[asyncio.Event] = None


def _signal_handshake() -> None:
    """Mark the handshake watchdog as satisfied. Idempotent and cheap."""
    ev = _handshake_event
    if ev is not None and not ev.is_set():
        ev.set()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    _signal_handshake()
    return _build_tools_list()


# --- MCP read-only annotations --------------------------------------------- #
# Claude Code's plan mode prompts for approval on every tool it cannot prove is
# read-only. jcm is read-only by charter, so annotate each tool with
# ToolAnnotations(readOnlyHint=...) and plan mode runs the query tools silently
# while still gating the handful that mutate index/session/config state.
#
# The write-set is derived from the authoritative counter.STATE_CHANGING_ACTIONS
# so it can't drift from source. Two things are added on top:
#   * order / route — the front door can dispatch a state-changing action, so
#     they are not read-only. (menu stays read-only.)
#   * _ANNOTATION_ONLY_WRITERS — dual-mode tools whose DEFAULT path is a pure read
#     but which can mutate under a specific argument. They must be readOnlyHint=
#     False (conservative — mislabeling a writer read-only is the harmful
#     direction, and this matches jdoc/jdata's write-set), yet they are
#     deliberately NOT in counter.STATE_CHANGING_ACTIONS: that set gates the
#     order() dispatcher's allow_state_change opt-in, and forcing it on the common
#     read path would break e.g. order("check_embedding_drift") drift reports. So
#     the annotation write-set and the order-gate write-set diverge by exactly
#     these dual-mode tools.
# (index_dependency lives in STATE_CHANGING_ACTIONS itself as of v1.108.104, so
# it no longer needs a special case here — the counter's order gate and these
# annotations otherwise derive from one list.)
_ANNOTATION_ONLY_WRITERS: frozenset[str] = frozenset({
    "check_embedding_drift",  # reports by default; force=true re-pins the canary
})

_NON_READONLY_TOOLS: frozenset[str] = _counter.STATE_CHANGING_ACTIONS | {
    "order",
    "route",
} | _ANNOTATION_ONLY_WRITERS

# Tools that CAN reach the network (all user-invoked, all README-disclosed):
# GitHub fetch, cloud summarizer (opt-in), or a cloud embedding provider.
# Everything else is annotated openWorldHint=False — a gating client can prove
# the suite's no-network claim per tool instead of taking the README's word.
# order/route can dispatch any catalog action, so they inherit True.
_OPEN_WORLD_TOOLS: frozenset[str] = frozenset({
    "index_repo",         # GitHub API fetch
    "index_folder",       # cloud summarizer when configured + opted in
    "index_file",         # cloud summarizer when configured + opted in
    "summarize_repo",     # cloud summarizer when configured + opted in
    "embed_repo",         # cloud embedding provider when configured
    "check_embedding_drift",  # re-embeds canaries via the provider
    "test_summarizer",    # probes the configured provider
    "install_pack",       # starter-pack download
    "order",              # front door — can dispatch the above
    "route",              # front door — can dispatch the above
})


def _apply_readonly_annotations(tools: list[Tool]) -> list[Tool]:
    """Attach ToolAnnotations(readOnlyHint=...) to any tool lacking annotations.

    Read tools (readOnlyHint=True) run silently in Claude Code plan mode; the
    write-set (_NON_READONLY_TOOLS) is marked readOnlyHint=False so those still
    prompt. Tools that already carry annotations are left untouched. Returns a
    new list; input Tool objects are copied (model_copy) rather than mutated.
    """
    annotated: list[Tool] = []
    for tool in tools:
        if tool.annotations is None:
            tool = tool.model_copy(
                update={
                    "annotations": ToolAnnotations(
                        readOnlyHint=tool.name not in _NON_READONLY_TOOLS,
                        openWorldHint=tool.name in _OPEN_WORLD_TOOLS,
                    )
                }
            )
        annotated.append(tool)
    return annotated


def _build_tools_list() -> list[Tool]:
    """Build the full tool list, applying config-driven filtering and overrides."""
    all_tools = [
        Tool(
            name="index_repo",
            description="Index a GitHub repository's source code. Fetches files, parses ASTs, extracts symbols, and saves to local storage. Set JCODEMUNCH_USE_AI_SUMMARIES=false to disable AI summaries globally. github.com URLs only.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub repository URL or owner/repo string"
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Use AI to generate symbol summaries. Supports Anthropic, Gemini, OpenAI-compatible endpoints, MiniMax, and GLM-5 via env vars. When false, uses docstrings or signature fallback.",
                        "default": True
                    },
                    "extra_ignore_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional gitignore-style patterns to exclude from indexing (merged with JCODEMUNCH_EXTRA_IGNORE_PATTERNS env var)"
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "When true and an existing index exists, only re-index changed files.",
                        "default": True
                    },
                    "max_size": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Per-file byte cap for this run, overriding config and the 512000-byte default. Files over the cap are skipped entirely and their symbols never enter the index; the response names them in `warnings`. Omit to use config / JCODEMUNCH_MAX_FILE_SIZE."
                    }
                },
                "required": ["url"]
            }
        ),
        Tool(
            name="index_folder",
            description="Index a local folder of source code. Response surfaces `discovery_skip_counts` and `no_symbols_files` for diagnosing missing files. Skips .gitignore and extra_ignore_patterns matches.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to local folder (absolute or relative; ~ expands)."
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Generate symbol summaries via AI. When false, falls back to docstrings or signature.",
                        "default": True
                    },
                    "extra_ignore_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional gitignore-style exclude patterns."
                    },
                    "follow_symlinks": {
                        "type": "boolean",
                        "description": "Include symlinked files. Symlinked directories are never followed.",
                        "default": False
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "When an existing index exists, only re-index changed files.",
                        "default": True
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional explicit paths (absolute or relative to `path`). When set, skips the directory walk; directories in the list are recursed. Walk-path validation applies."
                    },
                    "identity_mode": {
                        "type": "string",
                        "enum": ["config", "local", "git"],
                        "description": "Repo-identity strategy. `config` (default): respect existing index. `local`: path-keyed. `git`: git-root-keyed (monorepo subdir merging).",
                        "default": "config"
                    },
                    "max_size": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Per-file byte cap for this run, overriding config and the 512000-byte default. Files over the cap are skipped entirely and their symbols never enter the index; the response names them in `warnings`. Per-call only — for a repo with a permanently oversize file, set `max_file_size` in its .jcodemunch.jsonc instead. Omit to use config / JCODEMUNCH_MAX_FILE_SIZE."
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="summarize_repo",
            description=(
                "Re-run AI summarization on all symbols in an existing index. "
                "Use this when index_folder completed but AI summaries are missing — "
                "e.g., the background summarization thread was interrupted, AI was disabled "
                "at index time, or the summarizer provider wasn't configured yet. "
                "With force=true (recommended), clears all existing summaries and re-runs "
                "the full 3-tier pipeline (docstring → AI → signature fallback)."
            
                " Requires a configured summarizer provider; without one the pipeline falls back to docstrings and signatures."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or local/hash)"
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "If true, clear all existing summaries and re-summarize every symbol. "
                            "Required when index_folder already applied signature fallbacks. "
                            "If false, only process symbols with no summary at all."
                        ),
                        "default": False
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="index_file",
            description="Index a single file within an existing index. Surgical update after edits. The file must be under an already-indexed folder's source_root. Can also add new files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to index."
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Generate symbol summaries via AI. When false, falls back to docstrings or signature.",
                        "default": True
                    },
                    "context_providers": {
                        "type": "boolean",
                        "description": "Whether to run context providers",
                        "default": True
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="index_dependency",
            description=(
                "Resolve and index an INSTALLED third-party dependency of an "
                "already-indexed local repo — the version actually in "
                "node_modules or the repo's virtualenv site-packages, read "
                "from package metadata (no registry lookup, fully local). "
                "Copies a filtered snapshot into the index store and indexes "
                "it as its own queryable repo (version visible in the repo "
                "id), then reports what docs the package ships. Use when the "
                "agent needs ground truth for a library API instead of "
                "guessing from training data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Host repository identifier (must be locally indexed).",
                    },
                    "package": {
                        "type": "string",
                        "description": (
                            "npm package (supports @scope/name) or PyPI "
                            "distribution/import name, as installed."
                        ),
                    },
                    "ecosystem": {
                        "type": "string",
                        "enum": ["auto", "npm", "pypi"],
                        "description": (
                            "Where to resolve: 'auto' tries node_modules then "
                            "repo-local virtualenvs (.venv/venv/env)."
                        ),
                        "default": "auto",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Cap on code files copied into the snapshot (truncation is reported).",
                        "default": 2000,
                    },
                },
                "required": ["repo", "package"],
            },
        ),
        Tool(
            name="import_runtime_signal",
            description=(
                "Ingest a runtime trace file into the runtime_* tables for the target "
                "repo. source='otel' takes OTel JSON / JSON-Lines / .gz and maps spans "
                "via (file_path, line_no, function_name); source='sql_log' takes "
                "pg_stat_statements CSV or a generic SQL JSON-Lines log and maps queries "
                "via referenced tables (file-stem match) and dbt/SQLMesh column metadata; "
                "source='stack_log' takes a plain-text application log or JSON-Lines "
                "record set with Python / JVM / Node.js tracebacks and writes to both "
                "runtime_calls (severity-agnostic rollup) and runtime_stack_events "
                "(per-severity counts: error/warn/info). Returns {records, mapped, "
                "unmapped, redactions_fired, unmapped_reasons, evicted} plus source-"
                "specific fields (columns_recorded for sql_log; severity_counts and "
                "frames for stack_log). PII is redacted at the chokepoint by default. "
                "apm is reserved."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["otel", "sql_log", "stack_log", "apm"],
                        "description": "Trace source format. Phases 1+4+5 accept 'otel', 'sql_log', and 'stack_log'.",
                        "default": "otel",
                    },
                    "path": {
                        "type": "string",
                        "description": "Absolute filesystem path to the trace file",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/name) — defaults to the current directory's resolved repo",
                    },
                    "redact_enabled": {
                        "type": "boolean",
                        "description": "Override the runtime_redact_enabled config key. Disable ONLY for offline debugging on synthetic data.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="get_runtime_coverage",
            description=(
                "Runtime coverage histogram for a repo or a single file: count of "
                "indexed symbols with vs without runtime evidence, plus the diagnostic "
                "list of unmapped runtime spans (likely reflective dispatch the AST "
                "missed). Pairs with Phase 2's per-result _runtime_confidence stamping. "
                "Returns coverage_pct=0 with sources=[] when no traces have been ingested."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/name)"},
                    "file_path": {
                        "type": "string",
                        "description": "Optional repo-relative file path. When set, scopes the histogram to this file.",
                    },
                    "unmapped_limit": {
                        "type": "integer",
                        "description": "Cap on the unmapped_runtime list (default 50)",
                        "default": 50,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="find_hot_paths",
            description=(
                "Top-N symbols ranked by total runtime hit count across ingested traces, "
                "with per-symbol p50/p95 latency, sources contributing, and last_seen. "
                "Optionally filtered by a name substring. Pairs with get_blast_radius to "
                "answer 'is this PR touching code that runs 4M times/day?' Returns an "
                "empty results list when no traces have been ingested."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/name)"},
                    "query": {
                        "type": "string",
                        "description": "Optional case-insensitive substring filter on symbol name",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Cap on returned rows (default 20, max 200)",
                        "default": 20,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="find_unused_paths",
            description=(
                "Symbols with zero (or stale) runtime hits over the look-back window. "
                "Distinct from find_dead_code: this surfaces code that's reachable on "
                "paper but never executed — only possible to detect with runtime data. "
                "Excludes test files and entry-point filenames by default. Returns an "
                "empty results list when no traces have been ingested (refuses to flag "
                "every symbol as 'unused' against an empty runtime baseline)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/name)"},
                    "since_days": {
                        "type": "integer",
                        "description": "Look-back window in days (default 90)",
                        "default": 90,
                    },
                    "include_tests": {
                        "type": "boolean",
                        "description": "Include symbols in test files",
                        "default": False,
                    },
                    "include_entry_points": {
                        "type": "boolean",
                        "description": "Include symbols in entry-point filenames (main.py, wsgi.py, etc.)",
                        "default": False,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap on returned rows (default 200, max 1000)",
                        "default": 200,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_redaction_log",
            description=(
                "Per-pattern PII redaction counts from runtime_redaction_log. "
                "Operators run this to verify the redaction chokepoint is firing on "
                "production traffic — covers the OTel / SQL / stack ingest paths "
                "(file-based or HTTP live-ingest, Phase 6). Returns "
                "{patterns: [{source, pattern, count, last_redacted}], "
                "total_redactions, sources}. Empty patterns list = either no traffic "
                "yet, or JCODEMUNCH_RUNTIME_REDACT was disabled."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/name)"},
                    "source": {
                        "type": "string",
                        "enum": ["otel", "sql_log", "stack_log", "apm"],
                        "description": "Optional filter to a single source label",
                    },
                    "since_days": {
                        "type": "integer",
                        "description": "Lookback window for last_redacted filter (default 30)",
                        "default": 30,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="list_repos",
            description=(
                "List all indexed repositories. "
                "START HERE before using Grep/Read/search tools — check if the project is "
                "already indexed, then use search_symbols / get_symbol_source instead of "
                "native file reads. If jcodemunch tools appear as deferred in your tool list, "
                "call ToolSearch to load their schemas first."
                if config_module.get("discovery_hint", True)
                else "List all indexed repositories."
            
                " Lists only indexes under the active storage_path."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_watch_status",
            description=(
                "Report watch-all daemon coverage: every locally-indexed repo, "
                "each repo's staleness / reindex-in-progress state, and the "
                "OS-level service status. Call before relying on index freshness "
                "when you suspect files may have changed since the last index."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="resolve_repo",
            description="Resolve a filesystem path to its indexed repo identifier. O(1) lookup — faster than list_repos for finding a single repo. Accepts repo root, worktree, subdirectory, or file path. Pass an absolute path; a relative one resolves against the server's working directory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute filesystem path (repo root, worktree, subdirectory, or file)"
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="get_file_tree",
            description="Get the file tree of an indexed repository, optionally filtered by path prefix. Results are capped at max_files (default 500) to prevent token overflow; use path_prefix to scope large trees.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "Optional path prefix to filter (e.g., 'src/utils')",
                        "default": ""
                    },
                    "include_summaries": {
                        "type": "boolean",
                        "description": "Include file-level summaries in the tree nodes",
                        "default": False
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum number of files to return (default 500). When truncated, response includes total_file_count and a hint to use path_prefix.",
                        "default": 500
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_file_outline",
            description="Get all symbols (functions, classes, methods) in a file with full signatures (including parameter names) and summaries. Use signatures to review naming at parameter granularity without reading the full file. Pass repo and file_path (e.g. 'src/main.py'). Indexed symbols only, so an unparsed file returns empty.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file within the repository (e.g., 'src/main.py')"
                    },
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to query in batch mode. Returns a grouped results array."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_symbol_source",
            description="Get full source of one symbol (symbol_id → flat object) or many (symbol_ids[] → {symbols, errors}). Supports verify, context_lines, fqn (PHP FQN via PSR-4), and an optional bounded mode that caps returned source for large symbols/batches.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Single symbol ID — returns flat symbol object"
                    },
                    "symbol_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple symbol IDs — returns {symbols, errors}"
                    },
                    "verify": {
                        "type": "boolean",
                        "description": "Verify content hash matches stored hash (detects source drift)",
                        "default": False
                    },
                    "verify_against": {
                        "type": "string",
                        "enum": ["cache", "git_sha"],
                        "description": "Where to source the comparison target when verify=True. 'cache' (default) compares against the content_hash stored in the index — self-referential, only catches incoherent tamper of ~/.code-index/. 'git_sha' additionally compares the cached source against the file slice at the commit the index was built at — externally attested, catches divergence between the cache and the upstream source. Adds git_sha_verification and git_sha_rev fields to the response.",
                        "default": "cache"
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of lines before/after symbol to include for context",
                        "default": 0
                    },
                    "fqn": {
                        "type": "string",
                        "description": "PHP fully-qualified class name (e.g. 'App\\Models\\User'). Resolves to symbol_id via PSR-4. Alternative to symbol_id."
                    },
                    "source_start_line": {
                        "type": "integer",
                        "description": "Bounded mode: absolute file line (1-based, same frame as `line`/`end_line`) to start the returned source slice; clamped to the symbol body."
                    },
                    "source_end_line": {
                        "type": "integer",
                        "description": "Bounded mode: absolute file line (1-based, inclusive) to end the returned source slice; clamped to the symbol body."
                    },
                    "max_source_lines": {
                        "type": "integer",
                        "description": "Bounded mode: keep at most the first N lines of the (ranged) slice. Sets source_truncated + metadata when it shortens the body."
                    },
                    "max_source_bytes": {
                        "type": "integer",
                        "description": "Bounded mode: UTF-8-safe per-symbol byte cap on the returned source. Verify still hashes the full body."
                    },
                    "max_total_source_bytes": {
                        "type": "integer",
                        "description": "Bounded mode (batch): cap on total returned source bytes across all symbols. Oversized symbols come back partial (source_truncated) rather than dropped, preventing an N×per-symbol blowup."
                    },
                    "receipt": {
                        "type": "boolean",
                        "description": _RECEIPT_ARG_DESCRIPTION,
                        "default": False
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_file_content",
            description="Get cached source for a file, optionally sliced to a line range. Reads the indexed copy, not the working tree.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file within the repository (e.g., 'src/main.py')"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional 1-based start line (inclusive)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional 1-based end line (inclusive)"
                    }
                },
                "required": ["repo", "file_path"]
            }
        ),
        Tool(
            name="search_symbols",
            description="Search for symbols matching a query across the entire indexed repository. Returns matches with signatures and summaries. Searches the index, not the working tree.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (matches symbol names, signatures, summaries, docstrings)"
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional filter by symbol kind",
                        "enum": ["function", "class", "method", "constant", "type", "template", "import"]
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., 'src/**/*.py')"
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional filter by language",
                        "enum": _build_language_enum()
                    },
                    "decorator": {
                        "type": "string",
                        "description": "Optional filter: only return symbols with this decorator (case-insensitive substring match, e.g. 'route', 'property', 'Deprecated')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (ignored when token_budget is set)",
                        "default": 10
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Token budget cap. When set, results are sorted by score and greedily packed until the budget is exhausted, charging each row's actual payload size (compact rows ~15 tokens, so a budget can admit many rows). Overrides max_results — pass max_results without token_budget when row count matters. Reports token_budget, tokens_used, and tokens_remaining in _meta."
                    },
                    "detail_level": {
                        "type": "string",
                        "description": "Controls result verbosity. 'compact' returns id/name/kind/file/line only (~15 tokens each, best for broad discovery). 'standard' returns signatures and summaries (default). 'full' inlines source code, docstring, and end_line — equivalent to search + get_symbol in one call.",
                        "enum": ["compact", "standard", "full"],
                        "default": "standard"
                    },
                    "debug": {
                        "type": "boolean",
                        "description": "When true, each result includes a score_breakdown showing per-field scoring contributions (name_exact, name_contains, name_word_overlap, signature_phrase, signature_word_overlap, summary_phrase, summary_word_overlap, keywords, docstring_word_overlap). Also adds candidates_scored to _meta.",
                        "default": False
                    },
                    "fuzzy": {
                        "type": "boolean",
                        "description": "Enable fuzzy matching. When true, uses trigram overlap + Levenshtein distance as fallback when BM25 scores are low. Fuzzy results include match_type, fuzzy_similarity, and edit_distance fields.",
                        "default": False
                    },
                    "fuzzy_threshold": {
                        "type": "number",
                        "description": "Minimum Jaccard trigram similarity (0.0–1.0) for fuzzy candidates. Lower values surface more candidates. Default 0.4.",
                        "default": 0.4
                    },
                    "max_edit_distance": {
                        "type": "integer",
                        "description": "Maximum Levenshtein distance for direct name matching (catches typos). Default 2.",
                        "default": 2
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["relevance", "centrality", "combined"],
                        "description": "Ranking strategy. 'relevance' (default) = BM25 text match. 'centrality' = filter by query, rank by PageRank. 'combined' = BM25 + PageRank weighted.",
                        "default": "relevance"
                    },
                    "semantic": {
                        "type": "boolean",
                        "description": "Enable semantic (embedding-based) search. " + _PROVIDER_HINT + " When false (default) there is zero performance impact.",
                        "default": False
                    },
                    "semantic_weight": {
                        "type": "number",
                        "description": "Weight for semantic score in hybrid BM25+embedding ranking (0.0–1.0). BM25 receives 1-weight. Default 0.5. Set to 0.0 for identical results to pure BM25; set to 1.0 for pure semantic.",
                        "default": 0.5
                    },
                    "semantic_only": {
                        "type": "boolean",
                        "description": "Skip BM25 entirely and rank solely by embedding cosine similarity. Implies semantic=true.",
                        "default": False
                    },
                    "fusion": {
                        "type": "boolean",
                        "description": "Enable multi-signal fusion (Weighted Reciprocal Rank) across lexical, structural, similarity, and identity channels. Produces higher-quality ranking than linear score addition. When True, sort_by is ignored.",
                        "default": False
                    },
                    "fqn": {
                        "type": "string",
                        "description": "PHP fully-qualified class name (e.g. 'App\\Models\\User'). Resolves via PSR-4 and uses the class name as query. Alternative to query."
                    },
                    "receipt": {
                        "type": "boolean",
                        "description": _RECEIPT_ARG_DESCRIPTION,
                        "default": False
                    }
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="invalidate_cache",
            description="Delete the index and cached files for a repository. Forces a full re-index on next index_repo or index_folder call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="search_text",
            description="Full-text search across indexed file contents. Useful when symbol search misses (e.g., string literals, comments, config values). Supports regex (is_regex=true) and context lines around matches (context_lines=N, like grep -C). Searches the indexed copy, not the working tree.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Text to search for. Case-insensitive substring by default. Set is_regex=true for full regex (e.g. 'estimateToken|tokenEstimat|\\.length.*0\\.25'). Limits: 500 chars plain, 200 chars when is_regex=true. Split longer alternations into multiple calls."
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "When true, treat query as a Python regex (re.search, case-insensitive). Supports alternation (|), character classes, lookaheads, etc. Max 200 chars; nested quantifiers (e.g. '(a+)+') are rejected to prevent catastrophic backtracking.",
                        "default": False
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., '*.py')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return",
                        "default": 20
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context to include before and after each match (like grep -C N). Essential for understanding code around matches.",
                        "default": 0
                    },
                    "receipt": {
                        "type": "boolean",
                        "description": _RECEIPT_ARG_DESCRIPTION,
                        "default": False
                    }
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="get_repo_outline",
            description="Get a high-level overview of an indexed repository: directories, file counts, language breakdown, symbol counts. Lighter than get_file_tree. Directories only, not files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="find_importers",
            description="Find all files that import a given file. Answers 'what uses this file?'. has_importers=false on a result means that importer is itself unreachable (dead code chain). Supports dbt {{ ref() }} edges. Use file_paths for batch queries. Set cross_repo=true to also find importers in other indexed repos. Import edges only; textual uses are not reported.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "file_path": {"type": "string", "description": "Target file path within the repo (e.g. 'src/features/intake/IntakeService.js'). Use for single-file queries. Cannot be used together with file_paths."},
                    "file_paths": {"type": "array", "items": {"type": "string"}, "description": "List of target file paths for batch queries. Returns a results array. Cannot be used together with file_path."},
                    "max_results": {"type": "integer", "default": 50, "description": "Maximum results per file"},
                    "cross_repo": {"type": "boolean", "default": False, "description": "When true, also search other indexed repos for cross-repo importers (package-level scope). Default: false (or JCODEMUNCH_CROSS_REPO_DEFAULT env var). Only valid with singular file_path or a single-element file_paths batch; combined with a multi-file file_paths batch it returns an error (use singular calls for cross-repo evidence)."},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="find_references",
            description="Find all files that import or reference an identifier via the import graph. Answers 'where is this imported / re-exported?'. SCOPE: import sites + dbt `{{ ref() }}` edges + (when `include_call_chain=true`) symbols whose bodies textually mention the identifier. Does NOT exhaustively enumerate every call site across the codebase — for that, combine with search_text or use get_call_hierarchy on the resolved symbol_id. Use `identifiers` for batch queries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "identifier": {"type": "string", "description": "Symbol or module name to search for (e.g. 'bulkImport', 'IntakeService'). Use for single-identifier queries. Cannot be used together with identifiers."},
                    "identifiers": {"type": "array", "items": {"type": "string"}, "description": "List of symbol or module names to search for (batch mode). Returns a results array. Cannot be used together with identifier."},
                    "max_results": {"type": "integer", "default": 50, "description": "Maximum results"},
                    "include_call_chain": {
                        "type": "boolean",
                        "default": False,
                        "description": "When true (singular mode only), each reference entry includes calling_symbols: symbols in that file whose bodies mention the identifier. Default false.",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="check_references",
            description="Check if an identifier is referenced anywhere: imports + file content. Combines find_references and search_text into one call. Returns is_referenced (bool) for quick dead-code detection. Accepts multiple identifiers in one call via identifiers param. Content matches are capped at max_content_results (default 20), and a match inside a comment or string still counts as referenced.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "identifier": {"type": "string", "description": "Single identifier to check"},
                    "identifiers": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Multiple identifiers to check in one call. Returns grouped results.",
                    },
                    "search_content": {
                        "type": "boolean", "default": True,
                        "description": "Also search file contents (not just imports). Set false for fast import-only check.",
                    },
                    "max_content_results": {
                        "type": "integer", "default": 20,
                        "description": "Max files to return per identifier for content search.",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="search_columns",
            description="Search column metadata across indexed models. Works with any ecosystem provider that emits column data (dbt, SQLMesh, database catalogs, etc.). Returns model name, file path, column name, and description. Use instead of grep/search_text for column discovery — 77% fewer tokens. Covers only providers that emit column metadata into the index, and returns at most max_results (default 20).",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (matches column names and descriptions)"
                    },
                    "model_pattern": {
                        "type": "string",
                        "description": "Optional glob to filter by model name (e.g., 'fact_*', 'dim_provider')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 20
                    }
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="get_context_bundle",
            description=(
                "Get full source + imports for one or more symbols in one call. "
                "Multi-symbol bundles deduplicate shared imports. "
                "Set token_budget to cap response size; use budget_strategy to control what's kept. "
                "Supports fqn (PHP FQN via PSR-4) as alternative to symbol_id."
            
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Single symbol ID (backward-compatible). Use symbol_ids for multi-symbol bundles."
                    },
                    "symbol_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of symbol IDs for a multi-symbol bundle. Imports are deduplicated across symbols that share a file."
                    },
                    "include_callers": {
                        "type": "boolean",
                        "description": "When true, each symbol entry includes a 'callers' list of files that directly import its defining file.",
                        "default": False
                    },
                    "output_format": {
                        "type": "string",
                        "description": "'json' (default) or 'markdown' — markdown renders a paste-ready document with imports, docstrings, and source blocks.",
                        "enum": ["json", "markdown"],
                        "default": "json"
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Max tokens to return. When set, symbols are ranked and trimmed to fit. Uses budget_strategy to prioritize."
                    },
                    "budget_strategy": {
                        "type": "string",
                        "enum": ["most_relevant", "core_first", "compact"],
                        "description": (
                            "'most_relevant' (default) ranks by file centrality (import in-degree). "
                            "'core_first' keeps the primary symbol first, ranks rest by centrality. "
                            "'compact' strips source bodies — returns signatures only."
                        ),
                        "default": "most_relevant"
                    },
                    "include_budget_report": {
                        "type": "boolean",
                        "description": "When true, include a 'budget_report' field showing tokens used, symbols included/excluded, and strategy applied.",
                        "default": False
                    },
                    "fqn": {
                        "type": "string",
                        "description": "PHP fully-qualified class name (e.g. 'App\\Models\\User'). Resolves to symbol_id via PSR-4. Alternative to symbol_id."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_session_stats",
            description="Get token savings stats for the current MCP session. Returns tokens saved and cost avoided (this session and all-time), per-tool breakdown, session duration, and cumulative totals. Use to see how much jCodeMunch has saved you. Savings are modelled estimates from the committed benchmark artifacts (cited in savings_provenance), not per-call measurements.",
            inputSchema={
                "type": "object",
                "properties": {},
            }
        ),
        Tool(
            name="analyze_perf",
            description="Per-tool latency telemetry: p50/p95/max in ms, error rate, plus cache hit-rate by tool. Defaults to the in-memory session ring; pass window=1h|24h|7d|all to query persisted telemetry.db (requires perf_telemetry_enabled). Useful for finding slow tools, cold caches, and regressions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "enum": ["session", "1h", "24h", "7d", "all"],
                        "default": "session",
                        "description": "session = in-memory ring; others read telemetry.db.",
                    },
                    "top": {
                        "type": "integer",
                        "default": 20,
                        "description": "Cap on slowest tools to return.",
                    },
                    "tool": {
                        "type": "string",
                        "description": "Restrict the analysis to a single tool name.",
                    },
                    "compare_release": {
                        "type": "string",
                        "description": "Compare current session against a saved baseline at benchmarks/token_baselines/v{version}.json (e.g. \"1.74.0\"). Adds baseline_diff to the response with per-tool deltas in tokens_saved and latency.",
                    },
                    "ledger": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include ranking_ledger summary (per-repo and per-tool event counts, average confidence, identity hits, semantic usage). Reads telemetry.db ranking_events table populated since v1.78.0; requires perf_telemetry_enabled.",
                    },
                },
            }
        ),
        Tool(
            name="check_embedding_drift",
            description="Pin (or re-check) a 16-string canary against the active embedding provider. On first run with capture=True (or force=True), embeds CANARY_STRINGS and persists the vectors to ~/.code-index/embed_canary.json. Subsequent calls re-embed those strings and report cosine drift; alarm fires when max drift exceeds threshold (default 0.05 = cos sim < 0.95). Use after upgrading providers, when retrieval quality drops unexpectedly, or as a periodic background check. Requires an active embedding provider. The first run only captures the baseline; drift is reported from the second run onward.",
            inputSchema={
                "type": "object",
                "properties": {
                    "capture": {
                        "type": "boolean",
                        "default": False,
                        "description": "Pin a fresh canary instead of running the drift check. No-ops when a canary already exists unless force=True.",
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "Re-pin the canary before checking. Use after intentional provider/model upgrades.",
                    },
                    "threshold": {
                        "type": "number",
                        "default": 0.05,
                        "description": "Cosine-distance threshold above which the alarm fires (per-canary maximum, not mean).",
                    },
                },
            }
        ),
        Tool(
            name="tune_weights",
            description="Learn per-repo retrieval weights from the v1.78.0 ranking ledger. Computes confidence correlations for the semantic and identity-match channels and writes overrides to ~/.code-index/tuning.jsonc. search_symbols reads those overrides at query time when the caller doesn't pass an explicit semantic_weight. Learns from a recency window of the ledger (default 90 days) so stale events can't anchor the weights. Safe to re-run; idempotent for stable signal. Requires perf_telemetry_enabled and the ranking ledger it writes; without that history there is nothing to learn from.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Limit tuning to a single repo. Default: every repo present in the ledger.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Compute proposed deltas without writing tuning.jsonc.",
                    },
                    "min_events": {
                        "type": "integer",
                        "default": 50,
                        "description": "Skip repos with fewer ledger events than this (defends against overfitting on small samples).",
                    },
                    "explain": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include per-signal correlations (mean confidence with/without semantic and identity channels) in the response.",
                    },
                    "max_age_days": {
                        "type": "integer",
                        "default": 90,
                        "description": "Only learn from ledger events newer than this many days. Keeps stale events from anchoring weights to an outdated query distribution. 0 = lifetime ledger.",
                    },
                },
            }
        ),
        Tool(
            name="get_session_context",
            description="Get the current session context — files accessed, searches performed, and edits registered during this MCP session. Use to avoid re-reading the same files. Truncated to max_files (default 50) and max_queries (default 20), and covers this server process only.",
            inputSchema={
                "type": "object",
                "properties": {
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum number of files to return in files_accessed.",
                        "default": 50,
                    },
                    "max_queries": {
                        "type": "integer",
                        "description": "Maximum number of queries to return in recent_searches.",
                        "default": 20,
                    },
                },
            }
        ),
        Tool(
            name="get_session_snapshot",
            description="Get a compact session snapshot for context continuity. Returns a ~200 token markdown summary of files explored, edits made, searches performed, and dead ends. Designed for injection after context compaction to restore session orientation. Truncated to max_files (10), max_searches (5) and max_edits (10); it is a summary, not a full session log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "max_files": {
                        "type": "integer",
                        "default": 10,
                        "description": "Maximum focus files to include.",
                    },
                    "max_searches": {
                        "type": "integer",
                        "default": 5,
                        "description": "Maximum key searches to include.",
                    },
                    "max_edits": {
                        "type": "integer",
                        "default": 10,
                        "description": "Maximum edited files to include.",
                    },
                    "include_negative_evidence": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include dead-end searches (negative evidence) in snapshot.",
                    },
                },
            },
        ),
        Tool(
            name="get_file_risk",
            description=(
                "Per-symbol composite risk for one file. For each function or "
                "method, returns a 0-100 composite score (higher = healthier; "
                "lower = riskier) plus per-axis sub-scores (complexity, exposure, "
                "churn, test_gap). Powers the VS Code risk-density gutter. "
                "complexity is per-symbol (cyclomatic from the index); the other "
                "three axes are file-level (shared across all symbols in the file) "
                "because per-symbol caller-count needs find_references per symbol "
                "and would be too slow for save-time refresh."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repo identifier (owner/name, full id, or bare display name).",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file within the indexed repo.",
                    },
                },
                "required": ["repo", "file_path"],
            },
        ),
        Tool(
            name="diff_health_radar",
            description=(
                "Compare two health-radar payloads (from get_repo_health.radar) "
                "and return axis-by-axis deltas, composite delta, grade movement, "
                "and a one-line verdict. Pure data transform — no index access, "
                "no I/O. Designed for PR-time diff-grade reports: run "
                "get_repo_health on the base branch, run it on the PR branch, "
                "pass both radar payloads here. Returns regressions/improvements "
                "lists for axes that moved more than 3 points."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "baseline": {
                        "type": "object",
                        "description": "Radar payload from baseline (e.g. base branch). The `radar` field of a get_repo_health response.",
                    },
                    "current": {
                        "type": "object",
                        "description": "Radar payload from current (e.g. PR branch). The `radar` field of a get_repo_health response.",
                    },
                },
                "required": ["baseline", "current"],
            },
        ),
        Tool(
            name="finalize_handoff",
            description=(
                "Finalize one canonical Markdown handoff for a completed repository "
                "audit/analysis (jcodemunch.handoff/v1). The server assembles YOUR "
                "sections deterministically, validates every evidence_refs entry "
                "against what this session actually retrieved (symbol ids or file "
                "paths served by search_symbols / get_ranked_context — unknown refs "
                "fail closed), persists the result session-scoped, and returns a "
                "compact receipt {handoff_id, resource_uri, sha256, length, "
                "canonical:true}. Read the immutable body via the "
                "munch://handoff/<id> resource; repeated reads are byte-identical. "
                "Appendices are included exactly once; no character limit; never "
                "writes to the repository."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier the handoff is about.",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task/question this handoff answers (becomes the title).",
                    },
                    "sections": {
                        "type": "array",
                        "description": "Ordered report sections, each {heading, content} (markdown). The caller authors these; the server only assembles. Optional per-section claims[] bind evidence to an individual claim instead of one global list (handoff/v2).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {"type": "string"},
                                "claims": {
                                    "type": "array",
                                    "description": "Optional caller-authored claims, each {id, statement, evidence_refs, classification?}. Ids must be unique across the handoff; each claim's refs are attested separately and rendered beside the claim.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "statement": {"type": "string"},
                                            "evidence_refs": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "classification": {"type": "string"},
                                        },
                                        "required": ["id", "statement", "evidence_refs"],
                                    },
                                },
                            },
                            "required": ["heading"],
                        },
                    },
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Symbol ids or file paths retrieved this session; validated against the session retrieval record.",
                    },
                    "profile": {
                        "type": "string",
                        "default": "general",
                        "description": "Handoff profile label (e.g. source_audit).",
                    },
                    "appendices": {
                        "type": "array",
                        "description": "Optional named appendices, each {name, content, content_type?}; names must be unique.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "content": {"type": "string"},
                                "content_type": {"type": "string"},
                            },
                            "required": ["name", "content"],
                        },
                    },
                },
                "required": ["repo", "task", "sections", "evidence_refs"],
            },
        ),
        Tool(
            name="digest",
            description=(
                "Agent stand-up briefing for a repo. Returns a tight (~200 token) "
                "markdown digest of (a) what changed since the agent's last session "
                "(by tracking git HEAD between calls), (b) the current risk surface "
                "(top hotspots by complexity × churn), and (c) dead-code candidates. "
                "Each item references symbol_ids the agent can immediately query "
                "with get_symbol_source / get_call_hierarchy / check_references. "
                "Designed for session-start context injection: call once when you "
                "open a repo, get oriented to the load-bearing changes without cold "
                "exploration."
            
                " Truncated to max_changed_files (5), max_hotspots (3) and max_dead_code (3), and the change section needs local git history."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repo identifier (owner/name, full id, or bare display name).",
                    },
                    "since_sha": {
                        "type": "string",
                        "description": "Override the last-seen SHA (for re-running a delta).",
                    },
                    "max_changed_files": {
                        "type": "integer",
                        "default": 5,
                        "description": "Cap on changed-files list (default 5).",
                    },
                    "max_hotspots": {
                        "type": "integer",
                        "default": 3,
                        "description": "Cap on hotspot list (default 3).",
                    },
                    "max_dead_code": {
                        "type": "integer",
                        "default": 3,
                        "description": "Cap on dead-code candidates (default 3).",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="plan_turn",
            description="Plan the next turn by analyzing query against the codebase. Returns confidence level (high/medium/low), recommended symbols/files, and guidance. Use as opening move for any task. Recommends at most max_recommended symbols (default 5) and ranks only what the index holds.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier.",
                    },
                    "query": {
                        "type": "string",
                        "description": "What you're looking for (task description or symbol name).",
                    },
                    "max_recommended": {
                        "type": "integer",
                        "description": "Maximum number of symbols to recommend.",
                        "default": 5,
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Optional. Your active model identifier (e.g. 'claude-haiku-4-5'). "
                            "When supplied and adaptive_tiering is enabled, plan_turn invokes "
                            "the tier-switch logic as a side effect — the exposed tool list is "
                            "narrowed to the tier mapped to this model via config.jsonc:"
                            "model_tier_map. Prefer this form over calling announce_model "
                            "separately — it adds zero extra requests."
                        ),
                    },
                },
                "required": ["repo", "query"],
            }
        ),
        Tool(
            name="register_edit",
            description="Register file edits to invalidate caches. Call after editing files to clear BM25 cache and search result cache for the repo. Clears caches only. It does not re-index unless reindex=true, so search results stay stale until you do.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier.",
                    },
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths that were edited.",
                    },
                    "reindex": {
                        "type": "boolean",
                        "description": "If True, also reindex the files.",
                        "default": False,
                    },
                },
                "required": ["repo", "file_paths"],
            }
        ),
        Tool(
            name="test_summarizer",
            description=(
                "Diagnostic probe: send one request to the configured AI summarizer and report status, provider, timing, and any error detail. Call it to confirm summarization is wired up before indexing a large repo. It checks connectivity only; a healthy probe says nothing about summary quality. Disabled in the shipped default config, so enable it before calling."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Slow-response threshold in ms.",
                        "default": 15000,
                    },
                },
            },
        ),
        Tool(
            name="audit_agent_config",
            description=(
                "Audit agent configuration files (CLAUDE.md, .cursorrules, copilot-instructions.md, etc.) "
                "for token waste. Reports per-file token cost, stale symbol references, dead file paths, "
                "redundancy between global and project configs, bloat patterns, and scope leaks. "
                "Cross-references against the jcodemunch index to catch references to renamed or deleted "
                "symbols and files that no other linter can detect."
            
                " Reports findings only; it never edits a config file. Stale-reference detection needs the repo indexed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": (
                            "Repository identifier for cross-referencing symbols and files. "
                            "If omitted, skips stale-reference and dead-path checks."
                        ),
                    },
                    "project_path": {
                        "type": "string",
                        "description": "Project directory to scan for config files. Defaults to cwd.",
                    },
                },
            },
        ),
        Tool(
            name="suggest_corrections",
            description=(
                "Mine the ranking telemetry ledger for retrieval regret (re-query churn, "
                "low confidence, thin/ambiguous results, stale-at-query, vocabulary gaps) "
                "and return a prioritized, explainable set of SUGGESTED corrections: "
                "CLAUDE.md routing/glossary lines (as unified-diff previews), index-freshness "
                "hints, stale-config findings, and a dry-run ranking-weight proposal. "
                "Read-only by charter — it never writes a user file; applying a patch is your "
                "keystroke. Requires perf_telemetry_enabled; returns an honest hint when off."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository whose retrieval ledger to analyze.",
                    },
                    "project_path": {
                        "type": "string",
                        "description": "Project directory holding the config files to target. Defaults to cwd.",
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "Rolling window of ledger history to mine (default 30).",
                        "default": 30,
                    },
                    "all_time": {
                        "type": "boolean",
                        "description": "Ignore the window and analyze the full ledger.",
                        "default": False,
                    },
                    "apply_weights": {
                        "type": "boolean",
                        "description": "Persist the ranking-weight proposal to the tuning.jsonc sidecar (NOT user source). User files are never written regardless.",
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="get_dependency_graph",
            description="Get the file-level dependency graph for a given file. Traverses import relationships up to 3 hops. Use to understand what a file depends on ('imports'), what depends on it ('importers'), or both. Prerequisite for blast radius analysis. Set cross_repo=true to include cross-repository edges.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "file": {
                        "type": "string",
                        "description": "File path within the repo (e.g. 'src/server.py')"
                    },
                    "direction": {
                        "type": "string",
                        "description": "'imports' (files this file depends on), 'importers' (files that depend on this file), or 'both'",
                        "enum": ["imports", "importers", "both"],
                        "default": "imports"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Number of hops to traverse (1–3)",
                        "default": 1
                    },
                    "cross_repo": {
                        "type": "boolean",
                        "description": "When true, include cross-repo edges (imports that resolve to packages in other indexed repos). Default: false.",
                        "default": False,
                    },
                },
                "required": ["repo", "file"]
            }
        ),
        Tool(
            name="get_symbol_diff",
            description="Diff symbol sets between two indexed snapshots. Shows added, removed, and changed symbols. Branch workflow: index branch A as repo-main, index branch B as repo-feature, then diff. Compares by (name, kind), so a renamed symbol appears as one removal plus one addition, not a rename.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_a": {"type": "string", "description": "First repo identifier (the 'before' snapshot)"},
                    "repo_b": {"type": "string", "description": "Second repo identifier (the 'after' snapshot)"},
                },
                "required": ["repo_a", "repo_b"],
            },
        ),
        Tool(
            name="get_class_hierarchy",
            description="Get the full inheritance hierarchy for a class: ancestors (base classes via extends/implements) and descendants (subclasses/implementors). Works across Python, Java, TypeScript, C#, and any language where class signatures contain 'extends' or 'implements'. Bases are resolved from indexed signature text, so a dynamically assigned or generated base class is not found.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "class_name": {"type": "string", "description": "Name of the class to analyse"},
                },
                "required": ["repo", "class_name"],
            },
        ),
        Tool(
            name="get_related_symbols",
            description="Find symbols related to a given symbol using heuristic clustering: same-file co-location (weight 3), shared importers (weight 1.5), and name-token overlap (weight 0.5/token). Useful for discovering what else to read when exploring an unfamiliar codebase.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "symbol_id": {"type": "string", "description": "ID of the symbol to find relatives for"},
                    "max_results": {"type": "integer", "description": "Maximum results (default 10, max 50)", "default": 10},
                },
                "required": ["repo", "symbol_id"],
            },
        ),
        Tool(
            name="suggest_queries",
            description="Suggest search queries, entry-point files, and index stats. Good first call on an unfamiliar repo — surfaces most-imported files, top keywords, and ready-to-run example queries. Suggestions come from index statistics rather than your task, so treat them as starting points.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_blast_radius",
            description="Find all files affected by changing a symbol. Returns confirmed files (import + name match) and potential files (import only, e.g. wildcard). Use before renaming or deleting a symbol. Set cross_repo=true to also find consumers in other indexed repos. Set include_source=true to get source snippets at each reference site (fix-ready context in one call). For automated edit plans, use plan_refactoring instead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name or ID to analyse (e.g. 'calculateScore' or a full symbol ID)"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Import hops to traverse (1 = direct importers only, max 3). Default 1.",
                        "default": 1
                    },
                    "include_depth_scores": {
                        "type": "boolean",
                        "description": "When true, adds impact_by_depth (files grouped by hop distance) and per-depth risk scores. overall_risk_score and direct_dependents_count are always included. Default false.",
                        "default": False
                    },
                    "cross_repo": {
                        "type": "boolean",
                        "description": "When true, also find files in other indexed repos that consume this repo's package. Default: false.",
                        "default": False,
                    },
                    "call_depth": {
                        "type": "integer",
                        "description": "When > 0, also find symbols that *call* this symbol (call-level analysis). Returns a callers list alongside the import-level confirmed/potential. Max 3. Default 0 (disabled).",
                        "default": 0,
                    },
                    "fqn": {
                        "type": "string",
                        "description": "PHP fully-qualified class name (e.g. 'App\\Models\\User'). Resolves to symbol via PSR-4. Alternative to symbol."
                    },
                    "decorator_filter": {
                        "type": "string",
                        "description": "Optional: filter confirmed results to only those containing symbols with this decorator (case-insensitive substring match)"
                    },
                    "include_source": {
                        "type": "boolean",
                        "description": "When true, each confirmed file includes source_snippets (lines referencing the symbol) and symbols_in_file (nearby symbol signatures). Use for fix-ready context without extra tool calls. Default false.",
                        "default": False,
                    },
                    "source_budget": {
                        "type": "integer",
                        "description": "Max tokens for source snippets across all files (default 8000). Files are prioritized by reference count.",
                        "default": 8000,
                    },
                    "include_decisions": {
                        "type": "boolean",
                        "description": "When true, attach a read-only 'decisions' block: decision-bearing commits (revert/perf/refactor/rename/bugfix) mined from the git history of the focal symbol's file and the confirmed affected files, plus a volatility read ('3 reverts + 2 perf rewrites in 180d — review before changing'). Surfaced from the commit record; nothing is persisted. Default false (spends a few git-log calls).",
                        "default": False,
                    },
                },
                "required": ["repo", "symbol"]
            }
        ),
        Tool(
            name="get_call_hierarchy",
            description=(
                "Return incoming callers and outgoing callees for a symbol, N levels deep. "
                "Uses AST-derived call detection: callers = symbols in importing files that "
                "mention this name; callees = imported symbols mentioned in this symbol's body. "
                "Useful for understanding how a symbol fits into the call graph before refactoring. "
                "For a 'what breaks if I delete this?' answer, use get_impact_preview instead."
            
                " Detection is name-based, so same-name symbols in different modules can merge and dynamic dispatch is missed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Symbol name or full ID to analyse. Use search_symbols to find IDs."
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["callers", "callees", "both"],
                        "description": "'callers' = who calls this symbol; 'callees' = what this symbol calls; 'both' (default).",
                        "default": "both",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Maximum hops to traverse (1–5). Default 3.",
                        "default": 3,
                    },
                },
                "required": ["repo", "symbol_id"],
            },
        ),
        Tool(
            name="get_impact_preview",
            description=(
                "Show what breaks if a symbol is removed or renamed. "
                "Walks the call graph transitively to find every symbol that calls this one, "
                "returning affected symbols grouped by file with call-chain paths. "
                "Use this before deleting or renaming a symbol to understand full impact. "
                "For a structured caller/callee tree, use get_call_hierarchy instead."
            
                " Walks the same name-matched call graph, so a caller reached only by dynamic dispatch or reflection is missing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Symbol name or full ID to analyse. Use search_symbols to find IDs."
                    },
                    "include_decisions": {
                        "type": "boolean",
                        "description": "When true, attach a read-only 'decisions' block: decision-bearing commits (revert/perf/refactor/rename/bugfix) mined from the git history of the focal symbol's file and the impacted files, plus a volatility read. Surfaced from the commit record; nothing is persisted. Default false (spends a few git-log calls).",
                        "default": False,
                    },
                },
                "required": ["repo", "symbol_id"],
            },
        ),
        Tool(
            name="get_symbol_provenance",
            description=(
                "Trace the complete authorship lineage and evolution narrative of a symbol "
                "through git history. Returns every commit that touched the symbol (or its file), "
                "classified into semantic categories (creation, bugfix, refactor, feature, perf, "
                "rename, revert, etc.) with extracted commit intent. Includes a human-readable "
                "narrative summarising who created it, why, how it evolved, and how volatile it is. "
                "Use before refactoring unfamiliar code to understand the 'why' behind it. "
                "Requires a locally indexed repo (index_folder)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name or full ID as returned by search_symbols.",
                    },
                    "max_commits": {
                        "type": "integer",
                        "description": "Maximum commits to analyse (default 25, max 100).",
                        "default": 25,
                    },
                },
                "required": ["repo", "symbol"],
            },
        ),
        Tool(
            name="get_pr_risk_profile",
            description=(
                "Produce a unified risk assessment for all changes between two git refs (branch, PR, "
                "or SHA range). Fuses five signals — blast radius, complexity, churn, test gaps, "
                "and change volume — into a single composite risk_score (0.0–1.0) with actionable "
                "recommendations. Returns the top-5 riskiest changed symbols, untested symbols, "
                "and per-signal breakdowns. Designed for CI gating and code review workflows. "
                "Requires a locally indexed repo (index_folder)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "base_ref": {
                        "type": "string",
                        "description": "Base SHA/ref to compare from. Defaults to the SHA stored at index time.",
                    },
                    "head_ref": {
                        "type": "string",
                        "description": "Head SHA/ref to compare to (default 'HEAD').",
                        "default": "HEAD",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Churn look-back window in days (default 90).",
                        "default": 90,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_dependency_cycles",
            description=(
                "Detect circular import chains in a repository. "
                "Returns every strongly-connected component (set of files that mutually import "
                "each other, directly or transitively). Run this to identify architectural "
                "problems before a refactor, or to understand why a module is hard to test in isolation."
            
                " Detects cycles in the file-level import graph only; it says nothing about call-level or runtime cycles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_coupling_metrics",
            description=(
                "Return afferent coupling (Ca), efferent coupling (Ce), and instability score "
                "for a file/module. Ca = files that import this module (dependents). "
                "Ce = files this module imports (dependencies). "
                "Instability I = Ce/(Ca+Ce): 0 = stable, 1 = unstable. "
                "Use to identify fragile modules and guide refactoring priorities."
            
                " Counts import edges only, so a module coupled through configuration, strings, or dependency injection reads as stable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "module_path": {
                        "type": "string",
                        "description": "File path within the repo (e.g. 'src/utils.py')"
                    },
                },
                "required": ["repo", "module_path"],
            },
        ),
        Tool(
            name="get_layer_violations",
            description=(
                "Check whether imports respect declared architectural layer boundaries. "
                "Reports every import that crosses a forbidden layer boundary. "
                "Layer rules can be passed directly or defined in .jcodemunch.jsonc under "
                "'architecture.layers'. Use to enforce clean architecture and detect "
                "dependency-direction violations (e.g. API layer importing DB layer directly)."
            
                " Files that match no declared layer are skipped, so coverage depends on your layer rules."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "rules": {
                        "type": "array",
                        "description": (
                            "Layer definitions. Each entry: {name, paths: [...], may_not_import: [...]}. "
                            "If omitted, reads from .jcodemunch.jsonc architecture.layers."
                        ),
                        "items": {"type": "object"},
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="check_rename_safe",
            description=(
                "Check whether renaming a symbol to a new name would cause name collisions. "
                "Scans the symbol's own file and every file that imports it, "
                "looking for an existing symbol with the proposed new name. "
                "Returns safe=true when no collisions are found. "
                "Run this before any rename/refactor to avoid silent breakage. "
                "For a full rename plan with edits, use plan_refactoring."
            
                " Scoped to the symbol's own file and its importers; a collision in a file that uses the name without importing it is not detected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": (
                            "Symbol ID to rename (e.g. 'src/utils.py::helper#function'). "
                            "Bare name accepted when unambiguous."
                        ),
                    },
                    "new_name": {
                        "type": "string",
                        "description": "Proposed new symbol name (not a full ID, just the name).",
                    },
                },
                "required": ["repo", "symbol_id", "new_name"],
            },
        ),
        Tool(
            name="check_delete_safe",
            description=(
                "Composite preflight: can this symbol be deleted safely? Combines find_importers "
                "(cross-repo), check_references, find_dead_code confidence, runtime evidence "
                "(Phase 7 traces when available), and entry-point heuristics into a single verdict + "
                "one-line recommended_action. Verdict tiers: safe_to_delete / test_coverage_only / "
                "internal_only / internal_uses_blocking / external_uses_blocking / cross_repo_blocking "
                "/ runtime_observed / entry_point. Top-5 blockers ranked by severity. Read-only — "
                "never mutates the codebase. "
                "ALREADY CONSULTED, do not re-run to confirm this verdict: find_dead_code, "
                "find_importers, check_references. "
                "Response carries `stop_rule.terminal`: true means no further jcodemunch call "
                "changes this verdict, so stop checking and decide. It does NOT mean safe — a "
                "blocking verdict is terminal too. When false, `stop_rule.would_change_verdict` "
                "names the specific action that would move it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "symbol": {
                        "type": "string",
                        "description": "Symbol ID or name to evaluate for deletion safety.",
                    },
                    "cross_repo": {
                        "type": "boolean",
                        "description": "Include other indexed repos in the analysis (default true).",
                        "default": True,
                    },
                    "include_runtime": {
                        "type": "boolean",
                        "description": "Consult runtime_calls for production evidence (default true).",
                        "default": True,
                    },
                },
                "required": ["repo", "symbol"],
            },
        ),
        Tool(
            name="check_edit_safe",
            description=(
                "Composite preflight: can this symbol be edited safely? Where check_delete_safe asks "
                "who breaks if it disappears, this asks what your regression risk is if you modify it "
                "and what you must preserve. Fuses signature impact (external/cross-repo importers), "
                "cyclomatic complexity, test-coverage presence, and runtime traffic into a single "
                "verdict + one-line recommended_action. Verdict tiers: safe_to_edit / untested / "
                "complexity_risk / signature_impact / runtime_critical. Top-5 blockers ranked by "
                "severity. Read-only — never mutates the codebase. "
                "ALREADY CONSULTED, do not re-run to confirm this verdict: find_importers, "
                "check_references. "
                "Response carries `stop_rule.terminal`: true means no further jcodemunch call "
                "changes this verdict, so stop checking and decide. It does NOT mean safe — a "
                "blocking verdict is terminal too. When false, `stop_rule.would_change_verdict` "
                "names the specific action that would move it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "symbol": {
                        "type": "string",
                        "description": "Symbol ID or name to evaluate for edit safety.",
                    },
                    "cross_repo": {
                        "type": "boolean",
                        "description": "Include other indexed repos in the analysis (default true).",
                        "default": True,
                    },
                    "include_runtime": {
                        "type": "boolean",
                        "description": "Consult runtime_calls for production evidence (default true).",
                        "default": True,
                    },
                },
                "required": ["repo", "symbol"],
            },
        ),
        Tool(
            name="find_implementations",
            description=(
                "Find concrete implementations of an interface, abstract class, or method. "
                "Multi-source resolution with confidence scoring: SCIP/LSP evidence (1.0), AST class "
                "hierarchy (0.85), duck-typed name match (0.65), decorator handler (0.45) — declared "
                "priors; _meta.confidence_provenance states each channel's basis and measured "
                "precision/recall. "
                "Classifies each impl (subclass_override / interface_impl / duck_typed / "
                "decorator_handler / subclass), ranks by PageRank × byte_length, attaches "
                "differs_by breakdown. Optional cross_repo=true surfaces impls in other indexed "
                "repos via the package registry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "symbol": {
                        "type": "string",
                        "description": "Symbol ID or name of the interface/abstract/method to analyse.",
                    },
                    "relationship_kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional whitelist: subclass_override, interface_impl, duck_typed, "
                            "decorator_handler, subclass. Defaults to all."
                        ),
                    },
                    "include_subclasses": {
                        "type": "boolean",
                        "description": "Walk class hierarchy for class-kind targets (default true).",
                        "default": True,
                    },
                    "cross_repo": {
                        "type": "boolean",
                        "description": "Also search other indexed repos via the package registry (default false).",
                        "default": False,
                    },
                    "rank_by_importance": {
                        "type": "boolean",
                        "description": "Sort by confidence then PageRank × byte_length (default true).",
                        "default": True,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap on returned implementations (default 50).",
                        "default": 50,
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Hard cap on response payload (default 4000).",
                        "default": 4000,
                    },
                },
                "required": ["repo", "symbol"],
            },
        ),
        Tool(
            name="plan_refactoring",
            description=(
                "Generate edit-ready refactoring instructions for renaming, moving, extracting, or "
                "changing the signature of a symbol. Returns {old_text, new_text} blocks for every "
                "affected file — directly compatible with Edit tool. Handles import rewrites, "
                "collision detection, new file generation, and multi-file coordination. "
                "Use BEFORE executing any multi-file refactoring to get a complete edit plan in one call."
            
                " Returns a plan only and never writes a file. Apply the returned blocks yourself, then re-index."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "symbol": {
                        "type": "string",
                        "description": (
                            "Symbol name or ID to refactor. For extract, comma-separated list "
                            "(e.g. 'helper,process_data')."
                        ),
                    },
                    "refactor_type": {
                        "type": "string",
                        "enum": ["rename", "move", "extract", "signature"],
                        "description": "Type of refactoring to plan.",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "New name for rename operations.",
                    },
                    "new_file": {
                        "type": "string",
                        "description": "Destination file path for move/extract operations.",
                    },
                    "new_signature": {
                        "type": "string",
                        "description": "New function signature (e.g. 'foo(x, y, z=0)').",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Import hops to traverse (1-3, default 2).",
                        "default": 2,
                    },
                },
                "required": ["repo", "symbol", "refactor_type"],
            },
        ),
        Tool(
            name="get_dead_code_v2",
            description=(
                "Find likely-dead functions and methods using three independent evidence signals: "
                "(1) the symbol's file is not reachable from any entry point via the import graph "
                "(filename heuristic + package.json main/module/exports/bin), "
                "(2) no indexed symbol calls this symbol in the call graph, "
                "(3) the symbol name is not re-exported from any __init__ or barrel file "
                "(recursively follows CJS `module.exports = require(...)` and ES `export * from`). "
                "Each result includes a confidence score (0.33 = 1 signal, 0.67 = 2 signals, 1.0 = all 3). "
                "More reliable than single-signal dead-code detection. "
                "Use min_confidence=0.67 for high-confidence results only. "
                "v1.80.7+ — `max_results` (default 100) caps response size; "
                "`file_pattern` scopes analysis to a glob like `src/**`."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "min_confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold 0.0–1.0 (default 0.5 = at least 2/3 signals).",
                        "default": 0.5,
                    },
                    "include_tests": {
                        "type": "boolean",
                        "description": "Include test files in analysis (default false).",
                        "default": False,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap on returned dead symbols (default 100, 0 = unlimited). _meta.truncated + _meta.total_matches flag when capped.",
                        "default": 100,
                        "minimum": 0,
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob (e.g. `src/**`, `*.py`) — only scopes the RESULTS, not the population the signals are measured over.",
                    },
                    "degeneracy_cutoff": {
                        "type": "number",
                        "description": "Advanced. Fire rate at or above which a signal is treated as a constant and gets no vote (default 0.90; must be >0.5 and <=1.0). Pass 1.0 for pre-1.108.231 volume.",
                        "default": 0.90,
                        "exclusiveMinimum": 0.5,
                        "maximum": 1.0,
                    },
                    "entry_point_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Glob patterns for files to treat as live roots, for frameworks the filename heuristic cannot see (e.g. 'handlers/*.py' for AWS Lambda, 'route.ts' for Next.js App Router). Matched with fnmatch against the repo-relative path AND against the bare filename, so 'route.ts' catches the file at any depth. NOTE: '**' is NOT recursive here — 'handlers/**/*.py' will not match 'handlers/h.py'. Use 'handlers/*.py' for one level, or a bare filename to match anywhere. Same matcher as find_dead_code.",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_extraction_candidates",
            description=(
                "Identify functions in a file that are good candidates for extraction to a shared module. "
                "A candidate must have high cyclomatic complexity (doing a lot) AND "
                "be called from multiple other files (already implicitly shared). "
                "Results are ranked by score = complexity × caller_file_count. "
                "Requires re-indexing with jcodemunch-mcp >= 1.16 to populate complexity data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Relative file path within the repo (e.g. 'src/utils.py').",
                    },
                    "min_complexity": {
                        "type": "integer",
                        "description": "Minimum cyclomatic complexity threshold (default 5).",
                        "default": 5,
                    },
                    "min_callers": {
                        "type": "integer",
                        "description": "Minimum number of distinct caller files (default 2).",
                        "default": 2,
                    },
                },
                "required": ["repo", "file_path"],
            },
        ),
        Tool(
            name="get_symbol_complexity",
            description=(
                "Return cyclomatic complexity, nesting depth, and parameter count for a single symbol. "
                "Complexity data is stored at index time (requires jcodemunch-mcp >= 1.16 / INDEX_VERSION 7). "
                "assessment field: 'low' (1-4), 'medium' (5-10), 'high' (11+). "
                "Re-index the repo if all metrics show 0 (pre-1.16 index)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Full symbol ID as returned by search_symbols or get_file_outline.",
                    },
                },
                "required": ["repo", "symbol_id"],
            },
        ),
        Tool(
            name="get_churn_rate",
            description=(
                "Return git churn metrics for a file or symbol: commit count, unique authors, "
                "first_seen date, last_modified date, and churn_per_week over a configurable window. "
                "assessment: 'stable' (<=1/week), 'active' (<=3/week), 'volatile' (>3/week). "
                "Requires a locally indexed repo (index_folder); GitHub-indexed repos are not supported."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "target": {
                        "type": "string",
                        "description": "Relative file path (e.g. 'src/utils.py') or a full symbol ID.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Look-back window in days (default 90).",
                        "default": 90,
                    },
                },
                "required": ["repo", "target"],
            },
        ),
        Tool(
            name="get_delivery_metrics",
            description=(
                "Call this when you want a cost-per-outcome read on a codebase — how much "
                "AI spend produced durable change over a window, not raw commit or token "
                "volume. "
                "Quantify durable-change delivery over a window: of the non-merge commits "
                "in the last window_days, how many landed and stuck (commits_durable) vs were "
                "reverted or re-touched within rework_horizon_days (churn-back). commits_durable "
                "is the honest numerator for a cost-per-outcome ratio — divide AI spend over the "
                "same window by it to show how much got done for how little, instead of rewarding "
                "raw activity. Hub files co-touched by most commits (CHANGELOG, version, a "
                "monolithic dispatch module) are excluded from the rework signal (auditable via "
                "_meta.hub_files_excluded). Durability is trailing: commits inside the horizon are "
                "flagged commits_provisional (not yet settled). Diagnostic trend, not a score to "
                "chase. Requires a locally indexed repo (index_folder); GitHub-indexed repos are "
                "not supported."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "Look-back window in days (default 30).",
                        "default": 30,
                    },
                    "rework_horizon_days": {
                        "type": "integer",
                        "description": "Days within which a re-touch counts as churn-back; also "
                                       "defines the provisional tail (default 14).",
                        "default": 14,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_parity_map",
            description=(
                "Use when migrating or porting code from one tree or repo to another and "
                "you need to know what's already moved, what silently diverged, and what's "
                "still unported. "
                "Map migration/port parity between a SOURCE symbol tree and a TARGET tree "
                "(two subpaths of one repo, or two repos). For each source function/method/class "
                "it reports: ported (equivalent counterpart exists), ported_diverged (counterpart "
                "exists but its signature/body drifted — the failure a name-only check reports as "
                "done), unported (no counterpart), orphaned (unported and no migrated caller — a "
                "possible intentional drop), or added (target-only surface). Rename-aware: a "
                "ported-and-renamed symbol is matched by structural+behavioral similarity, not a "
                "false unported+added pair. When include_port_plan is set, the unported symbols are "
                "ordered by the source dependency graph (leaves first) with cycles grouped, each "
                "carrying unblocked + blocking_deps. Read-only and plan-only: it never edits or "
                "ports anything. parity_pct is a labelled estimate."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_repo": {
                        "type": "string",
                        "description": "Repo id of the tree being ported FROM.",
                    },
                    "target_repo": {
                        "type": "string",
                        "description": "Repo id of the tree being ported TO (may equal source_repo).",
                    },
                    "source_path": {
                        "type": "string",
                        "description": "Optional subtree within source_repo (file-path prefix).",
                    },
                    "target_path": {
                        "type": "string",
                        "description": "Optional subtree within target_repo (file-path prefix).",
                    },
                    "match_threshold": {
                        "type": "number",
                        "description": "Similarity floor (0-1) for rename matching (default 0.75).",
                        "default": 0.75,
                    },
                    "divergence": {
                        "type": "string",
                        "description": "Divergence policy: 'signature' (default), 'signature+body', "
                                       "or 'name_only' (presence only, no divergence check).",
                        "enum": ["signature", "signature+body", "name_only"],
                        "default": "signature",
                    },
                    "rename": {
                        "type": "boolean",
                        "description": "Match renamed symbols by similarity (default true). "
                                       "Auto-disabled past the pair budget on very large scopes.",
                        "default": True,
                    },
                    "include_port_plan": {
                        "type": "boolean",
                        "description": "Emit the dependency-ordered plan over unported symbols.",
                        "default": True,
                    },
                },
                "required": ["source_repo", "target_repo"],
            },
        ),
        Tool(
            name="get_decorator_census",
            description=(
                "Repo-wide census of decorators / annotations / attributes: 'where is every "
                "@app.route / @Injectable / @pytest.fixture / [Serializable], and how many?' in one "
                "read-only call. Cross-language by construction (aggregates the decorators the index "
                "stored on each symbol). Forms are NORMALIZED (leading @, call-arguments, and [] "
                "brackets stripped) so @app.route('/a') and @app.route('/b') count under one bucket "
                "instead of scattering; each bucket keeps the distinct raw_forms it collapsed, a "
                "per-decorator symbol-kind breakdown, and a file count. Filter by name_filter "
                "(substring on the normalized name), scope_path (subtree), or kind; include_sites "
                "lists the exact decorated symbols. Pairs with get_signal_chains / get_endpoint_impact "
                "(this surfaces the decorator surface; those resolve what it wires together)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "name_filter": {
                        "type": "string",
                        "description": "Case-insensitive substring on the normalized decorator "
                                       "name (e.g. 'route', 'fixture', 'inject').",
                    },
                    "scope_path": {
                        "type": "string",
                        "description": "Optional subtree prefix (file-path) to restrict the census.",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional symbol-kind filter (function/method/class/...).",
                    },
                    "include_sites": {
                        "type": "boolean",
                        "description": "List the decorated symbols per bucket (capped at max_sites_per).",
                        "default": False,
                    },
                    "max_decorators": {
                        "type": "integer",
                        "description": "Cap on histogram rows (default 100).",
                        "default": 100,
                    },
                    "max_sites_per": {
                        "type": "integer",
                        "description": "Cap on sites listed per decorator when include_sites (default 50).",
                        "default": 50,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_architecture_metrics",
            description=(
                "Structural concentration, dependency depth, and modularity in one read-only "
                "call, over the file import graph. concentration: Gini coefficient (0 even -> 1 "
                "hoarded) over per-file symbol count, size, fan-in (importers), and fan-out "
                "(imports) + the top concentrators — answers 'is complexity/coupling piling up in "
                "a few files?' which a hotspot list (the peaks) can't. depth: longest dependency "
                "chain + level distribution (Lakos levelization) over the cycle-condensed DAG. "
                "modularity: cluster count + the hidden coupling a Design Structure Matrix "
                "highlights (back-edges = cycle-participating import edges) without the NxN matrix. "
                "Does not duplicate get_layer_violations (specific violations) or "
                "get_dependency_cycles (the cycles); does not touch the health-radar composite."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Number of top concentrators to list per Gini metric (default 10).",
                        "default": 10,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_hotspots",
            description=(
                "Return the top-N highest-risk symbols ranked by hotspot score = "
                "cyclomatic_complexity x log(1 + commits_last_N_days). "
                "Identifies code that is both complex and frequently changed — the highest "
                "bug-introduction risk in the codebase. Methodology matches CodeScene/Adam Tornhill. "
                "Requires jcodemunch-mcp >= 1.16 for complexity data and a locally indexed repo for churn."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Number of results to return (default 20).",
                        "default": 20,
                    },
                    "days": {
                        "type": "integer",
                        "description": "Churn look-back window in days (default 90).",
                        "default": 90,
                    },
                    "min_complexity": {
                        "type": "integer",
                        "description": "Minimum cyclomatic complexity to include (default 2).",
                        "default": 2,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_repo_health",
            description=(
                "Return a one-call triage snapshot of the entire repository: symbol counts, "
                "dead code %, average cyclomatic complexity, top 5 hotspots, dependency cycle count, "
                "and unstable module count. "
                "Designed to be the first tool called in any new session — one call gives a complete "
                "picture to guide follow-up analysis."
            
                " The dead-code percentage is a heuristic estimate, and the hotspot ranking needs local git history."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Churn look-back window for hotspot calculation (default 90).",
                        "default": 90,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_untested_symbols",
            description=(
                "Find functions and methods with no evidence of being exercised by any test file. "
                "Uses import-graph reachability + name matching (AST call_references when available, "
                "word-boundary text heuristic as fallback). Returns symbols classified as 'unreached' "
                "(no test file imports the source file) or 'imported_not_called' (test imports the "
                "module but no test references this specific function). "
                "This is heuristic reachability, NOT runtime coverage — it answers 'does any test "
                "reference this symbol?' rather than 'what % of lines are covered.' "
                "Use after get_repo_health for a deeper quality picture."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob to narrow which source files are analysed (e.g. 'src/**/*.py').",
                    },
                    "min_confidence": {
                        "type": "number",
                        "description": "Minimum confidence to include (0.0–1.0, default 0.5).",
                        "default": 0.5,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap on returned symbols (default 100).",
                        "default": 100,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="search_ast",
            description=(
                "Cross-language AST pattern matching. Finds structural code patterns "
                "across all 70+ indexed languages using a single query — no need to know "
                "language-specific AST node types. Two modes: (1) preset anti-patterns "
                "(empty_catch, bare_except, deeply_nested, nested_loops, god_function, "
                "eval_exec, hardcoded_secret, todo_fixme, magic_number, reassigned_param), "
                "or (2) custom mini-DSL (call:*.unwrap, string:/password/i, comment:/TODO/i, "
                "nesting:5+, loops:3+, lines:80+). Use category='all' to run every preset "
                "at once, or category='security'/'error_handling'/'complexity'/'performance'/"
                "'maintenance' for a focused scan. Every match is attributed to its enclosing "
                "indexed symbol with complexity metadata. Requires a locally indexed repo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Preset name (empty_catch, bare_except, deeply_nested, nested_loops, "
                            "god_function, eval_exec, hardcoded_secret, todo_fixme, magic_number, "
                            "reassigned_param) or custom query (call:NAME, string:/REGEX/i, "
                            "comment:/REGEX/i, nesting:N+, loops:N+, lines:N+). "
                            "Mutually exclusive with category."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Run all presets in a category: security, error_handling, "
                            "complexity, performance, maintenance, or all."
                        ),
                    },
                    "language": {
                        "type": "string",
                        "description": "Restrict scan to one language (e.g. 'python', 'typescript').",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Glob filter on file paths (e.g. 'src/**/*.py').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap on total matches returned (default 50).",
                        "default": 50,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_symbol_importance",
            description=(
                "Return the most architecturally important symbols in a repo, ranked by "
                "PageRank or in-degree centrality on the import graph. Useful for "
                "orientation: surfaces the symbols that most of the codebase depends on. "
                "New tool: use after indexing to understand repo architecture at a glance."
            
                " Ranks at most top_n symbols (default 20) over the import graph, so a symbol reached only dynamically scores zero."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "top_n": {"type": "integer", "description": "Number of top symbols to return (default 20, max 200)", "default": 20},
                    "algorithm": {
                        "type": "string",
                        "enum": ["pagerank", "degree"],
                        "description": "'pagerank' (default) = full PageRank on import graph; 'degree' = simple in-degree count (faster).",
                        "default": "pagerank",
                    },
                    "scope": {"type": "string", "description": "Limit to a subdirectory prefix (e.g. 'src/core')"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="find_similar_symbols",
            description=(
                "Find clusters of similar functions/methods/classes — consolidation candidates. "
                "Blends three signals: semantic (embedding cosine when embed_repo has run), "
                "structural (signature-token Jaccard + size ratio), and behavioral (callee-set Jaccard). "
                "Runs union-find clustering, classifies each cluster (near_duplicate / similar_logic / "
                "parallel_implementation), picks a canonical symbol per cluster (highest PageRank), "
                "and surfaces 'differs_by' breakdowns so an agent can recommend keep-this/replace-those. "
                "Pre-filters via BM25 inverted index — sub-N^2 on large repos. Degrades gracefully "
                "without embeddings (mode='structural'). Skip tests/dunders/generated files by default."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "threshold": {
                        "type": "number",
                        "description": "Minimum combined similarity to form a cluster edge (0.0–1.0). Default 0.80.",
                        "default": 0.80,
                    },
                    "min_size": {
                        "type": "integer",
                        "description": "Minimum byte_length per symbol (default 30; filters out getters/wrappers).",
                        "default": 30,
                    },
                    "max_clusters": {
                        "type": "integer",
                        "description": "Cap on clusters returned (default 25).",
                        "default": 25,
                    },
                    "include_tests": {
                        "type": "boolean",
                        "description": "When False (default), test files are skipped — tests intentionally share shapes.",
                        "default": False,
                    },
                    "scope": {
                        "type": "string",
                        "description": "Optional glob to limit to a subdirectory (e.g. 'src/core/*').",
                    },
                    "include_kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Symbol kind whitelist. Defaults to ['function', 'method', 'class'].",
                    },
                    "semantic_weight": {
                        "type": "number",
                        "description": "Embedding weight when embeddings are present (0.0–1.0). Default 0.6.",
                        "default": 0.6,
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Hard cap on the response's payload (default 4000).",
                        "default": 4000,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_repo_map",
            description=(
                "Query-less, token-budgeted, signature-level overview of a repository. "
                "Groups symbols by file, ranks files by PageRank on the import graph, and "
                "greedy-packs signatures (not bodies) under token_budget. Designed for "
                "cold-start orientation — 'I just cloned this repo, what matters here?'. "
                "Pair with get_tectonic_map (module topology) "
                "and get_ranked_context (query-driven) once you know what to ask for."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "token_budget": {
                        "type": "integer",
                        "description": "Hard cap on returned tokens (default 2048).",
                        "default": 2048,
                    },
                    "scope": {
                        "type": "string",
                        "description": "Optional glob to limit to a subdirectory (e.g. 'src/core/*').",
                    },
                    "max_per_file": {
                        "type": "integer",
                        "description": "Max signatures emitted per file (default 5, capped at 50).",
                        "default": 5,
                    },
                    "include_kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of symbol kinds to restrict results (e.g. ['class', 'function']).",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="find_dead_code",
            description=(
                "Find dead code — files and symbols with zero importers and no entry-point role. "
                "Uses the import graph to identify unreachable code. Returns confidence scores "
                "(1.0 = provably unreachable, 0.7 = all importers are themselves dead). "
                "Set granularity='file' for file-level results only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "granularity": {
                        "type": "string",
                        "enum": ["symbol", "file"],
                        "description": "'symbol' (default) returns dead symbols; 'file' returns dead files only.",
                        "default": "symbol",
                    },
                    "min_confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold 0.0–1.0. Default 0.8. Use 1.0 for provably unreachable only.",
                        "default": 0.8,
                    },
                    "include_tests": {
                        "type": "boolean",
                        "description": "Treat test files as live roots (default false — test files are excluded from dead code candidates).",
                        "default": False,
                    },
                    "entry_point_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional glob patterns to treat as live roots (e.g. 'cli/*.py', 'scripts/*').",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_ranked_context",
            description=(
                "Assemble the best-fit context for a query within a token budget. "
                "Ranks all symbols by relevance (BM25) and/or centrality (PageRank), "
                "loads source for the top candidates, and packs greedily until token_budget is exhausted. "
                "Exact symbol names in the query (qualified, CamelCase, snake_case) are pinned ahead "
                "of the ranking; include identifiers verbatim. "
                "Use when you want 'the best N tokens of context for this task' without specifying exact symbols."
            
                " Truncates at token_budget."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "query": {"type": "string", "description": "Natural language or identifier describing the task (max 500 chars)"},
                    "token_budget": {
                        "type": "integer",
                        "description": "Hard cap on returned tokens (default 4000).",
                        "default": 4000,
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["combined", "bm25", "centrality"],
                        "description": (
                            "'combined' (default) = BM25 + PageRank weighted sum. "
                            "'bm25' = pure text relevance. "
                            "'centrality' = PageRank only, filtered to query-matching symbols."
                        ),
                        "default": "combined",
                    },
                    "include_kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of symbol kinds to restrict results (e.g. ['class', 'function']).",
                    },
                    "scope": {
                        "type": "string",
                        "description": "Optional glob pattern to limit search to a subdirectory (e.g. 'src/core/*').",
                    },
                    "fusion": {
                        "type": "boolean",
                        "description": "Enable multi-signal fusion (Weighted Reciprocal Rank) for ranking. Combines lexical, structural, and identity channels.",
                        "default": False,
                    },
                    "compress": {
                        "type": "boolean",
                        "description": "Keystone-protected structural compression: prune low-signal lines from oversized bodies so more relevant symbols fit the budget (control-flow/returns/signatures always kept). Model-free; pruned items carry source_pruned + line counts. Default False.",
                        "default": False,
                    },
                    "receipt": {
                        "type": "boolean",
                        "description": _RECEIPT_ARG_DESCRIPTION,
                        "default": False,
                    },
                },
                "required": ["repo", "query"],
            },
        ),
        Tool(
            name="assemble_task_context",
            description=(
                "Task-aware single-call orchestrator. Auto-classifies task into "
                "explore/debug/refactor/extend/audit/review intent, runs the right sub-tools, "
                "returns one source-attributed capsule under token_budget."
            
                " Bounded by token_budget; see intent_detected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "task": {
                        "type": "string",
                        "description": "Natural-language task description. Anchors auto-extracted from task text.",
                    },
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional anchor symbol IDs or names; auto-extracted from task when omitted.",
                    },
                    "intent": {
                        "type": "string",
                        "enum": ["explore", "debug", "refactor", "extend", "audit", "review"],
                        "description": "Optional override; auto-detected from task when omitted.",
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "End-to-end hard cap on returned tokens (default 8000).",
                        "default": 8000,
                    },
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional whitelist of stages to run (e.g. ['anchor', 'blast', 'runtime']).",
                    },
                    "cross_repo": {
                        "type": "boolean",
                        "description": "When True, layer cross-repo signals (default false).",
                        "default": False,
                    },
                },
                "required": ["repo", "task"],
            },
        ),
        Tool(
            name="get_changed_symbols",
            description=(
                "Map a git diff to affected symbols: given two commits, returns which symbols "
                "were added, removed, modified, or renamed. Useful after merging a PR to answer "
                "'what actually changed?' for code review or regression triage. "
                "Requires a locally indexed repo (index_folder). "
                "Defaults to comparing current HEAD against the SHA stored at index time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier — must be locally indexed with index_folder"},
                    "since_sha": {
                        "type": "string",
                        "description": "Compare from this git SHA or ref. Defaults to the SHA stored at index time.",
                    },
                    "until_sha": {
                        "type": "string",
                        "description": "Compare to this git SHA or ref (default 'HEAD').",
                        "default": "HEAD",
                    },
                    "include_blast_radius": {
                        "type": "boolean",
                        "description": "Also return downstream importers (blast radius) for each changed symbol (default false).",
                        "default": False,
                    },
                    "max_blast_depth": {
                        "type": "integer",
                        "description": "Hop limit when include_blast_radius=true (default 3, max 5).",
                        "default": 3,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="embed_repo",
            description=(
                "Precompute and cache symbol embeddings for semantic search. "
                "Optional warm-up: search_symbols with semantic=true lazily embeds missing "
                "symbols on first use, but embed_repo warms the cache upfront so the first "
                "semantic query returns immediately. "
                + _PROVIDER_HINT
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "batch_size": {
                        "type": "integer",
                        "description": "Symbols per embedding batch (default 50).",
                        "default": 50,
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Recompute all embeddings even if they already exist (default false).",
                        "default": False,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_cross_repo_map",
            description=(
                "Return which indexed repos depend on which other indexed repos at the package level. "
                "Shows the full cross-repository dependency map based on package names extracted from "
                "manifest files (pyproject.toml, package.json, go.mod, Cargo.toml, etc.). "
                "Use to visualize how your indexed repos are interconnected. "
                "Pass repo to filter to a single repo's perspective."
            
                " Edges come from package names in manifest files, so a path or git dependency with no manifest entry is invisible."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Optional repo ID to filter. If omitted, returns the full cross-repo map.",
                    },
                },
            },
        ),
        Tool(
            name="get_group_contracts",
            description=(
                "Surface the de-facto API contracts across a group of indexed repos. Walks each "
                "member's named imports, resolves them to symbols in other members via the package "
                "registry, and classifies each shared symbol into one of four verdict tiers: "
                "'de_facto_api' (used by ≥min_importers external repos), 'leaky_internal' (underscore-"
                "prefixed or in _internal/ but imported externally — architecture violation), "
                "'dead_contract' (declared public but unused externally; opt-in), 'version_skew' "
                "(same name imported via multiple specifier roots — coordination risk). Attaches "
                "stability score (churn-weighted), last breaking change (from get_symbol_provenance), "
                "and runtime hits (when traces have been ingested). Pairs with get_cross_repo_map: "
                "that gives the repo-level edge graph; this zooms in to the symbol-level surface."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repos": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of indexed repo IDs (owner/name or bare names). Must be ≥2.",
                    },
                    "min_importers": {
                        "type": "integer",
                        "description": "Minimum distinct external repo importers to surface a contract (default 2).",
                        "default": 2,
                    },
                    "include_internal": {
                        "type": "boolean",
                        "description": "Surface leaky_internal contracts (architecture violations). Default true.",
                        "default": True,
                    },
                    "include_dead_contracts": {
                        "type": "boolean",
                        "description": "Surface public symbols with zero external importers. Default false.",
                        "default": False,
                    },
                    "classify": {
                        "type": "boolean",
                        "description": "Attach verdict tier per contract. Default true.",
                        "default": True,
                    },
                    "churn_days": {
                        "type": "integer",
                        "description": "Window for stability scoring (default 90).",
                        "default": 90,
                    },
                    "max_contracts": {
                        "type": "integer",
                        "description": "Cap on returned contracts (default 50).",
                        "default": 50,
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Hard cap on response payload (default 4000).",
                        "default": 4000,
                    },
                },
                "required": ["repos"],
            },
        ),
        Tool(
            name="get_tectonic_map",
            description=(
                "Discover the logical module topology of a codebase by fusing three coupling signals: "
                "structural (import edges), behavioral (shared symbol references), and temporal "
                "(git co-churn). Returns tectonic plates (auto-detected file clusters), each with "
                "an anchor file, cohesion score, inter-plate coupling, and drifters (files whose "
                "directory doesn't match their logical module). Detects nexus plates (god-module risk: "
                "coupled to ≥4 other plates). No k parameter — plate count emerges from the topology. "
                "Use to find hidden module boundaries, misplaced files, and architectural drift."
            
                " The temporal signal needs local git history; without it plates are built from structure and behaviour only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Git co-churn look-back window in days (default 90)",
                        "default": 90,
                    },
                    "min_plate_size": {
                        "type": "integer",
                        "description": "Minimum files per plate to include; smaller groups go to isolated_files (default 2)",
                        "default": 2,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_signal_chains",
            description=(
                "Discover how external signals (HTTP requests, CLI commands, scheduled tasks, events) "
                "propagate through the codebase via the call graph. Each signal chain traces a path "
                "from a gateway (entry point) through its callees to leaf symbols. "
                "Two modes: (1) Discovery — omit symbol to map all chains with orphan detection; "
                "(2) Lookup — pass a symbol name/ID to find which user-facing chains it participates in "
                "(e.g. 'validate_email sits on POST /api/users and cli:import-users'). "
                "Detects gateways from route decorators (Flask/FastAPI/Spring/NestJS/ASP.NET), "
                "CLI commands (@click, @app.command), task queues (@celery, @dramatiq), event handlers, "
                "and standard entry points (main.py, __main__.py). "
                "Use before refactoring to understand which user-facing behaviors depend on a symbol."
            
                " Traces at most max_depth hops (default 5) and recognises only the listed gateway patterns, so a custom framework's entry points are missed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name or ID for lookup mode. When provided, returns only chains containing that symbol. Omit for discovery mode (all chains).",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Filter gateways by kind: http, cli, event, task, main, test.",
                        "enum": ["http", "cli", "event", "task", "main", "test"],
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "BFS depth limit per chain (1–8, default 5).",
                        "default": 5,
                    },
                    "include_tests": {
                        "type": "boolean",
                        "description": "Include test_* functions as gateways (default false).",
                        "default": False,
                    },
                    "include_flow_edges": {
                        "type": "boolean",
                        "description": (
                            "Resolve framework flow edges the call graph is blind to (default true). "
                            "String-dispatched handlers (Django path()/re_path(), Express "
                            "router.get(path, handler), Flask add_url_rule, Rails to:) surface as http "
                            "gateways even with no route decorator, and templates a chain renders "
                            "(render/render_template/res.render/view) attach as a per-chain 'views' list. "
                            "Set false for pure call-graph behavior."
                        ),
                        "default": True,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_endpoint_impact",
            description=(
                "Endpoint-centric impact analysis: 'what breaks if I change this HTTP endpoint?' "
                "Given an endpoint (method + URL, e.g. 'GET /users') or a handler symbol, returns "
                "the handler plus what changing it affects — importing files + callers (blast radius) "
                "and any templates it renders. Read-only. Resolves string-dispatch routes "
                "(Django/Express/Flask/Rails) and decorator routes (Flask/FastAPI/Spring) by their "
                "local path; for prefix-composed FastAPI (APIRouter prefix) or Spring class-level "
                "mappings whose full URL isn't resolved yet, pass handler_symbol_id instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "endpoint": {
                        "type": "string",
                        "description": "HTTP endpoint to analyse, e.g. 'GET /users' or '/users' (verb optional). One of endpoint / handler_symbol_id is required.",
                    },
                    "handler_symbol_id": {
                        "type": "string",
                        "description": "Analyse a handler symbol directly instead of by URL (use for prefixed routes whose full path isn't resolved).",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Import hops for blast radius (1 = direct importers; max 3).",
                        "default": 1,
                    },
                    "call_depth": {
                        "type": "integer",
                        "description": "Call-graph hops for caller detection (0 disables; max 3).",
                        "default": 2,
                    },
                    "include_infra": {
                        "type": "boolean",
                        "description": "Attach per-impact infra links: env vars / compose services / Dockerfiles / CI jobs / scripts whose project-intel cross-references land in the endpoint's blast-radius files (downstream), plus what exposes the app (compose ports, K8s Service/Ingress; precision host_port unless an Ingress path rule names the route). File-granular evidence.",
                        "default": False,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="render_diagram",
            description=(
                "Render any graph-producing tool's output as rich, annotated Mermaid markup. "
                "Pass the raw output dict from get_call_hierarchy, get_signal_chains, "
                "get_tectonic_map, get_dependency_cycles, get_impact_preview, "
                "get_blast_radius, or get_dependency_graph. Auto-detects the source tool "
                "and picks the optimal diagram type: flowchart TD (call hierarchy, blast radius), "
                "flowchart BT (impact preview), flowchart LR (tectonic plates, dependency graph, "
                "cycles), or sequenceDiagram (signal chains). Encodes metadata as visual signals: "
                "edge colors for resolution confidence, node shapes for symbol kind, subgraph "
                "grouping by file/plate/depth, risk heat coloring. Themes: 'flow' (blue/purple "
                "depth gradient), 'risk' (red/yellow/green heat), 'minimal' (monochrome). "
                "Smart pruning keeps output under max_nodes."
            
                " Prunes to max_nodes (default 80), so a large graph renders partially. It reads the dict you pass and never queries the index."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "object",
                        "description": "Raw output dict from any supported graph-producing tool.",
                    },
                    "theme": {
                        "type": "string",
                        "enum": ["flow", "risk", "minimal"],
                        "description": "Visual theme: 'flow' (architecture), 'risk' (impact), 'minimal' (docs). Default: flow.",
                        "default": "flow",
                    },
                    "max_nodes": {
                        "type": "integer",
                        "description": "Maximum nodes before smart pruning (default 80, range 10–200).",
                        "default": 80,
                    },
                    **({
                        "open_in_viewer": {
                            "type": "boolean",
                            "description": (
                                "When true, also open the rendered mermaid in the local mmd-viewer. "
                                "The HTML file is written under <index_storage>/temp/mermaid/. "
                                "Non-fatal: if the viewer is missing, mermaid is returned anyway."
                            ),
                            "default": False,
                        },
                    } if config_module.get("render_diagram_viewer_enabled", False) else {}),
                },
                "required": ["source"],
            },
        ),
        Tool(
            name="get_project_intel",
            description=(
                "Auto-discover and parse non-code knowledge files (Dockerfiles, CI configs, "
                "docker-compose, K8s manifests, .env templates, Makefiles, package.json scripts) "
                "and cross-reference them to indexed code symbols. Returns structured intelligence "
                "grouped by category: infra, ci, config, deps, api, data. "
                "For categories already in the index (OpenAPI, Terraform, GraphQL, Protobuf, dbt), "
                "pulls from the index directly. Requires a local index (index_folder)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or display name).",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category to return: all, infra, ci, config, deps, api, data.",
                        "default": "all",
                        "enum": ["all", "infra", "ci", "config", "deps", "api", "data"],
                    },
                    "scope_path": {
                        "type": "string",
                        "description": "Optional subpath (relative to source_root) to restrict intel discovery to a single workspace member — e.g. 'packages/api'. When omitted, the whole repo is scanned. Use `list_workspaces` to enumerate the available members. Cross-references still consult the global index so a package's container still resolves against repo-level code.",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="list_workspaces",
            description=(
                "Enumerate monorepo workspace members for an indexed repo. Detects "
                "pnpm (pnpm-workspace.yaml), yarn/npm (package.json workspaces), "
                "turborepo (turbo.json), lerna (lerna.json), rush (rush.json), "
                "Go (go.work), and Cargo ([workspace] members). Returns "
                "[{path, package_name, manager}, ...] plus an `is_monorepo` flag "
                "and the list of managers that contributed. Use the returned "
                "`path` values as the `scope_path` argument on get_project_intel "
                "to retrieve per-package intel (Dockerfile / CI / deps) instead of "
                "the repo-wide aggregate."
            
                " Detects only the listed layouts; any other workspace arrangement returns is_monorepo=false."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or display name).",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="winnow_symbols",
            description=(
                "Run a multi-axis constraint query against the index in a single round trip. "
                "Accepts an ordered list of criteria (AND) intersecting signals no other tool "
                "composes: kind, language, name (regex), file glob, cyclomatic complexity, "
                "decorator, direct call references, summary/docstring text, and git churn. "
                "Survivors are ranked by importance (PageRank, default), complexity, churn, "
                "or name. Use for questions like 'complex untested functions that call db.Exec' "
                "or 'deprecated methods still churning in the last 30 days' — cases that would "
                "otherwise require 4-5 separate calls and client-side merging."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)",
                    },
                    "criteria": {
                        "type": "array",
                        "description": (
                            "Ordered list of filters. Each item is {axis, op, value}. "
                            "Supported axes: kind (in/eq), language (in/eq), name (eq/matches), "
                            "file (matches - glob), complexity (>,<,>=,<=,==), decorator (contains), "
                            "calls (contains - matches call_references), summary (contains), "
                            "churn (>,<,>=,<=,== with optional window_days, default 90). "
                            "All criteria must match (AND)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "axis": {"type": "string"},
                                "op": {"type": "string"},
                                "value": {},
                                "window_days": {
                                    "type": "integer",
                                    "description": "Only used when axis='churn'. Days of git history to scan (default 90).",
                                },
                            },
                            "required": ["axis", "op", "value"],
                        },
                    },
                    "rank_by": {
                        "type": "string",
                        "enum": ["importance", "complexity", "churn", "name"],
                        "default": "importance",
                        "description": "Ranking axis for survivors.",
                    },
                    "order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "default": "desc",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 20,
                        "description": "Hard cap on returned results.",
                    },
                },
                "required": ["repo", "criteria"],
            },
        ),
        # --- Runtime tier-switch tools (always force-included below) ---------
        Tool(
            name="set_tool_tier",
            description=(
                "Explicit tier override for the current session. "
                "Narrows or widens the exposed tool list to 'core' / 'standard' / 'full'. "
                "Prefer plan_turn(model=...) for routine per-task use; use "
                "set_tool_tier only when you need an explicit override (e.g. escalate "
                "mid-task to 'full' after a capability-gated failure)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tier": {
                        "type": "string",
                        "enum": ["core", "standard", "full"],
                    },
                },
                "required": ["tier"],
            },
        ),
        Tool(
            name="announce_model",
            description=(
                "Agent self-reports its active model identifier. Server resolves to a "
                "tier via model_tier_map (fuzzy: normalize → exact → glob → substring "
                "→ '*' → 'full') and narrows the exposed tool list accordingly. "
                "Idempotent: a second call with the same model is a cheap no-op. "
                "Prefer calling plan_turn(model=...) for routine per-task use; use "
                "announce_model as a fallback when plan_turn is not appropriate for "
                "the current task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Your active model identifier, e.g. 'claude-haiku-4-5'."},
                },
                "required": ["model"],
            },
        ),
        Tool(
            name="jcodemunch_guide",
            description=(
                "Return the version-current CLAUDE.md / AGENT.md policy snippet for "
                "jcodemunch-mcp — the same text produced by `jcodemunch-mcp claude-md "
                "--generate`. Lets an agent keep a one-line CLAUDE.md (e.g. \"Call "
                "jcodemunch_guide and strictly follow its instructions.\") instead of "
                "pasting a static snippet that drifts from the installed version. "
                "Idempotent, no repo context required. Matches the active tool "
                "surface, tier and disabled_tools — list 'jcodemunch_guide' in "
                "disabled_tools to hide it."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]
    # --- The Counter: register the front door + capture the raw catalog ------
    all_tools = all_tools + _counter_front_door_tools()
    global _RAW_CATALOG, _DECLARED_ARG_KEYS
    _RAW_CATALOG = list(all_tools)
    # Snapshot the DECLARED argument surface here, before the compact-schemas
    # strip below. `compact_schemas` pops rarely-used params out of the published
    # schema while "the underlying handler still accepts them"
    # (_COMPACT_STRIP_PARAMS), and it pops them out of the same dicts _RAW_CATALOG
    # holds — so building the argument contract from that catalog afterwards made
    # a hidden-but-honored param look like a caller mistake, which downgrades the
    # verdict and costs a well-formed call its absence evidence. Exactly the
    # `suppress_meta` bug from v1.108.177, one layer down. Key sets are frozen
    # values, so this snapshot cannot be mutated by the strip.
    _DECLARED_ARG_KEYS = {
        t.name: frozenset(props)
        for t in all_tools
        if isinstance((props := (t.inputSchema or {}).get("properties")), dict) and props
    }
    surface = _effective_surface()
    if surface == "counter":
        # Collapse to the front door + always-present controls. Tier filtering
        # is intentionally bypassed: 'counter' is the surface choice itself.
        keep = _COUNTER_FRONT_DOOR | _ALWAYS_PRESENT_TOOLS
        tools = [t for t in all_tools if t.name in keep]
        _apply_description_overrides(tools)
        return _apply_readonly_annotations(tools)
    # Non-counter surfaces keep existing behavior byte-for-byte: the front-door
    # tools stay hidden (still callable via call_tool), so 'full' and the
    # existing tiers are unchanged on upgrade.
    all_tools = [t for t in all_tools if t.name not in _COUNTER_FRONT_DOOR]
    # Start with a mutable copy for filtering.
    tools = list(all_tools)
    # --- Profile filtering ---------------------------------------------------
    profile = _effective_profile()
    allowed = _resolve_tier_bundle(profile)
    if allowed is not None:
        tools = [t for t in tools if t.name in allowed]

    # Filter out disabled tools. _UNDISABLEABLE_TOOLS (runtime tier controls)
    # are never removed by default — disabling them would lock the user out of
    # switching tiers in-session. Other meta tools (jcodemunch_guide) honor
    # disabled_tools. Users who set `allow_disabling_tier_controls=true` opt
    # out of the safety net (issue #299) — useful for tool-cap budgets.
    disabled = config_module.get("disabled_tools", [])
    allow_disable_tier = config_module.get("allow_disabling_tier_controls", False)
    protected = frozenset() if allow_disable_tier else _UNDISABLEABLE_TOOLS
    if disabled:
        disabled_set = set(disabled) - protected
        if disabled_set:
            tools = [t for t in tools if t.name not in disabled_set]

    # Re-add tier-survivors that weren't disabled. Anything in
    # _ALWAYS_PRESENT_TOOLS but explicitly in disabled_tools stays hidden.
    disabled_set = set(disabled) if disabled else set()
    present_names = {t.name for t in tools}
    missing = _ALWAYS_PRESENT_TOOLS - present_names - (disabled_set - protected)
    if missing:
        tools.extend(t for t in all_tools if t.name in missing)

    # SQL gating: auto-disable search_columns when SQL not in languages
    languages = config_module.get("languages")
    if languages is not None and "sql" not in languages:
        tools = [t for t in tools if t.name != "search_columns"]

    # --- Compact schemas: strip rarely-used params ---------------------------
    if config_module.get("compact_schemas", False):
        for tool in tools:
            if not isinstance(tool.inputSchema, dict):
                continue
            props = tool.inputSchema.get("properties")
            if not props:
                continue
            strip_set = _COMPACT_STRIP_PARAMS.get(tool.name)
            if strip_set:
                for param in strip_set:
                    props.pop(param, None)
            # Demote large mechanical enums to free-string filters (capability
            # preserved; the tool accepts any string for these params).
            for param in _COMPACT_DEMOTE_ENUM_PARAMS:
                pschema = props.get(param)
                if isinstance(pschema, dict) and "enum" in pschema:
                    props[param] = {k: v for k, v in pschema.items() if k != "enum"}

    # Merge descriptions from config (runs after disabled_tools filter)
    _apply_description_overrides(tools)

    return _apply_readonly_annotations(tools)


def _apply_description_overrides(tools: list) -> None:
    """Apply description overrides from config to tool schemas."""
    descriptions = config_module.get_descriptions()
    if not descriptions:
        return

    shared = descriptions.get("_shared", {})

    for tool in tools:
        raw = descriptions.get(tool.name)
        if raw is None:
            tool_desc: dict = {}
        elif isinstance(raw, str):
            # Flat format: "tool_name": "description" → override tool description only
            tool.description = raw
            tool_desc = {}
        else:
            tool_desc = raw

        # Nested format: override tool-level description via "_tool" key
        # "_tool": "" means "use hardcoded minimal base only" (empty string override)
        if "_tool" in tool_desc:
            tool.description = tool_desc["_tool"]

        # Override parameter descriptions (applies even if only _shared is set)
        if isinstance(tool.inputSchema, dict):
            props = tool.inputSchema.get("properties", {})
            for param_name, param_schema in props.items():
                if not isinstance(param_schema, dict):
                    continue
                # Tool-specific override takes precedence over _shared
                # Empty string means "use hardcoded minimal base only"
                desc_override = tool_desc.get(param_name)
                if desc_override is None:
                    desc_override = shared.get(param_name)
                if desc_override is not None:
                    props[param_name] = {**param_schema, "description": desc_override}


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Advertise the runtime identity resource (munch.runtime.identity/v1, #371)
    plus any session-finalized canonical handoffs (jcodemunch.handoff/v1, #374)."""
    _signal_handshake()
    resources = [
        Resource(
            uri=runtime_identity.IDENTITY_URI,
            name="runtime-identity",
            description=(
                "Process provenance for this server instance "
                f"({runtime_identity.IDENTITY_SCHEMA}): product, version, "
                "transport, pid, OS-derived process_start, per-process "
                "instance_id, optional launch_id echo. Read-only, no side effects."
            ),
            mimeType="application/json",
        )
    ]
    from . import handoff as _handoff
    for row in _handoff.list_handoff_resources():
        resources.append(
            Resource(
                uri=row["uri"],
                name=row["name"],
                description=row["description"],
                mimeType=_handoff.HANDOFF_CONTENT_TYPE,
            )
        )
    # Evidence receipts (jcodemunch.evidence/v1, #377 phase 2). A resource, not a
    # tool: the tool-schema budget is a real constraint, and a receipt is read on
    # demand by a client that wants the body rather than shipped inside every
    # response.
    from .evidence import receipts as _receipts
    for row in _receipts.list_evidence_resources():
        resources.append(
            Resource(
                uri=row["uri"],
                name=row["name"],
                description=row["description"],
                mimeType=_receipts.EVIDENCE_CONTENT_TYPE,
            )
        )
    return resources


@server.read_resource()
async def read_resource(uri) -> "list[ReadResourceContents]":
    _signal_handshake()
    if str(uri) == runtime_identity.IDENTITY_URI:
        return [
            ReadResourceContents(
                content=runtime_identity.identity_json(),
                mime_type="application/json",
            )
        ]
    from . import handoff as _handoff
    rec = _handoff.handoff_for_uri(str(uri))
    if rec is not None:
        return [
            ReadResourceContents(
                content=rec["body"],
                mime_type=_handoff.HANDOFF_CONTENT_TYPE,
            )
        ]
    from .evidence import receipts as _receipts
    if str(uri).startswith(_receipts.EVIDENCE_URI_PREFIX):
        envelope = _receipts.evidence_for_uri(str(uri))
        if envelope is not None:
            return [
                ReadResourceContents(
                    # Deterministic: repeated reads of one receipt are
                    # byte-identical. A proof that renders differently on the
                    # second read is not a proof.
                    content=_receipts.envelope_json(envelope),
                    mime_type=_receipts.EVIDENCE_CONTENT_TYPE,
                )
            ]
        # Name the failure rather than collapsing every miss into "unknown".
        _envelope, _why = _receipts.lookup(str(uri))
        raise ValueError(f"Evidence receipt not available ({_why}): {uri}")
    raise ValueError(f"Unknown resource: {uri}")


_WORKFLOW_PROMPT_TEXT = """\
# jcodemunch-mcp — Workflow Guide

Use these tools instead of Grep/Read/search for any indexed repository.

## Step-by-step

1. **list_repos** — check if the project is already indexed.
   - If not found, run **index_folder** (local) or **index_repo** (GitHub URL).

2. **search_symbols** — find functions, classes, methods by name or description.
   - Use `detail_level: "full"` to get source inline, or follow up with **get_symbol_source**.

3. **get_context_bundle** — get symbol source + its imports in one call.

4. **search_text** — fall back to full-text / regex search for string literals or comments.

5. **get_file_outline** — list all symbols in a file without reading the whole thing.

## Claude Code deferred-tool note

jcodemunch tools may appear as *deferred* in your system-reminder. Call **ToolSearch** with
a query like `"list repos"` or `"search symbols"` to load the full schema before use.
Set `discovery_hint: false` in config.jsonc to suppress the reminder in tool descriptions.
"""

_EXPLORE_PROMPT_TEXT = """\
# Explore — Build a mental model of an unfamiliar repo

Goal: Onboard to a repo you've never seen before.

1. **list_repos** → check if indexed. If not, run **index_folder** (local) or **index_repo** (GitHub).
2. **get_repo_outline** → directory structure, languages, most-imported files, most-central symbols (PageRank).
3. **get_repo_health** → dead code %, avg complexity, hotspots, dependency cycles, unstable modules.
4. **get_file_outline** on the 2–3 most-central files → understand the core.
5. **get_class_hierarchy** → inheritance structure (if OOP codebase).
6. **get_dependency_graph** on the entry point file (`direction="importers"`, `depth=2`) → what depends on the core.
7. **search_symbols** with `sort_by="centrality"` → find the most important symbols across the repo.
"""

_ASSESS_PROMPT_TEXT = """\
# Assess — Pre-merge impact analysis

Goal: Understand the blast radius of a change before merging.

**Quick path** (one call): **get_pr_risk_profile** → unified risk score fusing blast radius, \
complexity, churn, test gaps, and change volume. Includes actionable recommendations.

**Deep path** (manual drill-down):
1. **get_changed_symbols** → map the git diff to added/removed/modified/renamed symbols.
2. **get_blast_radius** on each changed file → depth-scored transitive impact + `has_test_reach` per file.
3. **get_impact_preview** on key changed symbols → "what breaks?" analysis.
4. **get_symbol_provenance** on unfamiliar symbols → understand why the code exists before changing it.
5. **check_rename_safe** if any symbols were renamed → verify no broken refs.
6. **get_untested_symbols** on affected files → flag unreached symbols in the blast radius.
7. **get_coupling_metrics** on changed files → check if the change increases coupling.
8. **get_dependency_cycles** → check if the change introduces new cycles.
9. **search_ast** with `category='security'` on changed files → catch hardcoded secrets or eval() calls in the diff.
"""

_TRIAGE_PROMPT_TEXT = """\
# Triage — Diagnose a repo's code quality

Goal: Get a complete health picture in one guided session.

1. **get_repo_health** → one-call snapshot (dead code %, complexity, hotspots, cycles, unstable modules).
2. **find_dead_code** with `min_confidence=0.8` → high-confidence dead code candidates for removal.
3. **get_untested_symbols** → functions with no test-file reachability.
4. **get_dependency_cycles** → full cycle list with file paths.
5. **get_hotspots** with `top_n=10`, `days=90` → highest-risk symbols by complexity × churn.
6. **get_layer_violations** → architectural boundary violations.
7. **get_extraction_candidates** → functions that should be refactored out.
8. **get_coupling_metrics** on hotspot files → instability analysis.
9. **search_ast** with `category='all'` → sweep for anti-patterns (empty catches, god functions, magic numbers, etc.).
"""

_TRACE_PROMPT_TEXT = """\
# Trace — Investigate a bug through the call graph

Goal: Follow a suspected bug from symptom to root cause.

1. **search_symbols** for the function name or error message keyword.
2. **get_symbol_source** on the suspect symbol → read the implementation.
3. **get_call_hierarchy** with `direction="callers"`, `depth=3` → who calls this?
4. **get_call_hierarchy** with `direction="callees"`, `depth=2` → what does it call?
5. **get_context_bundle** on the suspect symbol → full source + imports in one call.
6. **find_references** for the symbol name → all files that reference it.
7. **get_blast_radius** on the suspect file → what else could be affected?
8. **get_symbol_diff** if a recent change is suspected → compare current vs. previous version.
"""


@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """Return available workflow guidance prompts."""
    _signal_handshake()
    return [
        Prompt(
            name="workflow",
            description="Step-by-step guide for using jcodemunch-mcp tools in Claude Code.",
        ),
        Prompt(
            name="explore",
            description="Build a mental model of an unfamiliar repo.",
        ),
        Prompt(
            name="assess",
            description="Pre-merge impact analysis — blast radius, reachability, coupling.",
        ),
        Prompt(
            name="triage",
            description="Diagnose a repo's code quality — dead code, hotspots, cycles.",
        ),
        Prompt(
            name="trace",
            description="Investigate a bug through the call graph from symptom to root cause.",
        ),
    ]


_PROMPT_MAP: dict[str, tuple[str, str]] = {
    "workflow": (_WORKFLOW_PROMPT_TEXT, "jcodemunch-mcp workflow guide for Claude Code."),
    "explore": (_EXPLORE_PROMPT_TEXT, "Explore — build a mental model of an unfamiliar repo."),
    "assess": (_ASSESS_PROMPT_TEXT, "Assess — pre-merge impact analysis."),
    "triage": (_TRIAGE_PROMPT_TEXT, "Triage — diagnose a repo's code quality."),
    "trace": (_TRACE_PROMPT_TEXT, "Trace — investigate a bug through the call graph."),
}


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None = None) -> GetPromptResult:
    """Return the requested prompt content."""
    _signal_handshake()
    entry = _PROMPT_MAP.get(name)
    if entry is None:
        raise ValueError(f"Unknown prompt: {name}")
    text, description = entry
    return GetPromptResult(
        description=description,
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )
        ],
    )


# Tools excluded from auto-watch (no folder target, meta-only, or file-path arg)
_AUTO_WATCH_EXCLUDED = frozenset({
    "list_repos",
    "get_session_stats",
    "get_session_context",
    "get_session_snapshot",
    "index_file",  # path arg is a file path, not a folder; requires repo already indexed
    "analyze_perf",
    "tune_weights",
    "check_embedding_drift",
})

# Tools that index their own `path` argument, so the pre-dispatch hook must not
# index it too (#384, fixed v1.108.189). These are DEFERRED, not excluded: the
# folder is still registered for watching, just AFTER the tool runs and without
# a second indexing pass.
#
# Excluding them outright was the obvious alternative and was rejected — it
# silently removes auto-start-watching from the single most natural way a user
# would ask for a folder to be watched, which is a behaviour removal under the
# 1.x zero-surprise contract. Dropping only the redundant `ensure_indexed` was
# rejected too: the watch task's own initial index would then race the tool's
# index on the same `indexwrite` lock (60s waits), which is plausibly worse than
# the duplicate work it replaces. Deferring avoids both — by the time the watch
# task starts, the tool's index is on disk and there is nothing left to race.
_AUTO_WATCH_DEFERRED = frozenset({
    "index_folder",
})


def _get_source_root(repo: str, storage_path: Optional[str]) -> Optional[str]:
    """Resolve repo ID to folder path using IndexStore public API.

    Returns None if the repo is not indexed.
    """
    # Parse owner/name from repo ID (format: "owner/name" or "local/name-hash")
    parts = repo.split("/", 1)
    if len(parts) != 2:
        return None
    owner, name = parts

    try:
        from .storage import IndexStore
        store = IndexStore(base_path=storage_path)
        return store.get_source_root(owner, name)
    except Exception:
        logger.debug("Failed to resolve source_root for %s", repo, exc_info=True)
        return None


async def _auto_watch_if_needed(
    name: str, arguments: dict, storage_path: Optional[str]
) -> Optional[str]:
    """Auto-watch hook: ensure unwatched repos are indexed before tool execution.

    Hook fires BEFORE tool dispatch to ensure the tool runs against fresh data.

    Returns a folder path when the registration has been DEFERRED to after the
    tool runs (#384) — the caller must hand it to ``_auto_watch_after_tool``.
    Returns None in every other case, including every pre-v1.108.189 path.
    """
    global _watcher_manager

    # Check if watcher is running and auto-watch is enabled
    if _watcher_manager is None:
        return None

    if not config_module.get("watch", False):
        return None

    # Check if tool is excluded
    if name in _AUTO_WATCH_EXCLUDED:
        return None

    # Extract folder from arguments
    folder: Optional[str] = None

    # Path-based tools
    if "path" in arguments:
        try:
            folder = str(Path(arguments["path"]).expanduser().resolve())
        except Exception:
            pass

    # Repo-based tools
    if not folder and "repo" in arguments:
        repo = arguments["repo"]
        if repo:
            folder = _get_source_root(repo, storage_path)

    if not folder:
        return None

    # Check if already watched
    if _watcher_manager.is_watched(folder):
        return None

    # The tool about to run indexes this exact folder itself. Do nothing now;
    # register the watch afterwards, against the index the tool produces (#384).
    if name in _AUTO_WATCH_DEFERRED:
        return folder

    # Index ONCE, then adopt that index (v1.108.191).
    #
    # Both arms of this used to index twice. The takeover arm started a watch
    # task (whose own initial index runs as a concurrent asyncio task) and THEN
    # awaited ensure_indexed on the same folder, putting two writers on the
    # `indexwrite` lock where every acquire waits up to 60s. The fall-through
    # arm awaited ensure_indexed and then called add_folder, whose watch task
    # walked the same tree a second time -- serialized, so not a race, but a
    # redundant full walk on EVERY eager auto-watch, not just index_folder.
    #
    # ensure_indexed is the one to keep: it is awaited (so the tool runs against
    # fresh data, which is this hook's whole purpose) and it is race-safe via
    # the manager's _pending coordination. The watch tasks' initial index is the
    # redundant one, so it is skipped and the caller's index is adopted.
    #
    # Ordering matters: ensure_indexed must complete BEFORE any watch task
    # starts, or the task builds its hash cache from an index that is about to
    # be rewritten underneath it.
    try:
        await _watcher_manager.ensure_indexed(folder)

        # record_index_ready stays False on this path: ensure_indexed already
        # wrote the real reindex record, and the watch task must not overwrite
        # it with a synthetic one.
        maybe_takeover = getattr(_watcher_manager, "maybe_takeover", None)
        if maybe_takeover is not None:
            result = await maybe_takeover(folder, skip_initial_index=True)
            if result.get("status") in {"started", "already_watched"}:
                logger.debug("Auto-watch: indexed, took over %s", folder)
                return None

        await _watcher_manager.add_folder(folder, skip_initial_index=True)
        logger.debug("Auto-watch: indexed and watching %s", folder)
    except Exception:
        logger.debug("Auto-watch failed for %s", folder, exc_info=True)
    return None


async def _auto_watch_after_tool(folder: str) -> None:
    """Register a deferred auto-watch after the indexing tool has run (#384).

    Runs no index of its own: the tool that just completed is what made the
    index current, so a second pass here would be the exact duplication this
    deferral exists to remove.
    """
    global _watcher_manager

    if _watcher_manager is None:
        return
    if _watcher_manager.is_watched(folder):
        return

    try:
        # Standby takeover still applies — another process may hold the lock.
        # Unlike the eager path this does NOT call ensure_indexed afterwards.
        #
        # v1.108.190 (#388, @Bortlesboat): the takeover branch has to skip the
        # initial index too. v1.108.189 covered add_folder and left this path
        # doing a full pass over the tree the tool had just indexed, so the
        # double index survived for exactly the case where another process had
        # been watching the folder. Caught by their independent fix for #384.
        maybe_takeover = getattr(_watcher_manager, "maybe_takeover", None)
        if maybe_takeover is not None:
            result = await maybe_takeover(
                folder, skip_initial_index=True, record_index_ready=True,
            )
            if result.get("status") in {"started", "already_watched"}:
                logger.debug("Auto-watch (deferred): took over %s", folder)
                return

        # record_index_ready=True: the index_folder tool indexes but writes no
        # reindex record, so get_watch_status learns nothing unless the watcher
        # records readiness on its behalf.
        await _watcher_manager.add_folder(
            folder, skip_initial_index=True, record_index_ready=True,
        )
        logger.debug("Auto-watch (deferred): watching %s without reindex", folder)
    except Exception:
        logger.debug("Deferred auto-watch failed for %s", folder, exc_info=True)


# --- Turn-economy steering (v1.108.158) --------------------------------------
# Measured driver (2026-07-22 benchmark-harness run): exploration sessions hop
# search -> outline -> source 2-3x more than a raw baseline, and each MCP round
# trip re-drags the cached context. The one-call openers (get_ranked_context /
# assemble_task_context) existed but no session used them. Steering is
# ADVISORY only: terse, bounded to one nudge per session, never alters dispatch.
_STEER_HOP_TOOLS = frozenset({
    "search_symbols", "search_text", "get_file_outline", "get_symbol_source",
})
_STEER_BUNDLE_TOOLS = frozenset({
    "get_ranked_context", "assemble_task_context", "get_context_bundle", "plan_turn",
})
_STEER_NUDGE_AT = 3
_steer_state: dict = {"hops": 0, "bundles": 0, "nudged": False, "repos": []}


def _steer_note_call(name: str, arguments: dict, result) -> None:
    """Record hop/bundle traffic + resolved repo ids (process == session)."""
    if name in _STEER_HOP_TOOLS:
        _steer_state["hops"] += 1
    elif name in _STEER_BUNDLE_TOOLS:
        _steer_state["bundles"] += 1
    repo = None
    if name == "resolve_repo" and isinstance(result, dict):
        repo = result.get("repo")
    elif isinstance(arguments, dict):
        repo = arguments.get("repo")
    if isinstance(repo, str) and repo and repo not in _steer_state["repos"] and len(_steer_state["repos"]) < 5:
        _steer_state["repos"].append(repo)


def _steer_hint_due(name: str, result) -> bool:
    """One-time advisory: ≥N hop calls, zero bundle calls, on a search response."""
    return (
        name in ("search_symbols", "search_text")
        and isinstance(result, dict)
        and "error" not in result
        and not _steer_state["nudged"]
        and _steer_state["bundles"] == 0
        and _steer_state["hops"] >= _STEER_NUDGE_AT
    )


# Cap on ids listed in the _meta.already_delivered advisory (the count is exact;
# the list is a sample so a broad re-search can't flood the envelope).
_DELIVERY_ANNOTATE_MAX = 20


def _delivery_est_tokens(source) -> int:
    """Bytes/4 of a delivered body — the same scale as the savings meter."""
    return len(source) // 4 if isinstance(source, str) else 0


def _delivery_entries(name: str, result):
    """Yield ``(symbol_id, est_tokens, full_source)`` for a tool response.

    Full-source deliveries are where the bytes are; signature/summary rows are
    recorded so a repeat is still reported, but never priced as redundant
    (docs/prd-cue-anchored-delivery.md §7 Q3).

    Deliberately separate from the ``note_served`` calls above: that record is
    what the handoff contract (#374/#377) attests evidence_refs against, and
    broadening it would change what a handoff can cite.
    """
    if not isinstance(result, dict) or "error" in result:
        return

    def _entry(e: dict):
        sid = e.get("symbol_id") or e.get("id")
        if not sid:
            return None
        src = e.get("source")
        return sid, _delivery_est_tokens(src), bool(src)

    if name == "search_symbols":
        rows = result.get("results", [])
    elif name == "get_ranked_context":
        rows = result.get("context_items", [])
    elif name in ("get_symbol_source", "get_context_bundle"):
        # Both are shape-follows-input: a single id returns a flat object, a
        # batch returns {"symbols": [...]}.
        flat = _entry(result)
        if flat:
            yield flat
        rows = result.get("symbols", [])
    else:
        return

    for row in rows:
        if isinstance(row, dict):
            got = _entry(row)
            if got:
                yield got


async def _handle_counter_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch the Counter front door (order / menu / route)."""
    if name == "order":
        return await _handle_order(arguments)
    if name == "menu":
        return _handle_menu(arguments)
    if name == "route":
        return await _handle_route(arguments)
    return [TextContent(type="text", text=json.dumps({"error": f"Unknown front-door tool '{name}'"}))]


# Common arg-name aliases agents reach for when ordering an action without the
# full schema in front of them (the Counter's whole point is that schemas are
# not resident). Applied only when the alias key is NOT a declared property and
# the target IS. Keep this table tight — confident mappings only.
_ORDER_ARG_ALIASES: dict[str, tuple[str, ...]] = {
    "path": ("file_path", "folder_path", "file_paths"),
    "file": ("file_path", "file_paths"),
    "files": ("file_paths",),
    "folder": ("folder_path", "path"),
    "pattern": ("file_pattern",),
    "text": ("query",),
    "search": ("query",),
    "symbol": ("symbol_id",),
    "symbols": ("symbol_ids",),
    "id": ("symbol_id",),
    "ids": ("symbol_ids",),
}


def _order_action_properties(action: str) -> dict:
    """The action's declared inputSchema properties, from the UNFILTERED catalog
    (under tool_surface=counter, list_tools only carries the front door)."""
    for t in _raw_catalog_tools():
        if t.name == action:
            schema = t.inputSchema or {}
            props = schema.get("properties")
            return props if isinstance(props, dict) else {}
    return {}


def _normalize_order_args(action: str, args: dict) -> dict:
    """Map near-miss arg names onto the action's declared schema.

    Agents calling order() work without resident schemas, so they guess arg
    names ('path' for 'file_path') and the downstream tool blows up with an
    internal error — one wasted turn per session (measured in the codebench
    arm run, 2026-07-22). Rules, applied only when the given key is absent
    from the schema and the target is not already provided:
      1. alias table  2. pluralize  3. singularize  4. unique '_<key>' suffix.
    Then coerce scalar<->single-item-list to match the target's declared type.
    Unmappable keys pass through untouched (permissive tools stay permissive).
    """
    props = _order_action_properties(action)
    if not props:
        return args
    out = dict(args)
    for key in list(out):
        if key in props:
            continue
        target = None
        satisfied = False
        for cand in _ORDER_ARG_ALIASES.get(key, ()):
            if cand not in props:
                continue
            if cand in out:
                # The intended arg is already explicitly provided — mapping the
                # alias to a LATER candidate would hand the tool both forms
                # (e.g. file_path + file_paths). Leave the stray key untouched.
                satisfied = True
                break
            target = cand
            break
        if satisfied:
            continue
        if target is None and key + "s" in props and key + "s" not in out:
            target = key + "s"
        if target is None and key.endswith("s") and key[:-1] in props and key[:-1] not in out:
            target = key[:-1]
        if target is None:
            suffix_hits = [p for p in props if p.endswith("_" + key) and p not in out]
            if len(suffix_hits) == 1:
                target = suffix_hits[0]
        if target is not None:
            out[target] = out.pop(key)
    for k, v in list(out.items()):
        prop = props.get(k)
        if not isinstance(prop, dict):
            continue
        if (
            prop.get("type") == "string"
            and isinstance(v, list)
            and len(v) > 1
            and isinstance(props.get(k + "s"), dict)
            and props[k + "s"].get("type") == "array"
            and k + "s" not in out
        ):
            # A multi-item list handed to a singular prop whose plural sibling
            # exists — order("get_symbol_source", {"symbol_id": [a, b, c]}) —
            # belongs on the plural (was: TypeError unhashable-list downstream).
            out[k + "s"] = out.pop(k)
        elif prop.get("type") == "array" and isinstance(v, str):
            out[k] = [v]
        elif prop.get("type") == "string" and isinstance(v, list) and len(v) == 1 and isinstance(v[0], str):
            out[k] = v[0]
    return out


async def _handle_order(arguments: dict) -> list[TextContent] | CallToolResult:
    """order(action, args): validate against the catalog + charter gate, then
    re-enter the normal pipeline for the resolved action."""
    action = arguments.get("action")
    args = arguments.get("args") or {}
    if not isinstance(args, dict):
        return [TextContent(type="text", text=json.dumps({"error": "order 'args' must be an object."}, indent=2))]
    allow = bool(arguments.get("allow_state_change", False))
    err = _counter.order_gate(action, _catalog_names(), allow)
    if err is not None:
        return [TextContent(type="text", text=json.dumps({"error": err, "tool": "order"}, indent=2))]
    return await call_tool(action, _normalize_order_args(action, dict(args)))


def _handle_menu(arguments: dict) -> list[TextContent]:
    """menu(query?, limit?): search/browse the action catalog."""
    query = arguments.get("query")
    try:
        limit = int(arguments.get("limit", 25))
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(limit, 200))
    rows = _counter.search_catalog(_catalog_rows(), query, limit)
    clean = [{k: v for k, v in r.items() if k != "_description"} for r in rows]
    payload = {
        "tool": "menu",
        "query": query or None,
        "count": len(clean),
        "total_actions": len(_catalog_names()),
        "actions": clean,
        "hint": "Dispatch with order(action, args). Get a task->action pick with route(task).",
    }
    return [TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]


async def _handle_route(arguments: dict) -> list[TextContent] | CallToolResult:
    """route(task, repo?, execute?, model?): intent -> recommended action(s),
    optionally dispatching the top one in the same call."""
    task = arguments.get("task")
    if not task or not isinstance(task, str):
        return [TextContent(type="text", text=json.dumps({"error": "route requires a 'task' string."}, indent=2))]
    repo = arguments.get("repo")
    execute = bool(arguments.get("execute", False))
    names = _catalog_names()
    recs = _counter.classify_intent(task, names)
    if not recs:  # fall back to catalog search when no curated rule matched
        for r in _counter.search_catalog(_catalog_rows(), task, 3):
            recs.append({"action": r["action"], "why": r["summary"]})
    for r in recs:
        tmpl = _counter.shape_execute_args(r["action"], repo, task)
        if tmpl is None:
            # No auto-shaped args; prefer a curated example over a bare hint.
            ex = _counter.example_for(r["action"])
            tmpl = ex if ex is not None else {"repo": repo or "<repo>", "_hint": "see menu for args"}
        r["args_template"] = tmpl
        r["state_changing"] = _counter.is_state_changing(r["action"])
    payload = {"tool": "route", "task": task, "recommended": recs}
    if not recs:
        payload["hint"] = "No confident action match. Call menu(query=...) to browse."
        return [TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]
    if execute:
        action = recs[0]["action"]
        exec_args = _counter.shape_execute_args(action, repo, task)
        if exec_args is None:
            payload["executed"] = False
            payload["execute_error"] = (
                f"Cannot auto-build args for '{action}' from (repo, task). "
                f"Call order('{action}', args) with explicit arguments."
            )
            return [TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]
        if _counter.is_state_changing(action):
            payload["executed"] = False
            payload["execute_error"] = f"Top action '{action}' is state-changing; dispatch it explicitly via order(allow_state_change=true)."
            return [TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]
        model = arguments.get("model")
        if model and action == "plan_turn":
            exec_args["model"] = model
        result = await call_tool(action, exec_args)
        head = TextContent(type="text", text=json.dumps(
            {"tool": "route", "task": task, "executed_action": action, "args": exec_args},
            separators=(",", ":")))
        if isinstance(result, CallToolResult):
            # The routed action failed (isError); surface its content under the
            # route envelope and propagate the error signal rather than list()-ing
            # a non-iterable CallToolResult.
            return CallToolResult(content=[head, *result.content], isError=result.isError)
        return [head] + list(result)
    return [TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]


def _error_call_result(text: str) -> CallToolResult:
    """Wrap an error payload so MCP clients that branch on ``isError`` see the
    failure (F-P01), while the JSON body stays in ``content`` for in-band
    parsers (the v1.108.30 contract). Success results stay a plain
    ``list[TextContent]`` (the SDK wraps them ``isError=False``), so this is
    additive on the wire — only failures gain the ``isError`` signal.
    """
    _record_response_tokens(text)
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


def _record_response_tokens(text: str) -> None:
    """Count served response text toward the session budget (v1.108.146).

    Best-effort — a tracker failure never affects the response.
    """
    try:
        from .storage.token_tracker import record_response_text
        record_response_text(text)
    except Exception:
        logger.debug("Response token recording failed", exc_info=True)


def _response_text_bytes(result: "list[TextContent] | CallToolResult") -> int:
    """UTF-8 byte size of everything a result would put on the wire."""
    content = getattr(result, "content", result)
    if not isinstance(content, list):
        return 0
    total = 0
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            total += len(text.encode("utf-8", errors="replace"))
    return total


def _enforce_response_cap(
    name: str, result: "list[TextContent] | CallToolResult"
) -> "list[TextContent] | CallToolResult":
    """Refuse a single response larger than the configured ceiling (#425).

    ⚠ Applied in the wrapper around the dispatcher rather than inside it, so it
    is immune to early returns BY CONSTRUCTION — the same reason
    ``evidence/producers.mint()`` lives at a chokepoint. The dispatcher has more
    than a dozen ``return`` sites across the MUNCH-encoded, JSON, in-band-error
    and front-door paths; a check placed at any one of them is a check the next
    new branch will not have.

    ⚠ It REFUSES rather than truncating. A shortened body is indistinguishable
    from a complete one to the caller, so silently returning less is the one
    outcome worse than an error here. The error names the actual size, the
    limit, and the key that moves it.

    An already-failing result is passed through untouched: capping an error
    would replace a specific diagnosis with a generic one.
    """
    try:
        from .security import get_max_response_bytes
        limit = get_max_response_bytes()
        if limit <= 0:
            return result  # explicitly uncapped
        size = _response_text_bytes(result)
        if size <= limit:
            return result
        if getattr(result, "isError", False):
            return result
        logger.warning(
            "response_cap: %s produced %d bytes, over the %d-byte limit", name, size, limit
        )
        return _error_call_result(json.dumps({
            "error": (
                f"Response too large: {name} produced {size:,} bytes, over the "
                f"{limit:,}-byte single-response limit."
            ),
            "tool": name,
            "response_bytes": size,
            "response_max_bytes": limit,
            "hint": (
                "Narrow the request (line ranges, filters, a smaller token_budget), "
                "or raise `response_max_bytes` in config.jsonc / "
                "JCODEMUNCH_RESPONSE_MAX_BYTES. This is a RESPONSE limit and is "
                "independent of `max_file_size`, which governs indexing."
            ),
        }, separators=(",", ":")))
    except Exception:
        # A cap that can fail closed would be worse than no cap.
        logger.debug("Response cap check failed; passing result through", exc_info=True)
        return result


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict) -> list[TextContent] | CallToolResult:
    """Dispatch a tool call, then bound the reply it produced.

    Kept as the registered entry point (and the name the front door re-dispatches
    through) so every route into the dispatcher passes the cap.
    """
    from .storage.token_tracker import begin_call_context, end_call_context

    # **The dispatcher is re-entrant.** `order` and executable `route` both re-enter the
    # registered `call_tool`, so one client request can produce two `tool_calls` rows. Giving
    # each entry its own `call_uid` keeps a ranking event joined to exactly one latency row,
    # which is why token-based reset matters: setting `None` in the `finally` would clear the
    # outer entry's value and silently write `NULL` for it. The consequence to expect is that
    # `COUNT(DISTINCT call_uid)` counts dispatcher entries rather than client requests, and
    # the front-door row has no matching ranking event.
    call_token = begin_call_context()
    try:
        return _enforce_response_cap(name, await _call_tool_impl(name, arguments))
    finally:
        end_call_context(call_token)


async def _call_tool_impl(name: str, arguments: dict) -> list[TextContent] | CallToolResult:
    """Handle tool calls."""
    _signal_handshake()
    storage_path = os.environ.get("CODE_INDEX_PATH")
    logger.info("tool_call: %s args=%s", name, {k: v for k, v in arguments.items() if k != "content"})

    _t0_call = time.perf_counter()
    _call_ok = True
    _reporter_ref = None  # progress reporter; drained in finally (#359)
    _deferred_watch = None  # folder to start watching AFTER dispatch (#384)
    try:   # main handler try starts here, before coerce
        # Extract cross-cutting args that are not part of any tool's schema.
        # `format` controls compact-output encoding (see .encoding package).
        _requested_format = None
        if isinstance(arguments, dict) and "format" in arguments:
            # ⚠⚠ COPY BEFORE POPPING. `pop` on the caller's own dict strips
            # `format` from it, so a caller that reuses one args object gets
            # JSON on the first call and whatever `server_output` resolves to
            # on every call after — silently, because the first call proves the
            # argument works. Over the wire each call arrives as a fresh dict
            # and nothing shows; the exposed callers are in-process ones, which
            # includes the Counter front door re-dispatching through here.
            #
            # Found via #482: two tests reusing one `args` dict got a MUNCH
            # payload on their second call and failed in `json.loads` at char 0.
            # ⚠ It only surfaced on 3 of 8 CI legs, because the second call
            # lands on `auto` and the 15% encoding gate then decides per
            # response — so the same defect reads as an environment quirk.
            arguments = dict(arguments)
            _requested_format = arguments.pop("format")
        # Coerce stringified booleans/integers/numbers before routing
        schema = (await _ensure_tool_schemas()).get(name)
        if schema:
            arguments = _coerce_arguments(arguments, schema)
            try:
                jsonschema.validate(instance=arguments, schema=schema)
            except jsonschema.ValidationError as e:
                return _error_call_result(json.dumps(
                    {"error": f"Input validation error: {e.message}"}, indent=2
                ))

        # The Counter front door: order/menu/route. Handled before repo-scoped
        # strict-freshness/auto-watch (the front door isn't repo-scoped; order
        # re-enters call_tool for the real action, which then runs those hooks).
        if name in _COUNTER_FRONT_DOOR:
            return await _handle_counter_tool(name, arguments)

        # Session yield tracking (v1.108.146): repeated identical calls +
        # follow-through/edit-through signals for get_session_stats' `yield`
        # block. Observation only — never alters dispatch.
        try:
            from .storage import token_tracker as _yield_tracker
            if name != "get_session_stats":
                import hashlib as _hashlib
                _sig = _hashlib.sha1(
                    json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:16]
                _yield_tracker.note_call_signature(name, _sig)
            if name in ("get_symbol_source", "get_context_bundle"):
                _fetch_ids = []
                if arguments.get("symbol_id"):
                    _fetch_ids.append(arguments["symbol_id"])
                if isinstance(arguments.get("symbol_ids"), list):
                    _fetch_ids.extend(s for s in arguments["symbol_ids"] if s)
                if _fetch_ids:
                    _yield_tracker.note_fetched(_fetch_ids)
            elif name == "register_edit" and isinstance(arguments.get("file_paths"), list):
                _yield_tracker.note_edited_files(arguments["file_paths"])
            elif name == "index_file" and arguments.get("path"):
                _yield_tracker.note_edited_files([arguments["path"]])
        except Exception:
            logger.debug("Yield tracking failed", exc_info=True)

        # jcm#329: cheap per-tool argument validation BEFORE strict-freshness
        # waits and auto-watch reindexing. A call doomed to instant rejection
        # must not pay unbounded pre-dispatch work first (field report: 29s
        # to reject an over-long regex behind an auto-watch reindex).
        if name == "search_text":
            from .tools.search_text import validate_query_args
            _arg_err = validate_query_args(
                arguments.get("query", ""), bool(arguments.get("is_regex", False))
            )
            if _arg_err is not None:
                return _error_call_result(json.dumps(_arg_err, indent=2))

        # Strict freshness mode: wait for any in-progress reindex to complete
        # before serving query results (except for write/index tools).
        # MUST use asyncio.to_thread — threading.Event.wait() cannot run on the event loop.
        repo_arg = arguments.get("repo")
        if (name not in _EXCLUDED_FROM_STRICT and repo_arg):
            strict_ms = config_module.get("strict_timeout_ms", 500)
            await asyncio.to_thread(await_freshness_if_strict, repo_arg, timeout_ms=strict_ms)

        # Project-level tool disabling: check if tool is disabled for this project
        # Global disabled tools are filtered out in list_tools() schema; project-level
        # rejection happens here since schema is global (can't be changed per-project).
        # `allow_disabling_tier_controls=true` lets users opt out of the
        # _UNDISABLEABLE_TOOLS safety net (issue #299).
        allow_disable_tier = config_module.get("allow_disabling_tier_controls", False, repo=repo_arg)
        protected_at_call = frozenset() if allow_disable_tier else _UNDISABLEABLE_TOOLS
        if name not in protected_at_call and config_module.is_tool_disabled(name, repo=repo_arg):
            return _error_call_result(json.dumps({
                "error": (
                    f"Tool '{name}' is disabled in this project's configuration. "
                    f"Project-level tool disabling is set via the 'disabled_tools' key "
                    f"in the .jcodemunch.jsonc file. Remove '{name}' from 'disabled_tools' to re-enable."
                )
            }, indent=2))

        # Auto-watch: ensure unwatched repos are indexed before tool execution
        try:
            _deferred_watch = await _auto_watch_if_needed(name, arguments, storage_path)
        except Exception:
            logger.debug("Auto-watch check failed", exc_info=True)

        # Progress notifications for long-running tools
        _progress_cb = None
        if name in ("index_repo", "index_folder", "index_file", "embed_repo"):
            try:
                from .progress import (
                    make_progress_notify, ProgressReporter, HeartbeatReporter,
                )
                _label = {"index_repo": "Index", "index_folder": "Index",
                          "index_file": "Index", "embed_repo": "Embed"}[name]
                _progress_notify = make_progress_notify(server)
                if _progress_notify:
                    _reporter = ProgressReporter(_progress_notify, _label)
                    _progress_cb = _reporter.update
                    _reporter_ref = _reporter  # drained in finally (#359)
                else:
                    # v1.108.189 (#383): the client sent no progressToken, so
                    # the spec forbids progress notifications. Fall back to an
                    # elapsed-time heartbeat on the log channel instead of
                    # running silently — silence is what made a healthy long
                    # index indistinguishable from a hang in #375.
                    _heartbeat = HeartbeatReporter(_label)
                    if _heartbeat.enabled:
                        _heartbeat.start()
                        _progress_cb = _heartbeat.update
                        _reporter_ref = _heartbeat  # finished + closed in finally
            except Exception:
                logger.debug("Progress setup failed", exc_info=True)

        if name == "index_repo":
            from .tools.index_repo import index_repo
            result = await index_repo(
                url=arguments["url"],
                use_ai_summaries=arguments.get("use_ai_summaries", _default_use_ai_summaries()),
                storage_path=storage_path,
                incremental=arguments.get("incremental", True),
                extra_ignore_patterns=arguments.get("extra_ignore_patterns"),
                progress_cb=_progress_cb,
                max_size=arguments.get("max_size"),
            )
            _result_cache_invalidate()
        elif name == "index_folder":
            from .tools.index_folder import index_folder
            _ai = arguments.get("use_ai_summaries", _default_use_ai_summaries())
            result = await asyncio.to_thread(
                functools.partial(
                    index_folder,
                    path=arguments["path"],
                    use_ai_summaries=_ai,
                    storage_path=storage_path,
                    extra_ignore_patterns=arguments.get("extra_ignore_patterns"),
                    follow_symlinks=arguments.get("follow_symlinks", False),
                    incremental=arguments.get("incremental", True),
                    paths=arguments.get("paths"),
                    identity_mode=arguments.get("identity_mode", "config"),
                    progress_cb=_progress_cb,
                    max_size=arguments.get("max_size"),
                )
            )
            _result_cache_invalidate()
        elif name == "summarize_repo":
            from .tools.summarize_repo import summarize_repo
            result = await asyncio.to_thread(
                functools.partial(
                    summarize_repo,
                    repo=arguments["repo"],
                    force=arguments.get("force", False),
                    storage_path=storage_path,
                )
            )
        elif name == "index_file":
            from .tools.index_file import index_file
            _ai = arguments.get("use_ai_summaries", _default_use_ai_summaries())
            result = await asyncio.to_thread(
                functools.partial(
                    index_file,
                    path=arguments["path"],
                    use_ai_summaries=_ai,
                    storage_path=storage_path,
                    context_providers=arguments.get("context_providers", True),
                    progress_cb=_progress_cb,
                )
            )
            _result_cache_invalidate()
        elif name == "index_dependency":
            from .tools.index_dependency import index_dependency
            result = await asyncio.to_thread(
                functools.partial(
                    index_dependency,
                    repo=arguments["repo"],
                    package=arguments["package"],
                    ecosystem=arguments.get("ecosystem", "auto"),
                    max_files=arguments.get("max_files", 2000),
                    storage_path=storage_path,
                )
            )
            _result_cache_invalidate()
        elif name == "import_runtime_signal":
            from .tools.import_runtime_signal import import_runtime_signal
            result = await asyncio.to_thread(
                functools.partial(
                    import_runtime_signal,
                    source=arguments.get("source", "otel"),
                    path=arguments["path"],
                    repo=arguments.get("repo"),
                    redact_enabled=arguments.get("redact_enabled"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_runtime_coverage":
            from .tools.get_runtime_coverage import get_runtime_coverage
            result = await asyncio.to_thread(
                functools.partial(
                    get_runtime_coverage,
                    repo=arguments["repo"],
                    file_path=arguments.get("file_path"),
                    unmapped_limit=arguments.get("unmapped_limit", 50),
                    storage_path=storage_path,
                )
            )
        elif name == "find_hot_paths":
            from .tools.find_hot_paths import find_hot_paths
            result = await asyncio.to_thread(
                functools.partial(
                    find_hot_paths,
                    repo=arguments["repo"],
                    query=arguments.get("query"),
                    top_n=arguments.get("top_n", 20),
                    storage_path=storage_path,
                )
            )
        elif name == "find_unused_paths":
            from .tools.find_unused_paths import find_unused_paths
            result = await asyncio.to_thread(
                functools.partial(
                    find_unused_paths,
                    repo=arguments["repo"],
                    since_days=arguments.get("since_days", 90),
                    include_tests=arguments.get("include_tests", False),
                    include_entry_points=arguments.get("include_entry_points", False),
                    max_results=arguments.get("max_results", 200),
                    storage_path=storage_path,
                )
            )
        elif name == "get_redaction_log":
            from .tools.get_redaction_log import get_redaction_log
            result = await asyncio.to_thread(
                functools.partial(
                    get_redaction_log,
                    repo=arguments["repo"],
                    source=arguments.get("source"),
                    since_days=arguments.get("since_days", 30),
                    storage_path=storage_path,
                )
            )
        elif name == "list_repos":
            from .tools.list_repos import list_repos
            result = await asyncio.to_thread(
                functools.partial(list_repos, storage_path=storage_path)
            )
        elif name == "get_watch_status":
            from .tools.get_watch_status import get_watch_status
            result = await asyncio.to_thread(
                functools.partial(get_watch_status, storage_path=storage_path)
            )
        elif name == "resolve_repo":
            from .tools.resolve_repo import resolve_repo
            result = await asyncio.to_thread(
                functools.partial(
                    resolve_repo,
                    path=arguments["path"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_file_tree":
            from .tools.get_file_tree import get_file_tree
            result = await asyncio.to_thread(
                functools.partial(
                    get_file_tree,
                    repo=arguments["repo"],
                    path_prefix=arguments.get("path_prefix", ""),
                    include_summaries=arguments.get("include_summaries", False),
                    max_files=arguments.get("max_files"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_file_outline":
            from .tools.get_file_outline import get_file_outline
            result = await asyncio.to_thread(
                functools.partial(
                    get_file_outline,
                    repo=arguments["repo"],
                    file_path=arguments.get("file_path") or arguments.get("file"),
                    file_paths=arguments.get("file_paths"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_file_content":
            from .tools.get_file_content import get_file_content
            result = await asyncio.to_thread(
                functools.partial(
                    get_file_content,
                    repo=arguments["repo"],
                    file_path=arguments["file_path"],
                    start_line=arguments.get("start_line"),
                    end_line=arguments.get("end_line"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_symbol_source":
            from .tools.get_symbol import get_symbol_source
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol_source,
                    repo=arguments["repo"],
                    symbol_id=arguments.get("symbol_id"),
                    symbol_ids=arguments.get("symbol_ids"),
                    verify=arguments.get("verify", False),
                    verify_against=arguments.get("verify_against", "cache"),
                    context_lines=arguments.get("context_lines", 0),
                    storage_path=storage_path,
                    fqn=arguments.get("fqn"),
                    source_start_line=arguments.get("source_start_line"),
                    source_end_line=arguments.get("source_end_line"),
                    max_source_lines=arguments.get("max_source_lines"),
                    max_source_bytes=arguments.get("max_source_bytes"),
                    max_total_source_bytes=arguments.get("max_total_source_bytes"),
                )
            )
        elif name == "search_symbols":
            from .tools.search_symbols import search_symbols
            kind_filter = arguments.get("kind")
            if kind_filter and kind_filter not in VALID_KINDS:
                result = {"error": f"Unknown kind '{kind_filter}'. Valid values: {sorted(VALID_KINDS)}"}
            else:
                result = await asyncio.to_thread(
                    functools.partial(
                        search_symbols,
                        repo=arguments["repo"],
                        query=arguments["query"],
                        kind=kind_filter,
                        file_pattern=arguments.get("file_pattern"),
                        language=arguments.get("language"),
                        decorator=arguments.get("decorator"),
                        max_results=arguments.get("max_results", 10),
                        token_budget=arguments.get("token_budget"),
                        detail_level=arguments.get("detail_level", "standard"),
                        debug=arguments.get("debug", False),
                        fuzzy=arguments.get("fuzzy", False),
                        fuzzy_threshold=arguments.get("fuzzy_threshold", 0.4),
                        max_edit_distance=arguments.get("max_edit_distance", 2),
                        sort_by=arguments.get("sort_by", "relevance"),
                        semantic=arguments.get("semantic", False),
                        semantic_weight=arguments.get("semantic_weight", 0.5),
                        semantic_only=arguments.get("semantic_only", False),
                        fusion=arguments.get("fusion", False),
                        storage_path=storage_path,
                        fqn=arguments.get("fqn"),
                    )
                )
        elif name == "invalidate_cache":
            from .tools.invalidate_cache import invalidate_cache
            result = await asyncio.to_thread(
                functools.partial(
                    invalidate_cache,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
            _result_cache_invalidate()
        elif name == "search_text":
            from .tools.search_text import search_text
            result = await asyncio.to_thread(
                functools.partial(
                    search_text,
                    repo=arguments["repo"],
                    query=arguments["query"],
                    file_pattern=arguments.get("file_pattern"),
                    max_results=arguments.get("max_results", 20),
                    context_lines=arguments.get("context_lines", 0),
                    is_regex=arguments.get("is_regex", False),
                    storage_path=storage_path,
                )
            )
        elif name == "get_repo_outline":
            from .tools.get_repo_outline import get_repo_outline
            result = await asyncio.to_thread(
                functools.partial(
                    get_repo_outline,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
        elif name == "find_importers":
            from .tools.find_importers import find_importers
            result = await asyncio.to_thread(
                functools.partial(
                    find_importers,
                    repo=arguments["repo"],
                    file_path=arguments.get("file_path"),
                    file_paths=arguments.get("file_paths"),
                    max_results=arguments.get("max_results", 50),
                    storage_path=storage_path,
                    cross_repo=arguments.get("cross_repo"),
                )
            )
        elif name == "find_references":
            from .tools.find_references import find_references
            result = await asyncio.to_thread(
                functools.partial(
                    find_references,
                    repo=arguments["repo"],
                    identifier=arguments.get("identifier"),
                    identifiers=arguments.get("identifiers"),
                    max_results=arguments.get("max_results", 50),
                    storage_path=storage_path,
                    include_call_chain=arguments.get("include_call_chain", False),
                )
            )
        elif name == "check_references":
            from .tools.check_references import check_references
            result = await asyncio.to_thread(
                functools.partial(
                    check_references,
                    repo=arguments["repo"],
                    identifier=arguments.get("identifier"),
                    identifiers=arguments.get("identifiers"),
                    search_content=arguments.get("search_content", True),
                    max_content_results=arguments.get("max_content_results", 20),
                    storage_path=storage_path,
                )
            )
        elif name == "search_columns":
            from .tools.search_columns import search_columns
            result = await asyncio.to_thread(
                functools.partial(
                    search_columns,
                    repo=arguments["repo"],
                    query=arguments["query"],
                    model_pattern=arguments.get("model_pattern"),
                    max_results=arguments.get("max_results", 20),
                    storage_path=storage_path,
                )
            )
        elif name == "get_context_bundle":
            from .tools.get_context_bundle import get_context_bundle
            result = await asyncio.to_thread(
                functools.partial(
                    get_context_bundle,
                    repo=arguments["repo"],
                    symbol_id=arguments.get("symbol_id"),
                    symbol_ids=arguments.get("symbol_ids"),
                    include_callers=arguments.get("include_callers", False),
                    output_format=arguments.get("output_format", "json"),
                    token_budget=arguments.get("token_budget"),
                    budget_strategy=arguments.get("budget_strategy", "most_relevant"),
                    include_budget_report=arguments.get("include_budget_report", False),
                    storage_path=storage_path,
                    fqn=arguments.get("fqn"),
                )
            )
        elif name == "get_ranked_context":
            from .tools.get_ranked_context import get_ranked_context
            result = await asyncio.to_thread(
                functools.partial(
                    get_ranked_context,
                    repo=arguments["repo"],
                    query=arguments["query"],
                    token_budget=arguments.get("token_budget", 4000),
                    strategy=arguments.get("strategy", "combined"),
                    include_kinds=arguments.get("include_kinds"),
                    scope=arguments.get("scope"),
                    fusion=arguments.get("fusion", False),
                    compress=arguments.get("compress", False),
                    storage_path=storage_path,
                )
            )
        elif name == "assemble_task_context":
            from .tools.assemble_task_context import assemble_task_context
            result = await asyncio.to_thread(
                functools.partial(
                    assemble_task_context,
                    repo=arguments["repo"],
                    task=arguments["task"],
                    symbols=arguments.get("symbols"),
                    intent=arguments.get("intent"),
                    token_budget=arguments.get("token_budget", 8000),
                    include=arguments.get("include"),
                    cross_repo=arguments.get("cross_repo", False),
                    storage_path=storage_path,
                )
            )
        elif name == "get_session_stats":
            from .tools.get_session_stats import get_session_stats
            result = await asyncio.to_thread(
                functools.partial(
                    get_session_stats,
                    storage_path=storage_path,
                )
            )
            # Tool-surface schema receipt (v1.108.153). Advisory only — a
            # failure here must never break the stats tool.
            try:
                result["tool_surface"] = _tool_surface_stats()
            except Exception:
                logger.debug("tool_surface stats failed", exc_info=True)
        elif name == "analyze_perf":
            from .tools.analyze_perf import analyze_perf
            result = await asyncio.to_thread(
                functools.partial(
                    analyze_perf,
                    window=arguments.get("window", "session"),
                    top=arguments.get("top", 20),
                    tool=arguments.get("tool"),
                    storage_path=storage_path,
                    compare_release=arguments.get("compare_release"),
                    ledger=arguments.get("ledger", False),
                )
            )
        elif name == "tune_weights":
            from .tools.tune_weights import tune_weights
            result = await asyncio.to_thread(
                functools.partial(
                    tune_weights,
                    repo=arguments.get("repo"),
                    dry_run=arguments.get("dry_run", False),
                    min_events=arguments.get("min_events", 50),
                    explain=arguments.get("explain", False),
                    max_age_days=arguments.get("max_age_days", 90),
                    storage_path=storage_path,
                )
            )
        elif name == "check_embedding_drift":
            from .tools.check_embedding_drift import check_embedding_drift
            result = await asyncio.to_thread(
                functools.partial(
                    check_embedding_drift,
                    capture=arguments.get("capture", False),
                    force=arguments.get("force", False),
                    threshold=arguments.get("threshold", 0.05),
                    storage_path=storage_path,
                )
            )
        elif name == "get_session_context":
            from .tools.get_session_context import get_session_context
            result = await asyncio.to_thread(
                functools.partial(
                    get_session_context,
                    max_files=arguments.get("max_files", 50),
                    max_queries=arguments.get("max_queries", 20),
                    storage_path=storage_path,
                )
            )
        elif name == "get_session_snapshot":
            from .tools.get_session_snapshot import get_session_snapshot
            result = await asyncio.to_thread(
                functools.partial(
                    get_session_snapshot,
                    max_files=arguments.get("max_files", 10),
                    max_searches=arguments.get("max_searches", 5),
                    max_edits=arguments.get("max_edits", 10),
                    include_negative_evidence=arguments.get("include_negative_evidence", True),
                    storage_path=storage_path,
                )
            )
        elif name == "get_file_risk":
            from .tools.get_file_risk import get_file_risk
            result = await asyncio.to_thread(
                functools.partial(
                    get_file_risk,
                    repo=arguments["repo"],
                    file_path=arguments["file_path"],
                    storage_path=storage_path,
                )
            )
        elif name == "diff_health_radar":
            from .tools.health_radar import diff_health_radar
            result = await asyncio.to_thread(
                functools.partial(
                    diff_health_radar,
                    baseline=arguments["baseline"],
                    current=arguments["current"],
                )
            )
        elif name == "finalize_handoff":
            from . import handoff as _handoff
            from .storage import token_tracker as _handoff_tracker
            result = _handoff.finalize_handoff(
                repo=arguments["repo"],
                task=arguments["task"],
                sections=arguments["sections"],
                evidence_refs=arguments["evidence_refs"],
                profile=arguments.get("profile", "general"),
                appendices=arguments.get("appendices"),
                served_ids=_handoff_tracker.served_symbol_ids(),
            )
        elif name == "digest":
            from .tools.digest import compose_digest
            result = await asyncio.to_thread(
                functools.partial(
                    compose_digest,
                    repo=arguments["repo"],
                    since_sha=arguments.get("since_sha"),
                    max_changed_files=arguments.get("max_changed_files", 5),
                    max_hotspots=arguments.get("max_hotspots", 3),
                    max_dead_code=arguments.get("max_dead_code", 3),
                    storage_path=storage_path,
                )
            )
        elif name == "plan_turn":
            from .tools.plan_turn import plan_turn
            # Extract model for tier-switch piggyback before passing to plan_turn
            model = arguments.pop("model", None) if isinstance(arguments, dict) else None
            result = await asyncio.to_thread(
                functools.partial(
                    plan_turn,
                    repo=arguments["repo"],
                    query=arguments["query"],
                    max_recommended=arguments.get("max_recommended", 5),
                    storage_path=storage_path,
                )
            )
            announcement = None
            if isinstance(model, str) and model:
                announcement = await _apply_model_announcement(model)
            if announcement is not None and isinstance(result, dict):
                result["tier_announcement"] = announcement
        elif name == "register_edit":
            from .tools.register_edit import register_edit
            result = await asyncio.to_thread(
                functools.partial(
                    register_edit,
                    repo=arguments["repo"],
                    file_paths=arguments["file_paths"],
                    reindex=arguments.get("reindex", False),
                    storage_path=storage_path,
                )
            )
        elif name == "test_summarizer":
            from .tools.test_summarizer import test_summarizer
            result = await asyncio.to_thread(
                functools.partial(
                    test_summarizer,
                    timeout_ms=arguments.get("timeout_ms", 15000),
                )
            )
        elif name == "audit_agent_config":
            from .tools.audit_agent_config import audit_agent_config
            result = await asyncio.to_thread(
                functools.partial(
                    audit_agent_config,
                    repo=arguments.get("repo"),
                    project_path=arguments.get("project_path"),
                    storage_path=storage_path,
                )
            )
        elif name == "suggest_corrections":
            from .tools.suggest_corrections import suggest_corrections
            result = await asyncio.to_thread(
                functools.partial(
                    suggest_corrections,
                    repo=arguments.get("repo"),
                    project_path=arguments.get("project_path"),
                    storage_path=storage_path,
                    window_days=arguments.get("window_days", 30),
                    all_time=arguments.get("all_time", False),
                    apply_weights=arguments.get("apply_weights", False),
                )
            )
        elif name == "get_dependency_graph":
            from .tools.get_dependency_graph import get_dependency_graph
            result = await asyncio.to_thread(
                functools.partial(
                    get_dependency_graph,
                    repo=arguments["repo"],
                    file=arguments["file"],
                    direction=arguments.get("direction", "imports"),
                    depth=arguments.get("depth", 1),
                    storage_path=storage_path,
                    cross_repo=arguments.get("cross_repo"),
                )
            )
        elif name == "get_blast_radius":
            from .tools.get_blast_radius import get_blast_radius
            result = await asyncio.to_thread(
                functools.partial(
                    get_blast_radius,
                    repo=arguments["repo"],
                    symbol=arguments["symbol"],
                    depth=arguments.get("depth", 1),
                    include_depth_scores=arguments.get("include_depth_scores", False),
                    storage_path=storage_path,
                    cross_repo=arguments.get("cross_repo"),
                    call_depth=arguments.get("call_depth", 0),
                    fqn=arguments.get("fqn"),
                    decorator_filter=arguments.get("decorator_filter"),
                    include_source=arguments.get("include_source", False),
                    source_budget=arguments.get("source_budget", 8000),
                    include_decisions=arguments.get("include_decisions", False),
                )
            )
        elif name == "get_call_hierarchy":
            from .tools.get_call_hierarchy import get_call_hierarchy
            result = await asyncio.to_thread(
                functools.partial(
                    get_call_hierarchy,
                    repo=arguments["repo"],
                    symbol_id=arguments["symbol_id"],
                    direction=arguments.get("direction", "both"),
                    depth=arguments.get("depth", 3),
                    storage_path=storage_path,
                )
            )
        elif name == "get_impact_preview":
            from .tools.get_impact_preview import get_impact_preview
            result = await asyncio.to_thread(
                functools.partial(
                    get_impact_preview,
                    repo=arguments["repo"],
                    symbol_id=arguments["symbol_id"],
                    storage_path=storage_path,
                    include_decisions=arguments.get("include_decisions", False),
                )
            )
        elif name == "get_symbol_provenance":
            from .tools.get_symbol_provenance import get_symbol_provenance
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol_provenance,
                    repo=arguments["repo"],
                    symbol=arguments["symbol"],
                    max_commits=arguments.get("max_commits", 25),
                    storage_path=storage_path,
                )
            )
        elif name == "get_pr_risk_profile":
            from .tools.get_pr_risk_profile import get_pr_risk_profile
            result = await asyncio.to_thread(
                functools.partial(
                    get_pr_risk_profile,
                    repo=arguments["repo"],
                    base_ref=arguments.get("base_ref"),
                    head_ref=arguments.get("head_ref", "HEAD"),
                    days=arguments.get("days", 90),
                    storage_path=storage_path,
                )
            )
        elif name == "get_dependency_cycles":
            from .tools.get_dependency_cycles import get_dependency_cycles
            result = await asyncio.to_thread(
                functools.partial(
                    get_dependency_cycles,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_coupling_metrics":
            from .tools.get_coupling_metrics import get_coupling_metrics
            result = await asyncio.to_thread(
                functools.partial(
                    get_coupling_metrics,
                    repo=arguments["repo"],
                    module_path=arguments["module_path"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_layer_violations":
            from .tools.get_layer_violations import get_layer_violations
            result = await asyncio.to_thread(
                functools.partial(
                    get_layer_violations,
                    repo=arguments["repo"],
                    rules=arguments.get("rules"),
                    storage_path=storage_path,
                )
            )
        elif name == "check_rename_safe":
            from .tools.check_rename_safe import check_rename_safe
            result = await asyncio.to_thread(
                functools.partial(
                    check_rename_safe,
                    repo=arguments["repo"],
                    symbol_id=arguments["symbol_id"],
                    new_name=arguments["new_name"],
                    storage_path=storage_path,
                )
            )
        elif name == "check_delete_safe":
            from .tools.check_delete_safe import check_delete_safe
            result = await asyncio.to_thread(
                functools.partial(
                    check_delete_safe,
                    repo=arguments["repo"],
                    symbol=arguments["symbol"],
                    cross_repo=arguments.get("cross_repo", True),
                    include_runtime=arguments.get("include_runtime", True),
                    storage_path=storage_path,
                )
            )
        elif name == "check_edit_safe":
            from .tools.check_edit_safe import check_edit_safe
            result = await asyncio.to_thread(
                functools.partial(
                    check_edit_safe,
                    repo=arguments["repo"],
                    symbol=arguments["symbol"],
                    cross_repo=arguments.get("cross_repo", True),
                    include_runtime=arguments.get("include_runtime", True),
                    storage_path=storage_path,
                )
            )
        elif name == "find_implementations":
            from .tools.find_implementations import find_implementations
            result = await asyncio.to_thread(
                functools.partial(
                    find_implementations,
                    repo=arguments["repo"],
                    symbol=arguments["symbol"],
                    relationship_kinds=arguments.get("relationship_kinds"),
                    include_subclasses=arguments.get("include_subclasses", True),
                    cross_repo=arguments.get("cross_repo", False),
                    rank_by_importance=arguments.get("rank_by_importance", True),
                    max_results=arguments.get("max_results", 50),
                    token_budget=arguments.get("token_budget", 4000),
                    storage_path=storage_path,
                )
            )
        elif name == "plan_refactoring":
            from .tools.plan_refactoring import plan_refactoring
            result = await asyncio.to_thread(
                functools.partial(
                    plan_refactoring,
                    repo=arguments["repo"],
                    symbol=arguments["symbol"],
                    refactor_type=arguments["refactor_type"],
                    new_name=arguments.get("new_name"),
                    new_file=arguments.get("new_file"),
                    new_signature=arguments.get("new_signature"),
                    depth=arguments.get("depth", 2),
                    storage_path=storage_path,
                )
            )
        elif name == "get_dead_code_v2":
            from .tools.get_dead_code_v2 import get_dead_code_v2
            result = await asyncio.to_thread(
                functools.partial(
                    get_dead_code_v2,
                    repo=arguments["repo"],
                    min_confidence=arguments.get("min_confidence", 0.5),
                    include_tests=arguments.get("include_tests", False),
                    max_results=arguments.get("max_results", 100),
                    file_pattern=arguments.get("file_pattern"),
                    storage_path=storage_path,
                    degeneracy_cutoff=arguments.get("degeneracy_cutoff"),
                    entry_point_patterns=arguments.get("entry_point_patterns"),
                )
            )
        elif name == "get_extraction_candidates":
            from .tools.get_extraction_candidates import get_extraction_candidates
            result = await asyncio.to_thread(
                functools.partial(
                    get_extraction_candidates,
                    repo=arguments["repo"],
                    file_path=arguments["file_path"],
                    min_complexity=arguments.get("min_complexity", 5),
                    min_callers=arguments.get("min_callers", 2),
                    storage_path=storage_path,
                )
            )
        elif name == "get_symbol_complexity":
            from .tools.get_symbol_complexity import get_symbol_complexity
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol_complexity,
                    repo=arguments["repo"],
                    symbol_id=arguments["symbol_id"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_churn_rate":
            from .tools.get_churn_rate import get_churn_rate
            result = await asyncio.to_thread(
                functools.partial(
                    get_churn_rate,
                    repo=arguments["repo"],
                    target=arguments["target"],
                    days=arguments.get("days", 90),
                    storage_path=storage_path,
                )
            )
        elif name == "get_delivery_metrics":
            from .tools.get_delivery_metrics import get_delivery_metrics
            result = await asyncio.to_thread(
                functools.partial(
                    get_delivery_metrics,
                    repo=arguments["repo"],
                    window_days=arguments.get("window_days", 30),
                    rework_horizon_days=arguments.get("rework_horizon_days", 14),
                    storage_path=storage_path,
                )
            )
        elif name == "get_parity_map":
            from .tools.get_parity_map import get_parity_map
            result = await asyncio.to_thread(
                functools.partial(
                    get_parity_map,
                    source_repo=arguments["source_repo"],
                    target_repo=arguments["target_repo"],
                    source_path=arguments.get("source_path"),
                    target_path=arguments.get("target_path"),
                    match_threshold=arguments.get("match_threshold", 0.75),
                    divergence=arguments.get("divergence", "signature"),
                    rename=arguments.get("rename", True),
                    include_port_plan=arguments.get("include_port_plan", True),
                    storage_path=storage_path,
                )
            )
        elif name == "get_decorator_census":
            from .tools.get_decorator_census import get_decorator_census
            result = await asyncio.to_thread(
                functools.partial(
                    get_decorator_census,
                    repo=arguments["repo"],
                    name_filter=arguments.get("name_filter"),
                    scope_path=arguments.get("scope_path"),
                    kind=arguments.get("kind"),
                    include_sites=arguments.get("include_sites", False),
                    max_decorators=arguments.get("max_decorators", 100),
                    max_sites_per=arguments.get("max_sites_per", 50),
                    storage_path=storage_path,
                )
            )
        elif name == "get_architecture_metrics":
            from .tools.get_architecture_metrics import get_architecture_metrics
            result = await asyncio.to_thread(
                functools.partial(
                    get_architecture_metrics,
                    repo=arguments["repo"],
                    top_n=arguments.get("top_n", 10),
                    storage_path=storage_path,
                )
            )
        elif name == "get_hotspots":
            from .tools.get_hotspots import get_hotspots
            result = await asyncio.to_thread(
                functools.partial(
                    get_hotspots,
                    repo=arguments["repo"],
                    top_n=arguments.get("top_n", 20),
                    days=arguments.get("days", 90),
                    min_complexity=arguments.get("min_complexity", 2),
                    storage_path=storage_path,
                )
            )
        elif name == "get_repo_health":
            from .tools.get_repo_health import get_repo_health
            result = await asyncio.to_thread(
                functools.partial(
                    get_repo_health,
                    repo=arguments["repo"],
                    days=arguments.get("days", 90),
                    storage_path=storage_path,
                )
            )
        elif name == "get_symbol_diff":
            from .tools.get_symbol_diff import get_symbol_diff
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol_diff,
                    repo_a=arguments["repo_a"],
                    repo_b=arguments["repo_b"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_class_hierarchy":
            from .tools.get_class_hierarchy import get_class_hierarchy
            result = await asyncio.to_thread(
                functools.partial(
                    get_class_hierarchy,
                    repo=arguments["repo"],
                    class_name=arguments["class_name"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_related_symbols":
            from .tools.get_related_symbols import get_related_symbols
            result = await asyncio.to_thread(
                functools.partial(
                    get_related_symbols,
                    repo=arguments["repo"],
                    symbol_id=arguments["symbol_id"],
                    max_results=arguments.get("max_results", 10),
                    storage_path=storage_path,
                )
            )
        elif name == "suggest_queries":
            from .tools.suggest_queries import suggest_queries
            result = await asyncio.to_thread(
                functools.partial(
                    suggest_queries,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_symbol_importance":
            from .tools.get_symbol_importance import get_symbol_importance
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol_importance,
                    repo=arguments["repo"],
                    top_n=arguments.get("top_n", 20),
                    algorithm=arguments.get("algorithm", "pagerank"),
                    scope=arguments.get("scope"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_repo_map":
            from .tools.get_repo_map import get_repo_map
            result = await asyncio.to_thread(
                functools.partial(
                    get_repo_map,
                    repo=arguments["repo"],
                    token_budget=arguments.get("token_budget", 2048),
                    scope=arguments.get("scope"),
                    max_per_file=arguments.get("max_per_file", 5),
                    include_kinds=arguments.get("include_kinds"),
                    storage_path=storage_path,
                )
            )
        elif name == "find_similar_symbols":
            from .tools.find_similar_symbols import find_similar_symbols
            result = await asyncio.to_thread(
                functools.partial(
                    find_similar_symbols,
                    repo=arguments["repo"],
                    threshold=arguments.get("threshold", 0.80),
                    min_size=arguments.get("min_size", 30),
                    max_clusters=arguments.get("max_clusters", 25),
                    include_tests=arguments.get("include_tests", False),
                    scope=arguments.get("scope"),
                    include_kinds=arguments.get("include_kinds"),
                    semantic_weight=arguments.get("semantic_weight", 0.6),
                    token_budget=arguments.get("token_budget", 4000),
                    storage_path=storage_path,
                )
            )
        elif name == "find_dead_code":
            from .tools.find_dead_code import find_dead_code
            result = await asyncio.to_thread(
                functools.partial(
                    find_dead_code,
                    repo=arguments["repo"],
                    granularity=arguments.get("granularity", "symbol"),
                    min_confidence=arguments.get("min_confidence", 0.8),
                    include_tests=arguments.get("include_tests", False),
                    entry_point_patterns=arguments.get("entry_point_patterns"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_untested_symbols":
            from .tools.get_untested_symbols import get_untested_symbols
            result = await asyncio.to_thread(
                functools.partial(
                    get_untested_symbols,
                    repo=arguments["repo"],
                    file_pattern=arguments.get("file_pattern"),
                    min_confidence=arguments.get("min_confidence", 0.5),
                    max_results=arguments.get("max_results", 100),
                    storage_path=storage_path,
                )
            )
        elif name == "search_ast":
            from .tools.search_ast import search_ast
            result = await asyncio.to_thread(
                functools.partial(
                    search_ast,
                    repo=arguments["repo"],
                    pattern=arguments.get("pattern"),
                    category=arguments.get("category"),
                    language=arguments.get("language"),
                    file_pattern=arguments.get("file_pattern"),
                    max_results=arguments.get("max_results", 50),
                    storage_path=storage_path,
                )
            )
        elif name == "get_changed_symbols":
            from .tools.get_changed_symbols import get_changed_symbols
            result = await asyncio.to_thread(
                functools.partial(
                    get_changed_symbols,
                    repo=arguments["repo"],
                    since_sha=arguments.get("since_sha"),
                    until_sha=arguments.get("until_sha", "HEAD"),
                    include_blast_radius=arguments.get("include_blast_radius", False),
                    max_blast_depth=arguments.get("max_blast_depth", 3),
                    storage_path=storage_path,
                )
            )
        elif name == "embed_repo":
            from .tools.embed_repo import embed_repo
            result = await asyncio.to_thread(
                functools.partial(
                    embed_repo,
                    repo=arguments["repo"],
                    batch_size=arguments.get("batch_size", 50),
                    force=arguments.get("force", False),
                    storage_path=storage_path,
                    progress_cb=_progress_cb,
                )
            )
        elif name == "get_cross_repo_map":
            from .tools.get_cross_repo_map import get_cross_repo_map
            result = await asyncio.to_thread(
                functools.partial(
                    get_cross_repo_map,
                    repo=arguments.get("repo"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_group_contracts":
            from .tools.get_group_contracts import get_group_contracts
            result = await asyncio.to_thread(
                functools.partial(
                    get_group_contracts,
                    repos=arguments.get("repos") or [],
                    min_importers=arguments.get("min_importers", 2),
                    include_internal=arguments.get("include_internal", True),
                    include_dead_contracts=arguments.get("include_dead_contracts", False),
                    classify=arguments.get("classify", True),
                    churn_days=arguments.get("churn_days", 90),
                    max_contracts=arguments.get("max_contracts", 50),
                    token_budget=arguments.get("token_budget", 4000),
                    storage_path=storage_path,
                )
            )
        elif name == "get_tectonic_map":
            from .tools.get_tectonic_map import get_tectonic_map
            result = await asyncio.to_thread(
                functools.partial(
                    get_tectonic_map,
                    repo=arguments["repo"],
                    days=arguments.get("days", 90),
                    min_plate_size=arguments.get("min_plate_size", 2),
                    storage_path=storage_path,
                )
            )
        elif name == "get_signal_chains":
            from .tools.get_signal_chains import get_signal_chains
            result = await asyncio.to_thread(
                functools.partial(
                    get_signal_chains,
                    repo=arguments["repo"],
                    symbol=arguments.get("symbol"),
                    kind=arguments.get("kind"),
                    max_depth=arguments.get("max_depth", 5),
                    include_tests=arguments.get("include_tests", False),
                    include_flow_edges=arguments.get("include_flow_edges", True),
                    storage_path=storage_path,
                )
            )
        elif name == "get_endpoint_impact":
            from .tools.get_endpoint_impact import get_endpoint_impact
            result = await asyncio.to_thread(
                functools.partial(
                    get_endpoint_impact,
                    repo=arguments["repo"],
                    endpoint=arguments.get("endpoint"),
                    handler_symbol_id=arguments.get("handler_symbol_id"),
                    depth=arguments.get("depth", 1),
                    call_depth=arguments.get("call_depth", 2),
                    include_infra=arguments.get("include_infra", False),
                    storage_path=storage_path,
                )
            )
        elif name == "render_diagram":
            from .tools.render_diagram import render_diagram
            result = await asyncio.to_thread(
                functools.partial(
                    render_diagram,
                    source=arguments["source"],
                    theme=arguments.get("theme", "flow"),
                    max_nodes=arguments.get("max_nodes", 80),
                    open_in_viewer=arguments.get("open_in_viewer", False),
                )
            )
        elif name == "list_workspaces":
            from .tools.list_workspaces import list_workspaces
            result = await asyncio.to_thread(
                functools.partial(
                    list_workspaces,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_project_intel":
            from .tools.get_project_intel import get_project_intel
            result = await asyncio.to_thread(
                functools.partial(
                    get_project_intel,
                    repo=arguments["repo"],
                    category=arguments.get("category", "all"),
                    scope_path=arguments.get("scope_path"),
                    storage_path=storage_path,
                )
            )
        elif name == "winnow_symbols":
            from .tools.winnow_symbols import winnow_symbols
            result = await asyncio.to_thread(
                functools.partial(
                    winnow_symbols,
                    repo=arguments["repo"],
                    criteria=arguments.get("criteria", []),
                    rank_by=arguments.get("rank_by", "importance"),
                    order=arguments.get("order", "desc"),
                    max_results=arguments.get("max_results", 20),
                    storage_path=storage_path,
                )
            )
        elif name == "set_tool_tier":
            tier = arguments.get("tier")
            if tier not in ("core", "standard", "full"):
                result = {"error": f"invalid tier: {tier!r}"}
            else:
                prev = _effective_profile()
                _set_session_tier(tier)
                if tier != prev:
                    await _emit_tools_list_changed()
                result = {"ok": True, "tier": tier, "changed": tier != prev}
        elif name == "announce_model":
            model = arguments.get("model", "")
            if not isinstance(model, str) or not model:
                result = {"error": "model parameter is required and must be a non-empty string"}
            else:
                result = await _apply_model_announcement(model)
        elif name == "jcodemunch_guide":
            from . import __version__ as _ver
            from .retrieval.provenance import measured_provenance as _measured_provenance
            result = {
                "version": _ver,
                "content": _generate_claude_md_snippet(missing_only=False),
                # Self-attesting contract: the measured artifacts behind the
                # suite's savings/quality claims, plus the declared-vs-measured
                # rule. Rides the guide (on-demand) — never the hot path.
                "provenance": _measured_provenance(),
            }
        else:
            result = {"error": f"Unknown tool: {name}"}

        # Feature 2: Session journal recording
        if config_module.get("session_journal", True):
            try:
                from .tools.session_journal import get_journal
                journal = get_journal()
                journal.record_tool_call(name)
                # Record file reads for relevant tools
                if name in {"get_file_content", "get_file_outline", "get_symbol_source", "get_context_bundle"}:
                    if isinstance(result, dict):
                        # Extract file paths from result
                        if name == "get_file_content" and "content" in result:
                            journal.record_read(arguments.get("file_path", ""), name)
                        elif name == "get_file_outline" and "symbols" in result:
                            journal.record_read(arguments.get("file_path", ""), name)
                        elif name == "get_symbol_source":
                            # Single symbol_id → flat result with "source"
                            sym_id = arguments.get("symbol_id", "")
                            if sym_id and "::" in sym_id:
                                journal.record_read(sym_id.split("::")[0], name)
                            # Batch symbol_ids → result has "symbols" list
                            for sym in result.get("symbols", []):
                                if "file" in sym:
                                    journal.record_read(sym["file"], name)
                        elif name == "get_context_bundle" and "symbols" in result:
                            # Record all files from the bundle
                            for sym in result.get("symbols", []):
                                if "file" in sym:
                                    journal.record_read(sym["file"], name)
                # Record searches
                elif name in {"search_symbols", "search_text"}:
                    if isinstance(result, dict):
                        result_count = result.get("result_count", 0)
                        query = arguments.get("query", "")
                        if query:
                            journal.record_search(query, result_count)
                        # Collect negative evidence for session state persistence
                        ne = result.get("negative_evidence")
                        if ne and isinstance(ne, dict):
                            import time as _t
                            journal.record_negative_evidence({
                                "query": query,
                                "repo": arguments.get("repo", ""),
                                "verdict": ne.get("verdict", ""),
                                "scanned_symbols": ne.get("scanned_symbols", 0),
                                "timestamp": _t.time(),
                            })
                elif name == "get_ranked_context":
                    if isinstance(result, dict):
                        query = arguments.get("query", "")
                        if query:
                            items_included = result.get("items_included", 0)
                            journal.record_search(query, items_included)
                        ne = result.get("negative_evidence")
                        if ne and isinstance(ne, dict):
                            import time as _t
                            journal.record_negative_evidence({
                                "query": query,
                                "repo": arguments.get("repo", ""),
                                "verdict": ne.get("verdict", ""),
                                "scanned_symbols": ne.get("scanned_symbols", 0),
                                "timestamp": _t.time(),
                            })
                # Persist a compact live snapshot so the out-of-process
                # PreCompact hook can read real session state (#334). Throttled.
                _maybe_flush_live_journal(journal)
            except Exception:
                logger.debug("Journal recording failed", exc_info=True)

        # Feature 7: Turn budget — record output and inject warnings
        try:
            budget_tokens = config_module.get("turn_budget_tokens", 20000)
            if budget_tokens > 0 and isinstance(result, dict):
                from .tools.turn_budget import get_turn_budget
                tb = get_turn_budget()
                # Reconfigure if config changed (thread-safe)
                tb.configure(budget_tokens, config_module.get("turn_gap_seconds", 30.0))
                # Advisory only: the result is already computed, so the reader —
                # not this dispatcher — decides what to do about budget pressure.
                # Deliberately does NOT shorten the payload. Discarding context
                # here, before the caller has revealed which parts it needs, is
                # the failure mode that eager compaction is known for.
                result_bytes = len(json.dumps(result, default=str))
                token_count = result_bytes // 4  # ~4 bytes per token
                budget_info = tb.record_output(token_count)
                if budget_info.get("budget_warning"):
                    meta = result.setdefault("_meta", {})
                    meta["budget_warning"] = budget_info["budget_warning"]
                    meta["turn_tokens_used"] = budget_info["turn_tokens_used"]
                    meta["turn_budget_remaining"] = budget_info["turn_budget_remaining"]
                    # Also promote to top-level for visibility
                    result["budget_warning"] = budget_info["budget_warning"]
            elif budget_tokens > 0:
                # Still record token count for non-dict results (errors, etc.)
                from .tools.turn_budget import get_turn_budget
                tb = get_turn_budget()
                tb.configure(budget_tokens, config_module.get("turn_gap_seconds", 30.0))
                # Approximate token count for non-dict results
                tb.record_output(len(json.dumps(result, default=str)) // 4)
        except Exception:
            logger.debug("Turn budget recording failed", exc_info=True)

        # Agent Selector: score complexity and annotate result
        try:
            agent_selector_cfg = config_module.get("agent_selector", {})
            if isinstance(agent_selector_cfg, dict) and agent_selector_cfg.get("mode", "off") != "off":
                if isinstance(result, dict) and "error" not in result and name in _AGENT_SELECTOR_TOOLS:
                    from .agent_selector import (
                        AgentSelectorConfig, ComplexitySignals, score_complexity, route,
                    )
                    as_config = AgentSelectorConfig.from_config(agent_selector_cfg)
                    # Build signals from result metadata
                    signals = ComplexitySignals(
                        retrievalSetSize=result.get("items_included", result.get("symbol_count", 0)),
                        symbolCount=result.get("symbol_count", len(result.get("symbols", result.get("context_items", [])))),
                        crossFileReferences=result.get("cross_file_refs", 0),
                        crossProjectReferences=result.get("cross_project", False),
                        languageComplexity=result.get("language_complexity", "standard"),
                        requestTokenEstimate=result.get("used_tokens", result.get("total_tokens", 0)),
                    )
                    assessment = score_complexity(signals, as_config)
                    current_model = arguments.get("_current_model")
                    decision = route(assessment, as_config, current_model)
                    # Annotate result
                    meta = result.setdefault("_meta", {})
                    meta["agent_selector"] = {
                        "score": assessment.score,
                        "tier": assessment.tier,
                        "recommendedModel": assessment.recommendedModel,
                    }
                    if decision.prompt_text:
                        result["agent_selector_prompt"] = decision.prompt_text
                    if decision.metadata_text:
                        result["agent_selector"] = decision.metadata_text
        except Exception:
            logger.debug("Agent selector scoring failed", exc_info=True)

        # Argument contract (v1.108.175): every tool reads its arguments
        # key-by-key, so a misspelled parameter is dropped in silence and the
        # call that runs is not the call that was asked for. Disclose the
        # ignored keys on every state, and downgrade `absent` to `degraded` so
        # the absence-refusal rule below does the refusing — MUST run before the
        # absence-evidence block for that to hold.
        #
        # #377 hardening item 10 moved this ahead of presentation filtering, so
        # anything it attaches is re-attached below once filtering has run.
        _ignored: list[str] = []
        try:
            _ignored = _arg_contract.unrecognized_keys(
                arguments, _declared_arg_keys(name)
            )
            if _ignored:
                _arg_contract.apply_argument_contract(result, _ignored)
                logger.debug("Ignored unknown arguments for %s: %s", name, _ignored)
        except Exception:
            logger.debug("Argument-contract check failed", exc_info=True)

        # Absence evidence (#377 phase 3): record every absence-shaped verdict
        # so a handoff claim can cite the SCAN when nothing was served, and
        # hand the caller the citable ref in-band. A ref is only surfaced when
        # the scan can actually prove absence; otherwise the verdict says so,
        # rather than offering a token that would be refused at finalization.
        #
        # #377 hardening item 10: this MUST run before presentation filtering.
        # `meta_fields` is a display preference, and a display preference must
        # never decide whether a scan counts as complete — filtering first meant
        # `meta_fields: []` silently deleted the evidence, and the narrower
        # `meta_fields: ["verdict"]` deleted `index_truncated` while keeping the
        # verdict, so a TRUNCATED scan reached note_absence as untruncated and
        # minted a citable ref the truncation gate exists to refuse.
        _absence_carrier: dict | None = None
        try:
            if isinstance(result, dict):
                _v = (result.get("_meta") or {}).get("verdict")
                if isinstance(_v, dict):
                    from . import handoff as _handoff_abs
                    # #415. The subject of a scan is not always spelled `query`.
                    # `note_absence` requires a non-empty string and returns
                    # (None, None) without one, so a verdict-emitting tool keyed on
                    # `symbol` recorded nothing and re-attached no carrier — and on
                    # the shipped default (`meta_fields: []`) the verdict itself is
                    # filtered out, so its refusal reached the caller as a bare
                    # empty response. The guard is emitting a verdict at all, which
                    # is a tool opting into the honesty contract; the fallback only
                    # names what that tool called its subject.
                    _subject = arguments.get("query") or arguments.get("symbol")
                    _ref, _why = _handoff_abs.note_absence(
                        name,
                        repo_arg,
                        _subject,
                        _v,
                        arguments=arguments,
                        truncated=bool((result.get("_meta") or {}).get("index_truncated")),
                    )
                    if _ref:
                        _v["evidence_ref"] = _ref
                        _absence_carrier = {"ref": _ref, "citable": True}
                    elif _why and (
                        _v.get("state") == "absent" or _v.get("absence_refused")
                    ):
                        # v1.108.184: `absence_refused` widens this to every
                        # zero-result scan whose absence claim was refused, not
                        # just the ones that still read `absent`. Every gate since
                        # v1.108.166 works by DOWNGRADING to `degraded`, so gating
                        # the disclosure on `absent` meant the better the gate
                        # worked, the less the caller was told: a refused scan came
                        # back as a bare empty response, and on a default install
                        # (`meta_fields: []`) with no verdict either.
                        _v["absence_citable"] = False
                        _v["absence_blocked_by"] = _why
                        _absence_carrier = {"citable": False, "blocked_by": _why}
        except Exception:
            logger.debug("Absence-evidence record failed", exc_info=True)

        # Evidence receipts (#377 phase 2): opt-in per call. A receipt binds one
        # canonical subject to one snapshot and one effective operation, so a
        # file-level citation stops being indistinguishable from a symbol-level
        # one. Default off means today's bytes, exactly.
        #
        # Runs AFTER the absence block, because an absence receipt links to the
        # scan that block recorded; and BEFORE presentation filtering, for the
        # same reason that block does (#377 item 10) — a display preference must
        # never decide what a scan proved. The carrier is re-attached below.
        #
        # Minting is gated on producer registration: an unregistered tool, or a
        # registered tool's unreviewed exit, mints nothing. That is what makes
        # the v1.108.179 early-return class structural instead of remembered.
        _receipt_carrier: dict | None = None
        try:
            if arguments.get("receipt") is True and isinstance(result, dict):
                from .evidence import producers as _receipt_producers
                _receipt_carrier = await asyncio.to_thread(
                    functools.partial(
                        _receipt_producers.mint,
                        name,
                        arguments,
                        result,
                        repo_arg,
                        storage_path,
                    )
                )
        except Exception:
            logger.debug("Evidence-receipt minting failed", exc_info=True)

        # `content_hash` is a 64-hex SHA-256 the caller was never offered a use
        # for: `verify` answers the drift question as a boolean, and a receipt
        # carries the digest out of band. Shipped on every row it cost ~83
        # characters per symbol on the hottest read path, so it is now emitted
        # only when something in the request actually consumes it.
        #
        # Stripped HERE, not at the emission site in tools/get_symbol.py, and
        # deliberately AFTER the mint block: `_row_subject` reads the digest
        # from the SERVED ROW and never re-reads the index, so a tool-side gate
        # would silently downgrade every receipt's `hash_source` from
        # `index_content_hash` to `served_bytes`. Running after mint makes the
        # receipt immune to this by construction rather than by remembering to
        # thread a flag. Same ordering rule as the filtering below: a
        # presentation choice must not decide what a scan proved.
        if (
            name == "get_symbol_source"
            and isinstance(result, dict)
            and not arguments.get("verify")
            and arguments.get("receipt") is not True
        ):
            result.pop("content_hash", None)
            for _sym in result.get("symbols", []):
                if isinstance(_sym, dict):
                    _sym.pop("content_hash", None)

        if isinstance(result, dict):
            meta_fields = config_module.get("meta_fields")
            if meta_fields == [] or arguments.get("suppress_meta"):
                result.pop("_meta", None)
                # Also strip nested _meta from batch tools (e.g. get_file_outline batch)
                for _item in result.get("results", []):
                    if isinstance(_item, dict):
                        _item.pop("_meta", None)
            elif isinstance(meta_fields, list):
                # Partial field inclusion — keep only the fields listed in meta_fields,
                # preserving tool-generated fields (timing_ms, tokens_saved, etc.)
                existing_meta = result.pop("_meta", {})
                _meta: dict[str, Any] = {}
                if "powered_by" in meta_fields:
                    _meta["powered_by"] = "jcodemunch-mcp by jgravelle · https://github.com/jgravelle/jcodemunch-mcp"
                for field in meta_fields:
                    if field in existing_meta:
                        _meta[field] = existing_meta[field]
                if _meta:
                    result["_meta"] = _meta
                # Also filter nested _meta from batch tools (e.g. get_file_outline batch)
                for _item in result.get("results", []):
                    if isinstance(_item, dict):
                        _item_meta = _item.pop("_meta", {})
                        _item_filtered: dict[str, Any] = {f: _item_meta[f] for f in meta_fields if f in _item_meta}
                        if "powered_by" in meta_fields:
                            _item_filtered["powered_by"] = "jcodemunch-mcp by jgravelle · https://github.com/jgravelle/jcodemunch-mcp"
                        if _item_filtered:
                            _item["_meta"] = _item_filtered

            # #377 hardening item 10: re-attach the contract keys when they did
            # not survive filtering. Both blocks now run against the COMPLETE
            # internal result — the point of the reorder — so what a display
            # preference removes has to be put back deliberately rather than
            # avoided by deciding safety after the fields were deleted. Same
            # shape jdocmunch and jdatamunch already use, because their default
            # config strips `_meta` outright. jcodemunch's default is
            # `meta_fields: []`, so this is the normal path, not the edge case.
            if _ignored and "ignored_arguments" not in (result.get("_meta") or {}):
                result.setdefault("_meta", {})["ignored_arguments"] = _ignored
            if _absence_carrier and "verdict" not in (result.get("_meta") or {}):
                result.setdefault("_meta", {})["absence_evidence"] = _absence_carrier
            if _receipt_carrier:
                # Unconditional, unlike the absence carrier: the receipt id has
                # no home in the verdict, so there is no "already present" case
                # and nothing for a filter to have left behind.
                result.setdefault("_meta", {})["receipts"] = _receipt_carrier

        # Per-call pulse for downstream consumers (dashboards, monitors)
        _saved = result.get("_meta", {}).get("tokens_saved", 0) if isinstance(result, dict) else 0
        _write_pulse(name, tokens_saved=_saved, base_path=storage_path)

        # Session yield: record which symbol ids this response served, and
        # attach the advisory budget block when the session is approaching or
        # over its configured session_token_budget (v1.108.146). `spent`
        # covers responses served BEFORE this one (recorded at return time).
        try:
            from .storage import token_tracker as _budget_tracker
            if isinstance(result, dict):
                if name == "search_symbols":
                    _budget_tracker.note_served(
                        e.get("id") for e in result.get("results", []) if isinstance(e, dict)
                    )
                elif name == "get_ranked_context":
                    _budget_tracker.note_served(
                        e.get("symbol_id") or e.get("id")
                        for e in result.get("context_items", []) if isinstance(e, dict)
                    )
                # Cue-anchored delivery ledger: tell the agent when it is being
                # handed bytes it already bought this session. Advisory only —
                # the response body is unchanged (P1 of
                # docs/prd-cue-anchored-delivery.md).
                _repeats = _budget_tracker.note_delivered(
                    _delivery_entries(name, result),
                    base_path=arguments.get("storage_path"),
                )
                if _repeats:
                    result.setdefault("_meta", {})["already_delivered"] = {
                        "count": len(_repeats),
                        "symbols": _repeats[:_DELIVERY_ANNOTATE_MAX],
                    }
                _b = _budget_tracker.budget_status()
                if _b is not None and _b["state"] in ("approaching", "over"):
                    result.setdefault("_meta", {})["budget"] = _b
        except Exception:
            logger.debug("Budget/yield attach failed", exc_info=True)

        # Turn-economy steering (v1.108.158): count hop vs bundle traffic; after
        # _STEER_NUDGE_AT hop calls with no bundle call, advise the one-call
        # opener ONCE, on a search response (where the next-query decision is
        # made). Forces JSON for that single response so the hint can't be
        # dropped by a lossy compact encoding.
        try:
            _steer_note_call(name, arguments, result)
            if _steer_hint_due(name, result):
                _steer_state["nudged"] = True
                result.setdefault("_meta", {})["hint"] = (
                    "Several search/read hops and no bundle call yet this session. "
                    "For exploration questions, get_ranked_context(repo, query, "
                    "token_budget) returns ranked, budget-packed context in ONE call. "
                    "Pass compress=True to fit more symbols in the same budget; "
                    "repo also accepts '.' or a filesystem path."
                )
                _requested_format = "json"
        except Exception:
            logger.debug("Steering attach failed", exc_info=True)

        # Response-level secret redaction — scrub leaked credentials
        # before they reach the LLM context window. Skipped for tools that
        # return raw cached source (any "secret" found is the user's own
        # checked-in code; the per-byte regex sweep is wasted latency on
        # tools whose payloads can be hundreds of KB).
        _SOURCE_DUMP_TOOLS = frozenset({
            "get_file_content", "get_symbol_source", "get_context_bundle",
        })
        if isinstance(result, dict) and name not in _SOURCE_DUMP_TOOLS:
            try:
                from .redact import is_redaction_enabled, redact_dict
                if is_redaction_enabled():
                    result, _redact_count = redact_dict(result)
                    if _redact_count > 0:
                        meta = result.setdefault("_meta", {})
                        meta["secrets_redacted"] = _redact_count
            except Exception:
                logger.debug("Secret redaction failed", exc_info=True)

        # Compact output encoding (MUNCH). Opt-in via `format` argument or
        # JCODEMUNCH_DEFAULT_FORMAT env; "auto" falls back to JSON unless
        # savings clear the gate threshold.
        try:
            from .encoding import encode_response
            from .storage.token_tracker import record_encoding_savings
            encoded, enc_meta = encode_response(name, result, _requested_format, repo=repo_arg)
            if enc_meta.get("encoding") != "json":
                saved = enc_meta.get("encoding_tokens_saved", 0)
                total_enc = record_encoding_savings(saved, base_path=storage_path, tool_name=name)
                if isinstance(result, dict):
                    m = result.setdefault("_meta", {})
                    m["encoding"] = enc_meta["encoding"]
                    m["encoding_tokens_saved"] = saved
                    m["total_encoding_tokens_saved"] = total_enc
                _record_response_tokens(encoded)
                return [TextContent(type="text", text=encoded)]
        except Exception:
            logger.debug("Compact encoding failed; emitting JSON", exc_info=True)

        _text = json.dumps(result, separators=(',', ':'))
        if isinstance(result, dict) and "error" in result:
            # In-band tool error (e.g. ambiguous/not-found repo, Unknown tool).
            # Carry the same JSON body but flag isError for clients that branch
            # on it (F-P01); the v1.108.30 passthrough already kept errors JSON.
            _call_ok = False
            return _error_call_result(_text)
        _record_response_tokens(_text)
        return [TextContent(type="text", text=_text)]

    except KeyError as e:
        _call_ok = False
        # A KeyError raised while extracting arguments in THIS dispatcher frame is
        # a genuine missing caller argument. A KeyError raised deeper — inside a
        # tool implementation (e.g. a dict-shape bug) — must NOT masquerade as a
        # schema/argument problem (#331). Distinguish by the originating frame.
        _tb = e.__traceback__
        while _tb is not None and _tb.tb_next is not None:
            _tb = _tb.tb_next
        _origin = _tb.tb_frame.f_code.co_filename if _tb is not None else ""
        if _origin and os.path.basename(_origin) != os.path.basename(__file__):
            logger.error("call_tool %s raised an internal KeyError", name, exc_info=True)
            payload = {
                "error": f"Internal error processing {name}",
                "summary": f"KeyError: {e}",
            }
            return _error_call_result(json.dumps(payload, separators=(',', ':')))
        _missing_msg = f"Missing required argument: {e}. Check the tool schema for correct parameter names."
        if str(e).strip("'\"") == "repo" and _steer_state["repos"]:
            # Informed retry (v1.108.158): agents ordering without resident
            # schemas omit repo — name what this session has already resolved.
            _missing_msg += " This session has resolved: " + ", ".join(_steer_state["repos"]) + ". Pass repo=<one of these>."
        return _error_call_result(json.dumps({"error": _missing_msg}, separators=(',', ':')))
    except Exception as exc:
        _call_ok = False
        logger.error("call_tool %s failed", name, exc_info=True)
        summary = " ".join((str(exc).strip().splitlines() or [""])[0].split())
        summary = f"{type(exc).__name__}: {summary}" if summary else type(exc).__name__
        if len(summary) > 200:
            summary = f"{summary[:197].rstrip()}..."
        payload = {
            "error": f"Internal error processing {name}",
            "summary": summary,
        }
        return _error_call_result(json.dumps(payload, separators=(',', ':')))
    finally:
        # Flush in-flight progress notifications BEFORE the response is
        # written (the SDK writes only after call_tool returns, and finally
        # runs before that). A progress notification trailing its response
        # is a protocol error to strict clients — Claude Code drops the
        # stdio connection / loses the tool result (#359).
        if _reporter_ref is not None:
            try:
                from .progress import drain_reporter, HeartbeatReporter
                # A heartbeat that already spoke closes the loop (#383) — an
                # operator told "still working after 120s" needs the line that
                # says it ended. ProgressReporter is deliberately NOT finished
                # here: its 100% send is the tool's to make, and synthesising
                # one would put a notification after the work it describes.
                if isinstance(_reporter_ref, HeartbeatReporter):
                    _reporter_ref.finish("ok" if _call_ok else "failed")
                await drain_reporter(_reporter_ref)
            except Exception:
                logger.debug("Progress drain failed for %s", name, exc_info=True)
        # Deferred auto-watch (#384): the tool has now indexed this folder, so
        # the watch task can start without repeating that work. Runs even when
        # the tool failed — add_folder is cheap and the watcher's own change
        # handling is what recovers a partial index.
        if _deferred_watch is not None:
            try:
                await _auto_watch_after_tool(_deferred_watch)
            except Exception:
                logger.debug("Deferred auto-watch failed", exc_info=True)
        try:
            from .storage.token_tracker import record_tool_latency
            duration_ms = (time.perf_counter() - _t0_call) * 1000.0
            _repo_arg = arguments.get("repo") if isinstance(arguments, dict) else None
            # v1.108.188: persist against the store the CALL named. analyze_perf
            # reads tool_calls and ranking_events through one base path, so a row
            # written to the default while the reader looks in a named store is
            # invisible to the only thing that consumes it.
            _store_arg = arguments.get("storage_path") if isinstance(arguments, dict) else None
            record_tool_latency(
                name, duration_ms, ok=_call_ok, repo=_repo_arg, base_path=_store_arg,
            )
        except Exception:
            logger.debug("Latency recording failed for %s", name, exc_info=True)


async def _run_server_with_watcher(
    server_coro_func,
    server_args: tuple,
    watcher_kwargs: dict,
    log_path: Optional[str] = None,
) -> None:
    """Run MCP server with a background watcher in the same event loop.

    Watcher runs in quiet mode (no stderr output). If log_path is provided,
    watcher output and errors go to that file. If log_path is "auto", a temp
    file is created in the system temp directory.
    """
    global _watcher_manager

    if watch_folders is None or WatcherManager is None:
        try:
            from .cli.upgrade import watch_extra_install_command

            cmd = watch_extra_install_command()
        except Exception:
            cmd = "pip install 'jcodemunch-mcp[watch]'"
        raise ImportError(
            f"watchfiles is required for --watcher. Install with: {cmd}"
        )

    import tempfile

    # Resolve log file path
    if log_path == "auto":
        log_path = os.path.join(
            tempfile.gettempdir(),
            f"jcw_{os.getpid()}.log",
        )

    stop_event = asyncio.Event()

    _log_path = log_path

    # Open log file handle if provided
    _log_file_handle: Optional[IO] = None
    if _log_path:
        try:
            _log_file_handle = open(_log_path, "a", encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not open watcher log %r: %s — continuing without log", _log_path, exc)
            _log_file_handle = None

    # Create WatcherManager and add initial paths
    manager = WatcherManager(
        debounce_ms=watcher_kwargs.get("debounce_ms", 200),
        use_ai_summaries=watcher_kwargs.get("use_ai_summaries", True),
        storage_path=watcher_kwargs.get("storage_path"),
        extra_ignore_patterns=watcher_kwargs.get("extra_ignore_patterns"),
        follow_symlinks=watcher_kwargs.get("follow_symlinks", False),
        quiet=True,
        log_file_handle=_log_file_handle,
    )
    manager._stop_event = stop_event

    # Add initial paths
    initial_paths = watcher_kwargs.get("paths", [])
    for path in initial_paths:
        folder = Path(path).expanduser().resolve()
        if folder.is_dir():
            await manager.add_folder(str(folder))

    _watcher_manager = manager

    # Create manager run task (self-restarts on crash)
    manager_task = asyncio.create_task(
        manager.run(),
        name="watcher-manager",
    )

    try:
        await server_coro_func(*server_args)
    except asyncio.CancelledError:
        pass  # Clean shutdown via Ctrl+C
    finally:
        _watcher_manager = None
        stop_event.set()
        # Remove all folders
        for folder in list(manager._watched):
            await manager.remove_folder(folder)
        manager.stop()
        manager_task.cancel()
        try:
            await asyncio.wait_for(manager_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            manager_task.cancel()
            try:
                await manager_task
            except asyncio.CancelledError:
                pass
        except (WatcherError, Exception) as exc:
            logger.warning("Watcher stopped with error: %s", exc)
        # Close log file handle
        if _log_file_handle is not None:
            try:
                _log_file_handle.close()
            except Exception:
                pass
        from .storage import IndexStore
        IndexStore(base_path=watcher_kwargs.get("storage_path") or os.environ.get("CODE_INDEX_PATH")).close()


async def run_stdio_server():
    """Run the MCP server over stdio (default)."""
    import sys

    import anyio

    from mcp.server.stdio import stdio_server

    from .stdio_guard import claim_stdout

    # Suite parity with jdoc#110. Take the real stdout for JSON-RPC and point
    # fd 1 at stderr BEFORE anything else runs, so no library, thread or child
    # process can reach the framed stream. `tools/embed_repo.py` builds a
    # SentenceTransformer inside a tool call, and a first embed on a machine
    # without the model cached downloads it mid-request.
    #
    # ⚠ This does NOT retire the handshake watchdog below. Chatter written by a
    # launcher BEFORE this process starts — the uvx case that cost a paying
    # client 5h+ — is already in the pipe and cannot be retracted after exec.
    _private_stdout, _stdout_swapped = claim_stdout()

    print(f"jcodemunch-mcp {__version__} by jgravelle · https://github.com/jgravelle/jcodemunch-mcp", file=sys.stderr)
    if not _stdout_swapped:
        # ⚠ Worth saying out loud: this is the configuration where a stray
        # library write can still corrupt a response.
        print(
            "[jcodemunch-mcp] could not isolate stdout for JSON-RPC; library "
            "output on stdout may corrupt framing",
            file=sys.stderr,
        )
    logger.info(
        "startup version=%s transport=stdio storage=%s ai_summaries=%s",
        __version__,
        os.path.expanduser(os.environ.get("CODE_INDEX_PATH", "~/.code-index/")),
        _default_use_ai_summaries(),
    )
    # Version-drift probe: on first launch after upgrade, emit a one-line
    # hint pointing at the release notes. Silent on first-ever launch and
    # on any OS-level failure.
    try:
        from .version_check import check_and_announce
        check_and_announce()
    except Exception:
        logger.debug("version_check probe failed", exc_info=True)
    # Feature 10: Restore session state on startup
    _restore_session_state()
    # Log tier bundle / disabled_tools overlap warnings
    _log_startup_validation_warnings()

    # Handshake watchdog. If the client never reaches any of our MCP
    # handlers (list_tools, list_resources, list_prompts, get_prompt,
    # call_tool) within JCODEMUNCH_HANDSHAKE_TIMEOUT seconds, write a
    # one-line stderr hint. This catches stdio-channel corruption — the
    # paying-client report against Codex/rmcp where uvx chatter on stdout
    # made the client wait 5h+ for a frame that was never coming. Set
    # JCODEMUNCH_HANDSHAKE_TIMEOUT=0 to disable.
    global _handshake_event
    _handshake_event = asyncio.Event()
    try:
        _handshake_timeout = float(os.environ.get("JCODEMUNCH_HANDSHAKE_TIMEOUT", "5"))
    except (ValueError, TypeError):
        _handshake_timeout = 5.0

    async def _handshake_watchdog() -> None:
        if _handshake_timeout <= 0:
            return
        try:
            await asyncio.wait_for(_handshake_event.wait(), timeout=_handshake_timeout)
        except asyncio.TimeoutError:
            sys.stderr.write(
                f"[jcodemunch-mcp] handshake not completed after "
                f"{_handshake_timeout:.0f}s — the client has not called any MCP "
                f"handler. If you spawn this server via `uvx`, stdout chatter "
                f"from package resolution can corrupt the JSON-RPC channel for "
                f"strict clients (notably Codex/rmcp). Workarounds: "
                f"(1) install the binary with `pip install jcodemunch-mcp` and "
                f"point your client at it directly, or (2) set UV_NO_PROGRESS=1 "
                f"UV_QUIET=1 in the spawn env. Set JCODEMUNCH_HANDSHAKE_TIMEOUT=0 "
                f"to silence this warning.\n"
            )
            sys.stderr.flush()
        except asyncio.CancelledError:
            pass

    _watchdog_task = asyncio.create_task(_handshake_watchdog())

    try:
        _stdout_arg = (
            anyio.wrap_file(_private_stdout) if _private_stdout is not None else None
        )
        async with stdio_server(stdout=_stdout_arg) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        if not _watchdog_task.done():
            _watchdog_task.cancel()
        from .storage import IndexStore
        IndexStore(base_path=os.environ.get("CODE_INDEX_PATH")).close()


def _make_auth_middleware():
    """Return a Starlette middleware class that checks JCODEMUNCH_HTTP_TOKEN if set."""
    token = os.environ.get("JCODEMUNCH_HTTP_TOKEN")
    if not token:
        return None

    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            auth = request.headers.get("authorization", "")
            if not hmac.compare_digest(auth, f"Bearer {token}"):
                return JSONResponse(
                    {"error": "Unauthorized. Set Authorization: Bearer <JCODEMUNCH_HTTP_TOKEN> header."},
                    status_code=401,
                )
            return await call_next(request)

    return Middleware(BearerAuthMiddleware)


def _make_rate_limit_middleware():
    """Return a Starlette middleware that rate-limits by IP (optional, opt-in).

    Reads JCODEMUNCH_RATE_LIMIT env var.  Value is max requests per minute per
    client IP.  0 or unset disables rate limiting (default — no behaviour change
    for existing deployments).

    Returns a Middleware instance, or None when rate limiting is disabled.
    """
    try:
        limit = int(os.environ.get("JCODEMUNCH_RATE_LIMIT", "0"))
    except (ValueError, TypeError):
        limit = 0
    if limit <= 0:
        return None

    import collections
    import time as _time

    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    _WINDOW = 60.0  # seconds
    _buckets: dict[str, collections.deque] = {}

    # Hard cap on tracked IPs so a botnet/rotating-NAT client cannot bloat
    # the bucket dict indefinitely. When full, evict the oldest-touched entry.
    _MAX_TRACKED_IPS = 10_000
    _last_touched: dict[str, float] = {}

    class RateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            ip = request.client.host if request.client else "unknown"
            now = _time.monotonic()
            bucket = _buckets.setdefault(ip, collections.deque())
            _last_touched[ip] = now
            # Evict timestamps outside the sliding window
            while bucket and now - bucket[0] >= _WINDOW:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(_WINDOW - (now - bucket[0])) + 1
                return JSONResponse(
                    {"error": f"Rate limit exceeded. Max {limit} requests per minute per IP."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            # If the bucket is now empty after window-eviction, drop the IP
            # entry entirely so cold IPs don't accumulate forever.
            if not bucket:
                _buckets.pop(ip, None)
                _last_touched.pop(ip, None)
            elif len(_buckets) > _MAX_TRACKED_IPS:
                # Cap exceeded: evict the least-recently-touched IP.
                oldest_ip = min(_last_touched, key=_last_touched.get)
                _buckets.pop(oldest_ip, None)
                _last_touched.pop(oldest_ip, None)
            return await call_next(request)

    return Middleware(RateLimitMiddleware)


async def run_sse_server(host: str, port: int):
    """Run the MCP server with SSE transport (persistent HTTP mode)."""
    import sys
    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.routing import Mount, Route
    except ImportError as e:
        raise ImportError(
            f"SSE transport requires additional packages: {e}. "
            'Install them with: pip install "jcodemunch-mcp[http]"'
        ) from e
    from mcp.server.sse import SseServerTransport

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    middleware = []
    auth_mw = _make_auth_middleware()
    if auth_mw:
        middleware.append(auth_mw)
    rate_mw = _make_rate_limit_middleware()
    if rate_mw:
        middleware.append(rate_mw)

    # Phase 6: optional /runtime/* live-ingest routes (off by default; gated
    # by runtime_ingest_enabled config + JCODEMUNCH_HTTP_TOKEN auth).
    from .runtime.http_routes import make_runtime_routes
    from .org.http_routes import make_org_routes
    runtime_routes = make_runtime_routes()
    org_routes = make_org_routes()

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
            *runtime_routes,
            *org_routes,
        ],
        middleware=middleware,
    )

    print(
        f"jcodemunch-mcp {__version__} by jgravelle · SSE server at http://{host}:{port}/sse",
        file=sys.stderr,
    )
    print(
        "NOTICE: the SSE transport is deprecated by the MCP 2026-07-28 spec and will "
        "eventually leave MCP SDKs. Prefer `serve --transport streamable-http` when "
        "your MCP client supports it; SSE keeps working here until hosts migrate.",
        file=sys.stderr,
    )
    if not os.environ.get("JCODEMUNCH_HTTP_TOKEN") and host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"WARNING: SSE bound to non-loopback host {host!r} without "
            f"JCODEMUNCH_HTTP_TOKEN — anyone on the network can drive this MCP server. "
            f"Set JCODEMUNCH_HTTP_TOKEN to require bearer auth.",
            file=sys.stderr,
        )
    logger.info(
        "startup version=%s transport=sse host=%s port=%d storage=%s",
        __version__, host, port,
        os.path.expanduser(os.environ.get("CODE_INDEX_PATH", "~/.code-index/")),
    )
    _note_adaptive_tiering_transport("sse")
    _log_startup_validation_warnings()
    # Feature 10: Restore session state on startup
    _restore_session_state()
    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
    await uvicorn.Server(config).serve()


async def run_streamable_http_server(host: str, port: int):
    """Run the MCP server with streamable-http transport (persistent HTTP mode)."""
    import sys
    import uuid
    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.routing import Route
    except ImportError as e:
        raise ImportError(
            f"Streamable-http transport requires additional packages: {e}. "
            'Install them with: pip install "jcodemunch-mcp[http]"'
        ) from e
    from mcp.server.streamable_http import StreamableHTTPServerTransport, MCP_SESSION_ID_HEADER

    # Session registry: session_id -> (transport, background_task)
    # Keeps server.run() alive across multiple HTTP requests from the same client.
    _sessions: dict[str, StreamableHTTPServerTransport] = {}
    _session_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
    _session_last_seen: dict[str, float] = {}

    # Resource caps. A misbehaving or hostile client must not be able to
    # balloon process memory by opening sessions and never sending DELETE.
    try:
        _MAX_SESSIONS = int(os.environ.get("JCODEMUNCH_MAX_SESSIONS", "1024"))
    except (ValueError, TypeError):
        _MAX_SESSIONS = 1024
    try:
        _SESSION_IDLE_TIMEOUT = float(os.environ.get("JCODEMUNCH_SESSION_IDLE_TIMEOUT", "300"))
    except (ValueError, TypeError):
        _SESSION_IDLE_TIMEOUT = 300.0

    def _drop_session(sid: str) -> None:
        _sessions.pop(sid, None)
        _session_last_seen.pop(sid, None)
        t = _session_tasks.pop(sid, None)
        if t and not t.done():
            t.cancel()

    async def _idle_session_sweeper() -> None:
        import time as _t
        try:
            while True:
                await asyncio.sleep(max(30.0, _SESSION_IDLE_TIMEOUT / 4))
                now = _t.monotonic()
                stale = [
                    sid for sid, ts in list(_session_last_seen.items())
                    if now - ts > _SESSION_IDLE_TIMEOUT
                ]
                for sid in stale:
                    logger.info("evicting idle MCP session %s (idle > %.0fs)", sid, _SESSION_IDLE_TIMEOUT)
                    _drop_session(sid)
        except asyncio.CancelledError:
            pass

    asyncio.create_task(_idle_session_sweeper())

    # Sentinel response: transport.handle_request() already wrote to the ASGI
    # send callable, so Starlette's endpoint wrapper must not send anything
    # else.  Returning this instead of None prevents the "NoneType is not
    # callable" TypeError.
    class _AlreadySent:
        async def __call__(self, scope, receive, send):
            pass

    _ALREADY_SENT = _AlreadySent()

    async def handle_mcp(request: Request):
        import time as _t
        session_id = request.headers.get(MCP_SESSION_ID_HEADER)

        # Route to existing session if client sent a session ID we recognise.
        if session_id and session_id in _sessions:
            transport = _sessions[session_id]
            _session_last_seen[session_id] = _t.monotonic()
            await transport.handle_request(request.scope, request.receive, request._send)
            # Clean up terminated sessions (e.g. after DELETE).
            if transport._terminated:
                _drop_session(session_id)
            return _ALREADY_SENT

        # Reject new sessions when the cap is reached so a noisy client cannot
        # exhaust memory / asyncio task slots.
        if len(_sessions) >= _MAX_SESSIONS:
            from starlette.responses import Response as StarletteResponse
            return StarletteResponse(
                f"Server at session capacity (max {_MAX_SESSIONS}); retry later.",
                status_code=503,
                headers={"Retry-After": "30"},
            )

        # New session — capture the caller's auth principal into this request's
        # context BEFORE spawning the session task: create_task copies the
        # context, so every handler in the session inherits it. Today the
        # transport session_id above always wins in _session_key(); the
        # principal takes over only when session ids stop being issued.
        _HTTP_PRINCIPAL.set(_principal_from_authorization(request.headers.get("authorization")))
        if _HTTP_PRINCIPAL.get() is None:
            _note_no_principal_session()

        # Generate a unique ID so the transport enforces it on all subsequent
        # requests, preventing cross-session pollution.
        new_id = uuid.uuid4().hex
        transport = StreamableHTTPServerTransport(mcp_session_id=new_id)
        _sessions[new_id] = transport
        _session_last_seen[new_id] = _t.monotonic()

        # streams_ready is set once transport.connect() has initialised its
        # internal memory streams.  We must wait for it before calling
        # handle_request(), which writes to those streams.
        streams_ready: asyncio.Event = asyncio.Event()

        async def _session_runner() -> None:
            try:
                async with transport.connect() as (read_stream, write_stream):
                    streams_ready.set()
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                    )
            except asyncio.CancelledError:
                pass
            finally:
                _sessions.pop(new_id, None)
                _session_tasks.pop(new_id, None)
                _session_last_seen.pop(new_id, None)

        task = asyncio.create_task(_session_runner())
        _session_tasks[new_id] = task

        try:
            # Wait up to 10 s for the transport to be ready.
            await asyncio.wait_for(streams_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            _drop_session(new_id)
            from starlette.responses import Response as StarletteResponse
            return StarletteResponse("Session setup timed out", status_code=500)

        try:
            await transport.handle_request(request.scope, request.receive, request._send)
        except Exception:
            task.cancel()
            raise
        return _ALREADY_SENT

    middleware = []
    auth_mw = _make_auth_middleware()
    if auth_mw:
        middleware.append(auth_mw)
    rate_mw = _make_rate_limit_middleware()
    if rate_mw:
        middleware.append(rate_mw)

    # Phase 6: optional /runtime/* live-ingest routes (off by default).
    from .runtime.http_routes import make_runtime_routes
    from .org.http_routes import make_org_routes
    runtime_routes = make_runtime_routes()
    org_routes = make_org_routes()

    starlette_app = Starlette(
        routes=[
            Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
            *runtime_routes,
            *org_routes,
        ],
        middleware=middleware,
    )

    print(
        f"jcodemunch-mcp {__version__} by jgravelle · streamable-http server at http://{host}:{port}/mcp",
        file=sys.stderr,
    )
    if not os.environ.get("JCODEMUNCH_HTTP_TOKEN") and host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"WARNING: streamable-http bound to non-loopback host {host!r} without "
            f"JCODEMUNCH_HTTP_TOKEN — anyone on the network can drive this MCP server. "
            f"Set JCODEMUNCH_HTTP_TOKEN to require bearer auth.",
            file=sys.stderr,
        )
    logger.info(
        "startup version=%s transport=streamable-http host=%s port=%d storage=%s",
        __version__, host, port,
        os.path.expanduser(os.environ.get("CODE_INDEX_PATH", "~/.code-index/")),
    )
    _note_adaptive_tiering_transport("streamable-http")
    _log_startup_validation_warnings()
    # Feature 10: Restore session state on startup
    _restore_session_state()
    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
    await uvicorn.Server(config).serve()


def _resolve_log_config(args) -> "tuple[str, Optional[str]]":
    """Resolve (level_name, log_file) with precedence: an explicit CLI flag, then
    the env var (JCODEMUNCH_LOG_LEVEL / JCODEMUNCH_LOG_FILE), then the persisted
    config key (log_level / log_file), then the hardcoded default.

    The config fallback lets `config set log_file <path>` drive logging without
    an env-block or MCP-config edit (e.g. when the jMunch Console enables it),
    while an explicit env var or CLI flag from the launching client still wins.
    Additive: with no env/CLI/config set, this resolves to WARNING + stderr,
    exactly as before."""
    from . import config as _cfg
    level_name = (
        getattr(args, "log_level", None)
        or os.environ.get("JCODEMUNCH_LOG_LEVEL")
        or _cfg.get("log_level", "WARNING")
        or "WARNING"
    )
    log_file = (
        getattr(args, "log_file", None)
        or os.environ.get("JCODEMUNCH_LOG_FILE")
        or _cfg.get("log_file", None)
    )
    return str(level_name).upper(), log_file


def _resolve_serve_endpoint(args) -> "tuple[str, str, int]":
    """Resolve (transport, host, port) for `serve` with precedence: an explicit
    CLI flag, then the env var (JCODEMUNCH_TRANSPORT / _HOST / _PORT), then the
    persisted config key (transport / host / port), then the hardcoded default.

    Without this, config.jsonc transport/host/port were inert at serve time —
    argparse read only the env var and the config keys were never consulted, so
    a client-launched server could not be pointed at a configured endpoint via
    config alone. An explicit CLI flag or env var from the launching client
    still wins. The serve argparse defaults are None so an unset flag is
    distinguishable from a deliberately-passed value."""
    from . import config as _cfg

    def _as_port(raw) -> "Optional[int]":
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    transport = (
        getattr(args, "transport", None)
        or os.environ.get("JCODEMUNCH_TRANSPORT")
        or _cfg.get("transport", None)
        or "stdio"
    )
    host = (
        getattr(args, "host", None)
        or os.environ.get("JCODEMUNCH_HOST")
        or _cfg.get("host", None)
        or "127.0.0.1"
    )
    port = (
        (args.port if getattr(args, "port", None) is not None else None)
        or _as_port(os.environ.get("JCODEMUNCH_PORT"))
        or _as_port(_cfg.get("port", None))
        or 8901
    )
    return str(transport), str(host), int(port)


def _setup_logging(args) -> None:
    """Configure logging from CLI args / env / config (see _resolve_log_config)."""
    level_name, log_file = _resolve_log_config(args)
    log_level = getattr(logging, level_name, logging.WARNING)
    handlers: list[logging.Handler] = []
    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    else:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    extra_ext = os.environ.get("JCODEMUNCH_EXTRA_EXTENSIONS", "")
    if extra_ext:
        logging.getLogger(__name__).info("JCODEMUNCH_EXTRA_EXTENSIONS: %s", extra_ext)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add logging args shared by all subcommands."""
    # Defaults are None so _resolve_log_config can apply the full precedence
    # chain (CLI flag > env var > log_level/log_file config key > default).
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level. Precedence: this flag, then JCODEMUNCH_LOG_LEVEL, then the log_level config key, then WARNING.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log file path. Precedence: this flag, then JCODEMUNCH_LOG_FILE, then the log_file config key, then stderr.",
    )


# Quick-start steps as DATA, not literal lines (#506).
#
# ⚠⚠ #495 filtered `### All tools` and left this section as six fixed strings
# that no filter reached, so the guide could still instruct a caller to run a
# tool `call_tool` rejects. **Fixing the reported section and leaving an
# adjacent one with the identical defect is the failure mode this project keeps
# hitting** — the same shape as #495's own "the filtering existed and a second
# generator walked around it".
#
# Each entry is (tools named, text, alternatives). A step whose tool will not
# dispatch is dropped whole and the remainder RENUMBERED, so the list never
# shows a gap or an orphaned continuation line.
_QUICK_START_STEPS: tuple[tuple[tuple[str, ...], str, tuple[tuple[str, str], ...]], ...] = (
    (
        ("list_repos",),
        "`list_repos` — check if the project is indexed.",
        (("index_folder", "local"), ("index_repo", "GitHub URL")),
    ),
    (
        ("search_symbols",),
        "`search_symbols` — find functions/classes by name or description.",
        (),
    ),
    (
        ("get_context_bundle",),
        "`get_context_bundle` — symbol source + imports in one call.",
        (),
    ),
    (
        ("search_text",),
        "`search_text` — full-text/regex search for literals and comments.",
        (),
    ),
)


def _quick_start_lines(active: Optional[set]) -> list[str]:
    """Numbered quick-start steps, restricted to tools that will dispatch.

    ``active`` of ``None`` means no filtering is needed and the output is
    byte-identical to the pre-#506 literal.
    """
    def _ok(name: str) -> bool:
        return active is None or name in active

    out: list[str] = []
    step = 0
    for tools, text, alternatives in _QUICK_START_STEPS:
        if not all(_ok(t) for t in tools):
            continue
        step += 1
        out.append(f"{step}. {text}")
        available = [f"`{t}` ({label})" for t, label in alternatives if _ok(t)]
        if available:
            out.append("   If not: " + " or ".join(available) + ".")
    return out


def _generate_claude_md_snippet(missing_only: bool = False) -> str:
    """Return the recommended CLAUDE.md prompt-policy snippet.

    When *missing_only* is True, reads ~/.claude/CLAUDE.md and returns only
    the tools not yet mentioned in it (as a minimal addendum block).
    Returns an empty string when the file is already fully up to date.
    """
    all_tools = list(_CANONICAL_TOOL_NAMES)

    if missing_only:
        claude_md = Path.home() / ".claude" / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8", errors="replace")
            missing = [t for t in all_tools if t not in content]
            if not missing:
                return ""
            tool_lines = "\n".join(f"- {t}" for t in missing)
            return (
                f"<!-- jcodemunch-mcp: add these new tools to your existing snippet -->\n"
                f"{tool_lines}\n"
            )
        # Fall through to full generation if CLAUDE.md doesn't exist yet

    # Group tools by category for readability (single source: module constant).
    # Under the front door the server advertises order/menu/route, so a snippet
    # naming ~90 tools directly describes calls the client never offers the
    # model (#397). The catalogue is still reachable, via `menu` and via this
    # guide's own listing, but the WORKFLOW an agent should follow is different,
    # and the workflow is what a policy snippet exists to convey.
    if not missing_only and _effective_surface() == "counter":
        try:
            from .cli.init import _CLAUDE_MD_POLICY_COUNTER
            return _CLAUDE_MD_POLICY_COUNTER
        except Exception:
            logger.debug("front-door snippet unavailable; using the full one", exc_info=True)

    categories = _SNIPPET_TOOL_CATEGORIES

    # #495: filter to what this process will actually dispatch.
    #
    # ⚠⚠ `disabled_tools` ships as `["test_summarizer"]`, so at SHIPPED DEFAULTS
    # this guide advertised a tool `call_tool` then refuses — an agent reads the
    # name here, calls it, and gets an error before the handler runs. Nothing
    # about that is configuration-dependent; it was the out-of-the-box state.
    #
    # ⚠⚠ The filtering already existed and a SECOND generator walked around it.
    # Commit e086e9a ("claude-md respects tool_profile and disabled_tools", #242)
    # added exactly this to `cli/init.py`, which is why the CLI policy path
    # filters correctly today. This function is the other generator and never
    # received it. **Reuse `_get_active_tools` rather than writing a third
    # filter** — a copy is how these two drifted apart in the first place.
    #
    # ⚠ Profile is honoured too, not just `disabled_tools`. The registered
    # description promises the guide "Matches the active tool surface, tier and
    # disabled_tools", and `tier` is the profile. A profile-hidden tool stays
    # dispatchable by name, so naming it costs context rather than erroring
    # (#397) — a weaker harm than the reported one, and the same promise.
    try:
        from .cli.init import _get_active_tools
        _active = _get_active_tools()
    except Exception:
        logger.debug("active-tool filter unavailable; listing all", exc_info=True)
        _active = None
    if _active is not None:
        categories = [
            (cat, [t for t in tools if t in _active])
            for cat, tools in categories
        ]
        # A category emptied by filtering is dropped whole; a bare "**Search:**"
        # with nothing after it reads as a surface with no tools in it.
        categories = [(cat, tools) for cat, tools in categories if tools]

    from . import __version__ as _ver
    lines = [
        f"## jcodemunch-mcp (v{_ver})",
        "",
        "Use jcodemunch-mcp tools instead of Grep/Read/Glob for any indexed repository.",
        "",
        "### Quick start",
        *_quick_start_lines(_active),
        "",
        "### All tools",
    ]
    for cat, tools in categories:
        lines.append(f"**{cat}:** " + ", ".join(f"`{t}`" for t in tools))
    lines.append("")
    lines.append("Never fall back to Grep, Read, or Glob for indexed repos.")
    lines.append("")
    return "\n".join(lines)


def _run_claude_md(generate: bool = False, fmt: str = "full") -> None:
    """Output the recommended CLAUDE.md snippet for the current tool set."""
    missing_only = fmt == "append"
    snippet = _generate_claude_md_snippet(missing_only=missing_only)
    if missing_only and not snippet:
        import sys as _sys
        print("CLAUDE.md is already up to date — no new tools to add.", file=_sys.stderr)
        return
    print(snippet, end="")


def _run_config(check: bool = False, init: bool = False, upgrade: bool = False) -> None:
    """Print the current effective configuration to stdout, or initialize config file."""
    from . import config as _cfg
    from . import __version__

    # Project-aware getter wrapper (issue #300 follow-up, surfaced by @slazarov).
    # If cwd has a .jcodemunch.jsonc, load it and route _cfg.get() through a
    # shim that injects repo=cwd when callers don't pass one explicitly. Without
    # this, `config --check` reports the project file as valid but the printed
    # config values still come from _GLOBAL_CONFIG alone, so any project-level
    # override is silently invisible in diagnostic output.
    _project_repo_key: Optional[str] = None
    _project_loaded_keys: set = set()
    _project_config_path_for_display = Path.cwd() / ".jcodemunch.jsonc"
    if _project_config_path_for_display.is_file():
        try:
            _cfg.load_project_config(str(Path.cwd()))
            _project_repo_key = str(Path.cwd().resolve())
            try:
                _pc_content = _project_config_path_for_display.read_text(encoding="utf-8")
                import json as _json_pc
                _project_loaded_keys = set(_json_pc.loads(_cfg._strip_jsonc(_pc_content)).keys())
            except Exception:
                _project_loaded_keys = set()
        except Exception:
            _project_repo_key = None
            _project_loaded_keys = set()

    class _ProjectAwareCfg:
        """Routes get() through the project-merged config when cwd has one."""
        def __init__(self, module, repo):
            self.__dict__["_mod"] = module
            self.__dict__["_repo"] = repo

        def get(self, key, default=None, repo=None):
            if repo is None:
                repo = self._repo
            return self._mod.get(key, default, repo=repo)

        def __getattr__(self, name):
            return getattr(self._mod, name)

    _cfg = _ProjectAwareCfg(_cfg, _project_repo_key)

    # Handle --upgrade
    if upgrade:
        storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
        config_path = Path(storage_path) / "config.jsonc"

        if not config_path.exists():
            print(f"No config file found at: {config_path}")
            print("Run `config --init` first to create one.")
            return

        added, warnings = _cfg.upgrade_config(config_path)
        if not added:
            print(f"Config is already up to date (version bumped to {__version__}).")
        else:
            print(f"Upgraded config to {__version__}. Added {len(added)} missing key(s):")
            for key in added:
                print(f"  + {key}")
        for w in warnings:
            print(f"  warning: {w}")
        return

    # Handle --init
    if init:
        storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
        config_path = Path(storage_path) / "config.jsonc"

        if config_path.exists():
            print(f"Config file already exists: {config_path}")
            print("Refusing to overwrite. Remove it first or use --check to validate it.")
            return

        config_path.parent.mkdir(parents=True, exist_ok=True)
        template = _cfg.generate_template()
        config_path.write_text(template, encoding="utf-8")
        print(f"Created config template: {config_path}")
        print("Edit it to customize jcodemunch-mcp settings.")
        return

    # Load config to get effective values
    _cfg.load_config()

    tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    enc = getattr(sys.stdout, "encoding", "ascii") or "ascii"

    def _safe(s, fallback):
        try:
            s.encode(enc)
            return s
        except (UnicodeEncodeError, LookupError):
            return fallback

    CHECK = _safe("✓", "OK")
    CROSS = _safe("✗", "!!")
    WARN  = _safe("!", "!")

    def dim(s):   return f"\033[2m{s}\033[0m" if tty else s
    def bold(s):  return f"\033[1m{s}\033[0m" if tty else s
    def green(s): return f"\033[32m{s}\033[0m" if tty else s
    def yellow(s): return f"\033[33m{s}\033[0m" if tty else s
    def red(s):   return f"\033[31m{s}\033[0m" if tty else s

    COL = 36

    def row(name, value, source="default"):
        tag = dim(f" [{source}]") if source != "default" else dim(" (default)")
        print(f"  {name:<{COL}} {value}{tag}")

    def env(var, default=""):
        val = os.environ.get(var)
        return (val if val is not None else default), (val is None)

    def section(title):
        print(f"\n{bold(title)}")

    def cfg_row(name, key, default, source=None, fmt=None):
        """Display a config value with source indicator."""
        val = _cfg.get(key, default)
        if fmt:
            val = fmt(val)
        effective_source = source or "default"
        print(f"  {name:<{COL}} {val}{dim(f' [{effective_source}]')}")

    print(bold(f"jcodemunch-mcp {__version__} — configuration"))

    # ── Config File ───────────────────────────────────────────────────────
    section("Config File")
    storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    config_path = Path(storage_path) / "config.jsonc"
    if config_path.exists():
        print(f"  {green(CHECK)} config.jsonc found: {config_path}")
    else:
        print(f"  {yellow(WARN)} config.jsonc not found: {config_path}")
        print(f"  {dim('  Using defaults + env var fallbacks. Run `config --init` to create a config file.')}")
    # Project-level .jcodemunch.jsonc visibility (jdoc #300 follow-up).
    if _project_repo_key is not None:
        print(
            f"  {green(CHECK)} .jcodemunch.jsonc loaded from cwd: {_project_config_path_for_display} "
            f"{dim(f'({len(_project_loaded_keys)} key(s) override global)')}"
        )
    elif _project_config_path_for_display.is_file():
        print(
            f"  {yellow(WARN)} .jcodemunch.jsonc present but failed to load: "
            f"{_project_config_path_for_display}"
        )

    # ── Indexing ──────────────────────────────────────────────────────────
    section("Indexing")
    # Detect source for each config key
    # Check the actual config file content (if exists) to determine if a key was
    # explicitly set in config vs defaulted
    _loaded_keys: set = set()
    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
            stripped = _cfg._strip_jsonc(content)
            import json as _json
            _loaded_keys = set(_json.loads(stripped).keys())
        except Exception:
            pass

    def _detect_source(key, default):
        if key in _project_loaded_keys:
            return "project"
        if key in _loaded_keys:
            return "config"
        env_var = next((e for e, c in _cfg.ENV_VAR_MAPPING.items() if c == key), None)
        if env_var and os.environ.get(env_var) is not None:
            return "env"
        return "default"

    def _fmt_list(v):
        if isinstance(v, list):
            return f"[{len(v)} items]" if len(v) > 3 else str(v)
        return str(v)

    # max_file_size is reported alongside its two siblings. v1.108.193 gave the
    # per-file cap a config key and an env var but no window: `config` listed the
    # other two limits and not this one, so the only way to read its effective
    # value was to call the resolver by hand — which is exactly what the reporter
    # exists to spare people (#375, @dkiaulakis).
    row("max_file_size", _cfg.get("max_file_size", 512000), _detect_source("max_file_size", 512000))
    row("max_folder_files", _cfg.get("max_folder_files", 2000), _detect_source("max_folder_files", 2000))
    row("max_index_files", _cfg.get("max_index_files", 10000), _detect_source("max_index_files", 10000))
    row("staleness_days", _cfg.get("staleness_days", 7), _detect_source("staleness_days", 7))
    row("max_results", _cfg.get("max_results", 500), _detect_source("max_results", 500))
    patterns = _cfg.get("extra_ignore_patterns", [])
    row("extra_ignore_patterns", _fmt_list(patterns) if patterns else dim("(none)"), _detect_source("extra_ignore_patterns", []))
    exts = _cfg.get("extra_extensions", {})
    row("extra_extensions", _fmt_list(exts) if exts else dim("(none)"), _detect_source("extra_extensions", {}))
    row("context_providers", str(_cfg.get("context_providers", True)).lower(), _detect_source("context_providers", True))
    path_map_val = _cfg.get("path_map", "")
    row("path_map", path_map_val if path_map_val else dim("(none)"), _detect_source("path_map", ""))

    # ── Meta Response Control ─────────────────────────────────────────────
    section("Meta Response Control")
    meta_fields = _cfg.get("meta_fields")
    if meta_fields is None:
        row("meta_fields", dim("(all fields)"), "config")
    elif meta_fields == []:
        row("meta_fields", dim("(none)"), _detect_source("meta_fields", []))
    else:
        row("meta_fields", _fmt_list(meta_fields), _detect_source("meta_fields", None))

    # ── Languages ─────────────────────────────────────────────────────────
    section("Languages")
    languages = _cfg.get("languages")
    if languages is None:
        row("languages", dim("(all languages)"), "default")
    else:
        row("languages", _fmt_list(languages), _detect_source("languages", None))

    # ── Tool Profile ──────────────────────────────────────────────────────
    section("Tool Profile")
    profile = _cfg.get("tool_profile", "full")
    profile_display = {"core": f"{green('core')} (~16 tools)", "standard": f"{yellow('standard')} (~40 tools)", "full": f"{dim('full')} (all tools)"}
    row("tool_profile", profile_display.get(profile, profile), _detect_source("tool_profile", "full"))
    compact = _cfg.get("compact_schemas", False)
    row("compact_schemas", green("enabled") if compact else dim("disabled"), _detect_source("compact_schemas", False))

    # ── Disabled Tools ────────────────────────────────────────────────────
    section("Disabled Tools")
    disabled = _cfg.get("disabled_tools", [])
    row("disabled_tools", _fmt_list(disabled) if disabled else dim("(none)"), _detect_source("disabled_tools", []))

    # ── Tool Tiering ──────────────────────────────────────────────────────
    section("Tool Tiering")
    adaptive = _cfg.get("adaptive_tiering", False)
    row("adaptive_tiering", green("enabled") if adaptive else dim("disabled"), _detect_source("adaptive_tiering", False))
    bundles = _cfg.get("tool_tier_bundles") or {}
    if isinstance(bundles, dict):
        for tier_name in ("core", "standard"):
            tools_in_tier = bundles.get(tier_name, [])
            if isinstance(tools_in_tier, list):
                row(f"  {tier_name} tier", f"{len(tools_in_tier)} tools", "config")
    # Check for bundle/disabled overlap
    from .tier_resolver import validate_bundle_disabled_overlap
    overlap_cfg = {
        "tool_tier_bundles": bundles,
        "disabled_tools": disabled,
    }
    overlap_warnings = validate_bundle_disabled_overlap(overlap_cfg)
    if overlap_warnings:
        for msg in overlap_warnings:
            print(f"  {WARN} {yellow(msg)}")
    else:
        print(f"  {CHECK} {green('No bundle/disabled_tools overlap')}")

    # ── Descriptions ──────────────────────────────────────────────────────
    section("Descriptions")
    descs = _cfg.get("descriptions", {})
    row("descriptions", _fmt_list(descs) if descs else dim("(none)"), _detect_source("descriptions", {}))

    # ── AI Summarizer ─────────────────────────────────────────────────────
    section("AI Summarizer")
    # These two rows read the LOADED config, not the raw environment (#393,
    # @rknighton). They used to call env() with hardcoded "true"/"" defaults, so
    # a config.jsonc setting `use_ai_summaries: false` was reported as `true`,
    # and `summarizer_provider: "none"` as `(auto-detect)` — while _detect_source
    # correctly tagged the row `[config]`. A wrong value wearing an authoritative
    # source tag is worse than no row at all: it says "this is what your file
    # says" about a number the file never contained. Runtime behaviour was always
    # correct; only the diagnostic lied. `summarizer_model` two rows down was
    # already fixed this way for the same reason (#300/#304, @slazarov) — the
    # neighbours were left behind.
    #
    # env-var fallback is already folded into _cfg at load time (config file wins
    # over env), so _cfg.get() IS the effective value and _detect_source() names
    # where it came from. Do not reintroduce a second, parallel resolution here.
    _use_ai_raw = _cfg.get("use_ai_summaries", "auto")
    # Tri-state: True / False / "auto". Render the configured value, then the
    # resolved gate when "auto" hides which way it landed.
    if isinstance(_use_ai_raw, bool):
        _use_ai_display = str(_use_ai_raw).lower()
    else:
        _use_ai_display = str(_use_ai_raw).strip().lower()
        if _use_ai_display == "auto":
            _use_ai_display = f"auto {dim('(resolves to ' + str(_default_use_ai_summaries()).lower() + ')')}"
    row("use_ai_summaries", _use_ai_display, _detect_source("use_ai_summaries", "auto"))
    # The gate the rest of this section branches on — same resolver the server
    # itself uses. Previously the env-only read, which is why a config-disabled
    # summarizer ALSO skipped the "AI summaries disabled" banner below and
    # printed an Active provider line instead.
    use_ai = _default_use_ai_summaries()
    provider = (_cfg.get("summarizer_provider", "") or "").strip()
    _provider_src = _detect_source("summarizer_provider", "")
    row(
        "summarizer_provider",
        provider if provider else dim("(auto-detect)"),
        _provider_src,
    )

    def _provider_pinned_by(name: str) -> str:
        """Explain WHERE an explicit provider pin came from.

        `provider` now reads the merged config, so the old hardcoded
        `JCODEMUNCH_SUMMARIZER_PROVIDER=<x>` suffix would name the env var for a
        pin that actually came from config.jsonc — the same misattribution #393
        is about, one line further down.
        """
        if _provider_src == "env":
            return f"JCODEMUNCH_SUMMARIZER_PROVIDER={name}"
        return f"summarizer_provider={name} [{_provider_src}]"

    # summarizer_model display (surfaced by @slazarov on #300, runtime fix #304).
    # As of v1.108.18, batch_summarize.py threads `repo=` through every
    # _config.get() call, so .jcodemunch.jsonc overrides DO flow to the runtime.
    # The display can now use the project-aware shim value directly.
    _sm_effective = (_cfg.get("summarizer_model", "") or "").strip()
    if _sm_effective:
        row("summarizer_model", _sm_effective, _detect_source("summarizer_model", ""))
    else:
        row("summarizer_model", dim("(provider default)"), "default")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    google_key = os.environ.get("GOOGLE_API_KEY", "")
    openai_base = os.environ.get("OPENAI_API_BASE", "")
    provider_name = get_provider_name()

    if not use_ai:
        print(f"  {yellow('AI summaries disabled')} — signature fallback active")
    elif provider_name == "anthropic":
        suffix = _provider_pinned_by("anthropic") if provider == "anthropic" else "ANTHROPIC_API_KEY set"
        print(f"  Active provider:  {green('Anthropic')}  ({suffix})")
        # Runtime: summarizer_model (config; project-aware as of #304) > ANTHROPIC_MODEL env > default
        if _sm_effective:
            row("  ANTHROPIC_MODEL", _sm_effective, _detect_source("summarizer_model", ""))
        else:
            model, d = env("ANTHROPIC_MODEL", "claude-haiku-*")
            row("  ANTHROPIC_MODEL", model, "env" if not d else "default")
    elif provider_name == "gemini":
        suffix = _provider_pinned_by("gemini") if provider == "gemini" else "GOOGLE_API_KEY set"
        print(f"  Active provider:  {green('Google Gemini')}  ({suffix})")
        if _sm_effective:
            row("  GOOGLE_MODEL", _sm_effective, _detect_source("summarizer_model", ""))
        else:
            model, d = env("GOOGLE_MODEL", "gemini-flash-*")
            row("  GOOGLE_MODEL", model, "env" if not d else "default")
    elif provider_name == "openai":
        base_label = openai_base or "https://api.openai.com/v1"
        suffix = _provider_pinned_by("openai") if provider == "openai" else "OPENAI_API_BASE set"
        print(f"  Active provider:  {green('OpenAI-compatible')}  ({suffix})")
        row("  OPENAI_API_BASE", base_label, "env" if openai_base else "default")
        if _sm_effective:
            row("  OPENAI_MODEL", _sm_effective, _detect_source("summarizer_model", ""))
        else:
            model_default = "gpt-4o-mini" if provider == "openai" and not openai_base else "qwen3-coder"
            model, d = env("OPENAI_MODEL", model_default)
            row("  OPENAI_MODEL", model, "env" if not d else "default")
        v, d = env("OPENAI_TIMEOUT", "60.0")
        row("  OPENAI_TIMEOUT", v, "env" if not d else "default")
        v, d = env("OPENAI_BATCH_SIZE", "10")
        row("  OPENAI_BATCH_SIZE", v, "env" if not d else "default")
        v, d = env("OPENAI_CONCURRENCY", str(_cfg.get("summarizer_concurrency", 4)))
        row("  OPENAI_CONCURRENCY", v, "env" if not d else "config")
        v, d = env("OPENAI_MAX_TOKENS", "500")
        row("  OPENAI_MAX_TOKENS", v, "env" if not d else "default")
    elif provider_name == "minimax":
        suffix = _provider_pinned_by("minimax") if provider == "minimax" else "MINIMAX_API_KEY set"
        print(f"  Active provider:  {green('MiniMax')}  ({suffix})")
        row("  OPENAI_API_BASE", "https://api.minimax.io/v1", "default")
        row("  OPENAI_MODEL", _sm_effective or "minimax-m2.7", _detect_source("summarizer_model", "") if _sm_effective else "default")
    elif provider_name == "glm":
        suffix = _provider_pinned_by("glm") if provider == "glm" else "ZHIPUAI_API_KEY set"
        print(f"  Active provider:  {green('GLM-5')}  ({suffix})")
        row("  OPENAI_API_BASE", "https://api.z.ai/api/paas/v4/", "default")
        row("  OPENAI_MODEL", _sm_effective or "glm-5", _detect_source("summarizer_model", "") if _sm_effective else "default")
    elif provider_name == "openrouter":
        suffix = _provider_pinned_by("openrouter") if provider == "openrouter" else "OPENROUTER_API_KEY set"
        print(f"  Active provider:  {green('OpenRouter')}  ({suffix})")
        row("  OPENAI_API_BASE", "https://openrouter.ai/api/v1", "default")
        row("  OPENAI_MODEL", _sm_effective or "meta-llama/llama-3.3-70b-instruct:free", _detect_source("summarizer_model", "") if _sm_effective else "default")
    elif provider == "none":
        print(f"  Active provider:  {yellow('none')} — explicitly disabled, signature fallback active")
    else:
        print(f"  Active provider:  {yellow('none')} — no API key set, signature fallback active")
        print(f"  {dim('Set ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_BASE, MINIMAX_API_KEY, ZHIPUAI_API_KEY, or OPENROUTER_API_KEY to enable')}")

    allow_remote = _cfg.get("allow_remote_summarizer", False)
    allow_label = str(allow_remote).lower()
    if not allow_remote and provider_name:
        allow_label += f" {dim('(only affects custom base URLs, not standard API endpoints)')}"
    row("allow_remote_summarizer", allow_label, _detect_source("allow_remote_summarizer", False))

    # ── Transport ──────────────────────────────────────────────────────────
    section("Transport")
    transport = _cfg.get("transport", "stdio")
    row("transport", transport, _detect_source("transport", "stdio"))
    if transport != "stdio":
        row("host", _cfg.get("host", "127.0.0.1"), _detect_source("host", "127.0.0.1"))
        row("port", _cfg.get("port", 8901), _detect_source("port", 8901))
        token = os.environ.get("JCODEMUNCH_HTTP_TOKEN", "")
        try:
            from . import credentials as _creds
            _kr_source = _creds.get_keyring_source_for("JCODEMUNCH_HTTP_TOKEN")
        except Exception:
            _kr_source = None
        _kr_label = f"keyring:{_kr_source}" if _kr_source else "env"
        row("JCODEMUNCH_HTTP_TOKEN", green("set") if token else yellow("not set"), _kr_label)
        rate = _cfg.get("rate_limit", 0)
        rate_label = f"{rate}/min per IP" if rate != 0 else "disabled"
        row("rate_limit", rate_label, _detect_source("rate_limit", 0))
    else:
        print(f"  {dim('stdio mode — HTTP transport vars ignored')}")

    # ── Watcher ───────────────────────────────────────────────────────────
    section("Watcher")
    row("watch", str(_cfg.get("watch", False)).lower(), _detect_source("watch", False))
    row("watch_debounce_ms", _cfg.get("watch_debounce_ms", 2000), _detect_source("watch_debounce_ms", 2000))
    row("freshness_mode", _cfg.get("freshness_mode", "relaxed"), _detect_source("freshness_mode", "relaxed"))
    row("claude_poll_interval", _cfg.get("claude_poll_interval", 5.0), _detect_source("claude_poll_interval", 5.0))

    # ── Logging ──────────────────────────────────────────────────────────
    section("Logging")
    row("log_level", _cfg.get("log_level", "WARNING"), _detect_source("log_level", "WARNING"))
    log_file = _cfg.get("log_file")
    row("log_file", log_file if log_file else dim("(stderr)"), _detect_source("log_file", None))

    # ── Privacy & Telemetry ───────────────────────────────────────────────
    section("Privacy & Telemetry")
    row("redact_source_root", str(_cfg.get("redact_source_root", False)).lower(), _detect_source("redact_source_root", False))
    stats_int = _cfg.get("stats_file_interval", 3)
    row("stats_file_interval", "disabled" if stats_int == 0 else f"every {stats_int} calls", _detect_source("stats_file_interval", 3))
    share = _cfg.get("share_savings", True)
    row("share_savings", green("enabled") if share else yellow("disabled"), _detect_source("share_savings", True))
    row("summarizer_concurrency", _cfg.get("summarizer_concurrency", 4), _detect_source("summarizer_concurrency", 4))

    # ── Keyring resolution (P1.3) ─────────────────────────────────────────
    # Surfaces which credential env vars were resolved from the system keyring
    # at startup. Helps an operator confirm the chokepoint is firing without
    # having to inspect the actual secret value.
    try:
        from . import credentials as _creds
        _resolved = [
            (var, _creds.get_keyring_source_for(var))
            for var in _creds.list_recognised_env_vars()
            if _creds.get_keyring_source_for(var) is not None
        ]
        if _resolved:
            section("Keyring resolution")
            for var, entry in _resolved:
                row(var, green("resolved"), f"keyring:{entry}")
    except Exception:
        pass  # keyring not installed, env vars not touched — nothing to show

    # ── --check ───────────────────────────────────────────────────────────
    if check:
        section("Checks")
        issues: list[str] = []
        # Sandbox/host-visibility-limited probes that could NOT be confirmed
        # either way from inside this process. Distinct from `issues`: a
        # restricted agent shell that cannot prove host writability must not be
        # reported as a confirmed configuration failure (issue #335).
        host_confirmation: list[str] = []

        # Validate config.jsonc
        config_issues = _cfg.validate_config(str(config_path))
        if config_issues:
            for issue in config_issues:
                print(f"  {red(CROSS)} config.jsonc: {issue}")
            issues.append("config")
        else:
            print(f"  {green(CHECK)} config.jsonc valid: {config_path}")

        # Probe cwd for project-level .jcodemunch.jsonc and validate if found.
        # Without this, users editing project config see no signal that the
        # file is being parsed at all (issue #300).
        project_config_path = Path.cwd() / ".jcodemunch.jsonc"
        if project_config_path.is_file():
            project_issues = _cfg.validate_config(str(project_config_path))
            if project_issues:
                for issue in project_issues:
                    print(f"  {red(CROSS)} .jcodemunch.jsonc: {issue}")
                issues.append("project_config")
            else:
                print(f"  {green(CHECK)} .jcodemunch.jsonc valid: {project_config_path}")

        # Storage writable?
        storage = Path(storage_path)
        try:
            storage.mkdir(parents=True, exist_ok=True)
            probe = storage / ".jcm_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"  {green(CHECK)} index storage writable: {storage}")
        except PermissionError as e:
            # In a sandboxed/restricted agent shell, EPERM/EACCES means "this
            # process cannot prove host writability", NOT "the host index
            # storage is actually unwritable". Don't present an indeterminate
            # sandbox probe as a confirmed failure (issue #335) — flag it for
            # host confirmation and tell the operator to rerun unsandboxed.
            if e.errno in {errno.EPERM, errno.EACCES}:
                print(
                    f"  {yellow(WARN)} index storage writability needs host confirmation: "
                    f"{storage} — {e}"
                )
                host_confirmation.append("storage")
            else:
                print(f"  {red(CROSS)} index storage not writable: {storage} — {e}")
                issues.append("storage")
        except Exception as e:
            print(f"  {red(CROSS)} index storage not writable: {storage} — {e}")
            issues.append("storage")

        # AI provider package installed?
        if use_ai:
            if provider_name == "anthropic":
                try:
                    import anthropic as _a
                    print(f"  {green(CHECK)} anthropic package installed (v{_a.__version__})")
                except ImportError:
                    print(f"  {red(CROSS)} anthropic not installed — run: pip install \"jcodemunch-mcp[anthropic]\"")
                    issues.append("anthropic")
            elif provider_name == "gemini":
                try:
                    import google.generativeai  # noqa: F401
                    print(f"  {green(CHECK)} google-generativeai package installed")
                except ImportError:
                    print(f"  {red(CROSS)} google-generativeai not installed — run: pip install \"jcodemunch-mcp[gemini]\"")
                    issues.append("gemini")
            elif provider_name in {"openai", "minimax", "glm"}:
                try:
                    import httpx  # noqa: F401
                    print(f"  {green(CHECK)} httpx available for OpenAI-compatible requests")
                except ImportError:
                    print(f"  {red(CROSS)} httpx not installed (required for OpenAI-compatible summarizer)")
                    issues.append("httpx")
            else:
                print(f"  {yellow(WARN)} no AI provider configured — signature fallback will be used")

        # HTTP transport packages installed?
        if transport != "stdio":
            missing = [pkg for pkg in ("uvicorn", "starlette", "anyio") if not _can_import(pkg)]
            if missing:
                print(f"  {red(CROSS)} HTTP packages missing: {', '.join(missing)} — run: pip install \"jcodemunch-mcp[http]\"")
                issues.append("http")
            else:
                print(f"  {green(CHECK)} HTTP transport packages installed (uvicorn, starlette, anyio)")

        # ── CLAUDE.md drift check ────────────────────────────────────────────
        section("CLAUDE.md check")
        claude_md_path = Path.home() / ".claude" / "CLAUDE.md"
        canonical_tools = list(_CANONICAL_TOOL_NAMES)
        if claude_md_path.exists():
            try:
                cm_content = claude_md_path.read_text(encoding="utf-8", errors="replace")
                # The README documents a supported one-line form: "Call the
                # jcodemunch_guide tool and strictly follow its instructions."
                # That tool returns the per-version policy at runtime, so the
                # full canonical tool list is not expected to appear in CLAUDE.md.
                # Treat any mention of jcodemunch_guide as valid setup.
                if "jcodemunch_guide" in cm_content:
                    print(f"  {green(CHECK)} CLAUDE.md uses jcodemunch_guide one-line form (version-pinned at runtime)")
                else:
                    missing_in_cm = [t for t in canonical_tools if t not in cm_content]
                    if missing_in_cm:
                        # Wrap into ~60-char lines for readability
                        _wrapped = _wrap_names(missing_in_cm)
                        print(f"  {yellow(WARN)} {len(missing_in_cm)} tool(s) not mentioned in CLAUDE.md:")
                        for _line in _wrapped:
                            print(f"       {dim(_line)}")
                        print(f"  {dim('  Run: jcodemunch-mcp claude-md --generate  (or --format=append for delta only)')}")
                        print(f"  {dim('  Or use the one-line form: add `Call the jcodemunch_guide tool and strictly follow its instructions.` to CLAUDE.md')}")
                        issues.append("claude_md")
                    else:
                        print(f"  {green(CHECK)} All {len(canonical_tools)} tools mentioned in CLAUDE.md")
            except Exception as _e:
                print(f"  {yellow(WARN)} Could not read CLAUDE.md: {_e}")
        else:
            print(f"  {yellow(WARN)} CLAUDE.md not found: {claude_md_path}")
            print(f"  {dim('  Run: jcodemunch-mcp claude-md --generate > /path/to/CLAUDE.md')}")

        # ── Hook check ─────────────────────────────────────────────────────────
        section("Hooks check")
        _settings_path = Path.home() / ".claude" / "settings.json"
        # DERIVED from the installer, never hand-listed. A hand-maintained copy
        # reports a hook `init` really installs as "not installed", i.e. the
        # diagnostic disagreeing with the runtime; `hook-sessionstart` was
        # omitted from the copy the day it shipped.
        from .cli.init import _enforcement_hooks, _extract_jcm_subcommand
        _expected_hooks = {}
        for _event, _rules in _enforcement_hooks().items():
            for _rule in _rules:
                for _h in _rule.get("hooks", []):
                    _sub = _extract_jcm_subcommand(_h.get("command", ""))
                    if _sub:
                        _expected_hooks[_sub] = (_event, _rule.get("matcher", ""))
        if _settings_path.exists():
            try:
                _settings = json.loads(_settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _settings = {}
            _installed_hooks = _settings.get("hooks", {})
            _found_any = False
            for _hook_cmd, (_event, _matcher) in _expected_hooks.items():
                # Compare SUBCOMMANDS, not a `jcodemunch-mcp <sub>` substring.
                # `_hook_invocation()` writes an absolute path whenever
                # `shutil.which` resolves, so the substring never matched an
                # `C:/.../jcodemunch-mcp.EXE hook-pretooluse` install and the
                # check reported every hook missing on a correctly-installed box.
                _present = False
                for _rule in _installed_hooks.get(_event, []):
                    for _h in _rule.get("hooks", []):
                        if _extract_jcm_subcommand(_h.get("command", "")) == _hook_cmd:
                            _present = True
                            break
                if _present:
                    _label = f"{_event}({_matcher})" if _matcher else _event
                    print(f"  {green(CHECK)} {_hook_cmd} installed [{_label}]")
                    _found_any = True
                else:
                    print(f"  {dim(f'  {_hook_cmd} not installed')}")
            if not _found_any:
                print(f"  {dim('  Run: jcodemunch-mcp init --hooks')}")
            # Warn about legacy shell scripts
            _hooks_dir = Path.home() / ".claude" / "hooks"
            if _hooks_dir.exists():
                _legacy = (
                    list(_hooks_dir.glob("jcodemunch_read_guard.*"))
                    + list(_hooks_dir.glob("jcodemunch_edit_guard.*"))
                    + list(_hooks_dir.glob("jcodemunch_index_hook.*"))
                )
                if _legacy:
                    print(f"  {yellow(WARN)} Legacy shell scripts detected (replaced by Python hooks):")
                    for _script in sorted(_legacy):
                        print(f"       {dim(_script.name)}")
                    print(f"       {dim('These can be removed. Run: jcodemunch-mcp init --hooks')}")
        else:
            print(f"  {dim('(~/.claude/settings.json not found — hooks not installed)')}")
            print(f"  {dim('  Run: jcodemunch-mcp init --hooks')}")

        print()
        if issues:
            print(yellow(f"  {len(issues)} issue(s) found — see above."))
            sys.exit(1)
        elif host_confirmation:
            # No confirmed failures, but at least one probe could only be
            # answered by the host (sandbox-limited). Exit 0 so an agent client
            # does not mistake a healthy install for a broken one, but tell the
            # operator to rerun outside the sandbox before acting on it (#335).
            print(yellow(
                f"  {len(host_confirmation)} check(s) need host confirmation — see above."
            ))
            print(dim(
                "  Rerun outside a sandbox or restricted shell before repairing"
                " or reporting drift."
            ))
        else:
            print(green("  All checks passed."))
    print()


def _wrap_names(names: list[str], width: int = 72) -> list[str]:
    """Wrap a flat list of names into lines no longer than *width* chars."""
    lines: list[str] = []
    current = ""
    for name in names:
        piece = (", " if current else "") + name
        if current and len(current) + len(piece) > width:
            lines.append(current)
            current = name
        else:
            current += piece
    if current:
        lines.append(current)
    return lines


def _can_import(module: str) -> bool:
    """Return True if module is importable without side effects."""
    import importlib.util
    return importlib.util.find_spec(module) is not None


def _format_refresh(out: dict) -> str:
    """Human-readable rendering of a `refresh` run or status (#395).

    States what remains and what it will cost, because the operator's actual
    question is "can I fit the rest of this in tonight's window", and a percent
    alone does not answer it.
    """
    if not out.get("success"):
        return f"error: {out.get('error')}"

    lines = [f"repo: {out.get('repo')}"]

    if "campaign" in out:  # --status
        gen, target = out.get("parser_generation"), out.get("parser_generation_target")
        if gen is None:
            return "\n".join(lines + ["no index for this path; run `jcodemunch-mcp index` first"])
        lines.append(f"parser generation: {gen} (current: {target})")
        if out.get("needs_refresh"):
            lines.append(f"NEEDS REFRESH: {out.get('needs_refresh_reason')}")
        c = out.get("campaign")
        if not c:
            lines.append("no campaign in progress")
            return "\n".join(lines)
        lines.append(
            f"campaign ({c.get('reason')}): {c.get('completed_files')}/{c.get('total_files')} "
            f"files, {c.get('percent')}% done, {c.get('remaining_files')} remaining"
        )
        lines.append(f"slices run: {c.get('slices_run')}, last run: {c.get('last_run_at')}")
        if c.get("complete"):
            lines.append("complete" + (", generation stamped" if c.get("stamped") else ""))
        if c.get("errors"):
            lines.append(f"recent errors: {len(c['errors'])} (see --json)")
        return "\n".join(lines)

    done, total = out.get("completed_files", 0), out.get("total_files", 0)
    lines.append(
        f"this run: {out.get('files_this_run')} files in {out.get('duration_seconds')}s "
        f"({out.get('stopped_because')})"
    )
    lines.append(f"progress: {done}/{total} files, {out.get('remaining_files')} remaining")

    per_file = (out.get("duration_seconds") or 0) / max(out.get("files_this_run") or 0, 1)
    remaining = out.get("remaining_files") or 0
    if remaining and per_file > 0:
        lines.append(
            f"estimated remaining work: ~{round(per_file * remaining / 60.0, 1)} min "
            f"at this run's rate"
        )
    if out.get("corpus_drifted"):
        lines.append(
            f"corpus grew by {out['corpus_drifted']} file(s) during the campaign; "
            f"they were appended and the generation stamp is deferred"
        )
    if out.get("complete"):
        lines.append(
            "campaign complete"
            + (f", parser generation stamped to {out.get('parser_generation')}"
               if out.get("stamped")
               else f", NOT stamped ({out.get('stamp_skipped_reason')})")
        )
    else:
        lines.append("run again to continue")
    if out.get("errors"):
        lines.append(f"errors: {len(out['errors'])} (see --json)")
    return "\n".join(lines)


def _force_utf8_stdio() -> None:
    """Make CLI output UTF-8 regardless of the platform locale (v1.108.262).

    ⚠⚠ On Windows, `sys.stdout` is the **console** stream (already UTF-8) when
    attached to a terminal and the **locale** stream (cp1252) when piped or
    redirected. So a command that prints any character cp1252 cannot encode
    works interactively and dies the moment anyone consumes it:

        jcodemunch-mcp receipt --explain            # fine
        jcodemunch-mcp receipt --explain | more     # UnicodeEncodeError, no output

    Measured: `receipt --explain` writes U+2212 MINUS SIGN and `render_diagram`
    writes U+2713 CHECK MARK. That is a hard traceback out of a shipped command,
    in the one configuration nobody exercises by hand and every script uses.
    Sibling of the cp1252 DECODE class swept in v1.108.230; that sweep covered
    subprocess input and left output alone.

    Fixed at the entry point rather than per string, because the next non-ASCII
    character someone adds must not reintroduce this.

    ⚠ The MCP stdio transport is unaffected: it wraps `sys.stdout.buffer` in its
    own TextIOWrapper, so it never reads the text layer being reconfigured here.
    Verified before this shipped.

    ⚠ `errors="replace"` is deliberate. Filesystem paths can carry surrogates
    from a `surrogateescape` decode, and those raise even under UTF-8. Mangling
    one character in a display string beats killing the command.

    ⚠ `PYTHONIOENCODING` is honoured as an explicit opt-out: if the operator
    named an encoding, that is a decision, not an accident.
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # replaced by a test harness or a captured buffer
            continue
        current = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if current in ("utf8", "utf8mb4"):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A stream that refuses reconfiguration is not a reason to refuse to
            # run; the command simply keeps the behaviour it had before.
            logger.debug("Could not force UTF-8 on sys.%s", stream_name, exc_info=True)


def main(argv: Optional[list[str]] = None):
    """Main entry point."""
    _force_utf8_stdio()
    from .security import verify_package_integrity
    verify_package_integrity()

    parser = argparse.ArgumentParser(
        prog="jcodemunch-mcp",
        description="jCodeMunch MCP server and tools.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- serve (default when no subcommand given) ---
    serve_parser = subparsers.add_parser("serve", help="Run the MCP server (default)")
    # Defaults are None so _resolve_serve_endpoint can apply the full precedence
    # (CLI flag > env var > config key > hardcoded default). An unset flag must
    # be distinguishable from a deliberately-passed value.
    serve_parser.add_argument(
        "--transport",
        default=None,
        choices=["stdio", "sse", "streamable-http"],
        help="Transport mode: stdio (default), sse, or streamable-http (also via JCODEMUNCH_TRANSPORT env var or the `transport` config key)",
    )
    serve_parser.add_argument(
        "--host",
        default=None,
        help="Host to bind to in HTTP transport mode (also via JCODEMUNCH_HOST env var or the `host` config key, default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on in HTTP transport mode (also via JCODEMUNCH_PORT env var or the `port` config key, default: 8901)",
    )
    _add_common_args(serve_parser)

    # --- Watcher options for serve ---
    serve_parser.add_argument(
        "--watcher",
        nargs="?",
        const="true",
        default=None,
        metavar="BOOL",
        help="Enable background file watcher alongside the server. "
             "Use --watcher or --watcher=true to enable, --watcher=false to disable.",
    )
    serve_parser.add_argument(
        "--watcher-path",
        nargs="*",
        default=None,
        metavar="PATH",
        help="Folder(s) to watch (default: current working directory)",
    )
    serve_parser.add_argument(
        "--watcher-debounce",
        type=int,
        default=None,
        metavar="MS",
        help="Watcher debounce interval in ms (default: from config, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
    )
    serve_parser.add_argument(
        "--watcher-idle-timeout",
        type=int,
        default=None,
        metavar="MINUTES",
        help="Auto-stop watcher after N minutes with no re-indexing (default: disabled)",
    )
    serve_parser.add_argument(
        "--watcher-no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries for watcher re-indexing",
    )
    serve_parser.add_argument(
        "--watcher-extra-ignore",
        nargs="*",
        help="Additional gitignore-style patterns to exclude from watching",
    )
    serve_parser.add_argument(
        "--watcher-follow-symlinks",
        action="store_true",
        help="Include symlinked files in watcher indexing",
    )
    serve_parser.add_argument(
        "--watcher-log",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="Log watcher output to file instead of stderr. "
             "Use --watcher-log for auto temp file, or --watcher-log=<path> for a specific file.",
    )
    serve_parser.add_argument(
        "--freshness-mode",
        default=None,
        choices=["relaxed", "strict"],
        help="Freshness mode: 'relaxed' (default) or 'strict' (block queries until watcher reindex finishes)",
    )

    # --- watch ---
    watch_parser = subparsers.add_parser(
        "watch",
        help="Watch folders for changes and auto-reindex",
    )
    watch_parser.add_argument(
        "paths",
        nargs="+",
        help="One or more folder paths to watch",
    )
    watch_parser.add_argument(
        "--debounce",
        type=int,
        default=None,
        metavar="MS",
        help="Debounce interval in ms (default: from config, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
    )
    watch_parser.add_argument(
        "--no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries during re-indexing",
    )
    watch_parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Include symlinked files in indexing",
    )
    watch_parser.add_argument(
        "--extra-ignore",
        nargs="*",
        help="Additional gitignore-style patterns to exclude",
    )
    watch_parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        metavar="MINUTES",
        help="Auto-shutdown after N minutes with no re-indexing (default: disabled)",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="Index all paths once (incremental) and exit immediately — no file watching",
    )
    _add_common_args(watch_parser)

    # --- config ---
    config_parser = subparsers.add_parser(
        "config",
        help="Show current effective configuration",
    )
    config_parser.add_argument(
        "--check",
        action="store_true",
        help="Also verify prerequisites (storage writable, AI packages installed, HTTP packages present)",
    )
    config_parser.add_argument(
        "--init",
        action="store_true",
        help="Generate a template config.jsonc file in CODE_INDEX_PATH",
    )
    config_parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Add missing keys from the current template to an existing config.jsonc, preserving user values",
    )
    config_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the effective configuration as structured JSON (key/type/value/default/source) for tooling",
    )
    config_parser.add_argument(
        "action",
        nargs="?",
        choices=["set", "unset"],
        help="set <key> <value> to write a config key, or unset <key> to clear it (default applies)",
    )
    config_parser.add_argument("key", nargs="?", help="config key for set/unset")
    config_parser.add_argument(
        "value", nargs="?",
        help="value for set: JSON (true, 7, [\"a\"], {\"k\":1}) or a bare string",
    )

    # --- list-repos ---
    list_repos_parser = subparsers.add_parser(
        "list-repos",
        help="List indexed repositories with counts, freshness, and watcher state",
    )
    list_repos_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON (repo_id/counts/languages/indexed_at/freshness/watcher_state/lock_holder)",
    )

    # --- surface (tool-surface schema receipt) ---
    surface_parser = subparsers.add_parser(
        "surface",
        help="Print the tool-surface schema receipt: visible vs catalog tool counts, schema token weight, tokens avoided by the active surface/tier",
    )
    surface_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the raw tool_surface block as JSON (same shape get_session_stats reports)",
    )

    # --- delete-index (CLI alias for the invalidate_cache tool) ---
    delete_index_parser = subparsers.add_parser(
        "delete-index",
        help="Delete a repository's index and cached data (CLI alias for the invalidate_cache tool)",
    )
    delete_index_parser.add_argument(
        "repo",
        help="Repository identifier (owner/repo or repo name, as shown by list-repos)",
    )
    delete_index_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the structured {success, repo, message|error} JSON result",
    )

    # --model choices derive from the single receipt price table so the two
    # CLI subparsers can never drift from the rates (see cli/receipt.py).
    from .cli.receipt import _MODEL_PRICES_USD_PER_MTOK as _RECEIPT_MODEL_PRICES
    _receipt_model_choices = sorted(_RECEIPT_MODEL_PRICES)

    # --- org-report / org-rollup (team SKU) ---
    org_report_parser = subparsers.add_parser(
        "org-report",
        help="Record this seat's token savings under its org (JCODEMUNCH_ORG_ID)",
    )
    org_report_parser.add_argument("--org", help="Org identifier (overrides JCODEMUNCH_ORG_ID)")
    org_report_parser.add_argument("--seat", help="Seat identifier (default: JCODEMUNCH_CLIENT_ID or hostname)")
    org_report_parser.add_argument("--endpoint", help="Org host URL to POST to (overrides JCODEMUNCH_ORG_ENDPOINT); omit to record locally")
    org_report_parser.add_argument("--model", default="opus", choices=_receipt_model_choices, help="Rate for the $ figure")
    org_report_parser.add_argument("--json", action="store_true", help="Emit JSON")

    org_rollup_parser = subparsers.add_parser(
        "org-rollup",
        help="Aggregate token savings across all seats in an org",
    )
    org_rollup_parser.add_argument("--org", help="Org identifier (overrides JCODEMUNCH_ORG_ID)")
    org_rollup_parser.add_argument("--json", action="store_true", help="Emit structured JSON (seats[] + totals)")

    license_parser = subparsers.add_parser(
        "license",
        help="Check jCodeMunch license status (gates the org-rollup team feature)",
    )
    license_parser.add_argument("--key", help="Validate this key (else uses JCODEMUNCH_LICENSE_KEY / config)")
    license_parser.add_argument("--json", action="store_true", help="Emit JSON status")

    # --- claude-md ---
    claude_md_parser = subparsers.add_parser(
        "claude-md",
        help="Generate a CLAUDE.md prompt-policy snippet for the current tool set",
    )
    claude_md_parser.add_argument(
        "--generate",
        action="store_true",
        help="Output the recommended CLAUDE.md snippet to stdout",
    )
    claude_md_parser.add_argument(
        "--format",
        choices=["full", "append"],
        default="full",
        dest="fmt",
        help="'full' (default) — complete snippet; 'append' — only tools not yet in your CLAUDE.md",
    )

    # --- index-file ---
    # --- index (full folder/repo index) ---
    index_parser = subparsers.add_parser(
        "index",
        help="Index a local folder or GitHub repo (default: current directory)",
    )
    index_parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Local path or owner/repo (default: current directory)",
    )
    index_parser.add_argument(
        "--no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries",
    )
    index_parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Include symlinked files in indexing",
    )
    index_parser.add_argument(
        "--extra-ignore",
        nargs="*",
        help="Additional gitignore-style patterns to exclude",
    )
    index_parser.add_argument(
        "--paths-from",
        metavar="FILE",
        help=(
            "Read explicit paths to index (one per line) from FILE. Use '-' for "
            "stdin. When set, the directory walk is skipped — only the listed "
            "paths are indexed. Entries may be absolute or relative to the "
            "target. Pipe-friendly with git / find / fd / rg. Lines starting "
            "with `#` are comments."
        ),
    )
    _add_common_args(index_parser)

    # --- index-file ---
    index_file_parser = subparsers.add_parser(
        "index-file",
        help="Re-index a single file within an existing indexed folder",
    )
    index_file_parser.add_argument(
        "path",
        help="Absolute path to the file to index",
    )
    index_file_parser.add_argument(
        "--no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries for this file",
    )
    _add_common_args(index_file_parser)

    # --- import-trace (Phases 1 + 4 + 5: OTel + SQL log + stack log ingest) ---
    import_trace_parser = subparsers.add_parser(
        "import-trace",
        help="Ingest a runtime trace file (OTel / SQL log / stack log) into the runtime_* tables",
    )
    import_trace_parser.add_argument(
        "--otel",
        dest="otel_path",
        metavar="PATH",
        help="Path to an OTel JSON, JSON-Lines, or .gz trace file",
    )
    import_trace_parser.add_argument(
        "--sql-log",
        dest="sql_log_path",
        metavar="PATH",
        help="Path to a pg_stat_statements CSV or generic SQL query JSON-Lines log",
    )
    import_trace_parser.add_argument(
        "--stack-log",
        dest="stack_log_path",
        metavar="PATH",
        help="Path to a plain-text app log or JSON-Lines record set with Python / JVM / Node.js stack traces",
    )
    import_trace_parser.add_argument(
        "--repo",
        dest="repo",
        default=None,
        help="Repo identifier (owner/name) — defaults to resolving the current directory",
    )
    import_trace_parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Disable PII redaction. Use ONLY for offline debugging on synthetic data.",
    )
    _add_common_args(import_trace_parser)

    # --- import-scip (compile-time evidence: SCIP index ingest) ---
    import_scip_parser = subparsers.add_parser(
        "import-scip",
        help="Ingest a SCIP index file (compiler-verified cross-references) into the scip_* tables",
    )
    import_scip_parser.add_argument(
        "scip_path",
        metavar="PATH",
        help="Path to a .scip index file (as emitted by scip-typescript / scip-python / scip-java / scip-go / rust-analyzer; .gz accepted)",
    )
    import_scip_parser.add_argument(
        "--repo",
        dest="repo",
        default=None,
        help="Repo identifier (owner/name) — defaults to resolving the current directory",
    )
    _add_common_args(import_scip_parser)

    # --- init ---
    init_parser = subparsers.add_parser(
        "init",
        help="One-command setup: register with MCP clients, install CLAUDE.md policy, hooks, and index",
    )
    init_parser.add_argument(
        "--client",
        nargs="*",
        default=None,
        metavar="CLIENT",
        help="MCP clients to configure (auto, claude-code, claude-desktop, cursor, windsurf, continue, none)",
    )
    init_parser.add_argument(
        "--claude-md",
        choices=["global", "project"],
        default=None,
        dest="claude_md",
        help="Install Code Exploration Policy to CLAUDE.md (global = ~/.claude/CLAUDE.md, project = ./CLAUDE.md)",
    )
    init_parser.add_argument(
        "--hooks",
        action="store_true",
        default=None,
        help="Install worktree lifecycle hooks into ~/.claude/settings.json",
    )
    init_parser.add_argument(
        "--copilot-hooks",
        action="store_true",
        dest="copilot_hooks",
        help="Write .github/hooks/hooks.json so GitHub Copilot CLI / cloud agent auto-reindex on edit",
    )
    init_parser.add_argument(
        "--index",
        action="store_true",
        help="Index the current working directory after setup",
    )
    init_parser.add_argument(
        "--audit",
        action="store_true",
        help="Audit agent config files for token waste, stale references, and bloat",
    )
    init_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would be done without making changes",
    )
    init_parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Walk through the full init process without making any changes, "
            "then summarise what would have been done and the benefit of each action"
        ),
    )
    init_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Accept all defaults non-interactively",
    )
    init_parser.add_argument(
        "--no-backup",
        action="store_true",
        dest="no_backup",
        help="Skip creating .bak backups of modified files",
    )
    init_parser.add_argument(
        "--share-savings",
        choices=["on", "off"],
        default=None,
        dest="share_savings",
        help=(
            "Explicitly write share_savings:<on|off> into ~/.code-index/config.jsonc. "
            "Useful for hardened install templates that need a durable opt-out; survives "
            "package upgrades because config --upgrade preserves user-set values."
        ),
    )
    init_parser.add_argument(
        "--no-share-savings",
        action="store_const",
        const="off",
        dest="share_savings",
        help="Shorthand for --share-savings=off.",
    )
    init_parser.add_argument(
        "--minimal",
        action="store_true",
        dest="minimal",
        help=(
            "Write only the MCP server registration; skip every other channel "
            "(CLAUDE.md policy paste, Cursor/Windsurf rules, AGENTS.md, hooks, "
            ".github/hooks, indexing, audit). Recommended for hardened install "
            "templates that don't want jcodemunch touching agent-policy files."
        ),
    )
    init_parser.add_argument(
        "--strict",
        action="store_true",
        dest="strict",
        help=(
            "Enforce munch-first hard: the PreToolUse hook DENIES native Read/Grep "
            "inside an indexed repo (use jcm tools instead). Installs the enforcement "
            "hooks and sets JCODEMUNCH_ENFORCE=strict in ~/.claude/settings.json. "
            "Offset/limit reads and paths outside every indexed repo still pass; "
            "default (no flag) stays advisory warn-only. Revert by re-running init "
            "without --strict."
        ),
    )

    # --- install (per-agent sugar over init) ---
    install_parser = subparsers.add_parser(
        "install",
        help="Per-agent install shortcut. `install claude-code` is sugar for `init --client claude-code --yes`.",
    )
    install_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Agent target: claude-code, claude-desktop, cursor, windsurf, continue, all. "
             "Omit with --list/--status for info-only output.",
    )
    install_parser.add_argument(
        "--list",
        action="store_true",
        dest="list_targets",
        help="List valid install targets and exit",
    )
    install_parser.add_argument(
        "--status",
        action="store_true",
        dest="status",
        help="Print current install state across every target",
    )
    install_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="With --status: emit JSON instead of pretty-printed output",
    )
    install_parser.add_argument(
        "--skills", action="store_true", dest="skills",
        help="Also emit the jcodemunch Claude Agent Skill bundle (.claude/skills/jcodemunch/SKILL.md)",
    )
    install_parser.add_argument(
        "--skills-scope", choices=["global", "project"], default="global",
        dest="skills_scope",
        help="Where to write the skill (default: global = ~/.claude/skills/jcodemunch/)",
    )
    install_parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Show what would happen without making changes",
    )
    install_parser.add_argument(
        "--no-backup", action="store_true", dest="no_backup",
        help="Skip creating .bak backups of modified files",
    )
    install_parser.add_argument(
        "--share-savings",
        choices=["on", "off"],
        default=None,
        dest="share_savings",
        help=(
            "Explicitly write share_savings:<on|off> into ~/.code-index/config.jsonc. "
            "Survives package upgrades."
        ),
    )
    install_parser.add_argument(
        "--no-share-savings",
        action="store_const",
        const="off",
        dest="share_savings",
        help="Shorthand for --share-savings=off.",
    )
    install_parser.add_argument(
        "--minimal",
        action="store_true",
        dest="minimal",
        help=(
            "Write only the MCP server registration; skip CLAUDE.md, rules, "
            "AGENTS.md, hooks, .github/hooks, indexing, audit."
        ),
    )

    # --- install-status (top-level read-only inspector) ---
    status_parser = subparsers.add_parser(
        "install-status",
        help="Print current install state (clients, policies, hooks).",
    )
    status_parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit JSON instead of pretty-printed output",
    )

    # --- uninstall ---
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Reverse `init` / `install`: remove jcodemunch entries from configs, policies, and hooks.",
    )
    uninstall_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Agent target to uninstall (claude-code, claude-desktop, cursor, windsurf, continue, all). "
             "Omit to uninstall every detected target plus shared policies and hooks.",
    )
    uninstall_parser.add_argument(
        "--keep-claude-md", action="store_true", dest="keep_claude_md",
        help="Preserve the CLAUDE.md policy block (do not strip it)",
    )
    uninstall_parser.add_argument(
        "--keep-cursor-rules", action="store_true", dest="keep_cursor_rules",
        help="Preserve .cursor/rules/jcodemunch.mdc",
    )
    uninstall_parser.add_argument(
        "--keep-windsurf-rules", action="store_true", dest="keep_windsurf_rules",
        help="Preserve the .windsurfrules policy block",
    )
    uninstall_parser.add_argument(
        "--keep-agents-md", action="store_true", dest="keep_agents_md",
        help="Preserve the AGENTS.md policy block",
    )
    uninstall_parser.add_argument(
        "--keep-hooks", action="store_true", dest="keep_hooks",
        help="Preserve jcodemunch hooks in ~/.claude/settings.json",
    )
    uninstall_parser.add_argument(
        "--keep-copilot-hooks", action="store_true", dest="keep_copilot_hooks",
        help="Preserve the Copilot postToolUse hook in .github/hooks/hooks.json",
    )
    uninstall_parser.add_argument(
        "--keep-skills", action="store_true", dest="keep_skills",
        help="Preserve the jcodemunch Claude Agent Skill bundle (~/.claude/skills/jcodemunch/)",
    )
    uninstall_parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Show what would happen without making changes",
    )
    uninstall_parser.add_argument(
        "--no-backup", action="store_true", dest="no_backup",
        help="Skip creating .bak backups of modified files",
    )
    uninstall_parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Accept all defaults non-interactively",
    )

    # --- hook-event ---
    hook_parser = subparsers.add_parser(
        "hook-event",
        help="Record a Claude Code worktree lifecycle event (used by hooks)",
    )
    hook_parser.add_argument(
        "event_type",
        choices=["create", "remove"],
        help="Event type: 'create' when a worktree is created, 'remove' when deleted",
    )
    _add_common_args(hook_parser)

    # --- hook-pretooluse ---
    subparsers.add_parser(
        "hook-pretooluse",
        help="PreToolUse hook: intercept Read on large code files, suggest jCodemunch (reads stdin)",
    )

    # --- hook-posttooluse ---
    subparsers.add_parser(
        "hook-posttooluse",
        help="PostToolUse hook: auto-reindex files after Edit/Write (reads stdin)",
    )

    # --- hook-copilot-posttooluse ---
    subparsers.add_parser(
        "hook-copilot-posttooluse",
        help="GitHub Copilot postToolUse hook: auto-reindex files after Edit/Write (reads stdin)",
    )

    # --- upgrade ---
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="Upgrade jcodemunch-mcp via pip and refresh hooks/config",
    )
    upgrade_parser.add_argument(
        "--no-pip",
        action="store_true",
        dest="no_pip",
        help="Skip 'pip install -U' and only refresh hooks/config",
    )
    upgrade_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Run init refresh non-interactively",
    )

    # --- observatory ---
    obs_parser = subparsers.add_parser(
        "observatory",
        help="Run the public OSS code-health observatory pipeline (static-site output).",
    )
    obs_sub = obs_parser.add_subparsers(dest="obs_action")
    obs_build = obs_sub.add_parser("build", help="Run the full pipeline against a config file.")
    obs_build.add_argument("--config", required=True, help="Path to the observatory config JSON.")
    obs_build.add_argument("--output-dir", default=None, help="Override config's output_dir.")
    obs_build.add_argument("--workdir", default=None, help="Override config's workdir.")
    obs_init = obs_sub.add_parser("init", help="Write a starter config file.")
    obs_init.add_argument("--out", default="observatory.config.json",
        help="Where to write the starter config.")

    # --- file-risk ---
    file_risk_parser = subparsers.add_parser(
        "file-risk",
        help="Print per-symbol risk JSON for a file (used by VS Code risk-density gutter)",
    )
    file_risk_parser.add_argument("file",
        help="Path to the file within an indexed repo.")
    file_risk_parser.add_argument("--repo", default=None,
        help="Repo identifier (auto-detected from file path if omitted).")
    file_risk_parser.add_argument("--storage-path", default=None,
        help="Override index storage location.")

    # --- refresh (#395) ---
    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Re-parse an indexed repo in bounded, resumable slices. For fleets "
             "where a full re-index is a scheduled maintenance event.",
    )
    refresh_parser.add_argument("path", nargs="?", default=".",
        help="Path to the indexed folder. Defaults to '.' (cwd).")
    refresh_parser.add_argument("--max-seconds", type=float, default=None,
        help="Wall-clock budget for THIS run (default 300). The run stops at the "
             "budget and persists its place; run again to continue.")
    refresh_parser.add_argument("--max-files", type=int, default=None,
        help="File budget for THIS run (default 250).")
    refresh_parser.add_argument("--pause-ms", type=int, default=0,
        help="Sleep between batches, in ms. This is the knob that lowers the DUTY "
             "CYCLE; the budgets bound when a run ends, not what it costs while "
             "it runs.")
    refresh_parser.add_argument("--batch-size", type=int, default=25,
        help="Files per index_folder call (default 25).")
    refresh_parser.add_argument("--ai-summaries", action="store_true",
        help="Generate AI summaries during refresh. OFF by default: a scheduled "
             "background job must not bill a paid summarizer without being asked.")
    refresh_parser.add_argument("--status", action="store_true",
        help="Report campaign progress and exit without doing any work.")
    refresh_parser.add_argument("--reset", action="store_true",
        help="Discard any in-progress campaign and re-enumerate the corpus.")
    refresh_parser.add_argument("--json", action="store_true",
        help="Emit JSON instead of human-readable text.")
    refresh_parser.add_argument("--storage-path", default=None,
        help="Override index storage location.")

    # --- health ---
    health_parser = subparsers.add_parser(
        "health",
        help="Print get_repo_health JSON to stdout (includes six-axis radar). For CI / scripting.",
    )
    health_parser.add_argument("repo", nargs="?", default=".",
        help="Repo identifier (path, owner/name, or bare display name). Defaults to '.' (cwd).")
    health_parser.add_argument("--days", type=int, default=90,
        help="Churn look-back window in days (default 90).")
    health_parser.add_argument("--radar-only", action="store_true",
        help="Emit only the `radar` sub-field instead of the full health response.")
    health_parser.add_argument("--storage-path", default=None,
        help="Override index storage location.")

    # --- delivery ---
    delivery_parser = subparsers.add_parser(
        "delivery",
        help="Print durable-change delivery metrics (and optional cost-per-outcome) for a window.",
    )
    delivery_parser.add_argument("repo", nargs="?", default=".",
        help="Repo identifier (path, owner/name, or bare display name). Defaults to '.' (cwd).")
    delivery_parser.add_argument("--window-days", type=int, default=30,
        help="Look-back window in days (default 30).")
    delivery_parser.add_argument("--rework-horizon-days", type=int, default=14,
        help="Days within which a re-touch counts as churn-back (default 14).")
    delivery_parser.add_argument("--cost", type=float, default=None,
        help="AI spend (dollars) over the same window; prints cost-per-durable-change.")
    delivery_parser.add_argument("--json", action="store_true",
        help="Emit the structured payload as JSON.")
    delivery_parser.add_argument("--storage-path", default=None,
        help="Override index storage location.")

    # --- parity ---
    parity_parser = subparsers.add_parser(
        "parity",
        help="Map migration parity between two symbol trees (ported / diverged / unported) + port plan.",
    )
    parity_parser.add_argument("source",
        help="Source repo id (ported FROM); a path, owner/name, or bare display name.")
    parity_parser.add_argument("target",
        help="Target repo id (ported TO); may equal source when comparing two subpaths.")
    parity_parser.add_argument("--source-path", default=None,
        help="Optional subtree within the source repo (file-path prefix).")
    parity_parser.add_argument("--target-path", default=None,
        help="Optional subtree within the target repo (file-path prefix).")
    parity_parser.add_argument("--match-threshold", type=float, default=0.75,
        help="Similarity floor (0-1) for rename matching (default 0.75).")
    parity_parser.add_argument("--divergence", default="signature",
        choices=["signature", "signature+body", "name_only"],
        help="Divergence policy (default 'signature').")
    parity_parser.add_argument("--no-rename", action="store_true",
        help="Disable rename matching (exact-name only).")
    parity_parser.add_argument("--no-port-plan", action="store_true",
        help="Skip the dependency-ordered port plan.")
    parity_parser.add_argument("--json", action="store_true",
        help="Emit the structured payload as JSON.")
    parity_parser.add_argument("--storage-path", default=None,
        help="Override index storage location.")

    # --- digest ---
    digest_parser = subparsers.add_parser(
        "digest",
        help="Agent stand-up briefing — since-last-session delta + risk surface + dead-code candidates",
    )
    digest_parser.add_argument("repo", nargs="?", default=".",
        help="Repo identifier (path, owner/name, or bare display name). Defaults to '.' (cwd).")
    digest_parser.add_argument("--since-sha", default=None,
        help="Override the last-seen SHA (for re-running a delta).")
    digest_parser.add_argument("--max-changed-files", type=int, default=5,
        help="Cap on changed-files list (default 5).")
    digest_parser.add_argument("--max-hotspots", type=int, default=3,
        help="Cap on hotspot list (default 3).")
    digest_parser.add_argument("--max-dead-code", type=int, default=3,
        help="Cap on dead-code candidates (default 3).")
    digest_parser.add_argument("--json", action="store_true",
        help="Emit the structured payload as JSON instead of markdown.")
    digest_parser.add_argument("--storage-path", default=None,
        help="Override index storage location.")

    # --- receipt ---
    receipt_parser = subparsers.add_parser(
        "receipt",
        help="Token-economy ledger: parse Claude transcripts, show modeled tokens-saved + dollar value",
    )
    receipt_parser.add_argument("--days", type=int, default=30,
        help="Rolling window size in days back from now (default 30; 0 = all-time). "
             "Ignored when --since/--until is given.")
    receipt_parser.add_argument("--since", default=None, metavar="DATE",
        help="Window start, inclusive (YYYY-MM-DD = local midnight, or an ISO datetime).")
    receipt_parser.add_argument("--until", default=None, metavar="DATE",
        help="Window end, exclusive. Pair with --since for calendar windows.")
    receipt_parser.add_argument("--by-day", action="store_true",
        help="Include a per-calendar-day series in the JSON export.")
    receipt_parser.add_argument("--model", choices=_receipt_model_choices, default="opus",
        help="Model rate to apply for the dollar conversion (default opus).")
    receipt_parser.add_argument("--export", metavar="FILE.csv|FILE.json", default=None,
        help="Write raw per-tool data to a file instead of the human report.")
    receipt_parser.add_argument("--explain", action="store_true",
        help="Print the per-tool savings multiplier table + methodology, then exit.")
    receipt_parser.add_argument("--rates", action="store_true",
        help="Print the model input-price table as JSON, then exit (scans nothing).")
    receipt_parser.add_argument("--projects-root", action="append", default=None,
        metavar="DIR",
        help="Claude Code projects directory to scan. Repeatable — pass it once per "
             "profile. Overrides discovery: with no --projects-root, the default root, "
             "CLAUDE_CONFIG_DIR, and roots seen in earlier sessions are all scanned.")
    receipt_parser.add_argument("--roots", action="store_true",
        help="Print the transcript roots that would be scanned, then exit.")

    # --- reflect ---
    reflect_parser = subparsers.add_parser(
        "reflect",
        help="Surface retrieval regret from the ranking ledger as suggested config corrections",
    )
    reflect_parser.add_argument("repo", nargs="?", default=".",
        help="Repo identifier (path, owner/name, or bare display name). Defaults to '.' (cwd).")
    reflect_parser.add_argument("--project-path", default=None,
        help="Directory holding the config files to target. Defaults to cwd.")
    reflect_parser.add_argument("--window-days", type=int, default=30,
        help="Rolling ledger window to mine (default 30).")
    reflect_parser.add_argument("--all", dest="all_time", action="store_true",
        help="Analyze the full ledger, ignoring the window.")
    reflect_parser.add_argument("--apply-weights", action="store_true",
        help="Persist the ranking-weight proposal to tuning.jsonc (sidecar, not user source).")
    reflect_parser.add_argument("--json", action="store_true",
        help="Emit the structured payload as JSON instead of the human report.")
    reflect_parser.add_argument("--storage-path", default=None,
        help="Override index storage location.")

    # --- whatsnew ---
    whatsnew_parser = subparsers.add_parser(
        "whatsnew",
        help="Refresh README recency block + write whatsnew.json from CHANGELOG.md (release flow)",
    )
    whatsnew_parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root (default: cwd)",
    )
    whatsnew_parser.add_argument(
        "--max-entries",
        type=int,
        default=3,
        help="Number of recent releases to include (default 3)",
    )

    # --- hook-precompact ---
    subparsers.add_parser(
        "hook-precompact",
        help="PreCompact hook: generate session snapshot before context compaction (reads stdin)",
    )

    # --- hook-taskcomplete ---
    subparsers.add_parser(
        "hook-taskcomplete",
        help="TaskCompleted hook: post-task diagnostics — dead code, untested symbols, dangling refs (reads stdin)",
    )

    # --- hook-subagent-start ---
    subparsers.add_parser(
        "hook-subagent-start",
        help="SubagentStart hook: inject condensed repo orientation for spawned agents (reads stdin)",
    )

    # --- hook-sessionstart ---
    subparsers.add_parser(
        "hook-sessionstart",
        help="SessionStart hook: re-inject the session snapshot after compaction/resume (reads stdin)",
    )

    # --- watch-claude ---
    wc_parser = subparsers.add_parser(
        "watch-claude",
        help="Auto-discover and watch Claude Code worktrees",
    )
    wc_parser.add_argument(
        "--repos",
        nargs="+",
        help="One or more git repository paths to poll for worktrees via `git worktree list`",
    )
    wc_parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Poll interval in seconds (default: from config, also via JCODEMUNCH_CLAUDE_POLL_INTERVAL)",
    )
    wc_parser.add_argument(
        "--debounce",
        type=int,
        default=None,
        metavar="MS",
        help="Debounce interval in ms for file watching (default: from config, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
    )
    wc_parser.add_argument(
        "--no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries during re-indexing",
    )
    wc_parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Include symlinked files in indexing",
    )
    wc_parser.add_argument(
        "--extra-ignore",
        nargs="*",
        help="Additional gitignore-style patterns to exclude",
    )
    _add_common_args(wc_parser)

    # --- watch-all ---
    wa_parser = subparsers.add_parser(
        "watch-all",
        help="Auto-discover every locally-indexed repo and auto-reindex on change",
    )
    wa_parser.add_argument(
        "--debounce", type=int, default=None, metavar="MS",
        help="Debounce interval in ms (default: from config)",
    )
    wa_parser.add_argument(
        "--rediscover-interval", type=float, default=None, metavar="SECONDS",
        help="Re-scan the index registry for new/removed repos every N seconds (default: 30)",
    )
    wa_parser.add_argument("--no-ai-summaries", action="store_true",
        help="Disable AI-generated summaries during re-indexing")
    wa_parser.add_argument("--follow-symlinks", action="store_true",
        help="Include symlinked files in indexing")
    wa_parser.add_argument("--extra-ignore", nargs="*",
        help="Additional gitignore-style patterns to exclude")
    _add_common_args(wa_parser)

    # --- watch-install / watch-uninstall / watch-status ---
    _add_common_args(subparsers.add_parser(
        "watch-install",
        help="Install watch-all as a login service (systemd/launchd/Task Scheduler)",
    ))
    _add_common_args(subparsers.add_parser(
        "watch-uninstall",
        help="Remove the installed watch-all login service",
    ))
    _add_common_args(subparsers.add_parser(
        "watch-status",
        help="Print watch-all service state + per-repo reindex status",
    ))

    # --- keyring (P1.3) ---
    keyring_parser = subparsers.add_parser(
        "keyring",
        help="Manage credentials in the system keyring (macOS Keychain / Windows Credential Manager / freedesktop Secret Service). Requires the [keyring] extra.",
    )
    keyring_sub = keyring_parser.add_subparsers(dest="keyring_action")
    keyring_set_p = keyring_sub.add_parser("set", help="Store a credential. Prompts for the value via getpass.")
    keyring_set_p.add_argument("name", help="Env-var name the credential maps to (e.g. ANTHROPIC_API_KEY)")
    keyring_set_p.add_argument("--from-env", action="store_true", help="Read the value from the current env var instead of prompting.")
    keyring_get_p = keyring_sub.add_parser("get", help="Print a stored credential to stdout (sensitive — pipe with care).")
    keyring_get_p.add_argument("name", help="Env-var name the credential maps to")
    keyring_del_p = keyring_sub.add_parser("delete", help="Remove a stored credential.")
    keyring_del_p.add_argument("name", help="Env-var name the credential maps to")
    keyring_sub.add_parser("list", help="List the credential env-var names jcodemunch recognises for keyring lookup.")

    # --- download-model ---
    dm_parser = subparsers.add_parser(
        "download-model",
        help="Download the bundled ONNX embedding model (all-MiniLM-L6-v2) for zero-config semantic search",
    )
    dm_parser.add_argument(
        "--target-dir",
        default=None,
        metavar="PATH",
        help="Custom directory to store the model (default: ~/.code-index/models/all-MiniLM-L6-v2/)",
    )

    # --- install-pack ---
    ip_parser = subparsers.add_parser(
        "install-pack",
        help="Download and install a Starter Pack pre-built index",
    )
    ip_parser.add_argument(
        "pack_id",
        nargs="?",
        default=None,
        help="Pack identifier to install (e.g. nodejs, fastapi)",
    )
    ip_parser.add_argument(
        "--license",
        default=None,
        dest="license_key",
        metavar="KEY",
        help="jCodeMunch license key (required for premium packs)",
    )
    ip_parser.add_argument(
        "--list",
        action="store_true",
        dest="list_packs",
        help="List all available starter packs",
    )
    ip_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download and overwrite an already-installed pack",
    )

    # Backwards compat: if first non-flag arg isn't a known subcommand,
    # prepend "serve" so legacy invocations like `jcodemunch-mcp --transport sse` still work.
    # But let --help and -V be handled by the top-level parser first.
    raw_argv = argv if argv is not None else sys.argv[1:]
    top_level_flags = {"-h", "--help", "-V", "--version"}
    if any(arg in top_level_flags for arg in raw_argv):
        args = parser.parse_args(raw_argv)
    else:
        known_commands = {"serve", "watch", "hook-event", "hook-pretooluse", "hook-posttooluse", "hook-copilot-posttooluse", "hook-precompact", "hook-taskcomplete", "hook-subagent-start", "hook-sessionstart", "watch-claude", "watch-all", "watch-install", "watch-uninstall", "watch-status", "config", "list-repos", "delete-index", "org-report", "org-rollup", "license", "index", "index-file", "import-trace", "import-scip", "claude-md", "init", "install", "install-status", "uninstall", "install-pack", "download-model", "upgrade", "whatsnew", "receipt", "digest", "reflect", "delivery", "parity", "refresh", "health", "file-risk", "observatory", "keyring", "surface"}
        # MCP-tool-name typos: route to the right CLI verb with a friendly hint.
        # `index_repo` and `index_folder` are MCP tools, not CLI subcommands.
        _CLI_ALIASES = {
            "index_repo": "index",
            "index-repo": "index",
            "index_folder": "index",
            "index-folder": "index",
            "index_file": "index-file",
        }
        first_pos = next((a for a in raw_argv if not a.startswith("-")), None)
        if first_pos in _CLI_ALIASES:
            target = _CLI_ALIASES[first_pos]
            print(
                f"jcodemunch-mcp: error: unknown subcommand `{first_pos}`. Did you mean:\n"
                f"    jcodemunch-mcp {target} <owner/repo>\n"
                f"    jcodemunch-mcp {target} <github-url>\n"
                f"    jcodemunch-mcp {target} <local-path>",
                file=sys.stderr,
            )
            sys.exit(2)
        has_subcommand = any(arg in known_commands for arg in raw_argv if not arg.startswith("-"))
        if not has_subcommand:
            raw_argv = ["serve"] + list(raw_argv)
        args = parser.parse_args(raw_argv)

    # P1.3 keyring resolution: rewrite any `keyring:NAME` env-var values to
    # the actual secret stored under that name in the system keyring. Runs
    # before any subcommand dispatch so all downstream code that calls
    # os.environ.get("ANTHROPIC_API_KEY") etc. sees the resolved value.
    # Skipped for the `keyring` subcommand itself (no point resolving env
    # vars when the user is about to manage them).
    if getattr(args, "command", None) != "keyring":
        try:
            from . import credentials as _creds
            _creds.resolve_credentials_in_env()
        except Exception:
            logger.debug("credential env resolution skipped", exc_info=True)

    if args.command == "config":
        action = getattr(args, "action", None)
        if action in ("set", "unset"):
            from . import config as _cfg
            as_json = getattr(args, "json", False)
            key = getattr(args, "key", None)
            if not key:
                _emit = (lambda d: print(json.dumps(d, indent=2))) if as_json \
                    else (lambda d: print(d.get("error", ""), file=sys.stderr))
                _emit({"success": False, "error": f"config {action} requires a key"})
                sys.exit(2)
            try:
                if action == "set":
                    if getattr(args, "value", None) is None:
                        raise ValueError("config set requires a value")
                    written = _cfg.set_config_value(key, args.value)
                    result = {"success": True, "key": key, "value": written,
                              "message": f"set {key} = {json.dumps(written)}"}
                else:
                    changed = _cfg.unset_config_value(key)
                    result = {"success": True, "key": key, "changed": changed,
                              "message": (f"cleared {key} (default applies)" if changed
                                          else f"{key} was not set")}
            except ValueError as e:
                if as_json:
                    print(json.dumps({"success": False, "key": key, "error": str(e)}, indent=2))
                else:
                    print(f"error: {e}", file=sys.stderr)
                sys.exit(1)
            if as_json:
                print(json.dumps(result, indent=2))
            else:
                print(result["message"])
            return
        if getattr(args, "json", False):
            from . import config as _cfg
            print(json.dumps(_cfg.config_report(repo=str(Path.cwd())), indent=2))
            return
        _run_config(
            check=getattr(args, "check", False),
            init=getattr(args, "init", False),
            upgrade=getattr(args, "upgrade", False),
        )
        return

    if args.command == "org-report":
        from .org.report import run_org_report
        res = run_org_report(
            model=getattr(args, "model", "opus"),
            org_id=getattr(args, "org", None),
            seat_id=getattr(args, "seat", None),
            endpoint=getattr(args, "endpoint", None),
        )
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
        elif res.get("error"):
            print(f"error: {res['error']}", file=sys.stderr)
        elif res.get("reported") is False:
            print(f"report to {res.get('endpoint')} failed: {res.get('error')}", file=sys.stderr)
        else:
            via = "posted to " + res["endpoint"] if res.get("transport") == "http" else "recorded locally"
            print(f"seat {res['seat_id']} in org {res['org_id']} ({via}): "
                  f"{res['tokens_saved']} tokens, ${res['usd']:.2f}, {res['calls']} calls")
        return

    if args.command == "org-rollup":
        from .org.store import org_rollup
        from .org.license import check_gate
        org = getattr(args, "org", None) or os.environ.get("JCODEMUNCH_ORG_ID", "")
        as_json = getattr(args, "json", False)
        if not org:
            print("error: provide --org or set JCODEMUNCH_ORG_ID", file=sys.stderr)
            return

        # org-rollup is the team-SKU (paid) feature — gate it. Individual tools
        # are untouched; seat reporting stays free so trial data accrues.
        # Load config first so a persisted `license_key` is visible — this handler
        # returns before the shared load_config() later in main(), same trap the
        # license handler hit in #364.
        config_module.load_config()
        gate = check_gate()
        if not gate["allowed"]:
            if as_json:
                print(json.dumps({
                    "error": gate["reason"],
                    "license_mode": gate["mode"],
                    "get_license": gate["get_license"],
                }, indent=2))
            else:
                print(f"org-rollup is unavailable: {gate['reason']}", file=sys.stderr)
                print(f"  Get a license: {gate['get_license']}", file=sys.stderr)
            return
        if gate["mode"] == "grace":
            print(f"note: {gate['reason']}  ({gate['get_license']})", file=sys.stderr)

        data = org_rollup(org)
        data["_license"] = {
            "mode": gate["mode"],
            "tier": gate.get("tier"),
            "grace_days_left": gate.get("grace_days_left"),
            "key": gate.get("key_masked"),
        }
        if as_json:
            print(json.dumps(data, indent=2))
        else:
            t = data["totals"]
            print(f"org {org}: {t['seat_count']} seats · {t['tokens_saved']} tokens · ${t['usd']:.2f} · {t['calls']} calls")
            for s in data["seats"]:
                print(f"  {s['seat_id']:<24} {s['tokens_saved']:>9} tok  ${s['usd']:>8.2f}  {s['calls']:>5} calls")
        return

    if args.command == "license":
        from .org.license import check_gate
        # Load config.jsonc so a persisted `license_key` is visible. This handler
        # returns before the shared load_config() call further down in main(), so
        # without this an installed license key would be ignored and only the
        # JCODEMUNCH_LICENSE_KEY env var / --key would work (issue #364).
        config_module.load_config()
        key = getattr(args, "key", None)
        if key:
            os.environ["JCODEMUNCH_LICENSE_KEY"] = key  # validate this key for this run
        gate = check_gate()
        if getattr(args, "json", False):
            print(json.dumps(gate, indent=2))
        else:
            from .org.license import format_license_status
            for line in format_license_status(gate, key_provided=bool(key)):
                print(line)
        return

    if args.command == "list-repos":
        from .tools.list_repos import repos_report
        report = repos_report(storage_path=os.environ.get("CODE_INDEX_PATH"))
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2))
        elif not report:
            print("No indexed repositories.")
        else:
            for r in report:
                langs = ", ".join(f"{k}:{v}" for k, v in sorted(r["languages"].items()))
                print(
                    f"{r['display_name']:<28} {r['symbol_count']:>6} sym  "
                    f"{r['file_count']:>5} files  {r['freshness']:<16} "
                    f"watcher={r['watcher_state']}"
                    + (f"  [{langs}]" if langs else "")
                )
        return

    if args.command == "surface":
        # Sits above the shared load_config() call in main(), so load config
        # here — tool_surface / tool_profile / compact_schemas / disabled_tools
        # all shape the receipt (the v1.108.121 license-CLI lesson).
        config_module.load_config()
        stats = _tool_surface_stats()
        if getattr(args, "json", False):
            print(json.dumps(stats, indent=2))
        else:
            print(f"Surface: {stats['surface']}  Profile: {stats['profile']}")
            print(
                f"Visible tools: {stats['visible_tools']} of {stats['catalog_tools']} "
                f"({stats['schema_tokens_visible']:,} of {stats['schema_tokens_catalog']:,} schema tokens)"
            )
            print(f"Schema tokens avoided: {stats['schema_tokens_avoided']:,} (estimator: {stats['estimator']})")
            print("Heaviest tool schemas:")
            for name, weight in stats["heaviest_tools"].items():
                print(f"  {name:<28} {weight:>5}")
        return

    if args.command == "delete-index":
        # CLI alias for the invalidate_cache MCP tool: resolves the repo,
        # deletes its index + cached data, and clears in-process caches.
        # Exit non-zero on failure so callers (e.g. the jMunch Console) can
        # detect it via the return code, not just the JSON body.
        from .tools.invalidate_cache import invalidate_cache
        result = invalidate_cache(args.repo, storage_path=os.environ.get("CODE_INDEX_PATH"))
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2))
        elif result.get("success"):
            print(result.get("message", f"Deleted index for {args.repo}"))
        else:
            print(result.get("error", f"No index found for {args.repo}"), file=sys.stderr)
        sys.exit(0 if result.get("success") else 1)

    if args.command == "claude-md":
        _run_claude_md(
            generate=getattr(args, "generate", False),
            fmt=getattr(args, "fmt", "full"),
        )
        return

    if args.command == "init":
        from .cli.init import run_init
        sys.exit(run_init(
            clients=args.client,
            claude_md=args.claude_md,
            hooks=args.hooks,
            # `--hooks` parses with default=None, so True here means a human
            # typed it. That is what survives `--minimal` (#397).
            hooks_explicit=(args.hooks is True),
            copilot_hooks=getattr(args, "copilot_hooks", False),
            index=args.index,
            audit=args.audit,
            dry_run=args.dry_run,
            demo=args.demo,
            yes=args.yes,
            no_backup=args.no_backup,
            share_savings=getattr(args, "share_savings", None),
            minimal=getattr(args, "minimal", False),
            strict=getattr(args, "strict", False),
        ))

    if args.command == "install":
        from .cli.init import (
            list_targets as _list_targets,
            install_status as _install_status,
            print_status as _print_status,
            run_init,
            _AGENT_ALIASES,
        )
        if getattr(args, "list_targets", False):
            _list_targets()
            sys.exit(0)
        if getattr(args, "status", False):
            _print_status(_install_status(), as_json=getattr(args, "as_json", False))
            sys.exit(0)
        target = args.target
        if not target:
            print(
                "install: please pass a target (e.g. `install claude-code`),\n"
                "        or use --list / --status for info-only output.",
                file=sys.stderr,
            )
            sys.exit(2)
        if target.lower() not in _AGENT_ALIASES:
            print(
                f"install: unknown target '{target}'. Valid: "
                f"{', '.join(sorted(_AGENT_ALIASES))}",
                file=sys.stderr,
            )
            sys.exit(2)
        client_arg = None if target.lower() == "all" else [target.lower()]
        sys.exit(run_init(
            clients=client_arg or ["auto"],
            claude_md="global",
            hooks=True,
            copilot_hooks=False,
            index=False,
            audit=False,
            dry_run=getattr(args, "dry_run", False),
            demo=False,
            yes=True,
            no_backup=getattr(args, "no_backup", False),
            skills=getattr(args, "skills", False),
            skills_scope=getattr(args, "skills_scope", "global"),
            share_savings=getattr(args, "share_savings", None),
            minimal=getattr(args, "minimal", False),
        ))

    if args.command == "install-status":
        from .cli.init import install_status as _install_status, print_status as _print_status
        _print_status(_install_status(), as_json=getattr(args, "as_json", False))
        sys.exit(0)

    if args.command == "uninstall":
        from .cli.init import run_uninstall
        sys.exit(run_uninstall(
            target=args.target,
            claude_md=not getattr(args, "keep_claude_md", False),
            cursor_rules=not getattr(args, "keep_cursor_rules", False),
            windsurf_rules=not getattr(args, "keep_windsurf_rules", False),
            agents_md=not getattr(args, "keep_agents_md", False),
            hooks=not getattr(args, "keep_hooks", False),
            copilot_hooks=not getattr(args, "keep_copilot_hooks", False),
            skills=not getattr(args, "keep_skills", False),
            dry_run=getattr(args, "dry_run", False),
            no_backup=getattr(args, "no_backup", False),
            yes=getattr(args, "yes", False),
        ))

    if args.command == "keyring":
        from . import credentials as _creds
        import getpass as _getpass

        action = getattr(args, "keyring_action", None)
        if action is None:
            print("keyring: please pass a subcommand (set/get/delete/list)", file=sys.stderr)
            sys.exit(2)
        try:
            if action == "set":
                name = args.name
                if getattr(args, "from_env", False):
                    value = os.environ.get(name, "")
                    if not value:
                        print(f"keyring set: env var {name} is empty or unset", file=sys.stderr)
                        sys.exit(2)
                else:
                    value = _getpass.getpass(f"Enter value for {name}: ")
                if not value:
                    print("keyring set: empty value, aborted", file=sys.stderr)
                    sys.exit(2)
                _creds.keyring_set(name, value)
                print(f"Stored {name} in system keyring under service '{_creds.SERVICE_NAME}'.")
                print(f"To use it, set: {name}=keyring:{name}  (in your MCP env block)")
                sys.exit(0)
            elif action == "get":
                value = _creds.keyring_get(args.name)
                if value is None:
                    print(f"No keyring entry for {args.name} under service '{_creds.SERVICE_NAME}'.")
                    sys.exit(1)
                print(value)
                sys.exit(0)
            elif action == "delete":
                removed = _creds.keyring_delete(args.name)
                if removed:
                    print(f"Removed {args.name} from system keyring.")
                else:
                    print(f"No keyring entry for {args.name} to remove (or removal failed).")
                sys.exit(0 if removed else 1)
            elif action == "list":
                print("Recognised credential env-var names (set any to keyring:<name> to enable keyring resolution):")
                for var in _creds.list_recognised_env_vars():
                    populated = _creds.keyring_get(var)
                    state = "stored" if populated else "not set"
                    print(f"  {var:<30}  {state}")
                sys.exit(0)
            else:
                print(f"keyring: unknown subcommand '{action}'", file=sys.stderr)
                sys.exit(2)
        except ImportError as e:
            print(f"keyring: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"keyring {action} failed: {e}", file=sys.stderr)
            sys.exit(1)

    if args.command == "download-model":
        from .embeddings.local_encoder import download_model as _download_model
        from pathlib import Path as _Path
        try:
            target = _Path(args.target_dir) if args.target_dir else None
            _download_model(target)
            sys.exit(0)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)  # noqa: T201
            sys.exit(1)

    if args.command == "install-pack":
        # run_install_pack resolves --license → env → config `license_key` so a
        # premium-pack entitlement set in config.jsonc is honored (not just --license).
        from .cli.install_pack import run_install_pack
        sys.exit(run_install_pack(
            pack_id=args.pack_id,
            license_key=args.license_key,
            list_packs=args.list_packs,
            force=args.force,
        ))

    if args.command == "hook-pretooluse":
        from .cli.hooks import run_pretooluse
        sys.exit(run_pretooluse())

    if args.command == "hook-posttooluse":
        from .cli.hooks import run_posttooluse
        sys.exit(run_posttooluse())

    if args.command == "hook-copilot-posttooluse":
        from .cli.hooks import run_copilot_posttooluse
        sys.exit(run_copilot_posttooluse())

    if args.command == "upgrade":
        from .cli.upgrade import run_upgrade
        sys.exit(run_upgrade(no_pip=args.no_pip, yes=args.yes))

    if args.command == "whatsnew":
        from .cli.whatsnew import main as whatsnew_main
        sys.exit(whatsnew_main([
            "--repo-root", args.repo_root,
            "--max-entries", str(args.max_entries),
        ]))

    if args.command == "observatory":
        from .cli.observatory import main as observatory_main
        argv = []
        if args.obs_action == "build":
            argv = ["build", "--config", args.config]
            if args.output_dir:
                argv += ["--output-dir", args.output_dir]
            if args.workdir:
                argv += ["--workdir", args.workdir]
        elif args.obs_action == "init":
            argv = ["init", "--out", args.out]
        else:
            argv = []
        sys.exit(observatory_main(argv))

    if args.command == "file-risk":
        from .cli.file_risk import main as file_risk_main
        argv = [args.file]
        if args.repo:
            argv += ["--repo", args.repo]
        if args.storage_path:
            argv += ["--storage-path", args.storage_path]
        sys.exit(file_risk_main(argv))

    if args.command == "refresh":
        from .tools.refresh import run as _refresh_run, status as _refresh_status
        # Dispatched above the shared load_config() in main(), so load config
        # here. See #426: get() now loads lazily, but an explicit call keeps
        # this handler's behaviour independent of that.
        config_module.load_config()
        if args.status:
            _out = _refresh_status(args.path, storage_path=args.storage_path)
        else:
            _out = _refresh_run(
                args.path,
                max_files=args.max_files,
                max_seconds=args.max_seconds,
                pause_ms=args.pause_ms,
                batch_size=args.batch_size,
                reset=args.reset,
                storage_path=args.storage_path,
                use_ai_summaries=args.ai_summaries,
            )
        if args.json:
            # Module-level `json` (line 9), NOT the `_json` alias other branches
            # use. Several handlers further down do `import json as _json`, which
            # makes `_json` a LOCAL for the whole of main() -- so referencing it
            # from a branch that runs BEFORE those imports raises
            # UnboundLocalError, not NameError. Shipped broken in v1.108.259 and
            # caught by ruff F821, which nobody read for four releases.
            print(json.dumps(_out, indent=2))
        else:
            print(_format_refresh(_out))
        sys.exit(0 if _out.get("success") else 1)

    if args.command == "health":
        from .cli.health import main as health_main
        argv = [args.repo, "--days", str(args.days)]
        if args.radar_only:
            argv += ["--radar-only"]
        if args.storage_path:
            argv += ["--storage-path", args.storage_path]
        sys.exit(health_main(argv))

    if args.command == "delivery":
        from .cli.delivery import main as delivery_main
        argv = [args.repo, "--window-days", str(args.window_days),
                "--rework-horizon-days", str(args.rework_horizon_days)]
        if args.cost is not None:
            argv += ["--cost", str(args.cost)]
        if args.json:
            argv += ["--json"]
        if args.storage_path:
            argv += ["--storage-path", args.storage_path]
        sys.exit(delivery_main(argv))

    if args.command == "parity":
        from .cli.parity import main as parity_main
        argv = [args.source, args.target,
                "--match-threshold", str(args.match_threshold),
                "--divergence", args.divergence]
        if args.source_path:
            argv += ["--source-path", args.source_path]
        if args.target_path:
            argv += ["--target-path", args.target_path]
        if args.no_rename:
            argv += ["--no-rename"]
        if args.no_port_plan:
            argv += ["--no-port-plan"]
        if args.json:
            argv += ["--json"]
        if args.storage_path:
            argv += ["--storage-path", args.storage_path]
        sys.exit(parity_main(argv))

    if args.command == "digest":
        from .cli.digest import main as digest_main
        argv = [args.repo]
        if args.since_sha:
            argv += ["--since-sha", args.since_sha]
        argv += ["--max-changed-files", str(args.max_changed_files)]
        argv += ["--max-hotspots", str(args.max_hotspots)]
        argv += ["--max-dead-code", str(args.max_dead_code)]
        if args.json:
            argv += ["--json"]
        if args.storage_path:
            argv += ["--storage-path", args.storage_path]
        sys.exit(digest_main(argv))

    if args.command == "reflect":
        from .cli.reflect import main as reflect_main
        argv = [args.repo, "--window-days", str(args.window_days)]
        if args.project_path:
            argv += ["--project-path", args.project_path]
        if args.all_time:
            argv += ["--all"]
        if args.apply_weights:
            argv += ["--apply-weights"]
        if args.json:
            argv += ["--json"]
        if args.storage_path:
            argv += ["--storage-path", args.storage_path]
        sys.exit(reflect_main(argv))

    if args.command == "receipt":
        from .cli.receipt import main as receipt_main
        argv = ["--days", str(args.days), "--model", args.model]
        if args.since:
            argv += ["--since", args.since]
        if args.until:
            argv += ["--until", args.until]
        if args.by_day:
            argv += ["--by-day"]
        if args.export:
            argv += ["--export", args.export]
        if args.explain:
            argv += ["--explain"]
        if args.rates:
            argv += ["--rates"]
        for _root in (args.projects_root or []):
            argv += ["--projects-root", _root]
        if args.roots:
            argv += ["--roots"]
        sys.exit(receipt_main(argv))

    if args.command == "hook-precompact":
        from .cli.hooks import run_precompact
        sys.exit(run_precompact())

    if args.command == "hook-taskcomplete":
        from .cli.hooks import run_taskcomplete
        sys.exit(run_taskcomplete())

    if args.command == "hook-subagent-start":
        from .cli.hooks import run_subagentstart
        sys.exit(run_subagentstart())

    if args.command == "hook-sessionstart":
        from .cli.hooks import run_sessionstart
        sys.exit(run_sessionstart())

    # Apply config defaults for watcher keys: CLI args > config > env vars.
    # config.load_config() is called inside each subcommand handler, but we need
    # the values here to fill in None defaults from argparse.
    # load_config() is idempotent so calling it early is safe.
    config_module.load_config()

    # --watcher-debounce (serve subcommand) / --debounce (watch, watch-claude)
    # Only set if the attr exists on args and is None (not explicitly provided on CLI)
    _debounce = config_module.get("watch_debounce_ms", 2000)
    if getattr(args, "watcher_debounce", None) is None:
        args.watcher_debounce = _debounce
    if getattr(args, "debounce", None) is None:
        args.debounce = _debounce

    # --poll-interval (watch-claude subcommand)
    if getattr(args, "poll_interval", None) is None:
        args.poll_interval = config_module.get("claude_poll_interval", 5.0)

    # --freshness-mode is only relevant for serve subcommand; handled there

    _setup_logging(args)

    if args.command == "watch":
        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()
        if args.once:
            from .watcher import sync_folders

            asyncio.run(
                sync_folders(
                    paths=args.paths,
                    use_ai_summaries=use_ai,
                    storage_path=os.environ.get("CODE_INDEX_PATH"),
                    extra_ignore_patterns=args.extra_ignore,
                    follow_symlinks=args.follow_symlinks,
                )
            )
        else:
            from .watcher import watch_folders

            asyncio.run(
                watch_folders(
                    paths=args.paths,
                    debounce_ms=args.debounce,
                    use_ai_summaries=use_ai,
                    storage_path=os.environ.get("CODE_INDEX_PATH"),
                    extra_ignore_patterns=args.extra_ignore,
                    follow_symlinks=args.follow_symlinks,
                    idle_timeout_minutes=args.idle_timeout,
                )
            )
    elif args.command == "hook-event":
        from .hook_event import handle_hook_event

        handle_hook_event(event_type=args.event_type)
    elif args.command == "watch-all":
        from .watch_all import watch_all, DEFAULT_REDISCOVER_INTERVAL_S
        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()
        asyncio.run(
            watch_all(
                debounce_ms=args.debounce or int(os.environ.get("JCODEMUNCH_WATCH_DEBOUNCE_MS", "200")),
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
                extra_ignore_patterns=args.extra_ignore,
                follow_symlinks=args.follow_symlinks,
                rediscover_interval_s=args.rediscover_interval or DEFAULT_REDISCOVER_INTERVAL_S,
            )
        )
    elif args.command == "watch-install":
        import json as _json
        from .service_installer import install_service, InstallerError
        try:
            print(_json.dumps(install_service(), indent=2))
        except InstallerError as exc:
            print(f"watch-install failed: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "watch-uninstall":
        import json as _json
        from .service_installer import uninstall_service, InstallerError
        try:
            print(_json.dumps(uninstall_service(), indent=2))
        except InstallerError as exc:
            print(f"watch-uninstall failed: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "watch-status":
        import json as _json
        from .tools.get_watch_status import get_watch_status
        print(_json.dumps(get_watch_status(storage_path=os.environ.get("CODE_INDEX_PATH")), indent=2))
    elif args.command == "watch-claude":
        from .watcher import watch_claude_worktrees

        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()
        asyncio.run(
            watch_claude_worktrees(
                repos=args.repos,
                poll_interval=args.poll_interval,
                debounce_ms=args.debounce,
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
                extra_ignore_patterns=args.extra_ignore,
                follow_symlinks=args.follow_symlinks,
            )
        )
    elif args.command == "index":
        import json as _json
        t = args.target
        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()

        # `--paths-from FILE | -` reads one path per line; comments (`# ...`)
        # and blank lines are stripped. Empty input is a hard error so the
        # command doesn't silently fall through to a full-tree index.
        paths_arg: Optional[list] = None
        paths_from = getattr(args, "paths_from", None)
        if paths_from:
            paths_arg, _err = _load_index_paths_from_arg(paths_from)
            if _err is not None:
                print(_json.dumps({"success": False, "error": _err}, indent=2))
                sys.exit(1)

        # Heuristic: local paths start with /, ., or a Windows drive letter.
        # Everything else (owner/repo, github.com/owner/repo, https://github.com/...,
        # git@github.com:owner/repo) routes to the GitHub indexer, which calls
        # parse_github_url for normalization.
        is_local = "/" not in t or t.startswith("/") or t.startswith(".") or (len(t) > 1 and t[1] == ":")
        if is_local:
            from .tools.index_folder import index_folder as _index_folder
            result = _index_folder(
                path=t,
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
                extra_ignore_patterns=args.extra_ignore,
                follow_symlinks=args.follow_symlinks,
                paths=paths_arg,
            )
        else:
            if paths_arg is not None:
                print(_json.dumps({
                    "success": False,
                    "error": "--paths-from is only supported for local targets, not GitHub repos.",
                }, indent=2))
                sys.exit(1)
            from .tools.index_repo import index_repo as _index_repo
            result = asyncio.run(_index_repo(
                url=t,
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
            ))
        print(_json.dumps(result, indent=2))
        if not result.get("success"):
            sys.exit(1)
    elif args.command == "index-file":
        from .tools.index_file import index_file as _index_file
        import json as _json

        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()
        result = _index_file(
            path=args.path,
            use_ai_summaries=use_ai,
            storage_path=os.environ.get("CODE_INDEX_PATH"),
        )
        print(_json.dumps(result, indent=2))
        if not result.get("success"):
            sys.exit(1)
    elif args.command == "import-trace":
        from .tools.import_runtime_signal import import_runtime_signal as _import_runtime_signal
        import json as _json

        otel_path = getattr(args, "otel_path", None)
        sql_log_path = getattr(args, "sql_log_path", None)
        stack_log_path = getattr(args, "stack_log_path", None)
        provided = [p for p in (otel_path, sql_log_path, stack_log_path) if p]
        if not provided:
            print(
                "jcodemunch-mcp: error: import-trace requires one of --otel / --sql-log / --stack-log <path>",
                file=sys.stderr,
            )
            sys.exit(2)
        if len(provided) > 1:
            print(
                "jcodemunch-mcp: error: import-trace accepts exactly one of --otel / --sql-log / --stack-log. "
                "Run the command once per source if you have multiple.",
                file=sys.stderr,
            )
            sys.exit(2)
        if otel_path:
            source = "otel"
            trace_path = otel_path
        elif sql_log_path:
            source = "sql_log"
            trace_path = sql_log_path
        else:
            source = "stack_log"
            trace_path = stack_log_path
        result = _import_runtime_signal(
            source=source,
            path=trace_path,
            repo=args.repo,
            redact_enabled=not args.no_redact,
            storage_path=os.environ.get("CODE_INDEX_PATH"),
        )
        print(_json.dumps(result, indent=2))
        if not result.get("success", True):
            sys.exit(1)
    elif args.command == "import-scip":
        from .tools.import_scip import import_scip as _import_scip
        import json as _json

        result = _import_scip(
            path=args.scip_path,
            repo=args.repo,
            storage_path=os.environ.get("CODE_INDEX_PATH"),
        )
        print(_json.dumps(result, indent=2))
        if not result.get("success", True):
            sys.exit(1)
    else:
        # serve (default)
        # Re-run load_config() after _setup_logging() so config warnings/errors
        # go to the configured log destination (the early call at startup ran before logging was set up)
        config_module.load_config()

        # Version-drift probe: warn if `pip install -U` ran but `init` did not.
        # Stale hook templates can point at older binaries / event names.
        try:
            from .cli.init import read_install_version
            from . import __version__ as _current_version
            _stamped = read_install_version()
            if _stamped and _stamped != _current_version and _current_version != "unknown":
                logger.warning(
                    "jcodemunch-mcp upgraded %s -> %s but `init` has not been "
                    "re-run. Hook templates and config may be stale; run "
                    "`jcodemunch-mcp init --hooks` to refresh (needs no pip; "
                    "`jcodemunch-mcp upgrade` also works on pip installs).",
                    _stamped,
                    _current_version,
                )
        except Exception:
            logger.debug("install-version probe failed", exc_info=True)

        # Clean up orphan indexes whose source_root no longer exists
        try:
            from .storage import IndexStore

            storage_path = os.environ.get("CODE_INDEX_PATH")
            store = IndexStore(base_path=storage_path)
            # ⚠ ORDER IS DELIBERATE: repair starter-pack indexes BEFORE the
            # orphan sweep. Packs ship with the builder's `/tmp/jcm-pack-clones`
            # path in their meta, which the sweep read as a vanished local repo
            # and deleted on every start (#419). The sweep also skips them
            # independently, so a failure here degrades to "not repaired",
            # never to "deleted".
            healed = store.heal_pack_index_paths()
            if healed:
                logger.info("Repaired %d starter-pack index(es)", healed)
            cleaned = store.cleanup_orphan_indexes()
            store.close()
            if cleaned:
                logger.info("Cleaned up %d orphan index(es)", cleaned)
        except Exception:
            logger.debug("Orphan index cleanup failed", exc_info=True)

        config_module.load_all_project_configs()
        from .reindex_state import set_freshness_mode
        # Apply config default if --freshness-mode was not explicitly provided
        if args.freshness_mode is None:
            args.freshness_mode = config_module.get("freshness_mode", "relaxed")
        set_freshness_mode(args.freshness_mode)
        # Resolve transport/host/port with CLI > env > config > default so the
        # config.jsonc keys are honored at serve time (V11). Applies to both the
        # watcher and non-watcher dispatch branches below.
        args.transport, args.host, args.port = _resolve_serve_endpoint(args)
        runtime_identity.set_transport(args.transport)
        watcher_enabled = _get_watcher_enabled(args)
        watcher_from_cli = getattr(args, "watcher", None) is not None

        if watcher_enabled:
            try:
                import watchfiles  # noqa: F401
            except ImportError:
                if watcher_from_cli:
                    print(
                        "ERROR: --watcher requires watchfiles. "
                        "Install with: pip install 'jcodemunch-mcp[watch]'",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                logger.warning(
                    "watch is enabled in config but the 'watchfiles' "
                    "package is not installed; continuing without the "
                    "file watcher. Install with: pip install "
                    "'jcodemunch-mcp[watch]'"
                )
                watcher_enabled = False

        # Presence registry (jcm#375 follow-up): record that this server process
        # exists so `get_session_stats` can report how many jcodemunch servers
        # share this index store. A client that never reaps stdio servers at
        # session end accumulates them silently (25+ were found on one box,
        # holding ~17 GB between them). We cannot reap another program's
        # children; we can stop the sprawl being invisible. Registered here,
        # above both serve branches, so every transport is covered once. Best
        # effort, and never allowed to block startup.
        try:
            from .storage.process_registry import (
                register as _register_process,
                unregister as _unregister_process,
            )
            _register_process(
                args.transport, __version__, os.environ.get("CODE_INDEX_PATH")
            )
            import atexit
            atexit.register(_unregister_process)
        except Exception:
            logger.debug("process registry: register failed", exc_info=True)

        # Transcript root registry (jcm#421): Claude Code writes this session's
        # transcript under CLAUDE_CONFIG_DIR, which the client passes down to us
        # as a spawned child. Recording it here is what lets `receipt` count a
        # profile other than the default one — it scanned a hardcoded
        # ~/.claude/projects and reported 12 of 348 calls on a three-profile
        # box. No-op on the default profile; never allowed to block startup.
        try:
            from .storage.transcript_roots import register_session_root
            register_session_root()
        except Exception:
            logger.debug("transcript root registry: register failed", exc_info=True)

        # Import the native embedding backend here, on the main thread, before
        # any event loop starts. Deferring it to the first embed call runs it
        # inside an asyncio.to_thread worker while the main thread services the
        # transport, which deadlocks on the Windows loader lock and hangs the
        # call forever (jdatamunch-mcp#3, reproduced here). Above both dispatch
        # branches so every transport is covered once; no-op unless a native
        # provider (local_onnx / sentence-transformers) is configured.
        try:
            from .tools.embed_repo import warm_up_embedding_backend
            warm_up_embedding_backend()
        except Exception:
            logger.debug("embedding warm-up failed", exc_info=True)

        if watcher_enabled:
            # Watcher params: CLI flag > config > default
            cfg_paths = config_module.get("watch_paths", [])
            if args.watcher_path is not None:
                watcher_paths = args.watcher_path
            elif cfg_paths:
                watcher_paths = cfg_paths
            else:
                watcher_paths = [os.getcwd()]

            use_ai = not args.watcher_no_ai_summaries and _default_use_ai_summaries()

            watcher_kwargs = dict(
                paths=watcher_paths,
                debounce_ms=(
                    args.watcher_debounce
                    if args.watcher_debounce is not None
                    else config_module.get("watch_debounce_ms", 2000)
                ),
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
                extra_ignore_patterns=(
                    args.watcher_extra_ignore
                    if args.watcher_extra_ignore is not None
                    else config_module.get("watch_extra_ignore", []) or None
                ),
                follow_symlinks=(
                    args.watcher_follow_symlinks
                    or config_module.get("watch_follow_symlinks", False)
                ),
                idle_timeout_minutes=(
                    args.watcher_idle_timeout
                    if args.watcher_idle_timeout is not None
                    else config_module.get("watch_idle_timeout", None)
                ),
            )

            log_path = (
                getattr(args, "watcher_log", None)
                or config_module.get("watch_log", None)
            )

            try:
                if args.transport == "sse":
                    asyncio.run(_run_server_with_watcher(
                        run_sse_server, (args.host, args.port), watcher_kwargs, log_path,
                    ))
                elif args.transport == "streamable-http":
                    asyncio.run(_run_server_with_watcher(
                        run_streamable_http_server, (args.host, args.port), watcher_kwargs, log_path,
                    ))
                else:
                    asyncio.run(_run_server_with_watcher(
                        run_stdio_server, (), watcher_kwargs, log_path,
                    ))
            except KeyboardInterrupt:
                pass
        else:
            if args.transport == "sse":
                asyncio.run(run_sse_server(args.host, args.port))
            elif args.transport == "streamable-http":
                asyncio.run(run_streamable_http_server(args.host, args.port))
            else:
                asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
