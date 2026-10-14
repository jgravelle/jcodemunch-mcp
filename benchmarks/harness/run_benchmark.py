#!/usr/bin/env python3
"""jcodemunch-mcp benchmark harness.

Measures real (tiktoken cl100k_base) token counts for the jcodemunch
retrieval workflow vs an "open every file" baseline on identical tasks.

Usage:
    python benchmarks/harness/run_benchmark.py [owner/repo ...]
    python benchmarks/harness/run_benchmark.py --out results.md

If no repos are given, runs against all three canonical benchmark repos:
    expressjs/express  |  fastapi/fastapi  |  gin-gonic/gin

Those repos must already be indexed (jcodemunch index_repo owner/repo).

Methodology
-----------
Baseline:   concatenate all raw source files stored in the index, count tokens.
            This is the minimum tokens a "read everything first" agent would use.

jMunch:     for each task query —
              1. call search_symbols (max_results=5)     → count JSON response tokens
              2. call get_symbol for the top 3 hits      → count each source snippet
            Total = search response + 3 × symbol source.

Tokenizer:  tiktoken cl100k_base (GPT-4 / Claude family ballpark; consistent
            across runs regardless of model).

Output:     per-task rows + per-repo summary + grand summary table (markdown).

Reference artifact
------------------
`--reference` writes `benchmarks/jcm_reference.json`: the machine-readable
jCodeMunch side of every comparison harness in this directory. The comparison
harnesses (`run_rag_baseline.py`, `run_odysseus_compare.py`) READ that file
instead of carrying hand-typed constants, and refuse to print a ratio for a
repo the artifact does not cover.

Each repo entry records the index state it was measured against (file count,
baseline tokens). A comparison harness that measures a different index state
labels the row cross-run rather than dividing a fresh number by a stale one.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap: add src/ to path so we can import jcodemunch_mcp directly
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

import os  # noqa: E402

try:
    import tiktoken
except ImportError:
    sys.exit("tiktoken not found — run: pip install tiktoken")

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.search_symbols import search_symbols
from jcodemunch_mcp.tools.get_symbol import get_symbol_source as get_symbol

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task corpus — loaded from benchmarks/tasks.json if present, else hardcoded
# ---------------------------------------------------------------------------

_CORPUS_PATH = _REPO_ROOT / "benchmarks" / "tasks.json"

def _load_corpus():
    if _CORPUS_PATH.exists():
        import json as _json
        corpus = _json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
        repos = [r["id"] for r in corpus.get("repos", [])]
        tasks = [t["query"] for t in corpus.get("tasks", [])]
        pins = {r["id"]: r.get("sha") for r in corpus.get("repos", []) if r.get("sha")}
        return repos, tasks, pins
    # Fallback hardcoded values (kept in sync with tasks.json). No SHAs here on
    # purpose: a pin that lives in two places drifts, and an unpinned run must
    # be visibly unpinned rather than quietly pinned to a stale constant.
    return (
        ["expressjs/express", "fastapi/fastapi", "gin-gonic/gin"],
        ["router route handler", "middleware", "error exception", "request response", "context bind"],
        {},
    )

DEFAULT_REPOS, TASKS, PINNED_SHAS = _load_corpus()

SEARCH_MAX_RESULTS = 5
SYMBOLS_FETCHED = 3        # get_symbol calls per query in the jMunch workflow

TOKENIZER = "cl100k_base"  # tiktoken encoding name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_enc = tiktoken.get_encoding(TOKENIZER)


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _serialize(obj) -> str:
    """Stable JSON serialization of a tool response."""
    return json.dumps(obj, separators=(",", ":"), default=str)


def _parse_repo(repo_str: str) -> tuple[str, str]:
    """Split 'owner/repo' → (owner, repo). Handles 'local/name' too."""
    parts = repo_str.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "local", parts[0]


# ---------------------------------------------------------------------------
# Corpus state — which tree was measured, and was all of it there
# ---------------------------------------------------------------------------

def corpus_state(index, repo_str: str, pins: Optional[dict] = None) -> dict:
    """Describe the corpus a measurement was taken against.

    Two questions a published benchmark number has to be able to answer, and
    neither was recorded before v1.108.222:

    1. **Which upstream tree?** `index_repo` has no ref parameter — it fetches
       whatever the default branch points at today — so a run reproduces a
       published number only by accident. The pin lives in `tasks.json` and is
       checked here against the index's own `git_head`.
    2. **Was all of it indexed?** The file cap silently truncates. The
       `fastapi/fastapi` index behind every number published through
       v1.108.221 held 1,000 of 1,182 eligible files and said so nowhere; its
       coverage record was `{}`. (What the cap dropped turned out to be 182
       empty `__init__.py` files worth zero tokens, so the headline never moved
       — but that was discovered by measuring it, not by anything the artifact
       could tell you.)

    `complete` is tri-state on purpose. An index with no coverage record has
    not been found complete; it has not been measured. Collapsing that to
    `False` invents a defect, and collapsing it to `True` is the false-absence
    shape this project keeps closing.
    """
    pins = PINNED_SHAS if pins is None else pins
    head = getattr(index, "git_head", None) or None
    pinned = pins.get(repo_str)
    if not pinned:
        pin = "unpinned"
    elif not head:
        pin = "unknown"
    elif head == pinned:
        pin = "verified"
    else:
        pin = "mismatch"

    coverage = getattr(index, "coverage", None)
    if not isinstance(coverage, dict) or not coverage:
        complete, accepted = "unknown", None
    else:
        raw = coverage.get("complete")
        complete = raw if isinstance(raw, bool) else "unknown"
        accepted = coverage.get("files_accepted") or coverage.get("files_indexed")

    return {
        "git_head": head,
        "pinned_sha": pinned,
        "pin": pin,
        "complete": complete,
        "files_accepted": accepted,
    }


# Fields inside a SERVED payload whose value is a property of the machine and
# the moment rather than of retrieval. They are counted in the published figure
# — an agent really does pay for them — but they are pinned to a constant before
# the reproducibility signature is taken. See `stable_tokens`.
#
# ⚠ Measured 2026-08-03, and this is the whole reason `--verify-determinism`
# was red on CI while reproducing identical locally:
#
#   `timing_ms` tokenizes to 3 tokens below 1000ms and 4 at or above it, so a
#   query that straddles one second changes the payload by EXACTLY ONE TOKEN.
#   CI reported `search_tokens: 499 != 500`. A loaded runner straddles; a fast
#   dev box does not, which is why nobody could reproduce it.
#
#   `total_tokens_saved` is a MONOTONIC LIFETIME counter — it grows on every
#   call this installation has ever served. cl100k chunks digits in threes, so
#   it is 4 tokens from 10 to 12 digits and 5 from 13. It has not crossed yet,
#   which is the only reason it has not already done the same thing. A published
#   benchmark figure must not depend on how much the measuring machine has used
#   the product in its entire history.
_VOLATILE_PAYLOAD_KEYS = {"timing_ms", "total_tokens_saved"}


def _pin_volatile(obj):
    """Replace machine/moment-dependent payload values with fixed placeholders.

    Applied ONLY to the copy that feeds the reproducibility signature, never to
    the copy that is counted for publication.
    """
    if isinstance(obj, dict):
        return {
            k: (0 if k in _VOLATILE_PAYLOAD_KEYS else _pin_volatile(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_pin_volatile(x) for x in obj]
    return obj


# Per-task fields that inherit the wall clock through the counted payload, and
# so cannot be part of a reproducibility check. Each has a `stable_` counterpart
# that can.
_WALLCLOCK_DERIVED_FIELDS = {
    "search_ms", "tokens", "search_tokens", "fetch_tokens",
    "jmunch_tokens", "reduction_pct", "ratio",
    # Derived by dividing INTO jmunch_tokens, so they inherit its ±1-token
    # jitter. The grep baseline's own counts (`grep_baseline_tokens` and
    # friends) contain no wall-clock field and ARE compared — if grep-then-read
    # is not bit-reproducible, that is a real defect and the gate should say so.
    "grep_ratio", "grep_reduction_pct",
}


def token_signature(results: list[dict]) -> str:
    """Everything a REPRODUCTION has to match.

    ⚠⚠ **This deliberately does not compare the published token counts, and
    that is a concession, not an oversight.** The counted payload contains a
    wall-clock field (`_meta.timing_ms`), so those counts carry a ±1-token
    jitter that no seed can remove — see `_VOLATILE_PAYLOAD_KEYS` for the
    measurement. Asserting bit-equality on them made the gate red on every CI
    push from v1.108.222 onward for a reason that has nothing to do with
    retrieval.

    What is compared instead is `stable_tokens` and friends: the same payload,
    counted with those fields pinned. That is the question the gate was built to
    answer — *is retrieval reproducible* — and it is answerable. The published
    figures still count the payload as served, which is the pessimistic
    direction, and METHODOLOGY.md states the jitter.

    `search_ms` never entered the payload at all; it is dropped for the older
    reason that it means nothing to someone reproducing a token count.
    """
    def strip(obj):
        if isinstance(obj, dict):
            return {
                k: strip(v) for k, v in obj.items()
                if k not in _WALLCLOCK_DERIVED_FIELDS
            }
        if isinstance(obj, list):
            return [strip(x) for x in obj]
        return obj

    return json.dumps(strip(results), sort_keys=True, default=str)


def _signature_diff(first, second, path: str = "", _out=None) -> list[str]:
    """Every leaf that differs between two measurement passes, as `path: a != b`.

    Exists because `--verify-determinism` failing tells you nothing you can act
    on remotely. The interesting question is never *that* two passes differ, it
    is *which field* — a `tokens` figure moving is the retrieval bug the gate
    exists to catch, while `timing_ms` moving is the wall-clock digit width the
    harness already knows rides inside the counted payload. Those need opposite
    responses and a bare "DIFFERENT" cannot tell them apart.

    `search_ms` is dropped on both sides, matching `token_signature`.
    """
    out = [] if _out is None else _out
    if len(out) >= 40:
        return out
    if isinstance(first, dict) and isinstance(second, dict):
        for key in sorted(set(first) | set(second)):
            if key == "search_ms":
                continue
            here = f"{path}.{key}" if path else str(key)
            if key not in first or key not in second:
                out.append(f"{here}: present in only one pass")
                continue
            _signature_diff(first[key], second[key], here, out)
    elif isinstance(first, list) and isinstance(second, list):
        if len(first) != len(second):
            out.append(f"{path}: length {len(first)} != {len(second)}")
        else:
            for i, (a, b) in enumerate(zip(first, second)):
                _signature_diff(a, b, f"{path}[{i}]", out)
    elif first != second:
        out.append(f"{path}: {first!r} != {second!r}")
    return out


def _measure_in_subprocess(repos: list[str]) -> Optional[list[dict]]:
    """Re-measure `repos` in a fresh interpreter and return the raw results.

    ⚠ The second pass has to be a new PROCESS, not a second loop in this one.
    Repeating the loop in-process measures a WARM cache and is expected to
    disagree: `search_symbols` adds a ``_meta.cache_hit`` field once a query has
    been served, which costs 5 more tokens per query (~0.4% of the jMunch side).
    That is a real cost an agent pays on a repeated query, and it is not what
    this benchmark reports — every published number is a cold first call, which
    is the pessimistic direction. Comparing two loops in one process would fail
    this check forever for a reason that has nothing to do with reproducibility.

    `timing_ms` also rides inside the counted payload. Its rendered width has
    been stable across every run measured so far, but it is wall-clock and
    nothing guarantees that; a failure here that points only at timing digits is
    that, not a retrieval bug.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "second.json"
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *repos, "--json", str(out)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0 or not out.exists():
            print(proc.stderr[-2000:], file=sys.stderr)
            return None
        return json.loads(out.read_text(encoding="utf-8"))


