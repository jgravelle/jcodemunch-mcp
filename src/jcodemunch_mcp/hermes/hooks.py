"""Lifecycle hooks for the jcodemunch Hermes plugin.

These are Hermes plugin hooks (not Claude Code hooks). Unlike Claude
Code's ``PreToolUse`` hooks — which can exit with status 2 to hard-
block a tool call — Hermes plugin hooks are fire-and-forget observers:
their return values are ignored for every hook except ``pre_llm_call``,
which can inject extra context into the current turn's user message.

Given that constraint, we translate the three bash scripts from
``AGENT_HOOKS.md`` (Read Guard, Edit Guard, Index Hook) into a
combination of observer hooks and a proactive ``pre_llm_call``
guardrails message that nudges the model toward jcodemunch tools
before it ever reaches for ``terminal grep`` or raw ``write_file``.

The one hook that translates perfectly is the Index Hook: after every
successful ``write_file`` / ``patch``, we call ``jcm_index_file`` in
the background so subsequent retrievals stay fresh.

Environment variables
---------------------

``JCODEMUNCH_HERMES_DISABLE_HOOKS``
    Set to ``1`` to register the plugin's tools but none of its hooks.
    Useful when you want jcodemunch available without any nudging or
    auto-reindex behaviour.

``JCODEMUNCH_HERMES_HARD_BLOCK``
    Mirrors ``JCODEMUNCH_HARD_BLOCK`` from the bash Edit Guard. When
    set to ``1``, the edit guard prints a louder warning. It cannot
    actually block the edit (Hermes hooks can't prevent tool
    execution), but the warning is escalated in the log and surfaced
    into the next turn's injected context.

``JCODEMUNCH_HERMES_DEBUG``
    Set to ``1`` to enable verbose logging of every hook invocation.

``JCODEMUNCH_HERMES_AUTO_INDEX``
    Set to ``0`` to disable the post-edit auto-reindex. Defaults to
    on.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import Counter
from typing import Any, Dict, List, Optional, Set

from . import tools as _tools
from ._bridge import run_async_background

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Patterns                                                                    #
# --------------------------------------------------------------------------- #

# Extensions we consider "code" for the purposes of read/edit guards.
# Kept in sync with AGENT_HOOKS.md's bash EXT regex.
_CODE_EXTENSIONS: Set[str] = {
    "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs",
    "go", "rs", "java", "rb", "php", "cs", "cpp", "cc", "cxx",
    "c", "h", "hpp", "swift", "kt", "scala", "dart", "lua",
    "r", "hs", "ex", "exs", "sh", "sql", "vue", "svelte",
    "m", "mm", "pl", "erl", "clj", "cljs",
}

# Shell-command patterns that indicate the model is reaching for raw
# grep/find/cat instead of jcodemunch's structured retrieval. Anchored
# loosely with word boundaries so we don't match "cargo" as "ca(t)".
_RAW_EXPLORATION_BINARIES = (
    "grep", "rg", "egrep", "fgrep", "ag", "ack",
    "find", "fd",
    "cat", "head", "tail", "less", "more",
    "sed", "awk",
)

# Command-line patterns that are safe — builds, tests, package managers,
# version control. These short-circuit the read guard.
_SAFE_COMMAND_HEADS = (
    "npm", "yarn", "pnpm", "npx",
    "cargo", "rustc",
    "go ", "go\t",
    "python", "python3", "pip", "uv ", "uvx",
    "pytest", "jest", "vitest", "rspec", "phpunit",
    "mvn", "gradle",
    "git ", "git\t", "gh ",
    "docker", "kubectl", "helm",
    "brew", "apt", "apt-get", "dnf", "yum",
    "make ", "make\t",
    "jcodemunch",
)


_CODE_EXT_REGEX = re.compile(
    r"\.(" + "|".join(sorted(_CODE_EXTENSIONS)) + r")\b",
    re.IGNORECASE,
)
_BINARY_REGEX = re.compile(
    r"(?:^|[;\|\&\s])(" + "|".join(_RAW_EXPLORATION_BINARIES) + r")\b",
    re.IGNORECASE,
)
_UUID_TASK_ID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _looks_like_safe_command(cmd: str) -> bool:
    """True if the command starts with a known build/test/VCS binary."""
    stripped = cmd.lstrip()
    return any(stripped.startswith(head) for head in _SAFE_COMMAND_HEADS)


def _looks_like_code_exploration(cmd: str) -> bool:
    """True if the shell command looks like raw grep/find/cat against
    source files."""
    if not cmd:
        return False
    if _looks_like_safe_command(cmd):
        return False
    return bool(_BINARY_REGEX.search(cmd) and _CODE_EXT_REGEX.search(cmd))


def _is_code_path(path: Optional[str]) -> bool:
    if not path:
        return False
    return bool(_CODE_EXT_REGEX.search(path))


# --------------------------------------------------------------------------- #
# Session-scoped state                                                        #
# --------------------------------------------------------------------------- #

# Per-session counters so pre_llm_call can report "last turn you used
# terminal grep on source files" style remediation messages. Guard hits
# are stored under session_id; tool counts are collected under task_id
# and later aggregated back to the session.
_state_lock = threading.Lock()
_session_read_guard_hits: Dict[str, List[str]] = {}
_session_edit_guard_hits: Dict[str, List[str]] = {}
# Note: ``pre_tool_call`` receives a ``task_id`` which is NOT always the
# same as the ``session_id`` that ``on_session_start`` / ``on_session_end``
# see. In some Hermes providers (e.g. gpt-5.3-codex) task_id is a
# per-tool-call UUID while session_id is a different identifier entirely.
# We therefore keep tool counts under the task_id we receive AND maintain
# a mapping back to the active session_id so ``on_session_end`` can
# aggregate across every task_id that ran during that session.
_session_tool_counts: Dict[str, Counter] = {}
_session_indexed_repos_cache: Dict[str, Optional[List[Dict[str, Any]]]] = {}
# task_id → session_id lookup. Populated in ``read_guard`` and
# ``edit_guard`` by crediting calls to the most recently started
# session. Works perfectly for single-session CLI use; for concurrent
# gateway sessions the attribution is best-effort (last-writer-wins).
_task_to_session: Dict[str, str] = {}
_active_session_id: Optional[str] = None


def _session_key_for_task(task_id: str) -> str:
    """Return the session-scoped key under which hook state should live.

    Hermes only gives ``pre_tool_call`` / ``post_tool_call`` a
    ``task_id``. Some providers set ``task_id == session_id``; others
    use a per-tool UUID and only expose the real session identifier via
    ``on_session_start`` / ``on_session_end``. In the latter case we
    attribute best-effort to the currently active session.
    """
    with _state_lock:
        if task_id in _task_to_session:
            return _task_to_session[task_id]

        if not task_id:
            return _active_session_id or task_id

        # Direct-style providers pass the session identifier itself as
        # task_id. Keep that exact key unless the value looks like a
        # per-tool UUID, in which case we remap it to the active session.
        if _active_session_id is None or task_id == _active_session_id:
            return task_id

        if _UUID_TASK_ID_REGEX.match(task_id):
            _task_to_session[task_id] = _active_session_id
            return _active_session_id

        return task_id


def _record_read_hit(session_id: str, note: str) -> None:
    with _state_lock:
        _session_read_guard_hits.setdefault(session_id, []).append(note)


def _record_edit_hit(session_id: str, note: str) -> None:
    with _state_lock:
        _session_edit_guard_hits.setdefault(session_id, []).append(note)


def _bump_tool_count(task_id: str, tool_name: str) -> None:
    """Record a tool invocation under the given task_id and map it
    back to the currently active session so ``on_session_end`` can
    aggregate correctly even when task_id != session_id."""
    with _state_lock:
        _session_tool_counts.setdefault(task_id, Counter())[tool_name] += 1
        if _active_session_id is not None:
            _task_to_session[task_id] = _active_session_id


def _drain_read_hits(session_id: str) -> List[str]:
    with _state_lock:
        return _session_read_guard_hits.pop(session_id, [])


def _drain_edit_hits(session_id: str) -> List[str]:
    with _state_lock:
        return _session_edit_guard_hits.pop(session_id, [])


def _aggregate_counts_for_session(session_id: str) -> Counter:
    """Sum tool counts across every task_id mapped to this session.

    Some providers pass ``task_id == session_id`` (so the direct
    lookup in ``_session_tool_counts`` finds the entry); others pass
    a per-tool UUID (so we have to walk the ``_task_to_session``
    mapping). We handle both by collecting a set of unique keys to
    aggregate, then summing each one exactly once.
    """
    with _state_lock:
        keys_to_sum: set = set()
        # Direct lookup — the caller's session_id may itself be a
        # counter key if the provider passes task_id == session_id.
        if session_id in _session_tool_counts:
            keys_to_sum.add(session_id)
        # Every task_id that was credited to this session.
        for task_id, sid in _task_to_session.items():
            if sid == session_id and task_id in _session_tool_counts:
                keys_to_sum.add(task_id)

        totals: Counter = Counter()
        for key in keys_to_sum:
            totals.update(_session_tool_counts[key])
        return totals


def _set_active_session(session_id: Optional[str]) -> None:
    global _active_session_id
    with _state_lock:
        _active_session_id = session_id


def _reset_session(session_id: str) -> None:
    with _state_lock:
        _session_read_guard_hits.pop(session_id, None)
        _session_edit_guard_hits.pop(session_id, None)
        _session_indexed_repos_cache.pop(session_id, None)
        # Drop every task_id that belonged to this session, including
        # the counter entries they accumulated.
        task_ids = [
            tid for tid, sid in _task_to_session.items() if sid == session_id
        ]
        for tid in task_ids:
            _task_to_session.pop(tid, None)
            _session_tool_counts.pop(tid, None)
        # Also drop any direct-keyed counters (defensive).
        _session_tool_counts.pop(session_id, None)


# --------------------------------------------------------------------------- #
# 1. Read Guard — pre_tool_call observer                                      #
# --------------------------------------------------------------------------- #


def read_guard(tool_name: str, args: Dict[str, Any], task_id: str, **kwargs: Any) -> None:
    """Fires before every tool call.

    Detects when the agent is about to do raw code exploration through
    built-in Hermes tools (``terminal`` with grep/find/cat,
    ``search_files`` on a code-file glob, ``read_file`` on a source
    file) and records a hit against the session so ``pre_llm_call`` can
    inject remediation context next turn.

    This is the closest we can get to Claude Code's ``PreToolUse``
    exit-2 block: Hermes plugin hooks can't prevent the tool from
    executing, but we can warn loudly and nudge the model next turn.
    """
    try:
        if tool_name is None:
            return

        # Always log the entry so the debug log shows EVERY tool call
        # the hook inspected, regardless of whether it flagged. This
        # is how operators confirm the hook is actually running.
        logger.info(
            "read_guard fired: tool=%s session=%s",
            tool_name, task_id or "-",
        )

        _bump_tool_count(task_id, tool_name)

        # Let our own tools through — calling jcm_* is the *desired*
        # behaviour, not a violation.
        if tool_name.startswith(_tools.TOOL_PREFIX):
            return

        note: Optional[str] = None
        session_key = _session_key_for_task(task_id)

        if tool_name == "terminal":
            cmd = (args or {}).get("command", "") or ""
            if _looks_like_code_exploration(cmd):
                note = f"terminal: `{cmd[:120]}`"

        elif tool_name == "search_files":
            # Every search_files call is a candidate — jcm_search_symbols
            # gives structured results with symbol IDs and is much more
            # token-efficient for code.
            pattern = (args or {}).get("pattern", "") or ""
            path = (args or {}).get("path", "") or ""
            file_glob = (args or {}).get("file_glob", "") or ""
            # Only count it if the target looks like source code. Non-code
            # globs such as *.md / *.yaml should pass through untouched.
            if _is_code_path(path) or _is_code_path(pattern) or _is_code_path(file_glob):
                note = f"search_files pattern={pattern!r}"

        elif tool_name == "read_file":
            path = (args or {}).get("path", "") or ""
            if _is_code_path(path):
                note = f"read_file: {path}"

        if note:
            _record_read_hit(session_key, note)
            logger.warning(
                "read_guard FLAGGED: %s used for code exploration "
                "(session=%s) — %s. Prefer jcm_* tools for structured "
                "retrieval (will be nudged next turn via pre_llm_call).",
                tool_name, task_id or "-", note,
            )
        else:
            logger.debug(
                "read_guard pass-through: tool=%s (not code exploration)",
                tool_name,
            )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("jcm read_guard failed harmlessly: %s", exc)


# --------------------------------------------------------------------------- #
# 2. Edit Guard — pre_tool_call observer                                      #
# --------------------------------------------------------------------------- #

_EDIT_TOOLS: Set[str] = {"write_file", "patch"}


def edit_guard(tool_name: str, args: Dict[str, Any], task_id: str, **kwargs: Any) -> None:
    """Fires before ``write_file`` / ``patch``.

    Analogous to the ``jcodemunch_edit_guard.sh`` soft gate: we log a
    warning and record a hit against the session. ``pre_llm_call`` will
    inject a remediation reminder next turn pointing at
    ``jcm_get_symbol_source`` / ``jcm_get_blast_radius`` /
    ``jcm_find_references``.
    """
    try:
        if tool_name not in _EDIT_TOOLS:
            # Only log the interesting tools so the log isn't flooded
            # with noise from every tool call the hook is asked about.
            return

        logger.info(
            "edit_guard fired: tool=%s session=%s",
            tool_name, task_id or "-",
        )

        # Explicit allow — same escape hatch as the bash script.
        if _env_flag("JCODEMUNCH_HERMES_ALLOW_RAW_WRITE"):
            logger.debug("edit_guard pass-through: JCODEMUNCH_HERMES_ALLOW_RAW_WRITE=1")
            return

        path = (args or {}).get("path", "") or ""
        if not _is_code_path(path):
            logger.debug(
                "edit_guard pass-through: path=%s is not a code file",
                path,
            )
            return

        hard = _env_flag("JCODEMUNCH_HERMES_HARD_BLOCK")
        verb = "BLOCKED (advisory)" if hard else "warned"
        note = f"{tool_name} -> {path} [{verb}]"
        _record_edit_hit(_session_key_for_task(task_id), note)

        # Claude Code's exit-2 semantics don't exist in Hermes plugin
        # hooks — the edit will proceed either way. The hard-block flag
        # just escalates logging and makes the next-turn injection
        # noisier so the model is more likely to notice.
        level = logging.ERROR if hard else logging.WARNING
        logger.log(
            level,
            "edit_guard FLAGGED: raw %s on %s — consider jcm_get_symbol_source / "
            "jcm_get_blast_radius / jcm_find_references first "
            "(will be nudged next turn via pre_llm_call).",
            tool_name, path,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("jcm edit_guard failed harmlessly: %s", exc)


# --------------------------------------------------------------------------- #
# 3. Index Hook — post_tool_call background reindex                           #
# --------------------------------------------------------------------------- #


def _extract_edited_paths(tool_name: str, args: Dict[str, Any]) -> List[str]:
    """Pull every edited file path out of a tool's args.

    ``write_file`` / ``patch`` take a single ``path``. If either grows
    a batch variant in the future (list of edits), we handle that too
    without changes.
    """
    paths: List[str] = []
    args = args or {}
    single = args.get("path")
    if isinstance(single, str) and single:
        paths.append(single)
    edits = args.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                p = edit.get("path") or edit.get("file_path")
                if isinstance(p, str) and p:
                    paths.append(p)
    # Dedupe while preserving order.
    seen: Set[str] = set()
    unique: List[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _looks_like_success(result: Any) -> bool:
    """Tool results are always JSON strings in Hermes. Parse it and
    check for an ``error`` key. Anything we can't parse is treated as
    success — failing closed here would spam the index with no-ops."""
    if not isinstance(result, str):
        return True
    try:
        parsed = json.loads(result)
    except Exception:
        return True
    if not isinstance(parsed, dict):
        return True
    return "error" not in parsed


def index_hook(
    tool_name: str,
    args: Dict[str, Any],
    result: str,
    task_id: str,
    **kwargs: Any,
) -> None:
    """Fires after every tool call. For successful ``write_file`` /
    ``patch`` against code files, re-indexes the affected file in the
    background.

    Mirrors ``jcodemunch_index_hook.sh``. Failures are logged and
    swallowed — indexing issues must never block the agent.
    """
    try:
        if tool_name not in _EDIT_TOOLS:
            return

        logger.info(
            "index_hook fired: tool=%s session=%s",
            tool_name, task_id or "-",
        )

        if not _env_flag("JCODEMUNCH_HERMES_AUTO_INDEX", default="1"):
            logger.debug("index_hook skip: JCODEMUNCH_HERMES_AUTO_INDEX=0")
            return
        if not _looks_like_success(result):
            logger.debug("index_hook skip: tool result looked like an error")
            return

        paths = [p for p in _extract_edited_paths(tool_name, args) if _is_code_path(p)]
        if not paths:
            logger.debug(
                "index_hook skip: no code-file paths extracted from args"
            )
            return

        # Lazy import so importing the hooks module doesn't pull in
        # server.py on its own.
        from ..server import call_tool as _call_tool

        for path in paths:
            # Normalize to absolute — jcodemunch's index_file requires it.
            abs_path = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(abs_path):
                logger.debug("index_hook skip: %s does not exist", abs_path)
                continue

            logger.info(
                "index_hook SCHEDULED background reindex: %s", abs_path
            )
            run_async_background(
                _call_tool("index_file", {"path": abs_path})
            )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("jcm index_hook failed harmlessly: %s", exc)


# --------------------------------------------------------------------------- #
# 4. Session context injector — pre_llm_call                                  #
# --------------------------------------------------------------------------- #


_FIRST_TURN_GUIDANCE = """\
============================================================
  PRIORITY INSTRUCTION — jcodemunch plugin active
