---
description: "Run the harness bench tier on the working tree and on a ref (default origin/main) in the deterministic configuration, and report per Floor id — Floor, ref value, current value, delta, pass/fail — per row, never per total. Records to harness/results/latest.json only on a clean main; otherwise says where the scratch copy is."
argument-hint: "[ref]"
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
2. **Current tree.** Delete exactly `.claude/state/evidence/bench_cur.md`,
   `bench_ref.md`, `bench_compare.md`, `latest_cur.json`, `latest_ref.json`
   (`--summary` APPENDS, so a stale file doubles the table; `bench_table.md`
   belongs to `/feature` and stays). Note the interpreter:
   `uv run python -c "import sys; print(sys.version)"`. Then
   `uv run python -m harness bench --offline --write-results --summary .claude/state/evidence/bench_cur.md`
   and copy `harness/results/latest.json` to `evidence/latest_cur.json`.
   The offline tier is the deterministic configuration (pinned corpora,
   the no-network fixture); do not add flags. The self-latency corpus is a
   copy of each tree's `src/`, so its `corpus_sha256` differs between the
   two sides by design.
3. **Ref tree.** `git worktree add <scratchpad>/bench-ref <ref>` (never
   `git checkout` in the working tree; a checkout invalidates the D5 stamp
   and the index cache). In the worktree: `uv sync --locked --group dev
   --extra watch --python <the version step 2 printed>` (both sides on
   the same interpreter; the first dry-run got 3.13 against 3.12), then
   the same bench command ⚠ After ANY `uv sync` in a worktree, check the six jcodemunch hook paths in `~/.claude/settings.json` still point at this checkout's `.venv` (W-34: a worktree sync re-registered them to the worktree, which was then deleted).
   with `--summary .claude/state/evidence/bench_ref.md` (absolute path) and
   copy its `harness/results/latest.json` to `evidence/latest_ref.json`.
   `git worktree remove --force` the worktree when done. If the ref has no
   `harness/` (predates 2026-09-03), print `n/a` for every row and say why.
4. **Table.** ONE table, the ref column being the FRESH run of step 3
   (never the artifact committed on the ref: `scripts/pr_bench_comment.py
   --base-ref` reads `git show <ref>:harness/results/latest.json`, a run
   from another day, and its ref cells disagreed with the fresh run in
   every row on the first dry-run; do not use it here, W-28). Rows: every
   id in `harness/thresholds.json` (`harness.thresholds.load()` gives id,
   criterion, comparator, floor) that appears as a key inside either
   `latest_*.json`'s `artifacts.self_latency` block or as a `| threshold |`
   row of either summary. Columns: threshold, crit, floor (comparator and
   value), ref observed, current observed, delta (current minus ref,
   signed, same unit), verdict (from the current summary; `informational`
   for a `latency.*` row while F-19 is open, as the summary footer says).
   A value absent on either side prints `n/a`, never `0` (a refusal is not
   a zero). No token rows exist offline; say so under the table rather
   than leaving the reader to infer it. Per row, never per total (F-13).
5. **Record.** `--write-results` rewrites TWO tracked files:
   `harness/results/latest.json` and `harness/results/self_latency.json`.
   If `git rev-parse --abbrev-ref HEAD` is `main` and `git status
   --porcelain` shows nothing but those two: say they are rewritten and
   UNCOMMITTED, and that the weekly `main.yml` results PR is the path that
   commits them (a bot push to main cannot; enforce_admins). Otherwise
   `git checkout -- harness/results/latest.json harness/results/self_latency.json`
   and name the paths under `.claude/state/evidence/` where this run's
   table and JSONs are.
6. Print `bench_compare.md`.
