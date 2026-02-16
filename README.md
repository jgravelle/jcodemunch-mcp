# jCodeMunch MCP

### Make AI agents cheaper and faster on real codebases

![License](https://img.shields.io/badge/license-MIT-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![Polyglot](https://img.shields.io/badge/parsing-tree--sitter-9cf)

**Stop dumping files into context windows. Start retrieving exactly what the agent needs.**

jCodeMunch indexes a codebase once using tree-sitter AST parsing, then lets MCP-compatible agents (Claude Desktop, VS Code, etc.) **discover and retrieve code by symbol** instead of brute-reading files. Every symbol stores its signature plus a one-line summary, with full source retrievable on demand via O(1) byte-offset seeking.

> **Part of the Munch Trio** — see [The Munch Trio](#the-munch-trio) below for the full ecosystem including documentation indexing and unified orchestration.

---

## Proof first: Token savings in the wild

**Repo:** `geekcomputers/Python`
**Size:** 338 files, 1,422 symbols indexed
**Task:** Locate calculator / math implementations

| Approach          | Tokens | What the agent had to do              |
| ----------------- | -----: | ------------------------------------- |
| Raw file approach | ~7,500 | Open multiple files and scan manually |
| jCodeMunch MCP    | ~1,449 | `search_symbols()` → `get_symbol()`   |

### Result: **~80% fewer tokens** (~5× more efficient)

> Cost scales with tokens. Latency scales with how much irrelevant code the model must read.
> jCodeMunch reduces both by turning *search* into *navigation*.

---

## Why agents need this

Agents waste money when they:

* Open entire files to find one function
* Re-read the same code repeatedly
* Consume imports, boilerplate, and unrelated helpers

jCodeMunch gives agents **precision context access**:

* Search symbols by name, kind, or language
* Outline files without loading full contents
* Retrieve only the exact implementation of a symbol
* Fall back to full-text search when symbol lookup misses

Agents do not need larger context windows. They need **structured retrieval**.

---

## How it works

1. **Discovery** — files located via GitHub API or local directory walk
2. **Security filtering** — path traversal, secrets, binary detection, `.gitignore`
3. **Parsing** — tree-sitter AST extraction across supported languages
4. **Storage** — JSON index + raw files stored locally (`~/.code-index/`)
5. **Retrieval** — O(1) byte-offset seeking via stable symbol IDs

### Stable Symbol IDs

```
{file_path}::{qualified_name}#{kind}
```

Examples:

* `src/main.py::UserService.login#method`
* `src/utils.py::authenticate#function`

IDs remain stable across re-indexing when path, qualified name, and kind are unchanged.

---

## Installation

### Prerequisites

* Python 3.10+
* pip (or equivalent)

### Install

```bash
pip install git+https://github.com/jgravelle/jcodemunch-mcp.git
```

Verify installation:

```bash
jcodemunch-mcp --help
```

---

## Configure MCP Client

### Claude Desktop / Claude Code

macOS / Linux
`~/.config/claude/claude_desktop_config.json`

Windows
`%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "jcodemunch-mcp",
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

Environment variables are optional:

| Variable            | Purpose                                                              |
| ------------------- | -------------------------------------------------------------------- |
| `GITHUB_TOKEN`      | Higher GitHub API limits and private repository access               |
| `ANTHROPIC_API_KEY` | AI-generated symbol summaries (otherwise docstrings/signatures used) |

---

## Usage Examples

```
index_folder: { "path": "/path/to/project" }
index_repo:   { "url": "owner/repo" }

get_repo_outline: { "repo": "owner/repo" }
get_file_outline: { "repo": "owner/repo", "file_path": "src/main.py" }
search_symbols:   { "repo": "owner/repo", "query": "authenticate" }
get_symbol:       { "repo": "owner/repo", "symbol_id": "src/main.py::MyClass.login#method" }
search_text:      { "repo": "owner/repo", "query": "TODO" }
```

---

## Tools (11)

| Tool               | Purpose                     |
| ------------------ | --------------------------- |
| `index_repo`       | Index a GitHub repository   |
| `index_folder`     | Index a local folder        |
| `list_repos`       | List indexed repositories   |
| `get_file_tree`    | Repository file structure   |
| `get_file_outline` | Symbol hierarchy for a file |
| `get_symbol`       | Retrieve full symbol source |
| `get_symbols`      | Batch retrieve symbols      |
| `search_symbols`   | Search symbols with filters |
| `search_text`      | Full-text search            |
| `get_repo_outline` | High-level repo overview    |
| `invalidate_cache` | Remove cached index         |

All tool responses include a `_meta` envelope with timing and metadata.

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

See **LANGUAGE_SUPPORT.md** for full semantics.

---

## Security

Built-in indexing protections:

* Path traversal prevention
* Symlink escape protection
* Secret file exclusion (`.env`, `*.pem`, etc.)
* Binary detection
* Configurable file size limits

See **SECURITY.md** for details.

---

## Best Use Cases

* Large multi-module repositories
* Agent-driven refactors
* Architecture exploration
* Faster onboarding to unfamiliar codebases
* Token-efficient multi-agent workflows

## Not Intended For

* Language-server features (LSP diagnostics or completions)
* Editing workflows
* Real-time file watching
* Cross-repository global indexing
* Semantic program analysis (parsing is syntactic via AST)

---

## Environment Variables

| Variable            | Purpose                   | Required |
| ------------------- | ------------------------- | -------- |
| `GITHUB_TOKEN`      | GitHub API auth           | No       |
| `ANTHROPIC_API_KEY` | Symbol summary generation | No       |
| `CODE_INDEX_PATH`   | Custom cache path         | No       |

---

## The Munch Trio

jCodeMunch is part of a three-package ecosystem for structured agent retrieval:

| Package               | Purpose                                         |
| --------------------- | ----------------------------------------------- |
| **jcodemunch-mcp**    | Code symbol indexing                            |
| **jdocmunch-mcp**     | Documentation indexing                          |
| **jcontextmunch-mcp** | Cross-source orchestration and context assembly |

When using all three, configuring **jcontextmunch-mcp** alone is sufficient — it automatically orchestrates the others as subprocess MCP servers.

---

## Documentation

* USER_GUIDE.md — workflows and examples
* ARCHITECTURE.md — design and data flow
* SPEC.md — tool and algorithm specifications
* SECURITY.md — security policies
* SYMBOL_SPEC.md — symbol schema
* CACHE_SPEC.md — cache format and invalidation
* LANGUAGE_SUPPORT.md — parser details

---

## License

MIT

