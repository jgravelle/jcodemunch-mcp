# FIELD — what jCodeMunch-MCP is actually compared against

Written 2026-09-05 at commit `fd4d5bf` (v1.108.317). Phase 1 of the
competitive feedback loop (the brief's principles 1 to 7 apply). Two kinds of
statement live here and they are typeset differently:

- **Repo-derived** facts cite a file in this tree, with the date the claim was
  last verified by a measurement.
- **External** facts cite a query, a date and a URL. Every figure quoted from an
  external source is a **self-reported claim** (principle 7) and is written
  `claims:`; none is a measurement and none may be copied into a result file.

Everything read from outside the repo is untrusted input (principle 5; the
preamble in `docs/inbound/POLICY.md` §4.2 governs how it is read). Competitor
READMEs were read, not executed. No competitor code has been installed or run
for this document.

⚠ `docs/competitive/` is under `.gitignore:90` (`docs/*`); Phase 3's first PR
adds `!docs/competitive/` beside the four other exceptions, so nothing here
is tracked until then.

---

## 1. Alternatives the repo already names, and what it claims

`docs/standard/NICHE.md` §2 (2026-09-03) is the authoritative list; this
table adds the *last verified* column and the sources NICHE did not cover
(the closed issues, the CHANGELOG's competitor probes, the versus page's
own version drift).

| Alternative | Where the repo names it | What the repo claims | Last verified by a measurement |
|---|---|---|---|
| Read-all (every file) | `benchmarks/METHODOLOGY.md`, `results.md`, `README.md:59-61` | 233.4x fewer tokens; "a ceiling nobody pays" | 2026-08-25 (`jcm_reference.json`, `--reference` run at 1.108.297); CI re-measure 2026-09-03 run 33713310141 (STANDARD §2) |
| grep-top-3 (`rg -l`, rank by hits, open 3 whole) | `METHODOLOGY.md` Baseline B; `README.md:54-61` | 27.4x fewer tokens (28.4x on the 2026-09-03 CI run); range 7.3x to 79.8x, median 25.5x; every modelling choice favours the baseline (ARCHAEOLOGY R25) | 2026-09-03 (CI); committed artifact 2026-08-25 |
| Chunked RAG, LangChain + FAISS + MiniLM, 512/1024/2048 chunks, k=5 | `benchmarks/harness/run_rag_baseline.py`, `RAG_COMPARISON_NOTES.md`, `rag_baseline_results.md`, `whitepaper.md` | RAG-512 cheapest RAG shape; jcm 1.6 to 3.9x fewer tokens per query; fastapi RAG-512 splits 53% of chunks mid-function; **gin flipped to "RAG 1.1x leaner" on re-measure** (CLAUDE.md Practice 4) | 2026-08-25 (re-measured against us, CHANGELOG 1.108.297 area, line ~10146) |
| Odysseus `rag_server` (MiniLM, 1000-char chunks, k=5) | `run_odysseus_compare.py`, `odysseus_compare_results.md` | jcm 1.2x leaner on express; RAG 4.6x / 1.1x leaner on fastapi / gin with Odysseus answering 0.0 to 1.6 of 5 queries completely; "token count alone is a trap" | 2026-08-25 (same re-measure; two of three verdicts changed) |
| Aider RepoMap | `whitepaper.md` §12 | "complementary: the map provides orientation, symbol retrieval provides depth" | NEVER measured |
| LSP-backed same-lane leader, graph-based reviewer, self-updating indexer, single-binary graph store (names withheld) | `ROADMAP.md` "Competitor head-to-head — GATED on a VM" (2026-08-03) | Deliberately not measured: each runs third-party code a venv cannot contain; close condition = (1) a disposable VM/container that is not the maintainer host AND (2) a specific claim the two existing comparators cannot settle | NEVER measured. **Both close conditions are now met** — see §5.0 |
| IDE-native indexing (Cursor, Windsurf, VS Code, Cline, Zed) | `CLIENTS.md`, `README.md:9`, `README.md:130` | hosts, not rivals | NEVER measured; not runnable in a sandbox (§4) |
| The agent's own Read/Grep/Glob | user-level `CLAUDE.md` "Code Exploration Policy", `AGENT_HOOKS.md` | the real default competitor; the hooks exist because the default wins on friction | Indirectly, through grep-top-3 (2026-09-03) |
| Claude Code native tools, head to head on a dead-code task | issue #142 (@Brahm, 2026-03-21, v1.8.3, 50 iterations, Sonnet 4.6, hard tool enforcement) | **third-party measurement, against us**: jcm 1.31x MORE total tokens, 2.43x cache reads, 1.46x tool calls, equal F1; the maintainer reply called it "a ceiling" and the schema overhead "the most actionable" | 2026-03-21, by the reporter, on v1.8.3 (the tool-surface work of 1.108.66 onward post-dates it; never re-run) |
| jcm's own tool-list cost (51 tools, 11,562 tokens) | issue #242 (@gebeer, 2026-04-14) | led to `tool_profile` / `compact_schemas` (v1.44.0) and later the Counter | `benchmarks/schema_baseline.json` (STANDARD §4: counter 939 vs full 22,741 tokens) |
| Caveman, Repomix, codebase-memory-mcp, Headroom, CodeGraph, distill, SigMap, tokf | issue #290 (2026-05-10, closed): user asked for comparisons; reply added all eight to versus.php with prose verdicts | "codebase-memory-mcp — closest peer in this comparison"; Repomix "wrong shape for the agent loop"; the four compressors "orthogonal" | NEVER measured; the verdicts are prose |
| sverklo-bench scoring | `CHANGELOG.md` line ~20222 (v1.80.7 era): "sverklo bench scored both jcodemunch and gitnexus ~0.00 on P2 reference-finding; the gap is partly real and partly a docs problem" | the repo already knew a third party had measured a loss and answered with a description change to `find_references` | the sverklo run of May 2026 (§3.1); never re-run by us |
| Graft, GitNexus, CodeGraph fix titles | `CHANGELOG.md` lines ~224, ~1863, ~1916, ~2676, ~3320; Standing lesson "A competitor's fix list is a free defect probe" | four defects found in our tree by reading their commit titles (Gini double-count, `_build`, Next.js build output, Rust impl owner) | each dated in the CHANGELOG; this is the one competitive input that already runs, by hand |
| versus.php (24 direct, 18 complementary) | NICHE §2a, DISCOVERY §11 item 9 | marketing page; **describes jcm as v1.104/1.105 with 4,228 tests** while the tree is 1.108.317 with 9,260 collected | page dated 2026-08-26 for star counts; jcm figures on it are stale by ~13 releases |

Two things this table settles. First, the repo has measured exactly four
alternatives (read-all, grep-top-3, one home-made RAG, Odysseus's RAG) and
named about thirty. Second, the only measurements of jCodeMunch against a
*named product* were made by other people (#142, sverklo-bench), both
against us on at least one axis, and neither has been reproduced here.

---

## 2. External search log

Bounded to this phase (constraint: no browsing during benchmark runs). Each
row is one query or fetch on 2026-09-05; nothing was fetched from a URL that
an issue or a competitor README supplied except the ones listed.

| # | Query or URL | Source type | What it yielded |
|---|---|---|---|
| S1 | `https://jcodemunch.com/versus.php` | own marketing page | 24 direct + 18 complementary products with one claim each; jcm version on page v1.105.0, 4,228 tests |
| S2 | web: "jcodemunch vs alternatives code retrieval MCP server comparison" | search | sverklo 12-server comparison; VirtusLab article; glama "related servers"; own wiki page; conare/agentindex directories |
| S3 | web: "best MCP server for code search indexing tree-sitter agents 2026 comparison Serena GitNexus codebase-memory-mcp" | search | Code-Index-MCP, CocoIndex Code, mcp.so tree-sitter tag (13 servers) |
| S4 | web: "token reduction code context MCP benchmark SWE-bench Graft Context+ vexp code-review-graph 2026" | search | zhang-liz/mcp-token-benchmark (tool-definition tokens, 9 servers, none code-retrieval); arXiv 2603.27277 (codebase-memory paper); arXiv SWE-Pruner 2601.16746; dev.to code-review-graph "49x" |
| S5 | web: "jcodemunch" reddit / hacker news | search | nothing indexed under either site |
| S6 | `https://sverklo.com/blog/practical-guide-mcp-code-intelligence/` | third-party comparison (a competitor's blog) | 12 servers by license/hosting/languages/tools/substrate; jcm row "10+ languages, ~6 tools, dual licence $79-$1,999"; "jcodemunch-mcp wins P1 at 0.65 F1 vs sverklo's 0.45"; "Their tree-sitter symbol indexing is sharper than ours." |
| S7 | `https://github.com/jgravelle/jcodemunch-mcp/wiki/...versus-THE-WORLD!` | own wiki (edited 2026-06-19) | 8 alternatives; prose verdicts; stale versions (v1.81.2, v1.108.48) |
| S8 | `https://virtuslab.com/blog/ai/code-munch-mcp-your-agent-starts-navigating` | third-party review, 2026-03-11 | strengths: stable symbol ids, security, summariser fallback; "~80% fewer tokens, 5x", headline 99% "optimistic"; weaknesses: no type resolution or cross-file refs, staleness ("stale within hours of a busy sprint"), "seven languages", "500-file cap", licence and opt-out telemetry; alternatives named: Aider RepoMap, Greptile/GrepAI, Sourcegraph SCIP, IDE-native |
| S9 | `https://glama.ai/mcp/servers/jgravelle/jcodemunch-mcp/related-servers` | directory | long tail of tree-sitter MCPs: astllm-mcp, cerberus-mcp, code-cache-mcp, CodeSift (150 tools), codeweaver-mcp, cctx-mcp |
| S10 | `https://github.com/sverklo/sverklo-bench` | third-party benchmark repo, MIT code / CC-BY-4.0 tasks | 150 hand-verified tasks (P1 definition lookup, P2 reference finding, P4 file dependencies, P5 dead code) over express 4.21.1, lodash 4.17.21, requests 2.32.3 (+ sverklo HEAD, flask 3.0.3); F1 with line tolerances; token cost = input tokens to the agent incl. tool results; jcm driven "MCP stdio via uvx", no version stated; results May 2026 (§3.1) |
| S11 | `https://github.com/sverklo/sverklo/blob/main/BENCHMARKS.md` | competitor's own numbers | sverklo-only latency/size figures on five repos; no jcm row |
| S12 | `https://github.com/zhang-liz/mcp-token-benchmark` | third-party | `tools/list` token cost, o200k_base, 9 productivity servers; no code-retrieval server; "97% is inputSchema" for the worst |
| S13 | web: Graft repo; cymbal / Context+ / cocoindex | search | Graft = NanoNets/Graft (redirects to trailhq/Graft); cymbal = 1broseidon/cymbal (the versus page's `opensourcecov/cymbal` 404s); CocoIndex Code = cocoindex-io/cocoindex-code; "Context+" resolves to no repository |
| G1..G4 | `gh api repos/<slug>`, `/releases`, PyPI and npm JSON for every candidate | registries | the columns in §3; stars are as of 2026-09-05 and are not a quality signal |

Not found: any discussion thread where operators compare jCodeMunch with a
rival in their own words (S5). The framing evidence in §3.2 therefore comes
from published comparisons and directories, all of which are either ours or a
competitor's, and is weighted accordingly.

---

## 3. Candidates

### 3.0 Reading the table

Columns: distribution and licence as the registry reports them; current
release with its date and the last release before it (cadence); activity =
last push; *claims* = what the project says about itself on a STANDARD.md
axis, verbatim or near it, unverified; *sandbox* = whether the tool can be
installed once, then run with the network removed, on a GitHub-hosted ubuntu
runner or in a local Docker container (Docker 29.2.0 is on this box), with no
account, no daemon that outlives the run, and no key. UNKNOWN means the
README did not settle it and the adapter work in Phase 3 will.

### 3.1 Third-party measurements that include jCodeMunch

Recorded first because they are the only external *numbers* about us, and
principle 2 says a loss is a finding.

**sverklo-bench, May 2026, sverklo v0.20.2, jcm version unstated** (S10):

| Baseline | n | F1 | P1 definition | P2 references | P4 file deps | their audit grade |
|---|---|---|---|---|---|---|
| sverklo | 120 | 0.58 | 0.70 | 0.29 | 0.78 | B |
| smart-grep | 120 | 0.41 | 0.33 | 0.30 | 0.46 | — |
| **jcodemunch** | 120 | **0.32** | **0.78** | **0.00** | **0.34** | C |
| naive-grep | 120 | 0.27 | 0.07 | 0.14 | 0.42 | — |
| gitnexus | 120 | 0.24 | 0.23 | 0.00 | 0.25 | F |

Read as claims about their run, not as our numbers. What it says: jcm leads
every baseline on definition lookup (STANDARD criterion 1's retrieval half),
scores zero on reference finding, and loses to smart-grep on file
dependencies. The P2 zero is the one our CHANGELOG already answered with a
description edit (v1.80.7 era: `find_references` "does NOT exhaustively
enumerate every call site"). Whether it is "partly a docs problem" or a
capability gap is exactly what a same-methodology re-run settles, and it is
the strongest single argument for including sverklo-bench's P1/P2/P4 tasks in
Phase 2's task set (principle 1: a task only jcm answers is excluded, a task
jcm fails is kept).

Fairness notes on their run, to carry into DESIGN: jcm was driven over stdio
via `uvx` (version unpinned in the README as fetched, so it could have been any
1.10x release); which tools were called per task is not stated; the corpus is
JavaScript-heavy (express, lodash) with one Python repo; lodash is a single
5,000-line UMD file, a shape our symbol index was never measured on.

**#142, 2026-03-21, v1.8.3** (repo-derived, §1): jcm 1.31x more tokens than
Claude Code's native tools on a grep-optimal dead-code task, equal F1. The
2.43x cache-read multiplier was the tool-schema cost this project later
attacked with `tool_profile`, `compact_schemas` and the Counter; it has never
been re-run on a current release.

### 3.2 Candidates by category

Stars and dates are from `gh api` on 2026-09-05. "NOASSERTION" is GitHub's
value for a licence file it could not classify; the text is read in §5.

#### A. Other code-retrieval MCP servers (same lane)

| Tool | What it is | Distribution, licence | Release, cadence | Activity | Claims on STANDARD axes | Sandbox |
|---|---|---|---|---|---|---|
| **Serena** (oraios/serena) | LSP-backed semantic code toolkit exposed over MCP; find symbol / references / edits through a language server per language | PyPI `serena-agent` 1.7.0, MIT, Python >=3.11 <3.15; 28.9k stars | v1.7.0 2026-08-09, v1.6.1 2026-07-21 (≈monthly) | pushed 2026-09-05 | breadth: "40+ languages via LSP"; correctness: type-aware references/definitions; edits (out of our niche). No token or latency figure on its README as read | YES with toolchains: each language needs its server (pyright, typescript-language-server, gopls) installed before the network is cut; spawns child processes per session (the ROADMAP's "LSP-backed leader") |
| **codebase-memory-mcp** (DeusData) | Persistent knowledge graph over tree-sitter symbols; single static binary; git-aware | PyPI `codebase-memory-mcp` 0.10.8, MIT, Python >=3.8; also a static binary; 42.3k stars | v0.10.8 2026-08-19, 0.10.7 2026-08-18 (near-daily) | pushed 2026-09-05 | claims: "155 languages", "sub-ms queries", "99% fewer tokens", 0.299 MRR (versus page); paper arXiv 2603.27277 claims 83% answer quality vs 92% file-exploration at 10x fewer tokens | YES: pip or binary, local SQLite; the "closest peer" per #290's reply and sverklo's table |
| **code-review-graph** (tirth8205) | Local-first code intelligence graph, SQLite + FTS5 + RRF, optional embeddings; 28 tools | PyPI `code-review-graph` 2.3.8, MIT, Python >=3.10; 31.2k stars | v2.3.8 2026-08-21, v2.3.7 2026-07-18 (monthly) | pushed 2026-08-27 | claims: "~65x median token reduction (36x-376x)"; dev.to article "49x"; 23 languages | YES: pure pip as far as the README shows; UNKNOWN whether the optional embedding path downloads a model (would be disabled) |
| **CodeGraph** (colbymchenry/codegraph) | Pre-indexed code knowledge graph for Claude Code, C implementation, framework-aware route nodes | GitHub releases, MIT; 69.7k stars | v1.6.0 2026-08-26, v1.5.0 2026-07-21 (monthly) | pushed 2026-08-31 | claims: "158 tree-sitter grammars", "92-94% Explore-agent tool-call reduction", "100% local" | PROBABLY: native binary; UNKNOWN install path (versus page says Cypher queries; sverklo lists "CodeGraphContext" on Neo4j as a *different* project) |
| **GitNexus** (abhigyanpatwari) | Client-side knowledge graph, KuzuDB, browser UI, CLI | npm `gitnexus` 1.6.11, **PolyForm-Noncommercial-1.0.0**; 47.0k stars | v1.6.12-rc.2 2026-09-05 (release candidates weekly) | pushed 2026-09-05 | claims: 14 languages, Leiden community detection, execution-flow analysis | technically YES (npm, local); **licence question in §5** |
| **sverklo** (sverklo/sverklo) | Symbol graph + BM25 + embeddings + PageRank (RRF), git-pinned memory; 37 tools | GitHub releases (`.vsix` asset), MIT; 78 stars | v0.29.5 2026-08-12 | pushed 2026-08-12 | claims: "43x fewer tokens than naive grep"; F1 0.58 on its own bench | UNKNOWN: release asset is a VS Code extension; the MCP server's install path (npm?) not confirmed |
| **SocratiCode** | "Enterprise-grade (40m+ LOC)" plugin/skill/extension, KuzuDB | GitHub, **AGPL-3.0**; 3.3k stars | v1.12.0 2026-08-14 | pushed 2026-09-05 | claims: cross-file resolution, 3 languages | UNKNOWN; AGPL |
| **SigMap** (manojmallick) | Pre-ranks files by TF-IDF, writes signatures to a static `.context` file; MCP server | npm `sigmap` 8.29.0, MIT; 626 stars | v8.29.0 2026-09-01 (weekly) | pushed 2026-09-02 | claims: "96.8% token reduction across 21 repos", Zenodo-archived suite | YES (npm, zero deps) |
| **Octocode** (bgauryy/octocode) | CLI + MCP, AST + framework nodes, write primitives | npm `octocode-mcp` 18.2.2, MIT; 924 stars | last GitHub release 2025-12-15; npm moves without releases | pushed 2026-09-04 | claims: "88% mean retrieval savings (142.8k to 5.5k over 10 queries)" | YES (npm) |
| **Code-Index-MCP** (ViperJuice, now Consiliency) | 25+ tools, CozoDB, 48 languages | PyPI `code-index-mcp` 2.17.1, MIT; 57 stars | v1.4.0 2026-07-19 on GitHub; PyPI at 2.17.1 (the two disagree) | pushed 2026-07-19 | claims: "sub-100ms queries", file-watch updates | YES (pip) |
| **trace-mcp**, **Axon**, **Pharaoh**, **Context+**, **vexp**, **TokenSave**, **LemonCrow**, **LeanCTX** | named on versus.php | trace-mcp and Axon slugs 404; Pharaoh is hosted ($27/mo); "Context+" resolves to no repo; vexp is a hosted "reliability layer" (vexp.dev); LemonCrow is a runtime (61 stars); lean-ctx (yvgude, 3.7k, Apache, Rust) is a context-control/compression layer, not retrieval | — | — | claims as on the versus page | NO (hosted, absent, or a different layer) |
| Long tail (S9): astllm-mcp, cerberus-mcp, code-cache-mcp, CodeSift, codeweaver-mcp, cctx-mcp, wrale/mcp-server-tree-sitter (309 stars, pushed 2026-05-21), Helweg/open-codebase-index (185), bobmatnyc/mcp-vector-search (56) | tree-sitter MCPs with the same one-line claim ("up to 95%/99%") | various | — | — | — | not evaluated; the set review (§5.3) re-reads this list |

#### B. IDE-native or agent-native indexing

Cursor, Windsurf, VS Code Copilot, Cline, Zed, Claude Code's own Grep/Read.
Not runnable in a sandbox as a component: the index is inside a proprietary
client, is not addressable over a stable interface, and cannot be pinned to a
release. **NOT COMPARABLE** on every measured axis except through the null
alternative, which is the agent's native tools by construction (#142 is the
one measurement of that shape and it predates the Counter). Recorded, not in
the set.

#### C. grep and ripgrep style tools exposed to agents

| Tool | What it is | Distribution, licence | Release | Activity | Claims | Sandbox |
|---|---|---|---|---|---|---|
| **ripgrep** | the grep-top-3 baseline's engine | Unlicense; 68k stars | pushed 2026-08-04 | — | none | YES (already in the harness) |
| **cymbal** (1broseidon) | language-agnostic code navigation CLI, tree-sitter to SQLite FTS5; Go, single binary; skips generated files | GitHub release tarballs per OS with checksums, MIT; 317 stars | v0.14.0 2026-06-20, v0.13.5 2026-05-19 | pushed 2026-09-03 | claims: "~10-40 ms query latency", 20 languages | YES (binary, offline) |
| **CocoIndex Code** (cocoindex-io/cocoindex-code) | "embedded code search engine CLI (AST based)", also MCP; local embeddings by default | PyPI `cocoindex-code` 0.2.41, Apache-2.0, Python >=3.11; 2.7k stars | v0.2.41 2026-08-07, v0.2.40 2026-08-06 (near-daily) | pushed 2026-09-02 | claims: "instant token saving by 70%", 20+ languages, incremental re-index | PROBABLY: needs the embedding model fetched before the network is cut |

#### D. Embedding and vector retrieval layers

| Tool | What it is | Distribution, licence | Release | Activity | Claims | Sandbox |
|---|---|---|---|---|---|---|
| **LangChain + FAISS + MiniLM** (our `run_rag_baseline.py`) | the in-tree RAG comparator | pip; already run here | — | — | measured (§1) | YES (already runs offline after the model is cached) |
| **Claude Context** (zilliztech) | BM25 + dense vectors over Milvus | npm, MIT; 12.5k stars | — | pushed 2026-07-14 | broad languages | NO: external Milvus service |
| **mcp-server-qdrant** | generic vector store MCP | PyPI, Apache; 1.5k | — | pushed 2026-09-04 | not code-specific | NO: needs a Qdrant instance; not a code tool |
| **Greptile / GrepAI**, **Sourcegraph SCIP** (named by S8) | hosted semantic search; compiler-grade indexing | closed/hosted; SCIP is a format not a retrieval tool | — | — | — | NO |

#### E. Repository-map generators

| Tool | What it is | Distribution, licence | Release | Activity | Claims | Sandbox |
|---|---|---|---|---|---|---|
| **Aider RepoMap** (Aider-AI/aider) | tree-sitter tags + PageRank, token-budgeted map; the paper-cited "graph-based" approach S8 says beats jcm on cross-file awareness | PyPI `aider-chat` 0.86.2, Apache-2.0, **Python >=3.10 <3.13**; 48.8k stars | — | **pushed 2026-05-22** (quiet 3.5 months) | claims (S2): "4.3-6.5% context utilization" in an empirical study | YES: `aider --show-repo-map` runs offline; the map is a ranked text, not a retrieval call, so only the token axis applies |
| **Repomix** (yamadashy) | packs a repo into one file; `--compress` strips bodies via tree-sitter | npm `repomix` 1.18.0, MIT; 28.2k stars | v1.18.0 2026-08-08, v1.17.0 2026-07-21 (monthly) | pushed 2026-09-05 | claims: "50-80% fewer tokens where compression applies" | YES (npm) |
| **RepoMapper** (pdavis68) | PageRank token-budgeted map, 34+ languages | GitHub, MIT; 202 stars | — | pushed 2025-12-08 | — | YES but dormant |
| **Graft** (NanoNets, redirects to trailhq/Graft) | deterministic tree-sitter `graft build` writes a per-symbol wiring graph plus markdown "cards" into the repo; optional model enrichment; MCP server | npm `@nanonets/graft` 0.16.0 (PyPI `graft-cli` 0.0.6 is unrelated), MIT; 5.6k stars | npm-only cadence; created 2026-07-03 | pushed 2026-09-04 | claims: "33/50 SWE-bench Verified", 22 languages, 6 tools; "build, check, ask are deterministic tree-sitter — they never call a model" | YES for the deterministic path (no key); the enrichment pass needs a model and is out of scope |

#### F. The null alternative

Raw file reads with no retrieval tool: the read-all baseline (Baseline A) and
the agent-does-grep baseline (Baseline B) already exist in
`run_benchmark.py`, measured in the same run through the same reader
(ARCHAEOLOGY R22-R26). Always in the set, both shapes.

#### G. Compressors and memory layers (different layer, recorded only)

caveman (103.7k stars), Headroom (69.0k), RTK, Context Mode, distill, tokf,
lean-ctx, mem0, ClawMem, OpenViking: they shorten what the agent says or what
the shell returns, or store session memory. They compose with a retrieval
tool and do not answer a retrieval task, so no adapter can present them to the
harness interface (index, answer tasks, report tokens). Out of scope for the
comparison; in scope for the weekly release feed only if one of them adds
code retrieval.

### 3.3 Where jCodeMunch is mentioned beside alternatives, and how

| Venue | Framing | Trade-offs stated | Axes the framing weighs |
|---|---|---|---|
| sverklo blog (S6, a competitor) | 12-server table; jcm = "tree-sitter symbol index, ~6 tools, 10+ languages, dual licence" | "their tree-sitter symbol indexing is sharper than ours"; jcm wins P1 | correctness per task type; licence; tool count; hosting |
| sverklo-bench (S10) | 5 baselines incl. two greps | jcm best at definitions, zero at references, below smart-grep on deps | correctness (F1) per task category; tokens to the agent |
| VirtusLab (S8, 2026-03) | a fair long review, now stale on facts | staleness of the index; no cross-file refs; "seven languages"; "500-file cap"; licence and telemetry | freshness (criterion 3), correctness (1), breadth (10), install/licence friction (6) |
| glama / conare / agentindex directories (S2, S9) | "alternatives" lists by keyword | "Context7 is the top-rated alternative" (a docs-retrieval tool, i.e. the directory's category is too coarse) | none measured |
| #142 (a user) | native tools vs jcm, hard enforcement | jcm costs more on grep-shaped tasks; schema overhead dominates | tokens (2), tool-surface (4) |
| #290 (a user) | "compare against these eight" | — | tokens (every one of the eight leads with a percentage) |
| versus.php, wiki (ours) | prose verdicts | licence (GitNexus NC), setup burden (Serena), write tools, hosted backends | tokens, breadth, licence, install |

What the market weighs, in the order the evidence supports: (1) a token
percentage, quoted by every product and comparable across none of them;
(2) correctness split by task type, where the one third-party benchmark puts
us first on definitions and last on references; (3) licence and install
burden (PolyForm NC, LSP toolchains, Neo4j/Milvus/Docker); (4) freshness,
raised by the one long-form review; (5) language count, marketed by the
graph products. Latency appears as a marketed number (cymbal) and nowhere
else. Nobody outside this repo weighs tool-surface discipline explicitly;
#142 and #242 weighed its consequence.

Two stale-fact findings for the human, outside this loop's remit (principle
6): VirtusLab's "seven languages" and "500-file cap" were true of an early
release and are quoted as current; versus.php quotes v1.105.0 and 4,228
tests. The loop never edits marketing; the entries are here so the report can
name them.

---

## 4. NOT COMPARABLE, by construction

Recorded now so DESIGN does not re-derive it.

- **IDE-native indexes** (category B): no addressable interface, no pin.
- **Hosted products** (Pharaoh, vexp, Greptile, Claude Context's Milvus): a
  network dependency during the run violates the sandbox rule, and a hosted
  index cannot be pinned to a corpus SHA (ARCHAEOLOGY R1, R4).
- **Compressors and memory layers** (category G): no retrieval task to answer.
- **Any axis whose method needs jcm internals**: criterion 1(a) extractor
  fidelity (the oracle harnesses grade OUR extractor's buckets), criterion 4
  tool-surface (a `tools/list` weight is measurable for every MCP server and
  will be reported, but the Counter's byte-pin and `core_compact` ceiling are
  ours), criteria 7, 8, 9 (release stability, security posture, telemetry
  honesty: properties of a codebase, not of a retrieval result). DESIGN
  decides per axis; the default for these is "capability difference, reported,
  not scored".

---

## 5. The comparison set

### 5.0 The ROADMAP gate is reversed, and here is which condition changed

`ROADMAP.md` gated a head-to-head on two conditions and said anyone reversing
it must name which changed. Both did. (1) A disposable machine that is neither
the maintainer host nor the release machine: the CI/CD layer (2026-09-04)
runs the bench tier on GitHub-hosted runners, and this box has Docker 29.2.0
for local reproduction with `--network none`; competitor code never touches
the maintainer's Python. (2) A specific claim the two existing comparators
cannot settle: sverklo-bench's P2 = 0.00 and P4 = 0.34 (a third party
measuring a loss on correctness, which neither RAG comparator measures at
all), and S8's "Aider wins on cross-file awareness". The ROADMAP's other
concern, that a brand name could not be published, is met by principle 6:
these results are internal.

### 5.1 Selection rule

A candidate enters the set when all hold: it answers at least the definition
and reference tasks (so the null alternative and it share a task set,
principle 1 and the task-fairness rule); it installs from a public registry or
a checksummed release asset, pinned; it runs with no network after install,
no account, no key, and no daemon that outlives the run; its licence permits
running it for internal evaluation; and it is either what third parties
compare us against or what users asked us to compare against (#290). Ties are
broken toward the tool whose configuration is documented well enough to write
the fairness note (principle 2).

### 5.2 The set (8 tools + 2 nulls)

| # | Tool | Category | Why it is in |
|---|---|---|---|
| 0a | read-all | null | always (Baseline A) |
| 0b | grep-top-3 | null | always (Baseline B; the agent's own tools) |
| 1 | Serena 1.7.0 | A | the alternative every third-party source names first; the ROADMAP's withheld "LSP-backed leader"; LSP references are the strongest challenger on P2 |
| 2 | codebase-memory-mcp 0.10.8 | A | "closest peer" in our own #290 reply and in sverklo's table; 42k stars; a paper with a methodology to compare against ours |
| 3 | code-review-graph 2.3.8 | A | 31k stars; pure pip; the loudest token claim (65x median) in the same lane, so the one most worth recomputing |
| 4 | CodeGraph 1.6.0 | A | 69.7k stars, the largest adoption in the lane; native binary, so it is also the install-friction test of the adapter design; source of two fix-title probes that paid |
| 5 | Graft 0.16.0 (deterministic path only) | E/A | quotes SWE-bench; source of two fix-title probes; a structurally different approach (pre-written cards) that principle 4's competitive-idea rule exists for |
| 6 | Aider RepoMap (aider-chat 0.86.2) | E | the approach a third party says beats us on cross-file awareness; the whitepaper's own "complementary" claim has never been tested; token axis only |
| 7 | cymbal 0.14.0 | C | the CLI shape (a subprocess, not a server) and the only marketed latency figure; single binary with checksums, the cleanest sandbox case |
| 8 | CocoIndex Code 0.2.41 | C/D | replaces the home-made LangChain RAG with a shipped embedding-based product; Apache; local model |

Budget: the offline bench tier runs in 31 s today (harness VERIFICATION);
each adapter adds a cold index of the three pinned corpora (186 / 1,186 / 98
files) plus the task set, three runs each for the stability note. Ceiling per
tool per full run: **20 minutes** wall, enforced per adapter; ceiling per full
run: **4 hours** on one runner, i.e. the set can grow to about ten tools
before the cadence or the ceiling has to move. Serena is expected to be the
one that tests the per-tool ceiling (three language servers, cold). A tool
that exceeds its ceiling is reported NOT COMPARABLE for that run, not
estimated.

### 5.3 Excluded, with reasons

| Tool | Reason |
|---|---|
| GitNexus | PolyForm-Noncommercial-1.0.0. Running it to benchmark a commercially licensed product is plausibly "commercial use" and the licence's noncommercial grant is what the versus page criticises it for. **Human decision**: include only if the maintainer reads the licence and accepts, or obtains permission; recorded in FINDINGS at Phase 3. Third parties rank it below both greps on their bench, which lowers the cost of waiting. |
| Repomix | a pack, not a retrieval loop: it cannot answer a per-task query, so no task set is shared with the null alternative. Its `--compress` output is a whole-repo cost; reportable as a capability difference (one-shot context) if the set review wants it. |
| sverklo (the tool) | 78 stars; install path unconfirmed (release asset is a `.vsix`). **Its benchmark tasks are adopted** in Phase 2 regardless (CC-BY-4.0), which is the part that matters. Re-evaluate at the set review. |
| SigMap, Octocode, Code-Index-MCP, SocratiCode, GrapeRoot, trace-mcp, the S9 long tail | adoption below the set's floor, or (SocratiCode) AGPL plus a plugin shape, or (trace-mcp, Axon) no resolvable repository. The weekly feed watches the excluded list for a release; the quarterly review re-reads stars and pushes. |
| Claude Context, mcp-server-qdrant | external database service during the run |
| Pharaoh, vexp, Greptile, "Context+", TokenSave, LemonCrow | hosted, absent, or a runtime rather than a retrieval tool |
| IDE-native indexing | §4 |
| caveman, Headroom, RTK, Context Mode, distill, tokf, lean-ctx, mem0 | different layer (§3.2 G) |
| Odysseus rag_server, LangChain RAG | already measured in-tree; superseded in the set by CocoIndex Code as the embedding representative; both comparators stay where they are and keep running |

### 5.4 Review cadence for the set itself

- **Weekly** (the release-feed check in Phase 3 item 6): a new release of a
  set member schedules a re-run; a release from the excluded list is logged
  only.
- **Quarterly** (first review 2026-12-05): re-read §3.2's registries for
  stars, last push and licence; re-fetch S6, S9 and any new third-party
  comparison that names jCodeMunch; a tool added to the set or removed from it
  is one PR touching this file and FINDINGS.md, reviewed like any other.
- **On trigger**: a third-party benchmark naming jCodeMunch beside a tool not
  in the set (the sverklo-bench shape), or an excluded tool crossing the
  adoption floor of the smallest member (cymbal, 317 stars), opens a
  `competitive-watch` draft.

---

## 6. What the field says about NICHE.md, for Phase 6

Recorded now, decided later. Three signals cut against the niche as written:

1. **Reference finding is weighed and we score zero on the one public
   measurement of it.** NICHE §1 puts "structural questions grep cannot
   answer: importers, blast radius, call hierarchy" as the secondary job. The
   field's benchmark treats references as a first-class retrieval task, the
   LSP tools answer it by construction, and our own CHANGELOG called the gap
   "partly real".
2. **"Symbol-level retrieval" is now the lane's table stakes**, not its
   differentiator: every category-A tool is tree-sitter symbol indexing with a
   graph or a store on top. The one thing a competitor conceded ("sharper
   symbol indexing") is criterion 1(a), which is measured for two languages
   neither of which is in the corpus mix.
3. **Freshness is the axis the long-form review led with**, and NICHE ranks it
   third with no CI gate. It is also an axis on which most category-A tools
   make no claim at all, so a measurement would separate the field.

One signal supports the niche: every third-party framing that praises
jCodeMunch praises precision at the symbol boundary, and every measured RAG
comparator loses on chunk integrity even where it wins on tokens.
