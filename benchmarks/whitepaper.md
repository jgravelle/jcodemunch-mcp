# Symbols, Not Chunks: AST-Based Retrieval Cuts LLM Code Context 1.6--3.9x vs. LangChain RAG on Real Codebases

**J. Gravelle**
March 2026

---

## Abstract

Large language models (LLMs) consume tokens proportionally to the context they receive. When applied to code understanding tasks, the dominant retrieval strategy --- chunk-based Retrieval-Augmented Generation (RAG) using vector embeddings --- injects substantial irrelevant context, wastes tokens, and frequently delivers fragments that split functions mid-definition. This paper presents an alternative: AST-based symbol retrieval, which uses tree-sitter parsing to extract complete syntactic units (functions, classes, methods) and serves them via deterministic lookup. We benchmark both approaches on three open-source web frameworks (Express.js, FastAPI, Gin) totaling 1,214 files and 1,024,421 baseline tokens. In head-to-head comparison against a naive fixed-chunk RAG pipeline (LangChain + FAISS + MiniLM-L6-v2), AST retrieval uses **1.6--3.9x fewer tokens per query** on every tested repository. Against a "read all files" lower-bound baseline, the reduction is 99.6% (263.9x). Two controlled A/B tests on a production Vue 3 codebase confirm 20% cost savings in end-to-end agentic workflows (p=0.0074), though with accuracy tradeoffs on fine-grained classification tasks. The structural advantage --- complete code units with no chunk boundary artifacts --- is orthogonal to the search mechanism and would apply equally to RAG pipelines that adopt symbol-level chunking. We argue that for code-specific retrieval, the retrieval unit should be the symbol, not the chunk.

---

## 1. Introduction

The integration of LLMs into software engineering workflows --- code generation, review, debugging, refactoring --- has accelerated rapidly. A common pattern has emerged: the agent needs to understand an unfamiliar codebase, so it retrieves relevant code and injects it into the model's context window.

The standard approach borrows from document retrieval: split source files into overlapping text chunks, embed them with a dense model, store the vectors in an index (typically FAISS or Chroma), and retrieve the top-k most similar chunks at query time. This is the RAG pattern, popularized by frameworks like LangChain, LlamaIndex, and Haystack.

RAG works reasonably well for prose documents, where any contiguous passage may contain relevant information. Code, however, has structure that prose does not. A function is a complete unit of meaning. Half a function is noise. The question this paper investigates is straightforward: *what happens when we replace arbitrary text chunks with complete syntactic units as the retrieval granularity?*

The answer, across three repositories in three languages, is that token consumption drops by 1.6--3.9x compared to a naive fixed-chunk RAG pipeline, with no embedding model, no vector store, and no chunk boundary artifacts. We use "naive" deliberately: the RAG baseline tested here uses a general-purpose embedding model and fixed-size chunking, not code-specific embeddings or AST-aware splitting. The comparison is against a common starting point, not a fully optimized pipeline.

---

## 2. Problem Statement

### 2.1 The Token Cost Problem

LLM API pricing is per-token. Context window size is finite. Both constraints create pressure to minimize irrelevant context. Yet the standard RAG pipeline is structurally biased toward over-retrieval:

1. **Fixed chunk size forces a precision/recall tradeoff.** Small chunks (512 tokens) reduce per-result noise but split functions mid-definition. Large chunks (2048 tokens) preserve more structure but include unrelated code from adjacent definitions.

2. **Top-k retrieval returns a fixed number of results regardless of query specificity.** A query matching one function still returns k chunks, most of which are noise.

3. **The search-then-fetch pattern double-counts tokens.** A typical workflow retrieves k results for inspection, then "fetches" the top n for the LLM. The top n appear in both the search response and the fetch response, inflating the effective token count.

### 2.2 The Chunk Boundary Problem

Consider a Python file with three functions, each ~400 tokens. A 512-token chunker produces chunks that look like this:

```
Chunk 1:  [end of function A] [start of function B ... truncated]
Chunk 2:  [... middle of function B ...] [start of function C]
Chunk 3:  [... end of function C] [module-level code]
```

An LLM receiving Chunk 1 gets the tail of one function and the head of another. It has no reliable way to determine where one definition ends and another begins. This is not a theoretical concern --- our measurements show that **53% of retrieved RAG-512 chunks for FastAPI are split mid-function** (Section 7.3).

### 2.3 Scaling Behavior

As codebases grow, the problem compounds. A 951-file repository like FastAPI produces 2,256 chunks at 512-token granularity. The embedding step alone takes 47 seconds. Query latency, while acceptable (12--36 ms), is orders of magnitude slower than an in-process BM25 lookup (<5 ms). The vector index occupies 7.5 MB on disk --- modest in absolute terms, but unnecessary if the retrieval unit can be derived from the source structure directly.

---

## 3. Background

### 3.1 RAG for Code

RAG (Retrieval-Augmented Generation) augments an LLM's fixed training knowledge with dynamically retrieved context. For code, the standard pipeline is:

