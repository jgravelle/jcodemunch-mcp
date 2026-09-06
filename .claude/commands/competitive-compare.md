---
description: "Run the competitive tier (one adapter or all) against the working tree and a ref, in the D2 container, and print DESIGN s5's table for the two jcm commits side by side, per row, never per total; write any draft to .claude/state/competitive/ only, never to the ledger. Nothing here types a number."
argument-hint: "[tool] [ref]"
---

<!--
purpose:  the interactive form of the competitive loop (docs/competitive/
          DESIGN.md s9.1): a session that changed retrieval asks how the
          change moved every competitive row before a PR, against a ref,
          with the same runner, checks and scorer the scheduled job uses
invokes:  benchmarks/competitive/run.py (corpus and task checks inside it),
          compare_ref.py (the table), findings.py (draft mode, to .claude/state/competitive/), trend.py
          (the Movement section over the two runs); docker; git worktree for
          the ref (never checkout in the working tree); skill
          benchmark-methodology
produces: .claude/state/evidence/competitive_cur/<result>.json + latest.md,
          competitive_ref/<result>.json + latest.md,
          competitive_compare.md (the side-by-side table);
          .claude/state/competitive/drafts/*.md (findings.py over the
          current run; drafts, nothing posted)
refuses:  a ref that does not resolve; a tool not in adapter.REGISTRY; to
          run without docker (the nulls and jcodemunch alone are not a
          comparison); to record into benchmarks/competitive/results/ (the
          recorded run is the scheduled job's or a deliberate --record by
          hand, never this command); to write to the ledger
-->

Tool: `${ARGUMENTS}` (first word, or `all`); ref: second word, default `origin/main`.

Load `benchmark-methodology`. Everything below runs the harness's
competitive tier; nothing here restates a threshold, a corpus, or a value.

1. **Arguments.** `TOOL` is the first argument or `all`; `REF` the second
   or `origin/main`. `git rev-parse --verify <REF>^{commit}` or refuse.
   `TOOL` must be a key of `adapter.REGISTRY` (`python -c "import sys;
   sys.path.insert(0,'benchmarks/competitive'); import adapter;
   print(sorted(adapter.REGISTRY))"`) or `all`; refuse otherwise. `docker
   info` must succeed or refuse: a `--sandbox none` run has no competitor
   row and is not a comparison.
2. **Adapters.** `all` is every REGISTRY name; a single tool is
   `null_readall,null_grep,jcodemunch,<TOOL>` (the nulls and jcodemunch
   are on every table by construction, DESIGN s5.3).
3. **Current tree.** Delete exactly `.claude/state/evidence/competitive_cur/`,
   `competitive_ref/` and `competitive_compare.md` (a stale run must not
   be read as this one). Then
   `MSYS_NO_PATHCONV=1 uv run python benchmarks/competitive/run.py --runs 3 --adapters <ADAPTERS> --sandbox docker --out-dir .claude/state/evidence/competitive_cur`.
   No `--record`: this command never writes `benchmarks/competitive/results/`.
   The corpus check and the task check run inside `run.py` and refuse
   before scoring; a refusal (exit 5) ends the command with its message.
   ⚠ The full set is hours on a workstation (FINDINGS CF-53); pass
   `--only <corpus ids>` after the adapters when the question is one
   corpus, or `--set none` for the self corpus alone (the corpus check
   is then recorded, not enforced, and the result header says so), and
   put the filter in the table's `--note`.
4. **Ref tree.** `git worktree add <scratchpad>/competitive-ref <REF>`
   (never `git checkout` in the working tree). In the worktree, `uv sync
   --locked --group dev --extra watch --python <X.Y.Z>` where `X.Y.Z` is
   the FULL version `uv run python -c "import sys;
   print(sys.version.split()[0])"` prints in the current tree (a
   major.minor pin lets uv pick another patch release, and the two
   headers then disagree on the interpreter: CF-59), then the same
   `run.py` line with `--out-dir
   <absolute path to .claude/state/evidence/competitive_ref>`. ⚠ After ANY
   `uv sync` in a worktree, check the six jcodemunch hook paths in
   `~/.claude/settings.json` still point at this checkout's `.venv`
   (W-34). `git worktree remove --force` the worktree when done. If the
   ref has no `benchmarks/competitive/run.py`, print `n/a` for every ref
   cell and say why.
5. **Table.** `uv run python benchmarks/competitive/compare_ref.py --cur
   .claude/state/evidence/competitive_cur --ref .claude/state/evidence/competitive_ref
   --out .claude/state/evidence/competitive_compare.md --note "<the
   tool filter and any --only filter, in words>"` (omit `--ref` when the
   ref had no `run.py`). The script writes ONE page from the two result
   files: the jcm rows first with `measured` on both sides and the signed
   difference (current minus ref, same unit: OUR movement, the reason the
   command exists), then every other `(axis, tool, corpus)` row present on
   either side with the ref's `measured` and delta, the current's, the
   current band, and `trend.classify` over the two gaps (`unchanged`,
   `flipped`, `widened`, `narrowed`; `no band recorded` when the current
   row has none). A value absent on either side is `n/a`, never 0. Per
   row, never per total (F-13). The header names both jcm commits, the
   corpora and their SHAs, the runs, the tools, and the fairness line,
   and before the first number each side's scorer sha256 and interpreter
   from its own header, with a warning line when they differ (a ref
   that predates a change to run.py, score.py, an adapter or the sandbox
   was scored by different code). Under the tables, counts of rows.
   Print the page; retype none of it. A FINDINGS row for the run, when
   one is due, is `--findings-row <id>` on the same line, never typed.
6. **Drafts.** `uv run python benchmarks/competitive/findings.py
   <competitive_cur/*.json> --history benchmarks/competitive/results/history.jsonl
   --out .claude/state/competitive/drafts --open-issues <a file holding []>`
   (an empty list: this is a local look, de-duplication is the scheduled
   job's; say so). Name the draft count by label under the table. Nothing
   is posted; nothing reaches the ledger.
7. Say which paths under `.claude/state/evidence/` and
   `.claude/state/competitive/` hold this run, and that
   `benchmarks/competitive/results/` is untouched.
