## Cut code-reading token costs by up to **99%**

Most AI agents explore repositories the expensive way:
open entire files → skim thousands of irrelevant lines → repeat.

**jCodeMunch indexes a codebase once and lets agents retrieve only the exact symbols they need** — functions, classes, methods, constants — with byte-level precision.

| Task                   | Traditional approach | With jCodeMunch |
| ---------------------- | -------------------- | --------------- |
| Find a function        | ~40,000 tokens       | ~200 tokens     |
| Understand module API  | ~15,000 tokens       | ~800 tokens     |
| Explore repo structure | ~200,000 tokens      | ~2k tokens      |

Index once. Query cheaply forever.  
Precision context beats brute-force context.

---

# jCodeMunch MCP

### Structured retrieval for serious AI agents

![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![Polyglot](https://img.shields.io/badge/parsing-tree--sitter-9cf)
[![PyPI version](https://img.shields.io/pypi/v/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)

> ## Commercial licenses
> jCodeMunch-MCP is **free for non-commercial use**.  Tip jar: (https://gravelle.gumroad.com/coffee)
> 
> **Commercial use requires a paid license.**
>
> **jCodeMunch-only licenses**
> - [Builder](https://gravelle.gumroad.com/l/jCodeMunchMCP_builder) — 1 developer
> - [Studio](https://gravelle.gumroad.com/l/jCodeMunchMCP_studio) — up to 5 developers
> - [Platform](https://gravelle.gumroad.com/l/jCodeMunchMCP_platform) — org-wide internal deployment
>
> **Want both code and docs retrieval?**
> - [Munch Duo Builder Bundle](https://gravelle.gumroad.com/l/MunchDuoBuilderBundle)
> - [Munch Duo Studio Bundle](https://gravelle.gumroad.com/l/MunchDuoStudioBundle)
> - [Munch Duo Platform Bundle](https://gravelle.gumroad.com/l/MunchDuoPlatformBundle)

**Stop dumping files into context windows. Start retrieving exactly what the agent needs.**

jCodeMunch indexes a codebase once using tree-sitter AST parsing, then allows MCP-compatible agents (Claude Desktop, VS Code, Google Antigravity, and others) to **discover and retrieve code by symbol** instead of brute-reading files.

Every symbol stores:
- Signature
- Kind
- Qualified name
- One-line summary
- Byte offsets into the original file

Full source is retrieved on demand using O(1) byte-offset seeking.

---

## Proof: Token savings in the wild

**Repo:** `geekcomputers/Python`  
**Size:** 338 files, 1,422 symbols indexed  
**Task:** Locate calculator / math implementations  

| Approach          | Tokens | What the agent had to do              |
| ----------------- | -----: | ------------------------------------- |
| Raw file approach | ~7,500 | Open multiple files and scan manually |
| jCodeMunch MCP    | ~1,449 | `search_symbols()` → `get_symbol()`   |

### Result: **~80% fewer tokens** (~5× more efficient)

Cost scales with tokens.  
Latency scales with irrelevant context.  

jCodeMunch turns search into navigation.

---

## Why agents need this

Agents waste money when they:

- Open entire files to find one function
- Re-read the same code repeatedly
- Consume imports, boilerplate, and unrelated helpers

jCodeMunch provides precision context access:

- Search symbols by name, kind, or language
- Outline files without loading full contents
- Retrieve exact symbol implementations only
- Fall back to full-text search when necessary

Agents do not need larger context windows.  
They need structured retrieval.

---

## How it works

1. **Discovery** — GitHub API or local directory walk  
2. **Security filtering** — traversal protection, secret exclusion, binary detection  
3. **Parsing** — tree-sitter AST extraction  
4. **Storage** — JSON index + raw files stored locally (`~/.code-index/`)  
5. **Retrieval** — O(1) byte-offset seeking via stable symbol IDs  

### Stable Symbol IDs

```
{file_path}::{qualified_name}#{kind}
```

Examples:

- `src/main.py::UserService.login#method`
- `src/utils.py::authenticate#function`

IDs remain stable across re-indexing when path, qualified name, and kind are unchanged.

---

## Installation

### Prerequisites

- Python 3.10+
- pip

### Install

```bash
pip install jcodemunch-mcp
```

Verify:

```bash
jcodemunch-mcp --help
```

---

## Configure MCP Client

> **PATH note:** MCP clients often run with a limited environment where `jcodemunch-mcp` may not be found even if it works in your terminal. Using [`uvx`](https://github.com/astral-sh/uv) is the recommended approach — it resolves the package on demand without requiring anything to be on your system PATH. If you prefer `pip install`, use the absolute path to the executable instead:
> - **Linux:** `/home/<username>/.local/bin/jcodemunch-mcp`
> - **macOS:** `/Users/<username>/.local/bin/jcodemunch-mcp`
> - **Windows:** `C:\\Users\\<username>\\AppData\\Roaming\\Python\\Python3xx\\Scripts\\jcodemunch-mcp.exe`

### Claude Code

The fastest way to add jCodeMunch to Claude Code is a single command:

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp
```

This registers the server at user scope (`~/.claude.json`) so it is available in every project. To add it to a specific project only, pass `--scope project`:

```bash
claude mcp add --scope project jcodemunch uvx jcodemunch-mcp
```

To include optional environment variables (e.g. `GITHUB_TOKEN` or `ANTHROPIC_API_KEY`):

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp \
  -e GITHUB_TOKEN=ghp_... \
  -e ANTHROPIC_API_KEY=sk-ant-...
```

Restart Claude Code after adding the server.

**Manual config** — if you prefer to edit the config file directly, the relevant files are:

| Scope   | Path |
| ------- | ---- |
| User (global) | `~/.claude.json` |
| Project | `.claude/settings.json` (in the project root) |

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

### Claude Desktop

Config file location:

| OS      | Path |
| ------- | ---- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux   | `~/.config/claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

**Minimal config (no API keys needed):**

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

**With optional AI summaries and GitHub auth:**

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

**With debug logging (useful when diagnosing why files are not indexed):**

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

> Logging flags can also be set via env vars `JCODEMUNCH_LOG_LEVEL` and `JCODEMUNCH_LOG_FILE`. Always use `--log-file` (or the env var) when debugging — writing logs to stderr can corrupt the MCP stdio stream in some clients.

After saving the config, **restart Claude Desktop** for the server to appear.

### Google Antigravity

1. Open the Agent pane → click the `⋯` menu → **MCP Servers** → **Manage MCP Servers**
2. Click **View raw config** to open `mcp_config.json`
3. Add the entry below, save, then restart the MCP server from the Manage MCPs pane

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

Environment variables are optional:

| Variable             | Purpose                                                  |
| -------------------- | -------------------------------------------------------- |
| `GITHUB_TOKEN`       | Higher GitHub API limits / private access                |
| `ANTHROPIC_API_KEY`  | AI-generated summaries via Claude Haiku (takes priority) |
| `ANTHROPIC_BASE_URL` | Third-party Anthropic-compatible endpoints (e.g. z.ai)   |
| `GOOGLE_API_KEY`     | AI-generated summaries via Gemini Flash                  |

---

## Usage Examples

```
index_folder: { "path": "/path/to/project" }
index_repo:   { "url": "owner/repo" }

get_repo_outline: { "repo": "owner/repo" }
get_file_outline: { "repo": "owner/repo", "file_path": "src/main.py" }
get_file_content: { "repo": "owner/repo", "file_path": "src/main.py", "start_line": 10, "end_line": 25 }
search_symbols:   { "repo": "owner/repo", "query": "authenticate" }
get_symbol:       { "repo": "owner/repo", "symbol_id": "src/main.py::MyClass.login#method" }
search_text:      { "repo": "owner/repo", "query": "TODO", "context_lines": 1 }
```

Local folder indexes are stored with stable hashed repo ids. Use `list_repos` to inspect the exact id, or the bare display name when it is unique.

---

## Tools (12)

| Tool               | Purpose                     |
| ------------------ | --------------------------- |
| `index_repo`       | Index a GitHub repository   |
| `index_folder`     | Index a local folder        |
| `list_repos`       | List indexed repositories   |
| `get_file_tree`    | Repository file structure   |
| `get_file_outline` | Symbol hierarchy for a file |
| `get_file_content` | Retrieve cached file content |
| `get_symbol`       | Retrieve full symbol source |
| `get_symbols`      | Batch retrieve symbols      |
| `search_symbols`   | Search symbols with filters |
| `search_text`      | Full-text search with context |
| `get_repo_outline` | High-level repo overview    |
| `invalidate_cache` | Remove cached index         |

Every tool response includes a `_meta` envelope with timing, token savings, and cost avoided:

```json
"_meta": {
  "timing_ms": 4.3,
  "tokens_saved": 48153,
  "total_tokens_saved": 1280837,
  "cost_avoided": { "claude_opus": 1.2038, "gpt5_latest": 0.4815 },
  "total_cost_avoided": { "claude_opus": 32.02, "gpt5_latest": 12.81 }
}
```

`total_tokens_saved` and `total_cost_avoided` accumulate across all tool calls and persist to `~/.code-index/_savings.json`.

---

## Recent Updates

**v0.2.10** — Pin `mcp<1.10.0` to prevent Windows `win32api` DLL crash on startup
**v0.2.9** — Community savings meter: anonymous token savings shared to a live global counter at j.gravelle.us (opt-out via `JCODEMUNCH_SHARE_SAVINGS=0`); updated model pricing (Opus $25/1M, GPT-5 $10/1M)
**v0.2.8** — Estimated cost avoided added to every `_meta` response (`cost_avoided`, `total_cost_avoided`)
**v0.2.7** — Security fix: `.claude/` excluded from sdist; structural CI guardrails prevent credential bundling
**v0.2.5** — Path traversal hardening in `IndexStore`; `jcodemunch-mcp --help` now works
**v0.2.4** — Live token savings counter (`tokens_saved`, `total_tokens_saved` in every `_meta`)
**v0.2.3** — Google Gemini Flash support (`GOOGLE_API_KEY`); auto-selects between Anthropic and Gemini
**v0.2.2** — PHP language support

---

## Supported Languages

| Language   | Extensions    | Symbol Types                            |
| ---------- | ------------- | --------------------------------------- |
| Python     | `.py`         | function, class, method, constant, type |
| JavaScript | `.js`, `.jsx` | function, class, method, constant       |
| TypeScript | `.ts`, `.tsx` | function, class, method, constant, type |
| Go         | `.go`         | function, method, type, constant        |
| Rust       | `.rs`         | function, type, impl, constant          |
| Java       | `.java`       | method, class, type, constant           |
| PHP        | `.php`        | function, class, method, type, constant |
| Dart       | `.dart`       | function, class, method, type           |
| C#         | `.cs`         | class, method, type, record             |
| C          | `.c`          | function, type, constant                |
| C++        | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`, `.h`* | function, class, method, type, constant |
| Elixir     | `.ex`, `.exs` | class (module/impl), type (protocol/@type/@callback), method, function |
| Ruby       | `.rb`, `.rake` | class, type (module), method, function |
| SQL        | `.sql`         | function (CREATE FUNCTION, CTE, dbt macro/test/materialization), type (CREATE TABLE/VIEW/SCHEMA/INDEX, dbt snapshot) |

\* `.h` is parsed as C++ first, then falls back to C when no C++ symbols are extracted.

See LANGUAGE_SUPPORT.md for full semantics.

---

## Security

Built-in protections:

- Path traversal prevention (owner/name sanitization + `_safe_content_path` enforcement)
- Symlink escape protection
- Secret file exclusion (`.env`, `*.pem`, etc.)
- Binary detection
- Configurable file size limits

See SECURITY.md for details.

---

## Best Use Cases

- Large multi-module repositories  
- Agent-driven refactors  
- Architecture exploration  
- Faster onboarding  
- Token-efficient multi-agent workflows  

---

## Not Intended For

- LSP diagnostics or completions  
- Editing workflows  
- Real-time file watching  
- Cross-repository global indexing  
- Semantic program analysis  

---

## Local LLMs (Ollama / LM Studio)

You can use local, privacy-preserving AI models to generate summaries by providing an OpenAI-compatible endpoint.

For **Ollama**, run a model locally, then configure the MCP server:
```json
"env": {
  "OPENAI_API_BASE": "http://localhost:11434/v1",
  "OPENAI_MODEL": "qwen3-coder"
}
```

For **LM Studio**, ensure the Local Server is running (usually on port 1234):
```json
"env": {
  "OPENAI_API_BASE": "http://127.0.0.1:1234/v1",
  "OPENAI_MODEL": "openai/gpt-oss-20b"
}
```

> [!TIP]
> **Performance Note:** Local models can be slow to load into memory on their first request, potentially causing the MCP server to time out and fall back to generic signature summaries. It is highly recommended to **pre-load the model** in Ollama or LM Studio before starting the server, or increase the `OPENAI_TIMEOUT` environment variable (e.g., to `"120.0"`) to allow more time for generation.

---

## Environment Variables

| Variable                    | Purpose                   | Required |
| --------------------------- | ------------------------- | -------- |
| `GITHUB_TOKEN`              | GitHub API auth           | No       |
| `ANTHROPIC_API_KEY`         | Symbol summaries via Claude Haiku (takes priority) | No       |
| `ANTHROPIC_BASE_URL`        | Third-party Anthropic-compatible endpoints (e.g. z.ai) | No       |
| `ANTHROPIC_MODEL`           | Model name for Claude summaries (default: `claude-haiku-4-5-20251001`) | No       |
| `GOOGLE_API_KEY`            | Symbol summaries via Gemini Flash | No       |
| `GOOGLE_MODEL`              | Model name for Gemini summaries (default: `gemini-2.5-flash-lite`) | No       |
| `OPENAI_API_BASE`           | Base URL for local LLMs (e.g. `http://localhost:11434/v1`) | No |
| `OPENAI_API_KEY`            | API key for local LLMs (default: `local-llm`) | No |
| `OPENAI_MODEL`              | Model name for local LLMs (default: `qwen3-coder`) | No |
| `OPENAI_TIMEOUT`            | Timeout in seconds for local requests (default: `60.0`) | No |
| `OPENAI_BATCH_SIZE`         | Symbols per summarization request (default: `10`) | No |
| `OPENAI_CONCURRENCY`        | Max parallel batch requests (default: `1`) | No |
| `OPENAI_MAX_TOKENS`         | Max output tokens per batch response (default: `500`) | No |
| `CODE_INDEX_PATH`           | Custom cache path         | No       |
| `JCODEMUNCH_MAX_INDEX_FILES`| Maximum files to index per repo/folder (default: `10000`) | No |
| `JCODEMUNCH_SHARE_SAVINGS`  | Set to `0` to disable anonymous community token savings reporting | No       |
| `JCODEMUNCH_LOG_LEVEL`      | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `WARNING`) | No       |
| `JCODEMUNCH_LOG_FILE`       | Path to log file. If unset, logs go to stderr. Use a file to avoid polluting MCP stdio. | No       |

### Community Savings Meter

Each tool call contributes an anonymous delta to a live global counter at [j.gravelle.us](https://j.gravelle.us). Only two values are ever sent: the tokens saved (a number) and a random anonymous install ID — never code, paths, repo names, or anything identifying. The anon ID is generated once and stored in `~/.code-index/_savings.json`.

To disable, set `JCODEMUNCH_SHARE_SAVINGS=0` in your MCP server env.

---

## Documentation

- USER_GUIDE.md
- ARCHITECTURE.md
- SPEC.md
- SECURITY.md
- LANGUAGE_SUPPORT.md

---

## Star History

<a href="https://www.star-history.com/#jgravelle/jcodemunch-mcp&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=jgravelle/jcodemunch-mcp&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=jgravelle/jcodemunch-mcp&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=jgravelle/jcodemunch-mcp&type=date&legend=top-left" />
 </picture>
</a>

---

## License (Dual Use)

This repository is **free for non-commercial use** under the terms below.  
**Commercial use requires a paid commercial license.**

---

## Copyright and License Text

Copyright (c) 2026 J. Gravelle

### 1. Non-Commercial License Grant (Free)

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to use, copy, modify, merge, publish, and distribute the Software for **personal, educational, research, hobby, or other non-commercial purposes**, subject to the following conditions:

1. The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

2. Any modifications made to the Software must clearly indicate that they are derived from the original work, and the name of the original author (J. Gravelle) must remain intact. He's kinda full of himself.

3. Redistributions of the Software in source code form must include a prominent notice describing any modifications from the original version.

### 2. Commercial Use

Commercial use of the Software requires a separate paid commercial license from the author.

“Commercial use” includes, but is not limited to:

- Use of the Software in a business environment
- Internal use within a for-profit organization
- Incorporation into a product or service offered for sale
- Use in connection with revenue generation, consulting, SaaS, hosting, or fee-based services

For commercial licensing inquiries, contact:  
j@gravelle.us | https://j.gravelle.us

Until a commercial license is obtained, commercial use is not permitted.

### 3. Disclaimer of Warranty

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHOR OR COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
