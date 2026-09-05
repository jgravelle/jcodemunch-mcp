# Competitive tier — 2026-09-05T22:28:13Z at cd9ad926 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@cd9ad926` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `da0b87147617`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@cd9ad926 (ran as cd9ad926, image `bb110d79ec4f`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `4fd9da49251f`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `420deed78818`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `2cfb1c9e8617`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `e887d86b5cfd`); `codegraph` github-release:colbymchenry/codegraph@1.6.0 (ran as 1.6.0, image `a962c69316f5`); `graft` npm:@nanonets/graft@0.16.0 (ran as 0.16.0, image `0da028ac1cf9`)

## tokens_per_task (ratio vs jcm)

| tool | self@cd9ad926 |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 881 (delta 0.532, band 82.8, MEANINGFUL) spread 17.7 |
| codebase_memory | 980.6 (delta 0.592, band 82.8, MEANINGFUL) spread 0 |
| code_review_graph | 324.8 (delta 0.196, band 82.8, MEANINGFUL) spread 0 |
| serena | 1.178e+04 (delta 7.12, band 82.8, MEANINGFUL) spread 0 |
| codegraph | 3119 (delta 1.88, band 82.8, MEANINGFUL) spread 0 |
| graft | 1482 (delta 0.895, band 82.8, MEANINGFUL) spread 0 |

## calls_per_task (ratio vs jcm)

| tool | self@cd9ad926 |
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

| tool | self@cd9ad926 |
|---|---|
| null_readall | 33.46 (delta 0.651, band 16) spread 5.04 [unstable: a spread exceeds 10% of its own median] |
| null_grep | 1.17 (delta 0.0227, band 16) spread 0.25 [unstable: a spread exceeds 10% of its own median] |
| jcodemunch | 51.43 spread 5.34 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 631 (delta 12.3, band 177) spread 59 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 17.98 (delta 0.35, band 16) spread 0.46 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 110.5 (delta 2.15, band 84.3) spread 28.1 [unstable: a spread exceeds 10% of its own median] |
| serena | 2809 (delta 54.6, band 211) spread 70.3 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 3.78 (delta 0.0735, band 16) spread 2.71 [unstable: a spread exceeds 10% of its own median] |
| graft | 823.5 (delta 16, band 319) spread 106 [unstable: a spread exceeds 10% of its own median] |

## index_cold_seconds (ratio vs jcm)

| tool | self@cd9ad926 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 16.74 spread 3.57 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 1.75 (delta 0.104, band 10.7) spread 0.512 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 6.102 (delta 0.364, band 10.7) spread 0.702 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 58.52 (delta 3.5, band 10.7) spread 3.56 [unstable: a spread exceeds 10% of its own median] |
| serena | 3.574 (delta 0.213, band 10.7) spread 0.724 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 1.484 (delta 0.0886, band 10.7) spread 0.226 [unstable: a spread exceeds 10% of its own median] |
| graft | 8.392 (delta 0.501, band 10.7) spread 0.91 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@cd9ad926 |
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

| tool | self@cd9ad926 |
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

| tool | self@cd9ad926 |
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

| tool | self@cd9ad926 |
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
