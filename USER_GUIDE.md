# User Guide

## Installation

```bash
pip install jcodemunch-mcp
```

Verify:

```bash
jcodemunch-mcp --help
```

> **Recommended:** use [`uvx`](https://github.com/astral-sh/uv) instead of `pip install` when configuring MCP clients. `uvx` runs the package on demand without requiring it to be on your system PATH.

Or from source:

```bash
git clone https://github.com/jgravelle/jcodemunch-mcp.git
cd jcodemunch-mcp
pip install -e .
```

---

## Configuration

### Claude Code

The fastest way to add jCodeMunch to Claude Code is a single command:

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp
```

This registers the server at user scope (`~/.claude.json`) so it is available in every project. To limit it to a single project, pass `--scope project`:

```bash
claude mcp add --scope project jcodemunch uvx jcodemunch-mcp
```

To include optional environment variables at the same time:

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp \
  -e GITHUB_TOKEN=ghp_... \
  -e ANTHROPIC_API_KEY=sk-ant-...
```

Restart Claude Code after running the command.

**Manual config** — if you prefer to edit the config file directly:

| Scope   | Path |
| ------- | ---- |
| User (global) | `~/.claude.json` |
| Project | `.claude/settings.json` (in the project root) |

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

Environment variables are optional — see the list in the Claude Desktop section below.

### Claude Desktop

Config file location:

| OS      | Path |
| ------- | ---- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux   | `~/.config/claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add to your `claude_desktop_config.json`:

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

Environment variables are optional:

* `GITHUB_TOKEN` enables private repositories and higher GitHub API rate limits.
* `ANTHROPIC_API_KEY` enables AI-generated summaries via Claude Haiku.
* `ANTHROPIC_MODEL` overrides the Claude model (default: `claude-haiku-4-5-20251001`).
* `GOOGLE_API_KEY` enables AI-generated summaries via Gemini Flash (used if `ANTHROPIC_API_KEY` is not set).
* `GOOGLE_MODEL` overrides the Gemini model (default: `gemini-2.5-flash-lite`).
* If neither key is set, summaries fall back to docstrings or signatures.

Restart Claude Desktop after saving the config.

### Cursor

Open **Cursor Settings** (`Ctrl+Shift+J`) → **Tools & MCP** → **+ New MCP Server** to open `mcp.json`, then add:

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

Save and check **Tools & MCP** — it will confirm whether the server started successfully. You may need to restart Cursor.

> **WSL users:** Configure separately for Windows and your Linux distro, and switch between them via the Tools & MCP panel as needed.

### VS Code

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

### Claude Code Status Line

If you use Claude Code, you can display a live token savings counter in the status bar:

```
Claude Sonnet 4.6 | my-project | ░░░░░░░░░░ 0% | 1,280,837 tkns saved · $6.40 saved on Opus
```

Ask Claude Code to set it up:

> "Add jcodemunch token savings to my status line"

Claude Code will add a segment that reads `~/.code-index/_savings.json` and calculates cost avoided at the Claude Opus rate ($25.00 / 1M tokens). The counter updates automatically after every jcodemunch tool call — no restart required.

To add it manually, read `~/.code-index/_savings.json` and extract `total_tokens_saved`:

```js
// Node.js snippet for a statusline hook
const f = path.join(os.homedir(), '.code-index', '_savings.json');
const total = fs.existsSync(f) ? JSON.parse(fs.readFileSync(f)).total_tokens_saved ?? 0 : 0;
const cost  = (total * 25.00 / 1_000_000).toFixed(2);
if (total > 0) output += ` │ ${total.toLocaleString()} tkns saved · $${cost} saved on Opus`;
```

---

### Google Antigravity

1. Open the Agent pane → click the `⋯` menu → **MCP Servers** → **Manage MCP Servers**
2. Click **View raw config** to open `mcp_config.json`
3. Add the entry below, save, then restart the MCP server from the Manage MCPs pane

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

### Explore a New Repository

```
index_repo: { "url": "fastapi/fastapi" }
get_repo_outline: { "repo": "fastapi/fastapi" }
get_file_tree: { "repo": "fastapi/fastapi", "path_prefix": "fastapi" }
get_file_outline: { "repo": "fastapi/fastapi", "file_path": "fastapi/main.py" }
```

### Explore a Local Project

```
index_folder: { "path": "/home/user/myproject" }
list_repos: {}
get_repo_outline: { "repo": "myproject" }
search_symbols: { "repo": "myproject", "query": "main" }
```

Local folder indexes use stable hashed repo ids under the hood. `list_repos` shows the exact stored id, and bare display-name lookup works when it is unique.

### Find and Read a Function

```
search_symbols: { "repo": "owner/repo", "query": "authenticate", "kind": "function" }
get_symbol: { "repo": "owner/repo", "symbol_id": "src/auth.py::authenticate#function" }
```

### Understand a Class

```
get_file_outline: { "repo": "owner/repo", "file_path": "src/auth.py" }
get_symbols: {
  "repo": "owner/repo",
  "symbol_ids": [
    "src/auth.py::AuthHandler.login#method",
    "src/auth.py::AuthHandler.logout#method"
  ]
}
```

### Verify Source Hasn't Changed

```
get_symbol: {
  "repo": "owner/repo",
  "symbol_id": "src/main.py::process#function",
  "verify": true
}
```

The response `_meta.content_verified` will be `true` if the source matches the stored hash and `false` if it has drifted.

### Search for Non-Symbol Content

```
search_text: { "repo": "owner/repo", "query": "TODO", "file_pattern": "*.py", "context_lines": 1 }
```

Use `search_text` for string literals, comments, configuration values, or anything that is not a symbol name. Results are grouped by file and include optional `before` / `after` context lines for each match.

### Read a File Slice

```
get_file_content: {
  "repo": "owner/repo",
  "file_path": "src/main.py",
  "start_line": 20,
  "end_line": 40
}
```

### Force Re-index

```
invalidate_cache: { "repo": "owner/repo" }
index_repo: { "url": "owner/repo" }
```

---

## Tool Reference

| Tool               | Purpose                       | Key Parameters                                                     |
| ------------------ | ----------------------------- | ------------------------------------------------------------------ |
| `index_repo`       | Index GitHub repository       | `url`, `use_ai_summaries`                                          |
| `index_folder`     | Index local folder            | `path`, `extra_ignore_patterns`, `follow_symlinks`                 |
| `list_repos`       | List all indexed repositories | —                                                                  |
| `get_file_tree`    | Browse file structure         | `repo`, `path_prefix`                                              |
| `get_file_outline` | Symbols in a file             | `repo`, `file_path`                                                |
| `get_file_content` | Cached file content           | `repo`, `file_path`, `start_line`, `end_line`                      |
| `get_symbol`       | Full source of one symbol     | `repo`, `symbol_id`, `verify`, `context_lines`                     |
| `get_symbols`      | Batch retrieve symbols        | `repo`, `symbol_ids`                                               |
| `search_symbols`   | Search symbols                | `repo`, `query`, `kind`, `language`, `file_pattern`, `max_results` |
| `search_text`      | Full-text search              | `repo`, `query`, `file_pattern`, `max_results`, `context_lines`    |
| `get_repo_outline` | High-level overview           | `repo`                                                             |
| `invalidate_cache` | Delete cached index           | `repo`                                                             |

---

## Symbol IDs

Symbol IDs follow the format:

```
{file_path}::{qualified_name}#{kind}
```

Examples:

```
src/main.py::UserService#class
src/main.py::UserService.login#method
src/utils.py::authenticate#function
config.py::MAX_RETRIES#constant
```

IDs are returned by `get_file_outline` and `search_symbols`. Pass them to `get_symbol` or `get_symbols` to retrieve source code. `search_text` returns file-and-line matches, not symbol IDs.

---

## Community Savings Meter

jCodeMunch contributes an anonymous token savings delta to a live global counter at [j.gravelle.us](https://j.gravelle.us) with each tool call. Only two values are ever sent: the tokens saved (a number) and a random anonymous install ID. No code, paths, repo names, or anything identifying is transmitted. Network failures are silent and never affect tool performance.

The anonymous install ID is generated once and stored locally in `~/.code-index/_savings.json`.

To disable, set `JCODEMUNCH_SHARE_SAVINGS=0` in your MCP server env:

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

## Local LLM Tuning Tips

AI summaries can be generated by a local LLM via any OpenAI-compatible server (e.g. [LM Studio](https://lmstudio.ai)). Set `OPENAI_API_BASE` and `OPENAI_MODEL` in your MCP server env to point at it.

**Recommended model:** [Qwen3-8B](https://lmstudio.ai/models/qwen/qwen3-8b) — fast, fits in 12 GB VRAM, and produces quality one-line summaries. In benchmarks it was ~5× faster than Devstral 24B with comparable summary quality.

**Critical for Qwen3:** Set the LM Studio **system prompt** to `/no_think` (and leave the "Enable thinking" toggle ON). Without this, the model wastes GPU time on chain-of-thought reasoning and leaks artifacts into summaries. Disabling thinking via the checkbox instead produces summaries that are too terse.

```json
"env": {
  "OPENAI_API_BASE": "http://127.0.0.1:1234/v1",
  "OPENAI_MODEL": "qwen/qwen3-8b",
  "OPENAI_API_KEY": "local-llm"
}
```

**Performance tuning:**
- `OPENAI_CONCURRENCY` — number of parallel batch requests to the LLM server (default: `1`). Higher values keep the GPU saturated.
- `OPENAI_BATCH_SIZE` — symbols packed per request (default: `10`).
- `OPENAI_MAX_TOKENS` — max output tokens per batch response (default: `500`).

---

## Troubleshooting

**"Repository not found"**
Check the URL format (`owner/repo` or full GitHub URL). For private repositories, set `GITHUB_TOKEN`.

**"No source files found"**
The repository may not contain supported language files (`.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.c`, `.h`, `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`), or files may be excluded by skip patterns.

**Rate limiting**
Set `GITHUB_TOKEN` to increase GitHub API limits (5,000 requests/hour vs 60 unauthenticated).

**AI summaries not working**
Set `ANTHROPIC_API_KEY` (Claude Haiku) or `GOOGLE_API_KEY` (Gemini Flash). Anthropic takes priority if both are set. Without either, summaries fall back to docstrings or signatures.

**Stale index**
Use `invalidate_cache` followed by `index_repo` or `index_folder` to force a clean re-index.

**Encoding issues**
Files with invalid UTF-8 are handled safely using replacement characters.

---

## Storage

Indexes are stored at `~/.code-index/` (override with the `CODE_INDEX_PATH` environment variable):

```
~/.code-index/
├── owner-repo.json       # Index metadata + symbols
└── owner-repo/           # Raw source files
    └── src/main.py
```

---

## Tips

1. Start with `get_repo_outline` to quickly understand the repository structure.
2. Use `get_file_outline` before reading source to understand the API surface first.
3. Narrow searches using `kind`, `language`, and `file_pattern`.
4. Batch-retrieve related symbols with `get_symbols` instead of repeated `get_symbol` calls.
5. Use `search_text` when symbol search does not locate the needed content.
6. Use `verify: true` on `get_symbol` to detect source drift since indexing.
