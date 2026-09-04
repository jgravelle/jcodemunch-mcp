"""H4: no PR without a full-tier pass on THIS tree (DESIGN section 4, D5).

purpose:  the PR gate's stage 2 is never the first place the full tier runs
invokes:  .claude/state/full-tier.json (written by run_full.py), git tree id,
          .claude/state/evidence/checklist.md (written by dod_checklist.py)
produces: nothing
refuses:  `gh pr create` when the stamp is absent, failed, or for another
          tree; when the branch is main; when the checklist is absent or has
          an `unmet` row
budget:   5 s
"""

from __future__ import annotations

import json
import re

from _common import (
    EVIDENCE,
    STATE,
    block,
    git,
    ok,
    read_hook_input,
    strip_heredocs,
    tool_command,
    tree_id,
)

PR_RE = re.compile(r"\bgh\s+pr\s+create\b")


def main() -> None:
    cmd = strip_heredocs(tool_command(read_hook_input()))
    if not PR_RE.search(cmd):
        ok()
    # Resolved after read_hook_input, which may rebind STATE to a worktree (W-30).
    STAMP = STATE / "full-tier.json"  # noqa: N806
    CHECKLIST = EVIDENCE / "checklist.md"  # noqa: N806
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "main":
        block(
            "pre_pr: on `main`; every change goes through a branch and the gate (enforce_admins is on)."
        )
    if not STAMP.exists():
        block(
            "pre_pr: no full-tier stamp. Run `python .claude/hooks/run_full.py` (the full tier with --summary) "
            "on this tree first; the /feature and /fix-issue commands do."
        )
    stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    now = tree_id()
    if not stamp.get("ok"):
        block(
            f"pre_pr: the last full tier on this box FAILED ({stamp.get('date')}, tree {stamp.get('tree', '?')[:12]})."
        )
    if stamp.get("tree") != now:
        block(
            "pre_pr: the full-tier stamp is for a DIFFERENT tree "
            f"(stamped {stamp.get('tree', '?')[:12]} at {stamp.get('date')}, now {now[:12]}). "
            "The tree changed after the run; run `python .claude/hooks/run_full.py` again."
        )
    if not CHECKLIST.exists():
        block(
            "pre_pr: no Definition-of-Done checklist. Run `python .claude/hooks/dod_checklist.py` and paste it into the PR body."
        )
    text = CHECKLIST.read_text(encoding="utf-8")
    unmet = [ln for ln in text.splitlines() if "| unmet |" in ln]
    if unmet:
        block("pre_pr: the checklist has unmet items:\n" + "\n".join(unmet))
    ok()


if __name__ == "__main__":
    main()
