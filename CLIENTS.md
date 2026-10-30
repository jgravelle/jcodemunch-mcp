# Client Setup — every agent and IDE that speaks MCP

jCodeMunch is an MCP server, so it plugs into any MCP-capable client. `jcodemunch-mcp init` auto-configures the common ones (Claude Code, Claude Desktop, Cursor, Windsurf, Continue, Codex CLI, opencode, Gemini CLI, Cline, VS Code/Copilot). This page collects the tested configurations for everything else, plus the client-specific gotchas.

For the general install flow, start at [QUICKSTART.md](QUICKSTART.md).

**Claude Code · Claude Desktop · Cursor · Windsurf · Codex CLI · opencode · Continue · Cline · Roo Code · Zed · Goose · Hermes Agent · Paperclip · Gemini CLI · Qwen Code · Kiro** — and more.

Tested configurations:

| Platform | Config |
|----------|--------|
| **Autohand Code** | `autohand mcp add jcodemunch uvx jcodemunch-mcp` ([CLI details](https://github.com/autohandai/code-cli/)); add `--scope project` before `jcodemunch` for project configuration |
| **Claude Code / Claude Desktop** | `jcodemunch-mcp init` (auto-detects and patches config) |
| **Cursor / Windsurf / Continue** | `jcodemunch-mcp init` or manual `mcp.json` |
| **Antigravity (Google)** | Add a `jcodemunch` entry to `~/.gemini/config/mcp_config.json` (shared by Antigravity 2.0 / IDE / CLI). See below. |
| **Gemini CLI (Google)** | `jcodemunch-mcp init`, or add a `jcodemunch` entry under `mcpServers` in `~/.gemini/settings.json` (or project `.gemini/settings.json`) — a *different file* from Antigravity's `~/.gemini/config/mcp_config.json` above. *(config per [vendor docs](https://geminicli.com/docs/tools/mcp-server/))* |
| **Qwen Code** | Add under `mcpServers` in `~/.qwen/settings.json` (or project `.qwen/settings.json`). *(config per [vendor docs](https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/))* |
| **Kiro (AWS)** | Add under `mcpServers` in `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user). *(config per [vendor docs](https://kiro.dev/docs/mcp/configuration/))* |
| **JetBrains AI Assistant** | Settings, then Tools, then AI Assistant, then Model Context Protocol (MCP), then Add. In the JSON configuration field, set `command` to `uvx` and `args` to `["jcodemunch-mcp"]`. If Claude Desktop already has jCodeMunch, **Import from Claude** adds every server configured there. Each server can be global or for the current project. *(config per [vendor docs](https://www.jetbrains.com/help/ai-assistant/mcp.html), checked 2026-10-28; not tested here)* |
| **Junie (JetBrains)** | Add under `mcpServers` in `.junie/mcp/mcp.json` at the project root, or `~/.junie/mcp/mcp.json` for all projects. Junie CLI and Junie in JetBrains IDEs use the same MCP JSON format. *(config per [vendor docs](https://junie.jetbrains.com/docs/junie-cli-mcp-configuration.html), checked 2026-10-28; not tested here)* |
| **OpenAI Codex CLI** | `jcodemunch-mcp init`, or add an `[mcp_servers.jcodemunch]` block to `~/.codex/config.toml` by hand (see below — **do not use `uvx` here**) |
| **opencode** | `jcodemunch-mcp init`, or add a `jcodemunch` entry under the top-level `mcp` key in `~/.config/opencode/opencode.json`. ⚠ Not the `mcpServers` shape — see below. *(config per [vendor docs](https://opencode.ai/docs/mcp-servers/))* |
| **Cline / Roo Code** | `jcodemunch-mcp init` writes the Cline **CLI** config (`~/.cline/mcp.json`). For the IDE extension use the MCP marketplace UI, or paste `command: uvx`, `args: ["jcodemunch-mcp"]` — Cline does not document the extension's settings path per-platform, so `init` does not guess at it. |
| **Zed** | Add to `settings.json` under `context_servers` |
| **Goose (Block)** | `goose configure` → Add Extension → command `uvx jcodemunch-mcp` |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | Add to `~/.hermes/config.yaml` — see [skill](https://github.com/NousResearch/hermes-agent/pull/10413) |
| **Paperclip** | `.mcp.json` at workspace root (auto-detected) |
| **Any other MCP client** | stdio: `jcodemunch-mcp`, HTTP: `jcodemunch-mcp serve --transport streamable-http` (SSE still available but deprecated by the MCP 2026-07-28 spec) |
| **VS Code (any MCP client)** | Install the [jCodeMunch VS Code extension](https://marketplace.visualstudio.com/items?itemName=jgravelle.jcodemunch-mcp-vscode) for on-save auto-reindex under Copilot Chat / Continue / Cline — closes the staleness gap when the host doesn't fire PostToolUse hooks |
| **GitHub Copilot (VS Code)** | `jcodemunch-mcp init` writes `.vscode/mcp.json` when the workspace has a `.vscode/` directory. ⚠ Top-level key is `servers`, not `mcpServers` — see below. *(config per [vendor docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers))* |
| **GitHub Copilot CLI / cloud agent** | `jcodemunch-mcp init --copilot-hooks` writes `.github/hooks/hooks.json` with a postToolUse rule for auto-reindex |
| **[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus)** (self-hosted AI workspace) | SSE transport: run `jcodemunch-mcp serve --transport sse` on the host (token **unset**), register the URL in the MCP Registry (see below) — *community-tested* |

<details>
<summary>Codex CLI config</summary>

`jcodemunch-mcp init` detects Codex and writes this for you — including
resolving the binary path. ⚠ If no `jcodemunch-mcp` executable is on your
`PATH`, `init` **declines to configure Codex** rather than falling back to
`uvx`, and tells you to run `uv tool install jcodemunch-mcp` first. That
refusal is deliberate; the paragraph below is why.

**Recommended (pre-installed binary, no `uvx`).** Codex's rmcp transport
is strict about the first JSON-RPC frame on stdout. `uvx`'s install
chatter on first run can poison the handshake, which historically
manifests as a silent multi-hour hang. Install it once with uv, which
also means there's no download when Codex first starts the server:

```bash
uv tool install jcodemunch-mcp
jcodemunch-mcp init
```

On Windows, when `jcodemunch-mcp` on your `PATH` is the install `init` is
configuring, `init` writes the bare name, so the entry keeps working after a
reinstall moves the binary. When `PATH` finds a different install, or none,
it writes the absolute path instead. On macOS and Linux it always writes the
absolute path: Codex started from a GUI or a non-login shell can have a
narrower `PATH` than the shell you ran `init` in, and bare-name launch was
tested on Windows only.

```toml
# ~/.codex/config.toml
[mcp_servers.jcodemunch]
command = "jcodemunch-mcp"
```

Or install into a project venv and point Codex at the binary directly:

```bash
python3 -m venv .venv
.venv/bin/pip install -U jcodemunch-mcp
.venv/bin/jcodemunch-mcp --help   # confirm the binary resolves
```

```toml
# ~/.codex/config.toml
[mcp_servers.jcodemunch]
command = "/absolute/path/to/.venv/bin/jcodemunch-mcp"
# (no args required)
```

If the handshake still doesn't complete, set
`JCODEMUNCH_HANDSHAKE_TIMEOUT=5` (the default) and watch stderr — v1.82.1+
emits a one-line hint when the client doesn't call any handler within
the window.

**Note for `codex review --background` and other non-interactive runs.**
Codex's MCP elicitation/approval system can silently *decline* tool
calls to unrecognized servers in non-interactive mode (visible in
`~/.codex/logs_2.sqlite` as `ResolveElicitation { decision: Decline }`
with no chatter on the server side). Interactive `codex` runs are
unaffected. Codex has a per-server approval key,
`default_tools_approval_mode`, and its `writes` value "prompts for tools
that aren't marked read-only". Every jCodeMunch tool that only reads
carries the MCP `readOnlyHint` annotation, so with `writes` the read
tools run without a prompt and indexing still asks (on the `full`
surface; on `counter`, `order` and `route` are writers and still ask, see
"Fewer approval prompts" below):

```toml
[mcp_servers.jcodemunch]
command = "jcodemunch-mcp"
default_tools_approval_mode = "writes"
```

A single tool can be overridden with
`[mcp_servers.jcodemunch.tools.<tool>]` and an `approval_mode` value.
Codex's docs don't say how these modes behave in a non-interactive run,
so check a background run's log after changing them. Source:
[Codex MCP docs](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
checked 2026-10-28.

**Legacy `uvx` config** (kept for reference; works on tolerant clients,
not recommended for Codex):

```toml
[mcp_servers.jcodemunch]
command = "uvx"
args = ["jcodemunch-mcp"]
# The first run filled 251 MB of uv cache before the server answered.
startup_timeout_sec = 30
```

Codex waits 10 seconds for a server to start unless `startup_timeout_sec`
says otherwise ([Codex MCP docs](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)).
Measured on 2026-10-28 (Windows, home broadband, `uvx` alone rather than
inside Codex): 7.0 s to the first reply with an empty uv cache, then 2.6 s
and 2.5 s once cached. A slower link can pass 10 s on the first run.
</details>

<details>
<summary>opencode config</summary>

`jcodemunch-mcp init` detects opencode and writes this for you. The manual
form, for a global install:

```json
// ~/.config/opencode/opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "jcodemunch": {
      "type": "local",
      "command": ["uvx", "jcodemunch-mcp"],
      "enabled": true
    }
  }
}
```

Project-level config goes in `opencode.json` at the project root; opencode
searches the current directory and walks up to the nearest Git directory.

⚠ **Three ways this differs from every other JSON client on this page, and
none of them error.** The top-level key is **`mcp`**, not `mcpServers`. Each
server needs an explicit **`"type": "local"`**. And **`command` is a single
array** carrying the executable *and* its arguments, rather than separate
`command` / `args` keys. Paste the shape used elsewhere in this file and
opencode parses the config happily, registers nothing, and reports no
problem — you just get an agent with no jCodeMunch tools.

⚠ The global path is `~/.config/opencode/` on **every** platform, Windows
included. opencode documents it as a fixed location and does not document
`XDG_CONFIG_HOME` support, so honouring that variable would write a file
opencode never reads.

</details>

<details>
<summary>GitHub Copilot (VS Code) config</summary>

`jcodemunch-mcp init` writes this when the workspace already has a
`.vscode/` directory. The manual form:

```json
// .vscode/mcp.json
{
  "servers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    }
  }
}
```

⚠ **The top-level key is `servers`, not `mcpServers`** — the third distinct
schema on this page, and like opencode's it fails quietly. VS Code reads the
file, finds nothing under the key it expects, and reports no error. `type`
is optional and defaults to `"stdio"` for a local server, so it is left off.

⚠ `init` requires an existing `.vscode/` directory rather than looking for
`code` on your `PATH`. The executable is present on most developer machines,
so detecting on it would create `.vscode/mcp.json` in whatever directory you
happened to run `init` from — a file you might then commit without meaning to.

VS Code's **user-level** MCP config is reached through the
**MCP: Open User Configuration** command rather than a documented path, and
it moves with the active profile, so `init` does not write it. Use that
command and paste the `servers` block above.

</details>

<details>
<summary>Antigravity (Google) config</summary>

Antigravity (2.0, IDE, and CLI) loads MCP servers from a single shared file at
`~/.gemini/config/mcp_config.json` (HOME-level only — project-local
`.antigravitycli/mcp_config.json` is read but not loaded). Add:

```json
// ~/.gemini/config/mcp_config.json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    }
  }
}
```

Restart Antigravity so it re-reads the config; tools appear under
`mcp(jcodemunch/*)`. To grant the jcodemunch agent skill to all Antigravity
tools, drop the bundle from `jcodemunch-mcp install claude-code --skills`
(at `~/.claude/skills/jcodemunch/SKILL.md`) into the shared skills dir
`~/.gemini/skills/jcodemunch/` (or the CLI-only `~/.gemini/antigravity-cli/skills/`).
</details>

<details>
<summary>Hermes Agent config</summary>

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  jcodemunch:
    command: "uvx"
    args: ["jcodemunch-mcp"]
```
</details>

<details>
<summary>Odysseus config (self-hosted AI workspace)</summary>

[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) runs in Docker and
indexes nothing itself; jCodeMunch indexes your code on the **host**. Run
jCodeMunch as an **SSE** server on the host and register its URL in Odysseus.
Its SSE client connects by URL only (no auth header), so leave the token unset
and secure the endpoint by network binding instead.

> **SSE deprecation note:** the MCP 2026-07-28 spec deprecates the SSE
> transport. jCodeMunch keeps serving SSE for hosts like Odysseus that don't
> offer streamable-http yet; once Odysseus adds a streamable-http registry
> option, switch to `serve --transport streamable-http` and URL
> `http://host.docker.internal:8848/mcp`.

**1. Start jCodeMunch on the host (no token):**

```bash
jcodemunch-mcp index .
jcodemunch-mcp serve --transport sse --host 0.0.0.0 --port 8848
```

Leave `JCODEMUNCH_HTTP_TOKEN` **unset** — Odysseus's SSE client sends no
`Authorization` header, so a token returns 401 on connect.

**2. In Odysseus → Settings → MCP Registry → Add server:**

- **Transport:** SSE
- **URL:** `http://host.docker.internal:8848/sse`
  (Linux: add `extra_hosts: ["host.docker.internal:host-gateway"]` to the
  Odysseus service in `docker-compose.yml`)

**3. Secure by network, not token.** Because the SSE path is unauthenticated,
bind jCodeMunch so only the Odysseus container can reach it (host-gateway
interface / firewall), not a public interface. `JCODEMUNCH_RATE_LIMIT` adds a
throttle.

**4. Restart Odysseus.** All jCodeMunch tools appear in chat + agents. Keep the
index fresh with `jcodemunch-mcp watch .`; use Odysseus's per-server
`disabled_tools` to trim the surface.

jCodeMunch is read-only by charter, and its `get_*` / `search_*` / `find_*` /
`check_*` tool naming satisfies Odysseus's plan-mode read-only gate, so the
suite stays usable in plan mode.

> *Community-tested:* the MCP protocol round-trip (SSE connect + tool discovery)
> is verified; the container-to-host network dial depends on your Docker setup.

</details>

## Fewer approval prompts

Most jCodeMunch tools only read the index. Each of those carries the MCP
`readOnlyHint` annotation. The tools that change index, session or config state
(indexing, cache invalidation, weight tuning, handoff records) are marked
as writers. If your client asks before every tool call, you can allow the
read tools and keep the prompt for the rest.

These steps name the tools of the `full` surface. A first-ever install
starts on the `counter` surface, which serves three front-door tools
instead: `menu` only reads, while `order` and `route` are marked as writers
because they can dispatch any tool, indexing included. There the client
keeps asking before each `order` and `route` call. To allow the read tools
by name, set `"tool_surface": "full"` in your global `config.jsonc`
(`~/.code-index/` by default, or under `CODE_INDEX_PATH`), or set
`JCODEMUNCH_TOOL_SURFACE=full`, which wins over the file.

**Claude Code.** In `.claude/settings.json` (the project, committable) or
`~/.claude/settings.json` (you), list the tools to allow. Tool names are
`mcp__<server>__<tool>`, and the server name `init` writes is `jcodemunch`:

```json
{
  "permissions": {
    "allow": [
      "mcp__jcodemunch__resolve_repo",
      "mcp__jcodemunch__plan_turn",
      "mcp__jcodemunch__search_symbols",
      "mcp__jcodemunch__search_text",
      "mcp__jcodemunch__get_symbol_source",
      "mcp__jcodemunch__get_file_outline",
      "mcp__jcodemunch__get_ranked_context",
      "mcp__jcodemunch__find_references",
      "mcp__jcodemunch__check_references",
      "mcp__jcodemunch__get_repo_outline",
      "mcp__jcodemunch__get_file_tree"
    ]
  }
}
```

`"mcp__jcodemunch__*"` allows every tool, indexing included.
*(per [Claude Code permissions](https://code.claude.com/docs/en/permissions), checked 2026-10-28)*

**Codex.** Set `default_tools_approval_mode = "writes"` on the server
entry; see the Codex block above.

**VS Code (Copilot Chat).** When a jCodeMunch tool asks, you can approve
it for the session, the workspace, or all future calls. Tools are grouped
by server, so you can trust the whole server at once. Setting
`chat.tools.eligibleForAutoApproval` to `false` for a tool keeps it
manual. `chat.tools.global.autoApprove` removes prompts for every tool
from every server, so it isn't a way to allow just this one.
*(per [VS Code approvals](https://code.visualstudio.com/docs/agents/run/approvals), checked 2026-10-28)*

**Cursor.** Cursor asks before using MCP tools by default. In Auto-review
mode, allowlisted MCP tools run immediately, and an Enterprise admin can set a
tool allowlist for each approved server.
*(per [Cursor MCP docs](https://cursor.com/docs/context/mcp), checked 2026-10-28)*

## Share it with your team

Commit a `.mcp.json` at the repo root and everyone who clones it gets the
same servers. This one adds jCodeMunch beside Context7 (library docs):

```json
{
  "mcpServers": {
    "jcodemunch": {
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    },
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp"
    }
  }
}
```

Claude Code asks each person to approve project servers the first time.
GitHub Copilot CLI reads the same file, from the working directory or any
parent up to the repo root, and loads it after you confirm folder trust
on first launch. An entry with no `type` is a local server to both.
*(per [Claude Code MCP docs](https://code.claude.com/docs/en/mcp) and [Copilot CLI docs](https://docs.github.com/en/enterprise-cloud@latest/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers), checked 2026-10-28)*
VS Code reads a different file (`.vscode/mcp.json`, top-level key
`servers`); see the Copilot block above.

Each developer still needs [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
Keep API keys out of a committed file. Personal, academic and other
non-commercial use is free; use inside a for-profit company needs a
commercial license ([buying guide](https://jcodemunch.com/buying.php)).

## Run it as a shared HTTP server

jCodeMunch can serve over HTTP for a team or a container instead of
running per developer over stdio:

```bash
JCODEMUNCH_HTTP_TOKEN=change-me jcodemunch-mcp serve --transport streamable-http
```

It listens on `127.0.0.1:8901` by default (`--host`, `--port`). Clients
connect to `http://<host>:8901/mcp` and send
`Authorization: Bearer change-me`; without the token they get 401. The
token is optional and everyone shares it; there are no per-user accounts.
The server has no TLS of its own. To reach it from other machines, put it
behind your own HTTPS reverse proxy rather than opening `--host 0.0.0.0`
to the network.

Every client sees the same repos and the same index. Saves from different
clients wait for each other, but indexing itself isn't coordinated, so
don't index the same repo from two clients at once.

To cap requests, set `JCODEMUNCH_RATE_LIMIT` (requests per minute per
client IP, off by default; over the limit returns HTTP 429 with
`Retry-After`). Behind a reverse proxy every request comes from the
proxy's IP, so it becomes one shared limit. You run and secure the host;
there is no hosted jCodeMunch service.

## Paperclip (multi-agent orchestration)

If you’re using **Paperclip** (the multi-agent orchestration platform), add a `.mcp.json` to your workspace root:

```json
{
  "mcpServers": {
    "jcodemunch": {
      "type": "stdio",
      "command": "uvx",
      "args": ["jcodemunch-mcp"]
    },
    "jdocmunch": {
      "type": "stdio",
      "command": "uvx",
      "args": ["jdocmunch-mcp"]
    }
  }
}
```

Paperclip’s Claude Code agents auto-detect `.mcp.json` at startup. Add both servers to give your agents symbol search + doc navigation without blowing the token budget.
