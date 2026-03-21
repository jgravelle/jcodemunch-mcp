# Installing jCodeMunch MCP from Source (Claude Code + Subscription)

A step-by-step guide for someone who has never installed an MCP server before.
This guide is specifically for **Claude Code with a subscription** (no API keys needed that way).

---

## What is an MCP server?

MCP (Model Context Protocol) is a standard that lets AI tools like Claude Code
talk to external programs. These programs ("MCP servers") run **on your machine**
and expose "tools" — functions that Claude can call during a conversation.

jCodeMunch is an MCP server that indexes your codebase and lets Claude retrieve
specific functions, classes, and symbols instead of reading entire files. This
saves tokens (= money and context window).

The flow looks like this:

```
You ask Claude something
    → Claude decides it needs code
    → Claude calls a jCodeMunch tool (e.g. "get this function")
    → jCodeMunch returns just that function
    → Claude continues with minimal token usage
```

Claude Code manages the MCP server process for you — it starts it when needed
and talks to it over stdin/stdout. You just need to install the code and tell
Claude Code where to find it.

---

## Prerequisites

- **Python 3.10+** — check with `python --version`
- **pip** — comes with Python
- **Claude Code** — already installed and working (subscription, no API key needed)
- **Git** — to clone the repo (you may already have it locally)

---

## Step 1: Install from source

Open a terminal and navigate to the repo:

```bash
cd C:\claude_checkouts\jcodemunch-mcp
```

Install in editable mode with the file-watching feature:

```bash
pip install -e ".[watch]"
```

This installs:
- **Core** — indexing, symbol retrieval, search (all the MCP tools)
- **watch** — auto-reindex when files change on disk

That's everything you need. No API keys, no external services.

### Add the Scripts directory to your PATH (Windows)

pip installs the `jcodemunch-mcp.exe` launcher into Python's `Scripts\` folder.
If that folder is not on your PATH, the command won't be found. Watch the pip
output for a warning like:

```
WARNING: The script jcodemunch-mcp.exe is installed in
'C:\Users\YourName\AppData\Roaming\Python\PythonXYZ\Scripts' which is not on PATH.
```

If you see that warning, add the printed directory to your PATH:

1. Press **Win + R**, type `sysdm.cpl`, press Enter
2. Go to **Advanced** → **Environment Variables**
3. Under **User variables**, select **Path** and click **Edit**
4. Click **New** and paste the directory from the warning
   (e.g. `C:\Users\YourName\AppData\Roaming\Python\Python314\Scripts`)
5. Click **OK** to close all dialogs
6. **Close and reopen your terminal** (the new PATH is only picked up by new
   terminal windows)

Replace `YourName` and `Python314` with the actual values from the pip warning.
Then close and reopen your terminal.

### Verify it works

```bash
jcodemunch-mcp --help
```

You should see usage info with subcommands like `run`, `watch`, `watch-claude`,
etc.

---

## How summaries work without an API key

jCodeMunch can generate short summaries for each function/class in the index.
With an API key, it would send function signatures to an AI service. **You
don't need this.**

Without an API key, jCodeMunch builds summaries from **docstrings** and
function signatures. This means:

- Functions with good docstrings get good summaries
- Functions without docstrings get a summary based on their signature alone
  (name + parameters + return type)

This works well — but it means **important functions should have docstrings**.
You'll configure Claude Code to write them automatically in Step 3.

---

## Step 2: Register the server with Claude Code

Run this one command:

```bash
claude mcp add -s user jcodemunch -- jcodemunch-mcp
```

This tells Claude Code: "when you need jcodemunch, run the `jcodemunch-mcp`
command."

### If that doesn't work (PATH issues)

If Claude Code can't find `jcodemunch-mcp`, use the full path instead.
Find it first:

```bash
where jcodemunch-mcp
```

