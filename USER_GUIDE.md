<div align="left" style="float:right; width: 320px; margin: 0 0 1rem 1rem; border: 1px solid #999; padding: 0.75rem; border-radius: 8px; background: #f8f8f8;">
  <strong>Contents</strong>
  <ul>
    <li><a href="#what-jcodemunch-actually-does">What jCodeMunch actually does</a></li>
    <li><a href="#1-quick-start">1. Quick Start</a></li>
    <li><a href="#2-add-jcodemunch-to-your-mcp-client">2. Add jCodeMunch to your MCP client</a></li>
    <li><a href="#3-the-step-people-skip-then-blame-the-software-for">3. Tell your agent to use jCodeMunch</a></li>
    <li><a href="#4-your-first-useful-workflows">4. Your first useful workflows</a></li>
    <li><a href="#5-core-mental-model">5. Core mental model</a></li>
    <li><a href="#6-tool-reference">6. Tool reference</a></li>
    <li><a href="#7-how-search-works">7. How search works</a></li>
    <li><a href="#8-token-savings-what-it-means-and-what-it-does-not-mean">8. Token savings</a></li>
    <li><a href="#9-live-token-savings-counter">9. Live token savings counter</a></li>
    <li><a href="#10-community-savings-meter">10. Community savings meter</a></li>
    <li><a href="#11-local-llm-tuning-for-summaries">11. Local LLM tuning</a></li>
    <li><a href="#12-storage-and-indexing">12. Storage and indexing</a></li>
    <li><a href="#13-troubleshooting">13. Troubleshooting</a></li>
    <li><a href="#14-best-practices">14. Best practices</a></li>
    <li><a href="#15-best-practices-for-prompting-the-agent">15. Prompting the agent</a></li>
    <li><a href="#16-final-advice">16. Final advice</a></li>
  </ul>
  <hr style="margin: 0.5rem 0;">
  <strong>Reference docs</strong>
  <ul>
    <li><a href="QUICKSTART.md">Quick Start</a></li>
    <li><a href="ARCHITECTURE.md">Architecture</a></li>
    <li><a href="UNDER_THE_HOOD.md">Under the Hood (technical manual)</a></li>
  </ul>
</div>

# jCodeMunch User Guide

## What jCodeMunch actually does

jCodeMunch helps AI agents explore codebases **without reading the whole damn file every time**.

Most agents inspect repos the expensive way:

1. open a large file  
2. skim hundreds or thousands of lines  
3. extract one useful function  
4. repeat somewhere else  
5. quietly set your token budget on fire

jCodeMunch replaces that with **structured retrieval**.

It indexes a repository once, extracts symbols with tree-sitter, stores metadata plus byte offsets into the original source, and lets your MCP-compatible agent retrieve **only the code it actually needs**. That is why token savings can be dramatic in retrieval-heavy workflows. :contentReference[oaicite:2]{index=2}

If you only remember one thing from this guide, make it this:

> **jCodeMunch is not magic because it is installed.  
> It is powerful because your agent uses it instead of brute-reading files.**

---

# 1. Quick Start

## Install

```bash
pip install jcodemunch-mcp
````

Verify the install:

```bash
jcodemunch-mcp --help
```

### Recommended: use `uvx` in MCP clients

For MCP client configuration, `uvx` is usually the better choice because it runs the package on demand and avoids PATH headaches.

> **`'uvx' is not recognized` (Windows / Cursor / any client)?** `uvx` ships with [uv](https://docs.astral.sh/uv/). Install it with `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then **fully restart your editor** so it picks up the new PATH. To skip uv entirely: `python -m pip install` the package and launch with `command: "python"`, `args: ["-m", "jcodemunch_mcp"]` instead.

---

# 2. Add jCodeMunch to your MCP client

## Claude Code

Fastest setup:

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp
```

Project-only install:

```bash
claude mcp add --scope project jcodemunch uvx jcodemunch-mcp
```

With optional environment variables:

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp \
  -e GITHUB_TOKEN=ghp_... \
  -e ANTHROPIC_API_KEY=sk-ant-...
```

Restart Claude Code afterward.

### Manual Claude Code config

| Scope   | Path                    |
| ------- | ----------------------- |
| User    | `~/.claude.json`        |
| Project | `.claude/settings.json` |

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

---

## Claude Desktop

Config file location:

| OS      | Path                                                              |
| ------- | ----------------------------------------------------------------- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux   | `~/.config/claude/claude_desktop_config.json`                     |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json`                     |

Minimal config:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    }
  }
}
```

With optional GitHub auth and AI summaries:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

### Optional environment variables

* `GITHUB_TOKEN`
  Enables private repos and higher GitHub API limits.

* `ANTHROPIC_API_KEY`
  Enables AI-generated summaries via Claude.

* `ANTHROPIC_MODEL`
  Overrides the default Anthropic model.

* `GOOGLE_API_KEY`
  Enables AI-generated summaries via Gemini if Anthropic is not configured.

* `GOOGLE_MODEL`
  Overrides the default Gemini model.

