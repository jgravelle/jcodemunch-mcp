# jcodemunch-mcp

Token-efficient MCP server for GitHub source code exploration via tree-sitter AST parsing.

## Overview

**jcodemunch-mcp** pre-indexes repository source code using tree-sitter AST parsing, extracting a structured catalog of every symbol (function, class, method, constant, type). Each symbol stores only its **signature + one-line summary**, with the ability to retrieve full source on demand.

### Token Savings

| Scenario | Raw dump | codemunch | Savings |
|----------|----------|-----------|---------|
| Explore 500-file repo structure | ~200,000 tokens | ~2,000 tokens | **99%** |
| Find a specific function | ~40,000 tokens | ~200 tokens | **99.5%** |
| Read one function body | ~40,000 tokens | ~500 tokens | **98.7%** |

## Quick Start

### Installation

```bash
pip install jcodemunch-mcp
```

### Configure MCP

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "github-codemunch": {
      "command": "jcodemunch-mcp",
      "env": {
        "GITHUB_TOKEN": "your_github_token",
        "ANTHROPIC_API_KEY": "your_anthropic_key"
      }
    }
  }
}
```

### Usage

1. **Index a repository**:
   ```
   index_repo: { "url": "owner/repo" }
   ```

2. **Search for symbols**:
   ```
   search_symbols: { "repo": "owner/repo", "query": "authenticate" }
   ```

3. **Get file outline**:
   ```
   get_file_outline: { "repo": "owner/repo", "file_path": "src/main.py" }
   ```

4. **Read symbol source**:
   ```
   get_symbol: { "repo": "owner/repo", "symbol_id": "src-main-py::MyClass.login" }
   ```

## Supported Languages

- Python (.py)
- JavaScript (.js, .jsx)
- TypeScript (.ts, .tsx)
- Go (.go)
- Rust (.rs)
- Java (.java)

## Tools

| Tool | Purpose |
|------|---------|
| `index_repo` | Index a repository's source code |
| `list_repos` | List indexed repositories |
| `get_file_tree` | Get repository file structure |
| `get_file_outline` | Get symbols in a file |
| `get_symbol` | Get full source of a symbol |
| `get_symbols` | Batch retrieve multiple symbols |
| `search_symbols` | Search across all symbols |

## Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `GITHUB_TOKEN` | GitHub API authentication | No |
| `ANTHROPIC_API_KEY` | AI summarization | No |
| `CODE_INDEX_PATH` | Custom storage path | No |

## License

MIT

