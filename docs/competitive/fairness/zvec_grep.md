# Fairness note: zvec-grep 0.2.2

Adapter `benchmarks/competitive/adapters/zvec_grep.py`; image
`benchmarks/competitive/sandbox/zvec_grep.Dockerfile` with the lockfile
`sandbox/zvec_grep.package-lock.json`. Written 2026-09-07 before the first
number was recorded (DESIGN §1.3). Everything quoted from the tool is data
(principle 5), read from npm `@zvec/zvec-grep` 0.2.2 (integrity
`sha512-6xsF21…`, published 2026-09-07T07:53Z, `engines.node >=22`, the
latest on that day) and the repository `zvec-ai/zvec-grep` at `main` on
2026-09-07: `README.md`, `docs/02-cli.md`, `docs/03-mcp.md`,
`docs/04-pipeline.md`, `docs/06-server.md`, `docs/07-embedding.md`, and the
shipped source (`src/cli/format/context.ts`, `src/cli/format/status.ts`,
`src/mcp/result-format.ts`, `src/engine/models/catalog.ts`). No page was
browsed during a run.

**This is set row 9** (FIELD.md §5.2; admitted 2026-09-07 on the §5.4
trigger, CF-66): ripgrep, BM25 and a local vector index over one workspace,
for "humans and AI agents", with a CLI and an MCP server. The CLI is the
surface measured (`--mode direct`, in-process); the MCP surface's schema
weight is captured uncharged because its server is HTTP behind a daemon.

## What the docs recommend

Install: "`npm install -g @zvec/zvec-grep`" (README), "Requires Node.js 22
or newer". Index: "`zg index --embedding local/potion-retrieval-32m`" in the
README's quickstart over two text files; `docs/07-embedding.md` lists
eleven local models and describes `local/potion-code-16m-v2` as "A fast
first index for a code repository", a "Small static Model2Vec model with a
1,024-token input limit", 256 dimensions; the tool's own catalog pins it to
Hugging Face `minishlab/potion-code-16M-v2` at revision
`e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b`. Model files "are downloaded on
first use and cached under `~/.zvec-grep/models` by default";
"`--model-cache` or `ZVEC_GREP_MODEL_CACHE`" moves the cache. No default
model: `--embedding`, `ZVEC_GREP_EMBEDDING` or a config default is required.

Query: "`zg query <query> [options]`" with `--hybrid`, `--fts`, `--vector`,
`--fuse`, `--rg [rg-options] <pattern> [path...]`, `--limit <n>`, `--human`,
`--preview none|short|full`, `--prefer-symbol`, `--symbol-type <type>`
(`docs/02-cli.md`). The query groups (`docs/04-pipeline.md`): hybrid
"Intent + lexical anchors, ranked sample"; FTS "Exact terms ranked"; vector
"Conceptual similarity"; ripgrep "Exhaustive literal/regex matching".
Results are "compact, grouped by query group, and ordered by that group's
retrieval rank"; "Indexed source previews are omitted unless requested".

Agent guidance (`docs/03-mcp.md`): "Agents use native grep or rg when
locating an exact word, quotation, name, date, key, filename, path, source
fragment, or regex is sufficient. For mixed tasks, start with
`zvec_grep_search`, then use native grep or rg".

Execution (`docs/06-server.md`): `direct` mode runs "entirely in the
current process"; `auto` (the default) uses a server only if one is already
running; the daemon starts only by `zg server on` and stops by `zg server
off`; `zg server run` runs it in the foreground. The MCP endpoint is
`http://127.0.0.1:7999/mcp` (`docs/03-mcp.md`); the default toolset is one
tool, `zvec_grep_search`; `--mcp-toolset full` gives six.

Where things live: the workspace index under `<root>/.zvec-grep/`
(`manifest.json`, `files.zvec`, `index.zvec`); the tool's home under
`ZVEC_GREP_HOME`. Freshness is reported as `fresh` or `possibly_stale`.

## What we configured and why

- **Install**: `npm ci` from a lockfile generated with `npm install
  --package-lock-only` on 2026-09-07 (248 packages, 76 non-optional; the
  172 optional ones are platform builds of `node-llama-cpp` and
  `onnxruntime` that npm resolves per platform), pinning
  `@zvec/zvec-grep@0.2.2` by the integrity hash npm enforces. Base image
  `node:22-bookworm-slim` by digest. `@vscode/ripgrep` and
  `onnxruntime-node` fetch prebuilt binaries in their postinstall, at build
  only.
