# Troubleshooting

Common issues and their solutions.

## Hooks Silently Fail on macOS / Linux ("command not found: jcodemunch-mcp")

**Symptom:** You ran `jcodemunch-mcp init --hooks`, hooks appear in
`~/.claude/settings.json`, but they never fire — or you see
`/bin/sh: jcodemunch-mcp: command not found` in Claude Code logs.

**Cause:** Claude Code spawns hooks via `/bin/sh`, **not** your interactive
shell (zsh / bash). `/bin/sh` uses a minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`)
and does **not** inherit your shell's PATH additions. So if `jcodemunch-mcp`
lives in `~/.local/bin` (pip `--user`), `~/Library/Python/3.x/bin` (macOS
framework Python), or a pipx-managed venv, the subshell can't find it even
though `which jcodemunch-mcp` works fine in your terminal.

**Fix (v1.80.5+):** `init --hooks` now writes the resolved absolute path
automatically. Just re-run:

```bash
jcodemunch-mcp init --hooks
```

It detects the legacy bare-name entries and migrates them. The new commands
in `~/.claude/settings.json` will look like:

```json
{"type": "command", "command": "/Users/you/.local/bin/jcodemunch-mcp hook-posttooluse"}
```

**Manual fix (older versions):** Replace every `jcodemunch-mcp` in
`~/.claude/settings.json` hook commands with the output of `which jcodemunch-mcp`.
Quote the path if it contains spaces.

> Credit: reported by a customer running pip `--user` on macOS with zsh.

---

## "No source files found" / Empty Index

**Symptom:** `index_folder` reports "No source files found" or an unexpectedly
empty index. See the [empty-discovery contract](SPEC.md#expected-error-behaviors)
to distinguish an indexing failure from a successful deletion-only refresh.

**Possible causes:** No supported source files remain, all files matched an
exclusion (directory name, file extension, or `.gitignore` rule), or discovery
could not read them.

**Fix:**
1. Check response warnings and file/directory read permissions. Successful
   indexing responses include `discovery_skip_counts`, which breaks down how
   many files were skipped and why; the early "No source files found" error
   does not include these counters.
2. If a directory is being skipped that shouldn't be, check if its name
   matches a built-in skip pattern (node_modules, __pycache__, .git, etc.)
   or a pattern in `JCODEMUNCH_EXTRA_IGNORE_PATTERNS`.
3. Run with `extra_ignore_patterns=[]` to disable extra patterns and see
   if files appear.

---

## Ranking feels diluted after indexing a whole project directory

**Symptom:** You point jcodemunch at a project root rather than at the code
itself. Search quality drops, results are crowded by files you did not mean to
index, and some queries come back empty. Re-indexing the package or crate alone
fixes it.

**Cause — usually not the one people reach for.** jcodemunch has **no filetype
ignore list**, because it works the other way round: a **157-extension
allow-list across 77 languages** (`parser/languages.py`). Anything unrecognised
is already dropped, counted as `wrong_extension`. That means `.md`, `.rst`,
`.txt`, `.html`, `.ini`, `.cfg`, `.csv`, `.env` and every backup suffix
(`.bak`, `.old`, `.orig`, `.backup`) are **never indexed today**. Adding a
default ignore list for "non-source extensions" would therefore change very
little.

Two things do dilute, and neither is an extension problem:

1. **Config and data files with legitimate source extensions.** `.json`,
   `.yaml`, `.yml`, `.toml`, `.xml`, `.sql`, `.sh` are deliberately indexed —
   they carry manifests, CI definitions and framework wiring that
   `get_project_intel` and route detection rely on.
2. **Duplicate source trees.** A `copy/`, `backups/`, `archived/` or vendored
   directory holding real `.py` / `.go` / `.ts` files gets indexed alongside the
   originals, so the same symbols appear twice and compete with each other. This
   is a **ranking** problem wearing an extension problem's clothes, and it is the
   more common cause.

   As of **v1.108.234** the singular forms `backup`, `old` and `archive` are
   skipped by default. ⚠ **Plurals and variants are not** — `backups/`,
   `archives/`, `archived/`, `old_data/` and `copy/` are still indexed, so this
   remains the case to check first.

**Fix, most effective first:**

1. **Scope the index.** Point at the package, crate or module rather than the
   repository root, or pass `paths=["src", "lib"]` to `index_folder` to index
   only named subdirectories. Cheapest fix and usually sufficient.
2. **Exclude duplicate trees by path**, not by extension:

   ```bash
   export JCODEMUNCH_EXTRA_IGNORE_PATTERNS="backups/,archives/,archived/,**/vendor-copy/**"
   ```

   Gitignore-style, comma-separated or a JSON array. Per call, pass
   `extra_ignore_patterns=[...]` to `index_folder`.

   ⚠ **The reverse case.** If a directory named `backup`, `old` or `archive` is
   a real part of your project, v1.108.234 now skips it. Un-skip it with the
   `exclude_skip_directories` config key — that removes an entry from the
   built-in list, where `JCODEMUNCH_EXTRA_IGNORE_PATTERNS` only adds:

   ```jsonc
   { "exclude_skip_directories": ["archive"] }
   ```
3. **Read `discovery_skip_counts` in the index response.** It reports which rule
   dropped what — `extra_ignore`, `gitignore`, `wrong_extension`, `too_large`,
   `secret`, `binary`. If the count you expect is zero, the rule you assumed was
   filtering is not the one doing the work.
4. `.gitignore` is already honoured; a tree ignored by git is not indexed.

⚠ **`wrong_extension` answers two different questions and cannot tell them
apart:** "that file is not source code" (a scope decision) and "this
installation cannot parse that language" (a capability gap). Since
`tree-sitter-language-pack` 1.x stopped bundling grammars, what a given install
can parse depends on its grammar pack, not on the repository. If a language you
expected is missing rather than merely crowded out, check that first — see
`evidence/capability.py`.

---

## AI Summarization Not Working

**Symptom:** All symbols have generic "signature fallback" summaries instead
of natural-language descriptions.

**Cause:** AI summarization requires both an API key **and** the corresponding
optional package installed.

**Fix:**
1. For Claude summaries: `pip install "jcodemunch-mcp[anthropic]"` and set
   `ANTHROPIC_API_KEY`.
2. For Gemini summaries: `pip install "jcodemunch-mcp[gemini]"` and set
   `GOOGLE_API_KEY`.
3. For OpenAI-compatible endpoints: `pip install "jcodemunch-mcp[openai]"` and set
   `OPENAI_API_BASE` to your endpoint (e.g.,
   `http://127.0.0.1:11434/v1` for Ollama).
