Quickstart - https://github.com/jgravelle/jcodemunch-mcp/blob/main/QUICKSTART.md

A crapload of detailed info: http://jcodemunch.com/

<!-- mcp-name: io.github.jgravelle/jcodemunch-mcp -->

## FREE FOR PERSONAL USE
**Use it to make money, and Uncle J. gets a taste. Fair enough?** [details](#commercial-licenses)

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [QUICKSTART.md](QUICKSTART.md) | Zero-to-indexed in three steps |
| [USER_GUIDE.md](USER_GUIDE.md) | Full tool reference, workflows, and best practices |
| [AGENT_HOOKS.md](AGENT_HOOKS.md) | Agent hooks and prompt policies |
| [CONFIGURATION.md](CONFIGURATION.md) | JSONC config file reference, migration from env vars |
| [GROQ.md](GROQ.md) | Groq Remote MCP integration, deployment, gcm CLI |
| [HEADLESS.md](HEADLESS.md) | Using jCodeMunch with `claude -p` (and the jragmunch CLI) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Internal design, storage model, and extension points |
| [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) | Supported languages and parsing details |
| [CONTEXT_PROVIDERS.md](CONTEXT_PROVIDERS.md) | dbt, Git, and custom context provider docs |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common issues and fixes |

---

## Cut code-reading token usage by **95% or more**

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

# jCodeMunch MCP

### Structured code retrieval for serious AI agents

![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![Polyglot](https://img.shields.io/badge/parsing-tree--sitter-9cf)
![jMRI](https://img.shields.io/badge/jMRI-Full-blueviolet)
[![PyPI version](https://img.shields.io/pypi/v/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)

---

### Mentioned by

- **Artur Skowroński** (VirtusLab) — *"roughly 80% fewer tokens, or 5× more efficient — index once, query cheaply forever"* · [GitHub All-Stars #15](https://virtuslab.com/blog/ai/code-munch-mcp-your-agent-starts-navigating)
- **Julian Horsey** (Geeky Gadgets) — *"3,850 tokens reduced to just 700 — a 5.5× improvement"* · [JCodeMunch AI Token Saver](https://www.geeky-gadgets.com/jcodemunch-mcp-token-savings/)
- **Sion Williams** — *"preserving tokens for tasks that actually require reasoning rather than retrieval"* · [March 2026 AI Workflow Update](https://sionwilliams.com/posts/2026-03-06-ai-workflow-update/)
- **Traci Lim** (AWS · ASEAN AI Lead) — *"structural queries that native tools can't answer: find_importers, get_blast_radius, get_class_hierarchy, find_dead_code"* · [5 Repos That Save Token Usage in Claude Code](https://www.tracilzw.com/posts/5-repos-save-token-usage-claude-code)
- **Eric Grill** — *"context is the scarce resource. Cut it by 90% and the whole stack gets cheaper and more reliable"* · [jCodemunch: Context Engine for AI Agents](https://www.ericgrill.com/blog/jcodemunch-mcp-context-engine-for-ai-agents)

[Full recognition page →](https://j.gravelle.us/jCodeMunch/recognition.php)

---

> ## Commercial licenses
>
> jCodeMunch-MCP is **free for non-commercial use**.
>
> **Commercial use requires a paid license.**
>
> **jCodeMunch-only licenses**
>
> * [Builder — $79](https://j.gravelle.us/jCodeMunch/descriptions.php#builder) — 1 developer
> * [Studio — $349](https://j.gravelle.us/jCodeMunch/descriptions.php#studio) — up to 5 developers
> * [Platform — $1,999](https://j.gravelle.us/jCodeMunch/descriptions.php#platform) — org-wide internal deployment
>
> **Want both code and docs retrieval?**
>
> * [Munch Duo Builder Bundle — $89](https://j.gravelle.us/jCodeMunch/descriptions.php#builder)
> * [Munch Duo Studio Bundle — $399](https://j.gravelle.us/jCodeMunch/descriptions.php#studio)
> * [Munch Duo Platform Bundle — $2,249](https://j.gravelle.us/jCodeMunch/descriptions.php#platform)

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

Recent releases have made that retrieval workflow sharper and more useful in real engineering work, with BM25-based symbol search, fuzzy matching, semantic/hybrid search (opt-in, zero mandatory dependencies), query-driven token-budgeted context assembly (`get_ranked_context`), dead code detection (`find_dead_code`), untested symbol detection (`get_untested_symbols`), git-diff-to-symbol mapping (`get_changed_symbols`), architectural centrality ranking (`get_symbol_importance`, PageRank), blast-radius depth scoring with source snippets, context bundles with token budgets, AST-derived call graphs and call hierarchy traversal, decorator-aware search and filtering, hotspot detection (complexity x churn), dependency cycles and coupling metrics, session-aware routing (`plan_turn`, turn budgets, negative evidence), agent config auditing, complexity-based model routing (Agent Selector), enforcement hooks (PreToolUse/PostToolUse/PreCompact), dependency graphs, class hierarchy traversal, multi-symbol bundles, live watch-based reindexing, automatic Claude Code worktree discovery (`watch-claude`), registry-wide auto-reindexing with one-command login-service install (`watch-all` + `watch-install` / `watch-uninstall` / `watch-status`; also exposed as MCP tool `get_watch_status`), auto-watch on demand (when `watch: true` in config, the server automatically indexes and watches any repo a tool is called against — ensuring fresh results from the first call), trusted-folder access controls, edit-ready refactoring plans (`plan_refactoring`) for rename, move, extract, and signature change operations, symbol provenance archaeology (`get_symbol_provenance` — full git lineage, semantic commit classification, evolution narrative), unified PR risk profiling (`get_pr_risk_profile` — composite risk score fusing blast radius, complexity, churn, test gaps, and volume), automatic response secret redaction (AWS/GCP/Azure/JWT/GitHub tokens scrubbed before reaching the LLM context window), and cross-language AST pattern matching (`search_ast` — 10 preset anti-pattern detectors + custom mini-DSL for structural queries like `call:*.unwrap`, `string:/password/i`, `nesting:5+`; works across all 70+ languages with universal node-type mapping).

---

## Real-world results

### Reproducible token efficiency benchmark

Measured with `tiktoken cl100k_base` across three public repos. Workflow: `search_symbols` (top 5) + `get_symbol_source` × 3 per query. Baseline: all source files concatenated (minimum cost for an agent that reads everything). [Full methodology and harness →](benchmarks/METHODOLOGY.md)

| Repository | Files | Symbols | Baseline tokens | jCodeMunch tokens | Reduction |
|------------|------:|--------:|----------------:|------------------:|----------:|
| expressjs/express | 34 | 117 | 73,838 | ~1,300 avg | **98.4%** |
| fastapi/fastapi | 156 | 1,359 | 214,312 | ~15,600 avg | **92.7%** |
| gin-gonic/gin | 40 | 805 | 84,892 | ~1,730 avg | **98.0%** |
| **Grand total (15 task-runs)** | | | **1,865,210** | **92,515** | **95.0%** |

Per-query results range from 79.7% (dense FastAPI router query) to 99.8% (sparse context-bind query on Express). The 95% figure is the aggregate. Run `python benchmarks/harness/run_benchmark.py` to reproduce.

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

### Structural queries native tools can't answer

`find_importers` tells you what imports a file. `get_blast_radius` tells you what breaks if you change a symbol, with depth-weighted risk scores and optional source snippets. `get_class_hierarchy` traverses inheritance chains. `get_call_hierarchy` traces callers and callees N levels deep using AST-derived call graphs, with optional LSP-enriched dispatch resolution for interface/trait method calls. `find_dead_code` finds symbols and files unreachable from any entry point. `get_untested_symbols` finds functions with no evidence of test-file reachability — the intersection of import-graph analysis and test-file detection. `get_changed_symbols` maps a git diff to the exact symbols that were added, modified, or removed. `get_symbol_importance` ranks your codebase by architectural centrality using PageRank on the import graph. `get_hotspots` surfaces the riskiest code by combining complexity with git churn. `get_dependency_cycles` detects circular imports. `get_coupling_metrics` measures module coupling and instability. `get_tectonic_map` discovers the logical module topology by fusing three coupling signals (imports, shared references, git co-churn) — revealing hidden module boundaries, misplaced files, and god-module risk without any configuration. `get_signal_chains` traces how external signals (HTTP requests, CLI commands, scheduled tasks, events) propagate through the codebase via the call graph — discovery mode maps all entry-point-to-leaf pathways and reports orphan symbols, lookup mode tells you which user-facing chains a specific symbol participates in (e.g. "validate_email sits on POST /api/users and cli:import-users"). These are not "faster grep" — they are questions grep cannot answer at all.

### Agent config hygiene

`audit_agent_config` scans your CLAUDE.md, .cursorrules, copilot-instructions.md, and other agent config files for token waste: per-file token cost, stale symbol references (cross-referenced against the index — catches renamed or deleted functions), dead file paths, redundancy between global and project configs, bloat, and scope leaks. No other tool can tell you "line 15 references a function that was renamed three weeks ago."

### Symbol provenance and PR risk profiling

`get_symbol_provenance` is git archaeology: given a symbol, it traces every commit that touched it, classifies each into semantic categories (creation, bugfix, refactor, feature, perf, rename, revert), extracts commit intent, and generates a human-readable narrative explaining who created it, why, and how it evolved. `get_pr_risk_profile` produces a unified risk assessment for a branch or PR — one call fuses blast radius, complexity, churn, test gaps, and change volume into a composite risk score (0.0–1.0) with actionable recommendations. All responses are automatically scanned for leaked credentials (AWS keys, JWTs, GCP service accounts, etc.) and redacted before reaching the LLM.

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

Free packs require no license. Licensed packs require a [jCodeMunch license](https://j.gravelle.us/jCodeMunch/#pricing). Use `--force` to re-download an already-installed pack.

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
- uses: jgravelle/jcodemunch-mcp/speedreview@main
  with:
    groq_api_key: ${{ secrets.GROQ_API_KEY }}
```

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

**Claude Code · Claude Desktop · Cursor · Windsurf · Codex CLI · Continue · Cline · Roo Code · Zed · Goose · Hermes Agent · Paperclip** — and more.

Tested configurations:

| Platform | Config |
|----------|--------|
| **Claude Code / Claude Desktop** | `jcodemunch-mcp init` (auto-detects and patches config) |
| **Cursor / Windsurf / Continue** | `jcodemunch-mcp init` or manual `mcp.json` |
| **OpenAI Codex CLI** | Add `[mcp_servers.jcodemunch]` block to `~/.codex/config.toml` (see below) |
| **Cline / Roo Code** | Add via the MCP marketplace UI or paste `command: uvx`, `args: ["jcodemunch-mcp"]` |
| **Zed** | Add to `settings.json` under `context_servers` |
| **Goose (Block)** | `goose configure` → Add Extension → command `uvx jcodemunch-mcp` |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | Add to `~/.hermes/config.yaml` — see [skill](https://github.com/NousResearch/hermes-agent/pull/10413) |
| **Paperclip** | `.mcp.json` at workspace root (auto-detected) |
| **Any other MCP client** | stdio: `jcodemunch-mcp`, HTTP: `jcodemunch-mcp serve --transport sse` |
| **VS Code (any MCP client)** | Install the [jCodeMunch VS Code extension](https://marketplace.visualstudio.com/items?itemName=jgravelle.jcodemunch-mcp-vscode) for on-save auto-reindex under Copilot Chat / Continue / Cline — closes the staleness gap when the host doesn't fire PostToolUse hooks |
| **GitHub Copilot CLI / cloud agent** | `jcodemunch-mcp init --copilot-hooks` writes `.github/hooks/hooks.json` with a postToolUse rule for auto-reindex |

<details>
<summary>Codex CLI config</summary>

```toml
# ~/.codex/config.toml
[mcp_servers.jcodemunch]
command = "uvx"
args = ["jcodemunch-mcp"]
```
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

## Star History

<a href="https://www.star-history.com/?repos=jgravelle%2Fjcodemunch-mcp&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=jgravelle/jcodemunch-mcp&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=jgravelle/jcodemunch-mcp&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=jgravelle/jcodemunch-mcp&type=date&legend=top-left" />
 </picture>
</a>
