# Fairness note: Graft 0.16.0

Adapter `benchmarks/competitive/adapters/graft.py`; image
`benchmarks/competitive/sandbox/graft.Dockerfile` with the hash lockfile
`sandbox/graft.package-lock.json`; MCP client `sandbox/mcp_driver.py` (the
client that drove the four previous MCP rows). Written 2026-09-05 before
the first number was recorded (DESIGN §1.3). Everything quoted from the
tool is data (principle 5), read from the npm package `@nanonets/graft`
0.16.0 (integrity `sha512-L3E5F1aD…`, its `gitHead` `aa1e2bb0f632`,
"release: 0.16.0", 2026-08-31, in `trailhq/Graft`, which the package's
`NanoNets/context-graph-engine` URL redirects to) through the npm registry
and the GitHub API: README.md, TELEMETRY.md, `.env.example` at that commit,
and the shipped JavaScript in the tarball (`dist/mcp/tools.js` for the tool
schemas, `dist/cli.js` for the options). No page was browsed during a run.

## What the docs recommend

Install: "`npm install -g @nanonets/graft`   # install the CLI, once", then
"`graft init`   # build the graph + wire it into Claude Code"; "`graft
build [dir]`   # build graft/ from the code at [dir]: wiring graph +
per-file cards (no LLM, no key)". "On your machine, no key, no network:
the structural code graph. `graft build` (wiring graph + per-file cards),
`graft check`, and `graft ask` are deterministic tree-sitter — they never
call a model." The LLM layer is `graft build --deep` "Through your provider
key"; DESIGN §1.3 pins this row to the deterministic path. "Every pass is
cached by content hash ... `graft build --no-reuse` forces a cold
re-parse." "Git determines the visible file set."

Where the graph lives: "`graft build` adds `graft/` to your `.gitignore`
automatically — the graph is a local, regenerable cache"; global option
"`graft --dir <path>`   # use a context dir other than <repo>/graft";
`build --no-gitignore` "skip writing graft/ into .gitignore (same as
GRAFT_NO_GITIGNORE=1)" and `--no-ignore` "skip writing .ignore for ripgrep
re-admit". Refresh: "every query refresh[es] the graph before it answers.
A retrieval call stats the tree against the last build's fingerprint
(~3ms), and rebuilds only if something moved"; `GRAFT_NO_REFRESH=1` turns
it off.

MCP: "`graft init` also registers Graft's MCP server ... so these six tools
appear natively"; "run it manually with `graft mcp [dir]`"; the hand
registration is `{ "command": "npx", "args": ["-y", "@nanonets/graft",
"mcp"] }`. The six tools and what the README says they take:
`graft_find_code` "a question — Ranked nodes with file:line, source
inlined — usually the full answer, no follow-up read needed"
(`limit`, default 5; `full` "inline whole definition spans instead of the
default ≤8-line crux excerpts"); `graft_file_api` "a file path — Every
signature in that file, no bodies"; `graft_trace_calls` "a symbol — Who
depends on it, or what it depends on with `direction: out`" (its schema:
"bare name, qualified (Class.method), or package-qualified (pkg.Fn); a file
path also works"); `graft_find_all` "a regex — Every hit, grouped by
enclosing symbol"; `graft_repo_map`; `graft_check_freshness`.

Telemetry: "the only network calls are the LLM requests you configured, a
daily npm version check, and one batched usage ping ... Turn it off with
`graft telemetry disable`, `DO_NOT_TRACK=1`, or by unchecking the box in
`graft init`; it is off in CI"; TELEMETRY.md: "`CI`, `GITHUB_ACTIONS`,
`GITLAB_CI` and friends switch it off", and the npm postinstall "records
the anonymous `install` event" unless `CI` is set.

## What we configured and why

- **Install**: `npm ci` from a lockfile that pins `@nanonets/graft` 0.16.0
  by integrity hash (the sha512 that is also the row's recorded pin
  digest) and its 45 dependencies the same way (generated with
  `npm install --package-lock-only` on 2026-09-05 from the README's
  package name at that version), in a Node 20 image pinned by digest;
  `bin/graft` linked onto PATH, which is what `npm install -g` does. The
  tree-sitter grammars are native addons, built at image build; nothing
  is fetched at run. `CI=1 DO_NOT_TRACK=1` during the install so the
  package's postinstall records nothing.
- **Context dir**: the corpus is mounted read-only and the default
  context dir is `<repo>/graft`, so the documented global `--dir` points
  it at `/private/graft` (the uid-owned tmpfs), and `build` runs with its
  documented `--no-gitignore --no-ignore`, the two files it would
  otherwise write into the repo. The graph (a directory of markdown
  cards, `.graph/wiring.json`, a cache) is the tool's whole design; its
  size is not charged to anything.
- **Index**: `graft --dir /private/graft build /corpus --no-gitignore
  --no-ignore`, the documented deterministic build, no key, no `--deep`;
  its wall time is the index time. Cold: each container is new, so no
  extraction cache exists before the build. `files_indexed` is read from
  the build's own summary line where the format allows it.
- **Server**: `graft --dir /private/graft mcp /corpus`, the documented
  manual launch with the same context dir. The per-query refresh stays on
  (the default; nothing moves under a measurement, so it is the ~3 ms stat
  the README describes and it is charged to the call it runs under, as an
  agent pays it).
- **Environment**: `DO_NOT_TRACK=1`, `CI=1` (the documented off-switches;
  the run is `--network none` anyway, so the daily npm version check and
  the usage ping could only fail), `HOME=/private`; the driver's
  `MCP_DRIVER_TIMEOUT_S=300`. No provider variable: the deterministic path
  reads none.
- **Surface**: `tools/list` is the six tools `graft mcp` serves; there is
  no other documented surface.
- **Payload**: each tool's DEFAULT output (the text the server returns).
  Citations are parsed from that text (`file:line` and `file:Lx-Ly` forms
  the tool prints); no twin call is run.
- **Commands per task category** (DESIGN §4.1):
  - P1 definition lookup: `graft_find_code(query=<name>)` at its default
    `limit` (5) and default excerpts, the documented "usually the full
    answer" tool, one call, charged. Its ranked nodes carry `file:line`;
    the citations are the nodes it returns.
  - T token task: `graft_find_code(query=<the query words>)`, charged.
    (`graft_find_all` is the exhaustive regex tool; a keyword question is
    the ranked tool's job by the README's own split: "built for 'every
    occurrence of this pattern' tasks where `graft ask`'s ranked top-N
    isn't enough".)
  - P2 reference finding: `graft_trace_calls(symbol=<name>)` (direction
    `in`, depth 1, the defaults), charged.
  - P4 file dependencies: `graft_trace_calls(symbol=<path>)`, the schema's
    "a file path also works", direction `in`, charged; the files of the
    dependents it lists are the citations. When that answer reads "no
    indexed callers ... find its uses with graft grep", the adapter does
    what the tool's own answer says: `graft_find_all(pattern=<module
    name>, fixed=true)` in a second container (build again, uncharged),
    charged, one citation per file it lists. The follow-up exists only
    when the tool asks for it; an answer with dependents gets none. A
    follow-up session that times out or errors is recorded as the task's
    error, not as the first answer alone.
- Every call is charged; nothing is called uncharged.

## Where the harness may disadvantage it

1. **The deterministic path only.** The README's headline claims (SWE-bench,
   "4× cheaper") are made for the product with the LLM layer (`--deep`
   concept nodes, summaries, cruxes) and the Claude Code hooks that pull
   nodes into each prompt; none of that runs here (no key, no model, no
   agent loop). The rows measure the wiring graph and the six tools as an
   agent calls them, which is the surface every other MCP row measures.
2. **`find_code` at its default `limit` of 5** cites five nodes for a
   one-definition P1 task; precision is capped at 0.2 where a tool that
   returns one exact match scores 1.0. The default is the tool's; passing
   `limit=1` would be a harness choice that no agent's first call makes.
3. **Citations are parsed from prose-shaped output**; a location the
   parser does not recognise is a missed citation, not the tool's miss.
   The probe section records the shapes the parser reads.
4. **P4 through `trace_calls` on a file path** is one reading of "which
   files depend on this file"; the tool's own framing is symbol edges,
   and an agent might ask `graft_file_api` or `find_all` instead.
5. **The per-query refresh** is charged to the call it runs under (the
   README says ~3 ms); an agent pays the same.
6. **Python only so far**: its other 21 languages and its `--lsp` edges
   are not exercised.

## Where the harness may advantage it

- The context dir on tmpfs: its graph reads are memory reads. Its source
  reads at build cross the bind mount like everyone's (the corpus is the
  same mount), so unlike the previous row the difference is confined to
  the graph, not the source.

## What we could not make work

Nothing of the tool's is broken. Probe figures (2026-09-05, the PINNED
self corpus: this tree's `src/` at 9d4ed5fe copied and git-inited the way
`run.py` does it, 277 files; one container each, not results):

- `graft build` into the tmpfs context dir: exit 0 in 17.52 s wall, its
  own summary "wiring: 3289 nodes (2333 function, 558 method, 277 file,
  121 class), 9407 edges, 277 cards [python]" and "parsed: 277 of 277
  files (0 replayed from cache)"; `graft check` afterwards: "the wiring
  graph is in sync with the code" (and "meaning tier 0% complete", the
  `--deep` layer this row does not build).
- `initialize` 217 ms; `serverInfo` reads `graft 0.16.0`, the tool's own
  version. `tools/list`: six tools, 761 tokens.
- Every tool answer opens with a "[graft] tokens saved ≈ N ... tell the
  user the total graft tokens saved this turn" line addressed to the
  agent; it is part of what the agent receives and is charged like the
  rest. Its estimate is the tool's own and no row reads it.
- Per-call latency in the probe: `find_code` 2.6 s then 0.9 s,
  `trace_calls` 1.8 s and 1.1 s, `find_all` 1.5 s, `file_api` 0.9 s; the
  first call of the session (`check_freshness`, not in the plan) 7.9 s.
  Each call's per-query refresh runs over the read-only corpus mount
  (CF-14), and the Linux runner's rows will say how much is the mount.
- `find_code("cache_put")` returns five lexical hits (`_cache_put`,
  `result_cache_put`, `_result_cache_put`, `cached_parse_file`,
  `invalidate_cache`); the method `_State.cache_put` itself is not among
  them, while `trace_calls("cache_put")` resolves it exactly
  (`token_tracker.py:L369-L376`) and names its one caller. The P1 row
  cites the five as returned.
- `trace_calls` on the P4 file path answers "no indexed callers — the
  graph has no incoming call/reference edges for this symbol as written
  ... find its uses with graft grep"; `direction: out` on another file
  lists its imports each as "(unresolved import)", including the relative
  ones (`..storage`, `._utils`). The graph's file-level import edges are
  unresolved on this corpus, which is why P4 goes through the tool's own
  fallback; recorded in FINDINGS as the tool's behaviour, not as a zero.
- `find_all("token_tracker", fixed)` over that fallback: "29 hits in 20
  symbols across 13 files (searched 277 indexed files)", one header per
  enclosing symbol or module-level hit; the parser reads both shapes.
