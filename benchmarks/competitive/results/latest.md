# Competitive tier — 2026-09-05T12:54:12Z at b70b439c (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@b70b439c` 275 files, sha256 `b70fbed3c3a3`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: True; scorer sha256 `3bd9cd9a0c91`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@b70b439c (ran as b70b439c, image `4be3c6b70c6a`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `26a4cbd50ec9`)

## tokens_per_task (ratio vs jcm)

| tool | self@b70b439c |
|---|---|
| null_readall | 9.401e+05 (delta 568, band 82.8, MEANINGFUL) spread 0 |
| null_grep | 3.662e+04 (delta 22.1, band 82.8, MEANINGFUL) spread 0 |
| jcodemunch | 1656 spread 0 |
| cymbal | 880.8 (delta 0.532, band 82.8, MEANINGFUL) spread 0.1 |

## calls_per_task (ratio vs jcm)

| tool | self@b70b439c |
|---|---|
| null_readall | 275 (delta 80.9, band 0.17, MEANINGFUL) spread 0 |
| null_grep | 3.5 (delta 1.03, band 0.17) spread 0 |
| jcodemunch | 3.4 spread 0 |
| cymbal | 2.5 (delta 0.735, band 0.17, MEANINGFUL) spread 0 |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@b70b439c |
|---|---|
| null_readall | 29.84 (delta 0.467, band 124) spread 2.45 [unstable: a spread exceeds 10% of its own median] |
| null_grep | 0.29 (delta 0.0045, band 124) spread 0.03 [unstable: a spread exceeds 10% of its own median] |
| jcodemunch | 63.94 spread 41.5 [unstable: a spread exceeds 10% of its own median] |
| cymbal | 689 (delta 10.8, band 240) spread 80 [unstable: a spread exceeds 10% of its own median] |

## index_cold_seconds (ratio vs jcm)

| tool | self@b70b439c |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 16.48 spread 0.357 |
| cymbal | 2.116 (delta 0.128, band 2.46) spread 0.819 [unstable: a spread exceeds 10% of its own median] |

## tools_list_tokens (ratio vs jcm)

| tool | self@b70b439c |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.365e+04 spread 0 |
| cymbal | NOT COMPARABLE |

## f1_P1 (difference vs jcm)

| tool | self@b70b439c |
|---|---|
| null_readall | 0 (delta -0.333, band 0.0167, MEANINGFUL) spread 0 |
| null_grep | 0.2669 (delta -0.0664, band 0.0167, MEANINGFUL) spread 0 |
| jcodemunch | 0.3333 spread 0 |
| cymbal | 0.2606 (delta -0.0727, band 0.0167, MEANINGFUL) spread 0 |

## f1_P2 (difference vs jcm)

| tool | self@b70b439c |
|---|---|
| null_readall | 0 (delta 0, band 0) spread 0 |
| null_grep | 0.1818 (delta 0.182, band 0, MEANINGFUL) spread 0 |
| jcodemunch | 0 spread 0 |
| cymbal | 1 (delta 1, band 0, MEANINGFUL) spread 0 |

## f1_P4 (difference vs jcm)

| tool | self@b70b439c |
|---|---|
| null_readall | 0.0001 (delta -0.667, band 0.0333, MEANINGFUL) spread 0 |
| null_grep | 0 (delta -0.667, band 0.0333, MEANINGFUL) spread 0 |
| jcodemunch | 0.6667 spread 0 |
| cymbal | 0.6154 (delta -0.0513, band 0.0333, MEANINGFUL) spread 0 |
