# Under the Hood — the jCodeMunch technical manual

The [USER_GUIDE](USER_GUIDE.md) is the owner's manual: install it, index a repo, run your
first workflows. This document is the workshop manual — how the machine actually works,
for developers who want to exploit the parts that don't show up in a quick start:
the honesty machinery, the ranking internals, the measurement contracts, and the
self-discipline systems that keep a 100+-tool server from eating your context window.

Everything described here ships in the current release and is exercised by the test
suite. Where a chapter cites a number, the number traces to a committed, reproducible
artifact — that contract is itself [Chapter 3](#chapter-3--every-number-states-its-basis).

## Chapter map

**In this manual:**

1. [How jCodeMunch knows when it's wrong](#chapter-1--how-jcodemunch-knows-when-its-wrong) — verdicts, confidence, freshness
2. [What the ranking actually does](#chapter-2--what-the-ranking-actually-does) — channels, exact-name pinning, learned weights, the regret loop
3. [Every number states its basis](#chapter-3--every-number-states-its-basis) — the declared/measured provenance contract and the gold corpus
4. [The meter only errs downward](#chapter-4--the-meter-only-errs-downward) — how token savings are counted
5. [The server disciplines itself](#chapter-5--the-server-disciplines-itself) — tool tiers and the schema token budget
6. [Evidence imports](#chapter-6--evidence-imports) — compiler indexes and runtime traces, imported rather than executed

**The rest of the shelf** (existing deep references this manual builds on):

- [ARCHITECTURE.md](ARCHITECTURE.md) — system overview: storage model, repo identity, indexing strategies, import graph
- [SPEC.md](SPEC.md) — protocol semantics and tool contracts
- [CONFIGURATION.md](CONFIGURATION.md) — configuration reference
- [CONTEXT_PROVIDERS.md](CONTEXT_PROVIDERS.md) — the context-provider framework
- [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md) and [benchmarks/METHODOLOGY.md](benchmarks/METHODOLOGY.md) — the savings methodology in full
- [schemas/](schemas/) — machine-readable JSON Schemas for the response contracts described here

**Forthcoming chapters** (planned, in priority order): the `_meta` envelope
field-by-field, the CLI reference (40+ subcommands), storage internals and
`INDEX_VERSION` semantics, the replay and gold-corpus harnesses as reusable tools,
and the hooks/watch subsystem.

---

## Chapter 1 — How jCodeMunch knows when it's wrong

Most retrieval tools return their top-k and let the agent guess whether to trust it.
jCodeMunch ships three independent honesty signals on retrieval responses, so the
agent can gate its next action on data instead of vibes.

### Retrieval verdicts (`negative_evidence`)

When a search comes back empty or weak, that's information — and it's easy to
misread. A missing result can mean *the thing doesn't exist* or *the index couldn't
see it*. The verdict contract distinguishes those:

- **`ok`** — confident matches returned.
- **`low_confidence`** — matches exist but score below the confidence threshold;
  verify before relying on them.
- **`absent`** — the corpus was genuinely scanned and the answer isn't there. The
  verdict carries the scan counts (how many symbols/files were actually examined),
  so "absent" is a claim with evidence, not a shrug.
- **`degraded`** — the index itself is impaired (stale, partial, or mid-rebuild);
  a missing or thin result may be truncation, not absence. Don't conclude anything
  from silence in this state.

The distinction between `absent` and `degraded` is the whole point: an agent that
treats every empty result as "doesn't exist" will confidently hallucinate the
negative. The contract is published as
[`schemas/retrieval-verdict.schema.json`](schemas/retrieval-verdict.schema.json).

### Calibrated confidence (`_meta.confidence`)

Every retrieval result carries a 0–1 confidence score composed from measurable
components: the top-1 vs top-2 score gap (is the winner dominant or is it a
coin flip?), absolute top-1 strength, whether an exact identity match is present,
and index freshness. One number, gateable: an agent can decide "above 0.8, use it;
below, fetch the source and check" without parsing prose.

### Per-symbol freshness

Every result entry is stamped `fresh`, `edited_uncommitted`, or `stale_index`,
derived from comparing the index's recorded git HEAD against the working tree's
actual HEAD plus per-file mtime checks. The index doesn't pretend the world stopped
at indexing time — it tells you, per symbol, whether what it's showing you still
matches the bytes on disk.

---

## Chapter 2 — What the ranking actually does

`get_ranked_context` looks like "BM25 + PageRank" from the outside. Inside, there
are five moving parts worth knowing about.

### Channel fusion

Ranked retrieval fuses independent signal channels — lexical (BM25 over symbol
text), structural (graph centrality), identity (exact and qualified-name matches),
and similarity (embeddings, when enabled) — rather than betting everything on one
scoring model. A query that's weak lexically can still win on identity or structure.

### Exact-name pinning (query shape)

Queries written by developers and agents are full of *source-shaped tokens*:
`Store.flush`, `get_ranked_context`, `TokenTracker`, `__init__`. The query-shape
analyzer recognizes qualified names, CamelCase, snake_case, and dunder forms
(filename look-alikes like `server.py` are deliberately excluded), and pins exact
symbol-name matches ahead of the lexical ranking — capped at 3 seeds per token and
5 total, so pinning can't crowd out the ranked results. The response reports what
happened in `_meta.query_shape`: which tokens were recognized as source-shaped and
how many symbols were seeded. Practical consequence, and the reason the tool
description now says so: **if you know the identifier, put it in the query verbatim.**

### PageRank over the import graph

Centrality isn't decoration. A symbol imported (directly or transitively) by half
the codebase ranks above an identically-scored leaf, which is usually what "the
important one" means when a query is ambiguous.

### Learned per-repo weights

`tune_weights` reads the persistent ranking ledger — the record of what was
retrieved and what the session actually used — and learns per-repo retrieval
weights, saved to `~/.code-index/tuning.jsonc`. Ranking adapts to the repo's
vocabulary and structure instead of shipping one global tune. Every release runs
the replay harness (`benchmarks/replay/`, nDCG/MRR/Recall) as a CI regression gate,
so a ranking change that helps one query class at the expense of another gets
caught before it ships.

### The regret loop

`suggest_corrections` (and the `reflect` CLI) mine the same ledger for **retrieval
regret** — cases where retrieval failed and the agent had to re-ask or fall back to
raw reads. It returns prioritized, explainable suggestions: a CLAUDE.md routing
line as a unified-diff preview, an index-freshness hint, a dry-run weight proposal.
It is read-only by design. It shows you the diff; applying it is your keystroke,
never the server's. The retrieval system critiques itself, and then stops.

---

## Chapter 3 — Every number states its basis

This is the chapter the others lean on. jCodeMunch emits a lot of numbers —
confidence priors, savings figures, quality metrics — and every one of them is
governed by a single rule:

> **A prior is never presented as a measurement.**

Every confidence constant traces to a stated basis, carried in responses as
machine-readable provenance:

- **`measured`** — backed by a committed, reproducible artifact
  ([`benchmarks/provenance/measured.json`](benchmarks/provenance/measured.json),
  [`benchmarks/provenance/channel_accuracy.json`](benchmarks/provenance/channel_accuracy.json)).
  CI re-runs the underlying measurement on every build and asserts the committed
  artifact matches the live result. The numbers structurally cannot drift from the
  run that produced them — a mismatch fails the build.
- **`declared`** — an engineering prior, honestly labeled as exactly that.

A `declared` value graduates to `measured` only when a gold-labeled corpus backs it.
The first such corpus is in: `benchmarks/goldset/` is an authored
implementation-pattern corpus across Python, TypeScript, and Go — declared
subclasses, duck-typed conformers, decorator-registered handlers — seeded with
deliberate false-positive traps (module-homonym base classes, same-name methods
from different domains, decorator substring collisions), every pair labeled with a
written rationale.

The measurement is candid about what it found. The duck-typing channel's declared
prior is 0.65; its measured precision on the corpus is 0.5. That gap is *visible in
every response* via `measured_ref` in `_meta.confidence_provenance`, rather than
silently absorbed into a constant. The operating priors change only through a
deliberate, replay-gated recalibration once the corpus is large enough to trust —
not by quietly editing a number to match last week's run.

The response contracts are published as JSON Schemas
([`schemas/confidence-provenance.schema.json`](schemas/confidence-provenance.schema.json),
[`schemas/ranked-context-response.schema.json`](schemas/ranked-context-response.schema.json)),
so a CI pipeline — yours, not just ours — can validate responses mechanically.

---

## Chapter 4 — The meter only errs downward

The token-savings meter is designed so that every known error term points the same
direction: **down**. If the meter is wrong, it's underselling.

- **~4 bytes per token.** The byte-to-token conversion undercounts denser code
  (real-world source frequently runs richer than 4 bytes/token). The full
  tiktoken-measured methodology behind the headline reduction figure is in
  [benchmarks/METHODOLOGY.md](benchmarks/METHODOLOGY.md).
- **Each matched file is credited once** per call, no matter how many symbols
  matched inside it.
- **Empty and negative results credit zero.** A search that saved you nothing
  records nothing; the counter clamps at zero rather than booking a phantom saving.
- **Cache hits count** (since v1.108.133) — a repeat query avoids the same raw
  reads as the first one, and for a long time the meter didn't record it. That was
  a systematic *under*count, fixed in the direction of accuracy.
- **Savings are measured in tokens**, not dollars. Currency valuations are applied
  at display time at current published input-token rates (see
  [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md)), so the stored record never bakes in a
  price that later changes.

The lifetime meter (`~/.code-index/_savings.json`) survives client reinstalls and
is independently checkable: run `jcodemunch-mcp receipt` against your own transcript
history and compare. The receipt's JSON export carries the same provenance block as
Chapter 3 — the ledger ships with its own receipts.

---

## Chapter 5 — The server disciplines itself

A tool server that exposes 100+ tools has a failure mode nobody talks about: its
own schema listing becomes the context-window tax it claims to fight.

- **Tool tiers.** The adaptive tool surface exposes a compact core tier by default
  and escalates on demand (`set_tool_tier`); the self-guide tool is always present
  so a one-line CLAUDE.md setup keeps working at any tier.
- **A hard schema budget, CI-enforced.** The compact core tier's full schema
  payload must fit under a 4,000-token ceiling, measured with a real tokenizer in
  the test suite. The test's own failure message forbids the easy cheat: *trim a
  description — do not regenerate the baseline to paper over it.* (This manual's
  release tripped that guard and got its prose tightened by it. The system works.)
- **Read-only annotations, machine-checkable.** Every retrieval tool ships MCP
  `readOnlyHint: true`; the handful of tools that reach the network or write an
  index carry `openWorldHint` honestly. Hosts that gate on annotations — plan
  modes, approval UIs — can trust the surface because the annotations are part of
  the tested contract, not marketing.
- **Progress hygiene.** Long operations throttle MCP progress notifications and
  drain them before the response, because a notification flood can cost a client
  the actual result.

---

## Chapter 6 — Evidence imports

Some evidence is stronger than static analysis can produce: what the compiler
resolved, what actually executed. jCodeMunch's rule is that such evidence is
**imported, never generated** — the server never runs your code, spawns a shell,
or executes SQL against your repo.

- **`import-scip`** ingests compiler-grade SCIP indexes (the format emitted by
  language-server tooling). Once imported, `find_implementations` carries a
  compile-time evidence channel at confidence 1.0 — a claim backed by the compiler,
  not a heuristic, and labeled as such in `_meta.confidence_provenance`.
- **`import-trace`** ingests runtime signal, feeding runtime-coverage and hot-path
  views with what actually ran rather than what statically might.
- **`import-trace --diagnostics <file>`** ingests a type checker's or linter's own
  output — `mypy --output json`, `pyright --outputjson`, `tsc --noEmit --pretty false`,
  `ruff check --output-format json`, or a generic JSON-Lines
  `{file, line, severity, message, code?, tool?}` — and attaches each finding to
  the innermost symbol containing its line. The `diagnostics` table is a
  **snapshot, replaced per tool** on every ingest, because a fixed error must
  disappear; every consumer (`check_edit_safe`, `get_changed_symbols`,
  `get_pr_risk_profile`, `get_symbol_provenance`) reports the commit the snapshot
  was taken at (`as_of`) and a tri-state `current` against HEAD. No data is
  disclosed as no data; a zero appears only where the checker ran and found
  nothing. Produce the file in CI or a pre-commit hook and ingest it there.

All three keep the read-only charter intact: the strong evidence enters through a file
you hand the tool, produced by systems you already run, on your terms.

Per-language recipes, the CI ordering that avoids a high `unmapped` count, and
what the `stale` flag actually compares are in [SCIP.md](SCIP.md).

---

*Corrections welcome — if a claim in this manual doesn't match the source, that's a
bug in the manual and we'd like the issue.*
