"""H1: the fast tier before a commit (docs/workflows/DESIGN.md section 4).

purpose:  a diff CI would reject on stage 1 never reaches the human
invokes:  `uv run python -m harness fast --summary`, the format check with
          pr-gate.yml's own scope and command (read from the workflow file,
          never restated), `uv run python -m harness check types.error_max`
          when pyright is importable
produces: .claude/state/evidence/fast.md
refuses:  a `git commit` when any of the three FAILS (exit 2 with the
          verdict lines); docs-only commits are not checked at all
budget:   150 s; past it, WARNING naming what was skipped, commit allowed
"""

from __future__ import annotations

import re

from _common import (
    EVIDENCE,
    REPO,
    Budget,
    block,
    budget_warning,
    git,
    ok,
    read_hook_input,
    run_budgeted,
    split_segments,
    strip_heredocs,
    tool_command,
    warn,
)

BUDGET_SECONDS = 150
CODE_ROOTS = (
    "src/",
    "tests/",
    "harness/",
    "scripts/",
    "benchmarks/harness/",
    ".github/",
)
COMMIT_RE = re.compile(r"\bgit\s+(?:-\S+\s+)*commit\b")


def _format_command() -> str | None:
    """The `fast: format` step's command from pr-gate.yml, verbatim up to the tee."""
    text = (REPO / ".github" / "workflows" / "pr-gate.yml").read_text(encoding="utf-8")
    m = re.search(
        r"^\s*(uvx ruff\S* format --check[^\n|]*?)(?:\s*2>&1.*)?$", text, re.M
    )
    return m.group(1).strip() if m else None


def main() -> None:
    payload = read_hook_input()
    cmd = strip_heredocs(tool_command(payload))
    if not COMMIT_RE.search(cmd):
        ok()
    # The hook runs BEFORE the line it is asked about, so it can only judge a
    # commit whose content already exists. A line that also CREATES or EDITS
    # files (a printf, a heredoc, `python -`, `sed -i`) hides the commit's
    # content from every check here: two probes slipped past two earlier
    # rules this way (FINDINGS W-11, W-15). Such a line is refused outright,
    # not checked: create and edit first, then commit in a line of its own.
    segments = split_segments(cmd)
    provable = all(
        re.match(
            r"^(?:cd\s|git\s+(?:add|commit|status|diff|rm|mv|log|show|rev-parse|branch)\b|\{|\}|echo\s)",
            s,
        )
        for s in segments
    )
    if not provable:
        block(
            "pre_commit: this line does more than add and commit, so the hook cannot see "
            "what the commit will contain. Make the file changes in their own tool call, "
            "then run `git add ... && git commit ...` alone (FINDINGS W-15)."
        )
    staged = git("diff", "--cached", "--name-only").split()
    if re.search(
        r"\bgit\s+(?:add|rm|mv)\b|\bcommit\b[^|;&]*\s(?:-\S*a\S*|--all)\b", cmd
    ):
        staged += [
            ln[3:].strip().strip('"')
            for ln in git("status", "--porcelain", "--untracked-files=all").splitlines()
        ]
    if not any(p.startswith(CODE_ROOTS) for p in staged):
        ok()

    budget = Budget(BUDGET_SECONDS)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary = EVIDENCE / "fast.md"
    summary.unlink(missing_ok=True)  # `--summary` appends (W-20)
    skipped: list[str] = []
    failures: list[str] = []

    rc, out = run_budgeted(
        ["uv", "run", "python", "-m", "harness", "fast", "--summary", str(summary)],
        budget,
    )
    if rc is None:
        skipped.append("the fast tier")
    elif rc != 0:
        tail = [
            ln for ln in out.splitlines() if " FAIL" in ln or "failed" in ln.lower()
        ][-12:]
        failures.append("fast tier FAIL:\n" + "\n".join(tail or out.splitlines()[-12:]))

    fmt = _format_command()
    if fmt is None:
        skipped.append("the format check (pr-gate.yml `fast: format` step not found)")
    else:
        rc, out = run_budgeted(fmt, budget, shell=True)
        if rc is None:
            skipped.append("the format check")
        elif rc != 0:
            failures.append(
                f"format check FAIL (`{fmt}`):\n" + "\n".join(out.splitlines()[-8:])
            )

    rc, _ = run_budgeted(
        ["uv", "run", "python", "-c", "import pyright"],
        Budget(min(15, max(budget.left(), 1))),
    )
    if rc == 0:
        rc, out = run_budgeted(
            ["uv", "run", "python", "-m", "harness", "check", "types.error_max"], budget
        )
        if rc is None:
            skipped.append("the type ratchet (types.error_max)")
        elif rc != 0:
            failures.append(
                "types.error_max FAIL:\n" + "\n".join(out.splitlines()[-4:])
            )
    else:
        skipped.append(
            "the type ratchet (pyright not importable here; CI's `fast: types` runs it)"
        )

    if failures:
        block(
            "pre_commit: commit refused (DESIGN section 4, H1). Fix, then commit again.\n\n"
            + "\n\n".join(failures)
        )
    if skipped:
        warn("PreToolUse", budget_warning("pre_commit", ", ".join(skipped), budget))
    ok()


if __name__ == "__main__":
    main()