* `JCODEMUNCH_SUMMARIZER_PROVIDER`
  Forces the AI summarizer provider. Supported values: `anthropic`, `gemini`,
  `openai`, `minimax`, `glm`, `none`. If unset, `jcodemunch-mcp` auto-detects
  by API keys in this order: Anthropic, Gemini, OpenAI-compatible, MiniMax, GLM-5.

* `OPENAI_API_BASE`
  Enables OpenAI-compatible summaries against a local or custom endpoint when
  no higher-priority provider is configured or when
  `JCODEMUNCH_SUMMARIZER_PROVIDER=openai`.

* `allow_remote_summarizer`
  Controls whether OpenAI-compatible endpoints may point to non-localhost
  hosts. The default is `false`, which means endpoints such as
  `http://127.0.0.1:11434/v1` work, but remote hosts such as
  `https://api.minimax.io/v1` are blocked. When blocked, jcodemunch does not
  send code to that provider and falls back to docstring or signature-based
  summaries. Set `allow_remote_summarizer: true` in `config.jsonc` when you
  intentionally want to use a hosted OpenAI-compatible provider.

* `MINIMAX_API_KEY`
  Enables AI-generated summaries via MiniMax using the default model
  `minimax-m2.7`.

* `ZHIPUAI_API_KEY`
  Enables AI-generated summaries via GLM-5 using the default endpoint
  `https://api.z.ai/api/paas/v4/`.

* `JCODEMUNCH_PATH_MAP`
  Remaps stored path prefixes so an index built on one machine can be reused on
  another without re-indexing. Format: `orig1=new1,orig2=new2` where `orig` is
  the prefix as stored in the index (the path used at index time) and `new` is
  the equivalent path on the current machine. Each pair is split on the last `=`,
  so `=` signs within path components are preserved. Pairs are comma-separated;
  path components containing commas are not supported. The first matching prefix
  wins — list more-specific prefixes before broader ones when they overlap.

  Example (Linux index reused on Windows):
  ```
  JCODEMUNCH_PATH_MAP=/home/user/Dev=C:\Users\user\Dev
  ```

* `JCODEMUNCH_CONTEXT_PROVIDERS=0`
  Disables context-provider enrichment during indexing.

* `JCODEMUNCH_EMBED_MODEL`
  Activates local embedding via `sentence-transformers`. Set to a model name such as `all-MiniLM-L6-v2`. Install the optional dep with `pip install "jcodemunch-mcp[semantic]"`.

* `OPENAI_EMBED_MODEL`
  Activates OpenAI embedding (requires `OPENAI_API_KEY` also set). Example: `text-embedding-3-small`.

* `GOOGLE_EMBED_MODEL`
  Activates Gemini embedding (requires `GOOGLE_API_KEY` also set). Example: `models/text-embedding-004`.

Restart Claude Desktop after saving.

### Debug logging

If you need to troubleshoot indexing or server startup, use a log file instead of stderr:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": [
        "jcodemunch-mcp",
        "--log-level", "DEBUG",
        "--log-file", "/tmp/jcodemunch.log"
      ]
    }
  }
}
```

---

## Cursor

Open **Settings → Tools & MCP → New MCP Server**, then add:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    }
  }
}
```

Save and confirm the server starts successfully.

---

## VS Code

Add to `.vscode/settings.json`:

```json
{
  "mcp.servers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_..."
      }
    }
  }
}
```

---

## Google Antigravity

Antigravity's `agy` CLI inherits Gemini-CLI's config shape. Edit
`~/.gemini/config/mcp_config.json` and add the `jcodemunch` entry:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

On first `/mcp` load inside `agy`, the CLI caches each tool schema at
`~/.gemini/antigravity-cli/mcp/jcodemunch/<tool>.json`. Permissions live in
`~/.gemini/antigravity-cli/settings.json` as
`"permissions": { "allow": [ "mcp(jcodemunch/*)" ] }`. Restart `agy` after
editing the config.

Prefer a PyPI-independent install channel? Point `--from` at the repo and it
always pulls the latest (requires `git`; builds from source):
`["--from", "git+https://github.com/jgravelle/jcodemunch-mcp.git", "jcodemunch-mcp"]`.

If you already have jcodemunch configured for Claude Code or Gemini CLI,
`agy plugin import claude` (or `import gemini`) can pull the existing
config across without re-editing JSON.

## Odysseus (self-hosted AI workspace)

[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) runs in Docker and
has first-class MCP support, but it indexes nothing itself — jCodeMunch indexes
your code on the **host**. So instead of a stdio `command`/`args` entry (which
would run inside the container and only see volume-mounted code), run jCodeMunch
as an **SSE** server on the host and point Odysseus at the URL.

One catch found by reading Odysseus's source: its SSE client connects by URL
only and sends **no `Authorization` header**, and its other HTTP transport
expects OAuth rather than a static token. jCodeMunch uses a static
`JCODEMUNCH_HTTP_TOKEN`, so for Odysseus you leave the token **unset** and
secure the endpoint by network binding.

**1. Start jCodeMunch on the host (token unset):**

```bash
jcodemunch-mcp index .
jcodemunch-mcp serve --transport sse --host 0.0.0.0 --port 8848
```

**2. In Odysseus → Settings → MCP Registry → Add server:**

