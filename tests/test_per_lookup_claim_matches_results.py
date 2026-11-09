"""The token claim is the mean reduction per lookup, read from results.md.

The README and the docs quoted the grep-and-read figure pooled over all 15
lookups (total grep tokens against total jCodeMunch tokens). Pooled, the two
fastapi lookups with the largest grep baselines weigh the most, so the figure
was higher than any typical lookup's. The claim is now the mean of the 15
per-lookup reductions, which `run_benchmark.py` writes in results.md's Grand
Summary.

Nothing here types the number: it is recomputed from results.md's per-lookup
rows, and every page that quotes it must agree. The pooled figure may still
appear in a table labelled as pooled, never as the headline.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results.md"
CLAIM_PAGES = [
    ROOT / "README.md",
    ROOT / "TOKEN_SAVINGS.md",
    ROOT / "benchmarks" / "README.md",
    ROOT / "benchmarks" / "METHODOLOGY.md",
    ROOT / "benchmarks" / "REPRODUCING.md",
    ROOT / "docs" / "comparisons" / "jcodemunch-vs-serena.md",
]
_LOOKUP_ROW = re.compile(
    r"^\| `[^`]+` \| [\d,]+ \| ([\d,]+) \| ([\d,]+) \| [\d.]+x \| \*\*[\d.]+x\*\* \|$", re.M
)
_CLAIM = re.compile(r"(\d+(?:\.\d)?)%\s+fewer\s+tokens\s+per\s+lookup\s+on\s+average")


def _lookups():
    text = RESULTS.read_text(encoding="utf-8")
    rows = [(int(g.replace(",", "")), int(j.replace(",", ""))) for g, j in _LOOKUP_ROW.findall(text)]
    return text, rows


def _mean_and_pooled():
    text, rows = _lookups()
    mean = sum(1 - j / g for g, j in rows) / len(rows) * 100
    pooled = (1 - sum(j for _, j in rows) / sum(g for g, _ in rows)) * 100
    return text, rows, mean, pooled


def _pooled_ratio():
    _, rows = _lookups()
    return sum(g for g, _ in rows) / sum(j for _, j in rows)


def _pooled_headlines(text, pooled, ratio):
    """Pooled figures stated as a headline: percent or ratio, prose or table."""
    flat = re.sub(r"\s+", " ", text.replace("**", ""))
    p, r = f"{pooled:.1f}%", f"{ratio:.1f}x"
    found = [h for h in (f"{p} average", f"{p} fewer", f"{p} reduction", f"{r} fewer", f"{r} less")
             if h in flat]
    for line in text.splitlines():
        if line.startswith("|") and (p in line or r in line) and "pooled" not in line.lower():
            found.append(line.strip())
    return found


def test_results_md_holds_fifteen_lookups():
    _, rows = _lookups()
    assert len(rows) == 15, len(rows)


def test_the_grand_summary_row_is_the_mean_of_the_lookup_rows():
    text, rows, mean, _ = _mean_and_pooled()
    m = re.search(
        r"^\| \*\*Mean reduction per lookup vs grep-top-3\*\* \((\d+) lookups\) \| \*\*([\d.]+)%\*\* \|$",
        text, re.M,
    )
    assert m, "results.md Grand Summary has no mean-per-lookup row"
    assert int(m.group(1)) == len(rows)
    assert m.group(2) == f"{mean:.1f}"


@pytest.mark.parametrize("page", CLAIM_PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_quoted_claim_equals_the_mean(page):
    _, _, mean, pooled = _mean_and_pooled()
    text = page.read_text(encoding="utf-8")
    quoted = _CLAIM.findall(text)
    assert quoted, f"{page.name} no longer states the per-lookup claim"
    assert set(quoted) == {f"{mean:.1f}"}, quoted
    found = _pooled_headlines(text, pooled, _pooled_ratio())
    assert not found, f"{page.name} states the pooled figure unlabelled: {found}"


def test_the_pooled_guard_catches_each_form():
    _, _, _, pooled = _mean_and_pooled()
    ratio = _pooled_ratio()
    p, r = f"{pooled:.1f}%", f"{ratio:.1f}x"
    for bad in (f"**{r} fewer tokens** than grep", f"{p} fewer tokens", f"a {p} reduction",
                f"| **Grand total (15 task-runs)** | 1 | 2 | **{r}** |"):
        assert _pooled_headlines(bad, pooled, ratio), bad
    for ok in (f"| Grand total, pooled | 1 | {r} |", f"the totals are 1 -> 2 tokens ({p}, {r}); pooled"):
        assert not _pooled_headlines(ok, pooled, ratio), ok


@pytest.mark.skipif(find_spec("tiktoken") is None, reason="tiktoken not installed")
def test_the_generator_writes_the_mean_row():
    spec = importlib.util.spec_from_file_location(
        "_bench_harness_mean", ROOT / "benchmarks" / "harness" / "run_benchmark.py"
    )
    harness = importlib.util.module_from_spec(spec)
    sys.modules["_bench_harness_mean"] = harness
    spec.loader.exec_module(harness)

    def task(query, grep, jm):
        return {
            "query": query, "baseline_tokens": 10_000, "jmunch_tokens": jm,
            "reduction_pct": 99.0, "ratio": 10_000 / jm,
            "grep_baseline_tokens": grep, "grep_ratio": round(grep / jm, 2),
            "grep_reduction_pct": round((1 - jm / grep) * 100, 1),
            "search_tokens": 4, "fetch_tokens": 6, "hits_fetched": 3, "search_ms": 1.0,
        }

    rendered = harness.render_markdown(
        [{
            "repo": "o/n", "display": "n", "file_count": 3, "symbol_count": 9,
            "baseline_tokens": 10_000,
            "tasks": [task("a", 100, 10), task("b", 1000, 10)],
        }],
        harness.TOKENIZER,
    )
    # Per lookup 90% and 99%: the mean is 94.5%; pooled, 1 - 20/1100 is 98.2%.
    assert "| **Mean reduction per lookup vs grep-top-3** (2 lookups) | **94.5%** |" in rendered
    assert "| Reduction vs grep-top-3, pooled totals | 98.2% |" in rendered
    assert "Baseline B is the number to quote" in rendered
