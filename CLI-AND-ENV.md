# CLI Subcommands and Env Vars — the derivable half

The derivable half of `CLAUDE.md`'s **CLI Subcommands** and **Env Vars**:
what each subcommand and variable *does*. Rotated out on 2026-08-31 under
Maintenance Practice 5, verbatim.

⚠⚠ **This file is NOT loaded into a session, and that is the point.**
`jcodemunch-mcp --help` and `jcodemunch-mcp config` answer most of it live,
and `src/jcodemunch_mcp/config.py` holds every default — which is why it can
leave the always-loaded budget without losing anything.

⚠⚠ **The invariants did NOT move.** Every subcommand and variable whose
entry states a prohibition, a constraint whose violation causes a defect, or a
rationale keeps its full row in `CLAUDE.md` and is DELIBERATELY ABSENT here.
Nothing is duplicated between the two files:
`tests/test_cli_env_split.py` fails if a row appears in both, or in neither.

⚠ So a row missing from this file is not undocumented — it is documented in
`CLAUDE.md`, because what it needed said was worth a session's context.

⚠ User-facing configuration guidance lives in `CONFIGURATION.md`, which
documents 18 of these variables in prose form. That overlap predates this
split and is not resolved by it.

## CLI Subcommands
| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (`stdio`, `sse`, or `streamable-http`) |
| `init` | Interactive one-command onboarding: detect MCP clients, write config, install the silent hooks (re-index after edits, PreCompact, TaskCompleted), index. `--policy` also writes the Code Exploration Policy (CLAUDE.md, AGENTS.md, Cursor/Windsurf rules); `--guidance` also installs the model-facing hooks (PreToolUse advice, SubagentStart briefing, SessionStart restore). An install that already has either keeps it; `--remove-guidance` removes both, with backups, and does nothing else. `--git-hook` writes a git post-checkout hook into the repository in this directory (see `hook-post-checkout`); `uninstall` run in the same repository removes it unless `--keep-git-hook` |
| `install <agent>` | (v1.105.1) Per-agent shortcut over `init`; targets: `claude-code`, `claude-desktop`, `cursor`, `windsurf`, `continue`, `all`. `install --list` enumerates; `install --status` reports state (JSON via `--json`). Like `init`, writes the policy only with `--policy` and the model-facing hooks only with `--guidance`. **v1.107.0:** `--skills` also emits the Claude Agent Skill bundle (`~/.claude/skills/jcodemunch/SKILL.md` by default; `--skills-scope project` for project-local) |
| `install-status` | (v1.105.1) Read-only report of which clients / policies / hooks currently have jcodemunch wired; `--json` for scripting. **v1.107.0:** also reports `skills.global.present` and `skills.project.present`. Also reports `guidance` (the model-facing hooks and policy blocks still installed) and, when there are any, the `init --remove-guidance` command that removes them; it never removes them itself |
| `diagnose` | Write one local report to `<storage>/diagnose/` and print its path: server versions, Python and OS; each index as `index-N` with file count, symbol count, source bytes, index bytes on disk and embeddings yes/no/unknown; tool surface, profile, tool count and schema tokens; each policy target as present, current or differing, with a digest; per tool calls, tokens returned and tokens saved, with the date each count began. Built from a fixed list of fields and of what each value may be: no path, no repository name. Sends nothing. `--stdout` prints it and writes no file; `--include-anon-id` adds the anonymous savings id, which is otherwise left out. The per-tool counts come from `_tool_counters.json` (config key `tool_counters`, default on); counts a running server has not written yet are not in the report. |
| `storage` | Print how much disk the index store uses: its path and total, each index by name (largest first, database and content cache apart) and every other entry in the store; `--top N` (default 10, `-1` for all), `--json`. Stats files only: it opens no database, follows no symlink, and counts a file it can't read as not measured, never as 0 bytes |
| `watch <paths>` | File watcher — auto-reindex on change |
| `watch-claude` | Auto-discover and watch Claude Code worktrees |
| `watch-all` | Auto-discover **every** locally-indexed repo and keep it fresh; rediscovers on interval |
| `watch-install` | Install `watch-all` as a login service (systemd / launchd / Task Scheduler) |
| `watch-uninstall` | Remove the installed `watch-all` login service |
| `watch-status` | Print service state + per-repo reindex status (also exposed as MCP tool `get_watch_status`) |
| `hook-event create\|remove` | Record a worktree lifecycle event (called by Claude Code hooks) |
| `index [target]` | Index a local folder (default: `.`) or GitHub repo (`owner/repo`). One command, no init required. `--identity-mode config\|local\|git` (#1017) chooses how a local target is keyed, as `index_folder`'s `identity_mode` does; default `config`, refused for a GitHub target. A target that is an existing directory is local, even one spelled `owner/repo` or `github.com/owner/repo`; `https://github.com/owner/repo` forces the GitHub repo |
| `index-file <path>` | Re-index a single file within an existing indexed folder (used by PostToolUse hooks) |
| `import-scip <path.scip> [--repo <id>]` | (v1.108.96) Ingest a SCIP index file (compiler-verified cross-references from scip-typescript / scip-python / scip-java / scip-go / rust-analyzer; .gz accepted) into the scip_* tables. Hand-rolled protobuf reader, no deps. `find_references` then tags `compiler_verified` refs + appends compiler-only refs. Cap via `JCODEMUNCH_SCIP_MAX_ROWS`. |
| `config` | Print effective configuration grouped by concern |
| `config set <key> <value>` / `config unset <key>` | (v1.108.51) Write/clear a config key in the global config.jsonc (typed, comment-preserving, validated; `--json` for tooling) |
| `config --check` | Also validate prerequisites (storage writable, AI pkg installed, HTTP pkgs present) |
| `claude-md --refresh [--yes] [--dry-run] [--match-server]` | Replace an installed agent policy block that differs from this version's, in the global and the project CLAUDE.md. Prints the diff and asks before writing; keeps a `CLAUDE.md.jcodemunch-<time>.bak`; replaces only the block. `--generate --format policy` prints the current text without writing. `--match-server` writes this version's policy for the settings a server recorded when its notice last said a block differs, instead of this shell's, for a setting set only in the MCP client's environment |
| `config --upgrade` | Add missing keys from current template to existing config.jsonc, preserving user values |
| `download-model` | Download bundled ONNX embedding model (all-MiniLM-L6-v2) for zero-config semantic search; `--target-dir` override |
| `install-pack [id]` | Download and install a Starter Pack pre-built index; `--list` for catalog, `--license KEY` for premium |
| `hook-pretooluse` | PreToolUse hook: steer Read/Grep/Glob/leading-Bash-search toward jCodemunch inside indexed repos (reads JSON stdin) |
| `hook-posttooluse` | PostToolUse hook: auto-reindex files after Edit/Write (reads JSON stdin) |
| `hook-taskcomplete` | TaskCompleted hook: post-task diagnostics — dead code, untested symbols, dangling refs (reads JSON stdin) |
| `hook-post-checkout <old> <new> [flag]` | git post-checkout hook, written by `init --git-hook`: on a branch checkout (flag 1), re-index only the files `git diff --no-renames` lists between the two commits. Does nothing on a folder with no index, so it never builds a first index from a partial list; always exits 0 |
| `hook-subagent-start` | SubagentStart hook: inject condensed repo orientation for spawned agents (reads JSON stdin) |
| `whatsnew` | Refresh README recency block + write `whatsnew.json` from `CHANGELOG.md` (release flow) |
| `digest` | Agent stand-up briefing — composes since-last-session delta + risk surface + dead-code candidates; tracks per-repo last-seen SHA at `~/.code-index/digest_state/`; also exposed as MCP tool `digest`. v1.108.68 adds a one-line retrieval-regret summary when the ledger has clusters |
| `delivery` | (v1.108.69) Print durable-change delivery metrics for a window — `delivery [repo] [--window-days N] [--rework-horizon-days N] [--cost DOLLARS] [--json]`. Thin CLI over `get_delivery_metrics`; `--cost` prints the headline cost-per-durable-change (how much got done for how little). Read-only git archaeology |
| `parity` | (v1.108.111) Map migration parity between two symbol trees — `parity <source> <target> [--source-path P] [--target-path P] [--match-threshold F] [--divergence signature\|signature+body\|name_only] [--no-rename] [--no-port-plan] [--json]`. Thin CLI over `get_parity_map`: ported/diverged/unported/orphaned/added counts + dependency-ordered port plan. Read-only/plan-only |
| `health` | Print `get_repo_health` JSON to stdout (includes six-axis radar). For CI/scripting; `--radar-only` for just the radar sub-field. Used by the v1.88.0 health-radar GitHub Action |
| `file-risk` | Print per-symbol risk JSON for a file (composite score + four-axis breakdown). Used by the v0.2.0 VS Code risk-density gutter |
| `observatory build\|init` | Public OSS code-health observatory pipeline — clones, indexes, scores a configured repo list; writes static HTML + RSS + JSON to an output dir. v1.90.0; CI repo-id bug fixed in v1.90.1. Live at https://jgravelle.github.io/jcodemunch-observatory/ |
| `org-report` / `org-rollup` | (v1.108.38/39) Team SKU: record this seat's savings under its org / aggregate across seats. `org-rollup` is the licensed feature (v1.108.42 gate). |
| `license` | (v1.108.42) Check jCodeMunch license status — `license [--key KEY] [--json]`; reports licensed / evaluation / unlicensed, tier, trial days left. Gates `org-rollup` only. |
| `surface` | (v1.108.154) Print the tool-surface schema receipt (same block `get_session_stats` reports as `tool_surface`) — surface/profile, visible vs catalog counts, schema tokens, avoided, heaviest schemas. `--json` for tooling (the Console's Tool surface cost card shells it). Scans nothing. |

## Env Vars
| Var | Default | Purpose |
|-----|---------|---------|
| `CODE_INDEX_PATH` | `~/.code-index/` | Index storage location |
| `JCODEMUNCH_SKIP_MSBUILD_OUTPUT` | 1 | (v1.108.338, #1001) Prune `obj/`/`bin/` when a .NET project file (`.csproj`/`.vbproj`/`.fsproj`) sits BESIDE them (config key `skip_msbuild_output`). The project-file marker exists for `bin/`, which holds committed hand-written entrypoints in Node, Ruby and Go projects — a name-only rule would delete real source. ⚠ A solution file (`.sln`/`.slnx`) is NOT a marker: MSBuild writes output beside the project, and a solution root's `bin/` may be hand-written. Counted as `msbuild_output` in `discovery_skip_counts`, NOT a withheld reason, so absence stays citable. Only an explicit `false` disables it. ⚠ Routes: applied by the full walk, the watcher fast path and `refresh`; `index_file` does not apply it (a gap shared by every skip rule, and the next full walk removes what it added); `index_repo` gets `.vs/` but not this marker rule. |
| `JCODEMUNCH_SKIP_NUGET_PACKAGES` | 1 | (v1.108.338, #1001) Prune a `packages/` dir proven to be a NuGet restore tree by a child dir holding `<Id>.<Version>/<Id>.<Version>.nupkg` (config key `skip_nuget_packages`). The artifact marker is the design: `packages/` is hand-written source in Flutter, Dart and JS monorepos, so a name-only rule deletes real code, and loosening it to "child looks like `<Id>.<SemVer>`" keys on a naming convention rather than on evidence the writer left. Where a `.gitignore` already lists `packages` this changes which rule excluded them and not what is indexed; it moves the corpus only where no such entry exists. Two test-pinned false negatives, both erring toward indexing: a v2 tree with `.nupkg` files stripped, and the v3 global cache which nests one level deeper. The probe is bounded at 100 child dirs. Counted as `nuget_packages`, NOT withheld. Only an explicit `false` disables it. ⚠ Routes: applied by the full walk, the watcher fast path and `refresh`; `index_file` does not apply it (a gap shared by every skip rule, and the next full walk removes what it added); `index_repo` does not apply it. |
| `JCODEMUNCH_SKIP_VIRTUALENVS` | 1 | (L-206) Prune a Python virtualenv's installed packages whatever the venv is called: a `site-packages` directory at `Lib/site-packages` or `lib/pythonX.Y/site-packages` (also `lib64`, `pypyX.Y`) under a root whose `pyvenv.cfg` carries the `home =` key every venv tool writes (PEP 405) (config key `skip_virtualenvs`). `venv` and `.venv` were already skipped by name; this catches `env`, `jcm-venv`, `py313` and the rest. ⚠⚠ **Only `site-packages` is pruned**: the venv root, `bin/`, `Scripts/`, `lib/` and `include/` stay, because a venv created in a project root (`python -m venv .`) sits beside hand-written `bin/` and `lib/`, and a POSIX venv merges into an existing `lib/`; two review rounds found a wider rule dropping source. A file merely named `pyvenv.cfg` with no `home` key prunes nothing. Name-gated, so a directory not called `site-packages` costs a string compare. Counted as `virtualenv`, NOT withheld. Only an explicit `false` (or `0`/`no`/`off`) disables it, in the config file or the env var; a typo keeps it on with a warning (L-207). ⚠ Routes: applied by the full walk, the watcher fast path and `refresh`; explicit `paths=` and `index_file` still name any file; `index_repo` does not apply it. |
| `JCODEMUNCH_MAX_INDEX_FILES` | 10,000 | File cap for repo indexing |
| `JCODEMUNCH_MAX_FOLDER_FILES` | 2,000 | File cap for folder indexing |
| `JCODEMUNCH_FILE_TREE_MAX_FILES` | 500 | Cap for get_file_tree results |
| `JCODEMUNCH_GITIGNORE_WARN_THRESHOLD` | 500 | Missing-.gitignore warning threshold (0 = disable) |
| `JCODEMUNCH_USE_AI_SUMMARIES` | auto | AI summarization mode: `auto` (detect provider), `true` (use explicit config), `false`/`0`/`no`/`off` (disable) |
| `JCODEMUNCH_SUMMARIZER_PROVIDER` | — | Explicit summarizer provider: `anthropic`, `gemini`, `openai`, `minimax`, `glm`, `openrouter`, `none` |
| `JCODEMUNCH_SUMMARIZER_MODEL` | — | Model name override for the selected summarizer provider |
| `JCODEMUNCH_EXTRA_IGNORE_PATTERNS` | — | Always-on gitignore patterns (comma-sep or JSON array) |
| `JCODEMUNCH_PATH_MAP` | — | Cross-platform path remapping; format: `orig1=new1,orig2=new2` |
| `JCODEMUNCH_STALENESS_DAYS` | 7 | Days before get_repo_outline emits a staleness_warning |
| `JCODEMUNCH_MAX_RESULTS` | 500 | Hard cap on search_columns result count |
| `JCODEMUNCH_HTTP_TOKEN` | — | Bearer token for HTTP transport auth (opt-in) |
| `JCODEMUNCH_RATE_LIMIT` | 0 | Max requests/minute per client IP in HTTP transport (0 = disabled) |
| `JCODEMUNCH_REDACT_SOURCE_ROOT` | 0 | Set 1 to replace source_root with display_name in responses |
| `JCODEMUNCH_SHARE_SAVINGS` | 1 | Set 0 to disable anonymous token savings telemetry |
| `JCODEMUNCH_TOKENIZER_DOWNLOAD` | 1 | Set 0 to stop tiktoken fetching `cl100k_base.tiktoken` on first use (config key `tokenizer_download`); `tokens_saved` then uses the bytes/token table |
| `JCODEMUNCH_HOOK_GREP_ANSWER` | 0 | Set `1` to have the PreToolUse hook (installed by `init --guidance` or `--strict`) answer a Grep from the index. Off by default: on short bug-fix tasks it raised session cost without solving more of them. When on, a Grep inside an indexed repo whose match set is at or over `JCODEMUNCH_HOOK_GREP_ANSWER_MIN_LINES` or `JCODEMUNCH_HOOK_GREP_ANSWER_MIN_FILES` is refused once per session, search root and arguments, with every match counted as the reason: by file and enclosing symbol, by file alone when that does not fit, and past that the busiest files with the rest summed; the matching lines are not included. The answer opens by naming the index, the number of lines and files that match the pattern case-insensitively (a count from the index, not Grep's own output, which is case-sensitive unless told otherwise and lists files by default) and this setting as the off switch. The same Grep again runs. A Grep under both thresholds runs untouched. The answer covers only the files the index holds. It answers nothing (the Grep runs) when the index is one the file cap cut short, an indexed file changed since indexing, the search finds nothing, is cut short (including a match set that reaches the hook's scan cap) or takes over `JCODEMUNCH_HOOK_ANSWER_BUDGET_SECONDS`, or the Grep uses `type`, `multiline`, context lines, `head_limit`, `offset`, a count, a `**` glob, or a `path` below the repo together with a `glob`. |
| `JCODEMUNCH_HOOK_GREP_ANSWER_MIN_LINES` | 66 (measured) | With `JCODEMUNCH_HOOK_GREP_ANSWER=1`, the number of matching lines at or over which the hook answers a Grep. A Grep under this (and under `JCODEMUNCH_HOOK_GREP_ANSWER_MIN_FILES` when that is set) runs untouched. A value that is not a positive integer means the default. |
| `JCODEMUNCH_HOOK_GREP_ANSWER_MIN_FILES` | unset (off) | With `JCODEMUNCH_HOOK_GREP_ANSWER=1`, the number of matching files at or over which the hook answers a Grep. Unset by default, so the file count alone answers nothing: no file count met the measurement rule the line threshold was chosen by. A value that is not a positive integer means unset. |
| `JCODEMUNCH_HOOK_ANSWER_BUDGET_SECONDS` | 4 | Seconds the PreToolUse hook waits for its `search_text` answer to a Grep before giving up and letting the Grep run. A non-positive or unparseable value means the default. |
| `JCODEMUNCH_ENFORCE` | advisory | How the PreToolUse hook treats a native search or read inside an indexed repo. `advisory` suggests the jcm route and lets the call run, except a Grep the hook answers from the index when `JCODEMUNCH_HOOK_GREP_ANSWER=1`, which it refuses once with that answer; `strict` (set by `init --strict`) denies it; `off` says nothing. Under `strict` a targeted or small Read and a path outside every indexed repo still pass. A shell search is denied only as a single command (`grep -rn x src`): a pipeline or chained line (`grep ... \| head`, `&&`, `;`) and a path the hook cannot resolve get advice only, because the rest of the line is usually real work. `find` is never denied. An unknown value means `advisory`. |
| `JCODEMUNCH_REDACT_RESPONSE_SECRETS` | 1 | Set 0 to disable response-level secret redaction (AWS/GCP/Azure/JWT/etc.) |
| `JCODEMUNCH_STATS_FILE_INTERVAL` | 3 | Calls between session_stats.json writes; 0 = disable |
| `JCODEMUNCH_PERF_TELEMETRY_MAX_ROWS` | 100000 | Rolling cap on persisted perf rows; oldest rows trimmed in 1k-row batches once exceeded. |
| `JCODEMUNCH_RUNTIME_MAX_ROWS` | 100000 | (Phase 0) Per-repo cap on rows in runtime_* tables (ingested in Phase 1+); FIFO eviction in 1k batches once exceeded. |
| `JCODEMUNCH_CLIENT_ID` | basename(`sys.argv[0]`) | (v1.106.0) Friendly client name recorded in `process_locks` metadata. Auto-detected for common runtimes (claude, cursor, codex). Override for custom or wrapper runtimes so `get_watch_status.watcher_holder.client_id` surfaces a meaningful name to other processes. |
| `ANTHROPIC_API_KEY` | — | Enables Claude Haiku summaries (`pip install "jcodemunch-mcp[anthropic]"`) |
| `GOOGLE_API_KEY` | — | Enables Gemini Flash summaries (`pip install "jcodemunch-mcp[gemini]"`) |
| `OPENAI_API_BASE` | — | Local LLM endpoint (Ollama, LM Studio) |
| `OPENAI_WIRE_API` | — | Set `responses` to use OpenAI Responses API instead of chat/completions |
| `OPENROUTER_API_KEY` | — | Enables OpenRouter summaries (default model: `meta-llama/llama-3.3-70b-instruct:free`) |
| `JCODEMUNCH_LOCAL_EMBED_MODEL` | — | Override path to bundled ONNX model directory (default: `~/.code-index/models/all-MiniLM-L6-v2/`) |
| `JCODEMUNCH_LOCAL_EMBED_THREADS` | 2 | ONNX intra-op threads for the bundled local encoder, read when the cached session is created. Invalid/non-positive values or failed CPU probes fall back to 2. Positive requests are capped at detected CPU availability; affinity support depends on Python/platform support. Windows can fall back to the machine total; CPU-time quotas are not detected. |
| `GEMINI_EMBED_TASK_AWARE` | 1 | Set `0`/`false`/`no`/`off` to disable task-type hints (`RETRIEVAL_DOCUMENT` / `CODE_RETRIEVAL_QUERY`) when using Gemini embeddings |
| `JCODEMUNCH_CROSS_REPO_DEFAULT` | 0 | Set 1 to enable cross-repo traversal by default in find_importers, get_blast_radius, get_dependency_graph |
| `JCODEMUNCH_REPO_VISIBILITY` | workspace | Which indexes `list_repos` shows and an ambiguous bare repo name can pick: `workspace` (the server's working directory, inside it, or containing it; the answer says how many it hid) or `all`. Config key `repo_visibility`. |
| `JCODEMUNCH_EVENT_LOG` | — | Set `1` to write `_pulse.json` on every tool call (per-call activity signal for dashboards) |
| `JCODEMUNCH_PARSE_CACHE` | — | Shared directory for the content-addressed parse cache (v1.108.40). Point all seats on a multi-home-dir box at the same path so identical files parse once across seats. Unset = disabled (no caching). |
| `JCODEMUNCH_PARSE_CACHE_MAX_ROWS` | 50000 | (v1.108.41) Row cap for the shared parse cache; FIFO-trimmed oldest-first by rowid after each write (stale-content/stale-version rows go first). `<= 0` disables the cap (unbounded). |
| `JCODEMUNCH_PARENT_WATCH_SECONDS` | 5 | (BACKLOG row 64) How often a stdio server checks that the process that started it is still running; when it is gone the server exits, even if its stdin pipe is still open. On Windows the check looks past a pip console-script `.exe`, a venv's `python.exe` redirector and `uv`/`uvx`/`py`/`pyw`, which only start the server and wait. A parent that cannot be checked is never treated as gone. `0` or negative turns it off; a value that does not parse keeps the default. SSE and streamable-http are not affected. Not measured: whether `uv run`/`uvx` on Linux or macOS exec the server in place; if they wait on it instead, the watch sees `uv` and the server lingers as before. |
| `JCODEMUNCH_ORG_ID` | — | Org identifier for the team-SKU rollup (`org-report` / `org-rollup`) |
| `JCODEMUNCH_ORG_ENDPOINT` | — | Org host URL that `org-report` POSTs seat savings to (`/org/report`); unset = record locally |
| `JCODEMUNCH_ORG_INGEST_ENABLED` | 0 | Set 1 on the org host to accept `POST /org/report` (two-key turn with `JCODEMUNCH_HTTP_TOKEN`) |
| `JCODEMUNCH_LICENSE_NOTICE` | 1 | Set `0`/`false`/`no`/`off` to skip the one-time license line on the first tool answer of an install that never ran `init`, and the license line at the end of `receipt`. Also skipped under `CI` or `CLAUDE_CODE_REMOTE`, with a license key configured, after `jcodemunch-mcp config set license_reminder false`, or (the one-time line only) once the marker `license_notice_state.json` under `CODE_INDEX_PATH` exists |
| `JCODEMUNCH_LICENSE_REMINDER` | 1 | Set `0`/`false`/`no`/`off` to silence the work-license reminder (7 days after the first index, then at most every 30 days, on stderr after a hand-run CLI command and on the first tool answer of a server process), and the license line at the end of `receipt`. Also silenced by `jcodemunch-mcp config set license_reminder false`, `JCODEMUNCH_LICENSE_NOTICE=0`, a configured license key, or `CI`/`CLAUDE_CODE_REMOTE`. State: `license_reminder_state.json` under `CODE_INDEX_PATH` |