- **Transport:** SSE
- **URL:** `http://host.docker.internal:8848/sse`

On Linux, give the container a route to the host by adding
`extra_hosts: ["host.docker.internal:host-gateway"]` to the Odysseus service in
its `docker-compose.yml`.

**3. Secure by network, not token.** Bind jCodeMunch so only the Odysseus
container can reach it (the host-gateway interface plus a firewall rule), not a
public interface — the SSE endpoint is unauthenticated. `JCODEMUNCH_RATE_LIMIT`
adds a per-client throttle.

**4. Restart Odysseus.** jCodeMunch's tools show up in chat and agents. Keep the
index current with `jcodemunch-mcp watch .`, and use Odysseus's per-server
`disabled_tools` list to hide any tools you don't want exposed. Because every
jCodeMunch tool is read-only and named `get_*` / `search_*` / `find_*` /
`check_*`, the suite stays usable inside Odysseus's plan mode.

The MCP round-trip (SSE connect + tool discovery) is verified; the
container-to-host network dial depends on your Docker networking, so treat this
as community-tested and report back if your setup needs different host routing.

---

# 3. The step people skip, then blame the software for

## Tell your agent to use jCodeMunch

Installing the server makes the tools available.

It does **not** guarantee your agent will stop opening giant files like a confused tourist with a flashlight.

> **Note:** For a comprehensive guide on enforcing these rules through agent hooks and prompt policies, see [AGENT_HOOKS.md](AGENT_HOOKS.md).

Give it an instruction like this:

```markdown
Use jcodemunch-mcp for code lookup whenever available. Prefer symbol search, outlines, and targeted retrieval over reading full files.
```

That one sentence can be the difference between:

* “this is incredible”
  and
* “I installed it and saw no change”

---

# 4. Your first useful workflows

## Explore a GitHub repository

```json
index_repo: { "url": "fastapi/fastapi" }
get_repo_outline: { "repo": "fastapi/fastapi" }
get_file_tree: { "repo": "fastapi/fastapi", "path_prefix": "fastapi" }
get_file_outline: { "repo": "fastapi/fastapi", "file_path": "fastapi/main.py" }
```

Use this when:

* you are new to a repo
* you want the lay of the land before reading code
* you want to avoid blind file spelunking

---

## Explore a local project

```json
index_folder: { "path": "/home/user/myproject" }
resolve_repo: { "path": "/home/user/myproject" }
get_repo_outline: { "repo": "myproject" }
search_symbols: { "repo": "myproject", "query": "main" }
```

Use this when:

* you want fast local indexing
* you are working on private code
* you want repeat retrieval without re-scanning the repo every time

### Local-folder enrichment

When indexing local folders, jCodeMunch can detect ecosystem tools and enrich the index with domain-specific metadata. The current built-in example is dbt support, which can fold model descriptions, tags, and column metadata into summaries and search keywords. ([GitHub][2])

---

## Find and read a function

```json
search_symbols: { "repo": "owner/repo", "query": "authenticate", "kind": "function" }
get_symbol_source: { "repo": "owner/repo", "symbol_id": "src/auth.py::authenticate#function" }
```

This is one of the core jCodeMunch loops:

1. search
2. identify the symbol
3. fetch only that symbol

That is where a lot of the token savings come from.

---

## Understand a class without reading the entire file

```json
get_file_outline: { "repo": "owner/repo", "file_path": "src/auth.py" }
get_symbol_source: {
  "repo": "owner/repo",
  "symbol_ids": [
    "src/auth.py::AuthHandler.login#method",
    "src/auth.py::AuthHandler.logout#method"
  ]
}
```

Use `get_file_outline` first to see the API surface, then retrieve only the methods you care about.

---

## Search for text that is not a symbol

```json
search_text: {
  "repo": "owner/repo",
  "query": "TODO",
  "file_pattern": "*.py",
  "context_lines": 1
}
```

Use this for:

* string literals
* comments
* configuration values
* weird text fragments
* anything that is not likely to appear as a symbol name

---

## Read only part of a file

```json
get_file_content: {
  "repo": "owner/repo",
  "file_path": "src/main.py",
  "start_line": 20,
  "end_line": 40
}
```

This is useful when the thing you need is line-oriented rather than symbol-oriented.

---

## Verify source has not drifted

```json
get_symbol_source: {
  "repo": "owner/repo",
  "symbol_id": "src/main.py::process#function",
  "verify": true
}
```

Check `_meta.content_verified` in the response.

This tells you whether the retrieved source still matches the indexed version.

---

## Force a re-index

```json
invalidate_cache: { "repo": "owner/repo" }
index_repo: { "url": "owner/repo" }
```

Use this when:

* the index is stale
* the repo changed substantially
* you want a clean reset

For GitHub repos, newer builds also store the Git tree SHA so unchanged incremental re-index runs can return immediately instead of re-downloading the universe just to discover nothing changed. ([GitHub][2])

## When a re-index is a maintenance event (fleets)

Everything above assumes re-indexing is cheap enough to run on request. On one
machine it is. On a fleet sharing a store it is a scheduled maintenance event,
which makes "re-index and try again" unactionable for exactly the people with the
most code.

`refresh` does the same work in bounded, resumable slices:

