# jcodemunch-mcp — issue and PR history

Rotated out of `CLAUDE.md` on 2026-08-21 under Maintenance Practice 5, verbatim.
These entries are CLOSED history: every one names a date, and the tracker state
in them expired the moment it was written.

⚠⚠ **Never quote an open-issue or open-PR count from this file.** Run the query.
The block below already contained one internally contradictory count when it was
rotated (a `ZERO open issues` line dated 2026-07-28 sitting above a `#375 REOPENED`
entry dated 2026-07-26). That is the failure this warning exists for.

The standing lessons drawn from these entries live in `CLAUDE.md` under
**Standing lessons**; each names a date you can grep for here.

---

**1.108.307 — "A phase boundary drawn at the wrong place" (2026-08-29).**
Rotated out of CLAUDE.md's Current State on 2026-08-30 under Maintenance
Practice 5, verbatim. Lessons it earned are in CLAUDE.md: derive a skip list
from its authority, a cache invalidated on every write is not a cache, and a
phase boundary drawn at the wrong place names the wrong subsystem confidently.

- **Prior (1.108.307):** **A phase boundary drawn at the wrong place.** #557 (@Ticki84) closed by the reporter, who cloned the repo and instrumented his own long-running `watch-all` after three of our hypotheses died on the thread. ⚠⚠ **`_walk_tsconfigs` descends into Rust's `target/` on EVERY watcher event: 13.58s of a 13.75s reindex, against 0.27s once excluded** — and it fires even when the watcher reports `no indexable changes`, which rules out parsing and persistence by itself. ⚠⚠ **`_TSCONFIG_SKIP_DIRS` WAS THE FOURTH COPY OF A SKIP LIST IN THIS TREE AND THE ONLY ONE DERIVING FROM NOTHING**, while `security._SKIP_DIRECTORY_NAMES` already contained `target` (rules at `imports.py` / `security.py`). **Adding `"target"` was the REPORTED fix and would have been our own "fix the call site, leave the mechanism" error.** ⚠⚠ **Second half: `index_folder` evicted the alias-map cache UNCONDITIONALLY**, so every watcher-driven single-file re-index re-paid the walk that `_load_tsconfig_aliases`' module-level cache exists to make once. **A cache invalidated on every write is not a cache** (`index_folder.py`): a targeted run keeps the map unless a tsconfig was touched; a run that cannot know still evicts. ⚠⚠ **THE RELEASE'S REAL LESSON IS ABOUT OUR OWN INSTRUMENT: .304's phase breakdown blamed `save=9.906s` and we believed it.** `save` includes rebuilding the in-memory `CodeIndex` after the SQLite transaction, and THAT reconstruction triggers the walk — so **a phase boundary drawn at the wrong place names the wrong subsystem CONFIDENTLY, which is worse than no breakdown at all**: it sent us hunting lock contention that was never there, and `process_locks.waited_seconds` (shipped in .305 for this) correctly reported nothing. ⚠ The build-tree regression asserts BEHAVIOUR, not timing: a poisoned `tsconfig.json` inside `target/` whose aliases must never appear in the map, which holds on any machine at any speed. A separate test fails if the skip set is re-hardcoded — **the derivation is the fix; `target` being present is only its first visible consequence.** ⚠ Measured here 0.617s -> 0.003s on a synthetic 9,200-entry `target/`, and that is a LOWER BOUND on a synthetic tree, not his number. [[grep-a-persisted-field-for-its-readers]] [[close-on-criteria-not-on-judgment]]

---

**1.108.306 — "A count taken after the page, and a field nobody read"
(2026-08-28).** Rotated out of CLAUDE.md's Current State on 2026-08-29 under
Maintenance Practice 5, verbatim. Standing lessons it earned are in CLAUDE.md:
a count taken after the page describes the page, a refusal is not a zero, and
grep a persisted field for its readers.

- **Prior (1.108.306):** **A count taken after the page, and a field nobody read.** Four issues from @lilubot, two sharing a root cause and a third falling out of the first. ⚠⚠ **`get_untested_symbols` computed `untested_count = len(symbols)` AFTER the `max_results` slice and derived `reached_pct` from it, and `get_repo_health` asks for `max_results=1` because it "only needs the count"** — so the PUBLISHED health/radar test axis read ~100% reach on every repository with untested code (**4,893 of 6,352, 23.0%, published as 100**). It reached the grade and the observatory. ⚠⚠ **The sweep for other instances found NONE, and that is the finding** — `find_importers`/`find_references`/`get_dead_code_v2` all count before slicing. Invisible to any single call; `test_counts_survive_truncation.py` holds the property. ⚠⚠ **`detect_framework` persists `entry_point_patterns` into `context_metadata` on every index, and a tree-wide search found that key WRITTEN IN ONE PLACE AND READ IN NONE.** Three tools each reproduced their own answer to "is this a root?" and every one was Python (`_ENTRY_POINT_FILENAMES` is eleven `.py` names plus `Makefile`), so a Next.js repo detected ZERO entry points: v2 returned `dead_symbols: []`, and 203 of 366 "unstable" files were `route.ts` handlers whose Ca is 0 BY CONSTRUCTION. ⚠⚠ **Consuming that field naively would have been FAR WORSE than the defect: Flask and FastAPI shipped `"*.py"` in it**, and under fnmatch a `*` crosses `/`, so the first reader would have declared every Python file in a Flask repo a live root — dead-code detection off across an ecosystem, silently, with those repos' coupling denominators emptied. Removed at the source AND refused by `_is_catch_all`, gated over every profile; **directory SCOPE is what saves a pattern** (`routes/*.php` fine, `**/*.php` not). ⚠⚠ **Coupling excludes entry points from the DENOMINATOR as well as the numerator** — numerator-only shrinks a count without shrinking what it is a fraction of, **the 84.0 B -> 88.8 B sign error of .305 pointing the other way**. So an entry point with a real `Ce` problem is graded by nothing; the count and profile are disclosed. ⚠ Only the DETECTED profile excludes (see `get_repo_health.py`). ⚠⚠ **A REFUSAL IS NOT A ZERO** — v2 returning `[]` WITH a `signal_warning` became `dead_code_pct: 0.0` and an axis of 100, the strongest claim assembled from an admission that nothing was established; it withholds composite and grade through .305's own `unmeasurable_axes` now. ⚠ Toolchain manifests leave the dead-code population by NAME (nothing imports a lockfile by design; **`package.json` was reported dead by the same run that READS it to find entry points**) — never by extension, so an orphaned `data/fixtures.json` is still a finding. ⚠ #560 was VERIFIED, not fixed: all four type-only import spellings resolve. Tested anyway, because the claim rested on nothing. ⚠⚠ **Found on the way: THREE consumers reading keys their producers never emit, two in one renderer** — the post-task untested diagnostic and `assemble_task_context`'s audit stage were dark for their whole lives. **The test guarding the first was the reason nobody noticed: its mock returned the INVENTED key**, and a fabricated producer makes an absent-key defect structurally invisible to a test written about that exact code path. ⚠⚠ **The unmocked replacement PASSED against the reintroduced defect on its first version** — it asserted the name appeared in the message, and the name appears in three sections. **An assertion that does not name which producer put the string there proves nothing about that producer.** [[a-mock-can-supply-a-contract-the-producer-lacks]] [[grep-a-persisted-field-for-its-readers]]

---

**1.108.305 — "Only the reader was never fixed" (2026-08-28).** Rotated out
of CLAUDE.md's Current State on 2026-08-29 under Maintenance Practice 5,
verbatim. Standing lessons it earned are in CLAUDE.md: the shallow-clone
reader, NOT APPLICABLE vs COULD NOT MEASURE, `.get(k, default)` is not a None
guard, the host timezone selecting a test's input format, and the sdist
allowlist.

- **Prior (1.108.305):** **Only the reader was never fixed.** Nine tools run `git log --since=N days`; a shallow clone answers every churn question with a small number and exit 0, so `churn_surface` ranked nothing but complexity and the grade came out FLATTERING. ⚠⚠ **We had fixed this TWICE — Practice 6's Action `fetch --depth=1`, and the observatory's cloner (81.3 B vs 75.6 C at ONE identical commit) — and both times we made OUR clones deep.** `actions/checkout` defaults to `fetch-depth: 1`, so every user running the Action or `jcodemunch-mcp health` in their own CI kept the defect **on their own pull requests**. Third instance of the standing lesson. ⚠ `tools/_git_history.py` asks COVERAGE, not shallowness, tri-state. ⚠⚠ **THE FIRST FIX MADE THE NUMBER WORSE, which is the release.** Omitting `churn_surface` the way `runtime_coverage` is omitted took the tree **84.0 B -> 88.8 B** against a truth of **77.3 C**: dropping a LOW-scoring axis RAISES a mean. **NOT APPLICABLE and COULD NOT MEASURE are different states and only the first may be dropped silently.** `compute_radar` takes `unmeasurable_axes` and **withholds composite AND grade**; measured axes stand. Default path byte-for-byte unchanged. ⚠⚠ **Two `None` sites the tests then found, both user-facing**: `diff_radar`'s `.get("composite", 0.0)` — **the default NEVER fires for a present key holding None** — and `_verdict`, which would have printed **"no meaningful change"** on a contributor's PR on the one occasion nothing was measured. ⚠⚠ **CI caught a 3.10 break I could not reproduce on ANY local version: git renders a UTC offset as `Z`, unparseable by `fromisoformat` before 3.11 — and git only emits `Z` on a UTC host.** Runners are UTC, this box is CDT and got `-05:00`. **The host's timezone selected the input format**, so the version matrix was not the axis; the guard is a UNIT test over all four spellings. The tri-state held under the fault (`complete: None`, not a wrong verdict). ⚠⚠ **Also: `relnotes.md`, a scratch copy of the release notes, SHIPPED INSIDE THE PUBLISHED 1.108.304 SDIST** via `git add -A`. The canary tests prove NAMED bad paths are absent and could never have caught it — **a denylist catches the instance, an allowlist catches the class** — and `ALLOWED_ROOT_FILES` found a SECOND instance minutes later: `suite.log`, from this session's own gate runs. ⚠⚠ **@Ticki84 ran the new breakdown on the first build that had it and it answered at once: `save=9.906s` of a `10.000s` total, everything else summing to 0.094s** — the cost is entirely `incremental_save`. **That lock is taken BEFORE the write, so from the caller's timer a CONTENDED LOCK and a SLOW WRITE are the same number**; `process_locks` now reports `waited_seconds` and NAMES the holder past 1s. ⚠ The round `10.000s` says contention, but that is a HYPOTHESIS and shipping the instrument beats asking the reporter to test it — three earlier ones on this issue were each measured dead by him. [[a-module-that-imports-clean-has-been-tested-for-nothing]] [[a-one-directional-check-certifies-its-blind-side]]

