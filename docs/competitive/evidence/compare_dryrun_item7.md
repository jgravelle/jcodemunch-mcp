# Competitive compare: the working tree against a ref

Current: jcm `4137064b` (1.108.317), 2026-09-06T18:18:38Z, runs 3, sandbox `docker`, tree dirty True.
Ref: jcm `59d2e405` (1.108.317), 2026-09-06T18:22:49Z, runs 3, sandbox `docker`.

Corpora (current): `self@4137064b` 277 files, sha256 `c1cecae571a8`
Corpora (ref): `self@59d2e405` 277 files, sha256 `9f7c1857c643`
Tools (current): `null_readall`@baseline-A, `null_grep`@baseline-B, `jcodemunch`@4137064b, `cymbal`@0.14.0
Filter: tool cymbal (plus the nulls and jcodemunch); --set none (self corpus only; corpus check recorded, not enforced).

A competitor's README figure is not on this page. Every number was produced by one of these two runs on its corpus with this tokenizer (cl100k_base); `measured` is the median of the runs; a delta is the row's ratio (or difference) to its own side's jcm; `band` is the current run's; `movement` is `trend.classify` over the two gaps to jcm, judged against that band. Per row, never per total. `n/a` is a value one side did not produce, never 0.

## Our rows (jcodemunch): current minus ref, signed, in the axis's own unit

| axis | corpus | ref measured | current measured | difference | note |
|---|---|---|---|---|---|
| calls_per_task | self | 3.4 | 3.4 | +0 |  |
| f1_P1 | self | 0.3333 | 0.3333 | +0 |  |
| f1_P2 | self | 0 | 0 | +0 |  |
| f1_P4 | self | 0.4324 | 0.4324 | +0 |  |
| f1_P5 | self | n/a | n/a | n/a | NOT COMPARABLE |
| index_cold_seconds | self | 17.17 | 15.51 | -1.662 | unstable: a spread exceeds 10% of its own median |
| latency_call_ms | self | 54.36 | 50.45 | -3.91 | unstable: a spread exceeds 10% of its own median |
| tokens_per_task | self | 1656 | 1656 | +0 |  |
| tools_list_tokens | self | 2.365e+04 | 2.365e+04 | +0 |  |

## Every other row: the gap to jcm on each side

| axis | tool | corpus | ref measured | ref delta | current measured | current delta | band | movement | note |
|---|---|---|---|---|---|---|---|---|---|
| calls_per_task | cymbal | self | 2.5 | 0.7353 | 2.5 | 0.7353 | 0.17 | unchanged |  |
| calls_per_task | null_grep | self | 3.6 | 1.059 | 3.6 | 1.059 | 0.17 | unchanged |  |
| calls_per_task | null_readall | self | 277 | 81.47 | 277 | 81.47 | 0.17 | unchanged |  |
| f1_P1 | cymbal | self | 0.2606 | -0.0727 | 0.2606 | -0.0727 | 0.0167 | unchanged |  |
| f1_P1 | null_grep | self | 0.2299 | -0.1034 | 0.2299 | -0.1034 | 0.0167 | unchanged |  |
| f1_P1 | null_readall | self | 0 | -0.3333 | 0 | -0.3333 | 0.0167 | unchanged |  |
| f1_P2 | cymbal | self | 1 | 1 | 1 | 1 | 0 | unchanged |  |
| f1_P2 | null_grep | self | 0.1818 | 0.1818 | 0.1818 | 0.1818 | 0 | unchanged |  |
| f1_P2 | null_readall | self | 0 | 0 | 0 | 0 | 0 | unchanged |  |
| f1_P4 | cymbal | self | 0.4737 | 0.0413 | 0.4737 | 0.0413 | 0.0216 | unchanged |  |
| f1_P4 | null_grep | self | 0 | -0.4324 | 0 | -0.4324 | 0.0216 | unchanged |  |
| f1_P4 | null_readall | self | 0.0005 | -0.4319 | 0.0005 | -0.4319 | 0.0216 | unchanged |  |
| f1_P5 | cymbal | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| f1_P5 | null_grep | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| f1_P5 | null_readall | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| index_cold_seconds | cymbal | self | 1.993 | 0.116 | 1.73 | 0.1115 | 12.42 | unchanged | unstable: a spread exceeds 10% of its own median |
| index_cold_seconds | null_grep | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| index_cold_seconds | null_readall | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| latency_call_ms | cymbal | self | 712 | 13.1 | 668 | 13.24 | 282 | unchanged | unstable: a spread exceeds 10% of its own median |
| latency_call_ms | null_grep | self | 1.4 | 0.0258 | 1.31 | 0.026 | 19.62 | unchanged | unstable: a spread exceeds 10% of its own median |
| latency_call_ms | null_readall | self | 34.95 | 0.6429 | 33.22 | 0.6585 | 19.62 | unchanged | unstable: a spread exceeds 10% of its own median |
| tokens_per_task | cymbal | self | 880.9 | 0.532 | 880.9 | 0.532 | 82.8 | unchanged |  |
| tokens_per_task | null_grep | self | 1.18e+05 | 71.28 | 1.18e+05 | 71.28 | 82.8 | unchanged |  |
| tokens_per_task | null_readall | self | 1.161e+06 | 701.4 | 1.161e+06 | 701.4 | 82.8 | unchanged |  |
| tools_list_tokens | cymbal | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| tools_list_tokens | null_grep | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| tools_list_tokens | null_readall | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