def corpus_objection(state: dict) -> Optional[str]:
    """Why this corpus must not back a published number, or None when it may."""
    if state["pin"] == "unpinned":
        return "no SHA pinned in tasks.json"
    if state["pin"] == "unknown":
        return "index records no git_head, so the pin cannot be checked"
    if state["pin"] == "mismatch":
        return (
            f"indexed tree {str(state['git_head'])[:12]} != pinned "
            f"{str(state['pinned_sha'])[:12]}"
        )
    if state["complete"] is not True:
        return f"corpus completeness is {state['complete']!r}, not True"
    return None


# ---------------------------------------------------------------------------
# Baseline measurement
# ---------------------------------------------------------------------------

def _read_source(store: IndexStore, owner: str, name: str, index, content_dir: Path, rel_path: str) -> str:
    """Read one indexed source file's text.

    ⚠ **Both baselines go through this.** `measure_baseline` (read-everything)
    and `measure_grep_baseline` (grep-then-read) must see byte-identical
    content or the ratio between them describes two different corpora rather
    than two different workflows. Extracted verbatim from `measure_baseline`;
    it reads the same bytes it always did.
    """
    abs_path = content_dir / Path(rel_path.replace("/", "\\") if sys.platform == "win32" else rel_path)
    # Try both path separator forms
    if not abs_path.exists():
        abs_path = content_dir / rel_path
    try:
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        try:
            # Store-backed fallback (the real reader; the old
            # get_file_content_text name never existed → dead fallback).
            return store.get_file_content(owner, name, rel_path, index) or ""
        except Exception:
            return ""


