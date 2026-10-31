"""A partial index stops reporting itself fresh (#375 sub-problem C).

The reported symptom: "it has learned about 7,659 of 9,634 code files. But it
still reports itself as up to date. So 'I never learned that file' and 'that
file doesn't exist' look identical to every agent." That ambiguity is why a
paying user moved their default code lookup to another tool.

The cause is a category error, not a broken check. Freshness answered "is the
index BEHIND the tree in time" (index SHA vs git HEAD) and was being read as
"does the index COVER the tree". A corpus that dropped files at index time sits
at the SAME SHA as the checkout, so it truthfully reported `fresh` while whole
files were missing.

Underneath it, `index_folder`'s second pass dropped files with three
uncounted `continue`s, so the corpus could end up smaller than the walk
reported with nothing anywhere recording the difference.
"""

from __future__ import annotations

import json
import pathlib

import jsonschema

from jcodemunch_mcp.tools.index_folder import _coverage_report
from jcodemunch_mcp.retrieval.verdict import (
    build_verdict,
    coverage_is_incomplete,
    index_coverage_meta,
)


# --- the accounting ------------------------------------------------------------

def test_a_reconciled_walk_is_complete():
    cov = _coverage_report({}, files_indexed=10, no_symbols_count=0, files_accepted=10)
    assert cov["complete"] is True
    assert "unaccounted" not in cov


def test_an_unexplained_gap_is_incomplete_and_counted():
    cov = _coverage_report({}, files_indexed=8, no_symbols_count=0, files_accepted=10)
    assert cov["complete"] is False
    assert cov["unaccounted"] == 2


def test_named_drops_are_still_incomplete():
    """A drop we can name is still a file the walk said belonged in the corpus."""
    cov = _coverage_report(
        {}, files_indexed=8, no_symbols_count=0, files_accepted=10,
        post_discovery_drops={"no_language": 2},
    )
    assert cov["complete"] is False
    assert cov["dropped_after_discovery"] == {"no_language": 2}
    # Fully explained, so nothing is left unaccounted.
    assert "unaccounted" not in cov


def test_a_legacy_index_reports_unknown_never_complete():
    """Absence of a measurement must not read as evidence of coverage."""
    cov = _coverage_report({}, files_indexed=8, no_symbols_count=0)
    assert cov["complete"] is None
    assert "files_accepted" not in cov


def test_a_partial_walk_over_a_wider_index_reports_unknown():
    """v1.96 subdir-merge / branch-delta walk a prefix while the index holds more.

    Reconciliation does not describe that shape, so it must report unknown
    rather than a false incomplete.
    """
    cov = _coverage_report({}, files_indexed=50, no_symbols_count=0, files_accepted=10)
    assert cov["complete"] is None
    assert cov["reconciliation"] == "partial_walk_over_wider_index"


def test_incompleteness_requires_proof_not_silence():
    assert coverage_is_incomplete({"complete": False}) is True
    assert coverage_is_incomplete({"complete": True}) is False
    assert coverage_is_incomplete({"complete": None}) is False
    assert coverage_is_incomplete({}) is False
    assert coverage_is_incomplete(None) is False


# --- the verdict gate ----------------------------------------------------------

INCOMPLETE = {"complete": False, "files_indexed": 8, "files_accepted": 10, "unaccounted": 2}
COMPLETE = {"complete": True, "files_indexed": 10, "files_accepted": 10}
UNKNOWN = {"complete": None, "files_indexed": 8}


def test_zero_results_over_a_partial_index_cannot_claim_absence():
    v = build_verdict(result_count=0, scanned_files=5, coverage=INCOMPLETE)["verdict"]
    assert v["state"] == "degraded"
    assert v["channels"]["index"] == "partial"


def test_zero_results_over_a_complete_index_still_prove_absence():
    """Non-vacuity: the degrade above comes from the gap, not from the gate."""
    v = build_verdict(result_count=0, scanned_files=5, coverage=COMPLETE)["verdict"]
    assert v["state"] == "absent"
    assert v["channels"]["index"] == "fresh"


def test_unknown_coverage_behaves_exactly_as_before():
    """Every pre-upgrade index must be byte-identical, or this ships a
    regression to everyone who has not re-indexed."""
    v = build_verdict(result_count=0, scanned_files=5, coverage=UNKNOWN)["verdict"]
    assert v["state"] == "absent"
    assert v["channels"]["index"] == "fresh"


def test_a_partial_index_is_disclosed_even_when_results_come_back():
    """DISCLOSE on every state, refuse only the absence claim.

    Hits are real hits; the caller still deserves to know the corpus has holes.
    """
    v = build_verdict(
        result_count=3, scanned_files=5, best_score=9.0, coverage=INCOMPLETE
    )["verdict"]
    assert v["state"] == "ok"
    assert v["channels"]["index"] == "partial"


def test_a_rebuild_in_flight_outranks_a_known_gap():
    """Ordering is by how badly each condition undermines the answer."""
    v = build_verdict(
        result_count=0, scanned_files=5, coverage=INCOMPLETE, index_changed=True
    )["verdict"]
    assert v["channels"]["index"] == "rebuilding"


