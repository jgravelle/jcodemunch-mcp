# jcodemunch-mcp — Project Brief

## Current State
- **Version:** 1.108.287. **Yesterday's fixes stopped where the reports did.** Four defects (@rknighton, all filed within one minute), each probing a surface ADJACENT to something .286 shipped. ⚠⚠ **THE SAME SHAPE THREE TIMES IN THREE DAYS AND THAT IS THE FINDING: we fix the reported call site and leave the mechanism.** **#506** — .286 filtered the guide's `### All tools` and left `### Quick start` as six fixed strings no filter reached, so it still named a disabled tool as a numbered instruction. ⚠ **The previous fix scoped to the reported SECTION and so did its test** — `_advertised()` split on `### All tools`, so it could not see this section and would not have seen the next; it scans the whole document now. Steps are DATA, dropped whole and RENUMBERED. **#509** — `index_file` picked the deepest containing `source_root` with NO identity check, so a file from a nested independent clone was **WRITTEN** into the parent's index; `resolve_repo` stopped doing this in #492. ⚠ The check is **IMPORTED, not copied**, which also inherited #492's submodule boundary for free. **#508** — `index_file` passes `repo=` to three config reads and nothing on that path ever called `load_project_config`, so all three resolved to GLOBAL config. ⚠⚠ **.286 threaded that keyword through six sites (#491) without checking anything loads what it reads. A parameter that is present and does nothing is indistinguishable from the defect it was added to fix.** ⚠ Fixed at the ENTRY POINT, not by lazy-loading in `config.get()` — `load_project_config` does not cache a MISS. **#507** — `_get_active_tools` rebuilt the set from `tool_profile` + baked `_PROFILE_TIERS`, missing the SESSION tier override, `tool_tier_bundles` and the `languages` gate: **70 / 15 / 1** unmounted names. ⚠⚠ The session case needs NO configuration — `announce_model` writes the tier via `resolve_model_to_tier`, and `jcodemunch_guide` survives every tier. ⚠ **Filtering is a SUBTRACTION**, so an empty or failed build returns `None` = do not filter. ⚠⚠ **`tests/test_path_entry_point_invariants.py` is the deliverable**: written over ENTRY POINTS, with `resolve_repo`/`index_folder` as PASSING CONTROLS — which is what proves an invariant achievable rather than aspirational. **Write the ratchet before concluding the reported list is the list.** ⚠ **FOUR old tests were found asserting the defect they should have prevented** — see Maintenance Practice 9. [[never-batch-a-release-behind-someone-elses-clock]]
- **Prior (1.108.286):** **Three surfaces that advertised a product we were not running.** In each the code was fine and the thing DESCRIBING it was not. **#488** — `_detect_provider` returned the bundled ONNX encoder at priority 0, so `embed_model`/`JCODEMUNCH_EMBED_MODEL` was read after the early return and changed nothing, while the config comment (its ONLY documentation) described the opposite precedence. Explicit now outranks the default. ⚠⚠ **ONLY THE FREE ON-MACHINE PROVIDER WAS PROMOTED — Gemini and OpenAI stay BELOW the default, and that is the load-bearing half.** Full "explicit wins" turned `test_paid_embeddings_optin.py` red, which exists because jdoc's resolver auto-selected OpenAI from an ambient key and began **billing a remote account and shipping the corpus off the machine**. `embed_model` is free and local so promoting it costs a re-embed; promoting a cloud provider costs money and exfiltrates the corpus. **A principle stated over a set can be right for part of it and dangerous for the rest.** ⚠ The usability probe decides PRECEDENCE, never SELECTION — probing unconditionally broke `JCODEMUNCH_EMBED_MODEL` on every machine without the package and 33 tests said so. **#489** — the `semantic` schema and `search_symbols`' error named three key-requiring providers and omitted the bundled one, so **an agent reading the schema concludes semantic search is unavailable on a machine where it works for free** — no error, no warning, the capability simply goes unused. ⚠⚠ **The report named THREE sites; the ratchet found FIVE**, including the `embed_repo` tool description and `embed_drift`'s own copy naming the encoder LAST. **#495** — `jcodemunch_guide` walked a static constant, so **at shipped defaults** (`disabled_tools: ["test_summarizer"]`) it advertised a tool `call_tool` rejects before the handler runs. ⚠⚠ **The filtering already existed and a SECOND generator walked around it** (`e086e9a` fixed `cli/init.py` for #242); reused `_get_active_tools` rather than writing a third copy. ⚠⚠ **THREE TESTS IN THIS CYCLE WERE FOUND ASSERTING THE DEFECT THEY SHOULD HAVE PREVENTED** — `test_generate_full_snippet` required every canonical name to appear (so it could only pass while #495 existed), `test_embed_drift` pinned a literal wording, and two of my own in #489 checked the fix instead of the site. **When a fix turns an old test red, read whether the test was encoding the defect before 'fixing' the code back.** [[a-principle-over-a-set-may-be-right-for-part-of-it]]
- **Prior (1.108.285):** **Five answers that were asserted, not established.** Every fix here is one place that reported a result it had not checked. **#490** — the BM25 cache published `idf` before `centrality` while every reader treated `idf` as "ready", so a concurrent cold `search_symbols` raised `KeyError: 'centrality'`. ⚠⚠ **The window is the whole runtime of `_compute_centrality`, so it WIDENS with corpus size**, and the lock was real and correctly held — what leaked was the READINESS SIGNAL, which is read outside it by design. THREE modules carried the identical block. **#493** — `index_file` wrote live HEAD as the repo SHA after proving ONE file matched, clearing `repo_is_stale` for files still at the old commit. ⚠⚠ `index_folder._refresh_git_head_if_advanced` makes the IDENTICAL write and is CORRECT, because that run walked the corpus first. **The write is not the defect; what has been proven before it is.** **#492** — `resolve_repo` matched `source_root` containment alone, so a path in a nested independent clone returned the PARENT index as `indexed: true`. ⚠ Gitignored it reads `absent`, absorbed it reads `ok`: **two symptoms, one mis-resolution.** Guard is a `.git` stat, never a subprocess (#303), classified by where the pointer goes so submodules still resolve to the parent (#372). **#491** — both exclusion resolvers called `_config.get()` without `repo=`, so the per-project opt-out their OWN COMMENTS document never applied. ⚠ Fourth report of this shape after #300/#187/#304, and #301's ~40-site audit named neither. **#500** (filed by us) — `embed_repo`'s `# Detect dimension mismatch` comment implemented NO detection, so a model change left the store holding two vector widths, and `EmbeddingMatrix`, which infers width from the FIRST row, silently excluded every symbol embedded afterwards — cumulatively. ⚠⚠ **The read path was NOT the defect and was left alone: fixing the consumer would have hidden the producer.** ⚠ `skipped_dim_mismatch` was computed and read NOWHERE, and `capability.py` had called a non-existent `get_model()` behind a bare `except` since .221, reporting `model: "unknown"` for every repo. ⚠⚠ **THREE OF THE FIVE WERE A COMMENT OR DOCSTRING DESCRIBING BEHAVIOUR THE CODE DID NOT IMPLEMENT** (#491, #500, and #493's promise-shaped comment). **Prose in the tree is not evidence about the code beside it.** [[a-concurrency-test-must-pin-the-interleaving]]
- **Older releases (1.108.284 and earlier):** see `CHANGELOG.md`. The 1.108.182 entry ("a stall has a name and a ceiling", #375) and the 1.108.177-.181 #377 hardening arc are there in full.
- **Tests:** 7999 passed, 17 skipped, **0 failed** (1.108.287) **+ `uv run ruff check src/` clean**, measured on `main` at the release commit. ⚠ Reconciled by DECOMPOSITION against .286's 7993 total: #506 **+8** (existing file 10→18, +1 helper widened) + #509/#508 **5** + #507 **8**, and the corrected `test_full_surface_still_honours_profile` stays 1 = **7993 + 23 = 8016**. ⚠ 3.13 CI-env reproduce via `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q`: **7999 passed / 17 skipped, the same 8016 total AND the same skip split**, run SEQUENTIALLY after the local suite. ⚠ Prior (1.108.286): 7976 passed, 17 skipped, **0 failed**; ⚠ Prior (1.108.285): 7945 passed, 17 skipped, **0 failed**; ⚠ Prior (1.108.284): 7894 passed, 17 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: **7911 with `test_code_index_path_is_honoured.py`, 7902 without = exactly its 9**, and 7902 is .283's total, so nothing else moved. ⚠⚠ **This release also measured the REAL STORE, which no pass count can show**: a full run now CREATES nothing under `~/.code-index` and emits no `_watcher_*.signal`, so the process-lock scopes are isolated. `_savings.json` and `session_stats.json` still move, because `token_tracker` was deliberately left on the home default (see Current State). **Assert the side effect, not just the exit code.** ⚠ Prior (1.108.283): 7883 passed, 17 skipped, **0 failed**, and the 3.13 CI-env reproduce returned the SAME totals AND the same skip split — stronger than the usual same-total-different-split. ⚠⚠ **TWO INDEPENDENT FALSE-GREEN MECHANISMS were found across these two releases and BOTH reported `exit code 0`.** (1) `PYTHONPATH=src python -m pytest tests/ -n 4 --dist loadfile` — **pytest-xdist lives in the dev group inside `.venv` and is INVISIBLE to a bare `python -m pytest`**, so pytest rejected the flags, collected NOTHING, and exited 0 while the harness reported success. **Use `uv run pytest` whenever xdist flags are passed.** (2) **A trailing `| tail` swallows pytest's exit status** — a run with one real failure was reported as "exit code 0", because the pipeline's status is tail's. **Write to a log and echo the exit code BEFORE any pipe**; every number in this line was obtained that way. ⚠ Local suite is `uv run pytest tests/ -n 4 --dist loadfile` at ~200-300s against ~600s serial; CI pins `-n 4`, deliberately not `-n auto`. Prior (1.108.282): 7849 passed, 10 skipped, **0 failed** **+ `uv run ruff check src/` clean**. Prior (1.108.281): 7848 passed, 10 skipped, **0 failed**. ⚠⚠ **Reconciled by a SAME-TREE COLLECT against `origin/main`, and arithmetic against the previous release line would have been wrong by 16** — `main` moved twice between .280 and this bump (#474's 14 tests, #477's 2), so the usual "delta from the last release" method had two unrelated merges inside it. **Pick the method that matches how the work landed.** Measured: **7858 collected on the branch vs 7847 on `d10490e`, +11.** Decomposition: `test_v1_108_281.py` **10**, plus a net **+1** from the ratchet rearranging — four languages leave `EXEMPT` and join `test_declared_constant_pattern_extracts_a_constant` (+4) while the four `test_exemptions_are_not_stale` cases collapse into ONE empty-parametrize item (-3). ⚠ **The 10th skip is that empty parametrize** and is the ratchet AT REST, not a lost test; it re-arms the moment anyone adds an exemption (same shape as `_JS_VARIANT_EXEMPT` in .273). ⚠ The release commit adds no tests, so the post-bump run reproduces the pre-bump 7848/10 exactly. ⚠ 3.13 CI-env reproduce **7842 passed / 16 skipped, the SAME 7858 TOTAL**, different skip split, via `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q`. ⚠ Run SEQUENTIALLY after the local suite, never alongside it — two full runs share `~/.code-index` process-lock scopes and contention is the documented cause of .261's 47m outlier (.280 records the reversal). Prior (1.108.280): 7822 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .279's 7828 total is EXACTLY the 3 new `test_perf_db_path_resolution.py` tests, and that release carried no other code, so nothing else could have moved. ⚠⚠ **The two suites were run in SEQUENCE, not in parallel, and that was a deliberate reversal mid-release.** Both were started together, then the 3.13 arm was killed before it produced anything: two full runs on one box contend for the same `~/.code-index` process-lock scopes, and **contention is the documented cause of .261's 47m outlier**. A false red costs a re-run and, worse, a few minutes of reading a real-looking failure. **Sequence them; the wall-clock saving was never worth the ambiguity.** ⚠ 3.13 CI-env reproduce **7816 passed / 15 skipped, the SAME 7831 TOTAL**, different skip split, via `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q` — without `--extra watch` it collects 105 fewer and reports a clean pass (see .278 below). Prior (1.108.279): 7819 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .278's 7808 total is EXACTLY the 20 new `test_schtasks_locale.py` tests; the release's other half is docs-only, so nothing else could have moved. ⚠ 3.13 CI-env reproduce **7813 passed / 15 skipped, the SAME 7828 TOTAL**, different skip split. Prior (1.108.278): 7799 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Reconciled by DECOMPOSITION against .277's 7797 total: `test_identity_normalized_tier.py` 10 (#458) + `test_schema_baseline_transcription.py` 2 (#467) **- 1 REMOVED** (`test_the_core_compact_schema_budget_is_unchanged`) = **+11**, and 7797 + 11 = 7808 exactly. **A removal is part of the delta and the usual add-only arithmetic hides it.** ⚠⚠ **THE DOCUMENTED 3.13 REPRODUCE COMMAND UNDER-COLLECTS BY 105 TESTS, and it reports a clean pass while doing it.** `uv run --python 3.13 python -m pytest tests/ -q` collected **7703** against the local 7808. The missing 105 are ENTIRELY three watcher files (`test_watcher_serve.py` 49, `test_watcher_lock.py` 40, `test_watcher_dynamic.py` 16), each gated on `pytest.importorskip("watchfiles")` — and `watchfiles` is an OPTIONAL extra. **CI installs it** (`uv sync --locked --group dev --extra watch`, `test.yml:84`); the documented command does not. **Use `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q`** — that run is **7793 passed / 15 skipped, the SAME 7808 TOTAL**, different skip split. ⚠ **The totals convention is what caught it**: passed counts alone read as a plausible pass either way, and a whole subsystem being absent is invisible from `N passed`. ⚠ **Do NOT read this as .277's number being wrong** — 7782 + 15 = 7797 is internally consistent, so that run DID collect the watcher tests. `uv run` reuses an already-synced environment, so the same command can collect differently depending on what last synced it. **The command is unreliable, not that record.** Prior (1.108.277): 7788 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠⚠ **This release adds NO test file of its own and a flat delta would have been the RED flag on any other release** — every prior one ships a `test_v1_108_NNN.py`, so "no new tests" normally means the bump outran the work. Here the work landed across the day in #459/#462/#463/#464 and the release commit is version metadata + changelog + rotation only. **Reconciled by DECOMPOSITION rather than a same-tree collect**, because the collect diff has nothing to subtract: `test_html_file_class.py` 4 (#459) + `test_v1_108_277.py` 6 (#462) + `test_pid_reuse_identity.py` 10 (#451 via #464) + `test_claude_md_rotation.py` 4→9 = +5 (#463) = **25**, and .276's 7763 + 25 = 7788 exactly. ⚠ **Pick the reconciliation method that matches how the work landed**; applying the usual one here yields a zero and proves nothing. ⚠ 3.13 CI-env reproduce: **7782 passed / 15 skipped**, same 7797 TOTAL, different skip split — compare totals across interpreters, never passed counts. Prior (1.108.276) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7772 total, 7753 with `test_v1_108_276.py` ignored (= its 19); the **+5 over .275's 7748 is five new `def test_` functions in `test_tools.py`** from the #438/#439 drive-root work — COUNTED in `git diff v1.108.275..HEAD`, not inferred, because "nothing else moved" was not true this release and asserting it would have been the same shape of error the count notes below are about. ⚠⚠ **The 3.13 CI-env reproduce totals the SAME 7772 but splits 7757 passed / 15 skipped** — six tests that RUN on 3.10 SKIP there. **A passed-count comparison ACROSS interpreters is meaningless; compare TOTALS.** ⚠ **The 9th skip is the POSIX-only orphaned-inode test for #442** — Windows refuses to unlink a file with an open handle, so this box CANNOT produce that case. **Do not read that skip as cross-platform coverage**; it is a real local gap covered only by the portable unit test for the predicate. Prior (1.108.275) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7748 total, 7734 with `test_v1_108_275.py` ignored (= its 14), and 7734 is exactly .274's total, so nothing else moved. Prior (1.108.274) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7734 total, 7728 with `test_security_disclosure.py` ignored (= its 6), and 7728 is exactly .273's total, so nothing else moved. ⚠⚠ **This line was briefly written with a GUESSED number before the run finished, and the guess (7734) was the TOTAL rather than the passed count — it would have read as a plausible, wrong figure.** Never pre-write a count; the run is the only source. Prior (1.108.273) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7728 total, 7717 with `test_v1_108_273.py` ignored (= its 11), and the +1 over .272's 7717 is `next` ENTERING the #435 sweep now that its exemption is gone. ⚠ **The 8th skip is EXPECTED and is not a lost test**: `_JS_VARIANT_EXEMPT` is empty, so the ratchet parametrizes over an empty set and pytest skips it ("got empty parameter set"). That is the end state of a ratchet that did its job; it re-arms the moment anyone adds an exemption. ⚠⚠ **A version bump MID-RUN voids the run** — the rotation gate compares CLAUDE.md to `pyproject.toml`, so a suite spanning the bump is not evidence. Bump and rotate FIRST, then run once. (Done wrong on .273 and the run was discarded.) Prior (1.108.272) **+ `uv run ruff check src/` clean**. ⚠ Delta is EXACTLY the 9 new `test_v1_108_272.py` tests, reconciled by COLLECTING the same tree twice (7716 with the file, 7707 with it `--ignore`d) rather than by arithmetic against this line. ⚠⚠ **That method was forced, because this line was STALE by ~239 for two releases** — it read "7470 (1.108.269)" while .270's 31 and .271's 124 were never folded in, so the documented baseline was unusable as one. **A count that is only ever appended to during a release rots the moment a release skips it**; prefer a same-tree collect diff, which cannot go stale, and treat this number as a report rather than a baseline. ⚠⚠ **The count was mis-reported once during this release and the ARITHMETIC caught it, not the reading** — an intermediate run was quoted as "7469 passed, 0 failed", a combination that never happened: it was 7469 passed WITH 1 failed, totalling 7470. **Always reconcile passed+failed against the prior release's total plus the new test count**; eyeballing `N passed` at the end of a 17-minute run is how a red run gets read as green. ⚠ The failure was the CLAUDE.md rotation gate correctly refusing a Current State naming 1.108.269 while `pyproject.toml` still read .268 — **the gate fires BEFORE the version bump lands, so a red rotation test mid-release is expected and must not be waved through as "just the gate"**; it clears only when every pin site agrees. **Prior (1.108.268):** 7436 passed, 7 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .267's 7428 is EXACTLY the 8 new `test_stdio_guard.py` tests; nothing else moved. ⚠⚠ **The CLAUDE.md rotation gate caught a real mistake this release** — a 4th entry was added without demoting .267 or moving the `Older releases` boundary, and the gate failed the build rather than letting the history drift. **Prior (1.108.267):** 7428 passed, 7 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .266's 7404 is EXACTLY the 24 new `test_constant_extraction_guard.py` tests; nothing else moved. **Prior (1.108.266):** 7404 passed, 7 skipped, **0 failed** (isolated worktree run) **+ `uv run ruff check src/` clean + CI all 9 jobs green on the pushed SHA**. ⚠ The delta from .265's 7394 is EXACTLY the 10 new `test_format.py` cases; nothing else moved. ⚠⚠ **Nothing moving is itself the finding** — not one existing test pinned a fusion or semantic confidence value, which is precisely why a ~5x mis-scaling shipped and survived. ⚠ **+17 after .264 shipped**: the file-IO scanner needed TWO MORE iterations (see below), test-only, no bump. ⚠⚠ **A green suite is NOT a green build** — lint was RED for four releases while this line said 0 failed. Quote ALL THREE (suite, ruff, CI) from now on. ⚠⚠ **A green suite is NOT a green build** — lint was RED for four releases while this line said 0 failed. Quote BOTH numbers here from now on, and read the CI run for the pushed SHA. ⚠ **.261's run took 47m45s against ~16-17m before and after it on the same tree** — same counts, same result, so it was machine contention and NOT a signal. Do not treat a wall-clock outlier as a regression. ⚠⚠ **A config change is the one edit whose blast radius is the whole suite** - 128 test files touch `_GLOBAL_CONFIG` directly, so a "small" resolver change is never a small run. ⚠ **The "KNOWN 12 local-ONNX `test_semantic_search` env failures" are GONE** — .207's autouse `no_local_onnx` fixture fixed them, so a local run is now fully green and **any** red is a real signal. Do not carry that 12-failure allowance forward; it papered over a real failure once already (.197 had one hiding inside it). ⚠ **Still do not eyeball the COUNT** — diff the FAILED names against the same tree with your changes stashed; for .199 and .205 that diff was empty, and for .209 the failure set was empty outright, which is the one case that needs no baseline. ⚠ **Stashing is the wrong tool when the change is already committed and pushed** — for .205 the comparison ran in a throwaway `git worktree add --detach <pre-release-sha>`, which also survives a concurrent writer in the main tree.
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

### Tool-description quality (`benchmarks/description_smells/`)

Descriptions are scored against the rubric in arXiv:2602.14878. Two rules when you
touch a tool description:

- **`core_compact` has a HARD ceiling of 4,000 tokens** (v2 §10). The drift ratchet
  in `tests/test_schema_budget.py` offers "or update the baseline"; the sibling
  ceiling tests forbid it. Trim the description instead. Currently 3,990, so a
  core-tier tool has roughly ten tokens of slack, not a sentence's worth.
- **`tests/test_description_smells.py` gates Purpose and Length.** A new tool with a
  one-line description fails it. Two substantive sentences minimum: what it does and
  returns, plus one boundary or usage cue.

⚠ The audit reports two frames. The paper's scanner never sees `inputSchema`, so
schema-documented parameters score 1/5 by its rubric. Quote both frames or neither.

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

⚠ **Those two numbers are a 2026-07 snapshot from THIS harness and are not the
canonical figures.** `benchmarks/schema_baseline.json` is, written by
`benchmarks/harness/capture_schema_baseline.py` and guarded by
`tests/test_schema_budget.py`; it counts a different payload shape, so the two
sets will never agree digit for digit and neither is wrong. Quote the baseline
file. ⚠⚠ **Reconciled 2026-08-14: the Counter avoids 95.9%, not the ~98% that
`run_route_recall.py` asserted for two months** — that literal is now computed
from the baseline at runtime, with a test that fails if any schema-saving
percentage returns to that file. **The gap existed because the budget guardrail
only walked `tool_profile`, which does not apply to the front door at all**, so
the single largest lever in the project had no test under it.

⚠⚠ **The same run killed `tool_profile: "standard"` as a token lever: it drops 9
of 91 tools and 5.7% of the payload.** Anyone selecting it as the safe middle
setting gets nothing measurable. `core` (74.0%) and `counter` (95.9%) are the
only two settings that move the number; there is no gradient between them, and
the config surface currently implies there is. ⚠ Where the rest sits, from
`--breakdown`: under `full`, tool DESCRIPTIONS are 36% of the payload and
`compact_schemas` rewrites input schemas only, never descriptions. Schema
compaction is near its floor; descriptions are untouched ground.

⚠ Design flaw recorded so nobody repeats it: summing per-invocation input across
a RESUMED conversation counts accumulated context on every step, so the total is
dominated by how much the agent read early on, which compounds.

**2026-08-18: #488 DECIDED BY JJG — OPTION A, "explicit config outranks the
zero-config ONNX default", with disclosure.** NOT YET IMPLEMENTED; queued behind
#495. `_detect_provider` will check `embed_model` / `JCODEMUNCH_EMBED_MODEL` and
the cloud key pairs BEFORE returning `local_onnx` at priority 0, the result will
name the active provider and why, and the `config.jsonc` comment gets corrected.
⚠⚠ **This was only safe to decide because #500 shipped first.** Making explicit
config win makes provider changes MORE frequent, and before #500 each one left
the store holding two vector widths with the newer half silently excluded from
search. **The migration hazard that looked like a cost of option A was a
pre-existing defect option A would merely have made more likely to fire.**
⚠ **Option C (local-only per #302) was REJECTED on a factual error in the
report**: branches 1-3 are not vestigial, only SHADOWED, and only when
`[local-embed]` is ALSO installed. `[semantic]` without `[local-embed]` uses
branch 1 today and it works. **Say that to the reporter — their largest
suggestion rests on it.**
⚠ Disclosure is not optional in A: a caller whose provider changes needs to see
`model_changed_from` / `rebuild_reason` (#500's fields) rather than discover a
re-embed by watching the clock.

**2026-08-19: #506/#507/#508/#509 (@rknighton) FIXED BY US via PRs #510/#511/#512.**
All four were filed at 00:24-00:25 and every one probes a surface ADJACENT to
something we shipped the day before. Unreleased.
⚠⚠ **THE SAME SHAPE THREE TIMES IN THREE DAYS, and it is the reusable finding:
we keep fixing the reported call site and leaving the mechanism.** #495 was a
second GENERATOR carrying its own copy of the filter; #509 a second CALL SITE
with its own containment check; #507 a second DERIVATION of the tool set. In
each the fix is one sentence — **ask the authority instead of reproducing its
logic** — and in each we had applied it only where it was reported.
**#506** — v1.108.286 filtered `### All tools` and left `### Quick start` as six
fixed strings no filter reached, so the guide could still instruct a caller to
run a disabled tool. ⚠⚠ **The previous fix scoped to the reported SECTION, and
so did its test**: `_advertised()` split on `### All tools` and inspected only
what followed, so it could not observe this section and would not have observed
the next. Now scans the whole document. Steps are DATA now, dropped whole and
RENUMBERED, with the shared `index_folder`/`index_repo` continuation filtered
per-tool.
**#509** — `index_file` picked the deepest containing `source_root` with NO
identity check, so a file from a nested independent clone was WRITTEN into the
parent's index. ⚠ The check is **imported from `resolve_repo`, not copied** —
which is the lesson AND which inherited #492's submodule boundary for free, so a
submodule path still resolves to the parent (his Case 3, untouched). ⚠ The
refusal NAMES the repository; falling through to "no indexed folder contains
this path" was wrong on the facts and pointed at the wrong remedy.
**#508** — `index_file` passes `repo=` to three config reads and nothing on that
path ever called `load_project_config`, so the overlay was empty and all three
resolved to GLOBAL config. ⚠⚠ **v1.108.286 threaded that keyword through six
sites (#491) without checking anything loads what it reads. A parameter that is
present and does nothing is indistinguishable from the defect it was added to
fix.** ⚠ Fixed at the ENTRY POINT, not by lazy-loading inside `config.get()` —
`load_project_config` does not cache a MISS, so a lazy load re-stats on every
read for any repo without a project file, on the hottest function in the tree.
**#507** — `_get_active_tools` rebuilt the active set from `tool_profile` + the
baked `_PROFILE_TIERS`, missing three inputs `tools/list` reads: the SESSION tier
override, `tool_tier_bundles`, and the `languages` gate on `search_columns`.
Measured 70 / 15 / 1 unmounted names. ⚠⚠ **The session-override case needs NO
configuration** — `announce_model` writes the session tier via
`resolve_model_to_tier`, and `jcodemunch_guide` is in `_ALWAYS_PRESENT_TOOLS` so
it stays reachable at every tier. ⚠ **Filtering is a SUBTRACTION**, so an empty
or failed build returns `None` = do not filter: a policy naming a few
unavailable tools beats a policy with no workflow left in it.
⚠⚠ **`tests/test_path_entry_point_invariants.py` IS THE DELIVERABLE of that
batch.** Written over the ENTRY POINTS rather than the two reported functions,
with `resolve_repo` and `index_folder` as the PASSING CONTROLS in each pair —
which is what proves an invariant achievable rather than aspirational. It read
2 failed / 2 passed against the pre-fix tree. **Write the ratchet before
concluding the reported list is the list** (#489 found 5 sites for a 3-site
report the same way).
⚠ **Two of my own guards matched PROSE, not code**: #507's first version matched
the literal `_PROFILE_TIERS` and failed on the COMMENT explaining why the helper
must not use it. Walk the AST — it cannot see comments. Same fix the `src.`
twin-import guard needed.
⚠ Suite: **7999 passed, 17 skipped, 0 failed** on `main` + ruff clean.

**2026-08-18: #488 (@pnm-jgb) FIXED BY US via PR #505 — an explicit local model
now outranks the zero-config default, and the NARROWING is the entry.**
Unreleased.
⚠⚠ **JJG DECIDED "OPTION A"; WHAT SHIPPED IS A IN ONE BRANCH ONLY, AND HE
APPROVED THE NARROWING AFTER IT WAS SURFACED.** Full A turned
`tests/test_paid_embeddings_optin.py` RED, and that file is not incidental — it
exists because jdocmunch's resolver auto-selected OpenAI from an ambient
`OPENAI_API_KEY` and began **billing a remote account and shipping the indexed
corpus off the machine**. jcm's second line of defence IS that ONNX wins before
any cloud branch is reached. **A developer with `[local-embed]` and an exported
`OPENAI_APIKEY`+`OPENAI_EMBED_MODEL` would have silently started paying per call
and sending their source off the box.**
⚠⚠ **THE ASYMMETRY THE ISSUE NEVER ADDRESSED, and the reusable half:
`embed_model` is FREE and ON-MACHINE; Gemini and OpenAI are PAID and REMOTE.
Promoting the first costs a re-embed. Promoting the others costs money and
exfiltrates the corpus. A principle stated over a set ("explicit beats default")
can be right for part of the set and wrong for the rest — check what each member
costs before applying it uniformly.**
⚠ **A RED TEST IS SOMETIMES THE SPEC.** The instinct on 33 reds and one
money-safety red is to fix the tests. Here one of them was the design document
and the other 33 were reporting a real regression. **Read the docstring of a
failing test before assuming it is stale.**
⚠⚠ **The usability probe was WRONG on its first pass and 33 tests caught it.**
Probing `sentence_transformers` importability UNCONDITIONALLY meant that on any
machine without the package `JCODEMUNCH_EMBED_MODEL` selected nothing, so the
caller got a bare `None` instead of the actionable `pip install
'jcodemunch-mcp[semantic]'` error. **The probe now decides PRECEDENCE, never
SELECTION**: an uninstalled backend does not displace a WORKING ONNX install,
but with no ONNX the setting is selected as before.
⚠ `provider_reason` + `provider_skipped` added to `embed_repo`'s result: an
explicit setting we cannot honour is DISCLOSED, never dropped. Silently ignoring
it is the reported defect; silently failing on it at embed time is that defect
with a louder symptom.
⚠ **Option (4) from the report (remove branches 1-3 + the `[semantic]` extra +
~5 GB of torch) was DECLINED ON A FACTUAL ERROR IN THE REPORT** — those branches
are not vestigial, only SHADOWED, and only when `[local-embed]` is ALSO
installed. `[semantic]` without `[local-embed]` uses branch 1 today and it works.
**Said so on the thread; his largest suggestion rested on it.**
⚠ **This change was only possible because #500 shipped in .285.** Making explicit
config win makes provider changes more frequent, and before .285 each one split
the store silently. **The migration hazard was a pre-existing defect the change
would merely have made more likely to fire.**
⚠ `tests/test_explicit_embed_model_wins.py` (12), 6 red pre-fix **but only TWO
behavioural** — the other four fail because `_detect_provider_detailed` does not
exist there, which is a signature fact and not evidence. **Report that split;
"6 red" alone overstates it.** The 6 passing both sides are the money-safety
class and the wrapper-shape controls.
⚠ Suite: **7976 passed, 17 skipped, 0 failed** + ruff clean; +12 over .285's
7981-after-#495.

**2026-08-18: #504 (@lsg1103275794) VERIFIED, TIMEBOXED TO 2026-08-19, NOT YET
FIXED.** Repeat `index_folder` on a GIT ROOT never reaches the incremental
no-change path: the v1.96 collision guard at `index_folder.py:2224` is
`if _existing_source_root == _git_root:` with NO `walk_prefix` test, so a
full-root re-walk assigns `_merge_with_existing` and the incremental branch at
`:2402` (gated on `_merge_with_existing is None`) is unreachable. **Every
scheduled freshness check rebuilt the whole corpus.** Reproduced at .285.
⚠ **He offered to PR and sign the CLA; we said yes and posted the window with
the default (we implement + credit at expiry).** First-time contributor.
⚠⚠ **NOT a one-line fix and he said so BEFORE writing it** — `and walk_prefix`
alone breaks `test_full_root_walk_after_subdir_replaces_everything`, because a
full-corpus incremental diff cannot be layered onto a `source_roots` marker that
is still partial. His account: one full rebuild establishes `source_roots ==
[""]`, after which repeat root walks take the no-change path. **That is a
DISCLOSED MIGRATION and must reach the CHANGELOG, not be found by a user whose
first post-upgrade index is unexpectedly slow.**
⚠ **It makes `_refresh_git_head_if_advanced` fire MORE OFTEN** (no-change runs
finally happen), which is #493's ground from .285. Correct in `index_folder`
precisely because that path walks the whole corpus — **verify in review, do not
assume.**
⚠ Measured by him: 5.0-5.7s -> 1.58s on 1,132 files / 9,926 symbols. **His
machine, his number; do not transcribe as canonical.**
⚠ **Droppable from the release if it needs care.** It is a PERFORMANCE fix — the
index produced is correct, just rebuilt needlessly — and #447's SECURITY fix must
not wait behind it.

**2026-08-18: #495 (@rknighton) FIXED BY US via PR #503 — the guide advertised a
tool the same process refuses to run.** Unreleased.
⚠⚠ **AT SHIPPED DEFAULTS, no config file and no env overrides.**
`disabled_tools` ships `["test_summarizer"]` and
`_generate_claude_md_snippet` walked a static constant, so the guide named it,
`tools/list` omitted it, and `call_tool` rejected it before the handler ran.
**Reachable out of the box is what makes this worth a release rather than a
note.**
⚠⚠ **THE FILTERING ALREADY EXISTED AND A SECOND GENERATOR WALKED AROUND IT** —
`e086e9a` added it to `cli/init.py` for #242, and `server.py`'s generator never
got it. **Reused `_get_active_tools`; a third copy is how the first two
drifted.** Same shape as #491 (the guard existed, the call sites bypassed it) and
the `src.jcodemunch_mcp` twin sweep.
⚠ **Widened past the reporter's scope DELIBERATELY and said so on the PR**: they
scoped to `disabled_tools` correctly (a profile-hidden tool stays dispatchable,
so it costs context not failure), but the tool's own description promises to
match "surface, tier and disabled_tools", and `tier` IS the profile. Filtering
one and not the other leaves the description making a claim the code does not
keep.
⚠⚠ **`tests/test_config.py::test_generate_full_snippet` ASSERTED THAT EVERY
CANONICAL TOOL NAME APPEARS, so it could only pass WHILE THE BUG EXISTED.**
`test_summarizer` is canonical and disabled by default. **Third test this release
found asserting the behaviour it should have prevented** (after
`test_embed_drift.py`'s literal wording and my own two in #489). **When a fix
turns an old test red, read whether the test was encoding the defect before
"fixing" the code back.**
⚠ `tests/test_guide_respects_disabled_tools.py` (9), 5 red pre-fix; the four
constraints include a PIN ON `DEFAULTS["disabled_tools"]` so the issue's premise
cannot silently change out from under the case.
⚠ Suite: **7964 passed, 17 skipped, 0 failed** + ruff clean; +9 over .285's 7972.

**2026-08-18: #489 (@pnm-jgb) FIXED BY US via PR #502 — the tool schema
advertised three key-requiring providers and hid the free one.** Unreleased.
⚠⚠ **The `semantic` PARAMETER DESCRIPTION is the expensive site and the harm is
invisible from outside.** It is not documentation a human browses — it is the
tool schema, and the ONLY information an agent has when deciding whether to set
`semantic: true`. An agent reading "requires one of three env vars" against an
environment with none set correctly concludes semantic search is unavailable and
never tries it, **on a machine where it works for free**. No error, no warning,
no degraded result: **the inverse of a false positive, where the tool
under-reports its own function.**
⚠⚠ **THE REPORT NAMED THREE SITES; THE RATCHET FOUND FIVE.** The two extras were
only visible once a test asserted the PROPERTY instead of the instances: the
`embed_repo` TOOL DESCRIPTION in `server.py` (equally agent-facing, same
omission) and `retrieval/embed_drift.py`, whose own copy named the bundled
encoder **LAST**, behind the two that bill per call. **Write the ratchet before
concluding the reported list is the list.**
⚠ All five now derive from `embeddings/advice.py`; `_LOCAL_FIRST` leads both
strings, mirroring `_detect_provider`'s priority so advice and resolver cannot
disagree about which wins. Option (3) from the report — a schema stating the
RUNTIME fact rather than setup instructions — is NOT shipped; noted on the PR
rather than dropped.
⚠⚠ **MY BUDGET WARNING WAS WRONG AND MEASURING IS WHAT CAUGHT IT.** I told jjg to
watch the hard 4,000-token `core_compact` ceiling (10 tokens of headroom) and was
ready to trim a description. **`semantic` is in `_COMPACT_STRIP_PARAMS` and never
reaches the compact schema at all** — live `core_compact` is **3,990 before and
after**. A test pins that, so if `semantic` ever stops being stripped the budget
question returns visibly. **Measure the constraint before paying for it.**
⚠⚠ **`tests/test_embed_drift.py` PINNED THE LITERAL OLD WORDING, which is HOW
that site kept a stale copy** — a test keyed to one spelling of a sentence guards
the spelling, not the behaviour. **My own ratchet had the identical defect on its
first pass** (matched `"No embedding provider is configured"` WITH the `is`, and
caught `embed_drift` only by luck via a different clause), and **my site-2 test
asserted on the CONSTANT rather than on `search_symbols`** — true the moment the
constant exists, so it checked the fix instead of the site and passed against a
tree where that site was still stale. Corrected; it is now among the pre-fix
reds, and was not before. **Three instances of one mistake in one change.**
⚠ `tests/test_embedding_provider_advice.py` (10), 4 red against the pre-fix
CONSUMERS with `advice.py` present — stashing the module too only proves it is
new. **Keep the new module and revert the call sites; that is the pass that
means something.**
⚠ Suite: **7955 passed, 17 skipped, 0 failed** + ruff clean; +10 over .285's 7962.

**2026-08-18: #443 resolved a THIRD time, still ours.** v1.108.285 plus #489 both
touched the `[Unreleased]` block. Same resolution, suite **7961/17/0**, +6 =
exactly elfrost's tests, all 11 real CI checks green on the merge ref;
`license/cla` PENDING is the only blocker.
⚠ **The erase-on-push hazard fired again** (count=0 on the new head, back as
`pending` within ~2 minutes). Tally now: erased 2, survived 1. **Read the status
after every push to a fork; it is not predictable.**
⚠ **A comment was posted BEFORE CI confirmed it** ("everything green except
license/cla"). It held, but it was a prediction at the time. Post the claim after
the run, or say it is expected rather than observed.

**2026-08-18: #491 (@rknighton) FIXED BY US via PR #499 — the two exclusion
opt-outs never read the project config that documents them.** `security.py` read
`exclude_skip_directories` / `exclude_secret_patterns` without `repo=`, so the
project overlay was skipped and the documented per-project opt-out did nothing.
Unreleased; see CHANGELOG `[Unreleased]`.
⚠⚠ **The COMMENTS are what make it a defect rather than a missing feature.** The
note above the skip list says these are ordinary English words that can name a
real package, "which is why `exclude_skip_directories` exists"; `is_secret_file`'s
docstring claims it applies the project overrides ONE LINE above the global-only
read. **Both described an intent the code did not implement** — same shape as
#500's promise-without-detection, found the same day.
⚠ Nothing surfaces it: `discovery_skip_counts` gives `skip_dir: 2` with no
directory or rule name, and the pruned path goes to `logger.debug`.
⚠ **FOURTH report of one shape** after #300 / #187 / #304, and **#301 audited
~40 call sites for exactly this, listed `get_extra_ignore_patterns` as fixed and
named neither of these**; v1.108.197 then fixed the three `max_*` resolvers and
left them too. **A fifth audit finds a fifth instance; the ratchet finds it on
the commit that introduces one.**
⚠ **Signature-only would have been a FALSE GREEN** — adding the parameter and
leaving callers bare changes nothing observable, so the call-site check walks the
AST, not the signature.
⚠ **`index_repo` is exempt BY NAME, not by omission**: a project config is found
by walking up from a LOCAL path and a GitHub tree has no checkout, so passing the
owner/repo id would imply a lookup that cannot succeed. This is a stated
DEVIATION from the reporter's acceptance criterion 5, said so on the PR.
⚠ `tests/test_security_exclusions_are_project_overridable.py` (13), all red at
`b85ef61` — but **three are constraints, red only because `repo=` is not a
parameter there**, so each also asserts the no-argument form. **Do not report
"all red" without that distinction; it overstates the evidence.**

**2026-08-18: #500 FILED AND FIXED BY US via PR #501 — a promise in a comment
with no code behind it, found while checking whether #488 was safe to ship.**
`embed_repo`'s `# Detect dimension mismatch — if the stored model differs, force
a rebuild` implemented NO detection: `stored_dim` only seeded `dim`, nothing
compared stored model to active, and `set_dimension` fired only when
`dim is None` (first-ever embed). A model change wrote new-width vectors beside
the old under a meta row naming the first.
⚠⚠ **THE CONSEQUENCE COMPOUNDS AND IS SILENT.** `EmbeddingMatrix` infers width
from the FIRST row and drops the rest, and the inferred width follows the
majority of PRE-EXISTING rows — so **every symbol embedded after the change is
excluded from semantic search, forever, and the gap grows with every new file.**
Measured `{384: 6, 768: 1}` with meta reporting 384. A recall failure that reads
as a finding.
⚠⚠ **THE READ PATH IS NOT THE DEFECT AND WAS LEFT ALONE.** `_build`'s exclusion
is a faithful port of what `_cosine_similarity` did before the matrix existed and
its comment says so. **Fixing the consumer would have HIDDEN the producer** —
the fix must go where the mixed store is CREATED. Same lesson as #493's write:
find what was proven, not what was written.
⚠ **Unknown is not a change**: a store with no persisted model name must NOT
force a rebuild, or every existing user is billed a full re-embed for a model
that may be identical.
⚠ **`stored_dim` is cleared inside the `force` branch, which REPAIRS A SECOND
BUG nobody reported**: the pre-existing `task_type` force path cleared the store
and left `dim` seeded, so the `dim is None` gate never re-fired and the meta kept
advertising the old dimension against fresh vectors.
⚠ **`skipped_dim_mismatch` was computed, stored on the object and read NOWHERE**
(`grep` found only its three defining lines). **A count that exists and is
discarded is the same defect as not counting.** Now surfaced as
`_meta.semantic_partial` + `channels.semantic: "partial"`, because the producer
fix does not heal stores already mixed.
⚠ **`evidence/capability.py` has called `get_model()` since v1.108.221 behind a
`type: ignore` and a bare `except`**, so the capability certificate reported
`model: "unknown"` for EVERY repo. **Found by adding the method, not by reading
the call site** — a bare except around a `type: ignore` is a permanent silent
failure by construction.
⚠⚠ **THIS IS THE BLOCKER ON #488 AND THAT IS WHY IT WAS FILED SEPARATELY.**
Making explicit config outrank the ONNX default makes provider changes MORE
frequent, and until now each one silently degraded the index. **The "migration
hazard" that looked like a cost of #488's option A was a pre-existing defect
option A would merely have made more likely to fire.** Check whether a hazard is
introduced or merely exposed before pricing it against a design choice.
⚠ `tests/test_embedding_model_change.py` (9), 8 red at pre-fix (2 of those are
signature-only reds); the same-model no-rebuild control passes both sides.

⚠⚠ **PROCESS, MEASURED THIS SESSION: a push is the RELIABLE way to re-provoke a
missing `license/cla`; close+reopen is NOT.** #499 opened with **zero** statuses
on its head (the #479 shape — the bot never fired, which reads identically to
our-push-erased-it). Close+reopen left `count=0`; `git commit --amend --no-edit`
+ force-push restored it `success` within a minute. **That is now 2 failures and
1 success for close+reopen and 2 successes for a push.** ⚠ It also blocks the
merge for real now that `license/cla` is required (3d), so an unfired bot on OUR
OWN PR presents as `BLOCKED` with 11 green checks.
⚠ **Batching worked**: #490/#491/#492/#493/#500 were all merged before touching
#443, and it was resolved ONCE instead of five times. That is the lever policy 3b
leaves when the contributor PR is BLOCKED and cannot go first. `license/cla`
SURVIVED this push — the opposite of the previous one, so **read the status, do
not predict it**.

**2026-08-18: #493 + #492 (@rknighton) FIXED BY US via PRs #496 / #498.**
Unreleased; see CHANGELOG `[Unreleased]`.

**#493 — `index_file` advanced the repo `git_head` after proving one file.**
`repo_is_stale` is "index SHA differs from live HEAD", so refreshing one file out
of a two-file commit CLEARED staleness for the file never refreshed, and
`get_file_content` served commit-A content reading `channels.index: fresh`
against a clean tree.
⚠⚠ **THE WRITE IS NOT THE DEFECT; WHAT HAS BEEN PROVEN BEFORE IT IS.**
`index_folder._refresh_git_head_if_advanced` makes the IDENTICAL write on a
no-change run (#330) and is CORRECT there, because that run walked the corpus.
**Two calls, one write, opposite correctness.** The reporter drew that
distinction himself and the fix is built on it — a diff of the two functions
would have shown nothing.
⚠ Fix is one `git diff --name-only --relative` against the stored head; advance
only if every other moved path is one the index neither carries nor would index.
`--relative` is load-bearing (a monorepo subtree must not be held back by a
sibling commit). **An ADDED source file blocks too** — not in the corpus, so not
"a file we carry that moved", but advancing would certify a complete index over
a corpus missing a file.
⚠ **`_paths_changed_between` returns None for "could not ask", NEVER an empty
set**, or a failed git call reads as a clean diff. Unknown → do not advance,
same asymmetry as .209.
⚠ **Branch-delta path deliberately UNCHANGED** (writes `branch_meta`, own
`base_head`); the reporter made no claim about it. Recorded, not swept.
⚠ `tests/test_index_file_head_advance.py` (10): **5 red at `b85ef61`, and the
other 5 pass on BOTH sides BY DESIGN** — they are the constraint tests (#330
must not regress, a single-file commit must still clear staleness). **A guard
that never advanced would satisfy every assertion about the bug and leave every
repo reading stale forever.** Say so, or a reviewer reads them as vacuous.

**#492 — `resolve_repo` answered a repository question with a filesystem fact.**
Fast path 1 matched `source_root` containment alone, so a path inside an
independent nested clone returned the PARENT index as `indexed: true`.
⚠⚠ **Whether it LOOKS wrong depends on something irrelevant to the defect.**
Gitignored nested repo → read fails, `absent`, indistinguishable from a normal
empty result. Absorbed into the parent walk → same wrong repo, read SUCCEEDS,
`state: ok`. **Two symptoms, one mis-resolution** — and only the second case
proves it without involving absence semantics at all.
⚠ Guard is a `.git` stat, **never a subprocess**: fast path 1 exists to avoid
the `resolve_index_identity` walk that can HANG (#303), so a correctness guard
that spawned a process would trade the reported bug for the one the fast path
was built to prevent. Asserted by monkeypatching `subprocess.run` to raise.
⚠⚠ **Classify by where `.git` POINTS, not by file-vs-directory.**
`.git/worktrees/` vs `.git/modules/` is #372's distinction; submodules still
resolve to the parent because their content IS indexed into it. **A
`--separate-git-dir` clone leaves a `.git` FILE pointing at neither, and a
file/directory test reads it as a submodule** — tested by name.
⚠ A file outside the parent's corpus (gitignored/oversize/skipped) still
resolves to the parent: being outside the corpus and belonging to another
repository are different conditions.
⚠ `tests/test_resolve_repo_nested_repo_boundary.py` (11): 7 red at `b85ef61`,
4 boundary tests pass both sides by design. Submodules and linked worktrees
tested against REAL git layouts (`git submodule add -c protocol.file.allow=always`,
`git worktree add`), not fabricated `.git` markers.
⚠ Suite: **7923 passed, 17 skipped, 0 failed** + ruff clean; +21 over #490's
7919 decomposes as 10 + 11.

⚠⚠ **PROCESS TRAP, NEW AND CHEAP TO REPEAT: `gh pr merge --delete-branch` on a
PR that is the BASE of a stacked PR CLOSES the stacked PR.** GitHub normally
retargets a stacked PR when its base merges; deleting the base branch in the
same operation closes it instead. **A closed PR's base cannot be changed and it
cannot be reopened while the base is gone** — `gh pr edit --base` returns
"Cannot change the base branch of a closed pull request", `gh pr reopen` returns
"Could not open the pull request". #497 died this way and was recovered as #498
from the same intact head branch. **Merge a stacked base WITHOUT
`--delete-branch`**, or retarget the child first.
⚠⚠ **A PR stacked on a branch base GETS NO TEST MATRIX AND LOOKS CLEAN.**
`test.yml` is `pull_request: branches: [main]`, so #497 showed 3 green checks
(radar / retrieval gate / CLA) and `mergeStateStatus: CLEAN` with the matrix
never run — **the fork-PR "only license/cla ran" hazard wearing a different
costume, and `CLEAN` is the part that sells it.** Remedy is the workflow's own
escape hatch: `gh workflow run test.yml --ref <branch>` (all 9 jobs green,
run `32092744385`). **Count the checks; a green rollup is not a run matrix.**

**2026-08-18: #443's conflict was OURS for the SIXTH time, resolved on their
branch.** Three of our merges (#490, #492, #493) landed in the same
`[Unreleased]` block, and a CONFLICTING fork PR has no `refs/pull/N/merge` and
therefore NO CI. Merged `main` in, resolved to one `## [Unreleased]` with
elfrost's `#447` section first, pushed to their fork. Suite on the merged tree
**7929 / 17 / 0**, +6 = exactly their tests; all 11 CI checks green on the merge
ref; `license/cla` PENDING is the only blocker.
⚠⚠ **Six is not six incidents, it is one wrong merge order repeated** — and
this round it was avoidable in a way the earlier ones were not: **all three of
our merges happened while their PR sat blocked, and we batched none of them.**
Policy 3b governs ORDER when we have a choice; when the contributor PR is
BLOCKED and we ship anyway, the remaining lever is **how many separate
`[Unreleased]` merges we make before resolving once**. Resolve after the LAST
one, not after each.
⚠ **The CLA status was erased by our push and came back within ~2 minutes as
`pending`** — both halves of the documented hazard fired in one push (erases an
existing status, provokes a missing one). `count=0` was observed and is NOT
"cleared". Said so on the thread so eleven green checks are not read as done.

**2026-08-17: #490 (@rknighton) FIXED BY US via PR #494 — a cache that
announced readiness one key early.** The BM25 corpus cache publishes FOUR keys
behind a check-then-build guarded on `idf` alone, and
`cache["idf"], cache["avgdl"], cache["inverted"] = _compute_bm25(...)` is THREE
`__setitem__` calls, with `centrality` a fourth statement after a whole pass
over the corpus. A second caller passed the readiness check and raised
`KeyError: 'centrality'` — through the dispatcher, `Internal error processing
search_symbols`. Unreleased; see CHANGELOG `[Unreleased]`.
⚠⚠ **The window is the entire runtime of `_compute_centrality`, so it WIDENS
with corpus size** — the installs most likely to hit it are the ones where the
rebuild is most expensive. Do not file this shape as "a narrow race".
⚠ **The lock was real and correctly held; the build WAS single-flight** as #370
intended. What leaked is the readiness SIGNAL, which is read outside the lock by
design and therefore must not become true early. **Diagnose which of the two the
defect is before reaching for the lock.**
⚠⚠ **THREE modules carried the identical block** (`search_symbols`,
`get_ranked_context`, `plan_turn`), so fixing the reported one leaves two —
[[feedback_guard_every_path_that_shares_the_hazard]] again. One
`ensure_bm25_cache()` helper now serves all three; the fast path checks ALL FOUR
keys, not the sentinel, so a future reorder costs a lock acquisition instead of
a KeyError.
⚠ **`pagerank` and `name_map` were CHECKED and deliberately LEFT** — each writes
the one key it also checks, atomic by construction. Their
`getattr(index, "_bm25_lock", None) or threading.Lock()` fallback is a separate,
milder weakness (a fresh lock per caller guards NOTHING, so a lockless index
would duplicate work rather than crash); unreachable today because both
`CodeIndex` and `SelectiveIndexView` carry the lock. **Recorded rather than
swept**, same treatment as #473's module-level `perf_db_path()`.
⚠⚠ **THE FIRST SHIPPED-PATH TEST PASSED AGAINST THE BROKEN SOURCE.** Signalling
from inside the build and letting the second thread race is not enough on a
two-file corpus: the builder finishes before the racer arrives. Only when the
build is held open until the second caller is demonstrably INSIDE its call does
it go red. **A concurrency test that does not pin the interleaving is testing
its own machine's scheduler**, and the tell is the non-vacuity pass: 7 of 8 red
first time, 8 of 8 after. [[a-concurrency-test-must-pin-the-interleaving]]
⚠ His `Event` framing said this in the issue body — "it does not create a
window" — and the first test ignored it. **Read the reporter's note about their
own harness; it is usually load-bearing.**
⚠ Suite: **7902 passed, 17 skipped, 0 failed** + ruff clean. Total 7919 against
.284's 7911 = exactly the 8 new tests, so nothing else moved.

**2026-08-17: #476 (@rknighton) FIXED BY US — one telemetry db spent another's
trim.** `_perf_rows_since_trim` was one int on the `_State` process singleton
while the trim runs on `conn`, so with two stores alternating one `tool_calls`
was never trimmed. Now a dict. Unreleased; see CHANGELOG `[Unreleased]`.
⚠ **Low severity and the REPORT said so** — opt-in, local-only, single-store
installs cannot reach it, cost is disk. He rates his own findings honestly; the
standing note that he understates still holds, but check each time.
⚠⚠ **Keyed by the SAME `str(path)` the connection cache uses, and that IS the
fix.** Keyed on the raw `base_path` instead, two spellings of one directory each
get their own budget toward a trim on one shared table — the same defect wearing
a new key. v1.108.280 resolved that spelling problem for the cache after #465;
this inherits it rather than re-opening it. [[feedback_guard_every_path_that_shares_the_hazard]]
⚠ **Added `_ensure_perf_db_locked_with_key` rather than calling `_perf_db_path`
twice** — re-deriving the key at the trim site repeats that helper's `mkdir` on
every write, and #442 exists because per-write cost on this exact path was the
whole problem. Two callers keep the old connection-only signature.
⚠ `close_perf_dbs()` clears the counters with the connections so a key cannot
outlive its store; the bounded cost is ~1000 rows of slack for a database whose
connection is dropped mid-cycle, against a cap that is already an
every-1000-writes approximation.
⚠ Nothing is backfilled: an already-oversized `tool_calls` trims on its own next
cycle.
⚠ `tests/test_perf_trim_is_per_database.py` (4) asserts on the COUNTER MAP, not
row counts after 1000 writes — 2000 rows across two databases would be slow and
would pin the trim interval. **All 4 red against a restored single counter.**

**2026-08-17: #443's conflict was OURS and we resolved it on their branch.**
elfrost's PR sat `CONFLICTING/DIRTY` since 2026-08-12 — and **a conflicting fork
PR has no `refs/pull/N/merge`, so it gets NO CI AT ALL**, which is why it read as
stalled contributor work when it was our CHANGELOG merges. Merged `main` in,
resolved to one `## [Unreleased]` heading with their section first, pushed to
their fork, said on the thread that the conflict was ours. `MERGEABLE` again,
suite green (7856/17, +6 = their tests).
⚠ **Checked before promising: elfrost is a User, not an Organization**, so the
`maintainerCanModify`-lies trap did not apply and the push worked.
⚠ **The CLA status SURVIVED this push** — the documented erase-on-push hazard did
not fire here. Do not treat either outcome as the rule; read the status.
⚠⚠ **#447 was NOT implemented, deliberately.** Its window is posted publicly to
**2026-08-20** and jjg reaffirmed it stands as posted. Resolving the conflict is
the move that respects the promise AND unblocks them — it removes the reason the
PR was dark without shortening anything.

**Merged 2026-08-16: #479 (@mikemikimike) closes #475** — `IndexStore` /
`SQLiteIndexStore` keyed their init caches on the SPELLING of `base_path`, so a
relative `storage_path` skipped `mkdir` and schema setup for the second store
after a chdir. Two source lines. Unreleased; see CHANGELOG `[Unreleased]`.
⚠ **The mock cleanup is the larger half.** `patch("...Path.resolve",
return_value=X)` replaces `resolve` on the `Path` CLASS, so it answered for every
path in the process — including the storage path `IndexStore` resolves at
construction. Four `test_tools.py` tests then CREATED their index directory at
the faked location (a stray `C:\work\project` locally; `mkdir` death at
`/workspaces/myrepo` and `\\server\share\` in CI). ⚠⚠ **Nothing in the suite
could have reported it, because the writes landed where no assertion was
looking** — same family as #439's blanket `os.path.exists` mock. `_resolve_only`
in `tests/__init__.py` is narrow, and needs `autospec=True` so `self` reaches the
side effect. All 19 patch sites converted; the other 15 were wrong too, just
inert. ⚠ The `expanduser()` half MOVES an existing case (a literal `~` in
`storage_path` built a directory named `~`); disclosed, not migrated.

**2026-08-16: the suite runs in PARALLEL, and that surfaced a test living on file
ordering.** `pytest-xdist` at `-n 4 --dist loadfile`, wired into `test.yml`.
Measured on a 24-core box: **599s serial vs 183s parallel**, same 7,859
collected; CI's exact command (with coverage) is 258s locally. Test-only + CI, no
version bump; rides the next release.
⚠ **`--dist loadfile` is load-bearing.** Whole file per worker preserves
within-file order; the default `--dist load` spreads individual tests and breaks
any file sharing module-level state.
⚠ **Worker isolation is STRONGER, not weaker** — everything conftest resets
(`_GLOBAL_CONFIG`, index cache, perf-DB handles) is process-global and each
worker is its own process. What parallelism removes is the accidental
cross-FILE ordering the serial run gave for free.
⚠⚠ **The two failures it produced were NOT caused by parallelism — they
reproduce serially in isolation.** `test_css.py` and `test_json.py` imported via
`src.jcodemunch_mcp`, a **different module object** from `jcodemunch_mcp` (`is`
→ `False`) carrying its own `config._GLOBAL_CONFIG` that conftest never resets.
The twin lazily read the developer's real `~/.code-index/config.jsonc`,
`is_language_enabled` gated the language out of the `languages` allowlist, and
`parse_file` returned `[]` against a direct extractor's 10 symbols.
⚠ **Which half failed is the proof of mechanism**: `test_css.py` drives BOTH
`css` and `scss` through `parse_file` and only `scss` broke — `css` is in the
allowlist, `scss` is not. They passed serially only because `test_config.py`
(also `src.`-prefixed) overwrote the twin earlier in alphabetical order.
⚠ **Maintenance Practice #8 in a spelling its guard cannot see** —
`test_config_isolation_guard.py` knows nothing of the `src.` prefix. **14 files
still import through the twin**, and `test_al.py` / `test_blade.py` are the same
defect UNFIRED, passing only because `al` and `blade` sit in this box's config.
**Not fixed; the two live failures are.** Next sweep starts there. **DONE — see
the sweep entry immediately below.**

**2026-08-17: the package twin is RETIRED and the guard now sees the spelling.**
All 140 `src.jcodemunch_mcp` references across 14 test modules converted, and
`tests/test_config_isolation_guard.py` gained the check. Test-only, no version
bump; rides the next release.
⚠⚠ **The guard already existed and a different IMPORT PATH walked around it** —
the same shape as the defect that file was written for, where the guard existed
and the CALL SITES walked around the reset. That is why the check went INTO that
file rather than a new one.
⚠ **Two of the fourteen were live, twelve were unfired.** `test_al.py` and
`test_blade.py` are the identical `parse_file` defect and passed only because
`al` and `blade` sit in this box's `languages` allowlist.
⚠⚠ **The `patch("src.jcodemunch_mcp...")` form fails the OTHER way and is the
worse half**: it patches the twin's attribute while the test drives the canonical
module, so the patch does nothing and the test passes **without testing what it
names**. Two existed (`test_config.py:351`, `test_git_sha_verification.py:159`).
**Converting imports without converting these would have left a false green.**
⚠ Detector matches a string only when it STARTS with the twin root (the shape of
a patch target) and skips docstrings, so prose naming the hazard is not a
violation — asserted by name. ⚠ **`_TWIN_ROOT` is assembled from two literals so
the guard does not exempt ITSELF**; as one string it flags its own source line,
and exempting the file or special-casing its name both stop it policing itself.
⚠ **Non-vacuity proven against the REAL pre-fix tree**, not just synthetic
fixtures: restoring `tests/test_al.py` from `HEAD` turns it red naming lines 6-7.
`TWIN_EXEMPT` is EMPTY and its parametrize-over-nothing SKIP is the ratchet at
rest.
⚠ Suite: Windows **7850 passed, 17 skipped, 0 failed**, coverage 79.66%, ruff
clean. Delta from 7864 is EXACTLY **+3** and decomposes as +2 passing guard tests
and +1 skip (the empty parametrize) — the skip count moving 16 → 17 is the
ratchet arriving, not a lost test.
⚠ **CI pinned to `-n 4`, deliberately not `-n auto`** — GitHub runners are
4-core so `auto` matches today and would jump silently on a resize, and extra
workers contend on the same `~/.code-index` process-lock scopes that caused
.261's 47m outlier.
⚠⚠ **The local-uv lock hazard fired in its THIRD direction on a change that only
added a test runner.** Local uv 0.12.1 vs the CI pin 0.9.5 gave 76 insertions /
52 deletions, and beyond the known nvidia widening it **stripped
`python_full_version` guards off the google-api deps and `typing-extensions`**,
changing what installs on 3.10 vs 3.14. Re-locked with `uvx --from uv==0.9.5 uv
lock`: 24 insertions, 0 deletions. **Diff the lock after EVERY `uv lock`, not
just version bumps.**
⚠⚠ **It went RED on CI and the failure was a REAL production defect the serial
runner had never exercised — `call_tool` ate its caller's `format` argument.**
`arguments.pop("format")` popped from the CALLER's dict, so a caller reusing one
args object got JSON first and `server_output`'s default after. Fixed at the
dispatcher (`arguments = dict(arguments)`), not in the tests, because the Counter
front door re-dispatches through the same path. Over the wire it is unreachable —
every request arrives as a fresh dict — so only in-process callers are exposed.
⚠⚠ **It presented as an environment quirk and that is the reusable part.** The
second call falls back to `auto`, where the **15% encoding gate decides per
response**, and the response carries `timing_ms`. Coverage instrumentation slows
the call, moves that number, moves the byte count, tips the gate. Red on ubuntu
3.10/3.11/3.12, GREEN on ubuntu 3.13, green on all four Windows legs, green
locally without `--cov`, red locally with it. **Chasing the platform matrix would
have found nothing.**
⚠ **Reproduced on a WSL Ubuntu 3.12 copy, which is what made it cheap** — Windows
cannot produce it at all, and a CI cycle is 4 minutes against WSL's 3. Docker
Desktop was not running; `wsl -d Ubuntu` with a `tar`-copied tree and its own uv
was enough. ⚠ WSL interop expands `$PATH` into the command string and the Windows
PATH contains parens, so `bash -lc` dies on a syntax error — use absolute paths
and no variables.
⚠ `tests/test_dispatcher_arg_mutation.py` (3) asserts on the ARGUMENT DICT, never
on the response encoding, so it does not inherit the gate's environment
sensitivity. Reverting turns 2 of 3 red; the third is the control.
⚠ Suite with the fix: WSL Linux 3.12 **7833 passed, 0 failed** (+9 sdist errors
that are an artifact of copying without `.git`); Windows **see release line**.
Delta decomposes as 7828 + 2 fixed + 3 new = 7833. **Fold into the `Tests:` line
at release, not before.**

**2026-08-15: #428's remaining four languages IMPLEMENTED BY US (Rust, Go, Java,
PHP), closing it.** Shipped as 1.108.281 via PR #478; see Current State.
⚠⚠ **This is a REVERSAL of an open handoff, not a timebox expiring, and it was
jjg's call.** The half was @mussonking's by an offer with **no date on it** — the
standing rule is that every handoff names a date AND the default that fires on
it, and this one named neither, which is exactly how it sat seven days. Credit
for the report and for the plural-helper design stays his in the CHANGELOG.
**The process lesson is the open-ended offer, not the reversal.**
[[feedback_never_hand_off_without_a_timebox]]
⚠⚠ **Java needed more than a branch and the gate was the real defect.** The
constant walk was `parent_symbol is None`, which keeps function locals out — and
a Java constant is a class member, so `field_declaration` sat in
`constant_patterns` **unreachable by construction**. The gate now also accepts a
CONTAINER parent for `_CLASS_SCOPED_CONSTANT_LANGUAGES` (`{"java"}`), never a
function parent. ⚠ **Relaxing it for every language was DECLINED**: Python class
bodies, JS class fields and PHP class constants would all start emitting
constants they never have, moving symbol counts in every index and **every
published dead-code grade**. One named set, one sample per member, asserted by
name in `test_only_named_languages_reach_constants_through_a_container`.
⚠ **Scala looked like a counter-example and is not** — its `val_definition` is in
`symbol_node_types`, so it never touches the constant gate at all. Checking that
before copying its shape is what kept the widening narrow.
⚠ **The exclusions are the careful half**: Rust `static mut` (a
`mutable_specifier` says the binding changes), Java bare `final` (per-instance)
and bare `static` (mutable shared state). **A missing constant is a recall bug
the reporter could see; an ordinary field arriving as `kind="constant"` is a
precision bug nobody goes looking for.**
⚠ **Grammar shapes were DUMPED, not assumed** — the TOML left-recursion defect
came from assuming. Go binds N names two ways at once (`const ( ... )` groups
plus `const A, B = 1, 2`), which is what `_extract_constants` being plural is
for. ⚠ No case heuristic anywhere: `const` IS the declaration, and filtering on
case would silently drop Go's unexported lowercase constants.
⚠ `tests/test_v1_108_281.py` (10), **9 fail against `d10490e`**; the 1 passing
both sides is the control that Java function locals are still not constants.
`EXEMPT` in `test_constant_extraction_guard.py` is now **EMPTY** — the ratchet
forced its own deletion, and its parametrize-over-nothing SKIP is the ratchet at
rest, not a lost test.

**Merged 2026-08-14: #473 (@rknighton) closes #465** — the perf-db connection
cache keyed on the caller's SPELLING, not the resolved path, so a relative
`storage_path` wrote one store's telemetry rows into another's after a chdir.
Two source lines, three tests, unreleased; see CHANGELOG `[Unreleased]`.
⚠⚠ **The reusable half is where the fix went.** `_perf_db_path` has one caller
and all three telemetry sinks reach the cache through it, so resolving where the
path is BUILT fixes every consumer including the one the issue left open. Fixing
it at the cache would have covered the sinks that were reported and missed that
one ([[feedback_guard_every_path_that_shares_the_hazard]]).
⚠ **The module-level `perf_db_path()` helper is still unresolved ON PURPOSE**,
checked rather than assumed: it never touches the cache, and an unresolved
spelling opens the same file on disk. Recorded here so a later sweep does not
"finish the job" and call it a fix.
⚠ **Both exits needed the change and only one is covered by the row-level
tests** — they all pass an explicit `base_path`, so the no-argument exit carries
its own assertion. Reverting either `.resolve()` alone turns the file red, which
is the non-vacuity pass done per-edit rather than per-file.
⚠ **First timebox to expire in the contributor's favour under policy 3a**: #465
was handed off with a 2026-08-21 default, and the PR arrived in a day. The
window decides whose commit it is, and here it was his.
⚠ **His verification note is worth copying: he reported a DELTA, not totals.**
Ten test modules `importorskip` at module level, so a missing optional dep costs
a whole module as ONE skip and no per-test trace — two correct runs of the same
commit can differ by a hundred. Same class as the `--extra watch` under-collect
in the Tests line above, found independently from the other side.

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

**Closed 2026-08-15: #480 `neuforge-pay` metering pitch, at jjg's direction** —
a mass-mailed vendor solicitation, not a feature request. The same text sits in
**84 issues across GitHub**, including `Snailclimb/JavaGuide`, a Java reading
list with no endpoints and no LLM calls, so nothing about this repo was read
before filing. ⚠ **Three independent reasons, and the spam is the weakest one.**
It decorates `@app.get("/v1/query")` and jcm is a local stdio server with no
endpoint, no per-call price and no hosted session, so there is nothing to attach
to. A payment SDK is third-party **network egress plus a Merchant of Record
relationship** inside a tool that advertises itself as read-only and local — the
#382 objection exactly, and the class that caused the PyPI quarantine. And it
fails #380's demand bar: **branded providers get in when a USER asks**
(MiniMax/GLM/OpenRouter did), and one vendor pitching its own SDK to 84 repos is
not a user asking. ⚠ `neuforge-pay` itself was deliberately NOT fetched or
inspected; the decision rests on none of it.

**#381 (MCP Toplist badge) CLOSED by jjg** — 120 identical drive-by PRs from that
author; the badge renders "Top 1% of 81,432", not the rank the PR body promised,
and it is live third-party-controlled content in a README that also renders on PyPI.

9. **When a fix turns an OLD test red, check whether that test was encoding the
   defect before "fixing" the code back.** Four instances in one release cycle
   (2026-08-18/19): `test_generate_full_snippet` required EVERY canonical tool
   name to appear in the guide, so it could only pass while #495 existed;
   `test_embed_drift` pinned a literal error wording, which is how that site kept
   a stale copy through #489; `test_full_surface_still_honours_profile` asserted
   equality with the baked `_PROFILE_TIERS`, which is #507's premise; and two of
   my own in #489 asserted on the CONSTANT rather than the call site, so they
   checked the fix instead of the site.
   ⚠ **The tell is that the test states the mechanism rather than the outcome.**
   "every canonical name appears", "equals the tier table", "the message is this
   string" are all restatements of an implementation. "what it advertises is what
   it will dispatch" is the property. ⚠ A red suite invites fixing the tests; run
   the non-vacuity pass on the OLD test too — if it passes only against the
   pre-fix tree, it was the defect's witness, not its guard.

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

**2e. NEVER BATCH OUR RELEASE BEHIND SOMEONE ELSE'S CLOCK** (jjg, 2026-08-18,
after it happened). Policy 2 says a release is never blocked on an open issue.
**The way that rule gets broken is not by someone overruling it — it is by an
apparently sensible batching argument that never mentions it.**

⚠⚠ **The exact failure, recorded because it was MINE and it sounded reasonable.**
On 2026-08-18, five fixes were merged and green (#488/#489/#490's siblings,
#495). I recommended holding the release until 08-19/08-20 so it could include
#504 and #447, on the grounds that each of our releases re-conflicts elfrost's
CLA-blocked #443 and batching means resolving once instead of three times. jjg
accepted it. **That recommendation coupled our shipping schedule to a
contributor's CLA signature and a first-time reporter's availability, which is
the precise outcome policy 2 exists to prevent.**

⚠⚠ **It was also wrong ON THE MERITS, which is the part that generalises.**
Batching reduces the NUMBER of conflict resolutions, not whether they happen —
#443 conflicts on whatever release comes next, whenever that is. Each resolution
is a scripted three-way merge plus one suite run, measured at minutes. **The
trade was "finished, tested, user-facing fixes sit unreleased for two days" in
exchange for "we do a cheap chore once instead of three times." Weigh the cost
of the chore against the cost of the delay before proposing a batch; here it was
not close.**

⚠ **The timeboxes are NOT the problem and must not be "fixed".** Every one names
a default that ships the work regardless (policy 3a). A posted window decides
whose commit it is, never whether the fix ships or whether we can release. If a
window ever appears to block a release, the batching decision is what is
blocking it, not the window.

⚠ **The test, before proposing to hold a release:** name the thing being waited
for, and whether it is OURS. If it is anyone else's action — a signature, a PR, a
reply, a re-run — the answer is ship now and let them ride the next one.
Contributor work is never worse off for this: their default still fires, their
credit is unchanged, and their PR merges into a smaller diff.

⚠ **Corollary: "reduce OUR churn" is not a release criterion.** Conflict
resolution, re-runs and re-merges are our costs to absorb. The moment avoiding
them starts shaping WHEN users get fixes, the optimisation has inverted — we are
spending their latency to buy our convenience. [[never-batch-a-release-behind-someone-elses-clock]]

**3. A contributor's PR is never the only path.** Timebox it and keep our own
path warm (#388 taught this the expensive way).

**3a. NO TIMEBOX WE OFFER RUNS LONGER THAN 24 HOURS** (jjg, 2026-08-14, widened
the same day from the CLA-only version). It covers **every** shape: signing the
CLA, opening a PR already written, and taking an issue to implement. The CLA case
is the easiest to justify — CLA Assistant prompts the moment a PR opens and
signing takes about 30 seconds, so a longer window parks a finished, green,
reviewed fix behind a form — but the rule is not limited to it.

⚠⚠ **The window is only fair BECAUSE the default action preserves credit.** At
expiry we implement the fix ourselves and credit them in the CHANGELOG, the
release notes and the close comment. So the 24 hours decide whose COMMIT it is,
never whether they are credited and never whether the fix ships. Quote the
default in the same comment as the deadline — a 24-hour clock with an unstated
consequence reads as a threat, and it is not one.

⚠ **An extension the contributor ASKS FOR is not the same as a default we hand
out**, and CONTRIBUTING.md invites the ask by name. Hold it when they ask; the
clock exists to stop work going quiet, not to catch anyone out.

⚠ **Stated consequence, not hidden**: on an IMPLEMENTATION handoff, 24 hours
means in practice that we implement it and they are credited, because nobody
lands an additive-schema-plus-dispatcher change around a job in a day. That is a
change in what a handoff IS, not only in how long it lasts. It is the intended
trade — our throughput over their commit — and it should be made in the open
rather than discovered at expiry.

**3d. `license/cla` IS A REQUIRED STATUS CHECK ON `main`** (jjg, 2026-08-17).
Enabled because it was NOT one: until this date the repo had **no branch
protection, no rulesets and no required checks**, so the CLA was read but never
enforced and one distracted click could have merged unsigned code. Open PRs now
read `MERGEABLE/BLOCKED` rather than `MERGEABLE/UNSTABLE`.

```bash
GITHUB_TOKEN="" gh api repos/jgravelle/jcodemunch-mcp/branches/main/protection \
  --jq '{contexts:.required_status_checks.contexts, strict:.required_status_checks.strict, enforce_admins:.enforce_admins.enabled}'
```

⚠ **`enforce_admins: false` and `strict: false` are both deliberate.** The admin
override is what lets jjg land a merge pushed to a contributor's fork; `strict`
would force every PR to be up-to-date with `main` before merging, i.e. a rebase
after every release — the exact churn that kept #443 dark for five days.
⚠ Enabling protection also turned OFF force-push and deletion on `main`.
⚠⚠ **This composes with the status-erasure hazard and now FAILS CLOSED.** Our
push to a fork wipes `license/cla` from the new head (legacy statuses do not
follow a SHA); with the check required that reads as `BLOCKED` until the bot
re-posts, usually under a minute. Correct, and it will look like a new problem
the first time.
⚠⚠ **It does NOT solve vendor time-wasting and must not be sold as if it does.**
Signing costs a campaign nothing — #485's author has 748 merged PRs across
GitHub, so they clear CLAs routinely. The only contributor this gate blocked in
its first hour was **elfrost**, who found a real security defect. **Legal
exposure and spam are different problems; this closes the first, 3c closes the
second.**

**3c. PROFILE THE AUTHOR BEFORE REVIEWING A VENDOR-SHAPED PR** (jjg,
2026-08-17). Any PR adding a named third-party provider, gateway, SDK or
endpoint gets three queries FIRST, before a line of the diff is read:

```bash
GITHUB_TOKEN="" gh api users/<login> --jq '"created=\(.created_at[0:10]) repos=\(.public_repos) company=\(.company) bio=\(.bio)"'
GITHUB_TOKEN="" gh api "search/issues?q=is:pr+author:<login>&per_page=1" --jq .total_count
GITHUB_TOKEN="" gh api "search/issues?q=is:pr+author:<login>+<vendor>+in:title&per_page=1" --jq .total_count
```

⚠⚠ **The discriminator is the RATIO, not the volume.** A prolific contributor
is fine. #485's author had **3,089 PRs, 2,242 with "minimax" in the title
alone (73%), ~19/day since March**, and a profile reading
`company: Independent Developer`. #487's had 87 forks, 86 PRs, all OrcaRouter,
on a 7-day-old account. **Both were found in under a minute; #485 was reviewed
in depth twice before anyone looked.** That is the cost this rule removes.

⚠ **Also check whether we have a DEMAND signal**, which is the actual #380 bar
and is one query:
`gh api "search/issues?q=repo:jgravelle/jcodemunch-mcp+<vendor>"`. MiniMax
cleared it honestly as a summarizer (#184, a user asking); MiniMax TTS did not,
and the only tracker mention was the PR itself.

⚠⚠ **Quality is NOT the discriminator and must not be used as one.** #485's
diff was better than most human PRs — a real `output_format`-versus-container
finding, a three-point review addressed in hours, a self-corrected test count.
**Good work aimed at something nobody asked for is still something nobody asked
for.** Close on demand, credit the finding, and say plainly that quality was not
the reason.

⚠ **Do not assert employment you cannot prove.** State the numbers, ask the
affiliation question on the thread, and let the ratio speak. #487's author
volunteered their affiliation unprompted and it cost them nothing — that is the
contrast worth drawing, not an accusation.

⚠⚠ **A posted timebox's default can be RETRACTED IN THE OPEN when the facts
change, but never silently.** #485's clock promised that at expiry "we implement
the same change ourselves" and that the window "never decides whether the
feature ships." The authorship-and-credit half was honoured; the feature half
was withdrawn ON THE THREAD, with the reason, because it was written before the
campaign was known. **Letting a promise lapse quietly is the failure mode;
retracting it out loud is not.**

**3b. A MERGEABLE contributor PR merges BEFORE any changelog-touching work of
our own** (jjg, 2026-08-14). Not a courtesy and not a preference — a measured
cost. Every entry we add lands in the same `[Unreleased]` block a contributor's
entry occupies, so each of our merges puts their PR into conflict, and a
CONFLICTING fork PR has **no `refs/pull/N/merge`** and therefore gets no CI at
all. Their branch goes dark for a reason that has nothing to do with their
change.

⚠⚠ **Measured 2026-08-14: #443 conflicted FIVE TIMES IN ONE DAY** — twice from
our own PR merges, twice from releases, once from the docs work — and every one
was resolved by us pushing to their fork. **Five is not five incidents, it is
one wrong merge order repeated.**

⚠ **The boundary, or the rule fails on its first real case.** A BLOCKED
contributor PR cannot go first: #443 was unsigned-CLA the whole time, so
"contributor first" was never available. When it is blocked we ship anyway
(policy 2 — a release is never blocked on an open issue) and **we own the
resolution**: push the merge to their branch, resolve it ourselves, and say on
the thread that the conflict was ours. **This rule is about ORDER when we have a
choice, never about holding our work behind someone else's form.**

⚠⚠ **RE-READ THE THREAD FOR THE OPERATIVE DATE; DO NOT QUOTE ONE FROM HERE OR
FROM MEMORY.** Measured 2026-08-17 on #443: **2026-08-26 was posted 08-12
18:19**, elfrost accepted it on 08-13 13:05 quoting that date, and **08-20 was
posted 85 minutes later at 14:30** and reaffirmed thirteen times since. The
contributor never acknowledged the change and may still be planning around the
older date — which is the concrete harm, not the six days. **A thread can carry
two dates; only the query settles which is in force:**

```bash
GITHUB_TOKEN="" gh api repos/jgravelle/<repo>/issues/<n>/comments \
  --jq '.[] | select(.user.login=="jgravelle") | "\(.created_at[0:16])"' # then grep bodies for dates
```

⚠ The 08-12 wording also promised **"your authorship on the commit"** where the
08-20 wording promises **"credit"** — a second, quieter downgrade in the same
swap. When restating a default, restate the STRONGER posted version or say
explicitly that it changed. jjg's call on 2026-08-17 was that **08-20 stands**;
the lesson recorded here is the silent substitution, not the date.

⚠ **Do not shorten a timebox already posted.** State the new window on new PRs.
A public promise to a contributor outlives the policy that produced it, and
retracting one to save six days costs more than the six days. ⚠⚠ **Reaffirmed by
jjg when this rule widened: #447 (2026-08-20), #465 (2026-08-21) and #456
(2026-08-27) stand AS POSTED.** The new ceiling applies to timeboxes offered
after that date, and to nothing already promised.

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
