"""A symbol id missing only its owner or its ~N suffix names the ids it meant (#869).

Sixteen sites wrote their own not-found error, so an id built from a
search row's `file`, `name` and `kind` -- which misses that a member's id
carries its owner, and that same-named symbols in one file take `~1`, `~2` --
got a bare not-found and no hint that one real id was a qualifier away. A
benchmark adapter (#726) lost 52 follow-up calls that way.

The rule lives in one place, `retrieval.verdict.symbol_not_found`, and every
site asks it. It NEVER resolves: two classes in one file can each own a `pick`,
and choosing one would answer a question about a different symbol, so the
candidates are named and the call still fails.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from jcodemunch_mcp.investigator.deletion_safety import investigate_deletion_safety
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.check_rename_safe import check_rename_safe
from jcodemunch_mcp.tools.find_implementations import find_implementations
from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.get_context_bundle import get_context_bundle
from jcodemunch_mcp.tools.get_endpoint_impact import get_endpoint_impact
from jcodemunch_mcp.tools.get_impact_preview import get_impact_preview
from jcodemunch_mcp.tools.get_related_symbols import get_related_symbols
from jcodemunch_mcp.tools.get_signal_chains import get_signal_chains
from jcodemunch_mcp.tools.get_symbol import get_symbol_source
from jcodemunch_mcp.tools.get_symbol_complexity import get_symbol_complexity
from jcodemunch_mcp.tools.get_symbol_provenance import get_symbol_provenance
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.plan_refactoring import plan_refactoring

SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"

SITES = {
    "get_symbol_source": lambda repo, sid, sp: get_symbol_source(repo=repo, symbol_id=sid, storage_path=sp),
    "get_call_hierarchy": lambda repo, sid, sp: get_call_hierarchy(repo=repo, symbol_id=sid, storage_path=sp),
    "get_impact_preview": lambda repo, sid, sp: get_impact_preview(repo=repo, symbol_id=sid, storage_path=sp),
    "get_blast_radius": lambda repo, sid, sp: get_blast_radius(repo=repo, symbol=sid, storage_path=sp),
    "check_delete_safe": lambda repo, sid, sp: check_delete_safe(repo=repo, symbol=sid, storage_path=sp),
    "check_edit_safe": lambda repo, sid, sp: check_edit_safe(repo=repo, symbol=sid, storage_path=sp),
    "find_implementations": lambda repo, sid, sp: find_implementations(repo=repo, symbol=sid, storage_path=sp),
    "get_related_symbols": lambda repo, sid, sp: get_related_symbols(repo=repo, symbol_id=sid, storage_path=sp),
    "get_signal_chains": lambda repo, sid, sp: get_signal_chains(repo=repo, symbol=sid, storage_path=sp),
    "get_symbol_provenance": lambda repo, sid, sp: get_symbol_provenance(repo=repo, symbol=sid, storage_path=sp),
    "plan_refactoring": lambda repo, sid, sp: plan_refactoring(
        repo=repo, symbol=sid, refactor_type="rename", new_name="renamed", storage_path=sp
    ),
    "investigate_deletion_safety": lambda repo, sid, sp: investigate_deletion_safety(
        repo=repo, symbol=sid, storage_path=sp
    ),
    # Review round 1: three more sites, in spellings the first ratchet missed.
    "get_context_bundle": lambda repo, sid, sp: get_context_bundle(repo=repo, symbol_id=sid, storage_path=sp),
    "check_rename_safe": lambda repo, sid, sp: check_rename_safe(
        repo=repo, symbol_id=sid, new_name="renamed", storage_path=sp
    ),
    "get_symbol_complexity": lambda repo, sid, sp: get_symbol_complexity(repo=repo, symbol_id=sid, storage_path=sp),
    # Review round 2: a sixteenth, worded `No symbol ... in index.`
    "get_endpoint_impact": lambda repo, sid, sp: get_endpoint_impact(
        repo=repo, handler_symbol_id=sid, storage_path=sp
    ),
}


@pytest.fixture()
def indexed(tmp_path):
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "types.ts").write_text(
        "export class ZodObject {\n  pick(k: string) { return k; }\n}\n"
        "export class ZodArray {\n  pick(k: string) { return k; }\n}\n"
        "export class Only {\n  omit(k: string) { return k; }\n}\n",
        encoding="utf-8",
    )
    (proj / "src" / "twins.py").write_text(
        "import os\n\n\ndef f():\n    return os.sep\n\n\ndef f():\n    return 2\n",
        encoding="utf-8",
    )
    storage = str(tmp_path / "idx")
    result = index_folder(str(proj), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


# (requested id, the candidates it must name, in index order)
NEAR_MISSES = [
    ("owner-missing", "src/types.ts::omit#method", ["src/types.ts::Only.omit#method"]),
    (
        "owner-missing-two-owners",
        "src/types.ts::pick#method",
        ["src/types.ts::ZodObject.pick#method", "src/types.ts::ZodArray.pick#method"],
    ),
    ("suffix-missing", "src/twins.py::f#function", ["src/twins.py::f#function~1", "src/twins.py::f#function~2"]),
    ("suffix-wrong", "src/twins.py::f#function~3", ["src/twins.py::f#function~1", "src/twins.py::f#function~2"]),
]


def _error_of(result: dict) -> dict:
    """The error payload a site returned: the batch form nests it in `errors`."""
    if "errors" in result and result.get("errors"):
        return result["errors"][0]
    return result


@pytest.mark.parametrize("site", sorted(SITES))
@pytest.mark.parametrize("requested,expected", [n[1:] for n in NEAR_MISSES], ids=[n[0] for n in NEAR_MISSES])
def test_a_near_miss_names_its_candidates_and_never_resolves(indexed, site, requested, expected):
    repo, storage = indexed
    err = _error_of(SITES[site](repo, requested, storage))

    assert "Symbol not found" in str(err.get("error", "")), (site, err)
    assert err.get("near_miss_ids") == expected, (site, err)
    assert err.get("near_miss_total") == len(expected), (site, err)
    assert err.get("near_miss_truncated") is False, (site, err)


@pytest.mark.parametrize("site", sorted(SITES))
def test_an_id_with_no_near_miss_names_none(indexed, site):
    repo, storage = indexed
    err = _error_of(SITES[site](repo, "src/types.ts::nothing#method", storage))

    assert "Symbol not found" in str(err.get("error", "")), (site, err)
    assert "near_miss_ids" not in err, (site, err)


def test_a_different_kind_or_file_is_not_a_near_miss(indexed):
    """Only the owner and the ~N suffix may differ: `pick#function` is a
    different question from `pick#method`, and another file's `f` is another
    symbol."""
    from jcodemunch_mcp.retrieval.verdict import symbol_id_candidates

    symbols = [
        {"id": "a.ts::A.pick#method", "name": "pick", "file": "a.ts", "kind": "method"},
        {"id": "b.ts::B.pick#method", "name": "pick", "file": "b.ts", "kind": "method"},
        {"id": "a.ts::pick#function", "name": "pick", "file": "a.ts", "kind": "function"},
        {"id": "a.ts::A.picker#method", "name": "picker", "file": "a.ts", "kind": "method"},
        {"id": "a.ts::Apick#method", "name": "Apick", "file": "a.ts", "kind": "method"},
    ]

    assert symbol_id_candidates("a.ts::pick#method", symbols) == (["a.ts::A.pick#method"], 1)


def test_the_candidate_list_is_bounded_and_says_so():
    from jcodemunch_mcp.retrieval.verdict import SYMBOL_CANDIDATES_CAP, symbol_not_found

    symbols = [
        {"id": f"a.ts::C{i}.m#method", "name": "m", "file": "a.ts", "kind": "method"}
        for i in range(SYMBOL_CANDIDATES_CAP + 3)
    ]
    err = symbol_not_found("a.ts::m#method", symbols)

    assert len(err["near_miss_ids"]) == SYMBOL_CANDIDATES_CAP
    assert err["near_miss_total"] == SYMBOL_CANDIDATES_CAP + 3
    assert err["near_miss_truncated"] is True


def test_every_not_found_error_comes_from_the_authority():
    """The ratchet over the property, not the reported sites: no error response
    outside the authority says, in any wording of the family above, that a
    symbol is absent. Read from the AST, so prose and docstrings do not count."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.parent.name == "retrieval" and path.name == "verdict.py":
            continue
        offenders += [f"{path.relative_to(SRC)}:{line}" for line in _not_found_literals(path.read_text(encoding="utf-8"))]

    assert not offenders, offenders


