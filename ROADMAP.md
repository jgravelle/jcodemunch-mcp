# jCodeMunch Roadmap

Accepted design work that is **sequenced but not started**.

## Why this file exists

An issue is a problem to fix or a feature to build. Something we've agreed to
build *eventually*, with no start date and an unmet dependency, is neither — it
is a plan. Leaving plans open as issues makes the tracker a to-do list, and a
tracker that mixes "someone is blocked on this" with "we like this idea" tells
you nothing at a glance about either.

So: **an issue opens when work starts or when a user is blocked. Accepted but
unscheduled design lives here.**

Nothing on this page is rejected. Everything here has been reviewed, agreed to,
and given a close condition. When work begins, the entry gets an issue and this
page links to it.

The evidence-arc design (Phases 2, 4, 5 and 6 below) is
[@mightydanp](https://github.com/mightydanp)'s, proposed in
[this comment](https://github.com/jgravelle/jcodemunch-mcp/issues/377#issuecomment-5076253159)
on [#377](https://github.com/jgravelle/jcodemunch-mcp/issues/377) and accepted
as written. Entries outside that arc carry their own provenance line.

---

## The evidence arc

The through-line across all of it: **a tool that answers confidently regardless
of how little it holds is the expensive failure**, because "I never learned that
file" and "that file does not exist" look identical to every agent downstream.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Claim-scoped evidence (`claims` + per-claim `evidence_refs`) | **Shipped** — jcm 1.108.165, jdoc 1.116.0, jdata 1.25.0 |
| 2 | Exact immutable evidence receipts + producer registration | **P1/P2 shipped** (1.108.183); two P3 edges shipped (1.108.192). **P3 remainder below.** Not started |
| 3 | Absence evidence + subject state | **Shipped** — 1.108.178-.181 |
| 4 | Requirement matching | **Below.** Not started |
| 5 | Corpus / source-universe identity | **Below.** Not started |
| 6 | Path-first program understanding | **Below.** Not started |

---

## Phase 2 P3 remainder — evidence lifetime

Two of the four P3 edges shipped in **1.108.192**: the absence record is
deep-copied into the receipt at mint time (so a later scan on the same
`absent:<sha12>` key cannot contaminate or rescue it), and validation hands its
resolved envelopes to rendering (so one resolution feeds validate, render, hash
and persist). Both verified by @mightydanp against the shipped code.

What is left is the **expiry taxonomy** — a different axis from "a different
scan must not contaminate this one", and deliberately not answered by the
frozen record:

- **Successful retrieval should tombstone contradictory negative evidence**
  (design item 18). `note_absence` returns early on an `ok` state today, so a
  scan identity that recorded `absent` and now returns a match leaves the old
  record in place. Partly mitigated, not closed: a replayed cached `absent` is
  downgraded to `degraded` with its evidence token stripped
  (`subject_state.revalidate_verdict`), and a receipt stands on its own frozen
  record rather than a live lookup. The gap is the store itself, which still
  holds a record the world has contradicted.
- **Expiry and collision taxonomy** (design item 19). `lookup()` already
  separates `never_recorded` / `evicted` / `collision`. Still undistinguished:
  **wrong session**, **wrong repository or dataset**, and **expired**.
- **Session / snapshot identity and invalidation** — whether a receipt should
  expire because the tree moved on after it was minted.

**Close condition.** Design items 17-19 in full, minus the two halves shipped in
1.108.192. A caller holding a stale token must be told which of the five reasons
applies, and a negative record contradicted by a later successful scan of the
same canonical snapshot and effective search identity must not remain citable.

**Sequencing.** Nobody is blocked on this today: the two adversarial edges that
could produce a wrong attestation are closed, and the remaining cases fail
toward refusal rather than toward borrowed proof.

---

## Phase 4 — requirement matching

Caller-declared `requirements` and `coverage_requirements`: a handoff states up
front what it needed to cover, and finalization reports coverage against that
declaration rather than against whatever happened to be retrieved.

Accepted at design time, deferred with Phase 2 P2. Never tracked as work in
progress.

**Result vocabulary.** Per requirement, finalization must report exactly one of
five states, proposed by @mightydanp on #377 (comment 5124590663) and adopted
verbatim:

```text
measured and satisfied
measured and unsatisfied
not measured
unsupported at that precision
failed while measuring
```

The last three are what make this worth having as a vocabulary rather than a
boolean. `not measured` stops a declared requirement from being read as a
negative result. `unsupported at that precision` is the honest answer #339
needed, where the fix was to fail closed rather than imply a per-file precision
the tool did not have. `failed while measuring` separates a delegate that errored
from a signal that came back empty, which is the distinction
jgravelle/jdocmunch-mcp#69 was missing when unmeasured signals scored as zero.

This is a vocabulary we have already had to invent three times in narrower
places, which is the argument for building it once: `FreshnessProbe.
repo_freshness` became four-state in 1.108.180 because a boolean had nowhere to
put "I could not find out", `coverage.complete` is tri-state with a null, and
`retrieval/ledger_trust.py` puts an unclassifiable telemetry row in a third
bucket rather than folding it into the negative group.

**Aggregates must not flatten their children.** Refined by @mightydanp on #377
(comment 5134935796). The five states describe a single measurement; an
aggregate requirement is where they get lost, because a conclusive top-level
answer reads as complete whether or not every child was actually evaluated.

An aggregate retains a record per mandatory child:

```text
child requirement or signal id
requested scope and precision
actual scope and precision
result state
evidence refs
producer identity
failure or unsupported reason
```

Conclusiveness is asymmetric between the two combinators, and the asymmetry is
the operative detail:

- `all` is satisfied only when every mandatory child is measured and satisfied.
- `all` may be conclusively unsatisfied on a single measured failure, but the
  children that failed or were unsupported still have to be disclosed. A
  conclusive verdict is not a licence to drop the rest of the report.
- `any` may be conclusively satisfied on one measured child, and failures among
  the other attempted children still remain visible.
- When failed, unsupported, or absent children prevent a conclusive result, the
  aggregate reports the applicable non-measured state rather than guessing.

The aggregate result never replaces the child states. Otherwise a conclusive
top-level answer can still hide that part of the requested audit was unsupported
or failed, which is the same false-completeness hazard as a zero standing in for
an unmeasured signal, one level up.

**Close condition.** As accepted in the original design comment, plus: every
requirement in a finalized handoff resolves to exactly one of the five states
above, and no state is reachable by defaulting. A requirement that was never
evaluated reports `not measured` and must not render as unsatisfied. For
aggregates: every mandatory child keeps its own record and its own state, and no
aggregate verdict is reported that its retained child states do not support.

> A measured result requires affirmative proof that the measurement occurred.
> Missing data describes the measurement process, not the subject being measured.
>
> -- @mightydanp, #377

**Provenance binding.** Recorded as a stated direction, not scheduled: running
producer version, runtime and session identity, index schema version, index
generation, and producer capability fingerprint, bound into corpus and proof
identity. The adjacent case that motivated it (a future-version index imitating
absence) did not reproduce and is now pinned by
`tests/test_future_version_no_false_absence.py`, so this is about making
provenance legible rather than closing a known defect.

**Sequencing.** After the Phase 2 P3 remainder. Requirement coverage that cites
evidence with an unsettled lifetime inherits the unsettled lifetime.

---

## Phase 5 — corpus and source-universe identity

> A complete scan of an incomplete or misunderstood corpus is not complete
> evidence.

Phase 2 can identify an exact evidence object. Phase 5 identifies the *corpus*
and the producer capabilities behind it, so a receipt can say what universe it
was complete with respect to.

**Scope**

- **5A, common corpus manifest.** Content-addressed corpus identity that changes
  when eligible inputs, parser/profile capability, or generated/dependency
  inputs change.
- **5B, jCodeMunch source universe.** Source roots, modules, source sets,
  variants, generated roots from the resolved build model; dependency source
  provenance; per-file parse outcomes (a parser failure must not read as a
  successfully searched empty file); parser capability fingerprint; atomic index
  generations; per-repository watcher health.
- **5C, jDocMunch document universe.** Formats, conversion failures,
  content-load failures, embedding coverage, repository-group member state.
- **5D, jDataMunch dataset universe.** Row-walk coverage separated from
  column-profile, distinct-value, top-value, sample and embedding coverage.
- Proof kinds, so a producer may mint only the kind its operation actually
  supports.

**Close condition.** Every receipt names one corpus identity; corpus identity
changes when eligible inputs or producer capabilities change; failed inputs stay
visible; generated and dependency domains are explicit; document conversion and
embedding coverage are represented; data row, profile, sample and value coverage
are separate; a failed or cancelled generation cannot support absence.

**Sequencing.** After the remaining Phase 2 work above — the receipt schema
needs the extension points Phase 5 defines, and building them in the other order
means designing the extension points twice.

**Extension point already in place.** `evidence/receipts.py` carries
`coverage_fingerprint()` as the deliberate, opaque Phase 5 hook (1.108.183).

---

## Phase 6 — path-first program understanding with typed flow witnesses

We expose an import graph, a call hierarchy, framework flow edges, signal
chains, blast radius, related symbols, logical communities, compiler references
and runtime activity. Every one is a separate view. The missing abstraction is:

> an ordered, typed, evidence-backed path from an origin to an effect

A codebase is not understood because relevant symbols were found. It is
understood when the system can show where behavior begins, which ordered and
typed transitions connect its declarations, what governs them, what data and
state move through them, where paths branch, merge, cycle or become ambiguous,
which boundaries they cross, which steps are exact versus heuristic versus
unresolved versus runtime-observed, and what missing evidence stops the path
from being complete.

**Scope.** Canonical program nodes and typed edges with resolution and
provenance; ordered path witnesses whose confidence cannot exceed their weakest
required edge; signal chains that keep alternative paths separate instead of
flattening them into a node set; exact-identity path membership with ambiguity
preserved rather than silently resolved; multiple entrypoints to one handler
kept distinct; not-reached separated from unreachable; lifecycle order separated
from reachability; bounded control-flow conditions; data and state flow; runtime
adjacency preserved through ingestion; path-aware context packing that never
drops a bridge node; cross-repository contract nodes; and an immutable
`munch://path/<id>` resource a claim can cite.

jCodeMunch-led. Sibling equivalents considered later where the domain has real
path semantics.

**Close condition.** The acceptance list in the source comment, in full. **No
Cypher, SPARQL, GraphQL or external graph database** — a bounded path API is the
deliverable.

**Sequencing.** Depends on Phase 2 (exact evidence) and Phase 5 (corpus
identity). A path witness that cannot say which corpus it was traced over, or
cannot cite an exact evidence object per step, is not worth citing.

---

## Retrieval benchmark integrity — leakage split and size buckets

Not part of the evidence arc above. Proposed and accepted by the maintainers,
2026-07-29.

**The problem.** A retrieval score measured over queries that contain their own
answer's name is partly a measure of name matching, not of retrieval. We ship
exact seeding — `retrieval/query_shape.py` pins exact symbol-name matches ahead
of ranked matches in `get_ranked_context` — so a query corpus of that shape
flatters the feature by construction.

Our own authored fixture is the extreme case and is deliberately so:
`benchmarks/calibration/planted_queries.json` records that planted names are
slug-unique "so hit/miss is unambiguous and immune to corpus drift." That is
correct for what it measures — whether the verdict reports found when the
subject was found — and wrong for anything that reads it as retrieval quality.
Nothing in the repo says so where a reader would need to see it.

`benchmarks/goldset/gold.json` is **not** affected and needs no change. Its
targets are symbol identities rather than natural-language queries, and its
authored false-positive traps (module homonyms, same-name-different-domain
methods, substring decorator matches) already do the equivalent job.

**Scope**

- A deterministic leakage criterion over a query corpus: a task leaks when the
  tokenized query shares a stemmed token with the tokenized basenames or symbol
  names of its expected results.
- A `split` field per task (`easy` default, `hard`) and a corpus validator that
  exits non-zero when a `hard` task leaks, so CI can gate it.
- hit@k and MRR reported per split **and** per repository-size bucket, beside
  the overall figure rather than instead of it.
- A stated-limit line wherever a leakage-free number is published, naming n per
  split.
- An explicit note on `planted_queries.json` that it measures verdict coverage
  and is not a retrieval score.

**Close condition.** No retrieval number is published from this repository
without its split and its size bucket attached.

**Sequencing.** Bundled with the neutral third-party retrieval benchmark run.
Split machinery built before there is a corpus to split is a validator with
nothing to validate; the benchmark run without it produces a number that has to
be re-qualified afterwards. Size buckets are expected to cost us — large-repo
retrieval is our weakest measured cell, and the bucket that exposes it is the
one we would most like to leave out. That is the reason to build it into the
harness rather than decide per publication.

---

## Competitor head-to-head — GATED on a VM, and deliberately not scheduled

Not part of the evidence arc above. Assessed and gated 2026-08-03.

**The ask.** Measure jCodeMunch against a named same-lane MCP retrieval server,
both sides live, on one corpus in one run — the standard this project already
holds itself to everywhere else.

**Why it is gated rather than queued.** Every nearest-lane candidate requires
running third-party code on the measuring machine, and none of them is
containable by a virtualenv. A venv isolates Python *packages*; it does not
isolate `pip install`-time code execution (`setup.py` runs as the invoking
user, before the venv boundary means anything), native binaries written outside
`site-packages`, background daemons, listening ports, or self-updaters:

| Candidate | What escapes a venv |
|---|---|
| LSP-backed same-lane leader | spawns language servers across 40+ languages (node/go toolchains) |
| Graph-based reviewer | ships a background multi-repo watcher daemon |
| Self-updating indexer | self-updating install — the undisclosed-persistence class that caused our own PyPI quarantine |
| Single-binary graph store | native binary plus a visualiser bound to a localhost port |

⚠ **This is not squeamishness about competitors' code.** It is the same rule we
ask users to trust us on: no undisclosed background or network behaviour on a
machine that did not ask for it. Running a daemon on the maintainer host to win
an argument would be the one standard we cannot afford to apply asymmetrically.

**What already exists, and why the gap is smaller than "nobody has run one"
suggests.** Two selective-retrieval comparators ship today, both pure-pip and
venv-safe, both measuring the other side live in the same run on the same
corpus: `benchmarks/harness/run_rag_baseline.py` (LangChain + FAISS + MiniLM)
and `run_odysseus_compare.py` (embedding retrieval layer). Their published
numbers include rows where jCodeMunch **loses** — see `METHODOLOGY.md`. The
substantive question ("how does this fare against something that already
retrieves selectively?") is answered; what is missing is a brand name attached
to the answer.

⚠ **And a brand name is the one part we could not publish anyway.** Competitor
names stay out of shipped artifacts by standing policy, `versus.php` excepted.
So the deliverable is an internal number bought with a VM build and a daemon.

**Close condition.** Run it when *both* hold: (1) a disposable VM or container
exists that is not the maintainer host and not the release machine, and (2)
there is a specific claim the existing two comparators cannot settle. Absent
(2), a third comparator measures the same class again with more setup.

⚠ **Do not re-open this as "we have never benchmarked a competitor."** That
framing is false — it is a declined trade with recorded reasoning, not an
untouched gap. Anyone reversing it should say which of the two close conditions
changed.

## `install-pack --from`: install an index your own CI built

`install-pack` fetches the pack catalog and pre-built indexes from one hardcoded
host (`STARTER_PACK_API`, `cli/install_pack.py:14`). The mechanism is general;
only the destination is fixed. A team that indexes its own private repo in CI has
no supported way to hand that artifact to the rest of the team, so every seat
re-indexes the same code independently.

That duplication is not only CPU. With `use_ai_summaries` on, summary generation
spends provider tokens at index time, and N seats indexing one repo pay for the
same summaries N times. A pack built once with summaries already in it removes
that cost for everyone downstream of the build.

**Scope.** A `--from <url>` (and matching catalog override) pointing at a
catalog the customer hosts, with auth for a private endpoint, reusing the
existing archive layout and extraction path unchanged.

⚠ **Known blocker, now measured rather than read off the source: symbol bodies do
not travel in the `.db`.** `get_symbol_content` seeks `byte_offset` into a file
under a separate content directory and returns `None` when that file is absent
(`storage/sqlite_store.py`), and `build_pack.py` packages `.db` files only. So a
pack delivers search, outlines and signatures, while `get_symbol_source` comes
back empty unless the content cache ships too. Any design here settles that first,
because it changes the artifact's size profile.

Probed 2026-07-30 by installing the free `nodejs` pack into an empty store with
none of the packed repos checked out anywhere on the box. Bodies returned for
**0 of 50** symbols; `get_file_content` returned `None`; no content directory was
written. Size context recorded at the time: that pack is 10.6 MB of `.db` against
a Node checkout in the hundreds of MB, so carrying bodies looked like a different
product rather than a larger zip.

⚠⚠ **That conclusion was WRONG and is superseded by measurement (2026-08-01, see
"What the artifact actually weighs" below). It was wrong for a structural reason
worth keeping:** it compared the `.db` against the SOURCE, when the number that
decides the question is the content cache against **the `.db` we already ship**.
Measured, the cache adds about **1.5x to the shipped zip**. A larger zip, not a
different product. ⚠ **The general lesson, since this cost a design conclusion:
size the increment against the thing it joins, not against the thing it replaces.**

✅ **The silent half of this is FIXED in v1.108.204** and shipped on its own, ahead
of any `--from` design. The pack path used to return the symbol's name, line,
signature, docstring and `content_hash` alongside `"source": ""` under
`_freshness: "fresh"` and `_meta.verdict: {"state": "ok", "note": "Confident
matches returned."}`. A resolved symbol whose body cannot be read now carries
`source_status: "content_cache_missing"` and degrades the verdict, in
`get_symbol_source` and `get_context_bundle` alike.

**What remains open here is the artifact question, not the reporting one:**
whether a pack should carry the content cache at all. The tool now says what it
cannot produce; it still cannot produce it.

### What the artifact actually weighs

Measured 2026-08-01, because the paragraph above had been reasoning from an
unmeasured size profile since 2026-07-30. Method: fresh shallow clone, indexed
locally, all three sizes taken from the same tree in the same run. Both sides of
a ratio have to be measured together or the stale side drifts unaudited, which is
the same failure `benchmarks/` hit for four months (see Maintenance Practices #4
in `CLAUDE.md`). ⚠ **The pre-existing local indexes could not carry the SOURCE
side of this**: truncated at the 2,000-file `max_folder_files` cap, with stale or
absent `source_root`s, so any source ratio taken from them would have been a
fresh number over a stale one. They remain valid for the **cache-against-`.db`**
ratio only, because both of those come from the same artifact and truncation
drops files from each together; that is the one number they are quoted for below.

Zipped, which is what a pack actually ships:

| repo | `.db` zip | + content cache | source zip (no `.git`) | cache multiple | source / artifact |
|---|---|---|---|---|---|
| fastapi | 1.91 MB | 2.91 MB | 19.46 MB | **1.52x** | 6.69 |
| flask | 0.39 MB | 0.57 MB | 0.84 MB | **1.47x** | 1.48 |
| gin | 0.32 MB | 0.50 MB | 0.24 MB | **1.56x** | **0.47** |

**The number that decides the design is the zipped one, ~1.5x**, because a pack
ships zipped. Uncompressed the same three repos run 1.28x-1.34x; compression
favours the `.db` over the cache, which is why the shipped figure is the higher
of the two. ⚠ **Quote them separately - a single blended "about 1.5x" hides a
real 0.2x spread between the two methods.**

Corroboration, cache-against-`.db` only, from five older truncated indexes:
react 1.6x, django 1.4x, sqlalchemy 1.6x, langchain 1.6x, celery 1.5x
(uncompressed). So eight repos across Python, Go and TypeScript, none outside
1.28x-1.6x by either method. **Carrying bodies is a bounded multiple of an
artifact we already ship, not a new order of magnitude.** That answers the
artifact question in the affirmative on size grounds. ⚠ It does NOT answer the
licensing or trust questions, which are independent and unaddressed.

⚠⚠ **Unasked-for finding, and the one to carry forward: the artifact is NOT
reliably smaller than the source it indexes.** Gin's zipped artifact is **2.1x
LARGER** than gin's own zipped source; flask is only 1.5x smaller. Only fastapi
posts a big ratio, and that is composition rather than compression - roughly 20
of its 35 MB is docs and translations, and only 1,191 of 3,137 files were indexed
at all. **The "Nx smaller than the source" framing holds only for repos whose
bulk is non-code**, which every hand-picked pack so far happens to be. It does
not survive a code-dense repo, and gin is one of our own benchmark repos. ⚠ Do
not attach a size-savings claim to a pack for a repo we did not pick without
measuring that repo first; the Console's badge reads its ratio from the catalog's
`$PACK_SOURCE_MB` and cannot tell the difference.

⚠ **Stated limits of this measurement, so it is not over-read:** one shallow
clone each, single run, no repeats, NTFS. Fastapi's index covers a third of its
files. Reproduce with a clone + index + `du` on the `.db` and its sibling content
directory; there is no committed harness for this yet, and the numbers above are
not wired to `benchmarks/jcm_reference.json`. ⚠ **They are therefore hand-typed
figures, which is exactly what Maintenance Practices #4 forbids for anything
published outward.** They are fine as an internal design input; if any of them is
ever to appear in a README, on the site, or in a pack badge, it needs a harness
that writes them first.

### Validating an index you did not build

Raised 2026-08-01. The section above assumes the installing seat trusts the
builder, because in the `--from` case it *is* the builder: its own CI, its own
host. The interesting widening is the case where it is not, two unrelated users
who both depend on the same public third-party repo and would each otherwise
index it. ⚠ **That case dodges both findings that killed the enterprise broker**
(see `project_jcm_enterprise`): a shared dependency is a tree neither party
edits, so the freshness objection does not apply, and a public repo has nothing
to ACL. It is the Starter Pack shape with the catalog opened up, not a new
mechanism.

The question that gates it is whether a recipient can validate a received index
more cheaply than rebuilding it. **The answer splits in two, and only one half
is cheap.**

**The content cache can be attested cheaply, and the digest width is already
sufficient.** ⚠ **Checked, because the first pass through this got it wrong:**
`compute_content_hash` (`parser/symbols.py:80`) returns a full 64-character
SHA-256 over the symbol's slice, pinned by `test_hardening.py:356`. The truncated
12-hex helper is `config.py:_content_hash`, which caches project-config files and
has nothing to do with symbols. **No widening is required.**

What is missing is not width but an **external referent**. The digest is
self-referential: `verify_against="cache"` says so in its own docstring, and the
externally attested `git_sha` mode (`tools/get_symbol.py:109`) needs
`source_root`, precisely the checkout a recipient does not have. The construction
that works is to record the **git blob OID per file plus the built-from commit**,
so the recipient hashes the shipped cache locally and compares against tree
objects the forge publishes independently. No parse, no clone of file bodies, and
the comparison target stops coming from the uploader. ⚠ The manifest already
records a built-from commit for attribution (v1.108.205); this needs it per file
and for a different purpose, so do not assume the existing field covers it.

**Derived rows cannot be attested by any hash, only re-derived.** Symbols and
summaries are parse output; a digest over them proves internal consistency and
nothing else. The affordable form is a **spot-check re-parse** of a random sample
of files, which works only if parsing is deterministic for a fixed grammar set
and `INDEX_VERSION`, and that is an assumption with no test behind it today. Cost
then scales with the sample rather than the repo. ⚠⚠ **AI summaries are outside
this entirely.** They are non-deterministic, so unre-derivable, so unverifiable
by sampling, and they are also the one genuinely duplicated *token* cost the
paragraphs above give as the reason to share at all. A shared artifact probably
has to be heuristic-summaries-only and say so, or the field carries builder trust
that nothing else in the artifact does.

⚠ **State the guarantee accurately or it will be oversold.** Validation reduces
trust-in-uploader to trust-in-repo. It does not make a shared index safe: an
honest index of hostile code is still hostile, and it reaches the recipient's
agent as authoritative retrieval. Trust-in-repo is the right bar because it is
the same exposure as reading the repo, but it is not the same claim as "verified".

**One finding here is a live cost rather than a design, and it stands on its own
whether or not any of the above is ever built.** `tools/get_symbol.py:346` puts
`content_hash` in every `get_symbol_source` response entry unconditionally,
alongside `signature` and `source`, **not gated on `verify`**. A 64-hex digest is
about 83 characters per symbol returned, so a 20-symbol batch spends on the order
of 400 tokens on a field the caller was not offered a use for: `verify` already
answers the drift question as a boolean (`content_verified`), `get_changed_symbols`
answers it across a diff, and receipts carry `content_sha256` out of band with
only the id on the wire (`evidence/producers.py:483`). ⚠ **This is the same cost
in every response today; it is not created by anything in this section.**

⚠ **Gating it is a response-shape change on a shipped tool and wants an explicit
decision against the 1.x no-removal contract, so it is recorded here rather than
done.** Checked, in case it made the call easy: **no test asserts the field in a
tool response.** Every assertion found is storage-layer or parser-layer
(`test_hardening.py`, `test_call_references_model.py`, `test_css.py`,
`test_json.py`) and none is affected either way.

⚠ Adding blob OIDs is an `INDEX_VERSION` bump, which re-indexes every user and
re-downloads every pack. Do not land it alone; bank it against the next bump.

**Close condition for this sub-item.** A recipient can take an index built
elsewhere and establish, without a full re-index, that its content cache matches
a named upstream commit and that a sampled fraction of its symbol rows re-derive
from that content. Until the sampling assumption has a determinism test behind
it, the honest state is that only the content half is checkable.

**Non-goal.** Nothing here is approved work, and it does not become approved by
being written down. See the note under `#385`/`#386` in `CLAUDE.md`: parked
design with a close condition belongs in this file and not on the tracker.

**Close condition.** A seat installs an index built by a CI job it controls, from
a host it controls, and every tool that works against a locally built index works
against the installed one. Where that is not true (see the content-cache blocker),
the gap is reported by the tool rather than surfacing as an empty result.

**Provenance.** Fell out of the jCodeMunch Enterprise review (2026-07-30), which
concluded that no token-level saving requires a running shared component: every
win available is a build-time artifact property. This is the artifact channel
pointed at a private repo instead of our public catalog. Independently useful to a
solo developer with two machines, which is why it belongs to jcm rather than to
any enterprise layer.

---

## Catalog moratorium — IN FORCE from v1.108.218

No new top-level catalog actions until the exit conditions below are met. Pinned
and enforced by `tests/test_catalog_moratorium.py`; the contributor-facing
statement is in `CONTRIBUTING.md`.

**Why.** 91 actions, and `route` proposes the right one for a plain-language
task 45.8% of the time at rank 1. An action `route` never proposes is
functionally absent while still costing a schema, a 1.x compatibility promise,
an output contract and a test matrix. #397 is the sharp end: generated
`CLAUDE.md` named 25 tools against a server exposing 6.

⚠⚠ **THE MODEL VENDOR NOW PUTS A NUMBER ON THE CATALOG-SIZE COST, AND WE ARE
2-3x PAST IT** (recorded 2026-08-24 from Anthropic's [tool search
docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)):

> "Claude's ability to pick the right tool degrades once you exceed **30-50
> available tools**."

`jcodemunch-mcp surface` on `full` reports **91 visible of 94**; the canonical
`benchmarks/schema_baseline.json` puts that payload at **22,741 tokens**
(`full_full`) against **939** for `counter`. Their "when to use tool search"
list — 10+ tools, definitions over 10k tokens, a library that grows — describes
this server exactly.

⚠⚠ **THIS IS EVIDENCE FOR THE FREEZE, NOT AGAINST IT, AND IT IS THE FIRST
EXTERNAL NUMBER WE HAVE.** Every argument to exit has so far been about whether
`route` is good enough. This says the catalog is already past the size at which
the MODEL's own selection degrades, independent of `route`. **A 92nd action
makes the number worse on an axis the exit conditions do not measure at all.**

⚠⚠ **AND IT DOES NOT TOUCH THE EXIT CONDITIONS — do not cite it as progress.**
The headline "over 85 percent" reduction is a TOKEN figure over a five-server
MCP setup. Our wall is that `route` scores **52.2% vs a 51.4% majority
baseline** on agent wording, i.e. chance. Anyone quoting the 85% at this block
is answering an accuracy question with a token number — the same category
error as quoting 71.2% at the emitted distribution.

⚠⚠ **CORRECTION, 2026-08-24, SAME DAY AS THE ENTRY ABOVE: this block first said
their accuracy claim "carries no number". THAT WAS WRONG** — true of the tool
search DOCS page, false of the [Advanced tool
use](https://www.anthropic.com/engineering/advanced-tool-use) post it links,
which reports tool-selection accuracy **49% -> 74% on Opus 4** and **79.5% ->
88.1% on Opus 4.5** with tool search enabled, on "MCP evaluations". **Never
argue from an absence of evidence you did not go looking for.**

⚠⚠ **The verdict is unchanged and the REASON is now better.** It is not that
they published no number; it is that **their number measures a different
quantity**. Theirs is the MODEL's selection accuracy with a retrieval layer
available versus all tools loaded — i.e. it prices the Counter's premise, and
prices it favourably. Ours is `route`'s OWN rank-1 accuracy on agent-emitted
wording, measured directly, and it sits at chance. **A vendor number showing
that retrieval helps in general cannot substitute for our own measurement
showing that OUR router does not.** Their corpus is unpublished, so it is not
an instrument we can run either.

⚠ **Read it as raising the value of the Counter, not as clearing `route`.**
Those are separate claims and only the second is what conditions 1-3 gate.

⚠ **Their 85% and our 95.9% have the SAME epistemic status**: each characterises
one configuration, neither is a benchmark. Do not present theirs as validating
ours, and do not let ours be read as competing with theirs.

⚠ **The surface default is split and the distinction matters when quoting 91.**
`init` writes `tool_surface: "counter"` on a genuinely first-ever install (3
tools). The config template and the env fallback both resolve to `full`, so an
upgraded install, or one with no config, serves all 91. **The 30-50 finding
therefore applies to the carried-forward population, not to new installs** —
which is an argument about the default, tracked separately from this freeze.

**Exit conditions, named before the work** (same discipline as Arc 4's
thresholds — neither side picks the bar after seeing results):

⚠ **Kept as written in 2026-08; the enforced condition 1 is not this text**
(#715). `tests/test_catalog_moratorium.py` gates route@1 on the held-out
CONTROL subset of `holdout.json` against `route.control_at1` in
`harness/thresholds.json`, and the leakage ceiling is its
`EXIT_MAX_NAME_LEAKAGE`. The numbers below are the record, not the gate.

1. `route@1` >= **60%** on `benchmarks/route_recall/queries.json`;
2. mean name leakage <= **0.15** at that same measurement;
3. generated guidance references only actions callable under the active surface.

⚠⚠ **Condition 2 is not decoration.** A recall bar with no leakage bar is met
by writing queries that paraphrase tool descriptions — the exact failure
v1.108.218's target audit was run to avoid. Both move together or neither
counts.

⚠⚠ **CONDITIONS 1 AND 2 BOTH PASS TODAY AND THAT IS NOT CLEARANCE. READ THIS
BEFORE CONCLUDING THE FREEZE CAN LIFT.** Measured 2026-08-21 on `main`:
`route@1` = **71.2%** against the 60% bar, mean name leakage = **0.133** against
the 0.15 ceiling. Both conditions as written are satisfied, and have been since
v1.108.253.

⚠ **The figure was 69.5% here for two weeks and that was a STALE ARTIFACT, not a
measurement.** `results.json` had drifted from the code — descriptions moved, two
queries changed rank, and nothing re-ran the harness. The verdict is unchanged
(71.2 > 60), which is exactly why it went unnoticed.
`tests/test_route_recall_artifacts_are_fresh.py` now fails when either artifact
disagrees with a fresh run. **Maintenance Practice 4 says never hand-type a
benchmark number; a number read out of a stale artifact is the same defect one
level up.**

**What the conditions did not anticipate is that they name a CORPUS as well as a
bar.** `benchmarks/route_recall/queries.json` is human-phrased — the words a
person types. `route` does not receive those words. It receives the `task`
string an AGENT emits, and that is a different distribution.

**Measured on the emitted distribution** (`emitted_task_results.json`, run
2026-08-07, 40 cases sampled seed 421 from the non-pilot rows of
`rknighton/jcm-route-benchmark-corpus` v0.1.0, MIT-0, so it does not overlap the
corpus author's own pilot):

| metric | `route@1` | blind floor | vs floor |
| --- | ---: | ---: | ---: |
| strict | 30.0% | 45.0% | **-15.0** |
| exact | 55.0% | 80.0% | **-25.0** |
| family | 60.0% | 95.0% | **-35.0** |

**A result at or below its own floor is not routing** — a constant answer beats
it. ⚠ **The floors differ sharply per metric, so no number here may be quoted
against another metric's bar**, and none of them may be compared to the 60% in
condition 1, which is a different corpus and a different measurement.

⚠ **v1.108.253 answered the `@3` half and said so.** A content-search rule above
the broad `find` trigger, plus that trigger now offering `search_text` as an
appended alternate. On a held-out 20 the fix was never developed against,
strict@3 went **20% -> 80% against a 70% floor**, and single-recommendation
returns went 12 of 20 to zero. Human corpora did not regress. ⚠⚠ **`@1` did NOT
improve and remains far below floor**, deliberately: the `search_text` /
`search_symbols` split sits inside the labelling uncertainty the corpus author
flagged himself, and reordering to chase it fits the sample rather than the
intent.

⚠⚠ **DECIDED 2026-08-20 BY JJG: THE THREE CONDITIONS STAND AS WRITTEN. NO
FOURTH CONDITION.** A fourth for emitted-task `strict@1` was considered and
REFUSED, and the reason is the sentence four paragraphs above: **neither side
picks the bar after seeing results.** The case for adding one was real — the
discovery was the wrong POPULATION rather than a disappointing number — and it
was still refused, because "the measurement turned out to be of the wrong thing"
is exactly what every post-hoc bar change sounds like from the inside. **A gate
that can be amended once it is inconvenient is not a gate.**

⚠⚠ **THE CONDITIONS ARE THEREFORE NECESSARY AND NOT SUFFICIENT, AND THIS BLOCK
IS THE DISCLOSURE THAT MUST TRAVEL WITH THEM.** Quote it wherever they are
cited. Meeting all three permits the freeze to lift; it does not establish that
`route` selects well on the traffic it actually serves, and the table above is
the evidence that it does not. **Anyone proposing to exit owes an argument about
the emitted distribution, not a citation of 71.2%.**

⚠⚠ **AND @3 IS NOT THE ESCAPE HATCH. Corrected 2026-08-21.** The emitted harness
compared route's THREE guesses against a floor allowed ONE, and a comment in the
script argued that this was the fair comparison. It is not, and it was wrong in
route's favour. **A baseline gets as many guesses as the system it is the floor
for.** Against the best constant 3-SET:

| emitted, n=40 | route | best constant 3-set | delta |
|---|---|---|---|
| strict@3 | 62.5% | **92.5%** | **-30.0** |
| exact@3 | 70.0% | **100%** | **-30.0** |
| family@3 | 70.0% | **100%** | **-30.0** |

`strict@3` moves from **+17.5** against the 1-set floor to **-30.0** against the
k-matched one. **@3 does not rescue the emitted result; it deepens it.** Both
floors are now emitted by the harness (`blind_floor_kset`, `vs_kset_floor_pts`)
so neither can be re-derived by hand.

⚠⚠ **THE EMITTED CORPUS CANNOT DISCRIMINATE A ROUTER FROM A FIXED LIST, and that
is a property of the sample rather than a verdict on route.** Its best constant
3-set scores **100% exact**. 40 cases, **6** distinct primary labels, **87.5%**
in one family; the holdout is worse at 20 cases and **3** labels. A benchmark a
constant answer saturates has no discriminative range left at that k.

⚠ **The human corpus is a different instrument and route clears it decisively at
BOTH k** — now that it reports a floor at all, which it did not until
2026-08-21: `@1` 71.2% vs a 5.1% 1-set floor (**+66.1**), `@3` 86.4% vs a 13.6%
3-set floor (**+72.8**). 59 queries, **69** distinct targets. **Do not read the
emitted failure as "route does not route"; read it as "route does not route on
agent wording".**

⚠⚠ **THE OBJECTIVE, STATED PRECISELY.** Strip the degeneracy away and the
emitted corpus is one decision: **87.5% of golds are `search_text` or
`search_symbols`, split 18/17.** Majority-class baseline **51.4%**; route, on the
cases where it lands in the pair at all, is **12/23 = 52.2%**. Chance. One rule
— `/find|locate|where is|look up|search for|definition of/ -> search_symbols` —
takes 21 of 35 rank-1 picks because agent tasks open with "find".

**So the question is not `route@1` versus `route@3` over 91 actions. It is
`P(correct | gold in {search_text, search_symbols})`, currently 52.2 against a
51.4 chance line.** Anyone proposing to optimise route should say which of those
two numbers they intend to move.

⚠⚠ **AND MOST OF THAT DECISION DOES NOT NEED MAKING. Measured 2026-08-22, now
emitted by the harness as `pair_availability`: on 25 of the 35 pair-gold cases
(71.4%) route returns BOTH `search_text` and `search_symbols`.** Those are the
same 25 where the gold action appears in the recommendation list at all. The
caller therefore has both candidates in hand, and choosing between two returned
options costs nothing.

**That reframes 52.2%.** It is not "route gets a coin flip wrong". It is "route
declines to break a tie it has already surfaced, and `@1` scores that as
failure" — which is exactly the suspicion this block opened with: **`@1`
penalises a router for being honest about ambiguity.**

⚠⚠ **The residual is a DIFFERENT and LARGER failure.** In the other 10 cases
neither search action was offered at all — route went to the wrong
neighbourhood, not the wrong order:

    gold=search_text     offered=[check_delete_safe, check_edit_safe, check_rename_safe]
    gold=search_symbols  offered=[get_context_bundle, get_session_context, get_ranked_context]
    gold=search_symbols  offered=[tune_weights, announce_model, find_implementations]

**28.6% wrong neighbourhood versus 71.4% right-pair-wrong-order.** A single
"within-family 52.2%" fuses the two and hides which one is worth work; no
tie-break can fix the 28.6%.

⚠ **Availability is computed over the SAME 3 actions the response carries**, not
the untruncated internal ranking — crediting route with options the caller never
saw would be measuring the wrong thing.

**Consequence for H4** (the retrieval-outcome probe, the family's only survivor):
its prize is re-ranking a pair the caller can already see, on 71.4% of the cases
it targets. That is much smaller than the 52.2% figure suggests, and it was
invisible from that figure alone. **The whole motivating gap is 52.2 vs 51.4 on
23 cases.** Weigh a fresh corpus against that before building one; the last one
was cancelled for less.

⚠ **This also reframes why H1 and H2 died.** Both failed on COVERAGE — predicates
firing on 5-15% of queries. But the decision that needs making is not spread
across 91 actions; it is ONE binary that must be answered every time. **A
predicate reaching 15% cannot move a decision required at 100%**, which is why
purity was never the issue.

**H3, untested and named here rather than started:** `search_symbols` matches
symbol NAMES, `search_text` matches file CONTENT, so the discriminating fact is
whether the sought thing IS a symbol name in this repo. That is absent from the
query string — consistent with both refutations — and cheaply knowable from the
index. **Its coverage is 100% by construction**, which is precisely the failure
mode that killed H1 and H2. ⚠ It needs the 157 unused corpus rows sampled for
PAIR BALANCE rather than uniformly (uniform sampling is what produced a corpus a
constant list saturates), and the predicate declared before labels are read, same
protocol as H1/H2.

⚠⚠ **H3 WAS RUN AS A GROUNDED PILOT ON 2026-08-21 AND IS REFUTED.** 60 cases,
balanced 30/30, three repos pinned at the SHAs in `benchmarks/tasks.json`,
predicate registered before any case existed (`benchmarks/route_binary_pilot/`,
and `git log` shows `predicate.py` preceding `cases.json`). Full vocabulary
**53.3%** against a 50% floor, Wilson 95% **[40.9, 65.4]**, **p = 0.699**;
ablating each target's own name parts returns **50.0%, p = 1.000**. Leakage
existed (12 of 30 class-S tasks matched their own name) and bought nothing.

⚠⚠ **The mechanism inverts the whole family. The predicate answered
`search_symbols` on 58 of 60 tasks** — 100% of class S and **28 of 30 of class
T** — because in a real repository the symbol vocabulary absorbs ordinary
English. fastapi: 6,841 symbols to **4,303 matchable name parts**, and 14 of 16
common English words tested are among them (`message`, `path`, `error`,
`status`, `value`, `name`, `body`, `type`, `data`, `request`, ...). A membership
test fires on nearly any sentence.

| hypothesis | fires on | fails because |
|---|---|---|
| H1 identifier shape | ~15% | decides too few cases |
| H2 imperative verb | ~5% | decides too few cases |
| H3 vocabulary probe | **~97%** | decides them all the same way |

⚠⚠ **COVERAGE WAS NEVER THE PROPERTY THAT MATTERED, AND H3 WAS ARGUED FOR ON
EXACTLY THAT GROUND.** 100% coverage was necessary and not sufficient — the same
shape as conditions 1 and 2 being met without clearing the freeze. The property
all three lack is **separation**: a predicate must fire DIFFERENTLY on the two
classes, and firing often is not firing differently. **Any future hypothesis
should be screened on separation before anyone counts its coverage.**

⚠⚠ **THE CORPUS PROJECT IS CANCELLED, which is what the pilot was for.** The
protocol registered the asymmetry in advance: a negative is decisive. Building
cases bound to real repositories with labels assigned by someone who can see them
would have cost a project and hit the same wall, because the wall is not the
corpus — it is that vocabulary membership does not separate these classes in any
repository large enough to matter.

⚠ **Not ruled out, and it must NOT be run on these 60 cases:** a probe keyed on
retrieval OUTCOME rather than vocabulary membership — does `search_symbols`
actually outrank `search_text` for this query against this index? That compares
two scores instead of testing set membership, and index size does not trivially
defeat it. **That is H4; this corpus is spent, and reusing it would be a fitting
pass wearing an experiment's clothes.**

⚠⚠ **H3 IS NOT RUNNABLE ON THIS CORPUS, AND THE BLOCKER KILLS THE WHOLE REMAINING
HYPOTHESIS CLASS. Established 2026-08-21 before starting it.** The joint H1/H2
finding says the information is not in the query string and points at a signal
from OUTSIDE it — the repo, a first-pass retrieval, prior turns. **Every one of
those needs context the corpus does not carry.**

`emitted_task_cases.json` rows are
`{case_id, candidate_rank, prompt_text, gold_primary, gold_alts, emitted_task}`.
**There is no repository field**, and the tasks are heterogeneous by design:
"this project", "our mod", "the capture button handler". Of the 35 pair-labelled
cases, **4 name a resolvable repo.** There is nothing to index, so there is
nothing to probe — and pointing the probe at one shared index would score
accidental matches, not the property.

⚠ The 157 unused rows do NOT fix this. They come from the same generator and are
repo-less for the same reason. **"157 rows remain unused" is an asset only for
hypotheses about the query STRING — which is exactly the class already refuted
twice.** Do not cite the unused rows as readiness for an outside-the-string test.

⚠ The source corpus (`rknighton/jcm-route-benchmark-corpus` v0.1.0, MIT-0,
sha256 pinned in the artifact) is NOT vendored here, so even the string-only
hypotheses need a fetch first.

**What readiness costs, stated so the decision is priced rather than discovered:**
a corpus where each case is bound to a REAL repository at a pinned commit, tasks
generated against that repository, and gold labels assigned by someone who can
see it. That is corpus construction, not an afternoon. Until it exists,
`P(correct | gold in {search_text, search_symbols})` is a well-posed objective
with **no instrument that can measure a repo-grounded answer to it.**

⚠⚠ **This is the more useful half of the H3 work and it came from checking
readiness instead of assuming it.** The previous entry read as "the next
hypothesis is ready to run, 157 rows are waiting". It was not, and any of H3/H4/H5
in the same family would have hit the identical wall after the setup cost.

⚠ **This is deliberately the harder-to-abuse arrangement.** Under (b) the
counter-evidence becomes a number to clear and then forget. Under (a) it stays a
standing disclosure that has to be argued past every time.

⚠⚠ **THE NAMED NEXT PIECE OF WORK WAS TESTED AND REFUTED, 2026-08-20. DO NOT
BUILD IT.** v1.108.253 identified the missing rank-1 discriminator — "most
likely 'does the task name an identifier-shaped token'" — and declined to guess
without fresh data. It was measured instead of built:
`benchmarks/route_recall/measure_route_discriminators.py`, artifact
`route_discriminator_results.json`, over the same corpus digest the emitted-task run
used.

| sample | rule | majority floor | lift | coverage |
| --- | ---: | ---: | ---: | ---: |
| 164 raw prompts | 48.8% | 50.0% | **-1.2 pts** | 15% |
| 35 emitted tasks | 60.0% | 51.4% | +8.6 pts | 14% |

⚠ **The positive row is n=5.** Identifier-shape fires on five emitted tasks and
gets four right. Under the null that is **p = 0.17** — a one-in-six coincidence,
and one case flipping moves it twenty points. **The larger sample is the one to
read, and there the rule is WORSE THAN A CONSTANT ANSWER.**

⚠⚠ **COVERAGE IS THE FINDING, AND IT KILLS THE IDEA EVEN AT PERFECT PURITY.**
The predicate fires on ~15% of family cases either way, so **the 85% residue —
at 51-57% purity, which is the coin flip we started with — is untouched by any
version of this rule.** A discriminator that cannot reach the majority case is
not a discriminator for this problem. ⚠ Per-pattern breakdown is in the artifact
and nothing survives: `snake_case` 3/6, `PascalCase` 7/13, `camelCase` 1/3.
**Seven patterns were tested on one sample, so expect one to look good by
chance; none is a finding.**

⚠ **The predicate is declared in the script ABOVE the point labels are read**,
and that ordering is the only thing separating this from a search for a pattern
that fits. Anyone re-testing a discriminator hypothesis here does the same or the
result means nothing.

⚠⚠ **THE VERB HYPOTHESIS WAS TESTED THE SAME DAY AND ALSO REFUTED.** "Where is
X defined" against "everywhere X appears", predicate declared before labels,
same script and artifact (H2).

| sample | rule | floor | lift | coverage |
| --- | ---: | ---: | ---: | ---: |
| 35 emitted tasks | 48.6% | 51.4% | **-2.9 pts** | 14% |
| 164 raw prompts | 51.2% | 50.0% | +1.2 pts | **5%** |

⚠⚠ **On emitted tasks the sign is BACKWARDS, not merely absent**: the
`definition` bucket is **1 `search_symbols` / 4 `search_text`.** n=5, so
directional rather than proven — but the mechanism is plausible and worth
stating. **A request that names what it wants DESCRIPTIVELY — "the function that
parses config" — gives a symbol-name index nothing to match**, so descriptive
definition-seeking favours `search_text`. The hypothesis assumed the opposite.
⚠ `occurrence` fires **once in 164 prompts**, partly because .253's content rule
already covers that phrasing and partly because people do not talk that way.

⚠⚠ **THE JOINT FINDING IS WORTH MORE THAN EITHER REFUTATION: BOTH FAIL ON
COVERAGE, NOT PURITY.** H1 fires on ~15% of cases, H2 on 5-14%, and in both the
untouched residue sits at ~50% purity. **Two independent properties of the query
TEXT, each absent from 85-95% of real requests.** That is not two unlucky
guesses; it is evidence that **the information needed to route these requests is
not in the query string at all.** A third text-feature hypothesis should expect
the same result. ⚠ What is NOT ruled out is a signal from OUTSIDE the string —
the repository, a first-pass retrieval, or the caller's prior turns.

⚠ **The rows survive both tests.** Each predicate was declared before labels and
run ONCE; nothing was fitted, so the corpus retains its value for the next
hypothesis. **A tuning pass would have spent it.**
⚠⚠ **The likelier reading is that .253 was right on the merits: "find X" is
genuinely undecidable without more signal.** `route` returns 2-3 candidates on 38
of 40 cases BY DESIGN, and `strict@3` is 80% against a 70% floor. **`@1` is the
metric that penalises a router for being honest about ambiguity** — a perfectly
calibrated one that says "it is one of these two" scores zero on it. Decide
whether `@1` is the objective before optimising it.

⚠ **157 corpus rows remain unused** (197 minus the 40 sampled) and the author's
standing offer on #422 holds: *"willing, but do not wait on me... if you or
anyone else wants to run it, take it."* **That data is still there for the next
hypothesis; it has now answered this one.**

⚠ **Progress is measured from 45.8, never from 42.4.** The 3.4-point move
between them was v1.108.218's corpus correction, not routing work. A reader who
anchors to 42.4 will credit routing with a correction it did not make.

**What is withheld, and it is a surface not a capability.** Both exist, are
tested, and are unregistered: `investigate_deletion_safety` (v1.108.214, 19
tests) and the retrieval counterfactuals (v1.108.217). A test asserts both stay
importable, so the policy cannot quietly become deletion.

⚠ **Two of the report's four proposed conditions were DROPPED as unmeasurable,
deliberately rather than silently.** "Every catalog action has a clear usage
rate" needs telemetry that is opt-in and off by default, so it cannot gate
anything today. "Adjacent tools consolidated behind common intents" has no
threshold anyone could fail. A gate nobody can evaluate is a gate that gets
waived on the first argument; better to state three that bind than four that
sound thorough.

**The fastest way out** is route-recall work.
`benchmarks/route_recall/explain_misses.py` prints the live defect list with
each miss labelled by the gate that caused it. ⚠ **RUN IT; do not quote from
here.** The composition has changed twice since this line was written and the
buckets are not stable across releases.

Measured 2026-08-20 on `main`: **11 misses, 3 `rule_preempted` and 8
`ranked_below_cutoff`.** Only the second bucket is reachable by ranking work —
`rule_preempted` means a curated rule claimed the query and the right action was
NEVER SCORED. ⚠ The v1.108.218 line this replaces read 7 `rule_preempted` and 8
`no_lexical_overlap`, and **`no_lexical_overlap` is now empty**, so a reader
working from the old figures would have gone looking for a bucket that no longer
exists.

⚠ Three of the current misses are one query — *"is this name used anywhere at
all or can I drop it"* — claimed by `search_text` while wanting
`check_delete_safe`, `check_references` and `find_references`. ⚠⚠ **The severe
ones are not near-misses:** `get_churn_rate` at **rank 16**,
`get_dependency_graph` at **rank 17**, `register_edit` at **rank 34**. ⚠ And
*"I just edited a file, refresh it"* loses `index_file` at rank 5 to
`get_file_risk` — about as common an intent as this tool has.

---

## Adaptive large-repository data path (#398, @rknighton)

Four accepted arcs from a five-arc proposal. **Arc 5 shipped in v1.108.210** and
is not repeated here. The evidence pack behind all five is SHA-pinned to
`c2201a55`, order-balanced A/B, with canonical response parity; every code
citation in it was verified against our tree before acceptance.

⚠ **Do not quote the reporter's multipliers in any shipped artifact.** Re-measure
on our side first. Arc 5's own numbers moved substantially when we did: he
measured a 2.63%-7.65% fetch fraction on FastAPI and Django, and jcm's own repo
came out at **22.48%** (2,838 of 12,625 vectors). Both are real; they describe
different repository shapes. The saving held, the headline did not transfer.

### Arc 1 — generation-safe read contract

One read snapshot per request, with the code generation distinguished from the
embedding generation, and cache identity including branch, index format version,
embedding model, and vector dimension.

Accepted on **correctness** grounds; the reporter's own estimate is ~1.0x and
that is honest. We currently have three surfaces answering one question:
`subject_state.capture()` publishes `indexed_at` as `generation`,
`evidence/producers.py` re-exposes it as `index_generation`, and
`_db_mtime_ns` is read separately. That is the [[feedback_two_paths_one_decision]]
shape, and v1.108.209 shipped a fix for the same shape one layer down (a per-file
freshness classifier answering `fresh` on five paths where it could not measure,
while the repo-level classifier 40 lines above already had `unknown`).

**Close condition:** the three generation surfaces resolve to one authoritative
committed-revision contract, receipt semantics preserved byte-for-byte, and the
read-only SQLite path sees committed WAL data when sidecars exist without
creating them when they do not.

✅ **SHIPPED v1.108.215.** `storage/generation.py` holds both halves:
`IndexGeneration` / `describe()` for the generation contract, and
`connect_readonly()` for the read contract. Receipts byte-identical.

⚠ **The arc was worth more than its ~1.0x estimate, and the reason is worth
keeping.** The v1.108.185 note said `immutable=1` costs only un-read vectors,
i.e. a weaker ranking. Measured 2026-08-02: against an un-checkpointed WAL an
`immutable=1` reader raises **`no such table`**, and `EmbeddingStore.has_any()`
maps that to `False` — a confident "this repository has no embeddings" about a
repository embedded moments earlier. **Fourteen call sites shared the hazard in
both directions** (ten blind to the WAL, four creating sidecars, including a
`token_tracker` loop that did it to every index in `~/.code-index` at once).
A correctness arc with an honest ~1.0x throughput estimate was carrying a live
false-absence defect; do not price these arcs on the multiplier alone.

### Arc 2 — transactional selective code snapshots

Load repository/file metadata plus only the rows a tool needs, inside one SQLite
read transaction. An explicit storage-owned read view, **not** a silent change to
`load_index`; broad modes promote to the existing complete path.

Best median result in the proposal (2.24x-2.35x). Sequenced after Arc 1 because
its consistency guarantee is part of Arc 1's contract.

⚠⚠ **`immutable=1` MUST NOT be lifted into this path, and the reason is the
argument that justifies it elsewhere.** `get_all_readonly` / `get_many` accept
that un-checkpointed WAL vectors go unread, on the stated grounds that the
similarity channel only ADDS candidates, so a missed vector can weaken a ranking
and cannot manufacture a false absence. Arc 2 is exact row reads. There, an
invisible un-checkpointed row is not a weaker ranking - it is a symbol that
exists and was not returned, which is a false absence of exactly the kind the
evidence work exists to prevent. **The trade-off is channel-specific; it does not
generalize** (@rknighton, 2026-08-01, and this is a sharper statement of the
sequencing than the one we gave).

That makes Arc 1 a genuine prerequisite rather than a tidiness preference: Arc 1's
read contract is the thing that has to satisfy both sides at once - see committed
WAL data when sidecars already exist, create nothing when they do not - and only
that contract lets this path read exactly.

✅ **That contract now exists**: `storage.generation.connect_readonly` (v1.108.215).
Arc 2's exact-row reads open through it and inherit the guarantee, so the
`immutable=1` prohibition below is enforced by using the shared opener rather
than by remembering not to type the flag - `tests/test_generation_contract.py`
fails on any hand-rolled read-only URI outside `storage/generation.py`.

**Close condition:** supported narrow calls complete without hydrating the full
`CodeIndex`; unsupported and broad modes promote once with no change to errors,
suggestions, ordering, branch behavior, or response fields; **and no read on this
path uses `immutable=1`.**

✅ **SHIPPED v1.108.216.** `storage/selective.py` + `store.open_selective()`;
`get_symbol_source` is the first wired caller, byte-identical on hit, batch and
miss. A non-empty branch returns `None` (take the ordinary path) rather than
reproducing delta composition against a partial row set.

⚠ **The promotion boundary is `__getattr__`, not an allow-list, and that choice
is the arc.** Exact fields are copied onto the instance; everything else falls
through and promotes, **including fields that do not exist yet**. Forgetting to
update the module makes a request slower, never wrong. A test proves it with a
fake future field.

⚠⚠ **Near-miss worth not repeating.** The view uses `__slots__`, and
`_stamp_load_provenance` writes `_db_path`/`_loaded_mtime_ns` inside a bare
`except Exception`. Omitting those two slots would have sent
`subject_state.capture` — which runs on essentially every response — through
`__getattr__`, hydrating the whole corpus to answer a question about a file
mtime, **while every test about symbols still passed**. A silent swallow plus a
promoting fallback is a performance defect that hides behind green tests.

**Measured on our side** (both sides, one interleaved run, cold cache each
iteration), jcm's own index at 12,826 symbols: selective median 7.6 ms vs
hydrate 159.7 ms, **20.96x**. ⚠ **Not quotable as the arc's result** — narrowest
possible call, control corpus. The 2.24x-2.35x figure is a mixed-group median;
Django and FastAPI remain the load-bearing points and neither has been
re-measured here.

**Remaining, not blocking the close:** only `get_symbol_source` is wired.
`get_file_outline` and `get_context_bundle` are the obvious next callers (the
`files=` scope already exists and is tested); each is a separate wiring with its
own parity assertion, not a change to this path.

### Arc 3 — generation-scoped shared promotion — GATED

Promote a broad request's snapshot into one immutable full view keyed by the code
generation, single-flighted across concurrent callers.

⚠ **Gated on disclosure, not on the technique.** Retaining a view across requests
is background behavior, and every new background/persistent/network behavior must
be README-disclosed before shipping (the standing post-quarantine rule). It also
overlaps `JCODEMUNCH_INDEX_CACHE_TTL` (v1.108.172), which is deliberately opt-in
because cold hydration of a 665k-symbol index was measured at 7.5-11.4 minutes
(#370). **Reconcile with that switch; do not add a second retention policy beside
it.**

**Which direction the reconciliation runs — DECIDED 2026-08-02.** The proposal
asked whether Arc 3 sits under the existing switch or makes it unnecessary, on
the argument that a byte budget bounds a leaked process by construction and does
so without the idle-eviction bill that forced the TTL to default off.

Arc 3's budget is the **primary** policy and the TTL does not gate it. But the
byte budget does **not** subsume the TTL, and the reason is worth pinning:

⚠ **A byte budget is per PROCESS; the leak the TTL was built for is a count of
processes.** #375 was 25+ leaked stdio servers holding ~17 GB between them. A
per-process cap bounds how much each one can GROW to; it never reclaims the floor
each one is already sitting on, and 25 x budget is still 25 x budget. The TTL
evicts an idle cache toward zero, which is a different axis, and the only one
that reaches a process nobody is talking to.

So: **one policy, two axes** - byte/entry pressure and generation change (always
on, Arc 3's own), plus idle reclamation (opt-in, the existing
`JCODEMUNCH_INDEX_CACHE_TTL` spelling, retained under the 1.x no-removal
contract). Arc 3 must not introduce a second switch, a second eviction loop, or a
second vocabulary for either axis.

**Close condition:** byte- and entry-bounded retention with safe eviction and a
per-request fallback when the budget is full; expensive construction stays lazy;
`JCODEMUNCH_INDEX_CACHE_TTL` continues to mean idle reclamation and is honored by
the same holder rather than by a parallel one; README background-behavior section
updated in the same release.

### Arc 4 — adaptive certified semantic scoring — GATE CLEARED; LANE 1 SHIPPED, LANE 3 PARKED ON NEED

**Disposition recorded 2026-08-03, closing [#403](https://github.com/jgravelle/jcodemunch-mcp/issues/403).
Verdict: the measurement gate below is CLEARED.** @rknighton measured the
three-bucket breadth on real embeddings — Django 0.1591%, FastAPI 0.1809%, jcm
control 0.2686%, against a 10% PASS ceiling, with **0 genuine boundary
disagreements** against a 0.5% fail line. The thresholds were fixed in advance
and neither side moved them.

**What we verified ourselves, and what we did not.** We did not download the
86.9 MB archive or re-run the harness; the figures above are as reported.
(The no-LICENSE caveat that stood here is **resolved**: relicensed **MIT-0** on
2026-08-03, verified against the LICENSE file itself. Vendoring is permitted.)
What we did check is the part that could invalidate the result from our side —
the gate's own stated precondition — and it had NOT been met:

⚠⚠ **The `(score, symbol_id)` tie-break key had not shipped when the measurement
ran.** This section says it "is required regardless and ships FIRST, alone"
precisely so bucket (1) collapses *before* the number is taken. Every ranking
sort was still `key=lambda x: x[0]` at v1.108.227. **This does not invalidate the
verdict, and the direction is why:** an uncollapsed exact-tie bucket makes
measured breadth an UPPER bound, so a 0.159% pass holds a fortiori — it can only
shrink. It would have invalidated a FAIL or anything near the 10% line. Recorded
because the next person to read "gate cleared" should know which half of the
protocol actually ran.

**Lane 1 has already shipped, and not as Arc 4 work.** v1.108.223 answered
[#399](https://github.com/jgravelle/jcodemunch-mcp/issues/399) with exactly this
arc's first lane: a retained, L2-normalised float32 matrix cached per store
stamp, NumPy as an optional accelerator with a tested pure-Python fallback and
zero mandatory dependency. Measured 1942 ms → 2.9 ms warm on 30,479 × 384.

⚠⚠ **That changes what this evidence is for.** Bucket (3) — float32 and float64
disagreeing on ordering — now describes **production code**, not parked design.
And measured here on a deliberately near-tied synthetic 4,000-vector corpus, the
two shipped lanes **disagreed at rank 0**: two installs of the same version
ranking differently based only on whether NumPy was importable. **The tie-break
key shipped in v1.108.228 on the strength of that**, which is a better reason than
the one this section originally gave it.

⚠⚠ **CORRECTION 2026-08-08. This paragraph used to answer that finding with
@rknighton's "zero disagreement on real corpora", and that number no longer
exists — he [withdrew it himself](https://github.com/jgravelle/jcodemunch-mcp/issues/398#issuecomment-5175071271)
on 2026-08-04, because the packet had compared its exact baseline against a local
float32 certification candidate and **the shipped NumPy-absent lane never ran**.
We said on the issue the same day that the synthetic rank-0 finding "stands
unrebutted, and we'll record it that way rather than as contested" — and then left
the withdrawn zero standing here as its counterweight for four days. A retracted
result is not weaker evidence; it is none, and quoting one to make our own hazard
look inert is worse than having no counterweight at all.**

**The real production-lane comparison, pinned to `v1.108.228`
([reported 2026-08-08](https://github.com/jgravelle/jcodemunch-mcp/issues/398#issuecomment-5228065764)).**
Shipped NumPy-present against shipped NumPy-absent, the four dimensions reported
separately as asked:

| dimension | ranking problems | repeated lane pairs |
| --- | ---: | ---: |
| rank-0 difference | 0 of 12 | 0 of 120 |
| ordered top-k difference | 0 of 12 | 0 of 120 |
| top-k membership difference | 0 of 12 | 0 of 120 |
| **exact-tie partition difference** | **8 of 12** | **80 of 120** |

Supplemented by 5,000 mechanically generated queries over the same three frozen
indexes: **no query changed its first result at any depth**, while ordered-list
differences appear below rank 0 (5 at depth 5, rising to 114 at depth 100) and
tie partitions inside the retained top 100 differ for 130 of 5,000.

⚠ **State this as "no rank-0 difference on these corpora", never as "does not
fire in practice".** It does fire: below rank 0, and on tie partitions in most
problems. The earlier phrasing was true of the withdrawn number and is false of
this one. @rknighton draws the line himself and we should not draw it looser than
its author: the evidence supports keeping the faster lane, and it does not support
claiming the lanes are equivalent, that rank 0 can never change, or that the
production incidence is zero. The synthetic capability is not in question either —
his own replay reproduces our four-symbol case across 24 insertion orders and 50
fresh processes, and 3,211 of 10,002 boundary-targeted geometric cases change
rank 0.

⚠ **OPEN QUESTION, and it is ours: does the `.228` tie-break key touch this hazard
at all?** `(-score, symbol_id)` makes ordering deterministic *given* a set of
scores, which is exactly what bucket (1) needed. But an exact-tie partition
difference means the two lanes disagree about which items are equal in the first
place, and no tie-break key reconciles that. The credit this section gives `.228`
above may therefore be for a different defect than the one the 8-of-12 row
describes. Asked on the issue; not answered here.

⚠ **The 33-query screen in the earlier packet is retired, by its own author's
measurement.** Against the completed 5,000-query replay it scored **15% precision
and 4.4% sensitivity** (5 of 33 nominations real, 109 of 114 real differences
missed), because it emulated both lanes inside NumPy instead of running them.
Nothing above rests on it. Recorded because a cheap screen that nominates a subset
for full replay is a pattern we would otherwise be tempted to reuse.

**Lane 3 (certified uncertainty-set rescoring) is PARKED, and the premise to
re-examine is its own.** It exists to make an approximate scorer safe by
rescoring only uncertain candidates. The exact scorer is now 2.9 ms warm. Before
building it, someone has to show what it accelerates that is still slow —
otherwise it is a certification subsystem and a memory cap bolted to a path that
already costs single-digit milliseconds. **Lane 2 (chunked streaming) is
likewise parked**; v1.108.223's 2-repo cache bound addresses part of the memory
concern it was for.

⚠ **Stated limit of the evidence, not held against it:** four fixed queries per
corpus, one logical candidate row each. The gate never specified a query count,
and picking one now — after seeing a result we like — is exactly the move the
"neither side picks the bar after seeing results" rule forbids. It bounds how
strongly the breadth figures generalise; it does not bound the verdict.

⚠ **The query-count limit is narrower than it was, but it has not gone away.**
The 2026-08-08 comparison adds 12 ranking problems and a 5,000-query replay, and
the 120 repeated lane pairs demonstrate stability rather than 120 independent
situations. Those queries are mechanically generated from indexed symbols, not
user traffic and not complete tool calls, over three frozen corpora. **A
production-rate estimate still does not exist**, and would need representative
real usage across enough users and time.

---

Three exact-result lanes (retained float32 matrix / chunked streaming / certified
uncertainty-set rescoring), NumPy as an optional accelerator only.

⚠⚠ **The gate is a number we do not have.** Every semantic figure in the proposal
uses deterministic synthetic 384-dim vectors attached to real symbol IDs. Scoring
cost scales with vector count and dimension, so the matrix lane should carry over
— but **lane 3 certification breadth depends on embedding CONTENT**, and real code
embeddings produce more near-ties than the fixture. The reporter names this
himself. If certification fires often on real embeddings, the fast lane degrades
toward the exact scorer it replaces while carrying an optional native dependency
and a memory-cap subsystem.

**The measurement is SPECIFIED, 2026-08-02**, because a pooled number cannot fail
this arc for the right reason. Breadth reports in three buckets, never one
(@rknighton's refinement, accepted):

1. **exact ties** — identical embeddings, identical scores. Duplicated
   docstrings, boilerplate, generated files, near-identical test methods.
2. **near ties** — distinct scores inside float32 epsilon.
3. **genuine boundary cases** — float32 and float64 actually disagree on the
   ordering.

They have opposite remedies. If (1) dominates the answer is a deterministic
tie-break key, not abandoning the lane. If (3) dominates the gate has found the
real thing and the lane fails.

⚠ **The tie-break key is required regardless and ships FIRST, alone.** Today the
ranking sorts on score with a stable sort, so ties break on insertion order:
deterministic but arbitrary. Certifying that means reproducing an arbitrary row
order bit for bit. A `(score, symbol_id)` key is independently right - a ranking
should not depend on which row SQLite handed back first - and it collapses bucket
(1) before the measurement runs, so the number measures what it claims to.

**Corpus — Django is authoritative, FastAPI is the second point, jcm's own index
is a control and NOT authoritative.** It is the cheapest (already embedded) and
it is the one we tune against, so it is the wrong place to certify. Tie density
is a function of corpus HOMOGENEITY rather than size, so it gets reported as a
corpus property alongside the result, per repository, not folded into a pooled
figure.

**Threshold — named before the run, deliberately.** On the authoritative corpus:

- certification fires on **<= 10%** of scored candidates → PASS
- 10-25% → the lane needs a design answer before it ships
- **> 25%** → FAIL, the lane does not carry its dependency
- bucket (3) above **0.5%** → FAIL regardless of speed; that is a correctness
  signal, not a cost one

The 10% line is where the reported 9.7x-16.2x generation-warm result still clears
5x once the certified fraction pays exact-scorer cost. Fixing the number in
advance is the point: neither side picks the bar after seeing results.

**Close condition:** the three-bucket breadth measured on REAL embeddings on
Django, with tie density reported per corpus, against the thresholds above.
Until that exists this is accepted design, not approved work.

⚠ NumPy is native code, so the accelerated lane inherits
[[feedback_native_imports_belong_on_the_main_thread]]: one guarded main-thread
import before the four serve runners, extending `warm_up_embedding_backend()`
rather than adding a second startup mechanism, and disabled when
`JCODEMUNCH_EAGER_EMBED_IMPORT=0`. The base install must keep zero mandatory
NumPy dependency.

**Supporting material.** The Arc 1 / Arc 2 harnesses and fixed-schema CSVs are
published at [`rknighton/jcm-398-evidence`](https://github.com/rknighton/jcm-398-evidence)
with a `verify.py` that recomputes the quoted figures from the shipped CSVs.
⚠ Two standing cautions from the author, both worth honoring before aggregating:
the archive retains earlier exploratory rows at `6996cc08` beside the `c2201a55`
rows and **no `6996cc08` row backs a quoted figure** (filter on the provenance
column first), and Arc 2's classification screen is one pair per case, so its 58
Express/Gin sub-50ms cases cannot separate from timing variance - Django (5.17x)
and FastAPI (2.59x) are the load-bearing part. ✅ **Licensing resolved 2026-08-03: the repository is now
[MIT-0](https://github.com/rknighton/jcm-398-evidence/blob/main/LICENSE)** (MIT
No Attribution), relicensed by @rknighton on his own initiative after noticing
the omission. Verified against the LICENSE file, not the announcement. No
conditions, no attribution requirement, no notice to carry, so `verify.py` and
the harnesses may be vendored into this tree outright. The earlier
read-and-rerun-only restriction no longer applies.

**Provenance.** Filed by @rknighton as #398 on 2026-08-01 with a full research
archive offered on request. Split per the one-issue-one-verdict rule; #398 closed
as the umbrella once Arc 5 shipped and these four landed here. A post-close review
of the shipped Arc 5 code by the same reporter produced **v1.108.211** (the
`count()` removal collapsed a repository-level state, and the note attached to it
was giving wrong advice), which is why this section stays live after the umbrella
closed.

---

## Skill-candidate advisory — "a skill might outperform a doc here" — SHIPPED

`agent_selector` already tells a caller *a lesser model might handle this*: cheap
signals, named thresholds, an annotation on the result, and `mode: off` until you
ask for it. The same shape applied to agent config files answers a different
question with the same economics — **which parts of an always-resident doc are
paying rent they don't earn?**

A global `CLAUDE.md` is loaded into every session under its directory. A section
that only matters when you happen to be working in one subtree still costs its
full token weight on every turn that has nothing to do with it. That is a skill:
same prose, loaded when the topic comes up.

### The signal is scope concentration, not size

⚠⚠ **A size threshold is the wrong instrument and must not ship as one.** "Your
CLAUDE.md is large" fires on every large CLAUDE.md, tells the user what they can
already see, and gets muted — after which the advisory costs tokens and delivers
nothing. The measurable claim is that a section's *references* are concentrated
while its *residency* is global.

`audit_agent_config` already holds both halves. `_extract_symbol_refs` and
`_extract_file_refs` pull the symbols and paths a section names; the index
resolves them; `_estimate_tokens` prices the section; `_discover_files` already
distinguishes global configs from project ones. A section whose resolved
references all land under one subtree is scoped content sitting in an unscoped
file. Finding shape:

> `CLAUDE.md` lines 412-486 — 3,100 est. tokens, resident every session. All 14
> symbol references and 9 path references resolve under
> `src/jcodemunch_mcp/runtime/`. Sections with this profile are skill candidates:
> same prose, loaded when the topic comes up.

⚠ **STATED LIMIT, do not let the finding imply otherwise: nothing records which
config section was actually relevant to a turn.** Resident cost and concentration
ratio are both measured; *need* is not. The finding must say what it observed and
must not assert the section went unused. Adding that telemetry is a separate
decision with its own privacy surface, and this entry does not assume it.

### Why this one carries no derivation risk

The recommended action is a cut-and-paste of prose the user already wrote, plus a
pointer. No model rewrite, no summary, nothing derived, no receipt to forge.

⚠ **This is the whole reason this entry exists and a document-to-skill
*generator* does not.** Distilling a source document into
model-written per-chapter files produces an artifact an agent then answers from,
with no evidence binding it to the source — the opposite posture to the evidence
arc above, under the same brand. A generator is out of lane. An advisory over
prose the user owns is not. Do not let the two merge during implementation: if a
future revision proposes writing the skill *content*, that is a different
proposal and needs its own review.

### Placement

No new tool — the catalog moratorium holds.

- a sixth check in `tools/audit_agent_config.py`, sibling to `_check_bloat` /
  `_check_redundancy` / `_check_scope_leaks`
- a fifth finding category in `suggest_corrections`, alongside routing /
  vocabulary / index-freshness / stale-config
- rides the `reflect` CLI unchanged; its difflib unified-diff preview already
  renders exactly this edit (a deletion plus a pointer)
- scoring copies `agent_selector`'s structure — signals dataclass, weights dict,
  a `DEFAULT_THRESHOLDS`-style named floor, `mode: off | manual | auto`

### Close condition — met 2026-08-06, same day it was filed

Built in `tools/audit_agent_config.py` (`_check_skill_candidates`,
`_split_sections`, `_best_subtree`, `_resolve_section_refs`), surfaced as the
`skill_candidate` correction kind in `suggest_corrections`, gated by
`skill_advisor_mode` (default `off`). Tests in `tests/test_skill_candidates.py`.

Each condition as accepted, and how it landed:

- *resident-token cost and concentration both computed from the index rather
  than file size* — **amended, deliberately.** Token cost cannot come from the
  index; it comes from the section text via `_estimate_tokens`. What the
  condition was protecting is that the **trigger** is index-resolved references,
  never size: `_check_skill_candidates` returns `[]` outright when no index is
  available, and `test_large_section_with_scattered_refs_is_not_flagged` fails
  any implementation that reverts to a size threshold.
- *finding text states relevance was not measured* — shipped, in the message and
  as a `relevance_measured: False` field. Test-asserted.
- *`mode` defaults to `off`* — shipped. An unrecognised value also resolves to
  `off`, so a typo cannot silently enable it.
- *floor tuned against real configs outside this repo, tuning set named in the
  test* — shipped; `_TUNING_SET` in the test file names the five configs, their
  sizes, and the two findings they produce.
- *a skill generator is out of scope* — held. Nothing is generated, summarised,
  or rewritten; `suggested_patch` is deliberately `None` because a diff showing
  only the deletion would read as "delete this section".

⚠⚠ **Two things measurement changed, both worth keeping.**

**The thresholds inverted.** The filed design assumed a high concentration floor
meant a strict check. It does not: the floor and the subtree-share cap pull
against each other, because a narrow subtree that fails the floor hands selection
to its permissive parent. An 0.8 floor with an 0.6 share cap found **nothing** in
the tuning corpus — that is "off", not "strict". The share cap is the real
discriminator (measured: genuine subtrees 0.12-0.13 of their tree, package roots
0.33-0.50), so it tightened to 0.25 and the floor came *down* to 0.65.

**The dogfood case was wrong.** This entry cited jcm's own Current State
section — 157 entries, ~233k chars, ~58k tokens per session — as the case the
advisory would have caught. It would not. Measured, jcm's `CLAUDE.md` yields
**zero** skill candidates: Current State is genuine bloat, but its references
span the whole package, so it is not scope-concentrated. `_check_bloat` is the
check that catches it, and already did. The claim was plausible, load-bearing for
the entry's motivation, and false.

⚠ **The corpus produced two findings and one of them is a known weak positive**
(`AGENTS.md` "Session-Aware Routing" — global guidance that scores concentrated
because the tool names it cites resolve to the files implementing those tools).
Recorded in `_TUNING_SET` rather than tuned away. **Two findings is not a
precision measurement and must not be quoted as one.**

### Follow-on found while building, not fixed as scope creep

`_stale_config_corrections` read `f.get("type")`; `audit_agent_config` labels
findings with `category`. The membership test could not match, so the
`stale_config` correction kind had **never once been emitted** since it was
written. Fixed in the same commit because it is the identical wiring this entry
extends, with a regression test that fails against the old reader.

### Provenance

jjg, 2026-08-06, after reviewing
[book-to-skill](https://github.com/virgiliojr94/book-to-skill) by
[@virgiliojr94](https://github.com/virgiliojr94) — an MIT-licensed tool that turns
a book, doc folder, or source collection into an Agent Skills bundle — and
concluding the advisory was the part worth having here. Its idea, a different
lane, and the framing below is a comment on fit with *this* project's evidence
posture, not a criticism of that one.

⚠ The motivating example originally recorded here — this repo's own Current State
bloat, 157 entries / ~233k chars / ~58k est. tokens per session, noted 2026-07-25
in maintenance practice #5 — **did not survive measurement**; see the close
condition above. The advisory is still worth having, but it answers a narrower
question than the one that prompted it, and the example that sold it was not an
example of it.

---

## Split `server.py` — the dispatcher is past every reasonable file limit

`src/jcodemunch_mcp/server.py` is **10,549 lines / ~513 KB**. On 2026-08-08 it
crossed `DEFAULT_MAX_FILE_SIZE` (512,000 bytes) by **532 bytes**, and this
project's own MCP entrypoint stopped entering this project's own index. The file
grew another 1,239 bytes in the day it took to write #429 up.

v1.108.269 shipped the observability and the escape hatch: an oversize file is
now named in the indexing response instead of surfacing as a counter inside a
refused verdict, `max_size` is reachable over MCP, and `index_repo` stopped
hardcoding the cap. **None of that is this entry.** It made the repository
navigable; the file is still the size it was.

⚠ **Raising a limit is not a fix when the file is the problem.** The cap moved
once already for this class of file (v1.108.193, then v1.108.197 for the
per-project key). Moving it again buys the same amount of time and teaches the
next person to move it a third time. `server.py` will cross whatever ceiling
replaces 512 KB.

⚠⚠ **This is the highest-blast-radius refactor available in this repository**,
which is why it is a plan and not an in-flight issue. `server.py` owns tool
registration, the `call_tool` dispatch chain, CLI subcommand dispatch, auth and
rate-limit middleware, all three transports, and the Counter front door. Several
guarantees are load-bearing *because* they sit at a single chokepoint —
`evidence/producers.py` mints receipts from the `call_tool` chokepoint
specifically so it is immune to early returns by construction, and the response
size ceiling (#425) wraps that same dispatcher. A split that relocates a
chokepoint without relocating its immunity argument silently voids it.

It is also the most-edited file in the project, so any split is a merge-conflict
event for every branch open at the time.

### Close conditions

- No single module in `src/jcodemunch_mcp/` exceeds `DEFAULT_MAX_FILE_SIZE`, with
  the default cap **unchanged** — raising the limit does not satisfy this.
- Indexing this repository reports `too_large: 0` and `complete: true`, so
  absence is citable across the whole tree including the entrypoint.
- The `call_tool` chokepoint keeps a single ingress. If dispatch is split, the
  receipt-minting and response-cap wrappers still see every call, and a test
  asserts an exit cannot bypass them.
- Tool count and every `inputSchema` are byte-identical across the split. A
  schema that changes shape during a file move is a surface change wearing a
  refactor's clothes.

### Provenance

Found in-house and filed by jjg as
[#429](https://github.com/jgravelle/jcodemunch-mcp/issues/429) on 2026-08-08,
alongside the two defects that shipped as v1.108.269. Parked here rather than
left open per the standing rule that an issue opens when work starts or a user is
blocked; accepted design with no start date is a plan. #429's close comment
points here, so the promise that this is tracked resolves to a real entry.

---

## `input_examples` — INVESTIGATED AND DECLINED, 2026-08-24 (NEGATIVE result)

⚠⚠ **Not an accepted entry.** The Conventions below say a rejected proposal
gets a closed issue, not a roadmap line. This is here anyway because the thing
worth preserving is a **measurement plus a blocker**, and a closed issue is not
where anyone looks before asking "why don't we ship tool-use examples?" — the
same reason the `codex_surface` negative lives in `CLAUDE.md`. It is filed as a
finding, and it carries the condition that would reopen it.

**The prompt.** Anthropic's [Advanced tool
use](https://www.anthropic.com/engineering/advanced-tool-use) reports
`input_examples` improving accuracy **72% -> 90%** on complex tasks. We already
curate 32 example argument objects in `counter.EXAMPLES`, consumed today only
by `menu` rows and `route`'s `args_template`. The raw material exists.

**What was measured** (bytes/4, the estimator `jcodemunch-mcp surface` uses):

| | |
|---|---:|
| catalog tools | 94 |
| with a curated example | **32 (34%)** |
| schema tokens, no examples | 27,474 |
| schema tokens, + examples | 28,029 |
| delta | **+555 (2.0%)** |
| per covered tool | +17.3 |
| resident set of 5, under deferral | **+62** |

⚠ **The token objection is dead** and should not be raised again. 2.0% overall,
and consistent with the vendor's own "~20-50 tokens for simple examples".

⚠⚠ **THE BLOCKER IS THAT THE FIELD DOES NOT EXIST FOR US.** `input_examples` is
an Anthropic **API** field on user-defined tool definitions. The MCP `Tool`
type has no such field — `name`, `title`, `description`, `inputSchema`,
`outputSchema`, `icons`, `annotations`, `meta`, `execution`, and nothing else.
The connector builds tool definitions from our `tools/list`, and no
documentation maps a non-standard field through.

⚠⚠ **MCP's model allows extras, so we COULD attach it, and that is the trap.**
The docs state: *"Each example must be valid according to the tool's
`input_schema`. Invalid examples return a 400 error."* So the downside of
betting on undocumented behaviour is not "no benefit" — it is **a 400 that
breaks the user's session**. Unverifiable upside against a session-breaking
downside is the wrong trade.

⚠ **There IS an available route, and it is NOT this feature.** The tool-use
system prompt embeds `{{ TOOL DEFINITIONS IN JSON SCHEMA }}` and our
`inputSchema` passes through verbatim, so the standard JSON Schema `examples`
keyword reaches the model on every host with no connector support. **Do not
claim the 72% -> 90% figure for it** — different mechanism, same category error
as quoting a token figure at an accuracy question. It also collides with the
hard **4,000-token `core_compact` ceiling**, which currently has about ten
tokens of slack.

⚠ **Order of levers, from the vendor's own page**: *"Provide extremely detailed
descriptions. This is by far the most important factor... Prioritize
descriptions, but consider `input_examples` for complex tools."* Descriptions
are first-order and we already gate them (`tests/test_description_smells.py`).

### What would reopen this

A **wire test**, and nothing short of it: attach examples, serve over
`streamable-http`, run a real request through the MCP connector, and read back
whether the constructed tool definitions carry them. That needs a deployed HTTP
endpoint and API credits. **Until someone has run that, "the connector might
pass it through" is a guess, and this entry stays closed.**

---

## Portable handoff: resuming a task on a different model, host or provider

Accepted 2026-08-25. Builds directly on the v1 handoff contract (#374) and the
v2 arc (#377), both designed by **@mightydanp** (handle only, name unknown).
Sequencing is not authorship: this entry extends their design, it does not
replace it.

### Why this is a jcm arc and not a new product

The industry push is multi-model adaptability, and every major provider is still
an island. The obvious hedge is an orchestration utility that persists sessions
across providers. That build is wrong for two reasons worth writing down, because
they will be re-proposed.

**A session-format converter has a short half-life.** AGENTS.md spread across
Codex, Cursor, Copilot and Amp inside a year. MCP went cross-provider in roughly
the same window. Providers have a positive incentive to make *inbound* migration
easy, so the format half gets commoditized by the very people it was meant to
hedge against.

**Faithful session transfer is impossible, and that is the useful fact.** Signed
thinking blocks, encrypted reasoning tokens, cached prefixes, provider-specific
tool-call ids and sampler state are all bound to a provider and usually to a
model version. None of it moves. So every cross-provider resume is a
*re-grounding* problem, not a copy problem. The transcript is the wrong unit of
persistence. Task state plus verifiable evidence is the right one, and
re-grounding a task against a repo is what this server already does.

That makes portable handoff an expression of the evidence arc above, not a
departure from it. It also ages in our favour: a 1M-token window makes "we save
you tokens" weaker every quarter, but a model that can read everything still
cannot say which part it actually used.

### The blocking defect, verified 2026-08-25 (do NOT re-derive)

`src/jcodemunch_mcp/handoff.py:45-49` stores handoffs in an in-process dict:

```
# Session-scoped store: handoff_id -> {"body": str, "receipt": dict}.
# Process lifetime == MCP session lifetime (same precedent as server._steer_state);
# process exit is the cleanup.
_lock = threading.Lock()
_handoffs: dict[str, dict] = {}
```

⚠ Quoted verbatim, `_lock` included, because that lock is the whole argument
against reading this as a stub nobody finished: the store was built for
concurrent access **within** a process. P0 does not add safety that is missing,
it moves the boundary the safety is drawn around.

`finalize_handoff`'s docstring says "persist", and `list_handoff_resources` is
documented "one per finalized handoff **this session**". ⚠⚠ **The artifact cannot
cross to another provider because it cannot cross the process boundary at all.**
Every phase below is gated on P0.

The second gap is already recorded under Phase 2: `_validate_evidence` attests a
ref matching a served symbol id **or the file component of one**, and
`render_handoff` emits refs in a single global Evidence block, so v1 proves
"retrieved this session" and binds no ref to any individual claim. P2 closes it.

### P0 - durable handoff store

Move `_handoffs` to disk. Follow `<CODE_INDEX_PATH>/refresh_state/` and
`digest_state/`, which are the actual on-disk precedents, rather than inventing a
location.

⚠⚠ **Do NOT model this on the `munch://evidence/<id>` receipts.** They look like
the obvious precedent and they are not one: `evidence/receipts.py` is
**session-scoped, in memory, bounded at 500** — the same defect as `_handoffs`,
one subsystem over. Copying it reproduces what P0 exists to fix.

- The handoff id stays the address. `get_handoff` and `handoff_for_uri` read
  through to disk.
- `list_handoff_resources` lists what is on disk, not what this process wrote.
- **Local-first. No network, no service, no sync.** See the out-of-scope note.
- Retention and eviction stated in the README before shipping, because a new
  on-disk artifact is new persistent behavior and those get disclosed first.

**Close condition:** a handoff finalized in one process is readable through
`munch://handoff/<id>` by a different process started afterwards, proven by a
test that crosses the boundary rather than by clearing and repopulating the dict.

### P1 - `resume_handoff`, the symmetric read side

Handoff is write-only today. Add the receiving half: take a handoff id, return a
budgeted, model-agnostic context bundle for a fresh session.

- Compose over `assemble_task_context` and `get_ranked_context`. This must not
  grow a parallel retrieval path.
- Re-resolve every evidence ref against the **current** index, not the snapshot
  the handoff was written against.
- Honour a token budget the same way the rest of the retrieval surface does.

⚠⚠ **`resume_handoff` is a new MCP tool, so its final step is BLOCKED and this
phase must be planned that way from the start.** `CATALOG_CEILING` is 91 and the
binding half of exit condition 1 is control-subset route@1 at **40.0% against a
55.0% bar** — not a near miss, and the name-leakage half has to clear too.
Build the capability, register it in `WITHHELD`, expose it when the gate
clears. That is the route `investigate_reuse_before_write` took in v1.108.296,
and the reason to say so here is that a phase whose ceiling is discovered at
the end reads as buildable until the last commit.

**Close condition:** `finalize_handoff` in session A then `resume_handoff` in
session B on a different model reconstructs the task without reading session A's
transcript. ⚠ Reached through the module function, not a catalog action, until
the moratorium lifts.

### P2 - per-claim freshness verdicts

The differentiator, and the piece that closes the v1 evidence gap. A resumed
session's worst failure is not missing context. It is confidently stale context:
the file moved, the symbol was renamed, the claim was true at 14:00 and false at
16:00.

- Bind refs to individual claims instead of to one global Evidence block.
- On resume each claim carries a verdict: `still_true`, `drifted`,
  `unverifiable`, computed from `get_changed_symbols`, `get_symbol_provenance`
  and `check_edit_safe`, all shipped.
- `unverifiable` is a first-class state, not an error. Absence of proof is
  reported as absence of proof, consistent with the absence-honesty behavior
  elsewhere in this server.

This is the part no provider can match cheaply, because computing it needs a
repo index and a retrieval record, not a bigger context window.

**Close condition:** renaming a symbol cited by a stored handoff flips that
claim's verdict to `drifted` on the next resume, and only that claim.

### P3 - host projection

Hosts read different memory files: CLAUDE.md, AGENTS.md, GEMINI.md,
`.cursorrules`. A resumed handoff should emit into the host's native shape.

- A small emit layer over the rendered handoff. No per-host retrieval logic.
- Writing is opt-in and never silent. The target path is reported.

**Close condition:** one handoff emits a valid AGENTS.md and a valid CLAUDE.md
whose task content is equivalent, verified by a rendering test rather than by
eyeball.

### Explicitly out of scope

**A hosted sync service.** Three independent reasons, and they compound.

1. It is a persistent network service holding user prompts and source code. This
   account was quarantined on PyPI once over an undisclosed persistent service.
   That is a design constraint, not a scar to be brave about.
2. It puts jMunch in the model wire, which is the lane this suite has stayed out
   of alongside its no-mutate and no-execute position.
3. It is the half the big players will commoditize. Building the commoditizable
   half while skipping the defensible half is the worst available split.

Sync is the user's git. The artifact is a file.

**A standalone package.** A fourth install surface splits already-thin adoption
and buys nothing that P0 through P3 do not.

### Provenance

Strategic question from jjg, 2026-08-25: what is the adaptation path if the major
providers compete directly with jcodemunch. The answer recorded here is that
multi-model adaptability is not where they can attack, because they cannot be
multi-model. Durability and verification of the handoff artifact is the lane.

---

## Conventions

- Entries here are **accepted**, not speculative. A rejected proposal gets a
  closed issue with reasoning, not a roadmap line.
- Each entry keeps its **close condition** verbatim from the design that was
  accepted, so scope cannot drift quietly between filing and building.
- When an entry starts, it gets an issue, and its line here gains the link.
- Credit stays attached to the entry. Sequencing is not authorship.
