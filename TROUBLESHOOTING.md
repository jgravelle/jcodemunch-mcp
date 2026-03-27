# Troubleshooting

Common issues and their solutions.

## "No source files found" / Empty Index

**Symptom:** `index_folder` completes but reports 0 files indexed.

**Cause:** All files matched a skip pattern (directory name, file extension,
or `.gitignore` rule).

**Fix:**
1. Check `discovery_skip_counts` in the response — it breaks down how many
   files were skipped and why (binary extension, secret filter, gitignore, etc.).
2. If a directory is being skipped that shouldn't be, check if its name
   matches a built-in skip pattern (node_modules, __pycache__, .git, etc.)
   or a pattern in `JCODEMUNCH_EXTRA_IGNORE_PATTERNS`.
3. Run with `extra_ignore_patterns=[]` to disable extra patterns and see
   if files appear.

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
   `JCODEMUNCH_SUMMARIZER_PROVIDER=minimax`.
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

## Index Integrity Check Failed

**Symptom:** `load_index` returns None with a log warning about checksum mismatch.

**Cause:** The index file was modified outside of jcodemunch (hand-edited,
corrupted, or tampered with).

**Fix:** Re-index the repository. The checksum sidecar (`.json.sha256`) will
be regenerated automatically.
