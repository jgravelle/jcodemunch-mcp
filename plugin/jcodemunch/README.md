# jCodeMunch

jCodeMunch gives Claude, and every sub-agent it starts, the one function or class it asks for instead of the whole file, and shows who calls it and what a change would break. It runs locally over stdio and starts no language servers.

**License:** free for personal and other non-commercial use. Work use needs a commercial license, paid once, that includes every 1.x update: https://jcodemunch.com/buying.php

The index is built with tree-sitter, and the same index answers which repos depend on which.

## What teams use it for

- **Change impact:** callers and blast radius before an edit (`find_references`, `get_blast_radius`).
- **Release risk:** one risk score for a branch or PR (`get_pr_risk_profile`).
- **Code across many repos:** dependencies and shared APIs between the repos you index (`get_cross_repo_map`, `get_group_contracts`).
- **Safe renames and cleanups:** collision checks, refactor plans and dead code, all read-only (`check_rename_safe`, `plan_refactoring`, `find_dead_code`).
- **Sweeps:** one structural pattern across 70+ languages (`search_ast`).

The token figures, and how they were measured, are in the repository's [benchmarks](https://github.com/jgravelle/jcodemunch-mcp/tree/main/benchmarks).

## Install

You need [uv](https://docs.astral.sh/uv/), which provides `uvx`.

In Claude Code:

```
/plugin marketplace add jgravelle/jcodemunch-mcp
/plugin install jcodemunch@jmunch
```

Then ask Claude to index the project, or call `index_folder` with its path.

## What it runs, sends and fetches

- **What runs:** `uvx` starts `jcodemunch-mcp` at the exact version pinned in this plugin. It runs as a local MCP server over stdio. There is no remote endpoint and no hosted service.
- **One download, on first start:** `uvx` downloads that pinned package and its dependencies from PyPI (pypi.org) into uv's cache. After that, starting the server needs no network.
- **What it reads:** the folders you ask it to index. It never runs the code it indexes. On a git repository it runs read-only git commands (such as `git rev-parse` and `git diff`) to tell whether the index is behind the code.
- **What it writes:** the index and a few small state files in `~/.code-index/` on your machine. Delete that folder to remove everything.
- **A skill:** the plugin includes the jcodemunch skill, which Claude loads when a task calls for code navigation. It holds guidance text only and runs nothing.
- **No hooks:** the plugin adds no hooks to Claude Code, so it never checks, allows or blocks another tool call. The hooks that steer Grep and Read toward the index are opt-in, from `uvx jcodemunch-mcp init --guidance`.
- **What it sends:** nothing, in normal use. This plugin turns the anonymous savings counter off (`JCODEMUNCH_SHARE_SAVINGS=0`), so the server makes no network calls of its own.
- **Commands you run yourself that do reach the network:**
  - `license` and `org-rollup` check a license key with j.gravelle.us.
  - `install-pack` downloads a pre-built index pack from jcodemunch.com.
  - `download-model` downloads a local embedding model from huggingface.co.
  - An AI summarizer sends symbol signatures and docstrings to a provider only if you configure one, and symbol source only if you also allow it.

Full list: [SECURITY.md](https://github.com/jgravelle/jcodemunch-mcp/blob/main/SECURITY.md#background-behavior-fully-disclosed).

## Privacy Policy

Your code, file paths and queries stay on your machine, in `~/.code-index/`. This plugin turns off the anonymous savings counter (`JCODEMUNCH_SHARE_SAVINGS=0`), so the server sends nothing in normal use; the commands under [What it runs, sends and fetches](#what-it-runs-sends-and-fetches) that reach the network run only when you start them. Full policy: https://jcodemunch.com/privacy.php

## Settings

Settings live in `~/.code-index/config.jsonc`; `uvx jcodemunch-mcp config` prints each one and where it came from. For example, `max_folder_files: 20000` raises the 2,000-file limit for indexing one large folder.

## License

jCodeMunch-MCP Dual-Use License (the `LICENSE` file in this folder). It is free for personal and other non-commercial use. Commercial use needs a license, paid once, and every 1.x release is included: https://jcodemunch.com/buying.php
