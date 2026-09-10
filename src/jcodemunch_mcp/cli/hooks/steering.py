"""PreToolUse steering toward jCodemunch before a native file tool runs.

All interception is gated on the target being inside an INDEXED repo (jcm
can serve it there; elsewhere the hints would just error):
    Read on a large code file → get_file_outline / get_symbol_source;
    Grep → search_text / search_symbols / find_references;
    Glob → get_file_tree / search_symbols / get_repo_outline;
    Bash command lines that OPEN with a search command (grep/rg/find/...)
    → the same jcm routes.

The output-channel rule (exit-0 reaches the model only via
``additionalContext``) lives on ``_common._emit_additional_context``.
"""

import json
import logging
import os
import re

from ._common import (
    _CODE_EXTENSIONS,
    _emit_additional_context,
    _norm_path,
    _note_transcript_root,
    _path_overlaps,
    _read_hook_payload,
)

logger = logging.getLogger(__name__)



# Minimum file size to trigger jCodemunch suggestion.
# Override with JCODEMUNCH_HOOK_MIN_SIZE env var.
# Garbage parses to the DEFAULT, never to a crash: every hook entry point
# imports this module, so a ValueError here would kill all hooks at once.
try:
    _MIN_SIZE_BYTES = int(os.environ.get("JCODEMUNCH_HOOK_MIN_SIZE", "4096"))
except ValueError:
    _MIN_SIZE_BYTES = 4096


def _enforce_mode() -> str:
    """jCodemunch enforcement tier for native file tools (``JCODEMUNCH_ENFORCE``).

    * ``"advisory"`` (default): nudge via ``additionalContext`` but **allow** —
      the v1.108.47 behavior. A hard deny here would break Read-before-Edit.
    * ``"strict"``: **deny** a native Read/Grep that an indexed-repo jcm route
      can already serve. Targeted reads (``offset``/``limit``), tiny files, and
      paths outside every indexed repo still pass, so the escape hatch is always
      one step away and jcm is never blamed for a search it can't serve.
    * ``"off"``: no nudge, no deny — fully silent.

    Unknown values fall back to ``"advisory"`` so a typo never hard-blocks tools.
    Opt in to strict with ``jcodemunch-mcp init --strict`` (persists the env var
    into ~/.claude/settings.json) or by exporting it yourself.
    """
    val = os.environ.get("JCODEMUNCH_ENFORCE", "advisory").strip().lower()
    if val in {"strict", "deny", "block", "hard"}:
        return "strict"
    if val in {"off", "0", "false", "no", "none", "silent"}:
        return "off"
    return "advisory"


