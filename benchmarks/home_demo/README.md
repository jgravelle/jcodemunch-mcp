# Home page demo: all five queries, including the misses

The jcodemunch.com home page shows one lookup on fastapi. That query was one of
five, written and frozen before anything was measured. This folder publishes
all five, with what each one returned, so the demo can be checked against the
ones that did not make the page.

**Short version:** on 1.108.391, two of the five queries put an answering
symbol in the top 5, and in both cases it is the public `Depends` API at rank
3, not the code that resolves dependencies. Three queries put no answering
symbol in the top 5.

## What was measured

- **Repository:** fastapi at commit `a64dfbbd21a445288ff583d58e1f646fe6baf3af`
  (the same pin as the published token benchmark, `benchmarks/tasks.json`).
- **Version:** jcodemunch-mcp 1.108.391, installed in a fresh virtual
  environment, with a fresh index of that checkout.
- **Method:** the functions of the published benchmark harness,
  `benchmarks/harness/run_benchmark.py` at v1.108.389 (`0e636483`), unchanged:
  - **jCodeMunch:** `search_symbols(max_results=5, detail_level="standard")`,
    then `get_symbol_source` for the top 3 hits. The figure is the tokens of
    those four responses.
  - **Grep, then read the top 3 files:** every indexed file is matched
    line by line against the query's words, files are ranked by matching
    lines, the file list is counted, and the top 3 files are read whole.
  - **Read the library:** every `*.py` file under `fastapi/` (48 files),
    without tests, docs or examples. This is narrower than the read-all
    figure in `benchmarks/results.md`, which counts every indexed file.
- **Tokens:** tiktoken `cl100k_base`. No model was called.

### The answer rule, fixed before measuring

A query "has the answer in the top 5" only if one of its top 5 hits is:

- `solve_dependencies` (`fastapi/dependencies/utils.py`), the function that
  resolves a request's dependencies;
- `get_dependant` (`fastapi/dependencies/utils.py`); or
- the public `Depends` (`fastapi/param_functions.py` function or
  `fastapi/params.py` class).

Fields such as `Depends.dependency` and every other symbol do not count.

## Results

| # | Query | Grep, then read the top 3 files | Read the library | jCodeMunch | Answer in the top 5? | Best rank of an answering symbol (top 100) |
|--:|---|--:|--:|--:|---|---|
| 1 | how does fastapi dependency injection work | 101,778 | 151,227 | 3,469 | yes: `Depends` (function) at 3 | Depends 3 |
| 2 | how does FastAPI resolve Depends | 95,045 | 151,227 | 27,490 | yes: `Depends` (class) at 3 | Depends 3 |
| 3 | where are Depends dependencies resolved for a request | 134,405 | 151,227 | 1,224 | no | Depends 8, solve_dependencies 31 |
| 4 | dependency injection Depends resolution | 8,019 | 151,227 | 1,192 | no | Depends 20 |
| 5 | how are path operation dependencies solved | 129,663 | 151,227 | 1,463 | no | Depends 9, solve_dependencies 22 |

`get_dependant` is not in the top 100 for any query.

### Top 5 hits per query

1. `openapi/docs.py::_html_safe_json`, `routing.py::_FASTAPI_SCOPE_KEY`,
   **`param_functions.py::Depends`**, `SolvedDependency.dependency_cache`
   (field), `params.Depends.dependency` (field).
2. `FastAPI.include_router`, `FastAPI.__init__`, **`params.py::Depends`**
   (class), `ParamDetails.depends` (field), `APIRouter.include_router`.
3. `_FrontendStaticFiles._get_resolved_directory`,
   `_get_resolved_absolute_path`, and three `.dependencies` fields
   (`APIRoute`, `_APIRouteLike`, `_EffectiveRouteContext`).
4. Five `UV_RESOLUTION` / `uv-resolution` keys in
   `.github/workflows/test.yml`.
5. `SolvedDependency` (class), `utils.py::generate_operation_id_for_path`, a
   `pyproject.toml` ruff setting, and two `operationId` fields in
   `openapi/models.py`.

Full ids, token splits and ranks: [`results-1.108.391.json`](results-1.108.391.json).
The itemised grep files and fetched symbols for query 1, the one on the home
page: [`query-1-detail-1.108.391.json`](query-1-detail-1.108.391.json).

## Reading it

- **Query 1 is the home-page demo.** It finds the public `Depends` API at
  rank 3: how a user declares a dependency, not how FastAPI resolves one. The
  resolution code, `solve_dependencies`, is not in its top 100.
- **The grep figures are large partly because of the wording.** Words like
  "how" and "does" match most files: query 1's words match 907 of the 1,186
  indexed files. Grep-then-read costs 95,045 to 134,405 tokens on the four
  natural-language queries. The terse
  query 4 keeps it to 8,019. A ratio against grep on wordy queries is larger
  than one on terse queries for that reason.
- **Query 2 is expensive for jCodeMunch** (27,490 tokens): fetching its top 3
  hits, `FastAPI.include_router`, `FastAPI.__init__` and the `Depends` class,
  costs 18,057 of them.
- **Queries 4 and 5 return settings-file entries.** CI workflow keys and a
  `pyproject.toml` setting matched "resolution" and "path operation".

These are the 1.108.391 results. A later version that ranks differently will
be measured again on the same pin, with the same scripts and the same answer
rule. The new results will be added beside these, not over them.
