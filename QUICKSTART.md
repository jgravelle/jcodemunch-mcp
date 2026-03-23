# jCodeMunch Quick Start

Get from zero to 95% token savings in three steps.

---

## Step 1 — Install

```bash
pip install jcodemunch-mcp
```

> **Recommended alternative:** use `uvx` instead of `pip install`. It resolves the package on demand and avoids PATH issues where MCP clients can't find the executable.

---

## Step 2 — Add to your MCP client

### Claude Code (one command)

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp
```

Restart Claude Code. Confirm with `/mcp` — you should see `jcodemunch` listed as connected.

### Claude Desktop

Edit the config file for your OS:

| OS      | Path |
|---------|------|
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux   | `~/.config/claude/claude_desktop_config.json` |

Add the `jcodemunch` entry:

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

Restart Claude Desktop.

### Other clients (Cursor, Windsurf, Roo, etc.)

Any MCP-compatible client accepts the same JSON block above in its MCP config file.

---

## Step 3 — Tell Claude to use it

**This step is the most commonly missed.** Installing the server makes the tools
*available* — but Claude defaults to its built-in file tools (Read, Grep, Glob) and
will never touch jCodeMunch without explicit instructions.

Create or edit `~/.claude/CLAUDE.md` (global — applies to every project):

```markdown
## Code Exploration Policy
Always use jCodemunch-MCP tools — never fall back to Read, Grep, Glob, or Bash for code exploration.
- Before reading a file: use get_file_outline or get_file_content
- Before searching: use search_symbols or search_text
- Before exploring structure: use get_file_tree or get_repo_outline
- Call resolve_repo with the current directory first; if not indexed, call index_folder.
```

You can also add the same block to a project-level `CLAUDE.md` in your repo root.

> **Note:** For a comprehensive guide on enforcing these rules through agent hooks and prompt policies, see [AGENT_HOOKS.md](AGENT_HOOKS.md).

---

## First use

1. Open a project in Claude Code (or Claude Desktop).
2. Ask: *"Index this project"* — Claude will call `index_folder` on the current directory.
3. Ask: *"Find the authenticate function"* — Claude calls `search_symbols`, then `get_symbol`. No file reads.

**Verify it's working:** ask *"Is this project indexed?"* — Claude should call `resolve_repo` with the current directory. To see all indexed repos, ask *"What repos do you have indexed?"* — Claude will call `list_repos`.

---

## Quick cheat sheet

| Goal | Tool |
|------|------|
| Index a local project | `index_folder { "path": "/your/project" }` |
| Index a GitHub repo | `index_repo { "url": "owner/repo" }` |
| Re-index one file after editing | `index_file { "path": "/your/project/src/foo.py" }` |
| Find a function by name | `search_symbols { "repo": "...", "query": "funcName" }` |
| Read a specific function | `get_symbol { "repo": "...", "symbol_id": "..." }` |
| See all files + structure | `get_repo_outline { "repo": "..." }` |
| See a file's symbols | `get_file_outline { "repo": "...", "file_path": "..." }` |
| Full-text search | `search_text { "repo": "...", "query": "TODO" }` |
| Find what imports a file | `find_importers { "repo": "...", "file_path": "..." }` |
| Find all references to a name | `find_references { "repo": "...", "identifier": "..." }` |

> **Full tool reference with parameters:** [USER_GUIDE.md §6](USER_GUIDE.md#6-tool-reference)

---

## Troubleshooting

**Claude isn't calling jCodeMunch tools**
→ Check that `CLAUDE.md` exists and contains the Code Exploration Policy above.
→ Run `/mcp` in Claude Code to confirm the server is connected.

**`jcodemunch-mcp` not found**
→ Use `uvx jcodemunch-mcp` in your config instead of the bare command name — it bypasses PATH entirely.

**30% more tokens than without it**
→ The agent is using jCodeMunch *in addition to* native file tools, not *instead of* them. The `CLAUDE.md` policy in Step 3 is the fix.

**Index seems stale for one file**
→ Call `index_file { "path": "/absolute/path/to/file" }` to re-index just that file instantly.

**Index seems stale across the whole project**
→ Re-run `index_folder` with `incremental: false` to force a full rebuild, or call `invalidate_cache`.

**Not sure what's configured?**
→ Run `jcodemunch-mcp config` to see all effective settings at a glance. Add `--check` to also verify that your AI provider package is installed and your index storage is writable.

---

## Keeping the index fresh (large repos)

For large monorepos, re-running `index_folder` after every edit can be slow. Run the **watch daemon** in a separate terminal to automatically re-index when files change:

```bash
# With uvx (note the --with flag for the optional extra)
uvx --with "jcodemunch-mcp[watch]" jcodemunch-mcp watch /path/to/repo

