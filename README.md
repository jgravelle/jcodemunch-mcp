# jCodeMunch MCP

**The most token-efficient MCP server for precise source code retrieval via tree-sitter AST parsing.** Cut AI token costs 86-99% on code exploration (96% average, benchmarked at 28.3x fewer tokens than a grep-and-read agent) and stop burning your context window reading entire files.

> **Real results, live from production**
> **838B+ tokens saved** · **136,000+ reporting installs** · **$4.2M+ in AI spend avoided** · **100,000+ kg CO₂ prevented**
> Counter figures as of 2026-08-17, valued at the $5/MTok Claude Opus **input** rate. All four only grow, so read them as floors. Live at **[jcodemunch.com](https://jcodemunch.com/)**.

Works with **Claude Code**, **Cursor**, **VS Code**, **Codex CLI**, **Windsurf**, **Continue**, and [any MCP-compatible client](CLIENTS.md).

[**Install now**](#install) · [**Quickstart**](QUICKSTART.md) · [**See the evidence**](#evidence) · [**Pricing**](https://jcodemunch.com/?utm_source=github&utm_medium=readme&utm_campaign=jcm_readme_top#pricing)

[![PyPI version](https://img.shields.io/pypi/v/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)
![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
[![Issues closed](https://img.shields.io/github/issues-closed/jgravelle/jcodemunch-mcp?label=issues%20closed&color=brightgreen)](https://github.com/jgravelle/jcodemunch-mcp/issues?q=is%3Aissue+is%3Aclosed)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20102349.svg)](https://doi.org/10.5281/zenodo.20102349)

<!-- mcp-name: io.github.jgravelle/jcodemunch-mcp -->

**Free for personal use.** Use it to make money, and Uncle J. gets a taste. Fair enough? [Commercial licenses below.](#licensing-and-commercial-use)
Our guarantee: if jCodeMunch doesn't pay for itself, you don't pay for jCodeMunch.

---

## Why jCodeMunch?

Most AI agents explore repositories the expensive way: open entire files, skim thousands of irrelevant lines, repeat. That is not "a little inefficient." That is a **token incinerator**.

**jCodeMunch indexes a codebase once and lets agents retrieve only the exact code they need**: functions, classes, methods, constants, outlines, and tightly scoped context bundles, with byte-level precision. It parses source with tree-sitter, stores structured symbol metadata (signature, kind, qualified name, summary, byte offsets) alongside raw file content in a local index, and fetches exact implementations on demand instead of re-reading files over and over.

| Task | Traditional approach | With jCodeMunch |
| --- | --- | --- |
| Find a function | Open and scan large files | Search symbol, fetch exact implementation |
| Understand a module | Read broad file regions | Pull only relevant symbols and imports |
| Explore repo structure | Traverse file after file | Query outlines, trees, and targeted bundles |
| "What breaks if I change X?" | Not possible | `get_blast_radius` |

Index once. Query cheaply. Keep moving. **Precision context beats brute-force context.**

---

## Evidence

### Reproducible token efficiency benchmark

Measured with `tiktoken cl100k_base` across three public repos pinned to upstream commits, run 2026-09-03 on v1.108.316. Workflow: `search_symbols` (top 5) + `get_symbol_source` × 3 per query. Two baselines, same run, same corpus, same file reader:

- **Grep-top-3**: `rg -l` the query terms, rank files by match count, open the top 3 whole. This is what a competent agent without the tool actually does, and it is the number to quote.
- **Read-all**: every indexed source file concatenated. A ceiling nobody pays; retained for continuity with previously published figures.

| Repository | Files | Symbols | Grep-top-3 baseline | jCodeMunch | vs grep | vs read-all |
|------------|------:|--------:|--------------------:|-----------:|--------:|------------:|
| expressjs/express | 186 | 455 | 15,724 avg | 1,017 avg | **15.5x** | 152.0x |
| fastapi/fastapi | 1,186 | 13,240 | 85,296 avg | 2,218 avg | **38.4x** | 372.0x |
| gin-gonic/gin | 98 | 1,451 | 31,975 avg | 1,573 avg | **20.3x** | 96.5x |
| **Grand total (15 task-runs)** | | | **664,975** | **23,467** | **28.3x** | 241.1x |

**Against a grep-and-read agent: 96.5% reduction, 28.3x fewer tokens.** Per-query results range from 7.6x to 81.2x (median 26.1x); no single multiple describes every query. Against read-all the figure is 99.6%, but nobody pays that ceiling. Compact [MUNCH](SPEC_MUNCH.md) wire encoding then trims a median 45.5% more bytes off responses.

Full methodology, pinned commits, harness, and known caveats: [benchmarks/METHODOLOGY.md](benchmarks/METHODOLOGY.md) · [Reproduce it yourself](benchmarks/REPRODUCING.md) · [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md)

### Independent A/B test on a production codebase

50-iteration A/B test on a real Vue 3 + Firebase production codebase, jCodeMunch vs native tools (Grep/Glob/Read), Claude Sonnet 4.6, fresh session per iteration: success rate 80% vs 72%, timeout rate 32% vs 40%, mean cache creation down 10.5%. Tool-layer savings isolated from fixed overhead: 15-25%. One finding category appeared exclusively in the jCodeMunch variant: orphaned file detection via `find_importers`, a structural query native tools cannot answer without scripting. Full report: [benchmarks/ab-test-naming-audit-2026-03-18.md](benchmarks/ab-test-naming-audit-2026-03-18.md)

### Mentioned by

- **Artur Skowroński** (VirtusLab): *"roughly 80% fewer tokens, or 5× more efficient — index once, query cheaply forever"* · [GitHub All-Stars #15](https://virtuslab.com/blog/ai/code-munch-mcp-your-agent-starts-navigating)
- **Traci Lim** (AWS · ASEAN AI Lead): *"structural queries that native tools can't answer: find_importers, get_blast_radius, get_class_hierarchy, find_dead_code"* · [5 Repos That Save Token Usage in Claude Code](https://www.tracilzw.com/posts/5-repos-save-token-usage-claude-code)
- **Julian Horsey** (Geeky Gadgets): *"3,850 tokens reduced to just 700 — a 5.5× improvement"* · [JCodeMunch AI Token Saver](https://www.geeky-gadgets.com/jcodemunch-mcp-token-savings/)
- **Eric Grill**: *"context is the scarce resource. Cut it by 90% and the whole stack gets cheaper and more reliable"* · [jCodemunch: Context Engine for AI Agents](https://www.ericgrill.com/blog/jcodemunch-mcp-context-engine-for-ai-agents)

[Full recognition page →](https://jcodemunch.com/recognition.php)

---

## Install

#### One-click installs

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_jCodeMunch-007ACC?style=for-the-badge&logo=visualstudiocode&logoColor=white)](vscode:mcp/install?%7B%22name%22%3A%20%22jcodemunch%22%2C%20%22command%22%3A%20%22uvx%22%2C%20%22args%22%3A%20%5B%22jcodemunch-mcp%22%5D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install-24bfa5?style=for-the-badge&logo=visualstudiocode&logoColor=white)](vscode-insiders:mcp/install?%7B%22name%22%3A%20%22jcodemunch%22%2C%20%22command%22%3A%20%22uvx%22%2C%20%22args%22%3A%20%5B%22jcodemunch-mcp%22%5D%7D)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_jCodeMunch-122122?style=for-the-badge&logo=cursor&logoColor=white)](cursor://anysphere.cursor-deeplink/mcp/install?name=jcodemunch&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyJqY29kZW11bmNoLW1jcCJdfQ==)

#### Recommended: one command

```bash
uv tool install jcodemunch-mcp
jcodemunch-mcp init
```

No virtualenv to manage, nothing written into system Python, and it works as-is on PEP 668 distros (Ubuntu 24.04+, Debian 12+) where bare `pip install` is refused. [Don't have `uv` yet?](https://docs.astral.sh/uv/getting-started/installation/)

`init` auto-detects your MCP clients (Claude Code, Claude Desktop, Cursor, Windsurf, Continue), writes their config entries, installs the CLAUDE.md prompt policy so your agent actually uses jCodeMunch, optionally installs enforcement hooks, optionally indexes your project, and audits your agent config files for token waste.

<details>
<summary><b>Other install paths</b></summary>

| Command | Use it when |
|---|---|
| `uvx jcodemunch-mcp` | **Zero install.** Runs from an ephemeral environment — nothing lands on disk permanently. The client entries `init` writes already invoke the server this way, so for most setups this is all that ever runs. ⚠ Enforcement hooks are the exception: they're spawned by a minimal-PATH subshell and resolve the executable by name, so they need `uv tool install` (or `pipx`/`pip`) to work. |
| `pipx install jcodemunch-mcp` | You already standardise on pipx |
| `pip install jcodemunch-mcp` | Inside a virtualenv you manage yourself |

</details>

Verify:

```bash
jcodemunch-mcp --version
```

#### Manual Claude Code setup

```bash
claude mcp add -s user jcodemunch -- uvx jcodemunch-mcp
```

No install step — `uvx` fetches and runs the server on demand. Prefer it on your PATH (and required for enforcement hooks)? `uv tool install jcodemunch-mcp`, then `claude mcp add -s user jcodemunch jcodemunch-mcp`.

Then tell the agent to prefer the tools. This matters more than people think; installation makes the tools available but does not break the agent's brute-reading habit. One line in your CLAUDE.md does it:

```markdown
Call the jcodemunch_guide tool and strictly follow its instructions.
```

Using Cursor, Windsurf, Codex CLI, Antigravity, Gemini CLI, Qwen Code, Kiro, Cline, Zed, Goose, Hermes, Odysseus, or Paperclip? Every tested client configuration lives in **[CLIENTS.md](CLIENTS.md)**. Optional extras (local semantic search, AI summaries per provider) are in [QUICKSTART.md](QUICKSTART.md); the system surfaces each extra pulls in are documented in [SECURITY.md](SECURITY.md#optional-extras--system-surfaces-each-pulls-in).

---

## Quickstart

Full walkthrough: **[QUICKSTART.md](QUICKSTART.md)**. The two-minute version, inside your agent after `init`:

1. Ask: *"Index this repo with jcodemunch."*
2. Ask: *"Using jcodemunch, find the function that handles authentication and show me its source."*

The agent should answer via `search_symbols` and `get_symbol_source`, returning tens of lines instead of whole files. Confirm with `get_session_stats`: it reports tokens served and savings for the session. That is where the numbers on the meter come from.

Want to skip initial indexing for popular frameworks? Pre-built **starter packs**: `jcodemunch-mcp install-pack --list` (free packs need no license).

---

## What you can do

- **Retrieve one symbol instead of loading a file.** `get_symbol_source` returns the exact function body, byte-precise, for the majority of edits that touch one function in a 700-line file (~95% savings on that read).
- **Assemble a whole task's context in one call.** `assemble_task_context` classifies the task intent, extracts anchor symbols, and runs the right tool sequence under one token budget. `plan_turn` routes the turn before the first read.
- **Ask structural questions grep can't answer.** `find_importers`, `get_blast_radius`, `get_call_hierarchy`, `find_dead_code`, `get_changed_symbols`, `get_hotspots`, `search_ast` anti-pattern sweeps, and more. Two of them sound alike and are not: `check_references` answers where a name is used (import sites plus every file whose content mentions it), `find_references` answers who imports it, over the import graph alone, so a call site is invisible to it.
- **Preflight risky changes, and know when to stop.** `check_edit_safe`, `check_delete_safe`, `get_pr_risk_profile`, and `plan_refactoring` with edit-ready `{old_text, new_text}` blocks. The two safety checks return `stop_rule.terminal`: true means no further jcodemunch call moves the verdict, so re-running `find_importers` or `check_references` to be sure is wasted work. It means final, not safe. False names the specific thing that would change the answer.
- **Trust the answers.** Calibrated confidence scores, freshness flags, coverage contracts on absence claims, compiler-verified references via SCIP import, and automatic secret redaction before anything reaches the LLM.
- **Keep the index fresh automatically.** Watch modes, agent hooks, and a VS Code extension close the staleness gap.

That's the highlight reel. The complete tour of 90+ tools, the MUNCH compact wire format, evidence receipts, offloadable-work annotation, and the session-economics instrumentation is in **[CAPABILITIES.md](CAPABILITIES.md)**, with internals in [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md).

<!-- WHATSNEW:START -->
#### What's new

- **[v1.108.316](https://github.com/jgravelle/jcodemunch-mcp/releases/tag/v1.108.316)** (2026-09-02) — A display preference edited the data it was displaying
- **[v1.108.315](https://github.com/jgravelle/jcodemunch-mcp/releases/tag/v1.108.315)** (2026-09-01) — A fix for a false positive can install a false negative
- **[v1.108.314](https://github.com/jgravelle/jcodemunch-mcp/releases/tag/v1.108.314)** (2026-09-01) — A rate written for a future date is wrong for every day before it
<!-- WHATSNEW:END -->

---

## When does it help (and when doesn't it)?

| Scenario | Native tool | jCodeMunch | Savings |
|----------|-------------|------------|---------|
| Edit one function (700-line file) | `Read` → 700 lines | `get_symbol_source` → 30 lines | ~95% |
| Understand a file's structure | `Read` → full content | `get_file_outline` → names + signatures | ~80% |
| Find which file to edit | `Grep` many files | `search_symbols` → exact match | comparable |
| Edit requires whole-file context | `Read` → full content | `get_file_content` → full content | ~0% |
| "What breaks if I change X?" | not possible | `get_blast_radius` | unique capability |

It helps most on targeted edits (one function, one method, one class), which is the majority of real editing work. Edits that genuinely require the entire file (restructuring file-level state, reordering logic spanning hundreds of lines) see no advantage. Best fits: large repositories, unfamiliar codebases, agent-driven exploration, refactoring and impact analysis, and teams cutting AI token costs without making agents dumber.

**Languages:** 70+ via tree-sitter, including Python, JavaScript/TypeScript, Go, Rust, Java, C/C++, C#, PHP, Ruby, Swift, and Kotlin. Full matrix: [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md). **Monorepos:** yes; incremental indexing, workspace-member detection, subpath scoping.

---

<a id="background-behavior-fully-disclosed"></a>

## Deferring the tool schemas (Anthropic tool search)

If you reach jCodeMunch through the [MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector) on a model that supports [tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool), you can keep our schemas out of your context prefix entirely and let Claude load only the two or three tools a request needs. **You do not set `defer_loading` per tool** — set it once for the whole server:

```json
{
  "mcp_servers": [
    { "type": "url", "url": "https://your-host/mcp", "name": "jcodemunch" }
  ],
  "tools": [
    { "type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25" },
    {
      "type": "mcp_toolset",
      "mcp_server_name": "jcodemunch",
      "default_config": { "defer_loading": true },
      "configs": {
        "resolve_repo":       { "defer_loading": false },
        "search_symbols":     { "defer_loading": false },
        "get_ranked_context": { "defer_loading": false }
      }
    }
  ]
}
```

Send it with the beta header `mcp-client-2025-11-20`. **Both halves are required** — `mcp_servers` alone is a validation error, and so is `mcp_toolset` without the matching `mcp_server_name`.

⚠ **The MCP connector takes a URL, so this applies to jCodeMunch served over `sse` or `streamable-http`** (`jcodemunch-mcp serve --transport streamable-http`), not to the default local stdio setup. On stdio, whether schemas are deferred is up to your client, and `tool_surface: "counter"` below is the lever you control.

The `configs` block above follows Anthropic's own advice — keep your 3–5 most-used tools resident so common requests skip the search round trip — and per-tool `configs` overrides `default_config`.

Deferred definitions are excluded from the system-prompt prefix and appended inline as `tool_reference` blocks when Claude discovers them, **so prompt caching is preserved** — this is not the cache-invalidating kind of dynamic tool list. At least one tool in the request must stay non-deferred, or the API returns a 400.

⚠ **This is a different mechanism from our own `tool_surface: "counter"`**, and you do not need both. Tool search is host-side and works across every MCP server you have connected; the Counter is server-side, works on any host including ones with no tool-search support, and is what `init` configures on a first-ever install. Pick whichever your host supports — see [CONFIGURATION.md](CONFIGURATION.md#the-counter--collapse-to-a-3-tool-front-door-tool_surface) for the Counter and `jcodemunch-mcp surface` for what your install actually advertises.

---

## Security, privacy, and background behavior

Local-first by design: indexes live at `~/.code-index/`, and the base package's only default network behavior is an anonymous savings counter (random ID plus aggregate token counts, no code, no paths, no PII; opt out with `share_savings: false`). Everything the server does beyond answering a tool call (file watching, the opt-in login service, license validation, model downloads, org reporting) is opt-in or opt-out, visible, and reversible, and every item is enumerated in **[SECURITY.md](SECURITY.md#background-behavior-fully-disclosed)** alongside the path-traversal, symlink, and secret-redaction controls.

---

## Per-project configuration

Most settings live in the global `~/.code-index/config.jsonc`, but any of them can be overridden for a single repository by dropping a `.jcodemunch.jsonc` at its root. It is an **overlay**: keys it declares win, keys it omits fall through to global and then to the built-in default, so it only needs to contain what differs.

```jsonc
// <your-repo>/.jcodemunch.jsonc
{
  "max_file_size": 1048576,
  "languages": ["python", "typescript", "racket"]
}
```

### Declaring Racket defining forms

Racket projects routinely define their own defining forms with `define-syntax`, and a static parser cannot know what those bind — `(defstep (check-admin) ...)` is indistinguishable from a function call. Declaring them makes their bindings searchable:

```jsonc
{
  "racket_definition_forms": {
    "defstep":  "function",
    "defstudy": "constant",
    "defvar":   "constant",
    "define-schema": "class"
  }
}
```

Each entry maps a form name to what it binds: `function`, `constant`, `class` or `type`. Where the name sits is read from the source rather than declared — `(defstep (check-admin) ...)` takes the head of the parameter list, `(defstudy consent ...)` takes the bare symbol — so a form that appears in both shapes works either way.

⚠ This is an assertion, not something jCodeMunch can verify. A wrong declaration puts a name in the index that Racket does not actually bind. Declarations are also matched only after every built-in form, so declaring `define` or `struct` has no effect — the built-in handling wins.

### Declaring what a Racket `#lang` looks like

A `#lang` line names a *reader*, and jCodeMunch's Racket parser reads S-expressions. The distribution's langs are built in (`racket/*`, `typed/racket*`, `s-exp`, `info`, `at-exp …`, and the document langs `scribble/*`, `pollen`, `punct`, `markdown` …), but a project's own lang is unknown to it and is treated as a document — no symbols, still text-searchable — until you say what its syntax is:

```jsonc
{
  "racket_langs": {
    "conscript": "at-exp",
    "mylang": "sexp"
  }
}
```

`sexp` is plain S-expressions; `at-exp` is at-exp text bodies over Racket (read with `@` as the command character, exactly as `#lang at-exp` reads them, so prose containing `;` `"` `#` or `|` is prose); `text` is a document language that is never walked. A key also covers its sub-langs (`conscript` matches `conscript/with-require`), and a project may demote a lang as well as promote one. An at-exp lang whose reader uses another command character declares it with the object form — `"mylang": {"tier": "at-exp", "command_char": "◊"}` — the way Racket's `make-at-readtable` takes `#:command-char`.

Both keys change what the parser emits for *unchanged* files, so a change to either is stamped on the index and forces one full re-parse on the next index (`rebuild_reason: "racket_config_changed"`); you do not need to touch the files or clear the index. An index holding Racket files that was built before this stamp existed re-parses once the same way (`rebuild_reason: "racket_index_predates_gate"`).

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [QUICKSTART.md](QUICKSTART.md) | Zero-to-indexed in three steps |
| [CLIENTS.md](CLIENTS.md) | Tested configuration for every MCP client |
| [USER_GUIDE.md](USER_GUIDE.md) | Full tool reference, workflows, and best practices |
| [CAPABILITIES.md](CAPABILITIES.md) | The complete capability reference beyond the highlight reel |
| [CONFIGURATION.md](CONFIGURATION.md) | Config file reference, token-control levers, tool tiering, the Counter |
| [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md) | The technical manual: verdicts, ranking internals, provenance contracts |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Internal design, storage model, and extension points |
| [GROQ.md](GROQ.md) | Groq Remote MCP, the gcm CLI, speedreview GitHub Action |
| [HEADLESS.md](HEADLESS.md) | Using jCodeMunch with `claude -p` |
| [AGENT_HOOKS.md](AGENT_HOOKS.md) | Agent hooks and prompt policies |
| [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) | Supported languages and parsing details |
| [SECURITY.md](SECURITY.md) | Security controls, data movement, background behavior |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common issues and fixes |
| [CHANGELOG.md](CHANGELOG.md) · [ROADMAP.md](ROADMAP.md) | Release history and what's next |

---

## Licensing and commercial use

jCodeMunch-MCP is released under the **jCodeMunch-MCP Dual-Use License** ([full terms](LICENSE)). **Free for non-commercial use. Commercial use requires a paid license**, one-time, sold by jMunch LLC via Stripe:

**jCodeMunch-only:** [Builder, $79](https://jcodemunch.com/descriptions.php#builder) (1 developer) · [Studio, $349](https://jcodemunch.com/descriptions.php#studio) (up to 5) · [Platform, $1,999](https://jcodemunch.com/descriptions.php#platform) (org-wide internal deployment)

**Full jMunch suite (code + docs + data):** [Trio Builder, $99](https://jcodemunch.com/descriptions.php#builder) · [Trio Studio, $449](https://jcodemunch.com/descriptions.php#studio) · [Trio Platform, $2,499](https://jcodemunch.com/descriptions.php#platform)

Not sure it's worth it? Run your own numbers through the [ROI calculator](https://jcodemunch.com/roi.php?utm_source=github&utm_medium=readme&utm_campaign=jcm_readme_bottom), or forward [the finance-team version](https://jcodemunch.com/for-finance.php?utm_source=github&utm_medium=readme&utm_campaign=jcm_readme_bottom) to whoever signs off. The guarantee stands: if jCodeMunch doesn't pay for itself, you don't pay for jCodeMunch.

Conditions on all uses: retain the copyright notice, clearly mark modifications and keep the original author's name intact (he's kinda full of himself), and include a prominent modification notice in source redistributions. The Software may not be renamed, rebranded, or published to any public package registry, and is provided "AS IS" without warranty. [LICENSE](LICENSE) controls.

---

## FAQ

**How much can I save on Claude / Opus tokens?**
In retrieval-heavy workflows, code-reading tokens typically drop 86-99%, benchmarked at 96.5% average (28.3x) against a grep-and-read agent across 15 tasks and 3 repositories. Per-query results span 7.6x to 81.2x. Methodology: [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md) and [benchmarks/](benchmarks/).

**How is this different from RAG or grep-based tools?**
jCodeMunch retrieves at the **symbol level** with byte-level precision (functions, classes, importers, blast radius, hierarchies) rather than fuzzy chunks (RAG) or raw line matches (grep) the agent still has to read and reason over.

**Is it free for personal use?**
Yes. Commercial use needs a license; see [above](#licensing-and-commercial-use).

**Where's the deep-dive on X?**
Capabilities: [CAPABILITIES.md](CAPABILITIES.md). Config: [CONFIGURATION.md](CONFIGURATION.md). Clients: [CLIENTS.md](CLIENTS.md). Internals: [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md). Or the firehose: [jcodemunch.com](https://jcodemunch.com/).

---

Extras: [OSS code-health observatory](https://jgravelle.github.io/jcodemunch-observatory/) (weekly six-axis snapshots of Express, FastAPI, Gin, Django, and friends) · [Token Cost Radar](https://jcodemunch.com/radar/) (daily AI token cost intelligence) · [jMunch Console](https://github.com/jgravelle/jmunch-console) (free MIT GUI for one-click upgrades)
