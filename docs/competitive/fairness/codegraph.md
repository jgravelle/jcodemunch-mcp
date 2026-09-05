# Fairness note: CodeGraph 1.6.0

Adapter `benchmarks/competitive/adapters/codegraph.py`; image
`benchmarks/competitive/sandbox/codegraph.Dockerfile`; MCP client
`sandbox/mcp_driver.py` (the client that drove the three previous MCP rows).
Written 2026-09-05 before the first number was recorded (DESIGN §1.3).
Everything quoted from the tool is data (principle 5), read from
`colbymchenry/codegraph` at tag v1.6.0 (commit `dfccdf62547f`, release
2026-08-26) through the GitHub API: README.md, TELEMETRY.md, the release's
`SHA256SUMS`, and the linux-x64 release bundle itself (`lib/dist/**/*.js`,
the shipped JavaScript, for the tool schemas and the environment variables
the README names). No page was browsed during a run.

## What the docs recommend

Install: "No Node.js required — one command grabs the right build for your
OS: `curl -fsSL .../install.sh | sh`" or `npm i -g @colbymchenry/codegraph`;
"CodeGraph bundles its own runtime — nothing to compile, no native build,
works the same everywhere." Every release ships "a self-contained build
(bundled Node runtime)" per platform with `SHA256SUMS` and build
attestations. Then `codegraph install` wires the MCP server into each agent
("`command: codegraph, args: [serve, --mcp]`" is the config it writes), and
"`cd your-project && codegraph init` creates the local `.codegraph/`
directory and builds the full graph in the same step". `init --yes` is
"Non-interactive: skip every prompt and take the defaults (for scripts / CI /
container bootstraps)". "Auto-sync is enabled by default. CodeGraph watches
the project and updates the graph on every file change"; `serve --no-watch`
"Disable[s] the file watcher (no auto-sync; useful on slow filesystems like
WSL2 /mnt drives)".

Surface: "When running as an MCP server, CodeGraph exposes a **single
tool** — `codegraph_explore`", which "Answer[s] almost any question in one
call ... returning the relevant symbols' verbatim source grouped by file,
plus the call paths between them and a blast-radius summary". "The other
tools (`codegraph_node`, `codegraph_search`, `codegraph_callers`,
`codegraph_callees`, `codegraph_impact`, `codegraph_files`,
`codegraph_status`) stay fully functional but **unlisted by default** ...
Re-enable any of them for the MCP surface with the `CODEGRAPH_MCP_TOOLS`
environment variable (e.g. `CODEGRAPH_MCP_TOOLS=explore,node,search,callers`)".
`codegraph_node` in file mode returns "that file's current on-disk source
with line numbers ... PLUS a one-line note of which files depend on it";
`symbolsOnly` returns "just the file's symbol map + dependents".
`codegraph_callers` "List[s] functions that call <symbol>".

