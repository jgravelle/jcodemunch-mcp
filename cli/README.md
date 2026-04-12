# jCodeMunch CLI - AN UNSUPPORTED ADD-ON

## Why MCP is the right interface

jCodeMunch was designed from the ground up as an MCP server, and that choice was not accidental.

**Structured I/O without parsing tax.** When an agent calls `search_symbols` over MCP, results arrive as typed JSON with schemas the client already understands. No stdout to parse, no column boundaries to guess, no escaping to handle. The agent reads the `_meta` envelope, sees the tokens saved, and chains directly into the next call. A CLI tool can return JSON too — but the agent still has to parse untyped stdout, and the client has no schema to validate against. That parsing step burns tokens and introduces ambiguity that MCP eliminates by design.

**Tool discovery at connection time.** MCP clients enumerate every available tool and its parameter schema when the server starts. The agent knows what it can call, what parameters are required, and what types they expect — before it makes a single request. With CLI, the agent either needs hardcoded knowledge of every subcommand and flag, or it runs `--help` and parses the output. More tokens spent on overhead instead of the actual task.

**Zero-config integration.** `pip install jcodemunch-mcp`, add one JSON block to your client config, done. Every MCP-compatible client — Claude Code, Cursor, Windsurf, Zed, Continue, Antigravity — picks it up with full type signatures and structured return values. CLI integration requires per-client shell wiring, PATH management, and often Docker or platform-specific install steps.

**Built-in cost accounting.** Every MCP tool response includes a `_meta` envelope carrying `tokens_saved`, `total_tokens_saved`, and `cost_avoided` — a running ledger that persists to `~/.code-index/_savings.json`. You could build equivalent tracking into a CLI wrapper, but MCP gives it to you for free because the protocol already defines the envelope.

**Ecosystem direction.** Every major AI client — Claude Desktop, Claude Code, VS Code Copilot, Antigravity, and others — supports MCP natively. Investing in MCP fluency pays forward; investing in CLI wrappers pays sideways.

**If you are using jCodeMunch with an AI agent, use the MCP interface.** That is what it was built for.

---

## On "CLI-first" agent frameworks

Projects like [CLI-Anything](https://github.com/HKUDS/CLI-Anything) make a compelling case that CLIs with structured JSON output are the right interface for AI agents to control software that has no API. We agree with the thesis — and it clarifies why jCodemunch takes the opposite approach.

CLI-Anything exists to bridge software that *lacks* a native agent interface. When GIMP or Blender ships no MCP server, an LLM-generated CLI with JSON output is the best available option. A thoughtful solution to a real gap.

jCodemunch has no gap to bridge. It was written as an MCP server from the first commit. The protocol that CLI-Anything approximates with JSON output is what jCodemunch speaks natively:

| | CLI-Anything-style | jCodemunch MCP |
|---|---|---|
| Transport | Shell subprocess + stdout | Native MCP protocol |
| Output | JSON strings, parsed by agent | Structured tool results, typed with schemas |
| Tool discovery | `--help` parsing or hardcoded knowledge | Automatic schema enumeration at connect |
| Cost accounting | Roll your own | `_meta` envelope: `tokens_saved`, `cost_avoided` per call |
| Ecosystem fit | Bridge for apps with no API | First-class citizen in every MCP client |

The CLI in this directory exists for the same reason CLI-Anything exists: sometimes the native interface isn't available (no AI agent in the loop, CI script, terminal session). When that's your situation, use it. When an agent is present, it would be a step backwards from the interface jCodemunch was built to provide.

---

## For those who insist

If you need to drive jCodeMunch from a shell script, a CI pipeline, or a terminal session without an AI agent in the loop, `cli.py` is here for you.

It calls the same underlying Python functions the MCP server calls — `search_symbols`, `get_symbol`, `index_folder`, and the rest — reading from and writing to the same `~/.code-index/` store. There is no separate process to start, no socket to connect to, no daemon to manage. Install the package, run the script, get output.

It is intentionally minimal. The MCP server is the product. This is a screwdriver taped to the side.

### Usage

```
python cli.py list
python cli.py index /path/to/project
python cli.py index owner/repo
python cli.py outline <repo>
python cli.py outline <repo> src/main.py
python cli.py search <repo> <query>
python cli.py get <repo> <symbol_id>
python cli.py text <repo> <query>
python cli.py file <repo> <file_path>
python cli.py invalidate <repo>
```

Output is JSON. Pipe to `jq` for readability.
