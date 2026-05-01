# Using jCodeMunch with headless Claude (`claude -p`)

jCodeMunch shines in interactive Claude Code, but the same retrieval surface is
available from **headless** Claude — the `claude -p` mode used for CI bots, batch
refactors, fan-out agents, and "chat with your repo" services. Slice-level
retrieval inside the subprocess means the model never receives giant file dumps,
so token costs stay low even for fully automated workloads.

This page is the canonical recipe.

---

## TL;DR — use `jragmunch`

The opinionated path is the [**jragmunch CLI**](https://github.com/jgravelle/jragmunch-cli):

```bash
pip install jragmunch
jragmunch doctor                                      # verify wiring
jragmunch ask "how does auth work in this repo"       # slice-level Q&A
jragmunch review --base main                          # diff-aware PR review
jragmunch refactor "rename foo to bar" --targets foo  # fan-out batch refactor
jragmunch tests --max 10                              # generate tests for untested symbols
jragmunch sweep "TODO\(remove\)" --action remove      # pattern-driven cleanup
```

It wraps `claude -p` with jCodeMunch pre-wired, sane allowlists, and structured
JSON output.

**Billing is subscription-by-default**: jragmunch strips `ANTHROPIC_API_KEY`
from the subprocess env before spawning `claude`, so the CLI uses your
Max/Pro Claude OAuth login while respecting their TOS, and you pay $0 in
dollars. Pass `--use-api` to opt in to billing via the Anthropic API.

If you want the raw recipe, read on.

---

## The raw recipe

### 1. Make sure `claude` and `jcodemunch-mcp` are both installed

```bash
npm install -g @anthropic-ai/claude-code
pip install jcodemunch-mcp
claude mcp add jcodemunch jcodemunch-mcp     # registers the MCP server
```

### 2. Index the repo once

```bash
cd /path/to/repo
jcodemunch-mcp index .
```

### 3. Invoke `claude -p` with the right flags

```bash
claude -p "Explain how auth works here." \
  --allowedTools "mcp__jcodemunch__*,Read,Glob,Grep" \
  --output-format stream-json \
  --include-partial-messages \
  --verbose
```

**The flags that matter:**

| Flag | Why |
|------|-----|
| `--allowedTools mcp__jcodemunch__*` | Pre-approves every jCodeMunch tool. Without this, headless Claude blocks on every tool-call permission prompt. |
| `--output-format stream-json` | Machine-parseable. The `system/init` event reports which MCP servers loaded — use it to fail fast if jCodeMunch isn't connected. |
| `--mcp-config <path-or-json>` | (Optional) pin the MCP set so other registered servers don't leak in and slow startup. |
| `--add-dir <path>` | Whitelist the repo if your CWD is elsewhere. |
| `--permission-mode acceptEdits` | More permissive than default for trusted CI; use `bypassPermissions` only in sandboxed runs. |

**Don't use `--bare`.** It exists precisely to skip MCP/hooks/skills auto-discovery; with `--bare`, jCodeMunch won't load.

### 4. Parse the JSON

The `result` event is terminal and contains:

```json
{
  "type": "result",
  "result": "...the model's answer...",
  "usage": { "input_tokens": 1840, "output_tokens": 612 },
  "total_cost_usd": 0.0231,
  "duration_ms": 4210,
  "is_error": false
}
```

The `system/init` event (first line of stream) lists `mcp_servers` — assert
`jcodemunch` is present before paying for tokens.

---

## Patterns this enables

- **Local code Q&A.** Solo dev asks questions about a repo from the terminal;
  jcodemunch retrieves the slices, claude assembles the answer.
- **Personal-repo review on push.** A solo dev wires a diff-aware review verb
  into their own repo's CI using `CLAUDE_CODE_OAUTH_TOKEN`. Anthropic
  explicitly permits this individual-use pattern.
- **Batch refactors / test generation / sweeps.** Fan out one subprocess per
  target with narrow context; aggregator merges results.
- **Doc-drift watchers.** Code change → jdocmunch finds doc sections referencing
  changed symbols → subprocess proposes doc edits.
- **"Chat with your repo" services.** Wrapper that shells out to `claude -p`
  with `mcp__jcodemunch__*` allowlisted. Better answers than embedding-only
  RAG because the model can actively call retrieval tools.
- **Editor/IDE side commands.** "Explain this symbol", "who calls this",
  "is this dead?" — shell out instead of eating the user's interactive context.

> **A note on team/business CI:** Anthropic's [Claude Code legal and
> compliance docs](https://code.claude.com/docs/en/legal-and-compliance)
> distinguish *individual ordinary use* (permitted on Pro/Max subscriptions)
> from *business / always-on / multi-contributor* deployments (which require
> API keys). Solo dev on their own repo with `CLAUDE_CODE_OAUTH_TOKEN` =
> permitted. Shared team CI / commercial automation = use API keys. The
> [jragmunch CLI](https://github.com/jgravelle/jragmunch-cli) defaults to
> subscription mode and exposes `--use-api` for the team/business case.

All of the above are first-class verbs in `jragmunch`. The CLI is a working
reference implementation if you want to build your own variant.

---

## Things that bite once

- **macOS + nvm:** if jCodeMunch shells out to `node`/`npx` from a non-interactive
  subprocess shell, your nvm-installed Node may not be on PATH. Use absolute
  paths in the MCP server's `command` field, or initialize nvm in non-interactive
  shells.
- **Index staleness:** if the index was built on a different machine or before
  recent edits, results degrade. Run `check_embedding_drift` (MCP tool) or
  `jcodemunch-mcp watch-all` to keep it fresh.
- **Rate limits in fan-out:** large parallel fan-outs hit limits fast on
  subscription-backed `claude`. For heavy workloads, point `claude` at an API
  key.

---

## See also

- [jragmunch-cli](https://github.com/jgravelle/jragmunch-cli) — the opinionated CLI
- [QUICKSTART.md](QUICKSTART.md) — interactive Claude Code setup
- [USER_GUIDE.md](USER_GUIDE.md) — full tool reference
- [GROQ.md](GROQ.md) — Groq remote MCP, the other headless story
