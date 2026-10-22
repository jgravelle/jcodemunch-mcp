# jcodemunch-mcp -- Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)  
**Workflow:** `search_symbols` (top 5) + `get_symbol` x 3  
**Baseline A (read-all):** all source files concatenated  
**Baseline B (grep-top-3):** `rg -l` the query terms, then open the top 3 files whole  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **186** |
| Symbols extracted | **989** |
| Baseline tokens (all files) | **154,569** |
| Upstream commit | `1faf228935aa` (pinned) |
| Corpus complete | yes |

| Query | Read-all&nbsp;tokens | Grep-top-3&nbsp;tokens | jMunch&nbsp;tokens | Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |
|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|
| `router route handler` | 154,569 | 9,890 | 1,074 | 143.9x | **9.21x** |
| `middleware` | 154,569 | 11,777 | 1,159 | 133.4x | **10.16x** |
| `error exception` | 154,569 | 18,961 | 1,139 | 135.7x | **16.65x** |
| `request response` | 154,569 | 21,620 | 1,279 | 120.9x | **16.9x** |
| `context bind` | 154,569 | 16,371 | 297 | 520.4x | **55.12x** |
| **Average** | — | — | — | 210.9x | **21.6x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 459 | 615 | 3 | 68.5 |
| `middleware` | 464 | 695 | 3 | 1.8 |
| `error exception` | 474 | 665 | 3 | 3.0 |
| `request response` | 537 | 742 | 3 | 3.5 |
| `context bind` | 297 | 0 | 0 | 34.2 |

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
| `router route handler` | 825,326 | 97,495 | 1,440 | 573.1x | **67.7x** |
| `middleware` | 825,326 | 36,575 | 1,918 | 430.3x | **19.07x** |
| `error exception` | 825,326 | 100,987 | 1,309 | 630.5x | **77.15x** |
| `request response` | 825,326 | 130,461 | 4,778 | 172.7x | **27.3x** |
| `context bind` | 825,326 | 60,963 | 3,334 | 247.5x | **18.29x** |
| **Average** | — | — | — | 410.8x | **41.9x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 567 | 873 | 3 | 1420.5 |
| `middleware` | 623 | 1,295 | 3 | 2.9 |
| `error exception` | 558 | 751 | 3 | 4.5 |
| `request response` | 571 | 4,207 | 3 | 11.0 |
| `context bind` | 580 | 2,754 | 3 | 4.7 |

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
| `router route handler` | 151,842 | 18,789 | 1,541 | 98.5x | **12.19x** |
| `middleware` | 151,842 | 13,190 | 1,733 | 87.6x | **7.61x** |
| `error exception` | 151,842 | 44,021 | 1,123 | 135.2x | **39.2x** |
| `request response` | 151,842 | 39,929 | 1,761 | 86.2x | **22.67x** |
| `context bind` | 151,842 | 43,946 | 1,247 | 121.8x | **35.24x** |
| **Average** | — | — | — | 105.9x | **23.4x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 547 | 994 | 3 | 150.1 |
| `middleware` | 445 | 1,288 | 3 | 2.4 |
| `error exception` | 479 | 644 | 3 | 3.9 |
| `request response` | 649 | 1,112 | 3 | 3.9 |
| `context bind` | 520 | 727 | 3 | 7.2 |

</details>

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline A total, read-all (15 task-runs) | 5,658,685 |
| Baseline B total, grep-top-3 | 664,975 |
| jMunch total | 25,132 |
| Reduction vs read-all | 99.6% |
| Ratio vs read-all | 225.2x |
| **Reduction vs grep-top-3** | **96.2%** |
| **Ratio vs grep-top-3** | **26.5x** |

> **Baseline B is the number to quote.** Read-all is a ceiling nobody pays: it assumes an agent opens every file in the repository before acting. Grep-then-read is what a competent agent without this tool actually does, and it is 11.8% of the read-all figure — so measuring against read-all overstates the advantage by about 9x.

> Measured with tiktoken `cl100k_base`. Read-all = every indexed source file. Grep-top-3 = `rg -l` the query terms, then open the top 3 matching files whole. jMunch = search_symbols (top 5) + get_symbol x 3 per query. Both baselines are measured in THIS run against THIS corpus.