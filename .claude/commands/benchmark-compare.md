---
description: "Run the harness bench tier on the working tree and on a ref (default origin/main) in the deterministic configuration, and report per Floor id — Floor, ref value, current value, delta, pass/fail — per row, never per total. Records to harness/results/latest.json only on a clean main; otherwise says where the scratch copy is."
argument-hint: [ref]
---

<!--
purpose:  every correction in the benchmark loop was one artifact re-measured
          and its mirrors not; this compares per row and never types a number
invokes:  uv run python -m harness bench --offline --write-results --summary;
          git worktree (never checkout in the working tree);
          scripts/pr_bench_comment.py; uv run python -m harness threshold <id>;
          skill benchmark-methodology
produces: .claude/state/evidence/bench_cur.md, bench_ref.md, latest_cur.json,
          latest_ref.json, bench_compare.md (the table); on a clean main,
          harness/results/latest.json (uncommitted, stated)
refuses:  a ref that does not resolve; recording on main with a dirty tree
-->

Ref: ${ARGUMENTS:-origin/main}

Load `benchmark-methodology`. Everything below runs the harness; nothing
here restates a Floor or types a value.

1. **Ref resolves.** `git rev-parse --verify <ref>^{commit}` or refuse.
2. **Current tree.** Clear `.claude/state/evidence/bench_*` and `latest_*`.
   `uv run python -m harness bench --offline --write-results --summary .claude/state/evidence/bench_cur.md`
   then copy `harness/results/latest.json` to
   `.claude/state/evidence/latest_cur.json`. The offline tier is the
   deterministic configuration (pinned corpora, the no-network fixture);
   do not add flags.
3. **Ref tree.** `git worktree add <scratchpad>/bench-ref <ref>` (never
   `git checkout` in the working tree; a checkout invalidates the D5 stamp
   and the index cache). In the worktree: `uv sync --locked --group dev
   --extra watch` if `.venv` is absent there, then the same bench command
   with `--summary .claude/state/evidence/bench_ref.md` (absolute path) and
   copy its `harness/results/latest.json` to `evidence/latest_ref.json`.
   `git worktree remove --force` the worktree when done. If the ref has no
   `harness/` (predates 2026-09-03), print `n/a` for every row and say why.
4. **Table.** For every threshold id present in either `bench_*.md`
   summary table (`| threshold | criterion | floor | observed | verdict |`
   rows) or either `latest_*.json` `artifacts` block: Floor from
   `uv run python -m harness threshold <id>`, ref observed, current
   observed, delta (current minus ref, signed, same unit), verdict from the
   current summary. A value absent on either side prints `n/a`, never `0`
   (a refusal is not a zero). Where `scripts/pr_bench_comment.py` knows the
   id (latency and token rows), use its rendering:
   `python scripts/pr_bench_comment.py --base-ref <ref> --results harness/results/latest.json --summary .claude/state/evidence/bench_compare.md`
   and append the remaining rows yourself from the two JSONs (FINDINGS W-2).
   Per row, never per total: the total hid one cause behind another (F-13).
5. **Record.** If `git rev-parse --abbrev-ref HEAD` is `main` and
   `git status --porcelain` is empty apart from `harness/results/latest.json`:
   say that `latest.json` is rewritten and UNCOMMITTED, and that the weekly
   `main.yml` results PR is the path that commits it (a bot push to main
   cannot; enforce_admins). Otherwise `git checkout -- harness/results/latest.json`
   to restore the tracked copy and name the paths under
   `.claude/state/evidence/` where this run's table and JSONs are.
6. Print `bench_compare.md`.
