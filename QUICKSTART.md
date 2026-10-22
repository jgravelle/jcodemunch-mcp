#### One-click installs:

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_jCodeMunch-007ACC?style=for-the-badge&logo=visualstudiocode&logoColor=white)](vscode:mcp/install?%7B%22name%22%3A%20%22jcodemunch%22%2C%20%22command%22%3A%20%22uvx%22%2C%20%22args%22%3A%20%5B%22jcodemunch-mcp%22%5D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install-24bfa5?style=for-the-badge&logo=visualstudiocode&logoColor=white)](vscode-insiders:mcp/install?%7B%22name%22%3A%20%22jcodemunch%22%2C%20%22command%22%3A%20%22uvx%22%2C%20%22args%22%3A%20%5B%22jcodemunch-mcp%22%5D%7D)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_jCodeMunch-000000?style=for-the-badge&logo=cursor&logoColor=white)](cursor://anysphere.cursor-deeplink/mcp/install?name=jcodemunch&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyJqY29kZW11bmNoLW1jcCJdfQ==)
[![Claude Code](https://img.shields.io/badge/Claude_Code-CLI_install-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](#works-with)
[![Codex CLI](https://img.shields.io/badge/Codex_CLI-Config_install-10a37f?style=for-the-badge&logo=openai&logoColor=white)](#works-with)

Prefer the command line?

```
pip install jcodemunch-mcp
uvx jcodemunch-mcp
```

# jCodeMunch Quick Start

Get from zero to 95% token savings in one command.

---

## The fast way (recommended)

```bash
pip install jcodemunch-mcp
jcodemunch-mcp init
```

`init` walks you through everything interactively:

1. **Detects your MCP clients** (Claude Code, Claude Desktop, Cursor, Windsurf, Continue) and writes the config entry for each
2. **Installs the Claude Code hooks whose output the model never sees** — PostToolUse auto-reindex after an edit, the PreCompact checkpoint and the task-complete note. Nothing is added to your agent's context by default.
3. **Optionally adds guidance** — `--policy` writes the Code Exploration Policy into CLAUDE.md and AGENTS.md (and the Cursor and Windsurf rules); `--guidance` adds the model-facing hooks (the PreToolUse read advice, the SubagentStart briefing and the SessionStart restore). Both put text in front of the model on the turns they apply to.
4. **Optionally indexes your current project**
5. **Audits your agent config files** for token waste — flags bloated CLAUDE.md files, stale symbol references, redundancy between global and project configs, and scope leaks

For non-interactive setups (CI, scripts, dotfiles):

```bash
jcodemunch-mcp init --yes --claude-md global --hooks --index --audit
```

Run `jcodemunch-mcp init --dry-run` to preview what it would do without changing anything. Or try `jcodemunch-mcp init --demo` — walks through the full process without making changes, then prints what *would* have happened.

After `init` completes, restart your MCP client(s). Confirm with `/mcp` in Claude Code — you should see `jcodemunch` listed as connected.

> **Recommended:** use `uvx` instead of `pip install`. It resolves the package on demand and avoids PATH issues where MCP clients can't find the executable.

> **`'uvx' is not recognized` (Windows / Cursor / any client)?** `uvx` ships with [uv](https://docs.astral.sh/uv/). Install it with `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then **fully restart your editor** so it picks up the new PATH. To skip uv entirely: `python -m pip install` the package and launch with `command: "python"`, `args: ["-m", "jcodemunch_mcp"]` instead.

---

## The manual way

If you prefer to set things up yourself, follow these three steps.

### Step 1 — Install

```bash
pip install jcodemunch-mcp
```

### Step 2 — Add to your MCP client

#### Claude Code (one command)

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp
```

Restart Claude Code. Confirm with `/mcp` — you should see `jcodemunch` listed as connected.

#### Claude Desktop

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

#### OpenClaw

**Option A — CLI (one command):**

```bash
openclaw mcp set jcodemunch '{"command":"uvx","args":["jcodemunch-mcp"]}'
```

**Option B — Edit config directly:**

Add the entry to `~/.openclaw/openclaw.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"],
      "transport": "stdio"
    }
  }
}
```

Restart the gateway:

```bash
openclaw gateway restart
```

Verify the server is registered:

```bash
openclaw mcp list
```

**Per-agent routing (optional):** if you run multiple OpenClaw agents, you can restrict which ones get jCodeMunch access:

```json
{
  "agents": {
    "coder": {
      "mcpServers": ["jcodemunch", "filesystem", "github"]
    }
  }
}
```

#### Other clients

Works with **Cursor, Windsurf, Codex CLI, Continue, Cline, Roo Code, Zed, Goose, Hermes Agent, Paperclip** — and any other MCP-compatible client. Most accept the same JSON block above in their MCP config file. Codex CLI uses TOML instead:

```toml
# ~/.codex/config.toml
[mcp_servers.jcodemunch]
command = "uvx"
args = ["jcodemunch-mcp"]
```

### Step 3 — Tell your agent to use it

**This step is the most commonly missed.** Installing the server makes the tools
*available* — but agents default to their built-in file tools and will never touch
jCodeMunch without explicit instructions.

#### Claude Code / Claude Desktop

Create or edit `~/.claude/CLAUDE.md` (global — applies to every project):

```markdown
## Code Exploration Policy
Use jCodemunch-MCP tools where an index is the cheaper route, and the built-in tools (Grep, Glob, Read) where they are.
- Relationships are what the index is for: who imports a symbol (find_references), where a name is used (check_references), what a change breaks (get_blast_radius), who calls a function (get_call_hierarchy), what is unreachable (find_dead_code).
- In a large repository: search_symbols or search_text to find code, get_file_outline and get_symbol_source to read one symbol and not the whole file.
- On a small repository the built-in tools are usually the cheaper route for finding and reading code.
- Before the first jCodemunch call: resolve_repo with the current directory; if not indexed, call index_folder.
```

You can also add the same block to a project-level `CLAUDE.md` in your repo root.

> [!TIP]
> `jcodemunch-mcp init` handles steps 2 and 3 automatically.

> [!IMPORTANT]
> **CLAUDE.md is a soft rule.** It works well under normal conditions, but agents can ignore it when moving fast, under load, or deep in a complex task — not because they forgot, but because native tools feel faster in the moment. If you need reliable enforcement, install the [hook scripts](AGENT_HOOKS.md) with `jcodemunch-mcp init --guidance` — they intercept `Grep`, `Glob`, and `Bash` at the tool-call level and redirect Claude before the shortcut fires.

#### OpenClaw

Create a system prompt file for your agent (e.g. `~/.openclaw/agents/coder.md`) and add the same policy:

```markdown
## Code Exploration Policy
Use jCodemunch-MCP tools where an index is the cheaper route, and the built-in tools (Grep, Glob, Read) where they are.
- Relationships are what the index is for: who imports a symbol (find_references), where a name is used (check_references), what a change breaks (get_blast_radius), who calls a function (get_call_hierarchy), what is unreachable (find_dead_code).
- In a large repository: search_symbols or search_text to find code, get_file_outline and get_symbol_source to read one symbol and not the whole file.
- On a small repository the built-in tools are usually the cheaper route for finding and reading code.
- Before the first jCodemunch call: resolve_repo with the current directory; if not indexed, call index_folder.
```

Then point your agent config at that file in `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "named": {
      "coder": {
        "systemPromptFile": "~/.openclaw/agents/coder.md"
      }
    }
  }
}
```

Without this prompt policy, your OpenClaw agent will have the tools available but never use them.

---

## First use

1. Open a project in your agent (Claude Code, Claude Desktop, OpenClaw, etc.).
2. Ask: *"Index this project"* — the agent will call `index_folder` on the current directory.
3. Ask: *"Find the authenticate function"* — the agent calls `search_symbols`, then `get_symbol_source`. No file reads.

**Verify it's working:** ask *"Is this project indexed?"* — the agent should call `resolve_repo` with the current directory. To see all indexed repos, ask *"What repos do you have indexed?"* — the agent will call `list_repos`.

## Check your token savings

Ask your agent: *"How many tokens has jCodeMunch saved me?"*

The agent will call `get_session_stats`, which returns:

| Field | Meaning |
|-------|---------|
| `session_tokens_saved` | Tokens saved in the current session |
| `total_tokens_saved` | Lifetime tokens saved (persists across sessions) |
| `session_cost_avoided` | Estimated cost avoided this session, broken down by model |
| `total_cost_avoided` | Lifetime cost avoided, broken down by model |
| `tool_breakdown` | Per-tool token savings for the current session |
| `latency_per_tool` | p50/p95/max/error_rate per tool exercised this session (v1.74.0+) |
| `result_cache` | Hit-rate stats for the session result cache |

Lifetime stats persist to `~/.code-index/session_stats.json`. If this file exists, jCodeMunch is working and saving you tokens. If the numbers are zero, the agent is likely still using built-in file tools — revisit Step 3 above.

Want a slowest-tools / coldest-caches view? Ask: *"Run analyze_perf"* — the agent will call the `analyze_perf` tool and report which tools are slow and which caches are cold.

---

## Quick cheat sheet

| Goal | Tool |
|------|------|
| Index a local project | `index_folder { "path": "/your/project" }` |
| Index a GitHub repo | `index_repo { "url": "owner/repo" }` (also accepts full `https://github.com/owner/repo`, `.git`, SSH, or bare `github.com/...` forms) |
| Re-index one file after editing | `index_file { "path": "/your/project/src/foo.py" }` |
| Find a function by name | `search_symbols { "repo": "...", "query": "funcName" }` |
| Read a specific function | `get_symbol_source { "repo": "...", "symbol_id": "..." }` |
| See all files + structure | `get_repo_outline { "repo": "..." }` |
| See a file's symbols | `get_file_outline { "repo": "...", "file_path": "..." }` |
| Full-text search | `search_text { "repo": "...", "query": "TODO" }` |
| Find what imports a file | `find_importers { "repo": "...", "file_path": "..." }` |
| Find all references to a name | `find_references { "repo": "...", "identifier": "..." }` |

> **Full tool reference with parameters:** [USER_GUIDE.md §6](USER_GUIDE.md#6-tool-reference)

---

## Troubleshooting

**Agent isn't calling jCodeMunch tools**
→ Check that your prompt policy exists (CLAUDE.md for Claude, systemPromptFile for OpenClaw) and contains the Code Exploration Policy from Step 3.
→ Claude Code: run `/mcp` to confirm the server is connected.
→ OpenClaw: run `openclaw mcp list` to confirm `jcodemunch` appears.

**Agent uses jCodeMunch in simple tasks but falls back to file reads in complex ones**
→ This is the "pressure bypass" — the agent sees the rule and skips it anyway because native tools feel faster.
→ Claude Code: CLAUDE.md can't stop this. Install the enforcement hooks with `jcodemunch-mcp init --guidance`: [AGENT_HOOKS.md](AGENT_HOOKS.md).
→ OpenClaw: name in your systemPromptFile the questions that go to the index (who uses a symbol, what a change breaks, who calls a function). An absolute rule against the built-in tools costs more than it saves on a small repository.

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

---

## Using this at work?

jCodeMunch is free for personal use. Commercial use needs a paid license — one-time,
not a subscription.

* [**What it saves you**](https://jcodemunch.com/roi.php?utm_source=github&utm_medium=quickstart&utm_campaign=jcm_quickstart_footer) — put your team size and query volume into the ROI calculator
* [**Pricing**](https://jcodemunch.com/?utm_source=github&utm_medium=quickstart&utm_campaign=jcm_quickstart_footer#pricing) — Builder $79 · Studio $349 · Platform $1,999
* [**Live token counter**](https://jcodemunch.com/?utm_source=github&utm_medium=quickstart&utm_campaign=jcm_quickstart_footer) — what the whole user base has saved so far

If jCodeMunch doesn't pay for itself, you don't pay for jCodeMunch.
