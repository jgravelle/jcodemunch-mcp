"""Shared plumbing for the workflow hooks (docs/workflows/DESIGN.md section 4).

purpose:  read the hook JSON, locate the repo, run a command under a budget,
          and report in the three shapes the design allows: block (exit 2 with
          the reason on stderr), warn (exit 0 with additionalContext), pass.
invokes:  nothing on its own
produces: nothing on its own
refuses:  nothing on its own

A hook past its budget WARNS and names what it skipped (D7); it never
silently passes and never blocks on its own slowness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Windows consoles default to cp1252; an ARCHAEOLOGY row or a harness verdict
# carries characters outside it, and a hook that dies encoding its own reason
# blocks with a traceback instead of the reason (Standing lesson: encoding=).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # a stream with no reconfigure (a pipe replaced by the runner) keeps its encoding

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STATE = REPO / ".claude" / "state"
EVIDENCE = STATE / "evidence"
# W-43: ONE table of paths, with the questions each answers, from which the
# three hook lists are projected. The lists grew apart with three memberships
# (a hooks-only commit skipped the fast tier while the checklist called the
# same edit a code change, and a hook edit left the D5 stamp valid).
#   stamp    - the full tier's verdict depends on it (D5 tree identity)
#   fast     - a commit touching it runs the fast tier first (H1)
#   redgreen - a change under it needs a red/green pair (checklist row 1)
#   bench    - a change under it needs the bench tier (checklist row 10)
# `.claude/hooks/` moves the stamp and needs a pair, and does NOT trigger the
# fast tier: harness/tiers.json's fast list carries no hook test, so that run
# would judge nothing about the change; the full tier runs them.
QUESTIONS = frozenset({"stamp", "fast", "redgreen", "bench"})
PATH_TABLE: dict[str, frozenset[str]] = {
    "src/": frozenset({"stamp", "fast", "redgreen"}),
    "tests/": frozenset({"stamp", "fast", "redgreen"}),
    "harness/": frozenset({"stamp", "fast", "redgreen", "bench"}),
    "scripts/": frozenset({"stamp", "fast", "redgreen"}),
    "benchmarks/": frozenset({"stamp", "redgreen", "bench"}),
    "benchmarks/harness/": frozenset({"fast"}),
    ".github/": frozenset({"stamp", "fast"}),
    "pyproject.toml": frozenset({"stamp"}),
    "uv.lock": frozenset({"stamp"}),
    ".claude/hooks/": frozenset({"stamp", "redgreen"}),
    # The dispatcher's latency Floors read it; under src/, so stamp/fast/redgreen
    # already hold through the "src/" row, and this row adds the bench question.
    "src/jcodemunch_mcp/server.py": frozenset({"bench"}),
}


def paths_for(question: str) -> tuple[str, ...]:
    """The paths that answer one question, in table order."""
    if question not in QUESTIONS:
        raise ValueError(f"unknown question {question!r}; one of {sorted(QUESTIONS)}")
    return tuple(p for p, qs in PATH_TABLE.items() if question in qs)


# What the full tier's verdict depends on (tree identity for the D5 stamp).
TIER_PATHS = paths_for("stamp")


def _rebind_repo(cwd: str | None) -> bool:
    """Point REPO/STATE/EVIDENCE at the checkout the SESSION is in.

    Claude Code runs a project hook with `$CLAUDE_PROJECT_DIR` fixed, so a
    session working in a `git worktree` of this repo got the explicit
    scripts (run_full.py, dod_checklist.py resolve from their own file)
    and NONE of the automatic ones: the /fix-issue probe's reintroducing
    commit passed pre_commit silently (FINDINGS W-30). The payload's `cwd`
    names the real tree; if it is a checkout of this repo, use it.

    Returns True when REPO now names that checkout (already did, or was
    rebound), False when `cwd` is not a checkout carrying `.claude/hooks`.
    """
    global REPO, STATE, EVIDENCE
    if not cwd:
        return False
    try:
        top = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    if not top:
        return False
    top_path = Path(top).resolve()
    if top_path == REPO:
        return True
    if not (top_path / ".claude" / "hooks").exists():
        return False
    REPO = top_path
    STATE = REPO / ".claude" / "state"
    EVIDENCE = STATE / "evidence"
    # The hooks import these names at module level; rebind their copies too.
    main = sys.modules.get("__main__")
    for name, value in (("REPO", REPO), ("STATE", STATE), ("EVIDENCE", EVIDENCE)):
        if main is not None and hasattr(main, name):
            setattr(main, name, value)
    return True


def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}
    _rebind_repo(payload.get("cwd"))
    return payload


def tool_command(payload: dict) -> str:
    ti = payload.get("tool_input") or {}
    return str(ti.get("command") or "")


_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\s*$", re.S | re.M)


def strip_heredocs(cmd: str) -> str:
    """Drop heredoc BODIES so prose that mentions a verb is not the verb.

    A FINDINGS entry piped through `python - <<'EOF'` carried the words
    `git commit` and tripped H1 (W-19). Quoted strings are kept: a commit
    message naming `git commit` sits beside a real one anyway, and the
    deny guard deliberately does not strip anything.
    """
    return _HEREDOC_RE.sub("<<HEREDOC>>", cmd)


def split_segments(cmd: str) -> list[str]:
    """Split a shell line on `&&`, `||` and `;` OUTSIDE quotes.

    A commit message with a semicolon in it is one segment, not three
    (the third H1 probe of the day refused its own commit over one).
    """
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            elif c == "\\" and i + 1 < len(cmd):
                buf.append(cmd[i + 1])
                i += 1
        elif c in ("'", '"'):
            quote = c
            buf.append(c)
        elif cmd.startswith(("&&", "||"), i):
            out.append("".join(buf))
            buf = []
            i += 1
        elif c == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


#: Command words that change a shell's or PowerShell's working directory.
_CWD_WORDS = frozenset(
    {"cd", "chdir", "pushd", "popd", "set-location", "push-location", "pop-location", "sl"}
)
#: `NAME=value`, and PowerShell's `$env:NAME=value` (this box's prescribed
#: `$env:GITHUB_TOKEN=""` prefix must not read as an expansion, review round 5).
_ASSIGNMENT_RE = re.compile(r"^(?:\$env:)?[A-Za-z_][A-Za-z0-9_]*=")
_GIT_LOCATION_RE = re.compile(r"(?i)^(?:\$env:)?GIT_(?:DIR|WORK_TREE)=")
#: `f()`, `f(){`, `f()` followed by a body word: a function defined on the line.
_FUNCTION_DEF_RE = re.compile(r"^[\w.:-]+\(\)\{?$|^\(\)\{?$")
_GIT_BASH_DRIVE_RE = re.compile(r"^/([A-Za-z])(/|$)")


def _words(seg: str) -> list[str] | None:
    """A segment's words as the shell splits them, or None when they cannot be split."""
    import shlex

    try:
        return shlex.split(seg, posix=True)
    except ValueError:
        return None


