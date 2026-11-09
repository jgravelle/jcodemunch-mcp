"""The benchmark harnesses' text follows the pinned task corpus and the prose rules.

#1367 renamed express's fifth query to `param bind` in `benchmarks/tasks.json`,
and the RAG baseline's built-in fallback list kept `context bind`. A run
without the corpus file would then measure a query the published run never
asked. The published prose in `results.md` also kept an em dash its template
still wrote; the project's public text uses none.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "benchmarks" / "harness"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_bench_{name}", HARNESS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _rag_fallback_queries() -> list[str]:
    """The query list `_load_tasks` returns without a corpus file, read from the
    source: importing the harness needs the optional RAG packages."""
    tree = ast.parse((HARNESS / "run_rag_baseline.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_load_tasks")
    last = fn.body[-1]
    assert isinstance(last, ast.Return) and isinstance(last.value, ast.Tuple)
    return ast.literal_eval(last.value.elts[1])


def test_the_rag_fallback_queries_are_the_corpus_queries():
    corpus = json.loads((ROOT / "benchmarks" / "tasks.json").read_text(encoding="utf-8"))
    assert _rag_fallback_queries() == [t["query"] for t in corpus["tasks"]]


def test_the_rag_fallback_repos_are_the_corpus_repos():
    tree = ast.parse((HARNESS / "run_rag_baseline.py").read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body
        if isinstance(n, ast.Assign) and [getattr(t, "id", None) for t in n.targets] == ["DEFAULT_REPOS"]
    )
    corpus = json.loads((ROOT / "benchmarks" / "tasks.json").read_text(encoding="utf-8"))
    assert ast.literal_eval(node.value) == [r["id"] for r in corpus["repos"]]


def _prose_lines(text: str) -> list[str]:
    """Lines that are not table rows: a table cell's lone dash is a placeholder."""
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("|")]


def test_the_published_results_prose_has_no_em_dash():
    text = (ROOT / "benchmarks" / "results.md").read_text(encoding="utf-8")
    bad = [ln[:120] for ln in _prose_lines(text) if "—" in ln]
    assert bad == []


def test_the_results_template_writes_no_em_dash_into_prose():
    bench = _load("run_benchmark")
    task = {
        "query": "a", "baseline_tokens": 10_000, "jmunch_tokens": 10,
        "reduction_pct": 99.0, "ratio": 1000.0,
        "grep_baseline_tokens": 100, "grep_ratio": 10.0, "grep_reduction_pct": 90.0,
        "search_tokens": 4, "fetch_tokens": 6, "hits_fetched": 3, "search_ms": 1.0,
    }
    results = [
        {
            "repo": "o/n", "display": "n", "file_count": 3, "symbol_count": 9,
            "baseline_tokens": 10_000, "tasks": [task, dict(task, query="b")],
        },
        # The error branch writes its own heading.
        {"repo": "o/broken", "error": "index failed"},
    ]
    rendered = bench.render_markdown(results, bench.TOKENIZER)
    bad = [ln[:120] for ln in _prose_lines(rendered) if "—" in ln]
    assert bad == []
