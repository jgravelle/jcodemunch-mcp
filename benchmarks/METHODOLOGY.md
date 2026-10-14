# Benchmark Methodology

This document provides full methodological detail for the token efficiency
benchmarks reported in `results.md` and the project README.

## Scope

The benchmark measures **retrieval token efficiency** — how many LLM input
tokens a code exploration tool consumes compared to reading all source files.
It does **not** measure answer quality, latency, or end-to-end task completion.

## Repositories Under Test

All repositories are public and pinned to a specific upstream **commit**, not
to a branch. No filtering or cherry-picking of files was applied beyond
jcodemunch's standard skip patterns (node_modules, __pycache__, etc.).

| Repository | Commit | Files Indexed | Symbols Extracted | Baseline Tokens |
|------------|--------|:------------:|:-----------------:|:--------------:|
| expressjs/express | `1faf228935aa` | 186 | 989 | 154,569 |
| fastapi/fastapi | `a64dfbbd21a4` | 1,186 | 13,699 | 825,326 |
| gin-gonic/gin | `75ccf94d605a` | 98 | 1,822 | 151,842 |

The commits are pinned in [`tasks.json`](tasks.json) and re-checked on every
run: the harness refuses to overwrite the published artifact when a measured
index reports a different `git_head`, or when its corpus completeness is
anything other than `True`.

⚠ **The fastapi row above changed on 2026-08-02, and the reason is worth
stating.** Every number published through v1.108.221 was measured against a
`fastapi/fastapi` index holding **1,000 of 1,182 eligible files** — truncated
by a file cap that was 1,000 at index time, with an empty coverage record and
nothing in the artifact able to say so. Re-measuring against the whole tree
showed the 182 dropped files were all empty `__init__.py` files under
`docs_src/`, worth zero tokens: `baseline_tokens` for fastapi is 823,784 either
way and the headline never moved. That is the good outcome, not the point. The
point is that it took a measurement to find out, and until v1.108.222 the
artifact could not have told anyone.

## Query Corpus

Five queries chosen to represent common code exploration intents:

| Query | Intent |
|-------|--------|
| `router route handler` | Core route registration / dispatch |
| `middleware` | Middleware chaining and execution |
| `error exception` | Error handling and exception propagation |
| `request response` | Request/response object definitions |
| `context bind` | Context creation and parameter binding |

These are defined in `tasks.json` for full reproducibility.

## Baseline Definition

Two baselines are measured, **in the same run, against the same corpus, through
the same file reader**. Neither is a stored constant.

**Baseline A — read-all.** All indexed source files concatenated and tokenized.
This is the **minimum** cost for a "read everything first" agent. It is a
ceiling nobody pays, and it is the basis of the historical 99.6% / 245.9x
figures.

**Baseline B — grep-top-3.** What a competent agent *without* this tool actually
does: `rg -l` the query's terms across the same corpus (paths only), rank the
matching files by match count, open the top 3 in full. Total = file-list tokens
+ 3 whole files.

⚠ **Every modelling choice in Baseline B is made in the baseline's favour, i.e.
against jCodeMunch.** A baseline its own vendor tunes is not evidence unless the
thumb is on the other side of the scale:

1. **The grep cost is `rg -l` — paths only.** That is the leanest output a real
   agent gets. `rg` *with* matched lines is also measured
   (`grep_match_lines_tokens`, 528,394 tokens across the corpus) and is strictly
   larger; using it would roughly double Baseline B and report **~49x instead of
   ~28x**. The smaller number is the published one.
2. **Files are ranked by match count.** Real grep output has no ranking — the
   agent guesses. Ranking gives the baseline a better-than-chance shot at the
   right file.
3. **Matching is case-insensitive substring on ANY term**, the most permissive
   reading, so the baseline finds more.

⚠ **Disclosed limit: Baseline B reads files WHOLE**, because that is what agents
do — this project's own PreToolUse hook exists to intercept whole-file `Read`
calls. An agent that reads a line range pays less, and **no estimator for that
is offered.** The size-proportional estimator removed from the comparison
harnesses is exactly why this project does not model an unmeasured side.

## jcodemunch Workflow