```bash
jcodemunch-mcp refresh . --max-seconds 300     # one cron slot
jcodemunch-mcp refresh . --status              # how far along, no work done
```

Each run does as much as its budget allows, records where it stopped, and
returns. Running it again continues from there. The work converges whether it
gets one long window or twenty short ones, so it fits a maintenance schedule
instead of demanding one.

| Flag | Effect |
|------|--------|
| `--max-seconds` | Wall-clock budget for this run (default 300) |
| `--max-files` | File budget for this run (default 250) |
| `--pause-ms` | Sleep between batches. **This is the duty-cycle knob** |
| `--batch-size` | Files per underlying index call (default 25) |
| `--ai-summaries` | Generate AI summaries. **Off by default** |
| `--status` / `--reset` | Report progress / discard and re-enumerate |

### What it costs

Measured on this repository (795 files, 15.4k symbols, Windows, cold): **60 files
in 3.8 s**, about **63 ms per file**, so a full campaign is roughly **50 s** of
CPU spread over as many slices as you choose. Scale from your own file count; a
run reports its own rate and an estimate of the work remaining, which is a better
number than this one because it is measured on your hardware and your code.

Three things worth knowing before scheduling it:

* **The budget bounds when a run ENDS, not what it costs while it runs.** A slice
  running flat out still saturates a core. `--pause-ms` is what lowers the duty
  cycle. Python cannot preempt a running parse (tree-sitter is C), so the same
  limit applies here as to `JCODEMUNCH_PARSE_BUDGET_SECONDS`.
* **AI summaries are off by default here**, unlike `index_folder`. A scheduled
  background job must not bill a paid summarizer API without being asked.
* **A `parser_generation` upgrade is only claimed when the whole corpus has been
  re-parsed**, verified by re-running discovery at the end. If the repo grew
  during the campaign, the new files are appended and the stamp is deferred
  rather than claimed early. A partially re-parsed index keeps reporting the
  older generation, which is what makes the claim worth anything.

`refresh` re-parses an existing index; it does not build the first one. Run
`jcodemunch-mcp index <path>` for that.

---

# 5. Core mental model

## What jCodeMunch stores

Each symbol is indexed with structured metadata such as:

* signature
* kind
* qualified name
* one-line summary
* byte offsets into the original file

That lets jCodeMunch fetch the exact source later by byte offset rather than opening and re-parsing the entire file on every request. ([GitHub][1])

## Stable symbol IDs

Symbol IDs look like this:

```text
{file_path}::{qualified_name}#{kind}
```

Examples:

```text
src/main.py::UserService#class
src/main.py::UserService.login#method
src/utils.py::authenticate#function
config.py::MAX_RETRIES#constant
```

These IDs stay stable across re-indexing as long as path, qualified name, and kind stay the same. ([GitHub][1])

## Runtime identity resource

The server publishes one MCP **resource** (not a tool): `munch://runtime/identity`, a read-only `munch.runtime.identity/v1` JSON document identifying this exact server process. Multi-agent harnesses use it to tell command-line-identical servers apart, detect restarts, and refuse cleanup when identity doesn't match.

Fields: `schema`, `product`, `version`, `transport` (`stdio` / `sse` / `streamable-http`), `pid`, `process_start {value, source}`, `instance_id` (uuid4 minted once per process lifetime — a restart always changes it, even if the PID is reused), and optional `launch_id`.

`process_start.source` is `"os"` when the timestamp comes from the operating system (Windows `GetProcessTimes`, Linux `/proc` starttime); on platforms where that's unobtainable it falls back to the server's own first-read clock and says so with `"self_recorded"` — the value is never fabricated as OS evidence.

Set `JCODEMUNCH_LAUNCH_ID` (or the suite-generic `MUNCH_LAUNCH_ID`) in the server's environment to have an opaque launch token echoed back as `launch_id`; unset means the field is omitted. The resource is computed on demand, reads nothing from disk, and writes nothing. Command lines, env, cwd, hostnames, and repo paths are deliberately excluded. The same contract ships in jdocmunch-mcp and jdatamunch-mcp.

---

# 6. Tool reference

### Indexing & Repository Management

| Tool | What it does | Key parameters |
|------|--------------|----------------|
| `index_repo` | Index a GitHub repository | `url`, `incremental`, `use_ai_summaries`, `extra_ignore_patterns` |
| `index_folder` | Index a local folder | `path`, `incremental`, `use_ai_summaries`, `extra_ignore_patterns`, `follow_symlinks` |
| `index_file` | Re-index one file — faster than `index_folder` for surgical updates | `path`, `use_ai_summaries`, `context_providers` |
| `embed_repo` | Precompute and cache all symbol embeddings for semantic search in one pass (optional warm-up; embeddings are also computed lazily on first semantic query) | `repo`, `batch_size`, `force` |
| `list_repos` | List all indexed repositories | — |
| `resolve_repo` | Resolve a filesystem path to its repo ID — O(1) lookup, preferred over `list_repos` when you know the path | `path` |
| `invalidate_cache` | Delete cached index and force a full re-index | `repo` |
| `audit_agent_config` | Audit agent config files (CLAUDE.md, .cursorrules, etc.) for token waste, stale symbol/file references, redundancy, bloat, and scope leaks | `repo`, `project_path` |

