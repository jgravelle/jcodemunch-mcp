# Competitive tier — 2026-09-05T12:26:53Z at cdc6542 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@cdc6542` 275 files, sha256 `b70fbed3c3a3`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@cdc6542 (ran as cdc6542)

## tokens_per_task (ratio vs jcm)

| tool | self@cdc6542 |
|---|---|
| null_readall | 9.401e+05 (delta 464, band 101, MEANINGFUL) spread 0 |
| null_grep | 3.663e+04 (delta 18.1, band 101, MEANINGFUL) spread 0 |
| jcodemunch | 2026 spread 0 |

## calls_per_task (ratio vs jcm)

| tool | self@cdc6542 |
|---|---|
| null_readall | 275 (delta 68.8, band 0.2, MEANINGFUL) spread 0 |
| null_grep | 3.75 (delta 0.938, band 0.2, MEANINGFUL) spread 0 |
| jcodemunch | 4 spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@cdc6542 |
|---|---|
| null_readall | 35.96 (delta 1.57, band 16.1) spread 5.37 [unstable: a spread exceeds 10% of its own median] |
| null_grep | 0.34 (delta 0.0149, band 2.22) spread 0.11 [unstable: a spread exceeds 10% of its own median] |
| jcodemunch | 22.88 spread 0.74 |

## index_cold_seconds (ratio vs jcm)

| tool | self@cdc6542 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 3.106 spread 0.193 |

## tools_list_tokens (ratio vs jcm)

| tool | self@cdc6542 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.274e+04 spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@cdc6542 |
|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |
| null_grep | 0.2669 (delta -0.0664, band 0.0167, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 |