============================================================

This Hermes session has the jcodemunch tree-sitter AST toolkit
installed. It exposes ~46 tools prefixed `jcm_`. For ANY code
exploration, navigation, or editing task in this conversation you
MUST prefer `jcm_*` tools over raw `terminal` with grep/find/cat,
over `search_files` on source-file globs, and over `read_file` on
`.py`/`.ts`/`.tsx`/`.js`/`.go`/`.rs`/`.java`/`.rb`/`.php`/`.cs`/
`.cpp`/`.c`/`.h`/`.swift`/`.kt`/`.scala` files.

Mandatory substitution table:

  Raw approach                         Preferred jcm tool
  ------------------------------------ ----------------------------------
  grep / rg 'X' on source files        jcm_search_text or jcm_search_symbols
  Find a function/class by name        jcm_search_symbols
  Read a specific function body        jcm_get_symbol_source
  List all symbols in a file           jcm_get_file_outline (NOT read_file)
  Understand an unfamiliar repo        jcm_list_repos + jcm_get_repo_outline
  Find what imports a file             jcm_find_importers
  Find all call sites of an identifier jcm_find_references
  Before editing a function            jcm_get_blast_radius + jcm_find_references
  Get symbol + imports + callers       jcm_get_context_bundle

Workflow on a fresh repo:
  1. jcm_list_repos                    — see what's already indexed
  2. jcm_index_folder                  — if the current project isn't yet
  3. jcm_get_repo_outline              — orient yourself
  4. jcm_search_symbols / jcm_search_text — find what you need
  5. jcm_get_symbol_source             — read it