1. **Chunking.** Source files are split into fixed-size token windows (typically 256--2048 tokens) with overlap (5--15%) to mitigate boundary effects.
2. **Embedding.** Each chunk is passed through a dense embedding model (e.g., `all-MiniLM-L6-v2`, `text-embedding-3-small`) to produce a vector representation.
3. **Indexing.** Vectors are stored in an approximate nearest-neighbor index (FAISS, Chroma, Pinecone).
4. **Retrieval.** At query time, the query is embedded and the top-k nearest chunks are returned.

This pipeline was designed for prose documents and adapted for code. The adaptation is imperfect: code has syntactic structure (functions, classes, modules) that prose does not, and that structure is semantically meaningful.

**Semantic and AST-aware chunking.** The RAG ecosystem has recognized the fixed-chunk limitation. LangChain, LlamaIndex, and other frameworks offer *semantic chunking* (split at natural breakpoints detected by embedding similarity shifts) and *AST-aware chunking* (split at function or class boundaries using a parser). AST-aware chunking in particular eliminates the chunk boundary problem described in Section 2.2. We did not benchmark these strategies --- doing so would require choosing among multiple implementations with different heuristics, and the comparison would conflate chunking strategy with embedding model quality. We note, however, that AST-aware chunking and AST symbol retrieval share the same core insight: the retrieval unit for code should align with syntactic boundaries. The remaining difference is the search mechanism (embedding similarity vs. BM25) and the retrieval interface (opaque chunks vs. structured symbol metadata). Section 8 discusses this distinction further.

### 3.2 Context Window Constraints

Modern LLMs offer context windows ranging from 128K to 2M tokens. A naive approach --- load the entire codebase --- is feasible for small projects but fails quickly. A 951-file Python framework tokenizes to ~700K tokens. A production monorepo can easily exceed 10M tokens. Even where the window is large enough, longer contexts degrade attention quality, increase latency, and cost proportionally more.

### 3.3 Tree-Sitter and AST Parsing

Tree-sitter is an incremental parsing framework that produces concrete syntax trees for source code in ~40 languages. Unlike regex-based heuristics, tree-sitter parsing is grammar-driven: it identifies functions, classes, methods, type definitions, and other syntactic constructs with the same precision as the language's own compiler front-end. Parse time is typically sub-second for single files and under 15 seconds for a 951-file repository.

---

## 4. Approach: AST Symbol Retrieval

### 4.1 Core Idea

Instead of chunking source files into arbitrary token windows, parse them into their natural syntactic units: functions, classes, methods, type definitions. Index these **symbols** by name, qualified name, and file path. At query time, search the symbol index (not a vector index) and return the complete source code of matched symbols.

The retrieval unit is no longer a 512-token fragment of unknown provenance. It is a complete, self-contained definition --- the exact code the developer would navigate to in an IDE.

### 4.2 Indexing Pipeline

```
Source files  →  tree-sitter parse  →  symbol extraction  →  BM25 index + SQLite store
```

For each file:
1. Detect language from file extension.
2. Parse with the appropriate tree-sitter grammar.
3. Walk the AST to extract top-level and nested symbols (functions, classes, methods, type aliases, constants).
4. Store each symbol's metadata (name, qualified name, kind, file path, line range) and full source text in a SQLite database.
5. Build a BM25 inverted index over symbol names and qualified names.

The entire pipeline is deterministic. No embedding model is involved. No GPU is required. Index build time scales linearly with file count: <1 second for 98 files (Gin), ~5--15 seconds for 951 files (FastAPI).

### 4.3 Retrieval Workflow

The retrieval workflow mirrors the discover/search/retrieve pattern common in code exploration:

```
Query: "middleware"
  ↓
Step 1: search_symbols("middleware", max_results=5)
  → Returns ranked symbol metadata: name, kind, file, line range, score
  → Token cost: ~370 tokens (metadata only, not full source)
  ↓
Step 2: get_symbol_source(top_3_symbol_ids)
  → Returns complete source code of the 3 best-matching symbols
  → Token cost: ~640 tokens (3 complete function bodies)
  ↓
Total: ~1,010 tokens
Baseline (all files): 137,978 tokens
Reduction: 99.3%
```

Three properties distinguish this from RAG retrieval:

1. **The search step returns metadata, not source.** The LLM (or agent) can inspect symbol names, kinds, and file locations before deciding which symbols to retrieve in full. This is analogous to scanning a table of contents before reading chapters.

2. **The retrieve step returns complete syntactic units.** Every result starts at a definition boundary and ends at the matching closing brace or dedent. There are no mid-function fragments.

3. **Result count is adaptive.** If a query matches one symbol strongly, the agent retrieves one symbol. RAG always returns k chunks regardless of query specificity.

### 4.4 Stable Symbol Identifiers

Each symbol receives a deterministic identifier derived from its repository, file path, and qualified name. This ID is stable across reindexing (unless the symbol is renamed or moved). Stable IDs enable:

- **Caching.** A previously retrieved symbol can be recognized without re-fetching.
- **Cross-reference.** Import graphs, call hierarchies, and blast radius analysis can reference symbols by ID.
- **Incremental updates.** When a file changes, only its symbols are re-extracted. The rest of the index is untouched.

---

## 5. Implementation Overview

