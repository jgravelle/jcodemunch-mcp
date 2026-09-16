"""Every published per-repo benchmark cell is derived from `jcm_reference.json`.

⚠⚠ `tests/test_provenance.py` gates the GRAND TOTAL — `measured.json` against
the reference — and nothing gated the per-repo rows. So the grand total stayed
correct in both READMEs while three different sets of per-repo numbers drifted
apart underneath it (workflows FINDINGS W-16, which refused three consecutive
releases before this):

| source | express | fastapi | gin | grand |
|---|---|---|---|---|
| `benchmarks/jcm_reference.json` (CI-captured) | 1007.2 | 2149.4 | 1536.8 | 23,467 |
| `README.md` | 1,017 | 2,218 | 1,573 | 23,467 |
| `benchmarks/README.md` | 1,002 | 2,271 | 1,577 | **24,249** |

That the per-repo totals in the reference SUM to the grand total both files
already printed is what proves all three describe one run: 5036 + 10747 + 7684 =
23,467. The rows were simply never regenerated when the reference was recaptured.

⚠ A ratchet over the grand total alone cannot see this, because the total is the
figure everyone remembers to update. **Gate the cells the reader actually reads.**

Practice 4: never hand-type a benchmark number. This file is what makes that
enforceable for the README tables rather than merely stated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE = _ROOT / "benchmarks" / "jcm_reference.json"

# (file, does the table carry a read-all baseline column of its own)
_TABLES = (
    Path("README.md"),
    Path("benchmarks") / "README.md",
)

_ROW = re.compile(
    r"^\|\s*(?P<repo>[\w.-]+/[\w.-]+)\s*\|(?P<rest>.*)\|\s*$", re.MULTILINE
)


def _reference() -> dict:
    return json.loads(_REFERENCE.read_text(encoding="utf-8"))


def _cells(rest: str) -> list[str]:
    return [c.strip() for c in rest.split("|")]


def _numbers(cells: list[str]) -> list[float]:
    """Every bare number in a row, with thousands separators and markup gone."""
    out = []
    for c in cells:
        c = c.replace("*", "").replace(",", "").replace("avg", "").strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)x?", c)
        if m:
            out.append(float(m.group(1)))
    return out


def _published_rows(path: Path) -> dict[str, list[float]]:
    text = (_ROOT / path).read_text(encoding="utf-8")
    rows = {}
    for m in _ROW.finditer(text):
        nums = _numbers(_cells(m.group("rest")))
        if len(nums) >= 3:
            rows.setdefault(m.group("repo"), nums)
    return rows


@pytest.mark.parametrize("path", _TABLES, ids=[str(p) for p in _TABLES])
def test_the_table_names_every_repo_in_the_reference(path):
    """Non-vacuity: a table this cannot parse would pass every assertion below."""
    published = _published_rows(path)
    for repo in _reference()["repos"]:
        assert repo in published, (
            f"{path} has no parseable row for {repo}; this guard is measuring nothing"
        )


def test_the_references_own_average_is_its_total_over_its_queries():
    """The artifact's `avg_tokens_per_query` is the field both tables publish,
    so it is checked against the totals before anything is derived from it.

    Without this the tables could agree with a field that had itself drifted.
    """
    for repo, r in _reference()["repos"].items():
        expected = round(r["jmunch_total_tokens"] / r["queries"])
        assert r["avg_tokens_per_query"] == expected, (
            f"{repo}: reference says avg {r['avg_tokens_per_query']} but "
            f"{r['jmunch_total_tokens']}/{r['queries']} rounds to {expected}"
        )


@pytest.mark.parametrize("path", _TABLES, ids=[str(p) for p in _TABLES])
def test_the_jcodemunch_average_is_the_reference_total_over_its_queries(path):
    """The cell a reader quotes, against the artifact CI captured.

    Rounded to the nearest token, which is how both tables print it.
    """
    ref = _reference()
    published = _published_rows(path)
    wrong = []
    for repo, r in ref["repos"].items():
        expected = r["avg_tokens_per_query"]
        if expected not in published[repo]:
            wrong.append(f"{repo}: expected {expected}, row has {published[repo]}")
    assert not wrong, (
        f"{path} per-repo jCodeMunch averages disagree with "
        f"benchmarks/jcm_reference.json:\n  " + "\n  ".join(wrong)
    )


@pytest.mark.parametrize("path", _TABLES, ids=[str(p) for p in _TABLES])
def test_the_grand_total_is_the_sum_of_the_per_repo_totals(path):
    """The identity that proves the rows and the total describe ONE run.

    If a future reference splits them, this fails and the tables must be
    regenerated together rather than one of them patched.
    """
    ref = _reference()
    summed = sum(r["jmunch_total_tokens"] for r in ref["repos"].values())
    assert summed == ref["grand"]["jmunch_tokens"], (
        f"the reference's own per-repo totals sum to {summed} but its grand total "
        f"is {ref['grand']['jmunch_tokens']}; regenerate it before touching a table"
    )
    text = (_ROOT / path).read_text(encoding="utf-8")
    assert f"{summed:,}" in text, (
        f"{path} does not carry the grand total {summed:,} that its own per-repo "
        f"rows sum to"
    )


@pytest.mark.parametrize("path", _TABLES, ids=[str(p) for p in _TABLES])
def test_the_read_all_ratio_is_derived_from_the_published_average(path):
    """A ratio is arithmetic over two cells, so it cannot be edited alone.

    ⚠ Both tables express `vs read-all` as the whole corpus against ONE query,
    not against the repo's five-query total -- that is the published convention
    and this asserts it rather than re-deciding it.

    ⚠⚠ Derived from the PUBLISHED (rounded) average, not from the unrounded
    quotient, so a reader who divides the two printed cells gets the printed
    ratio. Using the unrounded value here put this guard and the table one tenth
    apart on fastapi (384.0 against 384.1) -- a disagreement invisible to
    everyone except the guard, which is the wrong way round.
    """
    ref = _reference()
    published = _published_rows(path)
    wrong = []
    for repo, r in ref["repos"].items():
        avg = r["avg_tokens_per_query"]
        expected = round(r["baseline_tokens"] / avg, 1)
        if expected not in published[repo]:
            wrong.append(f"{repo}: expected {expected}x, row has {published[repo]}")
    assert not wrong, (
        f"{path} `vs read-all` ratios are not derived from the published average "
        f"and the reference baseline:\n  " + "\n  ".join(wrong)
    )
