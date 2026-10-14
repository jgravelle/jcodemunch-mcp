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
| `router route handler` | 154,569 | 9,890 | 1,106 | 139.8x | **8.94x** |
| `middleware` | 154,569 | 11,777 | 1,159 | 133.4x | **10.16x** |
| `error exception` | 154,569 | 18,961 | 1,139 | 135.7x | **16.65x** |
| `request response` | 154,569 | 21,620 | 1,158 | 133.5x | **18.67x** |
| `context bind` | 154,569 | 16,371 | 297 | 520.4x | **55.12x** |
| **Average** | — | — | — | 212.6x | **21.9x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 468 | 638 | 3 | 60.2 |
| `middleware` | 464 | 695 | 3 | 1.5 |
| `error exception` | 474 | 665 | 3 | 2.3 |
| `request response` | 466 | 692 | 3 | 2.5 |
| `context bind` | 297 | 0 | 0 | 37.1 |

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
| `router route handler` | 825,326 | 97,495 | 1,577 | 523.4x | **61.82x** |
| `middleware` | 825,326 | 36,575 | 1,844 | 447.6x | **19.83x** |
| `error exception` | 825,326 | 100,987 | 1,244 | 663.4x | **81.18x** |
| `request response` | 825,326 | 130,461 | 4,685 | 176.2x | **27.85x** |
| `context bind` | 825,326 | 60,963 | 1,383 | 596.8x | **44.08x** |
| **Average** | — | — | — | 481.5x | **47.0x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 609 | 968 | 3 | 1245.9 |
| `middleware` | 557 | 1,287 | 3 | 2.6 |
| `error exception` | 521 | 723 | 3 | 3.7 |
| `request response` | 535 | 4,150 | 3 | 8.7 |
| `context bind` | 566 | 817 | 3 | 4.0 |

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
| `error exception` | 151,842 | 44,021 | 1,095 | 138.7x | **40.2x** |
| `request response` | 151,842 | 39,929 | 1,366 | 111.2x | **29.23x** |
| `context bind` | 151,842 | 43,946 | 1,687 | 90.0x | **26.05x** |
| **Average** | — | — | — | 105.2x | **23.1x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 547 | 994 | 3 | 131.5 |
| `middleware` | 445 | 1,288 | 3 | 2.0 |
| `error exception` | 463 | 632 | 3 | 2.7 |
| `request response` | 492 | 874 | 3 | 3.0 |
| `context bind` | 495 | 1,192 | 3 | 5.7 |

</details>

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline A total, read-all (15 task-runs) | 5,658,685 |
| Baseline B total, grep-top-3 | 664,975 |
| jMunch total | 23,014 |
| Reduction vs read-all | 99.6% |
| Ratio vs read-all | 245.9x |
| **Reduction vs grep-top-3** | **96.5%** |
| **Ratio vs grep-top-3** | **28.9x** |

> **Baseline B is the number to quote.** Read-all is a ceiling nobody pays: it assumes an agent opens every file in the repository before acting. Grep-then-read is what a competent agent without this tool actually does, and it is 11.8% of the read-all figure — so measuring against read-all overstates the advantage by about 9x.

> Measured with tiktoken `cl100k_base`. Read-all = every indexed source file. Grep-top-3 = `rg -l` the query terms, then open the top 3 matching files whole. jMunch = search_symbols (top 5) + get_symbol x 3 per query. Both baselines are measured in THIS run against THIS corpus.