# jcodemunch-mcp -- Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)  
**Workflow:** `search_symbols` (top 5) + `get_symbol` x 3  
**Baseline:** all source files concatenated (minimum for "open every file" agent)  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **34** |
| Symbols extracted | **117** |
| Baseline tokens (all files) | **73,838** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 73,838 | 1,221 | **98.3%** | 60.5x |
| `middleware` | 73,838 | 1,360 | **98.2%** | 54.3x |
| `error exception` | 73,838 | 1,381 | **98.1%** | 53.5x |
| `request response` | 73,838 | 1,699 | **97.7%** | 43.5x |
| `context bind` | 73,838 | 169 | **99.8%** | 436.9x |
| **Average** | — | — | **98.4%** | **129.7x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 416 | 805 | 3 | 3.1 |
| `middleware` | 348 | 1,012 | 3 | 2.4 |
| `error exception` | 430 | 951 | 3 | 60.7 |
| `request response` | 484 | 1,215 | 3 | 2.5 |
| `context bind` | 169 | 0 | 0 | 2.2 |

</details>

## fastapi/fastapi

| Metric | Value |
|--------|-------|
| Files indexed | **156** |
| Symbols extracted | **1,359** |
| Baseline tokens (all files) | **214,312** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 214,312 | 43,474 | **79.7%** | 4.9x |
| `middleware` | 214,312 | 24,271 | **88.7%** | 8.8x |
| `error exception` | 214,312 | 2,233 | **99.0%** | 96.0x |
| `request response` | 214,312 | 5,966 | **97.2%** | 35.9x |
| `context bind` | 214,312 | 2,102 | **99.0%** | 102.0x |
| **Average** | — | — | **92.7%** | **49.5x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 2,827 | 40,647 | 3 | 117.5 |
| `middleware` | 7,106 | 17,165 | 3 | 41.6 |
| `error exception` | 579 | 1,654 | 3 | 57.9 |
| `request response` | 770 | 5,196 | 3 | 156.5 |
| `context bind` | 683 | 1,419 | 3 | 16.8 |

</details>

## gin-gonic/gin

| Metric | Value |
|--------|-------|
| Files indexed | **40** |
| Symbols extracted | **805** |
| Baseline tokens (all files) | **84,892** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 84,892 | 1,355 | **98.4%** | 62.7x |
| `middleware` | 84,892 | 2,178 | **97.4%** | 39.0x |
| `error exception` | 84,892 | 1,470 | **98.3%** | 57.7x |
| `request response` | 84,892 | 1,642 | **98.1%** | 51.7x |
| `context bind` | 84,892 | 1,994 | **97.7%** | 42.6x |
| **Average** | — | — | **98.0%** | **50.7x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 467 | 888 | 3 | 23.2 |
| `middleware` | 728 | 1,450 | 3 | 130.8 |
| `error exception` | 489 | 981 | 3 | 8.9 |
| `request response` | 562 | 1,080 | 3 | 8.7 |
| `context bind` | 509 | 1,485 | 3 | 109.0 |

</details>

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline total (15 task-runs) | 1,865,210 |
| jMunch total | 92,515 |
| **Reduction** | **95.0%** |
| **Ratio** | **20.2x** |

> Measured with tiktoken `cl100k_base`. Baseline = all indexed source files. jMunch = search_symbols (top 5) + get_symbol x 3 per query.