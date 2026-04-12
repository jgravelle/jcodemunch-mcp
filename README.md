Quickstart - https://github.com/jgravelle/jcodemunch-mcp/blob/main/QUICKSTART.md

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

Recent releases have made that retrieval workflow sharper and more useful in real engineering work, with BM25-based symbol search, fuzzy matching, semantic/hybrid search (opt-in, zero mandatory dependencies), query-driven token-budgeted context assembly (`get_ranked_context`), dead code detection (`find_dead_code`), untested symbol detection (`get_untested_symbols`), git-diff-to-symbol mapping (`get_changed_symbols`), architectural centrality ranking (`get_symbol_importance`, PageRank), blast-radius depth scoring with source snippets, context bundles with token budgets, AST-derived call graphs and call hierarchy traversal, decorator-aware search and filtering, hotspot detection (complexity x churn), dependency cycles and coupling metrics, session-aware routing (`plan_turn`, turn budgets, negative evidence), agent config auditing, complexity-based model routing (Agent Selector), enforcement hooks (PreToolUse/PostToolUse/PreCompact), dependency graphs, class hierarchy traversal, multi-symbol bundles, live watch-based reindexing, automatic Claude Code worktree discovery (`watch-claude`), auto-watch on demand (when `watch: true` in config, the server automatically indexes and watches any repo a tool is called against — ensuring fresh results from the first call), trusted-folder access controls, and edit-ready refactoring plans (`plan_refactoring`) for rename, move, extract, and signature change operations.

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

`find_importers` tells you what imports a file. `get_blast_radius` tells you what breaks if you change a symbol, with depth-weighted risk scores and optional source snippets. `get_class_hierarchy` traverses inheritance chains. `get_call_hierarchy` traces callers and callees N levels deep using AST-derived call graphs, with optional LSP-enriched dispatch resolution for interface/trait method calls. `find_dead_code` finds symbols and files unreachable from any entry point. `get_untested_symbols` finds functions with no evidence of test-file reachability — the intersection of import-graph analysis and test-file detection. `get_changed_symbols` maps a git diff to the exact symbols that were added, modified, or removed. `get_symbol_importance` ranks your codebase by architectural centrality using PageRank on the import graph. `get_hotspots` surfaces the riskiest code by combining complexity with git churn. `get_dependency_cycles` detects circular imports. `get_coupling_metrics` measures module coupling and instability. These are not "faster grep" — they are questions grep cannot answer at all.

### Agent config hygiene

`audit_agent_config` scans your CLAUDE.md, .cursorrules, copilot-instructions.md, and other agent config files for token waste: per-file token cost, stale symbol references (cross-referenced against the index — catches renamed or deleted functions), dead file paths, redundancy between global and project configs, bloat, and scope leaks. No other tool can tell you "line 15 references a function that was renamed three weeks ago."

### Better engineering workflows

Useful for onboarding, debugging, refactoring, impact analysis, and exploring unfamiliar repos without brute-force file reading.

### Refactoring Planner

`plan_refactoring` generates exact edit-ready instructions for rename, move, extract, and
signature change operations. Returns `{old_text, new_text}` blocks compatible with any editor's
find-and-replace, plus import rewrites, collision detection, new file generation, and multi-file coordination.

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

If you’re using Claude Code:

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp
```

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
| `disabled_tools` | Remove tools from schema entirely | ~100–400 tokens/tool |
| `languages` | Shrink language enum + gate features | ~2–86 tokens/turn |
| `meta_fields` | Filter `_meta` response fields | ~50–150 tokens/call |
| `descriptions` | Control description verbosity | ~0–600 tokens/turn |

See the full template for all available keys. Run `jcodemunch-mcp config --init` to generate one.

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

## Star History

<a href="https://www.star-history.com/?repos=jgravelle%2Fjcodemunch-mcp&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=jgravelle/jcodemunch-mcp&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=jgravelle/jcodemunch-mcp&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=jgravelle/jcodemunch-mcp&type=date&legend=top-left" />
 </picture>
</a>