---

**1.108.304 — "Three hypotheses, each measured, each wrong" (2026-08-28).**
Rotated out of CLAUDE.md's Current State on 2026-08-29 under Maintenance
Practice 5, verbatim. ⚠⚠ **Read it beside 1.108.307**: the phase breakdown
this release shipped is the instrument that then blamed `save=`, and #557's
real cause turned out to be inside that phase boundary rather than the
subsystem it named.

- **Prior (1.108.304):** **Three hypotheses, each measured, each wrong.** #557 (@Ticki84, Windows, ~10s to reindex one file where `index_file` took ~0.2s) drew an old version, the watcher's hash-cache reload, the `JCODEMUNCH_INDEX_CACHE_TTL` cliff and context providers. He answered all of it: **1.108.303, TTL unset, providers confirmed off FROM THE LOG'S OWN SILENCE, and the DEBUG line's `(10.31s)` is `index_folder`'s OWN duration** — so the time is inside indexing and every hypothesis we offered is dead. ⚠⚠ **He answered SIX HOURS BEFORE our next comment, which re-asked all three.** ⚠⚠ **What the hunt found instead: the fast path OPENED with `store.load_index(owner, repo_name)  # always load base for branch check`** — a full symbol hydration on every watcher event, INSIDE the block whose entire purpose is to skip loading the index and THREE LINES ABOVE the `use_memory_hash_cache` flag that exists to make the store's hashes unnecessary. **The saving that flag names was never realised on a cold read, because this ran first regardless.** Every question that path asks of it is metadata (`branch`, `git_head`, `file_hashes`, `has_source_file`, the two re-parse stamps), so it is a `SelectiveIndexView` now: **zero symbol rows, 0.172s -> under 1ms cold on 13,906 symbols.** ⚠⚠ **`parser_generation` and `racket_config_digest` HAD TO JOIN `EXACT_FIELDS` or the fix moves the cost instead of removing it** — absent from that tuple they fall through `__getattr__`, which promotes, so the per-event upgrade check would still load every symbol to read one integer. **If it lives in a `meta` row it belongs in `EXACT_FIELDS`.** ⚠⚠ **The test asserts `promoted is False`, NOT that `open_selective` was called** — the mechanism check stays green while a newly added `existing_index.symbols` hydrates the corpus behind it, which is the only regression worth catching (Practice 9's shape, caught at authoring time for once). 4 of 5 new tests fail pre-fix; the 5th guards a future regression and is honestly vacuous today. ⚠ **NOT his 10s and the thread says so** — his index is 6,352 symbols, where that load costs under a tenth of a second here. Shipped because it is wrong. ⚠⚠ **So the release's real deliverable is the INSTRUMENT: the watcher's re-index line now splits its own duration** (`[base_index= classify= read_hash= parse= git_head= save=]`, also `phase_seconds` on the result). Three rounds of guessing spent a reporter's patience; one line of one log now names the subsystem. ⚠ **A full walk emits NO breakdown rather than a zeroed one** — an empty bracket would read as "the fast path ran and cost nothing", the opposite of what happened, so absence means the fast path was not taken, which is the first thing worth knowing. ⚠ **Practice 10's first real case, and it held**: touched files 122 tests in 10s, suite once as the gate at 10:18. ⚠⚠ **A background-task banner reported "exit code 0" for a suite that NEVER RAN** (`--timeout` plugin absent, pytest exited on argparse). Only the log's own `EXIT=` line is evidence; the banner is not. [[a-trailing-command-hides-pytests-exit-code]] [[a-module-that-imports-clean-has-been-tested-for-nothing]]

---

**1.108.303 — "The measurement was the defect" (2026-08-27).** Rotated out of
CLAUDE.md's Current State on 2026-08-28 under Maintenance Practice 5, verbatim.
The standing lessons it earned are in CLAUDE.md under **Standing lessons**
("a set cannot count", "a competitor's fix list is a free defect probe").

- **Prior (1.108.303):** **The measurement was the defect.** Five instruments reported a good number about something they could not observe, and four of them were ours. ⚠⚠ **THE RUST FIDELITY HARNESS, SIX DAYS OLD, GRADED A 37.9% NAME-COLLISION RATE AS A PERFECT RUN — it keyed bare names in a SET, and a set cannot COUNT.** Proven by deleting the second symbol of every duplicated name in the fixtures: `extra` and `missing` did not move, so extracting ONE of `defs.rs`'s **108 `is_switch`** scored like extracting all 108. What it hid: `impl Foo { fn new }` and `impl Bar { fn new }` both emitted a bare `new`, kind `function`, parent None, separated only by a `~1`/`~2` id suffix — **the trait's own declaration qualified fine (`T.go`), so traits had an owner and impls did not.** `impl_item` sat in `symbol_node_types` as `"class"` for the extractor's whole life and never produced ONE symbol (no `name_fields` entry could name it), **and a container becomes a parent only if it EMITTED one** — so it is a virtual scope now, emitting nothing, which is what `syn` says an impl block is too. ripgrep @ `3fce3b5b`: 1,331/3,514 (37.9%) across 44/110 files -> **55 (1.6%)**; 2,199 symbols move `function` -> `method`. ⚠ `undercount` and `qual_mismatch` gate at 0 beside `extra`/`wrong_span`; the oracle emits `qual` and tracks its scopes. **The owner is `self_ty`, NEVER the trait** — `impl Display for Foo` puts `fmt` on `Foo`, and keying on `Display` is the same collision one level up. ⚠ Two more gaps fell out of the new buckets, invisible to everything that shipped: a `const` inside an `impl` came out bare (35 in ripgrep) because `_constant_symbol` hardcodes `qualified_name = name`, and `associated_type` (a trait's `type Carried;`) was absent from `RUST_SPEC` — `.302`'s `function_signature_item` again. ⚠⚠ **`tests/test_rust_fidelity.py` listed its three fixture names as a LITERAL in every `parametrize`** — a SECOND roster beside the frozen artifact, where only the artifact had a test keeping it honest; the new `qualification.rs` was ungated on arrival. Read off disk now. ⚠ **Practice 9 fired again**: `test_rust_fn_in_impl` asserted `kind == "function"` under the docstring *"Without the impl parent being extracted, 'new' appears as a top-level function"* — the defect written down as intended behaviour, passing only while it existed. `PARSER_GENERATION` **6 -> 7** (`qualified_name`/`kind`/`parent` on unchanged content). ⚠⚠ **#556 by @otherjoel is THIRTEEN of the eighteen entries** — twelve findings, one per commit, each measured against Racket's EXPANDER, Racket's READER, or five real package layouts on disk rather than against our own output. `#lang` is read before the grammar runs (`#lang punct` was indexing Markdown code samples; `conscript` lost 61% of definitions and FABRICATED ~100 that error recovery re-parented to module level); collection paths resolve through `info.rkt` (splitflap 0/70 -> 13 edges, congame 304 -> 624); **the `define-generics` exemption was REMOVED rather than widened, because `extra: 0` was carrying a fabrication.** ⚠ He declined the `PARSER_GENERATION` bump ON PURPOSE and substituted a per-project `racket_config_digest` stamp — **an absent key is detectable forever, a stamp equal to the constant is not**, our own `.302` lesson used against us correctly. We bumped to 7 anyway for the Rust fix, so those indexes are reached twice; **the stamp is NOT redundant** — it fires when a PROJECT edits `racket_langs`, which no global counter can see. ⚠ Also: **`.next`/`.nuxt`/`.output`/`.svelte-kit`/`.angular`/`.turbo`/`.parcel-cache`/`.dart_tool` were indexed as source** (`.next/server/**` is a TRANSPILED copy of the user's own pages — the `_build` defect a FOURTH time); **the observatory scored eleven public repos on ONE COMMIT** so `churn_surface` read churn 1 everywhere and ranked nothing but complexity (jcm 81.3 B -> 75.6 C, i.e. we were FLATTERING ourselves); **`max_nesting` counted brackets**, which in Python measures the deepest EXPRESSION (`index_folder`: 3 reported, AST truth 6); and the codex cache-hit-rate cut **cannot separate its arms BY CONSTRUCTION** — it is a ratio, so the arm with the least schema scores highest. ⚠ Three of the five were found by reading a competitor's fix titles against our tree. [[a-competitors-fix-list-is-a-free-defect-probe]] [[a-guard-covered-only-by-positive-tests-can-be-deleted]]