def _emit_pretooluse_deny(reason: str) -> int:
    """Emit a Claude Code PreToolUse ``deny`` decision (stdout JSON) and exit 0.

    The deny lives in the JSON decision channel, and exit code stays 0 so the
    harness reads the decision, not a crash.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def _search_nudge_enabled() -> bool:
    """Whether the search→jcm nudge is active. Set JCODEMUNCH_HOOK_GREP_NUDGE=0
    (or false/no/off) to silence it."""
    return os.environ.get("JCODEMUNCH_HOOK_GREP_NUDGE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _indexed_source_roots() -> list[str]:
    """Normalised absolute source roots of every locally-indexed repo.

    Loaded fresh per call (the hook is a short-lived process). Best-effort:
    any failure yields ``[]`` so the hook silently allows the search rather
    than ever blocking it on a store hiccup.
    """
    try:
        from ...storage import IndexStore
        return [
            _norm_path(sr)
            for sr in IndexStore().list_source_roots()
            if sr.strip()
        ]
    except Exception:
        return []


def _grep_search_root(tool_input: dict, cwd: str) -> str:
    """Resolve the directory a Grep/Glob call will actually scan (normalised)."""
    path = (tool_input.get("path") or "").strip()
    base = cwd or os.getcwd()
    if not path:
        root = base
    elif os.path.isabs(path):
        root = path
    else:
        root = os.path.join(base, path)
    return _norm_path(root)


_SEARCH_ROUTES = {
    "Grep": (
        "  - search_text     : same regex/substring scan, ranked + winnowed\n"
        "  - search_symbols  : when hunting a definition (function/class/const/type)\n"
        "  - check_references : 'where is X used' (imports + every content match)\n"
        "  - find_references / find_importers : 'who imports this identifier / file'"
    ),
    "Glob": (
        "  - get_file_tree   : ranked, token-budgeted file listing\n"
        "  - search_symbols  : when the filename hunt is really a symbol hunt\n"
        "  - get_repo_outline: structure overview without a directory walk"
    ),
}
# Bash covers the same searches as Grep, plus find-style file discovery.
_SEARCH_ROUTES["Bash"] = (
    _SEARCH_ROUTES["Grep"]
    + "\n  - get_file_tree   : instead of `find` for file discovery"
)


def _emit_search_steering(tool_name: str, what: str, *, deny: bool) -> int:
    """Emit the nudge or deny for an already-gated search interception.

    Emit-only by design: each caller owns its own gate (nudge switch, store
    load, indexed-root overlap), because the Bash caller needs the loaded
    roots for its token scan and a gate here would either re-check what the
    caller proved or force a second store load.
    """
    routes = _SEARCH_ROUTES[tool_name]
    if deny:
        return _emit_pretooluse_deny(
            f"jCodemunch strict mode: {what} targets an indexed repo. Use the jcm "
            f"routes instead:\n{routes}\n"
            "(JCODEMUNCH_ENFORCE=advisory for warn-only, =off to disable.)"
        )
    return _emit_additional_context(
        "PreToolUse",
        f"jCodemunch: {what} targets an indexed repo. Exhaust the jcm "
        "routes first, since they're tighter and credited (a raw scan is neither):\n"
        f"{routes}\n"
        f"Fall back to {tool_name} only once those come up empty.",
    )


# Search commands intercepted at the START of a Bash command line (a grep
# after a pipe filters the previous command's output, which jcm cannot serve),
# mapped to whether strict mode may DENY them. `find` is never deniable:
# `find … -delete` opens with the same word, and a deny steering to search
# routes cannot do the deletion.
_BASH_SEARCH_COMMANDS = {
    "grep": True, "egrep": True, "fgrep": True,
    "rg": True, "ag": True, "ack": True,
    "find": False,
}
_BASH_SEARCH_RE = re.compile(
    r"^\s*\(?\s*"                                        # optional subshell paren
    r"(?:[A-Za-z_]\w*=(?:\"[^\"]*\"|'[^']*'|\S*)\s+)*"  # leading FOO=bar assignments
    r"(?:command\s+)?(" + "|".join(_BASH_SEARCH_COMMANDS) + r")\b"
)

# Absolute or ~-anchored path tokens inside a command line.
# Absolute or ~-anchored path tokens inside a command line.
#
# ⚠⚠ The drive-letter alternative is load-bearing on Windows and was missing
# (#541, found by the test for the shell-expansion half). `/c/Users/j/x.md`
# (git-bash) was seen and `C:/Users/j/x.md` was not, so a strict deny fired on
# a search targeting a path outside every indexed root — on the platform most
# of this project's users are on.
#
# ⚠ Unlike a `$VAR`, a drive-letter path IS resolvable, so this is a real fix
# rather than a downgrade: the guard now sees the target and answers correctly.
#
# ⚠ A leading quote is allowed so `grep "C:/x.md"` is seen. A quoted path
# containing a space still captures only its first segment, which resolves to
# "not inside any root" and therefore errs toward ALLOWING — the safe direction.
_BASH_PATH_TOKEN_RE = re.compile(
    r"(?:^|[\s=])[\"']?((?:/|~/|[A-Za-z]:[\\/])[^\s;|&)>\"']+)"
)

# A shell expansion the hook cannot resolve: $VAR, ${VAR}, $(cmd), `cmd`.
#
# ⚠⚠ Deliberately NOT a bare `\$`. A trailing `$` is a regex end-anchor and
# `grep -n "foo$" src/` is idiomatic — treating that as unresolvable would
# stop denying one of the commonest in-repo searches, weakening the very
# enforcement a strict-mode user opted into. Requiring a name char, `{` or `(`
# after the `$` separates an expansion from an anchor.
_SHELL_EXPANSION_RE = re.compile(r"\$[A-Za-z_{(]|`")


def _bash_path_is_unresolvable(command: str) -> bool:
    """True when the command's target depends on a shell expansion.

    ``_bash_targets_outside_roots`` reads path tokens out of the raw command
    string, so it is blind to anything the shell has not expanded yet: the
    hook cannot know what ``$B`` holds, and guessing is worse than not looking.
    """
    return _SHELL_EXPANSION_RE.search(command) is not None


def _bash_targets_outside_roots(command: str, roots: "list[str]") -> bool:
    """True when the command names an absolute/home path outside every indexed
    root — a search jcm cannot serve, so a strict deny would block real work
    while falsely claiming the search targets an indexed repo."""
    if re.search(r"(?:^|[\s='\"])\.\./", command):
        return True  # ../ escapes cwd; where it lands is not worth resolving.
    for tok in _BASH_PATH_TOKEN_RE.findall(command):
        if not _path_overlaps(_norm_path(os.path.expanduser(tok)), roots):
            return True
    return False


def _handle_bash(tool_input: dict, cwd: str, mode: str) -> int:
    """Bash PreToolUse branch: intercept command lines that OPEN with a local
    search command (grep/rg/find/...) scanning an indexed repo — the dominant
    route by which the model does exactly the job the jcm routes cover, and
    the escape hatch a strict-denied Grep would otherwise funnel it into.

    Any other Bash command (builds, git, pipelines that merely filter output
    through grep) passes silently. Strict mode denies only the pure-search
    commands, and only when the command names no path outside every indexed
    root; everything else it can only nudge, because the hook cannot judge an
    arbitrary command line safely enough to block it.
    """
    command = tool_input.get("command", "")
    if not isinstance(command, str):
        return 0
    m = _BASH_SEARCH_RE.match(command)
    if not m:
        return 0
    cmd_word = m.group(1)
    deny = mode == "strict" and _BASH_SEARCH_COMMANDS[cmd_word]
    if deny and re.search(r"[|&;]", command):
        deny = False  # Pipeline/compound: the non-search half is real work.
    if deny and _bash_path_is_unresolvable(command):
        # ⚠⚠ A DENY REQUIRES A RESOLVABLE TARGET (#541). The overlap test below
        # judges `cwd` only, so a search whose target sits outside every indexed
        # root is caught by `_bash_targets_outside_roots` — but only when the
        # path is written literally. `grep ~/x.md` is allowed and
        # `grep $HOME/x.md` was denied: same destination, opposite verdicts.
        #
        # ⚠ This is not a regex gap to close. `_handle_bash` deliberately does
        # not parse shell, and the caution already exists everywhere else here:
        # `find` is never deniable, a pipeline is never denied, `../` returns
        # silent rather than resolving where it lands. This was the one shape
        # the same rule was not applied to.
        #
        # Downgrade to a nudge rather than going silent: a wrong nudge is a
        # sentence of text, a wrong deny is blocked work.
        deny = False
    if not deny and not _search_nudge_enabled():
        return 0  # Guaranteed silent — skip the store load below.
    roots = _indexed_source_roots()
    # Overlap is judged on cwd only — parsing arbitrary shell for target
    # paths is not worth it; relative-path searches from the repo root are
    # the dominant pattern.
    root = _norm_path(cwd or os.getcwd())
    if not _path_overlaps(root, roots):
        return 0  # Outside every indexed repo — allow silently.
    if _bash_targets_outside_roots(command, roots):
        return 0  # Search names a path jcm cannot serve — stay silent.
    return _emit_search_steering("Bash", f"this `{cmd_word}` command", deny=deny)


def run_pretooluse() -> int:
    """PreToolUse hook: steer Claude toward jCodemunch before native file tools.

    Dispatch per tool (Grep/Glob/Bash-search/Read) is described in the module
    docstring. Strength is set by ``_enforce_mode()`` (``JCODEMUNCH_ENFORCE``): the default
    ``advisory`` tier nudges the model but allows (so Read-before-Edit and the
    Grep fallback keep working); ``strict`` denies the same calls but only when
    an indexed-repo jcm route can serve them — targeted reads (``offset`` /
    ``limit``), tiny files, non-code files, and paths outside every indexed repo
    always pass; ``off`` is fully silent.

    Returns exit code (always 0 — errors are swallowed to avoid blocking).
    """
    data = _read_hook_payload()
    if data is None:
        return 0  # Unparseable or hostile payload shape → allow

    _note_transcript_root(data)

    mode = _enforce_mode()
    if mode == "off":
        return 0  # No nudge, no deny.

    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    tool_name = data.get("tool_name", "")

    # Search interception: Grep/Glob do a job a jcm route already covers,
    # entirely off the savings meter; Bash (grep/rg/find at the start of the
    # command line) is the dominant unhooked route for the same job — and
    # under strict mode, the escape hatch a denied Grep funnels the model
    # into. Defensive: the hook must never crash the agent's search, so
    # swallow anything unexpected.
    if tool_name in ("Grep", "Glob", "Bash"):
        try:
            cwd = data.get("cwd", "")
            if tool_name == "Bash":
                return _handle_bash(tool_input, cwd, mode)
            deny = mode == "strict"
            if not deny and not _search_nudge_enabled():
                return 0  # Guaranteed silent — skip the store load below.
            if not _path_overlaps(
                _grep_search_root(tool_input, cwd), _indexed_source_roots()
            ):
                return 0  # Nothing indexed / outside every repo → allow.
            pattern = (tool_input.get("pattern") or "").strip()
            what = f"this {tool_name}" + (f" for `{pattern}`" if pattern else "")
            return _emit_search_steering(tool_name, what, deny=deny)
        except Exception:
            logger.debug("%s interception failed", tool_name, exc_info=True)
            return 0

    # --- Read interception ---
    file_path = tool_input.get("file_path", "")
    if not isinstance(file_path, str) or not file_path:
        return 0

    # Check extension
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in _CODE_EXTENSIONS:
        return 0  # Not a code file → allow

    # Check size
    try:
        size = os.path.getsize(file_path)
    except (OSError, ValueError):
        return 0  # Can't stat (missing, or hostile path e.g. NUL byte) → allow

    if size < _MIN_SIZE_BYTES:
        return 0  # Small file → allow

    # Targeted reads (offset/limit set) are likely pre-edit — allow silently.
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return 0

    # Both tiers only speak when the file lives inside an indexed repo — jcm
    # can actually serve it there. Outside every indexed repo the recommended
    # tools would just error, teaching the model that jcm hints are unreliable.
    try:
        roots = _indexed_source_roots()
        if not roots or not _path_overlaps(_norm_path(file_path), roots):
            return 0
    except Exception:
        logger.debug("indexed-roots gate failed", exc_info=True)
        return 0  # Never block on a store hiccup.

    if mode == "strict":
        # Strict: deny the full-file read. Targeted reads (offset/limit) have
        # already passed above, so the pre-Edit escape hatch is always open.
        return _emit_pretooluse_deny(
            f"jCodemunch strict mode: this {size:,}-byte code file is in an "
            "indexed repo. Use get_file_outline + get_symbol_source instead "
            "of a full Read. For an exact-line pre-Edit read, pass "
            "offset/limit and it will pass. (JCODEMUNCH_ENFORCE=advisory "
            "for warn-only, =off to disable.)"
        )

    # Advisory: full-file exploratory read on a large code file — warn but allow.
    # Hard deny breaks the Edit workflow (Claude Code requires Read before Edit).
    return _emit_additional_context(
        "PreToolUse",
        f"jCodemunch hint: this is a {size:,}-byte code file. "
        "Prefer get_file_outline + get_symbol_source for exploration. "
        "Use Read only when you need exact line numbers for Edit.",
    )