The implementation described here uses tree-sitter grammars for 70+ languages, a SQLite-backed symbol store, and BM25 for text search. The system runs as an MCP (Model Context Protocol) server, exposing tools that LLM agents call directly.

### 5.1 Language Support

Symbol extraction is grammar-driven. Each supported language has a tree-sitter grammar and an extraction spec that maps AST node types to symbol kinds:

| Language family | Languages | Symbol kinds extracted |
|-----------------|-----------|----------------------|
| C-like | C, C++, C#, Java, Go, Rust, Swift, Kotlin | functions, methods, classes, structs, interfaces, enums, type aliases |
| Dynamic | Python, JavaScript, TypeScript, Ruby, PHP, Lua | functions, methods, classes, decorators, module-level assignments |
| Functional | Haskell, Scala, Erlang, R, Julia | functions, type classes, data types, modules |
| Markup/Config | SQL, TOML, CSS, Bash | definitions, sections, rules |
| Specialized | Vue SFC, Razor (`.cshtml`), Assembly | component APIs, code blocks, labels/macros |

Custom extractors exist for languages where tree-sitter grammars lack clean named fields (Erlang: multi-clause function merging by arity; Fortran: module-qualified names; SQL: dbt Jinja preprocessing).

### 5.2 Storage and Index Architecture

```
~/.code-index/
  <repo-hash>/
    index.db          # SQLite: symbols table (name, kind, file, lines, source)
                      #         files table (path, hash, size_bytes)
                      #         imports table (file, specifier, resolved_path)
    content/          # Raw source files (for full-file retrieval)
```

The SQLite schema supports:
- **O(1) symbol lookup** by ID (hash index built in `__post_init__`).
- **BM25 search** over symbol names with optional language and file filters.
- **Import graph queries** for cross-reference tools (find_importers, blast_radius, dead_code).
- **Incremental updates** via content hashing --- only changed files are re-parsed.

### 5.3 Integration with LLM Workflows

The retrieval tools are exposed via MCP (Model Context Protocol), the open standard for LLM tool integration. An agent's interaction looks like:

```
Agent: search_symbols("router route handler", max_results=5)
  ← {symbols: [{id: "abc123", name: "route", kind: "function",
       file: "lib/router/index.js", lines: [45, 92]},...]}

Agent: get_symbol_source("abc123")
  ← {source: "Router.prototype.route = function route(path) {\n  ...full body...\n};",
     name: "route", kind: "function", lines: [45, 92]}
```

The agent receives exactly the code it needs --- a complete function definition --- without reading the entire file or receiving adjacent, unrelated code.

---

## 6. Benchmark Design

### 6.1 Repositories Under Test

Three public web frameworks spanning three languages, chosen for structural diversity:

| Repository | Language | Files indexed | Symbols extracted | Baseline tokens |
|------------|----------|:------------:|:-----------------:|:--------------:|
| expressjs/express | JavaScript | 165 | 181 | 137,978 |
| fastapi/fastapi | Python | 951 | 5,325 | 699,425 |
| gin-gonic/gin | Go | 98 | 1,489 | 187,018 |

**Baseline tokens** = all indexed source files concatenated and tokenized with `tiktoken` `cl100k_base`. This is the minimum cost for an agent that reads every file once. Real agents typically read files multiple times, making this a conservative baseline.

### 6.2 Query Corpus

Five queries representing common code exploration intents, defined in a public `tasks.json`:

| Query | Intent |
|-------|--------|
| `router route handler` | Core route registration / dispatch |
| `middleware` | Middleware chaining and execution |
| `error exception` | Error handling and exception propagation |
| `request response` | Request/response object definitions |
| `context bind` | Context creation and parameter binding |

Each query is run against each repository, producing 15 task-runs.

### 6.3 RAG Configuration

The RAG baseline uses a naive LangChain pipeline --- deliberately unoptimized, representing a common starting point rather than a production-tuned system:

- **Embeddings:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim, local inference)
- **Vector store:** FAISS (`faiss-cpu`, in-memory)
- **Splitter:** `RecursiveCharacterTextSplitter.from_tiktoken_encoder` (true token-based chunks)
- **Chunk sizes:** 512, 1024, 2048 tokens with ~10% overlap
- **Retrieval:** `similarity_search(query, k=5)`, top 3 used as "fetched"

Token counting: `search_tokens` (all 5 retrieved chunks serialized) + `fetch_tokens` (top 3 chunks serialized). This mirrors the AST workflow's `search_symbols` + `get_symbol_source` two-step pattern.

**What is not tested.** The RAG baseline does not use code-specific embedding models (CodeBERT, Voyage Code, StarEncoder), re-ranking passes (Cohere Rerank, cross-encoder), hybrid search (BM25 + dense), or AST-aware chunking. Any of these would likely improve RAG's token efficiency. The results in Section 7 should be read as "AST retrieval vs. naive RAG," not "AST retrieval vs. best-possible RAG."