Then register with the full path (it will be in your Python `Scripts\` folder):

```bash
claude mcp add -s user jcodemunch -- C:\full\path\to\jcodemunch-mcp
```

Note: on Windows, pip creates a small `.exe` shim in `Scripts\` — this is not
a precompiled binary. It's a standard pip-generated launcher (~100 bytes) that
just calls the Python entry point `jcodemunch_mcp.server:main`. All actual
code runs through the auditable Python source.

### Verify it's registered

Start Claude Code and type `/mcp`. You should see `jcodemunch` listed and
connected (green). If it shows as disconnected, restart Claude Code.

---

## Step 3: Configure Claude Code to use jCodeMunch

This is the step most people skip. Installing the server makes the tools
*available*, but Claude defaults to its built-in tools (Read, Grep, Glob)
and will never touch jCodeMunch unless you tell it to.

Create or edit `%USERPROFILE%\.claude\CLAUDE.md` and add:

```markdown
## Code Exploration Policy

Always use jCodeMunch-MCP tools — never fall back to Read, Grep, Glob, or Bash for code exploration.

- Before reading a file: use get_file_outline or get_file_content
- Before searching: use search_symbols or search_text
- Before exploring structure: use get_file_tree or get_repo_outline
- Call list_repos first; if the project is not indexed, call index_folder with the current directory.

## Docstring Policy

jCodeMunch builds symbol summaries from docstrings. Without docstrings,
summaries fall back to the function signature alone, which is less useful
for search and navigation.

When writing or modifying code:
- Write a concise docstring for every public function, method, and class.
- Write a docstring for private functions when their name + signature isn't
  self-explanatory (e.g. `_reconcile_state` yes, `_add(a, b)` no).
- Docstrings should say WHAT the function does and WHY, not repeat the
  parameter types.
- When modifying an existing function that lacks a docstring, add one.
```

You can also add this to a project-level `CLAUDE.md` in a specific repo root
if you only want it for certain projects.

---

## Step 4: First use

1. Open Claude Code in any project
2. Ask: "Index this project"
   — Claude calls `index_folder` on the current directory
3. Ask: "Find the authenticate function"
   — Claude calls `search_symbols`, then `get_symbol` — no file reads
4. Ask: "What repos do you have indexed?"
   — Claude calls `list_repos`

If Claude is still using Read/Grep instead of jCodeMunch tools, double-check
that your CLAUDE.md is in the right place and restart Claude Code.

**Technical note:** CLAUDE.md is a prompt instruction, not a hard constraint.
Claude is a probabilistic model — it will follow the policy *most* of the
time, but occasionally it may still fall back to built-in tools (Read, Grep,
Glob), especially for simple queries or when context is compressed. There is
no way to guarantee 100% compliance — to this day, this is simply how LLMs
behave and there is no fix for it. In practice, strong wording like "Always
use X — never fall back to Y" produces very high adherence. When it does slip,
nothing breaks — it just uses slightly more tokens than necessary.

---

## Step 5: Auto-reindex when files change

By default, the index is a snapshot taken when you run `index_folder`. If you
(or Claude) edit files after that, the index becomes stale.

To keep the index fresh automatically, run the watcher **in a separate
terminal**:

```bash
jcodemunch-mcp watch C:\path\to\your\project
```

**This terminal must stay open while you work.** The watcher is a long-running
process that monitors your project for file changes and incrementally reindexes
only the files that changed. If you close the terminal, the watcher stops and
the index won't update until you either restart it or manually re-run
`index_folder`.

Typical workflow:
1. Open a terminal, run `jcodemunch-mcp watch C:\your\project` — leave it open
2. Open another terminal, `cd C:\your\project`, run `claude` — work normally
3. When done, close both terminals

The watcher shares the same index storage as the MCP server — no extra config
needed.

---

## Optional: Disable telemetry

By default, jCodeMunch sends anonymous usage stats (just a token count +
random UUID — no code, no file paths, no repo names). To disable, register the
server with the opt-out variable:

```bash
claude mcp remove jcodemunch
claude mcp add jcodemunch -e JCODEMUNCH_SHARE_SAVINGS=0 -- jcodemunch-mcp
```

---

## Updating later

Since you installed in editable mode from source:

```bash
cd C:\claude_checkouts\jcodemunch-mcp
git pull
```

That's it — no reinstall needed. The editable install points directly at the
source code. Restart Claude Code to pick up changes.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `/mcp` shows jcodemunch disconnected | Restart Claude Code. Check `jcodemunch-mcp --help` works in a terminal. |
| Claude ignores jCodeMunch, uses Read/Grep | Check CLAUDE.md has the Code Exploration Policy (Step 3). |
| `jcodemunch-mcp` not found | Add the Python Scripts directory to your PATH (see Step 1). Run `where jcodemunch-mcp` to find the exact location. |
| Index seems stale | Run `index_folder` again, or start the watcher (Step 5). |
| Python version error | Need Python 3.10+. Check with `python --version`. |

---

## Using jCodeMunch with the GSD plugin

If you use the [GSD (Get-Shit-Done)](https://github.com/get-shit-done/gsd)
plugin for Claude Code, its subagents (executor, planner, researcher, etc.)
will **not** use jCodeMunch out of the box. There are two reasons:

1. **Tool allowlist** — each GSD agent has a fixed list of tools in its YAML
   frontmatter. None of them include `mcp__jcodemunch__*`, so even if
   instructions say "use jCodeMunch", the subagent cannot call the tools.
2. **Instructions** — GSD subagents read `./CLAUDE.md` from the project root,
   not your global `%USERPROFILE%\.claude\CLAUDE.md`. If your jCodeMunch
   instructions are only in the global file, subagents never see them.

### Fix: add jCodeMunch to GSD agent tool allowlists

GSD agent definitions live in `%USERPROFILE%\.claude\agents\gsd-*.md`. Each
file has a YAML frontmatter block with a `tools:` line. You need to append
`mcp__jcodemunch__*` to that line for every agent that explores code.

**Before** (example from `gsd-executor.md`):

```yaml
tools: Read, Write, Edit, Bash, Grep, Glob
```

**After**:

```yaml
tools: Read, Write, Edit, Bash, Grep, Glob, mcp__jcodemunch__*
```

Here is the complete list of files and their updated `tools:` lines:

| File | Updated `tools:` line |
|------|----------------------|
| `gsd-executor.md` | `Read, Write, Edit, Bash, Grep, Glob, mcp__jcodemunch__*` |
| `gsd-planner.md` | `Read, Write, Bash, Glob, Grep, WebFetch, mcp__context7__*, mcp__jcodemunch__*` |
| `gsd-phase-researcher.md` | `Read, Write, Bash, Grep, Glob, WebSearch, WebFetch, mcp__context7__*, mcp__jcodemunch__*` |
| `gsd-project-researcher.md` | `Read, Write, Bash, Grep, Glob, WebSearch, WebFetch, mcp__context7__*, mcp__jcodemunch__*` |
| `gsd-debugger.md` | `Read, Write, Edit, Bash, Grep, Glob, WebSearch, mcp__jcodemunch__*` |
| `gsd-codebase-mapper.md` | `Read, Bash, Grep, Glob, Write, mcp__jcodemunch__*` |
| `gsd-verifier.md` | `Read, Write, Bash, Grep, Glob, mcp__jcodemunch__*` |
| `gsd-plan-checker.md` | `Read, Bash, Glob, Grep, mcp__jcodemunch__*` |
| `gsd-integration-checker.md` | `Read, Bash, Grep, Glob, mcp__jcodemunch__*` |
| `gsd-nyquist-auditor.md` | `Read, Write, Edit, Bash, Glob, Grep, mcp__jcodemunch__*` |
| `gsd-roadmapper.md` | `Read, Write, Bash, Glob, Grep, mcp__jcodemunch__*` |
| `gsd-research-synthesizer.md` | `Read, Write, Bash, mcp__jcodemunch__*` |

Open each file, find the `tools:` line, and append `, mcp__jcodemunch__*` at
the end. The wildcard `*` covers all jCodeMunch tools
(`get_file_outline`, `search_symbols`, `get_symbol`, etc.) so you don't need
to list them individually.

### Also: project-level CLAUDE.md

Even with the tools unlocked, subagents need instructions telling them to
prefer jCodeMunch over built-in tools. GSD subagents read `./CLAUDE.md` from
the **project root**, not the global one. If your Code Exploration Policy
(from Step 3) is only in `%USERPROFILE%\.claude\CLAUDE.md`, copy it into a
`CLAUDE.md` at the root of each project where you want GSD subagents to use
jCodeMunch.

### Caution

These agent files are managed by the GSD plugin. When you update GSD
(`/gsd:update`), your changes may be overwritten. After each GSD update,
check whether the `tools:` lines still include `mcp__jcodemunch__*` and
re-apply if needed.
