# jcodemunch-mcp — Project Brief

## Current State
- **Version:** 1.108.278. **`exact` must mean exact, and a guardrail must not be its own baseline.** Two fixes, both cases of a measurement reporting a grade it did not make. ⚠⚠ **#458: `identity_type` said `exact` for a match only NORMALISATION made.** `_tokenize` folds case, strips leading underscores and drops punctuation, so for the query `_State` a pytest fixture named `state` and the class literally named `_State` both scored `identity 50.0 / "exact"`. The identity channel could not separate them, the tie fell through to BM25, and the shorter name with a docstring won by **0.355 points out of ~58** — a test fixture outranking the source symbol it tests, on the single highest-confidence query a caller can send. Normalised matches now score **40.0** and report `"normalized"`; literal stays 50.0, `prefix` 30.0 and `segment` 20.0 untouched, so the tiering mechanism already existed. ⚠ **Case folding ALONE is still `exact`, and that boundary was the decision** — `raw_lower` has been case-folded since the channel arrived, so making case load-bearing would move every `getuser`-for-`getUser` caller for no defect. A term-only caller (no raw query) also keeps `exact`: there is no spelling to be literal about, and grading it down would report a distinction never measured, **which is the defect itself, not the fix**. ⚠⚠ **The first end-to-end test PASSED against the broken code, and the reason generalises: a synthetic corpus can be too FAVOURABLE to the symbol under test.** BM25 normalises by document length, and the real `_State` is a large class that scored BELOW the two-line fixture on every lexical field (name 6.996 vs 8.012, signature 6.153 vs 7.389, summary 0.0 vs 5.992). A small synthetic class wins on lexical signals alone, so the ordering assertion held with and without the identity tier — **green, asserting exactly the right thing, and testing nothing.** The corpus now gives the class a long docstring of words unrelated to the query: length without a match. `tests/test_identity_normalized_tier.py` (10), **4 fail against `8cc01a0`**; the 6 passing both sides are the unchanged tiers and the term-only control. ⚠ Found by the `Retrieval-quality gate` failing on PR #457, a telemetry-only change that cannot affect ranking — the replay harness indexes THIS repo, so a new test file changed the corpus. **The gate's self-indexing sensitivity will keep producing false reds on ordinary test-adding PRs, and it also caught a defect nobody was looking for.** Neither half is addressed here. ⚠⚠ **Test-only second half (#467): a schema-budget guardrail asserted a FILE against COPIES OF ITSELF.** `test_the_core_compact_schema_budget_is_unchanged` read three numbers out of `benchmarks/schema_baseline.json` and asserted they equalled three literals written into the test. Both sides were the same frozen artifact, so it pinned the ARTIFACT and never the surface: the baseline was captured 2026-07 and the tool surface drifted underneath it release after release while the assertion stayed green. **It failed for the first time when the capture was re-run — firing on the one event that proves nothing regressed, and silent through every event it existed for.** ⚠ **Re-pinning it to the new number restores exactly that**, so it was REMOVED: `tests/test_schema_budget.py` already holds the 5% drift ceiling against a live `_build_tools_list()` and the §10 `<=4000` ceiling recomputed from the live build, which is the check written to catch a breach BEFORE the baseline is regenerated. ⚠ **2 of the 5 transcription sites were DOCSTRINGS** — a stale number in a comment survives longest precisely because nothing executes it. `tests/test_schema_baseline_transcription.py` (2) fails if any baseline value returns to `tests/` or `benchmarks/`, prose included, and passes against BOTH baselines so it does not depend on the pending re-capture landing; the three-digit counter arms are out of scope rather than guarded badly. ⚠⚠ **Process failure worth not repeating: merging these two put @elfrost's PR #443 back into CONFLICT and killed its CI, the SECOND time in two days.** Our `[Unreleased]` blocks sit exactly where a contributor's does, and **the standing rule is that contributor PRs merge BEFORE our own changelog-touching ones**. A conflicting FORK PR has no `refs/pull/N/merge`, so `pull_request` workflows cannot run at all — **the silent matrix is a SYMPTOM of the conflict, not a second problem.** Resolved from our side (`6a99004..6aec667`, CHANGELOG only) with the thread told the sequencing is ours to fix.
- **Prior (1.108.277):** **Reachability is not only the import graph, and liveness is not only the PID.** Three fixes, two from @phantom-man. ⚠⚠ **The through-line: an obvious signal was mistaken for the whole answer, and in both cases the wrong verdict carried NO hedge.** #461: a template is never IMPORTED — it is reached by a render edge `flow_edges` resolves — so `find_dead_code` reported an actively-rendered template `zero_importers` at **confidence 1.0** while `_resolve_template` resolved that same file from the same index in the same process. **1.0 is reserved for "no importers and not a test file"** (a test file gets 0.9, a cascade 0.7), so the one class we index BECAUSE another subsystem can prove it reachable was the one reported dead with maximum certainty, above the default 0.8 cutoff — unfilterable without discarding genuine findings. **A wrong answer at 1.0 is worse than the same answer hedged**, because confidence is what a caller uses to decide whether to look. ⚠ **Deliberately NOT an extension exemption**; two tests fail against one. A template nothing renders IS dead and is still reported — `.html` is not special, having an inbound render edge is. ⚠ **Two corrections to my OWN issue text, made rather than quietly contradicted**: the "this adds content scanning" objection was wrong (`find_dead_code` already reads content at `:115` and `:261`), and the observatory question is now MEASURED (templates emit no symbols, `dead_symbol_count` 0). ⚠ Scope CHECKED not assumed: `get_dead_code_v2` does NOT share it (symbols only, 0 template-derived entries) — contra #446 where both tools needed the fix. ⚠⚠ **#450 (@phantom-man): `_is_pid_alive` answered "is this PID taken?", not "is my process still there?"** — after PID reuse a two-week-old registry row resolved to a Chrome renderer and read as live forever. Creation time is now the identity anchor. **The best part is the Linux epoch choice**: `/proc/<pid>/stat` starttime kept BOOT-RELATIVE rather than converted via `btime`, because `btime` derives from the wall clock and a `settimeofday`-class step (suspend/resume, VM restore, first NTP sync) would move every recorded value at once — **a fix that mass-invalidates on clock adjustment is worse than the bug**. ⚠ I went looking for a hole and did NOT find one: measured on Win11 unelevated, `PROCESS_QUERY_LIMITED_INFORMATION` reads creation time for `csrss`/`wininit`/`services`/`lsass`(protected)/`svchost`, so the unreadable-create-time fallback is rare, and conservative anyway. ⚠ #452/#459: `.html`/`.htm` on the bundled html grammar with EMPTY `symbol_node_types` — zero symbols, so every symbol-driven consumer is untouched; the FILE entering `source_files` is the point, because `_resolve_template` returned `None` on every Django/Flask/Express/Rails repo before it. **Markdown DECLINED**: 2,410 section symbols would land in dead code and collapse the published `dead_code` grade — **a repo would score worse publicly for being well documented**. ⚠ Test-only: `test_claude_md_rotation.py` gained a NON-anchored heading check — see Tests.
- **Prior (1.108.276):** **A Windows drive-root child can prove it is a repository.** Four merged fixes: #438 (@JayceeB1), #453, #441 + #442 (@rknighton). ⚠⚠ **The one sentence worth carrying: a mock broad enough to satisfy an assertion can be broad enough to bypass what the assertion is about.** It fired THREE times in three costumes this release — a blanket `os.path.exists=True` that also answered `_is_container()`'s `/.dockerenv` probe (dropping `_MIN_PATH_PARTS` 3→2 so the guard under test was never called, proven by spying: zero invocations); an unpatched `Path.is_file` letting the runner's network decide a verdict; and a tripwire deriving from `Exception` that production's bare `except Exception` swallowed, so a deliberately re-broadened mock PASSED with the guard installed. ⚠ **The tell for the first was that it failed IDENTICALLY with and without the fix** — a test failing on both sides is as uninformative as one passing on both, and it is the CHEAPER tell because you are already looking at a red. **Run the non-vacuity pass even when the test is currently failing.** ⚠⚠ **My own review advice on #438/#439 was WRONG and nearly shipped a hole**: I said the depth helper subsumed `not drive.startswith("\\\\")`. It does not — the helper is a DEPTH rule, the clause is a SCOPE rule, and a UNC share root computes to **exactly 2, same as `C:\repo`**, so dropping it admits a whole file server. **Check what a predicate is FOR, not what it reads.** Corrected in the CHANGELOG rather than deleted. ⚠ **#453's root cause was NOT the one I inferred** — seven `read_text` network probes were real but production swallows them; the failure was `resolve_index_identity` → `folder_path.is_file()` (`storage/git_root.py:160`), and **only pulling attempt 1 of a rerun settled it**, because a rerun flips the run's conclusion and hides the failure from `gh run list`. ⚠⚠ **#442: measuring the SAFE fix first is what chose the risky one.** Skipping the eight `IF NOT EXISTS` statements while keeping per-write open/close captures **2%**; the open/close is the other 98%. Shipped 3.455ms vs 16.615ms (**79%**, agreeing with his 82%; absolutes are machine-local, the ratio transfers). `check_same_thread=False` is REQUIRED (searches dispatch via `asyncio.to_thread`) and SAFE only because every caller holds `_State._lock` — recorded at the call site. ⚠ Caching introduces two failure modes the report did not name: a stray `close()` poisons the cache (liveness probe catches it) and a DELETED db gets written into an unlinked inode forever (only the `exists()` check catches it). **Windows cannot produce the orphan case at all** — it refuses to unlink a file with an open handle — so that end-to-end test is POSIX-only and is the 9th skip. **Do not read that skip as cross-platform coverage.** ⚠ #441 pre-existing rows keep `NULL` = UNKNOWN and are NOT backfilled; inferring `count == len(returned_ids)` is the defect itself. **@rknighton filed it against his own earlier claim** in Discussion #430. `tests/test_v1_108_276.py` (19).
- **Older releases (1.108.275 and earlier):** see `CHANGELOG.md`. The 1.108.182 entry ("a stall has a name and a ceiling", #375) and the 1.108.177-.181 #377 hardening arc are there in full.
- **Tests:** 7799 passed, 9 skipped, **0 failed** (1.108.278) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by DECOMPOSITION against .277's 7797 total: `test_identity_normalized_tier.py` 10 (#458) + `test_schema_baseline_transcription.py` 2 (#467) **- 1 REMOVED** (`test_the_core_compact_schema_budget_is_unchanged`) = **+11**, and 7797 + 11 = 7808 exactly. **A removal is part of the delta and the usual add-only arithmetic hides it.** ⚠⚠ **THE DOCUMENTED 3.13 REPRODUCE COMMAND UNDER-COLLECTS BY 105 TESTS, and it reports a clean pass while doing it.** `uv run --python 3.13 python -m pytest tests/ -q` collected **7703** against the local 7808. The missing 105 are ENTIRELY three watcher files (`test_watcher_serve.py` 49, `test_watcher_lock.py` 40, `test_watcher_dynamic.py` 16), each gated on `pytest.importorskip("watchfiles")` — and `watchfiles` is an OPTIONAL extra. **CI installs it** (`uv sync --locked --group dev --extra watch`, `test.yml:84`); the documented command does not. **Use `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q`** — that run is **7793 passed / 15 skipped, the SAME 7808 TOTAL**, different skip split. ⚠ **The totals convention is what caught it**: passed counts alone read as a plausible pass either way, and a whole subsystem being absent is invisible from `N passed`. ⚠ **Do NOT read this as .277's number being wrong** — 7782 + 15 = 7797 is internally consistent, so that run DID collect the watcher tests. `uv run` reuses an already-synced environment, so the same command can collect differently depending on what last synced it. **The command is unreliable, not that record.** Prior (1.108.277): 7788 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠⚠ **This release adds NO test file of its own and a flat delta would have been the RED flag on any other release** — every prior one ships a `test_v1_108_NNN.py`, so "no new tests" normally means the bump outran the work. Here the work landed across the day in #459/#462/#463/#464 and the release commit is version metadata + changelog + rotation only. **Reconciled by DECOMPOSITION rather than a same-tree collect**, because the collect diff has nothing to subtract: `test_html_file_class.py` 4 (#459) + `test_v1_108_277.py` 6 (#462) + `test_pid_reuse_identity.py` 10 (#451 via #464) + `test_claude_md_rotation.py` 4→9 = +5 (#463) = **25**, and .276's 7763 + 25 = 7788 exactly. ⚠ **Pick the reconciliation method that matches how the work landed**; applying the usual one here yields a zero and proves nothing. ⚠ 3.13 CI-env reproduce: **7782 passed / 15 skipped**, same 7797 TOTAL, different skip split — compare totals across interpreters, never passed counts. Prior (1.108.276) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7772 total, 7753 with `test_v1_108_276.py` ignored (= its 19); the **+5 over .275's 7748 is five new `def test_` functions in `test_tools.py`** from the #438/#439 drive-root work — COUNTED in `git diff v1.108.275..HEAD`, not inferred, because "nothing else moved" was not true this release and asserting it would have been the same shape of error the count notes below are about. ⚠⚠ **The 3.13 CI-env reproduce totals the SAME 7772 but splits 7757 passed / 15 skipped** — six tests that RUN on 3.10 SKIP there. **A passed-count comparison ACROSS interpreters is meaningless; compare TOTALS.** ⚠ **The 9th skip is the POSIX-only orphaned-inode test for #442** — Windows refuses to unlink a file with an open handle, so this box CANNOT produce that case. **Do not read that skip as cross-platform coverage**; it is a real local gap covered only by the portable unit test for the predicate. Prior (1.108.275) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7748 total, 7734 with `test_v1_108_275.py` ignored (= its 14), and 7734 is exactly .274's total, so nothing else moved. Prior (1.108.274) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7734 total, 7728 with `test_security_disclosure.py` ignored (= its 6), and 7728 is exactly .273's total, so nothing else moved. ⚠⚠ **This line was briefly written with a GUESSED number before the run finished, and the guess (7734) was the TOTAL rather than the passed count — it would have read as a plausible, wrong figure.** Never pre-write a count; the run is the only source. Prior (1.108.273) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7728 total, 7717 with `test_v1_108_273.py` ignored (= its 11), and the +1 over .272's 7717 is `next` ENTERING the #435 sweep now that its exemption is gone. ⚠ **The 8th skip is EXPECTED and is not a lost test**: `_JS_VARIANT_EXEMPT` is empty, so the ratchet parametrizes over an empty set and pytest skips it ("got empty parameter set"). That is the end state of a ratchet that did its job; it re-arms the moment anyone adds an exemption. ⚠⚠ **A version bump MID-RUN voids the run** — the rotation gate compares CLAUDE.md to `pyproject.toml`, so a suite spanning the bump is not evidence. Bump and rotate FIRST, then run once. (Done wrong on .273 and the run was discarded.) Prior (1.108.272) **+ `uv run ruff check src/` clean**. ⚠ Delta is EXACTLY the 9 new `test_v1_108_272.py` tests, reconciled by COLLECTING the same tree twice (7716 with the file, 7707 with it `--ignore`d) rather than by arithmetic against this line. ⚠⚠ **That method was forced, because this line was STALE by ~239 for two releases** — it read "7470 (1.108.269)" while .270's 31 and .271's 124 were never folded in, so the documented baseline was unusable as one. **A count that is only ever appended to during a release rots the moment a release skips it**; prefer a same-tree collect diff, which cannot go stale, and treat this number as a report rather than a baseline. ⚠⚠ **The count was mis-reported once during this release and the ARITHMETIC caught it, not the reading** — an intermediate run was quoted as "7469 passed, 0 failed", a combination that never happened: it was 7469 passed WITH 1 failed, totalling 7470. **Always reconcile passed+failed against the prior release's total plus the new test count**; eyeballing `N passed` at the end of a 17-minute run is how a red run gets read as green. ⚠ The failure was the CLAUDE.md rotation gate correctly refusing a Current State naming 1.108.269 while `pyproject.toml` still read .268 — **the gate fires BEFORE the version bump lands, so a red rotation test mid-release is expected and must not be waved through as "just the gate"**; it clears only when every pin site agrees. **Prior (1.108.268):** 7436 passed, 7 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .267's 7428 is EXACTLY the 8 new `test_stdio_guard.py` tests; nothing else moved. ⚠⚠ **The CLAUDE.md rotation gate caught a real mistake this release** — a 4th entry was added without demoting .267 or moving the `Older releases` boundary, and the gate failed the build rather than letting the history drift. **Prior (1.108.267):** 7428 passed, 7 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .266's 7404 is EXACTLY the 24 new `test_constant_extraction_guard.py` tests; nothing else moved. **Prior (1.108.266):** 7404 passed, 7 skipped, **0 failed** (isolated worktree run) **+ `uv run ruff check src/` clean + CI all 9 jobs green on the pushed SHA**. ⚠ The delta from .265's 7394 is EXACTLY the 10 new `test_format.py` cases; nothing else moved. ⚠⚠ **Nothing moving is itself the finding** — not one existing test pinned a fusion or semantic confidence value, which is precisely why a ~5x mis-scaling shipped and survived. ⚠ **+17 after .264 shipped**: the file-IO scanner needed TWO MORE iterations (see below), test-only, no bump. ⚠⚠ **A green suite is NOT a green build** — lint was RED for four releases while this line said 0 failed. Quote ALL THREE (suite, ruff, CI) from now on. ⚠⚠ **A green suite is NOT a green build** — lint was RED for four releases while this line said 0 failed. Quote BOTH numbers here from now on, and read the CI run for the pushed SHA. ⚠ **.261's run took 47m45s against ~16-17m before and after it on the same tree** — same counts, same result, so it was machine contention and NOT a signal. Do not treat a wall-clock outlier as a regression. ⚠⚠ **A config change is the one edit whose blast radius is the whole suite** - 128 test files touch `_GLOBAL_CONFIG` directly, so a "small" resolver change is never a small run. ⚠ **The "KNOWN 12 local-ONNX `test_semantic_search` env failures" are GONE** — .207's autouse `no_local_onnx` fixture fixed them, so a local run is now fully green and **any** red is a real signal. Do not carry that 12-failure allowance forward; it papered over a real failure once already (.197 had one hiding inside it). ⚠ **Still do not eyeball the COUNT** — diff the FAILED names against the same tree with your changes stashed; for .199 and .205 that diff was empty, and for .209 the failure set was empty outright, which is the one case that needs no baseline. ⚠ **Stashing is the wrong tool when the change is already committed and pushed** — for .205 the comparison ran in a throwaway `git worktree add --detach <pre-release-sha>`, which also survives a concurrent writer in the main tree.
- **Python:** >=3.10
- **Tool count:** 91 visible in `full` / 94 in catalog (front door hidden; counts verified 2026-07-30 from `jcodemunch-mcp surface`, which is the only place to get them — do NOT hand-type this; +1 v1.108.111 `get_parity_map`, +1 v1.108.112 `get_decorator_census`, +1 v1.108.113 `get_architecture_metrics`); `tool_surface=counter` exposes a 3-tool front door (`order`/`menu`/`route`) instead

## Key Files
```
src/jcodemunch_mcp/
  server.py            # MCP dispatcher (async); CLI subcommand dispatch, auth/rate-limit middleware. v1.108.66: the Counter front door (order/menu/route) — _effective_surface()/_counter_front_door_tools()/_raw_catalog_tools()/_catalog_names() + surface-collapse in _build_tools_list + _handle_order/menu/route + early front-door branch in call_tool
  counter.py           # (v1.108.66) The Counter: adaptive tool surface logic (pure, no server import). FRONT_DOOR set; STATE_CHANGING_ACTIONS + exec/write-verb tripwire (_FORBIDDEN_VERB_RE) → order_gate(); idf-weighted search_catalog() for menu; _INTENT_RULES + classify_intent()/shape_execute_args() for route. v1.108.124: EXAMPLES (curated per-action example arg objects) + example_for() — catalog_entry attaches `example` into menu rows, _handle_route uses it as the args_template fallback; validated against live inputSchemas in test_counter.py. server.py owns Tool registration + call_tool re-dispatch; counter.py is fed plain data
  watcher.py           # WatcherManager class (dynamic folder watching); watch_folders() wrapper
  progress.py          # MCP progress notifications; ProgressReporter (thread-safe, monotonic), make_progress_notify() bridge. v1.108.189 adds HeartbeatReporter (#383) — the token-less fallback: elapsed-time WARNING lines on the LOG channel, duck-typing ProgressReporter so the dispatcher wires either identically. ⚠ Holds NO notify channel/session ref by construction (not in __slots__) and close() yields no futures, so it CANNOT become an unrequested notification; silent until the first JCODEMUNCH_HEARTBEAT_SECONDS elapses, and finish() is silent if it never spoke
  security.py          # Path validation, skip patterns, file caps
  redact.py            # Response-level secret redaction; regex patterns for AWS/GCP/Azure/JWT/GitHub/Slack/PEM/API keys/private IPs; redact_dict() post-processor
  config.py            # JSONC config: global + per-project layering, env var fallback, language/tool gating
  agent_selector.py    # Complexity scoring + model routing (off/manual/auto); default provider batting orders
  cli/
    init.py            # `jcodemunch-mcp init` — one-command onboarding (client detection, config patching, CLAUDE.md, Cursor rules, Windsurf rules, hooks); --demo flag. v1.105.1: `install <agent>` / `uninstall` / `install-status` verbs. v1.107.0: `--skills` flag on install, skills block in install_status report
    skills.py          # v1.107.0: Claude Agent Skill bundle writer. _build_skill_content() composes YAML frontmatter + tier-filtered tool-usage decision tree. install_claude_skill / uninstall_claude_skill / skill_status. Lives at ~/.claude/skills/jcodemunch/SKILL.md (global) or ./.claude/skills/jcodemunch/SKILL.md (project). Reuses _filter_policy_for_tools from init.py for tier awareness
    hooks.py           # PreToolUse (Read interceptor) + PostToolUse (auto-reindex) + PreCompact (session snapshot) + TaskCompleted (post-task diagnostics) + SubagentStart (repo briefing) hook handlers for Claude Code
  groq/
    cli.py             # `gcm` CLI entrypoint — codebase Q&A (single question + --chat mode)
    config.py          # GcmConfig dataclass: GROQ_API_KEY, model, token_budget, system prompt
    retriever.py       # Bridge to jCodeMunch: ensure_indexed(), retrieve_context()
    inference.py       # Groq API streaming + batch via OpenAI-compatible client
  parser/
    languages.py       # LANGUAGE_REGISTRY, extension → language map, LanguageSpec
    extractor.py       # parse_file() dispatch; custom parsers for Erlang, Fortran, SQL, Razor
    imports.py         # Regex import extraction (19 languages); extract_imports(), resolve_specifier(), build_psr4_map()
    fqn.py             # PHP FQN ↔ symbol_id translation (PSR-4); symbol_to_fqn(), fqn_to_symbol()
  encoding/
    __init__.py          # Dispatcher: encode_response(tool, response, format) — auto/compact/json
    format.py            # MUNCH on-wire primitives: header, legends (@N), scalars, CSV tables
    gate.py              # 15% savings threshold (JCODEMUNCH_ENCODING_THRESHOLD override)
    generic.py           # Shape-sniffer fallback encoder (covers all tools w/o custom encoder)
    decoder.py           # Public decode() — rehydrates MUNCH payloads back to dicts
    schemas/             # Per-tool custom encoders (tier-1, phase 2+); auto-discovered registry
  investigator/
    deletion_safety.py           # (v1.108.214) tri-state proof obligations; `investigate_deletion_safety`. NOT an MCP tool
    retrieval_counterfactual.py  # (v1.108.217) `explain_route(task, expected_action)` / `explain_misses(per_query)` — names the FIRST gate that excluded an action: `catalog_absent` / `empty_query` / `rule_preempted` / `no_lexical_overlap` / `ranked_below_cutoff`, in pipeline order (reporting more than the first is reporting consequences). ⚠ Uses the SAME `counter` functions the live front door uses — never a second scorer. ⚠⚠ `rule_preempted` = **never scored**, because `route` runs the fallback ONLY when no rule matched; do NOT read it as a ranking loss. NOT an MCP tool (item 3 moratorium), test-asserted
  storage/
    selective.py       # (v1.108.216, #398 Arc 2) `SelectiveIndexView` — a `CodeIndex`-SHAPED read view over metadata + named symbol rows. **NOT a subclass**: subclassing would inherit CodeIndex's corpus-wide methods silently operating over a partial `symbols` list, the one outcome this exists to make impossible. `EXACT_FIELDS` are copied onto the instance at construction; **everything else falls through `__getattr__` and promotes to one full load** — including fields invented later. `CORPUS_WIDE` documents the known ones and every entry is parametrized in the test. ⚠ `_PROVENANCE` (`_db_path`/`_loaded_mtime_ns`) MUST stay in `__slots__` — see Current State
    generation.py      # (v1.108.215, #398 Arc 1) THE READ CONTRACT, both halves. `IndexGeneration`/`describe(index)` — the ONE place `indexed_at`/`git_head`/`_db_path`/`_loaded_mtime_ns` are read off an index; empty string normalises to None once (three surfaces used to disagree). `rewritten_since_load` keeps unknown ≠ changed. `connect_readonly(db_path)` / `readonly_uri` / `wal_sidecar_present` — ⚠⚠ **neither single flag is right**: plain `mode=ro` CREATES `-wal`/`-shm` when absent (moves `_db_mtime_ns`, the .185 `rebuilding` bug), `immutable=1` cannot READ them when present (measured: raises `no such table`, which `has_any()` maps to a confident False). Reads the WAL when its sidecar exists, immutably when it does not. **Every read-only opener in the tree routes through this**; `test_generation_contract.py` fails on a hand-rolled `?mode=ro` URI anywhere else
    sqlite_store.py    # CodeIndex, save/load/incremental_save, WAL-aware LRU cache (_db_mtime_ns); get_source_root(). v1.106.0: save_index + migrate_from_json acquire `indexwrite` process_locks before SQLite writes, body extracted to `_save_index_locked` / `_migrate_from_json_locked`; serialises across MCP processes
    process_locks.py   # v1.106.0: generic multi-process coordination (acquire/release/inspect/held). Atomic O_EXCL + fcntl flock (Unix) + PID liveness + scoped lock files. Scopes: `watcher` (one-watcher-per-repo, shared with watcher.py) + `indexwrite` (save coordination). Metadata: pid/client_id/scope/target/started_at. JCODEMUNCH_CLIENT_ID env var sets friendly client name (defaults to sys.argv[0] basename)
  embeddings/
    ../storage/embedding_matrix.py # (v1.108.223, #399) Process-local cache of the L2-NORMALISED matrix, keyed by a size+mtime stamp over the .db AND its -wal/-shm sidecars. `get_matrix(db_path)` -> `EmbeddingMatrix | None`; `score_all(q)` is ONE `matrix @ q` under numpy and a norm-hoisted Python loop without it. ⚠ **numpy is opportunistic, never a dependency** — `_scores_python` is tested with numpy forced absent. ⚠ **The sidecars are load-bearing in the stamp**: a write lands in the WAL and may not touch the .db until a checkpoint, so a .db-only stamp pins a stale matrix across exactly the write it must see. ⚠ Rows are `array.array('f')` in the fallback, not `list[float]` (~8x the memory, and this is HELD not thrown away). Bounded to 2 repos; `JCODEMUNCH_EMBED_MATRIX_CACHE=0` disables retention only
    ../storage/embedding_store.py  # CRUD over symbol_embeddings. ⚠ **Five read paths, pick deliberately**: `iter_raw()` (.223, read-only, UNDECODED blobs, for embedding_matrix only); `get_all()` (read-WRITE conn, bumps .db mtime), `get_all_readonly()` (.185, `mode=ro&immutable=1`, does not), `get_many(ids)` (.210, targeted + read-only, chunked at 900 for SQLITE_MAX_VARIABLE_NUMBER), `has_any()` (.211, `SELECT 1 ... LIMIT 1`, read-only, TRI-STATE — `None` means could-not-establish and is NEVER `False`). ⚠ `count()` and `get_all()` both use `_connect()`, which runs PRAGMA+CREATE-TABLE on EVERY connection — an existence check is NOT free and moves the mtime. Prefer `get_many` whenever the caller already knows its ids, and `has_any` over `count()` for a pure existence question
    local_encoder.py   # Bundled ONNX local encoder (all-MiniLM-L6-v2, 384-dim); WordPiece tokenizer, encode_batch(), download_model()
  enrichment/
    lsp_bridge.py      # LSP bridge — opt-in compiler-grade call graph resolution via pyright/gopls/ts-language-server/rust-analyzer; LSPServer lifecycle, LSPBridge multi-server manager, enrich_call_graph_with_lsp() + enrich_dispatch_edges() (interface/trait dispatch resolution)
  retrieval/
    subject_state.py     # (v1.108.178) #377 item 3: what a scan's answer depends on, cheap enough to re-check. capture() at cache-WRITE (index generation, .db mtime, live git HEAD, + working-tree fingerprint ONLY for an absence) / changed() at cache-READ / revalidate_verdict() downgrades a replayed `absent` and strips the stale evidence token. UNKNOWN is never a change. v1.108.179 adds moved_during_scan() (item 6: before/after identity around a scan, fresh_head bypasses the TTL cache) + changed(when=) so the cached-replay and live-scan refusals read differently. v1.108.181 adds working_tree_state() (item 5: scope-level clean/dirty_in_scope/dirty_outside_scope/unknown/not_applicable; blocks ONLY on in-scope dirt the index has not re-read) + _parse_porcelain/_in_scope/_unreflected_in_index
    signal_fusion.py   # Weighted Reciprocal Rank (WRR) fusion: lexical + structural + similarity + identity channels
    ledger_trust.py    # (v1.108.186/.187) THE ONE RULE for which ranking_events labels are evidence, shared by tuning.py + regret.py + tools/analyze_perf.py instead of copied. semantic_label_is_trustworthy(row) refuses exactly (tool="get_ranked_context_fusion", semantic_used=1) — pre-fix rows from an exit that built no similarity channel. identity_label_is_trustworthy(row) (.187) refuses rows that RETURNED symbols while recording NO top1_score — the only exact signature of the exit that passed no ledger features; ⚠ it deliberately does NOT match on identity_hit itself (pre-fix is always 0 and 0 is an honest post-fix answer), and search_symbols_fusion's history is UNSEPARABLE (no discriminator exists, window is the only remedy). ⚠⚠ **(#440) `search_symbols` is unseparable for the SAME reason and over a MUCH larger share of the table** — both non-fusion exits built the same score-only ledger input, they too always passed top1_score, and search_symbols is the highest-volume producer in the ledger. Producers fixed via `_ledger_identity_rows` (see Current State); **do NOT read "the fusion rows are handled" as "the identity_hit column is clean"** — it is clean only for rows written after that fix. UNKNOWN, not False: consumers put them in a THIRD bucket and disclose the count. A short row is TRUSTED (this refuses a KNOWN lie; refusing the unclassifiable would be silent data loss). ⚠ The semantic rule EXPIRES if that exit ever builds a similarity channel — drift guard in tests/test_v1_108_186.py
    regret.py          # (v1.108.68) analyze_regret: mines the ranking_events ledger for SIX retrieval-regret signals (requery_churn/low_confidence/thin_result/ambiguous_top/stale_at_query/vocabulary_gap) as severity-ranked clusters. Pure read via token_tracker.ranking_db_query; no new tables. Consumed by suggest_corrections + the digest one-liner
  summarizer/
    batch_summarize.py # 3-tier: Anthropic > Gemini > OpenAI-compat > signature fallback
  tools/
    index_folder.py    # Local indexer (sync → asyncio.to_thread in server.py). v1.108.0 adds `paths=[...]` arg via new `resolve_explicit_paths()` helper to skip the directory walk when the caller supplies an explicit file/subdir list; security matches the walk path (outside-root / traversal / symlink-escape / oversize / unsupported-ext all warn-and-skip with per-entry warnings). v1.108.6 adds `identity_mode: "config"|"local"|"git"` arg — delegates to `storage/git_root.resolve_index_identity()` which is the single source of truth for local-folder → repo-ID resolution (replacing duplicated logic across watcher.py / resolve_repo.py / index_folder.py).
    refresh.py         # (v1.108.259, #395) Bounded, resumable repo-wide refresh. `run()` slices the corpus through `index_folder(paths=..., force_reparse=True)` under a wall-clock + file budget, persisting a cursor to `<CODE_INDEX_PATH>/refresh_state/<owner>__<name>.json` (atomic write) so N short windows converge like one long one. `status()` reports progress and does NO work. ⚠⚠ Stamps `parser_generation` ONLY after re-running discovery proves full-corpus coverage — drift appends and DEFERS, batch errors block, and `stamp_parser_generation` refuses to go backwards. ⚠ `use_ai_summaries` defaults FALSE here (opposite of `index_folder`): a scheduled job must not bill a paid summarizer unasked
    index_repo.py      # GitHub indexer (async, httpx)
    get_symbol.py      # get_symbol_source: shape-follows-input (id→flat, ids[]→{symbols,errors}). v1.108.70 bounded-source mode: optional source_start_line/source_end_line/max_source_lines/max_source_bytes/max_total_source_bytes return an explicitly-labeled slice (source_truncated + range/total metadata, source_is_bounded_view); verify stays full-body; context_lines+bound rejected. Pure helpers _utf8_safe_truncate + _bound_source
    search_columns.py  # Column search across dbt/SQLMesh models
    get_context_bundle.py   # Symbol + imports bundle; token_budget/budget_strategy
    get_ranked_context.py   # Query-driven budgeted context (BM25 + PageRank)
    resolve_repo.py    # O(1) path→repo-ID lookup
    find_importers.py  # Files that import a given file (import graph); cross_repo param
    find_references.py # Files that reference a given identifier. v1.108.96: _attach_scip_to_response unions SCIP compiler-verified reference edges (compile-time evidence P1)
    _scip_consume.py   # (v1.108.118) Shared SCIP-evidence reader for the graph consumers (P2): open_scip_reader (mode=ro, honest-None when scip_edges absent/empty incl. pre-v17) + scip_meta_and_stale + scip_meta_block. Used by get_blast_radius._attach_scip_to_blast + get_call_hierarchy._attach_scip_to_hierarchy
    test_summarizer.py # Diagnostic tool: probe AI summarizer, report status (disabled by default)
    package_registry.py # Cross-repo package registry: manifest parsing, registry building, specifier resolution
    get_cross_repo_map.py # Cross-repo dependency map at the package level
    _call_graph.py       # Shared AST-derived call-graph helpers (callers/callees, BFS)
    get_call_hierarchy.py # get_call_hierarchy: callers+callees for a symbol, N levels deep
    decision_context.py   # (v1.108.59) resolve_decision_context: read-only git-archaeology surfacer. Mines decision-bearing commits (revert/perf/refactor/rename/bugfix) for a set of files, reusing get_symbol_provenance's _run_git/_classify_commit/_extract_intent; dedupes by SHA, ranks by category weight × recency, emits digest + by_category + volatility + summary. Surface-only, nothing persisted. Consumed by get_blast_radius / get_impact_preview via include_decisions
    get_impact_preview.py # get_impact_preview: transitive "what breaks?" analysis. v1.108.59: include_decisions attaches a read-only `decisions` block (decision_context)
    plan_refactoring.py   # plan_refactoring: edit-ready plans for rename/move/extract/signature refactorings
    get_symbol_complexity.py  # get_symbol_complexity: cyclomatic/nesting/param_count for a symbol
    get_churn_rate.py         # get_churn_rate: git commit count for file or symbol over N days
    get_delivery_metrics.py   # (v1.108.69) get_delivery_metrics: durable-change delivery over a window. Classifies each non-merge commit into one bucket (revert_authored/reverted/reworked/durable) via _run_git; commits_durable is the numerator for cost-per-outcome (the `delivery` CLI's --cost divides AI spend by it). Hub files (CHANGELOG/version/monolithic dispatch, co-touched by >=max(4,20%) of commits) excluded from the rework signal (auditable via _meta.hub_files_excluded); commits_provisional flags the trailing tail. Reuses get_symbol_provenance._classify_commit for by_category. Read-only, no new tables
    get_symbol_provenance.py  # get_symbol_provenance: full git archaeology per symbol — authorship lineage, semantic commit classification, evolution narrative. Phase 5: optional stack_frequency block reading runtime_stack_events over a 30-day window — per-severity counts + first/last seen; narrative gains an appended sentence when error count >= 3
    get_pr_risk_profile.py    # get_pr_risk_profile: unified PR/branch risk assessment — fuses blast radius + complexity + churn + test gaps + volume into composite score. Phase 7: when runtime traces have been ingested, adds a 6th signal (runtime_traffic; W=0.15 with the static five rebalanced to 0.85 of their original weights) plus a runtime_dark_code_introduced flag for PRs that add code in files with zero runtime evidence. Static-only callers (no traces) keep the historical 5-signal mix bit-for-bit.
    get_architecture_metrics.py # (v1.108.113) get_architecture_metrics: concentration (Gini over per-file symbols/bytes/fan_in/fan_out + top concentrators) + depth (Lakos levelization, longest chain over SCC-condensed DAG) + modularity (WCC clusters + back_edges = DSM hidden coupling). Reuses _build_adjacency (get_dependency_graph) + _find_cycles. One tool vs their 3; NO N×N matrix; does NOT touch radar composite. Read-only analytics. Standard tier
    get_decorator_census.py   # (v1.108.112) get_decorator_census: repo-wide census of decorators/annotations/attributes. Aggregates the index's stored per-symbol `decorators` (cross-language, no parser work); normalized histogram (_normalize_decorator strips @/args/[]; _short_raw flattens+caps raw_forms), per-bucket symbol_kinds + file count; name_filter/scope_path/kind filters, include_sites. Read-only ANALYTICS (no tokens-saved _meta). Standard tier
    get_parity_map.py         # (v1.108.111) get_parity_map: correspondence-aware migration parity between a SOURCE and TARGET symbol tree (two subpaths of one repo, or two repos). Exact + rename matching (reuses find_similar_symbols _signature_tokens/_callee_set/_jaccard/_byte_ratio), status per source symbol (ported/ported_diverged/unported/orphaned/added), dependency-ordered port_plan (adjacency from _callee_set, SCC grouping via get_dependency_cycles._find_cycles, Kahn topo, unblocked/blocking_deps). Read-only/plan-only; parity_axes reserved for P3 suite axes. Standard tier
    get_hotspots.py           # get_hotspots: top-N high-risk symbols by complexity x churn
    get_repo_map.py           # get_repo_map: query-less, token-budgeted, signature-level repo overview ranked by PageRank — cold-start orientation. Reuses cached PageRank, emits signatures only (no bodies), greedy-packs per-file under token_budget
    find_similar_symbols.py   # find_similar_symbols: multi-signal consolidation detection — semantic (embeddings) + structural (signature/size) + behavioral (callee Jaccard); union-find clustering, verdict tier (near_duplicate / similar_logic / parallel_implementation), canonical pick by PageRank, differs_by breakdown. BM25 inverted-index pre-filter for sub-N^2 cost. Skips tests/dunders/generated by default.
    get_group_contracts.py    # get_group_contracts: cross-repo shared-symbol API surface for a group of indexed repos. Resolves named imports through the package registry, classifies each shared symbol into 4 verdict tiers (de_facto_api / leaky_internal / dead_contract / version_skew), attaches stability score (churn-weighted), last_breaking_change (from provenance), and runtime_hits (when traces exist). Pairs with get_cross_repo_map: that gives repo-level edges; this zooms in to the symbol-level surface.
    find_implementations.py   # find_implementations: multi-source concrete-impl discovery for interfaces/abstracts/methods. Four resolution channels with confidence scoring — LSP dispatch (1.0), AST class hierarchy (0.85), duck-typed name match (0.65), decorator handler (0.45). Classifies each impl (subclass_override / interface_impl / duck_typed / decorator_handler / subclass), ranks by PageRank × byte_length, attaches differs_by breakdown, optional cross_repo discovery.
    check_delete_safe.py      # check_delete_safe: composite preflight — can this symbol be deleted? Combines find_importers (cross_repo) + check_references + find_dead_code + runtime evidence + entry-point heuristics into a single verdict (safe_to_delete / test_coverage_only / internal_only / internal_uses_blocking / external_uses_blocking / cross_repo_blocking / runtime_observed / entry_point) plus top-5 blockers ranked by severity plus a one-line recommended_action. Read-only. Pairs with check_rename_safe for the rename-and-delete refactor flows. v1.104.1: track test_import_count separately from external_import_count so test-only consumption correctly downgrades to test_coverage_only. v1.108.6: honest-hint caveat — when `safe_to_delete` is reached AND `include_runtime=True` AND no traces are ingested for the repo (`_runtime_data_present()` returns False), the `recommended_action` surfaces that the verdict rests on static signals only and points at `import-trace`. `signals.runtime_data_present` surfaced for callers to introspect. Back-ported from `check_column_drop_safe` in jdatamunch-mcp v1.8.0.
    assemble_task_context.py  # assemble_task_context: task-aware single-call context orchestrator. Auto-classifies the task into one of six intents (explore/debug/refactor/extend/audit/review) via keyword scoring, auto-extracts anchor symbol names from the task, runs the intent-appropriate sub-tool sequence (digest + hotspots + tectonic for explore; anchor + callers + callees + blast + runtime for debug; anchor + rename_safe + delete_safe + implementations + similar for refactor; anchor + implementations + similar + decorators for extend; anchor + risk + blast + dead_code + untested for audit; changed + blast + risk + similar_changed for review), packs results into a single source-attributed capsule under token_budget. Each entry tagged with stage + source_tool. Intent classification is explainable (returns intent_keywords_matched + intent_confidence). Caller can override intent and include to force specific stages.
    get_tectonic_map.py       # get_tectonic_map: logical module topology via 3-signal fusion (structural+behavioral+temporal) + label propagation
    get_signal_chains.py      # get_signal_chains: entry-point-to-leaf pathway discovery; traces how HTTP/CLI/task/event signals propagate through the call graph; discovery + lookup modes. v1.108.58: include_flow_edges param consumes flow_edges.py — string-dispatched handlers become http gateways, rendered templates attach as a per-chain `views` list
    get_endpoint_impact.py    # (v1.108.90) Endpoint-centric impact: "what breaks if I change GET /users?" _collect_endpoints unifies flow_edges route edges (string-dispatch) + get_signal_chains decorator gateways (Flask/FastAPI/Spring local path) into one endpoint table; _match_endpoints (verb+path exact→suffix); _impact_for_handler fuses get_blast_radius (importers+callers) + render→view edges. Read-only, standard tier. handler_symbol_id bypasses URL resolution for prefixed routes. First slice of docs/prd-framework-routes-endpoint-impact.md; FastAPI prefix / Spring class-mapping composition is the follow-on
    flow_edges.py             # (v1.108.58) Language-agnostic framework flow-edge resolver. resolve_flow_edges(index, store, owner, name, kinds=("route","render")) emits typed edges the AST call graph misses: route→handler (Django path/re_path/url, Express/Fastify/Koa .get(p,h), Flask add_url_rule view_func=, Rails to:"ctrl#action") resolved to symbols via the import graph; render→view (render/render_template/res.render/view string templates) resolved to the template file when indexed. Shape-keyed (one resolver, not per-framework plugins); reuses _ContentCache/_symbol_body/build_symbols_by_file/resolve_specifier. Pure read path, no reindex. Decorator-bound handlers NOT re-emitted (they already surface as gateways)
    render_diagram.py         # render_diagram: universal Mermaid renderer; auto-detects source tool, picks optimal diagram type (flowchart/sequence), encodes metadata as visual signals; 3 themes, smart pruning; optional `open_in_viewer` (config-gated, spawns mmd-viewer)
    mermaid_viewer.py         # mmd-viewer spawn helper for render_diagram; resolve_viewer_path/open_diagram/cleanup_temp_dir; jcm- prefix for safe cleanup; config-gated via render_diagram_viewer_enabled + mermaid_viewer_path
    get_project_intel.py      # get_project_intel: auto-discover+parse non-code knowledge (Dockerfiles, CI configs, compose, K8s, .env templates, Makefiles, scripts); cross-references to code symbols; 6 categories. v1.108.0 adds `scope_path` arg to restrict discovery to a monorepo subpath (use list_workspaces.path values); validates against source_root (traversal/absolute/non-existent all error).
    list_workspaces.py        # (v1.108.0) Enumerate monorepo workspace members. Detects pnpm (pnpm-workspace.yaml), yarn/npm (package.json `workspaces:`), turborepo (turbo.json), lerna (lerna.json), rush (rush.json), Go (go.work `use (...)`, module name from go.mod), Cargo (Cargo.toml `[workspace] members`). Returns `[{path, package_name, manager}, ...]` plus `is_monorepo` + `managers`. Read-only, dependency-free (hand-rolled minimal TOML/YAML readers).
    get_repo_health.py        # get_repo_health: one-call triage snapshot (delegate aggregator); includes six-axis `radar` field (v1.87.0)
    health_radar.py           # Six-axis health radar (complexity/dead_code/cycles/coupling/test_gap/churn_surface) + diff_health_radar pure-function tool for PR-time diff-grade reporting (v1.87.0). Phase 7 (v1.100.0): optional 7th axis runtime_coverage when caller passes runtime_coverage_pct; axis is omitted otherwise so the composite stays comparable against pre-Phase-7 baselines. diff_radar walks the axes dict generically — picks up the new axis automatically.
    get_untested_symbols.py   # get_untested_symbols: find functions with no test-file reachability (import graph + name matching)
    search_ast.py             # search_ast: cross-language AST pattern matching; 10 preset anti-patterns + custom mini-DSL (call:, string:, comment:, nesting:, loops:, lines:); enriched with symbol context
    winnow_symbols.py         # winnow_symbols: multi-axis constraint-chain query; AND-intersects kind/language/name/file/complexity/decorator/calls/summary/churn in one round trip; ranks by importance/complexity/churn/name
    audit_agent_config.py    # audit_agent_config: token waste audit for CLAUDE.md, .cursorrules, etc.; cross-refs against index. Reused by suggest_corrections (_discover_files / _fuzzy_suggest / stale-config findings). Skill-candidate advisory (_check_skill_candidates / _split_sections / _best_subtree): flags always-resident H2 sections whose index-resolved refs concentrate in ONE subtree, gated by `skill_advisor_mode` (default off). ⚠ The signal is CONCENTRATION, not size — it returns [] with no index, and `subtreeShareCap` (0.25) not `concentrationFloor` is the discriminator, because a narrow subtree failing the floor hands selection to its permissive parent. ⚠ Findings state relevance was NOT measured; nothing records which section a turn needed
    suggest_corrections.py   # (v1.108.68) Retrieval-regret synthesis: fuses regret.analyze_regret clusters + audit_agent_config + WeightTuner dry-run into SUGGESTED corrections (routing/vocabulary/index-freshness/stale-config/skill-candidate) with difflib unified-diff CLAUDE.md previews. Read-only charter — never writes a user file; apply_weights touches only tuning.jsonc. Honest no-telemetry hint. ⚠ `_stale_config_corrections` read `f["type"]` while audit findings carry `category`, so stale_config had NEVER emitted; both spellings accepted now. ⚠ skill_candidate keeps `suggested_patch: None` deliberately — a diff showing only the deletion reads as "delete this section"
    analyze_perf.py          # analyze_perf: per-tool latency telemetry (p50/p95/max/error_rate) + cache hit-rate; reads in-memory session ring or persistent telemetry.db (opt-in via perf_telemetry_enabled); compare_release="X" loads benchmarks/token_baselines/vX.json and adds baseline_diff
  runtime/
    __init__.py          # Trace ingestion package (Phases 0-5): re-exports redact_trace_record, resolve_to_symbol_id, parse_otel_file, ingest_otel_file, OtelSpan, parse_sql_log_file, ingest_sql_log_file, SqlQueryRecord, parse_stack_log_file, ingest_stack_log_file, StackEvent, StackFrame, VALID_SOURCES = {'otel','sql_log','stack_log','apm'}
    redact.py            # Single chokepoint redact_trace_record(record, source) — strips emails, IPv4, SQL literals/numerics, JSON value blocks, Python locals reprs, plus all secret patterns from ../redact.py
    resolve.py           # resolve_to_symbol_id(conn, file, line, name) — best-effort (file, line, function) → symbol_id with suffix-match fallback for absolute trace paths against repo-relative index paths
    otel.py              # Phase 1 OTel JSON parser — handles JSON-Lines, single-document JSON, top-level array, and .gz transparently; extracts code.filepath / code.lineno / code.function / duration into OtelSpan
    ingest.py            # Phase 1 orchestrator ingest_otel_file(db_path, file_path, redact_enabled, max_rows) — parse → redact → resolve → upsert; computes per-batch p50/p95 from span durations; FIFO-evicts runtime_calls + runtime_unmapped down to max_rows when exceeded; persists per-pattern redaction counts to runtime_redaction_log
    sql_log.py           # Phase 4 SQL log parser — pg_stat_statements CSV (header autodetect; total_time/total_exec_time + mean_time/mean_exec_time aliases) + generic JSON-Lines (.jsonl/.json/.log) + top-level array fallback + .gz transparent; extracts table refs (FROM/JOIN/UPDATE/INSERT INTO/DELETE FROM/MERGE INTO; schema-qualified names → trailing ident) and column refs (qualified alias.col + bare idents in SELECT/WHERE/ON/HAVING/GROUP BY/ORDER BY)
    sql_ingest.py        # Phase 4 orchestrator ingest_sql_log_file(db_path, file_path, redact_enabled, max_rows) — parse → redact → resolve → upsert; resolver builds a one-shot read-only metadata snapshot (file-stem map, exact-name map, dbt_columns/sqlmesh_columns set); upserts runtime_calls + runtime_columns + runtime_unmapped + runtime_redaction_log under source='sql_log'; FIFO-evicts all three runtime tables
    stack_log.py         # Phase 5 stack-frame parser — Python tracebacks (`File "...", line N, in <name>` pairs), JVM tracebacks (`at pkg.Class.method(File.java:N)` + flattened `Caused by:` chains), Node.js stacks (named `at funcName (file.js:N:N)` + anonymous `at file.js:N:N` + node:events-style module paths). Plain-text + JSON-Lines structured-log + top-level array + .gz. Severity heuristic: looks 3 lines back for FATAL/CRITICAL/ERROR/WARN[ING]/INFO; default 'info'.
    stack_ingest.py      # Phase 5 orchestrator ingest_stack_log_file(db_path, file_path, redact_enabled, max_rows) — parse → redact (event.message) → resolve each frame → upsert; populates BOTH runtime_calls (severity-agnostic rollup so confidence-stamping fires) AND runtime_stack_events (per-severity counts). FIFO-evicts runtime_calls + runtime_unmapped + runtime_stack_events. Phase 6 adds ingest_stack_log_stream() that takes an in-memory text payload via the shared _ingest_stack_iter() pipeline.
    http_routes.py       # Phase 6 Starlette route handlers: POST /runtime/otel, POST /runtime/sql, POST /runtime/stack. Off by default — gated by runtime_ingest_enabled config + JCODEMUNCH_HTTP_TOKEN bearer auth. Per-repo asyncio.Lock serialises writes against the same SQLite DB. Body cap (default 5 MB) checked separately for on-wire and decompressed sizes (gzip-bomb guard). Repo selection via X-JCM-Repo header or ?repo= query. Mounted on both SSE and streamable-http transports.
    confidence.py        # Phase 2 RuntimeConfidenceProbe + attach_runtime_confidence (symbol-keyed) + attach_runtime_confidence_by_file (file-keyed). Stamps `_runtime_confidence` ∈ {confirmed, declared_only, unmapped} on result entries; emits `_meta.runtime_freshness` summary. Read-only connections use ?mode=ro&immutable=1 so they never bump WAL mtime and invalidate the CodeIndex LRU cache. Zero-cost when runtime_calls is empty.
  evidence/
    receipts.py          # (v1.108.183) #377 Phase 2 P1: the `jcodemunch.evidence/v1` envelope + session store. evidence_id() hashes EXACTLY (subject, effective_search, snapshot) — full sha256, never 12 hex; build_envelope/record_receipt (fail-closed on id reuse over differing content: an id that ever named two receipts names NEITHER after); lookup() returns (envelope, reason) with reason naming never_recorded/evicted/collision; PROOF_KINDS holds the jdoc/jdata halves too so parity attaches to ONE enum; coverage_fingerprint() is the OPAQUE Phase-5 (#385) extension point; envelope_json() is deterministic so repeated resource reads are byte-identical; _absence_links maps a Phase-3 `absent:` token to its receipt. Session-scoped, in memory, bounded at 500 + an evicted set
    producers.py         # (v1.108.183) #377 Phase 2 P2 — THE GATE. PRODUCERS registry (4 entries: get_symbol_source symbol_definition only / search_symbols + get_ranked_context symbol_definition+symbol_lookup_absence / search_text literal_text_absence only), each declaring verdict shape, proof kinds, canonical projector arg sets (scope_args NARROW, mode_args change WHICH operation ran), and completeness/freshness/coverage/integrity semantics. mint() is called from the call_tool chokepoint, so it is immune to early returns BY CONSTRUCTION; `_verdict_shape` is the gate — an exit that asserts an answer without the registered build_verdict shape cannot mint (the v1.108.179 class made structural). `_snapshot(trust_channel=)` binds subject_state.capture + repo_freshness + index_coverage_meta + verdict.working_tree; trust_channel=False for the symbol-verdict shape because ITS channels.index says `fresh` for a revisionless folder. `_row_subject` reads the SERVED row only and names what was not served in `limitations`
    scip.py              # (v1.108.96) Hand-rolled SCIP protobuf wire-format reader (no protobuf dep): _read_varint/_iter_fields walk varint + length-delimited fields, unknown fields skipped by construction. Parses Index/Metadata/Document/Occurrence/SymbolInformation/Relationship subset; packed AND unpacked int32 ranges, 3-/4-int range forms, .gz by magic sniff; ValueError (honest) on non-SCIP input. display_name_from_symbol = best-effort last-descriptor name (resolution FALLBACK only; primary channel is (file,line))
    scip_ingest.py       # (v1.108.96) ingest_scip_file: parse → resolve (definition map scip-symbol→(file,line) from Definition-role occurrences; enclosing symbol via runtime/resolve.resolve_to_symbol_id) → persist scip_edges (kinds: reference, implementation) / scip_unmapped (reasoned) / scip_meta (tool, ingested_at, git_head staleness anchor). Skips counted: Import-role occurrences (import graph covers) + `local N` symbols. _ensure_scip_tables covers pre-v17 DBs; FIFO eviction per JCODEMUNCH_SCIP_MAX_ROWS
  tools/
    get_runtime_coverage.py  # Phase 3: coverage histogram for repo or single file. {total_symbols, confirmed, declared_only, coverage_pct, sources, last_seen, unmapped_runtime[]}.
    find_hot_paths.py        # Phase 3: top-N symbols by runtime hit count, with p50/p95, sources, last_seen. Optional name substring filter. Pairs with get_blast_radius.
    find_unused_paths.py     # Phase 3 + 4: symbols with zero/stale runtime hits over the window. Excludes test files and entry-point filenames by default. Refuses when runtime_calls is empty (would trivially flag everything). Phase 4 dbt-aware extension: when context_metadata has *_columns + runtime_columns has rows, rescues SQL-file model symbols that have observed column reads (column-only audit-log shape) and surfaces dbt models whose declared columns have zero hits with reason='dbt_model_no_column_reads' + unused_columns list.
    get_redaction_log.py     # Phase 6: forensic accounting of PII redactions — surfaces per-pattern counts from runtime_redaction_log so operators can verify the redaction chokepoint is firing on production traffic. Filters by source + since_days. Read-only / immutable connection.
  retrieval/
    confidence.py        # compute_confidence/attach_confidence: 0-1 retrieval confidence score (geometric mean of gap, strength, identity, freshness sub-signals); attached to _meta.confidence on search_symbols / plan_turn / get_ranked_context
    freshness.py         # FreshnessProbe: v1.108.180 adds repo_freshness (fresh/stale/unknown/not_tracked, #377 item 4 — the boolean repo_is_stale rendered 'could not find out' as fresh) + _is_git_backed (walks up, so a monorepo subdir is not mislabeled not_tracked). per-result _freshness classification (fresh / edited_uncommitted / stale_index / **unknown, v1.108.209**); compares index SHA vs git HEAD + per-file mtime vs CodeIndex.file_mtimes; wired into search_symbols / get_symbol_source / get_context_bundle / get_ranked_context. ⚠ **classify() must NEVER answer `fresh` for a comparison it could not make** — no source root, moved root, file absent from the tree, stat raised, or no baseline (neither per-file mtime nor parseable indexed_at) all return `unknown`. That was .209's whole fix and it is easy to reintroduce, because the unmeasurable paths are the ones no local dev box ever exercises. summary() carries an `unknown` count and its buckets must sum to the entry count
    tuning.py            # WeightTuner + get_semantic_weight: learns per-repo semantic_weight from v1.78.0 ranking_events ledger; ±0.05 step (clamp 0.1-0.8) when mean confidence between semantic_used groups differs by ≥0.05; persists to ~/.code-index/tuning.jsonc; applied at query time when caller leaves semantic_weight at the default (identity_boost learning removed v1.108.102 — audit W6, was never consumed at query time)
    embed_drift.py       # CANARY_STRINGS (16) + capture_canary/check_drift: pins canary embeddings to ~/.code-index/embed_canary.json, re-checks cosine drift via check_embedding_drift MCP tool; catches silent provider model changes (Gemini/OpenAI/bundled-ONNX); default threshold 0.05 cosine distance
```

## CLI Subcommands
| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (`stdio`, `sse`, or `streamable-http`) |
| `init` | Interactive one-command onboarding: detect MCP clients, write config, install CLAUDE.md policy, hooks, index |
| `install <agent>` | (v1.105.1) Per-agent shortcut over `init`; targets: `claude-code`, `claude-desktop`, `cursor`, `windsurf`, `continue`, `all`. `install --list` enumerates; `install --status` reports state (JSON via `--json`). **v1.107.0:** `--skills` also emits the Claude Agent Skill bundle (`~/.claude/skills/jcodemunch/SKILL.md` by default; `--skills-scope project` for project-local) |
| `install-status` | (v1.105.1) Read-only report of which clients / policies / hooks currently have jcodemunch wired; `--json` for scripting. **v1.107.0:** also reports `skills.global.present` and `skills.project.present` |
| `uninstall [target]` | (v1.105.1) Reverse `init` / `install`. Preserves user-authored hook rules and content outside our policy region; removes files only when empty after stripping. `--keep-claude-md`, `--keep-hooks`, etc. scope what's reversed |
| `watch <paths>` | File watcher — auto-reindex on change |
| `watch-claude` | Auto-discover and watch Claude Code worktrees |
| `watch-all` | Auto-discover **every** locally-indexed repo and keep it fresh; rediscovers on interval |
| `watch-install` | Install `watch-all` as a login service (systemd / launchd / Task Scheduler) |
| `watch-uninstall` | Remove the installed `watch-all` login service |
| `watch-status` | Print service state + per-repo reindex status (also exposed as MCP tool `get_watch_status`) |
| `hook-event create\|remove` | Record a worktree lifecycle event (called by Claude Code hooks) |
| `index [target]` | Index a local folder (default: `.`) or GitHub repo (`owner/repo`). One command, no init required |
| `index-file <path>` | Re-index a single file within an existing indexed folder (used by PostToolUse hooks) |
| `refresh [path]` | (v1.108.259, #395) Re-parse an INDEXED repo in bounded, resumable slices — `--max-seconds` / `--max-files` / `--pause-ms` / `--batch-size` / `--status` / `--reset` / `--ai-summaries` / `--json`. For fleets where a full re-index is a scheduled maintenance event. ⚠ Does NOT build a first index; refuses with the command that does. ⚠ Stamps `parser_generation` only after VERIFIED full-corpus coverage |
| `import-trace [--otel <path> \| --sql-log <path> \| --stack-log <path>] [--repo <id>] [--no-redact]` | (Phases 1 + 4 + 5) Ingest a runtime trace file into the runtime_* tables. `--otel` takes JSON / JSON-Lines / .gz and maps spans by `(code.filepath, code.lineno, code.function)`; `--sql-log` takes pg_stat_statements CSV or generic SQL JSON-Lines and maps queries by referenced tables + dbt/SQLMesh column metadata; `--stack-log` takes plain-text app log or JSON-Lines record set with Python / JVM / Node.js tracebacks and writes severity-tagged frame counts to runtime_stack_events. Redacts PII at the chokepoint by default. Pass exactly one source flag. |
| `import-scip <path.scip> [--repo <id>]` | (v1.108.96) Ingest a SCIP index file (compiler-verified cross-references from scip-typescript / scip-python / scip-java / scip-go / rust-analyzer; .gz accepted) into the scip_* tables. Hand-rolled protobuf reader, no deps. `find_references` then tags `compiler_verified` refs + appends compiler-only refs. Cap via `JCODEMUNCH_SCIP_MAX_ROWS`. |
| `config` | Print effective configuration grouped by concern |
| `config set <key> <value>` / `config unset <key>` | (v1.108.51) Write/clear a config key in the global config.jsonc (typed, comment-preserving, validated; `--json` for tooling) |
| `config --check` | Also validate prerequisites (storage writable, AI pkg installed, HTTP pkgs present) |
| `config --upgrade` | Add missing keys from current template to existing config.jsonc, preserving user values |
| `download-model` | Download bundled ONNX embedding model (all-MiniLM-L6-v2) for zero-config semantic search; `--target-dir` override |
| `install-pack [id]` | Download and install a Starter Pack pre-built index; `--list` for catalog, `--license KEY` for premium |
| `hook-pretooluse` | PreToolUse hook: intercept Read on large code files, suggest jCodemunch (reads JSON stdin) |
| `hook-posttooluse` | PostToolUse hook: auto-reindex files after Edit/Write (reads JSON stdin) |
| `hook-precompact` | PreCompact hook: generate session snapshot before context compaction (reads JSON stdin) |
| `hook-taskcomplete` | TaskCompleted hook: post-task diagnostics — dead code, untested symbols, dangling refs (reads JSON stdin) |
| `hook-subagent-start` | SubagentStart hook: inject condensed repo orientation for spawned agents (reads JSON stdin) |
| `hook-sessionstart` | (v1.108.255, #420) SessionStart hook: re-inject the PreCompact snapshot into MODEL context on `compact`/`resume`/`fork`. Silent on `startup`/`clear`, because an unrelated session's journal presents stale files as current focus. Also the earliest point a custom-profile transcript root can be learned (#421), so registration runs BEFORE the source gate |
| `whatsnew` | Refresh README recency block + write `whatsnew.json` from `CHANGELOG.md` (release flow) |
| `receipt` | Token-economy ledger from Claude transcripts — modeled tokens-saved + dollar value at Fable/Opus/Sonnet/Haiku rates; `--explain`, `--export csv\|json`, `--days` (rolling), `--model`. v1.108.134: `--since`/`--until` for calendar windows (local dates; `--until` exclusive) + `--by-day` for a per-day series in the JSON export. v1.108.135: `--rates` dumps the model price table as JSON (scans nothing) so consumers price from the one table instead of a drifting copy |
| `digest` | Agent stand-up briefing — composes since-last-session delta + risk surface + dead-code candidates; tracks per-repo last-seen SHA at `~/.code-index/digest_state/`; also exposed as MCP tool `digest`. v1.108.68 adds a one-line retrieval-regret summary when the ledger has clusters |
| `reflect` | (v1.108.68) Surface retrieval regret as SUGGESTED config corrections — `reflect [repo] [--project-path] [--window-days N] [--all] [--apply-weights] [--json]`. Thin CLI over the `suggest_corrections` tool; read-only (only `--apply-weights` writes, and only the tuning.jsonc sidecar) |
| `delivery` | (v1.108.69) Print durable-change delivery metrics for a window — `delivery [repo] [--window-days N] [--rework-horizon-days N] [--cost DOLLARS] [--json]`. Thin CLI over `get_delivery_metrics`; `--cost` prints the headline cost-per-durable-change (how much got done for how little). Read-only git archaeology |
| `parity` | (v1.108.111) Map migration parity between two symbol trees — `parity <source> <target> [--source-path P] [--target-path P] [--match-threshold F] [--divergence signature\|signature+body\|name_only] [--no-rename] [--no-port-plan] [--json]`. Thin CLI over `get_parity_map`: ported/diverged/unported/orphaned/added counts + dependency-ordered port plan. Read-only/plan-only |
| `health` | Print `get_repo_health` JSON to stdout (includes six-axis radar). For CI/scripting; `--radar-only` for just the radar sub-field. Used by the v1.88.0 health-radar GitHub Action |
| `file-risk` | Print per-symbol risk JSON for a file (composite score + four-axis breakdown). Used by the v0.2.0 VS Code risk-density gutter |
| `observatory build\|init` | Public OSS code-health observatory pipeline — clones, indexes, scores a configured repo list; writes static HTML + RSS + JSON to an output dir. v1.90.0; CI repo-id bug fixed in v1.90.1. Live at https://jgravelle.github.io/jcodemunch-observatory/ |
| `org-report` / `org-rollup` | (v1.108.38/39) Team SKU: record this seat's savings under its org / aggregate across seats. `org-rollup` is the licensed feature (v1.108.42 gate). |
| `license` | (v1.108.42) Check jCodeMunch license status — `license [--key KEY] [--json]`; reports licensed / evaluation / unlicensed, tier, trial days left. Gates `org-rollup` only. |
| `surface` | (v1.108.154) Print the tool-surface schema receipt (same block `get_session_stats` reports as `tool_surface`) — surface/profile, visible vs catalog counts, schema tokens, avoided, heaviest schemas. `--json` for tooling (the Console's Tool surface cost card shells it). Scans nothing. |

## Architecture Notes
- `index_folder` is **synchronous** — dispatched via `asyncio.to_thread()` in server.py to avoid blocking the event loop
- `index_repo` is **async** (uses httpx for GitHub API)
- `has_index()` distinguishes "no file on disk" from "file exists but version rejected"
- Symbol lookup is O(1) via `__post_init__` id dict in `CodeIndex`

## Custom Parsers
Tree-sitter grammar lacks clean named fields for these — custom regex extractors:
- **Erlang**: multi-clause function merging by (name, arity); arity-qualified names (e.g. `add/2`)
- **Fortran**: module-as-container, qualified names (`math_utils::multiply`), parameter constants
- **SQL**: `_parse_sql_symbols` + `sql_preprocessor.py` strips Jinja (dbt); macro/test/snapshot/materialization as symbols
- **Razor/Blazor** (.cshtml/.razor): `@functions/@code` → C#, `@page`/`@inject` → constants, HTML ids

## Env Vars
| Var | Default | Purpose |
|-----|---------|---------|
| `CODE_INDEX_PATH` | `~/.code-index/` | Index storage location |
| `JCODEMUNCH_MAX_INDEX_FILES` | 10,000 | File cap for repo indexing |
| `JCODEMUNCH_MAX_FOLDER_FILES` | 2,000 | File cap for folder indexing |
| `JCODEMUNCH_FILE_TREE_MAX_FILES` | 500 | Cap for get_file_tree results |
| `JCODEMUNCH_GITIGNORE_WARN_THRESHOLD` | 500 | Missing-.gitignore warning threshold (0 = disable) |
| `JCODEMUNCH_USE_AI_SUMMARIES` | auto | AI summarization mode: `auto` (detect provider), `true` (use explicit config), `false`/`0`/`no`/`off` (disable) |
| `JCODEMUNCH_SUMMARIZER_PROVIDER` | — | Explicit summarizer provider: `anthropic`, `gemini`, `openai`, `minimax`, `glm`, `openrouter`, `none` |
| `JCODEMUNCH_SUMMARIZER_MODEL` | — | Model name override for the selected summarizer provider |
| `JCODEMUNCH_TRUSTED_FOLDERS` | — | Roots trusted for index_folder; whitelist mode by default |
| `JCODEMUNCH_EXTRA_IGNORE_PATTERNS` | — | Always-on gitignore patterns (comma-sep or JSON array) |
| `JCODEMUNCH_PATH_MAP` | — | Cross-platform path remapping; format: `orig1=new1,orig2=new2` |
| `JCODEMUNCH_STALENESS_DAYS` | 7 | Days before get_repo_outline emits a staleness_warning |
| `JCODEMUNCH_MAX_RESULTS` | 500 | Hard cap on search_columns result count |
| `JCODEMUNCH_HTTP_TOKEN` | — | Bearer token for HTTP transport auth (opt-in) |
| `JCODEMUNCH_RATE_LIMIT` | 0 | Max requests/minute per client IP in HTTP transport (0 = disabled) |
| `JCODEMUNCH_REDACT_SOURCE_ROOT` | 0 | Set 1 to replace source_root with display_name in responses |
| `JCODEMUNCH_SHARE_SAVINGS` | 1 | Set 0 to disable anonymous token savings telemetry |
| `JCODEMUNCH_REDACT_RESPONSE_SECRETS` | 1 | Set 0 to disable response-level secret redaction (AWS/GCP/Azure/JWT/etc.) |
| `JCODEMUNCH_STATS_FILE_INTERVAL` | 3 | Calls between session_stats.json writes; 0 = disable |
| `JCODEMUNCH_PERF_TELEMETRY` | 0 | Set 1 to enable persistent perf SQLite sink at ~/.code-index/telemetry.db (per-tool latency + ok flag + repo). In-memory ring is always tracked; the env var only controls durable persistence. |
| `JCODEMUNCH_PERF_TELEMETRY_MAX_ROWS` | 100000 | Rolling cap on persisted perf rows; oldest rows trimmed in 1k-row batches once exceeded. |
| `JCODEMUNCH_RUNTIME_MAX_ROWS` | 100000 | (Phase 0) Per-repo cap on rows in runtime_* tables (ingested in Phase 1+); FIFO eviction in 1k batches once exceeded. |
| `JCODEMUNCH_RUNTIME_REDACT` | 1 | (Phase 0) Set 0 to disable PII redaction at the runtime trace ingest chokepoint. Off ONLY for offline debugging on synthetic data — never on production traces. |
| `JCODEMUNCH_RUNTIME_INGEST_ENABLED` | 0 | (Phase 6) Set 1 to enable the HTTP live-ingest endpoints (POST /runtime/otel, /runtime/sql, /runtime/stack). Requires JCODEMUNCH_HTTP_TOKEN. Off by default — write endpoints are a deliberate two-key turn. |
| `JCODEMUNCH_RUNTIME_INGEST_MAX_BODY_BYTES` | 5242880 | (Phase 6) Per-request body cap in bytes (post-decompression). Decompressed size is checked separately from on-wire size — gzip-bomb guard. Minimum 1024. |
| `JCODEMUNCH_CLIENT_ID` | basename(`sys.argv[0]`) | (v1.106.0) Friendly client name recorded in `process_locks` metadata. Auto-detected for common runtimes (claude, cursor, codex). Override for custom or wrapper runtimes so `get_watch_status.watcher_holder.client_id` surfaces a meaningful name to other processes. |
| `ANTHROPIC_API_KEY` | — | Enables Claude Haiku summaries (`pip install "jcodemunch-mcp[anthropic]"`) |
| `GOOGLE_API_KEY` | — | Enables Gemini Flash summaries (`pip install "jcodemunch-mcp[gemini]"`) |
| `OPENAI_API_BASE` | — | Local LLM endpoint (Ollama, LM Studio) |
| `OPENAI_WIRE_API` | — | Set `responses` to use OpenAI Responses API instead of chat/completions |
| `JCODEMUNCH_OPENAI_EXTRA_BODY` | — | JSON object merged into every OpenAI-compatible `/chat/completions` + `/responses` summarizer request (config key `openai_extra_body`, project-overridable). Disable a thinking model's reasoning so the output budget isn't burned on reasoning tokens, e.g. `{"chat_template_kwargs":{"enable_thinking":false}}` (#323) |
| `OPENROUTER_API_KEY` | — | Enables OpenRouter summaries (default model: `meta-llama/llama-3.3-70b-instruct:free`) |
| `JCODEMUNCH_LOCAL_EMBED_MODEL` | — | Override path to bundled ONNX model directory (default: `~/.code-index/models/all-MiniLM-L6-v2/`) |
| `GEMINI_EMBED_TASK_AWARE` | 1 | Set `0`/`false`/`no`/`off` to disable task-type hints (`RETRIEVAL_DOCUMENT` / `CODE_RETRIEVAL_QUERY`) when using Gemini embeddings |
| `JCODEMUNCH_CROSS_REPO_DEFAULT` | 0 | Set 1 to enable cross-repo traversal by default in find_importers, get_blast_radius, get_dependency_graph |
| `JCODEMUNCH_EVENT_LOG` | — | Set `1` to write `_pulse.json` on every tool call (per-call activity signal for dashboards) |
| `JCODEMUNCH_WATCH_POLL_DELAY_MS` | 1000 | (v1.108.83) Poll interval (ms) used ONLY when watchfiles falls back to polling — which it auto-enables under WSL (#356). Default raised from watchfiles' 300ms to cut idle CPU; ignored when native FS events are in use. Falls back to `WATCHFILES_POLL_DELAY_MS` if set; non-positive/garbage → default. For Linux-filesystem repos under WSL, `WATCHFILES_FORCE_POLLING=false` opts back into inotify (~0 idle CPU). |
| `JCODEMUNCH_LIVE_JOURNAL` | 1 | (v1.108.57) Set `0`/`false`/`no`/`off` to disable the live session-journal write (`<CODE_INDEX_PATH>/_session_live.json`). On by default so the out-of-process PreCompact hook can read real session state (#334); throttled ≤1/~2s, paths+queries only, no file contents. |
| `JCODEMUNCH_TOOL_SURFACE` | `full` | (v1.108.66) Tool surface selector (config key `tool_surface`; env wins). `counter` collapses `list_tools` to the 3-tool front door (`order`/`menu`/`route`) + always-present controls. Any other value (default `full`) preserves existing tiered behavior byte-for-byte — front-door tools stay hidden but callable. Composes with the `core`/`standard`/`full` tier profiles. |
| `JCODEMUNCH_PARSE_CACHE` | — | Shared directory for the content-addressed parse cache (v1.108.40). Point all seats on a multi-home-dir box at the same path so identical files parse once across seats. Unset = disabled (no caching). |
| `JCODEMUNCH_PARSE_CACHE_MAX_ROWS` | 50000 | (v1.108.41) Row cap for the shared parse cache; FIFO-trimmed oldest-first by rowid after each write (stale-content/stale-version rows go first). `<= 0` disables the cap (unbounded). |
| `JCODEMUNCH_ORG_ID` | — | Org identifier for the team-SKU rollup (`org-report` / `org-rollup`) |
| `JCODEMUNCH_ORG_ENDPOINT` | — | Org host URL that `org-report` POSTs seat savings to (`/org/report`); unset = record locally |
| `JCODEMUNCH_ORG_INGEST_ENABLED` | 0 | Set 1 on the org host to accept `POST /org/report` (two-key turn with `JCODEMUNCH_HTTP_TOKEN`) |
| `JCODEMUNCH_LICENSE_KEY` | — | (v1.108.42) jCodeMunch license key (config key `license_key`). Gates the `org-rollup` team feature ONLY; everything else is free. Validated online vs `validate.php` (sticky-offline cache; 14-day grace for new orgs). **Requires a multi-seat tier — Studio or Platform** (v1.108.43); Builder doesn't unlock org-rollup. Check with the `license` CLI. |
| `JCODEMUNCH_INDEX_CACHE_TTL` | 0 (off) | (v1.108.172) Seconds an unused hydrated index may sit in the in-memory cache before being released. **OPT-IN: 0/unset/garbage = disabled = today's behavior exactly.** ⚠ **Do NOT default this on** — cold hydration of a 665k-symbol index was measured at 7.5-11.4 min (#370), so evicting during a quiet spell hands the next query that bill. For hosts whose MCP client leaks stdio servers (#375: 25+ instances, ~17 GB), where each idle process otherwise sits on its own cache. Swept on access, no timer thread. |
| `JCODEMUNCH_PROVIDER_BUDGET_SECONDS` | 30.0 | (v1.108.182) Wall-clock ceiling on ONE context provider's `detect()`+`load()`. Discovery runs before a single file is indexed, so an unbounded provider takes the whole index down with it (#375). On overrun the provider is skipped and NAMED in `providers_skipped` + `warnings`. `0`/negative = no ceiling (pre-.182 inline behaviour). ⚠ **A watchdog stops the CALLER waiting; it cannot stop the work** — Python cannot preempt a thread, so the abandoned provider keeps burning CPU until it finishes or polls `budget_expired()`. Only the Express walk polls it so far. |
| `JCODEMUNCH_PARSE_BUDGET_SECONDS` | 20.0 | (v1.108.182) Per-file wall-clock ceiling on `parse_file`, via `parse_file_budgeted`. On overrun the file is skipped and named in the index result's `warnings` instead of the run hanging. ⚠ **Armed only at or above 128 KiB** (`_PARSE_WATCHDOG_MIN_BYTES`) so the common path stays inline — a 2 KB file that takes 20s is a bug to see, not to paper over. `0`/negative disables. Same no-preemption caveat: tree-sitter is C code. |
| `JCODEMUNCH_MAX_FILE_SIZE` | 512000 | (v1.108.193, @dkiaulakis) Per-file byte cap for indexing (config key `max_file_size`; **settable per-project in `.jcodemunch.jsonc` as of v1.108.197 — before that the project file was parsed and then ignored**). ⚠ **This was the ONE limit of three with no route at all** — its neighbours `max_index_files`/`max_folder_files` each had a resolver, this was hardcoded. **Default deliberately UNCHANGED**; this is an escape hatch. ⚠ A file over the cap is `too_large`, which is now **WITHHELD** (real+current+wanted) rather than an ordinary exclusion, so it makes coverage `complete: false` and **refuses absence claims**. |
| `JCODEMUNCH_RESPECT_CACHEDIR_TAG` | 1 | (v1.108.270) Honour the Cache Directory Tagging Specification (<https://bford.info/cachedir/>): prune any directory holding a `CACHEDIR.TAG` whose **first 43 bytes** are the spec signature (config key `respect_cachedir_tag`). ⚠⚠ **The signature is VERIFIED — a file merely NAMED `CACHEDIR.TAG` excludes nothing.** A name-only check is an assertion about one instance of the property instead of the property, which is the exact defect class this answers. ⚠ The only exclusion rule here **declared by the WRITER** rather than listed by us, so a tool that drops a cache in your tree is honoured without jcm knowing its name, and it covers caches that are **not dotted** (which a dot-dir rule cannot). Counted as `cache_dir` in `discovery_skip_counts`; **NOT a withheld reason**, so absence stays citable — a tagged dir is derived data by its writer's own declaration, i.e. corpus definition like `gitignore`. Only an explicit `false` disables it. ⚠ Local walks only; `index_repo` is deliberately uncovered because validating the signature needs blob CONTENT the tree listing does not carry. |
| `JCODEMUNCH_RESPONSE_MAX_BYTES` | 1048576 | (v1.108.257, #425) Ceiling on a SINGLE MCP tool response in bytes, enforced in a wrapper AROUND the `call_tool` dispatcher (config key `response_max_bytes`). ⚠⚠ **This is a RESPONSE limit, deliberately NOT `max_file_size`** - before it existed, an INDEXING cap in another subsystem bounded reply size by coincidence, so raising that key to cover a large generated file silently raised the maximum reply. ⚠ Over the cap the call REFUSES with a structured error naming size, limit and the key that moves it; it never truncates, because a shortened body is indistinguishable from a complete one. `0` disables; any other invalid value falls back to the default so a typo cannot uncap the server. |
| `JCODEMUNCH_HEARTBEAT_SECONDS` | 30.0 | (v1.108.189, #383) Elapsed wall-clock seconds between heartbeat log lines when the client sent **no `progressToken`** — the MCP spec makes progress notifications the client's opt-in, so the fallback signal goes to the log instead. Emitted at **WARNING** (the default `log_level`, or nobody sees it) and **only after the first interval elapses**, so a run finishing inside the window is byte-for-byte as silent as before. ⚠ **Garbage parses to the DEFAULT, not to 0** — a typo must not reintroduce the silence this exists to fix. `0`/negative disables. |
| `JCODEMUNCH_EMBED_MATRIX_CACHE` | 1 | (v1.108.223, #399) Set `0`/`false`/`no`/`off` to stop RETAINING the decoded embedding matrix between queries. ⚠ **It does not disable the fast path** — the matrix is still built per call and scored in one vectorised pass, so only the SQLite decode is re-paid. On by default because the cache is what turns a ~2 s semantic query into ~3 ms; bounded to 2 repositories (~46 MB each at 30k x 384 float32) and dropped on every write to the store. Process memory only: nothing written, no network, dies with the process. Disclosed in README's "Background behavior". |
| `JCODEMUNCH_SCIP_MAX_ROWS` | 200000 | (v1.108.96) Row cap for `scip_edges` / `scip_unmapped` (compile-time evidence from `import-scip`); FIFO-evicted oldest-first in 1k batches. Negative disables the cap; env-only, deliberately not a config key. |
| `JCODEMUNCH_LAUNCH_ID` | — | (v1.108.152) Opaque host-supplied launch token echoed back as `launch_id` in the `munch://runtime/identity` resource (#371). Fallback: suite-generic `MUNCH_LAUNCH_ID`. Omitted from the payload when unset. Env-only, not a config key. |

## PR / Issue History
See `git log` and CHANGELOG.md. Active contributors: MariusAdrian88, DrHayt, tmeckel, drax1222, oderwat, thomasmodeneis, gokhanozdemir, horknfbr.

### Codex tool-surface benchmark (`benchmarks/codex_surface/`) — NEGATIVE result

⚠ Shipped in 1.108.271. Kept here rather than in the rotation because it is a
STANDING warning about a measurement, not a release note that ages out.

⚠⚠ **Do not quote the arm numbers; the honesty gate fired.** Four arms x three
repeats on FastAPI at a pinned commit, answering an
[r/codex benchmark](https://www.reddit.com/r/codex/comments/1vjfepe/) that put
jCodeMunch at **+28.45% on Codex** and **-3.34% on OpenCode**. Largest arm
difference 568,617 tokens against a baseline varying against ITSELF by
1,143,229. Directions were incoherent too (`full`, carrying 24,007 tokens of
schema, came out CHEAPER than baseline). The hypothesis is **untested, not
disproven** — the instrument cannot resolve an effect that size.

⚠⚠ **The finding that outlived the arms, and it corrects a claim this project
made: 86% of baseline input is CACHED.** The schema block is stable across
requests, so it is paid at full rate roughly ONCE and at cache-read rates after.
Any framing of "24,007 tokens in every request" is wrong, and that framing was
used here before measuring. **The fixed-cost story is a WEAKER explanation for
the r/codex result than the raw number suggests, not a stronger one.**
`--surface-only` still measures the schema exactly (90 tools / 24,007 tokens at
default `full`, 6 / 1,030 at `counter`) and needs no API credits; what it does
not measure is what that costs in practice.

⚠ Design flaw recorded so nobody repeats it: summing per-invocation input across
a RESUMED conversation counts accumulated context on every step, so the total is
dominated by how much the agent read early on, which compounds.

**In flight 2026-08-13: #441 + #442 (@rknighton), ranking-ledger write path.**
Same path .272 touched. Unreleased; see CHANGELOG `[Unreleased]`.
⚠⚠ **The reusable lesson is that measuring the SAFE fix first is what chose the
risky one.** #442 has an obvious low-risk shape — remember the schema is ready,
skip the eight `IF NOT EXISTS` statements, keep the per-write open/close, no
connection lifetime to manage. **Measured: it captures 2% of the available
saving.** The other 98% is the open/close itself. Had that not been measured, the
cheap fix would have shipped, looked principled, and delivered nothing.
⚠ Shipped path is **3.455ms vs 16.615ms**, a **79%** cut, agreeing with his 82%
(absolutes are machine-local; the ratio transfers). ⚠ `check_same_thread=False` is
REQUIRED (searches dispatch via `asyncio.to_thread`, so a cached connection
outlives its opening thread) and SAFE only because every caller holds
`_State._lock` — recorded at the call site because a future edit could quietly
invalidate it.
⚠⚠ **Caching a connection introduces TWO silent failure modes the report did not
name, and the benchmark found the first by crashing**: a stray `close()` poisons
the cache so every later caller gets a dead handle (telemetry off, every write
still reporting success), and a DELETED db file gets written into an unlinked
inode forever (pre-caching, the next event just recreated it). A liveness probe
catches only the first; the `exists()` check is what catches the second. Together
**0.344ms, 2.1%** of the pre-fix write. ⚠ **Windows cannot produce the orphan case
at all** (it refuses to unlink a file with an open handle) — the end-to-end test
is POSIX-only and says so, with a portable unit test for the predicate. **Do not
read that skip as cross-platform coverage.**
⚠ Suite at this point: **7763 passed, 9 skipped, 0 failed** + `ruff check src/`
clean. Reconciled by same-tree collect: 7772 total, 7753 with
`test_v1_108_276.py` ignored (= its 19), so nothing else moved. The 9th skip is
the POSIX-only orphan test. **Fold this into the `Tests:` line at release**, not
before — it is not a released count yet.
⚠ #441 pre-existing rows keep `NULL` = UNKNOWN and are NOT backfilled; inferring
`count == len(returned_ids)` is the defect itself. ⚠ **He filed it against his own
earlier claim** in Discussion #430 and caught it on re-verification. ⚠ Severity
checked and it is genuinely analysis-only: `regret` and `ledger_trust` read
`returned_ids` only for emptiness/>1, and truncation starts above 50.

**Merged 2026-08-13: #439 (@JayceeB1) Windows drive-root child Git repos, closing
#438** — plus **#453 fixed on top, test-only.** Both ride the next release; no
version bump. ⚠⚠ **The reusable lesson is one sentence and it cost most of a day:
a mock broad enough to satisfy an assertion can be broad enough to bypass what the
assertion is about.** It fired three times in three different costumes.

⚠⚠ **My own review advice on #439 was WRONG and nearly shipped a hole.** I told
them `_path_safety_part_count(path) == 2` subsumed `not drive.startswith("\\\\")`.
It does not: the helper is a **DEPTH** rule and the UNC clause is a **SCOPE** rule.
A UNC share root has ONE real part and the helper adds one for the
`\\server\share` anchor, so it computes to **exactly 2 — the same as `C:\repo`**.
With the clause gone, `\\server\share` holding a `.git` is admitted, handing the
indexer a whole file server through the guard that exists to stop that (#321/#322).
⚠ **`len(path.parts)` genuinely WAS a redundant depth notion — that half was
right.** The error was concluding that a second condition mentioning the same
variable must therefore be redundant too. **Check what a predicate is FOR, not what
it reads.** Restored with both clauses, the reason recorded in the docstring, and
the bad advice corrected in the CHANGELOG rather than deleted
([[feedback_a_fix_comment_is_not_evidence_about_its_siblings]] — same reason).

⚠⚠ **The regression test for it was ALSO wrong, in a way that PASSED.** It patched
`os.path.exists` to a blanket `True`, which also answers `_is_container()`'s
`/.dockerenv` probe — that drops `_MIN_PATH_PARTS` from three to two, so `2 < 2`
skips the guard entirely and `_is_shallow_windows_git_root` **was never called**
(proven by spying on it: zero invocations). ⚠ **The tell was that it failed
IDENTICALLY with and without the fix.** A test failing on both sides is as
uninformative as one passing on both sides, and it is the cheaper tell to notice
because you are already looking at a red. **Run the non-vacuity pass even when the
test is currently failing.**

⚠⚠ **#453's tripwire had the same disease a third time: it could not fire.**
`_no_real_access_under` raised `AssertionError`, and every read site it guards is
wrapped in a bare `except Exception` in production, so a deliberately re-broadened
mock **passed cleanly with the guard installed**. Now derives from
`BaseException`. **A guard that cannot fire is worse than no guard, because it
reads as coverage.** Always prove a new guard fires by breaking the thing it
watches.

⚠ **#453's actual root cause was NOT the one I inferred**, and the difference
mattered. I traced seven network `read_text` calls (`detect_framework` probing
manifests under a blanket `Path.exists=True`) and took them for the culprit; they
are real network I/O in a unit test but are **swallowed by production's
`except Exception` and never failed anything**. The failure was
`resolve_index_identity` → `folder_path.is_file()` (`storage/git_root.py:160`),
never patched. **Only pulling the real CI traceback settled it** — attempt 1 of a
rerun run, via `gh api .../runs/<id>/attempts/1/jobs`, because **a rerun flips the
run's conclusion to success and hides the failure from `gh run list`**.
`Path.is_file()` swallows ENOENT-class errors (a box with no such share) but
propagates `WinError 64` (a runner with live-but-failing networking) — same test,
opposite outcomes, decided by whose network answered.

⚠ **Process note: `git checkout -- <file>` destroyed uncommitted work TWICE**
during the non-vacuity passes, because the falsification edit and the fix lived in
the same file. Copy the fixed file to the scratchpad first and restore from that.

**Merged 2026-07-25: #379 (@oderwat) Gleam import extraction** — Gleam was already
in `LANGUAGE_REGISTRY`, so symbols extracted but the import graph stayed EMPTY,
leaving `find_importers`/`get_blast_radius`/`get_dependency_graph` silently blind
on Gleam projects. Same shape as the week's verdict work: capability present,
wiring absent. Verified against a TRIAL MERGE onto current main (branch-green is
not merged-green), 210 neighbouring import/language tests green. Landed AFTER the
1.108.170 release commit, so it rides the NEXT release, not that one.

**Merged 2026-07-25: #378 (@zuoYu-zzz) TOML symbol extraction** — tables → `type`,
array tables → `class`, key-value pairs → `constant`. Merged rather than
review-round-tripped, then fixed on top in **`f0eda7b`**. ⚠ **The defect worth
remembering: `_extract_key` scanned a `dotted_key`'s DIRECT children for
`bare_key`/`quoted_key`, but tree-sitter-toml nests `dotted_key`
LEFT-RECURSIVELY** (`[tool.ruff.lint]` = `dotted_key(dotted_key(tool, ruff),
lint)`), so every segment but the last was dropped. **Two-level paths worked,
which is exactly why it read as correct** — the bug only shows at three-plus, and
on jcm's OWN pyproject.toml `[tool.hatch.build.targets.wheel]` came back as
`wheel` with signature `[wheel]`, **a header that appears nowhere in the file**
(search_symbols would have handed an agent fabricated source text). Fix returns
path SEGMENTS and recurses; building from segments also fixed `name`/
`qualified_name`, which the PR set to the same value (now leaf / full dotted path,
matching every other extractor). New test asserts three- AND five-deep tables plus
a signature-occurs-in-source check, proven non-vacuous. **The PR's own test used
only single-segment headers, so nothing in the suite could have caught it** — the
general lesson for any new nested-grammar walker. Rides the next release with #379.

**Closed 2026-07-25: #380 Atlas Cloud summarizer** (@binyangzhu000-sudo). Closed on
DEMAND, not quality: CLA unsigned (hard blocker), and the capability is fully
reachable today via `OPENAI_API_BASE` + `SUMMARIZER_PROVIDER=openai` since Atlas
Cloud is OpenAI-compatible. Cost of merging was **8** permanent env-var spellings
(`ATLASCLOUD_`/`ATLAS_CLOUD_` × `API_KEY`/`API_BASE`/`BASE_URL`/`MODEL`) plus 3
aliases, permanent under the 1.x no-removal contract. ⚠ **Do NOT re-close a future
one of these "we don't take branded providers"** — MiniMax/GLM/OpenRouter are
exactly this shape and already merged; the comment concedes that on the record.
The bar is a user asking, same as platform installers. It correctly added
atlascloud to `_PAID_CLOUD_PROVIDERS`, so the money-safety guard was respected.
**Tracker state 2026-07-28: ZERO open issues, ZERO open PRs.** Verified against
`gh issue list --state open` / `gh pr list --state open`, with an
`--state all` query alongside to prove the empty result was not a failed query
([[feedback_empty_cli_query_is_not_evidence]]).

⚠⚠ **DO NOT quote a tracker count from this file — re-run the query.** The line
that used to live here said "Open issues: #375, #377. Open PRs: #387 and #388"
while a paragraph twelve lines below it recorded #388's own close. **It was
internally contradictory and it was believed anyway**, which is how a stale
"#375 ONLY" got written into this file on 2026-07-28 for an issue closed the day
before. A count is the one fact here with a guaranteed expiry date.

**Closed 2026-08-05: #414 (@MotoMato85) byte offsets slicing a decoded str in
16 extractors** — shipped as 1.108.244, see Current State. ⚠ **The report is the
best-instrumented one this project has received**: an AST audit finding exactly
35 `source` subscripts and classifying 34 as byte-offset plus the ONE correct
character-domain case, a per-symbol drift table proving the shift EQUALS the
extra UTF-8 byte count, a 118-function whole-repo measurement (105 wrong before,
1 after — and he diagnosed the survivor as an unrelated tree-sitter `ERROR`
node), and a fix proposal with an AST argument for why substituting `source` is
behaviour-preserving. **Every claim I checked reproduced byte-identically.**
⚠ **His proposed helper had one flaw worth remembering**: its
`for cut in range(4)` decode loop trims trailing bytes until a decode succeeds,
which for a slice containing an INVALID byte in the middle silently returns the
prefix and DROPS the rest. Shipped version only trims when the bad run reaches
the END of the chunk. **A test I wrote for the degradation path caught it**;
neither of us would have caught it by reading. ⚠⚠ **The reusable lesson is that
fixing the producer left the DATA wrong and the obvious remedy did not work**:
"re-index" is a no-op here, because the corrupt rows sit in files that never
changed ([[feedback_fixing_a_producer_does_not_fix_its_history]]). Hence
`PARSER_GENERATION`, and it must be checked BEFORE every early-returning fast
path. ⚠ A pure-ASCII fixture cannot fail on this class at all, which is why it
survived every existing parser test.

**Closed 2026-08-05: #413 (@LuigiNicaPRO) a silent full rebuild replacing a
requested incremental** — shipped as 1.108.243, see Current State. ⚠ **The
reusable lesson is that the READ side and the WRITE side of one store drifted.**
`inspect_index` was built in PR #291 specifically to discriminate the causes
`load_index` collapses into `None`, four read-path tools adopted it, and the two
INDEXING tools kept a hand-written branch that named one cause for seven. His
own grep is the diagnostic worth copying: `loadab` / `load_error` /
`index_status` / `sqlite_corrupt` / `index_present` returned **zero** hits in
`index_folder.py` while `existing_index is None` was present. ⚠ **He measured
the harm honestly and DOWN**: on his repos the substituted rebuild costs about a
second, and he said so unprompted rather than inflating it. The defect is the
undiagnosability, not the time. ⚠ **We shipped his options (1) and (2) and NOT
(3)** — an `on_unloadable_index="error"` parameter is permanent surface under the
1.x no-removal contract, and he stated (1)+(2) covers his case. Demand-driven, as
with platform installers.

**Closed 2026-08-04: #412 (@rknighton) git_sha verification accepted a truncated
cache** — shipped as 1.108.235, see Current State. ⚠ **The reusable lesson is
about WHERE it was measured, not about the comparison.** His repro exercised
`_slice_matches` directly, which is the layer his own #401 patch had edited. At
`get_symbol_source`'s entry point the sibling `content_verified` already returned
`False` on those caches, so the served response was contradictory rather than
uniformly wrong. **Neither view alone is the whole answer, and the tool response
is the one that describes what a caller experiences**
([[feedback_verify_at_the_users_entry_point]]). ⚠ **This is the first finding of
his that got LESS severe on inspection** — the standing note says he understates,
and #411 was the first time verifying upward came back neutral. Two in a row now
land off that pattern: **check every time, report whichever way it lands, and do
not reach for the expected direction.**

**Closed 2026-08-04: #411 (@rknighton) test config isolation** — `_run_config`
in `tests/test_v1_108_194.py` scrubbed `JCODEMUNCH_MAX_FILE_SIZE` but left the
subprocess reading the developer's real `~/.code-index/config.jsonc`, so both
`max_file_size` assertions failed on any box with the key set. Reported WITH a
`git apply --check`-verified patch; applied as written in `5a3ee39`.
⚠ **`TemporaryDirectory`, not the `mkdtemp` used elsewhere in that file** — the
config reporter WRITES a `config.jsonc` into the directory it is pointed at, so
an uncleaned temp dir per call accumulates. That is the whole reason the patch
costs a reindent, and it is the part a later "simplification" would undo.
⚠ **His severity framing was checked upward and HELD, which is the notable
part** — the standing note on this reporter is that he understates, so both
larger readings were tested and came back NEGATIVE. (1) Not a production
precedence bug: config beats env for `max_file_size` and the resolver agrees,
but `max_folder_files` / `max_index_files` behave identically, so .193's key
follows its siblings. (2) Not wider than one file: `test_surface_cli.py` is the
only other CLI-shelling test without `CODE_INDEX_PATH` isolation and it PASSES
under a hostile config because its assertions are shape-based, not value-based
(latently exposed if anyone adds a value-based one); `test_watch_all.py` is
isolated by argument (`watch_all.py:48` honours `storage_path`).
⚠ **The observation worth keeping: the file's docstring says it guards the #375
failure mode, so the test protecting the large-file escape hatch was broken by
USING the large-file escape hatch.** Reaching it at all required being exactly
the user it was written for, which is why it went unseen on every dev box that
had not capped out on a large repo. Same family as jdata's `test_v1_15_0` /
`test_v1_16_0` false-greens reading the real `~/.data-index`. Test-only, no
version bump; rides the next release.

Closed this session: **#390** (@lazy-geeek, its own repro already fixed by
`.194`), **#391** (@amarakramali, rewritten as 1.108.197), **#387** (@nyxst4ck,
rewritten wider), **#377** (P3 remainder + P4 to `ROADMAP.md` with close
conditions and @mightydanp's credit, same treatment as #385/#386). **#375** and
**#388** were already closed 2026-07-27.

⚠⚠ **PROCESS FAILURE WORTH NOT REPEATING: #388 fixed #384 and was opened
2026-07-27 06:51 UTC. We shipped our own .189 fix and closed #384 at 12:56 UTC
having NEVER LOOKED AT OPEN PRs — the cross-reference sat on #384's timeline the
whole time. CHECK `gh pr list` BEFORE WRITING CODE ON AN ISSUE.** Their fix then
went `CONFLICTING/DIRTY` because .189 rewrote the same functions. Resolved by
PORTING the gap they covered and we missed (`maybe_takeover`) in **v1.108.190**
with credit in the CHANGELOG, release notes and close comment, rather than
asking a pre-empted first-time contributor to both rebase onto our version of
their fix AND sign a CLA. **#388 closed 2026-07-27.** Cleaned up in
v1.108.189 on a standing rule jjg set: **an issue opens when work STARTS or when
a USER is BLOCKED** — an issue is a problem to fix or a feature to build, not a
to-do list. **#383 and #384 are FIXED** (see Current State); **#385/#386 (evidence
Phases 5 and 6) were CLOSED and moved to `ROADMAP.md`** — accepted design with no
start date and an unmet dependency is a plan, not an issue. ⚠ **Closing them is
NOT a rejection of @mightydanp's design and the close comments say so explicitly;
credit and close conditions moved verbatim.** ⚠ **The convention that GENERATED
the clutter was our own — "new scope gets its own close condition", cited in
#385's body. It is right for scope being WORKED and wrong for scope PARKED.**
Remaining: **#375** (needs a re-run from @dkiaulakis at >=1.108.182, not code) and
**#377** (down to two concrete Phase 2 P3 edges @mightydanp pinned 2026-07-27:
an absence receipt still links a MUTABLE `absent:<sha>` key `note_absence` can
overwrite across snapshots, and validation vs rendering do two SEPARATE receipt
lookups instead of one atomic snapshot).

**#375 (index_folder silent 1800s+ on Linux) — REOPENED 2026-07-26, and the
blocker is a RE-RUN, not code.** Closed 2026-07-26 on our own measurement after
five releases; @dkiaulakis re-ran at 1.108.176 and the SAME `tools/eidos` subtree
took **268s SIGTERM'd vs a 240s baseline at .169 — no improvement**, which is
exactly the condition the close comment said would reopen it. ⚠ **No py-spy this
round: ptrace is restricted in his sandbox and his agent correctly declined to
grant itself CAP_SYS_PTRACE mid-task.** ⚠ **The 5400s full-repo number is a
CLIENT-side MCP timeout and does NOT prove the server job stopped** — he flagged
that himself; he runs 10+ concurrent stdio servers and had no safe way to
identify his own process. What .176 DID deliver, in his words: "we can now see
the problem we could not previously see" — `index_coverage` read ABSENT before
and now reports a number plus `index_stale: true (git_head_lag)`. **The freshness
half stands; the stall is a separate axis.** v1.108.182 shipped three bounds in
response (provider-discovery budget, walk pruning at `iter_source_files`,
per-file `parse_file_budgeted`), two of them his own twice-proposed suggestions.
⚠ **STATED LIMIT, do not overclaim it: a watchdog stops the CALLER waiting, it
cannot stop the WORK** — Python cannot preempt a thread and tree-sitter is C, so
an abandoned parse keeps burning CPU. It makes the index finish and the gap
visible; it does not cap CPU. Sub-problems: **A -> #383, FIXED in .189. B closed
not-a-defect** (default `log_level` is WARNING, so a healthy run emits nothing).
**C fixed in .176** (a partial index no longer reports itself fresh; `complete`
is TRI-state and pre-.176 indexes report `null`, NEVER `true` — re-index or the
signal is not there to see). **D near-ruled-out** (every `indexwrite` acquire
passes `wait_seconds=60.0` and RAISES naming the holder, so it cannot present as
unbounded silence). **The double-index finding -> #384, FIXED in .189.**
⚠ **Next action is a PING, not a patch.**

**Closed 2026-07-26: #382 "Old tree sitter dependency?" (@kecsap)** — asked why we
pin `tree-sitter-language-pack>=0.7.0,<1.0.0` when "other code parser MCP tools
happily use >= 1.0.0". Tested 1.13.3 against the full suite before answering;
the pin STAYS, and the rationale now lives as a comment on the dep itself so this
is not re-derived. ⚠ **The load-bearing reason: 1.x STOPPED BUNDLING GRAMMARS.**
The wheel ships a single `_native.pyd` and an empty bindings dir; `get_parser`
downloads the grammar from a remote manifest into `%LOCALAPPDATA%\tree-sitter-
language-pack\v<ver>\libs` on first use (proven by watching that cache go 0 -> 67
shared libs while walking our language list). **That is runtime network access plus
executable-writes-to-disk in a tool that advertises itself as read-only and local,
and it breaks airgapped installs outright** — i.e. exactly the class of undisclosed
persistent/network behavior that caused the PyPI quarantine, so it could never ride
a dependency-housekeeping commit anyway. Two smaller blockers: **`autohotkey`,
`ejs`, `verse` do not exist in 1.x** (`DownloadError: not available for download`),
so bumping silently drops three languages; and **the nim grammar was swapped for a
different upstream** (`source_file/proc_declaration/identifier` ->
`module/stmt/routine/symbol/ident`), so our nim extractor returns zero symbols.
Suite on 1.13.3: **5812 passed, 12 skipped, 1 failed** (`test_nim_parsing`, and
only that). ⚠ **There is NO API incompatibility to cite — we use exactly one symbol
from this package, `get_parser`** — so do not argue the pin on API grounds; the
blockers are all behavioral. ⚠ **Unrelated pre-existing pathology found while
testing, NOT a 1.x regression and NOT filed: `get_parser("cobol").parse(b"x")`
hangs indefinitely on BOTH 0.13.0 and 1.13.3.** A 1-byte input, no timeout.
Unreachable today (we only feed it real `.cbl` files) but it invalidated the first
version of the compatibility harness, so any future per-language sweep must resolve
parsers WITHOUT parsing pathological input.

**#381 (MCP Toplist badge) CLOSED by jjg** — 120 identical drive-by PRs from that
author; the badge renders "Top 1% of 81,432", not the rank the PR body promised,
and it is live third-party-controlled content in a README that also renders on PyPI.

## Issue + release policy (2026-07-28)

**1. One issue, one verdict.** A multi-finding report gets SPLIT at triage into
one issue per finding, cross-linked, credit on each. Nothing is dropped and no
detail is discouraged. The reason is closure mechanics: a 4-finding issue closes
only when the last one settles, so three finished fixes sit behind one
unfinished conversation and the tracker cannot say which is which.

⚠ **This is the correction to a mistake we made deliberately.** On 2026-07-27 we
CONSOLIDATED five jdoc issues (#80/#89/#90/#93) into one gate, #95. It cut the
open count from 5 to 1 and manufactured a single artifact with the power to
block a release. **Tracker-tidiness and granularity pull in opposite directions;
do not optimize the count.**

**2. A release is NEVER blocked on an open issue**, including a verification we
asked for. Done + tested + green ships on schedule, carrying a plain-language
verification-status line (the #95 disclosure sentence is the template; it is
deliberately weaker than a sign-off and the changelog must never blur the two).
Late re-verification counts IN FULL and is announced retroactively. Nothing
expires. **Every timebox names its default action** ("verification by X, or Y
ships with disclosure Z"); a date with no stated consequence is a wish.

⚠ **The point is that a reviewer's thoroughness must never become a veto.** If
being careful can stall a release, careful review becomes expensive to accept,
which is backwards.

**3. A contributor's PR is never the only path.** Timebox it and keep our own
path warm (#388 taught this the expensive way).

**3a. An unsigned CLA gets TWENTY-FOUR HOURS, not two weeks** (set 2026-08-14
after #443 was given until 08-20). CLA Assistant prompts automatically the moment
a PR opens and signing takes about 30 seconds, so the window is sized to the
task. A longer one does not buy a contributor anything — it parks a finished,
green, reviewed fix behind a form.

⚠⚠ **The window is only fair BECAUSE the default action preserves credit.** At
expiry we implement the fix ourselves and credit them in the CHANGELOG, the
release notes and the close comment. So the 24 hours decide whose COMMIT it is,
never whether they are credited and never whether the fix ships. Quote the
default in the same comment as the deadline — a 24-hour clock with an unstated
consequence reads as a threat, and it is not one.

⚠ **Do not shorten a timebox already posted.** State the new window on new PRs.
A public promise to a contributor outlives the policy that produced it, and
retracting one to save six days costs more than the six days.

⚠⚠ **Do NOT answer "an issue is stuck" with aggregate stats.** Measured
2026-07-28: jcm median 0 days to close (80 issues, 70 within a day, 2 ever past
a week); jdoc median 1 day. **Those numbers are TRUE and they are NOT a
response.** jjg: a fraction of an eyelash commands full attention and impairs
binocularity; "it is a small fraction of your body" helps nobody. The cost of a
blocked issue is CONCENTRATED, not distributed. Design the fix at the OUTLIER
(policy 2), never at the median. See
[[feedback_dont_answer_pain_with_aggregates]].

Surfaces: `CONTRIBUTING.md` ("One issue, one verdict" + "A release is never
blocked on an open issue") and `.github/ISSUE_TEMPLATE/` (bug_report,
multi_finding_report, config.yml pointing parked design at ROADMAP.md).

⚠ **CONTRIBUTING.md is now IDENTICAL suite-wide** (jcm/jdoc/jdata differ only by
product name, repo slug, and jcm's extra quality-gates section). Two pre-existing
bugs fell out of normalizing it: **the documented install command
`pip install -e ".[test]"` was WRONG IN ALL THREE REPOS** — no repo declares a
`test` extra; dev deps live in a PEP 735 `[dependency-groups]` block, so the
FIRST command a new contributor ran failed. And jcm's
`README.md#license-dual-use` anchor pointed at a heading that does not exist
(`## License`). ⚠ **CI installs with `uv sync` and never runs the command the
docs give a human**, which is why this survived: the thing we test is not the
thing they do.

## Maintenance Practices

1. **Document every tool before shipping.** Any PR adding a new tool to `server.py`
   must simultaneously update: README.md (tool reference), CLAUDE.md (Key Files),
   CHANGELOG.md, and at least one test.
2. **Log every silent exception.** Every `except Exception:` block must emit at
   minimum `logger.debug("...", exc_info=True)`. For user-facing fallbacks (AI
   summarizer, index load), use `logger.warning(...)`.
3. **CHANGELOG.md** is the authoritative version history — update it with every release.
4. **Never hand-type a jCodeMunch benchmark number.** The comparison harnesses
   (`run_rag_baseline.py`, `run_odysseus_compare.py`) read
   `benchmarks/jcm_reference.json`, written by `run_benchmark.py --reference`.
   ⚠ **The failure this closes was invisible for four months:** our side was a
   2026-03-28 constant while the other side of every ratio was re-measured each
   run, so published ratios drifted on their own. Re-measuring moved all three
   per-repo figures AGAINST us and flipped a published winner (gin: `jcm 1.2x
   leaner` → `RAG 1.1x leaner`). ⚠ **A repo outside the artifact renders "not
   measured" — there is deliberately no estimator.** The removed one allocated
   our cost proportionally to repo size, i.e. it assumed the opposite of what we
   claim. `tests/test_benchmark_reference.py` fails on a returning `JCODEMUNCH_*`
   constant and asserts the estimator absent BY NAME. ⚠ **FOUR artifacts mirror
   one run** — `results.md`, `METHODOLOGY.md`, README, and
   `benchmarks/provenance/measured.json`. Re-syncing three and missing the
   fourth failed `test_provenance.py`, **inside the known 12 local-ONNX env
   failures**. `--reference` now rewrites the provenance block itself; two
   committed artifacts disagreeing is the same defect in a different costume.
   ⚠ **v1.108.222: the corpus is PINNED by upstream commit** in
   `benchmarks/tasks.json`, and `--reference` refuses to publish a number
   measured against an unpinned, drifted, or unknown-completeness corpus. **A
   fifth artifact now mirrors the run: `benchmarks/REPRODUCING.md`**, and a test
   fails if it does not name every pinned SHA. ⚠ **Never state a repo's file
   count as a property of the repo** — it is a property of the INSTALLATION
   (grammar pack, size limits, skip patterns), which is the whole point of the
   .221 capability certificate. Say which commit, and let the count live in the
   artifact beside the SHA that produced it.
5. **Keep `Current State` to the 3 newest releases.** It is a pointer, not a second
   changelog. On each release, add the new entry and drop the 4th-oldest — the detail
   already lives in CHANGELOG.md. (2026-07-25: this section had grown to 157 entries /
   ~233k chars, loading ~58k est. tokens into every session under this directory.)
6. **A CI step that produces a PUBLIC verdict is product surface — test its text.**
   `tests/test_health_radar_action.py` opened by asserting that the Action's shell
   and YAML steps "can only be exercised by running the Action in a real CI
   environment", and under that exemption
   `git fetch origin "$BASE" --depth=1` sat unread in the base-checkout step.
   ⚠ **`--depth=1` does not merely limit a download — against an already complete
   clone it SHORTENS it**, writing `.git/shallow`. `churn_surface` is
   `complexity x log(1 + churn)` with churn counted by `git log --since=<N> days
   ago`, so the base saw ONE commit, scored every file at churn <= 1, and came
   back artificially healthy. ⚠⚠ **Measured 2026-08-10 at a single commit,
   identical tree hash both sides: shallow 82.2 (B), full 75.5 (C), and
   `churn_surface` the only axis that moved.** The same commit graded B against
   itself. Every PR was charged for the gap, publicly, on the contributor's own
   thread. **Cannot execute it is not cannot check it** — the guard that closes
   this reads step text, which is weaker than running the Action and is still
   exactly what was missing.
7. **`confidence` is certainty language; ship a stop rule beside it.** A score
   says how sure we are, which invites the caller to go get surer.
   `tools/_stop_rule.py` answers the other question: can anything make it surer?
   ⚠ **`terminal` means FINAL, not SAFE** — a blocking verdict is terminal too.
   ⚠⚠ **A false `terminal: true` on a destructive action is the worst error this
   contract can make**, so every uncertainty resolves to False, including an
   unrecognised verdict. Motivated by arXiv 2608.01347, which measures
   verification loops as a distinct TOOL-borne waste carrier: the highest
   redundant-verification runs cost 18x the clean-run median and 2.5x the tool
   calls at no success gain. ⚠ `already_consulted` lives in the tool
   DESCRIPTION, not the response, because it is static per call and the
   description is cached — the same fixed-prefix versus per-turn split that
   paper measures. That makes it prose nobody diffs, so `test_stop_rule.py`
   binds it to real import sites and fails if a tool stops calling what we
   claim it consulted.
8. **A test must never read or write the developer's real global config.**
   `load_config()` with no `storage_path` resolves to `CODE_INDEX_PATH` or
   `~/.code-index/config.jsonc`, reads it, and with the default
   `create_missing=True` WRITES it when absent. ⚠⚠ **conftest's
   `_reset_global_config` already guarded this and already cited #411; a bare
   `load_config()` in a fixture runs AFTER that reset and re-pulls the real config
   straight past it.** The guard existed and the call sites walked around it, which
   is why `tests/test_config_isolation_guard.py` checks the CALL, not the reset.
   ⚠ **The write half is the worse half:** on a storage dir that looks like an
   existing install (any `.db`) with no config file, the config a test run creates
   has `tool_surface` ABSENT, resolving to `full`, and `_fresh_config_content` is
   explicit that `upgrade_config` can never back-inject it. A test run could pin a
   user to a surface nothing migrates them off. Found as three failures @lilubot
   hit on PR #433 and reasonably blamed on their own machine (#437). Our suite was
   green because this box has `max_folder_files` commented out, CI green because
   the runner has no config at all. **A test that passes on two machines and fails
   on a third, for a reason none of the three can see, is the defect.**
