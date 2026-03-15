# jcodemunch-mcp — Token Efficiency Benchmark

**Result: 95% average token reduction · tiktoken cl100k_base · 15 task-runs · 3 repos**

## What this measures

How many tokens a code-retrieval tool consumes versus an agent that reads every source file before acting.

**Baseline:** concatenate all indexed source files and count tokens. This is the *minimum* cost for a "read everything first" agent — real agents typically read files multiple times, so production savings are higher.

**jcodemunch workflow:** `search_symbols` (top 5 results) + `get_symbol` × 3 hits per query. Total = search response tokens + 3 × symbol source tokens.

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

| Repo | Files | Baseline tokens | Avg reduction |
|------|------:|----------------:|--------------:|
| expressjs/express | 34 | 73,838 | **98.4%** |
| fastapi/fastapi | 156 | 214,312 | **92.7%** |
| gin-gonic/gin | 40 | 84,892 | **98.0%** |
| **Grand total** | — | 1,865,210 | **95.0%** |

**95.0% average token reduction** across 15 task-runs · 20.2x ratio · tiktoken cl100k_base.

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
- The jcodemunch workflow counts `search_symbols` + `get_symbol` responses only — it does not count system prompt or tool description tokens, which are identical for both approaches.
- Token counts are from serialized JSON responses, not raw source, so they include field names and structure overhead. This slightly understates the reduction.