For each query:
1. Call `search_symbols(query, max_results=5)` — returns ranked symbol metadata.
2. Call `get_symbol_source()` on the top 3 matching symbol IDs — returns full source code.
3. **Total tokens** = search response tokens + 3 x symbol source tokens.

AI summaries were **disabled** during benchmarking (signature-only fallback).

## Token Counting Method

**Tokenizer:** `tiktoken` with `cl100k_base` encoding (used by GPT-4 and
compatible with Claude token estimates within ~5%).

Token counts are computed from the **serialized JSON response** strings,
not raw source bytes. This means:
- JSON field names and structure overhead are included (slightly understates savings).
- Retrieval is deterministic: the path exercised here is lexical and has no RNG.

### ⚠ The published counts carry a ±1-token jitter, and it is not removable

**Corrected 2026-08-03.** This section previously said "the count is
deterministic and reproducible across runs". That was wrong, and CI had been
saying so on every push since v1.108.222 while the claim stayed on the page.

The counted payload is the response **as an agent actually receives it**, and
that response contains `_meta.timing_ms`. Under `cl100k_base` a wall-clock
figure tokenizes to **3 tokens below 1000ms and 4 at or above it**, so a query
that straddles one second changes the measured payload by exactly one token.
A loaded CI runner straddles; a fast developer machine does not, which is why
this was invisible locally and red remotely. Observed on CI:
`search_tokens: 499 != 500`, a 0.2% move on one query of fifteen.

A second field has the same shape and has not fired yet: `_meta.total_tokens_saved`
is a **monotonic lifetime counter** for the installation. cl100k chunks digits
in threes, so it is 4 tokens from 10 to 12 digits and 5 from 13. It is at 11.

**We keep counting both**, because an agent really does pay for them and
excluding them would move every published ratio in our own favour. The
`--verify-determinism` gate instead compares a parallel `stable_tokens` figure —
the same payload counted with those two fields pinned — so it answers *is
retrieval reproducible* rather than *did the clock read the same*. A genuine
retrieval change still fails the gate; a timing straddle no longer does.

**So: reproduce a published figure to within ±1 token per query, not exactly.**
The `stable_tokens` values in the JSON output are the ones that reproduce bit
for bit.

### Distinction from runtime `_meta.tokens_saved`

The benchmark uses `tiktoken` for actual token counting. The runtime
`_meta.tokens_saved` field uses a byte approximation (`raw_bytes / 4`)
for zero-dependency speed. The byte approximation typically agrees within
~20% of `tiktoken` output for English-language code but can diverge for
non-ASCII content or heavily minified files. The `_meta` envelope includes
`"estimate_method": "byte_approx"` to make this explicit.

## Reproducing Results

Full instructions, including the pinned upstream commits, are in
[`REPRODUCING.md`](REPRODUCING.md). The short form:

```bash
pip install jcodemunch-mcp tiktoken

# Index the three repos from clones checked out at the SHAs in tasks.json.
# `index_repo` is deliberately NOT used here: it has no ref parameter and
# always fetches whatever the default branch points at today, so it cannot
# reproduce a pinned corpus.
jcodemunch-mcp index ./express --no-ai-summaries
jcodemunch-mcp index ./fastapi --no-ai-summaries
jcodemunch-mcp index ./gin     --no-ai-summaries

# Run the benchmark
python benchmarks/harness/run_benchmark.py

# Check your machine reproduces its own run before comparing to ours
python benchmarks/harness/run_benchmark.py --verify-determinism

# Write to file
python benchmarks/harness/run_benchmark.py --out benchmarks/results.md

# Refresh the artifact the comparison harnesses read
python benchmarks/harness/run_benchmark.py --reference
```

The harness script reads `tasks.json`, runs each query against each repo,
counts tokens with `tiktoken`, and outputs the markdown tables in `results.md`.

## Comparison Harnesses

`harness/run_rag_baseline.py` (LangChain RAG) and `harness/run_odysseus_compare.py`
(Odysseus `rag_server`) measure another retrieval layer live and report it beside
jCodeMunch. The jCodeMunch side of both tables is read from
[`jcm_reference.json`](jcm_reference.json), written by `run_benchmark.py --reference`.

Two rules make those tables answerable:

