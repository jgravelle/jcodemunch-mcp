# Capability Reference — the full tour

The [README](README.md) shows the headline capabilities. This document is the complete reference for everything the server does beyond core retrieval: composition tools, structural queries, evidence machinery, session economics, and the advisory annotation layer. For internals and design rationale, see [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md). For per-tool syntax and workflows, see [USER_GUIDE.md](USER_GUIDE.md).

## The one-paragraph capability index

Recent releases have made that retrieval workflow sharper and more useful in real engineering work, with BM25-based symbol search, fuzzy matching, semantic/hybrid search (opt-in, zero mandatory dependencies), query-driven token-budgeted context assembly (`get_ranked_context`), dead code detection (`find_dead_code`), untested symbol detection (`get_untested_symbols`), git-diff-to-symbol mapping (`get_changed_symbols`), architectural centrality ranking (`get_symbol_importance`, PageRank), cold-start orientation maps (`get_repo_map` — query-less, token-budgeted, signature-only repo overview ranked by PageRank), consolidation candidate detection (`find_similar_symbols` — multi-signal duplicate finder blending semantic embeddings, structural signature, and behavioral callee Jaccard; union-find clustering with verdict tiers and PageRank-based canonical-pick), cross-repo API contract surfacing (`get_group_contracts` — group of indexed repos in, ranked shared-symbol contracts out, each classified as de_facto_api / leaky_internal / dead_contract / version_skew with stability + breaking-change history + runtime hits), concrete-implementation discovery (`find_implementations` — multi-source resolution across LSP dispatch / class hierarchy / duck-typed / decorator-handler with confidence scoring), deletion preflight (`check_delete_safe` — composite verdict from importers + references + dead-code + runtime evidence + entry-point heuristics, with ranked blockers and recommended action), edit-safety preflight (`check_edit_safe` — the companion that answers "can I modify this," fusing signature impact, cyclomatic complexity, test-coverage presence, and runtime traffic into a verdict + recommended action), task-aware single-call context orchestration (`assemble_task_context` — natural-language task in, source-attributed context capsule out; auto-classifies into one of six intents with explainable keyword matching, auto-extracts anchor symbols from the task, runs the intent-appropriate sub-tool sequence end-to-end under one token budget), blast-radius depth scoring with source snippets, context bundles with token budgets, AST-derived call graphs and call hierarchy traversal, decorator-aware search and filtering, hotspot detection (complexity x churn), dependency cycles and coupling metrics, session-aware routing (`plan_turn`, turn budgets, negative evidence), agent config auditing, complexity-based model routing (Agent Selector), enforcement hooks (PreToolUse/PostToolUse/PreCompact), dependency graphs, class hierarchy traversal, multi-symbol bundles, live watch-based reindexing, automatic Claude Code worktree discovery (`watch-claude`), registry-wide auto-reindexing with one-command login-service install (`watch-all` + `watch-install` / `watch-uninstall` / `watch-status`; also exposed as MCP tool `get_watch_status`), auto-watch on demand (when `watch: true` in config, the server automatically indexes and watches any repo a tool is called against — ensuring fresh results from the first call), trusted-folder access controls, edit-ready refactoring plans (`plan_refactoring`) for rename, move, extract, and signature change operations, symbol provenance archaeology (`get_symbol_provenance` — full git lineage, semantic commit classification, evolution narrative), unified PR risk profiling (`get_pr_risk_profile` — composite risk score fusing blast radius, complexity, churn, test gaps, and volume), automatic response secret redaction (AWS/GCP/Azure/JWT/GitHub tokens scrubbed before reaching the LLM context window), and cross-language AST pattern matching (`search_ast` — 10 preset anti-pattern detectors + custom mini-DSL for structural queries like `call:*.unwrap`, `string:/password/i`, `nesting:5+`; works across all 70+ languages with universal node-type mapping).

The sections below unpack the pieces that deserve more than a parenthetical.

---

### One-call task orchestration — the tools compose, they don't sit in isolation

The retrieval primitives below are not a disconnected bag of tools the agent has to wire together by hand. Two composition tools drive the rest:

