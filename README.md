# jCodeMunch MCP

[![MCP Toplist](https://mcptoplist.com/badge/io.github.jgravelle%2Fjcodemunch-mcp.svg)](https://mcptoplist.com/server/io.github.jgravelle%2Fjcodemunch-mcp)

**The leading, most token-efficient MCP server for precise GitHub source code retrieval via tree-sitter AST parsing.** Cut AI token costs **95%+** on code exploration — stop burning your context window reading entire files.

> **Real results, live from production**
> **335B+ tokens saved** · **48,000+ developers** · **$1.69M+ in AI spend avoided** · **40,000+ kg CO₂ prevented**
> Live telemetry at **[jcodemunch.com](https://jcodemunch.com/)** — benchmark: **99.6% average token reduction** (15 tasks / 3 repos, 99.9% peak; run 2026-07-23).

Works with **Autohand Code**, **Claude Code**, **Cursor**, **VS Code**, **Codex CLI**, **Continue**, **Windsurf**, and any MCP-compatible client.

---

#### One-click installs:

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_jCodeMunch-007ACC?style=for-the-badge&logo=visualstudiocode&logoColor=white)](vscode:mcp/install?%7B%22name%22%3A%20%22jcodemunch%22%2C%20%22command%22%3A%20%22uvx%22%2C%20%22args%22%3A%20%5B%22jcodemunch-mcp%22%5D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install-24bfa5?style=for-the-badge&logo=visualstudiocode&logoColor=white)](vscode-insiders:mcp/install?%7B%22name%22%3A%20%22jcodemunch%22%2C%20%22command%22%3A%20%22uvx%22%2C%20%22args%22%3A%20%5B%22jcodemunch-mcp%22%5D%7D)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_jCodeMunch-122122?style=for-the-badge&logo=cursor&logoColor=white)](cursor://anysphere.cursor-deeplink/mcp/install?name=jcodemunch&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyJqY29kZW11bmNoLW1jcCJdfQ==)
[![Claude Code](https://img.shields.io/badge/Claude_Code-CLI_install-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](#works-with)
[![Codex CLI](https://img.shields.io/badge/Codex_CLI-Config_install-10a37f?style=for-the-badge&logo=openai&logoColor=white)](#works-with)

Prefer the command line?

```
pip install jcodemunch-mcp
uvx jcodemunch-mcp
```

For pinned/B2B deployments that want a version-stable install channel independent of PyPI, install straight from the repo (requires `git`, builds from source):

```
pip install git+https://github.com/jgravelle/jcodemunch-mcp.git
uvx --from git+https://github.com/jgravelle/jcodemunch-mcp.git jcodemunch-mcp
```

Quickstart - https://github.com/jgravelle/jcodemunch-mcp/blob/main/QUICKSTART.md

A crapload of detailed info: http://jcodemunch.com/

**Live OSS code-health observatory** — weekly six-axis health snapshots
of Express, FastAPI, Gin, Pydantic, Django, Flask, NestJS, Cobra, and
this very repo: https://jgravelle.github.io/jcodemunch-observatory/

**Token Cost Radar** — daily intelligence on AI token costs, minimization
strategies, and budget trends for teams running Claude Code / Cursor / MCP:
https://jcodemunch.com/radar/

<!-- mcp-name: io.github.jgravelle/jcodemunch-mcp -->
 
 
## FREE FOR PERSONAL USE
**Use it to make money, and Uncle J. gets a taste. Fair enough?** [details](#commercial-licenses)

Our guarantee:  If jCodeMunch doesn't pay for itself, you don't pay for jCodeMunch!

---
 
 
## Cut code-reading token usage by **95% or more** with precise symbol retrieval

Most AI agents explore repositories the expensive way:

open entire files → skim thousands of irrelevant lines → repeat.

That is not “a little inefficient.”
That is a **token incinerator**.

**jCodeMunch indexes a codebase once and lets agents retrieve only the exact code they need**: functions, classes, methods, constants, outlines, and tightly scoped context bundles, with byte-level precision.

In retrieval-heavy workflows, that routinely cuts code-reading token usage by **95%+** because the agent stops brute-reading giant files just to find one useful implementation.

| Task                   | Traditional approach      | With jCodeMunch                             |
| ---------------------- | ------------------------- | ------------------------------------------- |
| Find a function        | Open and scan large files | Search symbol → fetch exact implementation  |
| Understand a module    | Read broad file regions   | Pull only relevant symbols and imports      |
| Explore repo structure | Traverse file after file  | Query outlines, trees, and targeted bundles |

Index once. Query cheaply. Keep moving.
**Precision context beats brute-force context.**

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [QUICKSTART.md](QUICKSTART.md) | Zero-to-indexed in three steps |
| [USER_GUIDE.md](USER_GUIDE.md) | Full tool reference, workflows, and best practices |
| [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md) | **The technical manual** — verdicts, ranking internals, provenance contracts, the meter's design, evidence imports. For developers who want the machinery, not just the workflows |
| [AGENT_HOOKS.md](AGENT_HOOKS.md) | Agent hooks and prompt policies |
| [CONFIGURATION.md](CONFIGURATION.md) | JSONC config file reference, migration from env vars |
| [GROQ.md](GROQ.md) | Groq Remote MCP integration, deployment, gcm CLI |
| [HEADLESS.md](HEADLESS.md) | Using jCodeMunch with `claude -p` (and the jragmunch CLI) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Internal design, storage model, and extension points |
| [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) | Supported languages and parsing details |
| [CONTEXT_PROVIDERS.md](CONTEXT_PROVIDERS.md) | dbt, Git, and custom context provider docs |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common issues and fixes |
| [AGENT_INSTALL_UNIVERSAL.md](AGENT_INSTALL_UNIVERSAL.md) | Paste-and-go prompt for installing jCodemunch guidance into agent/IDE clients without a first-class `jcm install` target (Codex CLI, Cline, JetBrains AI, Aider, etc.). For Claude Code, Cursor, Windsurf, Continue — use `jcm install <client>` instead. |

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

## Structured code retrieval for serious AI agents

<!-- WHATSNEW:START -->
#### What's new

- **[v1.108.154](https://github.com/jgravelle/jcodemunch-mcp/releases/tag/v1.108.154)** (2026-07-21) — `surface` CLI subcommand
- **[v1.108.153](https://github.com/jgravelle/jcodemunch-mcp/releases/tag/v1.108.153)** (2026-07-21) — tool-surface schema receipt in session stats
- **[v1.108.152](https://github.com/jgravelle/jcodemunch-mcp/releases/tag/v1.108.152)** (2026-07-21) — runtime identity resource (#371)
<!-- WHATSNEW:END -->

![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![Polyglot](https://img.shields.io/badge/parsing-tree--sitter-9cf)
![jMRI](https://img.shields.io/badge/jMRI-Full-blueviolet)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20102349.svg)](https://doi.org/10.5281/zenodo.20102349)
[![PyPI version](https://img.shields.io/pypi/v/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)

---

### Mentioned by

- **Artur Skowroński** (VirtusLab) — *"roughly 80% fewer tokens, or 5× more efficient — index once, query cheaply forever"* · [GitHub All-Stars #15](https://virtuslab.com/blog/ai/code-munch-mcp-your-agent-starts-navigating)
- **Julian Horsey** (Geeky Gadgets) — *"3,850 tokens reduced to just 700 — a 5.5× improvement"* · [JCodeMunch AI Token Saver](https://www.geeky-gadgets.com/jcodemunch-mcp-token-savings/)
- **Sion Williams** — *"preserving tokens for tasks that actually require reasoning rather than retrieval"* · [March 2026 AI Workflow Update](https://sionwilliams.com/posts/2026-03-06-ai-workflow-update/)
- **Traci Lim** (AWS · ASEAN AI Lead) — *"structural queries that native tools can't answer: find_importers, get_blast_radius, get_class_hierarchy, find_dead_code"* · [5 Repos That Save Token Usage in Claude Code](https://www.tracilzw.com/posts/5-repos-save-token-usage-claude-code)
- **Eric Grill** — *"context is the scarce resource. Cut it by 90% and the whole stack gets cheaper and more reliable"* · [jCodemunch: Context Engine for AI Agents](https://www.ericgrill.com/blog/jcodemunch-mcp-context-engine-for-ai-agents)

[Full recognition page →](https://jcodemunch.com/recognition.php)

---

> ## Commercial licenses
>
> jCodeMunch-MCP is **free for non-commercial use**.
>
> **Commercial use requires a paid license.**
>
> **jCodeMunch-only licenses**
>
> * [Builder — $79](https://jcodemunch.com/descriptions.php#builder) — 1 developer
> * [Studio — $349](https://jcodemunch.com/descriptions.php#studio) — up to 5 developers
> * [Platform — $1,999](https://jcodemunch.com/descriptions.php#platform) — org-wide internal deployment
>
> **Want the full jMunch suite (code + docs + data)?**
>
> * [Munch Trio Builder Bundle — $99](https://jcodemunch.com/descriptions.php#builder)
> * [Munch Trio Studio Bundle — $449](https://jcodemunch.com/descriptions.php#studio)
> * [Munch Trio Platform Bundle — $2,499](https://jcodemunch.com/descriptions.php#platform)

**Stop paying your model to read the whole damn file.**

jCodeMunch turns repo exploration into **structured retrieval**.

Instead of forcing an agent to open giant files, wade through imports, boilerplate, comments, helpers, and unrelated code, jCodeMunch lets it navigate by **what the code is** and retrieve **only what matters**.

That means:

* **95%+ lower code-reading token usage** in many retrieval-heavy workflows 
* **less irrelevant context** polluting the prompt
* **faster repo exploration**
* **more accurate code lookup**
* **less repeated file-scanning nonsense**

It indexes your codebase once using tree-sitter, stores structured symbol metadata plus byte offsets into the original source, and retrieves exact implementations on demand instead of re-reading entire files over and over.

Recent releases have made that retrieval workflow sharper and more useful in real engineering work, with BM25-based symbol search, fuzzy matching, semantic/hybrid search (opt-in, zero mandatory dependencies), query-driven token-budgeted context assembly (`get_ranked_context`), dead code detection (`find_dead_code`), untested symbol detection (`get_untested_symbols`), git-diff-to-symbol mapping (`get_changed_symbols`), architectural centrality ranking (`get_symbol_importance`, PageRank), cold-start orientation maps (`get_repo_map` — query-less, token-budgeted, signature-only repo overview ranked by PageRank), consolidation candidate detection (`find_similar_symbols` — multi-signal duplicate finder blending semantic embeddings, structural signature, and behavioral callee Jaccard; union-find clustering with verdict tiers and PageRank-based canonical-pick), cross-repo API contract surfacing (`get_group_contracts` — group of indexed repos in, ranked shared-symbol contracts out, each classified as de_facto_api / leaky_internal / dead_contract / version_skew with stability + breaking-change history + runtime hits), concrete-implementation discovery (`find_implementations` — multi-source resolution across LSP dispatch / class hierarchy / duck-typed / decorator-handler with confidence scoring), deletion preflight (`check_delete_safe` — composite verdict from importers + references + dead-code + runtime evidence + entry-point heuristics, with ranked blockers and recommended action), edit-safety preflight (`check_edit_safe` — the companion that answers "can I modify this," fusing signature impact, cyclomatic complexity, test-coverage presence, and runtime traffic into a verdict + recommended action), task-aware single-call context orchestration (`assemble_task_context` — natural-language task in, source-attributed context capsule out; auto-classifies into one of six intents with explainable keyword matching, auto-extracts anchor symbols from the task, runs the intent-appropriate sub-tool sequence end-to-end under one token budget), blast-radius depth scoring with source snippets, context bundles with token budgets, AST-derived call graphs and call hierarchy traversal, decorator-aware search and filtering, hotspot detection (complexity x churn), dependency cycles and coupling metrics, session-aware routing (`plan_turn`, turn budgets, negative evidence), agent config auditing, complexity-based model routing (Agent Selector), enforcement hooks (PreToolUse/PostToolUse/PreCompact), dependency graphs, class hierarchy traversal, multi-symbol bundles, live watch-based reindexing, automatic Claude Code worktree discovery (`watch-claude`), registry-wide auto-reindexing with one-command login-service install (`watch-all` + `watch-install` / `watch-uninstall` / `watch-status`; also exposed as MCP tool `get_watch_status`), auto-watch on demand (when `watch: true` in config, the server automatically indexes and watches any repo a tool is called against — ensuring fresh results from the first call), trusted-folder access controls, edit-ready refactoring plans (`plan_refactoring`) for rename, move, extract, and signature change operations, symbol provenance archaeology (`get_symbol_provenance` — full git lineage, semantic commit classification, evolution narrative), unified PR risk profiling (`get_pr_risk_profile` — composite risk score fusing blast radius, complexity, churn, test gaps, and volume), automatic response secret redaction (AWS/GCP/Azure/JWT/GitHub tokens scrubbed before reaching the LLM context window), and cross-language AST pattern matching (`search_ast` — 10 preset anti-pattern detectors + custom mini-DSL for structural queries like `call:*.unwrap`, `string:/password/i`, `nesting:5+`; works across all 70+ languages with universal node-type mapping).

---

## Real-world results

### Reproducible token efficiency benchmark

Measured with `tiktoken cl100k_base` across three public repos. Workflow: `search_symbols` (top 5) + `get_symbol_source` × 3 per query. Baseline: all source files concatenated (minimum cost for an agent that reads everything). [Full methodology and harness →](benchmarks/METHODOLOGY.md)

| Repository | Files | Symbols | Baseline tokens | jCodeMunch tokens | Reduction |
|------------|------:|--------:|----------------:|------------------:|----------:|
| expressjs/express | 172 | 182 | 143,355 | ~1,040 avg | **99.3%** |
| fastapi/fastapi | 1,000 | 6,722 | 823,784 | ~2,490 avg | **99.7%** |
| gin-gonic/gin | 109 | 1,502 | 192,800 | ~1,510 avg | **99.2%** |
| **Grand total (15 task-runs)** | | | **5,799,695** | **25,220** | **99.6%** |

Per-query results range from 99.1% to 99.9%. The 99.6% figure is the aggregate (run 2026-07-23, v1.108.163, full un-capped indexes). Run `python benchmarks/harness/run_benchmark.py` to reproduce.

### A/B test on production codebase

Independent 50-iteration A/B test on a real Vue 3 + Firebase production codebase — JCodeMunch vs native tools (Grep/Glob/Read), Claude Sonnet 4.6, fresh session per iteration:

| Metric | Native | JCodeMunch |
|--------|--------|------------|
| Success rate | 72% | **80%** |
| Timeout rate | 40% | **32%** |
| Mean cost/iteration | $0.783 | **$0.738** |
| Mean cache creation | 104,135 | **93,178 (−10.5%)** |

Tool-layer savings isolated from fixed overhead: **15–25%.** One finding category appeared exclusively in the JCodeMunch variant: orphaned file detection via `find_importers` — a structural query native tools cannot answer without scripting.

Full report: [`benchmarks/ab-test-naming-audit-2026-03-18.md`](benchmarks/ab-test-naming-audit-2026-03-18.md)

---

## Why agents need this

Most agents still inspect codebases like tourists trapped in an airport gift shop:

* open entire files to find one function
* re-read the same code repeatedly
* consume imports, boilerplate, and unrelated helpers
* burn context window on material they never needed in the first place

jCodeMunch fixes that by giving them a structured way to:

* search symbols by name, kind, or language — with fuzzy matching and optional semantic/hybrid search
* inspect file and repo outlines before pulling source
* retrieve exact symbol implementations only
* grab a token-budgeted context bundle or ranked context pack for a task
* fall back to text search when structure alone is not enough
* detect dead code, trace impact, rank by centrality, and map git diffs to symbols
* plan the next turn with `plan_turn` — confidence-guided routing before the first read
* assemble a whole task's context in one call with `assemble_task_context` — intent-classified, multi-tool, single token budget
* track session state and avoid re-reading files the agent already explored

Agents do not need bigger and bigger context windows.

They need **better aim**.

---

## What you get

### Symbol-level retrieval

Find and fetch functions, classes, methods, constants, and more without opening entire files.

### Faster repo understanding

Inspect repository structure and file outlines before asking for source.

### Lower token spend

Send the model the code it needs, not 1,500 lines of collateral damage.

### One-call task orchestration — the tools compose, they don't sit in isolation

The retrieval primitives below are not a disconnected bag of tools the agent has to wire together by hand. Two composition tools drive the rest:

- **`assemble_task_context`** takes a natural-language task and returns a single source-attributed context capsule under a token budget. It auto-classifies the task into one of six intents (explore / debug / refactor / extend / audit / review), auto-extracts the anchor symbols, and runs the intent-appropriate sequence of the tools below end-to-end — so the agent gets the whole context for a task in **one request** instead of chaining five. Every entry is tagged with its `stage` and `source_tool`, so the provenance is auditable.
- **`plan_turn`** is the opening move: it analyzes the query against the index and returns a confidence-guided route — which tools to call, on which symbols, under a turn budget — *before* the first read. Low confidence means "this probably doesn't exist," so the agent stops instead of burning a budget hunting for a feature that isn't there.
- **`get_ranked_context`** packs the most relevant symbols for a query into a fixed token budget (BM25 + PageRank), when you want a ranked context pack rather than a full intent sequence. Source-shaped identifiers in the query (qualified names, CamelCase, snake_case) pin exact-name symbol matches ahead of the lexical ranking — include the identifier verbatim when you know it; `_meta.query_shape` reports what was recognized and seeded.

The point: jCodeMunch is structured retrieval *with* an orchestration layer over it, not a pile of primitives. The composition tools run the right sub-tools, in the right order, under one budget, in one call.

### Structural queries native tools can't answer

`find_importers` tells you what imports a file. `get_blast_radius` tells you what breaks if you change a symbol, with depth-weighted risk scores and optional source snippets. `get_class_hierarchy` traverses inheritance chains. `get_call_hierarchy` traces callers and callees N levels deep using AST-derived call graphs, with optional LSP-enriched dispatch resolution for interface/trait method calls. `find_dead_code` finds symbols and files unreachable from any entry point. `get_untested_symbols` finds functions with no evidence of test-file reachability — the intersection of import-graph analysis and test-file detection. `get_changed_symbols` maps a git diff to the exact symbols that were added, modified, or removed. `get_symbol_importance` ranks your codebase by architectural centrality using PageRank on the import graph. `get_hotspots` surfaces the riskiest code by combining complexity with git churn. `get_dependency_cycles` detects circular imports. `get_coupling_metrics` measures module coupling and instability. `get_tectonic_map` discovers the logical module topology by fusing three coupling signals (imports, shared references, git co-churn) — revealing hidden module boundaries, misplaced files, and god-module risk without any configuration. `get_signal_chains` traces how external signals (HTTP requests, CLI commands, scheduled tasks, events) propagate through the codebase via the call graph — discovery mode maps all entry-point-to-leaf pathways and reports orphan symbols, lookup mode tells you which user-facing chains a specific symbol participates in (e.g. "validate_email sits on POST /api/users and cli:import-users"). `get_endpoint_impact` answers the endpoint-shaped version of "what breaks if I change X": give it an HTTP endpoint (`GET /users`) or a handler symbol and it resolves the route to its handler — across string-dispatch (Django/Express/Flask/Rails) and decorator routes (Flask/FastAPI/Spring) — then fuses the blast radius (importing files + callers) with the templates that handler renders, in one read-only call mapping a URL to everything a change to it would touch; pass `include_infra=true` and it also crosses the code/infra boundary, surfacing the env vars, compose services, Dockerfiles, CI jobs, and scripts whose project-intel cross-references land in that endpoint's blast radius, plus what exposes the app to the outside world (compose port mappings, K8s Services and Ingresses) — each exposure labelled with its real precision, `host_port` unless an Ingress path rule literally names the route (`ingress_path`). These are not "faster grep" — they are questions grep cannot answer at all.

And the questions don't stop at your own code: `index_dependency` resolves a third-party package to the version *actually installed* in your repo (`node_modules` or a repo-local virtualenv — version read from package metadata, no registry lookup, nothing leaves your machine) and indexes it as its own queryable repo in one call. Your agent stops guessing a library's API from training data and starts reading the exact code it's running against — including compiled npm packages that ship only `dist/` with type declarations.

### Compiler-verified references — no language server required

AST-derived analysis is fast and language-broad, but dynamic dispatch and barrel re-exports can hide references from any static heuristic. `import-scip` closes that gap with evidence instead of guesswork: point it at a SCIP index file — the artifact `scip-typescript`, `scip-python`, `scip-java`, `scip-go`, `rust-analyzer`, and `scip-clang` already emit in CI — and jCodeMunch stores the compiler's own reference and implementation edges alongside the index. `find_references` then labels agreement as `verification: "compiler_verified"` and, more importantly, surfaces the references *only the compiler saw* as additional `source: "scip"` rows. The evidence is honest about its age: results ingested at an older index HEAD carry a `stale` flag and a re-import hint rather than posing as current truth. One command in CI (`scip-typescript index && jcodemunch-mcp import-scip index.scip`), zero language servers running, nothing executed by jCodeMunch itself, and everything stays on your machine.

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

Absence claims carry their own receipts: an `absent` or `degraded` verdict discloses a **coverage contract** — what the corpus *excluded* at index time (unsupported extensions, oversize/binary/secret skips, cap-dropped files, zero-symbol files) plus the index generation it was scanned against — so "scanned N symbols, found nothing" can't lie by omission. An index that predates the contract omits the block: coverage unknown is never presented as "nothing was excluded". Every verdict is also pinned to a `scorer` version, and `benchmarks/calibration/planted_queries.json` records planted positive/negative query rates re-measured live in CI — a scorer change without a re-measured artifact fails the build.

The first gold corpus is in: `benchmarks/goldset/` is an authored implementation-pattern corpus (declared subclasses, duck-typed conformers, decorator-registered handlers — plus deliberate false-positive traps: module-homonym base classes, same-name-different-domain methods, substring decorator matches), fully labeled with per-pair rationale. `benchmarks/goldset/measure.py` re-runs `find_implementations` against it and CI asserts the committed results (`benchmarks/provenance/channel_accuracy.json`) match the live measurement — the numbers literally cannot drift from the reproducible run. Each resolution channel's registry entry now carries its `measured_ref` (precision/recall on the corpus) beside the declared ranking prior, and `_meta.confidence_provenance` surfaces both. Scope is stated in the artifact: authored-pattern discrimination, not in-the-wild base rates.

### Local-first speed

Indexes are stored locally for fast repeated access.

---

## How it works

jCodeMunch indexes local folders or GitHub repos, parses source with tree-sitter, extracts symbols, and stores structured metadata alongside raw file content in a local index. Each symbol includes enough information to be found cheaply and retrieved precisely later. 

That includes metadata like:

* signature
* kind
* qualified name
* one-line summary
* byte offsets into the original file

So when the agent wants a symbol, jCodeMunch can fetch the exact source directly instead of loading and rescanning the full file.

---

## Background behavior, fully disclosed

Everything jCodeMunch does beyond answering a tool call is listed here. All of it is visible, opt-in or opt-out, and reversible.

- **File watching.** The `watch` / `watch-all` / `watch-claude` commands (and `watch: true` in config) re-index files when they change. Watching runs **inside a process you started** and stops when that process exits. Nothing monitors your filesystem unless a jCodeMunch process you launched is running.
- **Login service — explicit opt-in only.** `jcodemunch-mcp watch-install` registers `watch-all` as a login service (Windows Task Scheduler / macOS launchd / Linux systemd) so indexes stay fresh across reboots. This happens **only** when you run `watch-install` yourself; `init`, `install`, and normal server use never register a service. Inspect it with `watch-status`; remove it with `watch-uninstall`.
- **Anonymous savings telemetry.** The server periodically sends a random anonymous ID plus aggregate token-savings counters to the project's public community meter. No code, no file paths, no repo names, no PII — counters only. The sender is a single background daemon thread that starts lazily on the first share (never at import, and never if you have opted out), so a plain import has no background side effect. Opt out with `share_savings: false` in `config.jsonc` or `JCODEMUNCH_SHARE_SAVINGS=0`; redirect the endpoint with `JCODEMUNCH_TELEMETRY_URL`.
- **Agent hooks.** `init` / `install` can write hook entries (auto-reindex on edit, read-interception nudges) into your MCP client's settings. They're offered during the interactive flow, shown before writing, and fully removed by `uninstall`.
- **Local index storage.** Indexes live at `~/.code-index/` (override with `CODE_INDEX_PATH`). Delete the directory and every trace of indexing is gone.
- **Live session journal.** While the server runs, it periodically writes a small `_session_live.json` in `~/.code-index/` recording the files and searches the agent touched this session (paths and query strings only, no file contents). It exists so the out-of-process PreCompact hook can restore session orientation after context compaction. Throttled, atomically written, overwritten in place; disable with `JCODEMUNCH_LIVE_JOURNAL=0`.
- **User-invoked network calls.** A few commands you run explicitly reach the network. None run in the background or fire on a plain import; each happens only when you invoke the command:
  - **License validation.** `license`, `org-rollup`, and `install-pack --license` send your license key to `validate.php` on `j.gravelle.us` to confirm it. The key travels in the request body / a header, never the URL, so it can't land in intermediary access logs. This gates only the team `org-rollup` feature; the individual tools never call it.
  - **Starter-pack download.** `install-pack` fetches the pack catalog and any pre-built index pack you request from `j.gravelle.us` (a premium pack also sends your license key).
  - **Embedding-model download.** `download-model` — and the first semantic encode when the `[local-embed]` extra is installed — downloads the ONNX model (`all-MiniLM-L6-v2`, ~23 MB, one time) from `huggingface.co`; after that, semantic search needs no network.

Beyond the user-invoked calls listed above, the base package makes no other network calls and leaves no other persistent processes. AI-summary extras call their configured provider's API only when you enable them — see the extras matrix under [Start fast](#start-fast).

---

## Runtime identity resource

The server exposes one MCP resource, `munch://runtime/identity` — a read-only `munch.runtime.identity/v1` JSON document identifying this exact server process (`product`, `version`, `transport`, `pid`, OS-derived `process_start`, per-process-lifetime `instance_id`, optional `launch_id` echo of `JCODEMUNCH_LAUNCH_ID` / `MUNCH_LAUNCH_ID`). Multi-agent harnesses use it to tell command-line-identical servers apart and detect restarts. Computed on demand with no disk reads, writes, or network; when the OS process-start probe is unavailable the timestamp is disclosed as `source: "self_recorded"`, never fabricated. Command lines, env, cwd, hostnames, and repo paths are deliberately excluded. Same contract in jdocmunch-mcp and jdatamunch-mcp. Full field reference in [USER_GUIDE.md](USER_GUIDE.md#runtime-identity-resource).

---

## Canonical handoff (`finalize_handoff` + `munch://handoff/<id>`)

A multi-step repository audit can end with one authoritative, server-attested result instead of a client-specific Stop hook. The assistant authors the analysis; `finalize_handoff` takes those sections plus `evidence_refs`, validates every reference against what this session **actually retrieved** (symbol ids or file paths served by `search_symbols` / `get_ranked_context` — unknown refs fail closed with `isError`), deterministically assembles one canonical Markdown handoff (`jcodemunch.handoff/v1`), and returns a compact receipt: `{handoff_id, resource_uri, sha256, length, canonical: true}`. The immutable body is served by the `munch://handoff/<id>` resource — repeated reads are byte-identical. Session-scoped, in-memory, never writes to your repository; appendices appear exactly once; no character limit. `canonical: true` is advisory metadata for clients that support rendering an authoritative MCP resource directly. The server assembles and attests — it never authors conclusions.

---

## Start fast

> **Ubuntu 24.04+ / Debian 12+:** System Python is externally managed (PEP 668).
> Use `pipx install jcodemunch-mcp` or `uv tool install jcodemunch-mcp` instead
> of bare `pip install`.

### Option A: One command (recommended)

```bash
pip install jcodemunch-mcp
jcodemunch-mcp init
```

`init` auto-detects your MCP clients (Claude Code, Claude Desktop, Cursor, Windsurf, Continue), writes their config entries, installs the CLAUDE.md prompt policy so your agent actually uses jCodeMunch, optionally installs enforcement hooks (PreToolUse read guard + PostToolUse auto-reindex + PreCompact session snapshot), optionally indexes your project, and audits your agent config files for token waste. Run `jcodemunch-mcp init --help` for all flags.

> **Prefer a one-line CLAUDE.md?** From v1.71.0 the server exposes a
> `jcodemunch_guide` tool that returns the same policy snippet `claude-md
> --generate` prints — with the running version embedded. Keep this single
> line in your CLAUDE.md / AGENT.md and the guide always matches the installed
> server:
>
> ```markdown
> Call the jcodemunch_guide tool and strictly follow its instructions.
> ```
>
> The tool is force-included, so it can't be hidden by `disabled_tools` or
> tier filtering.

For non-interactive CI or scripting:

```bash
jcodemunch-mcp init --yes --claude-md global --hooks --index --audit
```

### Option B: Manual setup

#### 1. Install it

```bash
pip install jcodemunch-mcp
```

> **Want semantic search?** Install the local embedding extra for zero-config
> semantic search — no API keys, no internet after first download:
>
> ```bash
> pip install "jcodemunch-mcp[local-embed]"  # bundled ONNX encoder (recommended)
> jcodemunch-mcp download-model              # fetch model (~23 MB, one-time)
> ```
>
> **Want AI-generated summaries?** Install the extra for your provider:
>
> ```bash
> pip install "jcodemunch-mcp[anthropic]"   # Claude
> pip install "jcodemunch-mcp[gemini]"      # Gemini
> pip install "jcodemunch-mcp[openai]"      # OpenAI-compatible
> pip install "jcodemunch-mcp[all]"         # all providers + local embeddings
> ```
>
> Without an extra, summaries fall back to signatures (which still works — you
> just get shorter descriptions). Run `jcodemunch-mcp config --check` to verify
> your provider is installed and working.

<details>
<summary><strong>Extras matrix — system surfaces each extra pulls in</strong></summary>

Most extras are pure-Python and self-contained. A few pull libraries that touch
system surfaces worth noting for managed-endpoint and SOC 2 / HIPAA-adjacent
deployments. For the base package alone, none of these surfaces are introduced.

| Extra | Transitive dependencies of note | System surfaces |
|---|---|---|
| (base, no extra) | none | none |
| `[local-embed]` | `onnxruntime` | local CPU inference (no network after model download); model fetched on first run |
| `[anthropic]` | `anthropic` SDK | outbound HTTPS to `api.anthropic.com` when AI summaries are enabled |
| `[gemini]` | `google-generativeai` | outbound HTTPS to Google AI endpoints when AI summaries are enabled |
| `[openai]` | `openai` SDK | outbound HTTPS to `api.openai.com` (or `OPENAI_API_BASE`) when AI summaries are enabled |
| `[groq]` | `openai` SDK | outbound HTTPS to Groq endpoints; used by the `gcm` CLI and speedreview Action |
| `[groq-voice]` | `sounddevice`, `numpy` | **microphone access** — `sounddevice.InputStream` opens the system audio device when the voice path is invoked |
| `[groq-explain]` | `Pillow` | image decode / re-encode of attached screenshots |
| `[all]` | union of all the above | union of all surfaces above, including microphone (`[groq-voice]`) and image libraries (`[groq-explain]`) |

For managed-endpoint deployments where microphone access on developer machines
is policy-restricted (HIPAA, SOC 2, finance), pin to the base package or to the
specific provider extras you need. The voice and explain paths are opt-in
features, not part of the core MCP server functionality, and `[all]` is the
only extra that bundles them together.

</details>

#### Upgrading

Upgrade through whatever installed it, then restart your editor / MCP client so
it relaunches the server on the new version:

```bash
pip install -U jcodemunch-mcp          # pip
pipx upgrade jcodemunch-mcp            # pipx
uv tool upgrade jcodemunch-mcp        # uv tool
uvx jcodemunch-mcp@latest --help      # uvx (forces a cache refresh)
```

Check what you're running with `jcodemunch-mcp --version`.

> **Prefer not to touch the command line?** [jMunch Console](https://github.com/jgravelle/jmunch-console)
> (free, MIT, opt-in) makes upgrades one click — it shows an "update available"
> badge when a newer release exists and runs the install for you in a visible
> terminal (uv / pipx / pip, whichever you have), so you never copy a command.

#### 2. Add it to your MCP client

If you’re using Claude Code, pick whichever matches what you installed in step 1.

**Pip install (simplest, what most people do):**

```bash
claude mcp add -s user jcodemunch jcodemunch-mcp
```

The `-s user` flag registers it at user scope so it's available in every
project. Without it, the registration is project-local and you'll see it
missing the next time you `cd` elsewhere. If `jcodemunch-mcp` isn't found
on PATH (common on Windows where `pip install --user` installs to
`AppData\Roaming\Python\PythonXYZ\Scripts\`), use the absolute path:

```bash
# Windows
claude mcp add -s user jcodemunch "C:\Users\YOU\AppData\Roaming\Python\Python312\Scripts\jcodemunch-mcp.exe"
# macOS/Linux — check `which jcodemunch-mcp` first
claude mcp add -s user jcodemunch "$(which jcodemunch-mcp)"
```

**uvx (no pip install required, but uv must be on PATH):**

```bash
claude mcp add -s user jcodemunch uvx jcodemunch-mcp
```

> **`'uvx' is not recognized` (Windows / Cursor / any client)?** `uvx` ships with [uv](https://docs.astral.sh/uv/). Install it with `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then **fully restart your editor** so it picks up the new PATH. To skip uv entirely: `python -m pip install` the package and launch with `command: "python"`, `args: ["-m", "jcodemunch_mcp"]` instead.

If `/mcp` reports `failed` with no reason, run `claude --mcp-debug` or
check `%USERPROFILE%\AppData\Roaming\Claude\logs\mcp*.log` — the `/mcp`
summary hides the actual error.

If you’re using **Paperclip** (the multi-agent orchestration platform), add a `.mcp.json` to your workspace root:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "type": "stdio",
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    },
    "jdocmunch": {
      "type": "stdio",
      "command": "uvx",
      "args": ["jdocmunch-mcp"]
    }
  }
}
```

Paperclip’s Claude Code agents auto-detect `.mcp.json` at startup. Add both servers to give your agents symbol search + doc navigation without blowing the token budget.

#### 3. Tell your agent to actually use it

This matters more than people think.

Installing jCodeMunch makes the tools available. It does **not** guarantee the agent will stop its bad habit of brute-reading files unless you instruct it to prefer symbol search, outlines, and targeted retrieval. The changelog specifically calls out improved onboarding around this because it is a real source of confusion for first-time users. 

A simple instruction like this helps:

```markdown
Use jcodemunch-mcp for code lookup whenever available. Prefer symbol search, outlines, and targeted retrieval over reading full files.
```

> **Note:** `jcodemunch-mcp init` handles steps 2 and 3 automatically. For a comprehensive guide on enforcing these rules through agent hooks and prompt policies, see [AGENT_HOOKS.md](AGENT_HOOKS.md).

---

## Starter Packs

Pre-built indexes for popular frameworks and libraries. Skip the initial indexing step — install a pack and start querying immediately.

```bash
# List available packs
jcodemunch-mcp install-pack --list

# Install a free pack
jcodemunch-mcp install-pack fastapi

# Install a licensed pack
jcodemunch-mcp install-pack express --license YOUR-KEY
```

Free packs require no license. Licensed packs require a [jCodeMunch license](https://jcodemunch.com/#pricing). Use `--force` to re-download an already-installed pack.

---

## Groq Integration

Use jCodeMunch as a remote MCP tool with [Groq's](https://groq.com) ultra-fast inference — answer codebase questions in seconds with zero local setup.

```python
from openai import OpenAI

client = OpenAI(api_key="YOUR_GROQ_KEY", base_url="https://api.groq.com/openai/v1")

response = client.responses.create(
    model="llama-3.3-70b-versatile",
    input="What does parse_file do in jgravelle/jcodemunch-mcp?",
    tools=[{
        "type": "mcp",
        "server_label": "jcodemunch",
        "server_url": "https://YOUR_JCODEMUNCH_URL",
        "headers": {"Authorization": "Bearer YOUR_TOKEN"},
        "server_description": "Code intelligence via tree-sitter AST parsing.",
        "require_approval": "never",
    }],
)
```

Groq handles MCP tool discovery and execution server-side — one API call, no orchestration needed.

Self-host with Docker + Caddy for auto-TLS:

```bash
DOMAIN=mcp.example.com JCODEMUNCH_HTTP_TOKEN=secret docker compose up -d
```

See **[GROQ.md](GROQ.md)** for the full tutorial: allowed-tools presets, model recommendations, deployment options, and validation scripts.

### speedreview — AI Code Review GitHub Action

Get a structured PR review in under 5 seconds:

```yaml
# .github/workflows/speedreview.yml
- uses: jgravelle/jcodemunch-mcp/speedreview@v1.108.52
  with:
    groq_api_key: ${{ secrets.GROQ_API_KEY }}
```

For stricter supply-chain hygiene, pin to the tag's commit SHA instead of the
tag itself (`git ls-remote https://github.com/jgravelle/jcodemunch-mcp refs/tags/v1.108.52`).
The action installs pinned package versions by default and exposes
`jcodemunch_version` / `openai_version` inputs for override.

See **[speedreview/README.md](speedreview/README.md)** for full setup and configuration.

### gcm — Codebase Q&A CLI

Ask any question about any codebase. Get an answer in under 3 seconds.

```bash
pip install jcodemunch-mcp[groq]
export GROQ_API_KEY=gsk_...

# Ask about a GitHub repo (auto-indexes on first use)
gcm "how does authentication work?" --repo pallets/flask

# Ask about the current directory
gcm "where are the API routes defined?"

# Interactive chat mode
gcm --chat --repo facebook/react

# Use the fast 8B model
gcm "what does parse_file do?" --fast
```

Combines jCodeMunch's token-efficient retrieval (BM25 + PageRank) with Groq's 280+ tok/s inference for near-instant answers. See `gcm --help` for all options.

### gcm --voice — Voice-to-Codebase

Speak a question, hear the answer. Full audio loop: Whisper STT → retrieval → LLM → Orpheus TTS.

```bash
pip install jcodemunch-mcp[groq-voice]

# Voice conversation with a codebase
gcm --voice --repo pallets/flask

# Press Enter to start recording, Enter again to stop
# Or type a question directly as text fallback
```

Push-to-talk via Enter key. Caps answers to ~100 words for natural spoken delivery. Requires a microphone.

### gcm explain — Auto Repo Explainer

Generate a narrated explainer video for any codebase in a single command.

```bash
pip install jcodemunch-mcp[groq-explain]

# Generate a 60-second narrated explainer
gcm explain --repo pallets/flask -o flask-explainer.mp4

# With verbose timing
gcm explain --repo facebook/react -v
```

Pipeline: repo structure → LLM narration script → Orpheus TTS → Pillow slides → FFmpeg MP4. Requires FFmpeg on PATH.

---

## Configuration

Settings are controlled by a JSONC config file (`config.jsonc`) with env var fallbacks for backward compatibility. Defaults are chosen so that a fresh install works without any configuration.

### Quick setup

```bash
jcodemunch-mcp config --init       # create ~/.code-index/config.jsonc from template
jcodemunch-mcp config              # show effective configuration
jcodemunch-mcp config --check      # validate config + verify prerequisites
```

`--check` validates that your config file is well-formed, your AI provider package is installed, your index storage path is writable, and HTTP transport packages are present. Exits non-zero on any failure — useful for CI/CD or first-run scripts.

### Config file locations

| Layer | Path | Purpose |
|-------|------|---------|
| Global | `~/.code-index/config.jsonc` | Server-wide defaults |
| Project | `{project_root}/.jcodemunch.jsonc` | Per-project overrides |

Project config merges over global config — closest to the work wins.

### Token-control levers (reduce schema tokens per turn)

| Config key | What it controls | Typical savings |
|-----------|-----------------|----------------|
| `tool_profile` | `"core"` (16 tools), `"standard"` (51), `"full"` (62, default) | ~5-6k tokens (core) |
| `compact_schemas` | Strip rarely-used advanced params from schemas | ~1-2k tokens |
| `disabled_tools` | Remove individual tools from schema entirely | ~100–400 tokens/tool |
| `languages` | Shrink language enum + gate features | ~2–86 tokens/turn |
| `meta_fields` | Filter `_meta` response fields | ~50–150 tokens/call |
| `descriptions` | Control description verbosity | ~0–600 tokens/turn |

**Recommended for context-conscious setups:** `"tool_profile": "core", "compact_schemas": true` reduces the schema footprint from ~11.5k tokens to ~4k tokens.

See the full template for all available keys. Run `jcodemunch-mcp config --init` to generate one.

### Tool Tiering

jcodemunch-mcp exposes 60+ tools. On request-capped plans, having all of them visible to small models causes primitive-preference bias (many `search → read → search → read` cycles instead of one `get_context_bundle`). The server mitigates this by narrowing the exposed tool list per the running model.

#### Tiers (configurable)

Three tiers ship with sensible defaults, fully editable in `config.jsonc`:

- `core` (16 tools): indexing, search, retrieval. Recommended for Haiku / small local models.
- `standard` (51 tools): core + analytics / architecture / quality. Recommended for Sonnet / GPT-4o class.
- `full` (all 62 tools): no filter. Recommended for Opus / o1 / frontier models.

Edit `tool_tier_bundles.core` / `tool_tier_bundles.standard` in your `config.jsonc` to add or remove tools from each tier.

#### Runtime switching (opt-in, zero extra requests)

Runtime tier switching is **off by default**. To enable it, set in `config.jsonc`:

```jsonc
"adaptive_tiering": true
```

When on, `plan_turn` — already the opening-move tool — accepts an optional `model` parameter that switches the session tier as a side effect, with **no extra MCP request**:

```
plan_turn(repo="...", query="...", model="claude-haiku-4-5")
```

The server resolves the model to a tier via `model_tier_map` in config (fuzzy matching: normalizes the id, then exact → glob → substring → `*` → `full` fallback). Subsequent `tools/list` calls return only the narrowed set.

When `adaptive_tiering` is false, `plan_turn(model=...)` and `announce_model(...)` accept their arguments but do not switch the tier — the static `tool_profile` continues to drive the exposed tools. `set_tool_tier(tier=...)` remains honored either way because it's an explicit user call, not automatic behavior.

#### The Counter — collapse to a 3-tool front door (`tool_surface`)

`tool_surface` goes further than tiering: it collapses the resident tool list to a **three-tool front door** that fronts the entire catalog without losing any capability. Every turn the host serializes each resident tool's schema into context; the front door shrinks that fixed per-turn cost (≈21.6k → ≈0.9k tokens, ~24×) and removes the "pick one of N" dispatch dilution.

**New installs default to `counter`** so a first-time user gets maximal token savings out of the box, with every tool one `menu()` / `route()` call away. This default is applied only to a genuinely first-ever install: an **existing install keeps its surface across upgrades** — if you never set `tool_surface`, a package update leaves you on `full` exactly as before, never silently collapsing your tool list. Set it explicitly any time:

```jsonc
"tool_surface": "counter"
```

- **`order(action, args)`** — dispatch any catalog action by name. Read-only by default at the boundary: actions that change index/session state require `allow_state_change=true`, and execution/file-write verbs are refused outright (the front door is a charter checkpoint, never a mutation path).
- **`menu(query?)`** — search/browse the action catalog (compact rows: action, summary, required args, `state_changing`), so the full set of schemas needn't stay resident in context.
- **`route(task, repo?, execute?)`** — map a natural-language task to the best action(s); with `execute=true`, dispatch the top recommendation in the same call. Recommends `assemble_task_context` / `plan_turn` for context-gathering intents.

`counter` keeps the always-present controls (`set_tool_tier`, `announce_model`, `jcodemunch_guide`) alongside the front door. Setting `full` advertises every tool schema (the pre-`counter` behavior). The two mechanisms compose: under `counter`, `order` / `route` still reach every action regardless of the active `core` / `standard` / `full` tier. Seeing only three tools in your client's tool list is expected under `counter` — call `menu()` to list the full catalog.

#### `disabled_tools` precedence

`disabled_tools` applies **after** tier filtering. A tool listed in both a tier bundle and `disabled_tools` will not be exposed. The server logs a `WARNING` on startup and `jcodemunch-mcp config --check` prints a `WARN:` row if this happens.

### Architecture layer enforcement (`architecture.layers`)

Place a `.jcodemunch.jsonc` file at your project root to declare the layers your architecture must respect. `get_layer_violations` will then enforce that imports only flow in the declared direction.

```jsonc
// .jcodemunch.jsonc — example for a layered Python project
{
  "architecture": {
    "layers": [
      { "name": "api",      "paths": ["src/routes", "src/controllers"] },
      { "name": "service",  "paths": ["src/services"] },
      { "name": "repo",     "paths": ["src/repositories"] },
      { "name": "db",       "paths": ["src/models", "src/migrations"] }
    ],
    "rules": [
      { "layer": "api",     "may_not_import": ["db"] },
      { "layer": "service", "may_not_import": ["api"] },
      { "layer": "repo",    "may_not_import": ["api", "service"] }
    ]
  }
}
```

Call `get_layer_violations(rules=[...])` directly to pass rules inline — the config file is optional and used as a fallback. When no config is present, `get_layer_violations` infers layers from top-level directory structure.

### Deprecated env vars (v2.0 will remove)

The following env vars still work but are deprecated. Config file values take priority:

| Variable | Config key | Default |
|----------|-----------|---------|
| `JCODEMUNCH_USE_AI_SUMMARIES` | `use_ai_summaries` | `true` |
| `JCODEMUNCH_TRUSTED_FOLDERS` | `trusted_folders` | `[]` |
| `JCODEMUNCH_MAX_FOLDER_FILES` | `max_folder_files` | `2000` |
| `JCODEMUNCH_MAX_INDEX_FILES` | `max_index_files` | `10000` |
| `JCODEMUNCH_STALENESS_DAYS` | `staleness_days` | `7` |
| `JCODEMUNCH_MAX_RESULTS` | `max_results` | `500` |
| `JCODEMUNCH_EXTRA_IGNORE_PATTERNS` | `extra_ignore_patterns` | `[]` |
| `JCODEMUNCH_CONTEXT_PROVIDERS` | `context_providers` | `true` |
| `JCODEMUNCH_REDACT_SOURCE_ROOT` | `redact_source_root` | `false` |
| `JCODEMUNCH_STATS_FILE_INTERVAL` | `stats_file_interval` | `3` |
| `JCODEMUNCH_SHARE_SAVINGS` | `share_savings` | `true` |
| `JCODEMUNCH_TELEMETRY_URL` | (none) | community meter URL |
| `JCODEMUNCH_SUMMARIZER_CONCURRENCY` | `summarizer_concurrency` | `4` |
| `JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER` | `allow_remote_summarizer` | `false` |
| `JCODEMUNCH_RATE_LIMIT` | `rate_limit` | `0` |
| `JCODEMUNCH_TRANSPORT` | `transport` | `stdio` |
| `JCODEMUNCH_HOST` | `host` | `127.0.0.1` |
| `JCODEMUNCH_PORT` | `port` | `8901` |
| `JCODEMUNCH_LOG_LEVEL` | `log_level` | `WARNING` |

AI provider keys (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_BASE`, `MINIMAX_API_KEY`, `ZHIPUAI_API_KEY`, etc.), `JCODEMUNCH_SUMMARIZER_PROVIDER`, and `CODE_INDEX_PATH` are **always** read from env vars — they are never placed in config files.

AI provider priority in auto-detect mode: Anthropic → Gemini → OpenAI-compatible (`OPENAI_API_BASE`) → MiniMax → GLM-5 → signature fallback. Set `JCODEMUNCH_SUMMARIZER_PROVIDER` to force `anthropic`, `gemini`, `openai`, `minimax`, `glm`, or `none`. `jcodemunch-mcp config` shows which provider is active.

`allow_remote_summarizer` only affects OpenAI-compatible HTTP endpoints. When `false`, jcodemunch accepts only localhost-style endpoints such as Ollama or LM Studio on `127.0.0.1` and rejects remote hosts like `api.minimax.io`. When a remote endpoint is rejected, AI summarization falls back to docstrings or signatures instead of sending source code to that provider. Set `allow_remote_summarizer: true` in `config.jsonc` if you intentionally want to use a hosted OpenAI-compatible provider such as MiniMax or GLM-5.

`openai_extra_body` (config key, or `JCODEMUNCH_OPENAI_EXTRA_BODY` env var as a JSON object) is merged into every OpenAI-compatible `/chat/completions` and `/responses` summarizer request. Use it for provider knobs the standard payload doesn't expose — most commonly to turn off a local **thinking model's** reasoning so the output budget isn't spent on reasoning tokens (which silently degrades summaries to generic signatures). For llama.cpp / Qwen: `JCODEMUNCH_OPENAI_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}'`. When a summarization run produces mostly generic fallbacks despite successful responses, jcodemunch now logs a degradation warning pointing at this setting (issue #323).

---

## When does it help?

A common question: does this only help during exploration, or also when the agent is prompted to read a file before editing?

**It helps most when editing a specific function.** The "read before edit" constraint doesn't require reading the whole file — it requires reading the code. `get_symbol_source` gives you exactly the function body you're about to touch, nothing else. Instead of reading 700 lines to edit one method, you read those 30 lines.

| Scenario | Native tool | jCodemunch | Savings |
|----------|-------------|------------|---------|
| Edit one function (700-line file) | `Read` → 700 lines | `get_symbol_source` → 30 lines | ~95% |
| Understand a file's structure | `Read` → full content | `get_file_outline` → names + signatures | ~80% |
| Find which file to edit | `Grep` many files | `search_symbols` → exact match | comparable |
| Edit requires whole-file context | `Read` → full content | `get_file_content` → full content | ~0% |
| "What breaks if I change X?" | not possible | `get_blast_radius` | unique capability |

The cases where it doesn't help: edits that genuinely require understanding the entire file (restructuring file-level state, reordering logic that spans hundreds of lines). For those, `get_file_content` is roughly equivalent to `Read`. The cases where it helps most are targeted edits — one function, one method, one class — which is the majority of real editing work.

---

## Best for

* large repositories
* unfamiliar codebases
* agent-driven code exploration
* refactoring and impact analysis
* teams trying to cut AI token costs without making agents dumber
* developers who are tired of paying premium rates for glorified file scrolling

---

## New here?

Start with **[QUICKSTART.md](QUICKSTART.md)** for the fastest setup path.

Then index a repo, ask your agent what it has indexed, and have it retrieve code by symbol instead of reading entire files. That is where the savings start.

## Works with

jCodeMunch is an MCP server — it plugs into **every major agent and IDE that speaks MCP**:

**Claude Code · Claude Desktop · Cursor · Windsurf · Codex CLI · Continue · Cline · Roo Code · Zed · Goose · Hermes Agent · Paperclip · Gemini CLI · Qwen Code · Kiro** — and more.

Tested configurations:

| Platform | Config |
|----------|--------|
| **Autohand Code** | `autohand mcp add jcodemunch uvx jcodemunch-mcp` ([CLI details](https://github.com/autohandai/code-cli/)); add `--scope project` before `jcodemunch` for project configuration |
| **Claude Code / Claude Desktop** | `jcodemunch-mcp init` (auto-detects and patches config) |
| **Cursor / Windsurf / Continue** | `jcodemunch-mcp init` or manual `mcp.json` |
| **Antigravity (Google)** | Add a `jcodemunch` entry to `~/.gemini/config/mcp_config.json` (shared by Antigravity 2.0 / IDE / CLI). See below. |
| **Gemini CLI (Google)** | Add a `jcodemunch` entry under `mcpServers` in `~/.gemini/settings.json` (or project `.gemini/settings.json`) — a *different file* from Antigravity's `~/.gemini/config/mcp_config.json` above. *(config per [vendor docs](https://geminicli.com/docs/tools/mcp-server/))* |
| **Qwen Code** | Add under `mcpServers` in `~/.qwen/settings.json` (or project `.qwen/settings.json`). *(config per [vendor docs](https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/))* |
| **Kiro (AWS)** | Add under `mcpServers` in `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user). *(config per [vendor docs](https://kiro.dev/docs/mcp/configuration/))* |
| **OpenAI Codex CLI** | Add `[mcp_servers.jcodemunch]` block to `~/.codex/config.toml` (see below) |
| **Cline / Roo Code** | Add via the MCP marketplace UI or paste `command: uvx`, `args: ["jcodemunch-mcp"]` |
| **Zed** | Add to `settings.json` under `context_servers` |
| **Goose (Block)** | `goose configure` → Add Extension → command `uvx jcodemunch-mcp` |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | Add to `~/.hermes/config.yaml` — see [skill](https://github.com/NousResearch/hermes-agent/pull/10413) |
| **Paperclip** | `.mcp.json` at workspace root (auto-detected) |
| **Any other MCP client** | stdio: `jcodemunch-mcp`, HTTP: `jcodemunch-mcp serve --transport streamable-http` (SSE still available but deprecated by the MCP 2026-07-28 spec) |
| **VS Code (any MCP client)** | Install the [jCodeMunch VS Code extension](https://marketplace.visualstudio.com/items?itemName=jgravelle.jcodemunch-mcp-vscode) for on-save auto-reindex under Copilot Chat / Continue / Cline — closes the staleness gap when the host doesn't fire PostToolUse hooks |
| **GitHub Copilot CLI / cloud agent** | `jcodemunch-mcp init --copilot-hooks` writes `.github/hooks/hooks.json` with a postToolUse rule for auto-reindex |
| **[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus)** (self-hosted AI workspace) | SSE transport: run `jcodemunch-mcp serve --transport sse` on the host (token **unset**), register the URL in the MCP Registry (see below) — *community-tested* |

<details>
<summary>Codex CLI config</summary>

**Recommended (pre-installed binary, no `uvx`).** Codex's rmcp transport
is strict about the first JSON-RPC frame on stdout. `uvx`'s install
chatter on first run can poison the handshake, which historically
manifests as a silent multi-hour hang. Install the package into a
project venv and point Codex at the resolved binary directly:

```bash
python3 -m venv .venv
.venv/bin/pip install -U jcodemunch-mcp
.venv/bin/jcodemunch-mcp --help   # confirm the binary resolves
```

```toml
# ~/.codex/config.toml
[mcp_servers.jcodemunch]
command = "/absolute/path/to/.venv/bin/jcodemunch-mcp"
# (no args required)
```

If the handshake still doesn't complete, set
`JCODEMUNCH_HANDSHAKE_TIMEOUT=5` (the default) and watch stderr — v1.82.1+
emits a one-line hint when the client doesn't call any handler within
the window.

**Note for `codex review --background` and other non-interactive runs.**
Codex's MCP elicitation/approval system can silently *decline* tool
calls to unrecognised servers in non-interactive mode (visible in
`~/.codex/logs_2.sqlite` as `ResolveElicitation { decision: Decline }`
with no chatter on the server side). This is a Codex-side concern, not
a jcodemunch one — track upstream
[here](https://github.com/openai/codex) for the right per-server
auto-approve key. Interactive `codex` runs are unaffected.

**Legacy `uvx` config** (kept for reference; works on tolerant clients,
not recommended for Codex):

```toml
[mcp_servers.jcodemunch]
command = "uvx"
args = ["jcodemunch-mcp"]
```
</details>

<details>
<summary>Antigravity (Google) config</summary>

Antigravity (2.0, IDE, and CLI) loads MCP servers from a single shared file at
`~/.gemini/config/mcp_config.json` (HOME-level only — project-local
`.antigravitycli/mcp_config.json` is read but not loaded). Add:

```json
// ~/.gemini/config/mcp_config.json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    }
  }
}
```

Restart Antigravity so it re-reads the config; tools appear under
`mcp(jcodemunch/*)`. To grant the jcodemunch agent skill to all Antigravity
tools, drop the bundle from `jcodemunch-mcp install claude-code --skills`
(at `~/.claude/skills/jcodemunch/SKILL.md`) into the shared skills dir
`~/.gemini/skills/jcodemunch/` (or the CLI-only `~/.gemini/antigravity-cli/skills/`).
</details>

<details>
<summary>Hermes Agent config</summary>

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  jcodemunch:
    command: "uvx"
    args: ["jcodemunch-mcp"]
```
</details>

<details>
<summary>Odysseus config (self-hosted AI workspace)</summary>

[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) runs in Docker and
indexes nothing itself; jCodeMunch indexes your code on the **host**. Run
jCodeMunch as an **SSE** server on the host and register its URL in Odysseus.
Its SSE client connects by URL only (no auth header), so leave the token unset
and secure the endpoint by network binding instead.

> **SSE deprecation note:** the MCP 2026-07-28 spec deprecates the SSE
> transport. jCodeMunch keeps serving SSE for hosts like Odysseus that don't
> offer streamable-http yet; once Odysseus adds a streamable-http registry
> option, switch to `serve --transport streamable-http` and URL
> `http://host.docker.internal:8848/mcp`.

**1. Start jCodeMunch on the host (no token):**

```bash
jcodemunch-mcp index .
jcodemunch-mcp serve --transport sse --host 0.0.0.0 --port 8848
```

Leave `JCODEMUNCH_HTTP_TOKEN` **unset** — Odysseus's SSE client sends no
`Authorization` header, so a token returns 401 on connect.

**2. In Odysseus → Settings → MCP Registry → Add server:**

- **Transport:** SSE
- **URL:** `http://host.docker.internal:8848/sse`
  (Linux: add `extra_hosts: ["host.docker.internal:host-gateway"]` to the
  Odysseus service in `docker-compose.yml`)

**3. Secure by network, not token.** Because the SSE path is unauthenticated,
bind jCodeMunch so only the Odysseus container can reach it (host-gateway
interface / firewall), not a public interface. `JCODEMUNCH_RATE_LIMIT` adds a
throttle.

**4. Restart Odysseus.** All jCodeMunch tools appear in chat + agents. Keep the
index fresh with `jcodemunch-mcp watch .`; use Odysseus's per-server
`disabled_tools` to trim the surface.

jCodeMunch is read-only by charter, and its `get_*` / `search_*` / `find_*` /
`check_*` tool naming satisfies Odysseus's plan-mode read-only gate, so the
suite stays usable in plan mode.

> *Community-tested:* the MCP protocol round-trip (SSE connect + tool discovery)
> is verified; the container-to-host network dial depends on your Docker setup.

</details>

## FAQ

**How much can I save on Claude / Opus tokens?**
In retrieval-heavy workflows, code-reading tokens typically drop **95%+** because
the agent fetches exact symbols instead of brute-reading whole files — benchmarked
at a 99.6% average reduction across 15 tasks / 3 repositories, with peaks of 99.9%
on large repos. Compact [MUNCH](SPEC_MUNCH.md) encoding then trims another ~45%
off the wire. Full methodology and harness: [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md)
and [benchmarks/](benchmarks/).

**Does it work with large monorepos?**
Yes. It indexes incrementally, detects workspace members (pnpm / yarn / npm /
Turborepo / Cargo / Go workspaces), and scopes queries to subpaths, so retrieval
stays cheap as the repo grows. A file watcher keeps the index fresh.

**What languages are supported?**
70+ languages, including Python, JavaScript/TypeScript, Go, Rust, Java, C/C++,
C#, PHP, Ruby, Swift, and Kotlin via tree-sitter AST parsing. Full matrix:
[LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md).

**Which agents and IDEs does it work with?**
Any MCP client — Claude Code, Cursor, VS Code, Codex CLI, Continue, Windsurf,
and more. One-click and CLI installs are at the [top of this README](#one-click-installs)
and in the [Works With](#works-with) section.

**Is it free for personal use?**
Yes — free for personal use; commercial use needs a license. See
[Commercial Licenses](#commercial-licenses). The guarantee: if jCodeMunch doesn't
pay for itself, you don't pay for it.

**How is this different from RAG or grep-based tools?**
jCodeMunch retrieves at the **symbol level** with byte-level precision —
functions, classes, importers, blast radius, class hierarchies — rather than
returning fuzzy chunks (RAG) or raw line matches (grep) that the agent still has
to read and reason over. Index once, query exactly what you need.

## License

jCodeMunch-MCP is released under the **jCodeMunch-MCP Dual-Use License** — see
[LICENSE](LICENSE) for the authoritative terms. It is **free for non-commercial
use**; [commercial use requires a paid license](#commercial-licenses).

Conditions on all uses:

1. The above copyright notice and this permission notice must be retained in all
   copies or substantial portions of the Software.

2. Any modifications made to the Software must clearly indicate that they are
   derived from the original work, and the name of the original author
   (J. Gravelle) must remain intact. He's kinda full of himself.

3. Redistributions of the Software in source code form must include a prominent
   notice describing any modifications from the original version.

The Software may not be renamed, rebranded, or published to any public package
registry, and it is provided "AS IS" without warranty of any kind. The full,
controlling terms are in [LICENSE](LICENSE).
