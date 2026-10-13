## Code Exploration Policy

jCodemunch-MCP indexes this repository's symbols and how they relate. Use it where it is the cheaper route, and the built-in tools (Grep, Glob, Read) where they are.
- **Relationships are what the index is for:** who imports a symbol, where a name is used, what a change breaks, who calls a function, what is unreachable. The tools are listed under "Relationships & impact" below.
- **A name you already know, or a file you are about to work in:** the built-in search and `Read` are often as cheap. Take whichever is fewer steps.
- **On a small repository** the built-in tools are usually the cheaper route for finding and reading code.
- **A large repository, or a question with no known starting point:** the search and symbol tools below read less than opening whole files.
- The agent harness requires a `Read` before `Edit`/`Write` will succeed.

**Before the first jCodemunch call in a session** (skip it when the task needs none):
- `resolve_repo { "path": "." }` — confirm the project is indexed. If not: `index_folder { "path": "." }`
- `suggest_queries` — optional, when the repo is unfamiliar

**Finding code:**
- symbol by name → `search_symbols` (add `kind=`, `language=`, `file_pattern=`, `decorator=` to narrow)
- decorator-aware queries → `search_symbols(decorator="X")` to find symbols with a specific decorator (e.g. `@property`, `@route`); combine with set-difference to find symbols *lacking* a decorator (e.g. "which endpoints lack CSRF protection?")
- string, comment, config value → `search_text` (supports regex, `context_lines`)
- database columns (dbt/SQLMesh) → `search_columns`

**Reading code:**
- a file's shape without reading it → `get_file_outline`
- one or more symbols → `get_symbol_source` (single ID → flat object; array → batch)
- symbol + its imports → `get_context_bundle`
- specific line range only → `get_file_content` (last resort)

**Repo structure:**
- `get_repo_outline` → dirs, languages, symbol counts
- `get_file_tree` → file layout, filter with `path_prefix`

**Relationships & impact:**
- what imports this file → `find_importers`
- who imports this identifier → `find_references`
- where is this name used (imports + every content match) → `check_references`
- file dependency graph → `get_dependency_graph`
- what breaks if I change X → `get_blast_radius`
- who calls a function, and what it calls → `get_call_hierarchy`
- what symbols actually changed since last commit → `get_changed_symbols`
- find unreachable/dead code → `find_dead_code`
- class hierarchy → `get_class_hierarchy`

## Session-Aware Routing

**For a broad task, when you do not know where to start:**
1. `plan_turn { "repo": "...", "query": "your task description" }` — get confidence + recommended files. It is one call; skip it when you already know where to look.
2. Obey the confidence level:
   - `high` → go directly to recommended symbols, max 2 supplementary reads
   - `medium` → explore recommended files, max 5 supplementary reads
   - `low` → the feature likely doesn't exist. Report the gap to the user. Do NOT search further hoping to find it.
3. **One-call shortcut for a concrete task** — `assemble_task_context { "repo": "...", "task": "..." }` returns a single token-budgeted, source-attributed context capsule. It auto-classifies the task (explore / debug / refactor / extend / audit / review), auto-extracts anchor symbols, and runs the intent-appropriate sequence of the tools below end-to-end — so you get the whole context in one request instead of chaining the primitives by hand. Prefer it over a manual chain when the task is well-defined; fall back to step 1's routing when you need to decide *whether* the feature exists first.

**Interpreting search results:**
- `search_symbols` proves absence only when the scan is citable: `_meta.verdict.evidence_ref` holds an `absent:` token, or, where `_meta.verdict` is not shown, `_meta.absence_evidence.citable` is `true`. A `negative_evidence` of `no_implementation_found`, a `state` of `absent` or an empty result is not proof on its own. When absence is proven:
  - Do NOT re-search with different terms hoping to find it
  - Do NOT assume a related file (e.g. auth middleware) implements the missing feature (e.g. CSRF)
  - DO report: "No existing implementation found for X. This would need to be created."
  - DO check `related_existing` files — they show what's nearby, not what exists
  - Anything short of citable proves nothing (`absence_citable: false`, `citable: false`, or neither field present). Read the note or `absence_blocked_by`; if it names the index, re-index, then search again.
- If `verdict: "low_confidence_matches"`: examine the matches critically before assuming they implement the feature

**After editing files:**
- If PostToolUse hooks are installed (Claude Code only), edited files are auto-reindexed
- Otherwise, call `register_edit` with edited file paths to invalidate caches and keep the index fresh
- For bulk edits (5+ files), always use `register_edit` with all paths to batch-invalidate

**Token efficiency:**
- If `_meta` contains `budget_warning`: stop exploring and work with what you have. Results are never silently shortened — the warning is advisory, and what you got is complete
- Use `get_session_context` to check what you've already read — avoid re-reading the same files

## Model-Driven Tool Tiering

Your jcodemunch-mcp server can narrow the exposed tool list to suit the model you are running as. It is off unless `adaptive_tiering` is enabled in the server's config, and while it is off a model id changes nothing. Where it is on, pass your model id, exactly as your runner prints it, as `model="<your-model-id>"` on a `plan_turn` call you are already making, or call `announce_model(model="...")` once.

The server reads the model's family from the id (Opus, Sonnet, Haiku, GPT, o1, Llama), so any current id works and none needs listing here.