def _changes_dir(seg: str) -> bool:
    """Does this segment's COMMAND WORD change directory, or might it?

    Judged on the command word after shell quote removal, so `\\cd`, `c''d`
    and `c\\d` all read as `cd`, while a quoted title that mentions cd does
    not. Past leading assignments and a `(`/`{`, and through `builtin`,
    `command` and `exec`. A word the shell would expand (`$`, a backtick) or
    a segment that cannot be split is UNKNOWN, which reads as True: the
    caller refuses rather than guesses.
    """
    words = _words(seg)
    if words is None:
        return True
    # ANY word, not only the command word: `if cd A; then`, `time cd A`,
    # `! cd A` and PowerShell `& { cd A }` all change directory (review round 3).
    # A quoted title is one word after quote removal, so it never equals `cd`.
    for w in words:
        # A redirect glued to the word (`cd>/dev/null A`) still runs `cd`, and
        # PowerShell's `cd..` / `cd\` are functions that change location (round 4).
        base = re.sub(r"[<>].*$", "", w.lstrip("({&!")).lower()
        if base in _CWD_WORDS or re.match(r"cd[./\\]", base):
            return True
    i = 0
    while i < len(words) and (words[i].lstrip("({&!") == "" or _ASSIGNMENT_RE.match(words[i])):
        i += 1
    if i >= len(words):
        return False
    word = words[i].lstrip("({&!")
    # Words that run text this function cannot read: unknown, so a change.
    return "$" in word or "`" in word or word in ("eval", "source", ".")


