# DESIGN — the competitive comparison, as a harness tier

Written 2026-09-05 at `fd4d5bf` (v1.108.317), after `FIELD.md`. This is the
Phase 2 stop point; nothing here is built. Where a rule already exists
(ARCHAEOLOGY R1-R62, harness DESIGN §5, inbound POLICY §4/§7/§8) this
document binds to it by name and does not restate it.

## 0. Decisions the rest rests on

- **D1. The loop is deterministic and calls no model.** Every step is a
  script: install pinned tools, index pinned corpora, run pinned tasks, count
  tokens, compare, write drafts. There is no classifier, no summary prose,
  no `ANTHROPIC_API_KEY` in any job. The only text a human reads that a
  competitor wrote is a release title quoted as data in a draft. This is what
  makes principle 5 cheap to keep: untrusted text never reaches a model.
- **D2. Competitor code runs only inside a container with the network
  removed.** `docker build` from a pinned Dockerfile (install phase, network
  on), then `docker run --network none --read-only --cap-drop ALL
  --security-opt no-new-privileges --user 65534 --memory 8g --pids-limit
  512` with the corpus mounted read-only and one writable `/out`. GitHub's
  `ubuntu-latest` and this box (Docker 29.2.0) both run it. jCodeMunch runs
  in the same container shape from the working tree, so the sandbox cost is
  paid symmetrically (principle 1). The ROADMAP's venv objection is answered
  by the container, not argued with.
- **D3. jCodeMunch is driven the way its docs say, and so is everyone
  else.** Our side is the published workflow (`search_symbols(max_results=5)`
  + `get_symbol_source` x3, R27, AI summaries off, R28) over MCP stdio from
  the checkout, `tool_profile` at the shipped default. Each competitor's
  side is its README's recommended configuration at its pinned release,
  written down in its fairness note before its numbers exist.
- **D4. Two result vocabularies, never mixed.** `measured` is a number this
  loop produced. `claims` is what a README says. A result file has no
  `claims` field at all; claims live only in FIELD.md and in a draft's
  "what they say" line, quoted (principle 7).
- **D5. Every result is per task, per corpus, per run, and the reported
  value is the median of three runs with the spread beside it.** No grand
  ratio is published without its per-repo rows (R30, R31); the null
  alternative's row appears in every table so a reader can see what "no
  tool" costs on the same line.
- **D6. Drafts, not issues, until Phase 4 passes and a human flips the
  posting switch.** Writes go to the `inbound-ledger` branch under
  `competitive/`, by the inbound App, under the inbound ruleset. Posting to
  the issue tracker is a separate job gated by a second variable
  (`COMPETITIVE_POST_ENABLED`) that Phase 3 ships absent.
- **D7. Code lives in `benchmarks/competitive/`, docs in
  `docs/competitive/`.** `benchmarks/` is tracked and SHIPS in the sdist
  like every other benchmark harness (the sdist exclude list names
  `.claude/`, `.github/` and root files, not `benchmarks/`), so a result
  file may carry no local path, username or scratch location; the end-to-
  end test asserts the header holds only platform, python, cpu count and
  a CI flag. `docs/competitive/` needs a
  `!docs/competitive/` line in `.gitignore` (FIELD.md header). The harness
  gains a fourth tier name, `compete`, so `python -m harness compete` is
  the one command, listed in `harness/tiers.json` beside `bench`, and
  `python -m harness bench` is unchanged.

## 1. Adapters

### 1.1 The interface

`benchmarks/competitive/adapter.py` defines the protocol every tool
implements, including jCodeMunch and both nulls:

```
class Adapter(Protocol):
    name: str                 # "serena", "null_readall", "jcodemunch"
    pin: Pin                  # registry, package, version, digest, dockerfile sha
    categories: frozenset     # task categories it can answer: {"P1","P2","P4"}; nulls: all
    interface: str            # "mcp-stdio" | "cli" | "python" | "null"

    def image(self) -> str                        # build or reuse the pinned image; returns image digest
    def index(self, corpus: Corpus) -> IndexReport  # cold index inside the container; wall seconds, ok, stderr tail
    def reindex_one(self, corpus, path) -> float | None   # one-file incremental cost; None = NOT COMPARABLE
    def answer(self, corpus: Corpus, task: Task) -> Answer
    def tools_list_tokens(self) -> int | None      # MCP servers only; None otherwise
    def version(self) -> str                        # read from the running tool, never from the pin
```

`Answer` carries: `payload` (the exact bytes the agent would receive, every
tool result concatenated in call order), `tokens` (cl100k over `payload`,
R14/R15), `latency_ms` (per call, cold and warm), `cited` (the set of
`(file, line)` the payload names, extracted by the adapter's declared
parser), `calls` (count), `error`. A tool that needs more than one call to
answer a task pays for every call, as an agent would (R17's spirit).

`Corpus` is a pinned checkout (`tasks.json` SHA or the additional set in
§3) mounted read-only. `Task` is §4's record.

### 1.2 The null adapters (Phase 3 item 1)

- `null_readall`: Baseline A. `payload` = every indexed file concatenated
  (R23). Answers every category trivially and always "cites" everything, so
  its F1 is the precision floor, reported not scored.
- `null_grep`: Baseline B (R24-R26): `rg -l` the query terms, rank by match
  count, open the top 3 whole. Cited lines = the matching lines in those
  files. This is the agent-with-its-own-tools alternative and it is the row
  every competitor must beat to be worth installing.

Both reuse `run_benchmark.py`'s reader so the file set is identical to the
published benchmark (R22, R34). Item 1 proves the tier end to end with only
these two and jCodeMunch: the result file exists, the schema validates, the
three-run spread is recorded.

### 1.3 The eight adapters (Phase 3 item 2, one PR each)

Each adapter ships `adapters/<tool>.py`, `sandbox/<tool>.Dockerfile`
(base image by digest; install pinned by version and, where the registry
gives one, by hash), and `docs/competitive/fairness/<tool>.md`. The
fairness note is written and reviewed **before** the first number is
recorded and has four fixed headings: *What the docs recommend* (quoted,
with the README commit), *What we configured and why*, *Where the harness
may disadvantage it*, *What we could not make work* (empty is a claim).

| Tool | Interface | Categories | Known fairness hazards to write down first |
|---|---|---|---|
| Serena 1.7.0 | mcp-stdio | P1, P2, P4 | needs pyright, typescript-language-server, gopls in the image; first-call LSP warm-up is real latency and is reported cold and warm separately; its recommended "context" and "mode" for read-only use |
| codebase-memory-mcp 0.10.8 | mcp-stdio | P1, P2, P4 | pip vs static binary (pick pip, say so); which of its ~10 tools map to each category |
| code-review-graph 2.3.8 | mcp-stdio | P1, P2, P4 | 28 tools: the adapter uses the ones its README names for search/refs/deps; optional embeddings OFF (offline), stated |
| CodeGraph 1.6.0 | mcp-stdio or cli | P1, P2, P4 | native binary from a release asset; if its query language is Cypher, the adapter's queries are its README's examples verbatim |
| Graft 0.16.0 | cli (`graft build/ask`) then mcp | P1, P4 (P2 if `ask` answers it) | deterministic path only, no key; the pre-written cards are its whole design, so `graft build` time is its index cost and its cards are its payload |
| Aider RepoMap | cli (`aider --show-repo-map`) | token axis only | the map is one ranked text, not a per-task answer; F1 NOT COMPARABLE; tokens reported as a per-corpus cost with `--map-tokens` at its default |
| cymbal 0.14.0 | cli | P1, P2 | subprocess per call; its own latency claim is the number to recompute; FTS5 query syntax per its README |
| CocoIndex Code 0.2.41 | cli + mcp | P1 | embedding model baked into the image at build; its P2/P4 support decided by its docs, not assumed |

A tool that cannot be built into an image from its documented install, or
that opens a listening port or a background process the run does not own,
or that needs a key, is recorded in `docs/competitive/FINDINGS.md` and
excluded with the transcript of the attempt (Phase 3 rule). It keeps its
row in the result file as `not_runnable` with the reason, so its absence
is visible.

### 1.4 The jCodeMunch adapter

`adapters/jcodemunch.py`: runs `python -m jcodemunch_mcp` from the mounted
checkout in the same container base, `CODE_INDEX_PATH` on `/out`, config
file absent (shipped defaults), AI summaries off. P1 = `search_symbols` +
`get_symbol_source` on the top 3; P2 = `find_references` (+
`include_call_chain=true` only if the fairness note argues the README
recommends it for the task; the sverklo run did not, and the CHANGELOG said
the description was the problem, so the first run uses the default and a
second configuration is reported as a labelled variant, never silently
substituted); P4 = `get_dependency_graph` / `find_importers`. Our
`tools_list_tokens` is the `full` profile because that is the shipped
default (D3), with the `counter` figure reported beside it as a variant.

## 2. Axes

| STANDARD criterion | Comparable? | How it is measured across tools |
|---|---|---|
| 1(a) extractor fidelity | NOT COMPARABLE | grades our extractor's buckets against a language oracle; no tool exposes an equivalent. Reported: nothing |
| 1(b) retrieval quality | **COMPARABLE** as F1 per task category on the shared task set (§4), with the field's line tolerances; MRR/nDCG where a tool returns a ranked list, else F1 only | the axis the field weighs and we have never measured against a product |
| 1(c) goldset channel recall | NOT COMPARABLE | `find_implementations` channels are ours |
| 2 tokens per task | **COMPARABLE** | cl100k over `Answer.payload`, per task, per corpus; ratio vs `null_grep` and vs `null_readall` on the same row (R22-R31); tool-call count beside it |
| 3(b) one-file reindex cost | COMPARABLE where the tool has an incremental path in its docs, else NOT COMPARABLE per tool | `reindex_one` on the same edited file; a tool whose only path is full re-index reports that as its cost, labelled `full_reindex` |
| 3(c) cold index time | **COMPARABLE** | `index` wall seconds inside the container, same corpus, same CPU limit |
| 3(a) freshness property | NOT COMPARABLE | a property of our read paths |
| 4 tool-surface | COMPARABLE for MCP servers (`tools/list` token weight, cl100k, the zhang-liz shape); NOT COMPARABLE for CLI tools (reported as `interface: cli`, 0 schema cost, which is a real advantage and is said so) | our counter/core ceilings stay ours |
| 5 latency | **COMPARABLE** | per-call cold and warm p50/p95 over the task set, same container, same limits |
| 6 install friction | PARTIALLY | image build seconds and image size, and the count of prerequisites the Dockerfile had to install beyond the package (a proxy, labelled as one); the handshake and config-parity halves are ours |
| 7, 8, 9 | NOT COMPARABLE | properties of a codebase and its release process; the loop reports the pin and licence and nothing else |
| 10 breadth | REPORTED, not scored | the tool's claimed language count (`claims`, FIELD.md) beside the count of corpus files it actually indexed (`measured`); the second number is the honest one |

**Axis the field weighs that STANDARD lacks (proposed, not added):**
reference-finding recall as a named sub-metric of criterion 1. Today 1(b)
is nDCG/MRR/Recall on a replay set of definition-shaped queries; the field's
P2 category has no home. The proposal goes through §8 as a
`standard-proposal` draft after the first run, with the measured P2 row
attached, and a human edits STANDARD.md or declines.

## 3. Corpus fairness

### 3.1 What the pinned corpora are

`benchmarks/tasks.json`: express (JS, 186 files), fastapi (Python, 1,186),
gin (Go, 98). All three are web frameworks; all three are the kind of repo
symbol search flatters (many small named handlers); none is TypeScript,
Java, C#, Rust or a monorepo; the largest is 1,186 files; the queries are
five short keyword phrases written to match framework vocabulary (R27 era).

### 3.2 What the field indexes

From FIELD.md: sverklo-bench uses express, lodash (a single 17k-line UMD
file), requests, flask; the VirtusLab review's complaint is monorepo scale;
the graph products market 14-158 languages; the codebase-memory paper and
#142 test on the reporters' own TypeScript/JS projects; Serena's users are
by construction on LSP-served languages (TypeScript, Python, Go, Java, Rust).

### 3.3 The check, as a script (Phase 3 item 3)

`benchmarks/competitive/corpus_check.py` runs before scoring and fails
when: (a) the corpus set has fewer than four languages by file share; (b)
no corpus has a file over 5,000 lines (the lodash shape); (c) no corpus has
more than 2,000 indexed files; (d) every corpus is the same domain by its
own description (`tasks.json` `description`); (e) any corpus's language
share exceeds 60% of the set. Each threshold is written here once and read
by the script from a `corpus_policy.json` it ships with; they are not
Floors and do not go in `harness/thresholds.json`.

### 3.4 The verdict on today's corpus, and the remedy

The pinned three fail (a), (b), (c) and (d). **They are not swapped**: they
stay pinned and checksummed for the token Floor (`token.*`, R1-R6), and the
competitive tier runs them for continuity with every published jcm number.
The remedy is an **additional set**, `benchmarks/competitive/corpora.json`,
pinned by SHA the same way, proposed as: lodash 4.17.21 (single-file JS;
also sverklo's corpus, so their P1/P2/P4 tasks apply as written), requests
2.32.3 (Python library, not a framework; same reason), one TypeScript
repository between 2,000 and 5,000 files (chosen at Phase 3 by the
`corpus_check` criteria, SHA recorded before any tool runs on it), and one
repository over 10,000 files as the size bucket the ROADMAP said "is the
one we would most like to leave out". The additional set is where we expect
to lose; that is why it exists.

## 4. Task fairness

### 4.1 The task record

```
{ "id": "lodash-P2-017", "corpus": "lodash/lodash@<sha>", "category": "P2",
  "query": "where is `baseFlatten` referenced", "expected": [["lodash.js", 1043], ...],
  "tolerance_lines": 2, "source": "sverklo-bench@<commit> (CC-BY-4.0)" | "jcm-tasks.json" | "authored:<who>:<date>",
  "answerable_by": ["null_grep", "serena", ...], "capability_only": false }
```

Categories are the field's: P1 definition lookup, P2 reference finding, P4
file dependencies; P5 dead code is adopted for reporting only (their own
note: every baseline scores the same on it). Our five `tasks.json` queries
are kept as a fourth category, `T` (token task, no expected set), because
they have no ground truth and were never meant to be scored for
correctness; they carry the token Floor and nothing else.

### 4.2 Sources

sverklo-bench's task files at a pinned commit (CC-BY-4.0, attribution in
the file header), for lodash, requests and express (their express is
4.21.1, ours is `1faf228`; the adapter maps expected lines by content, and
a task whose expected line does not exist at our SHA is dropped with the
count reported). Tasks for the TypeScript and large corpora are authored in
Phase 3 by the same rule they used: hand-verified `(file, line)`, written
by reading the file, with the author and date in `source`. Nobody who
wrote an adapter writes tasks for the category that adapter is weakest on;
the reviewer checks this.

### 4.3 The answerability check (Phase 3 item 3)

`benchmarks/competitive/task_check.py` fails when: a task's category is
outside `null_grep`'s categories (it answers all four by construction, so
this catches a malformed record); a task's `expected` names a file absent
from the corpus at its SHA; a task's query mentions a jCodeMunch tool name,
a symbol id, or a `_meta` field. A task is flagged `capability_only: true`
when fewer than two non-null adapters declare its category; it is excluded
from every head-to-head table and listed in a "capability differences"
section with which tools declared it. The check is symmetric: a task only
Serena can answer (a rename-safety question, say) is excluded the same way.

