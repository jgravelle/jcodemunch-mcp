Quickstart - https://github.com/jgravelle/jcodemunch-mcp/blob/main/QUICKSTART.md

## FREE FOR PERSONAL USE
**Use it to make money, and Uncle J. gets a taste. Fair enough?** [details](#commercial-licenses)

---

## Cut code-reading token usage by **95% or more**

Most AI agents explore repositories the expensive way:

open entire files → skim thousands of irrelevant lines → repeat.

That is not “a little inefficient.”
That is a **token incinerator**.

**jCodeMunch indexes a codebase once and lets agents retrieve only the exact code they need**: functions, classes, methods, constants, outlines, and tightly scoped context bundles, with byte-level precision.

In retrieval-heavy workflows, that routinely cuts code-reading token usage by **95%+** because the agent stops brute-reading giant files just to find one useful implementation.

| Task                   | Traditional approach      | With jCodeMunch                             |
| ---------------------- | ------------------------- | ------------------------------------------- |
| Find a function        | Open and scan large files | Search symbol → fetch exact implementation  |
| Understand a module    | Read broad file regions   | Pull only relevant symbols and imports      |
| Explore repo structure | Traverse file after file  | Query outlines, trees, and targeted bundles |

Index once. Query cheaply. Keep moving.
**Precision context beats brute-force context.**

---

# jCodeMunch MCP

### Structured code retrieval for serious AI agents

![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![Polyglot](https://img.shields.io/badge/parsing-tree--sitter-9cf)
![jMRI](https://img.shields.io/badge/jMRI-Full-blueviolet)
[![PyPI version](https://img.shields.io/pypi/v/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jcodemunch-mcp)](https://pypi.org/project/jcodemunch-mcp/)

> ## Commercial licenses
>
> jCodeMunch-MCP is **free for non-commercial use**.
>
> **Commercial use requires a paid license.**
>
> **jCodeMunch-only licenses**
>
> * [Builder — $79](https://j.gravelle.us/jCodeMunch/descriptions.php#builder) — 1 developer
> * [Studio — $349](https://j.gravelle.us/jCodeMunch/descriptions.php#studio) — up to 5 developers
> * [Platform — $1,999](https://j.gravelle.us/jCodeMunch/descriptions.php#platform) — org-wide internal deployment
>
> **Want both code and docs retrieval?**
>
> * [Munch Duo Builder Bundle — $89](https://j.gravelle.us/jCodeMunch/descriptions.php#builder)
> * [Munch Duo Studio Bundle — $399](https://j.gravelle.us/jCodeMunch/descriptions.php#studio)
> * [Munch Duo Platform Bundle — $2,249](https://j.gravelle.us/jCodeMunch/descriptions.php#platform)

**Stop paying your model to read the whole damn file.**

jCodeMunch turns repo exploration into **structured retrieval**.

Instead of forcing an agent to open giant files, wade through imports, boilerplate, comments, helpers, and unrelated code, jCodeMunch lets it navigate by **what the code is** and retrieve **only what matters**.

That means:

* **95%+ lower code-reading token usage** in many retrieval-heavy workflows 
* **less irrelevant context** polluting the prompt
* **faster repo exploration**
* **more accurate code lookup**
* **less repeated file-scanning nonsense**

It indexes your codebase once using tree-sitter, stores structured symbol metadata plus byte offsets into the original source, and retrieves exact implementations on demand instead of re-reading entire files over and over.

Recent releases have also made that retrieval workflow sharper and more useful in real engineering work, with BM25-based symbol search, context bundles, compact search modes, query suggestions for unfamiliar repos, dependency graphs, class hierarchy traversal, blast-radius analysis, multi-symbol bundles, live watch-based reindexing, and benchmark reproducibility improvements.

---

## Why agents need this

Most agents still inspect codebases like tourists trapped in an airport gift shop:

* open entire files to find one function
* re-read the same code repeatedly
* consume imports, boilerplate, and unrelated helpers
* burn context window on material they never needed in the first place

jCodeMunch fixes that by giving them a structured way to:

* search symbols by name, kind, or language
* inspect file and repo outlines before pulling source
* retrieve exact symbol implementations only
* grab a context bundle when surrounding imports matter
* fall back to text search when structure alone is not enough

Agents do not need bigger and bigger context windows.

They need **better aim**.

---

## What you get

### Symbol-level retrieval

Find and fetch functions, classes, methods, constants, and more without opening entire files.

### Faster repo understanding

Inspect repository structure and file outlines before asking for source.

### Lower token spend

Send the model the code it needs, not 1,500 lines of collateral damage.

### Better engineering workflows

Useful for onboarding, debugging, refactoring, impact analysis, and exploring unfamiliar repos without brute-force file reading.

### Local-first speed

Indexes are stored locally for fast repeated access.

---

## How it works

jCodeMunch indexes local folders or GitHub repos, parses source with tree-sitter, extracts symbols, and stores structured metadata alongside raw file content in a local index. Each symbol includes enough information to be found cheaply and retrieved precisely later. 

That includes metadata like:

* signature
* kind
* qualified name
* one-line summary
* byte offsets into the original file

So when the agent wants a symbol, jCodeMunch can fetch the exact source directly instead of loading and rescanning the full file.

---

## Start fast

### 1. Install it

```bash
pip install jcodemunch-mcp
```

### 2. Add it to your MCP client

If you’re using Claude Code:

```bash
claude mcp add jcodemunch uvx jcodemunch-mcp
```

### 3. Tell your agent to actually use it

This matters more than people think.

Installing jCodeMunch makes the tools available. It does **not** guarantee the agent will stop its bad habit of brute-reading files unless you instruct it to prefer symbol search, outlines, and targeted retrieval. The changelog specifically calls out improved onboarding around this because it is a real source of confusion for first-time users. 

A simple instruction like this helps:

```markdown
Use jcodemunch-mcp for code lookup whenever available. Prefer symbol search, outlines, and targeted retrieval over reading full files.
```

---

## Best for

* large repositories
* unfamiliar codebases
* agent-driven code exploration
* refactoring and impact analysis
* teams trying to cut AI token costs without making agents dumber
* developers who are tired of paying premium rates for glorified file scrolling

---

## New here?

Start with **[QUICKSTART.md](QUICKSTART.md)** for the fastest setup path.

Then index a repo, ask your agent what it has indexed, and have it retrieve code by symbol instead of reading entire files. That is where the savings start.

