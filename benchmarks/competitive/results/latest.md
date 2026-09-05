# Competitive tier — 2026-09-05T21:11:15Z at 975e2a83 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@975e2a83` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `286b5e3d3b4f`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@975e2a83 (ran as 975e2a83, image `bcf982ba456a`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `48635c4c2260`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `870430b492e8`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `ece6f4ec3af2`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `08a1f03a473b`); `codegraph` github-release:colbymchenry/codegraph@1.6.0 (ran as 1.6.0, image `3583f323855e`); `graft` npm:@nanonets/graft@0.16.0 (ran as 0.16.0, image `f11e52a9b493`)

## tokens_per_task (ratio vs jcm)

| tool | self@975e2a83 |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 880.8 (delta 0.532, band 82.8, MEANINGFUL) spread 0 |
| codebase_memory | 980.6 (delta 0.592, band 82.8, MEANINGFUL) spread 0 |
| code_review_graph | 324.8 (delta 0.196, band 82.8, MEANINGFUL) spread 0 |
| serena | 1.178e+04 (delta 7.12, band 82.8, MEANINGFUL) spread 0 |
| codegraph | 3119 (delta 1.88, band 82.8, MEANINGFUL) spread 0 |
| graft | 1482 (delta 0.895, band 82.8, MEANINGFUL) spread 0 |

## calls_per_task (ratio vs jcm)

| tool | self@975e2a83 |
|---|---|
| null_readall | 277 (delta 81.5, band 0.17, MEANINGFUL) spread 0 |
| null_grep | 3.6 (delta 1.06, band 0.17, MEANINGFUL) spread 0 |
| jcodemunch | 3.4 spread 0 |
| cymbal | 2.5 (delta 0.735, band 0.17, MEANINGFUL) spread 0 |
| codebase_memory | 3.2 (delta 0.941, band 0.17, MEANINGFUL) spread 0 |
| code_review_graph | 1.1 (delta 0.324, band 0.17, MEANINGFUL) spread 0 |
| serena | 1.444 (delta 0.425, band 0.17, MEANINGFUL) spread 0 |
| codegraph | 1.3 (delta 0.382, band 0.17, MEANINGFUL) spread 0 |
| graft | 1.1 (delta 0.324, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@975e2a83 |
|---|---|
| null_readall | 36.24 (delta 0.686, band 5.46, MEANINGFUL) spread 1 |
| null_grep | 1.38 (delta 0.0261, band 5.46) spread 0.25 [unstable: a spread exceeds 10% of its own median] |
| jcodemunch | 52.83 spread 1.82 |
| cymbal | 706 (delta 13.4, band 96, MEANINGFUL) spread 32 |
| codebase_memory | 17.13 (delta 0.324, band 5.46, MEANINGFUL) spread 0.74 |
| code_review_graph | 138.3 (delta 2.62, band 157) spread 52.4 [unstable: a spread exceeds 10% of its own median] |
| serena | 6372 (delta 121, band 4.02e+03) spread 1.34e+03 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 4.3 (delta 0.0814, band 15.5) spread 5.18 [unstable: a spread exceeds 10% of its own median] |
| graft | 1001 (delta 18.9, band 835) spread 278 [unstable: a spread exceeds 10% of its own median] |

## index_cold_seconds (ratio vs jcm)

| tool | self@975e2a83 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 16.11 spread 0.266 |
| cymbal | 1.996 (delta 0.124, band 1.12) spread 0.372 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 6.585 (delta 0.409, band 1.34, MEANINGFUL) spread 0.445 |
| code_review_graph | 66.11 (delta 4.1, band 5.15, MEANINGFUL) spread 1.72 |
| serena | 8.858 (delta 0.55, band 1.2, MEANINGFUL) spread 0.4 |
| codegraph | 1.922 (delta 0.119, band 0.805) spread 0.251 [unstable: a spread exceeds 10% of its own median] |
| graft | 9.888 (delta 0.614, band 0.805, MEANINGFUL) spread 0.0121 |

## tools_list_tokens (ratio vs jcm)

| tool | self@975e2a83 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.365e+04 spread 0 |
| cymbal | NOT COMPARABLE |
| codebase_memory | 4791 (delta 0.203, band 1.18e+03, MEANINGFUL) spread 0 |
| code_review_graph | 7694 (delta 0.325, band 1.18e+03, MEANINGFUL) spread 0 |
| serena | 6476 (delta 0.274, band 1.18e+03, MEANINGFUL) spread 0 |
| codegraph | 1293 (delta 0.0547, band 1.18e+03, MEANINGFUL) spread 0 |
| graft | 761 (delta 0.0322, band 1.18e+03, MEANINGFUL) spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@975e2a83 |
|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |
| null_grep | 0.2299 (delta -0.103, band 0.0167, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 |
| cymbal | 0.2606 (delta -0.0727, band 0.0167, MEANINGFUL) spread 0 |
| codebase_memory | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 |
| code_review_graph | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 |
| serena | 1 (delta 0.667, band 0.0167, MEANINGFUL) spread 0 |
| codegraph | 0.5 (delta 0.167, band 0.0167, MEANINGFUL) spread 0 |
| graft | 0.3333 (delta 0, band 0.0167) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@975e2a83 |
|---|---|
| null_readall | 0 (delta 0, band 0) spread 0 |
| null_grep | 0.1818 (delta 0.182, band 0, MEANINGFUL) spread 0 |
| jcodemunch | 0 spread 0 |
| cymbal | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| codebase_memory | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| code_review_graph | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| serena | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| codegraph | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| graft | 1 (delta 1, band 0, MEANINGFUL) spread 0 |

## f1_P4 (difference vs jcm)

| tool | self@975e2a83 |
|---|---|
| null_readall | 0.0005 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| null_grep | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| jcodemunch | 0.4324 spread 0 |
| cymbal | 0.4737 (delta 0.0413, band 0.0216, MEANINGFUL) spread 0 |
| codebase_memory | 0.9825 (delta 0.55, band 0.0216, MEANINGFUL) spread 0 |
| code_review_graph | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| serena | NOT COMPARABLE |
| codegraph | 0.0541 (delta -0.378, band 0.0216, MEANINGFUL) spread 0 |
| graft | 0.4286 (delta -0.0038, band 0.0216) spread 0 |
