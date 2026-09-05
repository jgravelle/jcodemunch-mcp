# Competitive tier — 2026-09-05T17:45:12Z at 64e59032 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@64e59032` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `cf85f138f68b`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@64e59032 (ran as 64e59032, image `9090404d212e`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `8f92fb7358d6`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `33971f17f299`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `2be31a72453b`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `4d5db0a0661a`)

## tokens_per_task (ratio vs jcm)

| tool | self@64e59032 |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 880.8 (delta 0.532, band 82.8, MEANINGFUL) spread 0.1 |
| codebase_memory | 980.6 (delta 0.592, band 82.8, MEANINGFUL) spread 26.5 |
| code_review_graph | 324.8 (delta 0.196, band 82.8, MEANINGFUL) spread 0 |
| serena | 1.178e+04 (delta 7.12, band 82.8, MEANINGFUL) spread 0 |

## calls_per_task (ratio vs jcm)

| tool | self@64e59032 |
|---|---|
| null_readall | 277 (delta 81.5, band 0.17, MEANINGFUL) spread 0 |
| null_grep | 3.6 (delta 1.06, band 0.17, MEANINGFUL) spread 0 |
| jcodemunch | 3.4 spread 0 |
| cymbal | 2.5 (delta 0.735, band 0.17, MEANINGFUL) spread 0 |
| codebase_memory | 3.2 (delta 0.941, band 0.17, MEANINGFUL) spread 0 |
| code_review_graph | 1.1 (delta 0.324, band 0.17, MEANINGFUL) spread 0 |
| serena | 1.444 (delta 0.425, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@64e59032 |
|---|---|
| null_readall | 35.08 (delta 0.682, band 10.6, MEANINGFUL) spread 0.92 |
| null_grep | 1.32 (delta 0.0257, band 10.6, MEANINGFUL) spread 0.03 |
| jcodemunch | 51.41 spread 3.52 |
| cymbal | 702 (delta 13.7, band 171, MEANINGFUL) spread 57 |
| codebase_memory | 17.66 (delta 0.344, band 10.6) spread 3.48 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 103.7 (delta 2.02, band 20.7, MEANINGFUL) spread 6.91 |
| serena | 6176 (delta 120, band 905, MEANINGFUL) spread 302 |

## index_cold_seconds (ratio vs jcm)

| tool | self@64e59032 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 16.28 spread 1.09 |
| cymbal | 1.917 (delta 0.118, band 3.35) spread 1.12 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 6.226 (delta 0.382, band 3.27) spread 0.779 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 64.74 (delta 3.98, band 9.21, MEANINGFUL) spread 3.07 |
| serena | 8.03 (delta 0.493, band 3.65) spread 1.22 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@64e59032 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.365e+04 spread 0 |
| cymbal | NOT COMPARABLE |
| codebase_memory | 4791 (delta 0.203, band 1.18e+03, MEANINGFUL) spread 0 |
| code_review_graph | 7694 (delta 0.325, band 1.18e+03, MEANINGFUL) spread 0 |
| serena | 6476 (delta 0.274, band 1.18e+03, MEANINGFUL) spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@64e59032 |
|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |
| null_grep | 0.2299 (delta -0.103, band 0.0167, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 |
| cymbal | 0.2606 (delta -0.0727, band 0.0167, MEANINGFUL) spread 0 |
| codebase_memory | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 |
| code_review_graph | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 |
| serena | 1 (delta 0.667, band 0.0167, MEANINGFUL) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@64e59032 |
|---|---|
| null_readall | 0 (delta 0, band 0) spread 0 |
| null_grep | 0.1818 (delta 0.182, band 0, MEANINGFUL) spread 0 |
| jcodemunch | 0 spread 0 |
| cymbal | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| codebase_memory | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| code_review_graph | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| serena | 1 (delta 1, band 0, MEANINGFUL) spread 0 |

## f1_P4 (difference vs jcm)

| tool | self@64e59032 |
|---|---|
| null_readall | 0.0005 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| null_grep | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| jcodemunch | 0.4324 spread 0 |
| cymbal | 0.4737 (delta 0.0413, band 0.0216, MEANINGFUL) spread 0 |
| codebase_memory | 0.9825 (delta 0.55, band 0.0216, MEANINGFUL) spread 0 |
| code_review_graph | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| serena | NOT COMPARABLE |
