# jcodemunch-mcp — Token Efficiency Benchmark

**Result: 96.4% average token reduction (27.4x) vs a grep-and-read agent · tiktoken cl100k_base · 15 task-runs · 3 repos**

## What this measures

How many tokens a code-retrieval tool consumes versus an agent doing the same job without it. Two baselines are measured in the same run: **grep-top-3** (`rg -l`, then open the top 3 matching files whole — what a competent agent actually does) and **read-all** (every source file — a ceiling nobody pays).

**Baseline:** concatenate all indexed source files and count tokens. This is the *minimum* cost for a "read everything first" agent — real agents typically read files multiple times, so production savings are higher.

**jcodemunch workflow:** `search_symbols` (top 5 results) + `get_symbol_source` × 3 hits per query. Total = search response tokens + 3 × symbol source tokens.

**Tokenizer:** `tiktoken cl100k_base` — the GPT-4 / Claude family encoding. Consistent across runs regardless of model.

## Reproducing the results

```bash
pip install jcodemunch-mcp tiktoken

# Index the three canonical repos
jcodemunch index_repo expressjs/express
jcodemunch index_repo fastapi/fastapi
jcodemunch index_repo gin-gonic/gin

# Run the benchmark (prints markdown table + grand summary)
python benchmarks/harness/run_benchmark.py

# Optional: write results to file
python benchmarks/harness/run_benchmark.py --out benchmarks/results/my_run.md
```

## Task corpus

Tasks are defined in [`tasks.json`](tasks.json) — 5 queries × 3 repos = 15 measurements.

| ID | Query | Description |
|----|-------|-------------|
| `router-route-handler` | `router route handler` | Core route registration / dispatch logic |
| `middleware` | `middleware` | Middleware chaining and execution |
| `error-exception` | `error exception` | Error handling and exception propagation |
| `request-response` | `request response` | Request/response object definitions |
| `context-bind` | `context bind` | Context creation and parameter binding |

Repos: `expressjs/express`, `fastapi/fastapi`, `gin-gonic/gin`

## Canonical results

Full per-task tables are in [`results.md`](results.md).

| Repo | Files | Grep-top-3 baseline | Read-all baseline | jCodeMunch | vs grep | vs read-all |
|------|------:|--------------------:|------------------:|-----------:|--------:|------------:|
| expressjs/express | 186 | 15,724 avg | 154,569 | 1,007 avg | **15.6x** | 153.5x |
| fastapi/fastapi | 1,186 | 85,296 avg | 825,326 | 2,149 avg | **39.7x** | 384.1x |
| gin-gonic/gin | 98 | 31,975 avg | 151,842 | 1,537 avg | **20.8x** | 98.8x |
| **Grand total (15 task-runs)** | — | **664,975** | **5,658,685** | **23,467** | **28.3x** | **241.1x** |

**96.5% average token reduction · 28.3x** against grep-and-read; 99.6% · 241.1x
against read-all. tiktoken cl100k_base.

⚠ **This table was stale until 2026-08-03** — it carried a pre-v1.108.222 corpus
(165/951/98 files, 5,122,105 tokens, 263.9x) that no other artifact had matched
since the corpus was pinned. Per-repo rows are per query (`avg`); the grand total
sums 15 task-runs.

⚠⚠ **And it went stale again, in a way the 2026-08-03 fix could not catch.**
Until 2026-09-16 this table carried a jCodeMunch column of 1,002 / 2,271 / 1,577
and a grand total of 24,249, while `README.md` carried 1,017 / 2,218 / 1,573 over
a grand total of 23,467 and `benchmarks/jcm_reference.json` — the artifact CI
captures — said 1,007 / 2,149 / 1,537. Three sets of numbers for one run, and the
proof they ARE one run is that the reference's per-repo totals sum to the 23,467
both files already printed. Every cell above is now derived from the reference,
and `tests/test_benchmark_tables_mirror_the_reference.py` fails if either table
drifts from it again. ⚠ `tests/test_provenance.py` gated the grand total and
nothing gated the rows, which is why the total stayed right while the rows did
not: **gate the cells the reader actually reads.**

⚠ **The per-query spread that used to sit in this paragraph is not published any
more.** Both files stated one (7.3x–79.8x median 25.5x here, 7.6x–81.2x median
26.1x in `README.md`) and neither can be derived from the committed reference,
which carries totals and averages only. Rather than pick one or recompute it from
a run nobody can reproduce, it is withheld until `run_benchmark.py --reference`
records per-query figures.

To regenerate:

```bash
python benchmarks/harness/run_benchmark.py --out benchmarks/results.md
```

## Benchmarking a different tool

The task corpus in `tasks.json` is tool-agnostic. To evaluate another tool:

1. Use the same 3 repos and 5 queries.
2. Use the same baseline: all indexed source files concatenated, tokenized with `tiktoken cl100k_base`.
3. Measure total tokens consumed by your retrieval workflow per query (tool calls + responses).
4. Report per-task rows and the grand average using the same formula: `(1 - tool_tokens / baseline_tokens) * 100`.

If you publish results against this corpus, open an issue or PR and we'll link them here.

## Methodology notes

- The baseline is a lower bound. Agents that re-read files mid-task spend more.
- The jcodemunch workflow counts `search_symbols` + `get_symbol_source` responses only — it does not count system prompt or tool description tokens, which are identical for both approaches.
- Token counts are from serialized JSON responses, not raw source, so they include field names and structure overhead. This slightly understates the reduction.

## Related harnesses (v1.74.0+)

- **`benchmarks/replay/`** — replayable retrieval-quality benchmark.
  Fixtures pin `(query, expected_top_k_ids)` tuples; the harness runs
  each query through `search_symbols` and reports nDCG@k, MRR@k, and
  Recall@k. **Wired into CI** as the `Replay` workflow
  (`.github/workflows/replay.yml`): every push to `main` and every PR
  indexes the repo and runs
  `run_replay.py --fixture … --repo <indexed-id>
  --baseline-file results/self_v1_75_0-golden.json --gate $(python -m harness threshold replay.max_relative_drop)`, which
  exits non-zero if any aggregate metric drops more than 2% (relative)
  below the committed golden baseline. This is the regression gate that
  lets ranking-affecting changes (fusion weights, BM25 normalization,
  parser extraction) land with a proof they did not degrade retrieval.
  The `self_v1_75_0` fixture is locked at 1.0 across all metrics; update
  `self_v1_75_0-golden.json` (via `--write-result`) only on a deliberate,
  reviewed ranking change. Pass `--repo` to override the fixture's
  machine-specific repo id; `--baseline X.Y.Z` still gates against a
  version-pinned `results/{fixture}-v{X.Y.Z}.json` snapshot.
- **`benchmarks/token_baselines/`** — per-release token-savings + latency
  snapshots. `capture_token_baseline.py` reads
  the live session's `get_session_stats` + `latency_stats` and writes
  `benchmarks/token_baselines/v{VERSION}.json`. The `analyze_perf` tool
  consumes these via `compare_release="X.Y.Z"`.
