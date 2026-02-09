# jcodemunch-mcp User Guide

## Installation

### From PyPI

```bash
pip install jcodemunch-mcp
```

### From Source

```bash
git clone https://github.com/yourusername/jcodemunch-mcp.git
cd jcodemunch-mcp
pip install -e .
```

## Configuration

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "github-codemunch": {
      "command": "jcodemunch-mcp",
      "env": {
        "GITHUB_TOKEN": "ghp_xxxxxxxx",
        "ANTHROPIC_API_KEY": "sk-ant-xxxxxxxx"
      }
    }
  }
}
```

### VS Code

Add to `.vscode/settings.json`:

```json
{
  "mcp.servers": {
    "github-codemunch": {
      "command": "jcodemunch-mcp",
      "env": {
        "GITHUB_TOKEN": "ghp_xxxxxxxx"
      }
    }
  }
}
```

## Workflows

### Explore a New Repository

1. **Index the repo**:
   ```
   index_repo: { "url": "fastapi/fastapi" }
   ```

2. **Get file tree**:
   ```
   get_file_tree: { "repo": "fastapi/fastapi", "path_prefix": "fastapi" }
   ```

3. **Explore a file**:
   ```
   get_file_outline: { "repo": "fastapi/fastapi", "file_path": "fastapi/main.py" }
   ```

4. **Search for specific functionality**:
   ```
   search_symbols: { "repo": "fastapi/fastapi", "query": "dependency injection", "max_results": 5 }
   ```

### Find and Understand a Function

1. **Search for the function**:
   ```
   search_symbols: { "repo": "owner/repo", "query": "process_request" }
   ```

2. **Get the file outline** to see context:
   ```
   get_file_outline: { "repo": "owner/repo", "file_path": "src/handlers.py" }
   ```

3. **Read the full source**:
   ```
   get_symbol: { "repo": "owner/repo", "symbol_id": "src-handlers-py::process_request" }
   ```

### Understand a Class and Its Methods

1. **Get file outline** (shows class hierarchy):
   ```
   get_file_outline: { "repo": "owner/repo", "file_path": "src/auth.py" }
   ```

2. **Get all methods at once**:
   ```
   get_symbols: {
     "repo": "owner/repo",
     "symbol_ids": [
       "src-auth-py::AuthHandler.__init__",
       "src-auth-py::AuthHandler.login",
       "src-auth-py::AuthHandler.logout"
     ]
   }
   ```

## Tool Reference

### index_repo

Index a GitHub repository's source code.

**Input**:
```json
{
  "url": "owner/repo",
  "use_ai_summaries": true
}
```

**Output**:
```json
{
  "success": true,
  "repo": "owner/repo",
  "indexed_at": "2025-01-15T10:30:00",
  "file_count": 42,
  "symbol_count": 387,
  "languages": {"python": 20, "typescript": 15}
}
```

### list_repos

List all indexed repositories.

**Output**:
```json
{
  "count": 3,
  "repos": [
    {
      "repo": "owner/repo",
      "indexed_at": "2025-01-15T10:30:00",
      "symbol_count": 387,
      "file_count": 42,
      "languages": {"python": 20}
    }
  ]
}
```

### get_file_tree

Get repository file structure.

**Input**:
```json
{
  "repo": "owner/repo",
  "path_prefix": "src"
}
```

### get_file_outline

Get symbols in a file.

**Input**:
```json
{
  "repo": "owner/repo",
  "file_path": "src/main.py"
}
```

**Output**:
```json
{
  "repo": "owner/repo",
  "file": "src/main.py",
  "language": "python",
  "symbols": [
    {
      "id": "src-main-py::MyClass",
      "kind": "class",
      "name": "MyClass",
      "signature": "class MyClass(BaseClass):",
      "summary": "Handles user authentication.",
      "line": 15,
      "children": [...]
    }
  ]
}
```

### get_symbol

Get full source of a symbol.

**Input**:
```json
{
  "repo": "owner/repo",
  "symbol_id": "src-main-py::MyClass.login"
}
```

### get_symbols

Batch retrieve multiple symbols.

**Input**:
```json
{
  "repo": "owner/repo",
  "symbol_ids": ["id1", "id2", "id3"]
}
```

### search_symbols

Search across all symbols.

**Input**:
```json
{
  "repo": "owner/repo",
  "query": "authenticate",
  "kind": "function",
  "file_pattern": "src/**/*.py",
  "max_results": 10
}
```

## Troubleshooting

### "Repository not found"

- Check that the repository exists and is public (or set GITHUB_TOKEN)
- Verify the URL format: `owner/repo` or full GitHub URL

### "No source files found"

- The repository may not contain supported language files
- Check that files aren't in skip patterns (node_modules, vendor, etc.)

### Rate limiting

Set `GITHUB_TOKEN` for higher rate limits:
- Public repos: 60 requests/hour (no token) → 5000 requests/hour (with token)
- Private repos: token required

### AI summaries not working

Set `ANTHROPIC_API_KEY` for AI-generated summaries. Without it, summaries fall back to docstrings or signatures.

## Storage Location

Indexes are stored at:
- macOS/Linux: `~/.code-index/`
- Windows: `%USERPROFILE%\.code-index\`

Each repository gets:
- `{owner}-{repo}.json` - Index file
- `{owner}-{repo}/` - Raw source files

## Tips

1. **Start with file outline** - Understand a file's API before reading source
2. **Use search** - Find symbols by name or concept
3. **Batch retrieval** - Use `get_symbols` for related symbols
4. **Filter by kind** - Search for just `class` or `function`
5. **Re-index periodically** - Code changes; re-index to stay current