### Discovery & Outlines

| Tool | What it does | Key parameters |
|------|--------------|----------------|
| `suggest_queries` | Surface useful entry-point files, keywords, and example queries for an unfamiliar repo | `repo` |
| `get_repo_outline` | High-level overview: directories, file counts, language breakdown, symbol counts | `repo` |
| `get_file_tree` | Browse file structure, optionally filtered by path prefix | `repo`, `path_prefix`, `include_summaries` |
| `get_file_outline` | All symbols in a file with full signatures and summaries; supports batch via `file_paths` | `repo`, `file_path`, `file_paths` |

### Retrieval

| Tool | What it does | Key parameters |
|------|--------------|----------------|
| `get_symbol_source` | Retrieve symbol source: `symbol_id` (single, flat response) or `symbol_ids[]` (batch, `{symbols,errors}`); supports verify and context_lines | `repo`, `symbol_id`, `symbol_ids`, `verify`, `context_lines` |
| `get_context_bundle` | Symbol + its imports + optional callers in one bundle; supports multi-symbol, Markdown output, and token budgeting (`token_budget`, `budget_strategy`: `most_relevant`/`core_first`/`compact`, `include_budget_report`) | `repo`, `symbol_id`, `symbol_ids`, `include_callers`, `output_format`, `token_budget`, `budget_strategy`, `include_budget_report` |
| `get_ranked_context` | Query-driven token-budgeted context assembler — returns the best-fit symbols for a task, ranked by relevance + centrality and greedily packed to fit the budget. Exact symbol names in the query (qualified, CamelCase, snake_case) are pinned ahead of the ranking, so include identifiers verbatim when you know them; `_meta.query_shape` reports what was recognized | `repo`, `query`, `token_budget`, `strategy`, `include_kinds`, `scope` |
| `get_file_content` | Read cached file content, optionally sliced to a line range | `repo`, `file_path`, `start_line`, `end_line` |

### Search

| Tool | What it does | Key parameters |
|------|--------------|----------------|
| `search_symbols` | Search symbol index by name, signature, summary, or docstring; supports kind/language/file_pattern/decorator filters, fuzzy matching (`fuzzy`, `fuzzy_threshold`, `max_edit_distance`), centrality-aware ranking (`sort_by`: `relevance`/`centrality`/`combined`), and optional semantic/hybrid search (`semantic`, `semantic_weight`, `semantic_only`). Returns `negative_evidence` when results are empty or low-confidence | `repo`, `query`, `kind`, `language`, `file_pattern`, `decorator`, `max_results`, `token_budget`, `detail_level`, `fuzzy`, `sort_by`, `semantic` |
| `search_text` | Full-text search across indexed file contents; supports regex, context lines, and optional semantic search | `repo`, `query`, `is_regex`, `file_pattern`, `max_results`, `context_lines`, `semantic` |
| `search_columns` | Search column metadata across dbt / SQLMesh / database catalog models | `repo`, `query`, `model_pattern`, `max_results` |

### Relationship & Impact Analysis

| Tool | What it does | Key parameters |
|------|--------------|----------------|
| `find_importers` | Find all files that import a given file; supports batch via `file_paths`; each result includes `has_importers` flag for spotting transitive dead-code chains | `repo`, `file_path`, `file_paths`, `max_results` |
| `find_references` | Find all files that import or reference a given identifier; supports batch via `identifiers` | `repo`, `identifier`, `identifiers`, `max_results` |
| `check_references` | Quick dead-code check: is an identifier referenced anywhere? Combines import + content search | `repo`, `identifier`, `identifiers`, `search_content`, `max_content_results` |
| `get_dependency_graph` | File-level dependency graph up to 3 hops; direction = imports, importers, or both | `repo`, `file`, `direction`, `depth` |
| `get_blast_radius` | Which files break if this symbol changes? Returns confirmed/potential impacted files, `overall_risk_score`, `direct_dependents_count`; set `include_depth_scores=true` for `impact_by_depth` grouped by BFS layer; `include_source=true` returns source snippets and nearby symbols per entry (capped by `source_budget`); `decorator_filter` restricts to symbols with a given decorator | `repo`, `symbol`, `depth`, `include_depth_scores`, `include_source`, `source_budget`, `decorator_filter` |
| `get_call_hierarchy` | Callers and callees of a symbol, N levels deep (AST-derived on v8+ indexes, text heuristic fallback) | `repo`, `symbol_id`, `direction`, `depth` |
| `get_impact_preview` | Transitive "what breaks?" analysis — follows call chains to show downstream impact | `repo`, `symbol_id` |
| `get_hotspots` | Top-N high-risk symbols ranked by complexity x churn (git commit frequency) | `repo`, `top_n`, `days` |
| `get_coupling_metrics` | Afferent/efferent coupling and instability for a module path | `repo`, `module_path` |
| `get_dependency_cycles` | Detect circular dependencies in the import graph | `repo` |
| `get_extraction_candidates` | Suggest functions that could be extracted from a file based on complexity and caller count | `repo`, `file_path`, `min_complexity`, `min_callers` |
| `get_symbol_importance` | Rank symbols by architectural centrality using PageRank or in-degree on the import graph; surfaces the most load-bearing symbols in a repo | `repo`, `top_n`, `algorithm`, `scope` |
| `find_dead_code` | Find symbols and files unreachable from any entry point via the import graph; entry points auto-detected (main, __init__, CLI decorators, etc.) | `repo`, `granularity`, `min_confidence`, `include_tests`, `entry_point_patterns` |
| `get_changed_symbols` | Map a git diff to affected symbols; detects added/modified/removed/renamed symbols between two commits; optionally includes blast radius per changed symbol | `repo`, `since_sha`, `until_sha`, `include_blast_radius`, `max_blast_depth` |
| `get_class_hierarchy` | Full inheritance chain (ancestors + descendants) across Python, TS, Java, C#, and more | `repo`, `class_name` |
| `find_implementations` | Concrete implementations of an interface, abstract class, or method via multiple channels — SCIP/LSP evidence (1.0), AST hierarchy (0.85), duck-typed match (0.65), decorator handler (0.45); `_meta.confidence_provenance` states each channel's basis and measured precision/recall. Import compiler evidence first with `jcodemunch-mcp import-scip` for compile-time-grade results | `repo`, `symbol`, `cross_repo` |
| `get_related_symbols` | Symbols related to a given symbol via co-location, shared importers, and name-token overlap | `repo`, `symbol_id`, `max_results` |
| `get_symbol_diff` | Diff symbol sets of two indexed repo snapshots; detects added, removed, and changed symbols | `repo_a`, `repo_b` |