def measure_baseline(store: IndexStore, owner: str, name: str) -> dict:
    """Count tokens across ALL raw source files stored in the index."""
    content_dir = store._content_dir(owner, name)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"Not indexed: {owner}/{name}"}

    total_tokens = 0
    file_count = 0
    for rel_path in index.source_files:
        content = _read_source(store, owner, name, index, content_dir, rel_path)
        total_tokens += count_tokens(content)
        file_count += 1

    return {"tokens": total_tokens, "files": file_count}


# ---------------------------------------------------------------------------
# Realistic agent baseline: grep, then read the top N files
# ---------------------------------------------------------------------------

GREP_FILES_READ = 3        # files opened per query, mirroring SYMBOLS_FETCHED


def measure_grep_baseline(
    store: IndexStore, owner: str, name: str, index, query: str
) -> dict:
    """What a competent agent WITHOUT this tool actually pays for one query.

    ``ripgrep`` the corpus for the query's terms, rank the matching files, open
    the top ``GREP_FILES_READ`` in full. That is the workflow the
    read-everything baseline does not model and no agent performs.

    ⚠⚠ **Every modelling choice here is deliberately made in the baseline's
    favour, i.e. against us.** A baseline tuned to look weak is not evidence:

    * **The headline grep cost is ``rg -l`` — paths only, no matched lines.**
      That is the leanest output a real agent gets, so it is the smallest
      honest number. ``rg`` *with* matched lines is also measured
      (``match_lines_tokens``) and is strictly larger; it is reported but not
      used as the headline.
    * **Files are ranked by match count.** Real grep output has no ranking at
      all — the agent guesses. Ranking gives the baseline a better-than-chance
      shot at the right file.
    * **Matching is case-insensitive substring on ANY term** (``rg -i
      'a|b|c'``), the most permissive reading, so the baseline finds more.

    ⚠ **Files are read WHOLE**, because that is what agents do — this project's
    own PreToolUse hook exists precisely to intercept whole-file ``Read`` calls.
    An agent that reads a line range pays less, and no estimator for that is
    offered here; see the note in ``tasks.json``.

    Contains no wall-clock field, so unlike the jMunch counts this is
    bit-reproducible and ``token_signature`` compares it directly.
    """
    content_dir = store._content_dir(owner, name)
    terms = [t.lower() for t in query.split() if t]
    if not terms:
        return {"error": f"empty query: {query!r}"}

    per_file: list[tuple[int, str]] = []   # (match_count, rel_path)
    match_line_tokens = 0

    for rel_path in index.source_files:
        content = _read_source(store, owner, name, index, content_dir, rel_path)
        if not content:
            continue
        hits = 0
        for lineno, line in enumerate(content.splitlines(), 1):
            low = line.lower()
            if any(t in low for t in terms):
                hits += 1
                # `rg -i 'a|b|c'` output shape: path:lineno:line
                match_line_tokens += count_tokens(f"{rel_path}:{lineno}:{line}\n")
        if hits:
            per_file.append((hits, rel_path))

    # Deterministic: match count desc, then path asc. A tie broken by iteration
    # order would make the file set depend on index insertion order, the exact
    # class of defect v1.108.228 fixed in the ranker.
    per_file.sort(key=lambda x: (-x[0], x[1]))
    top = per_file[:GREP_FILES_READ]

    # `rg -l` output: one path per line. The headline grep cost.
    file_list_tokens = count_tokens("".join(f"{p}\n" for _, p in per_file))

    read_tokens = 0
    for _, rel_path in top:
        read_tokens += count_tokens(_read_source(store, owner, name, index, content_dir, rel_path))

    return {
        "tokens": file_list_tokens + read_tokens,
        "grep_tokens": file_list_tokens,
        "read_tokens": read_tokens,
        "files_matched": len(per_file),
        "files_read": len(top),
        # Reported for disclosure, never the headline: strictly larger, so
        # using it would flatter us.
        "match_lines_tokens": match_line_tokens,
    }


