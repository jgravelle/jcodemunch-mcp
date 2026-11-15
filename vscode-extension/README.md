# jCodeMunch — Auto Reindex + Risk Gutter (VS Code)

A small VS Code extension that does two things:

1. **Auto-reindex on save** — re-indexes each file you save in the editor.
2. **Risk-density gutter** *(new in 0.2.0)* — paints a colored dot in the editor gutter at every risky function/method header. Hover for the per-axis breakdown; green is invisible (signal-to-noise stays high).

## Why

### Auto-reindex on save

Claude Code's PostToolUse hook handles auto-reindexing in its own ecosystem. VS Code-side MCP clients (GitHub Copilot Chat, Continue, Cline, Roo Code, …) don't fire those hooks — so when you edit a file in the editor and another session queries jCodeMunch, the second session sees a stale index. This extension closes the gap by listening for `onDidSaveTextDocument` and shelling out to `jcodemunch-mcp index-file <path>`.

To keep every indexed repo current, including edits made outside VS Code (a branch switch, a pull, another editor), run `jcodemunch-mcp watch-all`. The save hook is a per-file nudge on top of that, and the two work together.

### Risk-density gutter

Most "show me dangerous code" features pick one signal — usually caller count. That's misleading: a well-tested utility with 47 callers is fine; a 47-caller method nobody's tested and just got rewritten is a landmine. The gutter shows the *fused* signal across four axes (complexity / exposure / churn / test_gap), color-coded by composite. Driven by `jcodemunch-mcp file-risk <path>` under the hood.

Refresh on file open + on save. Typing doesn't refresh — cyclomatic doesn't move with whitespace.

Two-line summary: **green = invisible, yellow/orange/red = look here**. Hover any decorated line to see why.

## Requirements

- VS Code 1.85+
- `jcodemunch-mcp >= 1.89.0` on `PATH` (the risk gutter requires `file-risk` from 1.89.0; auto-reindex works against any 1.81.0+)
- A workspace folder that has been indexed at least once (`jcodemunch-mcp index .`)

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `jcodemunch.indexOnSave.enabled` | `true` | Enable/disable auto-reindex |
| `jcodemunch.indexOnSave.command` | `jcodemunch-mcp` | Path to the CLI |
| `jcodemunch.indexOnSave.debounceMs` | `500` | Per-file debounce window |
| `jcodemunch.indexOnSave.exclude` | `[node_modules, .git, dist, build, .venv, venv, __pycache__, *.min.*]` | Glob patterns to skip |
| `jcodemunch.riskGutter.enabled` | `true` | Paint the per-symbol risk-density gutter |
| `jcodemunch.riskGutter.debounceMs` | `600` | Per-file debounce for the gutter refresh on save |

Output appears in the **jCodeMunch** output channel (View → Output → jCodeMunch).

## Install

From the VS Code marketplace:

```
ext install jgravelle.jcodemunch-mcp-vscode
```

Or via the Extensions panel — search for "jCodeMunch".

### Build from source

```bash
cd vscode-extension
npm ci
npm run compile
npm run package
code --install-extension jcodemunch-mcp-vscode-0.2.1.vsix
```

## Issues

File at https://github.com/jgravelle/jcodemunch-mcp/issues — tag `area:vscode`.