## 5. Scoring and reporting

### 5.1 Per axis, per tool

For each `(axis, tool, corpus)`: `measured` = median over three runs;
`spread` = max minus min over the three; `jcm` = our median on the same
row; `delta` = ratio for tokens, latency and index time (tool over jcm, so
below 1.0 is the competitor ahead), difference for F1 (tool minus jcm, so
above 0 is the competitor ahead); F1 matches ONE-TO-ONE: each expected line
takes the nearest still-unmatched cited line within the tolerance, so a
dense citer (grep returns every matching line) is not paid twice for one
hit; a read-all answer scores recall 1 and precision = expected over corpus
lines. `latency_call_ms` is the median wall time of one call over every
call of every task; the operations differ by tool, so it is the wait per
call, not a like-for-like operation. `stable` = each row's own spread is within
10% of its own median, judged first, so a row's instability cannot widen
the band it is then measured against (Phase 3 item 1 found the first
draft doing exactly that); `band` per harness DESIGN §5: max(5% of our
median, 3 x the larger of the two spreads); `meaningful` = both rows
stable and |delta| outside the band. An unstable row is reported
`unstable` and never `meaningful`.

### 5.2 Files

- `benchmarks/competitive/results/<UTC date>-<jcm commit>.json`, schema
  `jcm-competitive-result/v1`: header (date, jcm commit and version, runner,
  image digests, corpus SHAs, task-set sha256), then one record per row
  above with the three raw values, and a `not_runnable` list with reasons.
