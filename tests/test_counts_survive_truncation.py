"""A reported count must describe the repository, never the page (#559).

⚠⚠ `get_untested_symbols` computed `untested_count = len(symbols)` AFTER the
`max_results` slice, and derived `reached_pct` from it. `get_repo_health` calls
it with `max_results=1` because it "only needs the count", so the published
health/radar test axis read ~100% reach on every repository that had untested
code. @lilubot measured 4,893 untested of 6,352 (23.0% reached) reported as 100.

⚠ Written as a PROPERTY over every tool with a (count, capped list) pair rather
than as a regression test on the one that was reported. The sweep that found
this found nothing else -- `find_importers`, `find_references` and
`get_dead_code_v2` all count before slicing, and `find_references` carries a
comment saying why. This file is what keeps that true: the defect is invisible
to any single-call test, because one call's number is self-consistent. **Only
comparing two page sizes over the SAME repo can see it.**

⚠ The fixture must generate MORE rows than the small page, or every assertion
passes vacuously against the defect. `test_the_fixture_can_actually_truncate`
is the non-vacuity guard and must stay.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder

_BIG = 10_000
_SMALL = 1


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> tuple[str, str]:
    """A repo with many untested symbols, many importers and much dead code."""
    root: Path = tmp_path_factory.mktemp("truncation_repo")
    (root / "src").mkdir()

    # 12 untested functions + a hub module that 12 files import.
    (root / "src" / "hub.py").write_text(
        "def hub_target():\n    return 1\n", encoding="utf-8"
    )
    for i in range(12):
        (root / "src" / f"mod_{i}.py").write_text(
            f"from src.hub import hub_target\n\n\ndef untested_{i}():\n"
            f"    return hub_target() + {i}\n",
            encoding="utf-8",
        )
    (root / "src" / "covered.py").write_text(
        "def covered():\n    return 1\n", encoding="utf-8"
    )
    # Two declarations of one underscore-bearing name, so `search_symbols` has
    # more exact matches than the page cap and its (count, capped list) pair can
    # be graded like the four above (#699).
    for i in range(2):
        (root / "src" / f"shared_{i}.py").write_text(
            f"def shared_target():\n    return {i}\n", encoding="utf-8"
        )
    # Four modules a real entry point reaches, so get_dead_code_v2's signals
    # discriminate. ⚠ Without an entry point every signal fires on every
    # symbol, v2 refuses with `signal_warning`, and this whole case would
    # skip -- passing while proving nothing.
    for i in range(4):
        (root / "src" / f"live_{i}.py").write_text(
            f"def live_{i}():\n    return {i}\n", encoding="utf-8"
        )
    (root / "main.py").write_text(
        "".join(f"from src.live_{i} import live_{i}\n" for i in range(4))
        + "\n\ndef main():\n    return "
        + " + ".join(f"live_{i}()" for i in range(4))
        + "\n",
        encoding="utf-8",
    )

    (root / "tests").mkdir()
    (root / "tests" / "test_covered.py").write_text(
        "from src.covered import covered\n\n\ndef test_covered():\n"
        "    assert covered() == 1\n",
        encoding="utf-8",
    )

    storage = str(root / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def _untested(repo_id: str, storage: str, cap: int) -> dict:
    from jcodemunch_mcp.tools.get_untested_symbols import get_untested_symbols
    return get_untested_symbols(repo_id, max_results=cap, storage_path=storage)


def _importers(repo_id: str, storage: str, cap: int) -> dict:
    from jcodemunch_mcp.tools.find_importers import find_importers
    return find_importers(repo_id, "src/hub.py", max_results=cap, storage_path=storage)


def _references(repo_id: str, storage: str, cap: int) -> dict:
    from jcodemunch_mcp.tools.find_references import find_references
    return find_references(repo_id, identifier="hub_target",
                           max_results=cap, storage_path=storage)


def _dead(repo_id: str, storage: str, cap: int) -> dict:
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
    return get_dead_code_v2(repo_id, max_results=cap, storage_path=storage)


def _symbols(repo_id: str, storage: str, cap: int) -> dict:
    """`search_symbols`' `_meta.exact_match.exact` is a (count, capped list)
    pair like the four above, and this roster is a hand-kept literal that did
    not name it -- so the property ratchet existed and #699 shipped anyway.
    The query is underscore-bearing because the report is gated on
    `is_identifier_query`."""
    from jcodemunch_mcp.tools.search_symbols import search_symbols
    return search_symbols(repo_id, query="shared_target", max_results=cap,
                          storage_path=storage, detail_level="compact")


def _dig(payload: dict, dotted: str):
    """Read a possibly-nested key. `get_dead_code_v2` files its count under
    `_meta.total_matches`; the others publish theirs at the top level."""
    cur = payload
    for part in dotted.split("."):
        cur = cur[part]
    return cur


# (label, callable, count_key, list_key)
_CASES = [
    ("get_untested_symbols", _untested, "untested_count", "symbols"),
    ("find_importers", _importers, "importer_count", "importers"),
    ("find_references", _references, "reference_count", "references"),
    ("get_dead_code_v2", _dead, "_meta.total_matches", "dead_symbols"),
    ("search_symbols", _symbols, "_meta.exact_match.exact", "results"),
]
_IDS = [c[0] for c in _CASES]


@pytest.mark.parametrize("label,call,count_key,list_key", _CASES, ids=_IDS)
def test_the_fixture_can_actually_truncate(repo, label, call, count_key, list_key):
    """Non-vacuity: without this, every assertion below is trivially true."""
    repo_id, storage = repo
    full = call(repo_id, storage, _BIG)
    if "error" in full:
        pytest.skip(f"{label}: {full['error']}")
    found = _dig(full, count_key)
    assert found > _SMALL, (
        f"{label} produced {found} rows; the page cap of {_SMALL} cannot "
        f"truncate it, so this case proves nothing"
    )


@pytest.mark.parametrize("label,call,count_key,list_key", _CASES, ids=_IDS)
def test_count_is_a_property_of_the_repo_not_the_page(
    repo, label, call, count_key, list_key
):
    repo_id, storage = repo
    full = call(repo_id, storage, _BIG)
    paged = call(repo_id, storage, _SMALL)
    if "error" in full or "error" in paged:
        pytest.skip(f"{label} unavailable on this fixture")

    full_n, paged_n = _dig(full, count_key), _dig(paged, count_key)
    assert paged_n == full_n, (
        f"{label}.{count_key} moved from {full_n} to {paged_n} when only "
        f"max_results changed -- it is counting the page, not the repo"
    )


@pytest.mark.parametrize("label,call,count_key,list_key", _CASES, ids=_IDS)
def test_the_page_is_still_capped_and_says_so(repo, label, call, count_key, list_key):
    """The other half: a count that ignores the cap must not mean an uncapped list."""
    repo_id, storage = repo
    paged = call(repo_id, storage, _SMALL)
    if "error" in paged:
        pytest.skip(f"{label} unavailable on this fixture")

    assert len(paged[list_key]) <= _SMALL, f"{label} did not honour max_results"
    assert paged.get("_meta", {}).get("truncated") is True, (
        f"{label} truncated silently -- a short list that does not say so is "
        f"indistinguishable from a complete one"
    )