#: Any wording of "this symbol is not in the index", not the one spelling the
#: issue reported. Review round 1: `Symbol(s) not found: ...` and
#: `Symbol {id!r} not found in index.` sat in three more tools, and the first
#: ratchet matched the literal `Symbol not found`, so it could not see them.
_NOT_FOUND_RE = re.compile(
    r"(?i)(\bsymbols?(?:\(s\))?\b.{0,60}?\b(?:not found|not in (?:the )?index|does not exist|unknown|missing)\b"
    r"|\b(?:no|unknown|missing) symbol\b)"
)
# ⚠ Review round 2: a ratchet keyed to "not found" missed `No symbol {id!r} in
# index.` -- a second spelling-keyed guard after round 1's. The family above is
# every way an error can say the requested symbol is absent; `no symbol` is
# SINGULAR on purpose, because "No symbols in <repo>" is an empty index, not an
# answer to a symbol argument.


def _not_found_literals(source: str) -> list[int]:
    """Lines of an ERROR RESPONSE saying a symbol was not found: the value of
    an `"error"` key in a dict display, an f-string read whole (placeholders as
    `{}`). Every one of the fifteen sites had that shape; a finding's prose or
    a reason string that mentions a name not found in a FILE is not a tool's
    answer to a symbol argument, and a docstring is never a dict value."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "error"):
                continue
            if isinstance(value, ast.JoinedStr):
                text = "".join(p.value if isinstance(p, ast.Constant) else "{}" for p in value.values)
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                text = value.value
            else:
                continue
            if _NOT_FOUND_RE.search(text):
                lines.append(value.lineno)
    return sorted(lines)


@pytest.mark.parametrize("literal", [
    'f"Symbol not found: {x}"',
    'f"Symbol(s) not found: {x}"',
    'f"Symbol {x!r} not found in index."',
    '"symbols not found"',
    'f"No symbol {x!r} in index."',
    'f"Symbol {x} does not exist"',
    'f"unknown symbol {x}"',
])
def test_the_ratchet_sees_an_f_string_and_not_a_docstring(literal):
    source = (
        'def f(x):\n'
        '    """Returns Symbol not found when absent."""\n'
        f'    return {{"error": {literal}}}\n'
    )

    assert _not_found_literals(source) == [3]


def test_a_single_id_error_carries_the_near_misses_and_not_did_you_mean(indexed):
    """Single-mode `get_symbol_source` rebuilt its error from the message alone.
    It carries the authority's keys now; `did_you_mean` stays batch-only,
    because surfacing it here would be a new 1.x field with no disclosure."""
    repo, storage = indexed
    err = get_symbol_source(repo=repo, symbol_id="src/types.ts::omit#method", storage_path=storage)

    assert err["near_miss_ids"] == ["src/types.ts::Only.omit#method"]
    assert "did_you_mean" not in err
    assert "id" not in err


def test_several_missing_ids_are_named_together(indexed):
    repo, storage = indexed
    err = get_context_bundle(
        repo=repo, symbol_ids=["src/types.ts::omit#method", "src/twins.py::f#function"], storage_path=storage
    )

    assert err["error"].startswith("Symbol(s) not found: src/types.ts::omit#method, src/twins.py::f#function")
    assert err["near_miss_ids"] == [
        "src/types.ts::Only.omit#method", "src/twins.py::f#function~1", "src/twins.py::f#function~2",
    ]
    assert err["near_miss_total"] == 3


def test_an_ambiguous_name_keeps_its_candidates_shape(indexed):
    """`candidates` already meant the ambiguous-name list in four tools; the
    near-miss list has its own key so neither changes shape by branch."""
    repo, storage = indexed
    err = get_call_hierarchy(repo=repo, symbol_id="pick", storage_path=storage)

    assert all(isinstance(c, dict) and "id" in c for c in err["candidates"]), err
    assert "near_miss_ids" not in err


def test_an_empty_index_error_is_not_a_missing_symbol():
    """The plural is an empty index, not an answer to a symbol argument."""
    source = (
        'def f(r):\n'
        '    return {"error": f"No symbols in {r}. Nothing to measure."}\n'
    )

    assert _not_found_literals(source) == []


def test_overlapping_requests_count_each_near_miss_once():
    """Two missing ids can share near misses; the total is the union."""
    from jcodemunch_mcp.retrieval.verdict import symbol_not_found

    symbols = [
        {"id": "t.py::f#function~1", "name": "f", "file": "t.py", "kind": "function"},
        {"id": "t.py::f#function~2", "name": "f", "file": "t.py", "kind": "function"},
    ]
    err = symbol_not_found(["t.py::f#function", "t.py::f#function~3"], symbols)

    assert err["near_miss_ids"] == ["t.py::f#function~1", "t.py::f#function~2"]
    assert err["near_miss_total"] == 2
    assert err["near_miss_truncated"] is False
    assert " 2 indexed id(s)" in err["error"]
