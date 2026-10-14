# Token Savings: jCodeMunch MCP

jCodeMunch saves tokens on two independent axes:

1. **Retrieval** — agents fetch exact symbols instead of reading whole files.
2. **Encoding (MUNCH)** — responses that do go to the agent are packed in a
   purpose-built compact wire format, not verbose JSON.

The retrieval axis is the big number: **86-99% on code-reading tasks, 96.5%
average (28.9x)** against a grep-and-read agent across 15 tasks and 3 pinned
repositories. Quote the range, not the average alone: per-query results span
7.6x to 81.2x, so the aggregate on its own overstates the low end. The larger
99.6% / 245.9x figure is against a *read-every-file* baseline, which is a
ceiling nobody actually pays; see `benchmarks/METHODOLOGY.md` for why both are
published and which one to lead with. Encoding is a smaller-but-independent
multiplier on whatever traffic remains.
They compose — every byte saved on the wire is a byte the agent doesn't pay
to read.

## MUNCH encoding benchmark (v1.56.0)

Representative payloads through the dispatcher with `format="compact"`.
Measured as `(json_bytes - compact_bytes) / json_bytes` with `json.dumps`
using the default separators.

| Tool                   | Encoder | JSON bytes | Compact bytes | Savings |
|------------------------|---------|------------|---------------|---------|
| `find_references`      | `fr1`   | 2,265      | 1,242         | **45.2%** |
| `get_dependency_graph` | `dg1`   | 1,702      | 998           | **41.4%** |
| `get_call_hierarchy`   | `ch1`   | 4,133      | 2,238         | **45.9%** |
| `search_symbols`       | `ss1`   | 4,545      | 2,754         | **39.4%** |
| `get_repo_outline`     | `ro1`   | 4,642      | 2,277         | **50.9%** |
| `get_hotspots`         | `gen1`  | 1,987      | 887           | **55.4%** |

**Median 45.5% / mean 46.4% / min 39.4% / max 55.4%.**

Run it yourself:

```bash
cd munch-bench && python -m munch_bench.encoding_bench
```

Encoding is opt-in per call (`format="auto"` falls back to JSON when savings
drop below 15%) and does not affect retrieval-side savings. See
[SPEC_MUNCH.md](SPEC_MUNCH.md) for the wire format.

## Third-Party Validation

