# Fairness note: CocoIndex Code 0.2.41

Adapter `benchmarks/competitive/adapters/cocoindex.py`; image
`benchmarks/competitive/sandbox/cocoindex.Dockerfile` with the hash
lockfile `sandbox/cocoindex.requirements.txt`; MCP client
`sandbox/mcp_driver.py` (the client that drove the five previous MCP
rows). Written 2026-09-05 before the first number was recorded (DESIGN
§1.3). Everything quoted from the tool is data (principle 5), read from
PyPI `cocoindex-code` 0.2.41 (wheel sha256 `bf71bf24…`, uploaded
2026-08-07, `Requires-Python >=3.11`, the latest release on 2026-09-05)
and the repository `cocoindex-io/cocoindex-code` at tag `v0.2.41`:
`README.md`, `EMBEDDINGS.md`, `skills/ccc/references/settings.md` and
`management.md`, and the shipped source (`cli.py`, `server.py`,
`client.py`, `daemon.py`, `_daemon_paths.py`, `settings.py`,
`protocol.py`). No page was browsed during a run.

**This is the embedding representative of the set** (FIELD.md, set row
8): the one row whose retrieval is a vector search over chunks, with the
model run locally on the CPU. DESIGN §1.3 gives it P1; its docs describe
no references or dependents tool (its `grep` is "Structurally grep code
by example (no index or daemon required)", a pattern tool, not a
references tool), so P2 and P4 are not attempted and their rows are NOT
COMPARABLE. T tasks go through the same search.

## What the docs recommend

Install: "Using pipx: `pipx install 'cocoindex-code[full]'`" or "Using
uv: `uv tool install --upgrade 'cocoindex-code[full]'`". "The `[full]`
variant defaults to `Snowflake/snowflake-arctic-embed-xs`", which "runs
locally via sentence-transformers with no API key required"; EMBEDDINGS.md:
"`Snowflake/arctic-embed-xs` is a good choice in most situations",
"Smallest, most compatible default", and local sentence-transformers
models are "private, offline". The slim install "requires cloud providers
and API keys".

Use: "`ccc init`" to initialize a project, "`ccc index`" to build or
update the index, "`ccc search <query>`" for semantic search; "`ccc mcp`"
"Run as MCP server (stdio mode)" exposing one tool, `search`: "Semantic
code search across the entire codebase -- finds code by meaning, not just
text matching", with `query`, `limit` (default 5, 1..100), `offset`,
`refresh_index` (default True: "incrementally update the index before
searching"), `languages`, `paths`. Results are `file_path`, `language`,
`content`, `start_line`, `end_line`, `score` (`protocol.py`).

Where things live: "Index databases (`cocoindex.db` and
`target_sqlite.db`) live alongside settings in
`<project>/.cocoindex_code/`"; user-level settings in
`~/.cocoindex_code/global_settings.yml` (`COCOINDEX_CODE_DIR` overrides
the directory) with `embedding.provider` ("sentence-transformers" for
local models) and `embedding.model`; project settings in
`<project>/.cocoindex_code/settings.yml` (include and exclude patterns;
the defaults "exclude hidden dirs, node_modules, dist, __pycache__").
`init` writes the project settings and the line `/.cocoindex_code/` into
`.gitignore` "if a `.git` directory exists"; it prompts for the embedding
choice only when no global settings exist, and exits with code 1 when
stdin is not a TTY and no `--litellm-model` was given.

The daemon: "The daemon starts automatically on first use." Every
indexing and search command, and the MCP server, talks to it over a Unix
domain socket in the runtime directory (`COCOINDEX_CODE_RUNTIME_DIR`,
defaulting to the settings directory); the client spawns it with
`start_new_session=True`, logs to `daemon.log`, and it exits after
`daemon.idle_timeout_minutes` (default 180) with no client;
`daemon.keep_alive_with_mcp` (default true) keeps "the daemon and
embedding model warm" while an MCP session lives.

