# Reproducing the token-efficiency benchmark

Every number in [`results.md`](results.md) and [`jcm_reference.json`](jcm_reference.json)
comes from the run described here. If your run disagrees, the difference is
locatable: the artifact records which upstream tree each number was measured
against and whether all of that tree was indexed.

## What you need

- Python 3.10+, `pip install jcodemunch-mcp tiktoken`
- `git`
- ~2 GB of disk for the three clones and their indexes

## 1. Clone the pinned trees

The benchmark is pinned to specific upstream commits, listed in
[`tasks.json`](tasks.json). Indexing whatever `main` points at today measures a
different corpus and will not reproduce a published number.

```bash
mkdir bench-corpus && cd bench-corpus

git clone --filter=blob:none --no-checkout https://github.com/expressjs/express.git express
git -C express checkout 1faf228935aa0a13111f92c28ee795be64ce3f0f

git clone --filter=blob:none --no-checkout https://github.com/fastapi/fastapi.git fastapi
git -C fastapi checkout a64dfbbd21a445288ff583d58e1f646fe6baf3af

git clone --filter=blob:none --no-checkout https://github.com/gin-gonic/gin.git gin
git -C gin checkout 75ccf94d605a05fe24817fc2f166f6f2959d5cea
```

Directory names do not matter. The index is keyed from the clone's `origin`
remote, so `./gin` indexes as `gin-gonic/gin`, which is what the harness looks
up.

**Check out LF.** Clone with `-c core.autocrlf=false` on Windows (or run
`git config core.autocrlf false && git rm --cached -r -q . && git reset --hard`
inside each clone). A CRLF checkout serves `

` inside every fetched symbol
and measured **+603 tokens (2.5%)** over the same pins on 2026-09-03; the
published reference is captured on an LF checkout (harness F-13).

## 2. Index them

```bash
jcodemunch-mcp index ./express --no-ai-summaries
jcodemunch-mcp index ./fastapi --no-ai-summaries
jcodemunch-mcp index ./gin     --no-ai-summaries
```

AI summaries are off so the measurement is of retrieval, not of a summarizer
that may not be configured on your machine.

If a repo lands as `local/<dir>-<hash>` instead of `owner/repo`, the
`git_root_identity` knob is off in your config — set it back to `true` (its
default), delete the local-keyed index, and re-index.

**Do not lower the file cap.** Folder indexing caps at `max_folder_files`
(default 2,000; env `JCODEMUNCH_MAX_FOLDER_FILES`), and the largest of these
three corpora is 1,182 files, so all three fit. A lower cap truncates silently
— the harness will refuse to publish the result (see *Corpus objections*), but
only because that refusal was added in v1.108.222.

## 3. Run it

```bash
python benchmarks/harness/run_benchmark.py
```

To check that your machine reproduces the run at all before comparing numbers:

```bash
python benchmarks/harness/run_benchmark.py --verify-determinism
```

That measures the whole corpus twice, the second time in a fresh interpreter,
and fails if the token counts differ. There is no seed to pin — the retrieval
path the benchmark exercises is lexical and has no RNG — but that is a claim
worth checking on your hardware rather than taking on faith.

## What you should get

Captured on CI 2026-10-22 at jcodemunch-mcp 1.108.391, commit `15afcf75`, whose version string is 1.108.391 but which also carries the settings-file ranking change released in 1.108.392 and the field/constant change released after it (the symbol counts move with the
extractor; the files and baseline tokens are fixed by the pins):

| Repo | Commit | Files | Symbols | Baseline tokens |
|------|--------|------:|--------:|----------------:|
| expressjs/express | `1faf228935aa` | 186 | 989 | 154,569 |
| fastapi/fastapi | `a64dfbbd21a4` | 1,186 | 13,699 | 825,326 |
| gin-gonic/gin | `75ccf94d605a` | 98 | 1,822 | 151,842 |

Grand total across 15 task-runs, both baselines measured in the same run:

| Baseline | Tokens | jCodeMunch | Reduction | Ratio |
|---|--:|--:|--:|--:|
| **Grep-top-3** (quote this) | **664,975** | 25,132 | **96.2%** | **26.5x** |
| Read-all (ceiling) | 5,658,685 | 25,132 | 99.6% | 225.2x |

Small differences in `file_count` are expected across installations and are not
a bug in either run: what a machine can index depends on its grammar pack and
size limits, not on the repo alone. That is what the
[corpus capability certificate](../src/jcodemunch_mcp/evidence/capability.py)
exists to record. A difference in `git_head` is a different matter — that is a
different corpus and the numbers are not comparable.