1. **No harness carries its own jCodeMunch numbers.** They were hardcoded until
   2026-07-29 and had gone four months stale while the other side of every ratio
   was measured fresh in each run. Re-measuring moved all three per-repo figures
   against us and flipped one published winner. A CI guard
   (`tests/test_benchmark_reference.py`) fails the build if a `JCODEMUNCH_*`
   constant reappears in a comparison harness.
2. **An unmeasured repo prints "not measured".** A repo outside the reference
   artifact gets no jCodeMunch column and no ratio. The previous fallback
   estimated jCodeMunch's cost as proportional to repo size — a number that was
   never measured, whose premise contradicts what this project claims about
   retrieval cost. It is removed, and the guard asserts it absent by name.

When the index state behind the artifact differs from the one a comparison run
measures, the affected rows are marked cross-run and the difference is printed
under the table. A ratio across two corpora is labelled, never silently divided.

## Limitations

1. **Baseline is a lower bound.** Real agents re-read files, explore
   multiple branches, and load documentation. Actual baseline costs are
   higher.
2. **Query corpus is small.** Five queries cannot represent all code
   exploration patterns. Results for specific use cases may vary.
3. **No quality measurement.** The benchmark assumes retrieved symbols
   are relevant. Retrieval precision is measured separately by
   [jMunchWorkbench](https://github.com/jgravelle/jMunchWorkbench).
4. **Single tokenizer.** Claude and GPT tokenizers produce slightly
   different counts for the same input. We use `cl100k_base` as a
   common reference point.

## Retrieval Precision

Retrieval precision (96% as reported in jMunchWorkbench) is measured by:
1. Running the same queries against the same repos.
2. Having a human evaluator judge whether the top-3 retrieved symbols
   are relevant to the query intent.
3. Precision = (relevant symbols retrieved) / (total symbols retrieved).

This evaluation is performed by jMunchWorkbench, which runs the same
prompt in two modes (baseline vs. jcodemunch) and compares answers,
tokens, and latency side-by-side.

## Replayable Retrieval-Quality Benchmark (v1.76.0+)

Token efficiency is one axis; **ranking quality** is the other. The
`benchmarks/replay/` harness measures ranking quality with three
information-retrieval metrics on a fixed query corpus:

- **nDCG@k** — Normalized Discounted Cumulative Gain (binary relevance,
  normalized by ideal DCG); rewards relevant results near the top.
- **MRR@k** — Mean Reciprocal Rank of the first relevant item in top-k.
- **Recall@k** — fraction of all relevant items present in top-k.

Fixtures are JSON files at `benchmarks/replay/fixtures/*.json` with
shape `{name, repo, repo_sha, queries: [{query, expected_top_k}]}`.
The harness (`run_replay.py`) runs each query through `search_symbols`,
computes per-query and aggregate metrics, and optionally writes
`benchmarks/replay/results/{fixture}-v{VERSION}.json`.

A regression gate (`--baseline-file results/self_v1_75_0-golden.json
--gate <harness/thresholds.json: replay.max_relative_drop>`, or the version-pinned `--baseline X.Y.Z`) fails the run if
any aggregate metric drops by more than 2% vs the baseline. The shipped
`self_v1_75_0` fixture is locked at 1.0 nDCG/MRR/Recall. This gate is
wired into CI as the `Replay` workflow (`.github/workflows/replay.yml`),
so every push to `main` and every pull request runs it against the
committed golden baseline. See the `benchmarks/replay/` source for
details.

## Common Misreadings

**"The claim is up to 99%."**
The primary claim is **96.5% average (28.9x)** across all 15 task-runs, measured
against Baseline B — an agent that greps and opens the top 3 files (664,975 →
23,014 tokens). Against Baseline A, the read-everything ceiling, the same run
gives 99.6% / 245.9x (5,658,685 → 23,014). Both are published; B is the one to
quote.

⚠ **Per-query results span 7.6x to 81.2x** (median 26.1x). No single multiple
describes every query, and the floor — `middleware` on gin — is a number a real
user can hit.

⚠ The read-all aggregate is dominated by one repo: fastapi contributes 72.9% of
its baseline tokens, so 245.9x is largely a statement about fastapi (aggregate
245.9x vs median 138.7x). **That objection is much weaker for Baseline B**, whose
aggregate and median nearly agree (28.9x vs 26.1x, fastapi share 64.1%), because
grep cost tracks matches rather than repository size. Choosing B settles the
aggregate-vs-median question rather than inheriting it.

Every number above is re-measured by the same run, and the machine-readable copy lives in
[`jcm_reference.json`](jcm_reference.json). The comparison harnesses in `harness/` read that
artifact rather than keeping their own constants — see *Comparison harnesses* below.

**"I tested a different repo and got 80%."**
Results vary by repo structure. Flat script collections (e.g., a repository of hundreds
of unrelated standalone scripts) produce lower savings because the symbol index cannot
distinguish which script is relevant — the agent still has to scan broadly. The benchmark
repos (express, fastapi, gin) are structured application codebases where symbol-based
navigation is most effective. Testing a flat script collection and comparing to our
benchmark is an apples-to-oranges comparison.

**"The benchmark is cherry-picked."**
The three repos were chosen to represent common backend frameworks across different
languages (JavaScript, Python, Go). No file filtering beyond standard skip patterns
was applied. The harness (`benchmarks/harness/run_benchmark.py`) and query corpus
(`benchmarks/tasks.json`) are open source — run them yourself and publish the results.

**"The baseline is unrealistic."**
Correct, and this is now measured rather than conceded in prose. Nobody reads the
whole repository; a competent agent greps and opens a few files. **Baseline B
measures that agent**, on the same corpus in the same run:

| Against | Baseline tokens | Ratio | Reduction |
|---|--:|--:|--:|
| Read-all (Baseline A) | 5,658,685 | 245.9x | 99.6% |
| **Grep-top-3 (Baseline B)** | **664,975** | **28.9x** | **96.5%** |

**Baseline B is 11.8% of Baseline A, so measuring against read-all overstates the
advantage by roughly 8.5x.** Quote the Baseline B figure.

⚠ **The per-query spread on B is 7.6x to 81.2x** (median 26.1x), so no single
multiple describes every query. The floor — `middleware` on gin at **7.6x** — is
the honest worst case and is published here rather than left in the JSON.

⚠ One thing that improved under the realistic baseline, and it is worth saying
because it cuts our way: the read-all aggregate is badly distorted by the largest
repo (aggregate 245.9x vs median 138.7x, with fastapi contributing 72.9% of
baseline tokens). Baseline B's aggregate and median nearly agree (**28.9x vs
26.1x**, fastapi share 64.1%), because grep cost tracks *matches* rather than
repository size. **The realistic baseline is not merely smaller — it is a
better-behaved statistic.**