- **Model at build**: `local/potion-code-16m-v2`, the catalog's code model
  and the smallest, warmed by indexing a one-file workspace at build with
  `ZVEC_GREP_MODEL_CACHE=/opt/zg-models`; the run points at that cache under
  `--network none`, so a missing file fails at build, never as the tool's
  failure at run. ⚠ **This is not the model the tool's published SWE-QA
  run used** (`qwen/qwen3.7-text-embedding`, remote; CF-66). Our loop
  calls no model (DESIGN D1) and the README recommends the local models
  for code; the number recorded here is for the local model and says so.
- **Mode**: `ZVEC_GREP_MODE=direct` for every command: no daemon, no port
  during any charged call. The brief excludes a tool that opens "a
  listening port or a background process the run does not own"; direct
  mode is the tool's own documented way to run without either.
- **Corpus copy**: the index lives inside the workspace and the mount is
  read-only, so the corpus is copied to `/private/project` (the uid-owned
  tmpfs) once per container and the tool runs there (CocoIndex's shape).
  The copy is a harness cost, not charged. `ZVEC_GREP_HOME=/private/zg-home`.
- **Index**: `zg index . --embedding local/potion-code-16m-v2` in the copy,
  timed; that wall is the index time (cold: a new container, the model
  read from disk). `files_indexed` is the `N scanned` figure of the tool's
  own `printIndexResult` line (`N scanned, M added, ... `); `zg status`
  is saved beside it.
- **Commands per task category** (DESIGN §4.1), each a subprocess, each
  charged, the limit and preview at the tool's defaults:
  - P1 definition lookup: `zg query --prefer-symbol <name>`: the indexed
    hybrid route with the CLI's documented symbol preference. Citations
    are the `<path>:<start>-<end>` of each result header, at `start`.
  - T token task: `zg query <the query words>`, the same route without the
    preference.
  - P2 reference finding: `zg query --rg -w <name>`: its exhaustive route,
    which its own agent guidance names for "an exact word ... name".
    Scored at file level (line 0), one citation per file in the output.
  - P4 file dependencies: `zg query --rg -F <module stem>`: the same
    route, the module's basename as a fixed string (the Graft follow-up's
    shape). File level.
  - Nothing is called uncharged except the tools/list capture below.
