# Security Controls

jcodemunch-mcp indexes source code from local folders and GitHub repositories. This document describes the security controls that protect against common risks when handling arbitrary codebases.

---

## Path Traversal Prevention

All user-supplied paths are validated before any file is read or written.

* **`validate_path(root, target)`** resolves both paths to absolute form and verifies the target is a descendant of `root` using `os.path.commonpath()`.
* Applied during file discovery and again before each file read (defense in depth).
* Paths such as `../../etc/passwd` or absolute paths outside the repository root are rejected.

---

## Symlink Escape Protection

Symlinks can be used to escape the repository root and read arbitrary files.

* **Default:** `follow_symlinks=False` — symlinks are skipped during file discovery.
* When symlinks are followed (`follow_symlinks=True`), each symlink target is resolved and validated against the repository root. Escaping symlinks are skipped with a warning.
* **`is_symlink_escape(root, path)`** checks whether a symlink resolves outside the root.
* On Windows, environments without symlink support automatically skip symlink traversal.

---

## Default Ignore Policy

Files are filtered through multiple layers:

1. **SKIP_PATTERNS** — directories and files always excluded (e.g., `node_modules/`, `vendor/`, `.git/`, `build/`, `dist/`, generated files, lock files).
2. **`.gitignore`** — respected by default for both local folders and GitHub repositories (via the `pathspec` library).
3. **`extra_ignore_patterns`** — user-configurable additional gitignore-style patterns passed to indexing tools.

---

## Secret Exclusion

Credential files are excluded during indexing by a structured classifier
(`secret_classifier.classify_secret_file`, behind the `security.is_secret_file`
boolean). It decides from the **filename and directory shape only** — it never
reads file contents. Detection is organized into groups, in precedence order:

* **Exact credential names:** `.env`, `.env.*`, `.htpasswd`, `.netrc`, `.npmrc`,
  `.pypirc`, `credentials.json`, `service-account*.json`, `client_secret*.json`,
  `application_default_credentials.json`, `*-firebase-adminsdk-*.json`,
  `token.json`, `*.token`, `*.credentials`, `*.secrets`, `*.agekey`, and SSH
  private keys (`id_rsa*`, `id_ed25519*`, `id_dsa*`, `id_ecdsa*`).
* **Credential extensions:** `*.pem`, `*.key`, `*.p8`, `*.p12`, `*.pfx`, `*.ppk`,
  `*.jks`, `*.keystore`.
* **Path-specific credentials:** `~/.aws/credentials`, `~/.kube/config`,
  `~/.docker/config.json`, `~/.config/gcloud/application_default_credentials.json`,
  `~/.azure/accessTokens.json`, `~/.cargo/credentials[.toml]`, `~/.gem/credentials`,
  `composer/auth.json` — credential stores that only a path (not a basename) names.
* **Key-material directories:** a private-key/keystore file under `keys/`,
  `certs/`, `ssl/`, `tls/`, `pki/`, … (private-key extensions only — **public**
  certs `*.crt`/`*.cer`/`*.der` are not excluded just for living here).
* **Secret-store data:** data/config files (`*.yaml`, `*.json`, `*.tfvars`,
  `*.tfstate[.backup]`, …) under a whole-segment `secret`/`secrets`/`credential`/
  `credentials`/`creds`/`vault` directory.
* **Broad `secret` basename:** a `secret`/`secrets` token at a **word boundary**
  (so `prod-secrets.yaml` is excluded but `secretariat.csv` is not), excluding
  source-code and documentation files (`secret_redaction.py` is code that
  *handles* secrets, not a credential file).

**Not excluded:** public SSH keys (`*.pub`), public certificates, template /
example fixtures (`*.example`, `*.sample`, `*.template`, `*.tmpl`, `*.dist`).

**Overrides** (`exclude_secret_patterns` config key): a group slug (e.g.
`key_material_directories`) disables that whole group; the legacy `*secret*`
token disables the broad-basename and secret-store groups; any other glob is a
per-pattern allow (a matching file is never treated as secret).