Telemetry: the tool "sends anonymous usage telemetry" and does "not
collect your source code, file paths, queries, search results,
embeddings, settings, or any other content"; off with
`export COCOINDEX_DISABLE_USAGE_TRACKING=1`. Claims: "Instant token
saving by 70%."

## What we configured and why

- **Install**: `pip install --require-hashes` from a lockfile that pins
  `cocoindex-code[full]==0.2.41` and its dependencies by version and
  sha256 (compiled with `uv pip compile --generate-hashes` for linux /
  Python 3.12 on 2026-09-05), in a `python:3.12-slim-bookworm` image
  pinned by digest. The `[full]` extra is the documented local-model
  install. The one deployment choice: `torch` resolves from PyTorch's CPU
  wheel index (`+cpu` build) instead of PyPI's CUDA build, because the
  sandbox has no GPU and the CUDA wheel is several gigabytes of libraries
  the run cannot use; the model, the embeddings and the arithmetic are
  the same, on the CPU either way (`embedding.device` unset, so the tool
  detects CPU itself).
- **Model at build**: `Snowflake/snowflake-arctic-embed-xs`, the `[full]`
  default, downloaded once at image build into `HF_HOME=/opt/hf`; the run
  sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` under `--network
  none`, so a missing weight would fail loudly at build, never read as
  the tool's failure at run.
- **Settings**: the run's script writes the documented user-level file
  `$HOME/.cocoindex_code/global_settings.yml` with the two documented keys
  (`embedding.provider: sentence-transformers`, `embedding.model:
  Snowflake/snowflake-arctic-embed-xs`) before anything else, which is
  what `ccc init` would have written after its interactive prompt; the
  prompt cannot run without a TTY, and `--litellm-model` would select
  the remote provider instead. Nothing else in the file; daemon settings
  at their defaults.
- **Corpus copy**: the index, the settings and the `.gitignore` line all
  live inside the project, and the corpus mount is read-only, so the
  corpus is copied to `/private/project` (the uid-owned tmpfs) once per
  container and the tool runs there; `HOME=/private`, so the settings
  directory, the daemon's socket, pid and log are on the same tmpfs. The
  copy is a harness cost, not charged.
- **Index**: `ccc init` then `ccc index` in the project copy, timed
  together; that wall is the index time. It includes the daemon's first
  start and the model load, because the tool's own design is that the
  first command pays them ("starts automatically on first use"), and
  each container is new. `files_indexed` is read from `ccc status`'s own
  output where it gives a file count.
- **Server**: `ccc mcp` in the project copy, the documented launch; the
  driver speaks MCP over stdio to it. `tools/list` is its one tool.
- **Commands per task category** (DESIGN §4.1):
  - P1 definition lookup: `search(query=<name>)` at the defaults (`limit`
    5, `refresh_index` true), charged; each result's `file_path` and
    `start_line` is a citation.
  - T token task: `search(query=<the query words>)` at the defaults,
    charged.
  - P2 and P4: not attempted; no documented tool answers them (see above).
  - Every call is charged; nothing is called uncharged.
- **The daemon and D2**: the daemon is a background process, and the
  brief excludes a tool that opens "a background process the run does
  not own". This one is owned by the run: it is spawned inside the
  container by the tool's own client, listens on a Unix socket on the
  container's tmpfs (no port), and dies with the container. It is the
  tool's documented architecture and every row here pays its start
  inside the index time.
- **Environment**: `COCOINDEX_DISABLE_USAGE_TRACKING=1` (the documented
  off-switch; the run is `--network none` anyway), `HF_HOME`,
  `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HOME=/private`, the
  driver's `MCP_DRIVER_TIMEOUT_S=300`.
- **Payload**: the tool's `search` result as the server returns it (the
  structured result serialised by its MCP framework); citations are
  parsed from the `file_path`/`start_line` fields of that text.

## Where the harness may disadvantage it

1. **CPU embedding on a laptop-class runner.** Its index time is the
   embedding of every chunk on the CPU; a machine with a GPU or its
   recommended larger models would change the number in both directions
   (speed and quality). The `[full]` default model is the smallest tier
   in its own EMBEDDINGS.md ranking.
2. **`limit` 5 for a one-definition P1 task**: precision is capped at
   0.2 where a tool that returns one exact match scores 1.0; the default
   is the tool's, and `limit=1` would be a harness choice no agent's
   first call makes.
3. **`refresh_index` true on every call** re-runs the incremental index
   check before each answer; that is the tool's default and is charged
   to the call it runs under, as an agent pays it.
4. **A semantic search is asked an identifier.** P1 queries are symbol
   names; an embedding model ranks by meaning, and an exact-name lookup is
   the lexical tools' home ground. The T tasks are the fairer test of
   what it does.
5. **P2 and P4 are absent by its docs**, so the row has fewer axes; that
   is a scope statement, not a zero.
6. **Python only so far**; its other languages are not exercised.

## Where the harness may advantage it

- The project copy on tmpfs: its file reads and its databases are memory
  reads, where the bind-mount rows read the mount (CF-14). Confined to
  this row's index and calls alike.

## What we could not make work

Nothing of the tool's is broken. Probe figures (2026-09-05, the PINNED
self corpus: this tree's `src/` copied without bytecode and git-inited
the way `run.py` does it, 277 tracked files; one container, not
results; the capture is `tests/fixtures/competitive/cocoindex_mcp.json`):

- Image build: pip 172 s, the model download 36 s (`cocoindex_build.log`
  in the session scratchpad, not in the tree); `ccc version` inside the
  image: 0.2.41.
- The settings file written by the script satisfied `ccc init` without a
  prompt: "Created project settings:
  /private/project/.cocoindex_code/settings.yml". `ccc index`: "Indexing:
  277 files listed | 277 added, 0 deleted, 0 reprocessed, 0 unchanged,
  error: 0", "Chunks: 7205", "Files: 274", "python: 7205 chunks" (three of
  the 277 listed files yield no chunk; the tool's own count is 274 and
  that is what the index report carries). `init` + `index` together:
  186.04 s wall, the daemon's start and the model load inside it
  (`daemon.log`: "Daemon starting (PID 13, version 0.2.41)", "Listening on
  /private/.cocoindex_code/daemon.sock", "No device provided, using cpu",
  "Loading SentenceTransformer model from
  Snowflake/snowflake-arctic-embed-xs"). The whole container: 194.3 s.
- On disk after the index: `target_sqlite.db` 20,500,480 bytes and a
  `cocoindex.db` directory in the project copy; `daemon.sock`,
  `daemon.pid`, `daemon.log`, `global_settings.yml` under the tmpfs HOME.
  Nothing on the read-only mount.
- `initialize` 779 ms; `serverInfo` `cocoindex-code 0.2.41`; `tools/list`
  one tool, 397 tokens.
- Per-call latency at the defaults (`refresh_index` true): 849 ms for the
  first search, then 329 to 441 ms; the same query with
  `refresh_index=false` (one extra call for the note): 15 ms. The default
  refresh is most of every call, and it is the default an agent gets.
- Result size: 4,133 to 5,701 characters of JSON per call (five results
  with `content`, `start_line`, `end_line`, `score`); `file_path` is
  relative to the project. The corpus files carry CRLF line endings (the
  checkout's), which the chunks reproduce; every row sees the same bytes.
- `search("cache_put")` returns five chunks of tool-name lists in
  `config.py`, `server.py` and a hook file, not the method
  `_State.cache_put`; the same for `validate_path` and `ProgressReporter`
  (lists that mention the name, ranked above the definition). An
  embedding model asked an identifier ranks by meaning (disadvantage 4);
  recorded as the tool's answer, cited as returned.
- Its stderr through the driver: empty.
