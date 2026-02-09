# Build Prompt for jcodemunch-mcp

Copy everything below the line into a new Claude Code session opened in `C:\ai\jcodemunch-mcp\`.

---

## Prompt

You are building **jcodemunch-mcp**, a token-efficient MCP server that indexes GitHub repository source code using tree-sitter AST parsing. When an AI assistant needs to understand a codebase, this server provides structured access to symbols (functions, classes, methods, constants, types) with 97%+ token savings over dumping raw files.

### Project Context

This project is a sibling to `github-docs-mcp` (which indexes documentation/Markdown). It follows the same architectural patterns but parses **source code** instead of Markdown. The specification documents are complete — you should build exactly what they describe.

### Reference Documents (read all of these first)

Read these files in order before writing any code:

1. **`SPEC.md`** — Complete technical specification. Contains:
   - All 7 MCP tools with exact input schemas and output formats
   - `Symbol` and `CodeIndex` dataclass definitions
   - Search algorithm with weighted scoring
   - Three-tier summarization strategy
   - File discovery algorithm
   - Error handling table

2. **`ARCHITECTURE.md`** — Architecture and parser design. Contains:
   - Project directory structure (every file and its purpose)
   - `LanguageSpec` dataclass and language registry pattern
   - AST node type mappings for all 6 languages (Python, JS, TS, Go, Rust, Java)
   - Signature/docstring/decorator extraction strategies per language
   - tree-sitter integration details (`tree-sitter-language-pack`)
   - Storage schema and byte-offset content retrieval
   - Comparison table: what maps from docs-mcp to codemunch-mcp

3. **`BUILD_ORDER.md`** — Step-by-step implementation guide. Contains:
   - 6 phases, 20 steps, with dependencies
   - Test checkpoints after each phase
   - Dependency graph showing what blocks what

4. **`pyproject.toml`** — Already configured with correct dependencies and entry point.

### Build Instructions

Follow `BUILD_ORDER.md` exactly. Build in phase order (1 through 6). After each phase, run the test checkpoint to verify correctness before moving to the next phase.

**Phase 1** (Steps 1-5): Scaffold + Python parser — Get `parse_file()` working for Python.
**Phase 2** (Steps 6-7): Storage + Summarizer — Save/load indexes, generate summaries.
**Phase 3** (Steps 8-11): File discovery + index_repo tool — Index a real GitHub repo.
**Phase 4** (Steps 12-16): Query tools — All 6 query tools working.
**Phase 5** (Steps 17-19): MCP server + remaining languages — Server runs, all 6 languages parse.
**Phase 6** (Step 20): README, User Guide, comprehensive tests.

### Quality Standards

- **Follow the specs precisely.** The data models, tool schemas, algorithms, and file structure are all specified. Don't deviate unless you hit a real technical issue (document what and why).
- **Use the same patterns as the spec documents describe.** The architecture is deliberately similar to github-docs-mcp to be familiar.
- **Test at each checkpoint.** Don't proceed to the next phase if the current phase's tests fail.
- **Error handling matters.** Implement the full graceful degradation table from SPEC.md Section 7.
- **Token efficiency is the product.** Every tool response should return the minimum tokens needed. Never include full source code when a signature + summary suffices.

### Key Technical Decisions (already made)

1. **`tree-sitter-language-pack`** — one dependency for all grammars, pre-compiled wheels
2. **`pathspec`** — .gitignore matching
3. **`git/trees?recursive=1`** API — single-call file discovery
4. **Byte-offset content retrieval** — O(1) symbol source reads
5. **Three-tier summarization** — docstring > AI (Haiku) > signature fallback
6. **Storage at `~/.code-index/`** — separate from docs-mcp

### Dependencies (from pyproject.toml)

- `mcp>=1.0.0` — MCP server framework
- `httpx>=0.27.0` — async HTTP client for GitHub API
- `anthropic>=0.40.0` — AI summarization (optional)
- `tree-sitter-language-pack>=0.7.0` — tree-sitter grammars
- `pathspec>=0.12.0` — .gitignore matching

Start by reading all four spec documents, then begin Phase 1, Step 1.