# With pip
pip install "jcodemunch-mcp[watch]"
jcodemunch-mcp watch /path/to/repo
```

The watcher shares the same index storage as the MCP server — no extra configuration needed.

### Auto-watching Claude Code worktrees

If you use Claude Code, each session can create a git worktree. `watch-claude` automatically discovers these worktrees and indexes them — no manual paths needed. There are two discovery modes that can be used independently or together.

#### Hook-driven mode (recommended)

This is the fastest option: zero polling, instant reaction. Claude Code's `WorktreeCreate` and `WorktreeRemove` hooks notify jcodemunch-mcp directly.

**Step 1: Install the hooks.** Add the following to your `~/.claude/settings.json` (`%USERPROFILE%\.claude\settings.json` on Windows). If you already have a `hooks` section, merge these entries into it:

```json
{
  "hooks": {
    "WorktreeCreate": [{
      "matcher": "",
      "hooks": [{"type": "command", "command": "jcodemunch-mcp hook-event create"}]
    }],
    "WorktreeRemove": [{
      "matcher": "",
      "hooks": [{"type": "command", "command": "jcodemunch-mcp hook-event remove"}]
    }]
  }
}
```

> If you installed with `uvx` instead of `pip`, use `uvx jcodemunch-mcp hook-event create` and `uvx jcodemunch-mcp hook-event remove` in the hook commands.

**Step 2: Run watch-claude** in a separate terminal:

```bash
jcodemunch-mcp watch-claude
```

Every time Claude Code creates a worktree, the hook records the event to `~/.claude/jcodemunch-worktrees.jsonl` and `watch-claude` picks it up instantly. When a worktree is removed, the watcher stops and the index is cleaned up.

#### `--repos` mode (no hooks needed)

If you prefer not to install hooks, point `watch-claude` at your git repositories. It polls `git worktree list` every 5 seconds and automatically watches any Claude-created worktrees it finds (those with branches named `claude/*` or `worktree-*`):

```bash
# Watch worktrees across multiple repos
jcodemunch-mcp watch-claude --repos ~/projects/myapp ~/projects/api

# Custom poll interval (seconds)
jcodemunch-mcp watch-claude --repos ~/projects/myapp --poll-interval 10
```

This works with any worktree layout — whether Claude Code puts them in `<repo>/.claude/worktrees/`, `~/.claude-worktrees/`, or a custom location.

#### Combining both modes

If you have hooks installed and also want to cover repos that might have existing worktrees from before the hooks were set up:

```bash
jcodemunch-mcp watch-claude --repos ~/projects/myapp ~/projects/api
```

When a manifest file exists, `watch-claude` uses both hook events and git polling. Worktrees discovered by either method are not double-watched.

#### Shared options

All standard watch options work with `watch-claude`:

```bash
jcodemunch-mcp watch-claude --repos ~/project --debounce 5000 --no-ai-summaries --follow-symlinks
```

---

## Checking your configuration

Run the built-in diagnostic at any time:

```bash
jcodemunch-mcp config          # print all effective settings
jcodemunch-mcp config --check  # also validate prerequisites
```

The output is grouped into four sections:

| Section | What it shows |
|---------|--------------|
| **Core** | Index storage path, file caps, staleness threshold |
| **AI Summarizer** | Which provider is active (Anthropic / Gemini / Local LLM / none), relevant model vars |
| **HTTP Transport** | Transport mode; HOST/PORT/TOKEN/rate-limit only shown when not in stdio mode |
| **Performance & Privacy** | Stats write interval, telemetry sharing, source-root redaction |

`--check` verifies: index storage is writable, the active AI provider's package is installed (`anthropic`, `google-generativeai`, or `httpx`), and HTTP transport packages (`uvicorn`, `starlette`, `anyio`) are present when HTTP mode is configured. Exits non-zero if anything is missing.

---

For the full reference — all env vars, AI summaries, HTTP transport, dbt/SQL support, and more — see [README.md](README.md).
