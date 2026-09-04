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
import subprocess
import sys
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
# What the full tier's verdict depends on (tree identity for the D5 stamp).
TIER_PATHS = (
    "src",
    "tests",
    "harness",
    "scripts",
    "benchmarks",
    "pyproject.toml",
    "uv.lock",
    ".github",
)


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


def tree_id() -> str:
    """Identity of the working tree: HEAD's tree plus a digest of the uncommitted diff.

    A full-tier run is valid for exactly this identity, whatever its age (D5).
    """
    # The identity covers what the full tier's verdict DEPENDS on: the code
    # roots, the packaging and the harness. A CHANGELOG line, a PR body draft
    # or a docs/ edit after the run does not invalidate it; committing the
    # same content does not either (the first /feature run had to run the
    # tier twice, W-21). The harness's own footprint (pytest-cov's
    # `.coverage.<host>.<pid>` files, the hook state) never counts (W-13).
    # Residual: a doc edit CAN flip a doc-reading test (CLAUDE.md size); the
    # PR gate is the authority for that, this hook is the early one.
    tracked = git("ls-tree", "-r", "HEAD", "--", *TIER_PATHS)
    diff = git("diff", "HEAD", "--", *TIER_PATHS)
    untracked = "\n".join(
        ln
        for ln in git(
            "status", "--porcelain", "--untracked-files=all", "--", *TIER_PATHS
        ).splitlines()
        if ln.startswith("??") and not ln[3:].startswith(".coverage")
    )
    return hashlib.sha256((tracked + diff + untracked).encode("utf-8")).hexdigest()[:24]


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
    try:
        r = subprocess.run(
            cmd,
            cwd=REPO,
            shell=shell,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=left,
            env=env or {**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return None, ""
    return r.returncode, (r.stdout or "") + (r.stderr or "")


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