### Session & Routing

| Tool | What it does | Key parameters |
|------|--------------|----------------|
| `plan_turn` | Opening-move router: runs BM25 + PageRank against a query, returns confidence level, recommended symbols/files, insertion point suggestions, and budget advisor | `repo`, `query`, `max_recommended` |
| `get_session_context` | Returns session history: files read, searches run, edits made, tool call counts — use to avoid re-reading the same files | — |
| `get_session_snapshot` | Compact ~200-token markdown summary of session state (focus files, edits, searches, negative evidence) — designed for context injection after compaction | — |
| `register_edit` | Post-edit cache invalidation: clears BM25 and search caches for edited files, optionally reindexes | `file_path`, `reindex` |
| `set_tool_tier` | Switch the exposed tool surface between tiers in-session (new installs default to a compact core tier); `jcodemunch_guide` is always present at every tier | `tier` |
| `get_dead_code_v2` | Multi-signal dead code detection: entry-point reachability, call graph, reference search, framework awareness | `repo`, `min_confidence`, `include_tests` |

### Utilities

| Tool | What it does | Key parameters |
|------|--------------|----------------|
| `get_session_stats` | Token savings, cost avoided, per-tool breakdown, and `latency_per_tool` (p50/p95/max/error_rate) for the current session | — |
| `analyze_perf` | Per-tool latency telemetry + cache hit-rates. Defaults to in-memory session ring; `window=1h\|24h\|7d\|all` reads `~/.code-index/telemetry.db` (opt-in). `compare_release="X.Y.Z"` diffs against a saved token baseline. `ledger=true` summarises ranking events. | `window`, `top`, `tool`, `compare_release`, `ledger` |
| `tune_weights` | Learn per-repo retrieval weights from the v1.78.0 ranking ledger. Writes `~/.code-index/tuning.jsonc` with a `semantic_weight` override applied at query time. | `repo`, `dry_run`, `min_events`, `explain` |
| `check_embedding_drift` | Pin (or re-check) a 16-string canary against the active embedding provider. Catches silent provider model changes that quietly degrade hybrid retrieval. | `capture`, `force`, `threshold` |

### Retrieval health signals (v1.74.0+)

Every retrieval response now carries:

- `_meta.confidence` — calibrated 0–1 retrieval-quality score on `search_symbols` / `plan_turn` / `get_ranked_context`. Combine top-1/top-2 score gap, top-1 strength, identity match, and freshness.
- `_meta.freshness` — `{fresh, edited_uncommitted, stale_index}` counts plus `repo_is_stale` flag derived from index SHA vs `git rev-parse HEAD` and per-file mtime checks.
- Per-symbol `_freshness` field on each entry — useful when a partial reindex left some files stale.
- `negative_evidence` — a four-state retrieval verdict on empty or weak results: `ok`, `low_confidence`, `absent` (the corpus was scanned, with scan counts, and the answer isn't there), or `degraded` (the index is impaired — don't treat silence as absence). Lets an agent distinguish "provably not here" from "couldn't look properly."
- `_meta.confidence_provenance` (on `find_implementations`) — every channel confidence states its basis, `declared` (engineering prior) or `measured` (backed by a committed, CI-re-run benchmark artifact), with gold-corpus precision/recall where measured.

These contracts are published as JSON Schemas in [`schemas/`](schemas/); the full design rationale is in [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md).




### Workflow patterns