def test_a_known_gap_outranks_mere_lag():
    v = build_verdict(
        result_count=0, scanned_files=5, coverage=INCOMPLETE, index_stale=True
    )["verdict"]
    assert v["channels"]["index"] == "partial"


def test_the_coverage_block_rides_along_so_the_gap_is_inspectable():
    v = build_verdict(result_count=0, scanned_files=5, coverage=INCOMPLETE)["verdict"]
    assert v["coverage"]["unaccounted"] == 2


# --- the absence-refusal rule falls out of the existing check -------------------

def test_a_partial_scan_is_refused_with_a_reason_that_names_the_cause():
    from jcodemunch_mcp.handoff import note_absence

    v = build_verdict(result_count=0, scanned_files=5, coverage=INCOMPLETE)["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle-partial", v)
    assert ref is None
    assert why and "never entered the corpus" in why


def test_a_complete_scan_still_mints_a_citable_ref():
    from jcodemunch_mcp.handoff import note_absence

    v = build_verdict(result_count=0, scanned_files=5, coverage=COMPLETE)["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle-complete", v)
    assert why is None
    assert ref and ref.startswith("absent:")


# --- our own published contract -------------------------------------------------

def _schema():
    p = pathlib.Path(__file__).parent.parent / "schemas" / "retrieval-verdict.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_the_partial_verdict_validates_against_our_published_schema():
    """`channels` is closed and enumerates `index`, so a new value that is not
    published makes every mid-gap response fail OUR OWN contract. That is the
    v1.108.168 trap; it fired again here on the coverage block."""
    v = build_verdict(result_count=0, scanned_files=5, coverage=INCOMPLETE)["verdict"]
    jsonschema.validate(v, _schema())


def test_the_projected_coverage_shape_validates(small_index):
    """index_coverage_meta is what actually reaches the wire, not the raw
    persisted report."""
    from jcodemunch_mcp.storage import IndexStore

    owner, name = small_index["repo"].split("/", 1)
    index = IndexStore(base_path=small_index["store"]).load_index(owner, name)
    projected = index_coverage_meta(index)
    assert projected is not None
    v = build_verdict(result_count=0, scanned_files=1, coverage=projected)["verdict"]
    jsonschema.validate(v, _schema())


# --- end to end, against a real indexing run ------------------------------------

def test_a_real_walk_of_a_clean_tree_reconciles(tmp_path):
    """No false positives: an ordinary repo must report complete."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "src"
    src.mkdir()
    for i in range(4):
        (src / f"mod{i}.py").write_text(f"def f{i}():\n    return {i}\n")
    store = tmp_path / "store"
    store.mkdir()
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True

    owner, name = r["repo"].split("/", 1)
    cov = IndexStore(base_path=str(store)).load_index(owner, name).coverage
    assert cov["files_accepted"] == cov["files_indexed"] == 4
    assert cov["complete"] is True

def test_a_file_lost_after_discovery_is_counted_and_flips_complete(tmp_path, monkeypatch):
    """The real wiring: a file the walk ACCEPTED that never reaches the index.

    Simulated at the narrowest possible point. `validate_path` is called at
    exactly one site — index_folder's second pass — so rejecting one file there
    reproduces the shape of the bug precisely: discovery says the file belongs
    in the corpus, and it silently does not arrive.
    """
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools import index_folder as ifmod

    src = tmp_path / "src"
    src.mkdir()
    for i in range(4):
        (src / f"mod{i}.py").write_text(f"def f{i}():" + chr(10) + f"    return {i}" + chr(10))
    store = tmp_path / "store"
    store.mkdir()

    _real = ifmod.validate_path
    monkeypatch.setattr(
        ifmod, "validate_path",
        lambda root, path, **kw: False if path.name == "mod2.py" else _real(root, path, **kw),
    )
    r = ifmod.index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True

    owner, name = r["repo"].split("/", 1)
    cov = IndexStore(base_path=str(store)).load_index(owner, name).coverage
    assert cov["files_accepted"] == 4
    assert cov["files_indexed"] == 3
    assert cov["dropped_after_discovery"] == {"outside_root": 1}
    assert cov["complete"] is False
    # The drop is fully explained, so nothing is left unaccounted.
    assert "unaccounted" not in cov


def test_that_partial_index_then_refuses_to_prove_absence(tmp_path, monkeypatch):
    """The whole point, end to end: the lost file makes zero-results honest."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools import index_folder as ifmod

    src = tmp_path / "src"
    src.mkdir()
    for i in range(4):
        (src / f"mod{i}.py").write_text(f"def f{i}():" + chr(10) + f"    return {i}" + chr(10))
    store = tmp_path / "store"
    store.mkdir()

    _real = ifmod.validate_path
    monkeypatch.setattr(
        ifmod, "validate_path",
        lambda root, path, **kw: False if path.name == "mod2.py" else _real(root, path, **kw),
    )
    r = ifmod.index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    owner, name = r["repo"].split("/", 1)
    index = IndexStore(base_path=str(store)).load_index(owner, name)

    v = build_verdict(
        result_count=0, scanned_files=3, coverage=index_coverage_meta(index)
    )["verdict"]
    assert v["state"] == "degraded"
    assert v["channels"]["index"] == "partial"
    jsonschema.validate(v, _schema())