**Double-counting note.** The two-step token accounting (search 5 + fetch 3) means the top 3 chunks are counted in both passes. A simpler RAG workflow that calls `similarity_search(k=3)` and uses the results directly would avoid this overhead. We chose the two-step structure to mirror the AST workflow's metadata-then-source pattern, making the comparison structurally parallel. This inflates RAG's token count by roughly 30--40% relative to a single-pass retrieval. The 1.6--3.9x margin would narrow under single-pass accounting, though AST retrieval would still be more efficient due to the metadata-vs-source asymmetry in the search step.

### 6.4 AST Configuration

- **Parser:** tree-sitter (language-specific grammars)
- **Search:** BM25 over symbol names, `max_results=5`
- **Fetch:** `get_symbol_source` on top 3 symbol IDs
- **Token counting:** search response tokens + 3 x symbol source tokens

AI summaries were disabled during benchmarking (signature-only fallback).

### 6.5 Reproducibility

Both harnesses read file content from the same `IndexStore` instance (`IndexStore.load_index() → index.source_files`). Baselines are identical by construction. The harness scripts (`run_benchmark.py`, `run_rag_baseline.py`), query corpus (`tasks.json`), and raw results (`rag_baseline_results.json`) are open source.

---

## 7. Results

### 7.1 Token Efficiency: AST Retrieval

| Repository | Baseline tokens | AST avg/query | Reduction | Ratio |
|------------|:--------------:|:------------:|:---------:|:-----:|
| expressjs/express | 137,978 | 924 | 99.4% | 150.1x |
| fastapi/fastapi | 699,425 | 1,834 | 99.8% | 531.2x |
| gin-gonic/gin | 187,018 | 1,124 | 99.4% | 171.9x |
| **Grand total (15 runs)** | **5,122,105** | **19,406** | **99.6%** | **263.9x** |

Per-query detail (Express.js):

| Query | Baseline | AST tokens | Search | Fetch | Reduction |
|-------|:-------:|:----------:|:-----:|:----:|:---------:|
| `router route handler` | 137,978 | 886 | 381 | 505 | 99.4% |
| `middleware` | 137,978 | 1,008 | 370 | 638 | 99.3% |
| `error exception` | 137,978 | 859 | 362 | 497 | 99.4% |
| `request response` | 137,978 | 872 | 372 | 500 | 99.4% |
| `context bind` | 137,978 | 993 | 372 | 621 | 99.3% |

Per-query detail (FastAPI):

| Query | Baseline | AST tokens | Search | Fetch | Reduction |
|-------|:-------:|:----------:|:-----:|:----:|:---------:|
| `router route handler` | 699,425 | 1,199 | 464 | 735 | 99.8% |
| `middleware` | 699,425 | 1,643 | 460 | 1,183 | 99.8% |
| `error exception` | 699,425 | 873 | 383 | 490 | 99.9% |
| `request response` | 699,425 | 4,439 | 430 | 4,009 | 99.4% |
| `context bind` | 699,425 | 1,016 | 402 | 614 | 99.9% |

### 7.2 Token Efficiency: RAG Baseline

Best-performing RAG configuration per repo (RAG-512 in all cases):

| Repository | Baseline tokens | RAG-512 avg/query | Reduction | Ratio |
|------------|:--------------:|:----------------:|:---------:|:-----:|
| expressjs/express | 137,978 | 2,887 | 97.9% | 56.0x |
| fastapi/fastapi | 699,425 | 2,850 | 99.6% | 248.5x |
| gin-gonic/gin | 187,018 | 4,352 | 97.7% | 43.5x |

RAG token consumption increases with chunk size:

| Repository | RAG-512 avg | RAG-1024 avg | RAG-2048 avg |
|------------|:----------:|:-----------:|:-----------:|
| expressjs/express | 2,887 | 6,023 | 7,057 |
| fastapi/fastapi | 2,850 | 4,279 | 5,512 |
| gin-gonic/gin | 4,352 | 7,539 | 12,850 |

### 7.3 Head-to-Head Comparison

Both harnesses ran back-to-back on 2026-03-28 against the same index state.

| Repository | Best RAG avg/query | AST avg/query | AST advantage |
|------------|:-----------------:|:------------:|:------------:|
| expressjs/express | 2,887 (RAG-512) | 924 | **3.1x** |
| fastapi/fastapi | 2,850 (RAG-512) | 1,834 | **1.6x** |
| gin-gonic/gin | 4,352 (RAG-512) | 1,124 | **3.9x** |

AST retrieval uses fewer tokens on every tested repository. The margin ranges from 1.6x (FastAPI) to 3.9x (Gin). The FastAPI result is notable: this is the largest repo (951 files, 5,325 symbols), where dense embedding retrieval might be expected to have an advantage. It does not --- BM25 over symbol names plus selective source retrieval still outperforms vector similarity over text chunks.

### 7.4 Chunk Integrity

The "complete chunk" rate measures how often a retrieved RAG chunk starts at a definition boundary and has balanced braces/indentation. The "split" rate measures how often a chunk is cut mid-function.

| Repository | RAG-512 complete | RAG-512 split | RAG-1024 split | RAG-2048 split |
|------------|:---------------:|:------------:|:-------------:|:-------------:|
| expressjs/express | 7% | 7% | 7% | 0% |
| fastapi/fastapi | 7% | **53%** | **40%** | **33%** |
| gin-gonic/gin | 13% | 7% | 7% | 7% |