# ---------------------------------------------------------------------------
# jMunch workflow measurement
# ---------------------------------------------------------------------------

def measure_jmunch(repo_str: str, query: str) -> dict:
    """
    jMunch workflow for one query:
      1. search_symbols → serialize response → count tokens
      2. get_symbol for top N hits → count tokens
    """
    # 1. Search
    t0 = time.perf_counter()
    # v1.70.0: pin detail_level="standard" so benchmark numbers stay comparable
    # to runs before the auto-default flip (baseline methodology preserved).
    search_result = search_symbols(repo=repo_str, query=query, max_results=SEARCH_MAX_RESULTS, detail_level="standard")
    search_ms = (time.perf_counter() - t0) * 1000

    search_text = _serialize(search_result)
    search_tokens = count_tokens(search_text)
    # The same payload, counted with the machine/moment fields pinned. Published
    # figures use the count above (the payload as an agent actually receives it);
    # only the reproducibility gate reads this one.
    stable_search_tokens = count_tokens(_serialize(_pin_volatile(search_result)))

    # Extract symbol IDs from results
    symbols = search_result.get("results") or search_result.get("symbols") or []
    symbol_ids = [s.get("id") or s.get("symbol_id") for s in symbols if s.get("id") or s.get("symbol_id")]
    symbol_ids = symbol_ids[:SYMBOLS_FETCHED]

    # 2. Get symbol sources
    fetch_tokens = 0
    stable_fetch_tokens = 0
    for sid in symbol_ids:
        sym_result = get_symbol(repo=repo_str, symbol_id=sid)
        fetch_tokens += count_tokens(_serialize(sym_result))
        stable_fetch_tokens += count_tokens(_serialize(_pin_volatile(sym_result)))

    total = search_tokens + fetch_tokens
    return {
        "tokens": total,
        "search_tokens": search_tokens,
        "fetch_tokens": fetch_tokens,
        # Reproducibility basis only — never published, never compared against a
        # baseline. See `_VOLATILE_PAYLOAD_KEYS`.
        "stable_tokens": stable_search_tokens + stable_fetch_tokens,
        "stable_search_tokens": stable_search_tokens,
        "stable_fetch_tokens": stable_fetch_tokens,
        "hits_fetched": len(symbol_ids),
        "search_ms": round(search_ms, 1),
    }


# ---------------------------------------------------------------------------
# Per-repo benchmark
# ---------------------------------------------------------------------------

