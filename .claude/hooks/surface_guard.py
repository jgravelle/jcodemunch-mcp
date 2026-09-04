"""H3: the tool surface changed under an edit (DESIGN section 4).

purpose:  an edit under the tool-registration path recomputes the listing and
          says when it moved, so README, CLAUDE.md/KEY-FILES, CHANGELOG and the
          schema baseline are changed WITH the feature, not in the release commit
invokes:  `python scripts/surface_diff.py --base-ref HEAD` (working tree vs
          HEAD; never a hand-typed count)
produces: .claude/state/evidence/surface.md
refuses:  nothing; warning only (exit 2 feedback)
budget:   40 s
"""

from __future__ import annotations

import sys

from _common import (
    EVIDENCE,
    REPO,
    Budget,
    budget_warning,
    ok,
    read_hook_input,
    run_budgeted,
    tool_path,
    under,
    warn,
)

BUDGET_SECONDS = 40
SURFACE_PATHS = (
    ("src", "jcodemunch_mcp", "server.py"),
    ("src", "jcodemunch_mcp", "counter.py"),
    ("src", "jcodemunch_mcp", "cli", "policy.py"),
    ("src", "jcodemunch_mcp", "tools"),
    ("src", "jcodemunch_mcp", "encoding", "schemas"),
)


def _descriptions_flag() -> list[str]:
    """`--descriptions` once PR #592 (FINDINGS W-1) is on the base; the flag is read from the script, never assumed."""
    text = (REPO / "scripts" / "surface_diff.py").read_text(encoding="utf-8")
    return ["--descriptions"] if "--descriptions" in text else []


def main() -> None:
    payload = read_hook_input()
    path = tool_path(payload)
    if not any(under(path, *p) for p in SURFACE_PATHS):
        ok()
    budget = Budget(BUDGET_SECONDS)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out_path = EVIDENCE / "surface.md"
    rc, out = run_budgeted(
        [
            sys.executable,
            "scripts/surface_diff.py",
            *_descriptions_flag(),
            "--base-ref",
            "HEAD",
            "--summary",
            str(out_path),
        ],
        budget,
    )
    if rc is None:
        warn("PostToolUse", budget_warning("surface_guard", "the surface diff", budget))
    if rc == 0 and "no surface change" in out:
        ok()
    tail = "\n".join(out.splitlines()[-15:])
    warn(
        "PostToolUse",
        "surface_guard: the tool surface differs from HEAD after this edit.\n"
        + tail
        + "\nDoD 4: README tool reference, CLAUDE.md Key Files (invariant) or KEY-FILES.md (description), "
        "CHANGELOG naming each tool, and the schema baseline regenerated with the token delta stated. "
        "Stage 5 (`done: tool surface`) checks this on the PR. "
        "Description changes are listed above only when the script has `--descriptions` (FINDINGS W-1, PR #592).",
    )


if __name__ == "__main__":
    main()
