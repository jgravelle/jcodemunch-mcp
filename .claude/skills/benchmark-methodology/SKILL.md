---
name: benchmark-methodology
description: "Rules for measuring or quoting any benchmark number in this repo. Load before running the bench tier, writing a delta into a PR or CHANGELOG, or touching anything under benchmarks/."
---
# Benchmark methodology

Authority: `docs/harness/ARCHAEOLOGY.md` (the benchmark rows),
`benchmarks/METHODOLOGY.md`, `benchmarks/REPRODUCING.md`, CLAUDE.md
Practice 4, `docs/harness/FINDINGS.md` F-10, F-13, F-17.

Rules, each a pointer, none restated:
1. **Never hand-type a number.** Every figure comes from a run in THIS
   session or the CI-captured artifact `benchmarks/jcm_reference.json`
   (Practice 4). The comparators read that file; there is no estimator.
2. **Per row, never per total** (F-13): the total hid one cause behind
   another. Diff threshold ids one by one.
3. **Five mirrors move together** (Practice 4): `results.md`,
   `METHODOLOGY.md`, README, `provenance/measured.json`, `REPRODUCING.md`;
   `tests/test_provenance.py` is the gate and `--reference` rewrites them.
4. **The reference is captured where the gate runs** (F-13):
   `benchmark.yml` dispatch with `reference=true`; a dev-box number differs
   by CRLF, walk order and a HOME ledger (F-17).
5. **Deterministic configuration** is the bench tier itself: `--offline`,
   pinned corpora (`benchmarks/tasks.json`), the no-network fixture. Add no
   flags.
6. **A refusal is not a zero**: an absent value prints `n/a`.
7. **Floors live only in `harness/thresholds.json`**; read one with
   `uv run python -m harness threshold <id>`.
8. `benchmarks/schema_baseline.json` is the only source for schema-token
   figures (CLAUDE.md "Tier-switch pricing"); the harness counts a
   different payload and the two never agree digit for digit.