- `benchmarks/competitive/results/latest.md`: the human summary, one table
  per axis, rows = tools with both nulls first, columns = corpora, cell =
  `measured (delta, spread)`, plus the capability-differences section and
  the not-runnable list. Generated, never edited.
- Neither file carries a `claims` field (D4).

### 5.3 What the summary must say on every run

The per-repo rows before any aggregate (R30, R31); the null rows on every
table; the band on every table; the jcm variant rows (`counter` surface,
`include_call_chain`) labelled as variants under the default; the sentence
"a competitor's README figure is not on this page" in the header, because
a reader will look for it.

## 6. Trend tracking

`benchmarks/competitive/results/history.jsonl`: one line per run with the
header fields and every `(axis, tool, corpus)` median. The summary's last
section, *Movement*, lists per row: delta now, delta on the previous run,
delta on the first run, and one of `widened`, `narrowed`, `flipped`,
`unchanged` (within band), with the competitor's release on each of the
three runs beside it, so a movement that coincides with their release is
visible without being attributed. A row whose `jcm` value moved while the
competitor's release did not is our regression or our improvement and is
said so.

## 7. Findings to issues

### 7.1 Rules

Evaluated after every full run by `benchmarks/competitive/findings.py`,
over the result file and history only (no README, no network):

| Condition | Label | Fields the draft must carry |
|---|---|---|
| jcm behind a competitor on a COMPARABLE axis, `meaningful`, on any corpus | `competitive-gap` | axis, corpus, task category, our median and spread, theirs, the band, the competitor and its pinned release and image digest, the run file, and **a first hypothesis** chosen from a fixed list the script owns (`tool_not_called`, `ranking`, `coverage`, `payload_shape`, `index_missing_files`, `unknown`) by rule (e.g. our `cited` set empty on every task of the category → `tool_not_called`); never a fix |
| jcm ahead on an axis and the gap `narrowed` on two consecutive runs | `competitive-watch` | the three deltas, the two competitor releases across the runs, whether our value moved |
| a set member's release title (weekly feed, §9) matches the capability word-list (`reference`, `call graph`, `incremental`, `watch`, `rename`, `LSP`, `embedding`, `monorepo`) | `competitive-idea` | the title **quoted as data** with the feed's preamble line, the release URL, the STANDARD criterion the word maps to, and the fixed sentence "adoption is not implied; the tool-surface discipline (small front door, deep menu) is not moved by this" |
| a set member's release title or notes name a measured axis (`token`, `faster`, `latency`, `index`) | none; **schedules a re-run** (dispatch of `competitive-run.yml` with `reason=release:<tool>@<version>`) | recorded in the feed's audit record |
| everything else | none | recorded in the result file and history |

