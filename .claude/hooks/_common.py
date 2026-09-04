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
        pass

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STATE = REPO / ".claude" / "state"
EVIDENCE = STATE / "evidence"


def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def tool_command(payload: dict) -> str:
    ti = payload.get("tool_input") or {}
    return str(ti.get("command") or "")


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
    head_tree = git("rev-parse", "HEAD^{tree}").strip()
    # The harness's own footprint (pytest-cov's `.coverage.<host>.<pid>` files
    # at the root, the hook state) must not move the identity it is measured
    # against, or no full-tier run can ever stamp `ok` (FINDINGS W-13; the
    # guard-sampled-after-the-work lesson).
    status = "\n".join(
        ln
        for ln in git("status", "--porcelain", "--untracked-files=all").splitlines()
        if not (ln[3:].startswith(".coverage") or ln[3:].startswith(".claude/state/"))
    )
    diff = git("diff", "HEAD") + status
    return head_tree + ":" + hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16]


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
        pass
    if event == "PreToolUse":
        out["hookSpecificOutput"]["permissionDecision"] = "allow"
        out["hookSpecificOutput"]["permissionDecisionReason"] = message.splitlines()[0]
    sys.stdout.write(json.dumps(out) + "\n")
    sys.exit(0)


def ok() -> None:
    sys.exit(0)


def budget_warning(hook: str, skipped: str, budget: Budget) -> str:
    return f"WARNING: {hook} skipped {skipped} (budget {budget.seconds:.0f} s exceeded); nothing passed silently."
