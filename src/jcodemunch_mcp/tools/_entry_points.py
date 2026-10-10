"""Framework-declared entry points, read from the index (#561, #562).

⚠⚠ **The authority already existed and had NO readers.** ``detect_framework``
runs at index time and ``profile_to_meta`` persists the profile's
``entry_point_patterns`` into ``context_metadata`` -- for Next.js that is
exactly ``src/app/**/route.ts``, ``page.tsx``, ``layout.tsx`` and
``middleware.ts``. A tree-wide search found the key written in one place and
read in none. Every consumer that needed to know "is this file a root?"
reproduced its own answer instead, and every one of those answers was Python:
``find_dead_code._ENTRY_POINT_FILENAMES`` is ``main.py`` / ``app.py`` /
``__main__.py`` and eleven siblings, with no JS entry in it at all.

⚠ So this module adds no knowledge. It is the read half of a write that was
already happening, which is the standing lesson in its usual costume: **ask the
authority instead of reproducing its logic.** A framework this does not cover
is fixed in ``framework_profiles.py``, once, and every consumer here inherits
it.

⚠⚠ **``matches()`` returning False is NOT "this is an ordinary module".** No
detected profile means no declaration was available, and a caller that reads
that as a negative finding is asserting something nobody measured. Callers
wanting the difference read ``profile_name`` -- ``None`` there means unknown.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntryPointSpec:
    """The entry-point declaration an index carries, if any."""

    profile_name: Optional[str]
    patterns: tuple[str, ...]

    @property
    def declared(self) -> bool:
        """True when a framework profile actually named some roots.

        ⚠ A detected profile with an empty pattern list is still ``False``
        here: it declared nothing, so it can exclude nothing.
        """
        return bool(self.patterns)

    def matches(self, file_path: str) -> bool:
        """True when ``file_path`` is a root the framework declares."""
        if not self.patterns:
            return False
        norm = file_path.replace("\\", "/").lstrip("./")
        base = norm.rsplit("/", 1)[-1]
        for pat in self.patterns:
            if pat.endswith("/"):
                # Directory prefix (`cmd/`, `internal/`). fnmatch never
                # matches these -- `fnmatch("cmd/main.go", "cmd/")` is False --
                # so a prefix test is the only reading under which the Gin
                # profile declares anything at all.
                if norm == pat.rstrip("/") or norm.startswith(pat):
                    return True
                continue
            if fnmatch.fnmatch(norm, pat):
                return True
            if "/" not in pat and norm == base and fnmatch.fnmatch(base, pat):
                # A bare filename declares the ROOT-LEVEL file, not every file
                # of that name anywhere in the tree: `main.py` must not make
                # `src/vendor/main.py` a root. Profiles that mean the nested
                # form spell it out (`src/middleware.ts` sits beside
                # `middleware.ts` in the Next profile for exactly this reason).
                return True
        return False


_EMPTY = EntryPointSpec(profile_name=None, patterns=())

# ⚠⚠ A pattern that matches every source file DECLARES NOTHING, and consuming
# it is far worse than the defect this module fixes. The Flask and FastAPI
# profiles shipped `"*.py"` in their entry-point lists for their whole lives,
# harmless only because nothing read the field (the NestJS profile has a
# comment saying so). Under fnmatch `*` crosses `/`, so a naive reader would
# have declared every Python file in a Flask repo a live root -- turning the
# dead-code tool into one that reports nothing, on a whole ecosystem, silently.
#
# ⚠ The catch-alls are removed at the source too. This guard stays because a
# profile is a list of literals anyone can extend, and the failure is invisible
# from the edit: adding `*.ts` to a profile looks like widening coverage and is
# actually switching a subsystem off.
_CATCH_ALL_PATTERNS = frozenset({"*", "**", "*.*", "**/*"})


def _is_catch_all(pattern: str) -> bool:
    """True for a pattern that cannot distinguish a root from an ordinary file."""
    pat = pattern.strip()
    if pat in _CATCH_ALL_PATTERNS:
        return True
    # `*.py`, `*.ts`, `**/*.tsx`: a bare extension glob over the WHOLE tree.
    # ⚠ Directory scope is what saves a pattern here: `routes/*.php` names one
    # directory and is a perfectly good declaration, while `**/*.php` names
    # every PHP file there is. Only an unscoped (or `**`-scoped) extension
    # glob is a catch-all.
    head, _, stem = pat.rpartition("/")
    if head not in ("", "**"):
        return False
    return stem.startswith("*.") and "*" not in stem[2:] and stem[2:].isalnum()


def entry_point_spec(index) -> EntryPointSpec:
    """Read the framework profile an index was built with.

    Returns ``_EMPTY`` when the index predates profile persistence, was built
    for a framework we do not profile, or carries a malformed block -- all of
    which are "we do not know", never "there are no entry points".
    """
    meta = getattr(index, "context_metadata", None) or {}
    block = meta.get("framework_profile")
    if not isinstance(block, dict):
        return _EMPTY
    raw = block.get("entry_point_patterns")
    if not isinstance(raw, (list, tuple)):
        return _EMPTY
    kept: list[str] = []
    for p in raw:
        if not isinstance(p, str) or not p:
            continue
        if _is_catch_all(p):
            logger.debug(
                "entry_point_spec: ignoring catch-all pattern %r from profile %r",
                p, block.get("name"),
            )
            continue
        kept.append(p)
    patterns = tuple(kept)
    name = block.get("name")
    return EntryPointSpec(
        profile_name=name if isinstance(name, str) and name else None,
        patterns=patterns,
    )


# .NET files IIS or MSBuild invoke directly: nothing imports them by design.
# By extension, unlike the name-only manifest list, because the extension is the
# host's contract. `.master`/`.ascx` stay out: they have real importers.
_HOST_INVOKED_SUFFIXES = (".aspx", ".ashx", ".asmx", ".csproj", ".vbproj", ".fsproj", ".nuspec")
_HOST_INVOKED_NAMES = frozenset({
    "global.asax", "directory.build.props", "directory.build.targets", "directory.packages.props",
})

# Reached by edges the index does not carry, so "no importer" proves nothing;
# they may still be dead, so the dead-code tools cap rather than root them.
# Build-consumed: project-file items. Runtime-loadable: LoadControl, a CMS's
# stored control path, MasterPageFile set in code.
_BUILD_CONSUMED_SUFFIXES = (".resx", ".xaml", ".xsd", ".wsdl", ".props", ".targets")
_RUNTIME_LOADABLE_SUFFIXES = (".ascx", ".master")


def _basename_lower(file_path: str) -> str:
    return file_path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def is_host_invoked(file_path: str) -> bool:
    base = _basename_lower(file_path)
    return base in _HOST_INVOKED_NAMES or base.endswith(_HOST_INVOKED_SUFFIXES)


def unseen_consumer(file_path: str) -> Optional[str]:
    """`build_consumed`, `runtime_loadable`, or None.

    A host-invoked file is a root and never capped, so `Directory.Build.props`
    (also a `.props`) returns None.
    """
    if is_host_invoked(file_path):
        return None
    base = _basename_lower(file_path)
    if base.endswith(_BUILD_CONSUMED_SUFFIXES):
        return "build_consumed"
    if base.endswith(_RUNTIME_LOADABLE_SUFFIXES):
        return "runtime_loadable"
    return None


# ---------------------------------------------------------------------------
# package.json: the files a manifest DECLARES as roots
# ---------------------------------------------------------------------------
#
# One reader (LEDGER L-102). It lived twice, in `find_dead_code` and in
# `get_dead_code_v2`, with the same logic, and neither read `scripts`: a server
# started by `"start": "node server.js"` has no importer by construction and
# was published dead at confidence 1.0.

_JS_ENTRY_SUFFIXES = (
    "", ".js", ".ts", ".mjs", ".cjs", ".mts", ".cts", ".jsx", ".tsx",
    "/index.js", "/index.ts", "/index.mjs", "/index.cjs",
)

# ⚠⚠ THE RULE IS AN ALLOWLIST. A wrong root removes a dead file from the report
# and from the delete preflight with no symptom (#569), and four review rounds
# each found a new spelling a denylist had not named: a flag's value, a
# subcommand word, `cd client &&`, `pnpm --filter=web exec`. So a command roots
# a file only when EVERY token in front of it is one this table knows:
#
#   [NAME=value ...] [npx [-y] | cross-env [NAME=value ...]] RUNNER [known flags] [exec word] PATH
#
# and PATH is path-shaped (it has a `/` or a `.`), so a bare word is never read
# as a file. An unknown wrapper, an unknown flag, a shell keyword, a redirect, a
# pipe or a changed directory makes the command declare nothing. A missed root
# leaves a live file reported, which the reader can see.
#
# There is no list of flags that execute no file (`node --check x.js`, `-e`,
# `--test`, `--run`, `--version`): they are simply absent from `flags`, and an
# unknown flag declares nothing. ⚠ Adding one of them to `flags` roots its
# argument.
#
# Per runner, because one spelling means different things (`--watch` takes a
# value for nodemon and none for node; `-r` is `--require` for node and
# `--reload` for deno):
#   flags     known flags that take no value in the next token (boolean, or `--flag=value`)
#   value     known flags that take their value in the NEXT token
#   preload   value flags whose value is a module loaded before the entry
#   exec_sub  words that come before the file and mean "execute it"
#   exec      flags whose value is a COMMAND the runner runs (nodemon --exec)
#   suffixes  what the runner appends when the argument names no file exactly
#   prefixes  flag prefixes known as a family (`--allow-net`, `--unstable-kv`)
_NODE_FLAGS = frozenset({
    "--watch", "--watch-preserve-output", "--inspect", "--inspect-brk", "--trace-warnings",
    "--trace-deprecation", "--throw-deprecation", "--no-warnings", "--no-deprecation",
    "--enable-source-maps", "--experimental-modules", "--experimental-json-modules",
    "--experimental-vm-modules", "--experimental-strip-types", "--experimental-specifier-resolution",
    "--harmony", "--expose-gc", "--preserve-symlinks", "--abort-on-uncaught-exception",
    "--max-old-space-size", "--stack-size",
})
_NODE_VALUE = frozenset({
    "-C", "--conditions", "--watch-path", "--inspect-port", "--title", "--input-type",
    "--env-file", "--unhandled-rejections",
})
_NODE_PRELOAD = frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"})
_NODE_ESM_PRELOAD = frozenset({"--import", "--loader", "--experimental-loader"})
# ts-node's pretty-printing flag is deliberately absent: tests/test_tectonic_temporal_signal.py
# reads that literal anywhere under src/ as a git format argument.
_TS_NODE_FLAGS = frozenset({
    # `--esm` is absent: ts-node 10.9.2 then resolves the entry as ESM, which appends no
    # extension, so the flag is unknown here and the command declares nothing.
    "--files", "-T", "--transpile-only", "--transpileOnly", "--swc", "-H", "--compiler-host",
    "--skip-project", "--skipProject", "--skip-ignore", "--prefer-ts-exts", "--log-error",
    "--emit", "--type-check", "--typeCheck",
})
_TS_NODE_VALUE = frozenset({
    "-P", "--project", "-C", "--compiler", "-O", "--compiler-options", "--compilerOptions",
    "-I", "--ignore", "--scope-dir", "--scopeDir", "-D", "--ignore-diagnostics", "--transpiler",
})
_NONE: frozenset = frozenset()
_JS = ("", ".js", "/index.js")  # node tries `.js`; it never tries `.ts` or `.mjs`
_TS = ("", ".ts", ".tsx", ".js", "/index.ts", "/index.js")


def _spec(
    flags=_NONE, value=_NONE, preload=_NONE, exec_sub=_NONE, exec=_NONE, suffixes=_JS, prefixes=(),
    equals=True, trailing=False,
):
    """One runner's grammar. ``equals``: it reads `--flag=value`. ``trailing``: it
    reads options after the script too (nodemon does; node does not), so an
    option there makes the invocation declare nothing."""
    return {
        "flags": flags, "value": value, "preload": preload,
        "exec_sub": exec_sub, "exec": exec, "suffixes": suffixes, "prefixes": tuple(prefixes),
        "equals": equals, "trailing": trailing,
    }


_NODE_SPEC = _spec(_NODE_FLAGS, _NODE_VALUE, _NODE_PRELOAD, exec_sub=frozenset({"inspect"}))
_TS_NODE_SPEC = _spec(_TS_NODE_FLAGS, _TS_NODE_VALUE, frozenset({"-r", "--require"}), suffixes=_TS)
_SCRIPT_RUNNERS = {
    "node": _NODE_SPEC,
    "nodejs": _NODE_SPEC,
    "electron": _spec(_NODE_FLAGS, _NODE_VALUE, _NODE_PRELOAD),
    "tsx": _spec(
        _NODE_FLAGS | {"--no-cache"}, _NODE_VALUE | {"--tsconfig"},
        _NODE_PRELOAD, exec_sub=frozenset({"watch"}), suffixes=_TS,
    ),
    "ts-node": _TS_NODE_SPEC,
    # ts-node-esm 10.9.2, run: `ts-node-esm ./server` fails (ERR_MODULE_NOT_FOUND); ESM
    # resolution appends nothing, so only the path as written is the entry.
    "ts-node-esm": _spec(_TS_NODE_FLAGS, _TS_NODE_VALUE, frozenset({"-r", "--require"}), suffixes=("",)),
    # ts-node-dev's own parser (minimist, `lib/bin.js` in 2.0.0) and not ts-node's
    # table: it knows no `--esm`, `--swc`, `--inspect` or camelCase spelling, and
    # a flag it does not know takes the next word or goes to node and fails.
    "ts-node-dev": _spec(
        frozenset({
            "--emit", "--files", "-T", "--transpile-only", "--prefer-ts-exts", "--prefer-ts", "--log-error",
            "--skip-project", "--skip-ignore", "-H", "--compiler-host", "-s", "--script-mode", "--scope",
            "--deps", "--all-deps", "--dedupe", "--fork", "--exec-check", "--debug", "--poll", "--respawn",
            "--notify", "--no-notify", "--tree-kill", "--clear", "--cls", "--exit-child",
            "--error-recompile", "--quiet", "--rs",
        }),
        frozenset({
            "-C", "--compiler", "-P", "--project", "-I", "--ignore", "-D", "--ignore-diagnostics",
            "-O", "--compiler-options", "--scopeDir", "--transpiler", "--deps-level", "--compile-timeout",
            "--ignore-watch", "--interval", "--debounce", "--watch", "--cache-directory",
        }),
        frozenset({"-r", "--require"}), suffixes=_TS,
    ),
    "nodemon": _spec(
        frozenset({
            "-V", "--verbose", "-q", "--quiet", "-L", "--legacy-watch", "--no-stdin", "--exitcrash",
            "--inspect", "--inspect-brk", "-C", "--on-change-only", "--spawn", "--no-update-notifier",
        }),
        frozenset({
            "-w", "--watch", "-e", "--ext", "-i", "--ignore", "--config", "-d", "--delay",
            "-s", "--signal", "-P", "--polling-interval",
        }),
        frozenset({"-r", "--require"}), exec=frozenset({"-x", "--exec"}),
        # nodemon 3.1.14, run. It hands `--flag=value` to node (`--ignore=x` fails there,
        # `--inspect=9231` works), reads its options on both sides of the script
        # (`server.js --cwd sub` runs `sub/server.js`), takes the first argument that
        # EXISTS as the script, and gives an extensionless one the first `-e` extension.
        # So: no `=` form, no option after the script, and the path exactly as written.
        suffixes=("",), equals=False, trailing=True,
    ),
    "babel-node": _spec(
        frozenset({"--inspect", "--inspect-brk"}),
        frozenset({"--presets", "--plugins", "--extensions", "-x", "--config-file", "--ignore", "--only", "--env-name", "--root-mode"}),
        frozenset({"-r", "--require"}),
    ),
    # bun takes a config file only as `--config=FILE` and deno v8 flags only as
    # `--v8-flags=...` (bun 1.4.2 and deno 2.9.6, run): the word after the bare
    # flag is the file they execute, so both are no-value flags here.
    "bun": _spec(
        frozenset({"--watch", "--hot", "--smol", "--bun", "-b", "--silent", "--inspect", "--inspect-brk", "--inspect-wait", "--no-install", "-c", "--config"}),
        frozenset({"--env-file", "-d", "--define", "-l", "--loader", "--tsconfig-override", "--port", "--conditions"}),
        frozenset({"-r", "--preload", "--require", "--import"}),
        exec_sub=frozenset({"run"}), suffixes=("",),
    ),
    "deno": _spec(
        frozenset({
            "-A", "--allow-all", "--watch", "--no-check", "--check", "--unstable", "--no-lock", "--no-remote",
            "--no-npm", "--cached-only", "-q", "--quiet", "--no-prompt", "-r", "--reload", "--frozen",
            "--inspect", "--inspect-brk", "--v8-flags",
        }),
        frozenset({"-c", "--config", "--import-map", "--lock", "--cert", "--location", "--seed", "-L", "--log-level"}),
        exec_sub=frozenset({"run"}), suffixes=("",),
        prefixes=("--allow-", "--deny-", "--unstable-"),
    ),
    "pm2-runtime": _spec(exec_sub=frozenset({"start"})),
}
_RUNNABLE_SUFFIXES = (".js", ".ts", ".mjs", ".cjs", ".mts", ".cts", ".jsx", ".tsx")
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# The only operators a command may hold. Anything else the lexer returns as
# punctuation (a pipe, a redirect, `&`, a subshell) makes it declare nothing.
_SEQUENCE_OPERATORS = frozenset({"&&", "||", ";"})
# After one of these the rest of the command runs somewhere else.
_CHDIR_COMMANDS = frozenset({"cd", "pushd", "popd", "chdir"})
# A command holding one of these is shell grammar this reader does not model.
_SHELL_WORDS = frozenset({
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac",
    "{", "}", "!", "function", "builtin", "command", "eval", "source", ".", "set", "export",
    "exit", "exec", "return", "alias", "unalias", "trap", "unset", "shift", "break", "continue",
})


def _resolve_script_path(
    pkg_dir: str, token: str, source_files: frozenset, own_main: bool = True, suffixes: tuple = _JS
) -> Optional[str]:
    """The indexed file a path-shaped script argument names, relative to its package, or None.

    A directory argument (`node ./src`, `node .`) loads that directory's
    `package.json` `main` when it has one, which the field reader already
    handles. So a directory holding a `package.json` resolves to nothing here,
    except the script's own package when its manifest names no `main`
    (``own_main`` False): the runner loads `index.*` there.
    """
    token = token.replace("\\", "/")
    joined = posixpath.normpath(posixpath.join(pkg_dir, token)) if pkg_dir else posixpath.normpath(token)
    if joined.startswith("..") or joined.startswith("/"):
        return None
    if joined == ".":
        joined = ""
    if joined:
        if "" in suffixes and joined in source_files:
            return joined
        # `ts-node ./server` runs `server.js` when `server.ts` is beside it, ts-node-dev
        # runs `server.ts`, and a flag flips ts-node: two candidates declare nothing.
        hits = [joined + s for s in suffixes if s and not s.startswith("/") and joined + s in source_files]
        if joined + ".json" in source_files and (not hits or ".ts" in suffixes):
            # node tries `x.js`, then `x.json`, then `x/index.js`; ts-node tries
            # `x.json` before `x.ts`. A same-stem `.json` there is what runs.
            return None
        if hits:
            return hits[0] if len(hits) == 1 else None
    if (f"{joined}/package.json" if joined else "package.json") in source_files:
        if own_main or joined != pkg_dir:
            return None
    hits = [t for t in ((joined + s).lstrip("/") for s in suffixes if s.startswith("/")) if t in source_files]
    return hits[0] if len(hits) == 1 else None


# ⚠⚠ The command text is an allowlist too. Six review rounds each found shell
# syntax a list of exclusions read differently from a shell (`FOO=1 cd sub`,
# `\cd sub`, a `#` comment, a quoted `"&&"`, a newline, `\"`). So a command is
# read only when EVERY character is one of these, which leaves no variable, glob,
# tilde, comment, redirect, subshell, newline or tab to interpret.
_COMMAND_TEXT = re.compile(r"[A-Za-z0-9_ ./:=@,+\-&|;\"'\\]*")
# Inside quotes, nothing that could be an operator.
_QUOTED = re.compile(r"\"([^\"]*)\"|'([^']*)'")
_QUOTED_TEXT = re.compile(r"[A-Za-z0-9_ ./:@,+\-]*")
# A backslash is a path separator and nothing else: inside a word, before a
# name character. At the start of a word (`\cd`) or before a quote, a space or
# an operator it is an escape, and the command is not read.
_BACKSLASH_ESCAPE = re.compile(r"(?:^|[\s\"'&|;])\\|\\(?![A-Za-z0-9_.])")


def _script_segments(command: str) -> list[list[str]]:
    """The command as simple commands joined by `&&`, `||` or `;`, or nothing.

    A command that cannot be read whole declares nothing: a character outside
    `_COMMAND_TEXT`, a backslash that is not a path separator, a quoted string
    that holds anything but plain text, an unbalanced quote, or an operator
    other than the three above (`|`, `&`). Shell keywords are checked by the
    caller, on the program of each segment.
    """
    if not _COMMAND_TEXT.fullmatch(command) or _BACKSLASH_ESCAPE.search(command):
        return []
    for match in _QUOTED.finditer(command):
        if not _QUOTED_TEXT.fullmatch(match.group(1) or match.group(2) or ""):
            return []
    lexer = shlex.shlex(command.replace("\\", "/"), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEQUENCE_OPERATORS:
            segments.append([])
        elif token and set(token) <= set("();<>|&"):
            return []
        else:
            segments[-1].append(token)
    return [s for s in segments if s]


def _program(segment: list[str]) -> str:
    """The word a simple command runs, past `NAME=value` assignments, by its basename."""
    for token in segment:
        if not _ENV_ASSIGNMENT.match(token):
            return token.rsplit("/", 1)[-1]
    return ""


def _strip_prefix(tokens: list[str]) -> Optional[list[str]]:
    """The tokens from the program on, past `NAME=value`, `npx [-y]` and `cross-env`; None if unknown."""
    i = 0
    while i < len(tokens) and _ENV_ASSIGNMENT.match(tokens[i]):
        i += 1
    if i < len(tokens) and tokens[i] == "npx":
        i += 1
        while i < len(tokens) and tokens[i] in ("-y", "--yes"):
            i += 1
        if i < len(tokens) and tokens[i].startswith("-"):
            return None  # `npx --workspace=web ...` runs somewhere else
    if i < len(tokens) and tokens[i] in ("cross-env", "cross-env-shell"):
        i += 1
        while i < len(tokens) and _ENV_ASSIGNMENT.match(tokens[i]):
            i += 1
    return tokens[i:]


def _script_entries(
    command: str, pkg_dir: str, source_files: frozenset, own_main: bool = True, _depth: int = 0
) -> set[str]:
    """Files one `scripts` command runs: per runner invocation, its preloads and its entry file.

    The entry is the first plain argument, when it is path-shaped and resolves
    to an indexed file. Arguments after it belong to the program (`node
    build.js input.js`) and declare nothing; for a runner that reads options
    after the script (nodemon), an option there declares nothing. See the allowlist note above: an
    unknown token in front of the entry makes the invocation declare nothing.
    """
    found: set[str] = set()
    segments = _script_segments(command)
    if any(_program(segment) in _SHELL_WORDS for segment in segments):
        return found  # shell grammar this reader does not model
    for segment in segments:
        if _program(segment) in _CHDIR_COMMANDS:
            break  # everything after runs somewhere else (`FOO=1 cd sub`, `\cd sub` too)
        tokens = _strip_prefix(segment)
        if not tokens:
            continue
        spec = _SCRIPT_RUNNERS.get(tokens[0].rsplit("/", 1)[-1])
        if spec is None:
            continue
        args = tokens[1:]
        preloads: set[str] = set()
        entry_token: Optional[str] = None
        exec_command: Optional[str] = None
        declares = True
        exec_sub_open = bool(spec["exec_sub"])
        j = 0
        while j < len(args) and declares:
            arg = args[j]
            j += 1
            if arg == "--" and entry_token is not None:
                break  # the rest is the script's
            if entry_token is not None and arg.startswith("-"):
                declares = False  # an option after the script: the runner reads it, and it may move the entry
                continue
            if arg.startswith("-") and arg != "-":
                flag, eq, value = arg.partition("=")
                if eq and not spec["equals"]:
                    declares = False  # this runner hands `--flag=value` to node
                    continue
                takes_value = flag in spec["value"] or flag in spec["preload"] or flag in spec["exec"]
                if takes_value:
                    if not eq:
                        if j >= len(args) or args[j].startswith("-"):
                            declares = False  # a value flag with no value: not a command we can read
                            continue
                        value = args[j]
                        j += 1
                    if flag in spec["exec"]:
                        exec_command = value
                    elif flag in spec["preload"]:
                        # `-r esm`, `--import tsx`: a bare name is a package in
                        # node_modules, never `./esm`. Only a relative path is ours.
                        if value.startswith(("./", "../")):
                            # node resolves `--import`/`--loader` as ESM, which appends
                            # nothing (`node --import ./b x.js` fails, node 24, run).
                            # tsx resolves `--import` itself but not `--loader` (run).
                            esm = flag in _NODE_ESM_PRELOAD and (spec["suffixes"] == _JS or flag != "--import")
                            hit = _resolve_script_path(
                                pkg_dir, value, source_files, own_main, ("",) if esm else spec["suffixes"]
                            )
                            if esm and not hit:
                                declares = False  # unindexed, or the command fails at start: not decided
                                continue
                            if hit:
                                preloads.add(hit)
                    elif not eq and value.endswith(_RUNNABLE_SUFFIXES) and _resolve_script_path(
                        pkg_dir, value, source_files, own_main, ("",)
                    ):
                        declares = False  # is that a value, or the entry behind a flag we misread?
                elif flag not in spec["flags"] and not flag.startswith(spec["prefixes"] or ("\0",)):
                    declares = False  # an unknown flag: it may take a value, or change the directory
                continue
            if entry_token is not None:
                continue  # a plain argument of the script
            if exec_sub_open:
                exec_sub_open = False
                if arg in spec["exec_sub"]:
                    continue
            if "/" in arg or "." in arg:
                entry_token = arg
            else:
                declares = False  # a bare word is a subcommand or a script's name, never a path
            if not spec["trailing"]:
                break
        if not declares:
            continue
        if exec_command is not None:
            # nodemon appends its script argument to the command it was told to run.
            if _depth < 2:
                run = exec_command if entry_token is None else f"{exec_command} {shlex.quote(entry_token)}"
                found |= _script_entries(run, pkg_dir, source_files, own_main, _depth + 1)
            continue
        found |= preloads
        if entry_token is not None:
            hit = _resolve_script_path(pkg_dir, entry_token, source_files, own_main, spec["suffixes"])
            if hit:
                found.add(hit)
    return found


def package_json_entries(index, store, owner: str, repo_name: str) -> set[str]:
    """Source files a ``package.json`` declares as roots.

    ``main`` / ``module`` / ``browser`` / ``exports`` / ``bin`` name the file a
    consumer loads; ``scripts`` names the file a runner executes
    (:func:`_script_entries`). JS equivalent of the Python ``app.py`` /
    ``main.py`` filename rule, read from the manifest instead of guessed.
    """
    entries: set[str] = set()
    source_files = frozenset(index.source_files)
    for f in index.source_files:
        fn = f.replace("\\", "/").rsplit("/", 1)[-1]
        if fn != "package.json":
            continue
        content = store.get_file_content(owner, repo_name, f)
        if not content:
            continue
        try:
            pkg = json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(pkg, dict):
            continue
        candidates: list[str] = []
        for key in ("main", "module", "browser"):
            v = pkg.get(key)
            if isinstance(v, str):
                candidates.append(v)
        # `exports` can be a string, a dict of subpaths, or a conditional dict.
        exports = pkg.get("exports")
        if isinstance(exports, str):
            candidates.append(exports)
        elif isinstance(exports, dict):
            def _walk_exports(node):
                if isinstance(node, str):
                    candidates.append(node)
                elif isinstance(node, dict):
                    for v in node.values():
                        _walk_exports(v)
            _walk_exports(exports)
        # `bin` can be a string or a {name: path} dict.
        bins = pkg.get("bin")
        if isinstance(bins, str):
            candidates.append(bins)
        elif isinstance(bins, dict):
            candidates.extend(v for v in bins.values() if isinstance(v, str))

        pkg_dir = f.replace("\\", "/").rsplit("/", 1)[0] if "/" in f else ""
        for cand in candidates:
            cand = cand.lstrip("./").replace("\\", "/")
            joined = f"{pkg_dir}/{cand}" if pkg_dir else cand
            joined = joined.lstrip("/")
            if joined in source_files:
                entries.add(joined)
                continue
            for ext in _JS_ENTRY_SUFFIXES:
                trial = joined + ext
                if trial in source_files:
                    entries.add(trial)
                    break

        scripts = pkg.get("scripts")
        if isinstance(scripts, dict):
            own_main = isinstance(pkg.get("main"), str) and bool(pkg.get("main"))  # node reads `main` only
            for command in scripts.values():
                if isinstance(command, str):
                    entries |= _script_entries(command, pkg_dir, source_files, own_main)
    return entries