4. For MiniMax summaries: `pip install "jcodemunch-mcp[minimax]"`, set
   `MINIMAX_API_KEY`, and optionally force it with
   `JCODEMUNCH_SUMMARIZER_PROVIDER=minimax`. If MiniMax is reached through the
   hosted endpoint `https://api.minimax.io/v1`, also set
   `allow_remote_summarizer: true` in `config.jsonc`; otherwise jcodemunch
   rejects the non-localhost endpoint and falls back to signature summaries.
5. For GLM-5 summaries: `pip install "jcodemunch-mcp[zhipu]"`, set
   `ZHIPUAI_API_KEY`, and optionally force it with
   `JCODEMUNCH_SUMMARIZER_PROVIDER=glm`.
6. To verify: re-index and check the server logs for
   `"AI summarization failed, falling back to signature"` warnings.
7. To disable: set `JCODEMUNCH_USE_AI_SUMMARIES=0` or
   `JCODEMUNCH_SUMMARIZER_PROVIDER=none`.

---

## GitHub Rate Limit Errors (index_repo)

**Symptom:** `index_repo` fails with `403 Forbidden` or `429 Too Many Requests`.

**Cause:** GitHub's unauthenticated API limit is 60 requests/hour.

**Fix:**
1. Set `GITHUB_TOKEN` to a personal access token (no special scopes needed
   for public repos).
2. Authenticated requests get 5,000 requests/hour.
3. The server retries rate-limited requests with exponential backoff
   (up to 3 attempts).

---

## find_importers / find_references Return Empty Results

**Symptom:** `find_importers` or `find_references` returns `{"importers": []}`
even for files you know are imported.

**Cause:** The import graph is only built during indexing with jcodemunch v1.3.0+.
Indexes created by older versions don't have import data.

**Fix:** Re-index the repository:
```
index_folder(path="/your/project")
```
After re-indexing, `find_importers` and `find_references` will work.

---

## search_columns Returns "No column metadata found"

**Symptom:** `search_columns` returns an error about missing column metadata.

**Cause:** Column metadata is only extracted from dbt or SQLMesh projects that
have model YAML files with column definitions.

**Fix:**
1. Ensure your project has dbt `schema.yml` or SQLMesh model files with
   column definitions.
2. Re-index the project — the dbt/SQLMesh provider extracts column metadata
   during indexing.
3. Check that the index includes `context_metadata` with `dbt_columns` or
   `sqlmesh_columns` keys.

---

## Indexes Not Portable Between Machines

**Symptom:** An index created on one machine doesn't work on another.

**Cause:** Local indexes store `source_root` as an absolute path
(e.g., `/home/alice/projects/myapp`). File content is cached relative
to this path.

**Fix:** Re-index on the target machine. Indexes are designed to be
machine-local. For shared environments, use `index_repo` (remote GitHub
indexing) which doesn't depend on local paths.

---

## Windows: index_folder Hangs or Times Out

**Symptom:** `index_folder` never completes on Windows.

**Cause:** Two known issues (both fixed in v1.1.7):
1. Git subprocess inherits MCP stdin pipe, causing protocol corruption.
2. NTFS junctions (reparse points) cause infinite directory walks.

**Fix:**
1. Upgrade to jcodemunch-mcp >= 1.1.7.
2. If still stuck, check for circular NTFS junctions in your project
   directory tree.