### 7.2 De-duplication

Every draft carries a fingerprint line `competitive-id: <label>/<axis>/<tool>/<corpus>`
(no version, no value). Before writing, the script lists open issues with
any `competitive-*` label and reads their bodies for the same fingerprint;
a match means the draft is **updated in place** on the ledger branch
(`competitive/drafts/<fingerprint>.md`, newest values appended under a
dated heading) and no new issue is ever drafted. A closed issue with the
fingerprint does not block: the gap came back, and the draft says so with
the closed issue's number. The read is the same App-token `gh issue list`
the inbound layer uses; when it fails, the script refuses to draft (fail
closed on duplicates, like the budget).

### 7.3 Draft location and posting

Drafts are files on `inbound-ledger` under `competitive/drafts/`, written
by the App under the inbound ruleset, one per fingerprint, in the issue
template shape (title line, labels line, body). Posting is
`competitive-post.yml`: runs only when `INBOUND_ENABLED` is `true` AND
`COMPETITIVE_POST_ENABLED` is `true` (re-read before the first write, POLICY
§8), posts each draft whose file carries `approved: true` (a human edit on
the ledger branch, the inbound convention), applies exactly its label plus
`needs-human`, and writes the issue number back into the draft. Phase 3
ships the post job with the variable absent, so it cannot run.

