# Local encoder padding measurements

Windows 10, 8 logical CPUs, Python 3.12.11, NumPy 2.5.4, ONNX Runtime 1.31.1,
all-MiniLM-L6-v2, two intra-op threads. Reference encoder: official 1.108.429
(unchanged through 1.108.431), SHA-256
`1d745e21b8c7fc610a3f890a4902d3cf22b4d538923dd5cd92910f63c64c884a`.
These are one-machine observations, not a portable throughput guarantee.

## Why groups of 16?

Sixteen is provisional. The one retained run (below) puts groups of 8
slightly ahead of 16 at the default caller batch of 50, and earlier
exploratory runs at caller batches of 128 and 200 kept no output, so they
are not quoted here. The constant is internal, not a user configuration
surface; re-run the comparison below before changing it.

The regression tests use a 1e-5 tolerance; cross-platform bit identity is
not claimed.

## Repeat the group-size comparison

Use the local-embed optional dependencies and download the model before
timing. Set `JCODEMUNCH_LOCAL_EMBED_MODEL` to its cached directory. Save the
trusted official encoder from the published wheel and supply a checkpointed
copy of a code-index database with no WAL:

```sh
PYTHONPATH=src python benchmarks/benchmark_local_encoder.py \
  --baseline /path/to/official/local_encoder.py \
  --database /path/to/checkpointed-copy.db \
  --batch-size 50 --run-sizes 8 16 32 --repeats 2 \
  --output encoder-padding-results.json
```

The sample is `name`, `signature`, `summary` for `rowid % 97 = 0 LIMIT 512`,
matching the Django experiment. Repeat with caller batches 128 and 200 to
check the tradeoff. The script reads the database without modifying it,
loads no uncached model deliberately, reverses arm order on alternate
repeats, records every timing and asserts sampled vector equivalence. Run
it without concurrent tests or benchmarks. Output omits local database and
model paths so it can be shared after checking its contents.

## Fresh split-PR run (2026-11-10)

After both local full suites finished, the portable script above retained all
measurements on this branch at caller batch 50, 512 Django texts and two
threads. Arms ran baseline/8/16/32 and then 32/16/8/baseline:

| Encoder/group size | First pass texts/s | Reverse pass texts/s |
|---|---:|---:|
| Official full padding | 19.13 | 18.35 |
| 8 | 133.77 | 143.46 |
| 16 | 127.75 | 128.82 |
| 32 | 96.46 | 74.27 |

All eight results had max absolute vector difference 0.0. Raw seconds,
library versions and encoder hashes are in [encoder-padding-sample.json](encoder-padding-sample.json).
Eight is again faster at this caller batch; these measurements do not
establish 16 as an optimum. No concurrent test or benchmark from this task
was running during the measurement.