- **`assemble_task_context`** takes a natural-language task and returns a single source-attributed context capsule under a token budget. It auto-classifies the task into one of six intents (explore / debug / refactor / extend / audit / review), auto-extracts the anchor symbols, and runs the intent-appropriate sequence of the tools below end-to-end — so the agent gets the whole context for a task in **one request** instead of chaining five. Every entry is tagged with its `stage` and `source_tool`, so the provenance is auditable.
- **`plan_turn`** routes a broad task: it analyzes the query against the index and returns a confidence-guided route — which tools to call, on which symbols, under a turn budget — *before* the first read. Low confidence means "this probably doesn't exist," so the agent stops instead of burning a budget hunting for a feature that isn't there.
- **`get_ranked_context`** packs the most relevant symbols for a query into a fixed token budget (BM25 + PageRank), when you want a ranked context pack rather than a full intent sequence. Source-shaped identifiers in the query (qualified names, CamelCase, snake_case) pin exact-name symbol matches ahead of the lexical ranking — include the identifier verbatim when you know it; `_meta.query_shape` reports what was recognized and seeded.

The point: jCodeMunch is structured retrieval *with* an orchestration layer over it, not a pile of primitives. The composition tools run the right sub-tools, in the right order, under one budget, in one call.

### Structural queries native tools can't answer

`find_importers` tells you what imports a file. `get_blast_radius` tells you what breaks if you change a symbol, with depth-weighted risk scores and optional source snippets. `get_class_hierarchy` traverses inheritance chains. `get_call_hierarchy` traces callers and callees N levels deep using AST-derived call graphs, with optional LSP-enriched dispatch resolution for interface/trait method calls. `find_dead_code` finds symbols and files unreachable from any entry point. `get_untested_symbols` finds functions with no evidence of test-file reachability — the intersection of import-graph analysis and test-file detection. `get_changed_symbols` maps a git diff to the exact symbols that were added, modified, or removed. `get_symbol_importance` ranks your codebase by architectural centrality using PageRank on the import graph. `get_hotspots` surfaces the riskiest code by combining complexity with git churn. `get_dependency_cycles` detects circular imports. `get_coupling_metrics` measures module coupling and instability. `get_tectonic_map` discovers the logical module topology by fusing three coupling signals (imports, shared references, git co-churn) — revealing hidden module boundaries, misplaced files, and god-module risk without any configuration. `get_signal_chains` traces how external signals (HTTP requests, CLI commands, scheduled tasks, events) propagate through the codebase via the call graph — discovery mode maps all entry-point-to-leaf pathways and reports orphan symbols, lookup mode tells you which user-facing chains a specific symbol participates in (e.g. "validate_email sits on POST /api/users and cli:import-users"). `get_endpoint_impact` answers the endpoint-shaped version of "what breaks if I change X": give it an HTTP endpoint (`GET /users`) or a handler symbol and it resolves the route to its handler — across string-dispatch (Django/Express/Flask/Rails) and decorator routes (Flask/FastAPI/Spring) — then fuses the blast radius (importing files + callers) with the templates that handler renders, in one read-only call mapping a URL to everything a change to it would touch; pass `include_infra=true` and it also crosses the code/infra boundary, surfacing the env vars, compose services, Dockerfiles, CI jobs, and scripts whose project-intel cross-references land in that endpoint's blast radius, plus what exposes the app to the outside world (compose port mappings, K8s Services and Ingresses) — each exposure labelled with its real precision, `host_port` unless an Ingress path rule literally names the route (`ingress_path`). These are not "faster grep" — they are questions grep cannot answer at all.

And the questions don't stop at your own code: `index_dependency` resolves a third-party package to the version *actually installed* in your repo (`node_modules` or a repo-local virtualenv — version read from package metadata, no registry lookup, nothing leaves your machine) and indexes it as its own queryable repo in one call. Your agent stops guessing a library's API from training data and starts reading the exact code it's running against — including compiled npm packages that ship only `dist/` with type declarations.

### Compiler-verified references — no language server required