FastAPI's high split rate at 512-token chunks is a direct consequence of its code structure: many functions exceed 512 tokens, so the chunker cuts them. Increasing chunk size reduces splits but does not eliminate them, and increases token cost per retrieval.

**AST retrieval produces zero split results by construction.** Every returned symbol is a complete AST node --- a function, class, or method with full source from definition to closing delimiter.

### 7.5 Infrastructure Overhead

| Metric | RAG | AST |
|--------|-----|-----|
| Embedding model download | ~90 MB (one-time) | None |
| Runtime dependencies | LangChain + FAISS + sentence-transformers + torch (~1 GB) | tiktoken only (for benchmarking) |
| Index build (FastAPI, 951 files) | 23--49s (embedding-dominated) | 5--15s (tree-sitter parse) |
| Index build (Express, 165 files) | 6s | <1s |
| FAISS index size (FastAPI, 512) | 7,556 KB | ~few hundred KB (SQLite) |
| Query latency | 12--36 ms | <5 ms (BM25 in-process) |

The embedding step is the dominant cost in the RAG pipeline. For a 951-file repository, building the 512-token FAISS index requires ~47 seconds of CPU embedding time. The AST pipeline parses the same files in 5--15 seconds with no model inference.

### 7.6 End-to-End A/B Tests

Two controlled experiments were conducted on a production Vue 3 + Firebase codebase to measure real-world impact beyond synthetic benchmarks.

**Test 1: Naming audit (50 iterations, Claude Sonnet 4.6).** Each iteration scanned source files for misleading names, then applied fixes via three-subagent consensus.

| Metric | Native tools (Grep/Glob/Read) | AST retrieval | Delta |
|--------|:----------------------------:|:------------:|:-----:|
| Success rate | 72% | 80% | +8 pp |
| Timeout rate | 40% | 32% | -8 pp |
| Mean cost/iteration | $0.783 | $0.738 | -5.7% |
| Mean cache creation tokens | 104,135 | 93,178 | -10.5% |

Isolated tool-layer savings (controlling for fixed subagent overhead): **15--25%**.

**Test 2: Dead code detection (50 iterations, Claude Sonnet 4.6).** Pure tool-layer cost measurement with no subagent overhead.

| Metric | Native tools | AST retrieval | Delta |
|--------|:-----------:|:------------:|:-----:|
| Success rate | 96% | 92% | -4 pp |
| Mean cost/iteration | $0.4474 | $0.3560 | **-20.0%** |
| Mean total tokens | 449,356 | 289,275 | **-36%** |

