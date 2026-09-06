# Competitive tier — 2026-09-06T12:40:07Z at 0e3a1706 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@0e3a1706` 277 files, sha256 `c1cecae571a8`; `lodash/lodash@f299b52` 91 files, sha256 `f1a3a1985248`; `psf/requests@0e322af` 121 files, sha256 `2919d8bbe078`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `43d1b3db239f`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@0e3a1706 (ran as 0e3a1706, image `c6dc084baa67`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `81ec741d7cc8`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `0e13f44de1e1`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `7f01c21d1956`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `1f8c3413959c`); `codegraph` github-release:colbymchenry/codegraph@1.6.0 (ran as 1.6.0, image `9f9569c58b6e`); `graft` npm:@nanonets/graft@0.16.0 (ran as 0.16.0, image `8c515b68ec3f`); `aider` pypi:aider-chat@0.86.2 (ran as 0.86.2, image `6a553320bac6`); `cocoindex` pypi:cocoindex-code@0.2.41 (ran as 0.2.41, image `481ddddab3a7`)

## tokens_per_task (ratio vs jcm)

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 | 1.239e+06 (delta 1e+03, band 61.8, MEANINGFUL) spread 0 | 2.397e+06 (delta 1.73e+03, band 69.3, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 | 3.924e+05 (delta 317, band 61.8, MEANINGFUL) spread 0 | 3.17e+04 (delta 22.9, band 69.3, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 | 1237 spread 0 | 1387 spread 0 |
| cymbal | 880.9 (delta 0.532, band 82.8, MEANINGFUL) spread 0.1 | 467.9 (delta 0.378, band 61.8, MEANINGFUL) spread 0 | 449.1 (delta 0.324, band 69.3, MEANINGFUL) spread 0.643 |
| codebase_memory | 980.6 (delta 0.592, band 82.8, MEANINGFUL) spread 26.5 | 334.4 (delta 0.27, band 61.8, MEANINGFUL) spread 0 | 343 (delta 0.247, band 69.3, MEANINGFUL) spread 0 |
| code_review_graph | 324.8 (delta 0.196, band 82.8, MEANINGFUL) spread 0 | 771.7 (delta 0.624, band 61.8, MEANINGFUL) spread 0 | 908.8 (delta 0.655, band 69.3, MEANINGFUL) spread 0 |
| serena | 1.178e+04 (delta 7.12, band 82.8, MEANINGFUL) spread 0 | 1.34e+04 (delta 10.8, band 61.8, MEANINGFUL) spread 0 | 2398 (delta 1.73, band 69.3, MEANINGFUL) spread 0 |
| codegraph | 3119 (delta 1.88, band 82.8, MEANINGFUL) spread 0 | 723.1 (delta 0.585, band 61.8, MEANINGFUL) spread 0 | 847.8 (delta 0.611, band 69.3, MEANINGFUL) spread 0 |
| graft | 1482 (delta 0.895, band 82.8, MEANINGFUL) spread 0 | 647.7 (delta 0.524, band 61.8, MEANINGFUL) spread 0 | 874.7 (delta 0.631, band 69.3, MEANINGFUL) spread 0 |
| aider | 8591 (delta 5.19, band 193, MEANINGFUL) spread 64.2 | 7463 (delta 6.03, band 639, MEANINGFUL) spread 213 | 8261 (delta 5.96, band 69.3, MEANINGFUL) spread 0 |
| cocoindex | 1296 (delta 0.782, band 82.8, MEANINGFUL) spread 0 | 1693 (delta 1.37, band 61.8, MEANINGFUL) spread 0.308 | 1708 (delta 1.23, band 69.3, MEANINGFUL) spread 0.0769 |

## calls_per_task (ratio vs jcm)

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | 277 (delta 81.5, band 0.17, MEANINGFUL) spread 0 | 91 (delta 37.2, band 0.122, MEANINGFUL) spread 0 | 121 (delta 50.6, band 0.12, MEANINGFUL) spread 0 |
| null_grep | 3.6 (delta 1.06, band 0.17, MEANINGFUL) spread 0 | 3.741 (delta 1.53, band 0.122, MEANINGFUL) spread 0 | 3.464 (delta 1.45, band 0.12, MEANINGFUL) spread 0 |
| jcodemunch | 3.4 spread 0 | 2.444 spread 0 | 2.393 spread 0 |
| cymbal | 2.5 (delta 0.735, band 0.17, MEANINGFUL) spread 0 | 1.333 (delta 0.545, band 0.122, MEANINGFUL) spread 0 | 1.321 (delta 0.552, band 0.12, MEANINGFUL) spread 0 |
| codebase_memory | 3.2 (delta 0.941, band 0.17, MEANINGFUL) spread 0 | 8.185 (delta 3.35, band 0.122, MEANINGFUL) spread 0 | 6.071 (delta 2.54, band 0.12, MEANINGFUL) spread 0 |
| code_review_graph | 1.1 (delta 0.324, band 0.17, MEANINGFUL) spread 0 | 1.185 (delta 0.485, band 0.122, MEANINGFUL) spread 0 | 1.357 (delta 0.567, band 0.12, MEANINGFUL) spread 0 |
| serena | 1.444 (delta 0.425, band 0.17, MEANINGFUL) spread 0 | 1 (delta 0.409, band 0.122, MEANINGFUL) spread 0 | 4.043 (delta 1.69, band 0.12, MEANINGFUL) spread 0 |
| codegraph | 1.3 (delta 0.382, band 0.17, MEANINGFUL) spread 0 | 1.37 (delta 0.561, band 0.122, MEANINGFUL) spread 0 | 1.357 (delta 0.567, band 0.12, MEANINGFUL) spread 0 |
| graft | 1.1 (delta 0.324, band 0.17, MEANINGFUL) spread 0 | 1.154 (delta 0.472, band 0.122, MEANINGFUL) spread 0 | 1.179 (delta 0.492, band 0.12, MEANINGFUL) spread 0 |
| aider | 1 (delta 0.294, band 0.17, MEANINGFUL) spread 0 | 1 (delta 0.409, band 0.122, MEANINGFUL) spread 0 | 1 (delta 0.418, band 0.12, MEANINGFUL) spread 0 |
| cocoindex | 1 (delta 0.294, band 0.17, MEANINGFUL) spread 0 | 1 (delta 0.409, band 0.122, MEANINGFUL) spread 0 | 1 (delta 0.418, band 0.12, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | 31.01 (delta 0.605, band 161) spread 0.12 [unstable: a spread exceeds 10% of its own median] | 14.49 (delta 0.45, band 7.29, MEANINGFUL) spread 1.4 | 37.66 (delta 0.902, band 8.91) spread 2.97 |
| null_grep | 1.15 (delta 0.0225, band 161) spread 0.27 [unstable: a spread exceeds 10% of its own median] | 1.12 (delta 0.0348, band 7.29) spread 0.2 [unstable: a spread exceeds 10% of its own median] | 0.2 (delta 0.0048, band 4.5, MEANINGFUL) spread 0.01 |
| jcodemunch | 51.22 spread 53.6 [unstable: a spread exceeds 10% of its own median] | 32.21 spread 2.43 | 41.77 spread 1.5 |
| cymbal | 693 (delta 13.5, band 306) spread 102 [unstable: a spread exceeds 10% of its own median] | 348 (delta 10.8, band 531) spread 177 [unstable: a spread exceeds 10% of its own median] | 276 (delta 6.61, band 48, MEANINGFUL) spread 16 |
| codebase_memory | 18.07 (delta 0.353, band 161) spread 1.52 [unstable: a spread exceeds 10% of its own median] | 11.59 (delta 0.36, band 7.29, MEANINGFUL) spread 0.27 | 11.75 (delta 0.281, band 4.5, MEANINGFUL) spread 0.09 |
| code_review_graph | 123.1 (delta 2.4, band 165) spread 54.9 [unstable: a spread exceeds 10% of its own median] | 100.2 (delta 3.11, band 33.1) spread 11 [unstable: a spread exceeds 10% of its own median] | 95.18 (delta 2.28, band 44.1) spread 14.7 [unstable: a spread exceeds 10% of its own median] |
| serena | 2976 (delta 58.1, band 528) spread 176 [unstable: a spread exceeds 10% of its own median] | 1.201e+04 (delta 373, band 4.09e+03) spread 1.36e+03 [unstable: a spread exceeds 10% of its own median] | 915.6 (delta 21.9, band 786) spread 262 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 3.98 (delta 0.0777, band 161) spread 0.51 [unstable: a spread exceeds 10% of its own median] | 1.44 (delta 0.0447, band 7.29) spread 0.17 [unstable: a spread exceeds 10% of its own median] | 0.99 (delta 0.0237, band 4.5) spread 0.46 [unstable: a spread exceeds 10% of its own median] |
| graft | 1122 (delta 21.9, band 1.06e+03) spread 352 [unstable: a spread exceeds 10% of its own median] | 123.2 (delta 3.83, band 344) spread 115 [unstable: a spread exceeds 10% of its own median] | 229.3 (delta 5.49, band 56.5, MEANINGFUL) spread 18.8 |
| aider | 4643 (delta 90.6, band 1.91e+03) spread 636 [unstable: a spread exceeds 10% of its own median] | 5418 (delta 168, band 2.64e+03) spread 881 [unstable: a spread exceeds 10% of its own median] | 2949 (delta 70.6, band 813, MEANINGFUL) spread 271 |
| cocoindex | 435.4 (delta 8.5, band 161) spread 0.58 [unstable: a spread exceeds 10% of its own median] | 230.7 (delta 7.16, band 7.29, MEANINGFUL) spread 0.97 | 230.5 (delta 5.52, band 7.71, MEANINGFUL) spread 2.57 |

## index_cold_seconds (ratio vs jcm)

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| null_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch | 17.59 spread 2.95 [unstable: a spread exceeds 10% of its own median] | 3.018 spread 0.311 [unstable: a spread exceeds 10% of its own median] | 3.426 spread 1.15 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 1.985 (delta 0.113, band 8.84) spread 0.736 [unstable: a spread exceeds 10% of its own median] | 1.536 (delta 0.509, band 2.56) spread 0.855 [unstable: a spread exceeds 10% of its own median] | 0.496 (delta 0.145, band 3.46) spread 0.542 [unstable: a spread exceeds 10% of its own median] |
| codebase_memory | 6.034 (delta 0.343, band 8.84) spread 0.574 [unstable: a spread exceeds 10% of its own median] | 16.15 (delta 5.35, band 4.5) spread 1.5 [unstable: a spread exceeds 10% of its own median] | 10.51 (delta 3.07, band 3.46) spread 0.89 [unstable: a spread exceeds 10% of its own median] |
| code_review_graph | 58.21 (delta 3.31, band 27.5) spread 9.18 [unstable: a spread exceeds 10% of its own median] | 25.63 (delta 8.49, band 7.4) spread 2.47 [unstable: a spread exceeds 10% of its own median] | 17.27 (delta 5.04, band 4.95) spread 1.65 [unstable: a spread exceeds 10% of its own median] |
| serena | 4.762 (delta 0.271, band 8.84) spread 0.901 [unstable: a spread exceeds 10% of its own median] | 2.053 (delta 0.68, band 2.17) spread 0.723 [unstable: a spread exceeds 10% of its own median] | 3.18 (delta 0.928, band 4.39) spread 1.46 [unstable: a spread exceeds 10% of its own median] |
| codegraph | 1.782 (delta 0.101, band 8.84) spread 0.408 [unstable: a spread exceeds 10% of its own median] | 1.474 (delta 0.488, band 0.932) spread 0.0567 [unstable: a spread exceeds 10% of its own median] | 0.6405 (delta 0.187, band 3.46) spread 0.248 [unstable: a spread exceeds 10% of its own median] |
| graft | 9.237 (delta 0.525, band 8.84) spread 1.19 [unstable: a spread exceeds 10% of its own median] | 3.752 (delta 1.24, band 1.37) spread 0.457 [unstable: a spread exceeds 10% of its own median] | 1.497 (delta 0.437, band 3.46) spread 0.308 [unstable: a spread exceeds 10% of its own median] |
| aider | 6.763 (delta 0.385, band 8.84) spread 1.16 [unstable: a spread exceeds 10% of its own median] | 6.687 (delta 2.22, band 4.03) spread 1.34 [unstable: a spread exceeds 10% of its own median] | 3.542 (delta 1.03, band 3.46) spread 0.175 [unstable: a spread exceeds 10% of its own median] |
| cocoindex | 191.5 (delta 10.9, band 43.7) spread 14.6 [unstable: a spread exceeds 10% of its own median] | 95.1 (delta 31.5, band 28.7) spread 9.57 [unstable: a spread exceeds 10% of its own median] | 23.52 (delta 6.87, band 7.55) spread 2.52 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| null_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch | 2.365e+04 spread 0 | 2.365e+04 spread 0 | 2.365e+04 spread 0 |
| cymbal | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codebase_memory | 4791 (delta 0.203, band 1.18e+03, MEANINGFUL) spread 0 | 4791 (delta 0.203, band 1.18e+03, MEANINGFUL) spread 0 | 4791 (delta 0.203, band 1.18e+03, MEANINGFUL) spread 0 |
| code_review_graph | 7694 (delta 0.325, band 1.18e+03, MEANINGFUL) spread 0 | 7694 (delta 0.325, band 1.18e+03, MEANINGFUL) spread 0 | 7694 (delta 0.325, band 1.18e+03, MEANINGFUL) spread 0 |
| serena | 6476 (delta 0.274, band 1.18e+03, MEANINGFUL) spread 0 | 6476 (delta 0.274, band 1.18e+03, MEANINGFUL) spread 0 | 6476 (delta 0.274, band 1.18e+03, MEANINGFUL) spread 0 |
| codegraph | 1293 (delta 0.0547, band 1.18e+03, MEANINGFUL) spread 0 | 1293 (delta 0.0547, band 1.18e+03, MEANINGFUL) spread 0 | 1293 (delta 0.0547, band 1.18e+03, MEANINGFUL) spread 0 |
| graft | 761 (delta 0.0322, band 1.18e+03, MEANINGFUL) spread 0 | 761 (delta 0.0322, band 1.18e+03, MEANINGFUL) spread 0 | 761 (delta 0.0322, band 1.18e+03, MEANINGFUL) spread 0 |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | 397 (delta 0.0168, band 1.18e+03, MEANINGFUL) spread 0 | 397 (delta 0.0168, band 1.18e+03, MEANINGFUL) spread 0 | 397 (delta 0.0168, band 1.18e+03, MEANINGFUL) spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 | 0 (delta -0.34, band 0.017, MEANINGFUL) spread 0 | 0.0001 (delta -0.356, band 0.0178, MEANINGFUL) spread 0 |
| null_grep | 0.2299 (delta -0.103, band 0.0167, MEANINGFUL) spread 0 | 0.00809 (delta -0.332, band 0.017, MEANINGFUL) spread 0 | 0.01524 (delta -0.341, band 0.0178, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 | 0.34 spread 0 | 0.3566 spread 0 |
| cymbal | 0.2606 (delta -0.0727, band 0.0167, MEANINGFUL) spread 0 | 0.1032 (delta -0.237, band 0.017, MEANINGFUL) spread 0 | 0.3492 (delta -0.0075, band 0.0178) spread 0.00069 |
| codebase_memory | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 | 0 (delta -0.34, band 0.017, MEANINGFUL) spread 0 | 0 (delta -0.357, band 0.0178, MEANINGFUL) spread 0 |
| code_review_graph | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 | 0.4667 (delta 0.127, band 0.017, MEANINGFUL) spread 0 | 0.4667 (delta 0.11, band 0.0178, MEANINGFUL) spread 0 |
| serena | 1 (delta 0.667, band 0.0167, MEANINGFUL) spread 0 | NOT COMPARABLE | 0.6136 (delta 0.257, band 0.0178, MEANINGFUL) spread 0 |
| codegraph | 0.5 (delta 0.167, band 0.0167, MEANINGFUL) spread 0 | 0.45 (delta 0.11, band 0.017, MEANINGFUL) spread 0 | 0.49 (delta 0.133, band 0.0178, MEANINGFUL) spread 0 |
| graft | 0.3333 (delta 0, band 0.0167) spread 0 | 0.29 (delta -0.05, band 0.017, MEANINGFUL) spread 0 | 0.2966 (delta -0.06, band 0.0178, MEANINGFUL) spread 0 |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 | 0 (delta -0.34, band 0.017, MEANINGFUL) spread 0 | 0 (delta -0.357, band 0.0178, MEANINGFUL) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | 0 (delta 0, band 0) spread 0 | 0.00047 (delta 0.0005, band 0, MEANINGFUL) spread 0 | 0.00191 (delta 0.0019, band 0, MEANINGFUL) spread 0 |
| null_grep | 0.1818 (delta 0.182, band 0, MEANINGFUL) spread 0 | 0.1024 (delta 0.102, band 0, MEANINGFUL) spread 0 | 0.02802 (delta 0.028, band 0, MEANINGFUL) spread 0 |
| jcodemunch | 0 spread 0 | 0 spread 0 | 0 spread 0 |
| cymbal | 1 (delta 1, band 0, MEANINGFUL) spread 0 | 0.1109 (delta 0.111, band 0, MEANINGFUL) spread 0 | 0.08062 (delta 0.0806, band 0, MEANINGFUL) spread 0 |
| codebase_memory | 1 (delta 1, band 0, MEANINGFUL) spread 0 | 0 (delta 0, band 0) spread 0 | 0 (delta 0, band 0) spread 0 |
| code_review_graph | 1 (delta 1, band 0, MEANINGFUL) spread 0 | 0.0125 (delta 0.0125, band 0, MEANINGFUL) spread 0 | 0 (delta 0, band 0) spread 0 |
| serena | 1 (delta 1, band 0, MEANINGFUL) spread 0 | NOT COMPARABLE | 0.0346 (delta 0.0346, band 0, MEANINGFUL) spread 0 |
| codegraph | 1 (delta 1, band 0, MEANINGFUL) spread 0 | 0.01578 (delta 0.0158, band 0, MEANINGFUL) spread 0 | 0.01872 (delta 0.0187, band 0, MEANINGFUL) spread 0 |
| graft | 1 (delta 1, band 0, MEANINGFUL) spread 0 | 0.006733 (delta 0.0067, band 0, MEANINGFUL) spread 0 | 0.02819 (delta 0.0282, band 0, MEANINGFUL) spread 0 |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## f1_P4 (difference vs jcm)

| tool | self@0e3a1706 | lodash/lodash@f299b52 | psf/requests@0e322af |
|---|---|---|---|
| null_readall | 0.0005 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 | 2.5e-05 (delta -0.834, band 0.0417, MEANINGFUL) spread 0 | 0.00016 (delta -0.753, band 0.0377, MEANINGFUL) spread 0 |
| null_grep | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 | 0 (delta -0.834, band 0.0417, MEANINGFUL) spread 0 | 0 (delta -0.753, band 0.0377, MEANINGFUL) spread 0 |
| jcodemunch | 0.4324 spread 0 | 0.8341 spread 0 | 0.7532 spread 0 |
| cymbal | 0.4737 (delta 0.0413, band 0.0216, MEANINGFUL) spread 0 | 0 (delta -0.834, band 0.0417, MEANINGFUL) spread 0 | 0.7253 (delta -0.028, band 0.0377) spread 0 |
| codebase_memory | 0.9825 (delta 0.55, band 0.0216, MEANINGFUL) spread 0 | 0 (delta -0.834, band 0.0417, MEANINGFUL) spread 0 | 0 (delta -0.753, band 0.0377, MEANINGFUL) spread 0 |
| code_review_graph | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 | 0 (delta -0.834, band 0.0417, MEANINGFUL) spread 0 | 0 (delta -0.753, band 0.0377, MEANINGFUL) spread 0 |
| serena | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codegraph | 0.0541 (delta -0.378, band 0.0216, MEANINGFUL) spread 0 | 0.05555 (delta -0.778, band 0.0417, MEANINGFUL) spread 0 | 0.4312 (delta -0.322, band 0.0377, MEANINGFUL) spread 0 |
| graft | 0.4286 (delta -0.0038, band 0.0216) spread 0 | 0.6004 (delta -0.234, band 0.0417, MEANINGFUL) spread 0 | 0.5048 (delta -0.248, band 0.0377, MEANINGFUL) spread 0 |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## Tools not called (DESIGN s10: an adapter that cited nothing on every P task of a corpus)

- `jcodemunch` P2 on `lodash/lodash@f299b52` (30 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `jcodemunch` P2 on `self@0e3a1706` (3 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `cymbal` P4 on `lodash/lodash@f299b52` (12 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `codebase_memory` P2 on `lodash/lodash@f299b52` (30 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `codebase_memory` P4 on `lodash/lodash@f299b52` (12 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `codebase_memory` P4 on `psf/requests@0e322af` (15 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `code_review_graph` P4 on `lodash/lodash@f299b52` (12 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `code_review_graph` P4 on `psf/requests@0e322af` (15 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`
- `code_review_graph` P4 on `self@0e3a1706` (3 tasks, every `cited` set empty): NOT COMPARABLE there; hypothesis `tool_not_called`

## Movement

Per row: the delta now, on the previous recorded run, and on the first (11 earlier line(s) in history.jsonl); the gap's movement judged against this run's band; the competitor's release on each of the three runs beside it. A release beside a movement is a fact on the same line, not an attribution. A row marked `our improvement`/`our regression` is one where jcm's own value moved past the band while the competitor's release did not.

| axis | tool | corpus | now | previous | first | movement | release now / previous / first | jcm |
|---|---|---|---|---|---|---|---|---|
| calls_per_task | aider | lodash/lodash@f299b52 | 0.4091 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| calls_per_task | aider | psf/requests@0e322af | 0.4179 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| calls_per_task | aider | self | 0.2941 | 0.2941 | n/a | unchanged | 0.86.2 / 0.86.2 / n/a |  |
| calls_per_task | cocoindex | lodash/lodash@f299b52 | 0.4091 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| calls_per_task | cocoindex | psf/requests@0e322af | 0.4179 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| calls_per_task | cocoindex | self | 0.2941 | 0.2941 | n/a | unchanged | 0.2.41 / 0.2.41 / n/a |  |
| calls_per_task | code_review_graph | lodash/lodash@f299b52 | 0.4848 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| calls_per_task | code_review_graph | psf/requests@0e322af | 0.5672 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| calls_per_task | code_review_graph | self | 0.3235 | 0.3235 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| calls_per_task | codebase_memory | lodash/lodash@f299b52 | 3.349 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| calls_per_task | codebase_memory | psf/requests@0e322af | 2.537 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| calls_per_task | codebase_memory | self | 0.9412 | 0.9412 | 0.9412 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| calls_per_task | codegraph | lodash/lodash@f299b52 | 0.5606 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| calls_per_task | codegraph | psf/requests@0e322af | 0.5672 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| calls_per_task | codegraph | self | 0.3824 | 0.3824 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| calls_per_task | cymbal | lodash/lodash@f299b52 | 0.5455 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| calls_per_task | cymbal | psf/requests@0e322af | 0.5522 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| calls_per_task | cymbal | self | 0.7353 | 0.7353 | 0.7353 | unchanged | 0.14.0 / 0.14.0 / 0.14.0 |  |
| calls_per_task | graft | lodash/lodash@f299b52 | 0.472 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| calls_per_task | graft | psf/requests@0e322af | 0.4925 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| calls_per_task | graft | self | 0.3235 | 0.3235 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| calls_per_task | null_grep | lodash/lodash@f299b52 | 1.53 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| calls_per_task | null_grep | psf/requests@0e322af | 1.448 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| calls_per_task | null_grep | self | 1.059 | 1.059 | 1.059 | unchanged | baseline-B / baseline-B / baseline-B |  |
| calls_per_task | null_readall | lodash/lodash@f299b52 | 37.23 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| calls_per_task | null_readall | psf/requests@0e322af | 50.57 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| calls_per_task | null_readall | self | 81.47 | 81.47 | 81.47 | unchanged | baseline-A / baseline-A / baseline-A |  |
| calls_per_task | serena | lodash/lodash@f299b52 | 0.4091 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| calls_per_task | serena | psf/requests@0e322af | 1.69 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| calls_per_task | serena | self | 0.4248 | 0.4248 | n/a | unchanged | 1.7.0 / 1.7.0 / n/a |  |
| f1_P1 | cocoindex | lodash/lodash@f299b52 | -0.34 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| f1_P1 | cocoindex | psf/requests@0e322af | -0.3566 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| f1_P1 | cocoindex | self | -0.3333 | -0.3333 | n/a | unchanged | 0.2.41 / 0.2.41 / n/a |  |
| f1_P1 | code_review_graph | lodash/lodash@f299b52 | 0.1267 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| f1_P1 | code_review_graph | psf/requests@0e322af | 0.11 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| f1_P1 | code_review_graph | self | 0.3334 | 0.3334 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| f1_P1 | codebase_memory | lodash/lodash@f299b52 | -0.34 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P1 | codebase_memory | psf/requests@0e322af | -0.3566 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P1 | codebase_memory | self | 0.3334 | 0.3334 | 0.3334 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P1 | codegraph | lodash/lodash@f299b52 | 0.11 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| f1_P1 | codegraph | psf/requests@0e322af | 0.1334 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| f1_P1 | codegraph | self | 0.1667 | 0.1667 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| f1_P1 | cymbal | lodash/lodash@f299b52 | -0.2368 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P1 | cymbal | psf/requests@0e322af | -0.0075 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P1 | cymbal | self | -0.0727 | -0.0727 | -0.0727 | unchanged | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P1 | graft | lodash/lodash@f299b52 | -0.05 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| f1_P1 | graft | psf/requests@0e322af | -0.06 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| f1_P1 | graft | self | 0 | 0 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| f1_P1 | null_grep | lodash/lodash@f299b52 | -0.3319 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| f1_P1 | null_grep | psf/requests@0e322af | -0.3414 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| f1_P1 | null_grep | self | -0.1034 | -0.1034 | -0.1034 | unchanged | baseline-B / baseline-B / baseline-B |  |
| f1_P1 | null_readall | lodash/lodash@f299b52 | -0.34 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| f1_P1 | null_readall | psf/requests@0e322af | -0.3565 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| f1_P1 | null_readall | self | -0.3333 | -0.3333 | -0.3333 | unchanged | baseline-A / baseline-A / baseline-A |  |
| f1_P1 | serena | psf/requests@0e322af | 0.2569 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| f1_P1 | serena | self | 0.6667 | 0.6667 | n/a | unchanged | 1.7.0 / 1.7.0 / n/a |  |
| f1_P2 | code_review_graph | lodash/lodash@f299b52 | 0.0125 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| f1_P2 | code_review_graph | psf/requests@0e322af | 0 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| f1_P2 | code_review_graph | self | 1 | 1 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| f1_P2 | codebase_memory | lodash/lodash@f299b52 | 0 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P2 | codebase_memory | psf/requests@0e322af | 0 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P2 | codebase_memory | self | 1 | 1 | 1 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P2 | codegraph | lodash/lodash@f299b52 | 0.0158 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| f1_P2 | codegraph | psf/requests@0e322af | 0.0187 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| f1_P2 | codegraph | self | 1 | 1 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| f1_P2 | cymbal | lodash/lodash@f299b52 | 0.1109 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P2 | cymbal | psf/requests@0e322af | 0.0806 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P2 | cymbal | self | 1 | 1 | 1 | unchanged | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P2 | graft | lodash/lodash@f299b52 | 0.0067 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| f1_P2 | graft | psf/requests@0e322af | 0.0282 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| f1_P2 | graft | self | 1 | 1 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| f1_P2 | null_grep | lodash/lodash@f299b52 | 0.1024 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| f1_P2 | null_grep | psf/requests@0e322af | 0.028 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| f1_P2 | null_grep | self | 0.1818 | 0.1818 | 0.1818 | unchanged | baseline-B / baseline-B / baseline-B |  |
| f1_P2 | null_readall | lodash/lodash@f299b52 | 0.0005 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| f1_P2 | null_readall | psf/requests@0e322af | 0.0019 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| f1_P2 | null_readall | self | 0 | 0 | 0 | unchanged | baseline-A / baseline-A / baseline-A |  |
| f1_P2 | serena | psf/requests@0e322af | 0.0346 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| f1_P2 | serena | self | 1 | 1 | n/a | unchanged | 1.7.0 / 1.7.0 / n/a |  |
| f1_P4 | code_review_graph | lodash/lodash@f299b52 | -0.8341 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| f1_P4 | code_review_graph | psf/requests@0e322af | -0.7532 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| f1_P4 | code_review_graph | self | -0.4324 | -0.4324 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| f1_P4 | codebase_memory | lodash/lodash@f299b52 | -0.8341 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P4 | codebase_memory | psf/requests@0e322af | -0.7532 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P4 | codebase_memory | self | 0.5501 | 0.5501 | 0.5501 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| f1_P4 | codegraph | lodash/lodash@f299b52 | -0.7785 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| f1_P4 | codegraph | psf/requests@0e322af | -0.3221 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| f1_P4 | codegraph | self | -0.3783 | -0.3783 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| f1_P4 | cymbal | lodash/lodash@f299b52 | -0.8341 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P4 | cymbal | psf/requests@0e322af | -0.028 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P4 | cymbal | self | 0.0413 | 0.0413 | 0.0413 | unchanged | 0.14.0 / 0.14.0 / 0.14.0 |  |
| f1_P4 | graft | lodash/lodash@f299b52 | -0.2337 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| f1_P4 | graft | psf/requests@0e322af | -0.2485 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| f1_P4 | graft | self | -0.0038 | -0.0038 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| f1_P4 | null_grep | lodash/lodash@f299b52 | -0.8341 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| f1_P4 | null_grep | psf/requests@0e322af | -0.7532 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| f1_P4 | null_grep | self | -0.4324 | -0.4324 | -0.4324 | unchanged | baseline-B / baseline-B / baseline-B |  |
| f1_P4 | null_readall | lodash/lodash@f299b52 | -0.834 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| f1_P4 | null_readall | psf/requests@0e322af | -0.7531 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| f1_P4 | null_readall | self | -0.4319 | -0.4319 | -0.4319 | unchanged | baseline-A / baseline-A / baseline-A |  |
| index_cold_seconds | aider | lodash/lodash@f299b52 | 2.215 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| index_cold_seconds | aider | psf/requests@0e322af | 1.034 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| index_cold_seconds | aider | self | 0.3846 | 0.3217 | n/a | unchanged | 0.86.2 / 0.86.2 / n/a |  |
| index_cold_seconds | cocoindex | lodash/lodash@f299b52 | 31.51 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| index_cold_seconds | cocoindex | psf/requests@0e322af | 6.866 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| index_cold_seconds | cocoindex | self | 10.89 | 10.26 | n/a | unchanged | 0.2.41 / 0.2.41 / n/a |  |
| index_cold_seconds | code_review_graph | lodash/lodash@f299b52 | 8.491 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| index_cold_seconds | code_review_graph | psf/requests@0e322af | 5.04 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| index_cold_seconds | code_review_graph | self | 3.31 | 3.425 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| index_cold_seconds | codebase_memory | lodash/lodash@f299b52 | 5.349 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| index_cold_seconds | codebase_memory | psf/requests@0e322af | 3.068 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| index_cold_seconds | codebase_memory | self | 0.3431 | 0.3217 | 0.6901 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| index_cold_seconds | codegraph | lodash/lodash@f299b52 | 0.4883 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| index_cold_seconds | codegraph | psf/requests@0e322af | 0.187 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| index_cold_seconds | codegraph | self | 0.1013 | 0.0855 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| index_cold_seconds | cymbal | lodash/lodash@f299b52 | 0.5089 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| index_cold_seconds | cymbal | psf/requests@0e322af | 0.1448 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| index_cold_seconds | cymbal | self | 0.1129 | 0.0952 | 0.0891 | unchanged | 0.14.0 / 0.14.0 / 0.14.0 |  |
| index_cold_seconds | graft | lodash/lodash@f299b52 | 1.243 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| index_cold_seconds | graft | psf/requests@0e322af | 0.4369 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| index_cold_seconds | graft | self | 0.5252 | 0.4571 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| index_cold_seconds | serena | lodash/lodash@f299b52 | 0.6803 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| index_cold_seconds | serena | psf/requests@0e322af | 0.9281 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| index_cold_seconds | serena | self | 0.2707 | 0.2493 | n/a | unchanged | 1.7.0 / 1.7.0 / n/a |  |
| latency_call_ms | aider | lodash/lodash@f299b52 | 168.2 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| latency_call_ms | aider | psf/requests@0e322af | 70.6 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| latency_call_ms | aider | self | 90.65 | 93.09 | n/a | unchanged | 0.86.2 / 0.86.2 / n/a |  |
| latency_call_ms | cocoindex | lodash/lodash@f299b52 | 7.163 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| latency_call_ms | cocoindex | psf/requests@0e322af | 5.519 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| latency_call_ms | cocoindex | self | 8.502 | 8.805 | n/a | unchanged | 0.2.41 / 0.2.41 / n/a |  |
| latency_call_ms | code_review_graph | lodash/lodash@f299b52 | 3.11 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| latency_call_ms | code_review_graph | psf/requests@0e322af | 2.279 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| latency_call_ms | code_review_graph | self | 2.403 | 2.268 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| latency_call_ms | codebase_memory | lodash/lodash@f299b52 | 0.3598 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| latency_call_ms | codebase_memory | psf/requests@0e322af | 0.2813 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| latency_call_ms | codebase_memory | self | 0.3528 | 0.3809 | 0.3623 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| latency_call_ms | codegraph | lodash/lodash@f299b52 | 0.0447 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| latency_call_ms | codegraph | psf/requests@0e322af | 0.0237 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| latency_call_ms | codegraph | self | 0.0777 | 0.0831 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| latency_call_ms | cymbal | lodash/lodash@f299b52 | 10.8 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| latency_call_ms | cymbal | psf/requests@0e322af | 6.608 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| latency_call_ms | cymbal | self | 13.53 | 13.34 | 13.45 | unchanged | 0.14.0 / 0.14.0 / 0.14.0 |  |
| latency_call_ms | graft | lodash/lodash@f299b52 | 3.825 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| latency_call_ms | graft | psf/requests@0e322af | 5.49 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| latency_call_ms | graft | self | 21.91 | 17.79 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| latency_call_ms | null_grep | lodash/lodash@f299b52 | 0.0348 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| latency_call_ms | null_grep | psf/requests@0e322af | 0.0048 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| latency_call_ms | null_grep | self | 0.0225 | 0.0267 | 0.0242 | unchanged | baseline-B / baseline-B / baseline-B |  |
| latency_call_ms | null_readall | lodash/lodash@f299b52 | 0.4499 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| latency_call_ms | null_readall | psf/requests@0e322af | 0.9016 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| latency_call_ms | null_readall | self | 0.6054 | 0.6753 | 0.6579 | unchanged | baseline-A / baseline-A / baseline-A |  |
| latency_call_ms | serena | lodash/lodash@f299b52 | 373 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| latency_call_ms | serena | psf/requests@0e322af | 21.92 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| latency_call_ms | serena | self | 58.09 | 59.51 | n/a | unchanged | 1.7.0 / 1.7.0 / n/a |  |
| tokens_per_task | aider | lodash/lodash@f299b52 | 6.035 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| tokens_per_task | aider | psf/requests@0e322af | 5.958 | n/a | n/a | first run | 0.86.2 / 0.86.2 / n/a |  |
| tokens_per_task | aider | self | 5.188 | 5.213 | n/a | unchanged | 0.86.2 / 0.86.2 / n/a |  |
| tokens_per_task | cocoindex | lodash/lodash@f299b52 | 1.369 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| tokens_per_task | cocoindex | psf/requests@0e322af | 1.232 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| tokens_per_task | cocoindex | self | 0.7824 | 0.7824 | n/a | unchanged | 0.2.41 / 0.2.41 / n/a |  |
| tokens_per_task | code_review_graph | lodash/lodash@f299b52 | 0.624 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| tokens_per_task | code_review_graph | psf/requests@0e322af | 0.6554 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| tokens_per_task | code_review_graph | self | 0.1961 | 0.1961 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| tokens_per_task | codebase_memory | lodash/lodash@f299b52 | 0.2704 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| tokens_per_task | codebase_memory | psf/requests@0e322af | 0.2474 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| tokens_per_task | codebase_memory | self | 0.5922 | 0.5922 | 0.5922 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| tokens_per_task | codegraph | lodash/lodash@f299b52 | 0.5847 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| tokens_per_task | codegraph | psf/requests@0e322af | 0.6114 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| tokens_per_task | codegraph | self | 1.884 | 1.884 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| tokens_per_task | cymbal | lodash/lodash@f299b52 | 0.3783 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| tokens_per_task | cymbal | psf/requests@0e322af | 0.3239 | n/a | n/a | first run | 0.14.0 / 0.14.0 / 0.14.0 |  |
| tokens_per_task | cymbal | self | 0.532 | 0.5319 | 0.532 | unchanged | 0.14.0 / 0.14.0 / 0.14.0 |  |
| tokens_per_task | graft | lodash/lodash@f299b52 | 0.5238 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| tokens_per_task | graft | psf/requests@0e322af | 0.6308 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| tokens_per_task | graft | self | 0.8947 | 0.8947 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| tokens_per_task | null_grep | lodash/lodash@f299b52 | 317.3 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| tokens_per_task | null_grep | psf/requests@0e322af | 22.86 | n/a | n/a | first run | baseline-B / baseline-B / baseline-B |  |
| tokens_per_task | null_grep | self | 71.28 | 71.28 | 71.28 | unchanged | baseline-B / baseline-B / baseline-B |  |
| tokens_per_task | null_readall | lodash/lodash@f299b52 | 1002 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| tokens_per_task | null_readall | psf/requests@0e322af | 1728 | n/a | n/a | first run | baseline-A / baseline-A / baseline-A |  |
| tokens_per_task | null_readall | self | 701.4 | 701.4 | 701.4 | unchanged | baseline-A / baseline-A / baseline-A |  |
| tokens_per_task | serena | lodash/lodash@f299b52 | 10.83 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| tokens_per_task | serena | psf/requests@0e322af | 1.73 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| tokens_per_task | serena | self | 7.116 | 7.116 | n/a | unchanged | 1.7.0 / 1.7.0 / n/a |  |
| tools_list_tokens | cocoindex | lodash/lodash@f299b52 | 0.0168 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| tools_list_tokens | cocoindex | psf/requests@0e322af | 0.0168 | n/a | n/a | first run | 0.2.41 / 0.2.41 / n/a |  |
| tools_list_tokens | cocoindex | self | 0.0168 | 0.0168 | n/a | unchanged | 0.2.41 / 0.2.41 / n/a |  |
| tools_list_tokens | code_review_graph | lodash/lodash@f299b52 | 0.3253 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| tools_list_tokens | code_review_graph | psf/requests@0e322af | 0.3253 | n/a | n/a | first run | 2.3.8 / 2.3.8 / n/a |  |
| tools_list_tokens | code_review_graph | self | 0.3253 | 0.3253 | n/a | unchanged | 2.3.8 / 2.3.8 / n/a |  |
| tools_list_tokens | codebase_memory | lodash/lodash@f299b52 | 0.2026 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| tools_list_tokens | codebase_memory | psf/requests@0e322af | 0.2026 | n/a | n/a | first run | 0.10.8 / 0.10.8 / 0.10.8 |  |
| tools_list_tokens | codebase_memory | self | 0.2026 | 0.2026 | 0.2026 | unchanged | 0.10.8 / 0.10.8 / 0.10.8 |  |
| tools_list_tokens | codegraph | lodash/lodash@f299b52 | 0.0547 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| tools_list_tokens | codegraph | psf/requests@0e322af | 0.0547 | n/a | n/a | first run | 1.6.0 / 1.6.0 / n/a |  |
| tools_list_tokens | codegraph | self | 0.0547 | 0.0547 | n/a | unchanged | 1.6.0 / 1.6.0 / n/a |  |
| tools_list_tokens | graft | lodash/lodash@f299b52 | 0.0322 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| tools_list_tokens | graft | psf/requests@0e322af | 0.0322 | n/a | n/a | first run | 0.16.0 / 0.16.0 / n/a |  |
| tools_list_tokens | graft | self | 0.0322 | 0.0322 | n/a | unchanged | 0.16.0 / 0.16.0 / n/a |  |
| tools_list_tokens | serena | lodash/lodash@f299b52 | 0.2738 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| tools_list_tokens | serena | psf/requests@0e322af | 0.2738 | n/a | n/a | first run | 1.7.0 / 1.7.0 / n/a |  |
| tools_list_tokens | serena | self | 0.2738 | 0.2738 | n/a | unchanged | 1.7.0 / 1.7.0 / n/a |  |