AST-derived analysis is fast and language-broad, but dynamic dispatch and barrel re-exports can hide references from any static heuristic. `import-scip` closes that gap with evidence instead of guesswork: point it at a SCIP index file — the artifact `scip-typescript`, `scip-python`, `scip-java`, `scip-go`, `rust-analyzer`, and `scip-clang` already emit in CI — and jCodeMunch stores the compiler's own reference and implementation edges alongside the index. `find_references` then labels agreement as `verification: "compiler_verified"` and, more importantly, surfaces the references *only the compiler saw* as additional `source: "scip"` rows. The evidence is honest about its age: results ingested at an older index HEAD carry a `stale` flag and a re-import hint rather than posing as current truth. One command in CI (`scip-typescript index && jcodemunch-mcp import-scip index.scip`), zero language servers running, nothing executed by jCodeMunch itself, and everything stays on your machine. Per-language recipes and the CI ordering are in **[SCIP.md](SCIP.md)**.

### Agent config hygiene

`audit_agent_config` scans your CLAUDE.md, .cursorrules, copilot-instructions.md, and other agent config files for token waste: per-file token cost, stale symbol references (cross-referenced against the index — catches renamed or deleted functions), dead file paths, redundancy between global and project configs, bloat, and scope leaks. No other tool can tell you "line 15 references a function that was renamed three weeks ago."

### Symbol provenance and PR risk profiling

`get_symbol_provenance` is git archaeology: given a symbol, it traces every commit that touched it, classifies each into semantic categories (creation, bugfix, refactor, feature, perf, rename, revert), extracts commit intent, and generates a human-readable narrative explaining who created it, why, and how it evolved. `get_pr_risk_profile` produces a unified risk assessment for a branch or PR — one call fuses blast radius, complexity, churn, test gaps, and change volume into a composite risk score (0.0–1.0) with actionable recommendations. `get_delivery_metrics` quantifies durable-change delivery over a window: of the non-merge commits in the last N days, how many landed and stuck versus were reverted or re-touched (churn-back) within a short horizon — with churn-hub files (CHANGELOG, version, a monolithic dispatch module) excluded from the rework signal so a shared ledger can't masquerade as rework. The durable count is the honest numerator for a cost-per-outcome ratio: pair it with AI spend (the `delivery` CLI takes `--cost`) to show how much got done for how little, instead of rewarding raw activity. All responses are automatically scanned for leaked credentials (AWS keys, JWTs, GCP service accounts, etc.) and redacted before reaching the LLM.

### Cross-language AST pattern matching

`search_ast` brings structural code analysis to every language jCodeMunch indexes — write one query, match across all 70+ languages. **Preset anti-patterns** detect common problems without any configuration: `empty_catch` (silently swallowed errors), `bare_except` (catch-all handlers), `deeply_nested` (5+ control-flow levels), `nested_loops` (O(n³)+ performance risk), `god_function` (100+ line functions), `eval_exec` (injection-risk dynamic execution), `hardcoded_secret` (credential patterns in strings), `todo_fixme` (unfinished work markers), `magic_number` (unexplained numeric constants), and `reassigned_param` (overwritten function parameters). Run `category='all'` for a full sweep, or focus on `security`, `error_handling`, `complexity`, `performance`, or `maintenance`. **Custom queries** use a mini-DSL: `call:*.unwrap` (find method calls by glob), `string:/password/i` (regex over string literals), `comment:/TODO/i` (regex in comments), `nesting:5+`, `loops:3+`, `lines:80+` (threshold queries). Every match is attributed to its enclosing indexed symbol with complexity metadata — so you can see not just *where* the problem is, but *how bad* the surrounding function already is.

### Multi-axis constraint queries

`winnow_symbols` composes signals that every other tool exposes separately — kind, complexity, decorator, direct call references, file glob, name regex, git churn, and PageRank importance — into a single AND-intersected query. Agents stop making four or five calls and merging results by hand: "functions that call `db.Exec`, cyclomatic > 10, churned in the last 30 days, ranked by importance" resolves in one round trip. Supported axes expose their own operator set (`eq`, `in`, `matches`, `contains`, numeric comparisons); the window for churn-based filters is per-criterion. Results include per-symbol importance, complexity, and churn scores so the agent can explain *why* each survivor made the cut.

### Better engineering workflows

Useful for onboarding, debugging, refactoring, impact analysis, and exploring unfamiliar repos without brute-force file reading.

### Refactoring Planner

`plan_refactoring` generates exact edit-ready instructions for rename, move, extract, and
signature change operations. Returns `{old_text, new_text}` blocks compatible with any editor's
find-and-replace, plus import rewrites, collision detection, new file generation, and multi-file coordination.

