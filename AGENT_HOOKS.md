# Agent Hooks & Prompt Policies for jCodeMunch

> **Quick setup:** [QUICKSTART.md](QUICKSTART.md) · **Full tool reference:** [USER_GUIDE.md](USER_GUIDE.md)

Installing jCodeMunch makes the tools available. It does **not** guarantee your agent will stop opening files like a confused tourist with a flashlight.

This document covers two ways to enforce usage:

1. **Basic Setup (Prompt Policies)** — soft rules for any agent (Claude Code, Cursor, VS Code)
2. **Advanced Setup (Tool Hooks)** — hard redirects at the tool-call level (Claude Code only)

---

## Contents

- [Basic Setup: Prompt Policies](#basic-setup-prompt-policies)
  - [Claude Code (CLAUDE.md)](#claude-code-claudemd)
  - [Other AI Agents](#other-ai-agents)
- [Advanced Setup: Tool Hooks (Claude Code)](#advanced-setup-tool-hooks-claude-code)
  - [Hook Flow](#hook-flow)
  - [1. Read Guard (`jcodemunch_read_guard.sh`)](#1-read-guard-jcodemunch_read_guardsh)
  - [2. Edit Guard (`jcodemunch_edit_guard.sh`)](#2-edit-guard-jcodemunch_edit_guardsh)
  - [3. Index Hook (`jcodemunch_index_hook.sh`)](#3-index-hook-jcodemunch_index_hooksh)
  - [Installation & Setup](#installation--setup)
  - [Logging & Debugging](#logging--debugging)

---

## Basic Setup: Prompt Policies

Prompt policies tell your agent *which* jCodeMunch tool to reach for — so it routes correctly without needing to be blocked. Add these instructions to your agent's system prompt or rules file.

### Claude Code (CLAUDE.md)

Add the following to `~/.claude/CLAUDE.md` (global) or a project-level `CLAUDE.md`:

```markdown
## Code Exploration Policy

Always use jCodemunch-MCP tools for code navigation. Never fall back to Read, Grep, Glob, or Bash for code exploration.

**Start any session:**
1. `resolve_repo { "path": "." }` — confirm the project is indexed. If not: `index_folder { "path": "." }`
2. `suggest_queries` — when the repo is unfamiliar

**Finding code:**
- symbol by name → `search_symbols` (add `kind=`, `language=`, `file_pattern=` to narrow)
- string, comment, config value → `search_text` (supports regex, `context_lines`)
- database columns (dbt/SQLMesh) → `search_columns`

**Reading code:**
- before opening any file → `get_file_outline` first
- one symbol → `get_symbol`; multiple → `get_symbols` (fewer round-trips)
- symbol + its imports → `get_context_bundle`
- specific line range only → `get_file_content` (last resort)

**Repo structure:**
- `get_repo_outline` → dirs, languages, symbol counts
- `get_file_tree` → file layout, filter with `path_prefix`

**Relationships & impact:**
- what imports this file → `find_importers`
- where is this name used → `find_references`
- is this dead code → `check_references`
- file dependency graph → `get_dependency_graph`
- what breaks if I change X → `get_blast_radius`
- class hierarchy → `get_class_hierarchy`
- related symbols → `get_related_symbols`
- diff two snapshots → `get_symbol_diff`

**After editing a file:** `index_file { "path": "/abs/path/to/file" }` to keep the index fresh.
```

### Other AI Agents

Create or edit one of the following files depending on your editor:

- Cursor: **`.cursor/rules`**
- VS Code: **`.github/copilot-instructions.md`**
- Google Antigravity: **`.antigravity/rules`**

```text
jCodemunch-MCP is available. Use it instead of native file tools for all code exploration.

Start any session: resolve_repo → (if missing) index_folder → suggest_queries

Finding code:
  symbol by name       → search_symbols (kind=, language=, file_pattern= to narrow)
  string/comment/TODO  → search_text (is_regex=true for patterns, context_lines for context)
  database columns     → search_columns

Reading code:
  before opening a file → get_file_outline first
  one symbol            → get_symbol
  multiple symbols      → get_symbols (prefer over repeated get_symbol)
  symbol + imports      → get_context_bundle
  line range only       → get_file_content (last resort)

Repo structure:
  overview  → get_repo_outline
  files     → get_file_tree

Relationships & impact:
  what imports a file       → find_importers
  where is a name used      → find_references
  is this dead code         → check_references
  file dependency graph     → get_dependency_graph
  what breaks if I change X → get_blast_radius
  class hierarchy           → get_class_hierarchy

After editing a file: index_file { "path": "/abs/path" } to keep the index fresh.
```

---

## Advanced Setup: Tool Hooks (Claude Code)

> **Disclaimer:** These hooks intercept internal Claude Code tool calls. They modify the core behavior of the agent to heavily prefer jCodeMunch over native file exploration. Use them to strictly enforce jCodeMunch usage, but be aware they may block legitimate edge cases.

Claude Code supports `PreToolUse` and `PostToolUse` hooks. We provide three scripts to secure the entire lifecycle of code reading and writing:

1. **Read Guard** (`PreToolUse`): Blocks grep/find commands so the agent searches via index instead.
2. **Edit Guard** (`PreToolUse`): Warns or blocks the agent before modifying files blindly without using jCodeMunch read tools.
3. **Index Hook** (`PostToolUse`): Automatically re-indexes files immediately after the agent modifies them.

### Hook Flow

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant ReadG as Read Guard<br>(PreToolUse)
    participant EditG as Edit Guard<br>(PreToolUse)
    participant IndexH as Index Hook<br>(PostToolUse)
    participant MCP as jCodeMunch MCP

    Note over CC: Agent wants to read source
    CC->>ReadG: Bash / Grep / Glob
    alt Is Code Exploration
        ReadG-->>CC: Block (Exit 2) + Enforce search/get_symbols
    else Safe Command (e.g. npm test)
        ReadG-->>CC: Pass (Exit 0)
    end

    Note over CC: Agent wants to edit
    CC->>EditG: Edit / Write / MultiEdit
    alt Hard Block Mode
        EditG-->>CC: Block (Exit 2) + Require reading first
    else Soft Gate (Default)
        EditG-->>CC: Pass (Exit 0) + Warning to use MCP
    end

    Note over CC: Agent finishes edit
    CC->>IndexH: Edit completed
    IndexH->>MCP: uvx jcodemunch-mcp index_file
    MCP-->>IndexH: Updated index
    IndexH-->>CC: Pass (Exit 0)
```

### 1. Read Guard (`jcodemunch_read_guard.sh`)

Fires `PreToolUse`. Intercepts `Bash`, `Grep`, and `Glob` when they look like code exploration. Exit code `2` blocks the tool and sends your message back to Claude as feedback. Builds, tests, and git operations pass through untouched. (Note: `Read` is intentionally not blocked, as Edit/Write tools require a prior Read in session state).

**`~/.claude/hooks/jcodemunch_read_guard.sh`:**

```bash
#!/usr/bin/env bash
# PreToolUse guard: redirects code-exploration calls to jCodeMunch.
# Intercepts: Bash (grep/find/cat patterns), Grep, Glob.
# Read is intentionally NOT blocked — Edit/Write tools require a prior Read in session state.
# Exit 0 = pass through. Exit 2 = block with feedback.

# -e is intentionally omitted: helpers must never abort before the block message prints.
set -uo pipefail

INPUT=$(cat)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${SCRIPT_DIR}/jcodemunch_read_guard.log"

# Set JCODEMUNCH_DEBUG=1 to enable logging.
log() { [[ "${JCODEMUNCH_DEBUG:-0}" == "1" ]] && echo "[$(date '+%H:%M:%S')] $*" >> "$LOG" || true; }

# Claude Code sends {"tool_name": "Read", "tool_input": {...}} via stdin.
# Fall back to env var for manual testing.
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || true)
TOOL_NAME="${TOOL_NAME:-${CLAUDE_TOOL_NAME:-}}"

case "$TOOL_NAME" in
  Bash|Grep|Glob) ;;
  *) exit 0 ;;
esac

# Extract payload from tool_input (Claude Code) or top-level (manual testing)
export INPUT_JSON="$INPUT"
PAYLOAD=$(python3 - <<'PY'
import sys, json, os
try:
    d = json.loads(os.environ.get("INPUT_JSON", "{}"))
    # Claude Code wraps args in tool_input; fall back to top-level for manual tests
    inp = d.get("tool_input") or d
    print(" ".join(filter(None, [inp.get("command",""), inp.get("file_path",""), inp.get("pattern",""), inp.get("query","")])))
except Exception:
    pass
PY
) || true

log "TOOL=$TOOL_NAME PAYLOAD=$PAYLOAD"

EXT='py|ts|tsx|js|jsx|mjs|go|rs|java|rb|php|cs|cpp|c|h|swift|kt|scala|dart|lua|r|hs|ex|exs|sh|sql'
SAFE='(npm|yarn|pnpm|cargo|go |pytest|jest|vitest|rspec|mvn|gradle|git |docker|kubectl|uv |pip |brew |jcodemunch|uvx jcodemunch)'

IS_EXPLORATION=0

if [[ "$TOOL_NAME" == "Bash" ]]; then
    echo "$PAYLOAD" | grep -qE "$SAFE" && exit 0
    echo "$PAYLOAD" | grep -qEi "(grep|rg|find|cat|head|tail) .*\.($EXT)" && IS_EXPLORATION=1
fi
[[ "$TOOL_NAME" == "Grep" ]] && IS_EXPLORATION=1
[[ "$TOOL_NAME" == "Glob" ]] && echo "$PAYLOAD" | grep -qEi "\*\.($EXT)" && IS_EXPLORATION=1

[[ "$IS_EXPLORATION" -eq 0 ]] && exit 0

cat >&2 <<'EOF'
⛔ jCodeMunch guard — use structured retrieval instead.

  Discovery
  suggest_queries  → best first step for an unfamiliar repo
  get_repo_outline → high-level overview of the repo
  get_file_tree    → browse directory structure
  get_file_outline → list all symbols in a file (before reading any source)

  Retrieval
  search_symbols   → find a function/class/method by name
  get_symbol       → fetch a specific symbol's implementation
  get_symbols      → fetch multiple symbols in one call
  get_context_bundle → symbol + its imports (+ optional callers) in one call
  get_file_content → read a specific line range (last resort)

  Search
  search_text      → full-text search (strings, comments, TODOs)
  search_columns   → search dbt / SQLMesh / database column metadata

  Relationship & Impact
  find_importers   → what imports a file
  find_references  → where is an identifier used
  check_references → quick dead-code check
  get_dependency_graph → file-level dependency graph (up to 3 hops)
  get_blast_radius → what breaks if this symbol changes
  get_class_hierarchy  → full inheritance chain (ancestors + descendants)
  get_related_symbols  → symbols related via co-location / shared importers
  get_symbol_diff  → diff symbol sets between two indexed repo snapshots

  Utilities
  get_session_stats → token savings and cost-avoided breakdown for this session

Not indexed yet? → index_folder { "path": "/path/to/project" } first.
EOF

log "BLOCKED"
exit 2
```

### 2. Edit Guard (`jcodemunch_edit_guard.sh`)

Fires `PreToolUse`. Intercepts `Edit`, `Write`, and `MultiEdit`. By default, this is a **soft gate** that allows the edit but warns the agent to consult jCodeMunch first. You can enable a hard block to prevent blind edits via `JCODEMUNCH_HARD_BLOCK=1`.

**`~/.claude/hooks/jcodemunch_edit_guard.sh`:**

```bash
#!/usr/bin/env bash
# PreToolUse guard: nudges agent to consult jCodeMunch before editing.
# Intercepts: Edit, Write, MultiEdit.
# SOFT GATE — prints a warning to stderr but always exits 0 (edit proceeds).
# Hard-block mode: set JCODEMUNCH_HARD_BLOCK=1 in your environment.
# Exit 0 = pass through (with optional warning). Exit 2 = block (hard mode only).

# -e is intentionally omitted: helpers must never abort before the message prints.
set -uo pipefail

INPUT=$(cat)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${SCRIPT_DIR}/jcodemunch_edit_guard.log"

# Set JCODEMUNCH_DEBUG=1 to enable logging.
log() { [[ "${JCODEMUNCH_DEBUG:-0}" == "1" ]] && echo "[$(date '+%H:%M:%S')] $*" >> "$LOG" || true; }

TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || true)
TOOL_NAME="${TOOL_NAME:-${CLAUDE_TOOL_NAME:-}}"

log "TOOL=$TOOL_NAME"

case "$TOOL_NAME" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

# Respect explicit allow — skip warning entirely.
[[ "${JCODEMUNCH_ALLOW_RAW_WRITE:-0}" == "1" ]] && exit 0

# Extract the target file path for a more helpful message.
export INPUT_JSON="$INPUT"
TARGET=$(python3 - <<'PY'
import sys, json, os
try:
    d = json.loads(os.environ.get("INPUT_JSON", "{}"))
    inp = d.get("tool_input") or d
    print(inp.get("file_path", ""))
except Exception:
    pass
PY
) || true

if [[ -n "$TARGET" ]]; then
    FILE_HINT="  Target file: $TARGET"
else
    FILE_HINT=""
fi

# Hard-block mode: exits 2 and prevents the edit.
HARD_BLOCK="${JCODEMUNCH_HARD_BLOCK:-0}"

if [[ "$HARD_BLOCK" == "1" ]]; then
    ICON="⛔"
    VERB="blocked"
    EXIT_CODE=2
else
    ICON="⚠️ "
    VERB="allowed — but consider consulting jCodeMunch first"
    EXIT_CODE=0
fi

cat >&2 <<EOF
$ICON jCodeMunch edit guard — raw file edit $VERB.
$FILE_HINT

Before writing to source files, jCodeMunch read tools give you safer context:

  get_symbol / get_symbols     → confirm you are editing the right implementation
  get_file_outline             → see all symbols in the file before touching it
  get_blast_radius             → understand what else breaks if you change this
  find_references              → find all call sites that may need updating too
  search_text                  → locate related strings, comments, or config values

To suppress this warning:  JCODEMUNCH_ALLOW_RAW_WRITE=1
To hard-block all edits:   JCODEMUNCH_HARD_BLOCK=1

Not indexed yet? → index_folder { "path": "/path/to/project" } first.
EOF

log "${VERB} tool=$TOOL_NAME file=$TARGET"
exit $EXIT_CODE
```

### 3. Index Hook (`jcodemunch_index_hook.sh`)

Fires `PostToolUse` for `Edit`, `Write`, and `MultiEdit`. It automatically updates the jCodeMunch index for the modified files so the agent always retrieves fresh code in subsequent steps.

**`~/.claude/hooks/jcodemunch_index_hook.sh`:**

```bash
#!/usr/bin/env bash
# PostToolUse hook: re-indexes a file in jCodeMunch after it is written.
# Triggers on: Edit, Write, MultiEdit.
# Requires: jcodemunch-mcp running and reachable via uvx (or PATH).
# Exit 0 always — index failures are logged but never block the agent.

set -uo pipefail

INPUT=$(cat)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${SCRIPT_DIR}/jcodemunch_index_hook.log"

log() { [[ "${JCODEMUNCH_DEBUG:-0}" == "1" ]] && echo "[$(date '+%H:%M:%S')] $*" >> "$LOG" || true; }

# ── Extract tool name ────────────────────────────────────────────────────────
TOOL_NAME=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_name', ''))
" 2>/dev/null || true)
TOOL_NAME="${TOOL_NAME:-${CLAUDE_TOOL_NAME:-}}"

case "$TOOL_NAME" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

# ── Extract affected file path(s) ────────────────────────────────────────────
# Edit / Write  → tool_input.file_path  (single string)
# MultiEdit     → tool_input.edits[]    (array of {file_path, ...})
export INPUT_JSON="$INPUT"
FILE_PATHS=$(python3 - <<'PY'
import sys, json, os

try:
    d = json.loads(os.environ.get("INPUT_JSON", "{}"))
    inp = d.get("tool_input") or d

    paths = set()

    # Edit / Write
    fp = inp.get("file_path", "")
    if fp:
        paths.add(fp)

    # MultiEdit
    for edit in inp.get("edits", []):
        fp2 = edit.get("file_path", "")
        if fp2:
            paths.add(fp2)

    for p in sorted(paths):
        print(p)
except Exception as e:
    import sys
    print(f"parse-error: {e}", file=sys.stderr)
PY
) || true

if [[ -z "$FILE_PATHS" ]]; then
    log "SKIP tool=$TOOL_NAME — no file_path found"
    exit 0
fi

# ── Determine the repo root (best-effort) ───────────────────────────────────
# Walk up from the file to find the nearest .git, pyproject.toml, package.json,
# go.mod, Cargo.toml, or pom.xml — that becomes the index root.
find_repo_root() {
    local dir
    dir="$(cd "$(dirname "$1")" 2>/dev/null && pwd)" || return 1
    while [[ "$dir" != "/" ]]; do
        for marker in .git pyproject.toml package.json go.mod Cargo.toml pom.xml; do
            [[ -e "$dir/$marker" ]] && { echo "$dir"; return 0; }
        done
        dir="$(dirname "$dir")"
    done
    # Fall back to the file's own directory
    echo "$(cd "$(dirname "$1")" && pwd)"
}

# ── Re-index each affected file ─────────────────────────────────────────────
INDEX_FAILED=0

while IFS= read -r FILE_PATH; do
    [[ -z "$FILE_PATH" ]] && continue

    # Skip files that no longer exist (deleted in this edit).
    if [[ ! -f "$FILE_PATH" ]]; then
        log "SKIP $FILE_PATH — file does not exist (deleted?)"
        continue
    fi

    REPO_ROOT="$(find_repo_root "$FILE_PATH")"
    log "INDEX tool=$TOOL_NAME file=$FILE_PATH root=$REPO_ROOT"

    # Call jCodeMunch's index_file via the MCP CLI.
    # uvx runs jcodemunch-mcp on demand; swap for an absolute path if needed.
    INDEX_OUTPUT=$(uvx jcodemunch-mcp index_file \
        --path "$FILE_PATH" \
        --repo-root "$REPO_ROOT" \
        2>&1) || {
        log "ERROR indexing $FILE_PATH: $INDEX_OUTPUT"
        INDEX_FAILED=1
        continue
    }

    log "OK $FILE_PATH"
done <<< "$FILE_PATHS"

# ── Optional: surface index failures as a non-blocking hint ─────────────────
# PostToolUse hooks cannot block; we print to stderr so Claude Code can
# surface the message as a warning without interrupting the workflow.
if [[ "$INDEX_FAILED" -eq 1 ]]; then
    echo "⚠️  jCodeMunch: one or more files could not be re-indexed." \
         "Run index_folder manually if retrieval seems stale." >&2
fi

exit 0
```

### Installation & Setup

#### Step 1 — Save and make executable

Ensure the hooks folder exists:
```bash
mkdir -p ~/.claude/hooks
```

Save the three scripts above and make them executable:
```bash
chmod +x ~/.claude/hooks/jcodemunch_read_guard.sh
chmod +x ~/.claude/hooks/jcodemunch_edit_guard.sh
chmod +x ~/.claude/hooks/jcodemunch_index_hook.sh
```

#### Step 2 — Wire it up in settings

Merge into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Grep|Glob",
        "hooks": [{"type": "command", "command": "CLAUDE_TOOL_NAME={{tool_name}} ~/.claude/hooks/jcodemunch_read_guard.sh"}]
      },
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": "CLAUDE_TOOL_NAME={{tool_name}} ~/.claude/hooks/jcodemunch_edit_guard.sh"}]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": "CLAUDE_TOOL_NAME={{tool_name}} ~/.claude/hooks/jcodemunch_index_hook.sh"}]
      }
    ],
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

> `WorktreeCreate`/`WorktreeRemove` are only needed if you use `watch-claude`. Skip them if not.

> [!IMPORTANT]
> **Reopen Claude Code** after every change to `~/.claude/settings.json`. Hooks are loaded at startup — a running session will not pick up changes until the app is restarted.

### Step 3 — Verify

Claude Code sends a JSON envelope with `tool_name` and `tool_input` keys. Simulate that format when testing manually:

```bash
# Should print redirect message and exit 2
echo '{"tool_name":"Grep","tool_input":{"pattern":"TODO"}}' \
  | ~/.claude/hooks/jcodemunch_read_guard.sh
echo "exit: $?"

# Should exit 0 — builds are not blocked
echo '{"tool_name":"Bash","tool_input":{"command":"npm test"}}' \
  | ~/.claude/hooks/jcodemunch_read_guard.sh
echo "exit: $?"
```

---

## Logging & Debugging

The hooks are silent by default. Enable logging to see exactly what they intercept and why.

### Enable debug logging

Set `JCODEMUNCH_DEBUG=1` in your shell environment. The scripts will append timestamped lines to:

```
~/.claude/hooks/jcodemunch_read_guard.log
~/.claude/hooks/jcodemunch_edit_guard.log
~/.claude/hooks/jcodemunch_index_hook.log
```

The easiest way is to add it to your shell profile so it persists across sessions:

```bash
# ~/.zshrc or ~/.bashrc
export JCODEMUNCH_DEBUG=1
```

Then reload your shell and **reopen Claude Code** so the new environment variable is inherited by the hook process:

```bash
source ~/.zshrc   # or source ~/.bashrc
# Now reopen Claude Code
```

To enable it for a single manual test without touching your profile:

```bash
JCODEMUNCH_DEBUG=1 \
  echo '{"tool_name":"Grep","tool_input":{"pattern":"TODO"}}' \
  | ~/.claude/hooks/jcodemunch_read_guard.sh
```

### Read the logs

```bash
cat ~/.claude/hooks/jcodemunch_read_guard.log
# [15:04:22] TOOL=Grep PAYLOAD=TODO
# [15:04:22] BLOCKED
```

Live-tail while Claude Code is running:

```bash
tail -f ~/.claude/hooks/jcodemunch_*.log
```

### Log entry reference

| Entry | When written | Meaning |
|---|---|---|
| `TOOL=<name> PAYLOAD=<args>` | After tool name + payload are resolved | Hook ran for this tool call |
| `BLOCKED` | Just before `exit 2` | Guard fired — Claude was redirected |
| `INDEX tool=<name> file=<path>` | Before triggering index_file | Index hook starting |
| `OK <path>` | After successful index_file | File index has been updated |
| `ERROR indexing <path>` | If MCP command fails | Index failed |

### Rotate or clear the log

The log files grow indefinitely. Clear them when they are no longer needed:

```bash
> ~/.claude/hooks/jcodemunch_read_guard.log   # truncate in place
# or
rm ~/.claude/hooks/jcodemunch_*.log           # delete entirely
```

### Disable logging

Logging is **off by default** (`JCODEMUNCH_DEBUG=0`). Remove the export from your profile (or set it back to `0`) and reopen Claude Code.

---

> Full tool reference with parameters: [USER_GUIDE.md §6](USER_GUIDE.md#6-tool-reference)