When a secret file is detected, a warning is included in the indexing response.
Secret files are never stored in the index or cached content directory.

---

## Response-Level Secret Redaction

Secret *exclusion* above keeps credential **files** out of the index. Secret
*redaction* is the second, independent control: before any tool response leaves
the server, it is swept for credential-shaped strings and matches are masked, so
a secret that reached the index some other way does not reach the model's
context window.

It runs in the central `call_tool` dispatcher, so it covers every tool by
construction rather than by each tool remembering. Patterns cover AWS access and
secret keys, GCP service-account keys, Azure storage keys and client secrets,
JWTs, bearer tokens, GitHub tokens and fine-grained PATs, Anthropic, OpenAI and
Slack keys, PEM private keys, generic `api_key`-shaped assignments, and private
IPv4 addresses.

**Default: enabled.** Set `JCODEMUNCH_REDACT_RESPONSE_SECRETS=0` to disable it.

### Three tools are exempt, deliberately

`get_file_content`, `get_symbol_source` and `get_context_bundle` are **skipped**
(`server.py`, `_SOURCE_DUMP_TOOLS`). These are the tools whose job is to return
raw cached source, and the exemption is a considered tradeoff on two grounds: a
per-byte regex sweep over payloads that can run to hundreds of KB is latency
spent for no gain, and anything those tools return is the user's own checked-in
code being read back to them, not a credential crossing a boundary it had not
already crossed.

⚠ **The consequence, stated plainly, because it is the gap a reader needs:** a
credential hardcoded inside an ordinary source file is caught by neither control.
Not by the filename classifier, because the filename is ordinary; not by the
redactor, because those three paths are exempt. If that matters in your
environment, the mitigations are the ones you would use anyway — secret scanning
in CI, and pre-commit hooks — because the credential is in your repository
regardless of what this server does with it.

This exemption is a performance and scope decision, not a claim that source
files never contain secrets.

## File Size Limits

* **Default maximum:** 500 KB per file (configurable via `max_file_size` in config, the `JCODEMUNCH_MAX_FILE_SIZE` environment variable, or the `max_size` argument on a single `index_folder` / `index_repo` call).
* Files exceeding the limit are skipped during discovery, and the indexing response names them in `warnings`. A skipped file is treated as **withheld**, not excluded: coverage reports `complete: false` and absence claims over the corpus are refused, because "we never read that file" and "that symbol does not exist" are different answers.
* A configurable **file count limit** (default: 500 files) prevents runaway indexing of extremely large repositories. Can be overridden using the `JCODEMUNCH_MAX_INDEX_FILES` environment variable.

---

## Cache Directories