Independent A/B test by [@Mharbulous](https://github.com/Mharbulous) — 50 iterations, Claude Sonnet 4.6, real Vue 3 + Firebase production codebase. JCodeMunch vs native tools (Grep/Glob/Read) on a naming-audit task.

| Metric | Native | JCodeMunch | Delta |
|--------|--------|------------|-------|
| Success rate | 72% | **80%** | +8 pp |
| Timeout rate | 40% | **32%** | −8 pp |
| Mean cost/iteration | $0.783 | **$0.738** | −5.7% |
| Mean cache creation | 104,135 | **93,178** | −10.5% |
| Mean duration | 318s | **299s** | −6.0% |

**Tool-layer savings (fixed overhead removed): 15–25%.**

The blended 5.7% understates the real advantage because each iteration includes fixed overhead (subagent consensus calls, system prompt) independent of which tool variant runs. Isolating iterations with no findings removes that overhead and exposes the raw tool-layer delta.

One finding category appeared only in the JCodeMunch variant: `find_importers` identified two orphaned files with zero live importers — a structural query native tools cannot answer without scripting.

Full report: [`benchmarks/ab-test-naming-audit-2026-03-18.md`](benchmarks/ab-test-naming-audit-2026-03-18.md)

---

## Why This Exists

AI agents waste tokens when they must read entire files to locate a single function, class, or constant.
jCodeMunch indexes a repository once and allows agents to retrieve **exact symbols on demand**, eliminating unnecessary context loading.

---

## Example Scenario

**Repository:** Medium Python codebase (300+ files)
**Task:** Locate and read the `authenticate()` implementation

| Approach         | Tokens Consumed | Process                               |
| ---------------- | --------------- | ------------------------------------- |
| Raw file loading | ~7,500 tokens   | Open multiple files and scan manually |
| jCodeMunch MCP   | ~1,449 tokens   | `search_symbols` → `get_symbol_source` |

**Savings:** ~80.7%

---

## Typical Savings by Task

| Task                     | Raw Approach    | With jCodeMunch | Savings |
| ------------------------ | --------------- | --------------- | ------- |
| Explore repo structure   | ~200,000 tokens | ~2k tokens      | ~99%    |
| Find a specific function | ~40,000 tokens  | ~200 tokens     | ~99.5%  |
| Read one implementation  | ~40,000 tokens  | ~500 tokens     | ~98.7%  |
| Understand module API    | ~15,000 tokens  | ~800 tokens     | ~94.7%  |

---

## Scaling Impact

| Queries | Raw Tokens | Indexed Tokens | Savings |
| ------- | ---------- | -------------- | ------- |
| 10      | 400,000    | ~5k            | 98.7%   |
| 100     | 4,000,000  | ~50k           | 98.7%   |
| 1,000   | 40,000,000 | ~500k          | 98.7%   |

---

## Key Insight

jCodeMunch shifts the workflow from:

**”Read everything to find something”**
to
**”Find something, then read only that.”**

---

## Live Token Savings Counter

Every tool response includes real-time savings data in the `_meta` field:

```json
“_meta”: {
  “tokens_saved”: 2450,
  “total_tokens_saved”: 184320
}
```

- **`tokens_saved`**: Tokens saved by the current call (raw file bytes vs response bytes ÷ 4)
- **`total_tokens_saved`**: Cumulative total across all calls, persisted to `~/.code-index/_savings.json`

No extra API calls or file reads — computed using fast `os.stat` only.

### The meter is conservative by design

Every known error term points down. The ~4-bytes-per-token conversion undercounts denser code; each matched file is credited once per call no matter how many symbols matched inside it; empty and negative results credit zero (the counter clamps at zero rather than booking a phantom saving). Cache hits record the same savings as the miss that populated them — a repeat query avoids the same raw reads, and for a long time the meter didn't count that, which was a systematic *under*count, since fixed.

Savings are stored as **tokens**, never as currency. Dollar valuations are applied at display time at current published input-token rates, so the stored record never bakes in a price that later changes, and figures update automatically when vendors adjust pricing.

### The lifetime meter and the `receipt` CLI

`~/.code-index/_savings.json` holds the lifetime total plus per-day buckets, survives client reinstalls, and is multi-process safe. The `jcodemunch-mcp receipt` CLI reports it alongside windowed figures computed from your own local transcripts (`--since` / `--until` / `--by-day`; `--model` picks the valuation rate, `--rates` lists the price table). `receipt --export json` includes a `savings_provenance` block chaining the figures to the committed, CI-re-run measurement artifacts in `benchmarks/provenance/` — the report carries its own receipts. Full design: [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md) Chapter 4.

The windowed figures come from wherever Claude Code actually wrote your transcripts, not just the default profile. `CLAUDE_CONFIG_DIR` relocates that tree, so `receipt` scans a **union**: the default `~/.claude/projects`, the current `CLAUDE_CONFIG_DIR`, any roots earlier sessions registered (see [SECURITY.md](SECURITY.md#background-behavior-fully-disclosed)), deduplicated by session UUID so a copied or symlinked tree never double-counts. `--projects-root` remains an override for pinning a scan to a known tree, and is now repeatable so it can name every profile at once. `receipt --roots` prints the exact list that will be walked.

---

## Per-Tool Latency + Drift Tracking (v1.74.0+)

Beyond the savings counter, every `call_tool` invocation is timed and the
duration recorded into a per-tool ring buffer (cap 512 entries). `get_session_stats`
exposes a `latency_per_tool` field with `{count, p50_ms, p95_ms, max_ms,
errors, error_rate}` for every tool exercised this session.

```json
"latency_per_tool": {
  "search_symbols": {"count": 42, "p50_ms": 12.4, "p95_ms": 88.1, "max_ms": 312.0, "errors": 0, "error_rate": 0.0}
}
```

The `analyze_perf` tool surfaces the slowest tools by p95 and the coldest
caches by hit-rate; pass `window=1h|24h|7d|all` to read the persistent
SQLite sink (`~/.code-index/telemetry.db`, opt-in via
`perf_telemetry_enabled`). Use `compare_release="X.Y.Z"` to diff the
current session against a saved
`benchmarks/token_baselines/v{VERSION}.json` snapshot — useful for
catching token-efficiency regressions across releases.

Each retrieval result also carries a `_meta.confidence` (0–1) score and
per-symbol `_freshness ∈ {fresh, edited_uncommitted, stale_index}`
markers, so an agent can decide whether a token-cheap result is worth
trusting or whether it needs to spend a re-index first.

---
