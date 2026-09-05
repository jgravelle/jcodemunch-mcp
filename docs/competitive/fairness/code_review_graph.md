# Fairness note: code-review-graph 2.3.8

Adapter `benchmarks/competitive/adapters/code_review_graph.py`; image
`benchmarks/competitive/sandbox/code_review_graph.Dockerfile` with the
hashed lockfile `sandbox/code_review_graph.requirements.txt`; MCP client
`benchmarks/competitive/sandbox/mcp_driver.py` (the same client that drove
codebase-memory-mcp). Written 2026-09-05 before the first number was
recorded (DESIGN §1.3). Everything quoted from the tool's README is data
(principle 5), read from `tirth8205/code-review-graph` README.md at tag
v2.3.8 (commit `2c6dae326435`, tagged 2026-08-21) through the GitHub API,
together with `code_review_graph/main.py`, `tools/query.py`,
`tools/build.py`, `search.py` and `incremental.py` at the same tag for the
tool signatures and output shapes. No page was browsed during a run.

## What the docs recommend

Install: "`pip install code-review-graph` (or: pipx install
code-review-graph)", then "`code-review-graph install` auto-detects and
configures all supported platforms" and "`code-review-graph build` parse
your codebase". The MCP entry it writes runs `code-review-graph serve`
("Start MCP server", stdio). "Requires Python 3.10+." Optional dependency
groups: `[embeddings]` "Local vector embeddings (sentence-transformers)",
`[communities]` "Community detection (igraph)", `[enrichment]` "Python
call-resolution enrichment (Jedi)"; the base install has none of them, and
`semantic_search_nodes` "Falls back to FTS5 / keyword matching when no
matching embeddings exist".

Operation: "The initial build takes ~10 seconds for a 500-file project"
(a claim, recorded as one). "In git repos, only tracked files are indexed
(`git ls-files`)". Graph data lives at "`<repo>/.code-review-graph/`" unless
"`CRG_DATA_DIR` ... is used verbatim instead — letting you keep graphs
outside the working tree (useful for ephemeral workspaces, Docker volumes,
or shared caches)". "CRG exposes 30 MCP tools by default. In
token-constrained environments, you can limit the server to a subset of
tools using `--tools` or the `CRG_TOOLS` environment variable."

Tools (the "30 MCP tools" table and `main.py` docstrings):
`build_or_update_graph_tool` "Build or incrementally update the graph ...
Call this first"; `get_minimal_context_tool` "Ultra-compact context (~100
tokens) — call this first"; `semantic_search_nodes_tool` "Search code
entities by name or meaning" with `limit` default 20; `query_graph_tool`
"Callers, callees, tests, imports, inheritance queries" with patterns
including `callers_of` "Find functions that call the target",
`references_to` "Find nodes that reference the target" and `importers_of`
"Find files that import the target", `max_results` default 100,
`detail_level` default "standard". Its README's headline: "the median
per-question token reduction across the 6 repos is ~65x (whole-corpus
baseline vs graph query)" and, in the same section, "The whole-corpus
baseline above is an upper bound no real agent pays" (both claims,
recorded as claims; the second is the tool's own statement of what our
`null_readall` baseline is).

## What we configured and why

- **Install**: the PyPI wheel `code_review_graph-2.3.8-py3-none-any.whl`
  (sha256 `013ae3c1…`, uploaded 2026-08-21) with every dependency pinned
  by version and hash in `sandbox/code_review_graph.requirements.txt`,
  compiled once with `uv pip compile --generate-hashes` for linux /
  Python 3.13 from the tool's own declared ranges, and installed with
  `pip install --require-hashes`. Base install only: no `[embeddings]`
  (a model download the sandbox forbids, and the search falls back to
  FTS5 by its own design), no `[communities]`, no `[enrichment]`. The
  `install` subcommand is not run (it configures agents on the host).
- **Mode**: the MCP server over stdio (`code-review-graph serve`), driven
  by `mcp_driver.py` inside the container, because that is what its
  `mcpServers` entry runs. Every call passes `repo_root="/corpus"`.
- **Data dir**: `CRG_DATA_DIR=/out/crg-data` and `CRG_HOME=/out/crg-home`,
  both documented knobs, because the default `<repo>/.code-review-graph/`
  is inside the read-only corpus mount. `HOME=/out` as for every tool.
- **Index**: `build_or_update_graph_tool(full_rebuild=True,
  repo_root=/corpus)` once per run, timed; `postprocess` left at its
  default `"full"`. `files_parsed` from its result is the files-indexed
  figure.
