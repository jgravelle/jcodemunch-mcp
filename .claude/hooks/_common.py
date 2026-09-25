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


def _rebind_repo(cwd: str | None) -> None:
    """Point REPO/STATE/EVIDENCE at the checkout the SESSION is in.

    Claude Code runs a project hook with `$CLAUDE_PROJECT_DIR` fixed, so a
    session working in a `git worktree` of this repo got the explicit
    scripts (run_full.py, dod_checklist.py resolve from their own file)
    and NONE of the automatic ones: the /fix-issue probe's reintroducing
    commit passed pre_commit silently (FINDINGS W-30). The payload's `cwd`
    names the real tree; if it is a checkout of this repo, use it.
    """
    global REPO, STATE, EVIDENCE
    if not cwd:
        return
    try:
        top = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return
    if not top:
        return
    top_path = Path(top).resolve()
    if top_path == REPO or not (top_path / ".claude" / "hooks").exists():
        return
    REPO = top_path
    STATE = REPO / ".claude" / "state"
    EVIDENCE = STATE / "evidence"
    # The hooks import these names at module level; rebind their copies too.
    main = sys.modules.get("__main__")
    for name, value in (("REPO", REPO), ("STATE", STATE), ("EVIDENCE", EVIDENCE)):
        if main is not None and hasattr(main, name):
            setattr(main, name, value)


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