* jCodeMunch honours the [Cache Directory Tagging Specification](https://bford.info/cachedir/). A directory containing a `CACHEDIR.TAG` file whose first 43 bytes are `Signature: 8a477f597d28d172789f06886806bc55` is pruned from the walk along with everything beneath it.
* **The signature is verified.** A file merely named `CACHEDIR.TAG` excludes nothing.
* This is the only exclusion rule declared by the *writer* of a directory rather than listed by us, so a tool that writes derived data into your tree is honoured without jCodeMunch knowing its name, and it applies to cache directories that are not dot-directories.
* Pruned directories are counted as `cache_dir` in `discovery_skip_counts`. This is an ordinary exclusion, not a withholding: a tagged directory is regenerable derived data by its own declaration, so absence claims over the remaining corpus stay citable.
* Disable with `respect_cachedir_tag: false` or `JCODEMUNCH_RESPECT_CACHEDIR_TAG=0` if you tag a directory you nonetheless want indexed.
* Applies to local folder indexing. GitHub repository indexing does not honour the tag, because validating the signature requires the file's content and the tree listing carries only paths and sizes.

---

## Binary File Detection

Binary files are excluded using a two-stage check:

1. **Extension-based detection** — common binary extensions (`.exe`, `.dll`, `.so`, `.png`, `.jpg`, `.zip`, `.wasm`, `.pyc`, `.class`, `.pdf`, `.db`, `.sqlite`, etc.).
2. **Content-based detection** — files containing null bytes within the first 8 KB are treated as binary and skipped, even if the extension suggests source code.

---

## Encoding Safety

* All file reads use `errors="replace"` to substitute invalid UTF-8 bytes with the Unicode replacement character (U+FFFD) instead of raising decode errors.
* Symbol content retrieval also uses `errors="replace"` to ensure safe decoding.
* Cached raw files are stored using UTF-8 encoding.

---

## Storage Safety

* Index storage defaults to `~/.code-index/`.
* The storage path can be overridden using the `CODE_INDEX_PATH` environment variable.
* Repository identifiers are derived from `{owner}-{name}`, preventing path injection in storage locations.
* Index files are stored as JSON and validated during load to ensure schema integrity.

---

## Release artifact signing

GitHub release artifacts (wheel + sdist) are signed with
[sigstore-python](https://github.com/sigstore/sigstore-python) via a
GitHub Actions workflow (`.github/workflows/sign-release.yml`) triggered
on `release.published`. The workflow uses GitHub's OIDC identity as the
signer, so verification ties an artifact back to the specific workflow
in this repository that signed it — no long-lived signing keys, no
external trust roots beyond the Sigstore public-good infrastructure.

**Verifying a release** (Sigstore v3 bundle format, `.sigstore.json`):

```bash
TAG=v1.108.22  # or whichever release you want to verify
WHEEL=jcodemunch_mcp-${TAG#v}-py3-none-any.whl
BASE="https://github.com/jgravelle/jcodemunch-mcp/releases/download/${TAG}"

curl -L -o "${WHEEL}" "${BASE}/${WHEEL}"
curl -L -o "${WHEEL}.sigstore.json" "${BASE}/${WHEEL}.sigstore.json"

python -m pip install sigstore
python -m sigstore verify github \
    --bundle "${WHEEL}.sigstore.json" \
    --repository jgravelle/jcodemunch-mcp \
    --workflow-name "Sign release artifacts" \
    "${WHEEL}"
```

The trust shape is the same one PyPI's PEP 740 attestation pipeline uses:
the workflow runs in GitHub Actions, presents an OIDC identity claim to
Sigstore's transparency log, and the signature is recoverable from the
log via the bundle. Forward-only — releases prior to the signing
workflow's introduction don't carry signatures and aren't going to be
retroactively resigned.

---

## Files this server treats as security-sensitive

The following user-writable files participate in the server's trust chain. A
process that can write any of them can influence the behavior of every
subsequent MCP session: prompt context the agent sees, tool descriptions, hook
commands, and which MCP server gets launched. Endpoint-management teams and
hardened install templates should treat them with the same care as any other
piece of developer configuration that steers an AI agent.

* `~/.code-index/config.jsonc` — global server configuration. Settings here
  influence tool tier visibility, language gating, secret-pattern lists, and
  per-tool description overrides.
* `~/.code-index/` and everything under it — the symbol index, the
  optional telemetry SQLite, the bundled-encoder model directory, and the
  serialized session journal. Bodies cached here are a second copy of every
  indexed source file.
* `./.jcodemunch.jsonc` (per-project) — same key shape as the global
  config, scoped to the directory it lives in. Overrides only those keys
  it sets.
* `~/.claude/CLAUDE.md`, `./CLAUDE.md`, `AGENTS.md`,
  `.cursor/rules/jcodemunch.mdc`, `.windsurfrules` — agent-policy files
  that `jcodemunch-mcp init` may write or modify, with consent. Each is
  rendered into the agent's prompt at session start by the corresponding
  client.
* `~/.claude/settings.json` (PreToolUse / PostToolUse / PreCompact /
  TaskCompleted / SubagentStart / WorktreeCreate / WorktreeRemove hooks)
  — `init` registers hook commands here so Claude Code auto-reindexes
  after edits and surfaces session diagnostics. The hook commands run
  every relevant tool call in the host agent.
* `.github/hooks/hooks.json` — analogous hook surface for GitHub Copilot
  CLI / cloud agent flows.
* Generated MCP client config files (paths depend on which clients are
  installed): `~/Library/Application Support/Claude/claude_desktop_config.json`
  (macOS Claude Desktop), `%APPDATA%\Claude\claude_desktop_config.json`
  (Windows Claude Desktop), `~/.cursor/mcp.json`, `~/.continue/config.json`,
  and the project-scope `.mcp.json` written by `claude mcp add`. Each
  contains the command line Claude / Cursor / Continue spawn to launch
  the MCP server.

File-integrity monitoring at the endpoint level (SentinelOne, Tanium, etc.)
applied to these paths is a reasonable defense-in-depth control in any
managed-endpoint deployment.

---

## Persistent processes installed by `watch-install`

`jcodemunch-mcp watch-install` registers a login-time service that watches
indexed directories for filesystem changes and reindexes incrementally. This
is opt-in and reversible (`watch-uninstall`) but appears in endpoint hunts
that enumerate startup items, so document it as expected when the service is
present:

* **Linux (systemd user units):** `~/.config/systemd/user/jcodemunch-watch.service`.
  Enabled with `systemctl --user enable --now jcodemunch-watch.service`.
* **macOS (launchd LaunchAgent):**
  `~/Library/LaunchAgents/us.gravelle.jcodemunch-watch.plist`. Loaded with
  `launchctl bootstrap gui/$UID <plist>`.
* **Windows (Task Scheduler entry):** task named `jcodemunch-watch` under
  the current user, configured to run at logon.

The service runs `jcodemunch-mcp watch-all`, which performs no network I/O
and only writes back to the per-repo SQLite stores under `~/.code-index/`.

---

## Cache integrity verification modes

`get_symbol_source(verify=True)` hashes the retrieved source and compares
against the content hash stored in the index. Both values are derived from
the local cache directory, so the default verification is self-referential:
a coherent tamper of `~/.code-index/<repo>/` is durably trusted after
the tamper. Treat the cache directory accordingly — see the security-sensitive
files section above for why it's worth file-integrity monitoring.

Externally-attested verification is available via the
`verify_against="git_sha"` parameter on `get_symbol_source`: when set, the
cached source is compared against the working-tree git HEAD slice of the
same file, not against the cache's own stored hash. The response includes
a `git_sha_verification` field with one of:

- `git_sha_match` — the cached source matches the HEAD slice.
- `git_sha_mismatch` — the file exists in HEAD but the slice differs.
- `git_unavailable` — the file isn't in HEAD, git is unreachable, or the
  source isn't a git working tree.

Default remains `verify_against="cache"` for back-compat. For
managed-endpoint or supply-chain-conscious deployments where cache
integrity matters, the `git_sha` mode is the externally-attested signal;
the `cache` mode alone is best read as "the cache is internally
consistent," not "the cache matches the upstream source."

---

## Telemetry Data Locality

The performance and ranking telemetry introduced in v1.74.0–v1.80.0 is
**local-only** and **opt-in**:

* `~/.code-index/telemetry.db` (`tool_calls`, `ranking_events`) is written
  only when `perf_telemetry_enabled: true` (or `JCODEMUNCH_PERF_TELEMETRY=1`).
  Default is **disabled** — the in-memory latency ring is always tracked
  but no row touches disk.
* `~/.code-index/tuning.jsonc` (per-repo retrieval-weight overrides) is
  written only by an explicit `tune_weights` invocation.
* `~/.code-index/embed_canary.json` (16-string drift canary) is written
  only by an explicit `check_embedding_drift(capture=true)` invocation.
* No telemetry is sent over the network. The community token-savings
  counter (`share_savings`) is unrelated and sends exactly three fields:
  an integer delta, an integer lifetime total, and an anonymous UUID —
  never query strings, paths, repo names, or any configuration value.
  Disable with `JCODEMUNCH_SHARE_SAVINGS=0`.
* Stored ranking events include the **literal query string** (truncated
  result-id list, no source code). Treat the storage path with the same
  care as any local source you index.

---

## Summary of Controls

| Control                   | Location                       | Default                     |
| ------------------------- | ------------------------------ | --------------------------- |
| Path traversal validation | `security.validate_path()`     | Always enabled              |
| Symlink escape protection | `security.is_symlink_escape()` | Symlinks skipped by default |
| Secret file exclusion     | `security.is_secret_file()`    | Always enabled              |
| Response secret redaction | `redact.redact_dict()` in the `call_tool` dispatcher | Enabled; `JCODEMUNCH_REDACT_RESPONSE_SECRETS=0` disables. **Exempt:** `get_file_content`, `get_symbol_source`, `get_context_bundle` |
| Binary file detection     | `security.is_binary_file()`    | Always enabled              |
| File size limit           | File discovery pipeline        | 500 KB                      |
| File count limit          | File discovery pipeline        | 500 files                   |
| `.gitignore` respect      | Indexing pipeline              | Enabled                     |
| UTF-8 safe decode         | All file reads                 | `errors="replace"`          |
| Perf telemetry sink       | `perf_telemetry_enabled`       | **Disabled** (opt-in)       |
| Ranking ledger storage    | `perf_telemetry_enabled`       | **Disabled** (opt-in)       |
| Tuning overrides          | Explicit `tune_weights` call   | None until invoked          |
| Embedding canary          | Explicit `check_embedding_drift` call | None until invoked   |

---

## Background behavior, fully disclosed

Everything jCodeMunch does beyond answering a tool call is listed here. All of it is visible, opt-in or opt-out, and reversible.

- **File watching.** The `watch` / `watch-all` / `watch-claude` commands (and `watch: true` in config) re-index files when they change. Watching runs **inside a process you started** and stops when that process exits. Nothing monitors your filesystem unless a jCodeMunch process you launched is running.
- **Login service — explicit opt-in only.** `jcodemunch-mcp watch-install` registers `watch-all` as a login service (Windows Task Scheduler / macOS launchd / Linux systemd) so indexes stay fresh across reboots. This happens **only** when you run `watch-install` yourself; `init`, `install`, and normal server use never register a service. Inspect it with `watch-status`; remove it with `watch-uninstall`.
- **Anonymous savings telemetry.** The server periodically sends a random anonymous ID plus aggregate token-savings counters to the project's public community meter. No code, no file paths, no repo names, no PII — counters only. The sender is a single background daemon thread that starts lazily on the first share (never at import, and never if you have opted out), so a plain import has no background side effect. Opt out with `share_savings: false` in `config.jsonc` or `JCODEMUNCH_SHARE_SAVINGS=0`; redirect the endpoint with `JCODEMUNCH_TELEMETRY_URL`.
- **Startup import of the local embedding backend.** When a native embedding provider is configured (the bundled ONNX encoder, or a sentence-transformers model), the server imports that library at startup, on the main thread, before it begins serving. That adds a few seconds to launch and is not optional polish: importing it later, on the worker thread a tool call runs in, deadlocks on the Windows loader lock and hangs the call forever. Nothing is downloaded and no model is loaded — the import alone is what matters, and no network is touched. Opt out with `JCODEMUNCH_EAGER_EMBED_IMPORT=0`.
- **In-process embedding cache.** After a semantic search reads a repository's stored vectors out of `~/.code-index/`, the decoded matrix stays in that server process's memory so the next query doesn't re-read and re-decode the whole thing (roughly 46 MB for a 30,000-symbol index). At most 2 repositories are held at a time, it is dropped when the index is written, and it dies with the process — nothing is written anywhere and no network is touched. Turn retention off with `JCODEMUNCH_EMBED_MATRIX_CACHE=0`.
- **Agent hooks.** `init` / `install` can write hook entries (auto-reindex on edit, read-interception nudges) into your MCP client's settings. They're offered during the interactive flow, shown before writing, and fully removed by `uninstall`.
- **Local index storage.** Indexes live at `~/.code-index/` (override with `CODE_INDEX_PATH`). Delete the directory and every trace of indexing is gone.
- **Live session journal.** While the server runs, it periodically writes a small `_session_live.json` in `~/.code-index/` recording the files and searches the agent touched this session (paths and query strings only, no file contents). It exists so the out-of-process PreCompact hook can restore session orientation after context compaction. Throttled, atomically written, overwritten in place; disable with `JCODEMUNCH_LIVE_JOURNAL=0`.
- **Process presence file.** While a server runs, it writes one small JSON file at `~/.code-index/_processes/<pid>.json` recording its own PID, transport, version, start time, and the launching client's name — nothing about your code, repos, or queries. It exists so `get_session_stats` can tell you how many jCodeMunch servers are sharing one index store: some MCP clients don't reap stdio servers at session end, and they accumulate invisibly (one user found 25+ holding ~17 GB between them). The file is removed on exit, and any reader prunes entries whose process is no longer alive, so a hard kill leaves nothing behind. No daemon, no timer, no network.
- **Transcript root registry.** The server (at startup) and the hooks (when one fires) append the directory Claude Code writes this session's transcripts to — `$CLAUDE_CONFIG_DIR/projects`, or the grandparent of the hook payload's `transcript_path` — to a small JSON list at `~/.code-index/_transcript_roots.json`. **Directory paths only**: no transcript contents, no repo paths, no queries, no file contents, and nothing about your code. It exists so `jcodemunch-mcp receipt` can count the sessions you actually ran: it used to scan a hardcoded `~/.claude/projects`, so anyone running a second `CLAUDE_CONFIG_DIR` profile saw a ledger missing most of their calls (one user measured 12 of 348). No daemon, no timer, no network. `receipt --roots` prints exactly what is registered; delete the file to clear it, and delete `~/.code-index/` to erase everything.
- **Local performance ledger — off by default.** With `perf_telemetry_enabled: true` in `config.jsonc`, the server records tool latencies, ranking events (query strings and returned symbol ids), and one per-session row of delivery counts (`session_yield`: how many symbols were delivered, how many of those were the same symbol twice, and an estimated token count for the repeats) into `~/.code-index/telemetry.db`. It is what `analyze_perf`, `suggest_corrections`, and the weight tuner read. **This database never leaves your machine** — the anonymous savings meter above sends aggregate counters only and never reads this file. Off unless you turn it on; delete the file to erase it.
- **User-invoked network calls.** A few commands you run explicitly reach the network. None run in the background or fire on a plain import; each happens only when you invoke the command:
  - **License validation.** `license`, `org-rollup`, and `install-pack --license` send your license key to `validate.php` on `j.gravelle.us` to confirm it. The key travels in the request body / a header, never the URL, so it can't land in intermediary access logs. This gates only the team `org-rollup` feature; the individual tools never call it.
  - **Starter-pack download.** `install-pack` fetches the pack catalog and any pre-built index pack you request from `jcodemunch.com` (a premium pack also sends your license key). Each pack indexes third-party open-source repositories and carries their license and attribution files verbatim under `licenses/<owner>-<name>/` in your index directory; `install-pack` prints the terms and the path, and `install-pack --list` names them before you download.
  - **Embedding-model download.** `download-model` — and the first semantic encode when the `[local-embed]` extra is installed — downloads the ONNX model (`all-MiniLM-L6-v2`, ~23 MB, one time) from `huggingface.co`; after that, semantic search needs no network.
  - **Team savings report.** `org-report` (team SKU) sends **only** `org_id`, `seat_id`, `tokens_saved`, `usd`, `calls`, and a date. No code, no file paths, no queries, no repo names. It goes to a host **you** choose, on your own network, never to a jMunch server. With no `--endpoint` or `JCODEMUNCH_ORG_ENDPOINT` set it writes to a local file (`org_savings.db`) and nothing leaves the machine at all. ⚠ **`seat_id` defaults to your machine's hostname**, which often contains a person's name: set `JCODEMUNCH_CLIENT_ID` to send an identifier of your choosing instead. It runs only when you invoke it. There is no scheduler and no background reporting.

- **Accepting reports from other machines. Off by default, behind explicit gates.** jCodeMunch has **four** routes that accept writes from another computer. All four are off in a default install, all four require the HTTP transport to be running at all (`serve --transport streamable-http` or `--transport sse`), and all four require the request to carry your `JCODEMUNCH_HTTP_TOKEN` bearer.

  - **`POST /org/report`** — one machine acting as the "org host" that collects the seat reports above. Gated a third way by `org_ingest_enabled`, which defaults to **false** (`JCODEMUNCH_ORG_INGEST_ENABLED=1`). It stores exactly the six fields listed above, in `org_savings.db` on that host.
  - **`POST /runtime/otel`**, **`POST /runtime/sql`**, **`POST /runtime/stack`** — live ingestion of runtime traces, the same data `import-trace` accepts from a file. Gated a third way by `runtime_ingest_enabled`, which defaults to **false** (`JCODEMUNCH_RUNTIME_INGEST_ENABLED=1`). Bodies are capped (`JCODEMUNCH_RUNTIME_INGEST_MAX_BODY_BYTES`, 5 MB default, checked before *and* after decompression as a gzip-bomb guard) and PII is redacted at the ingest chokepoint unless you turn that off.

  With the token unset these routes return **503 rather than running unauthenticated** — a missing token disables the endpoint instead of opening it. A default install accepts nothing, listens for nothing, and needs no action from you to keep it that way.

  ⚠ Until v1.108.274 this list named `POST /org/report` and described it as the sole remote-write route. That was true when written and was not revisited when the runtime-ingest routes were added. Reported by [@elfrost](https://github.com/elfrost) ([#449](https://github.com/jgravelle/jcodemunch-mcp/issues/449)). The count is now pinned by `tests/test_security_disclosure.py`, because the promise this section makes is that the enumeration is *complete*, and a sentence cannot keep that promise on its own.

Beyond the user-invoked calls listed above, the base package makes no other network calls and leaves no other persistent processes. AI-summary extras call their configured provider's API only when you enable them — see the [extras matrix below](#optional-extras--system-surfaces-each-pulls-in).

---

## Optional extras — system surfaces each pulls in

Most extras are pure-Python and self-contained. A few pull libraries that touch
system surfaces worth noting for managed-endpoint and SOC 2 / HIPAA-adjacent
deployments. For the base package alone, none of these surfaces are introduced.

| Extra | Transitive dependencies of note | System surfaces |
|---|---|---|
| (base, no extra) | none | none |
| `[local-embed]` | `onnxruntime` | local CPU inference (no network after model download); model fetched on first run |
| `[anthropic]` | `anthropic` SDK | outbound HTTPS to `api.anthropic.com` when AI summaries are enabled |
| `[gemini]` | `google-generativeai` | outbound HTTPS to Google AI endpoints when AI summaries are enabled |
| `[openai]` | `openai` SDK | outbound HTTPS to `api.openai.com` (or `OPENAI_API_BASE`) when AI summaries are enabled |
| `[groq]` | `openai` SDK | outbound HTTPS to Groq endpoints; used by the `gcm` CLI and speedreview Action |
| `[groq-voice]` | `sounddevice`, `numpy` | **microphone access** — `sounddevice.InputStream` opens the system audio device when the voice path is invoked |
| `[groq-explain]` | `Pillow` | image decode / re-encode of attached screenshots |
| `[all]` | union of all the above | union of all surfaces above, including microphone (`[groq-voice]`) and image libraries (`[groq-explain]`) |

For managed-endpoint deployments where microphone access on developer machines
is policy-restricted (HIPAA, SOC 2, finance), pin to the base package or to the
specific provider extras you need. The voice and explain paths are opt-in
features, not part of the core MCP server functionality, and `[all]` is the
only extra that bundles them together.

