# jCodeMunch CLI - AN UNSUPPORTED ADD-ON

## Why MCP is the right interface

jCodeMunch was designed from the ground up as an MCP server, and that choice was not accidental.

The Model Context Protocol is not merely a transport layer — it is the native language of modern AI agents. When Claude (or any MCP-compatible agent) calls `search_symbols`, the result arrives structured, typed, and immediately actionable. There is no parsing step, no intermediate representation, no translation tax. The agent reads the `_meta` envelope, sees the tokens saved, and carries on. The entire round-trip — from query to precise symbol retrieval — takes milliseconds and costs a fraction of what brute-force file reading would.

MCP also gives you something a CLI fundamentally cannot: **context continuity**. An agent running inside Claude Code or Claude Desktop accumulates its tool call history. It knows what it searched for, what it retrieved, and what it has not yet looked at. It can chain calls intelligently — `list_repos` to confirm the index exists, `search_symbols` to find a candidate, `get_symbol` to read the exact implementation, `find_references` to trace usage — all within a single coherent reasoning thread. A CLI, by contrast, is stateless by definition. Each invocation starts cold.

There is also the question of cost accounting. Every MCP tool response includes a `_meta` envelope that carries `tokens_saved`, `total_tokens_saved`, and `cost_avoided` — a running ledger of exactly how much waste jCodeMunch has eliminated across your session. The savings accumulate, persist to `~/.code-index/_savings.json`, and are reported in real time. That ledger only exists because the agent and the tool are in continuous conversation. A CLI call has no session to accumulate against.

Finally, MCP is where the ecosystem is going. Every major AI client — Claude Desktop, Claude Code, VS Code Copilot, Google Antigravity, and others — supports MCP natively. Investing in MCP fluency pays forward; investing in CLI wrappers pays sideways.

**If you are using jCodeMunch with an AI agent, use the MCP interface.** That is what it was built for.

---

## On "CLI-first" agent frameworks

Projects like [CLI-Anything](https://github.com/HKUDS/CLI-Anything) make a compelling case that CLIs with structured JSON output are the right interface for AI agents to control software that has no API. We agree with the thesis — and it actually clarifies why jCodemunch takes the opposite approach.

CLI-Anything exists to bridge software that *lacks* a native agent interface. When GIMP or Blender ships no MCP server, an LLM-generated CLI with JSON output is the best available option. It is a thoughtful solution to a real gap.

jCodemunch has no gap to bridge. It was written as an MCP server from the first commit. The protocol that CLI-Anything is approximating with JSON output is the same protocol jCodemunch speaks natively:

| | CLI-Anything-style | jCodemunch MCP |
|---|---|---|
| Transport | Shell subprocess + stdout | Native MCP protocol |
| Output | JSON strings, parsed by agent | Structured tool results, typed |
| Session state | Stateless per invocation | Continuous, accumulated in agent context |
| Cost accounting | None | `_meta` envelope: `tokens_saved`, `cost_avoided` per call |
| Ecosystem fit | Bridge for apps with no API | First-class citizen in every MCP client |

The CLI in this directory exists for the same reason CLI-Anything exists: sometimes the native interface is not available (no AI agent in the loop, CI script, terminal session). When that is your situation, use it. When an AI agent is present, it would be a step backwards from the interface jCodemunch was built to provide.

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