- **The MCP schema weight, uncharged**: after every answer, the script
  starts `zg server run --listen 127.0.0.1:7999 --mcp-toolset agent` on
  the container's loopback, POSTs `initialize` and `tools/list` to the HTTP
  endpoint (Streamable HTTP; JSON or one SSE event per reply), records the
  tools as name/description/inputSchema (the driver's shape), and stops
  the server. It runs after the last charged call, inside the container,
  and dies with it. If it fails, `tools_list_tokens` is None and no row
  moves; the capture log says why.
- **Environment**: `ZVEC_GREP_MODE`, `ZVEC_GREP_HOME`,
  `ZVEC_GREP_MODEL_CACHE`, `ZVEC_GREP_EMBEDDING` (all documented
  variables), `HOME=/private`.
- **Payload**: the tool's DEFAULT agent-mode text (not `--human`), ANSI
  sequences stripped if any appear; on a non-zero exit with empty stdout,
  stderr is the payload (what the agent sees on a miss).

## Where the harness may disadvantage it

1. **A static embedding model.** `potion-code-16m-v2` is a Model2Vec
   static model, the fastest and smallest in its catalog; its published run
   used a remote transformer model. A larger local model
   (`jina-embeddings-v2-base-code`, `qwen3-embedding-0.6b`) would change
   the vector route's answers and its index time in both directions.
2. **A semantic route is asked an identifier.** P1 queries are symbol
   names; the hybrid route ranks "intent + lexical anchors" and
   `--prefer-symbol` is the documented lever, but the tool's own guidance
   sends an exact name to ripgrep. We measure its index on P1 because the
   index is the product; the T tasks are the fairer test of it.
3. **P2 and P4 measure ripgrep.** Its reference and dependency answers are
   its `--rg` route: exhaustive text matching, no symbol resolution. That
   is the tool's documented answer to those questions and it is charged as
   given; a reader should not read a P2 score here as a symbol-graph
   score.
4. **A subprocess per call.** Each query pays a Node.js process start and
   the index open; the daemon mode would amortise both and is not measured
   (D2's port rule). The latency column carries the start.
5. **Default preview**: the payload carries whatever preview the default
   emits; `--preview none` would shrink the token column and is a harness
   choice no first call makes.
6. **Seven structure-aware languages**; a corpus in another language falls
   to plain-text chunks.

## Where the harness may advantage it

- The project copy on tmpfs: its file reads and its index are memory
  reads, where the bind-mount rows read the mount (CF-14). Confined to
  this row's index and calls alike.
- Ripgrep on P2/P4 has no index to be stale against.

## What we could not make work

One thing, ours: the first tools/list capture passed `--token-file` to a
path that did not exist, and the server reads that file rather than
creating it ("ENOENT: no such file or directory, open
'/private/zg-token'"; the client then got a refused connection). The
script writes a random token to the file first now; the second capture
succeeded. Nothing of the tool's is broken.

Probe figures (2026-09-07, the PINNED self corpus: this tree's `src/` at
6e2c4b5a copied without bytecode and git-inited the way `run.py` does it,
277 tracked files; `run.py --adapters zvec_grep --set none --runs 1`, one
container, a smoke run, never recorded; the container's files are
`tests/fixtures/competitive/zvec_grep/`, the result file is the session's
`zvec_smoke_result.json`):

- Image build (npm ci, the ripgrep and onnxruntime postinstalls, the model
  warm): inside the run's 4 min 41 s wall (20:57:25 to 21:02:06 local),
  the run itself being about 90 s of it.
- `zg index . --embedding local/potion-code-16m-v2` in direct mode:
  "274 scanned, 274 added, 0 modified, 0 retried, 0 unchanged, 0 deleted,
  0 failed", "entities 2724", the tool's own "duration 7s (6537ms)";
  8.091 s by the script's clock (the process start included). `zg status`:
  "Coverage 100% 274 / 274 files", "Embedding local/potion-code-16m-v2,
  256 dimensions · cosine", "Storage .zvec-grep/index.zvec". Three of the
  277 tracked files are outside its scan (its default noise skips;
  CocoIndex's own count is also 274, whether the same three is not
  established).
- Per-call latency (each a Node.js process start plus the query): the
  indexed route 1,063 to 1,205 ms over the eight P1 and T calls; the
  `--rg` route 256 ms (P2) and 529 ms (P4). `latency_call_ms` 1,068.5
  in the result file.
- Result shape, indexed route: a `query groups (1):` header, `Q1
  [primary]: <query>`, `hits: 10`, then per hit `#<rank>
  matchedBy=fts+vector <path>:<start>-<end>` and one anchor source line
  with its number and a tab (the default preview); 10 hits at the default
  limit. `--rg` route: a file path line, then `<line>:` or `<start>-<end>
  [<kind> <name>]` blocks with the matching lines. Payloads 1,370 to
  1,721 characters for the indexed route (P1 `cache_put`: 496 tokens),
  252 (P2) and 3,250 (P4) for ripgrep; `tokens_per_task` 454.1.
- `cache_put` (P1): the definition `token_tracker.py:369-376` is hit #1;
  the other nine are the name's relatives (`result_cache_put`,
  `_cache_put`, `_result_cache_put`) and files that mention caching, so
  at a file-level expected set the P1 F1 is 0.1818 (one right file among
  the ten cited; disadvantage 2). P2 `--rg -w cache_put`: the two sites
  in `token_tracker.py`, F1 1.0. P4 `--rg -F token_tracker`: 15 files,
  F1 0.4286 against the expected importers (the fixed string also matches
  comments and docstrings that name the module).
- tools/list (second capture, uncharged, `--mcp-toolset agent` over the
  container's loopback, bearer token from the file the script wrote):
  one tool, `zvec_grep_search`, 6,156 characters, 1,531 cl100k tokens as
  name/description/inputSchema. For scale, jCodeMunch's Counter front
  door is three tools at 939 tokens and its full surface 22,741
  (`benchmarks/schema_baseline.json`, STANDARD §4); one tool is not the
  same as the smallest schema.
- Every row in the result file reads NOT COMPARABLE because the smoke run
  carried no jCodeMunch row (`--adapters zvec_grep` alone); the scheduled
  job's roster is `all`.