So 99.6% measures **what symbol-level retrieval avoids relative to loading the
corpus** — a ceiling on removable waste, and the right answer to "how much of a
repository does an agent actually need in context." It is not a prediction of
anyone's bill. 96.5% / 28.9x is the closer thing to that, and it still does not
model an agent that was already retrieving selectively.

Against comparators that *do* retrieve selectively, the margins are single-digit
multiples, and both sides are measured on the same corpus in the same run:

| Comparator | express | fastapi | gin | Report |
|---|--:|--:|--:|---|
| Tuned RAG pipeline, best chunk size | 3.2x | 1.3x | 2.5x | [`rag_baseline_results.md`](rag_baseline_results.md) |
| Embedding retrieval layer (chunk-based) | 1.2x | **0.2x** | **0.9x** | [`odysseus_compare_results.md`](odysseus_compare_results.md) |

The bolded cells are ones where the comparator injects **fewer** tokens than
jCodeMunch, and they are published rather than dropped. Read them next to that
report's `complete/5` and `split/5` columns: chunk-based retrieval buys its token
saving by returning fragments cut mid-definition, where jCodeMunch returns whole
symbols. Cheaper context that stops mid-function is not automatically better context,
but it is genuinely cheaper, and the table says so.

There is a narrower sense in which the baseline is conservative, and it only applies
*within* the read-everything comparison: we count one pass through each file. A real
agent that reads broadly re-reads files, branches across sessions, and loads
documentation, so its true cost is higher than the figure here. That makes 99.6% a
floor **for that class of agent** — which is a different claim from a floor in
general, and it should not be read as one.