### Calibrated retrieval signals (v1.74.0+ telemetry initiative)

Every retrieval result now ships with three machine-readable health signals so agents can stop guessing whether to trust the response:

- **`_meta.confidence`** — calibrated 0–1 score combining top-1/top-2 score gap, top-1 strength, identity-match presence, and freshness. Lets an agent gate follow-up `get_symbol_source` calls on a single number.
- **`_freshness ∈ {fresh, edited_uncommitted, stale_index}`** on every result entry, plus a `_meta.freshness` summary. Derived from index SHA vs `git rev-parse HEAD` and per-file mtime checks.
- **Per-tool latency telemetry** (`p50/p95/max/error_rate`) exposed via `get_session_stats.latency_per_tool` and the `analyze_perf` tool. Optional SQLite sink (`~/.code-index/telemetry.db`) for cross-session analysis.
- **`source_status`** on `get_symbol_source` / `get_context_bundle` entries — set when a symbol resolved but its body could not be read (the row lives in the `.db`, the bytes live in a separate content directory, and that directory can be pruned, copied without its sibling, or absent by design in a starter pack). The verdict degrades to `state: "degraded"` with `channels.content_cache: "missing"` and an `unavailable_source_count`, so an empty `source` is never presented as a confident answer. A zero-length body is a different case and is untouched.

The `tune_weights` tool reads the persistent ranking ledger and learns per-repo retrieval weights (saved to `~/.code-index/tuning.jsonc`). `check_embedding_drift` pins a 16-string canary to detect silent provider model changes. `benchmarks/replay/` provides a CI-friendly retrieval-quality regression gate (nDCG/MRR/Recall) that every release runs against.

The `suggest_corrections` tool (and the `reflect` CLI) close the loop: they mine the same ranking ledger for **retrieval regret** — where retrieval failed and the agent had to re-ask — and return a prioritized, explainable set of *suggested* fixes (a CLAUDE.md routing or glossary line as a unified-diff preview, an index-freshness hint, a stale-config finding, a dry-run weight proposal). It is read-only by design: it suggests a patch and shows you the diff; applying it is your keystroke, never the server's. Requires `perf_telemetry_enabled` (it has a ledger to read only then) and returns an honest hint when off.

### Token yield and advisory session budgets

`get_session_stats` speaks the FinOps vocabulary natively:

- **`yield`** — of the context served this session, how much showed downstream follow-through: served search results later fetched via `get_symbol_source`/`get_context_bundle`, or whose file was subsequently edited (`register_edit`/`index_file`). Reports `rate` with its components (`served_results`, `followed_through`) plus `repeated_identical_calls` per tool — the agent's redundant context spend, distinct from cache hits (those measure the server's cost; repeats cost the agent's context window even on a hit). One honest caveat: a search whose result lines answered the question outright has yield the call sequence can't see, which is why `rate` ships with its components and never as a lone grade.
- **`budget`** — set `session_token_budget` (config) to an advisory ceiling over **response tokens served** (the context this server injects into the agent). Once the session crosses 80% of the limit, every response carries `_meta.budget = {limit, spent, state}` in-band — exactly where runaway agent loops live — and `get_session_stats` always reports the block. It never blocks, throttles, or truncates: jCodeMunch is the instrument; hard caps belong to your gateway. `tool_breakdown` sits beside it for per-tool attribution.
- **`estimate_calibration`** — agents systematically underestimate what a plan will cost to execute. Every `plan_turn` call now prices its recommended route (`consumption_estimate = {estimated_tokens, expected_calls, basis}`) and the next `plan_turn` reconciles that estimate against the response tokens actually served in between. After 3 closed samples, the median `actual_vs_estimated` ratio appears in session stats, on the budget block, and back on `plan_turn` itself as `calibrated_tokens` — so "you're at 85% of budget" comes with "and your estimates run 2.4x hot", a calibration receipt instead of a bare forecast.

