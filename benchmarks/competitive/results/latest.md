# Competitive tier — 2026-09-06T00:08:43Z at 825d2cc9 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@825d2cc9` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `04eaf126a651`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@825d2cc9 (ran as 825d2cc9, image `85f064ec1519`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `dbd39bc278f2`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `552d4482ae2e`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `e7426b1a73fb`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `d4ce7610db4c`); `codegraph` github-release:colbymchenry/codegraph@1.6.0 (ran as 1.6.0, image `eb975d663130`); `graft` npm:@nanonets/graft@0.16.0 (ran as 0.16.0, image `d54dce4e85da`); `aider` pypi:aider-chat@0.86.2 (ran as 0.86.2, image `74aae790fbbd`); `cocoindex` pypi:cocoindex-code@0.2.41 (ran as 0.2.41, image `438dc29e852b`)

## tokens_per_task (ratio vs jcm)

| tool | self@825d2cc9 |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0.1 |
| cymbal | 880.8 (delta 0.532, band 82.8, MEANINGFUL) spread 0.1 |
| codebase_memory | 980.6 (delta 0.592, band 82.8, MEANINGFUL) spread 0 |
| code_review_graph | 324.8 (delta 0.196, band 82.8, MEANINGFUL) spread 0 |
| serena | 1.178e+04 (delta 7.12, band 82.8, MEANINGFUL) spread 0 |
| codegraph | 3119 (delta 1.88, band 82.8, MEANINGFUL) spread 0 |
| graft | 1482 (delta 0.895, band 82.8, MEANINGFUL) spread 0 |
| aider | 8631 (delta 5.21, band 251, MEANINGFUL) spread 83.8 |
| cocoindex | 1296 (delta 0.782, band 82.8, MEANINGFUL) spread 0.5 |

## calls_per_task (ratio vs jcm)

| tool | self@825d2cc9 |
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
| cocoindex | 1 (delta 0.294, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@825d2cc9 |
|---|---|
| null_readall | 33.15 (delta 0.675, band 195) spread 23.6 [unstable: a spread exceeds 10% of its own median] |
| null_grep | 1.31 (delta 0.0267, band 195) spread 0.72 [unstable: a spread exceeds 10% of its own median] |
| jcodemunch | 49.09 spread 65 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 655 (delta 13.3, band 2.12e+03) spread 705 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 18.7 (delta 0.381, band 195) spread 21 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 111.3 (delta 2.27, band 231) spread 77.1 [unstable: a spread exceeds 10% of its own median] |
| serena | 2921 (delta 59.5, band 1.48e+03) spread 494 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 4.08 (delta 0.0831, band 195) spread 3.19 [unstable: a spread exceeds 10% of its own median] |
| graft | 873.2 (delta 17.8, band 1.76e+03) spread 586 [unstable: a spread exceeds 10% of its own median] |
| aider | 4570 (delta 93.1, band 465) spread 155 [unstable: a spread exceeds 10% of its own median] |
| cocoindex | 432.2 (delta 8.8, band 804) spread 268 [unstable: a spread exceeds 10% of its own median] |

## index_cold_seconds (ratio vs jcm)

| tool | self@825d2cc9 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 18.65 spread 59.3 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 1.776 (delta 0.0952, band 178) spread 1.1 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 6.001 (delta 0.322, band 178) spread 4.65 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 63.88 (delta 3.43, band 178) spread 35 [unstable: a spread exceeds 10% of its own median] |
| serena | 4.65 (delta 0.249, band 178) spread 1.1 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 1.594 (delta 0.0855, band 178) spread 0.0846 [unstable: a spread exceeds 10% of its own median] |
| graft | 8.525 (delta 0.457, band 178) spread 0.765 [unstable: a spread exceeds 10% of its own median] |
| aider | 5.999 (delta 0.322, band 178) spread 0.709 [unstable: a spread exceeds 10% of its own median] |
| cocoindex | 191.4 (delta 10.3, band 178) spread 26 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@825d2cc9 |
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
| cocoindex | 397 (delta 0.0168, band 1.18e+03, MEANINGFUL) spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@825d2cc9 |
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
| cocoindex | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@825d2cc9 |
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
| cocoindex | NOT COMPARABLE |

## f1_P4 (difference vs jcm)

| tool | self@825d2cc9 |
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
| cocoindex | NOT COMPARABLE |