---

**2026-07: Codex tool-surface benchmark forensics.** Rotated out of CLAUDE.md
on 2026-08-28 under Maintenance Practice 5. The STANDING warnings stay there;
this is the full original section, verbatim.

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

---

**2026-08-24: #536 closed MANUALLY against the PUBLISHED artifact, and the first
probe was wrong.** Rotated out of CLAUDE.md's `server.py` Key Files entry on
2026-08-28 under Maintenance Practice 5; the rule it earned stays there.

A real stdio handshake to `jcodemunch-mcp==1.108.293` in a clean venv returns
`serverInfo {"name":"jcodemunch-mcp","version":"1.108.293"}` and a non-empty
`instructions`. This had to be done by hand because `__version__` is `"unknown"`
under `PYTHONPATH=src` — so a green test does not prove the wire carries a real
number, **and CI cannot close that gap either, because it runs from source too.**

⚠⚠ **The first probe was WRONG and the reason generalises: `uvx jcodemunch-mcp`
served a CACHED 1.108.275**, which predates both fixes, so the wire showed the
SDK's own version and no instructions — i.e. **exactly the pre-fix symptoms,
from a stale cache rather than a defect.** Pin the version and build a fresh venv
(`uv venv` + `uv pip install "jcodemunch-mcp==X.Y.Z"`); never probe through bare
`uvx` and never read its output as evidence about what we ship.

---

**2026-08-25: `refresh.py`'s coverage check asked only whether the corpus GREW,
and for its whole life could not see the opposite failure.** Rotated out of
CLAUDE.md's Key Files entry on 2026-08-28 under Maintenance Practice 5; the RULE
it earned stays there, this is the incident.

A source root that has moved, been unmounted, or been cleaned makes discovery
return `[]`, so `current` and `known` are both empty, nothing drifts, nothing
errors, and the campaign stamps the target `parser_generation` having re-parsed
ZERO files. **UNREPAIRABLE — a stamp equal to the constant is indistinguishable
from a genuine one, so the tool built to prevent the exempt bucket was putting
indexes INTO it.** It now refuses on `corpus_unreadable` (discovery empty, index
not) and `index_unreadable` (`_index_files` returned None — UNKNOWN blocks, the
same rule as `has_any()`).

⚠ EMPTY-vs-NON-EMPTY deliberately, NOT a shrink threshold: a repo may
legitimately lose most of its files, so partial shortfall is DISCLOSED as
`indexed_files_not_reparsed` rather than guessed at.

⚠ **Found by running the documented command on the three pinned benchmark
corpora** — bare `.git` dirs, 8,220 stale symbols, all three stamped in under a
second. The lesson is in Standing lessons as
"a one-directional check certifies its blind side".

---

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

**2026-08-20: #447 (@elfrost) IMPLEMENTED BY US via PR #519 at timebox expiry;
#443 CLOSED with credit.** `install-pack`'s pre-scan rejected a leading separator
and `..`, which is necessary and not sufficient: `C:/Windows/Temp/evil.txt`
carries neither, and `base / relative` DISCARDS `base` when `relative` is
absolute. `mkdir(parents=True)` ran BEFORE the write, so a hostile member created
directories outside the install root before any content existed. Unreleased.
⚠⚠ **THE PROVENANCE IS THE FIRST THING TO SAY, EVERY TIME.** elfrost found it,
analysed it and wrote a correct fix. We shipped our own pre-existing
`_safe_content_path` pattern applied to the call site that lacked it — an
INDEPENDENT path, not a clean-room copy — and said exactly that on both threads.
⚠ **Confinement by RESOLUTION, never by pattern.** A string test cannot finish
enumerating separator and drive spellings; resolving and comparing does not have
to. The pre-scan stays as an EARLY ABORT with the per-member check as the
authority — two checks, one authoritative, recorded at the call site.
⚠⚠ **The rule had THREE spellings already** (`security.validate_path` + a private
copy on `IndexStore` + another on `SQLiteIndexStore`) **and the new call site
would have been a fourth.** `security.resolve_within()` is the one definition now;
`SQLiteIndexStore` keeps its resolved-base cache by PASSING IT IN, so the hot path
survives without duplicating the rule to preserve it. A ratchet fails on a
`commonpath` anywhere else in `src/`.
⚠⚠ **THE FIRST REGRESSION TEST PASSED AGAINST THE UNFIXED SOURCE, AND THE
NON-VACUITY PASS WROTE A REAL FILE INTO A REAL WINDOWS SYSTEM DIRECTORY.** It
named the reported path verbatim, so the escape went OUTSIDE the directory the
assertion searched — invisible to `tmp_path.rglob`. **A test for an
ARBITRARY-WRITE defect EXECUTES that defect every time you prove it is not
vacuous, so the target must be somewhere the test OWNS.** Rebuilt against a
`tmp_path` sentinel; the artifact was deleted.
⚠ **The refusal is deliberately NOT platform-pinned.** `C:/...` is absolute on
Windows and an ordinary relative name on POSIX, where resolving it under the base
is CORRECT. Assert confinement; asserting that a string is refused writes platform
trivia into a security test.
⚠ **A second test of mine asserted an OS ACCIDENT** — that an embedded NUL fails
to resolve — and **passed serially while failing under xdist**, where the longer
worker temp path takes the other branch. The rule is that a RAISING resolve
refuses; which inputs happen to raise is the OS's business. Same tell as
Maintenance Practice 9: it stated a mechanism instead of an outcome.
⚠ Suite: **8083 passed, 17 skipped, 0 failed** + ruff clean, all 12 CI checks
green. 3 red at the call site, 1 red on the one-definition guard.
⚠⚠ **#443 cost EIGHT DAYS and SEVEN of our own conflicts and bought nothing.**
See policy 3a, now absolute at 24 hours.

