# jcodemunch — Hermes Agent plugin

This subpackage turns `jcodemunch-mcp` into a first-class
[Hermes Agent](https://hermes-agent.nousresearch.com) plugin. Install
`jcodemunch-mcp` into the same Python environment as Hermes and every
jcodemunch tool becomes available natively inside Hermes (CLI and
gateway) under the `jcm_` prefix — no MCP stdio subprocess, no bridge
CLI, no configuration files to edit.

## Why

- 49 code-exploration tools (tree-sitter AST indexing across 25+ languages)
- Dynamic discovery: new jcodemunch releases automatically expose their
  new tools to Hermes — no plugin update needed
- Lifecycle hooks that mirror the three Claude Code bash hooks from
  `AGENT_HOOKS.md` but run in-process as Python
- Graceful degradation: if the MCP SDK or tree-sitter can't import,
  the plugin logs a warning and Hermes keeps running normally

## Install

The plugin must be installed into **the same Python environment that
runs Hermes**. The Hermes Agent installer typically creates a venv at
`~/.hermes/hermes-agent/venv/`. Find it:

```bash
head -1 "$(which hermes)"
# → #!/home/host/.hermes/hermes-agent/venv/bin/python3
```

Then install directly into that venv:

```bash
# Using uv (works even if the venv has no pip):
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python3 \
    jcodemunch-mcp

# Or using pip if available in the venv:
~/.hermes/hermes-agent/venv/bin/pip install jcodemunch-mcp
```

The plugin is auto-discovered via the `hermes_agent.plugins` entry
point declared in `pyproject.toml` — no config files to edit.

On startup you should see something like:

```
Plugins (1): ✓ jcodemunch v1.21.27 (46 tools, 6 hooks)
```

Check with `/plugins` inside the CLI.

## What's in it

| File          | Role                                                                |
| ------------- | ------------------------------------------------------------------- |
| `__init__.py` | `register(ctx)` — discovers jcodemunch tools & wires hooks          |
| `tools.py`    | Schema conversion + sync handler factory (bridges MCP → Hermes)     |
| `hooks.py`    | Read guard, edit guard, index hook, session context injector        |
| `_bridge.py`  | Async→sync bridge (dedicated worker-thread event loop)              |
| `plugin.yaml` | Manifest (also consumed for manual `~/.hermes/plugins/` installs)   |

## Tool naming

Every jcodemunch tool is exposed with the `jcm_` prefix. When the
model calls `jcm_search_symbols` the plugin dispatches to jcodemunch's
`search_symbols` under the hood. Prefix keeps the namespace clean when
mixed with other code-search tools.

Selected examples:

- `jcm_index_folder` — index a local repo
- `jcm_search_symbols` — fuzzy symbol search
- `jcm_get_symbol_source` — pull a specific function/class body
- `jcm_find_references` / `jcm_find_importers` — reverse lookups
- `jcm_get_blast_radius` — impact analysis for a rename/change
- `jcm_get_hotspots` — high-churn × high-complexity files
- `jcm_get_ranked_context` — LLM-ready context bundle for a task

The canonical tool list lives in `_CANONICAL_TOOL_NAMES` inside
`jcodemunch_mcp/server.py`.

## Lifecycle hooks

The plugin ports the three Claude Code bash hooks from
[`AGENT_HOOKS.md`](../../../AGENT_HOOKS.md) to in-process Python
running inside Hermes. Hermes plugin hooks are **fire-and-forget
observers** — their return values are ignored for every hook except
`pre_llm_call`, which can inject extra context into the current turn's
user message. That means Claude Code's `exit 2` hard-block pattern has
no direct Hermes equivalent; instead the guards record violations
against the current session and the `pre_llm_call` hook surfaces them
as injected remediation context on the next turn — a softer but
arguably friendlier UX.

| Claude Code hook (bash)           | Hermes plugin hook                                                                 | Fidelity  |
|-----------------------------------|------------------------------------------------------------------------------------|-----------|
| Read Guard (`PreToolUse`, exit 2) | `pre_tool_call` observer + `pre_llm_call` next-turn remediation injection          | Soft only |
| Edit Guard (`PreToolUse`, exit 2) | `pre_tool_call` observer + `pre_llm_call` next-turn remediation injection          | Soft only |
| Index Hook (`PostToolUse`)        | `post_tool_call` — fire-and-forget `jcm_index_file` on the plugin's worker loop    | 100%      |
| *(none)*                          | `on_session_start` + `pre_llm_call` — first-turn workflow guidance + repo snapshot | Bonus     |

### 1. Read guard — `pre_tool_call`

Watches `terminal` (shell `grep`/`rg`/`find`/`cat`/`head`/`tail`
against source files), `search_files`, and `read_file` on code paths.
When a violation is detected, the call is logged and recorded against
the session so `pre_llm_call` can nudge the model toward
`jcm_search_symbols` / `jcm_get_symbol_source` / `jcm_search_text`
next turn.

### 2. Edit guard — `pre_tool_call`

Watches `write_file` and `patch` against code files. Logs a warning
and records a hit. Setting `JCODEMUNCH_HERMES_HARD_BLOCK=1` escalates
the log level and makes the next-turn remediation message louder, but
the edit still proceeds — Hermes plugin hooks cannot prevent tool
execution.

### 3. Index hook — `post_tool_call`

After every successful `write_file` or `patch` on a code file, the
hook schedules a background `jcm_index_file` call so subsequent
retrievals stay fresh. This is the one hook that translates perfectly
from the bash script — it runs on the plugin's worker event loop and
never blocks the agent.

### 4. Session context injector — `pre_llm_call`

On the first turn of every session, injects:

- The preferred jcodemunch workflow (`jcm_list_repos` → outline →
  symbol search → source retrieval)
- A snapshot of already-indexed repos (pulled once from
  `jcm_list_repos` and cached for the session)
- Current working directory if it looks like a repo root

On subsequent turns, if the read guard or edit guard recorded hits
since the last turn, a short remediation note is injected naming the
specific tools that should have been used.

### 5. Session start / end

`on_session_start` prewarms the indexed-repo cache in a background
thread so the first `pre_llm_call` injection never blocks. `on_session_end`
drops session-scoped counters and caches.

## Environment variables

| Variable                              | Default  | Effect                                                                     |
| ------------------------------------- | -------- | ---------------------------------------------------------------------------|
| `JCODEMUNCH_HERMES_DISABLE_HOOKS`     | `0`      | Register tools but no hooks                                                |
| `JCODEMUNCH_HERMES_HARD_BLOCK`        | `0`      | Escalate edit-guard warnings (still can't hard-block in Hermes)            |
| `JCODEMUNCH_HERMES_ALLOW_RAW_WRITE`   | `0`      | Silence the edit guard entirely                                            |
| `JCODEMUNCH_HERMES_AUTO_INDEX`        | `1`      | Set to `0` to disable post-edit auto-reindex                               |
| `JCODEMUNCH_HERMES_DEBUG`             | `0`      | Drop the plugin log level from INFO to DEBUG (verbose per-decision trace)  |
| `JCODEMUNCH_HERMES_LOG_DIR`           | (auto)   | Override the log directory (default: `~/.hermes/logs/jcodemunch/`) |

jcodemunch's own env vars (`CODE_INDEX_PATH`, `JCODEMUNCH_USE_AI_SUMMARIES`,
`ANTHROPIC_API_KEY`, etc.) work normally — the plugin doesn't touch them.

## Debug log file

The plugin attaches a dedicated `RotatingFileHandler` to the
`jcodemunch_mcp.hermes` logger on registration, so every log line
emitted from the plugin (including all six hooks and the registration
code path) lands in a predictable file:

    ~/.hermes/logs/jcodemunch/debug.log

This is the authoritative place to check **whether hooks are actually
firing in a given Hermes session**. Every hook entry is logged at INFO
level, so a trace like this appears after a single turn:

```
[2026-04-05 16:54:40] INFO  jcodemunch_mcp.hermes: register() entered — plugin v1.21.27
[2026-04-05 16:54:41] INFO  jcodemunch_mcp.hermes.hooks: register_hooks: done, 6 callbacks attached
[2026-04-05 16:54:41] INFO  jcodemunch_mcp.hermes.hooks: on_session_start fired: session=...
[2026-04-05 16:54:41] INFO  jcodemunch_mcp.hermes.hooks: pre_llm_call fired: session=... is_first_turn=True
[2026-04-05 16:54:41] INFO  jcodemunch_mcp.hermes.hooks: pre_llm_call INJECTED 3 block(s), 2547 chars
[2026-04-05 16:54:41] INFO  jcodemunch_mcp.hermes.hooks: read_guard fired: tool=terminal session=...
[2026-04-05 16:54:41] WARN  jcodemunch_mcp.hermes.hooks: read_guard FLAGGED: terminal ... grep -r TODO src/app.py
```

If the log file is **empty** after a Hermes session, the plugin is not
being loaded at all — check the entry-point install with
`pip show jcodemunch-mcp` and confirm `jcodemunch_mcp.hermes` is
importable from Hermes' Python env.

If the log file shows `register() entered` plus `registered N tools
and 6 hooks` but **no hook-fire lines**, Hermes is discovering the
plugin but not invoking its hooks during turns — which would indicate
a Hermes-side issue, not a plugin bug.

If hook-fire lines appear but the model is still using raw
grep/find/cat on source code, the guidance injection is being ignored
by the LLM. `JCODEMUNCH_HERMES_DEBUG=1` gives even more detail; the
next debugging step is usually to look at the full injected context
and make the `_FIRST_TURN_GUIDANCE` string in `hooks.py` more
prescriptive for your specific model.

The log rotates at 5 MB with 3 backups kept (`debug.log`,
`debug.log.1`, `debug.log.2`, `debug.log.3`).

## Troubleshooting

**Plugin shows 0 tools registered.**
Run `python -c "from jcodemunch_mcp.server import list_tools; print('ok')"`
in the env that runs Hermes. If that fails, the plugin can't import
it either. Check the Hermes logs for the specific import error.

**`cannot be called from a running event loop` errors.**
Shouldn't happen — the bridge runs its own loop on a worker thread.
If you see this, file an issue with the traceback.

**Tool calls hang.**
Default per-call timeout is 600 seconds (jcodemunch indexing can be
slow on large repos). Adjust `DEFAULT_TIMEOUT_SEC` in `tools.py` or
split work between `jcm_index_folder` once and `jcm_index_file` per
edit.

**Index hook doesn't fire.**
Check `JCODEMUNCH_HERMES_AUTO_INDEX` isn't set to `0`, confirm the
edit target is a recognised code extension (see `_CODE_EXTENSIONS` in
`hooks.py`), and run with `JCODEMUNCH_HERMES_DEBUG=1` to see the hook
log its decisions.