The 20% cost reduction is statistically significant (Wilcoxon p=0.0074, Cohen's d=-0.583).

**Accuracy tradeoff.** The cost savings came with measurable accuracy degradation on fine-grained tasks:

| F1 metric | Native tools | AST retrieval | Delta |
|-----------|:-----------:|:------------:|:-----:|
| Dead files (all exports unused) | 95.8% | 95.7% | equivalent |
| Alive files (with some dead exports) | 100.0% | 69.6% | **-30.4 pp** |
| Export-level (individual export liveness) | 93.3% | 64.1% | **-29.2 pp** |

Dead-file detection --- the coarsest classification --- was equivalent. But alive-file classification and individual export liveness were significantly worse with AST retrieval. Root cause analysis (detailed in the full report) identified three factors: (1) the JS import extractor missed dynamic `import()` calls (fixed in v1.8.1), (2) the agent's strategy stopped at file-level liveness without verifying individual exports, and (3) neither variant followed transitive dead-code chains (fixed in v1.8.3). Two of the three gaps were tool bugs subsequently fixed; the third was a task-framing issue.

The honest summary: AST retrieval is cheaper but not uniformly better. For tasks requiring file-level "is this dead?" decisions, accuracy is equivalent at 20% lower cost. For tasks requiring export-level granularity, the agent's retrieval strategy must be more deliberate --- the tool provides the capability (`find_references` returns zero results for unused exports), but the agent did not use it consistently.

---

## 8. Analysis

### 8.1 Why AST Retrieval Uses Fewer Tokens

Three mechanisms contribute:

1. **No irrelevant context per result.** A RAG chunk at any size includes code before and after the relevant definition. A symbol result includes only the definition itself. The average AST fetch returns 200--600 tokens of source per symbol; RAG-512 returns ~500 tokens per chunk, but 3--5 of the 5 chunks typically contain irrelevant code that happens to share embedding-space proximity with the query.

2. **The search step is cheaper.** AST search returns symbol metadata (~370 tokens for 5 results): name, kind, file, line range. RAG search returns the full text of 5 chunks (~1,800--2,900 tokens for 5 results). The metadata-first approach lets the agent make retrieval decisions before paying the full-source cost.

3. **Metadata/source separation.** In the AST workflow, the search step returns compact metadata (~370 tokens for 5 results) and the fetch step returns full source. No content is transmitted twice. In the RAG workflow as measured here, the top 3 chunks appear in both the search response (5 chunks) and the fetch response (3 chunks). This is a measurement artifact of our two-step accounting, not an inherent RAG limitation --- a single-pass `similarity_search(k=3)` pipeline would avoid it. We discuss the impact of this accounting choice in Section 6.3.

### 8.2 Confounded Variables: Unit vs. Search Mechanism

This benchmark varies two things simultaneously: the **retrieval unit** (chunk vs. symbol) and the **search mechanism** (embedding similarity vs. BM25). We attribute the advantage primarily to the retrieval unit, but we have not isolated the two variables. Two unrun experiments would help:

- **Embedding search over AST symbols.** Use the same symbol-level retrieval units, but search by embedding similarity instead of BM25. If results are comparable, the retrieval unit is the dominant factor.
- **BM25 search over fixed-size chunks.** Use the same chunk-based retrieval, but search by BM25 instead of embedding similarity. If BM25-over-chunks approaches AST retrieval's efficiency, the search mechanism is the dominant factor.

We suspect the retrieval unit is the larger contributor --- the metadata-vs-source asymmetry in the search step and the absence of irrelevant context per result are structural properties of symbol-level retrieval, independent of how symbols are ranked. But without these controls, we cannot claim this definitively.

Additionally, the query corpus (Section 6.2) consists of short keyword queries that lexically match symbol names. This is the scenario where BM25 has maximum advantage over dense embeddings. Queries requiring semantic inference (e.g., "what runs before the handler on each request" to find middleware) would likely favor embedding search. The results should be read with this bias in mind.

### 8.3 Why FastAPI's Margin Is Narrower

AST retrieval still wins on FastAPI (1.6x advantage), but the margin is smaller than on Express (3.1x) or Gin (3.9x). Two factors:

1. **FastAPI has high symbol density.** With 5,325 symbols across 951 files, BM25 over symbol names produces more candidates, and the top-3 fetched symbols are sometimes larger (e.g., `request response` on FastAPI fetches 4,009 source tokens due to large Request/Response class definitions).

2. **RAG-512 performs relatively well on large, well-structured Python files.** FastAPI's code style produces chunks that, while often split (53%), still contain semantically relevant code due to the framework's dense annotation style.

### 8.4 Where RAG Still Makes Sense

AST symbol retrieval is not a universal replacement for RAG:

1. **Natural language documentation.** Docstrings, README files, API descriptions, and inline comments are not syntactic symbols. RAG over prose documents remains appropriate for these artifacts. (A companion tool for section-level document retrieval handles this case separately.)

2. **Semantic similarity across naming conventions.** BM25 search requires lexical overlap between the query and symbol names. A query like "authentication" will not match a function named `verify_credentials` unless the surrounding qualified name or file path contains relevant terms. Dense embedding models capture this semantic proximity. For codebases with inconsistent naming, RAG may surface relevant code that BM25 misses.

3. **Codebases without parseable structure.** Configuration files, data pipelines, template languages, and heavily metaprogrammed code may not produce meaningful AST symbols. RAG handles these as opaque text, which is at least something.

### 8.5 Failure Modes

**AST retrieval fails when:**
- The query intent maps to code spread across many small utility functions with generic names.
- The symbol index is stale (file changed since last parse). Staleness detection mitigates this.
- The language lacks a tree-sitter grammar. Coverage is broad (70+ languages) but not complete.

**RAG fails when:**
- The relevant code is smaller than the chunk size (over-retrieval).
- The relevant code is larger than the chunk size (under-retrieval, split across chunks).
- The query is specific but the embedding model generalizes too aggressively, returning topically related but functionally irrelevant chunks.

---

## 9. Discussion

### 9.1 Implications for Developer Tooling

The results suggest that code retrieval tools should match their retrieval unit to the structure of the data. Code has natural units --- functions, classes, methods --- that are well-defined, complete, and independently meaningful. Using these as retrieval units eliminates an entire class of problems (chunk boundaries, irrelevant context, double-counting) without adding complexity.

This is not a new insight. IDEs have navigated code by symbols since the 1990s (ctags, IntelliSense, Language Server Protocol). What is new is that LLM agents can use the same granularity, and the token economics make it worth doing.

### 9.2 Toward a Retrieval Interface Standard

The retrieval workflow tested here follows a three-step pattern:

1. **Discover:** enumerate available repositories, files, or outlines.
2. **Search:** find relevant symbols by name, kind, or text query.
3. **Retrieve:** fetch complete source for selected symbols.

This pattern is general enough to standardize. One such effort is the jMunch Retrieval Interface (jMRI) [9], an open specification for token-efficient context retrieval in MCP servers. jMRI formalizes the discover/search/retrieve contract, requires that retrieved content represent complete semantic units (functions, classes, documentation sections), mandates stable identifiers for caching and cross-reference, and includes per-response token savings metadata so agents can measure efficiency per query. The specification defines two compliance tiers (Basic and Full), allowing implementations to adopt the interface incrementally regardless of their underlying search mechanism (BM25, embedding, hybrid).

The key insight behind jMRI --- and the one supported by this paper's results --- is that the retrieval *interface* should constrain the retrieval *unit*. An interface that guarantees complete syntactic units eliminates chunk boundary artifacts at the contract level, not as an implementation detail that individual tools may or may not get right.

### 9.3 Cost at Scale

At current LLM API pricing ($3--15 per million input tokens for frontier models), the difference between ~1,000 and ~3,000 tokens per query is small in absolute terms. At scale, it compounds. An agentic workflow that makes 50 retrieval queries per task, run across 100 tasks per day:

| Scenario | RAG-512 tokens/day | AST tokens/day | Monthly savings at $10/M |
|----------|:-----------------:|:--------------:|:------------------------:|
| Best case (Express-like, 3.1x margin) | 14,435,000 | 4,620,000 | $98.15 |
| Worst case (FastAPI-like, 1.6x margin) | 14,250,000 | 9,170,000 | $50.80 |

The range matters. On a tightly scoped codebase with well-named symbols, the savings are substantial. On a large, symbol-dense repository, the margin is real but more modest. For teams running agentic CI/CD, code review bots, or continuous refactoring agents across multiple repositories, even the worst-case savings are material over months.

### 9.4 MCP Ecosystem Fit

The Model Context Protocol (MCP) provides a standardized interface for LLM tools. AST symbol retrieval fits naturally into MCP's tool-call model: `search_symbols` and `get_symbol_source` are stateless, cacheable operations that return structured JSON. The agent controls retrieval depth --- it can fetch one symbol or ten, based on the search results. This is the opposite of RAG's "always return k chunks" model, and it gives agents fine-grained control over their token budget.

---

## 10. Limitations

### 10.1 Language Coverage

Tree-sitter grammars exist for many languages, and the implementation tested here supports 70+ (with custom regex-based extractors for languages where tree-sitter grammars lack clean named fields). Languages without grammars (niche DSLs, proprietary languages) require custom extractors or fall back to file-level retrieval. Adding a new language requires mapping AST node types to symbol kinds --- typically a few hours of work, but non-trivial.

### 10.2 Indexing Overhead

The AST index must be built before queries can be served. For a 951-file repository, this takes 5--15 seconds. For monorepos with tens of thousands of files, indexing may take minutes. Incremental indexing (re-parse only changed files) mitigates this for iterative workflows, but the initial build cost is unavoidable.

### 10.3 Query Corpus and Repository Diversity

Five queries across three repositories is sufficient to demonstrate the structural advantage on web framework codebases but does not claim coverage of all code exploration patterns or architectures.

**Repository bias.** All three repositories are HTTP request-routing frameworks. They share a common conceptual vocabulary (router, middleware, handler, request, response, context), and the query corpus maps directly to this vocabulary. Codebases with different structures --- compilers, ML training loops, game engines, infrastructure-as-code, heavily metaprogrammed or macro-heavy code --- may produce different results. We have not tested these.

**Query bias.** All five queries are short keyword phrases that lexically match symbol names. Queries requiring semantic inference, natural language phrasing, or cross-file tracing may favor embedding-based retrieval. The results generalize most confidently to keyword-style code navigation queries on well-structured application codebases.

### 10.4 Non-Code Use Cases

AST symbol retrieval is specific to source code. Documentation, configuration files, data files, and prose artifacts require different retrieval strategies. The benchmarks in this paper measure code retrieval only.

### 10.5 Retrieval Precision

The benchmark measures token efficiency, not retrieval precision. Whether the top-3 retrieved symbols are the *correct* symbols for a given query is a separate question. Independent evaluation (jMunchWorkbench) reports 96% precision on the same query corpus, but this metric is not the focus of this paper.

### 10.6 Single Tokenizer

All token counts use `tiktoken` with `cl100k_base`. Claude and GPT tokenizers produce slightly different counts for the same input. We use `cl100k_base` as a common reference point; relative ratios (AST vs. RAG) are stable across tokenizer choices.

---

## 11. Threats to Validity

**Internal validity.** The two-step token accounting (search 5 + fetch 3) inflates RAG's token count relative to a single-pass pipeline. We estimate this adds 30--40% to RAG's measured tokens. Even after adjusting, AST retrieval remains more efficient, but the margin narrows --- particularly on FastAPI, where the adjusted comparison would approach 1.1--1.2x.

**Construct validity.** Token count is a proxy for cost and context window pressure, not a direct measure of retrieval quality. A system that uses fewer tokens but returns irrelevant code is worse. We do not measure retrieval precision comparatively in this benchmark.

**External validity.** Three web frameworks from one architectural pattern, tested with keyword queries, do not represent all codebases or query types. Generalization to monorepos, DSLs, metaprogrammed code, or natural-language queries is unvalidated.

**Experimenter bias.** The AST retrieval system under test was developed by the author. The RAG baseline was implemented specifically for this comparison and was not optimized. A third-party replication using a production RAG pipeline would strengthen the findings.

---

## 12. Related Work

**CodeSearchNet** (Husain et al., 2019) established benchmarks for code search using natural language queries over function-level documentation. The retrieval unit is the function --- consistent with our approach --- but the search mechanism is embedding-based, not BM25 over symbol names.

**RepoMap** (Gauthier, 2023) uses tree-sitter to build repository outlines for LLM context, compressing file structure into tag-based summaries. This addresses the "what's in this repo" question but does not provide full source retrieval for individual symbols.

**Aider** (Gauthier, 2023) integrates repository maps with LLM code editing. Its `--map-tokens` budget controls how much structural context the LLM receives. This is complementary to symbol retrieval: the map provides orientation, symbol retrieval provides depth.

**SWE-agent** (Yang et al., 2024) and **SWE-bench** (Jimenez et al., 2024) evaluate LLM agents on real GitHub issues. These agents use file-level tools (open, scroll, search) that operate at a coarser granularity than symbol retrieval. Integrating symbol-level tools into SWE-agent's action space is a natural extension.

**GraphCodeBERT** (Guo et al., 2021) and **UniXcoder** (Guo et al., 2022) use data flow and AST structure during pre-training to improve code understanding. These models could serve as embedding backends for a hybrid approach: AST-structured retrieval with semantic re-ranking.

---

## 13. Conclusion

The standard approach to code retrieval for LLM agents --- chunking source files into fixed-size text windows and retrieving by vector similarity --- is structurally mismatched to code. Code has natural boundaries (functions, classes, methods) that chunking ignores. The result is wasted tokens, fragmented context, and unnecessary infrastructure. The RAG ecosystem's own movement toward AST-aware chunking implicitly acknowledges this mismatch.

AST-based symbol retrieval takes the idea to its logical conclusion: the retrieval unit is the symbol, not the chunk. The results on three web framework codebases are concrete: 1.6--3.9x fewer tokens per query than a naive fixed-chunk RAG pipeline, zero chunk-boundary artifacts, no embedding model, and sub-5ms query latency. End-to-end A/B tests on a production codebase confirm 20% cost savings in real agentic workflows, though with accuracy tradeoffs on fine-grained classification tasks that warrant further investigation.

These results have clear scope limitations: three repos from one architectural niche, keyword queries that favor BM25, and a RAG baseline that does not represent production-grade retrieval. The structural argument --- that code retrieval should respect syntactic boundaries --- is stronger than the specific numbers, and holds regardless of whether the search mechanism is BM25, dense embeddings, or a hybrid.

The issue is not the model. It is how we feed it.

---

## References

1. Husain, H., Wu, H.-H., Gazit, T., Allamanis, M., & Brockschmidt, M. (2019). CodeSearchNet Challenge: Evaluating the State of Semantic Code Search. *arXiv:1909.09436*.
2. Gauthier, P. (2023). Aider: AI pair programming in your terminal. https://aider.chat
3. Yang, J., Jimenez, C. E., Wettig, A., et al. (2024). SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering. *arXiv:2405.15793*.
4. Jimenez, C. E., Yang, J., Wettig, A., et al. (2024). SWE-bench: Can Language Models Resolve Real-World GitHub Issues? *ICLR 2024*.
5. Guo, D., Ren, S., Lu, S., et al. (2021). GraphCodeBERT: Pre-training Code Representations with Data Flow. *ICLR 2021*.
6. Guo, D., Lu, S., Duan, N., et al. (2022). UniXcoder: Unified Cross-Modal Pre-training for Code Representation. *ACL 2022*.
7. Maxime Brunet et al. (2024). Tree-sitter: An incremental parsing system for programming tools. https://tree-sitter.github.io
8. Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. *NeurIPS 2020*.
9. Gravelle, J. (2026). jMunch Retrieval Interface (jMRI) Specification. https://github.com/jgravelle/mcp-retrieval-spec

---

## Appendix A: Reproduction Instructions

The pinned clone, checkout and index steps are in
[`REPRODUCING.md`](REPRODUCING.md). Indexing a repo's current `main` measures a
different tree than the one reported here.

```bash
pip install jcodemunch-mcp tiktoken

# Clone each repo and check out the commit REPRODUCING.md names, then:
jcodemunch-mcp index ./express --no-ai-summaries
jcodemunch-mcp index ./fastapi --no-ai-summaries
jcodemunch-mcp index ./gin --no-ai-summaries

# Run AST benchmark (prints markdown table + grand summary)
python benchmarks/harness/run_benchmark.py

# Run RAG baseline (requires additional deps)
pip install -r benchmarks/requirements-rag-bench.txt
python benchmarks/harness/run_rag_baseline.py

# Write results to files
python benchmarks/harness/run_benchmark.py --out benchmarks/results.md
python benchmarks/harness/run_rag_baseline.py --out benchmarks/rag_baseline_results.md
```

Both harnesses read from the same `IndexStore`, guaranteeing identical file sets.

## Appendix B: Raw Data Availability

- AST benchmark results: `benchmarks/results.md`
- RAG baseline results: `benchmarks/rag_baseline_results.md` and `rag_baseline_results.json`
- Task corpus: `benchmarks/tasks.json`
- A/B test reports: `benchmarks/ab-test-naming-audit-2026-03-18.md`, `benchmarks/ab-test-dead-code-2026-03-18.md`
- A/B test raw data: https://gist.github.com/Mharbulous/bb097396fa92ef1d34d03a72b56b2c61
- Source code: https://github.com/jgravelle/jcodemunch-mcp
