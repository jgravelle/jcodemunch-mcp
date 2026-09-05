# Competitive tier — 2026-09-05T14:25:31Z at d6e3d78e (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@d6e3d78e` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: True; scorer sha256 `70a3141cfc1a`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@d6e3d78e (ran as d6e3d78e, image `34147a484cf1`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `f409ed707f23`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `c03957e25e20`)

## tokens_per_task (ratio vs jcm)

| tool | self@d6e3d78e |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 880.9 (delta 0.532, band 82.8, MEANINGFUL) spread 17.7 |
| codebase_memory | 1007 (delta 0.608, band 82.8, MEANINGFUL) spread 26.5 |

## calls_per_task (ratio vs jcm)

| tool | self@d6e3d78e |
|---|---|
| null_readall | 277 (delta 81.5, band 0.17, MEANINGFUL) spread 0 |
| null_grep | 3.6 (delta 1.06, band 0.17, MEANINGFUL) spread 0 |
| jcodemunch | 3.4 spread 0 |
| cymbal | 2.5 (delta 0.735, band 0.17, MEANINGFUL) spread 0 |
| codebase_memory | 3.2 (delta 0.941, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@d6e3d78e |
|---|---|
| null_readall | 32.17 (delta 0.653, band 8.22, MEANINGFUL) spread 2.74 |
| null_grep | 1.22 (delta 0.0248, band 5.13, MEANINGFUL) spread 0.05 |
| jcodemunch | 49.27 spread 1.71 |
| cymbal | 662 (delta 13.4, band 162, MEANINGFUL) spread 54 |
| codebase_memory | 17.37 (delta 0.352, band 5.13, MEANINGFUL) spread 0.81 |

## index_cold_seconds (ratio vs jcm)

| tool | self@d6e3d78e |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 15.02 spread 1.44 |
| cymbal | 1.749 (delta 0.117, band 4.32, MEANINGFUL) spread 0.1 |
| codebase_memory | 6.173 (delta 0.411, band 4.32) spread 0.638 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@d6e3d78e |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.365e+04 spread 0 |
| cymbal | NOT COMPARABLE |
| codebase_memory | 4791 (delta 0.203, band 1.18e+03, MEANINGFUL) spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@d6e3d78e |
|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |
| null_grep | 0.2299 (delta -0.103, band 0.0167, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 |
| cymbal | 0.2606 (delta -0.0727, band 0.0167, MEANINGFUL) spread 0 |
| codebase_memory | 0.6667 (delta 0.333, band 0.0167, MEANINGFUL) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@d6e3d78e |
|---|---|
| null_readall | 0 (delta 0, band 0) spread 0 |
| null_grep | 0.1818 (delta 0.182, band 0, MEANINGFUL) spread 0 |
| jcodemunch | 0 spread 0 |
| cymbal | 1 (delta 1, band 0, MEANINGFUL) spread 0 |
| codebase_memory | 1 (delta 1, band 0, MEANINGFUL) spread 0 |

## f1_P4 (difference vs jcm)

| tool | self@d6e3d78e |
|---|---|
| null_readall | 0.0005 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| null_grep | 0 (delta -0.432, band 0.0216, MEANINGFUL) spread 0 |
| jcodemunch | 0.4324 spread 0 |
| cymbal | 0.4737 (delta 0.0413, band 0.0216, MEANINGFUL) spread 0 |
| codebase_memory | 0.9825 (delta 0.55, band 0.0216, MEANINGFUL) spread 0 |
