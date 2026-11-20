# jcodemunch-mcp -- Token Efficiency Benchmark

**Methodology change, 2026-11-08.** From this run the benchmark counts the text the server sends on a default install: the compact encoding, with the `_meta` block dropped. Earlier runs counted the JSON the tool functions return, `_meta` included, which an agent never receives. One query changed too: "context bind", which returned no symbols on express, is now "param bind", so every one of the 15 lookups returns symbols. That run's figure was 96.2% fewer tokens per lookup on average than grep-and-read. The figure published before this change was 94.5%, measured 2026-10-22 with the earlier count and query ([rows](https://github.com/jgravelle/jcodemunch-mcp/blob/f817f6fc99cf7d8354f12767742850fb1ec5217a/benchmarks/results.md)). The two figures use different counts and queries, so they are not a before-and-after pair.

**Long sessions, 2026-11-19.** Each lookup in this benchmark starts with a fresh turn budget, as a single lookup does. In a long session with no 30-second pause, the server adds a budget warning to every reply once the session has used 80% of the default 20,000-token turn budget. Past 20,000 the warning costs about 24 tokens a reply (475 tokens over gin's 20 calls in CI run 44423477974). This benchmark does not count it.

**Tokenizer:** `cl100k_base` (tiktoken)  
**Workflow:** `search_symbols` (top 5) + `get_symbol` x 3  
**Counted:** the response text the server serves on a default install (`server.call_tool`, no config file)  
**Once per session, not in any lookup:** license notice 163 tokens (first answer on a machine), steering hint 66 tokens (one search)  
**Each repo's lookups as one session instead:** expressjs/express +0, fastapi/fastapi +0, gin-gonic/gin +0 tokens (the note on a symbol an earlier answer already delivered)  
**Baseline A (read-all):** all source files concatenated  
**Baseline B (grep-top-3):** `rg -l` the query terms, then open the top 3 files whole  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **186** |
| Symbols extracted | **1,085** |
| Baseline tokens (all files) | **154,569** |
| Upstream commit | `1faf228935aa` (pinned) |
| Corpus complete | yes |

| Query | Read-all&nbsp;tokens | Grep-top-3&nbsp;tokens | jMunch&nbsp;tokens | Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |
|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|
| `router route handler` | 154,569 | 9,890 | 506 | 305.5x | **19.55x** |
| `middleware` | 154,569 | 11,777 | 633 | 244.2x | **18.61x** |
| `error exception` | 154,569 | 18,961 | 645 | 239.6x | **29.4x** |
| `request response` | 154,569 | 21,620 | 990 | 156.1x | **21.84x** |
| `param bind` | 154,569 | 12,954 | 942 | 164.1x | **13.75x** |
| **Average** | — | — | — | 221.9x | **20.6x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 212 | 294 | 3 | 65.0 |
| `middleware` | 213 | 420 | 3 | 6.5 |
| `error exception` | 209 | 436 | 3 | 6.9 |
| `request response` | 243 | 747 | 3 | 7.7 |
| `param bind` | 201 | 741 | 3 | 8.0 |

</details>

## fastapi/fastapi

| Metric | Value |
|--------|-------|
| Files indexed | **1,186** |
| Symbols extracted | **13,699** |
| Baseline tokens (all files) | **825,326** |
| Upstream commit | `a64dfbbd21a4` (pinned) |
| Corpus complete | yes |

| Query | Read-all&nbsp;tokens | Grep-top-3&nbsp;tokens | jMunch&nbsp;tokens | Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |
|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|
| `router route handler` | 825,326 | 97,495 | 1,782 | 463.1x | **54.71x** |
| `middleware` | 825,326 | 36,575 | 1,304 | 632.9x | **28.05x** |
| `error exception` | 825,326 | 100,987 | 762 | 1083.1x | **132.53x** |
| `request response` | 825,326 | 130,461 | 4,199 | 196.6x | **31.07x** |
| `param bind` | 825,326 | 68,049 | 554 | 1489.8x | **122.83x** |
| **Average** | — | — | — | 773.1x | **73.8x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 275 | 1,507 | 3 | 482.2 |
| `middleware` | 327 | 977 | 3 | 7.3 |
| `error exception` | 241 | 521 | 3 | 9.3 |
| `request response` | 304 | 3,895 | 3 | 83.8 |
| `param bind` | 205 | 349 | 3 | 9.9 |

</details>

## gin-gonic/gin

| Metric | Value |
|--------|-------|
| Files indexed | **98** |
| Symbols extracted | **1,822** |
| Baseline tokens (all files) | **151,842** |
| Upstream commit | `75ccf94d605a` (pinned) |
| Corpus complete | yes |

| Query | Read-all&nbsp;tokens | Grep-top-3&nbsp;tokens | jMunch&nbsp;tokens | Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |
|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|
| `router route handler` | 151,842 | 18,789 | 958 | 158.5x | **19.61x** |
| `middleware` | 151,842 | 13,190 | 1,168 | 130.0x | **11.29x** |
| `error exception` | 151,842 | 44,021 | 557 | 272.6x | **79.03x** |
| `request response` | 151,842 | 39,929 | 1,074 | 141.4x | **37.18x** |
| `param bind` | 151,842 | 45,334 | 642 | 236.5x | **70.61x** |
| **Average** | — | — | — | 187.8x | **43.5x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 279 | 679 | 3 | 143.5 |
| `middleware` | 207 | 961 | 3 | 28.5 |
| `error exception` | 241 | 316 | 3 | 7.2 |
| `request response` | 289 | 785 | 3 | 8.2 |
| `param bind` | 251 | 391 | 3 | 9.0 |

</details>

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline A total, read-all (15 task-runs) | 5,658,685 |
| Baseline B total, grep-top-3 | 670,032 |
| jMunch total | 16,716 |
| Reduction vs read-all | 99.7% |
| Ratio vs read-all | 338.5x |
| **Mean reduction per lookup vs grep-top-3** (15 lookups) | **96.3%** |
| Reduction vs grep-top-3, pooled totals | 97.5% |
| Ratio vs grep-top-3, pooled totals | 40.1x |

> **Baseline B is the number to quote**, as the mean reduction per lookup. The pooled totals weigh the lookups with the largest grep baselines most. Read-all is a ceiling nobody pays: it assumes an agent opens every file in the repository before acting. Grep-then-read is what a competent agent without this tool actually does, and it is 11.8% of the read-all figure, so measuring against read-all overstates the advantage by about 8x.

> Measured with tiktoken `cl100k_base`. Read-all = every indexed source file. Grep-top-3 = `rg -l` the query terms, then open the top 3 matching files whole. jMunch = search_symbols (top 5) + get_symbol x 3 per query. Both baselines are measured in THIS run against THIS corpus.