- **`redelivery_rate`** — how often the session hands over a symbol it already bought. `repeated_identical_calls` only catches byte-identical repeat calls; it cannot see the shape that actually costs, which is the *same symbol* returned under a *different query* (two queries, two argument hashes, one set of bytes paid for twice). A session-scoped delivery ledger reports `redelivered_symbols`, `redelivery_rate`, and `redelivered_tokens_est` beside the yield block, and any response carrying already-delivered symbols gets an advisory `_meta.already_delivered = {count, symbols}`. **Advisory only — nothing is withheld and no response body changes**; you are told you're holding the bytes again, never quietly denied them. Re-showing a signature row is reported but never priced (cheap by construction), and an edit to a file evicts its ledger entries so stale bytes are never announced as already-delivered.

- **`tool_surface`** — what the tool surface itself costs: visible-vs-catalog tool counts, estimated schema tokens for each, `schema_tokens_avoided` by the active surface/tier (the Counter or a narrow profile), and the top-15 heaviest schemas. Counted at the same bytes/4 scale and serialization as the CI schema-budget guardrail, so the runtime receipt and the regression gate agree by construction. Also available with no MCP session as the `jcodemunch-mcp surface [--json]` CLI.

All of these are computed inline from session state — no new background behavior, no network calls, nothing persisted beyond the existing `session_stats.json`.

### Confidence provenance — every number states its basis

Every confidence constant the suite emits traces to a stated basis: **`measured`** (backed by a committed, reproducible benchmark artifact — `benchmarks/provenance/measured.json`, drift-guarded in CI so the constants and the artifact can never silently diverge) or **`declared`** (an engineering prior, honestly labeled as exactly that). `find_implementations` responses carry the per-channel basis in `_meta.confidence_provenance`, and the response contracts themselves are published as JSON Schemas in [`schemas/`](schemas/) (`retrieval-verdict`, `confidence-provenance`, `ranked-context-response`) so CI pipelines and agents can validate responses mechanically. A prior is never presented as a measurement: a `declared` value graduates to `measured` only when a gold-labeled corpus backs it, and a build that claims otherwise fails.

An absence claim is also refused when the ground moved under it. A zero-result scan proves nothing if the index was **being rewritten while the scan read it** — the target may sit in rows written after the scan passed them — so that case reports `degraded` and `channels.index: "rebuilding"` instead of `absent`. This is caught by re-checking the index file itself rather than in-process reindex state, because the rebuild is usually driven by a *separate* watcher process that in-process state cannot see. The rebuild is disclosed on every verdict, not only the refused one: a caller reading a successful result still deserves to know the index moved under it, and only the absence *claim* is withheld.

The verdict survives compaction. jCodeMunch's compact wire format encodes `_meta` through a strict allowlist, and the verdict is carried through it deliberately rather than trimmed for bytes — a safety signal the token-saving layer deletes is no safety signal, and a dropped verdict turns "the scan was degraded" into a confident-looking empty result. That applies to the absence `evidence_ref` too: a proof the server has already recorded stays citable in every response format.

Absence claims carry their own receipts: an `absent` or `degraded` verdict discloses a **coverage contract** — what the corpus *excluded* at index time (unsupported extensions, oversize/binary/secret skips, cap-dropped files, zero-symbol files) plus the index generation it was scanned against — so "scanned N symbols, found nothing" can't lie by omission. An index that predates the contract omits the block: coverage unknown is never presented as "nothing was excluded". Every verdict is also pinned to a `scorer` version, and `benchmarks/calibration/planted_queries.json` records planted positive/negative query rates re-measured live in CI — a scorer change without a re-measured artifact fails the build.

The first gold corpus is in: `benchmarks/goldset/` is an authored implementation-pattern corpus (declared subclasses, duck-typed conformers, decorator-registered handlers — plus deliberate false-positive traps: module-homonym base classes, same-name-different-domain methods, substring decorator matches), fully labeled with per-pair rationale. `benchmarks/goldset/measure.py` re-runs `find_implementations` against it and CI asserts the committed results (`benchmarks/provenance/channel_accuracy.json`) match the live measurement — the numbers literally cannot drift from the reproducible run. Each resolution channel's registry entry now carries its `measured_ref` (precision/recall on the corpus) beside the declared ranking prior, and `_meta.confidence_provenance` surfaces both. Scope is stated in the artifact: authored-pattern discrimination, not in-the-wild base rates.

### Local-first speed

Indexes are stored locally for fast repeated access.

---
---

## Compact output — the second token axis (MUNCH)

Retrieval decides **what** to send. MUNCH decides **how to pack it**.