The `competitive-gap`, `competitive-watch`, `competitive-idea` and
`standard-proposal` labels do not exist today; creating them is a human
step in RUNBOOK §10 (labels are on the inbound never-touch list, POLICY
§4.4, and this loop inherits that list).

## 8. Standard feedback

A `standard-proposal` draft is written when, on two consecutive runs, at
least one competitor's median on a COMPARABLE axis is better than
STANDARD.md's stated **Target** for that criterion (never a Floor), and the
row is `meaningful`. The draft names the criterion, the current Target text
verbatim, the competitor's measured value with its spread and release, our
value, and proposes a Target in the same units; it never proposes a Floor,
never edits `STANDARD.md` or `harness/thresholds.json`, and says in its
first line that the standard is edited only by a human. The §2 proposal
(reference-finding recall as a named sub-metric) uses the same label with
`axis: proposed`.

## 9. Schedules, budgets and safety

### 9.1 Jobs

- `competitive-run.yml`: monthly (first Sunday 03:00 UTC, an hour before
  the inbound sweep's window is quiet), `workflow_dispatch` with `reason`
  and an optional `tool` filter. Steps: kill-switch read (`switchtok` +
  `killswitch.py`, byte-for-byte the inbound pattern), budget check
  (`budget.py` gains a row `competitive-run: runs_per_day 1, timeout 240
  min`), corpus and task checks, then per tool: image build (network on),
  run (`--network none`), three repetitions; scoring, findings, ledger push
  by the App; audit record via `ledger.make_record` (`job:
  competitive-run`, `outcome` in the inbound vocabulary); artifacts
  `competitive-result-<run>` and `competitive-audit-<run>`.
- `competitive-feed.yml`: weekly (Sundays 04:00 UTC). Reads each set
  member's latest release through the GitHub API (tag, date, title; the
  body is fetched but only matched, never printed beyond the title), on
  `GITHUB_TOKEN` read-only; compares to `pins.json`; applies §7.1's two
  release rules; writes its audit record. It is the only network read the
  loop makes outside Phase 1, and it reads registries, never READMEs.
