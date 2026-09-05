# Fairness note: Serena 1.7.0

Adapter `benchmarks/competitive/adapters/serena.py`; image
`benchmarks/competitive/sandbox/serena.Dockerfile` with the hashed lockfile
`sandbox/serena.requirements.txt` and the pinned global configuration
`sandbox/serena_config.yml`; MCP client `sandbox/mcp_driver.py` (the same
client that drove the two previous MCP rows). Written 2026-09-05 before
the first number was recorded (DESIGN §1.3). Everything quoted from the
tool's README or user guide is data (principle 5), read from
`oraios/serena` at tag v1.7.0 (commit `949a27ef1e5f`, PyPI release
2026-08-09) through the GitHub API: README.md, `docs/02-usage/*.md`,
`docs/01-about/020_programming-languages.md`, `docs/02-usage/070_security.md`,
`src/serena/resources/serena_config.template.yml`, the context and mode
YAMLs, `src/serena/tools/symbol_tools.py`, `file_tools.py`,
`src/solidlsp/dependency_provider.py` and `language_servers/pyright_server.py`.
No page was browsed during a run.

## What the docs recommend

Install: "Serena is managed by *uv*, and installing uv is the only
required prerequisite. ... `uv tool install -p 3.13 serena-agent`", then
"`serena init`". Clients run "`serena start-mcp-server --context
<client> --project <path>`" over stdio ("Communication over stdio is the
default"). The CLI's `--context` default is `desktop-app` ("where
Serena's full toolset is provided", `excluded_tools: []`); base modes are
`interactive` and `editing` (`serena_config.template.yml`). "By default
... Serena will, by default, start a web-based dashboard on localhost";
`--enable-web-dashboard <true|false>` and `--enable-gui-log-window` are
documented flags, and the template carries `web_dashboard`,
`gui_log_window`, `tool_timeout: 240`, `default_max_tool_answer_chars:
150000`, `project_serena_folder_location: "$projectDir/.serena"` ("You
can customize this location globally") and `trusted_project_path_patterns:
[]` ("newly created configurations trust no project by default").

Python: "by default, uses Pyright (language `python`) ... Pyright,
BasedPyright, ty, and pyrefly require `uv`/`uvx` in PATH". The dependency
provider runs `uvx -p 3.13 --from pyright==1.1.403 pyright-langserver
--stdio` (`PYRIGHT_VERSION = "1.1.403"`), or, quoting its docstring, "the
LS-specific setting `ls_path` can be set to the path of an
already-installed language server executable, in which case it is
launched directly, bypassing uv entirely". Indexing: "Especially for
larger project, it can be advisable to index the project after creation
... `serena project index` ... Indexing has to be called only once."

Tools (symbol_tools.py, file_tools.py docstrings): `find_symbol`
("Retrieves information on all symbols/code entities ... based on the
given `name_path_pattern`", `include_body: whether to include the
symbol's source code. Use judiciously`); `find_referencing_symbols`
("Finds references to the symbol at the given `name_path` ... as well as
a short code snippet around the reference"; requires `relative_path`, "the
relative path to the file containing the symbol"); `search_for_pattern`
("regular expression to search for", the codex context's "for reads where
you don't know the symbol's name path, you can use the pattern search
tool"); `get_symbols_overview`. The README's retrieval table lists no
importers or file-dependency capability. The README carries no token or
latency figure; its evaluation pages compare agent runs, not tool costs.

## What we configured and why

- **Install**: the PyPI wheel `serena_agent-1.7.0-py3-none-any.whl`
  (sha256 `6dbf1459…`, uploaded 2026-08-09) and `pyright==1.1.403` (the
  version the tool pins) with every dependency pinned by version and hash
  in `sandbox/serena.requirements.txt`, compiled once with `uv pip compile
  --generate-hashes` for linux / Python 3.13 and installed with `pip
  install --require-hashes`. The README's `uv tool install` builds the
  same environment without a hash lock; the wheel and the pin are the
  same. Python only: the corpora so far are Python, and the DESIGN §1.3
  row's TypeScript and Go servers arrive with the corpora that need them.
- **Language server**: `ls_specific_settings: python: ls_path:
  /usr/local/bin/pyright-langserver`, the documented bypass of `uvx`, so
  no network step happens at run. `pyright` (the PyPI package) downloads
  Node and the pyright npm package at first use; the image runs it once at
  build with `PYRIGHT_PYTHON_GLOBAL_NODE=0`,
  `PYRIGHT_PYTHON_NODE_VERSION=22.18.0` (the version the tool's own
  Dockerfile pins), `PYRIGHT_PYTHON_ENV_DIR=/opt/nodeenv` and
  `PYRIGHT_PYTHON_CACHE_DIR=/opt/pyright-cache`, and the run reads those
  directories.
- **Global configuration** (`sandbox/serena_config.yml`, copied to
  `$SERENA_HOME` at container start because the tool writes logs and
  language-server files under it): the template's values except
  `web_dashboard: False`, `gui_log_window: False`, the `ls_path` above,
  and `project_serena_folder_location: "/out/serena-projects/$projectFolderName/.serena"`
  because the corpus mount is read-only and the default is inside the
  project. Trust patterns stay empty: nothing trust-gated is used.
- **Mode**: `serena start-mcp-server --project /corpus` (the dashboard and
  GUI log window are off through the configuration file above, not flags),
  the default context (`desktop-app`, the full toolset) and the default
  base modes,
  because that is the surface a user gets before choosing a client
  context. `tools/list` is therefore the full default surface; a
  client-specific context that excludes tools would weigh less, and a
  row per context is a separate configuration. No LLM runs, so the
  onboarding prompt is never triggered and no memory is written.
- **Index**: `--project` activation starts the language server at server
  start, and pyright analyses the workspace on its first request. The
  adapter records the driver's `initialize` round trip plus the wall time
  of one uncharged `get_current_config` call (the first request after
  the handshake) as the index time: that is what an agent waits for
  before its first answer. The second session (the follow-up calls) starts
  the server and the language server again; its own uncharged
  `get_current_config` takes the server restart, so that is charged to no
  task and to no index row (in the result file the first body read of the
  second session is about 120 ms, as the later ones are; in the run before
  the warm call it was about 6.5 s). What the uncharged call does NOT
  absorb is disadvantage 7 below. The documented `serena project index` is a separate CLI step an
  agent does not run; it is not measured. There is no index artefact to
  count files from; `files_indexed` is not reported.
- **Payload**: each tool's DEFAULT output, the JSON text the server
  returns, at `max_answer_chars` default. Citations are read from the
  same JSON (`relative_path`, `body_location.start_line`, LSP 0-based
  lines converted to 1-based); no twin call is run.
- **Commands per task category** (DESIGN §4.1):
  - P1 definition lookup: `find_symbol(name_path_pattern=<q>)` (no body),
    then, in a second session over the same project, `find_symbol` with
    `include_body=true` and `relative_path` for each of the first three
    matches, mirroring our own search-then-read-3 (R27). Both charged.
  - T token task: `search_for_pattern(substring_pattern=<words joined by
    |>, restrict_search_to_code_files=true)`, the documented tool for a
    query that is not a name path; a multi-word T query is not a symbol
    name and `find_symbol` would return nothing.
  - P2 reference finding: `find_symbol(name_path_pattern=<q>)` to learn
    the symbol's `relative_path` (the tool requires it), then
    `find_referencing_symbols(name_path, relative_path)` for each match
    whose name is exactly the query, second session, all charged.
    Citations: each referencing symbol's `relative_path` and
    `body_location.start_line`.
  - P4 file dependencies: NOT ANSWERED. The README's retrieval table has
    no importers or file-dependency row and the tools take symbol name
    paths; the row is NOT COMPARABLE rather than a zero. If a later probe
    finds a documented route, it becomes its own configuration. This
    deviates from DESIGN s1.3, whose row lists the tool under P1, P2, P4
    (written from the field survey before the docs were read); the
    adapter's categories are P1, P2, T, and the row's T is the pattern
    search below.
- **Environment**: `HOME=/out`, `SERENA_HOME=/out/serena-home`,
  `PYRIGHT_PYTHON_GLOBAL_NODE=0`, `PYRIGHT_PYTHON_NODE_VERSION`,
  `PYRIGHT_PYTHON_ENV_DIR`, `PYRIGHT_PYTHON_CACHE_DIR`; and the driver's
  own `MCP_DRIVER_TIMEOUT_S=300` (a per-call ceiling above the tool's
  `tool_timeout: 240`, so a slow answer is the tool's, not the harness's);
  nothing else.

## Where the harness may disadvantage it

1. **The first call pays the language server start** and the workspace
   analysis; that wall time is recorded as the index time and excluded
   from per-call latency, but pyright's background analysis may still be
   running when the first charged call arrives, which is what an agent
   also sees.
2. **`find_symbol` returns every match, unranked**; taking the first three
   for the source reads is a harness choice, not the tool's ranking. Its
   default `max_answer_chars` may shorten a large result, which the tool
   does by design and which is charged as returned.
3. **T through a regex alternation** of the query words is one reading of
   "keyword search"; an agent might write a tighter pattern. Charged as
   returned, including the context lines the tool adds.
4. **P4 not answered** rather than answered through an improvised route
   (`find_referencing_symbols` on a module name, say); the tool's
   documentation does not offer one.
5. **Python only, pyright only**: the tool's other Python backends
   (basedpyright, ty, pyrefly, jedi) and its other languages are not
   measured.
6. **The Windows bind mount** (CF-14) applies to its language server's
   file reads as to everyone's.
7. **The first symbolic call of a session costs more than the later ones,
   and it is charged.** In the result file the first `find_symbol` of
   session 1 is about 10 s in every run against about 7 s for the ones
   after it, with the uncharged `get_current_config` already answered:
   that call is not symbolic and does not trigger whatever the language
   server does on its first symbolic request. An agent pays the same first
   call; a warm call shaped like a task would be a charged-shape call made
   free, so it is recorded here instead of removed.

## What we could not make work

Nothing of the tool's is broken; two harness defects were found and fixed
by the probes (2026-09-05, self corpus, one run each, not results):

- **The language server died at start under `--network none`** in the
  first probe: with `PYRIGHT_PYTHON_NODE_VERSION` set, pyright's Python
  wrapper re-runs its Node installer on every start (`if path.exists()
  and not NODE_VERSION`), a download. The image sets the variable for the
  build's warm-up and unsets it for the run; pyright then starts in 0.6 s
  and reports "Found 277 source files" (its log).
- **Every call after the first hung** in the second probe: the tool logs
  each tool result to stderr at INFO, the driver read stderr only at exit,
  and the pipe filled. `mcp_driver.py` drains stderr continuously now
  (a harness fix that can only unblock a call, never change a number;
  the two earlier MCP rows are re-measured with the new driver in this
  PR's recorded run, so their pinned image digests move with it).

What the third probe found, recorded as the tool's behaviour (probe
figures, one run; the recorded rows and their per-task figures are the
result file FINDINGS CF-28 and CF-29 name):

- **Per-call latency on this box is seconds, not milliseconds**: the
  `find_symbol` and `search_for_pattern` calls of the first session took
  6 to 12 s each in the probe. The tool syncs file-system changes before a
  symbolic call and polls a change notifier at start; over the Windows
  bind mount (CF-14) that is the cost an agent pays here, and the Linux
  runner's rows will say whether it is the mount. The probe also showed
  the second session's first call carrying the language server's start,
  which is why that session has an uncharged warm call now (above).
- **`search_for_pattern` over the alternation of a T query's words
  returns every match** (1,019 across 277 files for "router|route|handler"
  in the probe), so the token row is dominated by the T tasks. That is the
  tool's answer to that pattern; an agent might narrow with
  `paths_include_glob` or a tighter regex, which the harness does not do
  for anyone. The row is recorded with this caveat.
- **`serverInfo.version` reads `1.28.1`**, the MCP SDK's version, not the
  tool's 1.7.0 (the CF-26 shape); the pin is the wheel hash and the
  adapter's `version()` reports the pin.
- `find_symbol` returned exactly one match for each P1 name and
  `find_referencing_symbols` the one same-file caller with its line
  (`body_location` is LSP 0-based; cited 1-based).
