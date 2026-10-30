---
name: jcodemunch
description: When to use jCodemunch MCP tools for symbol-level code navigation and when the built-in tools are the cheaper route. Loads on demand; complements the always-on policy block.
---

# jCodemunch Code Exploration Skill

Load this skill when navigating code, investigating dependencies, planning refactors, or assembling task context. The jcodemunch MCP server provides symbol-aware retrieval and graph relationships that regex and full-file reads cannot reproduce.

## When to load this skill

- Opening a session in an unfamiliar repo
- Tracing what depends on a symbol before changing it
- Finding similar implementations to consolidate
- Producing a context capsule for a multi-step task
- Auditing dead code, churn hotspots, or architectural health

## When NOT to load this skill

- You already know the exact file and line — just `Read` it.
- The repository is small: the built-in tools (Grep, Glob, Read) are   usually the cheaper route for finding and reading code on a small repository.
- The task is purely conversational with no code context.
- The repo is unindexed and you don't intend to index it.

## Tool taxonomy

The policy block already enumerates the canonical decision tree. What follows is procedural advice for *how* to use those tools, not a duplicate listing.

### Before the first jCodemunch call

Neither step is needed for a task the built-in tools already answer.

1. `resolve_repo({"path": "."})` — confirms the repo is indexed and queryable. If `indexed: false`, run `index_folder`.
2. For a broad task with no known starting point: `plan_turn({"repo": "...", "query": "..."})` — gets a confidence verdict + recommended files. Obey the confidence:
   - `high` → go directly to the recommended symbols.
   - `medium` → explore the recommended files, cap supplementary reads.
   - `low` → the capability likely doesn't exist; report the gap, do      not keep searching hoping it appears.

### Reading code

When you need one symbol out of a large file, this reads less than the whole file:

- `get_file_outline(file_path)` → see the symbol layout first.
- `get_symbol_source(symbol_id)` → fetch just the function body you need.
- `get_context_bundle(symbol_id)` → if you also need its imports/types.
- `get_file_content(file_path, start_line, end_line)` → last resort,   only for ranges that aren't symbol-shaped.

### Relationships

Each does something distinct; pick by question:

- "What imports this file?" → `find_importers`
- "Who imports this identifier?" → `find_references` (import graph only)
- "Where is this identifier used?" → `check_references` (imports + every content match, is_referenced yes/no)
- "What breaks if I change X?" → `get_blast_radius`
- "Who calls this / what does this call?" → `get_call_hierarchy`
- "Is this safe to rename?" → `check_rename_safe`
- "Is this safe to delete?" → `check_delete_safe` (8 verdict tiers)
- "What's similar to this in the codebase?" → `find_similar_symbols`

### Task orchestration

When a task spans multiple tools, prefer the orchestrator:

- `assemble_task_context({"task": "natural-language task"})`   auto-classifies into one of six intents (explore / debug / refactor   / extend / audit / review) and packs a token-budgeted,   source-attributed capsule. Each entry carries `stage` + `source_tool`   so you can see provenance.

## Anti-patterns

Avoid:

- Long `Read`-then-`Grep`-then-`Glob` chains for a question about   relationships (who references this, what breaks). The index answers   those in one call.
- Searching with different keywords after a search PROVED absence:   `_meta.verdict.evidence_ref` holds an `absent:` token, or   `_meta.absence_evidence.citable` is `true`. Report the gap. A   `no_implementation_found` verdict is not proof on its own:   read the note, re-index if it names the index, and search again.
- Reading a large file in full when `get_file_outline` would do.
- Calling primitives (`find_references` + `get_symbol_source` + ...)   separately when `assemble_task_context` or `get_context_bundle`   produces the whole capsule in one call.

## After editing

- If PostToolUse hooks are installed (Claude Code), edits are   auto-reindexed.
- Otherwise: `register_edit({"paths": [...]})` invalidates caches   and refreshes the index for those files.

## Multi-process awareness (v1.106.0)

If multiple agent sessions share this repo, `get_watch_status` surfaces `watcher_holder` per repo (pid, client_id, started_at, age_seconds). When `watched_by_another_process: true`, our watcher is intentionally idle — another process is keeping the index fresh.

## Tier model

The server can narrow the exposed tool list to suit the model you report. It is optional: pass your model id, exactly as your runner prints it, as `plan_turn`'s `model` parameter, or call `announce_model({"model": "..."})` once. The family is read from the id, so any current id works.

## Tool reference

The full canonical reference (with decision-tree bullet points matching the always-on preamble) follows. This restatement is intentional — when the skill is loaded the agent shouldn't need to also have the preamble in context.

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