---

## Windows: server drops mid-session, then `{"error":"Not connected"}` on every call

**Symptom:** On Windows, jCodeMunch connects and works for a burst of calls
(resolve/index/search), then every subsequent tool call in the same session
returns `{"error":"Not connected"}`. It can also fail right at the start during
a reindex. The client log shows a line like:

```
OPENSSL_Uplink(0000....,08): no OPENSSL_Applink
connection:transport_closed
```

(and often, separately, `invalid peer certificate: UnknownIssuer` when uvx
re-resolves dependencies).

**Cause:** `OPENSSL_Uplink: no OPENSSL_Applink` is a fatal Windows OpenSSL fault
that hard-aborts the process. It is a native abort, not a Python exception, so
jCodeMunch cannot catch it — once it fires the server is gone and every later
call hits a dead transport. It is an environment issue, not a jCodeMunch bug: a
second OpenSSL is loaded into the process (almost always a corporate
TLS-inspection / endpoint-security agent such as Zscaler, Netskope, or Cisco
Umbrella) and it aborts when an outbound HTTPS call routes through it. The
companion `UnknownIssuer` cert error on the same machine is the giveaway.
jCodeMunch's own TLS uses Python's bundled `_ssl`, which never needs applink;
the faulting library is the injected one.

**Fix:** Stop jCodeMunch from making the one background HTTPS call it makes
during a session — the opt-out savings telemetry. Set `JCODEMUNCH_SHARE_SAVINGS=0`
in the server's environment. In a Cursor / Claude Desktop MCP config:

```json
{
  "command": "uvx",
  "args": ["jcodemunch-mcp"],
  "env": { "JCODEMUNCH_SHARE_SAVINGS": "0" }
}
```

If you have an AI-summary provider key set, also add
`"JCODEMUNCH_USE_AI_SUMMARIES": "0"`. With no outbound call, the injected
OpenSSL is never exercised and the aborts stop.

Additional hardening on locked-down boxes:
1. Install into a venv and point your client at the resolved
   `jcodemunch-mcp.exe` instead of `uvx`, so each launch doesn't re-resolve
   dependencies over the failing TLS path. If you stay on `uvx`, add
   `--native-tls` so it uses the Windows trust store.
2. If aborts continue with telemetry disabled, the conflicting OpenSSL is being
   triggered by something else in your environment. Find the stray library with
   `where libcrypto-3-x64.dll` and `where libssl-3-x64.dll`, and confirm whether
   a security agent is doing TLS inspection.

---

## HTTP Transport "Connection Refused"

**Symptom:** `--transport sse` or `--transport streamable-http` fails with
`ImportError` or connection refused.

**Cause:** HTTP transport dependencies are optional.

**Fix:**
```bash
pip install "jcodemunch-mcp[http]"
```
Then restart with `--transport sse` or `--transport streamable-http`.

---

## HTTP Transport "401 Unauthorized"

**Symptom:** HTTP transport returns 401 for all requests.

**Cause:** `JCODEMUNCH_HTTP_TOKEN` is set, requiring bearer token auth.

**Fix:** Include the token in your MCP client's Authorization header:
```
Authorization: Bearer <your-JCODEMUNCH_HTTP_TOKEN-value>
```

---

## Retrieval feels slow

**Symptom:** Tool calls take longer than expected.

**Diagnose:** `analyze_perf { "window": "session" }` returns per-tool p50/p95/max latency from the in-memory ring (always tracked). For trend analysis across days, set `perf_telemetry_enabled: true` in `config.jsonc` and pass `window=1h|24h|7d|all`. The result includes `slowest_by_p95` and `cache.coldest_by_tool` to identify hot spots.

---

## Search confidence dropped without code changes

**Symptom:** `_meta.confidence` on `search_symbols` is suddenly low; agents report they can't find familiar code.

**Diagnose, in order:**
1. **Index drift** — check `_meta.freshness.repo_is_stale` on a recent search. `true` means the index SHA differs from the live `git rev-parse HEAD`. Re-run `index_folder`.
2. **Per-symbol staleness** — if individual results carry `_freshness: "edited_uncommitted"`, the file was edited since indexing. Either re-index or call `register_edit` on the changed paths.
3. **Embedding drift** (semantic mode) — if you use Gemini/OpenAI/sentence-transformers, the provider may have shifted weights silently. Run `check_embedding_drift`. If `alarm: true`, run `embed_repo(force=true)` and `check_embedding_drift(force=true)` to re-pin the canary.
4. **Tuned weights gone stale** — if `~/.code-index/tuning.jsonc` exists from a previous workload that no longer matches the codebase, delete the relevant repo entry or re-run `tune_weights`.

---

## Index Integrity Check Failed

**Symptom:** `load_index` returns None with a log warning about checksum mismatch.

**Cause:** The index file was modified outside of jcodemunch (hand-edited,
corrupted, or tampered with).

**Fix:** Re-index the repository. The checksum sidecar (`.json.sha256`) will
be regenerated automatically.