def benchmark_repo(repo_str: str) -> dict:
    store = IndexStore()
    owner, name = _parse_repo(repo_str)

    # Resolve: might be stored as local/name with hash suffix
    all_repos = store.list_repos()
    matched = None
    for r in all_repos:
        if r["repo"] == repo_str:
            matched = r
            break
    # Fallback: try display_name match
    if matched is None:
        for r in all_repos:
            display = r.get("display_name", "")
            if display and f"{owner}/{display}" == repo_str:
                matched = r
                break

    if matched is None:
        return {"error": f"Not indexed: {repo_str}. Run: jcodemunch index_repo {repo_str}"}

    actual_repo = matched["repo"]
    owner2, name2 = _parse_repo(actual_repo)

    # Baseline (compute once for the repo)
    baseline = measure_baseline(store, owner2, name2)
    if "error" in baseline:
        return baseline

    baseline_tokens = baseline["tokens"]
    file_count = baseline["files"]
    symbol_count = matched.get("symbol_count", 0)
    index = store.load_index(owner2, name2)
    state = corpus_state(index, repo_str)

    task_rows = []
    for query in TASKS:
        jm = measure_jmunch(actual_repo, query)
        if "error" in jm:
            task_rows.append({"query": query, "error": jm["error"]})
            continue
        # ⚠ Measured in THIS run against THIS corpus, never a stored constant.
        # A live number beside a frozen one is the defect that ran four months
        # (see jcm CLAUDE.md maintenance rule 4).
        gb = measure_grep_baseline(store, owner2, name2, index, query)
        reduction_pct = (1 - jm["tokens"] / baseline_tokens) * 100 if baseline_tokens > 0 else 0
        ratio = baseline_tokens / jm["tokens"] if jm["tokens"] > 0 else float("inf")
        grep_tokens = gb.get("tokens") if "error" not in gb else None
        grep_ratio = (
            round(grep_tokens / jm["tokens"], 2)
            if grep_tokens and jm["tokens"] > 0 else None
        )
        grep_reduction_pct = (
            round((1 - jm["tokens"] / grep_tokens) * 100, 1)
            if grep_tokens else None
        )
        task_rows.append({
            "query": query,
            "baseline_tokens": baseline_tokens,
            "jmunch_tokens": jm["tokens"],
            "reduction_pct": round(reduction_pct, 1),
            "ratio": round(ratio, 1),
            # Realistic agent baseline (grep -l, then read the top 3 files).
            "grep_baseline_tokens": grep_tokens,
            "grep_grep_tokens": gb.get("grep_tokens"),
            "grep_read_tokens": gb.get("read_tokens"),
            "grep_files_matched": gb.get("files_matched"),
            "grep_files_read": gb.get("files_read"),
            "grep_match_lines_tokens": gb.get("match_lines_tokens"),
            "grep_ratio": grep_ratio,
            "grep_reduction_pct": grep_reduction_pct,
            "search_tokens": jm["search_tokens"],
            "fetch_tokens": jm["fetch_tokens"],
            "hits_fetched": jm["hits_fetched"],
            "search_ms": jm["search_ms"],
            # Reproducibility basis (`--verify-determinism`), not a published
            # figure and never compared against a baseline.
            "stable_tokens": jm["stable_tokens"],
            "stable_search_tokens": jm["stable_search_tokens"],
            "stable_fetch_tokens": jm["stable_fetch_tokens"],
        })

    return {
        "repo": repo_str,
        "display": matched.get("display_name", name2),
        "file_count": file_count,
        "symbol_count": symbol_count,
        "baseline_tokens": baseline_tokens,
        "corpus": state,
        "tasks": task_rows,
    }


# ---------------------------------------------------------------------------
# Reference artifact — the jCodeMunch side of every comparison harness
# ---------------------------------------------------------------------------

# v2 adds the corpus block: the upstream SHA each number was measured against
# and whether that corpus was complete. v1 rows carried a file count and a
# baseline token total with no way to say which tree produced them.
REFERENCE_SCHEMA = "jcm-benchmark-reference/v2"
REFERENCE_PATH = _REPO_ROOT / "benchmarks" / "jcm_reference.json"


def _jcm_version() -> str:
    """Version of the tree being measured, not whatever happens to be installed.

    The harness runs against `src/` via the path bootstrap above, so importlib
    metadata can name a different build (or nothing at all) — read pyproject
    first and keep the installed distribution as the fallback.
    """
    try:
        import re

        text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        from importlib.metadata import version

        return version("jcodemunch-mcp")
    except Exception:
        return "unknown"


def build_reference(results: list[dict]) -> dict:
    """Machine-readable jCodeMunch measurements for the comparison harnesses.

    Every number here is measured by this run. Nothing is estimated, and a repo
    that errored is omitted rather than carried forward from an older artifact —
    a comparison harness must be able to tell "not measured" from "measured low".
    """
    repos: dict[str, dict] = {}
    grand_baseline = grand_jmunch = task_runs = 0

    for res in results:
        if "error" in res:
            continue
        valid = [t for t in res["tasks"] if "error" not in t]
        if not valid:
            continue
        jmunch_total = sum(t["jmunch_tokens"] for t in valid)
        repos[res["repo"]] = {
            "avg_tokens_per_query": round(jmunch_total / len(valid)),
            "jmunch_total_tokens": jmunch_total,
            "queries": len(valid),
            # Index state this was measured against. A comparison harness that
            # measures different values is looking at a different corpus.
            "baseline_tokens": res["baseline_tokens"],
            "file_count": res["file_count"],
            "symbol_count": res.get("symbol_count", 0),
            # Which upstream tree, and was all of it indexed. Without this a
            # reader can see that two runs disagree but not which corpus moved.
            "corpus": res.get("corpus", {}),
        }
        grand_baseline += sum(t["baseline_tokens"] for t in valid)
        grand_jmunch += jmunch_total
        task_runs += len(valid)

    return {
        "schema": REFERENCE_SCHEMA,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "jcodemunch_version": _jcm_version(),
        "tokenizer": TOKENIZER,
        "workflow": f"search_symbols(max_results={SEARCH_MAX_RESULTS}) + get_symbol_source x{SYMBOLS_FETCHED}",
        "baseline_definition": "all indexed source files concatenated",
        "grand": {
            "baseline_tokens": grand_baseline,
            "jmunch_tokens": grand_jmunch,
            "task_runs": task_runs,
        },
        "repos": repos,
    }