**2026-08-20: the licence identifier is MAJOR-ONLY, and jdoc/jdata are synced.**
@marcelruhf is a PLATFORM CUSTOMER operating an allowlist against this
identifier; jjg's standing instruction is top-tier consideration, bounded by no
harm to the rest of the user base. Unreleased (jcm PR #521).
⚠⚠ **His cheapest-looking option was the one worst FOR HIM and that is the
reusable half.** Dropping the version entirely means a substantive re-licence is
INVISIBLE to every allowlist — the identifier keeps matching while the terms
change. **It buys us zero churn by moving risk onto the licensee.** Keeping
`-1.1` churns him for a typo. Major-only says the thing he needs: minor is
editorial, major means read it again.
⚠⚠ **WE HAD ALREADY BROKEN THAT PROMISE ONCE, and checking is what found it.**
`f3c925c` (2026-07-10) ADDED a redistribution and attribution obligation to
LICENSE condition 2 while the header stayed at `Version 1.1`. **Nothing failed,
because a version line is a CONVENTION and conventions do not fail builds.** So
the terms text is pinned by DIGEST: any edit fails, and clearing it forces the
substantive-or-editorial choice AT the edit rather than downstream. **The test
cannot make that judgement and does not try — it makes the judgement happen.**
⚠ **Do it NOW was part of the answer, not a separate question.** .288 is the ONLY
release that ever carried an identifier, and PyPI metadata is immutable per
version, so every later release widens the transition. **Deferring a metadata
decision for discussion is not free when the cost grows monotonically.**
⚠⚠ **The digest was RED on all four Ubuntu legs and GREEN on all four Windows
legs**: it hashed RAW BYTES, and git rewrites line endings on checkout, so it
pinned a property of the CHECKOUT rather than of the terms. **A licence says the
same thing in either encoding.** Normalise before hashing. Second
platform-shaped self-inflicted test defect in two days (the other was xdist).
⚠ **jdoc #122 / jdata #4 (same reporter) MERGED, and BOTH had held CI.** Only
`license/cla` was reported — the matrices had runs sitting `action_required` and
had NEVER run: four on jdoc, two on jdata. `fork-pr-contributor-approval` was
`first_time_contributors` on both; relaxed to match jcm, which fixed it
2026-08-13. **A setting fixed in one repo of a suite is fixed in one repo.**
⚠ **He ported #518's ratchet into both siblings unasked and his version is
BETTER**: mine asserted a version suffix EXISTS, his makes a `Version` line and a
suffix imply each other BOTH WAYS — those LICENSE files state no version, where
mine would have demanded one. Adopted back into jcm as #520. **Ours was right
only about this repo's accident.**
⚠ Policy 3a/3b now present in jdoc and jdata CLAUDE.md; they carried policy 3 as
a single line and had neither the 24-hour ceiling nor the held-run diagnosis.

**2026-08-20: #517 (@marcelruhf) MERGED; #518 finished it.** PyPI published the
entire LICENSE text as `info.license` because `license = { file = "LICENSE" }`,
so a commercial user could not allowlist us BY IDENTIFIER — there was no
identifier to allowlist. PEP 639 now:
`license = "LicenseRef-jCodeMunch-Dual-Use-1.1"` + `license-files`, classifier
dropped. Unreleased.
⚠ **Verified on BUILT ARTIFACTS, not on the diff**: `License-Expression` +
`License-File` at `Metadata-Version: 2.5`, LICENSE still at
`dist-info/licenses/LICENSE`, `twine check` green on both.
⚠⚠ **He could see ONE surface and we declare the licence on THREE.**
`.claude-plugin/plugin.json` and the mcpb manifest both said `LicenseRef-Dual-Use`
— no product prefix, no version — so an allowlist keyed on the identifier still
needed two entries. **That is the reported defect one surface over**, the same
shape as #515 the day before. mcpb now DERIVES it from `pyproject.toml`.
⚠ **The version suffix is load-bearing.** LICENSE 1.2 must produce a NEW
identifier, or an allowlist that approved 1.1's terms keeps matching terms nobody
read. `test_license_identifier_agreement.py` pins the suffix to the file's own
`Version` line. **Raised the recurring-cost trade-off with the reporter rather
than deciding it for him** — he is the one operating an allowlist.
⚠ **PyPI metadata is IMMUTABLE per version**, so none of this reaches PyPI until
the next release and 1.108.287 keeps the full text. Said so on the thread; a
contributor who fixes packaging metadata needs to know when it takes effect.

**2026-08-19: #515 (@rknighton) FIXED BY US via PR #516 — the reference table
gave the wrong default.** `CONFIGURATION.md`'s Tools row read `[]` while
`DEFAULTS["disabled_tools"]` ships `["test_summarizer"]`, so a reader expected
91 canonical tools in the schema and found 90. Unreleased.
⚠⚠ **FOUR SURFACES DESCRIBE THIS DEFAULT AND THE THREE THAT AGREE ARE THE
POINT.** The generated config template, the `config --init` comment and
`test_guide_respects_disabled_tools.py`'s pin all state it correctly; only the
reference page disagreed, and it is the page a user opens when a tool they
expected is missing from the schema. **A value pinned by a test can still be
mis-documented — the pin guards the value, not every claim about it.**
⚠ **`tool_tier_bundles` was wrong the same way and he scoped it out**: documented
`{}`, ships populated. He was right that nothing observable changes (set-identical
to the `_TOOL_TIER_CORE` / `_TOOL_TIER_STANDARD` fallback the row already
described, verified). Fixed anyway — a cell that is accidentally harmless is still
a cell that will be read, and leaving it would have forced the ratchet to carry it
as an unexplained exception.
⚠⚠ **`tests/test_configuration_md_defaults.py` is the deliverable**, written over
the TABLE rather than the two reported rows: it parses every `| Key | Type |
Default | Description |` block and compares each cell against `config.DEFAULTS`,
so the next key someone documents is covered on the commit that documents it.
3 red pre-fix, 63 green after. The cross-check over all 60 documented keys found
exactly the two he named — his "every other documented default matches" held.
⚠ **The exemption is load-bearing, not a hole.** `tool_tier_bundles` cannot be
inlined so its cell is prose, and the test asserts its repr is genuinely too long
to fit — a small wrong value cannot hide behind the same escape hatch — plus it
asserts the claim the prose makes rather than trusting it. **An exemption that
does not police itself is how the ratchet becomes the next defect's cover.**
⚠ Suite: **8067 passed, 17 skipped, 0 failed** + ruff clean, all 12 CI checks
green on the merged SHA. Same-tree collect 8084 with the new file / 8021 without
= exactly its 63.
⚠⚠ **#443 conflicted for the SEVENTH time, on a DOCUMENTATION merge.** Policy 3b
governs order and was unavailable — #443 is CLA-BLOCKED, so it cannot go first —
so we shipped and owned the resolution. `license/cla` SURVIVED this push
(`pending`, count=1 on the new head), which is a genuinely unsigned state and not
an erasure. **Read the status; the tally is 3 erased / 3 returned now.**
⚠ **`gh pr checkout` sets the branch's upstream to the FORK but `git push origin`
still means OUR repo** — pushing the resolution with `origin` created a stray
branch in `jgravelle/jcodemunch-mcp` instead of updating theirs, and the PR stayed
`CONFLICTING`. Push to the FORK REMOTE by name (`git push elfrost HEAD:<branch>`);
the tell is the PR not changing state after an apparently successful push.

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
⚠⚠ **CLA erase-on-push tally, measured across five resolutions of #443: ERASED 3 times; RETURNED on its own twice; on 2026-08-19 it did NOT return within 5 minutes and was still absent when we stopped watching.** So the "our push provokes it back" note is a TENDENCY, not a remedy. ⚠ It does not block the CONTRIBUTOR — signing posts a fresh status against the current head — but the PR then shows eleven green checks and NO cla row, which reads as "done, waiting on them" and is the opposite. **Say so on the thread every time**; do not close+reopen to chase it (1 success / 2 failures, and it notifies them for nothing).
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

---

## Appendix: rotated release entries

Rotated out of `CLAUDE.md` **Current State** under Maintenance Practice 5
(3 newest releases), verbatim. Newest first.

- **Prior (1.108.295):** **What the guard could not see.** Four items, three of them a check that could not observe the thing it claimed to check. **(1) `_build`.** We skipped `build` and `.build` and NOT the underscore spelling — what Elixir/Mix, Sphinx and Dune use. ⚠⚠ **`mix` copies dependency SOURCES into `_build`, so an Elixir project indexed EVERY dependency symbol twice** and the copies competed with the originals in ranking: the v1.108.234 duplicate-source-tree defect wearing a third name. ⚠ Bounded by gitignore, listed anyway — `build/` is in that same gitignore. **(2) The strict deny (#541).** `_bash_targets_outside_roots` reads path tokens out of the RAW command string, so `grep ~/x.md` was ALLOWED and `grep $HOME/x.md` was DENIED — same destination, opposite verdicts. **A deny now requires a RESOLVABLE target**, the caution already applied to `find`, pipelines and `../`; it downgrades to a nudge, never to silence. ⚠⚠ **The detector is deliberately NOT a bare `\$`** — a trailing `$` is a regex end-anchor and `grep "foo$" src/` is idiomatic, so suppressing on any `$` would silently weaken the enforcement a strict user opted into. ⚠⚠ **A SECOND blindness surfaced FROM that test**: `_BASH_PATH_TOKEN_RE` matched only POSIX roots, so `/c/Users/j/x.md` was seen and **`C:/Users/j/x.md` was not** — a strict deny on a path outside every root, **on the platform most users are on**, with the verdict depending on how the drive was spelled. Genuinely resolvable, so a real fix not a downgrade. **(3) The cache hit-rate.** `analyze_perf` published `hit_rate` bare, where a hit is KEY-PRESENCE in the session LRU. arXiv:2608.20280 measured raw 51-60% falling to **1.1-2.2%** once validity was checked. ⚠⚠ **The system already knew the difference and the metric did not** — #377 item 3 revalidates cached ABSENCES, #404 re-annotates row freshness. Raw rate KEPT with `hit_rate_basis`, three buckets beside it, `hit_rate_revalidated` **None not 0.0** when nothing was validated. ⚠ Only `search_symbols` revalidates, so `hits_unvalidated` is non-empty BY CONSTRUCTION. **(4) Docs**: the `mcp_toolset` `default_config` defer path, and the vendor's **30-50 tools** degradation threshold against our 91. [[a-ratchet-can-pass-against-the-defect-it-names]]

- **Prior (1.108.288):** **The reported surface was never the only one.** Four fixes; in three of them the report named one site and the tree held several — which is .287's own finding ("we fix the reported call site and leave the mechanism") acted on BEFORE shipping rather than after. **#447** (@elfrost) — `install-pack`'s pre-scan rejected a leading separator and `..`, necessary and NOT sufficient: `C:/Windows/Temp/evil.txt` carries neither, and `base / relative` **DISCARDS `base`** when `relative` is absolute, with `mkdir(parents=True)` running BEFORE the write. Confinement is by RESOLUTION now; the pre-scan stays as an early abort. ⚠⚠ **The rule had THREE spellings already** (`security.validate_path` + a private copy on each index store) **and the new call site would have been a fourth** — one definition in `security.resolve_within()`, both stores delegating, ratchet on a stray `commonpath`. ⚠⚠ **THE FIRST REGRESSION TEST PASSED AGAINST THE UNFIXED SOURCE AND ITS NON-VACUITY PASS WROTE A REAL FILE INTO A REAL WINDOWS SYSTEM DIRECTORY**: it named the reported path verbatim, so the escape went OUTSIDE the directory the assertion searched. **A test for an ARBITRARY-WRITE defect EXECUTES that defect every time you prove it is not vacuous — the target must be somewhere the test OWNS.** ⚠ Refusal deliberately NOT platform-pinned: `C:/...` is absolute on Windows and an ordinary name on POSIX. ⚠ **Implemented BY US at timebox expiry; elfrost found it, analysed it and wrote a correct fix #443 could not merge for CLA reasons — provenance stated on both threads.** **#517** (@marcelruhf) — `license = { file = "LICENSE" }` made PyPI publish the whole licence TEXT as `info.license`, so a commercial user had no identifier to allowlist. PEP 639 expression now. ⚠⚠ **He could see ONE surface; we declared it on THREE** — plugin.json and the mcpb manifest both said `LicenseRef-Dual-Use`, so an allowlist still needed two entries. ⚠ **The version suffix is load-bearing**: LICENSE 1.2 must produce a NEW identifier or consent to 1.1's terms is inherited by terms nobody read; the ratchet pins the suffix to the file's own `Version` line. ⚠ PyPI metadata is IMMUTABLE per version, so it starts HERE and 1.108.287 keeps the full text. **#515** (@rknighton) — `CONFIGURATION.md` documented `disabled_tools` as `[]` against a shipped `["test_summarizer"]`. ⚠⚠ **FOUR surfaces describe that default and the THREE that agree are the point** — template, `config --init` comment, and a TEST PINNING THE VALUE. **A value pinned by a test can still be mis-documented; the pin guards the value, not every claim about it.** Ratchet compares EVERY `Default` cell in the document. **#504** (@lsg1103275794, his PR) — the v1.96 collision guard assigned `_merge_with_existing` on a matching `git_root` with NO `walk_prefix` test, so a full-root re-walk could never reach the incremental branch and every scheduled freshness check rebuilt the corpus. ⚠ **DISCLOSED MIGRATION**: one rebuild per index to establish `source_roots == [""]`. [[push-to-the-fork-remote-by-name]]

---

## Appendix: `Tests:` line history (rotated 2026-08-21)

Per-release suite counts for 1.108.286 and earlier, verbatim from `CLAUDE.md`.
The standing warnings drawn from these runs stayed in `CLAUDE.md`; what is here
is the per-release evidence behind them.

⚠ Prior (1.108.286): 7976 passed, 17 skipped, **0 failed**; ⚠ Prior (1.108.285): 7945 passed, 17 skipped, **0 failed**; ⚠ Prior (1.108.284): 7894 passed, 17 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: **7911 with `test_code_index_path_is_honoured.py`, 7902 without = exactly its 9**, and 7902 is .283's total, so nothing else moved. ⚠⚠ **This release also measured the REAL STORE, which no pass count can show**: a full run now CREATES nothing under `~/.code-index` and emits no `_watcher_*.signal`, so the process-lock scopes are isolated. `_savings.json` and `session_stats.json` still move, because `token_tracker` was deliberately left on the home default (see Current State). **Assert the side effect, not just the exit code.** ⚠ Prior (1.108.283): 7883 passed, 17 skipped, **0 failed**, and the 3.13 CI-env reproduce returned the SAME totals AND the same skip split — stronger than the usual same-total-different-split. ⚠⚠ **TWO INDEPENDENT FALSE-GREEN MECHANISMS were found across these two releases and BOTH reported `exit code 0`.** (1) `PYTHONPATH=src python -m pytest tests/ -n 4 --dist loadfile` — **pytest-xdist lives in the dev group inside `.venv` and is INVISIBLE to a bare `python -m pytest`**, so pytest rejected the flags, collected NOTHING, and exited 0 while the harness reported success. **Use `uv run pytest` whenever xdist flags are passed.** (2) **A trailing `| tail` swallows pytest's exit status** — a run with one real failure was reported as "exit code 0", because the pipeline's status is tail's. **Write to a log and echo the exit code BEFORE any pipe**; every number in this line was obtained that way. ⚠ Local suite is `uv run pytest tests/ -n 4 --dist loadfile` at ~200-300s against ~600s serial; CI pins `-n 4`, deliberately not `-n auto`. Prior (1.108.282): 7849 passed, 10 skipped, **0 failed** **+ `uv run ruff check src/` clean**. Prior (1.108.281): 7848 passed, 10 skipped, **0 failed**. ⚠⚠ **Reconciled by a SAME-TREE COLLECT against `origin/main`, and arithmetic against the previous release line would have been wrong by 16** — `main` moved twice between .280 and this bump (#474's 14 tests, #477's 2), so the usual "delta from the last release" method had two unrelated merges inside it. **Pick the method that matches how the work landed.** Measured: **7858 collected on the branch vs 7847 on `d10490e`, +11.** Decomposition: `test_v1_108_281.py` **10**, plus a net **+1** from the ratchet rearranging — four languages leave `EXEMPT` and join `test_declared_constant_pattern_extracts_a_constant` (+4) while the four `test_exemptions_are_not_stale` cases collapse into ONE empty-parametrize item (-3). ⚠ **The 10th skip is that empty parametrize** and is the ratchet AT REST, not a lost test; it re-arms the moment anyone adds an exemption (same shape as `_JS_VARIANT_EXEMPT` in .273). ⚠ The release commit adds no tests, so the post-bump run reproduces the pre-bump 7848/10 exactly. ⚠ 3.13 CI-env reproduce **7842 passed / 16 skipped, the SAME 7858 TOTAL**, different skip split, via `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q`. ⚠ Run SEQUENTIALLY after the local suite, never alongside it — two full runs share `~/.code-index` process-lock scopes and contention is the documented cause of .261's 47m outlier (.280 records the reversal). Prior (1.108.280): 7822 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .279's 7828 total is EXACTLY the 3 new `test_perf_db_path_resolution.py` tests, and that release carried no other code, so nothing else could have moved. ⚠⚠ **The two suites were run in SEQUENCE, not in parallel, and that was a deliberate reversal mid-release.** Both were started together, then the 3.13 arm was killed before it produced anything: two full runs on one box contend for the same `~/.code-index` process-lock scopes, and **contention is the documented cause of .261's 47m outlier**. A false red costs a re-run and, worse, a few minutes of reading a real-looking failure. **Sequence them; the wall-clock saving was never worth the ambiguity.** ⚠ 3.13 CI-env reproduce **7816 passed / 15 skipped, the SAME 7831 TOTAL**, different skip split, via `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q` — without `--extra watch` it collects 105 fewer and reports a clean pass (see .278 below). Prior (1.108.279): 7819 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .278's 7808 total is EXACTLY the 20 new `test_schtasks_locale.py` tests; the release's other half is docs-only, so nothing else could have moved. ⚠ 3.13 CI-env reproduce **7813 passed / 15 skipped, the SAME 7828 TOTAL**, different skip split. Prior (1.108.278): 7799 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Reconciled by DECOMPOSITION against .277's 7797 total: `test_identity_normalized_tier.py` 10 (#458) + `test_schema_baseline_transcription.py` 2 (#467) **- 1 REMOVED** (`test_the_core_compact_schema_budget_is_unchanged`) = **+11**, and 7797 + 11 = 7808 exactly. **A removal is part of the delta and the usual add-only arithmetic hides it.** ⚠⚠ **THE DOCUMENTED 3.13 REPRODUCE COMMAND UNDER-COLLECTS BY 105 TESTS, and it reports a clean pass while doing it.** `uv run --python 3.13 python -m pytest tests/ -q` collected **7703** against the local 7808. The missing 105 are ENTIRELY three watcher files (`test_watcher_serve.py` 49, `test_watcher_lock.py` 40, `test_watcher_dynamic.py` 16), each gated on `pytest.importorskip("watchfiles")` — and `watchfiles` is an OPTIONAL extra. **CI installs it** (`uv sync --locked --group dev --extra watch`, `test.yml:84`); the documented command does not. **Use `uv run --python 3.13 --group dev --extra watch python -m pytest tests/ -q`** — that run is **7793 passed / 15 skipped, the SAME 7808 TOTAL**, different skip split. ⚠ **The totals convention is what caught it**: passed counts alone read as a plausible pass either way, and a whole subsystem being absent is invisible from `N passed`. ⚠ **Do NOT read this as .277's number being wrong** — 7782 + 15 = 7797 is internally consistent, so that run DID collect the watcher tests. `uv run` reuses an already-synced environment, so the same command can collect differently depending on what last synced it. **The command is unreliable, not that record.** Prior (1.108.277): 7788 passed, 9 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠⚠ **This release adds NO test file of its own and a flat delta would have been the RED flag on any other release** — every prior one ships a `test_v1_108_NNN.py`, so "no new tests" normally means the bump outran the work. Here the work landed across the day in #459/#462/#463/#464 and the release commit is version metadata + changelog + rotation only. **Reconciled by DECOMPOSITION rather than a same-tree collect**, because the collect diff has nothing to subtract: `test_html_file_class.py` 4 (#459) + `test_v1_108_277.py` 6 (#462) + `test_pid_reuse_identity.py` 10 (#451 via #464) + `test_claude_md_rotation.py` 4→9 = +5 (#463) = **25**, and .276's 7763 + 25 = 7788 exactly. ⚠ **Pick the reconciliation method that matches how the work landed**; applying the usual one here yields a zero and proves nothing. ⚠ 3.13 CI-env reproduce: **7782 passed / 15 skipped**, same 7797 TOTAL, different skip split — compare totals across interpreters, never passed counts. Prior (1.108.276) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7772 total, 7753 with `test_v1_108_276.py` ignored (= its 19); the **+5 over .275's 7748 is five new `def test_` functions in `test_tools.py`** from the #438/#439 drive-root work — COUNTED in `git diff v1.108.275..HEAD`, not inferred, because "nothing else moved" was not true this release and asserting it would have been the same shape of error the count notes below are about. ⚠⚠ **The 3.13 CI-env reproduce totals the SAME 7772 but splits 7757 passed / 15 skipped** — six tests that RUN on 3.10 SKIP there. **A passed-count comparison ACROSS interpreters is meaningless; compare TOTALS.** ⚠ **The 9th skip is the POSIX-only orphaned-inode test for #442** — Windows refuses to unlink a file with an open handle, so this box CANNOT produce that case. **Do not read that skip as cross-platform coverage**; it is a real local gap covered only by the portable unit test for the predicate. Prior (1.108.275) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7748 total, 7734 with `test_v1_108_275.py` ignored (= its 14), and 7734 is exactly .274's total, so nothing else moved. Prior (1.108.274) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7734 total, 7728 with `test_security_disclosure.py` ignored (= its 6), and 7728 is exactly .273's total, so nothing else moved. ⚠⚠ **This line was briefly written with a GUESSED number before the run finished, and the guess (7734) was the TOTAL rather than the passed count — it would have read as a plausible, wrong figure.** Never pre-write a count; the run is the only source. Prior (1.108.273) **+ `uv run ruff check src/` clean**. ⚠ Reconciled by same-tree collect: 7728 total, 7717 with `test_v1_108_273.py` ignored (= its 11), and the +1 over .272's 7717 is `next` ENTERING the #435 sweep now that its exemption is gone. ⚠ **The 8th skip is EXPECTED and is not a lost test**: `_JS_VARIANT_EXEMPT` is empty, so the ratchet parametrizes over an empty set and pytest skips it ("got empty parameter set"). That is the end state of a ratchet that did its job; it re-arms the moment anyone adds an exemption. ⚠⚠ **A version bump MID-RUN voids the run** — the rotation gate compares CLAUDE.md to `pyproject.toml`, so a suite spanning the bump is not evidence. Bump and rotate FIRST, then run once. (Done wrong on .273 and the run was discarded.) Prior (1.108.272) **+ `uv run ruff check src/` clean**. ⚠ Delta is EXACTLY the 9 new `test_v1_108_272.py` tests, reconciled by COLLECTING the same tree twice (7716 with the file, 7707 with it `--ignore`d) rather than by arithmetic against this line. ⚠⚠ **That method was forced, because this line was STALE by ~239 for two releases** — it read "7470 (1.108.269)" while .270's 31 and .271's 124 were never folded in, so the documented baseline was unusable as one. **A count that is only ever appended to during a release rots the moment a release skips it**; prefer a same-tree collect diff, which cannot go stale, and treat this number as a report rather than a baseline. ⚠⚠ **The count was mis-reported once during this release and the ARITHMETIC caught it, not the reading** — an intermediate run was quoted as "7469 passed, 0 failed", a combination that never happened: it was 7469 passed WITH 1 failed, totalling 7470. **Always reconcile passed+failed against the prior release's total plus the new test count**; eyeballing `N passed` at the end of a 17-minute run is how a red run gets read as green. ⚠ The failure was the CLAUDE.md rotation gate correctly refusing a Current State naming 1.108.269 while `pyproject.toml` still read .268 — **the gate fires BEFORE the version bump lands, so a red rotation test mid-release is expected and must not be waved through as "just the gate"**; it clears only when every pin site agrees. **Prior (1.108.268):** 7436 passed, 7 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .267's 7428 is EXACTLY the 8 new `test_stdio_guard.py` tests; nothing else moved. ⚠⚠ **The CLAUDE.md rotation gate caught a real mistake this release** — a 4th entry was added without demoting .267 or moving the `Older releases` boundary, and the gate failed the build rather than letting the history drift. **Prior (1.108.267):** 7428 passed, 7 skipped, **0 failed** **+ `uv run ruff check src/` clean**. ⚠ Delta from .266's 7404 is EXACTLY the 24 new `test_constant_extraction_guard.py` tests; nothing else moved. **Prior (1.108.266):** 7404 passed, 7 skipped, **0 failed** (isolated worktree run) **+ `uv run ruff check src/` clean + CI all 9 jobs green on the pushed SHA**. ⚠ The delta from .265's 7394 is EXACTLY the 10 new `test_format.py` cases; nothing else moved. ⚠⚠ **Nothing moving is itself the finding** — not one existing test pinned a fusion or semantic confidence value, which is precisely why a ~5x mis-scaling shipped and survived. ⚠ **+17 after .264 shipped**: the file-IO scanner needed TWO MORE iterations (see below), test-only, no bump. ⚠⚠ **A green suite is NOT a green build** — lint was RED for four releases while this line said 0 failed. Quote ALL THREE (suite, ruff, CI) from now on. ⚠⚠ **A green suite is NOT a green build** — lint was RED for four releases while this line said 0 failed. Quote BOTH numbers here from now on, and read the CI run for the pushed SHA. ⚠ **.261's run took 47m45s against ~16-17m before and after it on the same tree** — same counts, same result, so it was machine contention and NOT a signal. Do not treat a wall-clock outlier as a regression. ⚠⚠ **A config change is the one edit whose blast radius is the whole suite** - 128 test files touch `_GLOBAL_CONFIG` directly, so a "small" resolver change is never a small run. ⚠ **The "KNOWN 12 local-ONNX `test_semantic_search` env failures" are GONE** — .207's autouse `no_local_onnx` fixture fixed them, so a local run is now fully green and **any** red is a real signal. Do not carry that 12-failure allowance forward; it papered over a real failure once already (.197 had one hiding inside it). ⚠ **Still do not eyeball the COUNT** — diff the FAILED names against the same tree with your changes stashed; for .199 and .205 that diff was empty, and for .209 the failure set was empty outright, which is the one case that needs no baseline. ⚠ **Stashing is the wrong tool when the change is already committed and pushed** — for .205 the comparison ran in a throwaway `git worktree add --detach <pre-release-sha>`, which also survives a concurrent writer in the main tree.

## Rotated from CLAUDE.md Current State at 1.108.301 (2026-08-26)

- **Prior (1.108.298):** **A campaign that saw nothing must not certify everything.** `refresh`'s pre-stamp discovery asked only whether the corpus had GROWN (`current - known`) and for its whole life could not see the opposite failure: a source root that has MOVED, been UNMOUNTED, or been CLEANED makes discovery return `[]`, so `current` and `known` are both empty, nothing drifts, nothing errors, and the campaign stamps the target generation having re-parsed **ZERO files**. ⚠⚠ **UNREPAIRABLE, which is what lifts it above a wrong number** — a stamp EQUAL to the constant is indistinguishable from a genuine one, so **the tool built to drain the exempt bucket was filling it**, and the way in was running the documented command. Found on the three pinned benchmark corpora: bare `.git` dirs, 8,220 pre-`.246` symbols, all three stamped `2` in under a second each. Now refuses on `corpus_unreadable`; ⚠ `_index_files` returning `None` refuses too (`index_unreadable`) — UNKNOWN blocks, same rule as `has_any()`. ⚠ **EMPTY-vs-NON-EMPTY deliberately, NOT a shrink threshold**: a repo may legitimately lose most of its files, so the partial case is DISCLOSED as `indexed_files_not_reparsed` rather than guessed at. Also **re-measured the benchmark reference, stale 22 days**: 27.9x -> **27.4x** vs grep-top-3, 237.3x -> **233.4x** vs read-all — our side moving AGAINST us, which is the failure Practice 4 exists for. ⚠ gin is the clean parser signal (+81 symbols from `#428`, identical corpus); express/fastapi each gained 4 files at the SAME commit, which is COVERAGE not parsing, and fastapi's symbol count did not move at all. ⚠⚠ **EIGHT artifacts mirror one run, not four** — both sync tests passed with FIVE still on August-3 figures, including README's line-3 tagline and a table whose grand total and per-repo rows were 22 days apart.  **Also ships Racket (`.rkt`/`.rktl`/`.rktd`), #548 by @otherjoel** — a custom head-symbol walker, because the tree-sitter grammar is fully HOMOICONIC (no named `define`/`struct` nodes; `(...)` and `[...]` share the `list` type), same shape as the three Lisps already here. ⚠⚠ **The PR's own point is the MEASUREMENT, not the feature**: `benchmarks/racket_fidelity/` scores the extractor against Racket's own expander over 211 files / 3,526 definitions, with `extra` and `wrong_span` **BOTH 0** and gated in CI off frozen oracle data so the check runs with no Racket installed. `syntax-original?` separates human-typed names from macro-introduced ones — without it the gap looks several times worse than it is. ⚠ `missing` (485, 86.2% coverage, **152 of 211 files completely clean**) and `callable_unknowable` (212) are REPORTED not gated, because neither is reachable by parsing more carefully. ⚠ **No `PARSER_GENERATION` bump and the reasoning is theirs, verified independently**: that counter re-parses files ALREADY in an index; `.rkt` was `wrong_extension` everywhere, so Racket arrives through DISCOVERY. Coverage, not extraction. ⚠⚠ **Known and pre-existing, disclosed by them, affects EVERY language ever added**: an explicit `languages` list — which `jcodemunch-mcp init` WRITES — never picks up a new language, and `config --upgrade` only injects missing KEYS so it cannot repair a list. ⚠ `.scrbl` deliberately unsupported ON A MEASUREMENT: it parses with `has_error: False` and yields garbage, and a green parse with an empty result is worse than no support. [[a-one-directional-check-certifies-its-blind-side]] [[a-sync-ratchet-that-checks-the-total-misses-the-rows]]

## Rotated from CLAUDE.md at 1.108.302-dev (2026-08-27)

Section: 'Open threads — verify, do not quote'. Rotated because it named
two issues whose state it could not vouch for and pointed at the live
surfaces anyway, which is the rule it was restating.

### Open threads — verify, do not quote

`#375` (Linux stall, needs a re-run not a patch) and `#377` (Phase 2 P3 edges)
were the last two carried here. Both may have moved. The catalog moratorium is
tracked in `Current State` and `ROADMAP.md`, which are the live surfaces.

## Rotated from CLAUDE.md Current State at 1.108.302 (2026-08-27)

- **Prior (1.108.299):** **A name the file never spells.** Racket `struct` forms now contribute the bindings the macro generates — `posn?`, `posn-x`, `posn-y`, setters under `#:mutable`, `make-posn` for the `define-struct` family — **names that occur NOWHERE in the file text**, so they exist in an index only if synthesised. They share the struct form's byte range, so `get_symbol_source("posn-x")` returns the form that generates it. **#549 by @otherjoel**, a follow-up to his own #548. 3,060 -> 3,438 symbols on the 211-file corpus; a real Racket project 1,754 -> 1,935. ⚠⚠ **The PR's own headline is the FABRICATION its harness caught, not the feature**: emitting `make-<name>` for every `define-struct` invented `make-base-object/c` for `(define-struct base-object/c (...) #:constructor-name NEVER_CALL_THIS)` — `#:constructor-name` REPLACES the default constructor where `#:extra-constructor-name` ADDS one. One fabricated name in 211 files, caught by the `extra` bucket that must stay at zero, **on the author of the harness**. ⚠ **Own fields only** — `(struct derived base (c))` binds `derived-c`, not `derived-a`; the supertype occupies the slot before the field list, so the fields are the FIRST list child after the name, never a fixed index and never the last list. That one rule is also what stops `#:guard (lambda (a b n) ...)` and `#:property prop:procedure (lambda (s) 1)` being read as fields, and what resolves `(serializable-struct/versions posn 1 (x y) ())` to `(x y)`. ⚠ `serializable-struct` and `serializable-struct/versions` were absent from the form table entirely and produced **no symbol at all, not even the struct name**. ⚠ `struct:<name>` deliberately not emitted (ranking noise); `#:name`/`#:extra-name` emitted as `type`, because a struct-type transformer is not callable. **Also `PARSER_GENERATION` 2 -> 3.** ⚠⚠ **#548 shipped Racket WITHOUT a bump and was RIGHT; this one needs one, and the pair is the clearest statement of that line we will get**: `.rkt` was `wrong_extension` everywhere, so the LANGUAGE arrived through DISCOVERY — a file nobody parsed cannot hold a stale parse. But .297 and .298 both parse Racket, so an index built by either holds `.rkt` at generation 2 with the old symbol set and will never re-read it. **Coverage does not need a bump; extraction does.** ⚠ Scope NARROW — three extensions, window opened 2026-08-24 — and bumped anyway because the decision is cheap today and IMPOSSIBLE next week. Also **the Racket coverage figure had THREE mirrors and the PR regenerated one**: `results.json` went 86.2 -> 86.5 / `missing` 485 -> 475 while `LANGUAGE_SUPPORT.md` and the harness README kept the older run, green either side. `tests/test_racket_fidelity_artifacts.py` derives six figures from the artifact and checks the ROWS, not the headline; `clean_files` and the 10-worst total are derived from `per_file` because no summary field carries them. ⚠ Five assertions fail against the stale tree, three pass, and the three that pass are the figures the change genuinely did not move. [[a-sync-ratchet-that-checks-the-total-misses-the-rows]] [[a-manual-stamp-drifts-from-what-it-names]]


## Rotated from CLAUDE.md: issue + release policy forensics (2026-08-28)

⚠⚠ **The RULES stayed in CLAUDE.md; this is the evidence behind them.** Each
policy there carries its operative statement, its one-line reason, and any
command a human runs. What moved here is the incident record — the measurements,
the dates, and the reasoning that produced each rule.

⚠ **Read this before proposing a change to any of those policies.** Several were
written after we broke them ourselves, and 2e in particular is recorded in the
first person precisely because the wrong call sounded reasonable at the time. A
policy argued against without its forensics is a policy argued against blind.

Verbatim copy of the section as it stood at 1.108.304+dev:

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

**2f. THE ONE CASE WHERE NOT CUTTING A RELEASE IS LEGAL — and it is narrow**
(jjg, 2026-08-20). 2e forbids holding a release behind someone else's clock.
This is not that, and the difference has to be stated precisely or 2f becomes
the loophole that kills 2e.

⚠⚠ **THE DISCRIMINATOR IS WHETHER A USER IS WAITING FOR ANYTHING IN THE BLOCK.**
In #443 we held a SECURITY FIX behind a contributor's CLA: real users, real
exposure, eight days. In 1.108.289 the entire `[Unreleased]` block is licence
metadata whose ONLY beneficiary is the customer we would be waiting on.
**Shipping it gets no user anything.** So the timing question is not "do we make
users wait" — it is "what serves the one party this release is for", which is a
different question with a different answer.

⚠ **The asymmetry that decides it: released metadata is PERMANENT per version,
unreleased metadata is FREE.** Cutting .289 before the customer confirms the
identifier form risks a THIRD spelling (.288 `-1.1`, .289 `-1`, .290 whatever),
and their allowlist fans out across all three. **This is the same immutability
argument that justified deciding FAST, pointing the other way at the release
step.** Deciding early is cheap; publishing early is not.

⚠⚠ **THE TEST, and it must be applied every time before invoking 2f: name what
is in the block and who is waiting for it. If ANY entry is a fix, a feature or a
correctness change, 2f does not apply and 2e governs — cut it now.** A block that
is entirely metadata for one named recipient is the only shape this covers.

⚠ **Both triggers fire independently and neither can be deferred**: the next
content update ships it regardless of any reply, and a reply ships it regardless
of what else is ready. **A held release with no trigger is a forgotten release**,
which is why the hold is recorded in Current State where it is read every
session, not only here.

⚠ **"We never wait for a reply to ship a fix; we can decline to CUT a release
whose only content is a thing the recipient has not confirmed."** Those are
different acts. Only the first is what 2 and 2e protect against.

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

**3d. `license/cla` IS A REQUIRED STATUS CHECK ON THE DEFAULT BRANCH OF ALL
THREE REPOS** (jjg, 2026-08-17 for jcm; extended suite-wide 2026-08-21).
Enabled because it was NOT one: until this date the repo had **no branch
protection, no rulesets and no required checks**, so the CLA was read but never
enforced and one distracted click could have merged unsigned code. Open PRs now
read `MERGEABLE/BLOCKED` rather than `MERGEABLE/UNSTABLE`.

⚠⚠ **For four days this was fixed in ONE repo of three, which is the recurring
shape.** jdoc was protected but required NOTHING; jdata had **no protection at
all**. Measured 2026-08-21 on jdata PR #5: the CLA was genuinely unsigned, the PR
read `MERGEABLE/UNSTABLE`, and nothing would have stopped the merge. **A setting
fixed in one repo of a suite is fixed in one repo** — the same sentence jdata's
own brief already carried about `fork-pr-contributor-approval`, written after a
contributor hit it first. All three now read identically: `contexts
["license/cla"]`, `strict false`, `enforce_admins false`, force-push and deletion
off.

```bash
# All three; the default branch is `main` for jcm and `master` for jdoc/jdata.
for r in jcodemunch-mcp:main jdocmunch-mcp:master jdatamunch-mcp:master; do
  GITHUB_TOKEN="" gh api "repos/jgravelle/${r%%:*}/branches/${r##*:}/protection"     --jq '{contexts:.required_status_checks.contexts, strict:.required_status_checks.strict, enforce_admins:.enforce_admins.enabled}'
done
```

⚠ **`enforce_admins: false` and `strict: false` are both deliberate.** The admin
override is what lets jjg land a merge pushed to a contributor's fork; `strict`
would force every PR to be up-to-date with `main` before merging, i.e. a rebase
after every release — the exact churn that kept #443 dark for five days.
⚠ Enabling protection also turned OFF force-push and deletion on `main`.
⚠⚠ **This composes with the missing-status hazard and now FAILS CLOSED.** ANY
new head — our push to a fork, or the contributor's own merge of `main` —
arrives with `license/cla` absent, and with the check required that reads as
`BLOCKED` until the bot posts on that SHA. Correct, and it will look like a new
problem the first time.
⚠⚠ **NOTHING IS ERASED AND THE WORD MATTERS — corrected 2026-08-24 on #535.**
This entry said a push "wipes" the status, which frames a per-SHA reporting
model as an act of destruction and sends you chasing the bot or the
contributor's signature. **Measured:** `license/cla` is a **legacy commit
status**, not a check run — every other check on the PR is a `check-run` from
the `github-actions` app, which GitHub re-runs per head automatically; a legacy
status is a record posted to ONE SHA by an external service, and a new commit
starts life with zero of them. On #535 the old head `0db4478` still read
`license/cla success` while the new head `ea12029` read `count=0`. **The old
status was untouched, and the signature — stored per ACCOUNT at
cla-assistant.io — was never in question.**
⚠ **The consequence is the useful part: the gate cannot tell "not signed" from
"not reported", so it fails closed to the same `BLOCKED` for both.** Absent
still means DO NOT MERGE. But the remedy is to get a status posted on the
current head, never to re-verify an agreement that did not change.
⚠ **"Usually under a minute" was optimistic.** On #535 the bot had not posted
30+ minutes after the new head, and it has never commented on that PR at all —
it posted the status directly, which is what it does when the author signed on
an earlier PR.

⚠⚠ **THE RE-TRIGGER, AND IT IS OURS — no contributor action, ~25 seconds**
(established 2026-08-24, #535). `license/cla` comes from a REPO WEBHOOK to
`https://cla-assistant.io/github/webhook/<repo>` on `pull_request`, not from
any in-repo workflow. **The event is not lost and the bot is not down** — the
deliveries list showed his `synchronize` arriving and answering `200 OK`, with
no status posted. **Redelivering that same event makes it post.**

```bash
HID=$(GITHUB_TOKEN="" gh api repos/jgravelle/<repo>/hooks --jq '.[0].id')
# ⚠ jq mangles delivery ids (past float precision) — read them with python.
GITHUB_TOKEN="" gh api "repos/jgravelle/<repo>/hooks/$HID/deliveries?per_page=15" > dv.json
python -c "import json;[print(x['delivered_at'][5:16],x['event']+'/'+str(x.get('action')),x['status_code'],x['id']) for x in json.load(open('dv.json'))[:8]]"
GITHUB_TOKEN="" gh api --method POST "repos/jgravelle/<repo>/hooks/$HID/deliveries/<exact-id>/attempts"
```

⚠ **Diagnose before reaching for it.** Both #535 deliveries were `synchronize`
on a `draft: true` PR and only the later one stayed silent, so **draft is NOT
the discriminator** — the distinguishing feature was a MERGE COMMIT pulling in
commits authored by us. Check the deliveries list first; a delivery that never
arrived is a different problem from one that arrived and did nothing.

⚠⚠ **NEVER POST THE STATUS OURSELVES.** We hold admin and the Status API would
clear the gate in one call. `license/cla` is a legal assertion about an
agreement, and a maintainer-authored `success` is a forged one — it would also
be indistinguishable from the genuine article afterwards. **Redelivering makes
CLA Assistant reach its OWN verdict, which is the whole difference.** If a
redeliver does not produce a status, the answer is a `recheck` comment or a
contributor push, never a hand-written status.
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

⚠⚠ **CLOSED OUT 2026-08-20, and 3a is now ABSOLUTE: 24 hours, no exceptions,
never again.** jjg, on reading the 08-12 comment on #443 offering **2026-08-26**:
"Not again. 24 hour. Tops. Ever." Every grandfathered window above has since
expired or closed and there are no live long timeboxes; **the grandfathering
clause is spent and must not be revived as precedent for a new one.** The next
posted window that exceeds 24 hours is a mistake regardless of what produced it.

⚠⚠ **The failure mode has a NAME now and naming it is the point: a CLA hostage
negotiation.** #443 went eight days — a real security fix, reviewed and green,
held behind a 30-second form, while SEVEN of our own merges conflicted its
branch. Not one of those days bought anything. The window never decides whether
the fix ships (the default action ships it) and never decides credit (the default
preserves it), so **a window longer than 24 hours purchases exactly one thing:
the chance the contributor's commit is theirs — and it pays for that chance in
the user's exposure to an unfixed defect.** Twenty-four hours is already generous
for that trade; eight days is not a trade at all.

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

## Rotated from CLAUDE.md Current State

### 1.108.308 (rotated 2026-08-30, at the 1.108.311 bump)

- **Prior (1.108.308):** **Ownership and freshness are different properties.** No user-facing fix: this release is about the fact that **we ran 1.108.293 against a 1.108.307 tree for six days and fourteen releases** while developing jcodemunch with jcodemunch. ⚠⚠ **The release checklist's eight steps are complete with respect to USERS and silent with respect to US** — none of them touch the dev box, and the package was a regular (copied) install, so nothing ever refreshed it. ⚠⚠ **`verify_package_integrity()` cannot see this and is not meant to**: it asks whether the running module belongs to the OFFICIAL distribution, a supply-chain question, and would certify a fourteen-release-old install without complaint. **Having a startup check that inspects the distribution made it feel covered.** ⚠⚠ **The version gap was not the real tell — the VERIFICATION PATH ROUTED AROUND THE PRODUCT.** Every fix that week was checked with `PYTHONPATH=src` rather than through the server; each choice was right alone and the pattern was the finding. ⚠ `install-status` reports `source_drift`, tri-state, and `drifted: None` (COULD NOT ESTABLISH) is never `False` — the .305/.306 defect shape, guarded by tests that fail 4-of-10 when UNKNOWN is collapsed. ⚠ All five suite packages are EDITABLE now, so tree-vs-install drift is impossible; only the restart remains, because a running server serves what it loaded. `scripts/repair-munch-installs.ps1` repairs an interpreter and REFUSES while any server runs — uninstalling one mid-session removes the `.pth` and dist-info, then dies on the locked `.exe`, leaving it unimportable. ⚠⚠ **My own two renderer tests reproduced #559**: a hand-written report stub that did not match the producer, dying on a key `install_status()` actually emits. They build on a real report now. [[a-mock-can-supply-a-contract-the-producer-lacks]]

### 1.108.309 (rotated 2026-08-30, at the 1.108.312 bump)

- **Prior (1.108.309):** **A mean hides the tail, and a default invents a comparison.** Two instruments reported a confident number about a distribution they could not see, both found by reading an external audit against our own tree (Revenium, 2026-08: **top 1% of 14,680 agent runs carried 46% of spend, top 5% carried 77%**, one unattended session at 4,819 calls). ⚠⚠ **`analyze_perf`'s `_diff_baseline` read `float(b.get("p50_ms", 0.0))`, and the ONLY baseline that ships carries `tokens_saved` and no latency keys at all** — so the zero stood in for a measurement nobody took and the subtraction was published as `p50_delta_ms`, a name asserting a comparison happened. Measured against `v1.108.163.json`: a tool at p95 900 ms reported `p95_delta_ms: 900.0`, read by any human as a 900 ms regression against a release that never timed it. ⚠⚠ **Its test could not see it because the FIXTURE WAS RICHER THAN THE ARTIFACT** — the synthetic baseline carried `calls`/`p50_ms`/`p95_ms`, keys the real file has for no tool, so the fabricated path was structurally invisible to the test written about that exact code. The guard reads every baseline OFF DISK now. ⚠ Absent → `None` + `not_comparable` naming the SIDE (`absent_in_baseline`/`absent_in_current`/`absent_in_both`); **calls and tokens keep a meaningful zero on the CURRENT side, latency has none**, and inventing one is the same defect from the other end. ⚠⚠ **Second half: `slowest_by_p95` ranks how slow ONE call is and was the ONLY ranking.** Where the time went is count x latency, and the orderings disagree whenever a fast tool is called often — 4,000 calls at p95 900 ms is 100x three calls at 12,000 ms, and the report put the second first. `heaviest_by_total_ms`/`totals` answer the other question; a share over a zero total REFUSES rather than dividing. ⚠⚠ **A ring-capped tool's share is a LOWER BOUND and the cap bites hardest on the busiest tool** — the one the ranking exists to surface — so `ring_capped_tools` names them. ⚠ The per-tool shape has ONE producer now (`token_tracker.latency_bucket`); `analyze_perf` held the second copy and the two AGREED DIGIT FOR DIGIT, which is what makes a later divergence invisible. Its local `_percentile` is deleted, not wrapped — an unused copy is what regrows. `p95_is_max` is MEASURED, not derived from n, and fires for every n <= 20: two published fields carried one sample. ⚠⚠ **Same shape one module over: `analyze_regret`'s `ratio` is a MEAN**, so one need burning 400 calls inside a corpus of 1,000 reports 1.4x and the digest one-liner quotes exactly that. `concentration` (basis `excess_calls` — every need costs one call, so a share over CALLS is diluted by the floor) reports `top_need_share` + `head_share`; the digest now distinguishes two ledgers the ratio cannot. [[a-fixture-authored-from-the-schema-tests-nothing]] [[a-module-that-imports-clean-has-been-tested-for-nothing]]

## Rotated from CLAUDE.md Current State at 1.108.317 (2026-09-04)

- **Prior (1.108.314):** **A rate written for a FUTURE date is wrong for every day before it.** `receipt`'s `_MODEL_PRICES_USD_PER_MTOK["sonnet"]` read `3.0` from 2026-06-24 commented "Sonnet 5 / 4.6" — **Sonnet 5 has NEVER been $3**: it launched at $2 introductory with the $3 rise SCHEDULED for 2026-09-01, **cancelled the day before it would have applied**. The entry recorded a FUTURE price as the current one and was wrong all 69 days it stood, and **nothing distinguishes that from a stale value** — both read as a plausible number beside a plausible date, and the date makes it look checked. ⚠⚠ **THE PIN AGREED WITH IT**: `_EXPECTED_RATES` restated `3.0`, and a third assertion was a DERIVED `"$0.09"` in rendered text that no search for the rate's NAME can see — so the suite was green against a rate the vendor never charged for the model named beside it. **Re-read the SOURCE when touching a pinned table, never the other copy.** ⚠ Four copies suite-wide and this fixes ONE; ours is right only in `storage/token_tracker.py`, whose key is `claude_sonnet_4_6` — **a key naming a FAMILY inherits whichever member's price someone last looked at.** jmunch/jdoc/jdata still carry it, and **jmunch also has `claude_opus` at the RETIRED $15 (3x) on the block stamped onto LIVE responses** — a self-flattering error, the one direction a savings metric must not drift; work orders written, not fixable from here. ⚠⚠ Also: the published `counter` surface is BYTE-PINNED (`tests/test_counter_surface_stability.py`; 6 tools, **4,184 B**; name+order, per-tool sha, total, whitelist membership, `tool_profile` independence; non-vacuity 5/5) — it sits in the CACHED PREFIX, so **a reworded description is a full-rate cache write for every user**, and it pins the catalog-can-GROW property arXiv:2608.22708 is built around, which the Counter had by construction and nothing asserted. ⚠ And `CLI Subcommands`+`Env Vars` (16.6% of the budget) split to `CLI-AND-ENV.md`: 69 rows moved, 27 stayed, **the ⚠ marker under-selected AGAIN** (9 of 27 keepers). [[a-ratchet-can-pass-against-the-defect-it-names]] [[a-budget-that-names-one-section-licenses-the-rest]]

## Rotated from CLAUDE.md on 2026-09-04 (workflows layer, DESIGN section 7): registry-row measurements

⚠⚠ **The MCP registry API nests each row as `{server: {...}, _meta: {...}}`**
(schema `2025-12-11`). `name`, `version` and `packages[]` sit under `server`;
`isLatest` and `publishedAt` sit under
`_meta["io.modelcontextprotocol.registry/official"]`. **A flat `row["name"]`
read returns ZERO rows on a publish that completely succeeded** — measured
minutes after `mcp-publisher` confirmed 1.108.301, where the flat parse found
0 of 45 rows and the nested parse found all 45 with `isLatest: 1.108.301`.

⚠⚠ **This is a SECOND false negative on top of the known paging trap, and
unlike that one it SURVIVES `&limit=100`** — so the documented remedy does not
help and the symptom is indistinguishable from a failed publish. **Never
re-publish on a zero-row read; fix the parse.** Also confirm
`server.packages[].version` advanced, not only `server.version` — an entry can
move one and not the other.


