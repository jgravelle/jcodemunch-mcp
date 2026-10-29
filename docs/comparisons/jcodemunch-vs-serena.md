# jCodeMunch and Serena

Checked 29 October 2026 against Serena v2.1.0 (released 19 October 2026; github.com/oraios/serena) and jCodeMunch 1.108.402.

Both are MCP servers that let a coding agent find code by symbol instead of reading whole files. They get there differently, and they're good at different jobs. A lot of people run both.

## How each one finds code

**Serena** runs real language servers (pyright, rust-analyzer, typescript-language-server, gopls and others) and routes the agent's calls through them. Its README lists support for over 40 languages that way. Each language needs its server installed. A paid JetBrains plugin (free trial) uses the IDE's own analysis instead. Because the answers come from a language server, references are type-aware: they follow aliases, inheritance and type narrowing the way your IDE does.

**jCodeMunch** parses source with tree-sitter grammars that ship inside the package, for 70+ languages. There is nothing else to install and no server process to start. It builds an index on disk once, updates it as files change, and returns a function or class by byte offset.

## What each one does

| | Serena | jCodeMunch |
|---|---|---|
| Read code by symbol | Yes, through the language server | Yes, from the tree-sitter index |
| Find references | Type-aware, from the language server; type hierarchy with the JetBrains plugin | `find_references`, `find_importers`, `check_references` from the index and import graph |
| Edit code | Yes: replace a symbol body, insert before or after a symbol, rename, safe delete; move and inline with the JetBrains plugin | No. Read-only by design. `plan_refactoring` returns edit blocks for the agent to apply with its own Edit tool |
| Compiler diagnostics | Yes, the language server's errors and warnings as tools | No |
| Debugging | Yes, with the JetBrains plugin: breakpoints and variable inspection | No |
| Change impact | Not listed in its README | `get_blast_radius`, `get_impact_preview`, `get_pr_risk_profile` |
| Dead code | Removing unused code ("propagate deletions") with the JetBrains plugin | `find_dead_code`, `check_delete_safe` |
| Docs and data files | Code only | Docs through jDocMunch, tabular data through jDataMunch |
| What you install | Serena plus a language server per language (or the JetBrains plugin) | One Python package |
| License | GPL-3.0-or-later since v2.0 (earlier releases MIT; its solidlsp library MIT) | Free for personal and other non-commercial use; commercial license, paid once |

## Tokens

On our benchmark, 15 lookups (5 each on express, fastapi and gin at pinned commits), jCodeMunch (one symbol search, then the source of the top 3 matches) returned 96.2% fewer tokens than a scripted grep-and-read baseline (search the query terms, read the top 3 matching files), counted with the cl100k tokenizer. Method and raw data are in `benchmarks/`.

That's against grep-and-read, not against Serena. We haven't published a measurement of the two side by side, and Serena's README doesn't publish a token benchmark either. If you want to know which one reads less on your code, run both on the same questions.

## Which to reach for

Serena fits when the agent is editing: type-aware renames and symbol edits, compiler diagnostics in the loop, or debugging through JetBrains, on a machine where the language servers are already set up.

jCodeMunch fits when there's no language server to lean on: CI runners, containers, a repo that mixes many languages, or a language without a good server. It also answers the team-level questions (what a change touches, what's dead, what a PR risks) and covers docs and data alongside code.

They can run together. If you do, the agent sees two ways to look up a symbol, so tell it which one to use for what (for example, jCodeMunch for finding and reading, Serena for editing).

## Claude Code's code intelligence plugins

Claude Code has opt-in code intelligence plugins, one per language, that connect a language server: the same idea Serena is built on. They give the agent go to definition, find references, type information, symbol search, implementations and call hierarchy, and they report type errors after each edit. You install the language server binary yourself, and Anthropic's docs describe the plugins for terminal sessions; cloud sessions don't run plugin language servers. If you already use one for go-to-definition in a typed language, keep it. jCodeMunch covers the cases where no server is running, including cloud sessions, and the repo-wide questions above.

## Sources

- Serena: README and GitHub API for oraios/serena, read 29 October 2026.
- jCodeMunch: this repository at 1.108.402; benchmark in `benchmarks/`.
- Claude Code: Anthropic's code intelligence plugin docs (code.claude.com/docs/en/plugins/code-intelligence), read 29 October 2026.

Corrections are welcome as an issue on this repository.