MEASURED_JSON_PATH = _REPO_ROOT / "benchmarks" / "provenance" / "measured.json"


def sync_measured_artifact(reference: dict, path: Path = MEASURED_JSON_PATH) -> bool:
    """Rewrite the `token_reduction` block of the provenance artifact from this run.

    `benchmarks/provenance/measured.json` backs every `basis="measured"` entry in
    the provenance registry, and its own header says never to hand-edit a number
    without re-running the cited benchmark. It was hand-edited anyway, because
    nothing connected it to the run — so a re-measure updated METHODOLOGY.md and
    left this file asserting the previous run's totals, and the CI drift guard
    (tests/test_provenance.py) failed.

    Only `token_reduction` is touched. Every other block is backed by a different
    artifact and a different run; rewriting them here would be the same mistake
    pointed the other way.
    """
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if "token_reduction" not in artifact:
        return False

    grand = reference["grand"]
    baseline, jmunch = grand["baseline_tokens"], grand["jmunch_tokens"]
    block = artifact["token_reduction"]
    block["average_pct"] = round((1 - jmunch / baseline) * 100, 1) if baseline else 0.0
    block["task_runs"] = grand["task_runs"]
    block["baseline_tokens"] = baseline
    block["jcodemunch_tokens"] = jmunch
    block["tokenizer"] = reference["tokenizer"]
    block["run_date"] = reference["captured_at"][:10]
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return True


