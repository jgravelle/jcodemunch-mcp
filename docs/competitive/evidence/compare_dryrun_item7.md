# Competitive compare: the working tree against a ref

Current: jcm `4137064b` (1.108.317), 2026-09-06T18:18:38Z, runs 3, sandbox `docker`, tree dirty True.
Ref: jcm `59d2e405` (1.108.317), 2026-09-06T18:22:49Z, runs 3, sandbox `docker`.
Scorer (current): sha256 `7dcd617ddb2d49b6e36848627989a76a9c5cb20ed911a1d785b9d5a8e7d457f8`; interpreter 3.12.4; wall 240 s.
Scorer (ref): sha256 `522dd5dd18f57def81a00cedeffe5db1f661d10196f522007f4a84b797f60c88`; interpreter 3.12.6; wall 241 s.
⚠ The two sides were scored by DIFFERENT scorer code (run.py, score.py, an adapter or the sandbox changed between the two commits): every `movement` below compares two scorers' outputs, and a row that moved may have moved with the scorer.
⚠ The two sides ran on different interpreters; a latency or index-time row is not a like-for-like measurement.

Corpora (current): `self@4137064b` 277 files, sha256 `c1cecae571a8bf3d09c87b90ffd6c87d83d4a1e7c356b7c3a583bdde432d37d9`
Corpora (ref): `self@59d2e405` 277 files, sha256 `9f7c1857c6434926dbf89a3fc342a0cd2cad9dc22178ebd5dc7a160384eba2aa`
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
| index_cold_seconds | self | 17.1740313 | 15.51210089 | -1.66193 | unstable: a spread exceeds 10% of its own median |
| latency_call_ms | self | 54.36 | 50.45 | -3.91 | unstable: a spread exceeds 10% of its own median |
| tokens_per_task | self | 1655.9 | 1655.9 | +0 |  |
| tools_list_tokens | self | 23652 | 23652 | +0 |  |

## Every other row: the gap to jcm on each side

| axis | tool | corpus | ref measured | ref delta | current measured | current delta | band | movement | note |
|---|---|---|---|---|---|---|---|---|---|
| calls_per_task | cymbal | self | 2.5 | 0.7353 | 2.5 | 0.7353 | 0.17 | unchanged |  |
| calls_per_task | null_grep | self | 3.6 | 1.0588 | 3.6 | 1.0588 | 0.17 | unchanged |  |
| calls_per_task | null_readall | self | 277 | 81.4706 | 277 | 81.4706 | 0.17 | unchanged |  |
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
| index_cold_seconds | cymbal | self | 1.993 | 0.116 | 1.73 | 0.1115 | 12.4163 | unchanged | unstable: a spread exceeds 10% of its own median |
| index_cold_seconds | null_grep | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| index_cold_seconds | null_readall | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| latency_call_ms | cymbal | self | 712 | 13.0979 | 668 | 13.2408 | 282 | unchanged | unstable: a spread exceeds 10% of its own median |
| latency_call_ms | null_grep | self | 1.4 | 0.0258 | 1.31 | 0.026 | 19.62 | unchanged | unstable: a spread exceeds 10% of its own median |
| latency_call_ms | null_readall | self | 34.95 | 0.6429 | 33.22 | 0.6585 | 19.62 | unchanged | unstable: a spread exceeds 10% of its own median |
| tokens_per_task | cymbal | self | 880.9 | 0.532 | 880.9 | 0.532 | 82.795 | unchanged |  |
| tokens_per_task | null_grep | self | 118038.4 | 71.2835 | 118038.4 | 71.2835 | 82.795 | unchanged |  |
| tokens_per_task | null_readall | self | 1161455 | 701.4041 | 1161455 | 701.4041 | 82.795 | unchanged |  |
| tools_list_tokens | cymbal | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| tools_list_tokens | null_grep | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |
| tools_list_tokens | null_readall | self | n/a | n/a | n/a | n/a | n/a | n/a | NOT COMPARABLE |

## Counts (of rows on this page, not of measurements)

Rows 36: jcm rows 9, of which 8 have both sides, 6 differ by exactly 0 and 0 moved past the current band; other rows 27, movement n/a 8, unchanged 19.