jcodemunch is typically 90-95% more token-efficient than raw file
reads and returns structured AST results instead of raw text dumps.

SCOPE — what this applies to:
  * Applies to source code files only (the extension list above).
  * Non-code files (README.md, Rules.md, *.txt, *.json config,
    *.yaml, *.toml, *.sql schemas) can still be read with stock
    `read_file` / `search_files` — jcodemunch is not for those.

This guidance is injected automatically by the jcodemunch Hermes
plugin. Treat it as a hard preference for this session, not an
optional suggestion. The plugin monitors tool use and will remind
you next turn if it sees raw grep/cat/read_file on source code.\
"""

_SHORT_REMINDER = (
    "Reminder: the jcodemunch plugin is active — for source-code "
    "reads/searches prefer `jcm_search_symbols`, `jcm_get_symbol_source`, "
    "`jcm_get_file_outline`, `jcm_search_text`. Non-code files can use "
    "stock tools."
)


def _fetch_indexed_repos(session_id: str) -> Optional[List[Dict[str, Any]]]:
    """Call ``jcm_list_repos`` once per session and cache the result
    in memory. Returns None on any failure — the injector will fall
    back to generic guidance."""
    with _state_lock:
        if session_id in _session_indexed_repos_cache:
            return _session_indexed_repos_cache[session_id]

    try:
        raw = _tools.call_jcm_tool("list_repos", {})
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            repos = None
        else:
            repos = parsed.get("repos")
            if not isinstance(repos, list):
                repos = None
    except Exception as exc:
        logger.debug("jcm session context: list_repos failed: %s", exc)
        repos = None

    with _state_lock:
        _session_indexed_repos_cache[session_id] = repos
    return repos


def _current_repo_status() -> str:
    """Return a short human-readable summary of whether the current
    working directory looks like a repo root and whether it seems to
    be indexed. Best-effort — never raises."""
    try:
        cwd = os.getcwd()
    except Exception:
        return ""

    markers = (".git", "pyproject.toml", "package.json", "go.mod",
               "Cargo.toml", "pom.xml")
    root = cwd
    for _ in range(10):
        if any(os.path.exists(os.path.join(root, m)) for m in markers):
            return f"Current working directory: {root}"
        parent = os.path.dirname(root)
        if parent == root:
            break
        root = parent
    return f"Current working directory: {cwd}"


def session_context_injector(
    session_id: str,
    user_message: str,
    conversation_history: list,
    is_first_turn: bool,
    model: str,
    platform: str,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Fires once per turn. Injects jcodemunch guidance into the
    current turn's user message.

    On the first turn of a session we inject the full workflow
    guidance plus a snapshot of already-indexed repos. On subsequent
    turns we only inject if the previous turn triggered a read/edit
    guard, in which case we show a short remediation note pointing at
    the right jcm_* tool for the job.
    """
    try:
        logger.info(
            "pre_llm_call fired: session=%s is_first_turn=%s platform=%s model=%s",
            session_id, is_first_turn, platform, model,
        )

        blocks: List[str] = []

        if is_first_turn:
            blocks.append(_FIRST_TURN_GUIDANCE)

            cwd_status = _current_repo_status()
            if cwd_status:
                blocks.append(cwd_status)

            repos = _fetch_indexed_repos(session_id)
            if repos:
                summary = ", ".join(
                    str(r.get("repo", "?")) for r in repos[:10]
                )
                suffix = "" if len(repos) <= 10 else f" (+{len(repos) - 10} more)"
                blocks.append(f"Indexed repos ({len(repos)}): {summary}{suffix}")
            elif repos is not None:
                # We got a valid response but zero repos.
                blocks.append(
                    "No repositories are currently indexed. Run "
                    "`jcm_index_folder` on the current project to enable "
                    "structured exploration."
                )

        # Remediation: drain any read/edit guard hits from the previous
        # turn and surface them. This fires regardless of is_first_turn
        # so even a mid-session switch to raw tools triggers the nudge.
        read_hits = _drain_read_hits(session_id)
        edit_hits = _drain_edit_hits(session_id)

        if read_hits:
            blocks.append(
                "⚠ Previous turn reached for raw code-exploration tools "
                + str(len(read_hits))
                + " time(s): "
                + "; ".join(read_hits[:5])
                + ". These usually have a faster jcm_* equivalent — "
                "`jcm_search_symbols`, `jcm_get_symbol_source`, "
                "`jcm_search_text`, or `jcm_get_file_outline`. Please "
                "use jcm_* tools going forward."
            )

        if edit_hits:
            prefix = (
                "⛔ HARD-BLOCK MODE: "
                if _env_flag("JCODEMUNCH_HERMES_HARD_BLOCK")
                else "⚠ Previous turn wrote to source files without consulting jcodemunch: "
            )
            blocks.append(
                prefix
                + "; ".join(edit_hits[:5])
                + ". Before the next edit, use `jcm_get_symbol_source` "
                "to confirm the target, `jcm_get_blast_radius` for impact, "
                "and `jcm_find_references` to find call sites."
            )

        if not blocks:
            # Subsequent turns with no violations — inject a single-line
            # reminder so the guidance doesn't entirely drop out of
            # context after the first turn. Cheap and non-intrusive.
            if not is_first_turn:
                logger.debug(
                    "pre_llm_call: injecting short reminder (no violations)"
                )
                return {"context": _SHORT_REMINDER}
            logger.debug("pre_llm_call: nothing to inject")
            return None

        payload = "\n\n".join(blocks)
        logger.info(
            "pre_llm_call INJECTED %d block(s), %d chars (first_turn=%s, "
            "read_hits=%d, edit_hits=%d)",
            len(blocks), len(payload), is_first_turn,
            len(read_hits), len(edit_hits),
        )
        return {"context": payload}
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("jcm session_context_injector failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Session lifecycle                                                           #
# --------------------------------------------------------------------------- #


def on_session_start(session_id: str, model: str, platform: str, **kwargs: Any) -> None:
    """Warm the list_repos cache so the first pre_llm_call injection
    can include indexed repos without blocking the turn, and record
    this session as the 'active' one so per-tool hooks can credit
    their counters back to it."""
    try:
        logger.info(
            "on_session_start fired: session=%s platform=%s model=%s",
            session_id, platform, model,
        )

        # Record the active session so read_guard / edit_guard can
        # attribute task_id-keyed counters back to this session.
        _set_active_session(session_id)

        # Prefetch on a background task — don't block startup.
        def _warm() -> None:
            try:
                _fetch_indexed_repos(session_id)
            except Exception:
                pass
        threading.Thread(target=_warm, name="jcm-prefetch", daemon=True).start()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("jcm on_session_start failed: %s", exc)


def on_session_end(
    session_id: str,
    completed: bool,
    interrupted: bool,
    model: str,
    platform: str,
    **kwargs: Any,
) -> None:
    """Log a short session summary and drop session-scoped state."""
    try:
        # Aggregate counts across every task_id that was credited to
        # this session. Works whether Hermes passes task_id==session_id
        # (some providers) or a per-tool UUID (e.g. gpt-5.3-codex).
        counts = _aggregate_counts_for_session(session_id)

        jcm_calls = sum(
            v for k, v in counts.items() if k.startswith(_tools.TOOL_PREFIX)
        )
        total = sum(counts.values())
        top_tools = ", ".join(f"{k}:{v}" for k, v in counts.most_common(5))

        logger.info(
            "on_session_end fired: session=%s total_tools=%d jcm_calls=%d "
            "completed=%s interrupted=%s top=[%s]",
            session_id, total, jcm_calls, completed, interrupted, top_tools,
        )
    finally:
        _reset_session(session_id)
        # Clear the active-session marker so stale task_id hits from
        # previous sessions aren't attributed to the next one.
        _set_active_session(None)


# --------------------------------------------------------------------------- #
# Registration helper                                                         #
# --------------------------------------------------------------------------- #


def register_hooks(ctx: Any) -> int:
    """Register every hook on the given Hermes plugin context.

    Returns the number of hooks that were actually registered, which
    will be 0 if ``JCODEMUNCH_HERMES_DISABLE_HOOKS=1``.
    """
    if _env_flag("JCODEMUNCH_HERMES_DISABLE_HOOKS"):
        logger.info(
            "register_hooks: disabled via JCODEMUNCH_HERMES_DISABLE_HOOKS"
        )
        return 0

    logger.info("register_hooks: wiring up lifecycle callbacks")

    registered = 0
    for event, callback in (
        ("pre_tool_call", read_guard),
        ("pre_tool_call", edit_guard),
        ("post_tool_call", index_hook),
        ("pre_llm_call", session_context_injector),
        ("on_session_start", on_session_start),
        ("on_session_end", on_session_end),
    ):
        try:
            ctx.register_hook(event, callback)
            logger.info("register_hooks: %s -> %s", event, callback.__name__)
            registered += 1
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "register_hooks: failed to register %s -> %s: %s",
                event, callback.__name__, exc,
            )
    logger.info("register_hooks: done, %d callbacks attached", registered)
    return registered