def load_reference(path: Path = REFERENCE_PATH) -> Optional[dict]:
    """Read the committed reference artifact, or None when it is absent/unusable.

    None is an honest answer and callers must render it as "not measured".
    Never substitute an estimate — that is the defect this artifact exists to
    close.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("schema") != REFERENCE_SCHEMA:
        return None
    if not isinstance(data.get("repos"), dict):
        return None
    return data


def reference_entry(reference: Optional[dict], repo: str) -> Optional[dict]:
    """The reference row for `repo`, or None when this run cannot be compared."""
    if not reference:
        return None
    entry = reference["repos"].get(repo)
    if not isinstance(entry, dict) or not entry.get("avg_tokens_per_query"):
        return None
    return entry


def reference_drift(entry: Optional[dict], file_count: int, baseline_tokens: int) -> Optional[str]:
    """Describe how this run's corpus differs from the reference measurement.

    Returns None when the two agree. A non-None result means the comparison is
    cross-run: the caller must say so rather than divide one against the other
    silently.
    """
    if not entry:
        return None
    deltas = []
    ref_files = entry.get("file_count")
    ref_baseline = entry.get("baseline_tokens")
    if ref_files and ref_files != file_count:
        deltas.append(f"{ref_files} -> {file_count} files")
    if ref_baseline and ref_baseline != baseline_tokens:
        deltas.append(f"baseline {ref_baseline:,} -> {baseline_tokens:,} tokens")
    return "; ".join(deltas) or None


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(results: list[dict], tokenizer: str) -> str:
    lines = []
    lines.append("# jcodemunch-mcp -- Token Efficiency Benchmark")
    lines.append("")
    lines.append(f"**Tokenizer:** `{tokenizer}` (tiktoken)  ")
    lines.append(f"**Workflow:** `search_symbols` (top {SEARCH_MAX_RESULTS}) + `get_symbol` x {SYMBOLS_FETCHED}  ")
    lines.append("**Baseline A (read-all):** all source files concatenated  ")
    lines.append(f"**Baseline B (grep-top-{GREP_FILES_READ}):** `rg -l` the query terms, then open the top {GREP_FILES_READ} files whole  ")
    lines.append("")

    grand_baseline = 0
    grand_grep = 0
    grand_jmunch = 0
    grand_tasks = 0

    for res in results:
        if "error" in res:
            lines.append(f"## {res.get('repo', '?')} — ERROR")
            lines.append(f"> {res['error']}")
            lines.append("")
            continue

        repo = res["repo"]
        lines.append(f"## {repo}")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Files indexed | **{res['file_count']:,}** |")
        lines.append(f"| Symbols extracted | **{res['symbol_count']:,}** |")
        lines.append(f"| Baseline tokens (all files) | **{res['baseline_tokens']:,}** |")
        state = res.get("corpus") or {}
        if state:
            head = state.get("git_head") or "unknown"
            marks = {"verified": "pinned", "mismatch": "**DRIFTED from pin**",
                     "unpinned": "**unpinned**", "unknown": "**unverifiable**"}
            lines.append(f"| Upstream commit | `{head[:12]}` ({marks.get(state.get('pin'), '?')}) |")
            complete = state.get("complete")
            lines.append(
                "| Corpus complete | "
                + ("yes" if complete is True else f"**{complete!r}**")
                + " |"
            )
        lines.append("")

        lines.append(
            "| Query | Read-all&nbsp;tokens | Grep-top-%d&nbsp;tokens | jMunch&nbsp;tokens "
            "| Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |" % GREP_FILES_READ
        )
        lines.append("|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|")

        repo_jmunch_sum = 0
        valid_tasks = [t for t in res["tasks"] if "error" not in t]
        for t in valid_tasks:
            gb = t.get("grep_baseline_tokens")
            gr = t.get("grep_ratio")
            lines.append(
                f"| `{t['query']}` "
                f"| {t['baseline_tokens']:,} "
                f"| {f'{gb:,}' if gb else '—'} "
                f"| {t['jmunch_tokens']:,} "
                f"| {t['ratio']}x "
                f"| {f'**{gr}x**' if gr else '—'} |"
            )
            repo_jmunch_sum += t["jmunch_tokens"]
            grand_jmunch += t["jmunch_tokens"]
            grand_baseline += t["baseline_tokens"]
            grand_grep += t.get("grep_baseline_tokens") or 0
            grand_tasks += 1

        if valid_tasks:
            avg_ratio = sum(t["ratio"] for t in valid_tasks) / len(valid_tasks)
            grep_ratios = [t["grep_ratio"] for t in valid_tasks if t.get("grep_ratio")]
            avg_grep = (
                f"**{sum(grep_ratios) / len(grep_ratios):.1f}x**" if grep_ratios else "—"
            )
            lines.append(
                f"| **Average** | — | — | — "
                f"| {avg_ratio:.1f}x "
                f"| {avg_grep} |"
            )
        lines.append("")

        # Detail table
        lines.append("<details><summary>Query detail (search + fetch tokens, latency)</summary>")
        lines.append("")
        lines.append("| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |")
        lines.append("|-------|-----------------:|------------------:|------------------:|---------------:|")
        for t in valid_tasks:
            lines.append(
                f"| `{t['query']}` "
                f"| {t['search_tokens']:,} "
                f"| {t['fetch_tokens']:,} "
                f"| {t['hits_fetched']} "
                f"| {t['search_ms']} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Grand summary
    if grand_tasks > 0:
        grand_reduction = (1 - grand_jmunch / grand_baseline) * 100
        grand_ratio = grand_baseline / grand_jmunch
        lines.append("---")
        lines.append("")
        lines.append("## Grand Summary")
        lines.append("")
        lines.append("| | Tokens |")
        lines.append("|--|-------:|")
        lines.append(f"| Baseline A total, read-all ({grand_tasks} task-runs) | {grand_baseline:,} |")
        if grand_grep:
            lines.append(f"| Baseline B total, grep-top-{GREP_FILES_READ} | {grand_grep:,} |")
        lines.append(f"| jMunch total | {grand_jmunch:,} |")
        lines.append(f"| Reduction vs read-all | {grand_reduction:.1f}% |")
        lines.append(f"| Ratio vs read-all | {grand_ratio:.1f}x |")
        if grand_grep:
            g_red = (1 - grand_jmunch / grand_grep) * 100
            lines.append(f"| **Reduction vs grep-top-{GREP_FILES_READ}** | **{g_red:.1f}%** |")
            lines.append(f"| **Ratio vs grep-top-{GREP_FILES_READ}** | **{grand_grep / grand_jmunch:.1f}x** |")
        lines.append("")
        if grand_grep:
            lines.append(
                f"> **Baseline B is the number to quote.** Read-all is a ceiling nobody "
                f"pays: it assumes an agent opens every file in the repository before "
                f"acting. Grep-then-read is what a competent agent without this tool "
                f"actually does, and it is {grand_grep / grand_baseline * 100:.1f}% of the "
                f"read-all figure — so measuring against read-all overstates the "
                f"advantage by about {grand_baseline / grand_grep:.0f}x."
            )
            lines.append("")
        lines.append(
            f"> Measured with tiktoken `{tokenizer}`. "
            f"Read-all = every indexed source file. "
            f"Grep-top-{GREP_FILES_READ} = `rg -l` the query terms, then open the top "
            f"{GREP_FILES_READ} matching files whole. "
            f"jMunch = search_symbols (top {SEARCH_MAX_RESULTS}) + "
            f"get_symbol x {SYMBOLS_FETCHED} per query. "
            f"Both baselines are measured in THIS run against THIS corpus."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # A measurement run is not a user: its tool calls must not be reported to the
    # public savings total, whoever runs this and wherever (cicd C-23). In main(),
    # not at import: a test that imports this module must not inherit it (review,
    # 2026-10-13). An explicit setting in the environment is kept.
    os.environ.setdefault("JCODEMUNCH_SHARE_SAVINGS", "0")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repos", nargs="*", help="owner/repo to benchmark (default: all 3 canonical repos)")
    parser.add_argument("--out", metavar="FILE", help="write markdown results to FILE")
    parser.add_argument("--json", metavar="FILE", dest="json_out", help="write raw JSON results to FILE")
    parser.add_argument(
        "--floor",
        action="store_true",
        help="exit non-zero when the run violates harness/thresholds.json "
             "token.grand_ratio_vs_grep or token.per_repo_rise_max (STANDARD criterion 2); "
             "a DOWNWARD move stays the re-sync warning",
    )
    parser.add_argument(
        "--reference",
        nargs="?",
        const=str(REFERENCE_PATH),
        metavar="FILE",
        help=(
            "write the machine-readable reference artifact the comparison harnesses read "
            f"(default: {REFERENCE_PATH.relative_to(_REPO_ROOT).as_posix()})"
        ),
    )
    parser.add_argument(
        "--allow-unpinned",
        action="store_true",
        help="write the reference artifact even when a corpus is unpinned, "
             "drifted, or of unknown completeness; stamps it provisional",
    )
    parser.add_argument(
        "--verify-determinism",
        action="store_true",
        help="measure the whole corpus twice and report whether the token "
             "counts are identical (they should be; there is no seed to pin)",
    )
    args = parser.parse_args()

    repos = args.repos or DEFAULT_REPOS

    print(f"jcodemunch-mcp benchmark harness  |  tokenizer: {TOKENIZER}", flush=True)
    print(f"Repos: {', '.join(repos)}", flush=True)
    print(f"Tasks: {len(TASKS)} queries × {len(repos)} repos = {len(TASKS) * len(repos)} measurements", flush=True)
    print()

    results = []
    for repo in repos:
        print(f"  benchmarking {repo} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        res = benchmark_repo(repo)
        elapsed = time.perf_counter() - t0
        if "error" in res:
            print(f"ERROR: {res['error']}")
        else:
            valid = [t for t in res["tasks"] if "error" not in t]
            avg_r = sum(t["reduction_pct"] for t in valid) / len(valid) if valid else 0
            print(f"done ({elapsed:.1f}s)  avg reduction {avg_r:.1f}%")
        results.append(res)

    if args.verify_determinism:
        print("\n  re-measuring in a fresh process ...", end=" ", flush=True)
        second = _measure_in_subprocess(repos)
        if second is None:
            print("FAILED to run")
            return 1
        identical = token_signature(results) == token_signature(second)
        print("identical" if identical else "DIFFERENT")
        if not identical:
            print(
                "Two processes measuring the same corpus produced different "
                "token counts. Nothing in this run is reproducible until that "
                "is found. (A warm-cache re-measure inside ONE process is "
                "expected to differ — see _measure_in_subprocess.)",
                file=sys.stderr,
            )
            # ⚠ Name the fields, do not just fail. This gate went red on CI at
            # v1.108.222 — the release that introduced it — and stayed red
            # through .223 and .224 while reproducing identical on the author's
            # box. A bare "DIFFERENT" is unactionable from a CI log: it cannot
            # distinguish the retrieval bug this exists to catch from the
            # wall-clock digit width `_measure_in_subprocess` already warns
            # about. Guessing from a remote failure is how the wrong thing gets
            # fixed twice.
            for line in _signature_diff(results, second):
                print(f"    {line}", file=sys.stderr)
            return 1

    print()
    md = render_markdown(results, TOKENIZER)
    print(md)

    if args.floor:
        from token_floor import verdicts as _floor_verdicts
        _ok, _lines = _floor_verdicts(results, load_reference())
        for _line in _lines:
            print(_line)
        if not _ok:
            print("token benchmark FLOOR violated (harness/thresholds.json); see lines above", file=sys.stderr)
            return 1

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\nResults written to: {args.out}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"JSON written to: {args.json_out}")

    if args.reference:
        ref_path = Path(args.reference)
        if len(repos) < len(DEFAULT_REPOS):
            # Writing a partial run over the artifact would silently drop repos
            # the comparison harnesses still need, and a missing repo reads as
            # "not measured" forever after.
            print(
                f"\nRefusing to write {ref_path}: benchmarked {len(repos)} repo(s), "
                f"artifact covers {len(DEFAULT_REPOS)}. Re-run without a repo filter.",
                file=sys.stderr,
            )
            return 1
        objections = []
        for res in results:
            if "error" in res:
                continue
            why = corpus_objection(res.get("corpus") or {"pin": "unpinned", "complete": "unknown"})
            if why:
                objections.append(f"  {res['repo']}: {why}")
        if objections and not args.allow_unpinned:
            # A published artifact whose corpus cannot be named is the defect
            # this gate exists to stop, not a warning to scroll past.
            print(
                f"\nRefusing to write {ref_path}: these corpora cannot back a "
                "published number.\n" + "\n".join(objections) +
                "\n  Fix the pins (see benchmarks/REPRODUCING.md), or pass "
                "--allow-unpinned to stamp the artifact as provisional.",
                file=sys.stderr,
            )
            return 1

        reference = build_reference(results)
        if objections:
            reference["provisional"] = True
            reference["caveats"] = [o.strip() for o in objections]
            print(
                "\nWARNING: writing a PROVISIONAL artifact — "
                f"{len(objections)} corpus objection(s) recorded in it.",
                file=sys.stderr,
            )
        ref_path.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
        print(f"Reference artifact written to: {ref_path}")
        if sync_measured_artifact(reference):
            print(f"Provenance artifact updated: {MEASURED_JSON_PATH}")
            print(
                "  Re-sync the prose mirrors of this run "
                "(benchmarks/results.md, benchmarks/METHODOLOGY.md, README.md) "
                "or tests/test_provenance.py will fail."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
