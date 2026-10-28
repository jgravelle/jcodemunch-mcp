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

On our benchmark, a lookup returns 96.2% fewer tokens than grep-and-read. That figure covers 15 lookups on express, fastapi and gin at pinned commits, counted with the cl100k tokenizer. It is per lookup, not per session.

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
- **What it reads:** the folders you ask it to index. It never runs the code it indexes. On a git repository, before a read it runs read-only git commands (`git diff`, `git ls-files`) to find files changed since the index was built, and re-indexes them.
- **What it writes:** the index and a few small state files in `~/.code-index/` on your machine. Delete that folder to remove everything.
- **No hooks:** the plugin adds no hooks to Claude Code, so it never checks, allows or blocks another tool call. The hooks that steer Grep and Read toward the index are opt-in, from `uvx jcodemunch-mcp init --guidance`.
- **What it sends:** nothing, in normal use. This plugin turns the anonymous savings counter off (`JCODEMUNCH_SHARE_SAVINGS=0`), so the server makes no network calls of its own.
- **Commands you run yourself that do reach the network:**
  - `license` and `org-rollup` check a license key with j.gravelle.us.
  - `install-pack` downloads a pre-built index pack from jcodemunch.com.
  - `download-model` downloads a local embedding model from huggingface.co.
  - An AI summarizer sends symbol signatures and docstrings to a provider only if you configure one, and symbol source only if you also allow it.

Full list: [SECURITY.md](https://github.com/jgravelle/jcodemunch-mcp/blob/main/SECURITY.md#background-behavior-fully-disclosed).

## Settings

Settings live in `~/.code-index/config.jsonc`; `uvx jcodemunch-mcp config` prints each one and where it came from. For example, `refresh_on_read: false` stops the re-index before a read.

## License

jCodeMunch-MCP Dual-Use License (the `LICENSE` file in this folder). It is free for personal and other non-commercial use. Commercial use needs a license, paid once, and every 1.x release is included: https://jcodemunch.com/buying.php
