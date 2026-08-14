"""Exact immutable evidence receipts (v1.108.183, #377 Phase 2 P1+P2).

The one criterion that defines this phase: **a served symbol must stop attesting
an unrelated file-level claim.** Phase 1 shipped with that caveat stated
publicly — ``handoff._validate_evidence`` attests a ref matching a served symbol
id OR the file component of one, so citing a whole file was attested when one
unrelated symbol from it was the only thing retrieved, and a reader of the
finalized handoff could not tell the two citations apart.

``TestTheAcceptanceCriterion`` closes it and proves the test is not vacuous: the
same finalization attests the file-level ref when the narrowing is disabled.

Everything else here is support for that: the envelope and its identity, the
snapshot binding, the ``munch://evidence/<id>`` resource, the opt-in ``receipt``
argument, and producer registration — the gate that stops an unreviewed exit
minting into the contract.

The end-to-end tests run the REAL route (real producer, real verdict, real
dispatcher chokepoint, real encoding, real ``finalize_handoff``, real resource
read). Hand-built verdict dicts are used only where the point IS the unit under
test, never to stand in for the route.

Helper names in this module are all prefixed ``_r183``: a plain ``_state``
helper in tests/test_v1_108_181.py outranked ``token_tracker._State`` for that
golden replay query and cost a red CI run.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import jsonschema
import pytest

from jcodemunch_mcp import handoff as _handoff_mod
from jcodemunch_mcp.evidence import producers as _r183_producers
from jcodemunch_mcp.evidence import receipts as _r183_receipts

_R183_ROOT = Path(__file__).resolve().parents[1]


def _r183_schema() -> dict:
    return json.loads(
        (_R183_ROOT / "schemas" / "evidence-receipt.schema.json").read_text(encoding="utf-8")
    )


def _r183_payload(res):
    """The response body from either MCP return shape, plus whether it errored."""
    if hasattr(res, "content"):
        return json.loads(res.content[0].text), True
    return json.loads(res[0].text), False


def _r183_raw(res) -> str:
    return res.content[0].text if hasattr(res, "content") else res[0].text


def _r183_call(server, tool, args):
    return _r183_payload(asyncio.run(server.call_tool(tool, args)))


def _r183_absence_ref(body):
    """The Phase 3 token from a response, under either meta_fields setting.

    jcodemunch ships ``meta_fields: []`` by DEFAULT, so on a default install the
    verdict is deleted before the agent sees it and the re-attached carrier is
    the only thing left holding the token (the v1.108.177 disclosure). A test
    that only reads ``_meta.verdict`` passes on a machine with a permissive
    config and proves nothing about the shipped path.
    """
    meta = body.get("_meta") or {}
    verdict = meta.get("verdict") or {}
    return verdict.get("evidence_ref") or (meta.get("absence_evidence") or {}).get("ref")


def _r183_receipt_ids(body):
    return ((body.get("_meta") or {}).get("receipts") or {}).get("ids") or []


@pytest.fixture()
def _r183_env(tmp_path, monkeypatch):
    """An indexed two-symbol repo plus the live server, on a private index store.

    The file deliberately holds a target symbol and an UNRELATED one, because
    "unrelated" is the whole word in the acceptance criterion.
    """
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "idx"))
    proj = tmp_path / "proj"
    (proj / "src" / "auth").mkdir(parents=True)
    (proj / "src" / "auth" / "session.py").write_text(
        "import os\n\n\ndef refresh_token(user):\n    return user\n\n\n"
        "def unrelated_helper(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    (proj / "src" / "auth" / "other.py").write_text(
        "def untouched():\n    return 0\n", encoding="utf-8"
    )
    from jcodemunch_mcp import server

    _r183_receipts.clear_receipts()
    _handoff_mod.clear_absences()
    _handoff_mod.clear_handoffs()
    indexed, _ = _r183_call(server, "index_folder", {"path": str(proj)})
    repo = indexed["repo"]
    yield server, repo, proj
    _r183_receipts.clear_receipts()
    _handoff_mod.clear_absences()
    _handoff_mod.clear_handoffs()


_R183_TARGET = "src/auth/session.py::refresh_token#function"
_R183_FILE = "src/auth/session.py"


def _r183_serve_and_receipt(server, repo):
    """Real route: search (populates the session record) then receipt the symbol."""
    _r183_call(server, "search_symbols", {"repo": repo, "query": "refresh_token", "format": "json"})
    body, _ = _r183_call(
        server,
        "get_symbol_source",
        {"repo": repo, "symbol_id": _R183_TARGET, "receipt": True, "format": "json"},
    )
    ids = ((body.get("_meta") or {}).get("receipts") or {}).get("ids") or []
    assert ids, "the registered producer minted no receipt"
    return ids[0], body


def _r183_finalize(server, repo, refs, task="t"):
    return _r183_call(
        server,
        "finalize_handoff",
        {
            "repo": repo,
            "task": task,
            "sections": [{"heading": "Findings", "content": "prose"}],
            "evidence_refs": refs,
            "format": "json",
        },
    )


# --- the acceptance criterion ------------------------------------------------


class TestTheAcceptanceCriterion:
    """A served symbol does not attest an unrelated file-level claim."""

    def test_a_receipt_citing_handoff_refuses_the_file_level_ref(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        body, is_error = _r183_finalize(
            server, repo, [f"munch://evidence/{eid}", _R183_FILE]
        )
        assert is_error is True
        assert "too broad" in body["error"]
        entry = body["over_broad"][0]
        assert entry["ref"] == _R183_FILE
        # The message names WHAT backed it, so the caller can see the gap rather
        # than guess at it.
        assert entry["backed_by"] == [_R183_TARGET]

    def test_the_same_finalization_attests_it_with_the_narrowing_disabled(
        self, _r183_env, monkeypatch
    ):
        """Non-vacuity. This is the pre-Phase-2 behavior, and it passed."""
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        monkeypatch.setattr(_handoff_mod, "_receipts_cited", lambda refs: False)
        body, is_error = _r183_finalize(
            server, repo, [f"munch://evidence/{eid}", _R183_FILE], task="t-off"
        )
        assert is_error is False
        assert body["evidence_attested"] is True
        assert body["evidence_count"] == 2

    def test_the_receipt_attests_its_own_subject_exactly(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        body, is_error = _r183_finalize(server, repo, [f"munch://evidence/{eid}"])
        assert is_error is False
        assert body["receipts_attested"] == 1
        assert body["evidence_precision"] == {"exact": 1, "symbol_exact": 0, "broadened": 0}

    def test_the_bare_id_is_accepted_as_well_as_the_uri(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        body, is_error = _r183_finalize(server, repo, [eid])
        assert is_error is False
        assert body["receipts_attested"] == 1


class TestBroadeningIsNamedNotHidden:
    """Without a receipt the historical broadening stands — and is disclosed."""

    def test_a_file_level_ref_alone_still_attests(self, _r183_env):
        server, repo, _proj = _r183_env
        _r183_call(
            server, "search_symbols", {"repo": repo, "query": "refresh_token", "format": "json"}
        )
        body, is_error = _r183_finalize(server, repo, [_R183_FILE])
        assert is_error is False
        assert body["evidence_attested"] is True

    def test_the_finalization_receipt_names_the_broadened_ref(self, _r183_env):
        server, repo, _proj = _r183_env
        _r183_call(
            server, "search_symbols", {"repo": repo, "query": "refresh_token", "format": "json"}
        )
        body, _ = _r183_finalize(server, repo, [_R183_FILE])
        assert body["evidence_precision"]["broadened"] == 1
        named = body["broadened_refs"][0]
        assert named["ref"] == _R183_FILE
        assert named["backed_by"] == [_R183_TARGET]

    def test_an_exact_symbol_id_ref_is_neither_broadened_nor_a_receipt(self, _r183_env):
        server, repo, _proj = _r183_env
        _r183_call(
            server, "search_symbols", {"repo": repo, "query": "refresh_token", "format": "json"}
        )
        body, is_error = _r183_finalize(server, repo, [_R183_TARGET])
        assert is_error is False
        # Omitted entirely, so a v1/v2 receipt from before Phase 2 is unchanged.
        assert "evidence_precision" not in body
        assert "broadened_refs" not in body
        assert "receipts_attested" not in body


# --- the envelope and its identity -------------------------------------------


class TestExactSubject:
    def test_the_subject_carries_the_range_and_the_content_hash(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, served = _r183_serve_and_receipt(server, repo)
        envelope, why = _r183_receipts.lookup(eid)
        assert why is None
        subject = envelope["subject"]
        assert subject["symbol_id"] == _R183_TARGET
        assert subject["file"] == _R183_FILE
        assert subject["start_line"] == served["line"]
        assert subject["end_line"] == served["end_line"]
        assert subject["content_sha256"] == served["content_hash"]

    def test_the_served_bytes_are_hashed_and_compared(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        integrity = _r183_receipts.lookup(eid)[0]["integrity"]
        assert integrity["hash_source"] == "index_content_hash"
        assert integrity["content_hash_matches_served_bytes"] is True

    def test_identity_is_the_resolved_repo_not_the_argument_string(self, _r183_env):
        """`.`, an alias, a path and a worktree can all name one repo."""
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        assert _r183_receipts.lookup(eid)[0]["subject"]["repo_identity"] == repo

    def test_a_row_that_carried_no_range_says_so_instead_of_inventing_one(self, _r183_env):
        """search_symbols at standard detail serves no end_line and no hash."""
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "refresh_token", "receipt": True, "format": "json"},
        )
        envelope = _r183_receipts.lookup(body["_meta"]["receipts"]["ids"][0])[0]
        assert "end_line" not in envelope["subject"]
        assert "content_sha256" not in envelope["subject"]
        joined = " ".join(envelope["limitations"])
        assert "end_line was not served" in joined
        assert "no content hash" in joined
        assert envelope["capabilities"]["proves_body_bytes"] is False

    def test_full_detail_serves_the_body_so_the_receipt_can_hash_it(self, _r183_env):
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {
                "repo": repo,
                "query": "refresh_token",
                "detail_level": "full",
                "receipt": True,
                "format": "json",
            },
        )
        envelope = _r183_receipts.lookup(body["_meta"]["receipts"]["ids"][0])[0]
        assert envelope["subject"]["end_line"] > 0
        assert envelope["integrity"]["hash_source"] == "served_bytes"
        assert envelope["capabilities"]["proves_body_bytes"] is True


class TestIdentityAndDeterminism:
    def test_repeated_resource_reads_are_byte_identical(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        uri = f"munch://evidence/{eid}"
        first = asyncio.run(server.read_resource(uri))[0].content
        second = asyncio.run(server.read_resource(uri))[0].content
        assert first == second
        assert json.loads(first)["evidence_id"] == eid

    def test_the_id_is_the_full_digest(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", eid)

    def test_a_different_snapshot_produces_a_different_id(self):
        subject = {"repo_identity": "local/x", "symbol_id": "a.py::f#function"}
        search = {"tool": "get_symbol_source", "scope": {}, "mode": {}}
        base = {"index_generation": "g1", "freshness": "fresh", "scorer_version": 1}
        moved = dict(base, index_generation="g2")
        assert _r183_receipts.evidence_id(
            subject, search, base
        ) != _r183_receipts.evidence_id(subject, search, moved)

    def test_a_different_freshness_alone_produces_a_different_id(self):
        subject = {"repo_identity": "local/x", "symbol_id": "a.py::f#function"}
        search = {"tool": "get_symbol_source", "scope": {}, "mode": {}}
        fresh = {"index_generation": "g1", "freshness": "fresh", "scorer_version": 1}
        stale = dict(fresh, freshness="stale")
        assert _r183_receipts.evidence_id(
            subject, search, fresh
        ) != _r183_receipts.evidence_id(subject, search, stale)

    def test_a_different_effective_operation_produces_a_different_id(self):
        """The item-12 lesson: a literal and a regex search are different operations."""
        subject = {"repo_identity": "local/x", "query": "needle"}
        snap = {"index_generation": "g1", "freshness": "fresh", "scorer_version": 1}
        literal = {"tool": "search_text", "scope": {}, "mode": {}}
        regex = {"tool": "search_text", "scope": {}, "mode": {"is_regex": True}}
        assert _r183_receipts.evidence_id(
            subject, literal, snap
        ) != _r183_receipts.evidence_id(subject, regex, snap)

    def test_the_same_scan_against_the_same_snapshot_is_the_same_id(self):
        args = (
            {"repo_identity": "local/x", "symbol_id": "a.py::f#function"},
            {"tool": "get_symbol_source", "scope": {}, "mode": {}},
            {"index_generation": "g1", "freshness": "fresh", "scorer_version": 1},
        )
        assert _r183_receipts.evidence_id(*args) == _r183_receipts.evidence_id(*args)


class TestFailClosed:
    def test_an_id_collision_on_differing_payloads_refuses_both(self):
        _r183_receipts.clear_receipts()
        envelope = _r183_receipts.build_envelope(
            proof_kind="symbol_definition",
            subject={"repo_identity": "local/x", "symbol_id": "a.py::f#function"},
            effective_search={"tool": "get_symbol_source", "scope": {}, "mode": {}},
            snapshot={"index_generation": "g", "freshness": "fresh", "scorer_version": 1},
        )
        assert _r183_receipts.record_receipt(envelope) == envelope["evidence_id"]
        contested = dict(envelope, limitations=["a different receipt under one id"])
        assert _r183_receipts.record_receipt(contested) is None
        # Neither survives: keeping one would make every later citation a coin flip.
        found, why = _r183_receipts.lookup(envelope["evidence_id"])
        assert found is None
        assert why == "collision"
        _r183_receipts.clear_receipts()

    def test_every_limitation_is_derivable_from_the_identity_payload(self):
        """Otherwise fail-closed eats a valid receipt.

        Limitations are not in the evidence id. If a condition could change them
        without changing the id, a cached replay whose verdict gained
        `revalidated` would produce one id with two different bodies, the
        collision check would fire, and it would delete a receipt an agent was
        already holding. Every condition token therefore lives IN the snapshot.
        """
        assert set(_r183_producers._CONDITION_LIMITS) == {
            "not_tracked",
            "freshness_unknown",
            "stale",
            "rebuilding",
            "partial",
            "no_coverage_contract",
            "semantic_unavailable",
            "replayed_from_stale_cache",
            "moved_during_scan",
            "incomplete_inputs",
            "omitted_matches",
        }

    def test_a_replayed_verdict_changes_the_id_rather_than_colliding(self):
        """The scenario the condition tokens exist for, end to end at unit level."""
        verdict = {
            "state": "ok",
            "scanned": {"symbols": 1, "files": 1},
            "channels": {"lexical": "ok", "semantic": "off", "index": "fresh"},
            "note": "n",
        }
        replayed = dict(verdict, revalidated={"stale_cache": True, "reason": "moved"})
        coverage = {"files_indexed": 1, "complete": True}
        clean = _r183_producers._conditions(verdict, "fresh", coverage)
        stale_cache = _r183_producers._conditions(replayed, "fresh", coverage)
        assert clean == []
        assert stale_cache == ["replayed_from_stale_cache"]
        subject = {"repo_identity": "local/x", "symbol_id": "a.py::f#function"}
        search = {"tool": "search_symbols", "scope": {}, "mode": {}}
        base = {"index_generation": "g", "freshness": "fresh", "scorer_version": 1}
        assert _r183_receipts.evidence_id(
            subject, search, dict(base, conditions=clean)
        ) != _r183_receipts.evidence_id(subject, search, dict(base, conditions=stale_cache))

    def test_re_recording_identical_content_is_idempotent(self):
        _r183_receipts.clear_receipts()
        envelope = _r183_receipts.build_envelope(
            proof_kind="symbol_definition",
            subject={"repo_identity": "local/y", "symbol_id": "b.py::g#function"},
            effective_search={"tool": "get_symbol_source", "scope": {}, "mode": {}},
            snapshot={"index_generation": "g", "freshness": "fresh", "scorer_version": 1},
        )
        first = _r183_receipts.record_receipt(envelope)
        assert _r183_receipts.record_receipt(dict(envelope)) == first
        assert _r183_receipts.lookup(first)[0] is not None
        _r183_receipts.clear_receipts()

    def test_an_unknown_receipt_is_never_recorded_not_merely_missing(self):
        _r183_receipts.clear_receipts()
        found, why = _r183_receipts.lookup(f"sha256:{'0' * 64}")
        assert found is None
        assert why == "never_recorded"

    def test_an_evicted_receipt_says_evicted(self, monkeypatch):
        """A bounded store must not report aged-out as never-happened."""
        _r183_receipts.clear_receipts()
        monkeypatch.setattr(_r183_receipts, "_MAXSIZE", 2)
        ids = []
        for n in range(4):
            envelope = _r183_receipts.build_envelope(
                proof_kind="symbol_definition",
                subject={"repo_identity": "local/z", "symbol_id": f"c.py::s{n}#function"},
                effective_search={"tool": "get_symbol_source", "scope": {}, "mode": {}},
                snapshot={"index_generation": "g", "freshness": "fresh", "scorer_version": 1},
            )
            ids.append(_r183_receipts.record_receipt(envelope))
        assert _r183_receipts.lookup(ids[0])[1] == "evicted"
        assert _r183_receipts.lookup(ids[-1])[1] is None
        _r183_receipts.clear_receipts()

    def test_a_citation_of_an_unknown_receipt_fails_closed_at_finalization(self, _r183_env):
        server, repo, _proj = _r183_env
        _r183_call(
            server, "search_symbols", {"repo": repo, "query": "refresh_token", "format": "json"}
        )
        body, is_error = _r183_finalize(server, repo, [f"sha256:{'1' * 64}"])
        assert is_error is True
        assert "never_recorded" in " ".join(body["unknown_refs"])

    def test_an_unknown_receipt_resource_read_names_the_reason(self, _r183_env):
        server, _repo, _proj = _r183_env
        with pytest.raises(ValueError, match="never_recorded"):
            asyncio.run(server.read_resource(f"munch://evidence/sha256:{'2' * 64}"))

    def test_an_undeclarable_proof_kind_builds_nothing(self):
        assert (
            _r183_receipts.build_envelope(
                proof_kind="vibes",
                subject={"repo_identity": "local/x"},
                effective_search={"tool": "search_symbols"},
                snapshot={},
            )
            is None
        )


# --- the snapshot binding ----------------------------------------------------


class TestSnapshotBinding:
    def test_a_plain_folder_binds_not_tracked_rather_than_fresh(self, _r183_env):
        """A subject with no revision at all must read ``not_tracked`` everywhere.

        ⚠ RE-GROUNDED in 1.108.240, not weakened. This test used to assert
        ``channels.index == "fresh"`` as a KNOWN-WRONG baseline: its original
        docstring recorded that ``build_symbol_verdict`` never got the #377
        item-4 four-state treatment, so its channel answered ``fresh`` for a
        subject that has no revision, while the receipt's snapshot correctly
        said ``not_tracked``. The test's real subject was that divergence — the
        receipt must not repeat a signal answering a different question.

        1.108.240 fixed the channel, so the divergence is gone by removing its
        cause. Asserting ``fresh`` here would now re-pin the defect.

        What still bites, and is why this is re-grounded rather than deleted:
        the snapshot reading and the limitation are produced independently of
        the channel, so they must remain correct on their own. If a later
        change makes the receipt merely ECHO ``channels.index``, these two
        assertions are what keep that honest.
        """
        server, repo, _proj = _r183_env
        eid, _body = _r183_serve_and_receipt(server, repo)
        envelope = _r183_receipts.lookup(eid)[0]
        assert envelope["channels"]["index"] == "not_tracked"
        assert envelope["snapshot"]["freshness"] == "not_tracked"
        assert any("no revision control" in x for x in envelope["limitations"])

    def test_an_unmeasured_working_tree_is_unknown_not_clean(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        assert _r183_receipts.lookup(eid)[0]["snapshot"]["working_tree"] == "unknown"

    def test_an_empty_revision_binds_as_null_not_as_a_revision(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        snapshot = _r183_receipts.lookup(eid)[0]["snapshot"]
        assert snapshot["indexed_revision"] is None
        assert snapshot["live_revision"] is None

    def test_the_scorer_is_pinned(self, _r183_env):
        from jcodemunch_mcp.retrieval.verdict import SCORER_VERSION

        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        assert _r183_receipts.lookup(eid)[0]["snapshot"]["scorer_version"] == SCORER_VERSION

    def test_a_real_checkout_binds_fresh_with_both_revisions(self, tmp_path, monkeypatch):
        """`fresh` is the one state a fixture cannot fake (the v1.108.180 lesson).

        On a plain folder every receipt reports `not_tracked`, so a suite made only
        of folder fixtures would never exercise the path where both revisions are
        read and compared, which is the whole point of the snapshot binding.
        """
        import subprocess

        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "idx"))
        proj = tmp_path / "gitproj"
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "a.py").write_text(
            "def refresh_token(user):\n    return user\n", encoding="utf-8"
        )
        for cmd in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@example.invalid"],
            ["git", "config", "user.name", "t"],
            ["git", "add", "-A"],
            ["git", "commit", "-qm", "init"],
        ):
            if subprocess.run(cmd, cwd=proj, capture_output=True).returncode != 0:
                pytest.skip("git is unavailable in this environment")
        from jcodemunch_mcp import server

        _r183_receipts.clear_receipts()
        try:
            indexed, _ = _r183_call(server, "index_folder", {"path": str(proj)})
            repo = indexed["repo"]
            body, _ = _r183_call(
                server,
                "get_symbol_source",
                {
                    "repo": repo,
                    "symbol_id": "src/a.py::refresh_token#function",
                    "receipt": True,
                    "format": "json",
                },
            )
            snapshot = _r183_receipts.lookup(_r183_receipt_ids(body)[0])[0]["snapshot"]
            assert snapshot["freshness"] == "fresh"
            assert snapshot["indexed_revision"]
            assert snapshot["indexed_revision"] == snapshot["live_revision"]
            assert snapshot["conditions"] == []

            # A zero-result scan measures the tree, so this one is not `unknown`.
            absent, _ = _r183_call(
                server,
                "search_symbols",
                {
                    "repo": repo,
                    "query": "zzz_absent_thing",
                    "receipt": True,
                    "format": "json",
                },
            )
            envelope = _r183_receipts.lookup(_r183_receipt_ids(absent)[0])[0]
            assert envelope["snapshot"]["working_tree"] == "clean"
            assert envelope["capabilities"]["proves_absence"] is True
            assert envelope["absence_ref"].startswith("absent:")
        finally:
            _r183_receipts.clear_receipts()
            _handoff_mod.clear_absences()

    def test_coverage_is_fingerprinted_so_two_corpora_cannot_share_an_id(self):
        a = _r183_receipts.coverage_fingerprint({"files_indexed": 10, "complete": True})
        b = _r183_receipts.coverage_fingerprint({"files_indexed": 11, "complete": True})
        assert a and b and a != b
        assert _r183_receipts.coverage_fingerprint(None) is None


# --- producer registration (P2) ---------------------------------------------


class TestProducerRegistration:
    def test_an_unregistered_tool_mints_nothing(self, _r183_env):
        server, repo, _proj = _r183_env
        body, is_error = _r183_call(
            server,
            "get_file_outline",
            {"repo": repo, "file_path": _R183_FILE, "receipt": True, "format": "json"},
        )
        assert is_error is False
        assert "receipts" not in (body.get("_meta") or {})

    def test_receipt_on_an_unregistered_tool_is_disclosed_as_ignored(self, _r183_env):
        """A producer with no registration produces no receipts, and the caller is
        told the argument did nothing rather than assuming it worked."""
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "get_file_outline",
            {"repo": repo, "file_path": _R183_FILE, "receipt": True, "format": "json"},
        )
        assert body["_meta"]["ignored_arguments"] == ["receipt"]

    def test_every_registered_producer_declares_the_receipt_argument(self):
        """Otherwise the argument contract would call a correct call a mistake."""
        from jcodemunch_mcp import server

        for tool in _r183_producers.PRODUCERS:
            declared = server._declared_arg_keys(tool)
            assert declared is not None, tool
            assert "receipt" in declared, tool

    def test_receipt_is_hidden_under_compact_schemas_but_still_honored(self):
        """The core_compact ceiling is 4000 tokens and the surface sits just under
        it, so a param on four core tools does not fit there at any description
        length. ⚠ The headroom figure is deliberately not written here — see
        `tests/test_schema_budget.py`, which measures it. It is
        stripped from the published schema — and the argument contract must still
        know the tool accepts it, or a well-formed opted-in call would be called a
        caller mistake and lose its absence evidence (the suppress_meta bug from
        v1.108.177, one layer down)."""
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.server import _build_tools_list, _declared_arg_keys

        cfg = config_module._GLOBAL_CONFIG
        previous = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas")}
        try:
            cfg["tool_profile"] = "core"
            cfg["compact_schemas"] = True
            published = {t.name: t.inputSchema for t in _build_tools_list()}
            for tool in _r183_producers.PRODUCERS:
                if tool in published:
                    assert "receipt" not in (published[tool].get("properties") or {}), tool
                # Snapshotted before the strip: hidden is not the same as unaccepted.
                assert "receipt" in _declared_arg_keys(tool), tool
        finally:
            for key, value in previous.items():
                if value is None:
                    cfg.pop(key, None)
                else:
                    cfg[key] = value
            _build_tools_list()

    # ⚠⚠ `test_the_core_compact_schema_budget_is_unchanged` was REMOVED on
    # 2026-08-14, and the reason is worth more than the test was. It read three
    # numbers out of benchmarks/schema_baseline.json and asserted they equalled
    # three copies of themselves written here. That pins
    # the ARTIFACT, not the surface: the baseline was captured in 2026-07 and
    # the tool surface drifted underneath it for seven months while the
    # assertion stayed green. It failed for the first time when someone re-ran
    # the capture — so it fired on the ONE event that proves nothing regressed,
    # and was silent through every event it existed for.
    #
    # The budget is guarded where it is measured: `tests/test_schema_budget.py`
    # holds the 5% drift ceiling against a live `_build_tools_list()` and the
    # §10 <=4000 hard ceiling recomputed from the live build, which is the check
    # that catches a breach BEFORE the baseline is regenerated. The intent this
    # test carried — a param on four core tools must not spend the core budget —
    # is asserted structurally by its sibling above, which fails if `receipt`
    # ever reaches the published core+compact schema.
    #
    # `tests/test_schema_baseline_transcription.py` fails if any literal from
    # the baseline file returns to this tree.

    def test_every_registered_proof_kind_is_a_known_proof_kind(self):
        for producer in _r183_producers.PRODUCERS.values():
            assert producer.proof_kinds
            assert producer.proof_kinds <= _r183_receipts.PROOF_KINDS, producer.tool

    def test_the_declared_snapshot_fields_match_what_is_bound(self, _r183_env):
        """Registration is the contract; a field it names must actually be there."""
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        snapshot = _r183_receipts.lookup(eid)[0]["snapshot"]
        declared = _r183_producers.PRODUCERS["get_symbol_source"].snapshot_fields
        assert set(declared) == set(snapshot)

    def test_every_producer_declares_its_semantics(self):
        """Registration is the contract, so an empty field is a missing review."""
        for producer in _r183_producers.PRODUCERS.values():
            for attribute in ("completeness", "freshness", "coverage", "integrity"):
                assert getattr(producer, attribute).strip(), (producer.tool, attribute)

    def test_a_result_without_the_registered_verdict_shape_cannot_mint(self):
        """The early-return gate, stated as a unit.

        An exit that asserts an answer without building the verdict its producer
        registered has not been reviewed for evidence capability. That is the
        v1.108.179 class, made structural.
        """
        legacy_shaped = {
            "result_count": 0,
            "results": [],
            "negative_evidence": {"verdict": "no_implementation_found"},
            "_meta": {},
        }
        assert (
            _r183_producers.mint("search_symbols", {"query": "x"}, legacy_shaped, "local/x")
            is None
        )

    def test_the_symbol_shaped_verdict_cannot_mint_for_a_retrieval_producer(self):
        symbol_shaped = {
            "results": [{"id": "a.py::f#function", "file": "a.py", "line": 1}],
            "_meta": {"verdict": {"state": "ok", "channels": {"index": "fresh"}, "note": "n"}},
        }
        assert (
            _r183_producers.mint("search_symbols", {"query": "x"}, symbol_shaped, "local/x")
            is None
        )

    def test_the_semantic_exit_declares_its_own_completeness(self):
        """This test used to assert the semantic exit had no verdict, so it could
        not mint. v1.108.184 gave it one — and the test failed, which is the gate
        doing its job: gaining a verdict made the exit mintable under a
        `completeness` reading "lexical BM25 over the inverted index", false for an
        embedding ranking. The mode was reviewed and now declares its own.
        """
        producer = _r183_producers.PRODUCERS["search_symbols"]
        for mode in ("hybrid", "semantic_only"):
            declared = producer.completeness_for(mode)
            assert declared != producer.completeness, mode
            assert "cosine" in declared, mode
        assert "BM25 over the inverted index" in producer.completeness_for(None)

    def test_both_fusion_exits_declare_their_own_completeness(self):
        """These two tests used to assert the fusion exits had no verdict, so they
        could not mint. v1.108.185 gave both one — and both failed, the same way the
        semantic pin failed at .184. The modes were reviewed and now declare their
        own completeness. That is twice the gate has caught its own author.

        The two reviews reached OPPOSITE conclusions, which is why modes get
        reviewed rather than having a rule ported onto them: a semantic zero result
        cannot prove absence, a fusion zero result can.
        """
        for tool in ("search_symbols", "get_ranked_context"):
            producer = _r183_producers.PRODUCERS[tool]
            declared = producer.completeness_for("fusion")
            assert declared != producer.completeness, tool
            assert "Weighted Reciprocal Rank" in declared, tool
            assert "corpus fact" in declared, tool

    def test_an_errored_result_mints_nothing(self):
        assert (
            _r183_producers.mint("search_symbols", {"query": "x"}, {"error": "nope"}, "local/x")
            is None
        )


class TestCanonicalProjector:
    def test_the_projector_records_the_operation_not_the_raw_arguments(self, _r183_env):
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {
                "repo": repo,
                "query": "refresh_token",
                "kind": "function",
                "receipt": True,
                "format": "json",
            },
        )
        effective = _r183_receipts.lookup(body["_meta"]["receipts"]["ids"][0])[0][
            "effective_search"
        ]
        assert effective["tool"] == "search_symbols"
        assert effective["query"] == "refresh_token"
        assert effective["scope"] == {"kind": "function"}

    def test_an_empty_valued_narrowing_arg_does_not_change_identity(self):
        """An omitted narrowing arg and an empty one are the same operation.

        Unit-level on purpose: the MCP layer validates arguments against the
        published inputSchema, so ``kind: None`` never reaches a tool at all —
        it is rejected as a type error before dispatch. The projector still has
        to drop empty values, because the dispatcher hands it whatever the
        caller sent and ``file_pattern: ""`` does get through.
        """
        producer = _r183_producers.PRODUCERS["search_symbols"]
        result = {"results": [], "_meta": {}}
        bare = _r183_producers._project(producer, {"query": "q"}, result)
        empty = _r183_producers._project(
            producer, {"query": "q", "file_pattern": "", "decorator": None}, result
        )
        assert bare == empty
        assert empty["scope"] == {}

    def test_a_narrowing_arg_does_change_identity(self, _r183_env):
        server, repo, _proj = _r183_env
        wide, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "refresh_token", "receipt": True, "format": "json"},
        )
        narrow, _ = _r183_call(
            server,
            "search_symbols",
            {
                "repo": repo,
                "query": "refresh_token",
                "file_pattern": "src/auth/*.py",
                "receipt": True,
                "format": "json",
            },
        )
        assert wide["_meta"]["receipts"]["ids"] != narrow["_meta"]["receipts"]["ids"]

    def test_the_scope_view_mirrors_the_projected_scope(self, _r183_env):
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {
                "repo": repo,
                "query": "refresh_token",
                "kind": "function",
                "receipt": True,
                "format": "json",
            },
        )
        envelope = _r183_receipts.lookup(body["_meta"]["receipts"]["ids"][0])[0]
        assert envelope["scope"] == envelope["effective_search"]["scope"]


# --- absence receipts and the Phase 3 contract -------------------------------


class TestAbsenceReceipts:
    def test_an_absence_receipt_links_to_the_recorded_scan(self, _r183_env):
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "no_such_symbol_anywhere", "receipt": True, "format": "json"},
        )
        envelope = _r183_receipts.lookup(_r183_receipt_ids(body)[0])[0]
        assert envelope["proof_kind"] == "symbol_lookup_absence"
        assert envelope["absence_ref"] == _r183_absence_ref(body)
        # The link is the point: absence_refusal stays the ONLY implementation of
        # the refusal rules, so a receipt cannot disagree with the gate.
        assert _handoff_mod.absence_record(envelope["absence_ref"]) is not None

    def test_an_absence_receipt_is_citable_when_the_scan_proves_absence(self, _r183_env):
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "no_such_symbol_anywhere", "receipt": True, "format": "json"},
        )
        eid = _r183_receipt_ids(body)[0]
        final, is_error = _r183_finalize(server, repo, [f"munch://evidence/{eid}"], task="abs")
        assert is_error is False
        assert final["absence_attested"] == 1
        assert final["receipts_attested"] == 1

    def test_a_refused_scan_refuses_its_receipt_through_the_same_rule(self, _r183_env):
        """The downgrade does the refusing; there is no second rule to keep in sync.

        ⚠ Rewritten in v1.108.192 (#377 P3). This used to express "unciteable"
        by MUTATING the live `_absences` record after the receipt was minted.
        That technique is no longer valid, and its invalidity is the fix: the
        `absent:` key carries no snapshot, so an in-place edit of the record it
        names is indistinguishable from a different scan of the same query
        overwriting it. A receipt that could be re-judged that way was never
        snapshot-bound. The receipt now carries its own frozen copy.

        The claim under test is unchanged and is now made at MINT time, which is
        where it always belonged: a scan that could not prove absence produces a
        receipt that is refused, by the same `absence_refusal` rule, with no
        second implementation.
        """
        server, repo, _proj = _r183_env
        # An ignored argument downgrades absent -> degraded at scan time, so the
        # scan is recorded as refusable and the receipt freezes it that way.
        body, _ = _r183_call(
            server,
            "search_symbols",
            {
                "repo": repo,
                "query": "no_such_symbol_anywhere",
                "regex": True,
                "receipt": True,
                "format": "json",
            },
        )
        eid = _r183_receipt_ids(body)[0]
        envelope = _r183_receipts.lookup(eid)[0]
        assert _handoff_mod.absence_refusal(envelope["absence_record"]) is not None
        final, is_error = _r183_finalize(server, repo, [f"munch://evidence/{eid}"], task="ref")
        assert is_error is True
        assert "refused" in final["error"]

    def test_mutating_the_live_record_cannot_re_judge_a_minted_receipt(self, _r183_env):
        """The behaviour change above, asserted directly rather than implied.

        Post-mint staleness is a different axis and is NOT what this answers:
        whether a receipt should EXPIRE because the tree moved on is the expiry
        taxonomy, still open under #377 Phase 2 P3. What is settled here is that
        an edit to a shared mutable key must not silently re-judge a receipt.
        """
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "no_such_symbol_anywhere", "receipt": True, "format": "json"},
        )
        eid = _r183_receipt_ids(body)[0]
        envelope = _r183_receipts.lookup(eid)[0]
        assert _handoff_mod.absence_refusal(envelope["absence_record"]) is None

        record = _handoff_mod._absences[envelope["absence_ref"]]
        record["channels"] = dict(record["channels"], index="stale")
        assert _handoff_mod.absence_refusal(record) is not None

        final, is_error = _r183_finalize(server, repo, [f"munch://evidence/{eid}"], task="ref")
        assert is_error is False, (
            "the live record was edited under a minted receipt and the receipt "
            "followed it; that is the mutable-key defect #377 P3 closed"
        )

    def test_a_refused_scan_marks_its_receipt_capabilities_honestly(self, _r183_env):
        server, repo, _proj = _r183_env
        # An ignored argument downgrades absent -> degraded, so the scan is
        # recorded and refused: the v1.108.175 contract feeding this one.
        body, _ = _r183_call(
            server,
            "search_symbols",
            {
                "repo": repo,
                "query": "no_such_symbol_anywhere",
                "regex": True,
                "receipt": True,
                "format": "json",
            },
        )
        ids = _r183_receipt_ids(body)
        assert ids
        envelope = _r183_receipts.lookup(ids[0])[0]
        assert envelope["capabilities"]["proves_absence"] is False
        assert any("cannot prove absence" in x for x in envelope["limitations"])

    def test_a_phase_3_ref_keeps_resolving_unchanged(self, _r183_env):
        """A shipped agent holding an `absent:` token must not break."""
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "no_such_symbol_anywhere", "format": "json"},
        )
        ref = _r183_absence_ref(body)
        assert ref and ref.startswith("absent:")
        final, is_error = _r183_finalize(server, repo, [ref], task="p3")
        assert is_error is False
        assert final["absence_attested"] == 1
        assert "evidence_precision" not in final

    def test_the_ref_helper_derives_what_note_absence_recorded(self):
        _handoff_mod.clear_absences()
        verdict = {
            "state": "absent",
            "scanned": {"symbols": 1, "files": 1},
            "channels": {"index": "fresh"},
        }
        ref, refusal = _handoff_mod.note_absence(
            "search_symbols", "local/x", "needle", verdict, arguments={"kind": "function"}
        )
        assert refusal is None
        assert ref == _handoff_mod.absence_ref_for(
            "search_symbols", "local/x", "needle", {"kind": "function"}
        )
        _handoff_mod.clear_absences()


# --- the carrier survives the wire ------------------------------------------


class TestTheCarrierSurvives:
    @pytest.mark.parametrize("fmt", ["json", "compact", "auto"])
    def test_the_receipt_id_survives_every_encoding(self, _r183_env, fmt):
        server, repo, _proj = _r183_env
        res = asyncio.run(
            server.call_tool(
                "search_symbols",
                {"repo": repo, "query": "refresh_token", "receipt": True, "format": fmt},
            )
        )
        assert "receipts" in _r183_raw(res)

    def test_the_receipt_id_survives_suppress_meta(self, _r183_env):
        """A display preference must never decide what a scan proved."""
        server, repo, _proj = _r183_env
        res = asyncio.run(
            server.call_tool(
                "search_symbols",
                {
                    "repo": repo,
                    "query": "refresh_token",
                    "receipt": True,
                    "suppress_meta": True,
                    "format": "json",
                },
            )
        )
        assert json.loads(_r183_raw(res))["_meta"]["receipts"]["count"] >= 1

    def test_the_receipt_id_survives_a_partial_meta_fields_filter(self, _r183_env, monkeypatch):
        server, repo, _proj = _r183_env
        from jcodemunch_mcp import config as config_module

        original = config_module.get

        def _r183_meta_only_timing(key, default=None, **kwargs):
            if key == "meta_fields":
                return ["timing_ms"]
            return original(key, default, **kwargs)

        monkeypatch.setattr(config_module, "get", _r183_meta_only_timing)
        body, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "refresh_token", "receipt": True, "format": "json"},
        )
        assert body["_meta"]["receipts"]["count"] >= 1

    def test_the_carrier_key_is_a_universal_contract_key(self):
        from jcodemunch_mcp.encoding.schema_driven import UNIVERSAL_META_JSON

        assert "receipts" in UNIVERSAL_META_JSON

    def test_the_carrier_holds_an_id_not_the_receipt_body(self, _r183_env):
        """The body is read from the resource; the response stays cheap."""
        server, repo, _proj = _r183_env
        _eid, body = _r183_serve_and_receipt(server, repo)
        carrier = body["_meta"]["receipts"]
        assert set(carrier) <= {
            "schema",
            "count",
            "ids",
            "proof_kinds",
            "resource_uri_prefix",
            "omitted",
            "omitted_note",
        }
        assert "subject" not in json.dumps(carrier)


class TestCompatibility:
    def test_a_call_without_receipt_is_byte_identical(self, _r183_env):
        """Testable, not asserted: the input picks the contract.

        Per-call telemetry (elapsed time, the running session meters, the turn
        budget) legitimately differs between two identical calls, so it is
        removed before comparison. Everything else — the rows, the verdict, the
        warnings, the whole payload — has to match to the byte.
        """
        server, repo, _proj = _r183_env
        args = {"repo": repo, "query": "refresh_token", "format": "json"}
        first = json.loads(_r183_raw(asyncio.run(server.call_tool("search_symbols", dict(args)))))
        second = json.loads(
            _r183_raw(asyncio.run(server.call_tool("search_symbols", dict(args, receipt=False))))
        )

        def _r183_strip_telemetry(payload):
            payload.pop("budget_warning", None)
            meta = payload.get("_meta")
            if isinstance(meta, dict):
                for key in (
                    "timing_ms",
                    "total_tokens_saved",
                    "budget",
                    "budget_warning",
                    "turn_tokens_used",
                    "turn_budget_remaining",
                    "cache_hit",
                    "already_delivered",
                ):
                    meta.pop(key, None)
            return payload

        assert json.dumps(_r183_strip_telemetry(first), sort_keys=True) == json.dumps(
            _r183_strip_telemetry(second), sort_keys=True
        )
        assert "receipts" not in json.dumps(first)

    def test_a_handoff_citing_no_receipts_renders_without_receipt_detail(self, _r183_env):
        server, repo, _proj = _r183_env
        _r183_call(
            server, "search_symbols", {"repo": repo, "query": "refresh_token", "format": "json"}
        )
        body, _ = _r183_finalize(server, repo, [_R183_TARGET], task="plain")
        rendered = asyncio.run(server.read_resource(body["resource_uri"]))[0].content
        assert "receipt:" not in rendered
        assert body["schema"] == "jcodemunch.handoff/v1"

    def test_a_handoff_citing_a_receipt_renders_its_subject(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        body, _ = _r183_finalize(server, repo, [f"munch://evidence/{eid}"], task="rendered")
        rendered = asyncio.run(server.read_resource(body["resource_uri"]))[0].content
        assert "receipt: symbol_definition" in rendered
        assert _R183_TARGET in rendered
        assert "snapshot: generation" in rendered

    def test_no_new_tool_was_added(self):
        """The tool-schema budget is a real constraint: receipts are a resource."""
        from jcodemunch_mcp import server

        names = {t.name for t in server._raw_catalog_tools()}
        assert not {n for n in names if "receipt" in n or "evidence" in n}


class TestTheResourceSurface:
    def test_a_minted_receipt_is_advertised(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        uris = {str(r.uri) for r in asyncio.run(server.list_resources())}
        assert f"munch://evidence/{eid}" in uris

    def test_the_advertised_row_never_shows_a_truncated_id_as_identity(self, _r183_env):
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        row = next(
            r
            for r in _r183_receipts.list_evidence_resources()
            if r["uri"].endswith(eid)
        )
        # Short prefix for humans in the NAME; the URI carries the full digest.
        assert row["name"] == f"evidence-{_r183_receipts.short_id(eid)}"
        assert eid in row["uri"]

    def test_the_runtime_identity_and_handoff_resources_still_resolve(self, _r183_env):
        server, repo, _proj = _r183_env
        from jcodemunch_mcp import runtime_identity

        payload = asyncio.run(server.read_resource(runtime_identity.IDENTITY_URI))
        assert json.loads(payload[0].content)["product"]

    def test_an_unrelated_uri_still_raises(self, _r183_env):
        server, _repo, _proj = _r183_env
        with pytest.raises(ValueError, match="Unknown resource"):
            asyncio.run(server.read_resource("munch://nope/1"))


class TestThePublishedSchema:
    def test_a_live_positive_receipt_validates(self, _r183_env):
        """Any new block is published in the same commit that emits it."""
        server, repo, _proj = _r183_env
        eid, _ = _r183_serve_and_receipt(server, repo)
        jsonschema.validate(_r183_receipts.lookup(eid)[0], _r183_schema())

    def test_a_live_absence_receipt_validates(self, _r183_env):
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {"repo": repo, "query": "no_such_symbol_anywhere", "receipt": True, "format": "json"},
        )
        jsonschema.validate(
            _r183_receipts.lookup(body["_meta"]["receipts"]["ids"][0])[0], _r183_schema()
        )

    def test_a_live_full_detail_receipt_validates(self, _r183_env):
        server, repo, _proj = _r183_env
        body, _ = _r183_call(
            server,
            "search_symbols",
            {
                "repo": repo,
                "query": "refresh_token",
                "detail_level": "full",
                "receipt": True,
                "format": "json",
            },
        )
        jsonschema.validate(
            _r183_receipts.lookup(body["_meta"]["receipts"]["ids"][0])[0], _r183_schema()
        )

    def test_the_schema_enumerates_every_proof_kind_the_code_knows(self):
        published = set(_r183_schema()["properties"]["proof_kind"]["enum"])
        assert published == set(_r183_receipts.PROOF_KINDS)

    def test_the_schema_enumerates_every_condition_token_the_code_emits(self):
        """Any new block is published in the same commit that emits it. The
        snapshot block is closed, so a token missing here fails our own contract."""
        published = set(
            _r183_schema()["properties"]["snapshot"]["properties"]["conditions"]["items"]["enum"]
        )
        assert published == set(_r183_producers._CONDITION_LIMITS)

    def test_the_schema_enumerates_every_freshness_and_tree_state(self):
        schema = _r183_schema()["properties"]["snapshot"]["properties"]
        assert set(schema["freshness"]["enum"]) == {"fresh", "stale", "unknown", "not_tracked"}
        assert set(schema["working_tree"]["enum"]) == {
            "clean",
            "dirty_in_scope",
            "dirty_outside_scope",
            "unknown",
            "not_applicable",
        }
