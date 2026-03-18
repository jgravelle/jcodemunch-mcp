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

* `JCODEMUNCH_CONTEXT_PROVIDERS=0`
  Disables context-provider enrichment during indexing.

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

1. Open the Agent pane
2. Click the `⋯` menu
3. Choose **MCP Servers** → **Manage MCP Servers**
4. Open **View raw config**
5. Add:

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

Restart the MCP server afterward.

---

# 3. The step people skip, then blame the software for

## Tell your agent to use jCodeMunch

Installing the server makes the tools available.

It does **not** guarantee your agent will stop opening giant files like a confused tourist with a flashlight.

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
list_repos: {}
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
get_symbol: { "repo": "owner/repo", "symbol_id": "src/auth.py::authenticate#function" }
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
get_symbols: {
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

### Search Column Metadata

```
search_columns: { "repo": "owner/repo", "query": "customer_id", "model_pattern": "fact_*" }
```

Use for discovering columns across dbt models, SQLMesh models, or any indexed data models. More efficient than grep for column discovery.

### Find Files That Import a Module

```
find_importers: { "repo": "owner/repo", "file_path": "src/services/AuthService.js" }
```

Answers "what uses this file?". Returns all files that import from the specified file path.

### Find References to a Symbol

```
find_references: { "repo": "owner/repo", "identifier": "bulkImport" }
```

Answers "where is this used?". Returns all files that import or reference a given identifier.

### Get Symbol with Context

```
get_context_bundle: {
  "repo": "owner/repo",
  "symbol_id": "src/auth.py::AuthHandler.login#method",
  "include_callers": true
}
```

Retrieves symbol source plus all imports. Set `include_callers: true` to also see which files import the symbol's file.

### Check Token Savings

```
get_session_stats: {}
```

Returns tokens saved and cost avoided in the current session and all-time.

### Explore Dependencies

```
get_dependency_graph: {
  "repo": "owner/repo",
  "file":": "src/server.py",
  "direction": "imports",
  "depth": 2
}
```

Understand file dependencies. `direction` can be "imports", "importers", or "both".

### Analyze Impact Before Refactoring

```
get_blast_radius: {
  "repo": "owner/repo",
  "symbol": "calculateScore",
  "depth": 2
}
```

Finds all files affected by changing a symbol. Use before renaming or changing function signatures.

### Compare Branches

```
get_symbol_diff: {
  "repo_a": "owner/repo-main",
  "repo_b": "owner/repo-feature"
}
```

Compare symbol sets between two indexed repositories. Index the same repo under two names to compare branches.

### Understand Class Hierarchy

```
get_class_hierarchy: { "repo": "owner/repo", "class_name": "BaseController" }
```

Returns ancestors (base classes) and descendants (subclasses) for any class.

### Find Related Code

```
get_related_symbols: {
  "repo": "owner/repo",
  "symbol_id": "src/auth.py::AuthHandler.login#method",
  "max_results": 10
}
```

Discovers related symbols using same-file co-location, shared importers, and name similarity.

### Get Started with a New Repo

```
suggest_queries: { "repo": "owner/repo" }
```

Great first call when exploring unfamiliar code. Suggests useful search queries, entry points, and index statistics.

### Force Re-index
This is useful when the thing you need is line-oriented rather than symbol-oriented.

---

## Verify source has not drifted

```json
get_symbol: {
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

---

# 6. Tool reference

| #  | Tool                  | Purpose                          | Key Parameters                                                     |
| -- | --------------------- | -------------------------------- | ------------------------------------------------------------------ |
| 1  | `index_repo`          | Index GitHub repository          | `url`, `use_ai_summaries`                                          |
| 2  | `index_folder`        | Index local folder               | `path`, `extra_ignore_patterns`, `follow_symlinks`                 |
| 3  | `list_repos`          | List all indexed repositories    | —                                                                  |
| 4  | `get_file_tree`       | Browse file structure            | `repo`, `path_prefix`                                              |
| 5  | `get_file_outline`    | Symbols in a file                | `repo`, `file_path`                                                |
| 6  | `get_file_content`    | Cached file content              | `repo`, `file_path`, `start_line`, `end_line`                      |
| 7  | `get_symbol`          | Full source of one symbol        | `repo`, `symbol_id`, `verify`, `context_lines`                     |
| 8  | `get_symbols`         | Batch retrieve symbols           | `repo`, `symbol_ids`                                               |
| 9  | `search_symbols`      | Search symbols                   | `repo`, `query`, `kind`, `language`, `file_pattern`, `max_results` |
| 10 | `search_text`         | Full-text search                 | `repo`, `query`, `file_pattern`, `max_results`, `context_lines`    |
| 11 | `search_columns`      | Search column metadata           | `repo`, `query`, `model_pattern`, `max_results`                    |
| 12 | `get_repo_outline`    | High-level overview              | `repo`                                                             |
| 13 | `invalidate_cache`    | Delete cached index              | `repo`                                                             |
| 14 | `find_importers`      | Find files importing a file      | `repo`, `file_path`, `max_results`                                 |
| 15 | `find_references`     | Find files referencing a symbol  | `repo`, `identifier`, `max_results`                                |
| 16 | `get_context_bundle`  | Symbol source + imports          | `repo`, `symbol_id`/`symbol_ids`, `include_callers`                |
| 17 | `get_session_stats`   | Token savings statistics         | —                                                                  |
| 18 | `get_dependency_graph`| File dependency graph            | `repo`, `file`, `direction`, `depth`                               |
| 19 | `get_blast_radius`    | Analyze impact of changes        | `repo`, `symbol`, `depth`                                          |
| 20 | `get_symbol_diff`     | Compare repos/branches           | `repo_a`, `repo_b`                                                 |
| 21 | `get_class_hierarchy` | Class inheritance                | `repo`, `class_name`                                               |
| 22 | `get_related_symbols` | Find related symbols             | `repo`, `symbol_id`, `max_results`                                 |
| 23 | `suggest_queries`     | Suggest search queries           | `repo`                                                             |

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

Practical takeaway:

* use a precise query when you know the symbol name
* add `kind` when you know whether you want a function, class, method, etc.
* use `file_pattern` or `language` when a repo is large or polyglot

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

The repo may not contain supported source files, or everything useful may have been excluded by skip patterns.

## Rate limiting

Set `GITHUB_TOKEN` to increase GitHub API limits.

## AI summaries are missing

Set `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY`. Without either, summaries fall back to docstrings or signatures.

## The index seems stale

Use `invalidate_cache` followed by a fresh `index_repo` or `index_folder`.

## The client cannot find the executable

Use `uvx`, or configure the absolute path to `jcodemunch-mcp`.

## Debug logs broke my MCP client

Do not log to stderr during stdio MCP sessions. Use `--log-file` or `JCODEMUNCH_LOG_FILE` instead. ([GitHub][1])

---

# 14. Best practices

1. Start with `get_repo_outline` when entering an unfamiliar repo.
2. Use `get_file_outline` before pulling source.
3. Use `search_symbols` before `get_file_content` whenever possible.
4. Use `get_symbols` for related items instead of making repeated single-symbol calls.
5. Use `search_text` for comments, strings, and non-symbol content.
6. Use `verify: true` when freshness matters.
7. Re-index when the codebase changes materially.
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