```
New / unfamiliar repo?
  → suggest_queries → get_repo_outline → get_file_tree

Looking for a symbol by name?
  → search_symbols  (add kind= / language= / file_pattern= to narrow)

Typo or partial name? (fuzzy)
  → search_symbols(fuzzy=true)

Concept search — "database connection" when the code says "db_pool"?
  → search_symbols(semantic=true)  (requires embedding provider)

What are the most architecturally important symbols?
  → get_symbol_importance  (PageRank on the import graph)

Get the best-fit context for a task without blowing the token budget?
  → get_ranked_context(query="...", token_budget=4000)

Looking for text, strings, or comments?
  → search_text  (supports regex and context_lines)

Need to read a function or class?
  → get_file_outline → get_symbol_source

Need symbol + its imports in one shot?
  → get_context_bundle  (add token_budget= to cap size)

What imports this file?
  → find_importers

Where is this identifier used?
  → find_references  (or check_references for a quick yes/no)

What breaks if I change this symbol?
  → get_blast_radius(include_depth_scores=true) → find_importers

What symbols actually changed since the last commit?
  → get_changed_symbols  (add include_blast_radius=true for downstream impact)

Is this code dead / unreachable?
  → find_dead_code  (or check_references for a single identifier)

Class hierarchy?
  → get_class_hierarchy

File dependency graph?
  → get_dependency_graph

What changed between two repo snapshots?
  → get_symbol_diff

Database column search (dbt / SQLMesh)?
  → search_columns

Starting a new task — what to look at first?
  → plan_turn(query="...") — confidence + recommended symbols/files

What files have I already read/edited this session?
  → get_session_context

Which symbols have a specific decorator (e.g. @app.route, @login_required)?
  → search_symbols(decorator="route")  or  get_blast_radius(decorator_filter="login_required")

Who calls this function? What does it call?
  → get_call_hierarchy(symbol_id="...", direction="callers")

Where are the riskiest parts of the codebase?
  → get_hotspots  (complexity × churn)

Are there circular dependencies?
  → get_dependency_cycles

Which functions should be extracted/refactored?
  → get_extraction_candidates(file_path="...")

How tightly coupled is this module?
  → get_coupling_metrics(module_path="src/core")
```

---

# 7. How search works

`search_symbols` is not a naive grep dressed up in a fake mustache.

The search logic uses weighted scoring across things like:

* exact name match
* name substring match
* word overlap
* signature terms
* summary terms
* docstring and keyword matches

Filters like `kind`, `language`, and `file_pattern` narrow the field before scoring. Zero-score results are discarded. ([GitHub][3])

**Fuzzy matching** — pass `fuzzy=true` to enable a trigram Jaccard + Levenshtein fallback that fires when BM25 confidence is low. Useful for typos or partial names (`conn` → `connection_pool`). Fuzzy results include `match_type`, `fuzzy_similarity`, and `edit_distance` fields. Zero behavioral change when `fuzzy=false` (default).

**Centrality-aware ranking** — pass `sort_by="centrality"` to rank results by PageRank on the import graph, or `sort_by="combined"` to blend BM25 and PageRank. Default stays `"relevance"` (pure BM25).

**Semantic / hybrid search** — pass `semantic=true` to enable embedding-based search alongside BM25. Requires a configured embedding provider (`JCODEMUNCH_EMBED_MODEL`, `OPENAI_API_KEY + OPENAI_EMBED_MODEL`, or `GOOGLE_API_KEY + GOOGLE_EMBED_MODEL`). `semantic_weight` controls the BM25/embedding blend (default 0.5). `semantic_only=true` skips BM25 entirely. Zero performance impact when `semantic=false` (default).

Practical takeaway:

* use a precise query when you know the symbol name
* add `kind` when you know whether you want a function, class, method, etc.
* use `file_pattern` or `language` when a repo is large or polyglot
* use `fuzzy=true` for typos, partials, or snake_case mismatches
* use `semantic=true` for concept-level queries when you don't know the exact symbol name

---

# 8. Token savings: what it means and what it does not mean

jCodeMunch can produce very large token savings because it changes the workflow from:

> read everything to find something

to:

> find something, then read only that

Typical task categories in the project’s own token-savings material show very large reductions for repo exploration, finding specific functions, and reading targeted implementations. ([GitHub][4])

But keep the mental model honest:

* savings happen when the agent actually uses targeted retrieval
* savings are strongest in retrieval-heavy workflows
* installing the MCP is not the same as changing agent behavior

That is why onboarding and prompting matter.

---

# 9. Live token savings counter

If you use Claude Code, you can surface a running savings counter in the status line.

Example:

```text
Claude Sonnet 4.6 | my-project | ░░░░░░░░░░ 0% | 1,280,837 tkns saved · $6.40 saved on Opus
```

The data comes from:

```text
~/.code-index/_savings.json
```

It tracks cumulative token savings and can be used to estimate avoided cost at a given model rate.

The `jcodemunch-mcp receipt` CLI turns the same data into an itemized report: windowed savings from your local transcripts (`--since`/`--until`/`--by-day`), the persistent lifetime meter (which survives client reinstalls), and dollar valuations at current published input-token rates for a model you pick (`--model`, `--rates` to list). The JSON export (`--export json`) includes a `savings_provenance` block chaining the figures to the committed measurement artifacts, so the report carries its own receipts. The meter is conservative by design — every known error term rounds down; see [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md) Chapter 4.