Every tool response can be emitted in a purpose-built compact wire format
instead of verbose JSON. Path prefixes are interned to short handles,
homogeneous lists of dicts pack into single-character-tagged CSV rows, and
per-column types are preserved so the decode is lossless.

```python
# any tool call accepts format=
find_references(identifier="get_user", format="auto")
# auto  — emit compact if savings ≥ 15%, otherwise JSON
# compact — always compact
# json    — never compact (back-compat passthrough)
```

Benchmark (v1.56.0): median **45.5%** bytes saved across 6 representative
tools, peaks at **55.4%** on graph and outline responses. Full spec in
[SPEC_MUNCH.md](SPEC_MUNCH.md); numbers and harness in
[TOKEN_SAVINGS.md](TOKEN_SAVINGS.md).

Encoding savings stack on top of retrieval savings — every byte off the wire
is a byte the agent doesn't pay to read.

---

---

## Offloadable-work annotation (`JMUNCH_OFFLOADABLE`, off by default)

**jCodeMunch can tell you when the expensive model may be unnecessary.**

When an answer is simple, self-contained, and complete enough to hand to a cheaper model, jCodeMunch marks it as `offloadable`.

That's all it does. It never calls another model, never routes the request, and never touches your API keys. You decide what happens next.

Off by default. Enable it with `JMUNCH_OFFLOADABLE=1`, then:

```python
# swap in your own repo, e.g. "owner/name"
result = get_symbol_source(repo="fastapi/fastapi", symbol_id=symbol_id)

label = (result.get("_meta", {})
               .get("offloadable", {})
               .get("offloadable", "not_evaluated"))

if label == "offloadable":
    answer = cheap_model(question, result["source"])
else:
    # covers "not_offloadable" and "not_evaluated" alike. Anything we
    # didn't approve goes to the good model. That includes the case where
    # the flag is off, so this is safe to ship before you turn it on.
    answer = good_model(question, result["source"])
```

The rest of this section is the detail behind that label.

Set `JMUNCH_OFFLOADABLE=1` (or the per-server `JCODEMUNCH_OFFLOADABLE=1`) and every `get_symbol_source` reply carries an advisory `_meta.offloadable` block saying whether the work that payload enables is grunt-work a cheaper model can do. Nothing is emitted unless you switch it on, and nothing about the answer itself changes when you do.

**We label. We never route, execute, or hold model credentials.** No new process, no network call, no catalog action, and no model of ours ever runs. What to do with the label is entirely the client's decision.

A router that sits in front of the model can only classify the **prompt**, because the prompt is all it can see from there. This sits downstream of retrieval and classifies **the evidence we just assembled** — whether the answer is literally present in the payload, how many containers it spans, whether anything was truncated, and whether any freshness or coverage signal came back unknown.

The verdict is **tri-state and reason-coded**, never a bare score:

```json
"offloadable": {
  "offloadable": "offloadable",
  "qualifiers": ["EXTRACTIVE", "SINGLE_CONTAINER", "IDENTITY_LOOKUP", "NO_ABSENCE_FLAGS", "SELF_CONTAINED"],
  "disqualifiers": [],
  "shape": {"units": 1, "containers": 1},
  "verify_with": {"tool": "check_references", "args": {"identifier": "compute_confidence"}},
  "criterion_version": 2
}
```

`not_evaluated` is not `not_offloadable` — "we did not assess it" and "this is not grunt-work" are different facts, and collapsing them is exactly the failure the tri-state exists to prevent. The criterion **fails closed**: every unknown bearing on the answer disqualifies, because a false `offloadable` sends real work to a model that will confabulate over the gap, while a false `not_offloadable` costs nothing but a missed saving.

`verify_with` names the call that would **adjudicate** a cheaper model's answer over this payload. That is the part that stops the label requiring trust in us: an annotation you cannot check is a vendor assertion, and this one ships next to the tool that checks it.

**What we measured, and what the numbers do not say.** The criterion was run against three pinned corpora — this repository, `fastapi/fastapi` at `a64dfbbd`, and `django/django` — before the label shipped, with the harness in [`benchmarks/offload/`](benchmarks/offload/). Findings worth stating plainly:

- On an index whose freshness cannot be established, **every** payload is refused. Django's index carries no source root, and all 300 sampled identity lookups gated on `TRI_STATE_UNKNOWN`. That is the fail-closed rule doing its job, not a defect.
- The falsifier checks a **necessary** condition — was the ground truth actually present in the payload — never a sufficient one. It cannot tell you a cheap model will answer correctly, only that the information it needs was there. **A pass is an upper bound on achievable accuracy and must not be read as an accuracy figure.**
- The batch arm's rate is a **floor**, not a representative rate: it deliberately scatters its sample across many files, which is close to the worst case for the container rule.

Same field contract in jdocmunch-mcp (sections/documents) and jdatamunch-mcp (columns/datasets) — the vocabulary is *units* and *containers* so all three speak it identically, and a pinned contract digest fails the build in any one of them that drifts.

---

## Runtime identity resource

The server exposes one MCP resource, `munch://runtime/identity` — a read-only `munch.runtime.identity/v1` JSON document identifying this exact server process (`product`, `version`, `transport`, `pid`, OS-derived `process_start`, per-process-lifetime `instance_id`, optional `launch_id` echo of `JCODEMUNCH_LAUNCH_ID` / `MUNCH_LAUNCH_ID`). Multi-agent harnesses use it to tell command-line-identical servers apart and detect restarts. Computed on demand with no disk reads, writes, or network; when the OS process-start probe is unavailable the timestamp is disclosed as `source: "self_recorded"`, never fabricated. Command lines, env, cwd, hostnames, and repo paths are deliberately excluded. Same contract in jdocmunch-mcp and jdatamunch-mcp. Full field reference in [USER_GUIDE.md](USER_GUIDE.md#runtime-identity-resource).

---

## Canonical handoff (`finalize_handoff` + `munch://handoff/<id>`)

A multi-step repository audit can end with one authoritative, server-attested result instead of a client-specific Stop hook. The assistant authors the analysis; `finalize_handoff` takes those sections plus `evidence_refs`, validates every reference against what this session **actually retrieved** (symbol ids or file paths served by `search_symbols` / `get_ranked_context` — unknown refs fail closed with `isError`), deterministically assembles one canonical Markdown handoff (`jcodemunch.handoff/v1`), and returns a compact receipt: `{handoff_id, resource_uri, sha256, length, canonical: true}`. The immutable body is served by the `munch://handoff/<id>` resource — repeated reads are byte-identical. Session-scoped, in-memory, never writes to your repository; appendices appear exactly once; no character limit. `canonical: true` is advisory metadata for clients that support rendering an authoritative MCP resource directly. The server assembles and attests — it never authors conclusions.

## Evidence receipts (`receipt: true` + `munch://evidence/<id>`)

Attesting that a reference was *retrieved this session* is not the same as narrowing *what it proves*. Citing a whole file used to be attested when one unrelated symbol from it was the only thing retrieved, and nothing in the finalized handoff told the two citations apart.

Pass `receipt: true` to `search_symbols`, `get_symbol_source`, `get_ranked_context` or `search_text` and the response carries a receipt **id** in `_meta.receipts`. The receipt itself (`jcodemunch.evidence/v1`) is read from `munch://evidence/<id>` on demand, so opting in costs a handful of bytes. It binds one canonical **subject** — symbol id, file, line range, `content_sha256` — to one **snapshot** (index generation, indexed and live revisions, four-state freshness, working-tree state, coverage digest, scorer pin) and to the **operation that actually ran**, and derives its identity from exactly those three: a different snapshot is a different receipt. A `limitations` list states what the receipt cannot prove — a row served without a body carries an identity, not its bytes.

Cite `munch://evidence/<id>` in a handoff and `finalize_handoff` attests exactly that subject; a file-level reference backed only by a served symbol from that file is then refused as over-broad. A handoff citing no receipts behaves and renders exactly as before, and its receipt now names any broadened reference (`evidence_precision`, `broadened_refs`) instead of leaving a reader unable to tell. Only reviewed producers can mint, so a tool that has not been through that review returns no receipt rather than a weak one. Session-scoped and in memory, like the handoff store. Default `false` is byte-for-byte today's response. No new tool is added.

Where this is going next — Phase 5 (corpus identity) and Phase 6 (typed path witnesses) — is written up in [ROADMAP.md](ROADMAP.md).

---
