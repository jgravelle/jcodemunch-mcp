# Competitive tier — 2026-09-05T13:33:45Z at e7d50a53 (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@e7d50a53` 277 files, sha256 `c1cecae571a8`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: True; scorer sha256 `b50dae87cf0d`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@e7d50a53 (ran as e7d50a53, image `0ef5bca7a7ed`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `55fa785541d2`)

## tokens_per_task (ratio vs jcm)

| tool | self@e7d50a53 |
|---|---|
| null_readall | 1.161e+06 (delta 701, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 1.18e+05 (delta 71.3, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 880.9 (delta 0.532, band 82.8, MEANINGFUL) spread 0.1 |

## calls_per_task (ratio vs jcm)

| tool | self@e7d50a53 |
|---|---|
| null_readall | 277 (delta 81.5, band 0.17, MEANINGFUL) spread 0 |
| null_grep | 3.6 (delta 1.06, band 0.17, MEANINGFUL) spread 0 |
| jcodemunch | 3.4 spread 0 |
| cymbal | 2.5 (delta 0.735, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@e7d50a53 |
|---|---|
| null_readall | 32.67 (delta 0.687, band 21.2) spread 1.06 [unstable: a spread exceeds 10% of its own median] |
| null_grep | 1.18 (delta 0.0248, band 21.2) spread 0.05 [unstable: a spread exceeds 10% of its own median] |
| jcodemunch | 47.53 spread 7.08 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 682 (delta 14.3, band 72) spread 24 [unstable: a spread exceeds 10% of its own median] |

## index_cold_seconds (ratio vs jcm)

| tool | self@e7d50a53 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 18.25 spread 3.26 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 1.707 (delta 0.0935, band 9.78) spread 0.103 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@e7d50a53 |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.365e+04 spread 0 |
| cymbal | NOT COMPARABLE |

## f1_P1 (difference vs jcm)

| tool | self@e7d50a53 |
|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |
| null_grep | 0.2299 (delta -0.103, band 0.0167, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 |
| cymbal | 0.2606 (delta -0.0727, band 0.0167, MEANINGFUL) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@e7d50a53 |
|---|---|
| null_readall | 0 (delta 0, band 0) spread 0 |
| null_grep | 0.1818 (delta 0.182, band 0, MEANINGFUL) spread 0 |
| jcodemunch | 0 spread 0 |
| cymbal | 1 (delta 1, band 0, MEANINGFUL) spread 0 |

## f1_P4 (difference vs jcm)

| tool | self@e7d50a53 |
|---|---|
| null_readall | 0.0001 (delta -0.941, band 0.0471, MEANINGFUL) spread 0 |
| null_grep | 0 (delta -0.941, band 0.0471, MEANINGFUL) spread 0 |
| jcodemunch | 0.9412 spread 0 |
| cymbal | 1 (delta 0.0588, band 0.0471, MEANINGFUL) spread 0 |