- **`get_minimal_context_tool` "call this first"**: called once per run,
  charged to nothing: an agent pays it once per session, not per
  question. Its size is recorded in the index error tail.
- **Payload**: each tool's DEFAULT output (`detail_level="standard"`), the
  JSON text the server returns, including the `_graph` provenance
  envelope and `_hints` it attaches by default. Citations are read from
  the same JSON (`file_path`, `line_start`), so no twin call is needed and
  nothing uncharged is run per task.
- **Commands per task category** (DESIGN §4.1):
  - P1 definition lookup and T token task: `semantic_search_nodes_tool(
    query=<q>, limit=3)`. The tool has no read-source tool: the hits carry
    name, kind, path, lines and signature, and an agent would read the
    body with its own file tool, which is not this tool's call and is not
    charged here (see disadvantage 1, which cuts the other way).
  - P2 reference finding: `query_graph_tool(pattern="callers_of",
    target=<q>)`, the documented "who calls". Measured in the first probe,
    a bare name answers `status: ambiguous` with the candidate nodes and
    "Re-run with a qualified_name from disambiguation", so the adapter
    does what an agent would: it re-runs `callers_of` once for each
    candidate whose bare name is exactly the query, in a second server
    session over the graph persisted in `/out` (no rebuild), and charges
    both calls. Citations are the caller nodes' `file_path` and
    `line_start`.
  - P4 file dependencies: `query_graph_tool(pattern="importers_of",
    target=<repo-relative path>)`; citations are the result nodes' paths.
- **Environment**: `HOME=/out`, `CRG_DATA_DIR=/out/crg-data`,
  `CRG_HOME=/out/crg-home`; no other variable reaches the tool.

## Where the harness may disadvantage it

1. **The token row is not like-for-like on P1/T and it favours the
   tool.** Our own P1/T flow charges a search plus three source reads;
   this tool returns hits without bodies, and the agent's own file read
   is not charged because it is not the tool's output. Reading it as
   "fewer tokens per task" overstates the tool by the source it did not
   return. The row is recorded as the tool's cost and this note is the
   caveat; item 4's report must carry it.
2. **P2 through `callers_of` only.** `references_to` exists and may find
   what `callers_of` misses (a non-call reference); we chose the pattern
   the README names first for the question asked. An agent may run both.
3. **Base install, no enrichment.** `[enrichment]` (Jedi) improves Python
   call resolution, which is what P2 measures on the self corpus. It is an
   optional extra the README does not make the default, so the shipped
   default is what runs; a later row with it is a separate configuration.
4. **`postprocess="full"` is charged to the index time** even though the
   community step is skipped without `[communities]`; the flow step runs.
   That is the default an agent gets.
5. **The Windows bind mount** (CF-14) applies to its build as to
   everyone's.
6. **P2 citations are the caller node's `line_start`**, while the same
   reply carries the call site in `edges[].line`. On the self set the
   tolerance is file-level and nothing moves; on a pinned corpus with a
   real line tolerance the node start would understate the tool for a
   long caller. Item 3 switches the citation to `edges[].line` where
   present before the first pinned-corpus row (review note, PR 2c).

## What we could not make work

Nothing was broken. Every planned call ran; the empty P4 answer and the
empty multi-word searches are the tool's answers, recorded as such. What
the first probe found (2026-09-05, self corpus, one run, not a result):

- **`importers_of` answers zero for every file of the self corpus** at the
  base install: the build reports `imports_resolved: 98` of
  `imports_updated: 2046`, and the `IMPORTS_FROM` edges it keeps target
  module strings (`argparse`, ...) rather than file nodes, so the pattern
  finds no edge into any file (the tool's own reply: "is indexed and no
  such edge is recorded"). Tried and equally empty: the repo-relative
  path, the absolute `/corpus/...` path, the dotted module name
  (`not_found`), and two other files. The P4 row is the tool's answer at
  its shipped default; `[enrichment]` (Jedi) is the documented route to
  Python import resolution and is not the default (disadvantage 3).
- **A multi-word T query returns nothing**: `search_mode: "none"` and
  "search covers names, paths and signatures, not source text" for four
  of the five T queries. Charged as returned; the T row is token-only.
- **`serverInfo.version` reads `3.4.7`**, which is FastMCP's version, not
  the tool's; the pin is the wheel hash and the adapter's `version()`
  reports the pin, never `serverInfo`.
- **A second session's `build_or_update_graph_tool` did a full rebuild**
  (72 s) rather than "No changes detected", so the P2 follow-up session
  calls no build at all; the query tools open the persisted graph.