---

# 10. Community savings meter

By default, jCodeMunch can contribute an anonymous savings delta to a global counter.

Only two values are sent:

* token savings delta
* a random anonymous install ID

No code, repo names, file paths, or identifying project data are transmitted, according to the guide. 

To disable it:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"],
      "env": {
        "JCODEMUNCH_SHARE_SAVINGS": "0"
      }
    }
  }
}
```

---

# 11. Local LLM tuning for summaries

You can generate summaries with a local OpenAI-compatible server such as LM Studio by setting:

```json
"env": {
  "OPENAI_API_BASE": "http://127.0.0.1:1234/v1",
  "OPENAI_MODEL": "qwen/qwen3-8b",
  "OPENAI_API_KEY": "local-llm"
}
```

Useful tuning knobs:

* `OPENAI_CONCURRENCY`
* `OPENAI_BATCH_SIZE`
* `OPENAI_MAX_TOKENS`

For hosted OpenAI-compatible providers, use explicit provider selection instead:

```json
"env": {
  "JCODEMUNCH_SUMMARIZER_PROVIDER": "minimax",
  "MINIMAX_API_KEY": "..."
}
```

```json
"env": {
  "JCODEMUNCH_SUMMARIZER_PROVIDER": "glm",
  "ZHIPUAI_API_KEY": "..."
}
```

If you document this section, I would keep it framed as optional power-user tuning, not required setup.

---

# 12. Storage and indexing

By default, indexes live under:

```text
~/.code-index/
```

Typical layout:

```text
~/.code-index/
├── owner-repo.json
└── owner-repo/
    └── src/main.py
```

The JSON index stores metadata, hashes, and symbol records. Raw files are stored separately for precise later retrieval. ([GitHub][3])

---

# 13. Troubleshooting

## “Repository not found”

Use `owner/repo` or a full GitHub URL. For private repos, set `GITHUB_TOKEN`.

## “No source files found”

See [empty-index troubleshooting](TROUBLESHOOTING.md#no-source-files-found--empty-index)
for discovery failures, exclusions, and successful deletion-only refreshes.

## Rate limiting

Set `GITHUB_TOKEN` to increase GitHub API limits.

## AI summaries are missing

Set one of `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_BASE`, `MINIMAX_API_KEY`, or `ZHIPUAI_API_KEY`. You can also force a specific provider with `JCODEMUNCH_SUMMARIZER_PROVIDER`. Without a configured provider, summaries fall back to docstrings or signatures.

## The index seems stale

Use `invalidate_cache` followed by a fresh `index_repo` or `index_folder`.

## The client cannot find the executable

Use `uvx`, or configure the absolute path to `jcodemunch-mcp`.

## Debug logs broke my MCP client

Do not log to stderr during stdio MCP sessions. Use `--log-file` or `JCODEMUNCH_LOG_FILE` instead. ([GitHub][1])

---

# 14. Best practices

1. Start with `suggest_queries` on any unfamiliar repo, then `get_repo_outline`.
2. Use `get_file_outline` before pulling source — see API surface before reading code.
3. Use `search_symbols` before `get_file_content` whenever possible.
4. Use `get_symbol_source` with `symbol_ids[]` or `get_context_bundle` for related items instead of repeated single-symbol calls.
5. Use `search_text` for comments, strings, and non-symbol content.
6. Use `verify: true` when freshness matters.
7. Re-index when the codebase changes materially. Use `index_file` for single-file updates.
8. Tell your agent to prefer jCodeMunch, or it may fall back to old brute-force habits.

---

# 15. Best practices for prompting the agent

Good:

* “Use jcodemunch to locate the authentication flow.”
* “Start with the repo outline, then find the class responsible for retries.”
* “Use symbol search instead of reading full files.”
* “Retrieve only the exact methods related to billing.”
* “Verify the symbol before quoting the implementation.”

Bad:

* “Read the whole repo and tell me what it does.”
* “Open every likely file.”
* “Search manually through source until you find it.”

You are trying to teach the model to **navigate**, not rummage.

---

# 16. Final advice

jCodeMunch works best when you treat it like a precision instrument, not a lucky rabbit’s foot.

Index the repo.
Ask for outlines.
Search by symbol.
Retrieve narrowly.
Batch related symbols.
Re-index when needed.
And most importantly, make your agent use the tools on purpose.

That is where the speed comes from.
That is where the accuracy comes from.
And that is where the ugly token bill finally starts to shrink.


[1]: https://github.com/jgravelle/jcodemunch-mcp "GitHub - jgravelle/jcodemunch-mcp: The leading, most token-efficient MCP server for GitHub source code exploration via tree-sitter AST parsing · GitHub"
[2]: https://raw.githubusercontent.com/jgravelle/jcodemunch-mcp/main/CHANGELOG.md "raw.githubusercontent.com"
[3]: https://raw.githubusercontent.com/jgravelle/jcodemunch-mcp/main/ARCHITECTURE.md "raw.githubusercontent.com"
[4]: https://raw.githubusercontent.com/jgravelle/jcodemunch-mcp/main/TOKEN_SAVINGS.md "raw.githubusercontent.com"
