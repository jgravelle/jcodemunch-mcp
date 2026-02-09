# jCodeMunch MCP
### Make AI agents cheaper and faster on real codebases

![License](https://img.shields.io/badge/license-MIT-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![Polyglot](https://img.shields.io/badge/parsing-tree--sitter-9cf)

**Stop dumping files into context windows. Start retrieving exactly what the agent needs.**

jCodeMunch MCP indexes a local codebase once, then lets MCP-compatible agents (Claude Desktop, OpenClaw, etc.) **discover and retrieve code by symbol** instead of brute-reading files.

---

## 🚀 Proof first: Token savings in the wild

**Repo:** `geekcomputers/Python`  
**Size:** 338 files • 1422 symbols indexed  
**Task:** Find calculator/math implementations

| Approach | Tokens (this run) | What the agent had to do |
|---|---:|---|
| Raw file approach | ~7,500 | Open multiple files blindly and skim |
| jCodeMunch MCP | ~1,449 | `search_symbols(...)` → `get_symbol(...)` |

### Result: **80.7% fewer tokens** (≈5.2× more efficient)

> Cost scales with tokens. Latency often scales with “how much junk the model must read”.  
> jCodeMunch reduces both by turning *search* into *navigation*.

![Token benchmark](benchmark.png)

---

## Why agents need this (and humans benefit too)

Agents waste money when they:
- open entire files just to find one function
- re-read the same code repeatedly
- drown in imports, boilerplate, and unrelated helpers

jCodeMunch gives agents **structured access**:
- **Search symbols** by name/topic
- **Outline files** without loading full contents
- **Retrieve only the exact implementation** of a symbol

Agents don’t need more context. They need **precision context access**.

---

## Architecture at a glance

![Architecture](docs/architecture.png)

**Pipeline**
1. Parse source structure (polyglot parsers)
2. Extract symbols + metadata (names, signatures, byte offsets)
3. Persist a lightweight local index
4. Serve MCP tools for discovery
5. Retrieve exact snippets via byte-offset precision

---

## Quickstart

```bash
git clone https://github.com/jgravelle/jcodemunch-mcp
cd jcodemunch-mcp
pip install -r requirements.txt
```

### Configure your MCP client (Claude Desktop / OpenClaw)
Point the server at **any local folder** containing a codebase. Index once, then query.

---

## Demo

Suggested demo flow:
1. `index_repo(path=...)`
2. `search_symbols(query="calculate")`
3. `get_symbol("...")`

---

## Tool suite

| Tool | Purpose |
|---|---|
| `index_repo` | Index any local codebase folder |
| `search_symbols` | Find symbols by name/topic |
| `get_file_outline` | View a file’s structural “API skeleton” |
| `get_symbol` | Retrieve the exact implementation |

---

## What it’s great for

- Large, messy repos where grepping is painful
- Agentic refactors across many files
- “Where is X implemented?” or “Who calls Y?” exploration
- Fast onboarding and architecture discovery
- Running cheaper agent swarms (OpenClaw-style)

---

## License
MIT