def any_dir_change(cmd: str) -> bool:
    """Does any segment of `cmd` change directory, or might it (see `_changes_dir`)?"""
    return any(_changes_dir(s) for s in split_segments(cmd))


def command_dir(cmd: str, pattern: re.Pattern[str], base: str) -> tuple[Path | None, str | None]:
    """Where the first segment matching `pattern` runs: `(dir, None)`, `(None, None)` or `(None, reason)`.

    The Bash tool resets its cwd to the session folder on every call, so a
    command that works in a worktree says so with `cd <dir> && ...` while the
    payload's `cwd` still names the session folder (jjg, 2026-10-19: pre_pr
    reads the tree `gh pr create` runs in). `(None, None)` means nothing
    changes directory, so `base` is where it runs. `(None, reason)` means a
    directory change this function cannot follow: a gate refuses on it,
    because a guess would fall back to a tree the command does not run in.

    Followed: a chain of plain `cd DIR` segments joined by `&&` or `;`, each
    relative one resolved against the previous, with `-P`/`-L`/`--` and the
    Git Bash `/c/...` drive spelling. Refused: a newline, `||`, a subshell or
    group, command substitution, `pushd`/`popd`/`chdir`/`Set-Location`/`sl`,
    a prefixed or `builtin` cd, `cd -` or a bare `cd`, and any directory
    change between two segments that match `pattern`.
    """
    segs = split_segments(cmd)
    first = next((i for i, s in enumerate(segs) if pattern.search(s)), None)
    if first is None:
        return None, None
    # ALLOWLIST, not a denylist (review round 4): the segment that matches must
    # run `gh` itself. `bash -c`, `sh -c`, `pwsh -c`, `cmd /c`, `env -C` and an
    # `echo` that merely names the command all run it elsewhere, or not at all,
    # and four rounds of naming spellings each found one the list missed.
    words = _words(segs[first]) or []
    i = 0
    while i < len(words) and _ASSIGNMENT_RE.match(words[i]):
        i += 1
    if i >= len(words) or words[i].lower() not in ("gh", "gh.exe"):
        return None, "gh runs inside another program, or the text only names it"
    # `--head`/`-H` and git's own location variables open the PR from a branch
    # or repository other than the tree judged here (review round 5).
    if any(w == "-H" or w.startswith(("-H", "--head")) for w in words[i + 1 :]):
        return None, "`--head`/`-H` names a branch other than this tree's"
    every = [w for s in segs[: first + 1] for w in (_words(s) or [])]
    if any(_GIT_LOCATION_RE.match(w) for w in every):
        return None, "`GIT_DIR`/`GIT_WORK_TREE` point gh at another repository"
    # A function defined on the line can change directory through a variable
    # no word-level check sees (`f(){ $X A; }; X=cd; f`), even one named gh.
    if any(_FUNCTION_DEF_RE.match(w) for w in every) or any(
        (_words(s) or [""])[0].lower() == "function" for s in segs[: first + 1]
    ):
        return None, "it defines a function on the same line"
    if not any(_changes_dir(s) for s in segs):
        return None, None
    if "\n" in cmd or "\r" in cmd:
        return None, "the command spans lines and changes directory; pass the PR body with --body-file"
    head = cmd[: cmd.find(segs[first])] if segs[first] in cmd else cmd
    for token, why in (("||", "`||`"), ("(", "a subshell"), ("{", "a group"), ("`", "a substitution"), ("$(", "a substitution")):
        if token in head or segs[first].startswith(token):
            return None, f"it changes directory inside {why}"
    if _changes_dir(segs[first]):
        return None, "the segment that runs it changes directory itself"
    later = segs[first + 1 :]
    if any(_changes_dir(s) for s in later) and any(pattern.search(s) for s in later):
        return None, "it changes directory between two matching commands"
    where: Path | None = None
    for seg in segs[:first]:
        if not _changes_dir(seg):
            continue
        words = _words(seg)
        if not words or words[0] != "cd":
            return None, f"`{seg[:60]}` may change directory (a keyword, an expansion or a command it cannot read)"
        args = [w for w in words[1:] if w not in ("-P", "-L", "--")]
        if len(args) != 1 or args[0] == "-":
            return None, f"`{seg[:60]}` names no single directory"
        arg = os.path.expanduser(args[0])
        if os.name == "nt":
            arg = _GIT_BASH_DRIVE_RE.sub(lambda m: m.group(1).upper() + ":/", arg)
        p = Path(arg)
        where = p if p.is_absolute() else (where or Path(base)) / p
    return where, None


