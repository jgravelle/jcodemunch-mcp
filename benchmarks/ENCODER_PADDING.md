# Local encoder padding measurements

Windows 10, 8 logical CPUs, Python 3.12.11, NumPy 2.5.4, ONNX Runtime 1.31.1,
all-MiniLM-L6-v2, two intra-op threads. Reference encoder: official 1.108.429
(unchanged through 1.108.431), SHA-256
`1d745e21b8c7fc610a3f890a4902d3cf22b4d538923dd5cd92910f63c64c884a`.
These are one-machine observations, not a portable throughput guarantee.

## Why groups of 16?

An exploratory experiment used 512 Django symbol texts and compared sorted
inference groups. Each arm was timed twice and the faster result was kept;
the original output was not saved. This table is transcribed evidence, not
a reproducible statistical conclusion:

| Caller batch | Whole-batch trim | Groups of 8 | Groups of 16 | Groups of 32 |
|---:|---:|---:|---:|---:|
| 50 | 50.4 | 169.0 | 137.6 | 98.6 |
| 128 | 43.0 | 167.2 | 147.8 | 104.5 |
| 200 | 37.4 | 133.9 | 136.4 | 145.6 |

Units: texts/second. Sixteen was a middle choice that was never the slowest
sorted group size in this sample. Eight was faster at the default caller
batch of 50; 32 was faster at 200. Sixteen is not established as optimal on
other corpora, thread counts or hardware. The constant is internal, not a
new user configuration surface.

## Subsequent validation of the selected size

The combined local prototype used the same padding path at two threads:

- Django, 512 texts, caller batches 50/128, both run orders: official
  18.61–20.46 vs patched 136.27–148.69 texts/s (6.78–7.94x).
- Five other repository samples, 511–512 texts each and caller batch 50:
  2.72–5.17x. The samples deliberately overrepresented the longest texts.
- Fastify, 2,593 symbols, scratch database copies: `embed_repo(force=True)`
  129.31 s official vs 26.96 s patched (4.80x); stored vectors had identical
  IDs and max absolute difference 0.0. Six semantic-only query result lists
  and scores matched. This is one end-to-end sample, not a general search
  latency claim.

All tested vectors had max absolute difference 0.0 on that machine/provider.
The regression test uses a 1e-5 tolerance; cross-platform bit identity is not
claimed. These historical numbers predate the independent PR split.

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
