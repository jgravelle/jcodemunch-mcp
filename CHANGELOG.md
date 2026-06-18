# Changelog

All notable changes to jcodemunch-mcp are documented here.

## [Unreleased]

### Added

- **Index templating-language files over a supported underlying source
  language** (issue #336). Files of the form `name.<underlying-ext>.<engine-ext>`
  — e.g. a Jinja2 template of TypeScript (`foo.ts.j2`), Jinja of Python
  (`service.py.jinja`), or a Twig template of TS (`widget.ts.twig`) — were
  previously dropped at discovery as `wrong_extension`, so every real source
  symbol inside them went unindexed. They are now recognized, the engine
  constructs are masked **offset-preserving**, and the body is re-parsed as its
  underlying language so symbols, signatures, imports, and line/byte positions
  all resolve correctly.
  - **Engines:** Jinja2 (`.j2`/`.jinja`/`.jinja2`) and Twig (`.twig`) — the
    first cut, scoped to the engines whose `name.<lang>.<engine>` double-extension
    convention this feature targets. The masking layer is a pluggable
    `TemplateEngine` registry (`parser/template_shared.py`), so adding a
    single-extension HTML-bodied engine (Handlebars/Liquid/Mustache) later is a
    few lines. EJS (`.ejs`) keeps its existing parser.
  - **Underlying language is inferred from the middle extension**, so *every*
    language jCodeMunch already supports works as the template body with no
    per-language work — the engine extension is stripped and the remaining name
    is resolved recursively (`foo.ts.j2` → `typescript`). The resolution step
    runs *after* the existing compound and last-extension checks in
    `get_language_for_path`, so it can only ever turn an unresolved path into a
    language — never re-resolve a `.d.ts` / `.blade.php` that already matched.
  - **Jinja/Twig `{% macro %}` / `{% block %}` definitions** are additionally
    surfaced as symbols, reusing the dbt directive path
    (`sql_preprocessor.extract_dbt_directives`, generalized to accept a directive
    keyword set) rather than a parallel implementation. The underlying body
    symbols keep their own sub-language tag.
  - **Offset-preserving mask** mirrors `astro_shared.mask_html_comments_keep_offsets`
    but fills each hole with an identifier placeholder (`{{ x }}` →
    same-length `_`), keeping value-position holes (`DEBUG = {{ x }}`) parseable
    in strict grammars — the offset-preserving form of dbt's `__jinja__`. A hole
    at a *name* position, or free template text emitted inside a block body, is
    best-effort (same contract as dbt SQL parsing).
  - **Bare templates** with no underlying-language extension (`report.j2`) are
    skipped — there is nothing to parse as source.
  - Purely additive: previously-skipped files only add symbols, with no
    `INDEX_VERSION` bump (a re-index picks up the now-recognized files).
  - New `tests/test_templates.py` (16 cases, incl. non-collision guards for
    `.d.ts` / `.test.ts` / `.blade.php` / bare `report.j2`) + fixtures under
    `tests/fixtures/templates/`. Implemented via offset-preserving masking +
    recursive `parse_file()` delegation (the Razor/Astro pattern), and the
    `get_language_for_path` resolution branch.

## [1.108.61] - 2026-06-18 - Every config key is now documented (no more "Other")

### Fixed

- **13 config keys shipped without a template section or description**, so
  `config --json` (and any consumer of it, including the jMunch Console's grouped
  config screen) emitted them under the `Other` catch-all group with an empty
  `description`. The keys existed in `DEFAULTS`/`CONFIG_TYPES` but were never
  added to the JSONC config template that `config_report` parses for group +
  description metadata. Added all of them to `generate_template()` under proper
  `=== Section ===` headers with inline comments:
  - `watch_paths`, `watch_extra_ignore`, `watch_follow_symlinks`,
    `watch_idle_timeout`, `watch_log` -> **Watcher**
  - `runtime_ingest_enabled`, `org_ingest_enabled`,
    `runtime_ingest_max_body_bytes` -> **Runtime & Org HTTP Ingest** (new section)
  - `license_key` -> **Licensing** (new section)
  - `cross_repo_default`, `discovery_hint` -> **Identity & Indexing Behavior**
  - `agent_selector` -> **Agent Selector (model routing)** (new section)
  - `enrichment` -> **Enrichment (LSP)** (new section)

  The `Other` group is now empty. Because metadata is parsed from the template
  (not the user's on-disk config), descriptions appear immediately on upgrade --
  no `config --upgrade` required. New regression test
  `TestConfigReportGrouping::test_no_key_falls_into_other_or_missing_description`
  fails loudly if a future key is added to `DEFAULTS` without a template entry.
  No config *value* behavior change. Surfaced via the jMunch Console.

## [1.108.60] - 2026-06-18 - `config --check` distinguishes sandbox-limited storage probes

### Fixed

- **`config --check` no longer reports a sandbox-limited index-storage probe as a
  confirmed failure** (issue #335). The storage check creates the index root, writes
  a `.jcm_probe` file, and unlinks it. Previously *every* exception was treated as a
  confirmed `storage` issue and exited `1` — so inside a restricted/sandboxed agent
  shell that can read the index but cannot write to the index root, a perfectly
  healthy install looked broken (`✗ index storage not writable … Operation not
  permitted`), which can lead an agent to "repair" or report a false configuration
  failure.
  - A `PermissionError` whose `errno` is `EPERM`/`EACCES` is now classified as
    **host-confirmation-needed** rather than a confirmed failure: it prints a
    `⚠ index storage writability needs host confirmation` row and a hint to rerun
    outside a sandbox or restricted shell before repairing or reporting drift.
  - When only host-confirmation items are present (no confirmed issues), the command
    **exits `0`** so an agent client does not mistake a healthy install for a broken
    one. Confirmed non-writable roots (e.g. a read-only filesystem, `EROFS`) still
    print `✗ index storage not writable` and exit `1`.
  - 4 regression tests in `tests/test_config.py`
    (`TestServerConfigCheckStorageProbe`): writable path passes/exits 0; confirmed
    non-writable still fails; mocked `PermissionError(EPERM)` reports host
    confirmation, not failure; and the output instructs rerunning outside the
    sandbox.

## [1.108.59] - 2026-06-17 - Decision-context surfacing in impact analysis

### Added

- **Read-only decision-context surfacing on `get_blast_radius` and
  `get_impact_preview`** (new opt-in `include_decisions`, default `false`). Impact
  analysis tells you *what* breaks; this adds *why the code is the way it is*. New
  `tools/decision_context.py` (`resolve_decision_context`) mines the git history of
  the focal symbol's file and the impacted files for **decision-bearing commits** —
  reverts, performance rewrites, refactors, renames, and root-cause bugfixes —
  reusing the existing commit classifier (`_classify_commit` / `_extract_intent`
  from `get_symbol_provenance`). It returns a deduped, recency-ranked digest plus a
  one-line volatility read (e.g. "8 decision-bearing commits in 180d across 5 files
  (3 revert, 2 perf, 3 bugfix) — high-churn history; review the rationale before
  changing"). When enabled, the response carries an additive `decisions` block.
  - **Source is the durable git commit record**, not ephemeral agent session logs —
    language- and agent-agnostic, and already half-parsed.
  - **Surface-only**: a read of history attached to the response. Nothing is
    persisted, no decision graph is written, and no user file is touched
    (read-only charter). Bounded cost (capped files × capped commits), which is why
    it's opt-in.
  - Degrades honestly: GitHub-indexed repos (no local working tree) and
    non-git source roots return an `{available: false, reason, hint}` block rather
    than a fabricated result.
  - Absent the flag, both tools are byte-identical to prior behavior; the
    `get_blast_radius` result cache key now includes `include_decisions`.

8 tests in `tests/test_v1_108_59.py` (git-seeded repo: category surfacing, volatility,
cache-keying, off-path parity, no-git honest hints). Tool count stays 82.

## [1.108.58] - 2026-06-17 - Framework flow-edge resolver for get_signal_chains

### Added

- **Language-agnostic framework flow-edge resolver** (`tools/flow_edges.py`,
  `resolve_flow_edges`). The AST call graph is structurally blind to the two
  most common ways a web framework wires a request to the code that handles it,
  and `get_signal_chains` now recovers both:
  - **route → handler.** A route registration that binds a URL string to a
    handler *referenced* rather than called/decorated leaves no call edge, so
    the handler never surfaced as an entry point. The resolver detects the
    registration *shape* — Django `path()`/`re_path()`/`url()`, Express/Fastify/
    Koa `router.get(path, handler)`, Flask `add_url_rule(view_func=...)`, Rails
    `get "x", to: "ctrl#action"` — resolves the handler reference to a symbol
    through the index's own import graph, and `get_signal_chains` promotes it to
    an `http` gateway. Repos that dispatch by string (e.g. a Django `urls.py`)
    now produce signal chains where they previously returned none. Decorator-
    bound handlers (`@app.route`, `@GetMapping`, `@Get()`) already surface as
    gateways and are deliberately not re-emitted.
  - **render → view.** A handler that renders a template named by a string
    literal (`render(request, "x.html")`, `render_template("x.html")`,
    `res.render("index")`, `view("home")`) has no call edge to the template.
    Each chain now carries an additive `views` list of the templates it renders;
    when the template file is itself indexed, the edge resolves to that file.
  This is **one shape-keyed resolver, not a plugin per framework**: detection
  keys on the structural shape of the call, and resolution reuses the same
  symbol-table + import-graph machinery `find_direct_callees` uses. It is a pure
  read path — no reindex, no `INDEX_VERSION` bump, nothing persisted.
- **`get_signal_chains` gains `include_flow_edges` (default `true`).** Set
  `false` for pure call-graph behavior. `_meta.flow_edges` reports
  `{route_gateways, unresolved_routes, render_views}`. With the flag off, or on a
  repo with no route/render shapes, output is byte-identical to the previous
  behavior (no new gateways, no `views` keys, no `flow_edges` meta).

10 tests in `tests/test_v1_108_58.py` (Django/Express/Flask resolution,
render→view capture, unresolved-route reporting, off-path parity).

### Fixed

- **`load_live_journal` 0-minute-budget boundary (flaky on Windows CI).** The
  freshness check used `age > timedelta(minutes=max_age_minutes)`, so a journal
  written "now" and read back within the same clock tick had `age == 0` and
  `0 > 0` was false — it loaded instead of being treated as stale, failing
  `test_v1_108_57.py::test_load_respects_max_age` intermittently on fast Windows
  runners. Changed to `>=` so a 0-minute budget always rejects, matching the
  documented semantics. No effect on normal budgets.

## [1.108.57] - 2026-06-17 - PreCompact hook reads a persisted live session journal (#334)

### Fixed

- **#334 — the PreCompact CLI hook can now see real session state.** The
  `hook-precompact` command runs as a separate process from the MCP server, so
  it imported `get_session_snapshot()`, read a fresh process-local
  `SessionJournal`, and emitted a healthy-looking but empty
  `Files explored: 0 | Searches: 0` snapshot — failing the one thing it exists to
  do. The live server process now persists a compact journal snapshot to a
  small, atomically written `_session_live.json` in `CODE_INDEX_PATH`
  (`save_live_journal`, throttled to at most once every ~2s on the tool-call
  path, best-effort, independent of `session_resume`). The hook reads it back
  (`snapshot_from_live`) and renders it through the same formatter the live tool
  uses — so the out-of-process snapshot now matches what the running server
  sees, including structural-landmark enrichment seeded from the persisted
  journal. When no live journal is readable, the hook emits an explicit
  "no live session journal" message instead of an apparently-successful
  zero-state snapshot. The snapshot renderer is factored into a shared
  `_render_snapshot()` so the live tool and the hook stay byte-identical.
  Disable the live journal with `JCODEMUNCH_LIVE_JOURNAL=0` (disclosed in the
  README's background-behavior section). Reported by @mmashwani with a
  source-cited probe of the process boundary. 10 tests in
  `tests/test_v1_108_57.py`, including a real cross-process check (seed a
  journal in one process, read it from a fresh `hook-precompact` subprocess).

## [1.108.56] - 2026-06-17 - no-change freshness refresh, cache-hit hardening, subset-scoped pruning (#330, #331, #333)

### Fixed

- **#330 — `index_folder` no-change runs now advance the stored `git_head`.**
  `FreshnessProbe` flags every result `stale_index` when the stored index SHA
  differs from live HEAD. A commit that changed only non-indexed files (or
  otherwise left indexed content unchanged) advanced HEAD while all three
  no-change return paths in `index_folder` returned `"No changes detected"`
  without ever refreshing the stored SHA — so retrieval kept reporting
  otherwise-current symbols as stale right after a successful incremental run.
  A new `_refresh_git_head_if_advanced()` helper writes a metadata-only delta
  (empty changed/new/deleted) that advances `git_head` + `indexed_at` when live
  HEAD has moved; it is wired into all three no-change paths (the watcher
  fast-path pre-read return, the mtime-only return — which now also passes
  `git_head` into its existing `incremental_save` — and the plain-incremental
  return). Best-effort: any failure is swallowed so a no-change run never
  hard-fails on the freshness-metadata refresh.

- **#331 — `search_symbols` cache hits no longer raise a `KeyError` that masquerades
  as a missing argument.** `_result_cache_get` explicitly tolerates a cached
  result without a `_meta` envelope, but the cache-hit return path assumed
  `_meta` existed when stamping `timing_ms`/`cache_hit`, so a legacy or restored
  cache entry lacking `_meta` raised `KeyError("_meta")`. Worse, the server
  dispatcher rendered *any* `KeyError` — including ones raised inside tool code —
  as `"Missing required argument: ..."`, turning an internal dict-shape bug into
  a bogus caller-schema error. Two-layer fix: cache hits now `setdefault` the
  `_meta` envelope, and the dispatcher's `KeyError` handler inspects the
  originating frame — only a `KeyError` raised in its own argument-extraction
  frame is reported as a missing argument; a `KeyError` raised deeper (inside a
  tool) is logged and returned as `"Internal error processing <tool>"` with the
  key in the summary.

- **#333 — `index_folder(paths=[...])` no longer prunes unlisted indexed files.**
  A subset refresh diffed the supplied `paths` against the *entire* stored
  index, so `detect_changes_with_mtimes` flagged every unlisted indexed file as
  deleted and `incremental_save` removed its symbols — a scoped refresh could
  silently shrink the whole index. `resolve_explicit_paths` now also returns
  `requested_rels` (the root-relative entries the caller listed, including
  directories and paths deleted from disk), and the incremental path rescopes
  `deleted` to only files covered by that subset. Listing the root (`.` / `""`)
  preserves full-corpus diff semantics; a listed file that was deleted on disk
  is still pruned (the `No source files found` guard now lets a deletion-only
  subset through). The rescope sits on the shared `deleted` list, so the
  branch-delta save path is covered too. Mirrors the jdocmunch #31 subset-diff
  contract on the code side.

- 9 tests in `tests/test_v1_108_56.py`.

## [1.108.55] - 2026-06-12 - turn-budget reset, honest budget packing, fast search_text rejection (#327, #328, #329)

### Fixed

- **#327 — Turn budget now resets in every reader, not only post-dispatch.**
  The idle-gap reset lived solely inside `record_output`, which the server
  calls after a tool's result is computed. Readers that run during tool
  execution — `plan_turn`'s budget advisor via `percent_used()`,
  `should_compact()` — therefore reported the previous turn's exhausted
  counter after any idle gap (143.4% observed after a 2-minute pause). The
  gap check is factored into `_maybe_reset_locked()` and fires in
  `percent_used()` and `should_compact()` as well; reader-triggered resets
  do not advance the activity timestamp, so consecutive readers all see the
  fresh turn. Documented (in the class docstring) that the singleton is
  process-global across repos and that the wall-clock gap behaves as a
  rolling exploration budget during continuous agent use.

- **#328 — `search_symbols` token_budget packs against payload, and fallback
  summaries no longer duplicate signatures.** All three search paths (BM25,
  semantic/hybrid, fusion) packed entries — and reported
  `_meta.tokens_used` — using each symbol's source-body `byte_length`, even
  in compact/standard mode where the row itself is ~50-80 bytes. A compact
  call with `token_budget=8000` admitted "as many symbols as fit 32000
  bytes of source code" (84 rows observed for `max_results=18`) while
  `tokens_used` described code nobody received. New `_packing_cost_bytes()`
  charges the encoded row in compact/standard and keeps the §1.2
  materialized `byte_length` in full mode; `tokens_used`/`tokens_remaining`
  now describe the payload. Separately, indexes built without an AI
  summarizer persist `signature_fallback` output (the signature truncated
  to 120 chars) as the summary; `_row_summary()` emits such echo-summaries
  as empty so standard rows no longer carry the signature twice
  (class/constant/type fallbacks like "Class Foo" are preserved). The
  documented "token_budget overrides max_results" contract is unchanged;
  schema + docstring now spell out that compact rows are cheap and budget
  mode can therefore return many rows.

- **#329 — `search_text` limits documented, and rejection is now instant.**
  The 200-char regex cap, 500-char plain-query cap, and nested-quantifier
  rejection existed only as in-tool checks discovered by hitting them, and
  a doomed call still paid the full pre-dispatch cost first — strict
  freshness waits plus auto-watch `ensure_indexed`, which can run an
  unbounded folder reindex (29.3s to reject a 204-char regex in the field).
  The checks are factored into `validate_query_args()` and `call_tool` runs
  it before strict-freshness and auto-watch, so argument rejections return
  in milliseconds. The limits are documented in the tool schema (`query` +
  `is_regex` field descriptions) and the docstring, including the guidance
  to split long alternations into multiple calls.

All three reported by @mmashwani with verified-in-source root causes.
Additive per the 1.x contract: no tool/response shape removals; budget
packing and tokens_used change only toward their documented meaning.
Regression tests in `tests/test_v1_108_55.py` (15).

## [1.108.54] - 2026-06-11 - list-repos / delete-index honor CODE_INDEX_PATH

### Fixed

- **`list-repos` and `delete-index` CLI verbs now honor `CODE_INDEX_PATH`.**
  Both dispatched into the store with no storage path, so they always read
  (and for `delete-index`, deleted from) `~/.code-index` regardless of the
  documented env override — unlike every other index-touching CLI verb.
  Consequences: scripted callers pointing at a custom store silently operated
  on the wrong one, and the `delete-index` CLI test, which relied on
  `CODE_INDEX_PATH` for isolation, was deleting a real index from the
  developer's home store on every local suite run while failing in CI where
  the home store is empty (CI red since v1.108.50). The test now also
  asserts store isolation explicitly so a future regression fails loudly
  instead of destroying a real index.

## [1.108.53] - 2026-06-11 - recency window on weight tuning

### Changed

- **`tune_weights` learns from a recency window, not the lifetime ledger.**
  New `max_age_days` parameter (default 90; `0` restores the lifetime read)
  on the MCP tool and `WeightTuner.learn()`. Previously the tuner read all
  ranking events ever recorded for a repo, so as a repo's query distribution
  drifted — new code, new agent habits, corpus growth — old events kept
  anchoring `semantic_weight` / `identity_boost` proposals toward behavior
  that no longer exists. Recent research on memory systems shows exactly this
  failure mode: accumulated context that can't distinguish relevant from
  stale signal degrades retrieval quality over time. The window keeps
  learning grounded in how the repo is actually being queried now. Per-repo
  results and the tool summary report the window used.

## [1.108.52] - 2026-06-10 - back on PyPI; background-behavior disclosure

### Added

- **"Background behavior, fully disclosed" README section.** Enumerates every
  long-running or out-of-band behavior in the package — file watching (dies
  with the process), the `watch-install` login service (explicit opt-in only,
  removable via `watch-uninstall`), the anonymous savings telemetry (counters
  only; opt out with `share_savings: false` / `JCODEMUNCH_SHARE_SAVINGS=0`),
  agent hooks written by `init`/`install` (reversed by `uninstall`), and the
  local index store at `~/.code-index/`. Added per PyPI's guidance when the
  package was cleared from quarantine: long-term operations outside expected
  package behavior must be disclosed in the README.

### Changed

- **PyPI install channel restored.** The package has been reviewed and cleared
  from quarantine; `pip install jcodemunch-mcp` / `uvx jcodemunch-mcp` work
  again. README, QUICKSTART, and USER_GUIDE drop the quarantine-era
  GitHub-release wheel pins and review notes: one-click badges (VS Code,
  VS Code Insiders, Cursor) point back at the bare package name, the hero
  commands lead with PyPI again, the Codex venv install reverts to
  `-U jcodemunch-mcp`, and the Antigravity config example uses
  `args: ["jcodemunch-mcp"]`. The `git+https` source install stays documented
  as the version-stable, PyPI-independent channel for pinned/B2B deployments.

## [1.108.51] - 2026-06-10 - `config set` / `config unset` CLI (typed JSONC write-back)

### Added

- **`jcodemunch-mcp config set <key> <value>`** and **`config unset <key>`** —
  write a config key back to the global `config.jsonc`, preserving comments.
  `<value>` is parsed as JSON (`true`, `7`, `0.5`, `["a","b"]`, `{"k":1}`,
  `null`) or kept as a bare string for string-typed keys. Coerced + type-checked
  against `CONFIG_TYPES`; unknown keys, wrong types, and the read-only `version`
  key are rejected. `--json` emits `{success, key, value|changed, message|error}`
  (exit 1 on error). `unset` clears a key so its built-in default applies.
  Motivation: the jMunch Console's Config screen needs to become editable, and
  the console drives jcm through the CLI.
- **`config.set_key()` / `set_config_value()` / `coerce_config_value()` /
  `unset_key()` / `unset_config_value()`** — general typed JSONC write-back,
  generalizing `set_bool_key` to every value type. A new comment/bracket/string
  aware value scanner (`_scan_jsonc_value_end`) lets the writer replace
  multi-line array/object values in place (including the `// "field",` comment
  lines embedded in the `languages` / `meta_fields` template arrays). Writes are
  re-parsed and the edited key is confirmed to read back as intended before the
  change is kept; on any failure the file is rolled back to its prior contents
  (a pre-existing unrelated config issue can't block a valid edit).

Additive: no behavior change to `config` (show), `config --json`, `--check`,
`--init`, `--upgrade`. New tests in `tests/test_config_set.py`.

## [1.108.50] - 2026-06-10 - `delete-index` CLI subcommand

### Added

- **`jcodemunch-mcp delete-index <repo> [--json]`** — CLI alias for the
  `invalidate_cache` MCP tool. Resolves the repo (owner/repo or bare name as
  shown by `list-repos`), deletes its index + cached data, and clears the
  in-process caches. Exits `0` on success and non-zero when no index is found,
  so scripted callers can branch on the return code, not just the JSON body.
  `--json` emits the structured `{success, repo, message|error}` result.
- Registered in `known_commands` (so the prepend-`serve` guard doesn't swallow
  it, per the v1.108.36 lesson).

### Notes

- Motivation: the jMunch Console's Index panel needs a delete action, and the
  Console drives jcm through the CLI (it does not speak MCP). Indexes are
  SQLite-backed, so a filesystem delete is unsafe; this routes through the
  canonical `invalidate_cache` path instead. Pure additive surface; no existing
  behavior changes. 1 new hermetic CLI test (`tests/test_delete_index_cli.py`)
  exercises index -> delete -> gone -> delete-again-fails against an isolated
  `CODE_INDEX_PATH`. Full suite 4515 passed / 10 skipped. GitHub-release wheel
  (PyPI #308).

## [1.108.49] - 2026-06-10 - Windows stdio git-stdin deadlock straggler

### Fixed

- **`get_symbol_source` (git-SHA verify path) could deadlock on Windows.**
  `_verify_against_git_sha` in `tools/get_symbol.py` spawned
  `git -C <root> show HEAD:<file>` without redirecting stdin. Over the MCP
  stdio transport the server's stdin is the JSON-RPC pipe; a git child that
  inherits it hangs forever, because Git for Windows' `cmd\git.exe` wrapper
  holds the inherited pipe handle even for commands that never read stdin.
  Added `stdin=subprocess.DEVNULL`. This is the same class of fix shipped for
  jdocmunch in v1.69.1 (PR #30) and already present across jcm's other git
  spawns (freshness, git_root, git_blame, index_store, resolve_repo, watcher,
  the get_* risk/churn tools). `get_symbol_source` is a hot read tool, so this
  was the highest-exposure straggler.
- **`hook_event.py` git spawns hardened too.** A re-audit found the four git
  spawns in the `hook-event` worktree CLI (`rev-parse`, `worktree add`,
  `worktree remove`, `branch -D`) also lacked the redirect. They run as a
  short-lived CLI hook (lower exposure than the server hot path) but inherit
  the hook's stdin, so they got `stdin=subprocess.DEVNULL` in the same sweep.
  **Every jcm git spawn now redirects stdin to DEVNULL.**

### Notes

- Pure defensive redirect — no behavior change on the success path (these git
  commands don't read stdin). 1 new regression test
  (`test_git_show_redirects_stdin_to_devnull` in
  `tests/test_git_sha_verification.py`) asserts the `git show` call passes
  `stdin=subprocess.DEVNULL`. Full suite 4514 passed / 10 skipped.
  GitHub-release wheel (PyPI #308).

## [1.108.48] - 2026-06-09 - opt-in strict enforcement (deny native Read/Grep)

### Added

- **`JCODEMUNCH_ENFORCE` enforcement tier** for the PreToolUse hook, read by
  `_enforce_mode()` in `cli/hooks.py`:
  - `advisory` (default): warn on stderr but **allow** — unchanged v1.108.47
    behavior. Read-before-Edit and the Grep fallback keep working.
  - `strict`: **deny** a native Read/Grep that an indexed-repo jCodemunch route
    can already serve, returning a Claude Code PreToolUse `deny` decision that
    names the credited route to use instead.
  - `off`: fully silent (no nudge, no deny).
  - Unknown values fall back to `advisory`, so a typo can never hard-block tools.
- **`init --strict`** (and `install_enforcement_hooks(strict=True)`): installs the
  enforcement hooks and persists `env.JCODEMUNCH_ENFORCE = "strict"` into
  `~/.claude/settings.json` so the hook subprocess reads it cross-platform (no
  shell env plumbing). Re-running `init` without `--strict` resets the flag to
  `advisory`; non-strict installs never invent the key.

### Notes

- **Strict is a redirect, not a wall.** Targeted reads (`offset`/`limit`, i.e.
  pre-Edit exact-line reads), tiny files (under `JCODEMUNCH_HOOK_MIN_SIZE`),
  non-code files, and any path outside every indexed `source_root` always pass —
  so jCodemunch is never blamed for a search it can't serve, and the escape
  hatch is one step away. The deny rides the stdout JSON decision channel; exit
  code stays 0 and stderr stays free.
- Strict is **opt-in** by design: deny-by-default would reverse the deliberate
  "never hard-block Read-before-Edit" stance and would change a previously-
  default behavior to raise, which the 1.x compatibility contract reserves for a
  2.x. Default installs stay advisory.
- 14 new tests (`TestStrictEnforce` in `tests/test_hooks.py` +
  strict-install cases in `tests/test_post_tool_use_hook.py`). Full suite 4513
  passed / 10 skipped. GitHub-release wheel (PyPI #308).

## [1.108.47] - 2026-06-09 - Grep-aware PreToolUse nudge

### Added

- The PreToolUse hook (`hook-pretooluse`) now also intercepts **Grep**, not just
  Read. When a Grep's search root falls inside an indexed repo, it nudges the
  agent to exhaust the credited jCodemunch retrieval routes first
  (`search_text` / `search_symbols` / `find_references` / `find_importers`)
  before the raw text scan. Grep is the tool that most often does a job a jcm
  route already covers, entirely off the savings meter; the nudge closes that
  leak. The hook still **allows** the Grep (exit 0) so it remains the fallback
  once the jcm routes come up empty.
- The nudge fires only when the search root is equal to, inside, or an ancestor
  of an indexed `source_root`; a Grep outside every indexed repo (or when
  nothing is indexed) is allowed silently. Set `JCODEMUNCH_HOOK_GREP_NUDGE=0`
  (or `false`/`no`/`off`) to disable it.
- `init` / `install` now register the PreToolUse hook with matcher `Read|Grep`
  (was `Read`). Existing installs keep firing on Read; re-run `init` (or change
  the matcher to `Read|Grep` by hand) to pick up Grep coverage.

### Notes

- Best-effort and non-blocking by construction: any index-store error yields no
  nudge rather than a blocked Grep, and the Grep branch swallows unexpected
  exceptions so a nudge can never crash the agent's tool call. 8 new tests
  (`TestGrepNudge` in `tests/test_hooks.py`). GitHub-release wheel (PyPI #308).

## [1.108.46] - 2026-06-09 - self-correcting savings telemetry

### Changed

- The anonymous community-meter telemetry (`token_tracker`) now sends the
  **absolute lifetime total** alongside the delta (`{anon_id, delta, total}`),
  and the server applies `GREATEST(stored, total)` instead of a pure additive
  `+= delta`. Previously a dropped or failed POST (best-effort, in-memory queue,
  no retry) permanently undercounted the meter, so the server-side total drifted
  below the client's true local `total_tokens_saved`. With absolute-total
  reporting, any single successful send self-heals the entire accumulated gap.
  Two invariants are preserved by design:
  - **Backward compatible** — older clients that send only `{anon_id, delta}`
    keep the additive path unchanged.
  - **Monotonic / no take-backs** — `GREATEST` never lowers a stored total, so a
    client that reset its local counter to 0 (or reports a smaller number)
    cannot shrink or zero out the DB row; clients can't reclaim reported savings.
    (A reset also yields a fresh `anon_id`, starting a new row rather than
    touching the old total.)

  The server change lives in `APIs/savings/post.php` (deployed separately to
  j.gravelle.us). 2 new tests in `tests/test_share_savings.py`
  (`TestTelemetrySelfCorrecting`).

## [1.108.45] - 2026-06-09 - config --json description off-by-one fix

### Fixed

- `_config_meta()` (which supplies the per-key `description` in `config --json`)
  mis-attributed comment blocks for keys the template documents with an indented
  block BELOW the key — `embed_model`, `allow_remote_summarizer`, `path_map`,
  `render_diagram_viewer_enabled`, and others. The template mixes two comment
  conventions (a block ABOVE the key vs. a block BELOW it) and the v1.108.44
  parser only handled "above," so each below-key block landed on the **next** key
  (an off-by-one): `embed_model` showed no description, `allow_remote_summarizer`
  showed `embed_model`'s text, `path_map` showed `allow_remote_summarizer`'s. The
  parser now uses the blank line as the discriminator — comments contiguous with
  the key they follow attach to that key; comments after a blank/value/header
  line begin the lead block for the next key. Surfaced by the jMunch Console
  rendering jcm's `config --json` on its config screen. No config value behavior
  change. 2 new tests in `tests/test_config.py` (`TestConfigReportGrouping`).

### Docs

- `TWEAKCC.md`: stop pinning the routing snippets to fixed tweakcc fragment
  filenames (`system-prompt-tool-usage-read-files.md`, etc.). Claude Code
  reorganized its system prompt and tweakcc regenerates the fragment set per
  installed CC version, so the hardcoded names had gone stale (#326, reported by
  @FlightXamb). Snippets are now keyed to behavior with instructions to locate
  the current section via `~/.tweakcc/system-prompts/` or the
  Piebald-AI/claude-code-system-prompts mirror; refreshed the apply workflow
  (`npx tweakcc --apply`) and rollback (`npx tweakcc --restore`).

## [1.108.44] - 2026-06-08 - config --json emits group + description per key

### Added

- `config_report()` (the `config --json` payload) now includes a **`group`** and
  **`description`** per key, derived from the config template's `=== Section ===`
  headers and inline comments via a new one-pass `_config_meta()` parser. Keys
  with no template section fall back to group `"Other"`; keys with no inline
  comment get an empty description. Makes `config --json` self-documenting and
  lets the jMunch Console render a grouped, collapsible config screen instead of
  a flat 83-key list. General-purpose for CI/dashboards; no behavior change to
  any config value. 4 new tests in `tests/test_config.py`
  (`TestConfigReportGrouping`).

## [1.108.43] - 2026-06-08 - Org-rollup gate requires a multi-seat tier (Studio+)

### Changed

- The org-rollup license gate now requires a **multi-seat tier** — `studio` or
  `platform` (`ORG_TIERS` in `org/license.py`). org-rollup is a team feature, so
  a valid but single-seat **Builder** license no longer unlocks it; it's treated
  like an unlicensed org (full 14-day grace window), then blocked with an
  *upgrade to Studio or Platform* message rather than a generic "get a license."
  The real tier is surfaced on the gate result so the CLI/Console can show
  "Builder — upgrade needed." Tier strings match what `validate.php` returns
  (lowercased). 9 tests in `tests/test_org_license.py` (Builder-excluded +
  Studio/Platform-licensed cases added).

## [1.108.42] - 2026-06-08 - Org-rollup license gate (team SKU billing)

### Added

- **License gate on `org-rollup` only** — the per-org savings dashboard is the
  paid team-SKU feature; this gates it. Every individual tool, indexing, search,
  and seat reporting (`org-report` / the `/org/report` ingest) stay free, so a
  trial org keeps accruing data before it pays. New `org/license.py` verifies a
  key online against the shared `validate.php` backend (`product=jcodemunch`;
  Stripe webhooks populate it) — the host holds no secrets, GETs the public
  endpoint, and trusts the JSON `valid` field (body over status code).
- `JCODEMUNCH_LICENSE_KEY` env var + `license_key` config key (surfaced by
  `config --json`, so the Console can show license status).
- `license` CLI subcommand — `jcodemunch-mcp license [--key KEY] [--json]`
  reports licensed / evaluation / unlicensed status, tier, and trial days left.

### Behavior

- **Sticky offline:** a server-confirmed key survives network failures (cached in
  `<CODE_INDEX_PATH>/license.json`, re-confirmed at most every 7 days); only an
  explicit `{"valid": false}` (revoked / expired / not found) blocks. A server
  outage never punishes a paying customer.
- **14-day grace window** from the first unlicensed `org-rollup` call lets a new
  org evaluate the dashboard; after it lapses with no valid key, `org-rollup`
  hard-refuses with a pricing link (JSON callers get `{error, license_mode,
  get_license}`). Licensed/grace runs attach a `_license` block to the rollup.
- 7 new tests in `tests/test_org_license.py`. Live backend contract verified
  (`product=jcodemunch` → 200 JSON; the body carries `valid` even on 400).

## [1.108.41] - 2026-06-08 - Parse cache: bounded size (FIFO eviction)

### Added

- `JCODEMUNCH_PARSE_CACHE_MAX_ROWS` (default `50000`; `<= 0` disables the cap)
  bounds the shared parse cache introduced in 1.108.40. The store is
  content-addressed, so it would otherwise grow without limit: every file edit
  mints a new `content_hash` whose prior entry can never hit again, and an
  `INDEX_VERSION` bump strands the entire previous generation of keys.

### Changed

- After each cache write, the store is FIFO-trimmed to the cap
  (`_evict_oldest`), deleting oldest-first by `rowid` (insertion order). Because
  stale-content and stale-version rows were written before the current
  generation, they are evicted first. The cache is recomputable, so
  over-eviction under concurrent seats only costs a re-parse, never correctness.
  Eviction is a no-op when under the cap, when disabled, or when the cache env
  var is unset (still a pure pass-through). 4 new tests in
  `tests/test_parse_cache.py` (9 total).
## [1.108.40] - 2026-06-07 - Content-addressed parse cache for shared-host indexing

### Added

- Opt-in shared parse cache (`parser/parse_cache.py`) for multi-seat machines.
  When N home dirs on one box index overlapping repos, each re-parses identical
  files; this caches tree-sitter parse output keyed by
  `(INDEX_VERSION, content_hash, language, filename)` in a shared SQLite store,
  so an identical file parses once across all seats — Nx parsing → 1x +
  (N-1) lookups. Enabled by pointing `JCODEMUNCH_PARSE_CACHE` at a directory all
  seats share; **unset = disabled = zero behavior change** (`cached_parse_file`
  is a pass-through to `parse_file`). Safety: the key includes `INDEX_VERSION`
  (a parser/schema bump invalidates the cache) and `filename` (symbol ids embed
  the path), so a hit is byte-identical to a fresh parse; any read/deserialize
  error falls back to a live parse — the cache can only ever cause a slower
  miss, never wrong symbols. Wired into the bulk indexing paths
  (`_indexing_pipeline.py`, `index_folder.py`). 5 tests in
  `tests/test_parse_cache.py`. First slice of the shared-host indexing fix; the
  daemon-mode option and cache eviction are follow-ups.

## [1.108.39] - 2026-06-07 - Org rollup HTTP transport (cross-machine seat reporting)

### Added

- `POST /org/report` ingest route on the org host — seats ship their savings
  over HTTP instead of needing a co-located store. Mounted on both the SSE and
  streamable-http transports (`org/http_routes.py`). Off by default, two-key
  turn mirroring the runtime ingest endpoints: `JCODEMUNCH_HTTP_TOKEN` bearer
  auth (existing Starlette middleware) + `JCODEMUNCH_ORG_INGEST_ENABLED=1`.
  Reuses the runtime body-read (gzip + size cap) and JSON helpers; records via
  the same `record_seat_report` sink as the local path.
- `org-report` now POSTs to a remote org host when `--endpoint` /
  `JCODEMUNCH_ORG_ENDPOINT` is set (httpx, with the bearer token), falling back
  to local recording otherwise. New `org_ingest_enabled` config key +
  `JCODEMUNCH_ORG_INGEST_ENABLED` env var. 5 tests in `tests/test_org_http.py`
  (Starlette TestClient round-trip + gate + seat-reporter POST); verified
  end-to-end over a real streamable-http server.

## [1.108.38] - 2026-06-07 - Org rollup (team SKU): per-seat savings aggregation

### Added

- New `org` package + two CLIs for cross-seat token-savings aggregation (the
  team/enterprise SKU). `org-report` computes this seat's transcript-derived
  savings (reusing the `receipt` machinery) and records them under
  `JCODEMUNCH_ORG_ID` keyed by seat + day; `org-rollup --json` aggregates all
  seats in an org → `{org_id, seats[], totals}`. Backed by a transport-agnostic
  SQLite store (`org/store.py`: `record_seat_report` upserts per seat+day,
  `org_rollup` sums per seat). Self-hosted model: seats record into the org
  host's store. Data is deliberately minimal — per-seat token/dollar/call counts
  only; no code, content, or paths. The cross-machine HTTP front door (seats
  POSTing to the org host) is the next increment over this same store. Powers
  the jMunch Console org-rollup card. 5 tests in `tests/test_org_rollup.py`.

## [1.108.37] - 2026-06-07 - `repos_report` includes source_root

### Added

- `repos_report()` (and `list-repos --json`) now include each repo's
  `source_root` filesystem path. `list_repos` already exposed it; it just wasn't
  forwarded into the cockpit report. Enables path-aware consumers — e.g. the
  jMunch Console launcher, which resolves a repo's working directory server-side
  rather than trusting a client-supplied path. Test updated in
  `tests/test_list_repos.py`.

## [1.108.36] - 2026-06-07 - `list-repos --json` cockpit view (counts + freshness + watcher state)

### Added

- New `list-repos` CLI subcommand. Human output lists indexed repos with symbol/
  file counts, freshness, and watcher state; `--json` emits a structured array
  (`repo_id`, `display_name`, `file_count`, `symbol_count`, `languages`,
  `indexed_at`, `freshness`, `watcher_state`, `lock_holder`). Backed by a new
  `tools.list_repos.repos_report()` that joins `list_repos` metadata with
  `get_watch_status` (staleness + watcher lock holder) keyed by source_root.
  Powers the jMunch Console index/watcher cockpit; general-purpose for CI and
  dashboards. Also registered `list-repos` in the backwards-compat
  `known_commands` set so it isn't misrouted to `serve`. Regression test in
  `tests/test_list_repos.py`.

## [1.108.35] - 2026-06-07 - Fix CI-only test break from the v1.108.33 claude resolver

### Fixed

- Two `test_init.py` tests (`test_configure_client_cli_success`,
  `test_configure_client_cli_already_exists`) mocked `subprocess.run` but not the
  `_claude_cli_exe()` resolver added in v1.108.33, so on machines without
  `claude` on PATH (every CI agent) `_configure_claude_code` short-circuited to
  "claude CLI not found" before the mocked subprocess and the assertions failed.
  The break was invisible locally because `claude` is installed there. Tests now
  also patch `_claude_cli_exe`, making them deterministic regardless of
  environment. No production code change.

## [1.108.34] - 2026-06-07 - `config --json` structured, source-attributed config export

### Added

- `config --json` emits the effective configuration as a structured array —
  one entry per known key with `key`, `type` (bool/int/float/string/list/dict),
  `value`, `default`, and `source` (default / global / project). Source
  attribution mirrors the human `config` output: a key is `project` if the
  repo's `.jcodemunch.jsonc` sets it, `global` if the global `config.jsonc` does,
  else `default`. Backed by a new reusable `config.config_report(repo=None)`.
  Machine-readable counterpart to `config` for tooling — CI, dashboards, and the
  jMunch Console's config panel (auto-generates toggles + source badges from
  this). Regression test in `tests/test_config.py`.

## [1.108.33] - 2026-06-07 - Claude Code CLI integration works on Windows / recognizes any launcher

### Fixed

- `install-status` reported Claude Code as not-configured on Windows even when
  jcodemunch was registered and connected. The CLI checks shelled out to a bare
  `["claude", ...]`, but the npm `claude` wrapper is `claude.CMD` / `.ps1` with
  no bare `.exe`, so `subprocess` raised `FileNotFoundError` and the check
  silently fell through to `configured=False` — `claude mcp list` never ran. The
  same broken invocation was in `install`/`uninstall` (`claude mcp add` /
  `remove`), so registering Claude Code via the CLI also no-op'd on Windows.
  Added a shared `_claude_cli_exe()` resolver (`shutil.which`, which honors
  PATHEXT and finds the shim); all three call sites use it. The detection itself
  was already launcher-agnostic — it matches the server name in `claude mcp
  list` output — so once the command actually runs it correctly recognizes
  custom launchers (e.g. a `jmunch-mcp --config jcodemunch.toml` multiplexer),
  venv paths, or `uvx`. 2 regression tests in `tests/test_install_uninstall.py`.

## [1.108.32] - 2026-06-06 - Share file reads across call-graph BFS traversals

### Changed

- `bfs_callers` / `bfs_callees` (`tools/_call_graph.py`) re-read the same files
  from storage once per visited node — a callers BFS re-reads an importer file
  for every target whose file it imports, so a symbol called from two siblings
  in one file caused that file (and its importers) to be read and re-split
  repeatedly. Added a per-traversal `_ContentCache` that memoizes
  `store.get_file_content` and `splitlines()`, threaded into `find_direct_callers`
  / `find_direct_callees` as an optional argument. Reads drop from O(nodes ×
  files) to O(distinct files) per traversal. This is the output-preserving form
  of the optimization: the BFS still iterates per node, so result order and the
  caller/callee sets are byte-identical — only redundant I/O is removed. The
  riskier name-union level-batching was rejected because it would reorder
  within-level results. Direct callers of the finders (`get_impact_preview`,
  `get_signal_chains`) are unaffected: the cache argument defaults to off.
  2 regression tests in `tests/test_call_hierarchy.py` (finder output identical
  with/without cache; BFS discovers the full tree reading no file twice).

## [1.108.31] - 2026-06-06 - Cache the per-call git HEAD probe

### Changed

- `FreshnessProbe` ran `git rev-parse HEAD` as a subprocess on every tool call
  that annotated results with a known source root (`search_symbols`,
  `get_symbol_source`, `get_context_bundle`, `get_ranked_context`, ...). The
  HEAD lookup is now cached process-wide and the subprocess re-runs only when a
  cheap stat-based signature of the refs that move with HEAD changes — `HEAD`,
  `packed-refs`, the reflog (`logs/HEAD`), and the current branch's loose ref
  (plus the worktree common-dir variants). When no signature can be computed
  (exotic git layouts), a 2s TTL bounds reuse so a burst of calls shares one
  subprocess instead of spawning one each. The signature deliberately tracks the
  loose ref / reflog, not just `HEAD`+`packed-refs`, because an ordinary commit
  moves those — keying on `HEAD` mtime alone would have served a stale SHA as
  fresh. A cache miss always recomputes, so correctness is unchanged. 3
  regression tests in `tests/test_freshness.py`.

## [1.108.30] - 2026-06-06 - Schema-encoded tools silently swallowed every error response

### Fixed

- Schema-encoded tools (`search_symbols`, `search_text`, `get_file_outline`,
  `get_call_hierarchy`, and every other tool with a custom encoder) silently
  dropped all error responses, leaving the agent with a blank "0 results" it
  could not distinguish from genuine emptiness. When a tool returned an
  `{"error": ...}` dict (ambiguous repo, repo-not-found, invalid
  `detail_level`/`sort_by`, query-too-long, no embedding provider), the
  dispatcher handed it to the schema encoder, whose declared tables/scalars/meta
  keys matched none of the error dict's keys — so it emitted only the static
  `__tables` schema line with an empty table and the `error` message vanished.
  Under the default `adaptive` output the size gate made it worse: a longer,
  more informative error inflated the JSON baseline, cleared the savings
  threshold more easily, and was the *more* likely to be encoded away. Fixed
  with an error-passthrough guard in `encode_response` that returns any dict
  containing an `error` key as raw JSON, placed before the format gate so it
  also holds under forced `compact`/`encoded` output. Two regression tests in
  `tests/encoding/test_dispatcher.py`.

- The OpenAI-compatible summarizer now honors the circuit breaker. Its
  `summarize_batch` override submitted `_summarize_one_batch` directly,
  bypassing `_run_batch` and its `_circuit_broken` check, so
  `summarizer_max_failures` never short-circuited the OpenAI provider the way
  it does for Anthropic and Gemini. A dead or failing endpoint was hammered for
  every batch instead of giving up after the configured number of consecutive
  failures. The override now submits `_run_batch`, so once the breaker trips the
  remaining batches fall straight through to signature summaries. Regression
  test in `tests/test_summarizer.py`.

## [1.108.29] - 2026-06-05 - WRR signal fusion was inert (all weights resolved to 0.0)

### Fixed

- Weighted Reciprocal Rank fusion never applied its channel weights, so every
  fused score was `0.0` and final ordering silently fell back to a stable sort
  over insertion order (issue #324, reported by @sektor10 with a full root-cause
  analysis and reproduction). The `fuse()` "use the default weight" sentinel
  checked `ch.weight != 1.0`, but all four `build_*_channel` constructors default
  their `weight` to `0.0` and the live callers (`get_ranked_context`,
  `search_symbols` on the `fusion=True` paths) never pass one. Since `0.0 != 1.0`,
  `fuse()` took the literal `0.0` for every channel and never consulted the
  weights dict, so `contribution = 0.0 / (k + rank) = 0.0` everywhere and the
  documented ranking (identity 2.0 > lexical 1.0 > similarity 0.8 > structural
  0.4) was a no-op. Fixed by moving to an unambiguous `None` sentinel:
  `ChannelResult.weight` and all four builder defaults are now `None` (meaning
  "use the default weight for this channel name"), and `fuse()` checks
  `ch.weight is not None`. An explicit weight (including `0.0`) is now respected
  as a real override. Default `search_symbols`, the embedding hybrid blend, and
  per-repo `semantic_weight` tuning never routed through `fuse()` and are
  unaffected. New regression tests assert non-zero channel contributions, that an
  identity-only top hit outranks a lexical-only one, and that the builder
  defaults match the sentinel `fuse()` recognises (`tests/test_signal_fusion.py`,
  17 -> 21 tests).

## [1.108.28] - 2026-06-05 - Thinking-model summary degradation: warning + configurable OpenAI extra body

### Added

- `openai_extra_body` config key (and `JCODEMUNCH_OPENAI_EXTRA_BODY` env var, a
  JSON object) is merged into every OpenAI-compatible `/chat/completions` and
  `/responses` summarizer request (issue #323, reported by @ggppdk). Lets callers
  pass provider-specific knobs the standard payload doesn't expose — the motivating
  case is disabling a local thinking model's reasoning so the output budget isn't
  consumed by reasoning tokens. For llama.cpp / Qwen:
  `JCODEMUNCH_OPENAI_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}'`.
  The config key is project-overridable via `.jcodemunch.jsonc`; when both the env
  var and config set the same top-level key, the config value wins.

### Changed

- The summarizer now reports **silent degradation**. Previously, a thinking model
  that spent its whole output budget on reasoning returned `200 OK` with no usable
  summary text — no exception fired, the circuit breaker never tripped, and every
  symbol quietly fell back to a generic signature while the run looked successful.
  A new run-level check counts symbols that fell back **despite** a successful
  response; when that fraction crosses 50%, jcodemunch logs a warning naming the
  likely cause and the remedy (disable thinking via `JCODEMUNCH_OPENAI_EXTRA_BODY`
  or raise `OPENAI_MAX_TOKENS`). The remedy hint is provider-aware (#323).

### Internal

- Factored the duplicated "assign summary or signature-fallback" loop out of all
  three provider summarizers into `BaseSummarizer._apply_summaries()`, which also
  drives the new degradation counters. New tests in `tests/test_summarizer.py`.

## [1.108.27] - 2026-05-30 - Windows UNC share-child indexing

### Fixed

- Windows UNC share child paths are no longer rejected by the broad-root guard in
  `index_folder()` (issue #321, fix #322 by @JayceeB1). `pathlib` stores a UNC
  share root like `\\server\share\` as a single anchor component, so
  `\\server\share\repo` had only two `Path.parts` and tripped the "fewer than 3
  path components" safety check meant to catch shallow roots like `C:\Users`. A
  new `_path_safety_part_count()` helper counts a UNC anchor as server + share, so
  `\\server\share\repo` is treated as logical depth 3 (allowed) while
  `\\server\share` stays at depth 2 (still rejected). Local-drive behavior
  (`C:\repo`) is unchanged, and the gate is `os.name == "nt"` so POSIX paths are
  untouched. Both guard sites (the hard reject and the container-shallow warning)
  route through the helper. Windows-gated regression tests in
  `tests/test_tools.py::TestWindowsUNCPathSafety`.

### Documentation

- README, QUICKSTART, and USER_GUIDE install sections now tell Windows / Cursor
  users what to do when `'uvx' is not recognized` (issue #320): install uv via the
  Astral one-liner and restart the editor, or skip uvx entirely with `python -m
  pip install` and a `python -m jcodemunch_mcp` launch. Version-agnostic, no
  wheel-pin.

## [1.108.26] - 2026-05-29 - get_file_outline batch mode dropped all symbols

### Fixed

- `get_file_outline` in **batch mode** (`file_paths=[...]`) returned empty
  outlines for every file — models saw "the outline came back empty" and either
  gave up or burned tokens re-querying (issue #319, reported by @MariusAdrian88,
  corroborated by @ImJustWeed). The bug was confined to the MUNCH `fo1` encoder:
  it only understood the **singular** response shape, where `symbols` sits at
  the top level. Batch mode nests the data one level down (`results[].symbols`
  with the count in `results[]._meta.symbol_count`), so the encoder emitted an
  empty `s` symbols table and a blank `symbol_count` for every file. The Python
  tool computed the outlines correctly; the encoding layer silently discarded
  them on the way out — which is why it reproduced even on perfectly-indexed
  files and looked config-dependent (only callers with compact encoding active
  saw it). The encoder now detects the batch shape, flattens each file's symbols
  into a single `file`-discriminated `s` table plus a per-file `results` table
  (file / language / file_summary / symbol_count), and regroups them on decode.
  Singular-mode output is unchanged. New batch round-trip regression test in
  `tests/encoding/test_tier1_roundtrip.py`.

## [1.108.25] - 2026-05-26 - secret-file detection: basename + secret-store dirs

### Fixed

- `is_secret_file()` no longer matches `SECRET_PATTERNS` against the full file
  path — the broad `*secret*` glob run as a path substring caused any directory
  whose name merely contains `secret` (e.g. a `services/secrets-manager/`
  microservice) to drop its entire subtree from the index. Matching is now
  basename-only, with the directory analogue handled deliberately: a file is
  still treated as secret material when it lives under a *whole-segment*
  `secret`/`secrets` directory **and** carries a non-source data/credential
  extension (`.yaml`, `.json`, `.env`, `.pem`, `.tfvars`, …). This preserves
  detection of real credentials stored as data files in conventional secret
  stores (Kubernetes / Terraform / Docker secrets, e.g.
  `config/secrets/database.yaml`) — which a naive basename-only fix would have
  silently started indexing — while leaving source files (`internal/secrets/router.go`)
  and a `secrets-manager` service directory correctly un-flagged. The
  documentation-extension safe-list for `*secret*` is unchanged, and the rule
  honors the existing `exclude_secret_patterns` opt-out. Addresses the report
  behind PR #317.

## [1.108.24] - 2026-05-26 - check_edit_safe edit-safety preflight

New tool `check_edit_safe`: the edit-safety companion to `check_delete_safe`.
Where delete-safety asks "who breaks if this disappears," edit-safety asks
"what's my regression risk if I modify it, and what must I preserve." It fuses
signature impact (external/cross-repo importers that depend on the current
contract), cyclomatic complexity, test-coverage presence, and runtime traffic
into a single verdict + one-line recommended_action. Verdict tiers:
`safe_to_edit` / `untested` / `complexity_risk` / `signature_impact` /
`runtime_critical`. Read-only; never mutates the codebase. Reuses the canonical
`check_delete_safe` signal helpers rather than duplicating them. Tool count 82.

Also: dropped the dead `_HTML_COMMENT_RE` in `imports.py` (unused since the
v1.108.23 Astro work moved comment masking into `astro_shared`).

## [1.108.23] - 2026-05-24 - Astro (.astro) language support

Adds full parsing support for `.astro` files (Astro v6), bringing the language
count to 74. Mixed-language components (TypeScript frontmatter, HTML template,
`<script>`/`<style>` blocks) follow the existing Razor recursive-delegation
strategy. PR #311 by @gokhanozdemir.

### Astro parser/import hardening

- Astro frontmatter parsing now handles UTF-8 BOM, CRLF, and malformed fences
  without crashing or drifting line offsets.
- Astro symbol extraction now includes template `id="..."` constants while
  ignoring IDs inside HTML comments.
- Astro `<script>` parsing now infers `lang="ts|tsx|jsx"` and skips JSON/JSON-LD
  data script blocks.
- Astro import extraction now adds template component usage edges with the
  same `{specifier, names}` shape as existing Vue/Nuxt edge producers.
- `.astro` is now treated as a JS-like extension in import specifier resolution,
  package registry heuristics, and Claude hook extension hints.
- Added focused Astro regression fixtures/tests for BOM+CRLF, malformed
  frontmatter, multi-script parsing, line mapping, synthetic-edge dedup, and
  no-crash behavior.

## [1.108.22] - 2026-05-22 - keyring credentials, metadata-only cache, init --minimal, docstring opt-out, git-SHA verification, sigstore release signing

Six additive enterprise-hardening items in one release. All purely
additive; existing user-facing behavior unchanged across every change.

### Keyring credential resolution (P1.3)

New optional extra `jcodemunch-mcp[keyring]` pulls the `keyring` package
(>=24). Any credential env var the server recognises can be set to
`"keyring:<name>"`, and at startup the server resolves that value to the
secret stored under `<name>` in the system keyring (macOS Keychain /
Windows Credential Manager / freedesktop Secret Service). Downstream code
that reads `os.environ.get(...)` sees the resolved value with no per-tool
changes required.

Recognised env vars: `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
`OPENAI_API_KEY`, `OPENAI_API_BASE`, `MINIMAX_API_KEY`, `ZHIPUAI_API_KEY`,
`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `JCODEMUNCH_HTTP_TOKEN`.

New CLI subcommand `jcodemunch-mcp keyring set|get|delete|list` for
managing entries. `jcodemunch-mcp config --check` output gains a "Keyring
resolution" section listing which env vars were resolved from the keyring
this session, so operators can verify the chokepoint is firing without
inspecting actual secret values.

Fail-closed semantics: missing keyring entries or unavailable backends
leave env vars at the literal `keyring:<name>` string rather than empty,
so downstream HTTP calls fail with "invalid token" instead of silently
sending unauthenticated requests.

8 new regression tests in `tests/test_credentials.py` cover the resolver,
the fail-closed cases, and the canonical env-var surface.

### Metadata-only cache mode (P1.4)

New config key `cache_mode: "full" | "metadata_only"` (default `"full"`,
no behavior change for existing installs). When set to `metadata_only`,
the SQLite symbol table is still written normally, but source bodies are
not persisted to disk — the `bodies/` directory under
`~/.code-index/<repo>/` stays empty.

`get_symbol_source` and `get_file_content` return a structured
`metadata_only_mode` error when invoked under this mode; every other tool
(search_symbols, get_file_outline, find_references, get_dependency_graph,
etc.) works normally because none of them need bodies.

Recommended for managed-endpoint deployments where policy disallows a
second on-disk copy of source (Time Machine / iCloud / OneDrive sync that
the canonical clone is excluded from).

4 new regression tests in `tests/test_cache_mode.py` cover the body-write
gate and the upgrade-preservation invariant.

### `init --minimal` flag (P1.7)

New `--minimal` flag on both `jcodemunch-mcp init` and
`jcodemunch-mcp install <agent>`. When set, writes only the MCP server
registration for the targeted client and skips every other channel:

- No CLAUDE.md policy paste.
- No Cursor / Windsurf rules.
- No AGENTS.md.
- No worktree hooks or enforcement hooks.
- No .github/hooks (Copilot).
- No indexing or audit step.

Recommended for hardened install templates that don't want jcodemunch
touching agent-policy files outside their existing source-controlled
posture. Aggregate breadth concern from F-08 is addressed by giving the
template a narrow scope rather than prohibiting `init` outright.

3 new regression tests in `tests/test_init_minimal.py` lock the contract
that no channel-writing helper fires under `--minimal`.

### Docstring summarizer opt-out (P1.5)

New config key `summarize_from_docstrings: bool` (default `true`).
Controls the Tier 1 summarizer (docstring-first-sentence extraction).
When `false`, Tier 1 is skipped entirely; summaries fall through to Tier 2
(AI summary, if configured) and Tier 3 (deterministic signature-shape
fallback). Neither of those tiers reads docstring content directly into
the summary string an agent sees in metadata position.

Recommended for security-conscious deployments that want to close the
indirect-prompt-injection (IPI) channel docstring-derived summaries
introduce when the same machine has indexed third-party / customer /
demo repositories whose docstrings are not under the team's review. The
host agent's tool-output handling remains the primary IPI control; this
flag is a defense-in-depth measure.

IPI mitigation guidance added to `AGENT_HOOKS.md`. Per-response
`_source` attribution field on summary objects is deferred to a follow-up
release pending a SQLite schema migration that persists the attribution
durably across indexing sessions; the opt-out lever ships now because
it's the substantive control.

5 new regression tests in `tests/test_summarize_from_docstrings.py`
cover the gate semantics.

### Externally-attested cache verification (P1.6)

New `verify_against` parameter on `get_symbol_source`, accepting
`"cache"` (default, self-referential hash check, unchanged from prior
behavior) or `"git_sha"`. When `"git_sha"`, the tool compares the cached
source against the working-tree git HEAD slice of the same file and
returns one of `git_sha_match`, `git_sha_mismatch`, or
`git_unavailable` in a new `git_sha_verification` field.

This addresses F-09: the default mode is self-referential and only
catches incoherent tamper of `~/.code-index/<repo>/`; the new mode is
externally attested and catches divergence between the cache and the
upstream source. Available alongside the existing cache-only mode, not as
a replacement.

`SECURITY.md` updated with the verification-mode documentation.

6 new regression tests in `tests/test_git_sha_verification.py` cover the
match / mismatch / unavailable / out-of-bounds branches against a real
ephemeral git repo. Skipped on environments without `git` on PATH.

### Sigstore release signing (P1.8)

New GitHub Actions workflow (`.github/workflows/sign-release.yml`) signs
the wheel + sdist attached to a GitHub Release with `sigstore-python`
and uploads the `.sigstore` bundles back to the release as additional
assets. Triggered on `release.published` so the maintainer's existing
`gh release create` flow gains forward-only signature coverage with no
upload pattern change.

Trust shape: GitHub Actions OIDC identity for the workflow file in this
repo signs to Sigstore's public-good transparency log. Verification ties
each artifact back to the workflow that signed it. Same shape PyPI's
PEP 740 attestation pipeline uses, layered on top of the existing
GitHub-release distribution channel.

`SECURITY.md` documents the verification recipe for downstream
consumers. Forward-only — releases prior to this change don't carry
signatures and aren't going to be retroactively re-signed.

### Tests

```
tests/test_credentials.py                  8 passed
tests/test_cache_mode.py                   4 passed
tests/test_init_minimal.py                 3 passed
tests/test_summarize_from_docstrings.py    5 passed
tests/test_git_sha_verification.py         6 passed (skipped without git)
full suite                              4400+ passed
```

## [1.108.21] - 2026-05-22 - explicit telemetry opt-out lever, speedreview Action pinned, docs hardening

Patch release covering three additive enterprise-hardening items.

### Explicit `share_savings` opt-out lever

New flag `--share-savings on|off` (and shorthand `--no-share-savings`) on both
`jcodemunch-mcp init` and `jcodemunch-mcp install <agent>`. When passed, writes
`"share_savings": <value>` explicitly into `~/.code-index/config.jsonc` before
any other init step runs, so the preference is durable even if the rest of
init is aborted partway through. Survives `jcodemunch-mcp config --upgrade`
across package upgrades; new regression tests in `tests/test_share_savings.py`
lock the invariant.

Default behavior is unchanged. The `share_savings` counter remains the primary
visibility we have into adoption growth. The new flag exists to give the
security-conscious a documented, durable opt-out without editing JSON. For
managed-endpoint deployments, the recommended posture is to set
`"JCODEMUNCH_SHARE_SAVINGS": "0"` in the MCP server env block in `.mcp.json` or
`claude_desktop_config.json` (lives in source control, survives any config-file
state).

New helpers in `config.py`:
- `set_bool_key(content, key, value)` — regex-based mutator that handles the
  three input shapes (commented template form, existing active form, absent
  key) idempotently. Reusable for future boolean config keys.
- `apply_share_savings(value, storage_path=None)` — end-to-end writer that
  creates `config.jsonc` from the template if missing, then sets the key.

### Speedreview Action: pinned package versions, tagged usage pattern

The `speedreview/action.yml` `pip install` line previously installed
`jcodemunch-mcp` and `openai` unpinned. Two changes:

- New action inputs `jcodemunch_version` (default `==1.108.21`) and
  `openai_version` (default `>=1.50,<2`) so the package versions are pinned
  by default and overridable per-workflow.
- README and `speedreview/README.md` updated to recommend `@v1.108.21` (or a
  commit SHA for stricter supply-chain hygiene) instead of `@main`. The bare
  `@main` form is no longer recommended for production workflows.

### Documentation hardening pass

`SECURITY.md` gained three new sections:

- **Files this server treats as security-sensitive** — explicit catalog of
  user-writable files that participate in the server's trust chain
  (`~/.code-index/config.jsonc`, generated MCP client configs,
  `~/.claude/settings.json` hooks, `.github/hooks/hooks.json`, agent-policy
  files like `CLAUDE.md` / `AGENTS.md`).
- **Persistent processes installed by `watch-install`** — the exact systemd
  unit name (Linux), LaunchAgent plist (macOS), and Task Scheduler entry
  (Windows) so endpoint-management hunts can map them.
- **Cache integrity verification modes** — documents the limitation of the
  default `verify=True` mode (self-referential against the local cache) and
  references the externally-attested mode on the near-term roadmap.

`README.md` install section gained an extras matrix that lists which system
surfaces each `[extra]` pulls in: notably `[groq-voice]` brings `sounddevice`
+ `numpy` (microphone access), `[groq-explain]` brings `Pillow` (image
decode), and `[all]` bundles both. Useful for managed-endpoint deployments
where audio / camera access on developer machines is policy-restricted.

`CONFIGURATION.md` "Privacy and telemetry" section expanded to document the
three durable `share_savings` opt-out paths. New "Cross-repo traversal"
section explicitly warns about data-mingling when `cross_repo_default=true`
is set in a deployment with both first-party and third-party indexed repos.

### Tests

```
tests/test_share_savings.py            8 passed
full suite                          4400+ passed
```

## [1.108.20] - 2026-05-19 - watcher fast-path applies all discovery filters via shared helper (#306)

Patch release. Filed as a follow-up audit to #300/#1.108.19 after
@domis86 noted that other discovery filters might also be "forgotten"
during watcher reindex.

## The audit

`v1.108.19` fixed `extra_ignore_patterns` on the watcher fast path.
That fix raised the question: which *other* filters from
`discover_local_files` does the fast path also skip? The inventory:

| Filter                                  | Full walk | Fast path (before 1.108.20) | Risk |
|-----------------------------------------|-----------|-----------------------------|------|
| `extra_ignore_patterns`                 | yes       | yes (v1.108.19)             | resolved |
| `.gitignore` (root-level)               | yes       | **no**                      | High |
| File size cap (`max_size`)              | yes       | **no**                      | Medium |
| Symlink protection (`follow_symlinks`)  | yes       | **no**                      | Medium |
| Symlink escape (`is_symlink_escape`)    | yes       | **no**                      | Medium |
| `skip_dirs_regex` (node_modules, etc.)  | yes (dir prune) | **no**                | Low/Medium |
| `SKIP_FILES_REGEX` (lockfiles, *.pyc)   | yes       | partial (via ext check)     | Low |
| Secret-file detection                   | yes       | **no**                      | adjacent |
| Binary-file content sniff               | yes       | **no**                      | adjacent |

High-impact gap: `.gitignore`. A user with a `build/` line in
`.gitignore` would see `build/` cleanly absent from the initial index,
then watch the watcher re-index `build/artifact.py` on the next save.

## The fix

Single helper `_should_index_file(file_path, cfg, gitignore_specs)`
holds every per-file filter check. Both code paths call it:

- `discover_local_files` (full walk) — loops files in each directory,
  hands them to the helper. `gitignore_specs` grows as `os.walk`
  descends (per-subdir `.gitignore` files), matching prior behaviour
  bit-for-bit.
- Watcher fast path in `index_folder` — pre-loads the root-level
  `.gitignore` once at fast-path entry and passes it to the helper for
  each changed file. Deletions bypass the filter set (a file indexed
  before its ignore rule existed should still be removed when deleted).

Documented tradeoffs on the fast path, to preserve the ~50ms per-event
latency goal:

- Only the **root-level** `.gitignore` loads on the fast path. Nested
  per-subdir `.gitignore` files are honoured by the full walk only.
  The fast path covers the common case (one `.gitignore` at repo root);
  monorepos with `cap/.gitignore` + `core/.gitignore` should re-run a
  full index after editing a nested `.gitignore`.
- `package.json` forced-path exemption from the size cap (issue #25)
  is skipped on the fast path. The initial full walk still applies it;
  subsequent fast-path edits to a forced file may hit the size cap.
- `is_binary_file` content sniff is disabled on the fast path because
  the next step reads the file bytes anyway — a second open for
  sniffing is wasteful. Extension filtering already rejects most
  binaries.

## Lock-in

The recurring pattern (filter applied on one code path but not
another) has now bitten three releases — v1.95.1 collision guards,
v1.96 incremental save merge, and now #306. The lock-in mechanism:

- All per-file filtering goes through `_should_index_file`. Adding a
  new filter is a single-edit operation; both code paths pick it up.
- Five regression tests in `TestFastPathHonorsAllDiscoveryFilters`
  cover gitignore + size cap + skip-dirs + symlink + deletion-bypass.
  Future filters should add a matching fast-path test alongside the
  helper edit.

## Files

- `src/jcodemunch_mcp/tools/index_folder.py` — new `_IndexFilters`
  dataclass, `_build_index_filters` factory, `_should_index_file`
  helper. `discover_local_files` inner loop refactored to call the
  helper. Watcher fast path builds the bundle once at entry and routes
  each non-delete event through the helper.
- `tests/test_watcher_memory_cache.py` — new
  `TestFastPathHonorsAllDiscoveryFilters` class, 5 tests.

5 new tests; 232 passed across the indexing surface; no behaviour
change for users on the full-walk path.

## [1.108.19] - 2026-05-19 - watcher fast-path honors `extra_ignore_patterns` (#300 follow-up)

Patch release. Reported by @domis86 on #300 after v1.108.18.

## The bug

A file under an `extra_ignore_patterns` prefix was correctly skipped on
the initial full-walk index, but on the next `modify` event the watcher
re-indexed it. Verified via repro:

```jsonc
// .jcodemunch.jsonc
{ "extra_ignore_patterns": ["docs/legacy/"] }
```

1. Initial `jcodemunch-mcp index` → `docs/legacy/...` correctly skipped.
2. `jcodemunch-mcp watch ./` running.
3. Edit `docs/legacy/.../File123.php`.
4. Watcher detects change, re-indexes → file IS now in the index.

Root cause: the watcher fast path in `index_folder` skips
`discover_local_files` (the function that builds the `pathspec`
ignore-filter) for performance. Only the language-extension check ran
in the classification loop; `extra_ignore_patterns` never got applied.

## The fix

In the watcher fast path (`if changed_paths and incremental:` branch),
compute `effective_extra` + `_fast_extra_spec` up front, then filter
matching files out of the classification loop. "Deleted" events are
let through unchanged: if a file is in the index from before the
pattern was set, deleting from disk should still remove it from the
index.

Same `get_extra_ignore_patterns(..., repo=str(folder_path))` call used
on the full walk, so project-level patterns from `.jcodemunch.jsonc`
work consistently across both paths.

## Tests

3 new regression tests in `tests/test_watcher_memory_cache.py
::TestFastPathExtraIgnorePatterns`:
modified-file-under-ignore-stays-unindexed, added-file-under-ignore-
stays-unindexed, modified-file-outside-ignore-still-indexed.

Full suite: 4429 passing.

## Follow-up audit

@domis86's "note 2" asked whether other config options might also be
forgotten on the watcher fast path. Audit answer: yes, several
(`.gitignore` per-dir specs, file size cap, symlink protection,
`SKIP_FILES_REGEX`, etc.). Tracked separately at #306 along with the
shared-helper refactor that would prevent this class of bug going
forward. This release fixes the user-reported case
(`extra_ignore_patterns`) in isolation; the broader audit lands in a
future minor.

## [1.108.18] - 2026-05-17 - summarizer runtime honors project config (#304)

Patch release. Closes #304 (the runtime gap I flagged when shipping
v1.108.17's display fix).

## The gap

`batch_summarize.py` read every summarizer-related config key from
`_GLOBAL_CONFIG` only (no `repo=` passed). So a user with
`summarizer_model` in `.jcodemunch.jsonc` saw the runtime ignore it —
the AI summarizer used the env-var fallback or hardcoded default
instead of their configured value. Same plumbing-audit shape as
#300 / #301 but in summarizer code.

## The fix

Thread `repo` from caller down to provider `__init__` so every
`_config.get(...)` call passes through the project-aware path:

```
summarize_symbols(symbols, use_ai, repo=<source_root>)
  └─ _create_summarizer(repo)
       ├─ _config.get("use_ai_summaries", repo=repo)
       ├─ _config.get("summarizer_provider", repo=repo)
       ├─ get_provider_name(repo=repo)
       ├─ get_model_name(repo=repo)
       └─ BatchSummarizer(repo=repo)  (or Gemini/OpenAI variant)
            └─ _config.get("summarizer_model", repo=self.repo)
            └─ _config.get("allow_remote_summarizer", repo=self.repo)
            └─ _config.get("summarizer_max_failures", repo=self.repo)
```

`BaseSummarizer` gained a `repo: Optional[str] = None` dataclass field
that all three concrete provider subclasses (Anthropic, Gemini,
OpenAI-compatible) inherit and pass to their config reads. The
`_make_openai_compat` factory also threads `repo` through to the
underlying `OpenAIBatchSummarizer` constructor.

Caller updates:

- `tools/_indexing_pipeline.py`: `deferred_summarize`,
  `parse_and_prepare_incremental`, `parse_and_prepare_full` now pass
  `repo=` (already had a `repo` parameter, just needed to forward it).
- `tools/index_folder.py`: two `summarize_symbols` call sites + the
  `deferred_summarize` daemon thread now pass `str(folder_path)`.
- `tools/summarize_repo.py`: passes `getattr(index, "source_root", None)`.

Defaults to `None` everywhere, so callers without a repo context keep
pre-#304 global-only behavior (no surprise regression for tests or
custom integrations).

## Display follow-up

v1.108.17 added a "project value not honored by runtime — see #304"
warning to `config --check` for the project-only `summarizer_model`
case. That warning is now obsolete: the display reverts to a clean
`[project]` source tag since the runtime actually honors the override.
The provider-specific `ANTHROPIC_MODEL` / `GOOGLE_MODEL` /
`OPENAI_MODEL` rows also pick up the project-aware value.

## Tests

5 new regression tests in `tests/test_summarizer.py::TestProjectAwareSummarizer`:
`get_model_name`/`get_provider_name` return project values with
`repo=` and global values without, `BatchSummarizer(repo=)` picks up
project `summarizer_model`, `_create_summarizer(repo=)` threads `repo`
through end-to-end, and the no-repo path keeps the pre-#304 global-only
contract. Updated 1 existing test in `TestSummarizerModelDisplay` whose
"runtime warning" assertion was specific to the v1.108.17 transition
state and is now obsolete.

23 unrelated tests in `test_summarizer.py` had lambda mocks shaped
`side_effect=lambda k, d=None: ...` that broke on the new `repo=`
kwarg; all updated to `lambda k, d=None, **kwargs: ...` for forward
compat. No behavior change in those tests.

Full suite: 4426 passing.

## [1.108.17] - 2026-05-17 - `config --check` reflects `summarizer_model` (#300 follow-up; #304 filed)

Patch release. Surfaced by @slazarov on #300: setting
`summarizer_model: "Qwen3.6-Plus"` in `.jcodemunch.jsonc` and running
`config --check` printed `OPENAI_MODEL qwen3-coder (default)`. The
display only consulted the `OPENAI_MODEL` env var and a hardcoded
fallback; it never looked at the `summarizer_model` config key that the
runtime summarizer actually reads.

Two display fixes:

1. **New `summarizer_model` row in the AI Summarizer section.** Reads
   `_GLOBAL_CONFIG["summarizer_model"]` directly (bypassing the v1.108.15
   project-aware shim) so the displayed value matches what the runtime
   actually sees. Source tag is `[config]` / `[env]` / `[default]` as
   appropriate.
2. **Provider-specific MODEL rows now consult `summarizer_model` first.**
   `ANTHROPIC_MODEL`, `GOOGLE_MODEL`, `OPENAI_MODEL` (for openai-compatible,
   minimax, glm, openrouter) display the configured `summarizer_model`
   when set, mirroring `batch_summarize.py`'s resolution order:
   `summarizer_model` config > provider env var > hardcoded default.

Honesty gate: project-level `summarizer_model` (set in
`.jcodemunch.jsonc` only, not in global `config.jsonc`) is NOT honored
by the runtime today — every `_config.get("summarizer_model")` call
in `batch_summarize.py` passes `repo=None`, so project configs are
silently dropped. The display now surfaces a yellow warning row when
this case is detected, pointing at #304 (filed) for the runtime fix.
Without that honesty signal, the v1.108.15 project-aware shim would
show "Qwen3.6-Plus [project]" while the actual summarizer used the
default — the worst kind of misleading diagnostic.

3 new regression tests in `TestSummarizerModelDisplay`:
global-value-shows-in-row, project-only-shows-runtime-warning,
OpenAI provider row reflects configured summarizer_model. Full suite:
4421 passing.

## [1.108.16] - 2026-05-17 - resolve_repo: provisional id bypass + `_read_origin_url` hardening (#303 follow-up)

Patch release. @rknighton rebuilt the original 130-worktree fixture and
validated against v1.108.15. The earlier fix handled the O(N) common-dir
scan but missed an earlier step: when `git_root_identity=true` is
configured, the provisional repo_id computation still routed through
`resolve_index_identity` → `detect_git_root` → `_read_origin_url`,
spawning `git config --get remote.origin.url`. That subprocess hung in
the reporter's environment, so the cold-start `resolve_repo` against an
unindexed worktree path still timed out.

Stack trace (from his report):

```
resolve_repo
  → _compute_repo_id
  → resolve_index_identity
  → detect_git_root
  → _read_origin_url
  → subprocess.run(["git", "config", "--get", "remote.origin.url"])
```

Two fixes:

1. **Canonical-candidate discovery now runs BEFORE the slow path.**
   Previously the order was: fast paths (exact source_root, containment)
   → slow path (compute repo_id, inspect store) → canonical discovery.
   Now: fast paths → canonical discovery → slow path. For worktree-of-
   indexed environments (the reporter's case), this hits a new
   `match_path: "canonical_candidate_fast"` return that uses a cheap
   local provisional repo_id and skips all git-identity probing.

2. **`_local_provisional_repo_id` helper** for the not-indexed final
   branch. Returns `local/<basename>-<hash>` via direct call to
   `_local_repo_name`, bypassing `resolve_index_identity` entirely. Real
   indexed repo IDs continue to be surfaced via `canonical_candidates`;
   the provisional `repo` value is descriptive, not authoritative.

3. **`_read_origin_url` defensive hardening** (defense-in-depth):
   `stdin=subprocess.DEVNULL` so the call can never block on stdin, plus
   env-neutralisation matching `_git_toplevel` (`GIT_CONFIG_NOSYSTEM=1`,
   `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`). Benefits all
   callers of this function (not just resolve_repo), including the
   indexing flow.

3 new regression tests in `tests/test_resolve_repo.py`:
`TestResolveRepoCanonicalCandidateFastPath` covers the worktree
fast-path return + the no-`remote.origin.url`-call invariant; `Test
ReadOriginUrlHardening` covers the stdin/env defensive posture. Full
suite: 4418 passing.

The `#277` worktree-canonical discovery shape is preserved; existing
`TestWorktreeCanonicalCandidates` tests pass unchanged. Wire change:
worktree-of-indexed responses now carry `_meta.match_path:
"canonical_candidate_fast"` instead of `"not_indexed"`. Additive.

## [1.108.15] - 2026-05-17 - `config --check` honors `.jcodemunch.jsonc` (#300 follow-up)

Patch release. Surfaced by @slazarov as a follow-up to #300 on v1.108.14.

`config --check` already validated `.jcodemunch.jsonc` (per the v1.108.12
fix that closed #300), but the rest of the configuration printout still
read `_GLOBAL_CONFIG` only. So a user with a project override would see:

```
Checks
  ✓ config.jsonc valid: ~/.code-index/config.jsonc
  ✓ .jcodemunch.jsonc valid: <project>/.jcodemunch.jsonc
```

while the displayed config rows above the Checks section showed the
global values, not the merged project-overridden ones. The validation
passed but the diagnostic answer ("what config will actually apply if I
run from here?") was silently wrong.

Fix: `_run_config` now probes cwd for `.jcodemunch.jsonc` at entry, calls
`load_project_config()` to populate `_PROJECT_CONFIGS[cwd]`, and routes
all 33 `_cfg.get()` display calls through a lightweight project-aware
shim that injects `repo=cwd` when callers don't pass one. The shim
delegates everything else to the real config module so the surface is
unchanged. Source detection (`_detect_source`) gains a "project" tier
that takes precedence over "config" / "env" / "default"; overridden
rows now read `[project]` instead of `[config]`.

The "Config File" section also reports project-config status:

```
Config File
  ✓ config.jsonc found: ~/.code-index/config.jsonc
  ✓ .jcodemunch.jsonc loaded from cwd: <project>/.jcodemunch.jsonc (N key(s) override global)
```

So a glance answers the previous question without reading the full table.

3 new tests in `tests/test_config.py::TestConfigDisplayHonorsProjectOverride`:
project override visible in output with `[project]` tag, Check section
reports project config loaded, no-project-file path stays on global-only
behavior (pre-fix bytes for users without a `.jcodemunch.jsonc`). Full
suite: 4415 passing.

## [1.108.14] - 2026-05-17 - resolve_repo perf at scale (#303)

Reported by @rknighton with a complete root-cause analysis, local patch,
and before/after timings. About 130 git worktrees and ~40 index DBs on
Windows; `resolve_repo` on an unindexed agent worktree path timed out
past 120s, falling agents back to broad local filesystem reads instead
of jCodeMunch.

Two cost sinks fixed:

1. **`_find_canonical_candidates` spawned one `git rev-parse` subprocess
   per indexed repo.** At 40 indexes on Windows that's 4-8s of process
   churn before any work happens. Replaced with `_git_common_dir_cheap`,
   which reads `.git` / `commondir` files directly:

   - Main checkout: `.git` is a directory; that's the common-dir.
   - Linked worktree (`git worktree add`): `.git` is a pointer file
     containing `gitdir: <path>`; the pointed-to directory's `commondir`
     file points back to the canonical `.git`. We follow that chain in
     pure-Python filesystem reads.
   - Submodule: `.git` pointer file with no `commondir`; the gitdir
     itself is treated as the common-dir.

2. **`_compute_repo_id(candidate, store=store)` walked the whole index
   store via `_existing_git_identity` for path-containment checks** on
   every candidate, even when a cheap source_root match would have
   answered the question. Added two fast paths that run before the
   legacy compute-then-inspect path:

   - **Exact source_root match.** Pre-fetch `store.list_repos()` once,
     return the indexed entry directly when the input path equals an
     indexed `source_root`.
   - **Source_root containment.** If the input path is under an indexed
     `source_root`, return that entry. Deepest match wins when multiple
     parents are indexed.

   For the not-indexed final case, `_compute_repo_id` is now called
   without `store=`, which skips the O(indexes) walk for the
   provisional repo-id computation.

Validation (reporter's environment, ~40 indexes / ~130 worktrees):

- Before: `resolve_repo(agent worktree path)` timed out after 120s.
- After:  `resolve_repo(canonical checkout)` ~0.095s,
          `resolve_repo(agent worktree path)` ~0.149s.

Wire shape: response now includes `_meta.match_path` indicating which
fast path served the result (`exact_source_root` | `source_root_containment`
| `computed_repo_id` | `not_indexed`). Additive; no other response
fields changed.

10 new regression tests in `tests/test_resolve_repo.py`:
`TestGitCommonDirCheap` (6: main checkout, linked worktree pointer
chain, submodule layout, non-git path, malformed pointer, empty pointer)
and `TestResolveRepoFastPaths` (4: exact source_root, source_root
containment, not_indexed match_path tag, no-git-subprocess invariant
on the fast path). Full suite: 4412 passing.

Worktree-canonical discovery (#277) behavior preserved bit-for-bit;
the existing `TestWorktreeCanonicalCandidates` tests pass unchanged.

## [1.108.13] — 2026-05-15 — project-config plumbing audit (#301)

Follow-up to #300. The audit issue asked: how many other `_config.get()`
call sites under `src/jcodemunch_mcp/tools/` have the same bug shape
(reading a project-overridable key without passing `repo=`, so
`.jcodemunch.jsonc` overrides are silently dropped)?

Audit scope was tools/*.py only, only `_config.get()` reads at tool
execution time. ~14 call sites surveyed; 9 were bugs of the #300 shape,
4 were correctly global-by-design (now commented as such in code), 2
were already correct.

**Bugs fixed (9 sites, 5 files):**

- `index_folder.py:875` `redact_source_root` — per-repo privacy
  preferences now honored.
- `index_folder.py:1321` `gitignore_warn_threshold` — monorepo and
  small-repo can set different thresholds.
- `index_folder.py:1343` `context_providers` — per-repo toggle for the
  dbt / terraform discovery.
- `index_folder.py:1347` `is_language_enabled("sql")` — per-project
  language gating now reaches the SQL-provider filter.
- `index_file.py:151` `context_providers` (same fix).
- `index_file.py:155` `is_language_enabled("sql")` (same fix).
- `get_repo_outline.py:167` `staleness_days` — per-repo freshness
  expectations now honored.
- `get_file_tree.py:42` `file_tree_max_files` — per-repo result caps.
- `search_columns.py:53` `max_results` — per-repo result caps.

All 9 sites now pass `repo=` (folder path, source root, or repo
identifier as appropriate). Each fix carries a `Project-overridable (#301)`
inline comment marking the audit decision; future contributors editing
these sites have explicit signal that the `repo=` is load-bearing.

**Global-by-design (4 sites, commented):** `embed_repo.py:39`
(per-project embedding models break cross-project semantic search),
`render_diagram.py:1104` and `mermaid_viewer.py:96` (server-level
viewer wiring), `test_summarizer.py:45` (diagnostic for global state).
These carry a `Global-only by design (#301)` comment so they don't get
mistakenly "fixed" later.

**Regression test:** `test_project_config_override_threshold_suppresses_warning`
in `tests/test_tools.py` proves the project-config override flows
through `index_folder` to the `gitignore_warn_threshold` call site.
One test covers the audit pattern; the remaining 8 fixes are mechanical
applications of the same shape and rely on the inline `(#301)` markers
plus the established #300 pattern for code-review catch.

## [1.108.12] — 2026-05-15 — `.jcodemunch.jsonc` extra_ignore_patterns now honored (#300)

Reported by **@domis86** in #300 with a clean Claude-assisted diagnosis.

**The bug:** A project-level `.jcodemunch.jsonc` with
`"extra_ignore_patterns": ["docs/legacy/"]` was loaded into
`_PROJECT_CONFIGS` correctly but never read by the indexer. The discovery
walk would log `ACCEPT: docs/legacy/...` for every file the user expected
to be filtered. The pattern was silently dropped.

**Root cause:** `get_extra_ignore_patterns()` in `security.py:337` called
`_config.get("extra_ignore_patterns", [])` without a `repo=` argument.
`config.get(key)` falls back to `_GLOBAL_CONFIG` when no repo is supplied,
so the project-level merged value was never consulted. The single
caller in `index_folder.py:585` had the resolved folder path on hand but
wasn't forwarding it.

**Fix:**
- `get_extra_ignore_patterns()` now accepts a `repo=` argument and
  forwards it to `_config.get`. Pre-#300 callers (no `repo=`) get
  identical behavior; the new path activates when callers thread the
  folder path through.
- `discover_local_files()` in `index_folder.py` now passes
  `repo=str(folder_path)` so `.jcodemunch.jsonc` overrides land at the
  consumption site.
- `jcodemunch-mcp config --check` now probes cwd for `.jcodemunch.jsonc`
  and validates it if present. domis86's "no mention = red flag"
  diagnostic gap closed: users editing the project config now see
  positive confirmation it parses (or what's wrong if it doesn't).
- 2 new regression tests in `tests/test_security.py` covering both the
  unit-level `repo=` plumbing and the full `discover_local_files`
  integration path.

**Workarounds for users on older versions** (per the issue):
- Pass `extra_ignore_patterns` to `index_folder` explicitly each call.
- Or set `JCODEMUNCH_EXTRA_IGNORE_PATTERNS` env var.
- Or move the patterns to `~/.code-index/config.jsonc` (affects every repo).

## [1.108.11] — 2026-05-15 — drift cleanup + Windows-CI test hardening

Docs / hygiene patch. No behavior change.

- **Config template:** `list_workspaces` (added v1.108.0) was missing
  from the `all_tools` list embedded in the generated `config.jsonc`
  template. New users running `jcodemunch-mcp init` will now see it
  alongside the other disable-able tools.
- **Windows CI flake:** `test_wait_seconds_polls_until_lock_released`
  (`tests/test_process_locks.py:194`) hardened to absorb GitHub Actions
  Windows-runner jitter. The release-thread `time.sleep(0.3)` was a
  floor, not a ceiling, and contended runners would stretch it past the
  2.0s elapsed-assertion ceiling. Bumped to 0.5s release / 3.0s ceiling;
  the underlying lock-semantics contract is unchanged.
- **README recency block:** regenerated via `jcodemunch-mcp whatsnew`
  to reflect v1.108.7 through v1.108.11 (was stuck on v1.91/v1.92/v1.93
  from 2026-05-09).

## [1.108.10] — 2026-05-15 — schema budget cleanup (post-v1.108.0/v1.108.6 drift)

Schema-budget regression caught after v1.108.9 ship. `core_compact` had
drifted from 3978 to 4180 tokens, breaking `test_schema_tokens_within_baseline_tolerance`
(5% drift guardrail) and putting the v2 success criterion (`core_compact <= 4000`)
out of reach.

Diagnosis: 95% of the +202-token drift came from `index_folder`, which
grew from 213 to 406 tokens between v1.108.0 (added the `paths` arg) and
v1.108.6 (added the `identity_mode` arg). The new arg descriptions had
absorbed use-case prose ("e.g. source files git just touched, the
changeset for a PR, or an rg / fd match list") that belongs in README,
not in tool schema — every MCP client paid that token cost on every
`tools/list` call.

Trims:
- `index_folder` and `index_file` descriptions: dropped diagnostic-hint
  prose and the env-var name list from `use_ai_summaries` (provider list
  is config detail, not call-time contract).
- `paths` arg: dropped the use-case bullet list; kept the contract
  ("skips the walk; directories recurse; walk-path validation applies").
- `identity_mode` arg: condensed the three-mode description.
- `extra_ignore_patterns`, `follow_symlinks`, `incremental`: minor
  tightening.

Result: `core_compact` is 3977 tokens (one below the original 3978
baseline, and 23 below the v2 success-criterion line). Other profiles
also shrunk; baseline updated for all six (`schema_baseline.json`).

No behavioral changes. No tool signatures changed. Pure description
shrinkage.

## [1.108.9] — 2026-05-14 — escape hatch for the `_UNDISABLEABLE_TOOLS` safety net (#299)

Requested by **@kecsap** in #299, as the follow-up offered when shipping #298.

The default `_UNDISABLEABLE_TOOLS = {set_tool_tier, announce_model}` guard
exists so users can't lock themselves out of in-session tier switching. For
users running against a hard tool-count cap (e.g. Antigravity's 50-tool
limit), those two slots are valuable enough to be worth giving up tier
switching for.

New config key:

```jsonc
"allow_disabling_tier_controls": false  // default
```

When set to `true`, `set_tool_tier` and `announce_model` may appear in
`disabled_tools` and will be removed from the schema (or rejected at
call-time for project-level disabling). Default `false` preserves
pre-1.108.9 behavior bit-for-bit.

The check threads through both enforcement sites: `list_tools()` schema
filtering and `call_tool()` project-level rejection. Config template
gains a commented `allow_disabling_tier_controls` block with the
Antigravity rationale.

4 new tests in `tests/test_server.py` covering both flag states across
both enforcement paths.

## [1.108.8] — 2026-05-14 — `jcodemunch_guide` honors `disabled_tools` (#298)

Reported by **@kecsap** in #298: listing `jcodemunch_guide` or `set_tool_tier`
in `disabled_tools` had no effect — both stayed in the initial MCP schema.

The force-include path in `_build_tools_list()` always re-added every member
of `_ALWAYS_PRESENT_TOOLS`, overriding the user's explicit opt-out.

Fix: split the always-present guarantee into two scopes.

- `_ALWAYS_PRESENT_TOOLS` (set_tool_tier, announce_model, jcodemunch_guide)
  still survives **tier filtering** — core/standard tiers keep all three.
- New `_UNDISABLEABLE_TOOLS` (set_tool_tier, announce_model) is the only set
  that survives **disabled_tools** — these are runtime tier controls and
  disabling them would lock the user out of switching tiers in-session.
- `jcodemunch_guide` is a documentation snippet, not a control surface, so
  it now honors `disabled_tools` for both schema visibility and call-time
  rejection. Same fix applied to the project-level `is_tool_disabled` check
  in `call_tool()`.

`set_tool_tier` and `announce_model` remain undisableable as before.

## [1.108.7] — 2026-05-12 — Windows hook path: forward slashes + path-shape-agnostic dedup

Two coupled bugs that produced the recurring `PreToolUse:Read hook error /
Failed with non-blocking status code: /usr/bin/bash: line 1:
C:Python314Scriptsjcodemunch-mcp.EXE: command not found` loop on Windows:

1. **`_hook_invocation()` wrote native-slash paths into settings.json.**
   `shutil.which("jcodemunch-mcp")` returns `C:\Python314\Scripts\jcodemunch-mcp.EXE`
   on Windows. JSON serialisation preserves the backslashes (`C:\\Python314\\...`),
   but Claude Code spawns the hook through bash, which treats every `\` as
   an escape character and silently eats them. The path becomes
   `C:Python314Scriptsjcodemunch-mcp.EXE` at execution time → "command not
   found." Fix: normalise to forward slashes on Windows. Forward slashes
   work in every Windows API that accepts a path (CreateProcess, PowerShell,
   Git Bash) and don't trigger bash escape parsing.

2. **`_merge_hooks()` substring marker missed absolute-path forms.** The
   marker `"jcodemunch-mcp hook-p"` failed to match
   `C:/.../jcodemunch-mcp.EXE hook-pretooluse` because `.EXE ` interrupts
   the substring. Every re-run of `init` thought it was a fresh install
   and appended a second copy, so settings.json accumulated duplicates.
   Fix: new `_extract_jcm_subcommand()` regex pulls the jcm subcommand
   out of any path shape (bare name, absolute, .EXE/.exe, slashes either
   way, quoted paths with spaces). `_merge_hooks` now compares
   subcommands instead of raw strings — bare and absolute forms of the
   same hook are recognised as the same hook.

Together these mean: re-running `jcodemunch-mcp init` on Windows now
produces working hook commands the first time, and subsequent re-runs
don't accumulate duplicates regardless of how `shutil.which` resolves
between invocations.

### Stats

- 19 new regression tests in `test_init_hooks_paths.py` covering both
  bugs across every path shape we've seen in the wild.
- Tool count: 81 (unchanged)

---

## [1.108.6] — 2026-05-12

### Restore local-first index identity as the default ([#295](https://github.com/jgravelle/jcodemunch-mcp/pull/295), @MariusAdrian88)

Reverts the v1.95 default of git-root identity. Local-folder indexing
now defaults to path-hash identity (`local/<basename>-<hash>`) again —
no `git` subprocess, no remote detection, works for non-git projects
and simple local clones. Git-root identity (with monorepo subdir
merging) becomes an explicit opt-in via the new `identity_mode: "git"`
config knob or per-call MCP argument on `index_folder`.

**No existing index is silently re-keyed.** A new `resolve_index_identity()`
helper checks the index store *before* any config decision:

- An existing `local/...` index for a path is returned as-is, even if
  the config says `git`.
- An existing `<owner>/<name>` git-keyed index that covers a path (via
  its stored `git_root`) is returned as-is, even if the config says
  `local`.
- Explicit conflicts (`identity_mode="local"` against an existing git
  index, or vice versa) raise `IdentityModeConflict` with a remediation
  hint pointing at `invalidate_cache`.
- The pathological "both forms exist" state raises
  `IdentityModeAmbiguous` rather than silently picking one.

Three duplicated identity-resolution paths (`_local_repo_id` in
`watcher.py`, `_compute_repo_id` in `resolve_repo.py`,
`_resolve_repo_identity` in `index_folder.py`) now all delegate to the
central helper. The `git_root` field on each index round-trips through
both the JSON and SQLite backends so the existing-git-index probe stays
cheap.

**New config keys** (`config.py` template):
- `identity_mode`: `"local"` (default) or `"git"`. Per-folder override
  via the same key with a `repo:` scope.
- `git_root_identity`: deprecated alias — `true` is equivalent to
  `identity_mode: "git"`. Kept for backwards compatibility.

**New MCP argument** on `index_folder`:
- `identity_mode`: `"config"` (default — consult the config),
  `"local"`, or `"git"`.

10 new tests in `tests/test_identity_mode.py` covering all four
preservation corners, conflict guards, the ambiguous-state guard, the
no-subprocess fast path, and config-template content. Existing
`test_git_root_identity.py` tests now pass `identity_mode="git"`
explicitly to reflect the opt-in shape.

### `check_delete_safe`: honest-hint caveat when no runtime data is ingested

Back-port of a UX pattern from the v1.6.0 sibling-parity work on
jdatamunch-mcp's `check_column_drop_safe`. When `check_delete_safe`
returns `safe_to_delete` *and* the operator hasn't opted out of the
runtime channel *and* the repo has no `runtime_calls` rows ingested,
the `recommended_action` now surfaces that fact:

> *No callers or refs found. Static signals only — no runtime traces
> ingested for this repo, so production traffic was not consulted.
> Run `import-trace` against representative traffic to strengthen this
> verdict.*

Previously the runtime channel just didn't fire in this state, which
made `safe_to_delete` implicitly claim coverage it couldn't actually
prove. A new `_runtime_data_present()` probe distinguishes "no traces
ingested" from "this symbol has zero hits in traces that exist."
`signals.runtime_data_present` is surfaced on every response (when
`include_runtime=True`) so callers can introspect.

When the operator explicitly passes `include_runtime=False`, the
caveat is suppressed — the signal there is "you asked us not to
check," not "we couldn't check."

3 new tests in `TestRuntimeDataCaveat`. The reverse direction
(`check_column_drop_safe` already does this in jData v1.8.0) was the
original source.

## [1.108.5] — 2026-05-12 — Watcher standby failover: second-server takeover when the lock releases

Multi-server bug fix from @MariusAdrian88 ([#293](https://github.com/jgravelle/jcodemunch-mcp/pull/293)).
Before this release, running two MCP servers against the same local repo
left only the first one watching: whichever process won the watcher lock
got to watch, and any server that lost the race stayed unwatched until
restart. The losing server's index drifted out of date silently, and the
choice of "which server has fresh data" depended on startup order.

### Fixed

- `WatcherManager` now tracks folders that failed lock acquisition in a
  `_standby` set and runs a per-folder signal-file watcher so standby
  managers can wake immediately when an active watcher releases its lock.
- `_release_lock()` writes a per-folder `.signal` file next to the lock
  on release. Standby managers `awatch()` the lock directory and react
  the moment a release happens, rather than polling.
- `_auto_watch_if_needed()` now attempts `maybe_takeover()` before
  falling through to normal reindex + watch. Tools dispatched against a
  standby folder pick up the lock opportunistically.
- After takeover, the auto-watch path awaits `ensure_indexed()` before
  returning so the tool call sees a fresh index, not a racing one.
- `maybe_takeover()` is throttled (1 s) and respects a configurable
  `_takeover_retry_seconds` fallback (default 30 s) so a stuck holder
  doesn't cause a hot loop.

### Hardened

- `remove_folder()` now clears any orphaned standby task even when the
  folder was never actively watched — previously a lock release could
  unexpectedly resurrect a standby that the caller had already removed.
- `stop()` now cancels active watch tasks and releases their locks
  instead of leaving them tracked indefinitely.
- `_start_watch_task()` extraction DRYs the watch-task setup across
  `add_folder`, `maybe_takeover`, and the crash-restart path.

### Tests

- New `TestWatcherSignalFile`, `TestWatcherManagerStandby`,
  `TestAutoWatchTakeover`, `TestWatcherStandbyFailover` suites covering
  signal-file payload, standby registration + cancellation, throttle,
  retry-interval, lock cleanup on `stop()`, and the full two-manager
  failover handoff. CI green on Linux + Windows × Python 3.10-3.13.

## [1.108.4] — 2026-05-12 — Universal prompt: short-circuit for known first-class targets

Follow-up to v1.108.3. The original `AGENT_INSTALL_UNIVERSAL.md` framing
omitted that `jcm install <client>` already covers Cursor, Windsurf, and
Continue at the guidance layer (writes `.cursor/rules/jcodemunch.mdc`,
etc.), not just Claude Code. Without a redirect, a Cursor user pasting
the prompt would run full environment discovery when a one-shot CLI
already does the job.

### Changed

- `AGENT_INSTALL_UNIVERSAL.md` gains a "Known first-class targets" section
  enumerating the CLI installers users should reach for first (Claude Code,
  Claude Desktop, Cursor, Windsurf, Continue).
- Step 1 of the prompt body now short-circuits: if the detected environment
  is one of the first-class targets, the agent halts and points the user at
  the CLI command instead of proceeding.
- Preamble clarifies the layer split — badges/CLI handle MCP server wiring;
  the universal prompt handles agent-side guidance — and explains that some
  users may need both (wiring + guidance) when their client isn't in the
  first-class table.
- README link description updated to call out which clients should use
  `jcm install <client>` instead.

Docs-only — no behaviour change in the CLI or MCP server.

## [1.108.3] — 2026-05-12 — Universal agent-installer prompt for non-Claude environments

Quick-win addition from @rknighton ([#292](https://github.com/jgravelle/jcodemunch-mcp/issues/292)):
ships `AGENT_INSTALL_UNIVERSAL.md`, a paste-and-go
prompt users can hand to any agent/IDE to discover its native instruction
mechanism and install jCodemunch (and jDocMunch) guidance there. The prompt
identifies the environment, checks whether file writes are allowed, preserves
existing user-authored instructions, respects the MCP-server-vs-skill
responsibility split, keeps the native-shell-and-file-read exceptions intact,
and emits a compatibility report users can paste into a follow-up issue.

This is documentation-only — no behaviour change, no new tools, no migration.
The compatibility reports drive demand signal for which client gets a
first-class installer next.

### Added

- `AGENT_INSTALL_UNIVERSAL.md` — environment-agnostic
  installer prompt for Codex, Cursor, Windsurf, Continue, Cline, JetBrains AI,
  and any other agent/IDE not yet covered by `jcm install --skills`.
- README pointer under the Documentation table.

## [1.108.2] — 2026-05-12 — Bound git-blame history walk + honour `git_root_identity=false`

Two regressions reported by @MariusAdrian88 ([#294](https://github.com/jgravelle/jcodemunch-mcp/issues/294))
that together explained the 25-30 s `index_folder` timeouts on his
local-only workflow.

### Fixes

- **`index_folder` now short-circuits the git-root probe when `git_root_identity` is false.**
  The retarget block at the standard-path entry called
  `detect_git_root()` (which spawns `git config --get remote.origin.url`)
  *before* checking the config knob — so operators who opted out of
  git-root identity still paid the probe cost on every reindex. The
  probe is now gated on the config first.
- **`GitBlameProvider.load()` no longer walks unbounded history.**
  The previous `git log --name-only` had no `-n` and no `--since` and
  a 30 s timeout, so on long-lived repos (hundreds of thousands of
  commits) it routinely consumed the entire MCP request budget. The
  walk is now capped at:
  - `-n 20000` commits
  - `--since=2.years.ago`
  - 10 s wall-clock subprocess timeout
  Files untouched in the window simply won't appear in the blame
  map — `get_file_context` returns None for them, the same behaviour
  as files outside a git working tree.

### New config knob

- **`git_blame_enabled`** (default `true`) — set `false` to skip the
  blame provider entirely on repos with very deep history where even
  the bounded walk is too slow. `git_blame` context is omitted from
  the index; everything else builds normally. Also surfaced as
  `JCODEMUNCH_GIT_BLAME_ENABLED` env var and documented in the
  JSONC config template.

### Notes
- Behavior change on long-lived repos: `last_author` / `last_modified`
  now reflect activity within the last 2 years (or 20 k commits)
  rather than all history. For files older than that window, both
  fields are absent from `FileContext` rather than reporting ancient
  attribution. If the older behaviour mattered for your workflow, the
  thresholds are exported as module-level constants
  (`GIT_BLAME_COMMIT_LIMIT`, `GIT_BLAME_SINCE`, `GIT_BLAME_TIMEOUT_S`)
  in `parser/context/git_blame.py` — and we'd want to hear about the
  use case.
- 8 new tests in `test_v1_108_2.py`. 4344 passed, 7 skipped.

## [1.108.1] — 2026-05-12 — Surface `git_root_identity` in the config template

`git_root_identity` was in `DEFAULTS`, the type-validation table, and had
an env-var alias (`JCODEMUNCH_GIT_ROOT_IDENTITY`), but was missing from
the self-documenting JSONC template — so operators had no way to discover
it without grepping source. Reported by @MariusAdrian88 (#291 comment).

The template now describes the knob, both states (true coalesces
`index ./packages` + `index ./scripts` into one repo index per clone;
false reverts to pre-v1.96 `local/<folder>-<hash>` per-path identity),
and when to flip it. No behavior change — pure documentation.

## [1.108.0] — 2026-05-12 — Explicit-paths indexing + workspace-aware project intel

Two additive features, both shipped behind new optional parameters; every
existing call shape is preserved.

### Explicit-paths indexing (`index_folder(paths=[...])`)

`index_folder` gains a `paths=[...]` parameter that bypasses the directory
walk and indexes only the listed files / subdirs. Each entry may be
absolute or relative to `path`. Useful for batch-indexing exactly the
files an agent already knows about — the changeset for a PR, the output
of `git diff --name-only`, an `rg` or `fd` match list — without paying
the cost (or risking the surprise) of a full-tree walk.

Validation matches the walk path: entries outside `walk_root`, traversal
attempts, symlink escapes, oversize files, and unsupported extensions all
warn-and-skip with per-entry messages in `warnings`. Directories in the
list are expanded via `discover_local_files` so the same .gitignore /
framework filter applies.

CLI: new `--paths-from FILE` flag on `jcodemunch-mcp index`. Use `-` for
stdin to compose with `git`, `find`, `fd`, `rg`:

```bash
git diff --name-only HEAD~5 -- '*.py' \
  | jcodemunch-mcp index . --paths-from -
```

Lines starting with `#` are comments. Empty input is a hard error so the
command doesn't silently fall through to a full-tree index. Rejected for
GitHub-repo targets (only local folders).

### Workspace-aware project intel (`list_workspaces`, `get_project_intel(scope_path=)`)

Monorepos increasingly mix multiple tech stacks under one git root —
`packages/api` is a Node service, `packages/cli` is a Rust binary,
`crates/foo` is a library, `go.work` ties three modules together. Until
now, `get_project_intel` returned a repo-wide aggregate; agents asking
about `packages/api` got the whole monorepo in their face.

**New `list_workspaces` tool** auto-detects workspace members from:
* **pnpm** — `pnpm-workspace.yaml` `packages:` globs
* **yarn / npm** — `package.json` `workspaces:` (list or {packages: [...]} form)
* **turborepo** — `turbo.json` signal layered over the underlying npm/yarn/pnpm config
* **lerna** — `lerna.json` `packages:` globs
* **rush** — `rush.json` `projects: [{packageName, projectFolder}, ...]`
* **Go** — `go.work` `use ( ... )` directive (module name read from each `go.mod`)
* **Cargo** — `Cargo.toml` `[workspace] members = [...]` globs

Returns `[{path, package_name, manager}, ...]` plus an `is_monorepo`
flag and the list of contributing managers. When the same path is
claimed by multiple managers (turborepo + pnpm is common), the
more-specific manager wins; `managers` surfaces every contributor.

**`get_project_intel(scope_path=...)`** restricts intel discovery to a
relative subpath under `source_root`. The cross-reference pass still
consults the global index, so a package's Dockerfile still resolves
against repo-level symbols. Combined with `list_workspaces`, agents can
now ask "what's the deployment story for `packages/api`?" and get just
that package's Dockerfile, CI workflows, `.env` template, and
`package.json` scripts — not the whole monorepo.

`scope_path` is validated against `source_root` (traversal attempts,
absolute paths outside the root, and non-existent directories all
error rather than silently fall through to a wider scan).

### Notes
- Both changes are additive — every existing call signature works
  unchanged. New params default to None / repo-wide behaviour.
- Tool count: 80 → 81 (added `list_workspaces`).
- `list_workspaces` joins the `standard` tool tier; `get_project_intel`
  was already in `standard`.
- 16 new tests in `test_v1_108_0.py`. 4336 passed, 7 skipped.

## [1.107.1] — 2026-05-11 — Truthful unloadable-index reporting

Fixes a real UX trap where `resolve_repo()` could report `indexed: true`
when only an index artifact existed on disk but could not be loaded by
query tools — so discovery said the repo was available while every
follow-up tool failed with a generic "Repository not indexed" message.

### What this fixes

- `IndexLoadStatus` + `inspect_index()`: read-only probe that classifies
  present-but-unloadable indexes (`sqlite_missing_meta`,
  `sqlite_corrupt`, `sqlite_future_version`, `json_invalid`) without
  mutating the existing `load_index()` contract.
- `resolve_repo()`: `indexed` now means queryable. Diagnostics
  (`index_present`, `loadable`, `status`, `load_error`, `backend`,
  `hint`) surface on the response when an index exists but is broken.
- `list_repos()`: damaged SQLite indexes stay visible with
  `loadable: false`; a valid legacy JSON fallback is recovered and
  migrated back to SQLite rather than masked by the broken DB.
- ~25 tools migrated to a shared `index_status_to_tool_error` helper —
  generic "not indexed" responses replaced with actionable
  unloadable-index errors that include the remediation hint.
- Regression coverage for corrupt metadata, future versions, corrupt
  SQLite files, JSON fallback recovery, and `list_repos` sort stability
  with damaged entries.

Contributed by @MariusAdrian88 (#291).

## [1.107.0] — 2026-05-11 — Claude Agent Skill bundle

Emits a `.claude/skills/jcodemunch/SKILL.md` bundle in the Claude Agent
Skill format. Claude loads the skill on demand for code-navigation tasks
instead of carrying the equivalent guidance in baseline context every
turn — saving tokens on every session that doesn't actually need the
detailed decision tree.

### What this adds

- `jcm install <agent> --skills` emits the skill bundle alongside the
  existing CLAUDE.md preamble, MCP config, and hooks. `--skills-scope
  global` (default) writes to `~/.claude/skills/jcodemunch/`;
  `--skills-scope project` writes to `./.claude/skills/jcodemunch/`.
- `jcm uninstall` removes the skill bundle by default. `--keep-skills`
  preserves it. Uninstall is symmetric: removes the SKILL.md, then the
  `jcodemunch/` and `.claude/skills/` parent directories if empty
  (preserving sibling skills authored by the user).
- `jcm install-status` reports skill presence per scope (`global` +
  `project`) with `[x]` / `[ ]` markers, plus JSON via `--json`.

### Skill content

YAML frontmatter (`name: jcodemunch`, on-demand description) followed by
a tool-usage decision tree:

- When to load / when not to load the skill
- Opening-move sequence (`resolve_repo` → `plan_turn` → obey confidence)
- Reading code (outline → symbol → context bundle → file content)
- Relationships (which tool answers which question)
- Task orchestration via `assemble_task_context` (6 intents)
- Anti-patterns (avoid Read-Grep-Glob chains on indexed repos)
- After-editing hygiene (`register_edit` if hooks aren't installed)
- Multi-process awareness (v1.106.0 `watcher_holder` semantics)
- Tier model + `model=` parameter convention
- Full tool reference (intentionally a restatement of the preamble
  content so the skill stands alone when loaded)

Body is tier-aware: the same `_filter_policy_for_tools` filter used by
CLAUDE.md strips references to disabled tools.

### Composes with existing onboarding

- Always-on policy (CLAUDE.md preamble) keeps its role — short, terse,
  loaded every turn.
- On-demand skill is longer, more procedural, loaded only when relevant.
- Both share the same source content + tier filter, so they never drift.

### Tests

16 new tests in `tests/test_skills.py` covering: YAML frontmatter shape,
marker detection, global + project scope install, idempotent re-install,
user-authored-SKILL preservation on uninstall, empty-parent-dir cleanup,
sibling-skill preservation, dry-run no-write, and a full round-trip
through `run_init` + `run_uninstall`. Full suite: 4309 passed, 7 skipped.

### Dogfooded across a real CLI flow

`jcm install claude-desktop --skills` against an isolated tmp HOME wrote
the SKILL.md (171 lines, 11 h2 sections, 10 tool references); status
correctly reported `present: true`; uninstall removed SKILL.md and both
parent directories cleanly. Initial dogfood caught a bug — the .bak file
created by the backup helper was being written inside the same directory
we were trying to rmdir, preventing cleanup. Fixed by making backup a
no-op in `uninstall_claude_skill` (skill content is regenerable from
template; backup has no real safety value on the way out). Re-dogfood
verified all three cleanup checks pass.

### Install symmetry

`jcm install --skills` adds skill; `jcm uninstall` removes it (matched
default). `--keep-skills` lets you uninstall everything else while
keeping the skill. `--skills-scope` chooses global vs project on install.

## [1.106.0] — 2026-05-11 — multi-process shared-index coordination

Multiple MCP-server processes (Claude Code + Cursor + Codex on the same repo)
can now safely share one on-disk index.

### What this fixes

- Two MCP servers both auto-indexing the same repo on startup used to race
  the SQLite write block — interleaved DELETE/INSERT batches could corrupt
  the index. Now serialised: second writer waits up to 60s for the first to
  finish, then proceeds.
- Two watchers on the same repo from different processes used to fight over
  reindex storms on every file change. The watcher slot was already mutex
  (since well before v1.106.0 — `_acquire_lock` in `watcher.py`); v1.106.0
  promotes it to a generic primitive shared with the new index-write lock,
  enriches the metadata, and surfaces holder identity to agents.
- `get_watch_status` now reports `watched_by_another_process` per repo plus
  `watcher_holder` = `{pid, client_id, started_at, age_seconds}` so agents
  understand the parallel session and don't misread the idle watcher as a
  bug.

### Capability summary

- Atomic `O_EXCL` create + Unix `fcntl.flock` (cross-platform, no native deps)
- Stale-lock recovery: PID-based reclaim; OS-level lock auto-released on
  holder exit (Unix); explicit liveness check on Windows
- Lock metadata visible cross-process via a sidecar JSON: `pid`, `hostname`,
  `client_id`, `scope`, `target`, `started_at`
- Two lock scopes: `watcher` (one-watcher-per-repo) and `indexwrite`
  (save coordination). Independent — same target, different scopes don't
  block each other.
- Status reporting via `get_watch_status.watcher_holder` surfaces full
  holder identity (pid, client_id, age) so agents understand parallel sessions
- Wait-and-retry on the context manager: `indexwrite` waits up to 60s for
  a parallel writer; `watcher` fails fast (legitimate to skip when another
  process is already watching)
- Client identification via `JCODEMUNCH_CLIENT_ID` env var (defaults to
  `sys.argv[0]` basename, so common runtimes are auto-identified)

### Implementation

- `src/jcodemunch_mcp/storage/process_locks.py` (new, ~280 lines): generic
  `acquire(scope, target, storage_path)` / `release` / `inspect` /
  `held` (context manager with `wait_seconds`) / `current_holder_diagnostic`.
  Preserves the atomic-O_EXCL + Unix-flock + PID-liveness semantics of the
  original watcher lock; adds `client_id` + scope-aware metadata.
- `src/jcodemunch_mcp/watcher.py`: replaced the inline lock helpers with
  thin wrappers calling `process_locks` under the existing public API
  (`_acquire_lock`, `_release_lock`, `_lock_path`, `_folder_hash`,
  `_is_pid_alive`). Existing callers unaffected. Schema: `"folder"` field
  renamed `"target"` (lock files are now generic across scopes).
- `src/jcodemunch_mcp/storage/sqlite_store.py`:
  - `save_index` now wraps its write block in `process_locks.held("indexwrite", f"{owner}/{name}", ..., wait_seconds=60)`. Body extracted to `_save_index_locked` for clarity. Raises `RuntimeError` with holder diagnostic if 60s elapses without acquire.
  - `migrate_from_json` gets the same treatment via `_migrate_from_json_locked`.
- `src/jcodemunch_mcp/tools/get_watch_status.py`: reads `process_locks.inspect("watcher", folder)` per repo, surfaces `watched_by_another_process` + `watcher_holder` fields.

### Tests

- 23 new tests in `tests/test_process_locks.py` covering scope/target
  independence, holder metadata, stale-lock recovery, corrupt-metadata
  handling, `held()` wait-and-retry behavior, `LockHolder.age_seconds`,
  `current_holder_diagnostic`, and an integration test that proves
  `save_index` raises with the correct diagnostic when the lock is held.
- 1 test in `tests/test_watcher_lock.py` updated for the renamed
  `target` field (was `folder`).

### Dogfooded across real OS processes

Two real Python processes calling `save_index` on the same repo at the same
moment via `multiprocessing.Barrier`: both succeed, second-writer elapsed
time is ~500ms longer than first (the wait), final index loads clean with
all 20 source files. Two real processes contending for the same watcher
slot: first acquires, second is rejected and sees the first's identity
(pid, client_id="dogfood-claude-code") via `process_locks.inspect`.

### Migration

None required. Existing code paths work unchanged. The lock file schema
field `folder` → `target` is internal to the implementation and not
documented as an API surface.

### Env vars

- `JCODEMUNCH_CLIENT_ID`: friendly name for this process in lock metadata.
  Defaults to the basename of `sys.argv[0]` (so `claude`, `cursor`, `codex`
  show up automatically when those binaries exec our entry point). Override
  for custom runtimes.

## [1.105.1] — 2026-05-10 — `install` / `uninstall` / `install-status` CLI verbs

UX polish over the existing `init` machinery. Three new top-level CLI verbs that
match the per-agent shape that's common in installer UX (`<tool> install
<agent>`), without forcing users to learn the broader `init --client foo
--claude-md global --hooks --yes` flag combo.

The substance hasn't changed — `init` still does everything — but three real
gaps got closed:

- **`uninstall` actually exists now.** Previously users had to hand-edit
  `~/.claude/settings.json`, `.cursor/rules/jcodemunch.mdc`, `CLAUDE.md`, etc.
  to back out an `init`. The new `uninstall` verb reverses every install path,
  preserves user-added rules (only drops entries whose command mentions
  `jcodemunch-mcp`), and removes files only when they're empty after stripping
  (i.e., we created them). `--keep-claude-md`, `--keep-hooks`, etc. let you
  scope what gets reversed.
- **`install-status`** reads the current state of every install target
  (clients, policies, hooks) and reports `[x]` / `[ ]`. JSON output via
  `--json` for scripting / CI.
- **Per-agent shortcut: `install <agent>`.** `jcm install claude-desktop` is
  sugar for `init --client claude-desktop --claude-md global --hooks --yes`.
  `install --list` enumerates valid targets (`claude-code`, `claude-desktop`,
  `cursor`, `windsurf`, `continue`, `all`). `install --status` mirrors
  `install-status`.

### Why this is a patch, not a minor

This is wrappers + an uninstall path over existing tested code, not new
capability. No new MCP tools. The UX gap (per-agent install verb shape) was
real; our underlying machinery already exceeded what was needed.

### Implementation

- `src/jcodemunch_mcp/cli/init.py`: added `run_uninstall()`,
  `install_status()`, `print_status()`, `list_targets()`,
  `_strip_policy_blocks()` (heading-aware contiguous-block remover),
  `_strip_jcm_hooks()`, plus per-target `uninstall_*` functions mirroring the
  existing `install_*` set. `_AGENT_ALIASES` dict maps friendly names to
  canonical MCPClient names.
- `src/jcodemunch_mcp/server.py`: three new subparsers (`install`,
  `uninstall`, `install-status`) and dispatch branches. `install <agent>`
  routes through `run_init()` with sensible per-agent defaults.
- 23 new tests in `tests/test_install_uninstall.py`. Covers:
  - `_strip_policy_blocks` preserves user content before and after our region
  - `_strip_jcm_hooks` preserves user-authored hook rules in the same event
  - Full install → status → uninstall round-trips for CLAUDE.md, cursor rules,
    windsurf rules, hooks, all on `tmp_path`
  - Pre-existing file content survives a round-trip
  - Dry-run makes no changes

### Dogfooded

Per `feedback_smoke_orchestrators.md`: smoke-tested the full
`install claude-desktop` → `install-status` → `uninstall` flow against an
isolated `tmp HOME` before tagging. Pre-state was all `[ ]`, post-install was
the expected `[x]` set, post-uninstall returned to all `[ ]` with no orphaned
state. User-detection on the temp HOME correctly picked up the seeded client
config files; `claude` CLI gracefully said "not found -- skipped" since it
wasn't on the temp PATH.

### Tests

4270 passed (4247 from v1.105.0 + 23 new), 7 skipped.

## [1.105.0] — 2026-05-10 — `assemble_task_context`: task-aware single-call orchestrator

A single MCP call that takes a natural-language task and returns task-tailored,
source-attributed context. Two independent competitive validations of the
pattern made shipping the orchestrator strategic; today's dogfood pass on the
v1.100→1.104 foundation tools made it safe.

### Intent classification (explainable)

Six intents, classified via keyword scoring with weighted matches:

- **`explore`** — orient on an unfamiliar codebase (digest + hotspots + tectonic)
- **`debug`** — diagnose a fault (anchor + callers + callees + blast + runtime)
- **`refactor`** — restructure existing code (anchor + rename_safe + delete_safe + implementations + similar)
- **`extend`** — add new behaviour (anchor + implementations + similar + decorators)
- **`audit`** — assess risk/quality (anchor + risk + blast + dead_code + untested)
- **`review`** — review a PR/diff (changed + blast + risk + similar_changed)

Classification returns `intent_detected`, `intent_confidence` (0.55–0.95),
and `intent_keywords_matched` so the agent can see *why* a classification fired
and override if wrong. The unclear-task default is `explore` with confidence 0.5.

### Per-entry source attribution

Every entry in the returned capsule carries `stage` and `source_tool` — the agent
can see which sub-tool produced what. The capsule is fully auditable rather
than opaque.

### Token-budget end-to-end

Single `token_budget` knob bounds the entire orchestration, not per sub-tool.
Greedy packing under the budget across all stages.

### Anchor extraction

When the caller doesn't pass `symbols`, candidate symbol names are extracted
from the task via word tokenization with a stop-list, then filtered to names
present in the index. First two anchors used per stage to keep cost bounded.

### Override hooks

- `intent` — force a specific strategy (e.g. always run review-mode)
- `include` — whitelist specific stages from the strategy; non-strategy stages
  can also be added (cross-cutting capability)
- `cross_repo` — layer cross-repo signals when working across a suite

### Capability summary

- **Intent classification:** explainable, returns matched keywords + confidence
- **Per-entry attribution:** every capsule entry carries `stage` + `source_tool`
- **Runtime evidence:** woven in when Phase 7 traces exist
- **Override hooks:** `intent` forces a strategy; `include` whitelists or
  adds individual stages
- **Suite-aware:** `cross_repo` flag layers cross-repo signals via the
  package registry; integrates `get_group_contracts` automatically
- **Token budget:** end-to-end greedy pack, not per-sub-tool
- **License:** free, MIT, no per-tier paywall

### Dogfood pass

Tested against `local/jcodemunch-mcp-0394b683` with all 6 intents before
shipping. Caught and fixed two bugs in the same session:
1. Token estimation under-counted (only signature/body; now serialises full payload).
2. Tectonic-map plate keys (used `anchor_file`; correct is `anchor`).

Both regression-tested. v1.105.0 ships with a clean dogfood.

### Parameters

```python
assemble_task_context(
    repo: str,
    task: str,
    symbols: list[str] | None = None,
    intent: str | None = None,           # explore/debug/refactor/extend/audit/review
    token_budget: int = 8000,
    include: list[str] | None = None,    # stage whitelist
    cross_repo: bool = False,
)
```

### Tier registration

- **Core tier**: included alongside `get_ranked_context`. Flagship orchestrator.
- **Standard / Full tiers**: present in both.
- Listed under Search & Retrieval in `_CANONICAL_TOOL_NAMES`.

### Tests

19 new tests covering intent classification (7), happy path (3), budget (2),
anchor extraction (2), include override (1), errors (2), result shape (2).
Full suite at 4247 passed, 7 skipped.

## [1.104.1] — 2026-05-10 — fix: `check_delete_safe` test-importer classification

Dogfood-discovered bug: when the only importer of a symbol's file was a test file,
`check_delete_safe` returned `external_uses_blocking` instead of `test_coverage_only`.
Test-file importers were folded into `external_import_count` before the verdict
selector could downgrade to the test-only tier.

### Fix

- Track `test_import_count` as a distinct signal from `external_import_count`
- Verdict selector now tests `test_import_count` (combined with `test_ref_count`)
  before falling through to `safe_to_delete`
- Blocker `kind` for test-file importers changed from `external_import` to `test_import`
- `signals` dict now surfaces `test_import_count` separately so callers can audit

### Regression test

Added `test_test_only_reference_returns_test_coverage_only` (tightened from the
previous permissive assertion) and `test_test_import_count_surfaced_separately`.
Full suite: 4228 passed, 7 skipped.

## [1.104.0] — 2026-05-10 — `find_implementations` + `check_delete_safe`

Two new capabilities — concrete-impl discovery and deletion preflight — both
built with classification, confidence scoring, and recommended-action
surfaces rather than flat list-or-bool primitives.

### `find_implementations` (new tool, Relationships tier)

Four-channel resolution with confidence per result:

| Channel | Confidence | Source |
|---|---|---|
| LSP dispatch | 1.0 | `enrichment/lsp_bridge.py` `dispatch_edges` |
| AST class hierarchy | 0.85 | `_build_class_maps` subclass walk |
| Duck-typed | 0.65 | matching-name methods with no declared inheritance |
| Decorator handler | 0.45 | `@route`, `@cli.command`, signal/event handlers |

Per-impl classification: `subclass_override`, `interface_impl`, `duck_typed`,
`decorator_handler`, `subclass`. Ranked by confidence then PageRank × byte_length.
`differs_by` breakdown attaches body-size / param / callee-overlap deltas vs. the
target — same idea as `find_similar_symbols`. Optional `cross_repo=true` discovers
impls in other indexed repos via the package registry.

### `check_delete_safe` (new tool, Impact & Safety tier)

Composite preflight that fuses five signals into a single verdict:

- `find_importers(cross_repo=true)` — files importing the target's file
- `check_references` — text-level identifier references (duck-typed callers)
- `find_dead_code` — confidence score the symbol is unreachable
- Runtime traces (Phase 7) — `runtime_calls` hit count for the symbol_id
- Entry-point heuristics — decorator patterns (`@route`, `@cli.command`),
  name patterns (`main`, `__main__`, `run`, `serve`, `cli`)

Eight verdict tiers, most-restrictive first:
`runtime_observed` → `entry_point` → `cross_repo_blocking` →
`external_uses_blocking` → `internal_uses_blocking` → `test_coverage_only` →
`internal_only` → `safe_to_delete`.

Top-5 blockers sorted by severity (1–5 scale), per-signal counts in `signals`,
and a one-line `recommended_action` per verdict so agents get a concrete next
step, not just yes/no. Read-only — never mutates the codebase.

### Capability summary

- **`find_implementations`** returns classified, confidence-scored, ranked
  results with divergence breakdown and optional cross-repo coverage —
  not just a flat list.
- **`check_delete_safe`** (read-only) fuses five signals including runtime
  evidence, classifies into 8 verdict tiers, surfaces the specific blockers
  + recommended action, and lets the agent apply the deletion via native
  Edit/Write — keeping us in our read-only design.

### Tier registration

- **Standard tier**: both tools included alongside `check_rename_safe`.
- **Full tier** (default): `find_implementations` in Relationships group, `check_delete_safe`
  in Impact & Safety group.

### Tests

23 new tests (10 for `find_implementations`, 13 for `check_delete_safe`).
Full suite at 4227 passed, 7 skipped.

## [1.103.0] — 2026-05-10 — `get_group_contracts`: classified cross-repo API surface

Surfaces the de-facto API contracts across a *group* of indexed repos. Built
to go beyond a flat list — classifies intent, scores stability, attaches
breaking-change history, surfaces runtime evidence.

### `get_group_contracts` (new tool, Architecture tier)

Given a list of indexed repo IDs treated as a group, walks each member's
named imports, resolves them through the package registry to symbols in
other group members, and classifies each shared symbol into one of four
verdict tiers:

- **`de_facto_api`** — used by ≥`min_importers` external repos (the actual
  public surface, regardless of `__all__` or underscore conventions).
- **`leaky_internal`** — declared internal (underscore prefix or path fragments
  like `_internal/`, `/private/`) but imported by other repos. Architecture
  violation — accidentally-public APIs that should be either intentionally
  promoted or properly hidden.
- **`dead_contract`** — declared public but imported by zero externals. Opt-in
  via `include_dead_contracts=True`; candidate for demotion/removal.
- **`version_skew`** — same logical symbol imported via multiple specifier
  roots in the group (e.g. direct path vs. re-export). Coordination risk —
  refactoring one path won't move the others.

### Per-contract metadata

- **`importer_count`** + **`importing_repos`** — who depends on this contract
- **`stability_score`** — 1.0 = unchanged, decays as `churn_commits_window`
  rises; log-scaled from `get_churn_rate` over `churn_days` (default 90)
- **`last_breaking_change`** — best-effort date from `get_symbol_provenance`,
  filtered to refactor/rename/revert/feature classifications
- **`runtime_hits`** — total trace hits over the window when Phase 7 runtime
  tables have rows; omitted otherwise. Read-only / immutable connection so
  the lookup never bumps WAL mtime or invalidates the CodeIndex LRU cache.
- **`specifier_roots`** — list of import roots that resolve to this contract;
  multi-root entries trigger version_skew classification.

### Capability summary

Where flat "list shared symbols" lookups stop, this tool classifies intent
(de_facto_api / leaky / dead / version_skew), scores stability against
churn, tracks the last breaking change from provenance, and folds in
runtime evidence when traces exist. Pairs with `get_cross_repo_map`: that
gives the repo-level dep graph; this zooms to the symbol-level surface.

Built for monorepos and microservice groups with real cross-repo named
imports — Pydantic-using FastAPI applications, Spring Boot microservice
suites, dbt projects sharing macros, anywhere two or more indexed repos
import symbols from each other.

### Parameters

```python
get_group_contracts(
    repos: list[str],                # ≥2 required
    min_importers: int = 2,
    include_internal: bool = True,
    include_dead_contracts: bool = False,
    classify: bool = True,
    churn_days: int = 90,
    max_contracts: int = 50,
    token_budget: int = 4000,
)
```

### Tier registration

- **Standard tier**: included alongside `get_cross_repo_map`.
- **Full tier** (default): listed under Architecture.

### Tests

16 new tests; full suite at 4204 passed, 7 skipped.
Schema baseline bumped (~5% per tier; new Tool definition is meaty).

## [1.102.0] — 2026-05-10 — `find_similar_symbols`: multi-signal consolidation detection

Finds clusters of similar functions for refactor consolidation. Built with
multi-signal fusion rather than single-signal lexical match —
multi-signal scoring, union-find clustering, verdict tiers, canonical pick,
and a "differs_by" breakdown so the result is actionable, not just suggestive.

### `find_similar_symbols` (new tool, Quality & Metrics tier)

Three-signal similarity blending:

1. **Semantic** — embedding cosine similarity (when `embed_repo` has run)
2. **Structural** — Jaccard over signature-token bag + symmetric byte-length ratio
3. **Behavioral** — Jaccard over the set of callee names (from `call_references`)

Graceful degradation: when embeddings are absent, falls back to a 50/50 blend of
structural + behavioral and labels the response `mode: "structural"` so callers
know confidence is lower. Verdict tier auto-adjusts to `parallel_implementation`
in structural mode.

### Cluster output, not pair output

Edges above `threshold` feed union-find; output is groups, not N-choose-2 noise.
Each cluster gets:

- **Verdict tier**: `near_duplicate` (avg ≥ 0.92), `similar_logic` (0.80–0.92),
  or `parallel_implementation` (structural-only mode).
- **Canonical pick**: highest-PageRank symbol becomes the suggested keep-this;
  reasoning surfaced as `score_reason: "highest_pagerank"` or `"largest_body"`.
- **differs_by**: one-line per-cluster breakdown of the divergence axis —
  `["body: ±18 bytes", "params: 2 (match)", "callees: 3 shared, 1 unique each"]`.
- **Impact ranking**: clusters sorted by `size × max_byte_length`, packed under
  `token_budget` so the largest consolidation wins surface first.

### Performance: BM25 pre-filter, sub-N^2

Scoring all N-choose-2 pairs is unworkable on 8k-symbol repos. We pre-filter to
only score pairs that share at least one BM25 inverted-index posting, with a
per-term posting cap and a hard pair cap (100k) as safety nets. On the
jcodemunch-mcp index (7,700+ symbols), the tool returns in ~1–2s.

### Default-on false-positive filters

- `min_size=30` bytes — kills `def get_x(): return self._x` swarms
- Test files excluded unless `include_tests=True` (tests intentionally share shapes)
- Dunders excluded (`__init__`, `__repr__`, etc. — forced by language)
- Generated-code filenames skipped (`_pb2.py`, `.gen.go`, `.generated.ts`, ...)

### Capability summary

Free, runs offline, blends three signals (semantic, structural, behavioral),
clusters rather than pairs, and surfaces the canonical pick + difference
signal so the agent can make a refactor recommendation rather than just a
list. [Comparison detail on the versus page.](https://j.gravelle.us/jCodeMunch/versus.php)

### Parameters

```python
find_similar_symbols(
    repo: str,
    threshold: float = 0.80,
    min_size: int = 30,
    max_clusters: int = 25,
    include_tests: bool = False,
    scope: Optional[str] = None,
    include_kinds: Optional[list] = None,    # default: function/method/class
    semantic_weight: float = 0.6,
    token_budget: int = 4000,
)
```

### Tier registration

- **Standard tier**: included alongside `find_dead_code`, `get_untested_symbols`.
- **Full tier** (default): listed under Quality & Metrics.

### Tests

14 new tests; full suite at 4188 passed, 7 skipped.

## [1.101.0] — 2026-05-10 — `get_repo_map`: cold-start orientation map

Adds a query-less, token-budgeted, signature-level repo overview for
"I just cloned this repo — what matters here?" Fills a seam alongside the
existing tools — `get_symbol_importance` exposes PageRank, `get_ranked_context`
does query-driven token packing, `get_tectonic_map` returns multi-signal
module topology; this tool requires no query and emits signatures only for
breadth per token.

### `get_repo_map` (new tool, Quality & Metrics tier)

- Groups symbols by file, ranks files by PageRank on the import graph,
  and greedy-packs signatures (not source bodies) under `token_budget`.
- Reuses the cached PageRank scores from `_bm25_cache` when scope is
  unspecified — sub-millisecond on warm indexes.
- Parameters: `repo` (required), `token_budget` (default 2048),
  `scope` (optional glob), `max_per_file` (default 5, capped at 50),
  `include_kinds` (optional list).
- Per-file kind priority: class > function > method > type > constant,
  ties broken by symbol byte length descending.
- Skips files whose PageRank score is zero so leaf utilities aren't
  surfaced ahead of architectural hubs.
- Emits a `note` when the import graph is empty (e.g. single-file repos)
  so callers know the rank ordering is uniform.

### Why this exists

The existing tools each have a different focus: `get_ranked_context`
requires a `query`, `get_tectonic_map` returns plate structure (not packed
signatures), `digest` is delta-focused. None answer the cold-start question
on a fresh clone. `get_repo_map` does, with full reuse of the existing
PageRank machinery.

### Tier registration

- **Standard tier** (`tool_profile=standard`): included alongside
  `get_symbol_importance`. Power-user trims via `disabled_tools`.
- **Full tier** (default): listed under Quality & Metrics in `_CANONICAL_TOOL_NAMES`.

## [1.100.0] — 2026-05-10 — Phase 7: runtime-aware PR risk (milestone capstone)

The reason this whole milestone existed. An agent reviewing a PR can now
distinguish *"you're touching code that runs 1M times/day in prod"* from
*"you're touching code that has run zero times this quarter."* Static
call graph + runtime evidence = decisions agents can defend.

The release is fully **backwards-compatible**: callers without ingested
traces see the historical six-axis radar and the historical five-signal
PR risk mix, bit-for-bit. The new behaviour activates only after
runtime data lands. (An earlier draft uploaded as v2.0.1 has been
superseded by this v1.100.0 release; the v1.x major line continues.)

### `get_pr_risk_profile` runtime weighting

- New signal: ``runtime_traffic`` — 0..1 score derived from the log of
  per-symbol runtime hit count averaged across the changed symbols.
  ``log1p(1M)`` maps to a runtime score of 1.0; quieter symbols get a
  proportional fraction.
- New flag: ``runtime_dark_code_introduced`` — True when the PR adds
  symbols whose file has zero runtime evidence at all. Either the new
  code is unreachable, or the trace coverage has a blind spot — both
  warrant review before merging.
- New field: ``runtime_dark_code_files`` — the actual file list, capped
  at 5 in the human-readable recommendation.
- **Two weight regimes**:
  * Static-only (no traces): five signals summing to 1.0 — historical
    behaviour preserved bit-for-bit.
  * Runtime-aware (traces present): six signals summing to 1.0; the
    five static weights are scaled by 0.85 to make room for a 0.15
    runtime weight.
- Reads from both ``runtime_calls`` and ``runtime_stack_events`` so a
  symbol's hit count includes both happy-path traffic and error frames.
- Read-only / immutable connection — never bumps WAL mtime, never evicts
  the LRU cache.

### Health radar 7th axis: ``runtime_coverage``

- ``compute_radar`` accepts a new ``runtime_coverage_pct`` keyword
  argument. When provided, it surfaces as a 7th axis with a linear
  0..100 score.
- ``get_repo_health`` populates the value via ``get_runtime_coverage``;
  failures (no traces, missing column, pre-v14 DB) leave the axis
  omitted so the composite stays comparable against pre-Phase-7
  baselines.
- ``diff_health_radar`` is axis-agnostic and surfaces the new axis
  automatically — no changes needed.
- Existing six-axis tests updated to reflect the new
  ``omitted_axes: ['runtime_coverage']`` default.

### Observatory runtime-evidence column

- ``append_run`` records ``runtime_evidence: bool`` per run based on
  whether the radar carries a ``runtime_coverage`` axis.
- The leaderboard tile shows a small ``live`` badge next to repos that
  have ingested traces. Hidden when False to keep the index page clean.
- Index page caption updated: "Six-axis radar (plus an optional seventh
  axis when runtime evidence is available)".

### Versioning rationale

This is the **milestone capstone** — Phases 0-6 built the runtime
ingest infrastructure (schemas, parsers, orchestrators, HTTP endpoint,
redaction chokepoint, MCP tools); Phase 7 closes the loop by feeding
that data back into the agent's risk assessment. Stays on the v1.x
line as a minor; the change is additive — pre-Phase-7 callers see no
behavioural difference.

### Tests

15 new tests in ``tests/test_runtime_phase7.py`` covering:

- Health radar with / without ``runtime_coverage_pct``.
- ``_score_runtime_coverage`` linear mapping + clamping.
- Composite drops when runtime axis is low (validates "empirical
  evidence at low coverage scores worse than static-only").
- ``diff_radar`` picks up the new axis automatically.
- ``_runtime_traffic_score`` aggregates correctly (zero on empty,
  monotonic in hits, average not max).
- ``_load_runtime_signal_for_changed`` behaviour (false when no data,
  true when present, combines runtime_calls + runtime_stack_events).
- Static-only and runtime-aware weights both sum to exactly 1.0.
- Observatory ``append_run`` flips ``runtime_evidence`` correctly.

One pre-existing test (``test_healthy_repo_grades_high``) updated to
reflect that ``runtime_coverage`` is now in the default-omitted set.

Suite at 4165 passed, 7 skipped.

### Total milestone footprint (Phases 0-7)

- 6 new SQLite tables: ``runtime_calls`` / ``runtime_edges`` /
  ``runtime_imports`` / ``runtime_unmapped`` / ``runtime_redaction_log``
  (Phase 0); ``runtime_columns`` (Phase 4); ``runtime_stack_events``
  (Phase 5).
- 4 ingest sources wired: OTel (Phase 1), SQL log (Phase 4), stack log
  (Phase 5), HTTP live (Phase 6); 1 reserved (apm).
- 5 new MCP tools: ``import_runtime_signal``, ``get_runtime_coverage``,
  ``find_hot_paths``, ``find_unused_paths``, ``get_redaction_log``.
- 6 existing tools enriched: ``search_symbols`` / ``get_symbol_source``
  / ``find_references`` / ``get_blast_radius`` / ``get_call_hierarchy``
  carry per-result ``_runtime_confidence`` (Phase 2);
  ``get_symbol_provenance`` carries optional ``stack_frequency`` block
  (Phase 5); ``get_pr_risk_profile`` and ``get_repo_health`` are
  runtime-aware (Phase 7).
- INDEX_VERSION 13 → 16 over three additive in-place migrations.
- ~3,000 LOC + 6 new test files (~115 new tests).

## [1.99.0] — 2026-05-10 — Phase 6: HTTP live-ingest endpoint

Adds an opt-in HTTP transport endpoint so production systems can ship
runtime signals to a running jcm instance in real time instead of via
nightly file imports. Built on top of the existing Starlette HTTP MCP
transport — same bearer auth, same rate limit, same redaction
chokepoint, same upserts, same FIFO eviction. The HTTP and file paths
are bit-for-bit interchangeable.

### Three new POST routes

- ``POST /runtime/otel``  — OTLP/JSON spans (Phase 1 wire format)
- ``POST /runtime/sql``   — pg_stat_statements CSV / JSON-Lines (Phase 4)
- ``POST /runtime/stack`` — Python / JVM / Node.js stacks (Phase 5)

Each route accepts the same body format the file-based ``import-trace``
CLI accepts. ``Content-Encoding: gzip`` is honoured under the body-size
cap. Repo identifier comes from the ``X-JCM-Repo`` header or
``?repo=owner/name`` query string. Each handler is thin: parse → hand to
the corresponding ``ingest_*_stream`` orchestrator → return the same
envelope the file ingestors return.

### Two-key turn (off by default)

The endpoint is **off by default**. Two flags must turn before traffic
flows:

1. ``JCODEMUNCH_HTTP_TOKEN`` — bearer auth (already required by the
   existing HTTP transport when bound to a non-loopback host).
2. ``JCODEMUNCH_RUNTIME_INGEST_ENABLED=1`` — explicit opt-in to the
   write side. Without this the routes return 503 even with a valid
   token.

Reasoning: a write endpoint is a bigger deal than a read endpoint;
operators should make the decision twice.

### Concurrency + safety

- **Per-repo asyncio.Lock** serialises writes against the same SQLite
  database file. Cheaper than letting WAL retry-storm under load and
  keeps upsert order deterministic. Lock registry is LRU-bounded at 256
  repos.
- **Per-request body cap** (default 5 MB after decompression; tunable
  via ``JCODEMUNCH_RUNTIME_INGEST_MAX_BODY_BYTES``) prevents DoS via
  giant or gzip-bomb payloads. Decompressed size is checked separately
  from on-wire size.
- **Same redaction chokepoint** the file ingestors use — every record
  routes through ``redact_trace_record()`` before any storage call.

### Stream orchestrators (the underlying refactor)

- ``ingest_otel_stream(db_path, text, ...)``
- ``ingest_sql_log_stream(db_path, text, fmt='auto', ...)``
- ``ingest_stack_log_stream(db_path, text, fmt='auto', ...)``

The file-based ``ingest_*_file`` functions are now thin wrappers around
shared ``_ingest_*_iter`` consumers; both file and stream paths share
the resolve→aggregate→persist pipeline. Equivalence test in the suite
asserts identical envelopes when fed identical content.

### New parser helpers

- ``iter_otel_from_text(text)`` — already used internally by
  ``parse_otel_file``; now public.
- ``iter_sql_from_text(text, fmt='auto'|'csv'|'jsonl')``
- ``iter_stack_from_text(text, fmt='auto'|'plain'|'jsonl')``

These let callers (HTTP routes, but also test harnesses) parse a
payload without writing it to disk first.

### New MCP tool: ``get_redaction_log``

Forensic accounting for operators: surfaces the per-pattern counts
recorded in ``runtime_redaction_log`` so you can verify the redaction
chokepoint is actually firing on production traffic. Filters by
``source`` (otel / sql_log / stack_log / apm) and ``since_days`` (default
30). Read-only / immutable connection so the LRU cache isn't evicted by
an mtime bump. Empty patterns list = either no traffic in the window
or ``JCODEMUNCH_RUNTIME_REDACT`` was disabled.

### Reference: OTel Collector exporter snippet

``examples/otel-collector/jcm-exporter.yaml`` — copy-paste
``otlphttp/jcm-otel`` / ``otlphttp/jcm-sql`` / ``otlphttp/jcm-stack``
exporter blocks for the OpenTelemetry Collector with bearer auth,
``X-JCM-Repo`` header, gzip compression, and retry policy. Adjacent
README documents the two-key turn and the verification flow
(``get_redaction_log`` → ``get_runtime_coverage``).

### Tests

17 new tests in ``tests/test_runtime_phase6.py`` covering:

- Stream-parser ↔ file-parser equivalence (OTel + SQL + stack).
- ``ingest_otel_stream`` envelope identical to ``ingest_otel_file``.
- HTTP route gating: 503 when disabled, 400 without repo, 404 when not
  indexed, 413 on oversized body, 400 on unknown ``?fmt=``.
- Happy path for all three routes (header-based + query-based repo
  selection + gzip Content-Encoding).
- ``get_redaction_log`` surfaces live-ingest redaction labels +
  filters by source.

Suite at 4150 passed, 7 skipped.

### Total v1.99.0 footprint

- 3 new HTTP routes mounted on both SSE and streamable-http transports
  (both under the same bearer auth + rate-limit middleware).
- 1 new module: ``runtime/http_routes.py`` (Starlette handlers + per-repo
  lock registry).
- 3 new ``ingest_*_stream`` public functions; existing file-based
  functions refactored to share the same downstream pipeline.
- 1 new MCP tool: ``get_redaction_log`` (74 → 75 with test_summarizer
  enabled, 73 → 74 with default config).
- 2 new env vars: ``JCODEMUNCH_RUNTIME_INGEST_ENABLED``,
  ``JCODEMUNCH_RUNTIME_INGEST_MAX_BODY_BYTES``.
- ``examples/otel-collector/`` dir with reference exporter + README.

Phase 7 (``get_pr_risk_profile`` runtime weighting — milestone capstone
with the seventh radar axis) is the only roadmap item left.

## [1.98.0] — 2026-05-10 — Phases 4 + 5: SQL log + stack-frame ingest

Two new runtime signal sources: pg_stat_statements / generic SQL JSON-Lines
(Phase 4) and Python / JVM / Node.js stack-frame logs (Phase 5). Together
they take the runtime trace coverage from "what HTTP endpoints ran" to
"every layer the request touched, including the data layer and every
frame in every error stack." INDEX_VERSION 14 → 16 over two additive
in-place migrations (v14→v15 and v15→v16). Existing OTel rows preserved.

### Phase 5 (new in this release)

- **New schema**: `runtime_stack_events(symbol_id, source, severity,
  count, first_seen, last_seen)` table + two indexes
  (`idx_runtime_stack_events_severity`, `idx_runtime_stack_events_symbol`).
  PK includes severity so a single symbol carries distinct rows for
  error / warn / info; the symbol's `runtime_calls` row gets a
  severity-agnostic rollup so existing confidence-stamping tools fire.
- **New parser** at `runtime/stack_log.py`: handles three dialects in
  one pass — Python tracebacks (`Traceback (most recent call last):`
  + `File "...", line N, in <name>` pairs), JVM tracebacks
  (`com.example.Foo: ...` + `    at pkg.Class.method(File.java:N)` +
  flattened `Caused by:` chains), and Node.js stacks
  (`Error: ...` + `    at funcName (file.js:N:N)` plus the anonymous
  `    at file.js:N:N` form). Tolerates `node:events`-style module
  paths in Node frames. JSON-Lines structured-log path with
  explicit `severity` / `level` fields overrides the heuristic.
  `.gz` transparent.
- **Severity heuristic**: looks at the line introducing the trace +
  three lines above for `FATAL` / `CRITICAL` / `ERROR` / `WARN[ING]` /
  `INFO` tokens. Defaults to `info` because some pipelines log every
  exception at info-level for post-hoc triage.
- **New ingest orchestrator** `runtime/stack_ingest.py`: parse → redact
  → resolve → upsert. Each frame's `(file, line, function)` runs through
  the existing OTel resolver (suffix-match fallback for absolute trace
  paths against repo-relative index paths). Stack-event upsert into
  `runtime_stack_events` and severity-agnostic rollup into `runtime_calls`
  share one transaction. FIFO eviction extended to also trim
  `runtime_stack_events`.
- **`import_runtime_signal({source: 'stack_log'})`**: third source wired
  into the existing MCP tool. Returns `{records, frames, mapped, unmapped,
  severity_counts: {error, warn, info}, redactions_fired,
  unmapped_reasons, evicted}`. CLI: `jcodemunch-mcp import-trace
  --stack-log <path>` mirrors `--otel` / `--sql-log`. Exactly one of the
  three is required.
- **`get_symbol_provenance` runtime enrichment**: new `stack_frequency`
  block with per-severity counts over a 30-day window plus
  `first_seen` / `last_seen`. When a symbol shows up in 3+ error stacks
  the narrative gains an appended sentence — operational risk signal
  the static git lineage couldn't otherwise convey. Read-only /
  immutable connection so the LRU cache isn't evicted by an mtime bump
  (matches the Phase 2 confidence-probe pattern). Zero-cost when
  `runtime_stack_events` is empty or doesn't exist (pre-v16).
- **Redaction**: every stack event's `message` field routes through the
  same `redact_trace_record()` chokepoint with `source='stack_log'`;
  `email_address`, `ipv4_address`, secrets registry, and friends fire
  and tally into `runtime_redaction_log`.
- **Tests**: 19 new tests in `tests/test_runtime_phase5.py` — Python /
  JVM / Node parser corpus, severity-tag inference, JSON-Lines path,
  end-to-end ingest (Python + JVM + Node), unmapped-frame routing,
  redaction firing on email-bearing messages, idempotency under repeat
  ingest, `get_symbol_provenance` integration (omits when empty,
  surfaces when present), and v15→v16 migration idempotency.

### Phase 4 (landed before v1.98.0; included here for completeness)

- **Schema**: `runtime_columns(model_name, column_name, source, count,
  first_seen, last_seen)` + 2 indexes; v14→v15 migration.
- **Parser** at `runtime/sql_log.py`: pg_stat_statements CSV
  (`total_time` / `total_exec_time` / `mean_time` / `mean_exec_time`
  aliases honoured for Postgres-version compatibility) + generic
  JSON-Lines + top-level array fallback + comment lines + `.gz`.
  Pure parsing; no DB writes.
- **Reference extraction**: regex-based — table refs from FROM / JOIN /
  INSERT INTO / UPDATE / DELETE FROM / MERGE INTO; column refs from
  qualified `alias.col` plus bare identifiers in SELECT / predicate
  blocks. Schema-qualified names trim to trailing identifier so they
  match dbt model names. Tolerates quoted identifiers.
- **Orchestrator** `runtime/sql_ingest.py`: parse → redact → resolve →
  upsert. Read-only resolver metadata snapshot covers file-stem map,
  exact-name map, and declared dbt columns. Writer transaction upserts
  runtime_calls + runtime_columns + runtime_unmapped + runtime_redaction_log.
- **`import_runtime_signal({source: 'sql_log'})`** + CLI
  `import-trace --sql-log <path>`.
- **`find_unused_paths` dbt-aware extension**: rescues SQL-file model
  symbols with observed column reads (column-only audit-log shape);
  surfaces models whose declared columns have zero hits with
  `reason='dbt_model_no_column_reads'` + `unused_columns: [...]` list.
  `_meta` gains `runtime_columns_present` and `rescued_by_column_hit`.
- **Tests**: 26 in `tests/test_runtime_phase4.py`.

### Total v1.98.0 footprint

- INDEX_VERSION 14 → 16 (two additive migrations)
- 4 new files: `runtime/sql_log.py`, `runtime/sql_ingest.py`,
  `runtime/stack_log.py`, `runtime/stack_ingest.py`
- 2 new tables (`runtime_columns`, `runtime_stack_events`) + 4 indexes
- 45 new tests (26 Phase 4 + 19 Phase 5); suite at 4133 passed, 7 skipped
- `import_runtime_signal` now accepts 3 of 4 source values (otel /
  sql_log / stack_log; apm reserved)
- `get_symbol_provenance` gains optional `stack_frequency` block

Phase 6 (HTTP live-ingest endpoint) and Phase 7 (`get_pr_risk_profile`
runtime weighting — the milestone capstone) follow.

## [1.97.1] — 2026-05-10 — Phase 3: runtime analytics tools

Three new MCP tools that turn the runtime tables (Phase 0-2) into
agent-actionable signals. No schema changes; INDEX_VERSION stays at 14.

- **`get_runtime_coverage(repo, file_path?)`** — coverage histogram for
  a repo or a single file. Returns `{total_symbols, confirmed,
  declared_only, coverage_pct, sources, last_seen, unmapped_runtime[]}`.
  The `unmapped_runtime` list surfaces span groups that pointed at code
  the AST extractor didn't catch — likely reflective dispatch.
  Read-only / immutable connection so the LRU cache is preserved.
- **`find_hot_paths(repo, query?, top_n=20)`** — top-N symbols ranked
  by total runtime hit count. Each row carries `runtime_count`,
  `p50_ms`, `p95_ms`, `sources`, `first_seen`, `last_seen`. Optional
  case-insensitive substring filter on name. Pairs with
  `get_blast_radius` so the agent learns "this PR touches a function
  called 4M times/day" before deciding review depth.
- **`find_unused_paths(repo, since_days=90, ...)`** — symbols with zero
  (or stale) runtime hits over the look-back window. Distinct from
  `find_dead_code` (static-only): catches code reachable on paper but
  never executed. Excludes test files and entry-point filenames by
  default; tunable via `include_tests` / `include_entry_points`.
  Refuses to flag any symbol when `runtime_calls` is empty (otherwise
  every symbol would trivially qualify).

### Added to standard tier

All three tools are in the standard tool profile, available to
Sonnet/GPT-4o/Gemini-class agents by default.

### Tests

15 new tests in `tests/test_runtime_phase3.py` covering coverage
histogram (zero-traces / repo-wide / file-scoped / unmapped surfacing /
unknown-repo error), hot-paths (empty / ranking / filtering / top-n
clamp), and unused-paths (no-runtime refusal / dark-symbol detection /
test-file inclusion toggle / entry-point inclusion toggle / reason
classification / meta counts). Suite: **4088 passed**, 7 skipped.

Schema-budget baseline refreshed for the three new tool schemas.

## [1.97.0] — 2026-05-10 — Runtime trace ingestion (Phases 0-2)

First runtime-aware release. Static call graph + ingested OTel trace data
combine on every result so agents can distinguish *"this code runs 1M
times/day in prod"* from *"this code has run zero times this quarter."*
Roadmap source: [todo.md](../todo.md).

### Phase 2 — Runtime confidence on existing tool results

- **`_runtime_confidence` per result** on `search_symbols`,
  `get_symbol_source`, `find_references`, `get_blast_radius`, and
  `get_call_hierarchy`. Values: `confirmed` (≥1 row in `runtime_calls`
  for this symbol), `declared_only` (in graph, no runtime evidence).
  `find_references` uses **file-level** confidence (any indexed symbol
  in the importing file with runtime evidence) since references aren't
  symbol-keyed.
- **`_meta.runtime_freshness` block**:
  `{sources, last_seen, coverage_pct}` — sources is sorted list of
  trace sources contributing (today: `['otel']`); last_seen is ISO-8601
  most-recent across the result set; coverage_pct is integer % of
  returned items with runtime evidence.
- **Zero-cost when no traces ingested**: probe checks for any row in
  `runtime_calls` at construction; if absent, no field is added and the
  response shape is identical to the v1.96.x contract. Read-only
  connections use `?mode=ro&immutable=1` so they never touch -shm/-wal
  files and never invalidate the CodeIndex LRU cache.
- **New module `runtime/confidence.py`**: `RuntimeConfidenceProbe` class
  + `attach_runtime_confidence()` and `attach_runtime_confidence_by_file()`
  helpers for symbol-keyed and file-keyed surfaces.
- **14 new tests in `tests/test_runtime_phase2.py`** covering the probe
  contract (zero-cost, stamping, coverage math), helper variants
  (symbol-keyed, file-keyed, invalid-db-path), and full integration on
  the 5 affected tools (with-and-without-runtime paths).

### Phase 1 — OTel JSON / JSON-Lines / .gz import

- **New MCP tool `import_runtime_signal({source, path, repo, redact_enabled?})`**.
  Parses an OTel trace file, redacts PII at the chokepoint, resolves spans
  to indexed symbols via `(code.filepath, code.lineno, code.function)`,
  and upserts into `runtime_calls` / `runtime_unmapped` /
  `runtime_redaction_log`. Returns `{records, mapped, unmapped,
  redactions_fired, unmapped_reasons, evicted}`. Phase 1 implements
  `source='otel'`; `sql_log` / `stack_log` / `apm` land in Phase 4+.
- **New CLI subcommand**: `jcodemunch-mcp import-trace --otel <path> [--repo <id>] [--no-redact]`.
  Same surface; prints the result dict as JSON. `--no-redact` is intended
  ONLY for offline debugging on synthetic data.
- **OTel parser (`runtime/otel.py`)**: handles three shapes — JSON-Lines
  (Collector `file` exporter default), single-document JSON, top-level
  array — plus transparent `.gz` decompression.
- **Ingest orchestrator (`runtime/ingest.py`)**: per-batch p50/p95
  latency from `endTimeUnixNano - startTimeUnixNano`, FIFO eviction
  down to `runtime_max_rows` when exceeded, and per-pattern redaction
  accounting.
- **Idempotency contract**: each ingest is **additive** — re-importing
  the same file re-adds counts. Future `replace=True` flag will no-op
  identical re-imports.
- **Surface integration**: `get_session_stats`'s `runtime_signal`
  now reflects real numbers post-ingest (was always zero in Phase 0).
- **21 new tests** in `tests/test_runtime_phase1.py` covering parser
  shapes, ingest happy-path / unmapped / no-code-attrs / redaction-log /
  idempotency / FIFO-eviction / session-stats integration / mapping-rate
  threshold (≥90% on synthetic fixture; actual 95%), and the MCP-tool
  wrapper (success / unknown-source / Phase-4-source / missing-index errors).

### Phase 0 — Trace-ingestion scaffold

- **INDEX_VERSION 13 → 14.** Existing v13 (and earlier v9–v12) databases
  auto-migrate on first open via `_migrate_v13_to_v14()`; the migration is
  idempotent and only adds tables (no existing rows touched).
- **Five new SQLite tables**: `runtime_calls`, `runtime_edges`,
  `runtime_imports`, `runtime_unmapped`, `runtime_redaction_log`. All
  start empty; zero on-disk cost until Phase 1 begins ingest.
- **New `jcodemunch_mcp.runtime` package**:
  - `redact_trace_record(record, source) → (redacted, labels)` — single
    chokepoint that strips PII (emails, IPv4, SQL literals, JSON value
    blocks, Python locals reprs) and existing high-entropy secrets
    (AWS/GCP/Azure/JWT/GitHub/Slack/PEM) before any storage call.
  - `resolve_to_symbol_id(conn, file_path, line_no, function_name)` —
    best-effort `(file, line, function)` → `symbol_id` resolver with
    suffix-match fallback for absolute trace paths against repo-relative
    index paths.
  - `VALID_SOURCES = {'otel', 'sql_log', 'stack_log', 'apm'}`.
- **New config keys**:
  - `runtime_max_rows` (default 100,000) — env `JCODEMUNCH_RUNTIME_MAX_ROWS`.
    Rolling cap for FIFO eviction once Phase 1 ships ingest.
  - `runtime_redact_enabled` (default `true`) — env `JCODEMUNCH_RUNTIME_REDACT`.
    Disabling permitted **only** for offline debugging on synthetic data.
- **`get_session_stats` now reports `runtime_signal: {rows, by_source}`**.
  Reads zero until Phase 1 ingest writes rows; non-zero readings prove
  ingest happened. Cheap probe — only opens databases that already have
  the runtime_calls table.
- **Tests**: 21 new tests in `tests/test_runtime_phase0.py` covering
  fresh v14 schema, v9→v14 migration chain, migration idempotency, the
  redaction chokepoint (incl. nested-dict and list recursion), and the
  resolver across exact / fallback-by-name / suffix-match / miss paths.

### Test status

- **4073 passed**, 7 skipped (+14 Phase 2, +21 Phase 1, +21 Phase 0 vs v1.96.2 baseline).

### Scope

`import_runtime_signal` is the foundation for ingesting **every** edge type
across **multiple** signal sources (Phase 1 ships OTel; Phase 4+ adds SQL
logs and stack traces; Phase 6 adds opt-in live ingest; Phase 7 wires
runtime weighting into `get_pr_risk_profile`).

## [1.96.2] — 2026-05-10 — `index` accepts full GitHub URLs + MCP-tool typo hints

Closes [#289](https://github.com/jgravelle/jcodemunch-mcp/issues/289).
The CLI's `index` subcommand now accepts every common GitHub URL form,
not just `owner/repo`:

```
jcodemunch-mcp index https://github.com/elastic/kibana
jcodemunch-mcp index https://github.com/elastic/kibana/
jcodemunch-mcp index https://github.com/elastic/kibana.git
jcodemunch-mcp index git@github.com:elastic/kibana.git
jcodemunch-mcp index github.com/elastic/kibana
jcodemunch-mcp index elastic/kibana   # still works
```

All five forms collapse to `elastic/kibana` via `parse_github_url`,
which now handles SSH (`git@host:path`), bare-host (`github.com/...`),
and trailing slashes alongside the existing `https://github.com/...`
and `owner/repo` forms.  Hostname validation still runs before any
network call (SSRF guard from v1.x.x is intact).

MCP-tool-name typos at the CLI now print a friendly hint instead of
an opaque argparse error:

```
$ jcodemunch-mcp index_repo https://github.com/elastic/kibana
jcodemunch-mcp: error: unknown subcommand `index_repo`. Did you mean:
    jcodemunch-mcp index <owner/repo>
    jcodemunch-mcp index <github-url>
    jcodemunch-mcp index <local-path>
```

Aliased typos: `index_repo`, `index-repo`, `index_folder`,
`index-folder` → `index`; `index_file` → `index-file`.  The CLI exits
2 (argparse-style usage error) so scripts can detect the mismatch.

Surfaced by @Bamieh on
[#275](https://github.com/jgravelle/jcodemunch-mcp/issues/275#issuecomment-4414717322).

No schema or index-format changes. Re-index not required.

## [1.96.1] — 2026-05-10 — Self-describing observatory `index.json`

`jcodemunch-mcp observatory build` now embeds three top-level metadata
fields in `index.json`:

```json
{
  "generator_version": "1.96.1",
  "index_version": 13,
  "built_at": "2026-05-10T13:42:07Z",
  "summaries": [...],
  "skipped": [...]
}
```

Verifiers can now confirm which jcm version + INDEX_VERSION produced a
published observatory run from the artifact alone, without
cross-referencing the CI workflow log.  Surfaced when checking that
v1.96.0 actually shipped through the observatory pipeline; the run
succeeded but `index.json` was silent on what produced it.

No schema or behavior changes. Re-index not required.

## [1.96.0] — 2026-05-10 — Subdir merge for monorepo workflows (#288 phase 2)

Closes [#288](https://github.com/jgravelle/jcodemunch-mcp/issues/288).
v1.95 made the storage identity git-root-aware so a clone of
`elastic/kibana` indexes as `elastic/kibana`.  v1.96 makes that identity
*useful* for the workflow the issue was filed against: indexing
different subdirs of one clone now coalesces into one repo index.

```
cd ~/work/kibana
jcodemunch-mcp index ./packages
jcodemunch-mcp index ./scripts
# -> one elastic/kibana index whose source_files contains both
#    "packages/p.py" and "scripts/s.py", paths git-root-relative
```

### What changed

* `index_folder` now retargets `folder_path` to the git root when one is
  detected; only `walk_root` (the user-passed subdir) is walked.  All
  file paths in the resulting index are git-root-relative.
* When an existing v1.96-format index already covers the same git root,
  files outside `walk_prefix` carry over and the fresh walk's data
  unions with them.  Re-indexing one subdir replaces only that prefix;
  every other subdir's files survive.
* A full-root walk (`index .`) supersedes any earlier subdir slices —
  `source_roots` becomes `[""]` and stale subdir entries drop.
* `CodeIndex` gains `source_roots: list[str]` listing the git-root-relative
  prefixes that have been walked.  `INDEX_VERSION` bumped 12 → 13.

### v1.95 indexes are rebuilt fresh on first v1.96 indexing

v1.95 stored file paths relative to whatever subdir the user pointed
at.  Mixing those into a git-root-relative merge would silently produce
inconsistent paths.  v1.96 detects the legacy format (existing index's
`source_root` ≠ `git_root`), drops it with a warning, and rebuilds
under the new scheme.  No silent corruption, but you'll need to re-run
each subdir-index command you previously used so they merge into the
new index.

### Opt-out preserved

`git_root_identity: false` (or `JCODEMUNCH_GIT_ROOT_IDENTITY=0`) keeps
the v1.94 per-subdir `local/<basename>-<hash>` identities.  No git-root
detection, no merge, no retarget.

### Known limitations (deferred)

* Ancestor `.gitignore` files outside `walk_root` aren't applied to
  the walk.  Subdir-level `.gitignore` files inside the walk continue
  to work normally.
* `context_metadata` merge is a shallow overlay — provider-specific
  per-file data may be lost on key overlap.  Revisit if a specific
  provider surfaces a bug.
* Branch-delta indexing on a non-base branch falls through the merge
  path; subdir merging on a feature branch is unverified.

### Tests

4001 passed, 7 skipped (5 new in `TestSubdirMerge`):
re-index-subdir replaces only that prefix; full-root walk after subdir
replaces everything; disjoint deeper subdirs both present after merge;
v1.95 legacy index is rebuilt rather than corrupted; opt-out preserves
per-subdir identities.

## [1.95.1] — 2026-05-10 — Hot-fix: refuse subdir overwrite under shared git-root identity

v1.95.0 introduced a regression for the exact workflow [#288](https://github.com/jgravelle/jcodemunch-mcp/issues/288)
was filed against. With `git_root_identity` on (default), running
`index ./packages` then `index ./scripts` from a clone of
`elastic/kibana` resolves both to identity `elastic/kibana`. The
v1.95.0 collision guard only fired when `git_root` *differed*, so the
second call's `save_index` silently wiped the first's content.

v1.95.1 extends the guard: same `git_root` + different `source_root` is
also a refuse, until v1.96 ships the actual subdir-merge logic. Error
points at the two workarounds:

* `cd <git_root> && jcodemunch-mcp index .` — one index covers all
  subdirs (the recommended path until v1.96).
* `git_root_identity: false` (or `JCODEMUNCH_GIT_ROOT_IDENTITY=0`) —
  fall back to v1.94 per-subdir `local/<basename>-<hash>` indexes.

Re-indexing the *same* subdir still succeeds (incremental re-index path
unchanged). Repos without an `origin` remote keep v1.94 per-subdir
behavior, so the refuse never fires for them.

**Recommendation: skip 1.95.0, install 1.95.1.** No data lost on
upgrade — the regression only triggered when a user actively re-ran
`index` against a different subdir of an already-indexed clone.

## [1.95.0] — 2026-05-10 — Git-root-aware index identity (#288 phase 1)

First slice of [#288](https://github.com/jgravelle/jcodemunch-mcp/issues/288).
When indexing a local clone, jcodemunch now walks up looking for a
`.git/` directory and — if it finds one with an `origin` remote
configured — derives the storage identity from that remote.  A clone of
`https://github.com/elastic/kibana` now indexes as `elastic/kibana`,
matching what `index_repo elastic/kibana` would produce, regardless of
the local folder name.

**Re-index recommended** — `INDEX_VERSION` bumped 11 → 12 to add the
`git_root` manifest field (foundation for v1.96 subdir merging).  Old
v11 indexes load fine with an empty default; identity is set at *create*
time, so a fresh re-index gives you the new naming.

### Identity rules

* `.git/` found, `origin` remote parses to `owner/repo` → identity is
  `owner/repo`.  Covers GitHub, GitLab, Bitbucket, ssh/https URLs, with
  or without `.git` suffix, with or without trailing slash.
* `.git/` found, no usable `origin` → identity stays
  `local/<basename>-<hash>` (v1.94 behavior) so unrelated local
  projects with the same folder basename never collide.
* No `.git/` anywhere up the tree → identity stays
  `local/<basename>-<hash>` (v1.94 behavior).

### Collision guard

Indexing a second working tree (different `git_root`) of an
already-indexed remote refuses with a clear error rather than silently
overwriting.  Two clones of `elastic/kibana` at different paths now
require either deleting one or opting out of git-root identity.

### Opt-out

Set `git_root_identity: false` in `~/.code-index/config.jsonc` (or
`JCODEMUNCH_GIT_ROOT_IDENTITY=0`) to disable the new behavior and keep
the v1.94 basename-plus-hash identity for everything.

### What this *does not* do yet

The bigger ask in #288 — making `index ./packages` and `index ./scripts`
coalesce into one `elastic/kibana` index instead of four scattered
ones — is **v1.96**.  v1.95 lays the foundation: `git_root` is now on
the manifest, so the merge logic has somewhere to anchor.  Indexing
subdirs still produces separate indexes today.

## [1.94.0] — 2026-05-09 — Symbol-aware selective re-export tracking

Closes [#286](https://github.com/jgravelle/jcodemunch-mcp/issues/286).
v1.93.0 made the import graph barrel-aware for **wildcard** re-exports
(`export * from <spec>`). The remaining ~5% — **selective**
re-exports (`export { Foo } from <spec>`, `export { Foo as Bar } from`,
`export { default as Qux } from`) — were captured as flag-less import
edges, so leaves re-exported through them under-attributed Ca: the
importer's `import { Foo } from './barrel'` resolved to the barrel
and stopped there.

**Re-index required** — `INDEX_VERSION` bumped from 10 → 11. Old
indexes degrade gracefully to v1.93 wildcard semantics (correct for
`export *`, over-credits on `export { X } from`).

### Edge shape

Re-export edges now carry `re_export_kind`:

```python
# Wildcard
{"specifier": "./leaf", "names": [], "is_re_export": True,
 "re_export_kind": "wildcard"}

# Selective
{"specifier": "./foo", "names": ["Foo"], "is_re_export": True,
 "re_export_kind": "selective",
 "re_export_origins": [{"exposed": "Foo", "original": "Foo"}]}
```

### Resolution

When walking imports of barrel B by consumer C:

* For each name `N` in `import { N } from B`:
  * If `re_exports_named[B][N]` exists → credit that leaf (chase
    chains via the `original` name through nested barrels).
  * Else if B has wildcard re-exports → fall back to wildcard
    expansion (mixed-barrel pattern: `export { X } from './x';
    export * from './y'`).
* For `import * as ns from B` (no name context) → over-credit:
  expand both wildcard leaves AND every named leaf. Safer fallback
  when the consumer's per-name use is opaque to the parser.

### Acceptance criteria from #286

- [x] `find_importers` against a leaf re-exported via `export { X }
      from` returns importers that consume `X` through the barrel,
      and excludes importers that consume only other names from the
      same barrel.
- [x] Tests cover: simple selective, rename, default re-export,
      mixed wildcard+selective, namespace import fallback, chained
      selective with rename, wildcard regression.

Out of scope (deferred to v1.95+): TypeScript `export type { Foo }
from <spec>` type-only re-exports; per-call-site Ca attribution.

## [1.93.1] — 2026-05-09 — Graceful watcher fallback when `watchfiles` extra is missing

Closes [#281](https://github.com/jgravelle/jcodemunch-mcp/issues/281). When
`watch: true` was set in `config.jsonc` but the optional `watchfiles`
package was not installed, `serve` printed an error to stderr and called
`sys.exit(1)` before the stdio MCP handshake completed. Codex (and any
other stdio MCP client) saw a server that vanished before exposing tools,
with no surfaced reason.

Now: if `--watcher` was passed *explicitly* on the CLI, the hard-exit
behavior is preserved (the user asked for the watcher and we respect that).
If `watch` came from config or env, the server logs a warning and starts
without the watcher, so the handshake completes and `mcp__jcodemunch__*`
tools surface normally. Reported by @mdtrahan.

## [1.93.0] — 2026-05-09 — TypeScript barrel-aware import graph + dotted-name resolver fix

Closes [#283](https://github.com/jgravelle/jcodemunch-mcp/issues/283).
Investigation of NestJS's persistent `coupling = 4.4` after v1.92.0
turned up two real bugs in the import resolver, both load-bearing
across every TypeScript project the observatory tracks. **Re-index
required** — `INDEX_VERSION` bumped from 9 → 10.

### Bug 1: `export * from <spec>` was never captured

The JS/TS extractor recognized `import {X} from`, `import 'side-effect'`,
`require(...)`, `import(...)`, and `export {X} from <spec>` (selective
re-export). It did **not** match `export * from <spec>` — the wildcard
re-export, NestJS's dominant pattern. Every barrel file's forwarding
edges were silently dropped.

### Bug 2: dotted basenames weren't resolved

`resolve_specifier('./injectable.decorator', ...)` returned `None`.
Reason: `posixpath.splitext('./injectable.decorator')` yields
`('./injectable', '.decorator')`. The `_candidates` helper checked
`if not ext`, saw `.decorator` as truthy, and skipped extension
permutation. The TS convention `*.service`, `*.controller`,
`*.decorator`, `*.module`, `*.spec` (pre-`.ts`) was unresolvable.
This affected *every* dotted-name TS file in *every* TS repo.

### Diagnostic that found both

`find_importers(packages/common/decorators/core/injectable.decorator.ts)`
returned **0** importers; `grep -rl '@Injectable\b'` returned **202+**.
After the fix, `find_importers` returns **791** (includes spec files).

### What changed

- `parser/imports.py::_JS_REEXPORT_STAR` — new regex matching
  `export * from <spec>` and `export * as ns from <spec>`. Edges
  flagged `is_re_export: True`.
- `parser/imports.py::_candidates` — new branch for unrecognized
  "extensions" (dotted basenames) that treats the full string as
  the stem.
- `tools/get_dependency_graph.py::_build_re_export_map` /
  `_expand_barrel_imports` — transitively expand barrel chains in
  the forward adjacency. Cycle-safe via per-source visited set.
- `tools/find_importers.py` — barrel-aware resolution; re-export-only
  files are forwarders, not importers (excluded from results).
- `INDEX_VERSION = 10` — forces a full re-extract so `is_re_export`
  metadata populates for all repos.

### Score impact

**Most repos benefit** — Python projects (jcm 86.7 → 98.3, A) and
Node projects with conventional layouts see lifted coupling and
unchanged cycles. **Some monorepos drop** — NestJS specifically
moves C → D (77.2 → 64.7) because the now-correct graph exposes
~18 real dependency cycles in its inter-package barrel chains.
That's an honest measurement, not a regression.

### Out of scope (intentional)

- Selective re-exports (`export {Foo} from <spec>`) still treated
  as one-hop import edges, not symbol-level forwarders. Symbol-aware
  re-export tracking is a v1.94 problem; the wildcard form is
  ~95% of real-world barrel use.
- Rust `#[cfg(test)] mod tests` (inline) needs AST analysis;
  unrelated open follow-up.

### Tests
- 13 new cases in `test_find_importers.py` covering the regex,
  the dotted-name resolver, and 3-deep barrel chain expansion.
- INDEX_VERSION assertion bumped in three guard tests.
- Full suite: 3968 passed, 7 skipped.

## [1.92.0] — 2026-05-09 — coupling axis: filename-pattern filter for inline test conventions

Closes [#280](https://github.com/jgravelle/jcodemunch-mcp/issues/280).
v1.91.0 fixed the coupling axis for projects that use a `tests/`
directory, but left untouched ecosystems that co-locate tests with
source: Go (`*_test.go`), TypeScript (`*.spec.ts`, `*.test.ts`),
JavaScript (Jest/Jasmine/Karma), Ruby (RSpec), and Java (JUnit).
The first observatory rebuild after v1.91.0 confirmed the gap:
test-heavy Python repos moved C → B/A, but Gin, Cobra, and NestJS
showed Δ = 0.

This release adds a regex-based filename filter alongside the
existing directory filter. `_is_production_path` now returns
`False` for any of:

- `*_test.go` — Go's standard testing convention
- `*.test.{js,jsx,ts,tsx}` — Jest
- `*.spec.{js,jsx,ts,tsx}` — Jasmine, Karma, Angular, NestJS
- `*_spec.rb` — RSpec
- `*Test.java` — JUnit (case-insensitive)

### Changed
- `tools/get_repo_health.py` introduces `_NON_PRODUCTION_FILENAME_RE`
  alongside `_NON_PRODUCTION_DIR_NAMES`. Both filters are applied;
  failing either drops the path from the production set.

### Added
- 16 new parametrized test cases in `TestProductionPathFilter`
  covering each filename convention plus near-miss cases that
  should *not* be filtered (`protest.go`, `manifest.ts`, etc.).

### Out of scope
- Inline test conventions like Rust's `#[cfg(test)] mod tests`
  cannot be detected by path alone. Would need an AST-aware
  approach. Open follow-up; not blocking.

## [1.91.0] — 2026-05-09 — coupling axis: exclude tests/benchmarks/scripts from the metric

The coupling axis was structurally biased against well-tested
projects. Tests, benchmark scripts, and example/script files have
`Ca=0` by construction (pytest collects tests, benchmarks run from
the shell, examples are illustrative) so they trivially meet the
instability > 0.7 threshold. In jcm itself, this caused **204 of
424 files** to register as "unstable" — but 200+ of those were
just test files. The coupling score ended up at **3.8/100** when
the production code's actual unstable share was **~2%**.

This release filters non-production directories from both the
numerator AND the denominator of the coupling computation. Inbound
references *from* test files still credit production Ca (so
well-tested code looks more stable, which is correct). The peer
repos in the observatory (Django, Flask, FastAPI, NestJS) all
benefit from the same fix.

### Changed
- `_count_unstable_modules` now returns `(unstable_count, production_total)`.
  Excluded directory names: `tests`, `test`, `benchmarks`, `examples`,
  `scripts` (matched at any path component).
- `compute_radar`'s `total_files` parameter docstring clarifies it's
  the coupling-axis denominator, not the repo total. The repo total
  in the response object is unchanged.
- Observatory scores will recompute on the next weekly run; expect
  most repos to move from C → B as the artificial test-driven
  penalty disappears.

### Added
- `_is_production_path` helper exported from `tools/get_repo_health.py`
  for downstream tools that want the same filter.
- `scripts/unstable_modules_diag.py` — dev diagnostic that prints the
  raw + production-filtered unstable views side by side. Use to
  confirm whether a low coupling score reflects real architectural
  coupling or test-suite dominance.
- 14 new tests covering the path filter and the new return signature.

## [1.90.1] — 2026-05-09 — observatory: fix repo identifier in health call

Patch release. The 1.90.0 observatory pipeline passed the cloned
repo's absolute filesystem path to `get_repo_health(repo=...)`,
which then tried to parse it as `owner/name` and tripped the path-
separator guard in `sqlite_store._safe_repo_component`. Every repo
in a CI run failed identically (`ValueError: Path separator in
name: 'home/runner/work/...'`).

### Fixed
- `tools/observatory.py::index_and_health` now reads the indexed
  identifier from `index_folder`'s response (`idx_result["repo"]`)
  and passes that to `get_repo_health`, instead of stringifying the
  checkout path.
- Added regression tests covering the `repo=` argument passed to
  `get_repo_health` and the missing-identifier guard.

## [1.90.0] — 2026-05-09 — OSS code-health observatory pipeline

todo.md item #7. New `observatory` CLI subcommand that runs an
end-to-end pipeline producing **static HTML + JSON + RSS artifacts**
for a curated list of OSS repos. Hosting deliberately decoupled —
the output is plain static files that can serve from any host (Mac
Mini Caddy, GitHub Pages, S3, fly.io static, Cloudflare Pages,
whatever wins).

### Added
- **`jcodemunch-mcp observatory init`** writes a starter config with
  a 5-repo launch list (Express, FastAPI, Gin, Pydantic, jcodemunch
  self-audit). Edit and ship.
- **`jcodemunch-mcp observatory build --config <file>`** runs the
  full pipeline:
  1. Clone-or-update each repo (shallow `--depth=1`)
  2. Index it via `index_folder`
  3. Run `get_repo_health` to capture the six-axis radar
  4. Append a record to `<output>/<slug>/history.json` (newest-first,
     capped at 52 entries — a year of weekly runs; same-SHA re-runs
     are no-ops)
  5. Render `<output>/<slug>/index.html` — a per-repo landing page
     with current radar, composite trend sparkline, and 12-run
     history table
  6. Render `<output>/<slug>/feed.xml` — per-repo RSS feed
  7. Render `<output>/index.html` — leaderboard sorted by composite,
     with per-repo sparklines
  8. Render `<output>/index.json` — machine-readable leaderboard
  9. Render `<output>/feed.xml` — cross-repo RSS feed
- **Pure static-site output** — no JS framework, no runtime CSS
  fetch, no server-side rendering. Plain HTML + inline SVG + minimal
  CSS that works behind any CDN.

### Why this beats sverklo's leaderboard structurally
A static page ages. An observatory pipeline **compounds** — every
weekly run produces fresh, dated, indexable content. Six months
out, `<host>/owner--repo/index.html` is the canonical "is this repo
getting healthier or worse?" answer on the open web.

### Hosting decision still pending
The pipeline ships independent of where it lives. Once the host is
chosen, deployment is "rsync `output_dir` somewhere and point a
domain at it." Likely candidates: Caddy on the Mac Mini (per
existing federation), GitHub Pages from a `gh-pages` branch, or a
fly.io static deploy. Tracked in `todo.md` item #7's notes.

### Sample workflow (cron)
```bash
# weekly
0 6 * * 1 cd /path/to/repo && jcodemunch-mcp observatory build --config observatory.config.json
```

## [1.89.0] — 2026-05-09 — VS Code risk-density gutter

todo.md item #6. Server-side `get_file_risk` tool + `file-risk` CLI +
extension v0.2.0 with the gutter renderer.

### Added (server)
- **`get_file_risk` MCP tool.** For each function/method in a file,
  returns a 0–100 composite risk score (higher = healthier; lower =
  riskier) plus per-axis sub-scores: `complexity` (per-symbol from
  cyclomatic), `exposure` (file-level fan-in), `churn` (file-level
  30-day commit count), `test_gap` (file-level: does any test file
  import this module?). Risk levels: `green` (≥85), `yellow` (70-85),
  `orange` (55-70), `red` (<55).
- **`jcodemunch-mcp file-risk <file>` CLI subcommand.** Prints the
  full `get_file_risk` JSON to stdout with auto-detection of the
  containing repo from the file path. Used by the v0.2.0 VS Code
  extension's gutter; suitable for any other consumer that wants
  per-symbol risk data without writing a Python wrapper.

### Added (VS Code extension v0.2.0)
- **Risk-density gutter.** Colored dot in the editor gutter at each
  function/method header — yellow / orange / red by composite risk
  level. Green is *invisible* (signal-to-noise stays high). Refreshes
  on file open + on save (typing does not refresh — cyclomatic
  doesn't move with whitespace edits).
- **Hover provider.** Hover any decorated line to see the per-axis
  breakdown as a markdown tooltip with concrete signals
  (cyclomatic value, importing-files count, 30-day commits, has-tests
  yes/no) plus drill-in suggestions (`get_call_hierarchy`,
  `get_pr_risk_profile`).
- **Two new settings.** `jcodemunch.riskGutter.enabled` (default
  true), `jcodemunch.riskGutter.debounceMs` (default 600).
- Extension renamed: **"jCodeMunch — Auto Reindex + Risk Gutter"**.
- Extension version bump: 0.1.0 → 0.2.0.

### Methodology
Per-axis scoring rules live in `tools/get_file_risk.py:_score_*` and
mirror the posture of `health_radar` (linear penalties, conservative
calibration). PR an updated formula with calibration data.

### Why per-symbol exposure / churn / test_gap aren't included
At file granularity, they require `find_references` per symbol +
per-symbol git blame, which is too slow to drive a save-time gutter
refresh. v2 may add per-symbol axes once the file-level signal proves
its value.

## [1.88.0] — 2026-05-09 — Health-radar GitHub Action + `health` CLI

todo.md item #5 (closeout). v1.87 shipped the radar data layer; this
release ships the PR-time auto-comment surface that turns it into
content-marketing infrastructure.

### Added
- **`jcodemunch-mcp/.github/actions/health-radar` composite action.**
  Runs in any consumer repo's CI on `pull_request` triggers. Indexes
  the PR branch + base branch, computes the radar on each, diffs them,
  and posts a sticky markdown PR comment. **Suggestion-style — never
  blocks merges.** Uses an HTML marker (`<!-- jcm-health-radar -->`)
  on the first line so subsequent runs PATCH the same comment instead
  of spamming the PR.
- **`jcodemunch-mcp health` CLI subcommand.** Thin wrapper over
  `get_repo_health` that prints the full JSON response (or just the
  `radar` sub-field with `--radar-only`) to stdout. Designed for CI
  pipelines and shell scripting; the new Action uses it directly.
- **Action documentation** at `.github/actions/health-radar/README.md`
  with a one-paste workflow example. Two-line install — `actions/checkout@v4`
  + `uses: jgravelle/jcodemunch-mcp/.github/actions/health-radar@v1.88.0`.
- **Dogfood workflow** at `.github/workflows/health-radar.yml` runs
  the action on every PR to this repo.

### Why a comment, not a status check
A heuristic that blocks merges gets the Action disabled by the first
frustrated maintainer. The radar comment is **explanatory, not
gating** — reviewers see the deltas, decide for themselves. Status
checks remain a possible v1.89 opt-in if users ask for it.

### Action consumer surface
```yaml
- uses: jgravelle/jcodemunch-mcp/.github/actions/health-radar@v1.88.0
```
That's the whole setup. Inputs (`python-version`, `jcodemunch-version`,
`base-ref`, `github-token`) all have sensible defaults.

## [1.87.0] — 2026-05-09 — Six-axis health radar + diff helper

todo.md item #5 (foundational layer). New radar field on
`get_repo_health` + new `diff_health_radar` MCP tool. The PR-time
GitHub Action that turns this into auto-comments ships in a follow-up
release once the radar shape settles.

### Added
- **Six-axis radar on `get_repo_health` responses.** Each axis
  produces a 0–100 score (higher = healthier); composite is the
  arithmetic mean; letter grade A/B/C/D/F follows by 10-point bands:
  - `complexity` — penalises high mean cyclomatic complexity.
  - `dead_code` — penalises high `dead_code_pct`.
  - `cycles` — penalises dependency-cycle count.
  - `coupling` — penalises high `unstable_modules / total_files` ratio.
  - `test_gap` — penalises low test reachability (`get_untested_symbols`).
  - `churn_surface` — bucketed penalty on top-1 hotspot score.
  Test-gap and churn axes degrade gracefully if their underlying
  tools error — the axis is omitted from the composite (listed in
  `radar.omitted_axes`) rather than zero-scored.
- **`diff_health_radar` MCP tool.** Pure data transform: takes two
  radar payloads (e.g. base branch radar + PR branch radar) and
  returns axis-by-axis deltas, composite delta, grade movement, and
  a one-line verdict. `regressions` / `improvements` lists capture
  axes that moved more than 3 points (small fluctuations are noise).
  Designed for PR-time diff-grade reporting in CI.

### Methodology
- Penalties are linear and conservative — calibration tuned so a
  typical "average" codebase lands around C, not B+. Scoring formulas
  are in `tools/health_radar.py:_score_*` and intentionally simple
  enough to read in 30 seconds. PR an updated formula if you have
  better calibration data.
- The radar is built from existing tools (no new heavy work):
  `get_repo_health` already aggregates the inputs;
  `get_untested_symbols` provides reach-pct; the radar layer just
  normalises and fuses.

### Coming in v1.88.0
- GitHub Action scaffold that runs `get_repo_health` on the PR base
  + branch and posts the diff as a sticky PR comment. Out of scope
  for v1.87 so the radar shape can settle before the comment format
  freezes.

## [1.86.0] — 2026-05-09 — `digest` agent stand-up briefing

todo.md item #4. New tool surface; agent-facing.

### Added
- **`digest` MCP tool** — composes a tight (~200 token) markdown
  briefing from existing tools so an agent walking into a session
  knows the load-bearing changes since its last visit + the current
  high-risk surface area, without cold exploration. Each item
  references symbol_ids the agent can immediately query
  (`get_symbol_source` / `get_call_hierarchy` / `check_references`).
- **State-aware delta tracking.** Per-repo state at
  `~/.code-index/digest_state/<owner>--<name>.json` records the SHA
  the agent last saw. On the next `digest` call, the briefing
  includes only the *delta* since that SHA. First call announces
  itself as a first session and seeds the state.
- **Three sections, all capped:**
  - *Since last session*: changed files + added/modified/removed
    symbols (composed via `get_changed_symbols`)
  - *Risk surface*: top hotspots by complexity × churn (composed
    via `get_hotspots`)
  - *Dead-code candidates*: top candidates (composed via
    `find_dead_code`)
  Each section degrades silently if its underlying tool errors —
  the briefing always returns something usable.
- **`jcodemunch-mcp digest` CLI subcommand** — prints the same
  briefing for human standup reading. Defaults to resolving the cwd
  via `resolve_repo`. `--json` for the structured payload.

### Why this matters
The briefing is **agent-facing, not human-facing.** An agent that
walks into a session already knowing the load-bearing changes
spends its first 3-4 tool calls *informed* instead of *exploratory* —
that's a token savings *and* a quality lift on the first response.

### Deferred to v2
- `serve --briefing` flag for auto-emission on session open.
- SubagentStart hook auto-injection of the digest's "since last
  session" section. Wiring it into `hook-subagent-start`'s static
  briefing requires per-client config decisions; opt-in for v2.

## [1.85.1] — 2026-05-09 — `receipt` defaults to Opus rate

### Changed
- **`jcodemunch-mcp receipt`** default `--model` flipped from `sonnet`
  to `opus`. Most jcodemunch users running heavy retrieval workloads
  are on an Opus-grade model — the headline number should reflect the
  rate where savings actually move a budget needle. Sonnet and Haiku
  remain available via `--model sonnet|haiku`, and both rates are
  still surfaced inline below the headline regardless of which model
  is chosen as the headline rate.

## [1.85.0] — 2026-05-09 — `receipt` token-economy ledger

todo.md item #3. New CLI surface; no behaviour change to existing tools.

### Added
- **`jcodemunch-mcp receipt`** — parses `~/.claude/projects/**/*.jsonl`
  transcripts, extracts every `mcp__jcodemunch__*` tool call + its
  result, applies per-tool savings multipliers calibrated against the
  published RAG benchmarks, and prints a dollar-denominated ROI ledger.
  Three model rates surfaced inline (Sonnet / Opus / Haiku); default is
  Sonnet. Per-tool breakdown ranked by savings-tokens.
- **`--explain`** prints the full per-tool multiplier table + the
  methodology page so the dollar number is auditable. Multipliers are
  deliberately conservative against the 30–56× RAG-benchmark numbers —
  underestimating savings keeps the dollar figure defensible.
- **`--export FILE.csv|FILE.json`** writes raw per-tool data for
  finance teams or downstream dashboards. CSV is human/Excel-friendly;
  JSON includes the totals + dollar value at the chosen model rate.
- **`--days N`** windows to the last N days (default 30; 0 for all-time).
- **`--projects-root PATH`** override for non-default Claude Code
  install layouts.

### Methodology
- Token savings is inherently counterfactual — we can't observe what
  naive Read+Grep would have cost without running it. The per-tool
  multipliers (in `cli/receipt.py`) are modeled, not measured. They're
  set lower than published benchmark ratios so the dollar number stays
  credible. Edit them in a PR if you have better calibration data.

## [1.84.0] — 2026-05-09 — One-click install badges + auto-recency block + version-drift hint

Top-of-fold UX wins (todo.md items #1, #2). Three additive surfaces, zero
behaviour change to existing tools.

### Added
- **One-click install badges** at the top of the README for VS Code,
  VS Code Insiders, and Cursor (URI-scheme deeplinks that pre-fill the
  MCP config and prompt for confirmation), plus shields-style buttons
  for Claude Code and Codex CLI that anchor to the in-README config
  section. Eliminates the "follow these 4 steps" friction at the
  highest-intent moment (someone reading the README to decide whether
  to install).
- **Auto-generated `What's new` block** in the README, populated from
  the top of `CHANGELOG.md` between `<!-- WHATSNEW:START -->` /
  `<!-- WHATSNEW:END -->` markers. Refreshed by the new
  `jcodemunch-mcp whatsnew` subcommand as part of the release flow,
  so the block can never go stale.
- **`whatsnew.json` artifact** generated alongside the wheel/sdist.
  Machine-readable feed of recent releases for downstream consumers
  (drift probes, dashboards, syndicated changelogs).
- **First-launch version-drift probe** (new module
  `version_check.py`) — when the server starts and the
  cached `~/.code-index/last_seen_version` differs from the running
  version, emit a one-line stderr hint pointing at the matching GitHub
  release notes. Silent on first-ever launch, on no-drift, and on any
  OS-level failure. Disable with `JCODEMUNCH_NO_VERSION_HINT=1`.
- **`whatsnew` CLI subcommand** — runs the README marker refresh +
  `whatsnew.json` generation locally; intended to be called from the
  release script before `python -m build`.

### Notes for users on older versions
- After upgrading from any 1.83.x, you'll see the new one-line stderr
  hint *once* on first launch ("upgraded 1.83.x → 1.84.0 — release
  notes: ..."). It's idempotent — subsequent launches stay silent
  until the next upgrade.
- README markers are additive — no impact on existing PyPI page
  rendering or in-README anchors.

## [1.83.2] — 2026-05-08 — Docs: Codex CLI install workaround

### Changed
- **README — Codex CLI config block rewritten** to lead with the
  pre-installed-binary install pattern instead of `uvx`. Closes the
  loop on the paying-client report that drove v1.82.1's handshake
  watchdog: the watchdog correctly stays silent under the venv install,
  but the README still recommended the `uvx` invocation that caused
  the original 5h 53min hang. Now points at the venv pattern, mentions
  `JCODEMUNCH_HANDSHAKE_TIMEOUT` for diagnostics, and adds a note on
  Codex's elicitation/approval system silently declining tool calls
  in `codex review --background` mode (a Codex-side concern, tracked
  upstream).

## [1.83.1] — 2026-05-08 — Reference-tool response shapes carry line numbers + flat import aliases

### Added
- **`find_references` matches now carry `line`** — each entry in
  `references[i].matches[j]` includes the 1-based line number of the
  import statement that introduced the reference. Lets harvesters and
  IDE deeplinks jump straight to the import site instead of opening the
  file to grep. Heuristic (first line where the specifier appears in
  any quote style); falls through silently when file content isn't
  available (remote-only indexes). Strictly additive — the existing
  `specifier`, `names`, `match_type` fields are unchanged.
- **`get_dependency_graph` exposes top-level `imports` and `importers`
  arrays** — the depth-1 outgoing and incoming neighbors of the queried
  file, as a flat list of file paths. Sibling of `edges` and
  `neighbors`. Most consumers asking *what does X import / who imports
  X?* want exactly this; emitting it directly removes the need to walk
  `neighbors[file][...]` and matches the universal `imports: [...]`
  convention that flat parsers expect. `nodes`, `edges`, and `neighbors`
  remain unchanged for callers that already use them.

## [1.83.0] — 2026-05-08 — `get_file_outline` no longer drops nested symbols

Thanks to @sanyapuer (#278) for the diagnosis, fix, and the test discipline
that goes with it.

### Fixed
- **`get_file_outline` silently dropped every nested symbol** (methods,
  constructors, fields, properties) on the MCP wire. The producer emitted a
  nested tree (`children` arrays), but the compact `fo1` encoder schema was
  designed for a flat list with `parent` ids and the generic encoder loop
  iterated only top-level rows — so nested children fell on the floor. MCP
  clients saw classes as if they had no methods, breaking the canonical
  "outline first, then drill down" workflow. Reproduces on every language
  (the failure lives in language-agnostic code), confirmed on C# and
  TypeScript. Producer now DFS-flattens the tree and writes `parent` ids
  directly, matching the encoder's existing schema intent.
- **`signature` field was silently nulled on the wire** by `get_file_outline`.
  The producer emitted it, but the encoder schema's `cols` list omitted it,
  so it never made it into the compact payload. `signature` is now part of
  the encoder schema.

### Changed
- **`get_file_outline` response shape is now flat with `parent` ids** in both
  the in-process and over-the-wire response. The previous in-process tree
  shape (nested `children` arrays) is removed. Direct Python callers that
  walked `result["symbols"][i]["children"]` must rebuild the tree with a
  `parent`-based group-by — one line. The on-wire compact format already
  carried `parent`; this just makes the in-process shape match.

## [1.82.1] — 2026-05-08 — Handshake watchdog for stdio transport

### Added
- **Stdio handshake watchdog**: if the client does not call any MCP
  handler (`list_tools`, `list_resources`, `list_prompts`, `get_prompt`,
  `call_tool`) within `JCODEMUNCH_HANDSHAKE_TIMEOUT` seconds (default
  5), write a one-line stderr hint pointing at likely causes and
  workarounds. Surfaces stdio-channel corruption immediately instead of
  letting strict clients (notably Codex/rmcp) sit silent for hours.
  Fires from a paying-client report where `uvx` chatter on stdout
  poisoned the first JSON-RPC frame; the host waited 5h 53min before
  the operator gave up. Set `JCODEMUNCH_HANDSHAKE_TIMEOUT=0` to disable.

## [1.82.0] — 2026-05-08 — Worktree-aware canonical index discovery

### Added
- **`resolve_repo` now surfaces canonical candidates for Git worktrees**
  (#277). When the resolved path isn't indexed but is a linked Git
  worktree, the response includes a `canonical_candidates` list of
  already-indexed repos that share the same `--git-common-dir`, plus
  a `worktree_of`-flavoured hint. Non-Claude MCP clients (Codex, VS
  Code, Cursor, Continue) running from temporary worktree paths can
  now route to the canonical index instead of creating a redundant
  per-worktree index. The branch-local indexing escape hatch is
  preserved — `index_folder` on a worktree path still works exactly
  as before for callers who want uncommitted/branch-local state.
  Thanks to @rknighton for the well-scoped feature request and the
  Git-signal direction.

### Sequencing
- Smallest useful slice; v1.83 will add opt-in `prefer_canonical`
  auto-routing and an `aliases.jsonc` config layer for cross-server
  canonical mapping (jcodemunch + jdocmunch + jdatamunch under one
  alias). Sibling jdocmunch release will follow the same pattern.

## [1.81.3] — 2026-05-06 — telemetry.db no longer poisons bare-name resolver

### Fixed
- **Bare-name `repo` lookups crashing when `JCODEMUNCH_PERF_TELEMETRY=1`**
  (#276). `list_repos()` globbed `*.db` indiscriminately, so
  `~/.code-index/telemetry.db` was treated as an indexed repo. Worse,
  `_connect()` auto-initialised the code-index schema on it, vandalising
  telemetry.db, and `_list_repo_from_db` returned a phantom entry with
  `repo=""`. That empty owner/name then crashed `_get_bare_name_map`
  (`"".split("/", 1)` → `ValueError: not enough values to unpack`),
  aborting the bare-name cache mid-build and poisoning every subsequent
  bare-name lookup for the session. Three-point fix: explicit skip-list
  `_NON_REPO_DB_FILES` filters telemetry.db (and any future non-repo
  `.db`) at both `list_repos` call sites; `_list_repo_from_db` now
  treats missing/empty `repo` meta as not-a-repo; `_get_bare_name_map`
  skips entries without `owner/name` shape. Thanks to @Will-Luck for
  the precise root-cause analysis.

## [1.81.2] — 2026-05-06 — Security & robustness audit fixes

### Fixed
- **stdio JSON-RPC corruption**: `download_model()` no longer prints to
  stdout when invoked from the lazy `_get_session()` path inside an MCP
  tool call. The auto-download branch now passes `quiet=True`, and the
  CLI-mode prints in `download_model()` go to stderr.
- **`SqliteStore._resolved_content_dirs`** is now an instance attribute,
  not a class attribute — entries no longer leak across every store
  instance for the life of the process.

### Changed
- **Streamable-HTTP session table** now caps at
  `JCODEMUNCH_MAX_SESSIONS` (default 1024) and an idle sweeper evicts
  sessions inactive longer than `JCODEMUNCH_SESSION_IDLE_TIMEOUT`
  (default 300 s). Excess sessions get HTTP 503 with a `Retry-After`
  header. SSE/streamable-http startup logs a warning when binding to a
  non-loopback host without `JCODEMUNCH_HTTP_TOKEN`.
- **Rate-limiter `_buckets`** drops empty entries after window-eviction
  and caps at 10,000 tracked IPs (LRU eviction). Closes a slow leak that
  defeated opt-in rate limiting on public deployments.
- **`search_text` regex mode** now enforces a 2-second wall-clock
  budget across the whole call. Returns `_meta.timed_out=true` when
  exceeded so a crafted regex can't pin a worker thread.
- **`resolve_repo._git_toplevel`** runs git with `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_CONFIG_GLOBAL=/dev/null`, and `GIT_TERMINAL_PROMPT=0` so an
  untrusted workspace cannot influence the probe via system/global git
  config or hook execution.
- **Response-level secret redaction** is skipped for `get_file_content`,
  `get_symbol_source`, and `get_context_bundle` — these tools return raw
  cached source where any "secret" found is the user's own checked-in
  code, and the per-byte regex sweep was wasted latency.

## [1.81.1] — 2026-05-04 — Docs: VS Code extension on the marketplace

### Changed
- README now points at the [VS Code marketplace listing](https://marketplace.visualstudio.com/items?itemName=jgravelle.jcodemunch-mcp-vscode)
  for the on-save reindex extension (published under v1.81.0). New rows
  in the MCP-client table for "VS Code (any MCP client)" and
  "GitHub Copilot CLI / cloud agent" so the discovery surface matches
  what's actually shippable today.

## [1.81.0] — 2026-05-04 — VS Code extension, GitHub Copilot hooks, upgrade command

### Added
- **VS Code extension (`vscode-extension/`)** — new sibling project that
  listens for `onDidSaveTextDocument` and shells out to
  `jcodemunch-mcp index-file <path>` so the index stays fresh while you
  edit in any MCP client running inside VS Code (Copilot Chat, Continue,
  Cline, Roo Code, …). Per-file debounce, configurable command path,
  and an exclude-glob list. Installs via VSIX from source today;
  marketplace publish is queued. Closes the parallel-sessions
  staleness gap from issue #273.
- **`jcodemunch-mcp init --copilot-hooks`** — writes
  `.github/hooks/hooks.json` with a `postToolUse` rule so GitHub Copilot
  CLI / cloud-agent runs auto-reindex edited files. Idempotent: re-runs
  detect existing rules and append-or-skip.
- **`jcodemunch-mcp hook-copilot-posttooluse`** subcommand — adapter
  that parses Copilot's stdin payload (toolArgs as JSON-string) and
  spawns `index-file` for code-file edits. Mirrors the existing Claude
  Code `hook-posttooluse` handler.
- **`jcodemunch-mcp upgrade`** — single command that wraps
  `pip install -U jcodemunch-mcp` + `init --hooks --yes` so users no
  longer need to remember the two-step post-release path. `--no-pip`
  flag for users who want only the config refresh.
- **Server-startup version-drift probe** — on `serve` start, compares
  the installed version against the version recorded by the last
  `init` run. Logs a warning if they differ so stale hook templates
  surface immediately rather than silently breaking. Stamp file at
  `~/.code-index/last_init_version.txt`.

### Tests
- `tests/test_copilot_hook.py` — 6 cases covering the Copilot stdin
  shape (string-encoded toolArgs, dict toolArgs, alternate path keys,
  non-code skip, invalid JSON).
- `tests/test_install_copilot_hooks.py` — 4 cases covering create /
  idempotent re-run / append-to-existing / dry-run.

3645 tests passing.

## [1.80.10] — 2026-05-04 — get_dead_code_v2: same-file caller detection

### Fixed
- **`get_dead_code_v2` no longer flags functions as dead when their
  only caller lives in the same file.** Surfaced by the sverklo bench
  on the sverklo repo (issue #25 follow-up by @nike-17): `parseDeadCode`
  is defined at `benchmark/src/baselines/jcodemunch.ts:311` and called
  at line 193 of the same file, but pre-1.80.10 the no-callers signal
  only inspected files that *imported* the symbol's file — the symbol's
  own file was never checked. Combined with `unreachable_file` (the
  file isn't imported anywhere because it's the entry) and
  `not_barrel_exported`, this produced confidence-1.0 false positives
  in nested-root TS monorepos and any module where helpers are defined
  and called locally. The AST fast path now also checks
  `called_names_by_file[sym_file]`; the text-heuristic fallback scans
  the symbol's own file body excluding the symbol's own line range so
  the definition itself isn't matched.

### Tests
- `tests/test_v1_80_10_dead_code_intra_file_calls.py` — covers both
  paths: an intra-file caller is no longer flagged dead, while a
  genuinely-unreferenced function in the same file still is.

## [1.80.9] — 2026-05-03 — Lodash-class repos: force-include + call-graph fallback

### Fixed
- **Files referenced by `package.json` `main`/`module`/`exports`/`bin`
  are now indexed regardless of the per-file size cap.** Surfaced by
  the sverklo bench rerun (issue #25, lodash recall=0). Lodash 4.17.21
  ships as a single 17K-line UMD/IIFE; `lodash.js` is 548 KB; the
  500 KB default cap silently excluded it from the index, leaving every
  published method invisible to dead-code analysis. New
  `_scan_package_json_forced_paths` helper pre-scans manifests under the
  index root (skipping `node_modules`), resolves `main`/`module`/`exports`/
  `bin` targets (handles string, conditional, subpath, and bin-dict
  shapes), and exempts those files from `discover_local_files`'s size
  check.
- **`get_dead_code_v2` no longer errors out when `index.imports` is
  empty.** Single-file projects, pre-bundled libraries, and monolithic
  IIFEs have no inter-file imports — the standard 3-signal analyzer has
  no graph to walk. Pre-1.80.9 returned `{"error": "No import data..."}`,
  which rendered the tool useless on these repos. Now falls through to
  a call-graph-only mode (`_call_graph_only_dead_code`): symbols whose
  names appear in nobody's `call_references` are flagged with a single
  `no_callers` signal at fixed 0.5 confidence. `_meta.mode =
  "call_graph_only"` + an explanatory `_meta.warning` make the weaker
  evidence visible to callers.

### Tests
- 9 new regression tests in `tests/test_v1_80_9_lodash_class.py`:
  `_scan_package_json_forced_paths` covers `main`, extension-less paths,
  `exports` dict, `bin` dict, `node_modules` skip, malformed-JSON
  resilience; `TestSizeCapExemption` verifies a 600 KB main file is
  indexed despite the 500 KB cap; `TestCallGraphOnlyFallback` covers
  the no-error guarantee end-to-end and the helper's signal/confidence
  output via a synthetic index.
- Suite: **3724 passing** (+9 from 3715), 7 skipped.

### Verified against lodash 4.17.21
- v1.80.8: 26 files, 213 symbols, `lodash.js` skipped, 89 mostly-noise
  dead candidates from `fp/_baseConvert.js`.
- v1.80.9: 27 files, 706 symbols (+493), `lodash.js` indexed via
  package.json forced path, 89 dead candidates now meaningful (perf
  benchmarking helpers, build-script utilities) — published methods no
  longer in the dead list.

## [1.80.8] — 2026-05-03 — find_dead_code v1: package.json entry-point parity

### Fixed
- **`find_dead_code` (v1) had the same JS-library false-positive as
  `get_dead_code_v2` (fixed in 1.80.7).** A library file like
  `lib/express.js` had only `index.js` as importer; that index had no
  further importers; v1 misclassified the file as `all_importers_dead`
  at confidence 0.7. Now seeds live roots with files declared by any
  `package.json`'s `main`/`module`/`exports`/`bin` field. Same
  `_package_json_entries` logic as v2.

### Tests
- New `TestPackageJsonEntryPoints::test_main_field_seeds_live_roots`.
- Suite: 3715 passing (+1).

### Process
- Added `todo.md` with explicit decisions/follow-ups from the sverklo
  bench round (P2 design, ESM `export ... from` parser support, v1
  deprecation question, smart-grep parity strategic question, and a
  recently-shipped log).

## [1.80.7] — 2026-05-03 — get_dead_code_v2: JS library false-positive fix + pagination

### Fixed
- **`get_dead_code_v2` flagged genuine library exports as dead on JS
  packages that use CommonJS / ES re-export patterns.** Surfaced by
  the [sverklo retrieval benchmark](https://github.com/sverklo/sverklo/issues/25),
  which scored us 0.00 on Express's P5 dead-code task because
  `createApplication` (Express's main export) was reported as dead.
  Three independent issues compounded:
  - **Reachability BFS walked the import graph backwards.** From an
    entry point it followed reverse adjacency (importers of the entry),
    not forward (what the entry imports). Library files imported by the
    entry were therefore wrongly treated as unreachable. New
    `_build_forward_adjacency` helper + bidirectional BFS in
    `_reachable_from_entry_points`.
  - **No JavaScript-library entry-point detection.** The filename list
    was Python-flavored (`app.py`, `main.py`, etc.); a JS package's
    entry is whatever `package.json` declares as `main`/`module`/
    `exports`/`bin`. New `_package_json_entries` helper parses package
    manifests (string and conditional/subpath `exports` shapes) and
    seeds the reachability set with whatever they reference.
  - **Barrel re-export scanning didn't follow re-export chains.** A
    barrel doing `module.exports = require('./X')` doesn't textually
    mention X's exported names, so the barrel-export signal fired for
    every X-defined symbol. `_barrel_exports` now recursively follows
    CJS `module.exports = require('./Y')`, ESM `export * from './Y'`,
    and ESM `export { foo } from './Y'` (depth-bounded to 4).

### Added
- `get_dead_code_v2(max_results=100, file_pattern=None)` —
  `max_results` caps the response (pre-1.80.7 was unbounded; on a
  large repo the response could exceed 8k tokens per call). Pass `0`
  for unlimited. `file_pattern` scopes analysis to a glob like
  `src/**`. `_meta.truncated` + `_meta.total_matches` flag when capped.
- `_meta.package_json_entries` lists detected JS-library entry points.

### Changed
- **`find_references` tool description clarifies its scope.** It tracks
  imports + dbt `{{ ref() }}` edges + (with `include_call_chain=true`)
  symbols whose bodies textually mention the identifier. It does NOT
  exhaustively enumerate every call site — for that, combine with
  `search_text` or `get_call_hierarchy`. (sverklo bench scored both
  jcodemunch and gitnexus ~0.00 on P2 reference-finding; the gap is
  partly real and partly a docs problem.)

### Tests
- 9 new regression tests in `tests/test_v1_80_7_dead_code_js_reexports.py`:
  Express-like CJS repro, regex coverage for CJS / ESM `export *` /
  ESM named re-export patterns, `max_results` truncation, `max_results=0`
  unlimited mode, `file_pattern` scope filtering.
- Suite: **3714 passing** (+9 from 3705), 7 skipped.

### Credit
Thanks to [@nike-17](https://github.com/sverklo/sverklo/issues/25) for
running a fair head-to-head benchmark, identifying the upstream issue
with a clear reproducer, and explicitly calling out our P1 (symbol
definition) win at 0.65 — the highest in the matrix.

## [1.80.6] — 2026-05-03 — config --check accepts the documented one-line form

### Fixed
- **`config --check` flagged the README's documented one-line CLAUDE.md
  setup as invalid (issue #271).** The README recommends a single line —
  `Call the jcodemunch_guide tool and strictly follow its instructions.` —
  because the `jcodemunch_guide` tool returns the version-pinned tool
  policy at runtime. The drift checker, however, was greping CLAUDE.md
  for every canonical tool name and reporting 65 missing tools, pushing
  users back toward the large generated snippet.
- The CLAUDE.md drift check now treats any mention of `jcodemunch_guide`
  in CLAUDE.md as a valid one-line setup and skips the per-tool grep.
  When the full canonical list **is** missing and the one-line form is
  also absent, the warning now also mentions the one-line alternative.
- New regression test: `test_check_passes_with_one_line_jcodemunch_guide_form`.

## [1.80.5] — 2026-05-02 — Hook PATH fix on macOS/Linux

### Fixed
- **Enforcement hooks silently failed on macOS/Linux when `jcodemunch-mcp`
  was installed to `~/.local/bin`, `~/Library/Python/*/bin`, or a pipx
  venv.** Reported by a paying customer. `init --hooks` was writing the
  bare command name `jcodemunch-mcp ...` into `~/.claude/settings.json`,
  but Claude Code spawns hooks via `/bin/sh` — which uses a minimal PATH
  (`/usr/bin:/bin:/usr/sbin:/sbin`) that doesn't inherit zsh/bash's
  PATH additions. Result: `command not found: jcodemunch-mcp` on every
  hook fire.
- `init --hooks` now resolves the executable via `shutil.which()` at
  install time and writes the **absolute path** into all 7 hook commands
  (PreToolUse, PostToolUse, PreCompact, TaskCompleted, SubagentStart,
  WorktreeCreate, WorktreeRemove). Re-running `init --hooks` migrates
  legacy bare-name entries automatically (the marker fallback in
  `_merge_hooks` deduplicates them).
- Paths containing spaces (e.g. Windows `C:\Program Files\...`) are
  quoted before being written to settings.json.
- `_WORKTREE_HOOKS` / `_ENFORCEMENT_HOOKS` constants replaced with
  `_worktree_hooks()` / `_enforcement_hooks()` builders so the path
  resolves at install time, not import time.
- New `_hook_invocation()` helper centralizes the resolution logic.

### Added
- TROUBLESHOOTING.md entry "Hooks Silently Fail on macOS / Linux" with
  cause + fix.
- AGENT_HOOKS.md callout under "Python CLI Hooks" section explaining the
  absolute-path behavior and how to migrate older installs.
- 4 new tests in `test_post_tool_use_hook.py` pinning the resolution
  behavior (absolute path, quoted spaces, bare-name fallback, all 5
  enforcement events).

## [1.80.4] — 2026-05-02 — Expanded client coverage in docs

### Changed
- **README "Works with" section** now explicitly names every major MCP-compatible
  client jCodeMunch supports: Claude Code, Claude Desktop, Cursor, Windsurf,
  Codex CLI, Continue, Cline, Roo Code, Zed, Goose, Hermes Agent, Paperclip — and
  any other MCP client. Previously the table only listed four rows and buried the
  rest under "Any MCP client."
- **Added Codex CLI config snippet** (`~/.codex/config.toml` `[mcp_servers.jcodemunch]`
  block) to both README and QUICKSTART. Recurring community question.
- **QUICKSTART step 2 "Other clients"** section names the same full client list
  instead of the previous "Cursor, Windsurf, Roo, etc."

No code changes — docs only.

## [1.80.3] — 2026-04-30 — WorktreeRemove hook fix (PR #270)

### Fixed
- **`WorktreeRemove` hook never actually removed the worktree** (#269,
  DrHayt). Two independent bugs in `cli/hook_event.py`:
  1. The legacy early-return for `worktree_path` in the payload skipped
     git commands entirely on `remove` — only the manifest was written.
     The legacy contract ("caller already owns the worktree, just record
     it") only ever made sense for `create`. Fix: gate the early-return
     on `event_type == "create"`.
  2. `git -C cwd worktree remove` ran from inside the worktree being
     removed, because Claude Code's session `cwd` *is* the worktree.
     git refuses to remove a worktree from inside itself. Fix: new
     `_resolve_main_repo()` helper resolves the main repo via
     `git rev-parse --path-format=absolute --git-common-dir`, falls back
     to `cwd` on failure. Both `git worktree remove` and `git branch -D`
     now run from the main repo root.
- When `worktree_path` is provided without `name`, the branch name
  (`worktree-<name>`) is derived from the path basename for cleanup.
- Two new tests in `tests/test_watch_claude.py` pin both regressions.

## [1.80.2] — 2026-04-30 — streamable-http TypeError fix (PR #268)

### Fixed
- **streamable-http transport raised `TypeError: 'NoneType' object is not
  callable` on every POST/DELETE** (#267, DrHayt). `handle_mcp` is registered
  as a Starlette endpoint, and Starlette's `request_response` wrapper invokes
  the return value as an ASGI response: `await response(scope, receive, send)`.
  The function wrote the full HTTP exchange directly via
  `transport.handle_request()` then implicitly returned `None`, so Starlette
  tried to call `None(...)`. Returning a no-op `_AlreadySent` ASGI sentinel
  satisfies the wrapper without sending a duplicate response. The timeout
  branch now returns the `StarletteResponse` directly instead of manually
  awaiting it on `request._send`.
- New `tests/test_streamable_http_integration.py` exercises the actual
  Starlette routing stack via `httpx.ASGITransport` (POST, DELETE, session
  reuse, 405 on disallowed methods, sequential POSTs). Existing
  `test_streamable_http_sessions.py` re-implemented routing with mocks and
  silently masked this regression.

## [1.80.1] — 2026-04-26 — Scala 3 parser fix (PR #262)

### Fixed
- **Scala 3 symbol extraction** (#262, irreversible-paths). The Scala
  language spec only matched `function_definition` nodes, silently
  dropping abstract defs (`function_declaration`), fields
  (`val_definition` / `var_definition`), and type aliases
  (`type_definition`). It also assumed the identifier field was named
  `name`, but tree-sitter-scala uses `pattern` for val/var nodes.
  `SCALA_SPEC` now declares the additional node types and the
  `pattern` name field. On a real Scala 3 + Spring Boot project the
  indexed symbol count went from 734 → 1,475 (≈2x).
- New `tests/test_scala_parser.py` covers Scala 3 significant-
  indentation syntax: traits, classes, objects, enums, vals, vars,
  type aliases, abstract defs, and nested methods.

## [1.80.0] — 2026-04-25 — closes the 7-release telemetry initiative

### Added — Embedding drift detector
- **`retrieval/embed_drift.py`.** Pins a 16-string deterministic canary
  (`CANARY_STRINGS`, append-only) and embeds it with the active provider.
  Snapshot persists to `~/.code-index/embed_canary.json` as
  `{provider, model, dim, captured_at, strings, vectors}`.
  `check_drift(threshold=0.05)` re-embeds the canaries and compares
  cosine similarity to the snapshot; alarm fires when max cosine
  distance exceeds the threshold.
- **`check_embedding_drift` MCP tool.** First-time use with `capture=True`
  pins the canary; subsequent calls run the drift check. `force=True`
  re-pins (use after intentional provider/model upgrades). Catches
  silent provider model changes (Gemini revs, OpenAI weight updates,
  bundled-ONNX swaps) that quietly degrade hybrid retrieval.
- Reuses `embed_repo._detect_provider` so the canary tracks the live
  encoder (no duplicate provider detection).
- Registered in canonical names, standard tier, default
  `tool_tier_bundles`, init template, CLAUDE.md Utilities snippet,
  EXCLUDED_FROM_STRICT, AUTO_WATCH_EXCLUDED.
- 17 tests in `tests/test_embed_drift.py` covering canary contract
  stability, capture idempotency, force re-pin, no-provider error
  path, zero-drift / large-drift / minor-noise paths, the wrapping
  tool's capture/check flow, cosine helper edge cases, and server
  registration.

### Verified
- v1.75.0 replay benchmark gate passes 1.0/1.0/1.0 — no nDCG/MRR/Recall
  regression.

### Telemetry initiative complete (v1.74.0 → v1.80.0)
1. v1.74.0 — telemetry foundation (per-tool latency + analyze_perf + opt-in SQLite sink)
2. v1.75.0 — retrieval confidence + token-accounting baseline
3. v1.76.0 — replayable benchmark + retrieval-quality harness
4. v1.77.0 — per-symbol freshness markers
5. v1.78.0 — ranking ledger
6. v1.79.0 — online weight tuning
7. v1.80.0 — embedding drift detector (this release)

## [1.79.0] — 2026-04-25

### Added — Online weight tuning (consumes the v1.78.0 ledger)
- **`retrieval/tuning.py` — `WeightTuner`.** Reads the `ranking_events`
  table for a given repo, splits events by signal (`semantic_used`,
  `identity_hit`), and proposes a `±0.05` step on the corresponding
  weight when the mean confidence delta between groups exceeds 0.05.
  Bounded — `semantic_weight` clamps to `[0.1, 0.8]` and
  `identity_boost` clamps to `[0.5, 2.0]`. Defaults to a `min_events=50`
  threshold so small samples can't move weights.
- **`tune_weights` MCP tool.** Runs the tuner over every repo in the
  ledger (or a single `repo`). `dry_run=True` proposes deltas without
  writing the file; `explain=True` includes the per-signal correlations
  used in the proposal. Registered in canonical names, standard tier,
  default `tool_tier_bundles`, init template, CLAUDE.md Utilities
  snippet, EXCLUDED_FROM_STRICT, AUTO_WATCH_EXCLUDED.
- **`~/.code-index/tuning.jsonc`.** Per-repo overrides persist as
  `{repos: {<repo_id>: {semantic_weight?, identity_boost?,
  learned_from_events, captured_at}}}`. Auto-generated header notes
  that `tune_weights` will overwrite per-repo entries on the next run.
- **Query-time application.** `search_symbols` resolves the active
  `semantic_weight` via `tuning.get_semantic_weight(repo)` when the
  caller passed the default `0.5`; explicit non-default values always
  win. The override is only applied when semantic / fusion modes are
  actively engaged — pure-BM25 calls are untouched.
- 17 tests in `tests/test_weight_tuning.py` covering get_semantic_weight
  precedence (explicit / override / default), tuner gating
  (insufficient events, no-signal, semantic-helps, semantic-hurts,
  dry-run), JSONC round-trip, tune_weights tool (no repos, all repos,
  --explain), server registration, and query-time override.

### Verified
- v1.75.0 replay benchmark gate passes 1.0/1.0/1.0 — no nDCG/MRR/Recall
  regression. The tuner is additive: with no learned overrides on disk
  the default behavior is identical.

## [1.78.0] — 2026-04-25

### Added — Ranking ledger (data-collection only; no behavior change)
- **`ranking_events` table in `~/.code-index/telemetry.db`.** Schema:
  `(ts, repo, tool, query_hash, query, returned_ids[json], top1_score,
  top2_score, confidence, semantic_used, identity_hit, repo_is_stale)`.
  Indexed on `repo`, `ts`, `query_hash` for fast aggregation.
- **`record_ranking_event(...)` in `storage/token_tracker.py`.** Append
  helper that no-ops when `perf_telemetry_enabled` is false. Wired into
  every retrieval path: `search_symbols` (BM25, semantic/hybrid, fusion),
  `plan_turn`, both `get_ranked_context` paths. Each call logs the
  query, the top returned IDs (cap 50), the score gap, the confidence
  value, and signals for whether semantic and identity-match channels
  fired.
- **`extract_ledger_features(scored_results)` helper** in
  `retrieval/confidence.py` — uniform `(top1_score, top2_score,
  identity_hit)` extraction so each retrieval tool feeds the ledger
  with the same feature shape.
- **`analyze_perf(ledger=True)` view.** New optional flag pulls
  `ranking_events` and returns `ranking_ledger` summary with
  per-repo (events, avg_confidence, identity_hits, semantic_used,
  stale_events) and per-tool (events) aggregates. Window filter
  (1h/24h/7d/all/session) applies to the ledger query.
- **`ranking_db_query(...)` reader** mirrors `perf_db_query` for the
  ledger table.
- 12 tests in `tests/test_ranking_ledger.py` covering feature
  extraction, persistence on/off, query-hash stability, repo/tool
  filters, ledger summary aggregation, and search_symbols invocation
  capture.

This release stores data; v1.79.0 will train per-repo ranking weights
from it.

## [1.77.0] — 2026-04-25

### Added — Per-symbol freshness markers
- **`_freshness ∈ {fresh, edited_uncommitted, stale_index}`** on every
  symbol returned by `search_symbols` (BM25, semantic, fusion paths),
  `get_symbol_source` (single + batch), `get_context_bundle` (single +
  multi), and both `get_ranked_context` paths. Lets agents decide when
  to trust a result and when to reindex first.
- **`retrieval/freshness.py` — `FreshnessProbe`.** Per-tool-call helper
  that classifies each result's file:
  * `stale_index` — index SHA differs from live `git rev-parse HEAD`
    (whole index is behind, every result inherits the marker).
  * `edited_uncommitted` — repo SHA matches but the file's on-disk mtime
    is newer than the indexed mtime (file was edited since indexing).
    Compares against `CodeIndex.file_mtimes` per-file when available;
    falls back to the index-wide `indexed_at` timestamp.
  * `fresh` — neither condition triggered.
  Probe caches `git HEAD` and per-file mtime stats so classifying many
  symbols in one call stays cheap.
- **Confidence integration.** `attach_confidence` now receives
  `is_stale=probe.repo_is_stale` from each retrieval path, so the
  freshness component of the confidence score finally reflects real
  index drift instead of always reading 1.0.
- **`_meta.freshness` summary** on each result envelope:
  `{fresh, edited_uncommitted, stale_index, repo_is_stale}` counts
  across the returned set — a one-glance health check.
- 11 tests in `tests/test_freshness.py` covering probe construction,
  SHA mismatch, per-file mtime comparison, in-place annotation,
  end-to-end through `search_symbols` (with a forced post-index edit)
  and `get_symbol_source`.

## [1.76.0] — 2026-04-25

### Added — Replayable benchmark + retrieval-quality harness
- **`benchmarks/replay/` module.** Replayable retrieval-quality fixtures
  + scoring metrics. Fixtures live in `benchmarks/replay/fixtures/*.json`
  with shape `{name, repo, repo_sha, queries: [{query, expected_top_k}]}`.
  Each fixture is the contract a release must keep meeting.
- **`metrics.py` — nDCG@k, MRR@k, Recall@k.** Pure-Python, dependency-free
  implementations. Binary-relevance nDCG normalized by ideal DCG;
  Recall = hits in top-k / total relevant; MRR = reciprocal rank of
  first relevant within top-k. `aggregate()` averages per-query metrics
  across a fixture.
- **`run_replay.py` — harness with regression gate.** Runs every query
  in a fixture through `search_symbols`, computes per-query and overall
  metrics, optionally writes `benchmarks/replay/results/{fixture}-v{X}.json`.
  `--baseline X.Y.Z --gate 0.02` exits non-zero if any aggregate metric
  drops by more than 2% (configurable) vs the saved baseline. Missing
  baseline counts as a pass with a "first run" note so new fixtures
  don't break CI.
- **`self_v1_75_0` seed fixture (10 queries) locked at 1.0 nDCG/MRR/Recall.**
  Covers every major surface — `search_symbols`, `compute_confidence`,
  `analyze_perf`, `record_tool_latency`, `_State`, `ndcg_at_k`,
  `build_identity_channel`, `ENTRY_POINT_DECORATOR_RE`, etc. Future
  releases run `run_replay.py --baseline 1.75.0 --gate 0.02` against
  this fixture as a CI regression guard.
- 19 tests in `tests/test_replay_metrics.py` (metric math, harness
  gate logic, fixture-shape contract, baseline lock).

## [1.75.0] — 2026-04-25

### Added — Retrieval confidence + token-accounting baseline
- **`_meta.confidence` (0–1) on retrieval results.** New
  `retrieval/confidence.py` exposes `compute_confidence` /
  `attach_confidence`. The score is a weighted geometric mean of four
  sub-signals: top-1 vs top-2 score gap, top-1 absolute strength, identity
  match presence, and result freshness. Wired into all three
  `search_symbols` paths (BM25, semantic/hybrid, fusion), `plan_turn`, and
  both `get_ranked_context` paths. Agents can read the number to gate
  follow-up retrieval calls — high-confidence results don't need a second
  query, low-confidence results suggest widening or asking the user.
- **Token-accounting baseline harness.**
  `benchmarks/harness/capture_token_baseline.py` snapshots the current
  session's `get_session_stats` + `latency_stats` to
  `benchmarks/token_baselines/v{VERSION}.json`. Schema: per-tool
  `{calls, tokens_saved, p50_ms, p95_ms, max_ms}` plus a session summary.
- **`analyze_perf(compare_release="...")`.** New parameter loads a saved
  baseline and returns `baseline_diff` — per-tool deltas in tokens_saved
  and latency vs the live session. Used to catch regressions in
  compression ratio or per-tool latency drift across releases. Missing
  baseline is reported via `baseline_meta.found=false` rather than an
  error.

## [1.74.0] — 2026-04-25

### Added — Telemetry foundation (kicks off the multi-release telemetry initiative)
- **Per-tool latency tracking.** Every `call_tool` invocation is now timed
  (high-resolution `time.perf_counter`) and recorded into a per-tool ring
  buffer (cap 512 entries) inside `_State`. `get_session_stats` now returns
  a `latency_per_tool` field with `{count, p50_ms, p95_ms, max_ms, errors,
  error_rate}` for every tool that ran during the session.
- **`analyze_perf` MCP tool.** New utility tool surfacing latency + cache
  telemetry. Defaults to the in-memory session ring; pass
  `window=1h|24h|7d|all` to query the persistent perf SQLite db. Returns
  the `top` slowest tools by p95 alongside the coldest cache hit-rates,
  scoped optionally to a single `tool` name. Registered in canonical tool
  names, standard tier bundle, default `tool_tier_bundles`, init template,
  and the CLAUDE.md Utilities snippet category. Excluded from strict
  freshness mode and auto-watch (read-only telemetry).
- **Opt-in perf SQLite sink.** When `perf_telemetry_enabled: true` in
  config (or env `JCODEMUNCH_PERF_TELEMETRY=1`), the latency recorder also
  appends rows to `~/.code-index/telemetry.db` (`tool_calls(ts, tool,
  duration_ms, ok, repo)`, indexed on `tool` and `ts`). Rolling cap via
  `perf_telemetry_max_rows` (default 100k). Disabled by default — every
  session tracks latency in memory but only the explicit opt-in writes a
  durable file.

### Fixed
- (Carried from v1.73.2): `get_repo_health` no longer raises NameError on
  decorated symbols.

## [1.73.2] — 2026-04-25

### Fixed
- **`get_repo_health` raised `NameError: _ENTRY_POINT_DECORATOR_RE is not defined`
  whenever any analysed symbol carried a decorator.** The constant was imported
  correctly as `ENTRY_POINT_DECORATOR_RE` (no leading underscore) from
  `parser/context/_route_utils.py`, but two call sites referenced
  `_ENTRY_POINT_DECORATOR_RE` (typo). Fixed in
  `tools/find_dead_code.py:74` and `tools/get_dead_code_v2.py:214`.
  Existing tests didn't catch the regression because their fixtures used
  bare functions; new test `test_no_nameerror_when_decorators_present`
  exercises the decorator-skip branch with an `@app.route` symbol.

## [1.73.1] — 2026-04-23

### Fixed
- **Register `get_watch_status` in tier bundles, config template, and CLAUDE.md
  snippet.** v1.73.0 added the tool to `_CANONICAL_TOOL_NAMES` and
  `_TOOL_TIER_STANDARD` but missed `DEFAULTS["tool_tier_bundles"]["standard"]`,
  `generate_template()`'s `all_tools` list, and the Utilities category in
  `_generate_claude_md_snippet()`. Effect on 1.73.0 users: `get_watch_status`
  was invisible in the `standard` tier, absent from new config templates, and
  absent from generated CLAUDE.md snippets. CI now guards this via
  `test_all_canonical_tools_accounted_in_tier_bundles`.

## [1.73.0] — 2026-04-23

### Added
- **`watch-all` subcommand + login-service installer.** Auto-discovers every
  locally-indexed repo via the existing `IndexStore.list_repos()` registry and
  keeps all of them fresh with one `WatcherManager`. Rediscovers on a
  configurable interval (default 30s) so repos added to the registry later are
  picked up without a restart. Skips GitHub repos (empty `source_root`) and
  repos whose source_root has been deleted.
- **`watch-install` / `watch-uninstall` / `watch-status` subcommands.** Install
  the watcher as a login service on all three platforms from one command:
  systemd user unit (Linux), launchd agent (macOS), Task Scheduler task
  (Windows). Service shells `sys.executable -m jcodemunch_mcp watch-all`, so it
  inherits the active virtualenv and skips any per-event `uvx` overhead.
- **`get_watch_status` MCP tool.** Surfaces per-repo staleness, in-progress
  reindexes, and service health to agents — callable before acting on anything
  that assumes a fresh index.

## [1.72.0] — 2026-04-21

Correctness + reach release: fixes six tier-1 MUNCH encoders that had shipped
against imagined response shapes (silent data loss in `get_blast_radius`,
`find_importers`, `find_references`, `get_signal_chains`, `get_tectonic_map`,
`get_dependency_cycles`), and adds generic monorepo/`extends`-chain support to
the tsconfig alias loader so path aliases resolve in any workspace layout.

### Fixed
- **MUNCH tier-1 encoder alignment** (#256, credit @MariusAdrian88). Six
  encoders realigned to the actual tool responses. Encoding IDs bumped
  (`br1→br2`, `fi1→fi2`, `fr1→fr2`, `sc1→sc2`, `tm1→tm2`, `dc1→dc2`); old
  payloads were lossy and are retired cleanly.
  - `get_blast_radius`: `symbol` now encoded as a nested dict (was serialized
    as Python repr string); `confirmed`/`potential` tables replace the
    always-empty `affected_symbols`/`importer_files`; correct scalar names
    (`importer_count`, `confirmed_count`, `potential_count`, `overall_risk_score`).
  - `find_importers`: columns `[file, specifier, has_importers]` (was invalid
    `line`/`column`); scalar `file_path` (was `file`); batch mode preserved via
    JSON blob.
  - `find_references`: nested `references[].matches[]` now flattened and
    regrouped round-trip; empty-match groups preserved via `__empty_groups__`.
  - `get_signal_chains`: all three response modes supported (discovery, lookup,
    no-gateway) with mode-specific `_meta` dispatch; correct column set
    including `gateway_name`, `chain_reach`, `depth_from_gateway`.
  - `get_tectonic_map`: correct plate/drifter columns (`plate_id`, `anchor`,
    `cohesion`, `majority_directory`); `isolated_files` moved to JSON (is a
    `list[str]`, not a dict table); optional plate fields pruned on decode.
  - `get_dependency_cycles`: `list[list[str]]` shape now transformed via
    `\x1f` separator (previously silently skipped by dict-only row guard).

### Added
- **`__stypes` type preservation in `schema_driven.py`** (#256). Non-string
  scalar types (int, float, bool) are hinted on encode and coerced on decode,
  reaching parity with `generic.py`. Old payloads without `__stypes` fall back
  to string decoding — backward-compatible.
- **Generic tsconfig discovery walker** (#257, credit @bertPB). Replaces the
  previous root-only lookup with a depth-limited (≤5) walker that finds all
  `tsconfig*.json` / `jsconfig*.json` files and follows each file's `extends`
  chain. Handles TS 5+ array-form `extends`, circular chains (via `seen_cfg`
  dedup), and package refs like `@tsconfig/recommended` (boundary-checked via
  `relative_to(root)`). Nx/Turborepo layouts (`libs/`, `services/`,
  `modules/`) and repos that centralize aliases in `tsconfig.base.json` /
  `tsconfig.paths.json` now resolve correctly. Skip list extended with
  `.turbo` and `.vercel`.

## [1.71.0] — 2026-04-21

One-line CLAUDE.md / AGENT.md via the new `jcodemunch_guide` tool. Resolves
issue #255 (credit @rsubr).

### Added
- **`jcodemunch_guide` tool.** Returns the version-current policy snippet —
  the exact text `jcodemunch-mcp claude-md --generate` emits, plus the running
  `version`. Agents can now keep a one-liner such as
  `"Call jcodemunch_guide and strictly follow its instructions."` in their
  CLAUDE.md / AGENT.md and never hand-edit it again when the tool surface
  changes. Force-included alongside `set_tool_tier` / `announce_model`, so it
  can't be hidden by `disabled_tools` or tier filtering. No repo context
  needed; idempotent.

## [1.70.0] — 2026-04-19

Context-optimization release — default detail level and token-budget bug fix.
Minor bump (not 2.0) because the changes are forward-compatible: explicit
`detail_level` values still honored, `token_budget` semantics unchanged in
signature. Agents that never passed `detail_level` see smaller responses on
broad discovery queries.

A/B benchmark (self-indexed, 16 discovery queries, `max_results=10`):
- **21.3% total token savings** vs prior default (median 18.9%, max 33.7%)
- **100% `token_budget` compliance** in full mode (was overshooting 5-20x)
- **Narrow queries (`max_results<5`) identical** to prior behavior

Results: `benchmarks/results_v1.70.0.md`. Reproduce: `PYTHONPATH=src python benchmarks/harness/ab_v1_70_0.py`.

### Changed
- **`search_symbols(detail_level=)` default is now `"auto"`.** Resolves to
  `"compact"` for broad discovery (no `token_budget`, no `debug`, `max_results
  >= 5`) and to `"standard"` otherwise. Explicit `detail_level` values are
  always honored — never silently overridden. Agents that didn't pass
  `detail_level` now get the cheapest representation by default for discovery
  queries, matching the published guidance. CLI (`cli/cli.py`) and benchmark
  harness pin `detail_level="standard"` explicitly to preserve output shape.
- **`meta_fields` documented default corrected to `[]`.** Runtime default in
  `config.py` has always been `[]` (strip `_meta` entirely). `CONFIGURATION.md`
  previously showed `null`, which caused agents to emit `_meta` fields users
  weren't opting into. Code is canonical; docs now match.
- **`use_ai_summaries` documented default corrected to `"auto"`** with the
  actual type (`bool or str`). Runtime accepts `"auto"`/`true`/`false`; docs
  previously reported a stale `true` default.

### Fixed
- **`search_symbols` full-mode respects `token_budget`.** The packer previously
  saw each result's pre-materialization `byte_length` (signature + summary
  only), then full-mode appended `source`, `docstring`, and `end_line` AFTER
  packing, overshooting declared budgets by 5-20x on real symbols. Full
  payload is now materialized before packing via a shared
  `_materialize_full_entry` helper; `byte_length` reflects the actual payload
  the caller will see. Applied uniformly to the main BM25 path, the semantic
  (embedding) path, and the fusion (WRR) path — all three had the same bug.
  Fuzzy-path entries materialize inline instead of via a trailing pass.

### Tests
- `tests/test_search_symbols_defaults.py` (11 new tests) — auto-resolution
  decision matrix, explicit-override honoring, full-mode budget adherence
  across BM25/fusion paths, edge cases (empty query, zero results, cache warm).
- `tests/test_docs_config_parity.py` — documented defaults in
  `CONFIGURATION.md` now parity-checked against the `DEFAULTS` dict in
  `config.py`. CI will fail if a default drifts between code and docs.
- `tests/test_schema_budget.py` — schema-token count tracked per
  `(tool_profile, compact_schemas)` against `benchmarks/schema_baseline.json`
  with a 5% drift tolerance; `core + compact_schemas=True` asserted under
  4000 tokens (v2-success-criterion guardrail).

## [1.63.1] — 2026-04-19

Hotfix — `get_hotspots` (and therefore `get_repo_health`, which aggregates
it) crashed with `UnboundLocalError: local variable 'top' referenced before
assignment` on any repo where `source_root` wasn't a git worktree — most
notably `index_repo` extracts living in the cache dir, where this is the
common case, not the exception.

### Fixed
- **`get_hotspots` no-git path de-indented (closes crash in `get_repo_health`).** The candidate-building block was nested inside the `if rc_check == 0:` branch, so when `git rev-parse --git-dir` failed (no `.git` reachable from `source_root`), the block never ran and `top` was never bound. The existing test suite masked this because `tmp_path` on the maintainer's machine nests inside the project's own git worktree, so `rev-parse` accidentally walked up and succeeded. Block is now de-indented to function-body level; a regression test monkeypatches `_run_git` to force the no-git path regardless of where tmp_path lives.

## [1.63.0] — 2026-04-19

Python import resolution — fixes under-resolution on layouts where the
effective source root is injected at runtime (conftest.py sys.path shims,
`PYTHONPATH`, setuptools `package_dir` with nested roots). Minor bump
because resolution behavior changes on affected repos; no wire-format or
session-state changes.

### Fixed
- **`resolve_specifier` handles runtime-injected Python source roots (closes #252).** On layouts where a nested package dir (e.g. `src/agent_platform/`) is simultaneously a real package AND a sys.path entry, `_python_source_roots` saw only the outer parent as a root, so specifiers like `shared.core.runtime` never resolved — causing `find_importers`, `find_references`, `get_blast_radius`, `get_dependency_graph`, and `find_dead_code` to under-report on affected files. The resolver now falls back to a cached `package_basename → parent_dirs` index and retries against the parents of any package whose name matches the specifier's first dotted segment. Scoped by concrete first-segment evidence in the import itself, not a broad suffix sweep — zero false-positive risk, memory bounded by distinct package-name count. Credit to @vaionetalex (skleung.uk@gmail.com) for the diagnosis and repro.

## [1.62.0] — 2026-04-19

Audit remediation — wire-format and session-state changes. Minor bump
because the MUNCH generic encoder gains new escape sequences; old
payloads still decode but new payloads are forward-only.

### Fixed
- **MUNCH generic encoder escapes newlines in quoted scalars (F1).** `assemble` joins sections with `\n\n` and `split_sections` splits on the same delimiter. Scalars containing a blank line (docstrings, stacktraces, multi-paragraph summaries routed through the generic fallback) truncated mid-record on decode; blocks after the truncation were misclassified as tables. `\\`, `\n`, `\r` are now escaped on write and reversed on read. Old un-escaped payloads still decode cleanly — the decoder only consumes known escape sequences.
- **MUNCH schema embed escapes separator characters (F11).** The `__tables` embed used `:` and `|` as separators with no escape for keys or column names containing those characters. Keys like `"stats:by_file"` and columns like `"col|weird"` are now percent-encoded (`%3A`, `%7C`, `%2C`, `%25`) on write and reversed on read.
- **Nested-table decode no longer silently drops data (F5).** When a parent key carried both a flattened table and a heterogeneous sibling that fell back to `__json.<parent>`, the decoder restored the parent as the JSON scalar first and the nested-table pass then silently skipped the assignment behind an `isinstance(..., dict)` guard. The scalar is now wrapped in `{"_scalar": ...}` so the nested table survives.
- **Per-session tier tracking switches to WeakKeyDictionary (F2 + F3).** `id()` can be reused after GC, so a freed session's tier override could be inherited by a newly-allocated replacement at the same address; separately, the LRU cap of 256 silently reset a live session's tier to config default under session-churn load. Both addressed by keying on a per-session UUID tracked in a WeakKeyDictionary: entries disappear exactly when the session is collected, no cap, no eviction, no id() reuse. Constant `_SESSION_TIER_CAP` removed.
- **`mermaid_viewer_path` config accepts bare command names (F6).** Configuring a bare command (`"mmd-viewer"`) was rejected by the strict executable-file-on-disk check and never reached `shutil.which`. Bare names (no path separator) now defer to `shutil.which`; absolute and relative paths still go through the strict check so a typo in a configured path isn't silently replaced by something on `PATH`.

### Changed
- Removed `_SESSION_TIER_CAP` constant and LRU-cap eviction from `server.py`. Test helpers and docs updated accordingly.

## [1.61.1] — 2026-04-19

Audit remediation — low-risk fixes. No wire-format or session-state
changes; all installs can upgrade without migration.

### Fixed
- **`model_tier_map` substring match normalizes both sides (F4, closes #249).** Layer-3 substring matching compared raw config keys against the normalized incoming model id, so provider-prefixed entries like `"anthropic/claude-haiku"` silently failed to match `"claude-haiku-4-5"`. Keys are now normalized the same way incoming ids are before the substring check; longest-match semantics preserved.
- **Bearer-token redaction requires the `Authorization` anchor (F9).** The previous pattern treated the Authorization prefix as optional, so prose containing the word `Bearer` followed by 20+ word-characters (docstrings, CLI help, example text) tripped redaction. We lose the naked-token-in-prose detection path in exchange for no false positives on ordinary documentation.
- **Redaction depth cap collapses all scalars past depth 20 (F10).** Past the cap, strings under 16 characters were returned raw on the theory that short strings cannot hold a secret. Recognizable credential prefixes (AWS access-key, GitHub-token prefixes) fit under that limit, so the carve-out leaked them. Any scalar or container at the cap now becomes the `[REDACTED:depth_exceeded]` sentinel.
- **`render_diagram` edge resolution picks the correct direction (F7).** On mutually-recursive pairs a symbol appears in both `callers` and `callees` with different `resolution` tiers. The edge-style lookup scanned the two lists concatenated and picked whichever id matched first, which surfaced the caller's resolution on every edge touching that symbol. Caller vs callee edges now consult their own list.
- **`render_diagram` honors `max_nodes` in the `impact_by_depth` branch (F8).** The depth-bucketed rendering ignored `max_nodes` entirely and emitted every file at every depth. Budget is now tracked per bucket; excess accumulates into `pruned_count` instead of blowing past the caller's cap.

## [1.61.0] — 2026-04-18

### Added
- **Per-session tier state makes `adaptive_tiering: true` safe under HTTP (#253).** The process-global `_session_tier_override` is now a session-keyed `OrderedDict` of overrides (LRU-capped at 256 entries). The key is the MCP session's `session_id` when present, otherwise `id(request_context.session)`; stdio and tests fall through to a `"__default__"` sentinel so their behavior is byte-for-byte unchanged. One HTTP client's `plan_turn(model=...)` or `set_tool_tier(...)` call no longer leaks into every other concurrent client on the same process. `set_tool_tier(None)` now evicts the entry rather than storing `None`. New `_reset_session_tiers()` test helper; five new tests cover cross-session isolation, `session_id` preference over identity, LRU eviction, and `None`-evicts semantics.

### Changed
- **HTTP + `adaptive_tiering: true` is supported again; v1.60.1's `HttpAdaptiveTieringError` refuse-to-start removed (#253).** The guard existed only because the underlying state was process-global; with session-keyed state the misconfiguration no longer exists. The startup hook is now `_note_adaptive_tiering_transport(transport)` — an INFO log when adaptive_tiering runs under `sse` / `streamable-http`, purely for observability. Operators who intentionally rely on the hard-fail should pin v1.60.1.

## [1.60.1] — 2026-04-18

### Changed
- **`adaptive_tiering: true` + HTTP transport now refuses to start (#248).** Previously emitted a startup WARNING. Process-global tier state leaks across concurrent HTTP clients — one client's `plan_turn(model=...)` flip silently changes the tool surface for every other concurrent client on the same server. That's a misconfiguration, not a heads-up condition. The server now logs an ERROR and aborts via `HttpAdaptiveTieringError` (a `SystemExit` subclass) on both `sse` and `streamable-http` transports. Stdio is unaffected. Existing installs running `adaptive_tiering: false` (the default) see zero change. A per-session tier-state fix that would make HTTP + adaptive_tiering actually safe remains tracked for a future release.

### Fixed
- **`model_tier_map` substring match now picks the longest-matching key (#249).** Previously depended on dict iteration order — a config with both `"claude": "standard"` and `"claude-haiku": "core"` could resolve `claude-haiku-4-5` to either tier depending on config write order. Longest-match-wins makes specific entries beat broader ones regardless of order. Three regression tests cover both insertion orders and the broader-key-still-matches-non-haiku case.

## [1.60.0] — 2026-04-18

### Added
- **Tiered tool surface with runtime model-driven switching (#246, @MariusAdrian88)** — jcodemunch's 60+ tools now narrow per model at runtime, so request-capped plans stretch further when a small model is driving. Three tiers (`core` / `standard` / `full`); tier bundle contents are user-editable in `config.jsonc` (moved from hardcoded constants in `server.py`) with baked-in defaults as a fallback. New `model_tier_map` config maps model identifiers to tiers via layered matching: normalize (lowercase, strip provider prefix, strip bracket/date suffixes) → exact → glob → substring → `*` wildcard → `full` fallback. Opt-in `adaptive_tiering: true` enables runtime switching; defaults preserve prior behavior. New tools: `set_tool_tier(tier=...)` for explicit override, `announce_model(model=...)` for non-plan_turn flows; `plan_turn(model=...)` piggybacks tier flips on the opening-move call (zero extra MCP requests). Both tier-control tools are force-included at list-time and call-time so users can never strand themselves via `disabled_tools`. Bundle ∩ `disabled_tools` conflicts emit startup WARNING + `config --check` diagnostic. HTTP transport + `adaptive_tiering: true` emits startup WARNING about process-global tier state leaking across clients (hard refusal tracked in #248).
- **Server error responses include `summary` field** — unexpected exceptions now add a short, sanitized `RuntimeError: ...` summary alongside the generic top-level `error`. Full traceback still lands in the server log.

### Fixed
- **MUNCH `search_text` round-trip restored (#246)** — the on-wire schema declared flat columns `file|line|line_content`, but the real tool response is nested `{results:[{file, matches:[...]}]}`. Two matches in the same file collapsed to one null row; the matched line `text` was read as `line_content` (wrong column); `before`/`after` context arrays were dropped entirely. Rewritten with flatten/regroup around the real nested shape; columns now `file|line|text|before|after` with context lines riding as JSON strings inside CSV cells (adversarial-tested for embedded commas, quotes, newlines). Encoding ID bumped `st1` → `st2`; legacy `st1` payloads still decode via a new `LEGACY_ENCODING_IDS` discovery hook in the encoder registry. `schema_driven.decode()` gains an opt-in `scalar_types` parameter so typed scalars (ints, floats, bools) round-trip correctly instead of stringifying — `search_text` declares its numeric/boolean fields; every other schema's behavior is unchanged.
- **`tools/list_changed` notifications actually emit now (#246)** — `_emit_tools_list_changed` was a `pass` placeholder, so the entire runtime-tier feature was silently never notifying clients. New `_get_mcp_session` helper does concrete `srv.request_context.session` lookup with narrowed `LookupError` / `AttributeError` handling; warns at WARNING level when the session lacks `send_tool_list_changed` so SDK version mismatches are visible. Integration test exercises the real `FakeServer.request_context.session` chain instead of mocking the helper.
- **`plan_turn` tie-safe heap (#246)** — the bounded heap stored `(score, entry)` tuples, so equal scores forced Python to compare `dict` values and raised `TypeError: '<' not supported between instances of 'dict' and 'dict'`. Heap now stores `(score, symbol_id, entry)` triples; regression test covers the equal-score case with two indexed `helper()` symbols.
- **`plan_turn` tier flip atomicity (#246)** — when `adaptive_tiering: true`, the tier switch now runs after the handler returns successfully. A handler failure can no longer leave a half-applied session tier.
- **`render_diagram` integration test portability (#250)** — test now skips cleanly when `MMD_VIEWER_PATH` is unset, instead of falling back to a hardcoded path only one contributor's machine had. Set `MMD_VIEWER_PATH` to opt in locally.

### Migration
- No config changes required. Existing installs see zero behavior change; `adaptive_tiering` defaults to `false`.
- Operators wanting runtime tier switching: set `adaptive_tiering: true` in `config.jsonc` and optionally customize `tool_tier_bundles` and `model_tier_map`. Run `jcodemunch-mcp config --upgrade` to pull the new keys into an existing config file without clobbering user values.
- The MUNCH `search_text` encoder bumped from `st1` to `st2`. Existing cached `st1` payloads continue to decode via `LEGACY_ENCODING_IDS`.

## [1.59.1] — 2026-04-18

### Fixed
- **Redaction no longer mangles source code (F1)** — the `bearer_token` pattern was over-broad: `(?i)(?:bearer|token)\s+...` matched ordinary identifiers like `def refresh_token session_identifier_name`. Pattern is now header-anchored (`Authorization:` context or capital `Bearer `). The `generic_api_key` pattern now requires the captured value to include all three character classes (upper+lower+digit) AND meet a Shannon entropy threshold (≥3.5 bits/char), so SCREAMING_CASE and snake_case identifiers pass through unchanged.
- **Redaction depth cap no longer leaks secrets (F2)** — past the 20-level recursion guard, `redact_dict` previously returned the raw subtree. Deeply nested payloads now collapse to `[REDACTED:depth_exceeded]` instead.
- **`_meta` string fields now scanned for secrets (F6)** — the blanket `_meta` bypass in `redact_dict` skipped strings inside `_meta.hint` / `_meta.error`, letting secrets echoed into metadata leak unredacted. Only scalar numeric/bool fields bypass scanning now; strings and nested containers are redacted.
- **`mermaid_viewer.open_diagram` contains filesystem errors (F4)** — `mkdir` and `write_text` failures previously propagated as exceptions, contradicting the docstring's "non-fatal on failure" contract. They now return `{opened: False, error: "write_failed: ..."}`.
- **Per-call temp-file purge (F3)** — `open_diagram` now prunes `jcm-*.mmd` files older than one hour on every invocation, preventing unbounded growth under repeated use.

### Security
- **Viewer executable gate (F5)** — `resolve_viewer_path` now checks that the configured path looks executable (exec suffix on Windows; execute bit on POSIX). Stale config pointing at a non-executable file now returns `None` instead of attempting to spawn it.

## [1.59.0] — 2026-04-17

### Added
- **`render_diagram` optional `open_in_viewer` parameter (#245, @MariusAdrian88)** — opt-in integration with the companion [mmd-viewer](https://github.com/MariusAdrian88/mmd-viewer) binary for instant visual preview of rendered Mermaid diagrams. Fully gated behind two new config keys: `render_diagram_viewer_enabled` (default `false`) controls whether the `open_in_viewer` parameter is exposed in the tool schema at all, so LLM clients see no change unless the feature is enabled locally; `mermaid_viewer_path` points at the executable (empty = `$PATH` lookup). When enabled and invoked, the rendered Mermaid is written to a `jcm-`-prefixed `.mmd` file under `<index_storage>/temp/mermaid/` and piped to the viewer on stdin. Non-fatal by design: viewer-missing or spawn-failure adds a `viewer_error` field to the response but always returns the Mermaid markup. Cleanup is selective — only `jcm-`-prefixed files are removed, on both startup (stale files from prior sessions) and shutdown (only if the viewer was invoked this session). Windows file-lock aware with 500 ms retry on unlink.

## [1.58.0] — 2026-04-17

### Added
- **MUNCH TypeScript decoder + agent hints (phase 5)** — reference TS decoder at `clients/ts/decoder.ts` (zero dependencies, ~200 lines) decodes both tier-1 and generic-fallback MUNCH payloads to plain JS objects; falls through to `JSON.parse` for non-MUNCH input. New `AGENT_HINTS.md` ships a drop-in prompt snippet so agents can read MUNCH payloads directly without a client-side decoder, plus a worked example walking through legend handles, scalar quoting, and table rehydration. Closes phase 5 of the MUNCH rollout — clients that cannot or will not decode MUNCH can still request `format="json"` to opt out per call or set `JCODEMUNCH_DEFAULT_FORMAT=json` to disable globally.

## [1.57.0] — 2026-04-17

### Added
- **MUNCH spec + benchmark harness (phase 4)** — full on-wire format spec shipped as [SPEC_MUNCH.md](SPEC_MUNCH.md) so third-party clients and alternate MCP servers can decode MUNCH payloads without depending on the Python reference decoder. New A/B encoding benchmark at `munch-bench/munch_bench/encoding_bench.py` (`python -m munch_bench.encoding_bench`) runs representative fixtures through the dispatcher and reports JSON bytes, compact bytes, savings %, and token-saved estimate. Current numbers: **median 45.5% / max 55.4%** across six tools covering both tier-1 and generic-fallback paths — exceeds the PRD's ≥30% median / ≥50% graph-tool targets.
- **README compact-output section** and updated `TOKEN_SAVINGS.md` with the dual-axis framing (retrieval + encoding compose independently).

## [1.56.0] — 2026-04-17

### Changed
- **MUNCH generic fallback hardened (phase 3)** — the shape-sniffer that covers every tool without a hand-tuned encoder now produces fully round-trippable output. Original table keys, column order, and per-column types (int / float / bool / str) are preserved via an embedded schema line instead of being emitted under synthesized `table_<tag>` keys. Top-level scalars now round-trip with their original types via a compact companion `__stypes` map. Nested `dict` values containing list-of-dicts children are flattened with a dotted key so downstream tools can still read them. Path-prefix legend promotion widened and byte-threshold tightened so interning only fires when it actually saves bytes. Table-tag alphabet widened from 7 to 26 and the tag-vs-scalar classifier rewritten against the `<char>,` CSV leading-byte signal so scalar keys can freely start with any letter. Malformed or pathological shapes (mixed arrays, oversized table counts, short lists) fall through to JSON-blob passthrough instead of crashing; the savings gate still discards compact output whenever it isn't a net win, so the fallback remains safe to fail-open across all 65+ tools without custom encoders.

## [1.55.0] — 2026-04-17

### Added
- **MUNCH tier-1 custom encoders (phase 2)** — 15 hand-tuned per-tool compact encoders now ship, replacing the generic fallback for the highest-payoff tools: `get_dependency_graph` (dg1), `get_call_hierarchy` (ch1), `find_references` (fr1), `find_importers` (fi1), `get_blast_radius` (br1), `get_impact_preview` (ip1), `get_signal_chains` (sc1), `get_dependency_cycles` (dc1), `get_tectonic_map` (tm1), `search_symbols` (ss1), `search_text` (st1), `search_ast` (sa1), `get_file_outline` (fo1), `get_repo_outline` (ro1), `get_ranked_context` (rc1). Each encoder declares a schema with column order, type hints, and intern columns; dispatcher picks the custom encoder when available and falls back to the generic shape-sniffer otherwise. Round-trip tested — decoded payload preserves all table contents and scalar fields. Sample `get_dependency_graph` response (8 edges, 6 files) drops from 721 bytes JSON to 490 bytes compact (32% savings); larger responses climb toward 50-70% as legend interning amortizes.
- **Schema-driven encoder helper** (`encoding/schema_driven.py`) lets future per-tool encoders be written in ~30 lines of declarative config: `TableSpec` + scalar/nested-dict/meta/json-blob declarations, then two one-line `encode`/`decode` wrappers.

## [1.54.0] — 2026-04-17

### Added
- **Compact response encoding (MUNCH, phase 1)** — opt-in second-axis token savings independent from retrieval-side optimization. Every tool response can now be emitted in a purpose-built compact format (path/symbol interning, tabular row packing, quoted-CSV data sections) instead of verbose JSON. New `format` argument on every tool accepts `"auto"` (default; falls back to JSON if savings <15%), `"compact"` (force), or `"json"` (never encode). Server-wide default overridable via `JCODEMUNCH_DEFAULT_FORMAT`. Phase 1 ships a schema-agnostic generic encoder that covers all 80+ tools; hand-tuned per-tool encoders land in subsequent releases. `_meta` now surfaces `encoding`, `encoding_tokens_saved`, and a new persisted `total_encoding_tokens_saved` counter so encoding savings are reported separately from retrieval savings. Decoder shipped at `jcodemunch_mcp.encoding.decoder.decode()` for clients that need to rehydrate payloads back to dicts.

## [1.53.0] — 2026-04-17

### Added
- **Constraint-chain retrieval** — new `winnow_symbols` tool runs a multi-axis query against the index in a single round trip. Accepts an ordered list of `{axis, op, value}` criteria (AND semantics) that intersect signals no existing tool composes: `kind`, `language`, `name` (regex), `file` (glob), `complexity`, `decorator`, `calls` (direct call references), `summary/docstring` text, and git `churn` (with configurable `window_days`). Survivors are ranked by PageRank-based `importance` (default), `complexity`, `churn`, or `name`. Replaces the common 4–5-call pattern of intersecting `search_symbols` + `get_hotspots` + `get_untested_symbols` + `find_references` client-side — e.g. "complex functions that call `db.Exec` and churned recently" resolves in one call. Reports `matched`, `total_scanned`, and `supported_axes` alongside ranked results.

## [1.52.1] — 2026-04-16

### Changed
- **`plan_refactoring` full language coverage** — import and definition patterns, import rewrite logic, and import formatting extended to ~40 previously-uncovered languages (erlang, solidity, zig, clojure, powershell, ocaml, fsharp, nim, tcl, dlang, pascal, ada, cobol, matlab, apex, css/scss/sass/less/styl, razor, blade, al, nix, ejs, verse, asm, vue, and others). Refactorings in these languages now emit correct edits instead of falling through to a generic default. New `TestLanguageCoverage` suite enforces parity between `LANGUAGE_REGISTRY` and the refactoring patterns so future language additions can't silently drift.
- **Config tool registry** — `config.py` `all_tools` list updated to include 25+ tools added since last refresh (`audit_agent_config`, `check_rename_safe`, `get_call_hierarchy`, `get_churn_rate`, `get_hotspots`, `get_impact_preview`, `get_pr_risk_profile`, `get_symbol_provenance`, `plan_refactoring`, `render_diagram`, `search_ast`, and more); alphabetically sorted.
- **Test parametrization** — 4 test files consolidated from 330 individual functions to 78 parametrized ones (322 pytest cases, all assertions preserved). Net −1,508 lines of boilerplate across `test_plan_refactoring.py`, `test_config.py`, `test_find_importers.py`, `test_hardening.py`, and `test_render_diagram.py`.

### Fixed
- **Test index contamination** — `test_project_intel.py` was orphaning 22 indexes in `~/.code-index/` on each run; now isolated to `tmp_path`.

Thanks to **@MariusAdrian88** for this contribution (#244).

## [1.52.0] — 2026-04-16

### Added
- **AST Pattern Matching** — new `search_ast` tool provides cross-language structural code pattern matching across all 70+ indexed languages. Write one query, match everywhere — no need to know language-specific tree-sitter node types. Two modes: **(1) Preset anti-patterns** — 10 curated detectors that auto-translate across languages: `empty_catch` (silently swallowed errors), `bare_except` (catch-all without specific type), `deeply_nested` (5+ control-flow levels), `nested_loops` (O(n³)+ triple loops), `god_function` (100+ line functions), `eval_exec` (dynamic code execution — injection risk), `hardcoded_secret` (credential patterns in string literals), `todo_fixme` (unfinished work markers), `magic_number` (unexplained numeric constants), `reassigned_param` (overwritten function parameters). **(2) Custom mini-DSL** — ad-hoc structural queries: `call:*.unwrap` (call-site glob matching), `string:/password/i` (regex over string literals), `comment:/TODO/i` (regex over comments), `nesting:5+` / `loops:3+` / `lines:80+` (threshold queries). Run by category (`security`, `error_handling`, `complexity`, `performance`, `maintenance`) or `category=all` for a full sweep. Every match is attributed to its enclosing indexed symbol with complexity metadata. Universal node-type mapping covers 15 language families (Python, JS/TS, Go, Rust, Java, C#, Ruby, PHP, C/C++, Kotlin, Swift, Dart). Results sorted by severity (error → warning → info).
- Updated **assess** and **triage** MCP prompt templates to recommend `search_ast` for security and anti-pattern sweeps.

## [1.51.0] — 2026-04-16

### Added
- **Symbol Provenance** — new `get_symbol_provenance` tool traces the complete authorship lineage and evolution narrative of any symbol through git history. Uses `git log -L` line-range tracking (with file-level fallback) to find every commit that touched a symbol, classifies each into semantic categories (creation, bugfix, refactor, feature, perf, rename, revert, etc.), extracts motivating intent from commit bodies, and generates a human-readable narrative summarising who created it, why, and how it evolved. Returns ranked author list, evolution summary with lifespan/frequency metrics, and dominant change pattern. Use before refactoring unfamiliar code to understand the "why" behind it.
- **PR Risk Profile** — new `get_pr_risk_profile` tool produces a unified risk assessment for all changes between two git refs. Fuses five orthogonal signals — blast radius (30%), complexity (25%), test gaps (20%), churn (15%), and change volume (10%) — into a single composite `risk_score` (0.0–1.0) with `risk_level` (low/medium/high/critical). Returns per-signal breakdowns, top-5 riskiest changed symbols, untested symbol list, and actionable recommendations. Designed for CI gating and the `/review` workflow. One call replaces manual orchestration of `get_changed_symbols` + `get_blast_radius` + `get_hotspots` + `get_untested_symbols`.
- **Response Secret Redaction** — all tool responses are now scanned for leaked credentials before reaching the LLM context window. Detects AWS access keys (AKIA...), AWS secret keys, GCP service account emails, Azure storage/client keys, JWT tokens, GitHub PATs (ghp_/gho_/...), Slack tokens, PEM private key headers, generic API keys (32+ char high-entropy values), and private IPv4 addresses (10.x, 172.16-31.x, 192.168.x). Matched values are replaced with `[REDACTED:<type>]` placeholders. Controlled by `redact_response_secrets` config key (default: true) or `JCODEMUNCH_REDACT_RESPONSE_SECRETS` env var. The `_meta` field reports `secrets_redacted` count when any redactions occur.
- Updated the **assess** MCP prompt template to recommend `get_pr_risk_profile` as the quick-path and `get_symbol_provenance` for deep-path analysis.

## [1.50.1] — 2026-04-16

### Fixed
- **Devcontainer/Docker support** — `index_folder` no longer rejects shallow paths like `/workspace` or `/app` when running inside a container. Auto-detects Docker (`/.dockerenv`), Podman (`/run/.containerenv`), VS Code devcontainers (`REMOTE_CONTAINERS`), GitHub Codespaces (`CODESPACES`), and generic container orchestrators (`container` env var). Minimum path depth is relaxed from 3 to 2 components; bare `/` is still rejected. `trusted_folders` remains available as a manual override. Fixes #243.

## [1.50.0] — 2026-04-15

### Added
- **Branch-Aware Delta Indexing** — jcodemunch-mcp now maintains per-branch delta layers instead of re-indexing from scratch when you switch git branches. One base index (typically `main`/`master`) stores the full index; non-base branches save only what changed relative to the base (O(delta) storage). At query time, the delta is composed onto the base to produce the branch-specific view. All 55+ tools auto-detect the current branch via `git rev-parse --abbrev-ref HEAD` — no new parameters required. Supports detached HEAD (uses commit SHA), non-git folders (graceful no-op), and stale delta detection (warns when base was re-indexed after the delta was created). New storage: `branch_deltas` and `branch_meta` tables in the existing SQLite DB. `list_repos` now shows indexed branches. INDEX_VERSION bumped to 9 (auto-migration from v8).

## [1.49.0] — 2026-04-15

### Added
- **Project Intelligence** — new `get_project_intel` tool auto-discovers and structurally parses non-code knowledge files (Dockerfiles, docker-compose, GitHub Actions, GitLab CI, CircleCI, K8s manifests, .env templates, Makefiles, package.json scripts, pyproject.toml scripts) and cross-references them to indexed code symbols. Returns structured intelligence grouped into 6 categories: `infra` (Docker stages/services/ports, K8s resources, Terraform from index), `ci` (pipeline jobs/triggers/run commands), `config` (env vars with defaults and comments), `deps` (scripts/targets/entry points), `api` (OpenAPI endpoints, GraphQL types, Protobuf services from index), `data` (dbt/SQLMesh models, column counts, migration files from index). Cross-references link Dockerfile entrypoints to source files, compose build contexts to directories, env var names to code that reads them, CI run commands to test files, and script targets to referenced paths. Every YAML parser has a regex fallback — works with zero optional dependencies. Single `os.walk` pass with 200-file cap, 256KB size guard, and 50-item output caps per category.

## [1.48.0] — 2026-04-15

### Added
- **Universal Mermaid Renderer** — new `render_diagram` tool transforms any graph-producing tool's output into rich, annotated Mermaid markup. Auto-detects the source tool from the dict's key signature and picks the optimal diagram type: `flowchart TD` for call hierarchies and blast radius, `flowchart BT` for impact previews, `flowchart LR` for tectonic plates / dependency graphs / cycles, and `sequenceDiagram` for signal chains. Encodes metadata as visual signals: edge colors for resolution confidence (green=LSP, blue=AST, orange=inferred, red=heuristic), node shapes by symbol kind, subgraph grouping by file/plate/depth, risk heat coloring, drifter/nexus callouts. Three themes: `flow` (blue/purple depth gradient), `risk` (red/yellow/green heat), `minimal` (monochrome). Smart pruning preserves topology under `max_nodes` budget (leaf removal → low-degree removal). Returns `mermaid` markup, `legend`, `node_count`, `edge_count`, `pruned_count`. Supports all 7 graph tools: `get_call_hierarchy`, `get_signal_chains`, `get_tectonic_map`, `get_dependency_cycles`, `get_impact_preview`, `get_blast_radius`, `get_dependency_graph`.

## [1.47.0] — 2026-04-15

### Added
- **Signal Chain Discovery** — new `get_signal_chains` tool traces how external signals (HTTP requests, CLI commands, scheduled tasks, events) propagate through the codebase via the call graph. Each chain starts at a **gateway** (route handler, CLI command, task decorator, event listener, main entry point) and follows BFS callees to leaf symbols. Two modes: **discovery** (omit `symbol` — maps all chains, reports orphan symbols not on any chain) and **lookup** (pass a `symbol` — returns which user-facing chains it participates in, e.g. "validate_email sits on POST /api/users and cli:import-users"). Detects gateways from Flask/FastAPI/Spring/NestJS/ASP.NET route decorators, @click/@app.command CLI, @celery/@dramatiq task queues, event handlers, and standard entry points. Filter by `kind` (http/cli/event/task/main/test). Reuses existing AST-resolved call graph infrastructure for 70+ language support.

## [1.46.0] — 2026-04-15

### Added
- **Tectonic Analysis** — new `get_tectonic_map` tool discovers the logical module topology of a codebase by fusing three independent coupling signals: structural (import edges), behavioral (shared symbol references), and temporal (git co-churn). Returns auto-detected file clusters ("plates"), each with an anchor file, cohesion score, inter-plate coupling map, drifter detection (files whose directory doesn't match their logical module), and nexus alerts (god-module risk). Plate count emerges from the topology — no k parameter. Pure Python label propagation, no external dependencies.

## [1.45.1] — 2026-04-15

### Documentation
- **Hermes Agent integration** — added "Works with" section to README with Hermes Agent config example; submitted optional skill PR to [NousResearch/hermes-agent#10413](https://github.com/NousResearch/hermes-agent/pull/10413)

## [1.45.0] — 2026-04-15

### Added
- **Enhanced BM25 tokenizer** — Porter-style suffix stemming ("searching" → "search", "running" → "run") and bidirectional abbreviation expansion (40 entries: "db" ↔ "database", "config" ↔ "configuration", etc.). Significantly improves recall for natural-language queries against code symbols.
- **Diversity-aware budget packing** — `get_ranked_context` now spreads results across files (per-file cap of 3, decay penalty for same-file repeats) instead of greedy same-file stacking. Produces more useful context bundles.

### Fixed
- **Content hash consistency** — drift detection always uses SHA-256, preventing false-positive staleness on existing indexes.

## [1.44.1] — 2026-04-14

### Fixed
- `claude-md` (and Cursor/Windsurf rule generators) now respects `tool_profile` and `disabled_tools` — only emits tools the model can actually call (#242).

## [1.44.0] — 2026-04-14

### Added
- **Tool profiles** — new `tool_profile` config key with three tiers to control context budget (#242):
  - `"core"` — 16 essential tools (indexing, search, retrieval, relationships). ~5-6k tokens saved vs full.
  - `"standard"` — core + analytics, architecture, quality, impact tools (~40 tools).
  - `"full"` — all tools (default, backwards-compatible).
- **Compact schemas** — new `compact_schemas` config key strips rarely-used advanced parameters (debug, fusion, semantic_*, fuzzy_*, etc.) from tool schemas. The server still accepts them — they're just hidden from the LLM. Saves ~1-2k tokens on top of any profile.
- `config` command now shows `tool_profile` and `compact_schemas` in the Tool Profile section.
- 6 new tests for profile filtering and compact schema stripping.

## [1.43.0] — 2026-04-13

### Added
- **6 new languages** — F# (`.fs`, `.fsi`, `.fsx`), Clojure (`.clj`, `.cljs`, `.cljc`, `.edn`), Emacs Lisp (`.el`), Nim (`.nim`, `.nims`, `.nimble`), Tcl (`.tcl`, `.tk`, `.itcl`), D (`.d`, `.di`)
- Custom tree-sitter parsers for all 6 languages with full symbol extraction: F# modules/functions/types/values, Clojure namespace-qualified defn/def/defprotocol/defrecord, Emacs Lisp defun/defvar/defconst/defmacro with docstrings, Nim proc/func/template/macro/type/var/let/const, Tcl proc with namespace nesting (`::`-qualified names), D functions/classes/structs/interfaces/enums/templates with nested method extraction

### Documentation
- Updated `server.json` version (1.8.6 → 1.43.0) and language count (25+ → 70+)
- Updated `benchmarks/whitepaper.md` language counts from "25+" to "70+"
- Added CONFIGURATION.md and GROQ.md to README.md documentation table
- Updated `LANGUAGE_SUPPORT.md` valid language names list with all 73 registered languages

## [1.42.0] — 2026-04-13

### Added
- **11 new languages** — Pascal/Delphi (`.pas`, `.dpr`, `.dpk`, `.lpr`, `.pp`), MATLAB (`.mat`, `.mlx`, + `.m` path-heuristic disambiguation vs Objective-C), Ada (`.adb`, `.ads`), COBOL (`.cob`, `.cbl`, `.cpy`), Common Lisp (`.lisp`, `.cl`, `.lsp`, `.asd`), Solidity (`.sol`), Zig (`.zig`, `.zon`), PowerShell (`.ps1`, `.psm1`, `.psd1`), Apex/Salesforce (`.cls`, `.trigger`), OCaml (`.ml`, `.mli`), PL/SQL (`.pls`, `.plb`, `.pck`, `.pkb`, `.pks` → existing SQL parser)
- Custom tree-sitter parsers for all 10 new grammar-backed languages with full symbol extraction: functions, classes, types, constants, methods, and language-specific constructs (COBOL paragraphs/sections, Solidity contracts/events/modifiers, Zig test declarations, Apex triggers, OCaml modules)
- MATLAB vs Objective-C `.m` file disambiguation via path heuristics (directories named `matlab/`, `toolbox/`, `simulink/` → MATLAB; `ios/`, `xcode/`, `cocoa/` → Objective-C)

## [1.41.0] — 2026-04-13

### Added
- **munch-bench** — Retrieval + Inference benchmark consolidated into the mothership (Phase 5 of Groq Integration). 110 questions across 11 repos, evaluation harness with Groq/OpenAI/Anthropic providers, static HTML leaderboard with Chart.js. Install with `pip install jcodemunch-mcp[bench]`, run with `munch-bench run --provider groq`. First results: Sonnet 0.81, Haiku 0.68, Groq Llama 0.69 judge scores.
- New optional dependency group `[bench]` (openai, anthropic, pyyaml, rich, jinja2)
- `munch-bench` CLI entrypoint: `run`, `compare`, `corpus-stats` subcommands

## [1.40.1] — 2026-04-13

### Fixed
- Fix `jcodemunch-mcp index <owner/repo>` CLI crash — was passing `repo=` instead of `url=` to `index_repo()`, causing `TypeError: got an unexpected keyword argument 'repo'`

## [1.40.0] — 2026-04-13

### Added
- **Voice-to-Codebase (`gcm --voice`)** — speak a question about your codebase, hear the answer spoken back. Full audio pipeline: Groq Whisper STT → jCodeMunch retrieval → Groq LLM → Orpheus TTS playback. Push-to-talk via Enter key, with text fallback. Install with `pip install jcodemunch-mcp[groq-voice]`. Supports multi-turn voice conversation, configurable model, and verbose timing.
- **Auto Repo Explainer (`gcm explain`)** — generate a narrated explainer video for any codebase in a single command. Pipeline: gather repo structure + key symbols → Groq LLM generates narration script → Orpheus TTS renders audio → Pillow renders 1920x1080 dark-theme slides → FFmpeg composites into MP4. Install with `pip install jcodemunch-mcp[groq-explain]` (requires FFmpeg on PATH). Produces 45-90 second videos with file tree and code snippet slides.
- New optional dependency groups: `[groq-voice]` (sounddevice, numpy), `[groq-explain]` (Pillow)
- 18 new tests for voice and explainer modules (`test_groq_voice.py`, `test_groq_explainer.py`)

## [1.39.1] — 2026-04-13

### Fixed
- **gcm: fix GitHub repo detection on Linux** — `_is_github_repo` now correctly identifies `owner/name` patterns on all platforms (was failing on Linux where `/` is `os.path.sep`)

## [1.39.0] — 2026-04-13

### Added
- **Codebase Q&A CLI (`gcm`)** — ask any question about any codebase, get an answer in under 3 seconds. Powered by jCodeMunch retrieval + Groq inference. Install with `pip install jcodemunch-mcp[groq]`. Supports GitHub repos (`--repo owner/name`), local directories, streaming output, interactive `--chat` mode, `--fast` flag for 8B model, and configurable token budget. Auto-indexes on first use.

## [1.38.0] — 2026-04-13

### Added
- **speedreview GitHub Action** (`speedreview/`) — AI code review in under 5 seconds. Composite action uses jCodeMunch locally for symbol-level diff analysis (`get_changed_symbols` + `get_blast_radius` + `get_ranked_context`) and Groq for sub-2s inference. Posts structured review as PR comment. Usage: `uses: jgravelle/jcodemunch-mcp/speedreview@main`.

## [1.37.0] — 2026-04-13

### Added
- **Groq Remote MCP integration** — full tutorial (`GROQ.md`), Docker deployment (`Dockerfile`, `docker-compose.yml`, `Caddyfile`), validation script (`examples/groq_validate.py`), and README section. Deploy jCodeMunch as an HTTPS SSE endpoint and connect via Groq's Responses API in a single API call. Includes allowed-tools presets (explore, deep, review, full) and model recommendations.

## [1.36.0] — 2026-04-12

### Added
- **Arduino language support** ([#239](https://github.com/jgravelle/jcodemunch-mcp/pull/239)): `.ino`/`.pde` files parsed via tree-sitter-arduino grammar (C++ superset). Classes, structs, enums, functions, constants extracted. Import extraction reuses `#include` path
- **VHDL language support** ([#239](https://github.com/jgravelle/jcodemunch-mcp/pull/239)): `.vhd`/`.vhdl`/`.vho`/`.vhs` files parsed via regex. Extracts entity, architecture, package, process, function, procedure, component, signal, constant, type/subtype. Import extraction for `library`/`use` clauses (`work` library excluded)
- **Verilog/SystemVerilog language support** ([#239](https://github.com/jgravelle/jcodemunch-mcp/pull/239)): `.v`/`.vh`/`.sv`/`.svh` files parsed via regex. Extracts module, interface, class, function, task, package, typedef, parameter/localparam, `` `define ``. Import extraction for `` `include `` directives

## [1.35.1] — 2026-04-12

### Fixed
- **invalidate_cache + index_folder reliability** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): `invalidate_cache` followed by `index_folder` (incremental) no longer returns "No changes detected". Fixes Windows WAL file-locking race, legacy JSON resurrection, and adds `_force_full_reindex` coordination flag
- **meta_fields config applied to batch results** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): `meta_fields` filter now strips/filters nested `_meta` in batch tool responses (e.g. `get_file_outline` with `file_paths=[...]`)
- **WatcherManager self-restarts on crash** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): monitoring loop auto-restarts with 100ms backoff, up to 5 consecutive attempts before clean exit
- **Orphan index cleanup on startup** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): indexes whose `source_root` no longer exists on disk are deleted at server startup

## [1.35.0] — 2026-04-12

### Added
- **`plan_refactoring` tool** ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236)): generate edit-ready `{old_text, new_text}` refactoring plans in a single call. Supports rename, move, extract, and signature change operations across all affected files. Handles import rewrites for 20+ languages, collision detection, inter-symbol dependency warnings, path alias detection, non-code file scanning, and multi-line signature capture. 325 new tests

### Fixed
- Python 3.10 compatibility in `plan_refactoring` — removed Python 3.12+ f-string syntax ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))
- False call sites no longer reported for multi-line signature continuation lines ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))
- `_plan_extract` no longer unconditionally adds source import when no staying symbol references extracted symbols ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))
- `_split_python_import` preserves indentation for imports inside `try:` blocks ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))

### Changed
- Extracted `_capture_multiline_sig()` helper and hoisted `_file_to_module()` to module level — net -142 lines of duplication ([#237](https://github.com/jgravelle/jcodemunch-mcp/pull/237))

## [1.34.0] — 2026-04-11

### Added
- **MCP progress notifications** ([#232](https://github.com/jgravelle/jcodemunch-mcp/issues/232)): `index_folder`, `index_repo`, `index_file`, and `embed_repo` now emit `notifications/progress` when the client provides a `progressToken`. Zero token cost — notifications go to the host (e.g. VS Code MCP widget), never the model. Shows label, ASCII bar, percent, count, and current item name
- **`ProgressReporter`** (`progress.py`): thread-safe, monotonic progress helper. No pulse threads, no fake drift — progress reflects real completed work
- **`make_progress_notify()`** (`progress.py`): bridge function that creates a thread-safe callback from the MCP request context, using `asyncio.run_coroutine_threadsafe` to safely send notifications from worker threads
- 16 new tests in `tests/test_progress.py` covering reporter lifecycle, monotonicity, thread safety, format, no-op behavior, and tool signature wiring

## [1.33.0] — 2026-04-11

### Added
- **Auto-watch on demand** ([#233](https://github.com/jgravelle/jcodemunch-mcp/pull/233)): when `watch: true` is set in config (or `JCODEMUNCH_WATCH=1`), the server automatically reindexes and starts watching any unwatched repo before a tool executes. Eliminates silent-stale-data that causes LLMs to abandon jcodemunch tools for the session. Race-safe via `asyncio.Condition` — concurrent tool calls to the same unwatched repo trigger only one reindex
- **`WatcherManager` class** (`watcher.py`): manages dynamic folder watching with `add_folder()`, `remove_folder()`, `is_watched()` (O(1)), `list_folders()`, `ensure_indexed()` (race-safe), and `run()` (crash recovery). Replaces direct task manipulation in `watch_folders()`
- **`get_source_root()`** (`sqlite_store.py`): lightweight metadata-only SQLite query to resolve repo ID to folder path without loading full `CodeIndex`
- **`watch` config key**: opt-in via `watch: true` in config.jsonc or `JCODEMUNCH_WATCH=1` env var (default: `false`)
- 15 new tests in `tests/test_watcher_dynamic.py` covering manager lifecycle, race guard, and auto-watch integration

### Fixed
- Restarted watch tasks (crash recovery in `WatcherManager.run()`) now receive the `on_reindex` callback — previously dropped, causing idle-timeout to fire prematurely after a task restart
- `_pending_results` dict in `WatcherManager.ensure_indexed()` no longer leaks — entries are popped after concurrent waiters consume them
- Individual `_watch_single` tasks are now explicitly cancelled and awaited during `watch_folders` shutdown (previously only manager/watchdog tasks were cancelled)

## [1.32.1] — 2026-04-10

### Fixed
- **`embed_repo` preflight performance** (#231): cache-discovery no longer loads and decodes every stored embedding blob just to get symbol IDs. New `EmbeddingStore.get_all_ids()` queries only the `symbol_id` column. Eliminates unnecessary CPU, memory, and latency on repos with existing embeddings

## [1.32.0] — 2026-04-10

### Added
- **`jcodemunch-mcp index` CLI command** ([#230](https://github.com/jgravelle/jcodemunch-mcp/issues/230)): Index a local folder or GitHub repo directly from the terminal. Defaults to the current directory when no target is given — no `init` required. Supports `--no-ai-summaries`, `--follow-symlinks`, and `--extra-ignore` flags

### Changed
- **Version renumbered from 2.1.0 → 1.32.0.** The 2.0.0 bump was premature — every change from 1.24.4 through 2.1.0 was purely additive (new tools, new opt-in config, new CLI subcommands). Nothing was removed, renamed, or made incompatible. INDEX_VERSION stayed at 8, all config defaults preserved existing behavior, and LSP/dispatch features are off by default. Per semver, additive features are minor bumps. The full renumbering: 1.24.4→1.25.0, 1.24.5→1.26.0, 1.25.0→1.27.0, 1.26.0→1.28.0, 1.27.0→1.29.0, 1.28.0→1.30.0, 2.0.0→1.31.0, 2.1.0→1.32.0. PyPI releases under the old numbers remain installable but are logically equivalent to their renumbered counterparts

## [1.31.0] — 2026-04-10

### Added
- **Interface & trait dispatch resolution** (Phase 5 / Gap 2C): resolves interface/trait method calls to their concrete implementations via LSP `textDocument/implementation`. Supports Go interfaces, Rust traits, TypeScript/Java/C#/PHP interfaces and abstract classes. Adds `dispatches_to` edges with `lsp_dispatch` resolution tier
- **`_detect_interface_keywords()`** in `parser/extractor.py`: tags interface/trait/abstract symbols in `keywords` during tree-sitter parsing — zero-cost, no INDEX_VERSION bump required. Covers Go (`interface_type`), Rust (`trait_item`), TypeScript (`interface_declaration`), Java/C# (interface + abstract class), PHP (interface + trait)
- **`goto_implementation()` on `LSPServer`**: new method parallel to `goto_definition()`, sends `textDocument/implementation` request. Updated `_initialize()` capabilities to advertise implementation support
- **`DispatchEdge` dataclass**: represents interface method → concrete implementation mapping with `lsp_dispatch` resolution
- **`resolve_implementations()` on `LSPBridge`**: resolves interface method positions to concrete implementations across multiple language servers. Caps at 50 implementations per interface method
- **`enrich_dispatch_edges()` entry point**: high-level function that scans parsed symbols for interface keywords, collects method positions, resolves implementations via LSP, and returns serializable edge dicts
- **`dispatch_edges` in `context_metadata`**: stored alongside existing `lsp_edges` in both full and incremental `index_folder` paths
- **`_dispatch_callers()` / `_dispatch_callees()`** in `_call_graph.py`: query dispatch edges to find concrete implementations (callees) or callers through interface dispatch. Integrated at highest priority in `find_direct_callers/callees`
- **`dispatches` section in `get_call_hierarchy`**: new response field showing interface dispatch relationships grouped by interface/method, with concrete implementation details
- **`lsp_dispatch_enriched` methodology**: when dispatch edges are present, `_meta.methodology` is `lsp_dispatch_enriched` and `confidence_level` is `high`
- 31 new tests in `tests/test_dispatch_resolution.py` covering interface keyword detection (15 languages), `goto_implementation` unit tests, `DispatchEdge` dataclass, `_dispatch_callers/_dispatch_callees` with mock indexes, `get_call_hierarchy` dispatches section, graceful degradation, and TS interface keyword propagation through `index_folder`

## [1.30.0] — 2026-04-10

### Added
- **LSP Bridge enrichment layer** (Gap 2B): new `enrichment/lsp_bridge.py` module — optional, opt-in integration with language servers for compiler-grade call graph resolution. Manages LSP server lifecycles for pyright (Python), typescript-language-server (TS/JS), gopls (Go), and rust-analyzer (Rust). Strictly additive: if a language server isn't installed, falls back to pure tree-sitter + heuristic with zero behaviour change
- **`lsp_resolved` resolution tier**: new highest-confidence tier in call graph edges. `get_call_hierarchy` now reports four tiers: `lsp_resolved` (compiler-grade via LSP), `ast_resolved` (direct tree-sitter match), `ast_inferred` (resolved via import graph), `text_matched` (heuristic). When LSP data is present, `_meta.methodology` is `lsp_enriched` and `confidence_level` is `high`
- **LSP enrichment in `index_folder`**: when `enrichment.lsp_enabled` is set to `true` in config.jsonc, the indexing pipeline calls LSP servers to resolve unqualified call sites after tree-sitter parsing. Resolved edges are stored in `context_metadata.lsp_edges` and consumed by the call graph at query time
- **`enrichment` config block**: new configuration section in config.jsonc — `enrichment.lsp_enabled` (default `false`), `enrichment.lsp_servers` (per-language server map), `enrichment.lsp_timeout_seconds` (default 30). Supports both global and per-project config
- 40 new tests in `tests/test_lsp_bridge.py` covering JSON-RPC helpers, server lifecycle, graceful degradation, call graph integration, config helpers, and index_folder integration

## [1.29.0] — 2026-04-10

### Added
- **Bundled ONNX local encoder** (Gap 1): new `embeddings/local_encoder.py` module ships a zero-config embedding provider using `all-MiniLM-L6-v2` (Apache 2.0, 384-dim, ~23 MB). Install via `pip install 'jcodemunch-mcp[local-embed]'` — no API keys, no internet after first download, no configuration. Includes a minimal WordPiece tokenizer (no `transformers` dependency) and L2-normalised mean-pooled output
- **`local_onnx` provider (priority 0)**: when `onnxruntime` is installed and the model is present, `embed_repo` and `search_symbols(semantic=true)` automatically use the bundled encoder — zero friction. Falls through to sentence-transformers/Gemini/OpenAI if unavailable
- **`download-model` CLI subcommand**: `jcodemunch-mcp download-model` fetches the ONNX model + vocab from HuggingFace to `~/.code-index/models/all-MiniLM-L6-v2/`. Auto-downloads on first `embed_repo` call if model is missing. Override path via `JCODEMUNCH_LOCAL_EMBED_MODEL` env var or `--target-dir` flag
- **`[local-embed]` install extra**: `pip install 'jcodemunch-mcp[local-embed]'` adds `onnxruntime>=1.16.0` dependency

## [1.28.0] — 2026-04-10

### Added
- **Unified signal fusion pipeline** (Gap 3 full): new `retrieval/signal_fusion.py` module implements Weighted Reciprocal Rank (WRR) fusion across four channels — lexical (BM25), structural (PageRank), similarity (embeddings), and identity (exact/prefix/segment match). Configurable per-channel weights via `config.jsonc` under `retrieval.fusion_weights`. Eliminates linear score addition in favour of proper rank fusion
- **`search_symbols(fusion=true)`**: new parameter activates multi-signal fusion ranking. Debug mode (`debug=true`) reports `fusion_score`, `channel_contributions`, and `channel_ranks` per result. `_meta` includes active channels, weights, and smoothing constant
- **`get_ranked_context(fusion=true)`**: fusion-based context assembly with per-item channel contribution breakdown in results
- **Post-task diagnostics hook** (Gap 4B): new `hook-taskcomplete` CLI subcommand — on task completion, runs three diagnostics scoped to session-modified files: `find_dead_code` (newly-orphaned symbols), `get_untested_symbols` (untested new code), `check_references` (unreferenced symbols). Injects a compact housekeeping nudge via `systemMessage`
- **Subagent briefing hook** (Gap 4C): new `hook-subagent-start` CLI subcommand — injects a condensed repo orientation (file/symbol/language stats, top-15 PageRank central symbols, full 40+ tool catalog) for spawned agents. Ensures subagents start with structural context
- Both new hooks are auto-registered in `~/.claude/settings.json` by `jcodemunch-mcp init` and `config --check` verifies their presence

## [1.27.0] — 2026-04-10

### Added
- **PreCompact structural landmarks** (Gap 4A): `run_precompact()` now enriches the session snapshot with PageRank-ranked top-20 central symbols and recently-changed symbols from the session journal. Gives the LLM a structural "table of contents" that survives context compaction
- **Per-edge resolution tiers** (Gap 2A): every edge in `get_call_hierarchy` callers/callees now carries a `resolution` field — `ast_resolved` (direct tree-sitter match), `ast_inferred` (resolved via import graph), or `text_matched` (heuristic word-boundary fallback). `_meta.resolution_tiers` summarises the tier distribution
- **Identity channel in search** (Gap 3 partial): `search_symbols` replaces the old `50.0` exact-name hack with a proper identity scoring channel — exact match (50), prefix match (30), qualified-ID segment match (20). Debug mode (`debug=true`) now reports `identity` score and `identity_type` in the per-field breakdown

## [1.26.0] — 2026-04-10

### Added
- **Guided workflow prompts**: 4 new MCP prompt templates alongside the existing `workflow` prompt — `explore` (onboard to an unfamiliar repo), `assess` (pre-merge impact analysis), `triage` (diagnose code quality), `trace` (investigate a bug through the call graph). Each composes existing jcodemunch tools into a step-by-step workflow. Accessible via the MCP prompt protocol (`list_prompts` / `get_prompt`)

## [1.25.0] — 2026-04-10

### Added
- **`get_untested_symbols`**: new tool — find functions and methods with no evidence of test-file reachability. Uses import-graph analysis + name matching (AST call_references when available, word-boundary text heuristic as fallback). Classifies symbols as "unreached" (no test imports the source file) or "imported_not_called" (test imports the module but no test references this specific function). Supports `file_pattern` glob filter, `min_confidence` threshold, and `max_results` cap
- **`get_blast_radius` enrichment**: every confirmed entry now includes a `has_test_reach: bool` field indicating whether any test file imports that file AND references the affected symbol by name
- **`_is_test_file()` expanded**: now recognizes JS/TS test patterns (`.spec.ts`, `.spec.js`, `.test.ts`, `.test.js`, `__tests__/`) in addition to existing Python patterns. Benefits both `find_dead_code` and `get_untested_symbols`

## [1.24.3] — 2026-04-10

### Added
- **`watch --once`**: one-shot index sync — indexes all paths incrementally and exits immediately. No watchfiles dependency required. Supports multiple paths. Exit code 1 if any path fails (#227, thanks @kecsap!)

## [1.24.2] — 2026-04-08

### Added
- **Starter Packs** (`install-pack` subcommand): download pre-built indexes for popular frameworks. `--list` shows the catalog, `--license KEY` for premium packs, `--force` to re-download. Free packs require no license
- **Per-call pulse signal** (`_pulse.json`): opt-in activity file for downstream dashboards and monitors. Set `JCODEMUNCH_EVENT_LOG=1` to enable. Writes tool name, timestamp, call count, and tokens saved on every tool call (#225)

### Fixed
- `test_summarizer`: "misconfigured" error now names the missing package and includes the exact `pip install` command instead of a generic message (#224)
- `config` output: `allow_remote_summarizer` moved from Privacy section to AI Summarizer section with clarification that it only affects custom base URLs, not standard API endpoints (#224)

## [1.24.1] — 2026-04-08

### Changed
- **Comprehensive doc audit** — reviewed all CHANGELOG entries from 1.21.13–1.24.0 and updated 6 user-facing docs:
  - **USER_GUIDE.md**: Added 12 missing tools (`plan_turn`, `get_session_context`, `register_edit`, `get_session_snapshot`, `get_call_hierarchy`, `get_hotspots`, `get_coupling_metrics`, `get_dependency_cycles`, `get_extraction_candidates`, `get_impact_preview`, `get_dead_code_v2`), `decorator` filter on `search_symbols`, `include_source`/`source_budget`/`decorator_filter` on `get_blast_radius`, negative evidence, and 9 new workflow patterns
  - **CONFIGURATION.md**: Added 15 missing config keys (`agent_selector`, `exclude_skip_directories`, `exclude_secret_patterns`, `languages_adaptive`, `session_journal`, `turn_budget_tokens`, `turn_gap_seconds`, `negative_evidence_threshold`, `search_result_cache_max`, `plan_turn_*_threshold`, `session_resume`, `session_max_age_minutes`, `session_max_queries`, `discovery_hint`, `strict_timeout_ms`)
  - **AGENT_HOOKS.md**: Added Python CLI hooks section (`hook-pretooluse`, `hook-posttooluse`, `hook-precompact`) with `init --hooks` as recommended install method; added call hierarchy, hotspots, decorator search, session tools to both prompt policy blocks
  - **ARCHITECTURE.md**: Added 16 missing tools to Tool Surface; updated directory structure with `agent_selector.py`, `cli/`, `parser/` details, `_call_graph.py`, `session_journal.py`, `session_state.py`, `turn_budget.py`, `plan_turn.py`
  - **README.md**: Added call hierarchy, hotspots, coupling metrics, dependency cycles to structural queries section; added session-aware routing, enforcement hooks, agent selector to feature list; updated `init` docs for `--hooks`
  - **QUICKSTART.md**: Added enforcement hooks to `init` feature list; documented `--demo` flag

## [1.24.0] — 2026-04-08

### Added
- **Agent Selector**: opt-in complexity-based model routing system that assesses request complexity using pre-processing signals and recommends (manual mode) or automatically selects (auto mode) the appropriate model tier (low/medium/high). Off by default — zero behavioral change for existing users
  - `ComplexityScorer`: weighted linear scoring using retrieval set size, symbol count, cross-file references, cross-project flag, language complexity, and token estimate
  - `ModelRouter`: three modes — `off` (default), `manual` (advisory prompts on step-up; `verbosePrompts` for step-down), `auto` (automatic routing with metadata annotation)
  - Default batting orders for Anthropic, OpenAI, and Google providers; fully customizable via `agentSelector` config block
  - Session-level init param overrides (`agentSelector.mode`, `agentSelector.activeProvider`, `agentSelector.verbosePrompts`)
  - Tier resolution edge cases: missing tier fallback, single-model provider passthrough, unknown provider graceful degradation
  - 39 new tests covering scorer, router, config, tier resolution, and language classification

## [1.23.5] — 2026-04-08

### Changed
- CI: bump `actions/checkout` v4→v5 and `astral-sh/setup-uv` v3→v6 for Node.js 24 compatibility (GitHub enforces June 2nd 2026)

## [1.23.4] — 2026-04-08

### Fixed
- Python import resolution: `resolve_specifier` now handles module-style absolute imports (`app.notifications.mentions`) by converting dots to slashes and trying each auto-detected source root (`backend/`, `src/`, etc.) as a prefix. Previously `posixpath.splitext` treated the last dotted component as a file extension, breaking all non-flat Python layouts (#223, @kallevaravas)
- Python import extraction: `_PY_FROM` and `_PY_IMPORT` regexes now allow optional leading whitespace, capturing function-local and class-body imports that were previously silently dropped (#223, @kallevaravas)

## [1.23.3] — 2026-04-07

### Fixed
- Tests: 6 `index_folder()` calls in `test_negative_evidence.py` were leaking index files into `~/.code-index/` instead of pytest's `tmp_path` (#222, @MariusAdrian88)

## [1.23.2] — 2026-04-07

### Added
- `get_blast_radius`: new `include_source` flag returns `source_snippets` (lines referencing the symbol) and `symbols_in_file` (nearby symbol signatures) on each confirmed entry — enables fix-ready context in one call without extra `get_symbol_source`/`get_file_content` round-trips. Optional `source_budget` (default 8000 tokens) caps output size; files prioritised by reference count (#221, @MariusAdrian88)

### Fixed
- `get_blast_radius`: `decorator_filter` was missing from session cache key, which could return stale filtered results

## [1.23.1] — 2026-04-07

### Changed
- Switch MCP tool responses from pretty-printed JSON (`indent=2`) to compact JSON (`separators=(',',':')`) — saves 30-40% tokens per response with zero information loss (fixes #219)

## [1.23.0] - 2026-04-07

### Added
- **AST-based call graph** — extract `call_expression` nodes during tree-sitter parsing and store as `call_references` per symbol. 13 languages supported including constructor calls (`new Foo()`). INDEX_VERSION bumped from 7 to 8 with full v7 backward compatibility (graceful degradation to text heuristic). Confidence upgraded from "low" to "medium" for AST-derived results.
- **Decorator awareness** — `search_symbols(decorator=...)` filter (case-insensitive substring match), `get_blast_radius(decorator_filter=...)`, and decorator surfacing in `get_file_outline` results. Enables cross-cutting concern discovery (e.g. "which endpoints lack CSRF protection?").
- **Negative evidence + enforcement signals** — structured `negative_evidence` and top-level `⚠ warning` strings in `get_ranked_context` and `search_symbols` when queries return empty/low-confidence results. `plan_turn` emits `action: "STOP_AND_REPORT_GAP"` on low/none confidence. Reduces LLM hallucination about missing features.
- **18 new framework route/middleware providers** — Flask, FastAPI, Express, Fastify, Hono, Koa, Gin, Chi, Echo, Fiber, Django (+ DRF), Spring Boot, NestJS, ASP.NET, Rails. Consolidated entry-point decorator regex into `_route_utils.py`. 8 new `FrameworkProfile` definitions.

### Changed
- **Performance optimizations** — single-pass AST walk for symbols + call sites, lazy `_callers_by_name` index (0ms load when unused), pre-computed `enrich_symbols` file context cache (~60-80% fewer provider calls), fuzzy search early-exit cap at 5× max_results, merged disambiguate + complexity pass, O(1) PHP detection via `languages` set.
- **`get_dead_code_v2` Signal 2** — uses AST `call_references` lookup (O(1)) on v8 indexes instead of O(N×M) file I/O.
- `budget_warning` promoted to top-level alongside `_meta` for visibility.

### Fixed
- Semantic search negative evidence used fragile nested ternary — replaced with named `best_score` variable.
- Empty query terms guard added to `search_symbols`, `get_ranked_context`, and `plan_turn`.

### Contributors
- @MariusAdrian88

## [1.22.6] - 2026-04-06

### Fixed
- **`_merge_hooks()` idempotency** — per-rule dedup instead of per-event. Previously, once any jcodemunch PreToolUse hook was installed, no additional PreToolUse rules could be added by `init --hooks`. Now each rule's command is checked individually, allowing incremental hook installation. Cherry-picked from @DrHayt's PR #214.
- **Worktree hook-event derivation** — Claude Code sends `{cwd, name}` in WorktreeCreate/WorktreeRemove payloads, not `worktreePath`. Derive path as `{cwd}/.claude/worktrees/{name}`. Legacy fields still accepted. Also outputs resolved path on stdout as Claude Code expects. Cherry-picked from @DrHayt's PR #214.
- **`config --check` hook validation** — now verifies Python hooks in `~/.claude/settings.json` instead of scanning for shell scripts in `~/.claude/hooks/`. Warns about legacy shell scripts if found. Cherry-picked from @DrHayt's PR #214.

## [1.22.5] - 2026-04-06

### Added
- **`TWEAKCC.md`** — guide for system prompt routing via [tweakcc](https://github.com/Piebald-AI/tweakcc) as an alternative to hook-based enforcement. Includes 8 prompt rewrites that embed jCodemunch preferences into Claude's core tool descriptions. Cross-referenced from AGENT_HOOKS.md. Credit: [@vadash](https://github.com/vadash). Closes #173.

### Fixed
- **PreToolUse hook no longer blocks Read** — changed from hard `deny` to a stderr warning. The deny broke the Edit workflow because Claude Code requires Read before Edit, forcing workarounds or env var overrides. Targeted reads (with `offset` or `limit`) are now silently allowed; full-file reads on large code files produce a stderr hint nudging toward `get_file_outline` + `get_symbol_source`. Aligns the Python CLI hook with the documented shell hook design in AGENT_HOOKS.md which explicitly notes "Read is intentionally NOT blocked".

## [1.22.4] - 2026-04-06

### Added
- **`get_session_snapshot` MCP tool** — compact ~200 token markdown summary of session state (focus files by read count, edited files, key searches, negative evidence). Designed for context injection after compaction to restore session orientation. Contributed by @MariusAdrian88. Closes #211.
- **PreCompact CLI hook** (`jcodemunch-mcp hook-precompact`) — automatically generates and injects a session snapshot before Claude Code context compaction via the `systemMessage` hook output field. Registered by `jcodemunch-mcp init`.
- **`sort_by` parameter for `get_context()`** — session journal now supports `sort_by="frequency"` (by read/edit/query count) in addition to the default `sort_by="timestamp"`.
- **`max_edits` parameter for `get_context()`** — limits the number of edited files returned, consistent with `max_files` and `max_queries`.

## [1.22.3] - 2026-04-06

### Added
- **`exclude_skip_directories` config** — remove entries from the built-in skip directory list at runtime. Mirrors the existing `exclude_secret_patterns` pattern. Example: set `["proto"]` to index protobuf directories that are skipped by default. Contributed by @DrHayt. Closes #209.

## [1.22.2] - 2026-04-05

### Fixed
- **CLI init indexing broken** — `run_index()` passed `folder_path=` to `index_folder()` which expects `path=`, causing `unexpected keyword argument` error on `jcodemunch-mcp init`. Closes #208.

## [1.22.1] - 2026-04-05

### Fixed
- **streamable-http session persistence** — `run_streamable_http_server` previously created a new `StreamableHTTPServerTransport` (and a new `server.run()` coroutine) for every incoming HTTP request, leaving follow-up calls like `tools/list` hitting an uninitialised session and failing with `-32602 INVALID_PARAMS`. The handler now maintains a session map keyed by `mcp-session-id`: on the first request a background `asyncio.Task` runs `transport.connect()` + `server.run()` for the lifetime of the session, and all subsequent requests from the same client are routed to the existing transport. Terminated sessions (e.g. after DELETE) are cleaned up automatically. Includes a 10-second setup timeout with graceful error response. Closes #204.
- 9 new tests in `test_streamable_http_sessions.py`.

## [1.22.0] - 2026-04-05

### Added
- **`plan_turn` tool** — opening-move router for any task. Runs BM25 + PageRank against the query, returns confidence level (high/medium/low/none), recommended symbols/files, insertion point suggestions for missing features, prior negative evidence detection, and a budget advisor when turn budget exceeds 60%.
- **`get_session_context` tool** — returns session history: files read, searches run, edits made, tool call counts. Use to avoid re-reading the same files.
- **`register_edit` tool** — post-edit cache invalidation. Clears BM25 token cache and search result cache for edited files; optionally reindexes.
- **Session journal** (`session_journal.py`) — process-lifetime singleton tracking reads, searches, edits, and negative evidence. Bounded at 5000 entries per category with LRU eviction. Thread-safe.
- **Turn budget** (`turn_budget.py`) — cross-call token accumulator. Injects `budget_warning` + `auto_compacted` into `_meta` when budget runs low. Configurable via `turn_budget_tokens` and `turn_gap_seconds`.
- **Session state persistence** (`session_state.py`) — save/restore session across restarts. Writes only on clean shutdown via `atexit`. Staleness validated against `indexed_at` on restore. Opt-in via `session_resume: true`.
- **Negative evidence in `search_symbols`** — when results are empty or below threshold, response includes structured `negative_evidence` with `verdict` (no_implementation_found / low_confidence_matches), `scanned_symbols`, `scanned_files`, `related_existing`.
- **LRU result cache in `search_symbols`** — 128-entry default, cache key includes `indexed_at` for automatic invalidation on reindex.
- **10 new config keys**: `negative_evidence_threshold`, `search_result_cache_max`, `session_journal`, `plan_turn_high_threshold`, `plan_turn_medium_threshold`, `turn_budget_tokens`, `turn_gap_seconds`, `session_resume`, `session_max_age_minutes`, `session_max_queries`.
- **CLAUDE.md policy updates** — routing rules for `plan_turn`, negative evidence handling, budget warning response, and a Read exception note (harness requires Read before Edit/Write).
- 75 new tests across 10 test files (2191 total, 0 regressions).

## [1.21.27] - 2026-04-04

### Added
- **PreToolUse enforcement hook** (`hook-pretooluse` subcommand) — intercepts `Read` calls on large code files (>=4KB, configurable via `JCODEMUNCH_HOOK_MIN_SIZE`) and returns a `deny` decision directing Claude to use `get_file_outline` + `get_symbol_source` instead. Non-code files and small files pass through silently. Addresses the "0% jcodemunch efficiency" problem where CLAUDE.md rules are ignored under cognitive load.
- **PostToolUse auto-reindex hook** (`hook-posttooluse` subcommand) — fires after `Edit` or `Write` on code files and spawns `jcodemunch-mcp index-file` in the background to keep the index fresh. Eliminates "index staleness anxiety" that caused users to bypass jcodemunch and fall back to `Read`.
- **Enforcement hooks in `init`** — `jcodemunch-mcp init` now offers to install both hooks into `~/.claude/settings.json` (PreToolUse matcher: `Read`, PostToolUse matcher: `Edit|Write`). Enabled by `--hooks` flag or interactive prompt. Idempotent, backup-aware, and respects `--dry-run`/`--demo`.
- New `_merge_hooks()` helper in `cli/init.py` — shared logic for merging hook definitions into settings.json, used by both worktree and enforcement hook installers.
- 25 new tests in `test_hooks.py` covering PreToolUse deny/allow logic, PostToolUse indexing, idempotent install, and edge cases (missing files, invalid JSON, Windows creation flags).

## [1.21.26] - 2026-04-04

### Added
- **Cursor rules injection in `init`** — when Cursor is detected, `jcodemunch-mcp init` now offers to write `.cursor/rules/jcodemunch.mdc` with `alwaysApply: true`. This ensures the code-exploration policy is in context for every Cursor agent turn, including subagents, fixing the unreliable tool-fallback behaviour reported by Cursor users.
- **Windsurf rules injection in `init`** — when Windsurf is detected, `init` now offers to append the code-exploration policy to `.windsurfrules`. Both files are idempotent, backup-aware, and respect `--dry-run`.
- **`--demo` flag for `init`** — `jcodemunch-mcp init --demo` walks through the full setup process without making any changes, then prints "Had this NOT been a demo, I would have:" followed by each action and its benefit. 10 new tests in `test_init.py`.

## [1.21.25] - 2026-04-03

### Added
- **`audit_agent_config` tool** — scans agent config files (CLAUDE.md, .cursorrules, copilot-instructions.md, .windsurfrules, settings.json, etc.) for token waste. Reports per-file token cost, stale symbol references (cross-referenced against the jcodemunch index), dead file paths, redundancy between global and project configs, bloat patterns, and scope leaks. 34 new tests.

## [1.21.24] - 2026-04-03

### Added
- **`jcodemunch-mcp init` subcommand** — one-command onboarding that auto-detects installed MCP clients (Claude Code, Claude Desktop, Cursor, Windsurf, Continue), writes their config entries, injects the Code Exploration Policy into CLAUDE.md, installs worktree lifecycle hooks, and optionally indexes the current directory. Supports `--dry-run`, `--yes` (non-interactive), `--client`, `--claude-md`, `--hooks`, `--index`, and `--no-backup` flags. 27 new tests in `test_init.py`.
- Updated QUICKSTART.md, README.md, and AGENT_HOOKS.md to lead with `init` as the recommended setup path.
- **`audit_agent_config` tool** — scans agent config files (CLAUDE.md, .cursorrules, copilot-instructions.md, .windsurfrules, settings.json, etc.) for token waste. Reports per-file token cost, stale symbol references (cross-referenced against the jcodemunch index), dead file paths, redundancy between global and project configs, bloat patterns, and scope leaks. 34 new tests in `test_audit_agent_config.py`.

## [1.21.23] - 2026-04-02

### Fixed
- **`@includeFirst` Blade directive now parsed** (`laravel.py`) — `_BLADE_INCLUDE_FIRST` regex captures the first (highest-priority) candidate from `@includeFirst(['primary.view', 'fallback.view'])` array arguments and injects it as an import edge. Previously the directive was silently dropped. The fallback candidates are intentionally omitted — only the preferred view is tracked. Closes #203.

## [1.21.22] - 2026-04-02

### Added
- **Stage 4: Cross-language dependency graph (Laravel)** — `laravel.py` now injects extra import edges via the new `get_extra_imports()` provider hook: Blade `@extends`/`@include`/`@includeWhen`/`@includeUnless`/`@component`/`view()` templates, `<x-*>` components, Eloquent relationship edges (`hasMany`/`belongsTo`/etc.), 40 built-in Laravel facades mapped to underlying `Illuminate\*` classes, route→controller file edges, and Inertia.js `Inertia::render`/`inertia()` → Vue/React page component resolution. Frontend `fetch`/`axios`/`useFetch` API calls are matched to Laravel route→controller files via wildcard URI matching.
- **Stage 5: Nuxt/Next.js context providers** — `parser/context/nuxt.py` (`NuxtContextProvider`) parses `pages/` for file-based routing, `server/api/` for API handlers with HTTP method extraction, and scans `composables/`/`utils/` for auto-import edges. `parser/context/nextjs.py` (`NextjsContextProvider`) handles App Router `page`/`layout`/`loading`/`error`/`route` files, route group `(auth)` segment collapsing, middleware detection, and HTTP method extraction from route handlers.
- **Stage 6: FQN ↔ symbol_id translation** — new `parser/fqn.py` with `symbol_to_fqn()` and `fqn_to_symbol()` for bidirectional PSR-4 translation. Optional `fqn` parameter added to `get_symbol_source`, `get_blast_radius`, `search_symbols`, and `get_context_bundle`. `_utils.py` gains `resolve_fqn()` helper. Detailed error messages for missing PSR-4 config, unindexed files, or namespace mismatch.
- **`collect_extra_imports()` merger in `context/base.py`** — called in all 4 indexing pipeline paths; deduplicates by specifier, swallows per-provider failures with a warning.
- **125 new tests** across `test_laravel_provider.py`, `test_nuxt_provider.py`, `test_nextjs_provider.py`, `test_fqn.py`, and `test_find_importers.py`; 2008 total passing.

## [1.21.21] - 2026-04-02

### Changed
- **`files_to_remove` kept as `set` in `incremental_save` (T8)** — `sqlite_store.py` no longer converts the union of `deleted_files` and `changed_files` to a list. The set is preserved through the function and passed to `_patch_index_from_delta`, making membership tests in the hot path (`in files_to_remove`) O(1) instead of O(n). sqlite3 calls receive `tuple(files_to_remove)`.
- **Defer `stat()` until after LRU key check in `load_index` (T9)** — `stat()` is now only called when the cache key is already present; cold-start loads skip the pre-load `stat()` syscall entirely. `_CACHE_MAX_SIZE` raised from 16 → 32.
- **Cap `_REPO_PATH_CACHE` at 512 entries (T23)** — `config.py` trims the oldest entries after each `update()` so the cache cannot grow unbounded in long-running server sessions.
- **`expanduser()` on startup storage path log (T24)** — all three transport startup log lines (`stdio`, `sse`, `streamable-http`) now call `os.path.expanduser()` on the `CODE_INDEX_PATH` value so the logged path shows the real expanded path on Windows instead of `~/.code-index/`.

## [1.21.20] - 2026-04-02

### Added
- **Dart import extractor (T19)** — `imports.py` now includes `_extract_dart_imports` (regex on `import`/`export` statements) registered as `"dart"` in `_LANGUAGE_EXTRACTORS`. Dart files no longer appear in `missing_extractors` after indexing. 9 new tests in `tests/test_dart_imports.py`; `test_parse_warnings.py` updated to use Elixir as the canonical missing-extractor example.
- **LANGUAGE_SUPPORT.md expanded (T20)** — added full extraction rows for CSS, SCSS, SASS, YAML, Ansible, OpenAPI, and JSON; fixed C# entry to list `constant (property/field/event)` symbol types (were incorrectly documented as "not indexed"); corrected CSS row previously listed only under "text search indexing"; SASS entry now documents the CSS-parser fallback.
- **Hypothesis property-based tests (T22)** — `tests/test_property_based.py` with 4 tests across 3 invariant classes: **ID uniqueness** (`TestIdUniqueness` — all symbol IDs in a freshly indexed folder are unique); **Incremental idempotency** (`TestIncrementalIdempotency` — indexing the same files twice yields the same symbol IDs and counts); **No self-imports** (`TestNoSelfImports` — no file in the import graph lists itself as an importer). `hypothesis>=6.0.0` added to dev dependency group. 4 new tests, 90 Hypothesis examples per run.

### Changed
- **`JCODEMUNCH_EXTRA_EXTENSIONS` valid language names** (T21) — added `scss`, `sass`, `less`, `styl`, `yaml`, `ansible`, `json`, `openapi`, `luau` to the documented list in LANGUAGE_SUPPORT.md.

## [1.21.19] - 2026-04-02

### Added
- **Methodology disclosure on all 6 analytical tools (T15)** — every analytical tool response now includes `_meta.methodology` and `_meta.confidence_level`. Values: `get_call_hierarchy` + `get_impact_preview` → `methodology: "text_heuristic"`, `confidence_level: "low"`; `get_symbol_complexity` → `methodology: "stored_metrics"`, `confidence_level: "medium"`; `get_churn_rate` → `methodology: "git_log"`, `confidence_level: "high"`; `get_hotspots` → `methodology: "complexity_x_churn"`, `confidence_level: "medium"`; `get_repo_health` → `methodology: "aggregate"`, `confidence_level: "medium"`; `get_dead_code_v2` → `methodology: "multi_signal"`, `confidence_level: "medium"`. 18 new tests in `tests/test_meta_disclosure.py`.
- **Import-gap signal in `index_folder` (T17)** — `index_folder` now reports `missing_extractors` (sorted list of languages that have symbol extraction but no import extractor) and `parse_warnings` when import graph coverage is incomplete. Example: indexing a folder with `.dart` files yields `missing_extractors: ["dart"]` and a human-readable `parse_warnings` entry. 4 new tests in `tests/test_parse_warnings.py`.
- **`framework_warning` in `get_dead_code_v2` (T18)** — when BFS finds zero standard entry points (`main.py`, `app.py`, etc.), all files are unreachable from entry points and Signal 1 fires for every symbol, inflating dead code counts. `get_dead_code_v2` now includes `framework_warning` in that case, advising callers to pass `entry_point_patterns`. 5 new tests in `tests/test_parse_warnings.py`.

### Fixed
- **Parameter count off-by-one for C-style zero-param functions (T16)** — `_count_params` in `parser/complexity.py` treated `void foo(void)` as a one-parameter function because `"void"` was a non-empty `params_str` with no commas, yielding `commas + 1 = 1`. Added a special case: `params_str == "void"` → return 0, matching the C/C++ convention that `(void)` declares zero parameters. `void*` and multi-param signatures containing `void` are unaffected. 3 new tests in `tests/test_complexity.py`.

## [1.21.18] - 2026-04-02

### Added
- **Correctness fixture library (T12)** — `tests/conftest.py` now exports three shared pytest fixtures (`small_index`, `medium_index`, `hierarchy_index`) that build deterministic synthetic Python repos with documented ground-truth expected outputs. Used across multiple test modules as the canonical in-process test corpus.
- **Tests for `get_class_hierarchy` (T13)** — 22 new tests in `tests/test_class_hierarchy.py` covering: `_parse_bases` unit tests (Python single/multi base, Java extends/implements, combined, lowercase filter, empty); hierarchy BFS error cases (repo not indexed, class not found); ancestor direction (no ancestors for root, direct parent, transitive chain, BFS nearest-first order); descendant direction (all descendants of root, direct children, leaf has none); meta fields (case-insensitive lookup, timing, class info, external base recorded as `"(external)"`).
- **Tests for `get_related_symbols` (T13)** — 14 new tests in `tests/test_related_symbols.py` covering: `_tokenize_name` unit tests (snake_case, camelCase, single word, short-token filter, lowercase); error cases (repo not indexed, symbol not found); same-file grouping (co-located symbols are related, scores positive); name-token overlap scoring; `max_results` cap; meta fields (timing, target symbol in response, required entry fields).
- **Tests for `get_symbol_diff` (T13)** — 15 new tests in `tests/test_symbol_diff.py` covering: error cases (repo A not indexed, repo B not indexed); added symbols (detected, count matches list); removed symbols (detected, count matches list); unchanged symbols (not in added/removed, identical repo → all unchanged); changed symbols (signature change detected, both signatures present); meta fields (timing, symbol counts, repo identifiers).
- **Tests for `suggest_queries` (T13)** — 11 new tests in `tests/test_suggest_queries.py` covering: error cases (repo not indexed, empty index); small repo stats (symbol count, file count, kind distribution, language distribution, example queries non-empty, required query fields); medium repo stats (file count, most_imported file structure, class+function kinds, repo field, timing meta).
- **Tests for rate-limit middleware (T13)** — 10 new tests in `tests/test_rate_limit.py` covering: factory returns `None` when `JCODEMUNCH_RATE_LIMIT` is 0, unset, invalid, or negative; returns non-`None` `Middleware` when limit is positive; sliding-window bucket logic: under-limit all allowed, over-limit rejected, expired entries evicted, limit=1 allows first denies second.
- **In-process perf benchmarks with latency budgets (T14)** — `tests/test_search_perf.py` rewritten from an external-index-dependent skip-if-not-indexed pattern to a fully self-contained suite. Builds a 5-file, 20+ symbol synthetic repo at module scope. New latency assertions: cold search < 2000 ms, warm search < 500 ms (BM25 cache benefit). Correctness assertions: result order stable across two consecutive calls, scores stable with `debug=True`, relevant symbol appears in top-5 for known query, all queries return non-empty results. Zero `pytest.skip` in the file.

### Changed
- **`tests/test_search_perf.py`** — removed `_require_index()` / `pytest.skip` pattern that caused CI-skip when `jcodemunch-mcp` was not indexed locally. Tests now run unconditionally against the synthetic in-process index.

## [1.21.17] - 2026-04-02

### Fixed
- **BM25 `avgdl` inflation corrected (T10)** — `_sym_tokens` computed `_dl` (document length) as `len(tokens)` where `tokens` is the weighted repeated bag (field-repetition multipliers make the name appear 3× in the bag, signature 2×, etc.). This inflated `_dl` and therefore `avgdl`, distorting the BM25 length-normalisation term `K`. Fixed by using `len(set(tokens))` — the unique-token count — consistent with how document-frequency (`df`) is already computed via `for t in set(toks)`. Symbols with overlap across name/signature/summary fields (the common case) were previously penalised as "long documents" when they are not.
- **BM25 rebuild canonical `_dl` enforcement (T11)** — `_compute_bm25` now overwrites `sym["_dl"]` with `len(unique_toks)` on every corpus rebuild. Previously the function used the cached `_dl` from `_sym_tokens`, meaning retained symbols carrying a pre-T10 `_dl` value (the inflated bag length) would make `avgdl` inconsistent with the new formula. The forced rewrite ensures the corpus and all scoring are internally consistent even when the BM25 cache is rebuilt over a mix of freshly computed and carried-forward symbols (e.g., after deferred AI summarisation). 11 new correctness tests added (`tests/test_bm25_correctness.py`).

## [1.21.16] - 2026-04-02

### Fixed
- **Watcher hash-cache double-read race eliminated (T6)** — after each incremental reindex the watcher previously re-read each changed file to compute the new content hash for its in-memory cache. If the file changed again between `index_folder`'s internal read and the watcher's post-reindex re-read, the cache recorded the wrong (newer) hash while the index held the older content. The *next* watchfiles event would then deliver `old_hash=<newer>`, `index_folder` would hash the file, see no difference, and silently skip re-parsing a stale index entry. Fixed by replacing per-file re-reads with a single `_build_hash_cache()` call that reads hashes from the store `index_folder` just wrote — the single authoritative source of truth. Removed the now-dead `_update_hash_cache` / `_remove_from_hash_cache` helpers and the unused `_file_hash` import.

## [1.21.15] - 2026-04-02

### Fixed
- **Deferred-summarize write-lock race eliminated (T7)** — a narrow but real race existed between the deferred summarization thread's generation check ("check 2") and its `incremental_save` call. A concurrent `mark_reindex_start` could bump `deferred_generation` and write a fresh index between those two points; the deferred thread would then overwrite it with stale AI summaries from the previous parse generation. Fixed by introducing a per-repo `threading.Lock` (`_repo_deferred_save_locks` in `reindex_state.py`). The deferred thread holds this lock across check 2 + save; `mark_reindex_start` holds it while bumping `deferred_generation`. This makes check-and-save atomic with respect to generation bumps: either the deferred thread saves before the new generation is written, or it sees the new generation and self-aborts. Added `gen=N` to deferred-summarize log messages so abandoned and completed saves are distinguishable in debug output (pre-T7 instrumentation).

## [1.21.14] - 2026-04-02

### Fixed
- **Threading locks added to all in-process caches (T5)** — four module-level caches were missing `threading.Lock` guards, leaving them vulnerable to data races under concurrent MCP requests (HTTP transport, multi-client stdio). Now protected:
  - `_bare_name_cache` (`tools/_utils.py`) — new `_BARE_NAME_LOCK`; check and write are each under the lock; expensive `list_repos()` I/O happens between the two lock acquisitions so the lock is never held during I/O.
  - `_REPO_PATH_CACHE` (`config.py`) — now protected by the existing `_CONFIG_LOCK`; reads (check) and bulk writes (`update`) are each atomic under the lock; store I/O happens outside.
  - `_alias_map_cache` (`parser/imports.py`) — new `_ALIAS_MAP_LOCK`; same check-then-build-then-write pattern.
  - `_sql_stem_cache` (`parser/imports.py`) — new `_SQL_STEM_LOCK`; same pattern.
- **`invalidate_cache` now clears all 5 in-process caches under their locks (T4.5)** — previously `_sql_stem_cache` was not cleared on `invalidate_cache`, leaving stale SQL stem mappings across re-indexes. Also, `_REPO_PATH_CACHE.clear()` and `_PROJECT_CONFIGS.pop()` were called outside `_CONFIG_LOCK`. All five caches (`_REPO_PATH_CACHE`, `_PROJECT_CONFIGS`, `_PROJECT_CONFIG_HASHES`, `_bare_name_cache`, `_sql_stem_cache`, `_alias_map_cache`) are now cleared under their respective locks.

## [1.21.13] - 2026-04-02

### Fixed
- **`truncated` flag now correct when `token_budget` packing drops results** — in the BM25 search path, the flag was computed using `candidates_scored > len(scored_results)` after the fuzzy augmentation pass, meaning fuzzy results appended after budget packing could mask dropped BM25 results and produce `truncated=False` incorrectly. Now tracked as a separate `budget_truncated` boolean computed immediately after packing; the final flag is `candidates_scored > heap_count or budget_truncated`. The semantic search path was already correct.
- **Call graph `"source"` label corrected** — `get_call_hierarchy` and `get_impact_preview` both returned `"source": "ast"` in `_meta`, implying type-resolved AST analysis. The implementation is word-token regex matching on raw file text. Label changed to `"source": "text_heuristic"` with updated tip text to accurately describe the approach and its limitations (false positives for common names, no dynamic dispatch).

## [1.21.12] - 2026-04-02

### Added
- **PSR-4 namespace resolution for PHP projects (Stage 1 of jgravelle/jcodemunch-mcp#201)** — `find_importers`, `get_blast_radius`, `get_dependency_graph`, `find_dead_code`, and all other import-graph tools now correctly resolve PHP `use App\Models\User` statements to `app/Models/User.php` via `composer.json` PSR-4 autoload mappings. Previously these tools returned zero results for PHP projects using Composer autoloading (effectively every modern PHP project). `build_psr4_map()` and `resolve_php_namespace()` are new public helpers; `CodeIndex` auto-loads the PSR-4 map at load time when PHP files are present and `source_root` is set. 61 new tests added.
- **PHP `property_declaration` symbol indexing** — PHP class properties (`protected $fillable`, `public string $name`, etc.) are now indexed as `property`-kind symbols, fixing a gap in PHP symbol coverage.
- **Laravel context provider** — new `LaravelContextProvider` detects Laravel projects (via `artisan` + `laravel/framework` in `composer.json`) and enriches symbols with: routes parsed from `routes/*.php`, Eloquent relationship/fillable/scope metadata from `app/Models/*.php`, controller-to-route mapping, and event→listener mappings from `EventServiceProvider`. Migration column definitions (from `database/migrations/*.php`) are exposed via `search_columns` under the `laravel_columns` key.
- **Framework profile auto-detection** — `detect_framework()` checks for Laravel, Nuxt, Next.js, Vue SPA, and React SPA at index time and applies framework-specific `ignore_patterns` (e.g. `vendor/`, `.nuxt/`, `.next/`) automatically. Profile `entry_point_patterns` and `layer_definitions` are stored in `context_metadata` for downstream use by `find_dead_code` and `get_layer_violations`. The `index_folder` result now includes `framework_profile` when a profile is active. Zero overhead for non-matching projects.

## [1.21.11] - 2026-04-02

### Added
- **`config --check` now detects CLAUDE.md and hook-script drift (issue #200)** — the existing check command gains two new sections. *CLAUDE.md check* reads `~/.claude/CLAUDE.md` and reports any canonical tool names absent from the file, pointing to `jcodemunch-mcp claude-md --generate` to fix them. *Hook scripts check* scans `~/.claude/hooks/jcodemunch_read_guard.*` and lists any tool names missing from the guard's feedback message.
- **`jcodemunch-mcp claude-md --generate`** — new subcommand that prints a ready-to-paste CLAUDE.md prompt-policy snippet listing all 45 tools in logical categories. `--format=append` outputs only the tools not yet mentioned in the existing `~/.claude/CLAUDE.md`, making it easy to diff-and-merge without rewriting the whole file.
- **`_CANONICAL_TOOL_NAMES` module-level tuple** — authoritative ordered list of every registered tool name, used by both the drift-detection checks and the snippet generator. Validated by a test that asserts no tool produced by `_build_tools_list()` is absent from the tuple.

## [1.21.10] - 2026-04-02

### Fixed
- **`index_folder` full re-index no longer crashes with `'dict' object has no attribute 'summary'` when an existing index is present (issue #198)** — `CodeIndex.symbols` is `list[dict]` (serialized symbol dicts), but the summary-preservation dict comprehension at the top of the full-index path used dot notation (`s.file`, `s.name`, `s.kind`, `s.summary`) instead of bracket notation (`s["file"]`, etc.). Any second full index (or first index when an in-memory stale cache remained after `invalidate_cache` on pre-1.21.8) would immediately fail with this `AttributeError`. Fixed by using `s["key"]` / `s.get("key")` throughout that comprehension. Regression test added.

## [1.21.9] - 2026-04-02

### Added
- **`workflow` MCP prompt** — Claude Code surfaces this as `/mcp__jcodemunch-mcp__workflow`, a slash command that injects step-by-step usage guidance (list_repos → search_symbols → get_symbol_source) directly into context. Provides reliable workflow instructions even when CLAUDE.md is absent or not loaded by the model.
- **`discovery_hint` config flag** (default `true`) — when enabled, the `list_repos` tool description includes a short note reminding Claude to prefer jcodemunch tools over native Grep/Read and to call `ToolSearch` if schemas appear deferred. Set `"discovery_hint": false` in `config.jsonc` to suppress this. Addresses jgravelle/jcodemunch-mcp#199.

## [1.21.8] - 2026-04-02

### Fixed
- **`invalidate_cache` now clears all four in-process caches (X1 / C4-B)** — previously `_REPO_PATH_CACHE`, `_PROJECT_CONFIGS`/`_PROJECT_CONFIG_HASHES`, `_alias_map_cache`, and `_bare_name_cache` were never evicted on `invalidate_cache`, leaving stale import graphs, wrong project config, and unresolvable repo names for the process lifetime. `invalidate_cache` now resolves `source_root` before deletion and clears all four caches in addition to the SQLite/JSON index.
- **`_alias_map_cache` evicted at the start of every `index_folder` run (C6-A)** — tsconfig/jsconfig path alias edits were permanently invisible to re-indexing because `_load_tsconfig_aliases` cached by `source_root` with no invalidation hook. `index_folder` now pops the stale entry before parsing begins, so alias-dependent import edges (`find_importers`, `find_references`, `get_dependency_graph`) are always computed against the current tsconfig.
- **`_sql_stem_cache` keyed by frozenset instead of `id()` (C7-A)** — the single-entry tuple cache used `id(source_files)` as its key. After the previous `source_files` set was GC'd, a new set allocated at the same address received the same `id`, causing a false cache hit and returning SQL stem mappings for the wrong file set. Replaced with a bounded frozenset-keyed dict (max 4 entries) for correct content-based identity.

## [1.21.7] - 2026-04-02

### Fixed
- **`search_symbols` no longer returns centrality-only results for out-of-corpus queries (C3)** — `_bm25_score` now guards the centrality bonus with `score > 0`, so it is only applied when at least one query term contributed BM25 relevance (or an exact name match fired). Previously, queries whose terms appeared in no indexed symbol produced BM25 score 0 for every symbol, but the unconditional `centrality` add-on gave structurally popular files scores > 0, causing them to pass the `score <= 0` filter and surface as apparent results with no indication they were purely import-graph artifacts.

## [1.21.6] - 2026-04-02

### Fixed
- **`_REPO_PATH_CACHE` negative entries no longer permanently suppress project config (C2)** — `_resolve_repo_key` previously wrote `None` into `_REPO_PATH_CACHE` for any identifier that couldn't be resolved at call time (e.g. during watcher startup before the first index completes). That entry was never invalidated, so all subsequent calls — including those after successful indexing — silently fell through to the global config, ignoring `.jcodemunch.jsonc` for the process lifetime. Removed the negative cache write; unknown identifiers now re-scan `list_repos()` on each call (cheap read) so project configs are picked up as soon as the repo is indexed.

## [1.21.5] - 2026-04-02

### Fixed
- **Deferred summarization no longer doubles `CodeIndex.symbols` in memory (C1)** — `_patch_index_from_delta` now builds a set of symbol IDs present in `new_sym_dicts` and skips any retained symbol whose ID is already being replaced. Previously, when `_run_deferred_summarize` called `incremental_save` with `changed_files=[]` and `deleted_files=[]`, every symbol was retained *and* appended again as a summarized copy, doubling the in-memory symbol list. This caused BM25 scores to be computed over a 2× corpus (wrong IDF, wrong `avgdl`) and `search_symbols` to return duplicate hits for the same symbol ID until the next cold cache load.

## [1.21.4] - 2026-04-02

### Added
- **JSON indexing and symbol extraction** — `.json` files are now indexed and text-searchable. Top-level object keys are extracted as `constant` symbols (e.g. `name`, `dependencies`, `scripts` in `package.json`; compiler options keys in `tsconfig.json`). Compound extensions `.openapi.json` / `.swagger.json` and well-known basenames (`openapi.json`, `swagger.json`) continue to resolve to `openapi` as before. Closes reported gap in issue #197 follow-up comment (nikolai-vysotskyi).
- **15 new tests** covering extension detection, compound-extension precedence, top-level key extraction, symbol kind/metadata, array-at-root edge case, and `parse_file()` dispatch.

## [1.21.3] - 2026-04-02

### Added
- **CSS preprocessor support (SCSS, SASS, Less, Stylus)** — `.scss`, `.sass`, `.less`, and `.styl` files are now indexed and text-searchable. SCSS additionally gets full symbol extraction: `$variables` → `constant`, `@mixin` → `function`, `@function` → `function`, rule-set selectors (including `%placeholders`) → `class`, `@media`/`@supports` → `type`. SASS, Less, and Stylus have no tree-sitter grammar in the pack so they index for text search only — `search_text` with `file_pattern: "**/*.scss"` now returns results. Closes issue #197 (reported by nikolai-vysotskyi).
- **24 new tests** covering SCSS extension detection, variable/mixin/function/selector/at-rule extraction, symbol ID uniqueness, byte metadata, `parse_file()` dispatch, empty-file edge case, and text-only confirmation for Less/SASS/Stylus.

## [1.21.2] - 2026-04-02

### Added
- **Summary preservation during full reindex** — when a full reindex runs over a repo that already has an index (e.g. after a schema bump or explicit `incremental=False` call), symbols whose file content hash is unchanged now reuse their existing AI-generated summaries instead of triggering new AI calls. Symbols in changed or new files are summarized normally. This is automatic and requires no parameter changes — the optimization fires whenever a prior index is present and has stored file hashes. Addresses issue #192 (reported by rknighton).

## [1.21.1] - 2026-04-02

### Fixed
- **`summarizer_concurrency` now respected by OpenAI-compatible provider** — `OpenAIBatchSummarizer` was reading concurrency from `OPENAI_CONCURRENCY` env var with a hardcoded default of 1, ignoring the `summarizer_concurrency` config key entirely. The default is now `_config.get("summarizer_concurrency", 4)`, so the config file (and `JCODEMUNCH_SUMMARIZER_CONCURRENCY` env var) correctly controls concurrency for all providers. `OPENAI_CONCURRENCY` env var still overrides when set. The `config` diagnostic display now shows the effective fallback value from config rather than the stale hardcoded 1. Reported by nikolai-vysotskyi (issue #194).

## [1.21.0] - 2026-04-02

### Added
- **CSS symbol extraction** — CSS files now produce real symbols: rule-set selectors (`.container`, `#header`, `body`, `:root`, compound selectors like `.navbar .item`) are extracted as `kind: class`; `@keyframes` as `kind: function`; `@media` and `@supports` blocks as `kind: type`. Previously CSS was indexed (text-searchable) but `get_file_outline` always returned 0 symbols. Fixes reported issue where users believed CSS was not supported at all.
- **17 new tests** (1641 total, 7 skipped): full coverage of CSS selector extraction, @-rule extraction, edge cases (empty file, comment-only file), symbol ID uniqueness, and `parse_file()` dispatch.

## [1.20.0] - 2026-04-02

### Changed
- **Lazy tool imports** — all 45 tool module imports in `server.py` are now deferred to the first `call_tool()` dispatch for each tool. Previously, importing `server.py` loaded every tool module (and their transitive dependencies: tree-sitter, httpx, pathspec, subprocess wrappers) regardless of which tools the session actually uses. Now only 7 tool modules load at startup (via the watcher's `index_folder` chain). Tools not called in a session are never imported. This reduces cold-start overhead for query-only sessions that never trigger indexing.
- **`_build_tools_list()` helper** — `list_tools()` now delegates to a named `_build_tools_list()` function, making the tool list construction easier to test and reason about independently of the MCP decorator.
- **Test patch targets updated** — tests that previously patched `jcodemunch_mcp.server.xxx` (where `xxx` is a tool function) now correctly patch `jcodemunch_mcp.tools.xxx_module.xxx_func`, which is where the name is looked up during dispatch. This follows Python's `unittest.mock.patch` best practice: patch where the name is looked up, not where it is defined.
- **No API or output schema changes.** Zero new tools, zero removed tools, zero field changes.

## [1.19.0] - 2026-04-01

### Added
- **`assessment` field on `get_hotspots` entries** — each hotspot now includes `assessment: "low" | "medium" | "high"` based on `hotspot_score` thresholds (low ≤ 3, medium ≤ 10, high > 10). Allows an LLM to relay findings directly without interpreting the raw score.
- **`architecture.layers` documented in README** — the `.jcodemunch.jsonc` reference now includes the full `architecture` block schema with a worked example for a typical layered Python project (api → service → repo → db). Used by `get_layer_violations`.
- **2 new tests** (1624 total, 7 skipped): `test_assessment_field_present`, `test_high_complexity_no_churn_is_low`.

## [1.18.0] - 2026-04-01

### Added
- **Session-level LRU result cache** — `get_blast_radius` and `find_references` (single-identifier mode) now cache their results for the duration of the MCP session. Repeated calls with the same arguments return instantly from the in-process cache with `_meta.cache_hit: true` instead of re-running the expensive BFS traversal and file-content scans. Cache is a 256-entry LRU (OrderedDict); oldest entries are evicted first. Thread-safe via the existing `_State` lock.
- **Automatic cache invalidation** — the result cache is cleared after any `index_repo`, `index_folder`, `index_file`, or `invalidate_cache` call so stale results are never served after re-indexing.
- **`get_session_stats` — `result_cache` field** — the existing `get_session_stats` tool now includes a `result_cache` section: `{total_hits, total_misses, hit_rate, cached_entries}`. Useful for tuning and for verifying that the cache is working in real sessions.
- **18 new tests** (1622 total, 7 skipped): `test_result_cache.py` covers get/put, hit/miss counters, by-tool breakdown, invalidation (all-repos and repo-specific), LRU eviction at maxsize, and the `result_cache` field in `get_session_stats`.

## [1.17.0] - 2026-04-01

### Added
- **`get_symbol_complexity(symbol_id)`** — returns cyclomatic complexity, max nesting depth, parameter count, line count, and a human-readable `assessment` ("low" / "medium" / "high") for any indexed function or method. Data is read directly from the index (no re-parsing); requires INDEX_VERSION 7 (jcodemunch-mcp >= 1.16).
- **`get_churn_rate(target, days=90)`** — returns git commit count, unique authors, first-seen date, last-modified date, and `churn_per_week` for a file or symbol over a configurable look-back window. `assessment` field: "stable" (≤1/week), "active" (≤3/week), "volatile" (>3/week). Accepts a relative file path or a symbol ID. Requires a locally indexed repo.
- **`get_hotspots(top_n=20, days=90, min_complexity=2)`** — ranks functions and methods by `hotspot_score = cyclomatic × log(1 + commits_last_N_days)`. Surfaces code that is both complex and frequently changed — the highest bug-introduction risk in the repo. Identical methodology to Adam Tornhill's CodeScene hotspot analysis. Falls back gracefully when git is unavailable (complexity-only scoring).
- **`get_repo_health(days=90)`** — one-call triage snapshot: total files/symbols, dead-code %, average cyclomatic complexity, top-5 hotspots, dependency cycle count, and unstable module count. Produces a `summary` string suitable for immediate relay. Designed to be the first tool called in any new session. Thin aggregator — delegates to individual tools, no duplicated logic.
- **Bug fix: complexity data now correctly persisted through `save_index`** — the symbol serialization dict in `save_index` was missing `cyclomatic`, `max_nesting`, and `param_count` fields (they were computed by the parser but silently dropped before DB write). Fixed by including these fields in the serialized dict. All tools depending on complexity data (`get_extraction_candidates`, `get_symbol_complexity`, `get_hotspots`) now return accurate values after a fresh `index_folder`.
- **36 new tests** (1604 total, 7 skipped): `test_symbol_complexity.py`, `test_churn_rate.py`, `test_hotspots.py`, `test_repo_health.py`.

## [1.16.0] - 2026-04-01

### Added
- **`check_rename_safe(symbol_id, new_name)`** — new tool that detects name collisions before renaming a symbol. Scans the symbol's defining file and every file that imports it, checking for an existing symbol already using the proposed new name. Returns `{safe, conflicts, checked_files}`. Use before any rename/refactor to avoid silent breakage.
- **`get_dead_code_v2()`** — enhanced dead-code detection with three independent evidence signals per function/method: (1) the symbol's file is not reachable from any entry point via the import graph, (2) no indexed symbol calls this symbol in the call graph, (3) the symbol name is not re-exported from any `__init__` or barrel file. Each result includes a `confidence` score (0.33 = 1 signal, 0.67 = 2 signals, 1.0 = all 3). More reliable than single-signal detection. Accepts `min_confidence` (default 0.5) and `include_tests` parameters.
- **`get_extraction_candidates(file_path, min_complexity, min_callers)`** — new tool that identifies functions worth extracting to a shared module. A candidate must have high cyclomatic complexity (doing a lot) AND be called from multiple other files (already implicitly shared). Results ranked by `score = cyclomatic × caller_file_count`.
- **Complexity metrics stored at index time** — `INDEX_VERSION` bumped from 6 to 7. Three new fields per symbol (functions and methods only): `cyclomatic` (McCabe complexity), `max_nesting` (bracket-nesting depth), `param_count`. Computed from symbol body text at index time via `parser/complexity.py`. Existing indexes are automatically migrated (columns added as NULL; re-index to populate). Consumed by `get_extraction_candidates`.
- **37 new tests** (1568 total, 7 skipped): `test_complexity.py`, `test_check_rename_safe.py`, `test_dead_code_v2.py`, `test_extraction_candidates.py`.

### Changed
- `INDEX_VERSION` is now 7 (was 6). Re-index required to populate complexity fields; existing indexes load and operate correctly with complexity = 0.

## [1.15.3] - 2026-04-01

### Added
- **`config --upgrade`** — new CLI flag that adds missing keys from the current version's template into an existing `config.jsonc`, preserving all user-set values. Useful after upgrading jcodemunch-mcp to a newer version that introduces new config keys. Updates the `"version"` field automatically and reports which keys were injected. Addresses the gap implied by the `"version"` field / "additive migrations" comment in `config.jsonc`. Requested by nikolai-vysotskyi in issue #191.

## [1.15.2] - 2026-04-01

### Added
- **`summarize_repo(repo, force)`** — new MCP tool that re-runs AI summarization on all symbols in an existing index. Useful when `index_folder` completed without AI summaries (deferred background thread was interrupted, AI was disabled at index time, or the provider wasn't configured). With `force=true`, clears all existing summaries and re-runs the full 3-tier pipeline (docstring → AI → signature fallback). Returns `{success, symbol_count, updated, skipped, duration_seconds}`. Reported by nikolai-vysotskyi in issue #190.
- **AI summarization progress logging** — `summarize_batch` (both `BaseSummarizer` and `OpenAIBatchSummarizer`) now logs progress at INFO level every ~10% of batches: `"AI summarization: N/M symbols (P%)"`. Start and completion are also logged. Previously there was zero feedback during 10–30 minute summarization runs on large codebases.
- **`summarization_deferred` field in `index_folder` response** — when the watcher-driven fast path fires a background summarization thread, the response now includes `"summarization_deferred": true` and a note suggesting `summarize_repo` as a synchronous fallback.

### Changed
- **Deferred summarization thread logging promoted to INFO** — thread start (`"Deferred AI summarization started for owner/repo (N symbols)"`) and completion (`"Deferred AI summarization saved N symbols for owner/repo"`) are now logged at INFO instead of DEBUG, making them visible in default logging configurations.

## [1.15.1] - 2026-04-01

### Fixed
- **Empty-array false positive in singular/batch mode detection** — `get_symbol_source`, `find_references`, `check_references`, `find_importers`, and `get_file_outline` each support a singular param (e.g. `symbol_id`) and a batch param (e.g. `symbol_ids`). Some MCP clients (observed with OpenCode + GPT codex) pass the batch param as an empty array `[]` even when invoking singular mode. Since `[] is not None` is `True`, the mutual-exclusivity guard fired and returned `"Provide symbol_id or symbol_ids, not both."` / `"Internal error processing find_references"`. Fixed by normalizing empty lists to `None` before the guard check in all five tools. Reported by razorree in issue #189.

## [1.15.0] - 2026-04-01

### Added
- **`get_dependency_cycles()`** — new tool detecting circular import chains in the repository. Uses Kosaraju's algorithm (iterative, no recursion limit) on the file-level import graph. Returns each strongly-connected component (set of files mutually reachable via imports) as a cycle. Useful for finding architectural problems and test-isolation blockers.
- **`get_coupling_metrics(module_path)`** — new tool returning afferent coupling (Ca, how many files import this module), efferent coupling (Ce, how many files this module imports), instability score I = Ce/(Ca+Ce), and a human-readable `assessment` ("stable" | "neutral" | "unstable" | "isolated"). Identifies fragile modules and guides refactoring priorities.
- **`get_layer_violations(rules?)`** — new tool validating inter-module imports against declared architectural layer boundaries. Reports every import that crosses a forbidden boundary. Rules can be passed directly or defined in `.jcodemunch.jsonc` under `architecture.layers`. Output includes `file`, `file_layer`, `import_target`, `target_layer`, `rule_violated` per violation.
- **`architecture` config key** — new `.jcodemunch.jsonc` / global config key (type: dict) for per-project layer definitions. Structure: `{"layers": [{"name": str, "paths": [str], "may_not_import": [str]}]}`. Consumed by `get_layer_violations` when no inline `rules` are provided.
- **36 new tests** (1527 total, 9 skipped) in `tests/test_architecture_tools.py`.

## [1.14.0] - 2026-04-01

### Added
- **`get_call_hierarchy(symbol_id, direction, depth)`** — new tool returning incoming callers and outgoing callees for any indexed symbol, N levels deep (default 3). Uses AST-derived detection: callers = symbols in importing files whose bodies mention the name; callees = imported symbols mentioned in the symbol's source body. No LSP required. Results include `{id, name, kind, file, line, depth}` per entry and `source: "ast"` in `_meta`.
- **`get_impact_preview(symbol_id)`** — new tool answering "what breaks if I delete or rename this?". DFS over the call graph transitively, returns all affected symbols grouped by file (`affected_by_file`) with call-chain paths (`call_chains`) showing how each symbol is reached from the target.
- **`_call_graph.py`** — shared internal module with `find_direct_callers`, `find_direct_callees`, `bfs_callers`, `bfs_callees` used by all call-graph tools.

### Changed
- **`get_blast_radius`** — new optional `call_depth` param (default 0, disabled). When `call_depth > 0`, adds `callers` list of symbols that actually call the target symbol (call-level analysis) alongside the existing import-level `confirmed`/`potential` lists. All existing fields unchanged; fully backwards-compatible.
- **`find_references`** — new optional `include_call_chain` param (default false, singular mode only). When true, each reference entry gains `calling_symbols`: symbols in that file whose source bodies mention the identifier. Batch mode ignores this flag.

## [1.13.2] - 2026-03-31

### Fixed
- **Per-project language config ignored during parsing** — `parse_file()` was calling `is_language_enabled(language)` without forwarding the `repo` path, so it always consulted the global config and never the per-project `.jcodemunch.jsonc`. Projects that declared their own `"languages"` list got `symbol_count: 0` when the global config had `"languages": []` (the recommended default). Fixed by threading `repo` from every `parse_file` call site (`index_folder`, `index_file`, `get_changed_symbols`, and all three pipeline functions in `_indexing_pipeline`) down to the language-gate check. `index_repo` is unaffected (remote repos have no local project config). Reported and root-caused by AmaralVini in issue #187.

## [1.13.1] - 2026-03-30

### Changed
- **`get_repo_outline` 2-level directory grouping for large repos** — when a repository has more than 500 indexed files, `directories` now groups by two path components (e.g., `src/api/`, `src/models/`) instead of only the top-level directory. Results are capped at 40 entries (highest file-count dirs first). Small repos (≤ 500 files) retain the existing 1-level behavior. Agents navigating large monorepos get actionable directory hints rather than a single coarse bucket.

## [1.13.0] - 2026-03-30

### Added
- **Cross-repository dependency tracking** — import graph tools (`find_importers`, `get_blast_radius`, `get_dependency_graph`, `get_changed_symbols`) now accept an opt-in `cross_repo: bool` parameter (default `false`). When enabled, the tools traverse repo boundaries using a package registry built from manifest files (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `*.csproj`). Cross-repo results are annotated with `"cross_repo": true` and `"source_repo"`. Zero behavior change when `cross_repo` is omitted.
- **`get_cross_repo_map` tool** — new tool that returns the full cross-repository dependency map at the package level, or filtered to a single repo. Shows `depends_on` and `depended_on_by` for each indexed repo, plus a flat `cross_repo_edges` list.
- **`package_names` field on `CodeIndex`** — package names are extracted from manifest files at index time (both `index_folder` and `index_repo`) and stored in the SQLite meta table. Old indexes load cleanly with `package_names = []`.
- **`package_registry.py`** — new module providing `extract_package_names()` (5 ecosystems: Python, JS/TS, Go, Rust, C#), `extract_root_package_from_specifier()` (language-aware root extraction), `build_package_registry()` (in-memory registry with mtime-based cache), and `resolve_cross_repo_file()`.
- **`cross_repo_default` config key** — boolean default for the `cross_repo` parameter across all import graph tools. Env var: `JCODEMUNCH_CROSS_REPO_DEFAULT`. Default: `false`.
- **53 new tests** (1431 total, 9 skipped).

## [1.12.9] — docs patch 2026-03-30

### Changed
- **QUICKSTART.md Step 3** — upgraded AGENT_HOOKS.md footnote to an `[!IMPORTANT]` callout naming the "pressure bypass" failure mode (agent sees CLAUDE.md rule, ignores it under load) and explaining why hooks are needed for hard enforcement.
- **QUICKSTART.md Troubleshooting** — added entry for "Claude uses jCodeMunch in simple tasks but falls back to Read/Grep in complex ones" pointing to AGENT_HOOKS.md.
- **AGENT_HOOKS.md intro** — sharpened to explicitly name the failure mode: the agent sees the rule and skips it anyway because native tools feel faster under pressure or in long sessions.

## [1.12.9] - 2026-03-29

### Added
- **Tri-state `use_ai_summaries`** (PR #186 — contributed by MariusAdrian88) — Config key and `JCODEMUNCH_USE_AI_SUMMARIES` env var now accept three values: `"auto"` (new default; auto-detect provider from API keys, identical to previous `true`), `true` (use explicit `summarizer_provider` + `summarizer_model` from config), `false` (disable AI summarization entirely). Existing boolean `true`/`false` configs are fully backward-compatible.
- **`summarizer_model` config key** — Override the default model for any provider via config or `JCODEMUNCH_SUMMARIZER_MODEL` env var. Priority: config key > provider-specific env var (`ANTHROPIC_MODEL`, `GOOGLE_MODEL`, etc.) > hardcoded default. Applies to all providers.
- **`summarizer_max_failures` config key** — Circuit breaker threshold (default 3). After this many consecutive batch failures the summarizer stops calling the API and falls back to signature summaries for all remaining symbols. A successful batch resets the counter. Set 0 to disable. Thread-safe (`threading.Lock`). Configurable via `JCODEMUNCH_SUMMARIZER_MAX_FAILURES`.
- **OpenRouter provider** — New provider via `OPENROUTER_API_KEY` using the OpenAI-compatible API at `openrouter.ai/api/v1`. Default model: `meta-llama/llama-3.3-70b-instruct:free` (zero cost). Auto-detect priority: last in chain (after GLM-5). Explicit selection: `summarizer_provider: "openrouter"` or `JCODEMUNCH_SUMMARIZER_PROVIDER=openrouter`. `jcodemunch-mcp config` now shows active OpenRouter section.
- **`test_summarizer` diagnostic tool** — Sends a probe request to the configured AI summarizer and reports status: `ok`, `disabled`, `no_provider`, `misconfigured`, `fallback`, `timeout`, or `error`. Disabled by default (remove from `disabled_tools` in config to enable). Optional `timeout_ms` parameter (default 15000).
- **`strict_timeout_ms` config key** — Configures the maximum milliseconds to block in `freshness_mode: strict` before proceeding with a stale index (previously hardcoded at 500ms). Default: 500.
- **`embed_model` config key** — Promotes `JCODEMUNCH_EMBED_MODEL` env var to a config file setting. Configures the sentence-transformers model for local semantic embeddings. Config key takes priority over env var.
- **`summarizer_provider` config key** — Promotes `JCODEMUNCH_SUMMARIZER_PROVIDER` env var to a config file setting. Takes priority over env var.
- **60+ new tests** (1397 total, 7 skipped).

### Fixed
- **`languages_adaptive` config key** (PR #185 — contributed by MariusAdrian88) — New boolean config key that enables automatic language detection based on files actually found in the indexed folder, overriding the `languages` allowlist for that run. Useful when indexing polyglot repos without maintaining an explicit language list.
- **`meta_fields` default changed to `[]`** (PR #185) — Previously defaulted to `null` (all meta fields included); now defaults to `[]` (no `_meta` block) for token-efficient responses. Set to `null` in config to restore all meta fields.

## [1.12.7] - 2026-03-29

### Added
- **MiniMax and GLM-5 summarizer providers** (PR #184 — contributed by SkaldeStefan) — `MINIMAX_API_KEY` auto-detects MiniMax M2.7 (`api.minimax.io/v1`) and `ZHIPUAI_API_KEY` auto-detects GLM-5 (`api.z.ai`), both via the existing OpenAI-compatible summarizer path. `JCODEMUNCH_SUMMARIZER_PROVIDER` env var added for explicit selection (`anthropic`, `gemini`, `openai`, `minimax`, `glm`, `none`). Auto-detect priority: Anthropic → Gemini → OpenAI-compatible → MiniMax → GLM-5. Remote providers (including MiniMax/GLM) still require `allow_remote_summarizer: true` in `config.jsonc`. `get_provider_name()` exported from `jcodemunch_mcp.summarizer`. `jcodemunch-mcp config` now shows active provider and new MiniMax/GLM sections. 10 new tests (1332 total).

### Fixed
- **`test_get_provider_name_unknown_falls_back_to_auto` test isolation** — test did not clear higher-priority env vars before auto-detecting MiniMax, causing false `anthropic` result in environments where `ANTHROPIC_API_KEY` is set.

## [1.12.6] - 2026-03-29

### Fixed
- **Gemini `CODE_RETRIEVAL_QUERY` KeyError on legacy SDK** (follow-up to #181) — The legacy `google-generativeai` package does not include `CODE_RETRIEVAL_QUERY` in its `TaskType` proto enum (it was introduced in the newer `google-genai` SDK). Passing that string to `genai.embed_content` caused a `KeyError` during semantic search. A new `_normalise_gemini_task_type` helper probes the installed SDK's `TaskType` enum at runtime and falls back `CODE_RETRIEVAL_QUERY` → `RETRIEVAL_QUERY` on legacy installs, producing equivalent retrieval quality. New SDK installs with `CODE_RETRIEVAL_QUERY` are unaffected. 5 new tests (1322 total).

## [1.12.5] - 2026-03-29

### Added
- **YAML and Ansible parser support** (PR #183 — contributed by SkaldeStefan) — `.yaml` and `.yml` files are now indexed as first-class symbols. A path-heuristic layer (`_looks_like_ansible_path`) automatically promotes Ansible-structured files (playbooks, roles, group_vars, host_vars, tasks, handlers, defaults) to the `ansible` language so they receive Ansible-aware symbol extraction: plays as `class`, tasks as `function`, roles and handlers as `type`, and variables as `constant`. Generic YAML falls back to a structural walker that emits container keys as `type` and scalar keys as `constant`. Multi-document YAML (multiple `---` sections) is handled correctly. pyyaml is already a base dependency — no extra install step. 8 new tests (1317 total).

## [1.12.4] - 2026-03-29

### Added
- **Task-aware embedding for Gemini** (closes #181) — When `GOOGLE_EMBED_MODEL` is configured, `embed_repo` now passes `task_type="RETRIEVAL_DOCUMENT"` to `genai.embed_content` for document indexing, and `search_symbols` passes `task_type="CODE_RETRIEVAL_QUERY"` when embedding the search query. Models that support task types (e.g. `text-embedding-004`, Gemini Embedding 2) produce measurably better code retrieval results; models that do not simply ignore the parameter. Other providers (sentence-transformers, OpenAI) are unaffected.
- **`GEMINI_EMBED_TASK_AWARE` env var** — Set to `0` / `false` / `no` / `off` to opt out of task-type routing (default: on). Useful if your Gemini model predates task-type support.
- **`embed_task_type` stored in meta** — The task type used when building the embedding index is now persisted. If you toggle `GEMINI_EMBED_TASK_AWARE`, `embed_repo` detects the mismatch and automatically forces a re-embed so query and document embeddings always come from the same task-type space.
- **`task_type` field in `embed_repo` response** — Present when a task type was applied; absent for providers that do not use one.
- 7 new tests (1309 total): `_gemini_task_aware` default/opt-out, Gemini document task type in `embed_repo`, `CODE_RETRIEVAL_QUERY` routing in `search_symbols`, opt-out disables task types, task-type change triggers re-embed, `EmbeddingStore` task type round-trip.

## [1.12.3] - 2026-03-29

### Fixed
- **Cross-process LRU cache invalidation** — SQLite WAL mode does not always update the `.db` file's mtime on commit. The watcher (a separate process) was writing new index data that the MCP server's in-memory cache never detected, causing agents to see stale results. New `_db_mtime_ns()` helper checks `max(db_mtime, db-wal_mtime)` so WAL writes are detected without an explicit cache eviction call. `os.utime()` added after `save_index()` and `incremental_save()` as a belt-and-suspenders measure; `os.utime()` runs *before* `_cache_put()` so the cached mtime matches what cross-process readers compute.
- **`get_file_tree` silently ignored `max_files`** — the parameter was present in the MCP schema but was never passed through `call_tool` dispatch.
- **Config template stale entries** — `wait_for_fresh` (removed v1.12.0) was still listed in `disabled_tools` template; staleness `_meta` fields (`index_stale`, `reindex_in_progress`, `stale_since_ms`) were still listed in `meta_fields` template.

### Added
- **`file_tree_max_files` config key** — configures the `get_file_tree` result cap via `config.jsonc` or `JCODEMUNCH_FILE_TREE_MAX_FILES` env var (default 500). Per-call `max_files` param still overrides.
- **`gitignore_warn_threshold` config key** — configures the missing-`.gitignore` warning threshold in `index_folder` via `config.jsonc` or `JCODEMUNCH_GITIGNORE_WARN_THRESHOLD` env var (default 500). Set `0` to disable entirely.
- **Config template overhaul** — all keys now have inline documentation; tools and meta_fields lists sorted alphabetically; all missing keys added (`trusted_folders_whitelist_mode`, `exclude_secret_patterns`, `path_map`, watcher params, transport docs); `version` field added for future migration tooling. Note: the template now defaults to `"meta_fields": []` (no `_meta` in responses) rather than `null` (all fields) — better for token efficiency; users who want `_meta` should uncomment the desired fields.
- 5 new tests covering `_db_mtime_ns` (no-WAL, WAL-newer, WAL-older) and the full cross-process cache invalidation scenario (1302 total). Contributed by MariusAdrian88 (PR #180).

## [1.12.2] - 2026-03-29

### Added
- **`.razor` (Blazor component) file support** — `.razor` extension now mapped to the `razor` language spec alongside `.cshtml`. `_parse_razor_symbols` extended to emit `@page` route directives and `@inject` dependency injection bindings as constant symbols, making Blazor component routes and injected services first-class navigable symbols. Includes `Counter.razor` test fixture and 8 new tests (1298 total). Contributed by drax1222 (PR #182).

## [1.12.1] - 2026-03-28

### Fixed
- **`get_file_tree` token overflow on large indexes** (closes #178) — results are now capped at `max_files` (default 500). When truncated, the response includes `truncated: true`, `total_file_count`, and a `hint` suggesting `path_prefix` to scope the query. `max_files` is exposed as a tool parameter so callers can raise it explicitly if needed.
- **`index_folder` silent over-inclusion** (closes #178) — when no `.gitignore` is present in the repo root and ≥ 500 files are indexed, a warning is now included in the result advising the user to add a `.gitignore` and re-index.
- 10 new tests (1288 total).

## [1.12.0] - 2026-03-28

### Removed
- **`check_freshness` and `wait_for_fresh` MCP tools** — no client ever consumed these; removing them saves ~400 schema tokens per call. Server-side freshness management via `freshness_mode` config key (`relaxed`/`strict`) remains fully functional.
- **Staleness `_meta` fields** (`index_stale`, `reindex_in_progress`, `stale_since_ms`) — ~30-50 tokens of annotated noise per response. The watcher still manages freshness internally; strict mode blocks silently in `call_tool` before returning clean results.
- `powered_by` removed from `_meta` common fields.

### Fixed
- **Watcher config layering** — `_get_watcher_enabled()` previously bypassed `config_module.get()` and read `JCODEMUNCH_WATCH` env var directly, silently ignoring the `"watch"` key in `config.jsonc`. Precedence is now: CLI flag > config file (with env var as fallback only when key absent).
- **Hash-cache miss reindex skip** — when the watcher's in-memory hash cache missed, the fallback read the file from disk. By the time `watchfiles` delivers the event the file already has new content, making `old_hash == new_hash` and silently skipping the change. Fixed with a `"__cache_miss__"` sentinel that guarantees re-parse on any cache miss.
- **Flaky Windows tests from SQLite WAL cache contamination** — tests that modified the DB directly didn't invalidate the in-memory LRU cache; WAL mode on Windows doesn't always update file mtime on write, so the cache key matched stale data. Fixed via `tests/conftest.py` autouse fixtures for cache clear and config reset, plus targeted `_cache_evict()` calls after direct DB writes.
- `test_openai_summarizer_timeout_config` now correctly flows `allow_remote_summarizer` through `load_config()` instead of reading from `config.get()` directly.

### Added
- **Config-driven watcher parameters** — all watcher options are now configurable via `config.jsonc` (CLI flags remain as overrides). New keys:
  - `watch_debounce_ms` (int, default 2000) — was wired in config.py but not forwarded to watcher kwargs
  - `watch_paths` (list, default `[]` → CWD) — folders to watch
  - `watch_extra_ignore` (list, default `[]`) — additional gitignore-style patterns
  - `watch_follow_symlinks` (bool, default `false`)
  - `watch_idle_timeout` (int or null, default `null`) — auto-stop after N minutes idle
  - `watch_log` (str or null, default `null`) — log watcher output to file; `"auto"` = temp file
- 25 new tests (1285 total).

## [1.11.17] - 2026-03-27

### Added
- **Optional semantic / embedding search (Feature 8)** — hybrid BM25 + vector search, opt-in only, zero mandatory new dependencies.
  - `search_symbols` gains three new params: `semantic` (bool, default `false`), `semantic_weight` (float 0–1, default 0.5), `semantic_only` (bool, default `false`). When `semantic=false` (default) there is zero performance impact and zero new imports.
  - **New `embed_repo` tool** — precomputes and caches all symbol embeddings in one pass (`batch_size`, `force` params). Optional warm-up; `search_symbols` lazily embeds missing symbols on first semantic query.
  - **New `EmbeddingStore`** — thin SQLite CRUD layer (`symbol_embeddings` table) in the existing per-repo `.db` file. Embeddings serialised as float32 BLOBs via stdlib `array` module. Persists across restarts; invalidatable per-symbol for incremental reindex.
  - **Three embedding providers** (priority order): local `sentence-transformers` (`JCODEMUNCH_EMBED_MODEL` env var), Gemini (`GOOGLE_API_KEY` + `GOOGLE_EMBED_MODEL`), OpenAI (`OPENAI_API_KEY` + `OPENAI_EMBED_MODEL`). `OPENAI_API_KEY` alone does **not** activate embeddings (prevents conflation with local-LLM summariser use).
  - **Hybrid ranking**: `combined = (1−w) × bm25_normalised + w × cosine_similarity`. BM25 normalised by max score over the candidate set. `semantic_weight=0.0` produces identical results to pure BM25.
  - **Pure Python cosine similarity** — `math.sqrt` + `sum()`, no numpy required.
  - `semantic=true` with no provider configured returns `{"error": "no_embedding_provider", "message": "..."}` (structured error, not a crash).
  - New optional dep: `pip install jcodemunch-mcp[semantic]` installs `sentence-transformers>=2.2.0`.
  - 22 new tests.

## [1.11.16] - 2026-03-27

### Added
- **Token-budgeted context assembly (Feature 5)** — two new capabilities:
  - `get_context_bundle` gains `token_budget`, `budget_strategy`, and `include_budget_report` params. When `token_budget` is set, symbols are ranked and trimmed to fit. `budget_strategy` controls how: `most_relevant` (default) ranks by file import in-degree, `core_first` keeps the primary symbol first then ranks the rest by centrality, `compact` strips all source bodies and returns signatures only. `include_budget_report=true` adds a `budget_report` field showing `budget_tokens`, `used_tokens`, `included_symbols`, `excluded_symbols`, and `strategy`. Fully backward-compatible: all new params default to existing behavior.
  - **New `get_ranked_context` tool** — standalone token-budgeted context assembler. Takes a `query` + `token_budget` (default 4000) and returns the best-fit symbols with their full source, greedy-packed by combined score. `strategy` controls ranking: `combined` (BM25 + PageRank weighted sum, default), `bm25` (pure text relevance), `centrality` (PageRank only). Optional `include_kinds` and `scope` params restrict the candidate set. Response includes per-item `relevance_score`, `centrality_score`, `combined_score`, `tokens`, and `source`. Token counting uses `len(text) // 4` heuristic with optional `tiktoken` upgrade (no hard dep). No new dependencies. 19 new tests.

## [1.11.15] - 2026-03-27

### Added
- **`get_changed_symbols` tool** — maps a git diff to affected symbols. Given two commits (`since_sha` / `until_sha`, defaulting to index-time SHA vs HEAD), returns `added_symbols`, `removed_symbols`, and `changed_symbols` (with `change_type`: "added", "removed", "modified", or "renamed"). `renamed` detection fires when body hash is identical but name differs. Set `include_blast_radius=true` to also return downstream importers (with `max_blast_depth` hop limit). Requires a locally indexed repo (`index_folder`); GitHub-indexed repos return a clear error. Requires `git` on PATH; graceful error if not available. Filters index-storage files (e.g. `.index/`) from the diff when the storage dir is inside the repo. No new dependencies. 12 new tests.

## [1.11.14] - 2026-03-27

### Added
- **`find_dead_code` tool** — finds files and symbols unreachable from any entry point using the import graph. Entry points auto-detected by filename (`main.py`, `__main__.py`, `conftest.py`, `manage.py`, etc.), `__init__.py` package roots, and `if __name__ == "__main__"` guards (Python only). Returns `dead_files` and `dead_symbols` with confidence scores: `1.0` = zero importers, no framework decoration; `0.9` = zero importers in a test file; `0.7` = all importers are themselves dead (cascading). Parameters: `granularity` ("symbol"/"file"), `min_confidence` (default 0.8), `include_tests` (bool), `entry_point_patterns` (additional glob roots). No new dependencies. 13 new tests.

## [1.11.13] - 2026-03-27

### Fixed
- **Manifest watcher reliability** — replaced `watchfiles.awatch()` in `_manifest_watcher` with a simple 0.5s polling loop. `watchfiles` was unreliable on Windows (especially in temp directories used by tests and agent hooks), causing the manifest watcher to silently miss create/remove events. Polling the manifest file's size every 500ms is sufficient for this append-only JSONL file and works reliably on all platforms.

## [1.11.12] - 2026-03-27

### Added
- **PageRank / centrality ranking** — new `get_symbol_importance` tool returns the most architecturally important symbols in a repo, ranked by full PageRank or simple in-degree on the import graph. Parameters: `top_n` (default 20), `algorithm` ("pagerank" or "degree"), `scope` (subdirectory filter). Response includes `symbol_id`, `rank`, `score`, `in_degree`, `out_degree`, `kind`, `iterations_to_converge`. New `sort_by` parameter on `search_symbols` ("relevance" | "centrality" | "combined") — "centrality" filters by BM25 query match but ranks by PageRank; "combined" adds PageRank as weighted boost to BM25 score; "relevance" (default) is unchanged (backward compatible). `get_repo_outline` now includes `most_central_symbols` (top 10 symbols by PageRank score, one representative per file, alongside the existing `most_imported_files`). PageRank implementation: damping=0.85, convergence threshold=1e-6, max 100 iterations, dangling-node correction, cached in `_bm25_cache` per `CodeIndex` load. 23 new tests.

## [1.11.11] - 2026-03-27

### Added
- **Fuzzy symbol search** — `search_symbols` gains three new parameters: `fuzzy` (bool, default `false`), `fuzzy_threshold` (float, default `0.4`), and `max_edit_distance` (int, default `2`). When enabled, a trigram Jaccard + Levenshtein pass runs as fallback when BM25 confidence is low (top score < 0.1) or when explicitly requested. Fuzzy results carry `match_type="fuzzy"`, `fuzzy_similarity`, and `edit_distance` fields; BM25 results carry `match_type="exact"`. Zero behavioral change when `fuzzy=false` (default). No new dependencies — pure stdlib (`frozenset` trigrams + Wagner-Fischer edit distance). 21 new tests.

## [1.11.10] - 2026-03-27

### Added
- **Blast radius depth scoring** — `get_blast_radius` now always returns `direct_dependents_count` (depth-1 count) and `overall_risk_score` (0.0–1.0, weighted by hop distance using `1/depth^0.7`). New `include_depth_scores=true` parameter adds `impact_by_depth` (files grouped by BFS layer, each with a `risk_score`). Flat `confirmed`/`potential` lists are preserved unchanged (backward compatible). 14 new tests.

## [1.11.9] - 2026-03-27

### Fixed
- **Windows CI: trusted_folders tests** — `_platform_path_str` was using `str(Path(...))` which on Windows returns backslash paths (`C:\work`). When embedded raw into f-string JSON literals in tests, the backslash produced invalid `\escape` sequences, causing `config.jsonc` parse failures across all 4 Windows matrix legs (6 tests failing). Fixed by switching to `.as_posix()`, which returns forward-slash paths (`C:/work`) that are valid in both JSON and Windows pathlib.

## [1.11.8] - 2026-03-27

### Added
- **`trusted_folders` allowlist for `index_folder`** (PR #175, credit: @tmeckel) — new `trusted_folders` config key (plus `trusted_folders_whitelist_mode`) restricts or blocks indexing by path. Whitelist mode (default) allows only explicitly named roots; blacklist mode blocks specific paths while trusting all others. Path-aware matching (not string-prefix). Project config supports `.`, `./subdir`, and bare relative paths. Escape-attempt paths are rejected. Empty list preserves existing behavior (backward compatible). Env var fallback via `JCODEMUNCH_TRUSTED_FOLDERS`.

## [1.11.7] - 2026-03-27

### Added
- **`check_freshness` tool** — compares the git HEAD SHA recorded at index time against the current HEAD for locally indexed repos. Returns `fresh` (bool), `indexed_sha`, `current_sha`, and `commits_behind`. GitHub repos return `is_local: false` with an explanatory message. `get_repo_outline` staleness check upgraded to SHA-based comparison (accurate) with time-based fallback for GitHub/no-git repos; `is_stale` added to `_meta`. 8 new tests.

## [1.11.6] - 2026-03-27

### Added
- **Structured file-cap warnings** — `index_folder` and `index_repo` now surface `files_discovered`, `files_indexed`, and `files_skipped_cap` fields plus a human-readable `warning` when the file cap is hit. Previously a silent "note".
- **`_meta` hint on single-symbol responses** — `search_symbols` and `get_symbol_source` single-symbol responses now include a `_meta` hint pointing to `get_context_bundle`.

### Changed
- **Benchmark docs** — `METHODOLOGY.md` expanded with a "Common Misreadings" section; reproducible results table added to README.

## [1.11.5] - 2026-03-26

### Fixed
- **`tsconfig.json`/`jsconfig.json` parsed as JSONC** — previously `json.loads()` silently failed on commented tsconfigs (TypeScript projects commonly use `//` comments in tsconfig.json), leaving `alias_map` empty and causing `find_importers`/`get_blast_radius` to return 0 alias-based results. Now parsed with the same JSONC stripper used for `config.jsonc`. Also adds a test for nested layouts with specific `@/lib/*` overrides. Closes #170. 5 new tests.

## [1.11.4] - 2026-03-25

### Fixed
- **TypeScript/SvelteKit path alias resolution** — `find_importers`, `get_blast_radius`, `get_dependency_graph`, and 5 other import-graph tools now resolve `@/*`, `$lib/*`, and other configured aliases by reading `compilerOptions.paths` from `tsconfig.json`/`jsconfig.json` at the project root. Also resolves TypeScript's ESM `.js`→`.ts` extension convention. `alias_map` is auto-loaded from `source_root` and cached at module level. Closes #169. 10 new tests.

## [1.11.3] - 2026-03-25

### Added
- **Debug logging for silent skip paths** — all three skip paths (`skip_dir`, `skip_file`, `secret`) now emit debug-level log lines. `skip_dir` and `skip_file` counters added to the discovery summary. `exclude_secret_patterns` config option suppresses specific `SECRET_PATTERNS` entries (workaround for `*secret*` glob false-positives on full relative paths in Go monorepos). (PR #168, credit: @DrHayt) 6 new tests.

## [1.11.2] - 2026-03-25

### Fixed
- **`resolve_repo` hang on Windows** — added `stdin=subprocess.DEVNULL` to the git subprocess call in `_git_toplevel()`. Without it, the git child process inherits the MCP stdio pipe and blocks indefinitely. Same pattern fixed in v1.1.7 for `index_folder`. Closes #166.
- **`parse_git_worktrees` hang on Windows** (watcher) — same missing `stdin=subprocess.DEVNULL` fix, preventative.

## [1.8.3] - 2026-03-18

### Added
- **`find_importers`: `has_importers` flag** — each result now includes `has_importers: bool`. When `false`, the importer itself has no importers, revealing transitive dead code chains without requiring recursive calls. Implemented as one additional O(n) pass over the import graph; no re-indexing required. Closes #132. Identified via 50-iteration dead code A/B test (#130).

## [1.8.2] - 2026-03-18

### Changed
- **`get_file_outline` tool description** — now explicitly states "full signatures (including parameter names)" and adds "Use signatures to review naming at parameter granularity without reading the full file." Parameter names were always present in the `signature` field; the description now makes this discoverable. Closes #131.

## [1.8.1] - 2026-03-18

### Fixed
- **Dynamic `import()` detection in JS/TS/Vue** — `find_importers` now detects Vue Router lazy routes and other code-splitting patterns using `import('specifier')` call syntax. Previously these files appeared to have zero importers and were misclassified as dead. Identified via 50-iteration dead code A/B test (#130, @Mharbulous); 4 Vue view files affected.

## [1.8.0] - 2026-03-18

### Security
- **Supply-chain integrity check** — `verify_package_integrity()` added to `security.py` and called at startup. Uses `importlib.metadata.packages_distributions()` to identify the distribution that actually owns the running code. If it differs from the canonical `jcodemunch-mcp`, a `SECURITY WARNING` is printed to stderr. Catches the fork-republishing attack class described at https://news.ycombinator.com/item?id=47428217. Silent for source/editable installs.

### Added
- **`authors` and `[project.urls]`** in `pyproject.toml` — PyPI pages now display official provenance metadata (author, homepage, issue tracker).

## [1.7.9] - 2026-03-18

### Added
- **JS/TS const extraction** — top-level `const` and `export const` declarations in JavaScript, TypeScript, and TSX are now indexed as `constant` symbols. Arrow functions and function expressions assigned to consts are correctly skipped (handled by existing function extraction). Accepts all identifier naming conventions for JS/TS.
- **`index_file` tool** (PR #126, credit: @thellMa) — re-index a single file instantly after editing. Locates the correct index by scanning `source_root` of all indexed repos (picks most specific match), validates security, computes hash + mtime, and exits early if the file is unchanged. Parses with tree-sitter, runs context providers, and calls `incremental_save()` for a surgical single-file update. Registered as a new MCP tool with `path`, `use_ai_summaries`, and `context_providers` parameters.
- **mtime optimization** (PR #126, credit: @thellMa) — `index_folder` and `index_repo` now check file modification time (`st_mtime_ns`) before reading or hashing. Files with unchanged mtimes are skipped entirely; hashes are computed lazily only for files whose mtime changed. Indexes store a `file_mtimes` dict; old indexes without mtime data fall back to hash-all for backward compatibility.
- **`watch-claude` CLI subcommand** — auto-discover and watch Claude Code worktrees via two complementary modes:
  - **Hook-driven mode** (recommended): install `WorktreeCreate`/`WorktreeRemove` hooks that call `jcodemunch-mcp hook-event create|remove`. Events are written to `~/.claude/jcodemunch-worktrees.jsonl` and `watch-claude` reacts instantly via filesystem watch.
  - **`--repos` mode**: `jcodemunch-mcp watch-claude --repos ~/project1 ~/project2` polls `git worktree list --porcelain` and filters for Claude-created worktrees (branches matching `claude/*` or `worktree-*`).
  - Both modes can run simultaneously. When a worktree is removed, the watcher stops and the index is invalidated.
- **`hook-event` CLI subcommand** — `jcodemunch-mcp hook-event create|remove` reads Claude Code's hook JSON from stdin and appends to the JSONL manifest. Designed to be called from Claude Code's `WorktreeCreate`/`WorktreeRemove` hooks.

### Changed
- **Shared indexing pipeline** (PR #126, credit: @thellMa) — new `_indexing_pipeline.py` consolidates logic previously duplicated across `index_folder`, `index_repo`, and the new `index_file`: `file_languages_for_paths()`, `language_counts()`, `complete_file_summaries()`, `parse_and_prepare_incremental()`, and `parse_and_prepare_full()`. All three tools now call the shared pipeline functions.
- `main()` subcommand set expanded to include `hook-event` and `watch-claude`.

## [1.7.2] - 2026-03-17

### Fixed
- **Stale `context_metadata` on incremental save** — `{}` from active providers was treated as falsy, silently preserving old metadata instead of clearing it. Changed to `is not None` check.
- **`_resolve_description` discarding surrounding text** — `"Prefix {{ doc('name') }} suffix"` now preserves both prefix and suffix instead of returning only the doc block content.
- **dbt tags only extracted from `config.tags`** — top-level `model.tags` (valid in dbt schema.yml) are now merged with `config.tags`, deduplicated.
- **Redundant `posixpath.sep` check** in `resolve_specifier` — removed duplicate of adjacent `"/" not in` check.
- **Inaccurate docstring** on `_detect_dbt_project` — said "max 2 levels deep" but only checks root + immediate children.

### Changed
- **Concurrent AI summarization** — `BaseSummarizer.summarize_batch()` now uses `ThreadPoolExecutor` (default 4 workers) for Anthropic and Gemini providers. Configurable via `JCODEMUNCH_SUMMARIZER_CONCURRENCY` env var. Matches the pattern already used by `OpenAIBatchSummarizer`. ~4x faster on large projects.
- **O(1) stem resolution** — `resolve_specifier` stem-matching fallback now uses a cached dict lookup instead of O(n) linear scan. Significant perf improvement for dbt projects with thousands of files, called in tight loops across 7 tools.
- **`collect_metadata` collision warning** — logs a warning when two providers emit the same metadata key, instead of silently overwriting via `dict.update()`.
- **`find_importers`/`find_references` tool descriptions** — now note that `{{ source() }}` edges are extracted but not resolvable since sources are external.
- **`search_columns` cleanup** — moved `import fnmatch` to top-level; documented empty-query + `model_pattern` behavior (acts as "list all columns for matching models").

## [1.7.0] - 2026-03-17

### Added
- **Centrality ranking** — `search_symbols` BM25 scores now include a log-scaled bonus for symbols in frequently-imported files, surfacing core utilities as tiebreakers when relevance scores are otherwise equal.
- **`get_symbol_diff`** — diff two indexed snapshots by `(name, kind)`. Reports added, removed, and changed symbols using `content_hash` for change detection. Index the same repo under two names to compare branches.
- **`get_class_hierarchy`** — traverse inheritance chains upward (ancestors via `extends`/`implements`/Python parentheses) and downward (subclasses/implementors) from any class. Handles external bases not in the index.
- **`get_related_symbols`** — find symbols related to a given one via three heuristics: same-file co-location (weight 3.0), shared importers (1.5), name-token overlap (0.5/token).
- **Git blame context provider** — `GitBlameProvider` auto-activates during `index_folder` when a `.git` directory is present. Runs a single `git log` at index time and attaches `last_author` + `last_modified` to every file via the existing context provider plugin system.
- **`suggest_queries`** — scan the index and get top keywords, most-imported files, kind/language distribution, and ready-to-run example queries. Ideal first call when exploring an unfamiliar repository.
- **Markdown export** — `get_context_bundle` now accepts `output_format="markdown"`, returning a paste-ready document with import blocks, docstrings, and fenced source code.

## [1.6.1] - 2026-03-17

### Added
- **`watch` CLI subcommand** (PR #113, credit: @DrHayt) — `jcodemunch-mcp watch <path>...` monitors one or more directories for filesystem changes and triggers incremental re-indexing automatically. Uses `watchfiles` (Rust-based, async) for OS-native notifications with configurable debounce. Install with `pip install jcodemunch-mcp[watch]`.
- `watchfiles>=1.0.0` optional dependency under `[watch]` and `[all]` extras.

### Changed
- `main()` refactored to use argparse subcommands (`serve`, `watch`). Full backwards compatibility preserved — bare `jcodemunch-mcp` and legacy flags like `--transport` continue to work unchanged.

## [1.6.0] - 2026-03-17

### Added
- **`get_context_bundle` multi-symbol bundles** — new `symbol_ids` (list) parameter fetches multiple symbols in one call. Import statements are deduplicated when symbols share a file. New `include_callers=true` flag appends the list of files that directly import each symbol's defining file.

### Changed
- Single `symbol_id` (string) remains fully backward-compatible.

## [1.5.9] - 2026-03-17

### Added
- **`get_blast_radius` tool** — find every file affected by changing a symbol. Given a symbol name or ID, traverses the reverse import graph (up to 3 hops) and text-scans each importing file. Returns `confirmed` (imports the file + references the symbol name) and `potential` (imports the file only — wildcard/namespace imports). Handles ambiguous names by listing all candidate IDs.

## [1.5.8] - 2026-03-17

### Changed
- **BM25 search** — replaced hand-tuned substring scoring in `search_symbols` with proper BM25 + IDF. IDF is computed over all indexed symbols at query time (no re-indexing required). CamelCase/snake_case tokenization splits `getUserById` into `get`, `user`, `by`, `id` for natural language queries. Per-field repetition weights: name 3×, keywords 2×, signature 2×, summary 1×, docstring 1×. Exact name match retains a +50 bonus. `debug=true` now returns per-field BM25 score breakdowns.

## [1.5.7] - 2026-03-17

### Added
- **`get_dependency_graph` tool** — file-level import graph with BFS traversal up to 3 hops. `direction` parameter: `imports` (what this file depends on), `importers` (what depends on this file), or `both`. Returns nodes, edges, and per-node neighbor map. Built from existing index data — no re-indexing required.

## [1.5.6] - 2026-03-17

### Added
- **`get_session_stats` tool** — process-lifetime token savings dashboard. Reports tokens saved and cost avoided (current session + all-time cumulative), per-tool breakdown, session duration, and call counts.

## [1.5.5] - 2026-03-17

### Added
- **Tiered loading** (`detail_level` on `search_symbols`) — `compact` returns id/name/kind/file/line only (~15 tokens/result, ideal for discovery); `standard` is unchanged (default); `full` inlines source, docstring, and end_line.
- `byte_length` field added to all `search_symbols` result entries regardless of detail level.

## [1.5.4] - 2026-03-17

### Added
- **Token budget search** (`token_budget=N` on `search_symbols`) — greedily packs results by byte length until the budget is exhausted. Overrides `max_results`. Reports `tokens_used` and `tokens_remaining` in `_meta`.

## [1.5.3] - 2026-03-17

### Added
- **Microsoft Dynamics 365 Business Central AL language support** (PR #110, credit: @DrHayt) — `.al` files are now indexed. Extracts procedures, triggers, codeunits, tables, pages, reports, and XML ports.

## [1.5.2] - 2026-03-17

### Fixed
- `tokens_saved` always reporting 0 in `get_file_outline` and `get_repo_outline`.

## [1.5.1] - 2026-03-16

### Added
- **Benchmark reproducibility** — `benchmarks/METHODOLOGY.md` with full reproduction details.
- **HTTP bearer token auth** — `JCODEMUNCH_HTTP_TOKEN` env var secures HTTP transport endpoints.
- **`JCODEMUNCH_REDACT_SOURCE_ROOT`** env var redacts absolute local paths from responses.
- **Schema validation on index load** — rejects indexes missing required fields.
- **SHA-256 checksum sidecars** — index integrity verification on load.
- **GitHub rate limit retry** — exponential backoff in `fetch_repo_tree`.
- **`TROUBLESHOOTING.md`** with 11 common failure scenarios and solutions.
- CI matrix extended to Windows and Python 3.13.

### Changed
- Token savings labeled as estimates; `estimate_method` field added to all `_meta` envelopes.
- `search_text` raw byte count now only includes files with actual matches.
- `VALID_KINDS` moved to a `frozenset` in `symbols.py`; server-side validation rejects unknown kinds.

## [1.5.0] - 2026-03-16

### Added
- **Cross-process file locking** via `filelock` — prevents index corruption under concurrent access.
- **LRU index cache with mtime invalidation** — re-reads index JSON only when the file changes on disk.
- **Metadata sidecars** — `list_repos` reads lightweight sidecar files instead of loading full index JSON.
- **Streaming file indexing** — peak memory reduced from ~1 GB to ~500 KB during large repo indexing.
- **Bounded heap search** — `O(n log k)` instead of `O(n log n)` for bounded result sets.
- **`BaseSummarizer` base class** — deduplicates `_build_prompt`/`_parse_response` across AI summarizers.
- +13 new tests covering `search_columns`, `get_context_bundle`, and ReDoS hardening.

### Fixed
- **ReDoS protection** in `search_text` — pathological regex patterns are rejected before execution.
- **Symlink-safe temp files** — atomic index writes use `tempfile` rather than direct overwrite.
- **SSRF prevention** — API base URL validation rejects non-HTTP(S) schemes.

## [1.4.4] - 2026-03-16

### Added
- **Assembly language support** (PR #105, credit: @astrobleem) — WLA-DX, NASM, GAS, and CA65 dialects. `.asm`, `.s`, `.wla` files indexed. Extracts labels, macros, sections, and directives as symbols.
- `"asm"` added to `search_symbols` language filter enum.

## [1.4.3] - 2026-03-15

### Fixed
- Cross-process token savings loss — `token_tracker` now uses additive flush so savings accumulated in one process are not overwritten by a concurrent flush from another.

## [1.4.2] - 2026-03-15

### Added
- XML `name` and `key` attribute extraction — elements with `name=` or `key=` attributes are now indexed as `constant` symbols (closes #102).

## [1.4.1] - 2026-03-14

### Added
- **Minimal CLI** (`cli/cli.py`) — 47-line command-line interface over the shared `~/.code-index/` store covering all jMRI ops: `list`, `index`, `outline`, `search`, `get`, `text`, `file`, `invalidate`.
- `cli/README.md` — explains MCP as the preferred interface and documents CLI usage.

### Changed
- README onboarding improved: added "Step 3: Tell Claude to actually use it" with copy-pasteable `CLAUDE.md` snippets.

## [1.4.0] - 2026-03-13

### Added
- **AutoHotkey hotkey indexing** — all three hotkey syntax forms are now extracted as `kind: "constant"` symbols: bare triggers (`F1::`), modifier combos (`#n::`), and single-line actions (`#n::Run "notepad"`). Only indexed at top level (not inside class bodies).
- **`#HotIf` directive indexing** — both opening expressions (`#HotIf WinActive(...)`) and bare reset (`#HotIf`) are indexed, searchable by window name or expression string.
- **Public benchmark corpus** — `benchmarks/tasks.json` defines the 5-task × 3-repo canonical task set in a tool-agnostic format. Any code retrieval tool can be evaluated against the same queries and repos.
- **`benchmarks/README.md`** — full methodology documentation: baseline definition, jMunch workflow, how to reproduce, how to benchmark other tools.
- **`benchmarks/results.md`** — canonical tiktoken-measured results (95.0% avg reduction, 20.2x ratio, 15 task-runs). Replaces the obsolete v0.2.22 proxy-based benchmark files.
- Benchmark harness now loads tasks from `tasks.json` when present, falling back to hardcoded values.

## [1.3.9] - 2026-03-13

### Added
- **OpenAPI / Swagger support** — `.openapi.yaml`, `.openapi.yml`, `.openapi.json`, `.swagger.yaml`, `.swagger.yml`, `.swagger.json` files are now indexed. Well-known basenames (`openapi.yaml`, `swagger.json`, etc.) are auto-detected regardless of directory. Extracts: API info block, paths as `function` symbols, schema definitions as `class` symbols, and reusable component schemas.
- `get_language_for_path` now checks well-known OpenAPI basenames before compound-extension matching.
- `"openapi"` added to `search_symbols` language filter enum.

## [1.3.8] - 2026-03-13

### Added
- **`get_context_bundle` tool** — returns a self-contained context bundle for a symbol: its definition source, all direct imports, and optionally its callers/implementers. Replaces the common `get_symbol` + `find_importers` + `find_references` round-trip with a single call. Scoped to definition + imports in this release.

## [1.3.7] - 2026-03-13

### Added
- **C# properties, events, and destructors** (PR #100) — `get { set {` property accessors, `event EventHandler Name`, and `~ClassName()` destructors are now extracted as symbols alongside existing C# method/class support.

## [1.3.6] - 2026-03-13

### Added
- **XML / XUL language support** (PR #99) — `.xml` and `.xul` files are now indexed. Extracts: document root element as a `type` symbol, elements with `id` attributes as `constant` symbols, and `<script src="...">` references as `function` symbols. Preceding `<!-- -->` comments captured as docstrings.

## [1.3.5] - 2026-03-13

### Added
- **GitHub blob SHA incremental indexing** — `index_repo` now stores per-file blob SHAs from the GitHub tree response and diffs them on re-index. Only files whose SHA changed are re-downloaded and re-parsed. Previously, every incremental run downloaded all file contents before discovering what changed.
- **Tokenizer-true benchmark harness** — `benchmarks/harness/run_benchmark.py` measures real tiktoken `cl100k_base` token counts for the jMunch retrieval workflow vs an "open every file" baseline on identical tasks. Produces per-task markdown tables and a grand summary.

## [1.3.4] - 2026-03-13

### Added
- **Search debug mode** — `search_symbols` now accepts `debug=True` to return per-result field match breakdown (name score, signature score, docstring score, keyword score). Makes ranking decisions inspectable.

## [1.3.3] - 2026-03-12

### Added
- **`search_columns` tool** — structured column metadata search across indexed models. Framework-agnostic: auto-discovers any provider that emits a `*_columns` key in `context_metadata` (dbt, SQLMesh, database catalogs, etc.). Returns model name, file path, column name, and description. Supports `model_pattern` glob filtering and source attribution when multiple providers contribute. 77% fewer tokens than grep for column discovery.
- **dbt import graph** — `find_importers` and `find_references` now work for dbt SQL models. Extracts `{{ ref('model') }}` and `{{ source('source', 'table') }}` calls as import edges, enabling model-level lineage and impact analysis out of the box.
- **Stem-matching resolution** — `resolve_specifier()` now resolves bare dbt model names (e.g., `dim_client`) to their `.sql` files via case-insensitive stem matching. No path prefix needed.
- **`get_metadata()` on ContextProvider** — new optional method for providers to persist structured metadata at index time. `collect_metadata()` pipeline function aggregates metadata from all active providers with error isolation.
- **`context_metadata` on CodeIndex** — new field for persisting provider metadata (e.g., column info) in the index JSON. Survives incremental re-indexes.
- Updated `CONTEXT_PROVIDERS.md` with column metadata convention (`*_columns` key pattern), `get_metadata()` API docs, architecture data flow, and provider ideas table

### Changed
- `search_columns` tool description updated to reflect framework-agnostic design
- `_LANGUAGE_EXTRACTORS` now includes `"sql"` mapping to `_extract_sql_dbt_imports()`

## [1.2.11] - 2026-03-10

### Added
- **Context provider framework** (PR #89, credit: @paperlinguist) — extensible plugin system for enriching indexes with business metadata from ecosystem tools. Providers auto-detect their tool during `index_folder`, load metadata from project config files, and inject descriptions, tags, and properties into AI summaries, file summaries, and search keywords. Zero configuration required.
- **dbt context provider** — the first built-in provider. Auto-detects `dbt_project.yml`, parses `{% docs %}` blocks and `schema.yml` files, and enriches symbols with model descriptions, tags, and column metadata. Install with `pip install jcodemunch-mcp[dbt]`.
- `JCODEMUNCH_CONTEXT_PROVIDERS=0` env var and `context_providers=False` parameter to disable provider discovery entirely
- `context_enrichment` key in `index_folder` response reports stats from all active providers
- `CONTEXT_PROVIDERS.md` — architecture docs, dbt provider details, and community authoring guide for new providers

## [1.2.9] - 2026-03-10

### Fixed
- **Eliminated redundant file downloads on incremental GitHub re-index** (fixes #86) — `index_repo` now stores the GitHub tree SHA after every successful index and compares it on subsequent calls before downloading any files. If the tree SHA is unchanged, the tool returns immediately ("No changes detected") without a single file download. Previously, every incremental run fetched all file contents from GitHub before discovering nothing had changed, causing 25–30 minute re-index sessions. The fast-path adds only one API call (the tree fetch, which was already required) and exits in milliseconds when the repo hasn't changed.
- **`list_repos` now exposes `git_head`** — so AI agents can reason about index freshness without triggering any download. When `git_head` is absent or doesn't match the current tree SHA, the agent knows a re-index is warranted.

## [1.2.8] - 2026-03-09

### Fixed
- **Massive folder indexing speedup** (PR #80, credit: @briepace) — directory pruning now happens at the `os.walk` level by mutating `dirnames[:]` before descent. Previously, skipped directories (node_modules, venv, .git, dist, etc.) were fully walked and their files discarded one by one. Now the walker never enters them at all. Real-world result: 12.5 min → 30 sec on a vite+react project.
  - Fixed `SKIP_FILES_REGEX` to use `.search()` instead of `.match()` so suffix patterns like `.min.js` and `.bundle.js` are correctly matched against the end of filenames
  - Fixed regex escaping on `SKIP_FILES` entries (`re.escape`) and the xcodeproj/xcworkspace patterns in `SKIP_DIRECTORIES`

## [1.2.7] - 2026-03-09

### Fixed
- **Performance: eliminated per-call disk I/O in token savings tracker** — `record_savings()` previously did a disk read + write on every single tool call. Now uses an in-memory accumulator that flushes to disk every 10 calls and at process exit via `atexit`. Telemetry is also batched at flush time instead of spawning a new thread per call. Fixes noticeable latency on rapid tool use sequences (get_file_outline, search_symbols, etc.).

## [1.2.6] - 2026-03-09

### Added
- **SQL language support** — `.sql` files are now indexed via `tree-sitter-sql` (derekstride grammar)
  - CREATE TABLE, VIEW, FUNCTION, INDEX, SCHEMA extracted as symbols
  - CTE names (`WITH name AS (...)`) extracted as function symbols
  - dbt Jinja preprocessing: `{{ }}`, `{% %}`, `{# #}` stripped before parsing
  - dbt directives extracted as symbols: `{% macro %}`, `{% test %}`, `{% snapshot %}`, `{% materialization %}`
  - Docstrings from preceding `--` comments and `{# #}` Jinja block comments
  - 27 new tests covering DDL, CTEs, Jinja preprocessing, and all dbt directive types
- **Context provider framework** — extensible plugin system for enriching indexes with business metadata from ecosystem tools. Providers auto-detect their tool during `index_folder`, load metadata from project config files, and inject descriptions, tags, and properties into AI summaries, file summaries, and search keywords. Zero configuration required.
- **dbt context provider** — the first built-in provider. Auto-detects `dbt_project.yml`, parses `{% docs %}` blocks and `schema.yml` files, and enriches symbols with model descriptions, tags, and column metadata.
- `context_enrichment` key in `index_folder` response reports stats from all active providers
- New optional dependency: `pip install jcodemunch-mcp[dbt]` for schema.yml parsing (pyyaml)
- `CONTEXT_PROVIDERS.md` documentation covering architecture, dbt provider details, and guide for writing new providers
- 58 new tests covering the context provider framework, dbt provider, and file summary integration

### Fixed
- `test_respects_env_file_limit` now uses `JCODEMUNCH_MAX_FOLDER_FILES` (the correct higher-priority env var) instead of the legacy `JCODEMUNCH_MAX_INDEX_FILES`

## [1.2.5] - 2026-03-08

### Added
- `staleness_warning` field in `get_repo_outline` response when the index is 7+ days old — configurable via `JCODEMUNCH_STALENESS_DAYS` env var

## [1.2.4] - 2026-03-08

### Added
- `duration_seconds` field in all `index_folder` and `index_repo` result dicts (full, incremental, and no-changes paths) — total wall-clock time rounded to 2 decimal places
- `JCODEMUNCH_USE_AI_SUMMARIES` env var now mentioned in `index_folder` and `index_repo` MCP tool descriptions for discoverability
- Integration test verifying `index_folder` is dispatched via `asyncio.to_thread` (guards against event-loop blocking regressions)

## [1.0.0] - 2026-03-07

First stable release. The MCP tool interface, index schema (v3), and symbol
data model are now considered stable.

### Languages supported (25)
Python, JavaScript, TypeScript, TSX, Go, Rust, Java, C, C++, C#, Ruby, PHP,
Swift, Kotlin, Dart, Elixir, Gleam, Bash, Nix, Vue SFC, EJS, Verse (UEFN),
Laravel Blade, HTML, and plain text.

### Highlights from the v0.x series
- Tree-sitter AST parsing for structural, not lexical, symbol extraction
- Byte-offset content retrieval — `get_symbol` reads only the bytes for that
  symbol, never the whole file
- Incremental indexing — re-index only changed files on subsequent runs
- Atomic index saves (write-to-tmp, then rename)
- `.gitignore` awareness and configurable ignore patterns
- Security hardening: path traversal prevention, symlink escape detection,
  secret file filtering, binary file detection
- Token savings tracking with cumulative cost-avoided reporting
- AI-powered symbol summaries (optional, requires `anthropic` extra)
- `get_symbols` batch retrieval
- `context_lines` support on `get_symbol`
- `verify` flag for content hash drift detection

### Performance (added in v0.2.31)
- `get_symbol` / `get_symbols`: O(1) symbol lookup via in-memory dict (was O(n))
- Eliminated redundant JSON index reads on every symbol retrieval
- `SKIP_PATTERNS` consolidated to a single source of truth in `security.py`

### Breaking changes from v0.x
- `slugify()` removed from the public `parser` package export (was unused)
- Index schema v3 is incompatible with v1 indexes — existing indexes will be
  automatically re-built on first use
