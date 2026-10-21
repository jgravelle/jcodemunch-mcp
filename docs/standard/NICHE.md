# NICHE — what jCodeMunch-MCP is for, and what it competes against

Written 2026-09-03 at commit `63a621d` (v1.108.316). Every claim here traces to a
file in this repository or a measurement recorded in `DISCOVERY.md`. Nothing was
looked up outside the repo except the two user-supplied sources in §2a.

## 1. The job, from the caller's side

**An agent calls jCodeMunch-MCP to get the exact source span that answers a
question about a codebase, at symbol granularity, in one round trip and at a
small fraction of the tokens that reading files or grepping would cost.**

Evidence: `README.md:3` (the product's own one-line claim), `benchmarks/METHODOLOGY.md`
(the measured workflow is `search_symbols(max_results=5) + get_symbol_source x3`,
`benchmarks/jcm_reference.json` key `workflow`), and the MCP `instructions` string
built in `server.py::_mcp_instructions()`, which names six core tools.

The secondary job, which grew on top of the first, is **structural questions grep
cannot answer**: importers, blast radius, call hierarchy, dead code, changed
symbols, hotspots (`README.md:151`). The tool catalog is 94 entries, 91 visible
under the `full` profile (measured, `DISCOVERY.md` §2); the retrieval core is six.

## 2. Alternatives the repo itself names

Only sources inside the tree count. Where the repo names a class but not a
product, the class is listed.

| Alternative | Where the repo names it | What the repo measured against it |
|---|---|---|
| **Read the whole file / read-all** | `benchmarks/METHODOLOGY.md`, `benchmarks/results.md` ("Read-all tokens" column) | 233.4x fewer tokens, 15 task-runs, 3 repos (`benchmarks/jcm_reference.json`, `README.md:59`). The README itself says "nobody pays that ceiling" (`README.md:61`). |
| **grep-top-3 and read** | `benchmarks/results.md` ("Grep-top-3 baseline"), `README.md:54-61` | 27.4x fewer tokens, median 25.5x, range 7.3x-79.8x. This is the baseline the headline is quoted against. |
| **Chunked RAG (512/1024/2048-token chunks, k=5)** | `benchmarks/RAG_COMPARISON_NOTES.md`, `benchmarks/rag_baseline_results.md`, `benchmarks/whitepaper.md` | RAG-512 was the cheapest RAG shape; jcm vs RAG is measured per repo and was re-measured AGAINST us on 2026-08-25 (gin flipped from "jcm 1.2x leaner" to "RAG 1.1x leaner", `CLAUDE.md` Practice 4). |
| **Odysseus (a named RAG-style MCP)** | `benchmarks/odysseus_compare_results.md` | jcm 1.2x leaner on express; RAG 4.6x and 1.1x leaner on fastapi and gin, with completeness columns showing Odysseus answered 0.0-1.6 of 5 queries completely. The token column alone flatters the alternative. |
| **Aider RepoMap** | `benchmarks/whitepaper.md` §12 Related Work | Discussed as complementary; not measured. |
| **LSP-backed same-lane MCP leader, graph-based reviewer, self-updating indexer, single-binary graph store** | `ROADMAP.md:308-354` ("Competitor head-to-head — GATED on a VM") | Deliberately NOT measured. Every candidate runs third-party code a venv cannot contain, and the project's own PyPI quarantine came from that class of undisclosed persistence. Brand names are withheld on purpose. |
| **IDE-native indexing (Cursor, VS Code, Windsurf, etc.)** | `CLIENTS.md`, `README.md:9` | Treated as HOSTS, not rivals. Nothing in the tree measures against them. |
| **Doing nothing / the agent's own Read+Grep tools** | `CLAUDE.md` "Code Exploration Policy" (the user-level instruction forbidding Read/Grep/Glob for exploration); `AGENT_HOOKS.md` (PreToolUse hooks that steer the agent away from raw reads) | This is the real default competitor: the agent already has file tools. The product ships hooks and a prompt policy BECAUSE the default wins on friction. Measured indirectly by the grep-top-3 baseline. |

Also inside the tree: `jdocmunch-mcp` and `jdatamunch-mcp` are siblings with a
declared boundary (suite `CLAUDE.md`, "Ecosystem Boundary"), not alternatives.
Markdown indexing asks (#454, #571) were declined on that boundary.

### 2a. Alternatives named outside the repo (user-supplied, 2026-09-03)

After Phase 6, jjg pointed at two sources the brief's "work from the repo"
rule had excluded. They are recorded here as a separate table so the
repo-derived list above stays distinguishable from marketing-page claims.

- `https://jcodemunch.com/versus.php` (fetched 2026-09-03): 24 products framed
  as direct competitors and 18 as complementary. ⚠ The page describes
  jCodeMunch as v1.104.1 with 4,228 tests; the tree is 1.108.316 with 9,174
  collected. Marketing copy is out of this session's scope; logged in
  `DISCOVERY.md` §11.
- `https://chatgpt.com/share/6a994862-...` (daily GitHub-activity digest of the
  direct competitors): title only, "24 live competitors fully reconciled"; the
  body renders client-side and could not be read by fetch. UNKNOWN content.

Direct competitors as the versus page names them, with the ONE claim each
leads with, verbatim from the page (none verified here):

| Product | Lead claim on the page | Axis it competes on |
|---|---|---|
| Raw file tools (Read/Grep/Glob/Bash) | jcm ~95% reduction, 58-100x on FastAPI | tokens (the default competitor, same as §2) |
| mcp-server-filesystem | 13 filesystem ops, no AST | tokens, symbol awareness |
| RepoMapper | 34+ languages, PageRank token-budgeted map | tokens, breadth |
| Pharaoh | TS + Python only, $27/mo Pro, Neo4j | breadth, cost, install |
| GitNexus | 14 languages, 45.8k stars (2026-08-26), PolyForm NC | breadth, license |
| Serena | 40+ languages via LSP, Python 3.13, edit tools | breadth, install, edits |
| Graft (NanoNets) | 22 languages, 6 tools, 33/50 SWE-bench Verified | correctness, surface size |
| GrapeRoot / Dual-Graph | 30-45% cost reduction, 37.7% fewer turns | tokens |
| vexp | 34 languages, 73% pass@1 at $0.67/task | correctness, cost |
| code-review-graph | ~65x median per-question (36x-376x), 30 tools, 30.9k stars | tokens, surface size |
| cymbal | 20 languages, ~10-40 ms query latency, Go binary | latency, install |
| Context+ | 23% fewer tokens, 25% fewer tool calls (SWE-bench Verified, 50 of 500) | tokens, correctness |
| Axon | 43 languages, Docker by default | breadth, install |
| SocratiCode | 3 languages, KuzuDB | correctness (cross-file resolution) |
| Octocode | 88% mean retrieval savings (142.8k -> 5.5k over 10 queries) | tokens |
| Repomix | 50-80% fewer tokens where compression applies | tokens |
| codebase-memory-mcp | 0.299 MRR | correctness |
| CodeGraph | 158 tree-sitter grammars, hybrid type resolution for 12 | breadth |
| SigMap | 34 languages, 96.8% token reduction across 21 repos | tokens |
| trace-mcp | 16 languages, incremental indexing | freshness |
| SDL-MCP | symbol-scoped edit tools | edits (out of our niche) |
| TokenSave | 40-50% reduction, "one call replaces ~42 min" | tokens |
| LeanCTX | 4-20x via escalation ladder | tokens |
| LemonCrow | ~30% more from a Claude subscription | tokens |

What this changes in the ranking: nothing in order, three things in emphasis.
(1) **Token reduction is the axis every rival quotes a number on**, and the
numbers are not comparable (different baselines, corpora, tokenizers), which is
why criterion 2's Method pins the corpus SHA, the baseline definition and the
tokenizer. (2) **Latency is a marketed axis** (cymbal's 10-40 ms) and we have no
artifact; that raises enforcement item 2's priority, not its position. (3)
**Language count is a marketed axis** (CodeGraph 158 grammars against our 79),
which supports keeping breadth at rank 10 only if fidelity per language is
measured, i.e. it makes item 11 more urgent than a count race. Two rivals
(Graft, Context+) quote SWE-bench Verified; ours is parked on a 120 GB disk
requirement (`benchmarks/swebench/PROTOCOL.md`).

## 3. Axes of competition, ranked

Ranking is by how much each axis plausibly drives adoption AND retention, argued
from what the repo's own 90-day issue history complains about (`DISCOVERY.md` §9)
and what the README leads with. The ranking is a judgment; the evidence beside
each row is not.

| Rank | Axis | Why it ranks here | Does jcm measure it today? How? |
|---|---|---|---|
| 1 | **Correctness of what is returned** (precise spans, no fabricated symbols, no false absence claims) | The largest closed-issue theme in 90 days (~42 of 140: #569, #566, #550, #559, #553). A wrong span or a confident "dead" verdict on live code costs the operator a debugging cycle and trust. Retention axis. | PARTIALLY. Rust and Racket fidelity harnesses against the language's own parser (`benchmarks/rust_fidelity/`, `benchmarks/racket_fidelity/`); `benchmarks/goldset/` and `benchmarks/deadcode_eval/` exist; the largest test-suite share is regression pins for reported wrong-result defects. No repo-wide precision/recall number for symbol retrieval is published. |
| 2 | **Token reduction per task** | The README headline, the name, the observatory, the receipt CLI. Adoption axis. | MEASURED. `benchmarks/harness/run_benchmark.py --reference` writes `benchmarks/jcm_reference.json` (27.4x vs grep-top-3; corpus pinned by SHA). Guarded by `tests/test_benchmark_reference.py` and `tests/test_provenance.py`. |
| 3 | **Index freshness and incremental cost** | Second-largest issue family with the cache/stale group (#572, #404, #405, #493, #565, #412). Stale-and-confident is the worst failure shape this project documents. | PARTIALLY. `retrieval/freshness.py` classifies per result; `subject_state.py` revalidates cached absence claims; `refresh.py` bounded re-parse. Incremental cost measured only ad hoc (this session: 738 ms for one edited file, `DISCOVERY.md` §2). No CI gate. |
| 4 | **Tool-surface discipline** (small front door, cached prefix stays stable) | `benchmarks/codex_surface/` found 86% of baseline input is CACHED, so the schema block is paid about once; `benchmarks/tier_switch/` priced a narrowing at 174 requests to break even. A surface that grows or moves its prefix bills every user. | MEASURED. `benchmarks/schema_baseline.json` (counter 939 tokens vs full 22,741; 95.9% avoided), `tests/test_schema_budget.py` (core_compact ceiling 4,000), `tests/test_counter_surface_stability.py` (6 tools byte-pinned), `tests/test_description_smells.py`. |
| 5 | **Latency** (query and index) | Performance/hang theme is ~13 of 140 issues (#557, #399, #370, #375). Hangs cost trust more than slowness; the fixes were budgets and heartbeats, not speedups. | PARTIALLY. `analyze_perf` reports p50/p95 per tool from a session ring and an opt-in telemetry.db; `analyze_perf` explicitly refuses to diff latency against the shipped token baselines because none measured it (v1.108.309). No CI latency gate. |
| 6 | **Install, config, and client friction** | ~22 of 140 issues (#416, #491, #508, #509, #437, #506, #507). `init` auto-detects five clients; 13 client configs in `CLIENTS.md`. First-run failures end adoption before any other axis is seen. | PARTIALLY. `tests/test_config_isolation_guard.py`, `test_cli_env_split.py`, upgrade_config tests. No end-to-end "fresh machine install to first query" measurement. The published-artifact handshake is a MANUAL step in the release skill (#536). |
| 7 | **Stability across releases** | 246 releases in 90 days (2.7 per day). Four consecutive releases once shipped on a red build (`CLAUDE.md` release note). Each release rewrites the cached tool prefix if a description changed. | PARTIALLY. CI matrix on every push; `whatsnew.json` regenerated per release; byte-pinned counter surface. Nothing measures behavioural drift between adjacent releases on `main` (the replay workflow exists; see `DISCOVERY.md` §4). |
| 8 | **Security and integrity of what is indexed** | The PyPI quarantine (2026-06-10) was the single most expensive incident and it was about disclosure, not exploits. Zip-slip (#443), path traversal, symlink escape, sdist credential leak (v0.2.6) all have regression tests. | PARTIALLY. `security.py` path validation + trusted-folder whitelist; `tests/test_sdist_exclusions.py`; `verify_package_integrity()`; redaction chokepoints. See `DISCOVERY.md` §6 for scanning and threat-model status. |
| 9 | **Observability and telemetry honesty** | The project's distinguishing habit: `hit_rate_basis`, tri-state UNKNOWN, refusals over zeros, disclosed background behaviour. Rarely an adoption driver; a retention driver for operators who read `_meta`. | MEASURED as properties: many tests pin them (`test_v1_108_186.py`, `test_result_cache_isolation.py`, stop-rule tests). Not a scalar metric. |
| 10 | **Breadth of language support** | 79 languages, 164 extensions (`parser/languages.py`, measured this session). Parser-coverage issues ~12 of 140. Breadth is table stakes past the top ten languages; fidelity (axis 1) matters more than count. | MEASURED as a count; fidelity measured for Rust and Racket only. |

## 4. What the ranking implies for the standard

The top three axes are the ones where a regression is most expensive and least
visible: a wrong span, a stale-but-confident answer, or a token figure that
drifted are all silent. `STANDARD.md` therefore weights its Floors toward
"no worse than the pinned artifact" ratchets on axes 1-4, and puts latency and
install friction behind explicit UNMEASURED gaps rather than inventing numbers
for them.