## Corpus objections

`--reference` refuses to overwrite the published artifact when any repo:

- has no SHA pinned in `tasks.json`,
- carries no `git_head`, so the pin cannot be checked,
- reports a `git_head` that differs from its pin, or
- has a corpus completeness of anything other than `True` — including
  `"unknown"`, which is what an index with no coverage record reports.

`--allow-unpinned` overrides the refusal and stamps the artifact
`"provisional": true` with the objections recorded inside it.

That last condition is not hypothetical. Every number published through
v1.108.221 was measured against a `fastapi/fastapi` index holding 1,000 of
1,182 eligible files, truncated by a file cap that was 1,000 at index time. The
index's coverage record was `{}` and nothing in the artifact could say whether
that corpus was whole. Re-measuring against the full tree showed the 182
dropped files were all empty `__init__.py` files under `docs_src/`, worth zero
tokens — the headline never moved. The point is that this was established by
measuring it, and until v1.108.222 there was no way to.

## Known measurement conditions

**The reference is captured on CI, not on a developer box** (2026-09-03,
harness F-13). `benchmark.yml` dispatched with `reference=true` runs
`--reference` on the ubuntu runner and uploads `jcm_reference.json`,
`results.md` and `provenance/measured.json`; those are what get committed. A
local run reproduces it to the token only ON THE SAME PLATFORM (the ubuntu
runner) and under three conditions: an LF checkout, a fresh `CODE_INDEX_PATH`,
and `~/.code-index/_savings.json` either absent or the same width (see the next
two paragraphs).

**On another operating system the total agrees and single rows do not.**
An outside reproduction report (Benchmark Bot, 2026-10-13, Windows 11, version
1.108.316, reported as run with an LF checkout, a fresh index and a fresh home
directory; not in this repository) found 6 of 15 rows within 1 token of the
published table, the rest off by up to 38, and the total within 0.13%. The likely
cause, inferred from harness F-13 and not isolated in that run, is that ranking
ties are broken by directory-walk order, which differs between Linux and Windows. This page said "to the token" without the platform until
2026-10-13.

**The published count includes the width of your lifetime savings counter.**
`_meta.total_tokens_saved` is read from `~/.code-index/_savings.json` in the
HOME directory, not from `CODE_INDEX_PATH`, and it rides inside every counted
search and fetch payload. A box with a nine-digit ledger measures +1 search and
+3 fetch tokens per query over a fresh runner, on every query, and each run
adds to that ledger. Repeating a run on one box can move the published-style
counts too: the same outside report found three repeats differing by up to 6
tokens on one query, with the three totals spread over 58 to 59 tokens, while
`stable_tokens` did not move. The cause was not isolated there; a ledger that
crosses a digit width between repeats is one that fits. `stable_tokens` pins it; the published figure does not.

**Every query is measured cold.** `search_symbols` adds a `_meta.cache_hit`
field once a query has been served in-process, which costs 5 more tokens
(~0.4% of the jCodeMunch side of a query). The benchmark reports the first,
uncached call — the pessimistic direction. Re-running the loop inside one
process will read about 0.4% worse and that is not a regression.

**`timing_ms` rides inside the counted payload, and it is why you should expect
±1 token per query rather than an exact match.** This paragraph used to say its
width "has been identical across every run measured so far". That stopped being
true and CI had been saying so since v1.108.222: under `cl100k_base` a
wall-clock figure is **3 tokens below 1000ms and 4 at or above**, so a query
straddling one second moves the payload by exactly one token. A loaded runner
straddles; a fast dev box does not. Observed: `search_tokens: 499 != 500`.

`--verify-determinism` therefore compares `stable_tokens` — the same payload
counted with `timing_ms` and the monotonic `total_tokens_saved` counter pinned —
not the published figure. **Those `stable_tokens` values reproduce bit for bit;
the published ones reproduce to ±1 token per query.** A gate failure now names
the field that moved, and if that field is `stable_tokens` it is a retrieval
change, not a clock. See [`METHODOLOGY.md`](METHODOLOGY.md) *Token Counting
Method*.

**The baseline is a lower bound**, and an unrealistic one — see
[`METHODOLOGY.md`](METHODOLOGY.md) *Limitations*. "Concatenate every indexed
file" is the floor for an agent that reads everything once; no agent works that
way, in either direction.
