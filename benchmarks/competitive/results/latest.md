# Competitive tier — 2026-09-05T12:08:43Z at fd4d5bf (1.108.317)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@fd4d5bf` 275 files, sha256 `b70fbed3c3a3`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@fd4d5bf (ran as fd4d5bf)

## tokens_per_task (ratio vs jcm)

| tool | self@fd4d5bf |
|---|---|
| null_readall | 9.441e+05 (delta 466, band 101, MEANINGFUL) spread 0 |
| null_grep | 3.667e+04 (delta 18.1, band 101, MEANINGFUL) spread 0 |
| jcodemunch | 2026 spread 0 |

## calls_per_task (ratio vs jcm)

| tool | self@fd4d5bf |
|---|---|
| null_readall | 275 (delta 68.8, band 0.2, MEANINGFUL) spread 0 |
| null_grep | 3.75 (delta 0.938, band 0.2, MEANINGFUL) spread 0 |
| jcodemunch | 4 spread 0 |

## latency_warm_ms (ratio vs jcm)

| tool | self@fd4d5bf |
|---|---|
| null_readall | 28.94 (delta 1.33, band 1.09, MEANINGFUL) spread 0.34 |
| null_grep | 0.15 (delta 0.0069, band 1.09, MEANINGFUL) spread 0.01 |
| jcodemunch | 21.73 spread 0.09 |

## index_cold_seconds (ratio vs jcm)

| tool | self@fd4d5bf |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.556 spread 0.0756 |

## tools_list_tokens (ratio vs jcm)

| tool | self@fd4d5bf |
|---|---|
| null_readall | NOT COMPARABLE |
| null_grep | NOT COMPARABLE |
| jcodemunch | 2.274e+04 spread 0 |

## f1_P1 (difference vs jcm)

| tool | self@fd4d5bf |
|---|---|
| null_readall | 0 (delta -0.635, band 0.0317, MEANINGFUL) spread 0 |
| null_grep | 0.6538 (delta 0.0189, band 0.0317) spread 0 |
| jcodemunch | 0.6349 spread 0 |
