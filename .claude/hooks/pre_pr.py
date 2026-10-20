"""H4: no PR without a full-tier pass on THIS tree (DESIGN section 4, D5).

purpose:  the PR gate's stage 2 is never the first place the full tier runs
invokes:  .claude/state/full-tier.json (written by run_full.py), git tree id,
          .claude/state/evidence/checklist.md (written by dod_checklist.py)
produces: nothing
refuses:  `gh pr create` when the stamp is absent, failed, or for another
          tree; when the branch is main; when the checklist is absent or has
          an `unmet` row. A tier whose ONLY failures are timing rows (unit
          `seconds`/`ms` in harness/thresholds.json) passes: CI judges those
          (jjg, 2026-10-16)
budget:   5 s
"""

from __future__ import annotations

import json
import re

from pathlib import Path

from _common import (
    PENDING_MARK,
    EVIDENCE,
    STATE,
    _rebind_repo,
    any_dir_change,
    block,
    command_dir,
    git,
    ok,
    read_hook_input,
    strip_heredocs,
    timing_only_failures,
    tool_command,
    tree_id,
)

# `gh -R o/r`, `gh -Ro/r`, `gh --repo=o/r`, `gh.exe` and the `pr new` alias all
# open a PR, in any case (Windows resolves `GH` to gh); the old pattern let them
# through with no check at all (W-58).
PR_RE = re.compile(r"(?i)\bgh(?:\.exe)?\s+(?:(?:-R\s*|--repo(?:\s+|=))\S+\s+)*pr\s+(?:create|new)\b")


def main() -> None:
    payload = read_hook_input()
    raw = tool_command(payload)
    cmd = strip_heredocs(raw)
    if not PR_RE.search(cmd):
        ok()
    # A quoted `<<X` can make strip_heredocs swallow a real `cd`; judge the RAW
    # command's segments (a body inside "$(cat <<'EOF' ...)" stays one quoted word).
    if cmd != raw and any_dir_change(raw):
        block(
            "pre_pr: cannot tell which tree the PR is opened from (a heredoc and a directory change in one "
            "command). Write the body to a file in a separate tool call, then pass it with --body-file."
        )
    # jjg (2026-10-19): the stamp is the one of the tree `gh pr create` RUNS in.
    # The Bash tool resets its cwd each call, so a worktree PR is
    # `cd <worktree> && gh pr create`; read that tree, never the session's.
    where, why = command_dir(cmd, PR_RE, str(payload.get("cwd") or Path.cwd()))
    if why:
        block(
            f"pre_pr: cannot tell which tree `gh pr create` runs in ({why}). "
            "Open it as `cd <checkout> && gh pr create ...`."
        )
    if where is not None and (not where.is_dir() or not _rebind_repo(str(where))):
        block(
            f"pre_pr: `gh pr create` runs in {where}, which is not a checkout "
            "carrying .claude/hooks, so there is no full-tier stamp to read for it."
        )
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
    # W-42 remedy (3): a pre_commit the runner killed leaves `fast.md` as its
    # pending block, and a checklist generated earlier can still read 12/12.
    FAST = EVIDENCE / "fast.md"  # noqa: N806
    fast_text = FAST.read_text(encoding="utf-8") if FAST.exists() else None
    if fast_text is None or PENDING_MARK in fast_text:
        block(
            "pre_pr: no fast-tier verdict on this box"
            + (" (the last pre_commit was killed before it wrote one)" if fast_text else "")
            + ". Run `uv run python -m harness fast --summary .claude/state/evidence/fast.md`, "
            "then the checklist again."
        )
    # jjg (2026-10-16): CI judges every timing row, so a fast tier whose ONLY
    # failures are timing rows does not block; any other failure does.
    if ("HARNESS FAIL" in fast_text or "**FAIL**" in fast_text) and not timing_only_failures(fast_text):
        block("pre_pr: the last fast tier on this box FAILED; fix, then run it again.")
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
