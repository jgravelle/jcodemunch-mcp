# Fairness note: codebase-memory-mcp 0.10.8

Adapter `benchmarks/competitive/adapters/codebase_memory.py`; image
`benchmarks/competitive/sandbox/codebase_memory.Dockerfile`; MCP client
`benchmarks/competitive/sandbox/mcp_driver.py`. Written 2026-09-05 before
the first number was recorded (DESIGN §1.3). Everything quoted from the
tool's README is data (principle 5), read from
`DeusData/codebase-memory-mcp` README.md at tag v0.10.8 (commit
`46ae198fc11c`, release 2026-08-19) through the GitHub API; the tool's own
`tools/list` input schemas were read from the running server in the sandbox.
No page was browsed during a run.

## What the docs recommend

Install: "One-line install" scripts, or pre-built binaries per platform
("Manual install ... Extract and install (each archive includes
`install.sh`)"), or "npm or pip". The installer "auto-detects installed
coding agents and configures their documented MCP entries". Manual MCP
configuration is one `mcpServers` entry with `"command": "/path/to/codebase-memory-mcp", "args": []`.

Operation: "CBM automatically shares one per-account coordination daemon
... the first daemon-backed CBM session starts it". "The ordinary `cli`
mode is intentionally separate: it runs one command locally and never
starts or connects to the coordination daemon"; and measured, every CLI
command prints "hint: this command started a temporary CBM daemon.
`codebase-memory-mcp daemon start` keeps one warm and removes this startup
cost from every CLI command."

Tools (the "MCP Tools" tables): `index_repository` "Index a repository into
the graph"; `search_graph` "Structured search by label, name pattern, file
pattern, degree filters"; `trace_path` "BFS traversal — who calls a function
and what it calls"; `query_graph` "Execute Cypher-like graph queries
(read-only)"; `get_graph_schema` "Node/edge counts ... Run this first";
`get_code_snippet` "Read source code for a function by qualified name";
`search_code` "Grep-like text search within indexed project files". Its
"Why" section leads with "120x fewer tokens — 5 structural queries: ~3,400
tokens vs ~412,000 via file-by-file search" (a claim, recorded as one).

## What we configured and why

- **Install**: the `linux-amd64-portable` release archive, verified against
  the published `checksums.txt` (`6eef4965…`) at image build, the binary
  copied to `/usr/local/bin`. Not the PyPI package: its wheel is 14 KB and
  fetches the native runtime on first run, a network step this sandbox
  forbids after build. Not the non-portable archive: it needs glibc 2.38
  and the pinned base is bookworm (2.36). The installer script is not run
  (it configures agents on the host).
- **Mode**: the MCP server over stdio, driven by `mcp_driver.py` inside the
  container, because that is the documented primary integration (the
  `mcpServers` entry) and because the CLI mode starts a temporary daemon
  per command that its own hint says costs seconds (measured: about 1.3 s
  of startup per CLI command, 4 s when it indexes). The daemon it starts
  lives and dies inside the container. Per-call latency is a stdio round
  trip, the same thing an agent waits for.
- **Private home**: the tool refuses a cache under a directory it does not
  own ("the directory CONTAINING 'cbm-cache' is not a usable
  private-directory parent"), which the `/out` bind mount is. The sandbox
  gives it a uid-owned, mode 0700 tmpfs at `/private` (`sandbox.run(...,
  private_home=True)`), `HOME` and `CBM_CACHE_DIR` there. Nothing else
  changes.
- **Index**: `index_repository(repo_path=/corpus)` once per run, timed;
  measured on the self corpus the tool refuses the mount root ("path is too
  broad to index as one root; name a project directory below it") and
  accepts `/corpus/src`, so the adapter falls back to indexing each
  top-level directory of the corpus that holds files, as its own project,
  and prefixes that project's relative paths with the directory name when
  citing. A query then runs once per project and every project's answer
  is charged, because that is what an agent would have to do. `mode` is
  left at its default (`full`).
- **`get_graph_schema` "Run this first"**: called once per run, its size
  recorded in the result's index error tail, charged to nothing: an agent
  pays it once per session, not per question.
- **Payload**: each tool's DEFAULT output (`format: tree` where the schema
  offers it, the "prefix-grouped text rows" it documents as the default).
  Citations come from an uncharged twin call with `format: json` where the
  tool has one, or from the same result when it is already JSON
  (`get_code_snippet`).
- **Commands per task category** (DESIGN §4.1):
  - P1 definition lookup and T token task: `search_graph(query=<q>,
    limit=3)` (its BM25 "Natural-language or keyword full-text search")
    then `get_code_snippet(qualified_name=<top hit>)` for each of the top
    3, mirroring our own search-then-read-3 (R27).
  - P2 reference finding: `trace_path(function_name=<q>, direction=inbound,
    depth=1)`, the documented "who calls a function"; citations from an
    uncharged `query_graph` over `CALLS` edges returning `file_path` and
    `start_line`, because the tree output names callers by qualified name
    without a line.
  - P4 file dependencies: `query_graph` with `MATCH (a)-[:IMPORTS]->(b)
    WHERE b.path CONTAINS '<file>' RETURN DISTINCT a.path`, the shape the
    README's own Cypher examples use; the charged payload is that result.
- **Environment**: `HOME=/private`, `CBM_CACHE_DIR=/private/cbm-cache`;
  no other variable reaches the tool.

## Where the harness may disadvantage it

1. **The root refusal costs it a project split.** On a corpus whose root it
   calls "too broad", every question is asked once per top-level project
   and every answer charged. On the self corpus there is one (`src`), so
   nothing is doubled there; on a pinned repository with several top-level
   directories it may be. The reason for the refusal is the tool's own
   heuristic and the adapter does what its message says ("name a project
   directory below it").
2. **P4 through hand-written Cypher.** An agent would have to write the
   query too, but a better query than ours may exist; the one used is the
   simplest that returned rows in the probe. The uncharged citation twins
   cost it nothing.
3. **Per-call latency includes the daemon's first-use cost** in whichever
   call first touches it inside the session; that is what an agent pays
   too.
4. **`search_graph` at `limit=3`** truncates its default 50; the tool
   reports `has_more: true` in the payload, which is charged. We chose 3
   to match our own top-3 fetch; the tool's default would be larger and
   more expensive for it.
5. **The Windows bind mount** (CF-14) applies to its index step as to
   everyone's.

## What we could not make work

- The CLI mode as the measured interface: it works (`--help`, `version`,
  `list_projects`) but starts a temporary daemon per command, so it was
  not chosen (see Mode). Nothing was broken.
- `index_repository` on the mount root `/corpus` (refused as "too broad";
  handled by the fallback above).