def tool_path(payload: dict) -> Path | None:
    ti = payload.get("tool_input") or {}
    p = ti.get("file_path") or ti.get("path")
    return Path(p).resolve() if p else None


def under(path: Path | None, *parts: str) -> bool:
    if path is None:
        return False
    try:
        rel = path.relative_to(REPO)
    except ValueError:
        return False
    return rel.parts[: len(parts)] == parts


def git(*args: str, timeout: int = 20) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return r.stdout


def git_env(env: dict, *args: str, timeout: int = 60) -> str:
    """`git` under an explicit environment (tree_id's throwaway GIT_INDEX_FILE)."""
    r = subprocess.run(
        ["git", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, ["git", *args], r.stdout, r.stderr)
    return r.stdout


UNREADABLE_PREFIX = "unreadable-"


def tree_id() -> str:
    """Identity of the working tree's CONTENT under the tier paths.

    A full-tier run is valid for exactly this identity, whatever its age (D5).
    """
    # The identity covers what the full tier's verdict DEPENDS on: the code
    # roots, the packaging and the harness. A CHANGELOG line, a PR body draft
    # or a docs/ edit after the run does not invalidate it; committing the
    # same content does not either (W-21, #675). The harness's own footprint
    # (pytest-cov's `.coverage.<host>.<pid>` files, the hook state) never
    # counts (W-13).
    # ⚠ #675: this hashed `ls-tree HEAD` + `git diff HEAD` + the untracked
    # listing, and committing moves a change from the second string into the
    # first, so the claim above was false for its whole life. The working
    # copy is staged into a THROWAWAY index (a copy of the real one, so the
    # stat cache spares re-hashing unchanged files) and written as a tree:
    # blob ids of the content, identical before and after a commit, with
    # untracked files included. The real index is never written.
    # Residual: a doc edit CAN flip a doc-reading test (CLAUDE.md size); the
    # PR gate is the authority for that, this hook is the early one.
    # ⚠ A tier path absent from both the working copy and the index is a
    # pathspec `git add` refuses OUTRIGHT, adding nothing; pass only live ones.
    # ⚠ Any git failure yields an id no stamp can hold (fail closed): a
    # constant fallback would let two failed reads certify each other.
    # ⚠⚠ The copy KEEPS the index's mtime (`copy2`, never `copyfile`). Git
    # re-reads a file whose stat matches its entry only when that entry is
    # "racily clean" (mtime >= the INDEX FILE's mtime); a copy stamped "now"
    # makes every entry look settled, so a same-size edit inside the racy
    # window is trusted from the stat cache and the id names STALE content.
    # A draft that used `copyfile` named stale content in 3 of 25 looped runs.
    # ⚠ It also writes loose blobs/trees for uncommitted content into
    # `.git/objects` (unreferenced, gc collects them); the INDEX is untouched.
    scratch = None
    try:
        fd, scratch = tempfile.mkstemp(prefix="tree-id-", suffix=".index")
        os.close(fd)
        real_index = git_env(
            dict(os.environ), "rev-parse", "--path-format=absolute", "--git-path", "index"
        ).strip()
        if real_index and os.path.exists(real_index):
            shutil.copy2(real_index, scratch)
        else:
            os.unlink(scratch)  # no index yet: git builds one from nothing
        env = {**os.environ, "GIT_INDEX_FILE": scratch}
        indexed = git_env(env, "ls-files", "--", *TIER_PATHS).splitlines()
        live = [
            p
            for p in TIER_PATHS
            if (REPO / p).exists() or any(f == p or f.startswith(p) for f in indexed)
        ]
        if live:
            git_env(env, "add", "-A", "--", *live)
        tree = git_env(env, "write-tree").strip()
        listing = git_env(env, "ls-tree", "-r", tree, "--", *TIER_PATHS)
    except (OSError, subprocess.SubprocessError) as exc:
        # Name the cause: a caller reporting only a mismatched id would send
        # the reader looking for a tree that moved.
        detail = (getattr(exc, "stderr", None) or str(exc)).strip()
        print(f"tree_id: could not read the tree under {REPO}: {detail}", file=sys.stderr)
        return UNREADABLE_PREFIX + os.urandom(8).hex()
    finally:
        if scratch and os.path.exists(scratch):
            try:
                os.unlink(scratch)
            except OSError:
                pass  # a leaked temp index costs disk, never the verdict
    content = "\n".join(
        ln
        for ln in listing.splitlines()
        if not ln.split("\t", 1)[-1].rsplit("/", 1)[-1].startswith(".coverage")
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def content_tree() -> str:
    """A git tree id over EVERY tracked file's working-copy content (#715).

    `tree_id` answers what the full tier depends on, so a fix that lives
    outside the code roots (a docs correction guarded by a new test) has one
    tier tree for its red and its green run by construction. This id covers
    those files too, and it is a real tree object, so `tree_diff_paths` can
    name what moved between two runs. Tracked files only (`add -u`): the
    harness's untracked footprint (`.coverage.*`) never moves it.
    Same throwaway-index and fail-closed rules as `tree_id`.
    """
    scratch = None
    try:
        fd, scratch = tempfile.mkstemp(prefix="content-tree-", suffix=".index")
        os.close(fd)
        real_index = git_env(
            dict(os.environ), "rev-parse", "--path-format=absolute", "--git-path", "index"
        ).strip()
        if real_index and os.path.exists(real_index):
            shutil.copy2(real_index, scratch)  # copy2: the racy-clean rule in tree_id
        else:
            os.unlink(scratch)
        env = {**os.environ, "GIT_INDEX_FILE": scratch}
        git_env(env, "add", "-u")
        return git_env(env, "write-tree").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        detail = (getattr(exc, "stderr", None) or str(exc)).strip()
        print(f"content_tree: could not read the tree under {REPO}: {detail}", file=sys.stderr)
        return UNREADABLE_PREFIX + os.urandom(8).hex()
    finally:
        if scratch and os.path.exists(scratch):
            try:
                os.unlink(scratch)
            except OSError:
                pass  # a leaked temp index costs disk, never the verdict


def tree_diff_paths(a: str, b: str) -> list[str] | None:
    """Paths that differ between two tree ids; None when git cannot compare them.

    None is UNKNOWN, never "nothing moved": `tree_diff_paths(x, x)` is `[]`.
    """
    try:
        out = git_env(dict(os.environ), "diff-tree", "-r", "--name-only", a, b)
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.splitlines() if p]


class Budget:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.start = time.monotonic()

    def left(self) -> float:
        return self.seconds - (time.monotonic() - self.start)


def run_budgeted(
    cmd: list[str] | str, budget: Budget, *, shell: bool = False, env=None
):
    """Run under what is left of the budget. Returns (rc, output) or (None, '') on timeout."""
    left = budget.left()
    if left <= 1:
        return None, ""
    # W-42: `subprocess.run(timeout=)` kills the CHILD and then waits on the
    # pipes, which a grandchild (`uv run` -> `python -m harness`) still holds;
    # on Windows that wait lasted as long as the harness did, the hook overran
    # the runner's backstop, the runner killed it, and the commit proceeded
    # with no verdict. The deadline has to take the whole tree down.
    popen_kw: dict = {}
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    p = subprocess.Popen(
        cmd,
        cwd=REPO,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or {**os.environ, "PYTHONIOENCODING": "utf-8"},
        **popen_kw,
    )
    try:
        out, err = p.communicate(timeout=left)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=DRAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Practice 2: a tree that outlives the kill must be visible.
            sys.stderr.write(
                f"run_budgeted: pid {p.pid} still holds its pipes {DRAIN_TIMEOUT} s after the tree kill\n"
            )
        return None, ""
    return p.returncode, (out or "") + (err or "")


def _kill_tree(p: subprocess.Popen) -> None:
    """Kill a process and everything it started, on both platforms."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
            capture_output=True, timeout=KILL_TIMEOUT,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()


# The kill path's ceiling, past the budget: the tree kill's own timeout plus
# the post-kill drain. The runner's backstop in settings.json must cover
# budget + KILL_CEILING + 10 (DESIGN section 4; W-42).
KILL_TIMEOUT = 5
DRAIN_TIMEOUT = 3
KILL_CEILING = KILL_TIMEOUT + DRAIN_TIMEOUT

PENDING_MARK = "NOT RUN"


def write_pending_summary(path: Path, hook: str) -> None:
    """Write a summary that reads as FAIL until a run replaces it (W-42).

    A hook killed from outside is neither `ok()` nor `block()`, and the runner
    reads its silence as consent; the one thing that survives the kill is a
    file written first. `harness --summary` APPENDS (W-20), so `settle_summary`
    drops this block once a verdict exists beneath it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"## harness fast: {PENDING_MARK}\n\n"
        f"{hook} started {time.strftime('%Y-%m-%dT%H:%M:%S')} and has not written a "
        "verdict. If this block is still here, the hook was killed before the run "
        "finished (docs/workflows/FINDINGS.md W-42).\n\nHARNESS FAIL\n",
        encoding="utf-8",
    )


