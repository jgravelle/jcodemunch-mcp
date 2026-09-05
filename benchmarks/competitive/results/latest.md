# Competitive tier — 2026-09-05T15:40:17Z at 95eb4a00 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@95eb4a00` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `bed2017a669b`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@95eb4a00 (ran as 95eb4a00, image `ea0f586886a5`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `d037ea1adfc3`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `e4f0d1b15870`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `3bea622df820`)

## tokens_per_task (ratio vs jcm)

| tool | self@95eb4a00 |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 880.8 (delta 0.532, band 82.8, MEANINGFUL) spread 0.1 |
| codebase_memory | 980.6 (delta 0.592, band 82.8, MEANINGFUL) spread 0 |
| code_review_graph | 324.8 (delta 0.196, band 82.8, MEANINGFUL) spread 0 |

## calls_per_task (ratio vs jcm)

| tool | self@95eb4a00 |
|---|---|
| null_readall | 277 (delta 81.5, band 0.17, MEANINGFUL) spread 0 |
| null_grep | 3.6 (delta 1.06, band 0.17, MEANINGFUL) spread 0 |
| jcodemunch | 3.4 spread 0 |
| cymbal | 2.5 (delta 0.735, band 0.17, MEANINGFUL) spread 0 |
| codebase_memory | 3.2 (delta 0.941, band 0.17, MEANINGFUL) spread 0 |
| code_review_graph | 1.1 (delta 0.324, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@95eb4a00 |
|---|---|
| null_readall | 35.52 (delta 0.668, band 10.5, MEANINGFUL) spread 0.32 |
| null_grep | 1.29 (delta 0.0242, band 10.5, MEANINGFUL) spread 0.05 |
| jcodemunch | 53.2 spread 3.49 |
| cymbal | 710 (delta 13.3, band 21, MEANINGFUL) spread 7 |
| codebase_memory | 17.66 (delta 0.332, band 25.2) spread 8.39 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 108.3 (delta 2.04, band 179) spread 59.8 [unstable: a spread exceeds 10% of its own median] |

## index_cold_seconds (ratio vs jcm)

| tool | self@95eb4a00 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 17.11 spread 1.74 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 2.033 (delta 0.119, band 5.23) spread 0.153 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 6.167 (delta 0.36, band 7.85) spread 2.62 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 66.23 (delta 3.87, band 22.8) spread 7.6 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@95eb4a00 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.365e+04 spread 0 |
| cymbal | NOT COMPARABLE |
| codebase_memory | 4791 (delta 0.203, band 1.18e+03, MEANINGFUL) spread 0 |
| code_review_graph | 7694 (delta 0.325, band 1.18e+03, MEANINGFUL) spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@95eb4a00 |
|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |
| null_grep | 0.2299 (delta -0.103, band 0.0167, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 |
| cymbal | 0.2606 (delta -0.0727, band 0.0167, MEANINGFUL) spread 0 |
| codebase_memory | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 |
| code_review_graph | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@95eb4a00 |
|---|---|
| null_readall | 0 (delta 0, band 0) spread 0 |
| null_grep | 0.1818 (delta 0.182, band 0, MEANINGFUL) spread 0 |
| jcodemunch | 0 spread 0 |
| cymbal | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| codebase_memory | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| code_review_graph | 1 (delta 1, band 0, MEANINGFUL) spread 0 |

## f1_P4 (difference vs jcm)

| tool | self@95eb4a00 |
|---|---|
| null_readall | 0.0005 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| null_grep | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| jcodemunch | 0.4324 spread 0 |
| cymbal | 0.4737 (delta 0.0413, band 0.0216, MEANINGFUL) spread 0 |
| codebase_memory | 0.9825 (delta 0.55, band 0.0216, MEANINGFUL) spread 0 |
| code_review_graph | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