- `competitive-post.yml`: as §7.3.
- `/competitive-compare [tool] [ref]` (Phase 3 item 7): the interactive
  form, following `docs/workflows/DESIGN.md` §1 and §8 (header comment,
  evidence under `.claude/state/evidence/`, a git worktree for the ref,
  never a number typed by hand). Runs one tool or all against the working
  tree and a ref, prints the §5 table for the two jcm commits side by side,
  and writes drafts to `.claude/state/competitive/` only, never to the
  ledger.

### 9.2 Budgets

Per tool per run: 20 minutes wall inside the container (`timeout`), image
build 10 minutes; the tool is `not_runnable: timeout` past either. Per full
run: 240 minutes, the workflow's `timeout-minutes`. Runs per day: 1
(`budget.py`, the inbound table gains the row in a PR that edits POLICY §7,
which is a human-reviewed change like any other). Disk: images pruned at
the end of the job. Cost: zero model spend by construction (D1).

### 9.3 Safety

- Container flags as in D2; no environment variable from the runner reaches
  the container except `HOME=/out` and `PATH`; the job holds no secret
  during the run step (the App token is minted only in the later ledger
  step, the inbound pattern).
- Image provenance: base image by digest, package by version and hash where
  the registry supplies one, the built image's digest recorded in the
  result header; a rebuild that produces a different digest for the same pin
  is a FINDING, not a silent update.
- Untrusted input: the inbound preamble (POLICY §4.2) heads every draft
  file so that whoever pastes a draft into a model session carries the
  rule with it; release titles are the only competitor text quoted, and
  they are quoted inside a fenced block labelled `data`. Phase 4's
  fabricated-README test feeds a README through the feed script and the
  adapter build and asserts nothing in it changes any output but a quoted
  title.
- Kill switch: `INBOUND_ENABLED` (POLICY §8), read by the App token first
  in every job and again before the ledger push; the feed job also honours
  it (a stopped layer schedules nothing).
- Never-touch list: POLICY §4.4's list plus `harness/thresholds.json`,
  `harness/corpora.json`, `benchmarks/tasks.json`, `docs/standard/**`,
  `README.md`, `benchmarks/results.md`, `benchmarks/jcm_reference.json`
  and the website; `tests/test_competitive_workflows.py` asserts no
  competitive job has a write step touching any of them (the inbound
  workflow tests are the template).
- Licences: a tool enters the set only under a licence that permits
  running it for evaluation (FIELD §5.1); GitNexus stays out until a human
  records the decision in FINDINGS.

## 10. Phase 4 hooks, so the design is testable as written

Each Phase 4 line maps to a script flag or a test: three runs on one commit
→ the result file's raw triples and `spread`; misconfigured adapter → a
fairness-note field the reviewer diffs against the Dockerfile and the
adapter's config dict, plus `task_check`'s `cited`-empty rule catching a
tool that was silently not called; fabricated README → the feed and build
paths with a fixture README; jcm-only task → `capability_only` exclusion
test; inside/outside band → `findings.py` unit tests over synthetic result
files; de-dup → a fixture open-issue list with the fingerprint; kill switch
→ the inbound workflow tests extended to `competitive-*.yml`; the
skeptical-competitor review → a section of VERIFICATION.md written by hand
per axis, each argument either changing this document or entering
FINDINGS.md as a known limitation.

## 11. What this design does not do

It does not run a model over any output, so it cannot say *why* a gap
exists beyond the fixed hypothesis list; that is `/triage-issue`'s job once
a draft is posted. It does not measure end-to-end task success (SWE-bench
remains parked, `benchmarks/swebench/PROTOCOL.md`). It does not measure
IDE-native indexes (FIELD §4). It does not touch marketing, and the two
stale-fact items in FIELD §3.3 are the human's.
