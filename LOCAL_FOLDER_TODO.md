# Local Folder Indexing for CodeMunch MCP

## Overview
Add `index_folder` tool to index local code directories (not just GitHub repos).

## TODO

### 1. Create index_folder.py tool
- [x] File: `src/jcodemunch_mcp/tools/index_folder.py`
- [x] Walk local directory using `pathlib.rglob()`
- [x] Filter files by extension (reuse LANGUAGE_EXTENSIONS)
- [x] Apply skip patterns (node_modules, .git, etc.)
- [x] Read file contents locally
- [x] Parse with tree-sitter (reuse parse_file)
- [x] Generate summaries (reuse summarize_symbols)
- [x] Save to IndexStore with folder path as key

### 2. Update server.py
- [x] Import index_folder function
- [x] Add "index_folder" tool to list_tools()
- [x] Handle "index_folder" in call_tool()

### 3. Test
- [x] Index a local folder (codemunch itself: 20 files, 123 symbols)
- [x] Verify search_symbols works
- [x] Verify get_file_outline works
- [x] Verify get_symbol works

## Status: ✅ COMPLETE

## Design Decisions

**Repo Identifier:** Uses `local/{folder_name}` as repo key (e.g., `local/jcodemunch-mcp`)

**Storage Path:** Same IndexStore location (~/.code-index/)

**Max Files:** 500 (same as GitHub version)

**Skip Patterns:** Reuse existing SKIP_PATTERNS from index_repo.py

## Files Created/Modified
- `src/jcodemunch_mcp/tools/index_folder.py` (NEW) ✓
- `src/jcodemunch_mcp/server.py` (MODIFY) ✓

## Usage Example

```json
{
  "name": "index_folder",
  "arguments": {
    "path": "C:/ai/my-project",
    "use_ai_summaries": true
  }
}
```

Then reference it as repo `local/my-project` for other tools:
- `search_symbols: { "repo": "local/my-project", "query": "auth" }`
- `get_file_outline: { "repo": "local/my-project", "file_path": "src/main.py" }`