Environment: "CodeGraph collects anonymous usage statistics ... turn it off
any time: `codegraph telemetry off` or `CODEGRAPH_TELEMETRY=0`, or
`DO_NOT_TRACK=1`" (TELEMETRY.md: `DO_NOT_TRACK=1` is "the cross-tool
standard — always honored"; the shipped code also reads it for the release
update check). `CODEGRAPH_NO_DAEMON=1` is named for "sandboxed environments"
(the watcher disabled; the shipped code: "one process serves one MCP client
over stdio"). `CODEGRAPH_DIR` renames the per-project data directory and
"must be a plain directory name" inside the project root. Skips: dependency,
build and cache directories, `.gitignore`, files over 1 MB.

## What we configured and why

- **Install**: the release bundle `codegraph-linux-x64.tar.gz` (sha256
  `de3391f7…`, matching the release's `SHA256SUMS`), extracted to `/opt`
  and its `bin/codegraph` linked onto PATH, which is what `install.sh` does
  without running the script. The bundle carries the Node runtime and the
  Rust kernel addon; nothing is compiled and nothing is fetched at run.
- **Project copy**: the corpus is mounted read-only and the tool keeps its
  index inside the project root (`.codegraph/`; `CODEGRAPH_DIR` can only
  rename it), so the container copies `/corpus` to `/private/project` (the
  uid-owned tmpfs) and indexes the copy. The copy is a harness cost, timed
  by nobody. The copy sits on tmpfs, so the tool's source reads during
  indexing and answering do NOT pay the Windows bind mount (CF-14) that
  every other adapter's reads pay; recorded under advantages below.
- **Index**: `codegraph init --yes /private/project`, the documented step
  3 with its documented non-interactive flag; its wall time is the index
  time. `files_indexed` is read from `codegraph status` where the format
  allows it.
- **Server**: `codegraph serve --mcp --path /private/project --no-watch`,
  the installer's own launch line (`serve --mcp`, and `--path` is what it
  adds for agents that do not pass a root) plus the documented watcher-off
  flag, because nothing changes under a measurement and a watcher's
  background work would be charged to whichever call it ran under.
- **Environment**: `DO_NOT_TRACK=1` (the documented off-switch; the run is
  `--network none` anyway, so a telemetry or update-check attempt could
  only fail), `CODEGRAPH_NO_DAEMON=1` (the documented sandboxed-environment
  setting: without it the server starts a background daemon and connects
  to it over a socket, a second process the container would also contain),
  `CODEGRAPH_MCP_TOOLS=explore,node,search,callers` (the README's own
  example, verbatim), `HOME=/private`; the driver's `MCP_DRIVER_TIMEOUT_S=300`.
- **Surface**: `tools/list` is measured with the README's example allowlist
  above, because that is the surface the calls below use. The default
  surface is ONE tool; its weight is recorded from a probe in the last
  section, not as a row, and a row per surface is a separate configuration.
- **Payload**: each tool's DEFAULT output (the text the server returns).
  Citations are parsed from that text (the `path:line` and `path` forms
  the tool prints); no twin call is run.
- **Commands per task category** (DESIGN §4.1):
  - P1 definition lookup: `codegraph_search(query=<name>, limit=3)` (the
    documented "Quick symbol search by name. Returns locations only") and
    `codegraph_node(symbol=<name>, includeCode=true)` ("ONE SYMBOL you can
    name — its location, signature, verbatim source"), both charged, in
    one session: the tool's own search-then-read, mirroring our
    search-then-read-3 (R27) with the read as one documented call. The
    `explore` tool the docs say to call first returns the source of every
    related symbol across several files (a probe: 70 symbols across 5
    files for one name); it is the T route below, where that is the
    question.
  - T token task: `codegraph_explore(query=<the query words>)`, charged,
    ONE SESSION PER T TASK: `explore` records what it sent earlier in a
    session and elides those lines on a later call ("Already sent earlier
    in this conversation ... Only the NEW lines are shown"), so a shared
    session would make a task's token count depend on which tasks ran
    before it. Each T container copies and indexes the corpus again; the
    index time is the first container's.
  - P2 reference finding: `codegraph_callers(symbol=<name>)`, charged.
  - P4 file dependencies: `codegraph_node(file=<path>, symbolsOnly=true)`,
    charged; the dependents it lists are the citations. The tool prints
    the first eight dependents and `+K more`; the citations are the
    eight, the count is in the payload, and nothing is invented (a probe:
    "used by 83 files" for the P4 file, eight named).
- Every call is charged; nothing is called uncharged.

## Where the harness may disadvantage it

1. **`--no-watch` and `CODEGRAPH_NO_DAEMON`** take away the shared
   background server and the auto-sync that its docs present as the
   product ("The index is never stale"); a measurement of a fixed corpus
   has no use for either, and the daemon's warm shared process might make
   a second session's first call cheaper than the row shows.
2. **`explore` returns whole symbols' source grouped by file** for a T
   query (a probe: 24.9 KB for one), and every byte is charged; a tool
   that returns locations is charged less on the token axis. The token
   row is the tool's answer to that query, and an agent asking a keyword
   question of this tool gets exactly that answer.
3. **Citations are parsed from prose-shaped output**; a location the
   parser does not recognise is a missed citation, not the tool's miss.
   The probe section records the shapes the parser reads.
4. **P4 through `codegraph_node` file mode** is one reading of "which
   files depend on this file"; `codegraph_impact` and `explore`'s
   blast-radius summary are others, and an agent might use those. The
   file mode names eight dependents and a `+K more` count, so recall on
   P4 is capped by the tool's own output shape, not by what its graph
   holds (it says 83); the row records what it returns.
5. **The README's example allowlist**, not the default surface, is what
   the schema row weighs; the default is lighter (one tool).
6. **Python only so far**: the tool's other languages and its framework
   route nodes are not exercised by this corpus.

## Where the harness may advantage it

- The project copy on tmpfs (above): its file reads are memory reads
  while every other adapter reads the corpus over the Windows bind mount
  (CF-14). The Linux runner's rows remove that difference.

## What we could not make work

Nothing of the tool's is broken. Probe figures (2026-09-05, self corpus,
one container each, not results):

- `codegraph init --yes` on the tmpfs copy: exit 0 in 4.08 s wall; its
  own log "Indexed 1,022 files ... 22,999 nodes, 57,032 edges in 1.4s"
  and `status` "Files: 1,022" (it indexes tests, benchmarks and YAML the
  corpus digest also covers; `files_indexed` reports the tool's count).
- `initialize` 108.8 ms; `serverInfo` reads `codegraph 1.6.0`, the tool's
  own version (the CF-26/CF-30 shape does not recur here).
- `tools/list` under the README's example allowlist: 4 tools, 1,523
  tokens (the row). The default surface: 1 tool, 392 tokens (not a row).
- Per-call latency in the probe: search 1.6 ms, node 2.0 ms, callers
  2.2 ms, node file mode 4.5 ms, explore 130 to 135 ms.
- `codegraph_callers(cache_put)` names two callers: the same-file
  `result_cache_put` and `_fill` in `tests/test_cache_hit_rate_basis.py`,
  which does call it. The self task set's P2 gold lists one. The second
  is a real reference the gold omits, so the P2 row scores the tool
  against an incomplete gold; recorded in FINDINGS for item 3, and the
  row is recorded as measured.
- `explore` for a T query returned 24.9 KB (109 symbols across 7 files,
  with source); that is the tool's answer and the token row's shape.
- A call to a tool outside the allowlist answers `Error: Tool
  codegraph_status is disabled via CODEGRAPH_MCP_TOOLS` in 12 ms; the
  plan calls none.
