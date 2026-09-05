# Competitive tier — 2026-09-05T23:03:21Z at 6215a70c (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@6215a70c` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `7d477e780943`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@6215a70c (ran as 6215a70c, image `5d9537a9fbba`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `6ac70cdf8d0b`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `a59ce722480a`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `e35dee5863a0`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `9e1fd8abb8c3`); `codegraph` github-release:colbymchenry/codegraph@1.6.0 (ran as 1.6.0, image `558a6fe63e40`); `graft` npm:@nanonets/graft@0.16.0 (ran as 0.16.0, image `ce655492a0a2`); `aider` pypi:aider-chat@0.86.2 (ran as 0.86.2, image `ce9377827fc2`)

## tokens_per_task (ratio vs jcm)

| tool | self@6215a70c |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 881 (delta 0.532, band 82.8, MEANINGFUL) spread 0.3 |
| codebase_memory | 980.6 (delta 0.592, band 82.8, MEANINGFUL) spread 26.5 |
| code_review_graph | 324.8 (delta 0.196, band 82.8, MEANINGFUL) spread 0 |
| serena | 1.178e+04 (delta 7.12, band 82.8, MEANINGFUL) spread 0 |
| codegraph | 3119 (delta 1.88, band 82.8, MEANINGFUL) spread 0 |
| graft | 1482 (delta 0.895, band 82.8, MEANINGFUL) spread 0 |
| aider | 8602 (delta 5.19, band 94.8, MEANINGFUL) spread 31.6 |

## calls_per_task (ratio vs jcm)

| tool | self@6215a70c |
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
| aider | 1 (delta 0.294, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@6215a70c |
|---|---|
| null_readall | 32.58 (delta 0.725, band 4.32, MEANINGFUL) spread 0.7 |
| null_grep | 1.17 (delta 0.026, band 4.32, MEANINGFUL) spread 0.09 |
| jcodemunch | 44.92 spread 1.44 |
| cymbal | 594 (delta 13.2, band 51, MEANINGFUL) spread 17 |
| codebase_memory | 16.74 (delta 0.373, band 4.32, MEANINGFUL) spread 0.45 |
| code_review_graph | 92.69 (delta 2.06, band 634) spread 211 [unstable: a spread exceeds 10% of its own median] |
| serena | 2819 (delta 62.8, band 293, MEANINGFUL) spread 97.5 |
| codegraph | 3.71 (delta 0.0826, band 4.32, MEANINGFUL) spread 0.28 |
| graft | 829.3 (delta 18.5, band 344) spread 115 [unstable: a spread exceeds 10% of its own median] |
| aider | 4467 (delta 99.4, band 198, MEANINGFUL) spread 66 |

## index_cold_seconds (ratio vs jcm)

| tool | self@6215a70c |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 15.74 spread 6.79 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 1.616 (delta 0.103, band 20.4) spread 0.081 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 5.558 (delta 0.353, band 20.4) spread 0.302 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 57.38 (delta 3.64, band 20.4) spread 1.6 [unstable: a spread exceeds 10% of its own median] |
| serena | 3.475 (delta 0.221, band 20.4) spread 0.836 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 1.546 (delta 0.0982, band 20.4) spread 0.276 [unstable: a spread exceeds 10% of its own median] |
| graft | 8.616 (delta 0.547, band 20.4) spread 0.291 [unstable: a spread exceeds 10% of its own median] |
| aider | 5.869 (delta 0.373, band 20.4) spread 0.937 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@6215a70c |
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
| aider | NOT COMPARABLE |

## f1_P1 (difference vs jcm)

| tool | self@6215a70c |
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
| aider | NOT COMPARABLE |

## f1_P2 (difference vs jcm)

| tool | self@6215a70c |
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
| aider | NOT COMPARABLE |

## f1_P4 (difference vs jcm)

| tool | self@6215a70c |
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
| aider | NOT COMPARABLE |