def settle_summary(path: Path) -> None:
    """Remove the pending block once the run appended its own."""
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    head, sep, rest = text.partition("\n## harness ")
    if PENDING_MARK in head and sep:
        path.write_text("## harness " + rest, encoding="utf-8")


#: Units of a Floor that measures wall clock on THIS box. jjg ruled (2026-10-14,
#: widened 2026-10-16) that CI judges every timing row, because a busy laptop
#: misses them for reasons the change did not cause. `hours` (release cadence)
#: and `minutes` (a CI job's configured timeout) are not measurements of the
#: box and keep blocking.
TIMING_UNITS = frozenset({"seconds", "ms"})

_FAIL_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|.*\|\s*\*\*FAIL\*\*\s*\|\s*$")
_PYTEST_RED_RE = re.compile(r"\b\d+ (?:failed|errors?)\b")


def timing_threshold_ids() -> frozenset[str]:
    """Ids of the timing Floors, read from `harness/thresholds.json`, the one home of a Floor."""
    try:
        data = json.loads((REPO / "harness" / "thresholds.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    return frozenset(
        t["id"] for t in data.get("thresholds", []) if isinstance(t, dict) and t.get("unit") in TIMING_UNITS
    )


def timing_only_failures(summary: str | None) -> list[str]:
    """The failing Floor ids when EVERY failure in a harness summary is a timing row; else [].

    ⚠ UNKNOWN blocks: no summary, a pending block, a failing summary with no
    FAIL row, a red pytest line, or an unreadable thresholds file all return [],
    which callers read as a real failure.

    ⚠ A real `--summary` file carries `## harness <tier>: FAIL` and `**FAIL**`
    rows, never the literal `HARNESS FAIL` (that goes to stdout). The first
    draft required it and never fired; its fixtures had invented the line.
    """
    if not summary or PENDING_MARK in summary:
        return []
    if _PYTEST_RED_RE.search(summary):
        return []
    failing = [m.group(1) for ln in summary.splitlines() if (m := _FAIL_ROW_RE.match(ln.strip()))]
    timing = timing_threshold_ids()
    if not failing or not timing or any(f not in timing for f in failing):
        return []
    return failing


def block(reason: str) -> None:
    sys.stderr.write(reason.rstrip() + "\n")
    sys.exit(2)


def warn(event: str, message: str) -> None:
    """Exit 0 with the message as additional context, so the agent sees it."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message.rstrip(),
        }
    }
    try:  # FINDINGS W-7: a warning the model ignores is invisible to the human
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        with (EVIDENCE / "hook_warnings.log").open("a", encoding="utf-8") as fh:
            fh.write(
                time.strftime("%Y-%m-%dT%H:%M:%S ") + message.splitlines()[0] + "\n"
            )
    except OSError:
        pass  # the log is a courtesy copy (W-7); the warning itself still goes to stdout
    if event == "PreToolUse":
        out["hookSpecificOutput"]["permissionDecision"] = "allow"
        out["hookSpecificOutput"]["permissionDecisionReason"] = message.splitlines()[0]
    sys.stdout.write(json.dumps(out) + "\n")
    sys.exit(0)


def ok() -> None:
    sys.exit(0)


def budget_warning(hook: str, skipped: str, budget: Budget) -> str:
    return f"WARNING: {hook} skipped {skipped}; nothing passed silently (budget {budget.seconds:.0f} s)."
